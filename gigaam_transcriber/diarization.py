"""
Модуль диаризации спикеров для GigaAM Transcriber.

Поддерживает:
- pyannote: Полная диаризация через pyannote/speaker-diarization-3.1
- hybrid: Гибридный подход с VAD + эмбеддинги + кластеризация
"""

import logging
import os
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

from .data_models import SpeakerSegment, TranscriptionSegment
from .exceptions import DiarizationError, HFTokenMissingError
from .speaker_mapping import assign_speakers_by_overlap

logger = logging.getLogger(__name__)

# Кэш для загруженных моделей
_diarization_pipeline = None
_embedding_model = None


class DiarizationManager:
    """Менеджер диаризации спикеров."""
    
    def __init__(
        self,
        hf_token: Optional[str] = None,
        device: str = "auto",
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        embedding_batch_size: Optional[int] = None,
        segmentation_batch_size: Optional[int] = None,
        embedding_backend: str = "torch",
    ):
        """
        Инициализация менеджера диаризации.

        Args:
            hf_token: HuggingFace токен для доступа к pyannote моделям
            device: Устройство ("auto", "cuda", "cpu", "mps")
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
            embedding_batch_size: Размер батча извлечения эмбеддингов (по умолч. 32)
            segmentation_batch_size: Размер батча сегментации (по умолч. 32)
        """
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.device = self._resolve_device(device)
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.embedding_batch_size = embedding_batch_size
        self.segmentation_batch_size = segmentation_batch_size
        self.embedding_backend = embedding_backend

        self._pipeline = None
    
    def _resolve_device(self, device: str) -> str:
        """Определение устройства."""
        if device == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return device
    
    @property
    def pipeline(self):
        """Ленивая загрузка pipeline диаризации."""
        if self._pipeline is None:
            self._pipeline = self._load_pipeline()
        return self._pipeline
    
    def _load_pipeline(self):
        """Загрузка pyannote pipeline."""
        if not self.hf_token:
            raise HFTokenMissingError()
        
        try:
            from pyannote.audio import Pipeline
            import torch
        except ImportError:
            raise DiarizationError(
                "pyannote.audio не установлен. "
                "Установите: pip install pyannote.audio"
            )
        
        # Устанавливаем токен для huggingface_hub
        try:
            from huggingface_hub import login
            # Пробуем логин, если токен не установлен в окружении
            if not os.getenv("HF_TOKEN"):
                login(token=self.hf_token, add_to_git_credential=False)
        except Exception as e:
            logger.debug(f"Не удалось установить токен через huggingface_hub: {e}")
        
        # Модель диаризации (устаревший fallback убран — он только маскировал
        # реальную причину 403 по основной модели)
        models_to_try = [
            "pyannote/speaker-diarization-3.1",
        ]
        
        last_error = None
        
        for model_id in models_to_try:
            try:
                logger.info(f"Попытка загрузки модели: {model_id}")
                # В новых версиях pyannote.audio используется 'token' вместо 'use_auth_token'
                try:
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        token=self.hf_token
                    )
                    logger.info(f"Модель {model_id} загружена успешно")
                    break
                except TypeError:
                    # Fallback для старых версий
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        use_auth_token=self.hf_token
                    )
                    logger.info(f"Модель {model_id} загружена успешно")
                    break
            except Exception as e:
                last_error = e
                error_str = str(e)
                
                # Проверяем на ошибку доступа
                if "403" in error_str or "gated" in error_str.lower() or "authorized" in error_str.lower():
                    logger.warning(
                        f"Нет доступа к модели {model_id}. "
                        f"Необходимо принять условия использования на HuggingFace:\n"
                        f"1. Перейдите на https://huggingface.co/{model_id}\n"
                        f"2. Нажмите 'Agree and access repository'\n"
                        f"3. Также примите условия для pyannote/segmentation-3.0\n"
                        f"4. Убедитесь, что токен имеет права 'read'"
                    )
                    continue
                else:
                    logger.warning(f"Ошибка при загрузке {model_id}: {e}")
                    continue
        else:
            # Если все попытки не удались
            if last_error:
                error_str = str(last_error)
                if "403" in error_str or "gated" in error_str.lower():
                    raise DiarizationError(
                        f"Нет доступа к моделям диаризации. "
                        f"Примите условия использования на HuggingFace:\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                        f"- https://huggingface.co/pyannote/segmentation-3.0\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization\n"
                        f"\nПосле принятия условий повторите попытку.",
                        cause=last_error
                    )
                else:
                    raise DiarizationError(
                        "Не удалось загрузить модель диаризации",
                        cause=last_error
                    )
            else:
                raise DiarizationError("Не удалось загрузить модель диаризации")
        
        # Перемещение на устройство
        device = torch.device(self.device)
        pipeline = pipeline.to(device)

        # Настройка размеров батчей (узкое место на CPU — извлечение эмбеддингов)
        if self.embedding_batch_size:
            try:
                pipeline.embedding_batch_size = self.embedding_batch_size
            except Exception as e:
                logger.debug(f"Не удалось задать embedding_batch_size: {e}")
        if self.segmentation_batch_size:
            try:
                pipeline.segmentation_batch_size = self.segmentation_batch_size
            except Exception as e:
                logger.debug(f"Не удалось задать segmentation_batch_size: {e}")

        # Подмена эмбеддера на ONNX (узкое место диаризации). Сегментация остаётся torch
        # (у неё нет ONNX-пути). ONNX Runtime работает на CPU.
        if self.embedding_backend == "onnx":
            try:
                from pyannote.audio.pipelines.speaker_verification import (
                    ONNXWeSpeakerPretrainedSpeakerEmbedding,
                )
                pipeline._embedding = ONNXWeSpeakerPretrainedSpeakerEmbedding(
                    device=torch.device("cpu"), token=self.hf_token
                )
                logger.info("Эмбеддер диаризации переключён на ONNX (CPU)")
            except Exception as e:
                logger.warning(f"Не удалось включить ONNX-эмбеддер, остаётся torch: {e}")

        logger.info(
            f"Диаризация на устройстве={self.device}, backend эмбеддера={self.embedding_backend}, "
            f"embedding_batch_size={getattr(pipeline, 'embedding_batch_size', '?')}, "
            f"segmentation_batch_size={getattr(pipeline, 'segmentation_batch_size', '?')}"
        )

        return pipeline
    
    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        hook=None,
    ) -> List[SpeakerSegment]:
        """
        Выполнить диаризацию аудио файла.
        
        Args:
            audio_path: Путь к аудио файлу (должен быть WAV, 16kHz, mono)
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
            
        Returns:
            Список сегментов с информацией о спикерах
        """
        audio_path = Path(audio_path)
        
        # Использование параметров по умолчанию
        min_speakers = min_speakers or self.min_speakers
        max_speakers = max_speakers or self.max_speakers
        
        try:
            # Подготовка параметров
            kwargs = {}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers
            else:
                if min_speakers is not None:
                    kwargs["min_speakers"] = min_speakers
                if max_speakers is not None:
                    kwargs["max_speakers"] = max_speakers
            
            # Прогресс-хук (опционально, для UI)
            if hook is not None:
                kwargs["hook"] = hook

            # Запуск диаризации
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                diarization = self.pipeline(str(audio_path), **kwargs)
            
            # Преобразование результатов (pyannote.audio 4.0+ API)
            segments = []
            for turn, speaker in diarization.speaker_diarization:
                segments.append(SpeakerSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=speaker
                ))
            
            # Сортировка по времени
            segments.sort(key=lambda s: s.start)
            
            # Переименование спикеров в человекочитаемый формат
            segments = self._rename_speakers(segments)
            
            return segments
            
        except Exception as e:
            raise DiarizationError(f"Ошибка при диаризации: {e}", cause=e)
    
    def _rename_speakers(
        self, 
        segments: List[SpeakerSegment]
    ) -> List[SpeakerSegment]:
        """
        Переименование спикеров в человекочитаемый формат.
        
        SPEAKER_00 -> Спикер №1
        SPEAKER_01 -> Спикер №2
        """
        # Получаем уникальных спикеров в порядке первого появления
        seen = set()
        speaker_order = []
        for seg in segments:
            if seg.speaker not in seen:
                seen.add(seg.speaker)
                speaker_order.append(seg.speaker)
        
        # Создаём маппинг
        speaker_map = {
            old_name: f"Спикер №{i+1}" 
            for i, old_name in enumerate(speaker_order)
        }
        
        # Применяем переименование
        for seg in segments:
            seg.speaker = speaker_map.get(seg.speaker, seg.speaker)
        
        return segments
    
    def map_speakers_to_transcription(
        self,
        transcription_segments: List[TranscriptionSegment],
        speaker_segments: List[SpeakerSegment],
    ) -> List[TranscriptionSegment]:
        """
        Сопоставление транскрипции с диаризацией по временным меткам.
        
        Для каждого сегмента транскрипции определяется спикер
        на основе временного пересечения с сегментами диаризации.
        
        Args:
            transcription_segments: Сегменты транскрипции
            speaker_segments: Сегменты диаризации
            
        Returns:
            Сегменты транскрипции с присвоенными спикерами
        """
        # Overlap-primary атрибуция (суммарное пересечение по спикеру) + nearest-фолбэк
        # + speaker_confidence. Логика вынесена в чистый модуль speaker_mapping
        # (общий шов: зовётся и из transcriber.py, и из cli_ui.py — долетает до UI).
        return assign_speakers_by_overlap(
            transcription_segments, speaker_segments, fill_nearest=True
        )


class HybridDiarization:
    """
    Гибридная диаризация: VAD + эмбеддинги + кластеризация.
    
    Этот подход легче и быстрее полной pyannote диаризации,
    но может быть менее точным.
    """
    
    def __init__(
        self,
        hf_token: Optional[str] = None,
        device: str = "auto",
        num_clusters: Optional[int] = None,
    ):
        """
        Инициализация гибридной диаризации.
        
        Args:
            hf_token: HuggingFace токен
            device: Устройство
            num_clusters: Ожидаемое количество спикеров
        """
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.device = device
        self.num_clusters = num_clusters
        
        self._embedding_model = None
        self._vad_model = None
    
    def _get_embedding_model(self):
        """Загрузка модели эмбеддингов спикера."""
        if self._embedding_model is None:
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:
                raise DiarizationError(
                    "speechbrain не установлен. "
                    "Установите: pip install speechbrain"
                )
            
            self._embedding_model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir="pretrained_models/spkrec-ecapa-voxceleb",
                run_opts={"device": self.device}
            )
        
        return self._embedding_model
    
    def diarize(
        self,
        audio_path: Path | str,
        speech_segments: List[Tuple[float, float]],
        num_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
        """
        Гибридная диаризация.
        
        Args:
            audio_path: Путь к аудио
            speech_segments: Сегменты речи от VAD [(start, end), ...]
            num_speakers: Количество спикеров
            
        Returns:
            Сегменты с метками спикеров
        """
        try:
            import numpy as np
            from sklearn.cluster import AgglomerativeClustering
            import torchaudio
        except ImportError as e:
            raise DiarizationError(
                "Не установлены зависимости для гибридной диаризации. "
                "Установите: pip install scikit-learn torchaudio",
                cause=e
            )
        
        num_speakers = num_speakers or self.num_clusters or 2
        
        # Загружаем аудио
        waveform, sr = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        
        # Ресэмплинг если нужно
        target_sr = 16000
        if sr != target_sr:
            resampler = torchaudio.transforms.Resample(sr, target_sr)
            waveform = resampler(waveform)
        
        # Получаем эмбеддинги для каждого сегмента
        embeddings = []
        model = self._get_embedding_model()
        
        for start, end in speech_segments:
            start_sample = int(start * target_sr)
            end_sample = int(end * target_sr)
            segment = waveform[:, start_sample:end_sample]
            
            if segment.shape[1] < target_sr * 0.5:  # Минимум 0.5 сек
                continue
            
            embedding = model.encode_batch(segment)
            embeddings.append(embedding.squeeze().cpu().numpy())
        
        if len(embeddings) < 2:
            # Недостаточно сегментов для кластеризации
            return [
                SpeakerSegment(start=s, end=e, speaker="Спикер №1")
                for s, e in speech_segments
            ]
        
        # Кластеризация
        embeddings = np.array(embeddings)
        n_clusters = min(num_speakers, len(embeddings))
        
        clustering = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric='cosine',
            linkage='average'
        )
        labels = clustering.fit_predict(embeddings)
        
        # Создаём результат
        result = []
        for (start, end), label in zip(speech_segments, labels):
            result.append(SpeakerSegment(
                start=start,
                end=end,
                speaker=f"Спикер №{label + 1}"
            ))
        
        return result


def get_diarization_manager(
    hf_token: Optional[str] = None,
    device: str = "auto",
    **kwargs
) -> DiarizationManager:
    """
    Получить менеджер диаризации.
    
    Args:
        hf_token: HuggingFace токен
        device: Устройство
        **kwargs: Дополнительные параметры
        
    Returns:
        Экземпляр DiarizationManager
    """
    return DiarizationManager(
        hf_token=hf_token,
        device=device,
        **kwargs
    )
