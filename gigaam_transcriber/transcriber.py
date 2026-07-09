"""
Основной класс GigaAMTranscriber - фасад для работы с GigaAM.

Обеспечивает:
- Транскрипцию аудио и видео файлов любой длительности
- Опциональную диаризацию спикеров
- Различные форматы вывода
"""

import logging
import os
import sys
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .audio_processor import AudioProcessor
from .data_models import (
    DiarizationMode,
    OutputFormat,
    TranscriptionResult,
    TranscriptionSegment,
)
from .decode import (
    DecodeOptions,
    decode_long_plain,
    decode_long_with_confidence,
    decode_onnx,
    decode_short,
)
from .diarization import DiarizationManager
from .exceptions import (
    EmptyAudioError,
    EmptyFileError,
    HFTokenMissingError,
    ModelLoadError,
    UnsupportedFormatError,
)
from .segment_merger import MergeConfig, SegmentMerger

logger = logging.getLogger(__name__)

# Добавляем путь к GigaAM в PYTHONPATH
GIGAAM_PATH = Path(__file__).parent.parent / "GigaAM"
if str(GIGAAM_PATH) not in sys.path:
    sys.path.insert(0, str(GIGAAM_PATH))


class GigaAMTranscriber:
    """
    Фасад для работы с GigaAM транскрипцией.

    Принципы:
    - Lazy loading моделей (загружаются при первом использовании)
    - Единообразный интерфейс для audio/video
    - Прозрачная обработка любой длительности
    - Graceful degradation при отсутствии HF_TOKEN

    Примеры использования:

    >>> # Простая транскрипция
    >>> transcriber = GigaAMTranscriber()
    >>> result = transcriber.transcribe("audio.wav")
    >>> print(result.text)

    >>> # С диаризацией
    >>> result = transcriber.transcribe("meeting.mp4", diarization="pyannote")
    >>> for seg in result.segments:
    ...     print(f"{seg.speaker}: {seg.text}")

    >>> # Сохранение в файл
    >>> result.save("transcript.json", format="json")
    """

    # Ограничение GigaAM для метода transcribe()
    MAX_SHORT_DURATION = 25.0  # секунд

    def __init__(
        self,
        model_name: str = "v3_e2e_rnnt",
        device: str = "auto",
        hf_token: str | None = None,
        cache_dir: Path | None = None,
        verbose: bool = False,
        fp16_encoder: bool = True,
        diar_device: str | None = None,
        embedding_batch_size: int | None = None,
        segmentation_batch_size: int | None = None,
        embedding_backend: str = "torch",
    ):
        """
        Инициализация транскрибера.

        Args:
            model_name: Имя модели GigaAM ("v3_e2e_rnnt", "v3_e2e_ctc", и т.д.)
            device: Устройство ("auto", "cuda", "cpu")
            hf_token: HuggingFace токен для pyannote диаризации
            cache_dir: Директория для кэша
            verbose: Подробный вывод
            fp16_encoder: Использовать FP16 для энкодера (быстрее на GPU)
            diar_device: Отдельное устройство для диаризации (None → совпадает с device;
                mps ускоряет извлечение эмбеддингов ~10× на Apple Silicon). Тюнинг не
                меняет текст ASR — только скорость/устройство диаризации.
            embedding_batch_size: Размер батча извлечения эмбеддингов (None → дефолт ~32).
            segmentation_batch_size: Размер батча сегментации (None → дефолт ~32).
            embedding_backend: Бэкенд эмбеддера диаризации ("torch" | "onnx").
        """
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.cache_dir = (
            Path(cache_dir) if cache_dir else Path.home() / ".cache" / "gigaam_transcriber"
        )
        self.verbose = verbose
        # На MPS (Apple GPU) fp16-путь GigaAM не валидирован → держим fp32 для стабильности
        self.fp16_encoder = False if self.device == "mps" else fp16_encoder
        # Тюнинг диаризации (пробрасывается в DiarizationManager при ленивом создании).
        self.diar_device = diar_device
        self.embedding_batch_size = embedding_batch_size
        self.segmentation_batch_size = segmentation_batch_size
        self.embedding_backend = embedding_backend

        # Lazy-loaded компоненты
        self._model = None
        self._audio_processor: AudioProcessor | None = None
        self._diarization_manager: DiarizationManager | None = None
        # Кэши ONNX-артефактов (ключ — int8-флаг; энкодер пробуем загрузить один раз).
        self._onnx_sessions: dict[bool, Any] = {}
        self._onnx_enc: Any = None
        self._onnx_enc_tried = False

        # Состояние GPU→CPU fallback (#14, L1). _intended_device — устройство, на которое
        # репарация вернёт модель на границе джобы после прошлого аварийного отката на CPU
        # (тёплый singleton сервера переиспользуется между запросами). _device_fell_back —
        # пометка ТЕКУЩЕЙ джобы (сбрасывается на входе каждой публичной джобы).
        self._intended_device = self.device
        self._device_fell_back = False

        # Создание директории кэша
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Настройка логирования
        if verbose:
            logging.basicConfig(level=logging.DEBUG)

        logger.info(f"GigaAMTranscriber инициализирован: model={model_name}, device={self.device}")

    def _resolve_device(self, device: str) -> str:
        """Определение устройства."""
        if device == "auto":
            try:
                import torch

                # MPS автоматически не выбираем: путь GigaAM на MPS не валидирован
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        if device == "mps":
            try:
                import torch

                if not torch.backends.mps.is_available():
                    logger.warning("MPS недоступен, откат на CPU")
                    return "cpu"
            except ImportError:
                return "cpu"
        return device

    def _gpu_to_cpu_fallback(self, error: Exception) -> bool:
        """Перенести GigaAM-модель на CPU после GPU-сбоя (OOM/RuntimeError на MPS/CUDA).

        Возвращает успех. Дальнейший декод идёт на CPU (метится metadata.device_fallback).
        Робастность на длинных встречах: лучше доделать на CPU, чем уронить весь прогон."""
        try:
            self.model.to("cpu")
            self.device = "cpu"
            self._device_fell_back = True
            logger.info("Модель перенесена на CPU после GPU-сбоя")
            return True
        except Exception:
            return False

    def _repair_device(self) -> None:
        """L1: репарация sticky GPU→CPU fallback на границе джобы.

        ``_gpu_to_cpu_fallback`` после ОДНОГО GPU-сбоя защёлкивает модель на CPU
        безвозвратно (``self.device='cpu'`` навсегда) → все последующие джобы тёплого
        singleton идут ×10 медленнее до перезапуска процесса. Здесь — на входе каждой
        публичной джобы — возвращаем модель на исходное устройство и сбрасываем
        пер-джобовую пометку, чтобы один битый файл не деградировал весь сервер.
        Если вернуть на GPU не удалось — остаёмся на CPU (лучше медленно, чем падать)."""
        if self._device_fell_back and self._intended_device in ("cuda", "mps"):
            try:
                if self._model is not None:
                    self._model.to(self._intended_device)
                self.device = self._intended_device
                logger.info(
                    f"Устройство восстановлено на {self._intended_device} "
                    "после прошлого GPU→CPU fallback"
                )
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass
            except Exception as e:
                logger.warning(f"Репарация устройства не удалась, продолжаем на CPU: {e!r}")
                return
        self._device_fell_back = False

    # =========================================================================
    # Свойства с ленивой загрузкой
    # =========================================================================

    @property
    def model(self):
        """GigaAM модель (ленивая загрузка)."""
        if self._model is None:
            self._model = self._load_model()
        return self._model

    @property
    def audio_processor(self) -> AudioProcessor:
        """Процессор аудио (ленивая загрузка)."""
        ap = self._audio_processor
        if ap is None:
            ap = self._audio_processor = AudioProcessor()
        return ap

    @property
    def diarization_manager(self) -> DiarizationManager:
        """Менеджер диаризации (ленивая загрузка)."""
        dm = self._diarization_manager
        if dm is None:
            dm = self._diarization_manager = DiarizationManager(
                hf_token=self.hf_token,
                device=self.diar_device or self.device,
                embedding_batch_size=self.embedding_batch_size,
                segmentation_batch_size=self.segmentation_batch_size,
                embedding_backend=self.embedding_backend,
            )
        return dm

    def _load_model(self):
        """Загрузка GigaAM модели."""
        try:
            import gigaam
        except ImportError:
            raise ModelLoadError(
                self.model_name,
                cause=ImportError("gigaam не установлен. " "Установите: pip install -e ./GigaAM"),
            )

        try:
            logger.info(f"Загрузка модели {self.model_name}...")
            model = gigaam.load_model(
                self.model_name,
                fp16_encoder=self.fp16_encoder,
                device=self.device,
            )
            logger.info(f"Модель {self.model_name} загружена успешно")
            return model
        except Exception as e:
            raise ModelLoadError(self.model_name, cause=e)

    # =========================================================================
    # Контекстный менеджер
    # =========================================================================

    def __enter__(self):
        """Вход в контекст."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - освобождение ресурсов."""
        self.cleanup()

    def cleanup(self):
        """Освобождение GPU памяти и ресурсов (модель, диаризация, ONNX-кэши)."""
        released = self._model is not None or self._diarization_manager is not None
        self._model = None
        self._diarization_manager = None  # pyannote-веса тоже занимают GPU-память
        self._onnx_sessions = {}
        self._onnx_enc = None
        self._onnx_enc_tried = False

        if released:
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

        logger.info("Ресурсы освобождены")

    # =========================================================================
    # Валидация
    # =========================================================================

    def _validate_input(self, path: Path) -> None:
        """Валидация входного файла."""
        if not path.exists():
            raise FileNotFoundError(str(path))

        if path.stat().st_size == 0:
            raise EmptyFileError(str(path))

        if not self.audio_processor.is_supported_file(path):
            raise UnsupportedFormatError(path.suffix)

    # =========================================================================
    # Основные методы транскрипции
    # =========================================================================

    def _write_outputs(
        self,
        result: TranscriptionResult,
        output_path: str | Path | None,
        output_format: "OutputFormat",
        emit_l0: bool,
    ) -> None:
        """Записать артефакты вывода (файл результата + opt-in L0-субстрат).

        Единая точка для основного прогона и для resume-ветки — иначе resume=True
        возвращал бы кэш в память, но не писал output_path/L0 на диск (нарушение контракта)."""
        if not output_path:
            return
        result.save(output_path, output_format)
        logger.info(f"Результат сохранён: {output_path}")
        # L0 evidence-субстрат (opt-in): transcript.v1.jsonl + sha256 рядом с выводом.
        if emit_l0:
            try:
                from .l0 import build_l0, write_l0

                l0_path = Path(output_path).with_suffix(".v1.jsonl")
                write_l0(build_l0(result), l0_path)
                logger.info(f"L0 записан: {l0_path}")
            except Exception as e:
                logger.warning(f"L0 пропущен: {e!r}")

    def transcribe(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        diarization: DiarizationMode = "none",
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        language: str = "ru",
        output_format: OutputFormat = "txt",
        merge_same_speaker: bool = True,
        min_segment_gap: float = 0.5,
        glossary: bool = True,
        second_opinion: bool = False,
        voiceprint: bool = False,
        voiceprint_gallery: str | Path | None = None,
        preclean: bool = False,
        backend: str = "torch",
        onnx_int8: bool = False,
        onnx_encoder: bool = False,
        word_timestamps: bool = False,
        resume: bool = False,
        manifest_path: str | Path | None = None,
        emit_l0: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> TranscriptionResult:
        """Универсальный метод транскрипции (audio/video, любая длительность).

        Пайплайн: подготовка аудио → декод (torch/onnx) → опц. диаризация →
        пост-проходы качества (флаги риска → voiceprint → сшивка → глоссарий → L2
        «второе мнение») → метаданные → вывод (output_path + opt-in L0) + manifest.

        Все флаги качества opt-in (default off), КРОМЕ ``glossary=True``.
        ``resume=True`` возвращает кэш из manifest только при совпадении file_hash,
        состава quality-слоёв и LAYER_VERSIONS. Кириллица вывода GigaAM
        неприкосновенна (I1): глоссарий/L2 правят только латиницу/числа.
        """
        input_path = Path(input_path)
        start_time = time.time()
        # L1: вернуть модель на исходное устройство, если прошлая джоба упала на CPU
        # (тёплый singleton сервера переиспользуется между запросами). No-op на CPU.
        self._repair_device()
        # Пер-джобовые опции декода — явная передача по цепочке (без мутации singleton).
        opts = DecodeOptions(
            backend=backend,
            onnx_int8=onnx_int8,
            onnx_encoder=onnx_encoder,
            word_timestamps=word_timestamps,
            progress_cb=progress_callback,
        )

        # Валидация
        self._validate_input(input_path)

        # Graceful degradation для диаризации (до resume-сигнатуры: manifest фиксирует
        # ФАКТИЧЕСКИЙ состав обработки).
        if diarization != "none" and self.hf_token is None:
            warnings.warn(
                "HF_TOKEN не установлен, диаризация будет пропущена. "
                "Установите переменную окружения HF_TOKEN для диаризации.",
                stacklevel=2,
            )
            diarization = "none"

        # Resume (#16): кэш результата по хэшу файла — повторный прогон пропускает ASR.
        # Сигнатура запроса входит в manifest: смена состава quality-слоёв или бамп
        # LAYER_VERSIONS инвалидирует кэш (иначе second_opinion=True вернул бы старый
        # результат без L2).
        from .manifest import manifest_path_for, resume_result, write_manifest

        request_sig = {
            "diarization": diarization,
            "glossary": glossary,
            "second_opinion": second_opinion,
            "voiceprint": voiceprint,
            "preclean": preclean,
            "backend": backend,
            "word_timestamps": word_timestamps,
        }
        _mpath = None
        if manifest_path:
            _mpath = Path(manifest_path)
        elif output_path:
            _mpath = manifest_path_for(output_path)
        if resume and _mpath:
            cached = resume_result(_mpath, input_path, request=request_sig)
            if cached is not None:
                logger.info(f"Resume: восстановлено из {_mpath} (ASR пропущен)")
                # Resume пропускает только ASR — output_path/L0 всё равно нужно записать
                # (кэш в памяти ≠ файл на диске; иначе тихо пропадает запрошенный артефакт).
                self._write_outputs(cached, output_path, output_format, emit_l0)
                return cached

        logger.info(f"Начало транскрипции: {input_path}")

        # Определяем тип файла и вызываем соответствующий метод (с постадийным таймингом)
        from .stage_timing import StageTimer

        # Preclean-фильтр (#17, opt-in): highpass=80 убирает НЧ-гул, loudnorm выравнивает громкость.
        _PRECLEAN = "highpass=f=80,loudnorm=I=-23:LRA=7:TP=-2"
        # Аудио для пост-проходов, читающих волну (voiceprint/second_opinion). Для видео —
        # извлечённый wav (видео-контейнер torchaudio не декодирует); удаляется в конце.
        post_audio: Path = input_path
        _video_tmp: Path | None = None
        timer = StageTimer()
        with timer.measure("decode_diarize"):
            if self.audio_processor.is_video_file(input_path):
                result, _video_tmp = self._transcribe_video(
                    input_path,
                    keep_temp_audio=True,
                    diarization=diarization,
                    preclean_filter=(_PRECLEAN if preclean else None),
                    opts=opts,
                    num_speakers=num_speakers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                )
                if _video_tmp is not None:
                    post_audio = _video_tmp
            else:
                result = self._transcribe_audio(
                    input_path,
                    diarization=diarization,
                    preclean_filter=(_PRECLEAN if preclean else None),
                    opts=opts,
                    num_speakers=num_speakers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                )

        # Пост-проходы качества — общие с transcribe_route_a (см. хелперы ниже).
        self._flag_text_quality(result.segments)

        # Voiceprint: переименовать анонимных «Спикер №N» в реальные имена по галерее голосов
        # (opt-in, precision-first: при сомнении метка остаётся «Спикер №N»). Меняет МЕТКУ
        # спикера, не текст (provenance текста не трогаем).
        if voiceprint and voiceprint_gallery and result.segments:
            try:
                from .voiceprint import DEFAULT_THRESHOLD, load_gallery, name_diarized_speakers

                refs, gtheta, gmargin = load_gallery(voiceprint_gallery)
                if refs:
                    thr = gtheta if gtheta is not None else DEFAULT_THRESHOLD
                    named = name_diarized_speakers(
                        result, post_audio, refs, thr=thr, margin=gmargin
                    )
                    if named:
                        result.metadata["voiceprint_named"] = named
                        logger.info(f"Voiceprint: {named} спикеров названо")
            except Exception as e:
                logger.warning(f"Voiceprint пропущен: {e!r}")

        # Сшивка сегментов
        if merge_same_speaker and result.segments:
            merger = SegmentMerger(MergeConfig(max_gap=min_segment_gap))
            result.segments = merger.merge_same_speaker_segments(
                result.segments, max_gap=min_segment_gap
            )
            result.text = " ".join(seg.text for seg in result.segments)

        # Глоссарий-runtime грузим один раз — нужен и канонизации, и fusion/праймингу L2.
        gloss_amap, gloss_suffixable = (
            self._load_glossary_runtime() if (glossary or second_opinion) else ({}, set())
        )
        if glossary:
            self._apply_glossary(result, gloss_amap, gloss_suffixable)
        if second_opinion:
            self._apply_second_opinion(result, post_audio, gloss_amap)

        # Извлечённый из видео wav больше не нужен (voiceprint/L2 прочитали его выше).
        if _video_tmp is not None and Path(_video_tmp).exists():
            try:
                Path(_video_tmp).unlink()
            except Exception:
                pass

        # Обновление метаданных
        from .versions import pipeline_versions

        processing_time = time.time() - start_time
        result.processing_time = processing_time
        result.language = language
        result.model_name = self.model_name
        result.metadata["source"] = str(input_path)
        timer.add("total", processing_time)
        result.metadata["stage_timing_sec"] = timer.as_dict()
        result.metadata["layer_versions"] = pipeline_versions()
        if self._device_fell_back:
            result.metadata["device_fallback"] = self.device
        if preclean:
            result.metadata["preclean"] = _PRECLEAN
        result.metadata["backend"] = opts.backend

        logger.info(
            f"Транскрипция завершена за {processing_time:.1f}с "
            f"({len(result.segments)} сегментов)"
        )

        # Сохранение результата (файл + opt-in L0) — общая точка с resume-веткой.
        self._write_outputs(result, output_path, output_format, emit_l0)

        # manifest (#16): записать для будущего resume (даже если resume=False сейчас).
        if _mpath is not None:
            try:
                write_manifest(result, input_path, _mpath, request=request_sig)
            except Exception as e:
                logger.warning(f"manifest не записан: {e!r}")

        return result

    # =========================================================================
    # Пост-проходы качества — общие для transcribe() и transcribe_route_a()
    # =========================================================================

    @staticmethod
    def _flag_text_quality(segments: list[TranscriptionSegment]) -> None:
        """Флаги риска текста (галлюцинации/лупы) — ПОМЕТКА, не правка (I1).

        Зовётся до сшивки (на сырых ASR-сегментах); merge объединит flags."""
        from .text_quality import detect_quality_flags

        for seg in segments:
            fl = detect_quality_flags(seg.text)
            if fl:
                seg.flags = sorted(set(seg.flags) | set(fl))

    @staticmethod
    def _load_glossary_runtime() -> tuple[dict, set]:
        """Глоссарий-runtime (alias_map + suffixable) — нужен канонизации и праймингу L2."""
        try:
            from .glossary import load_runtime

            return load_runtime()
        except Exception as e:
            logger.warning(f"Глоссарий не загружен: {e!r}")
            return {}, set()

    @staticmethod
    def _apply_glossary(result: TranscriptionResult, amap: dict, suffixable: set) -> None:
        """Канонизация имён/терминов — детерминированный I1-safe пост-проход.

        Меняет только курируемые алиасы (lint по russian/english_words), кириллица verbatim."""
        if not amap or not result.segments:
            return
        from .glossary import apply_to_segments

        n = apply_to_segments(result.segments, amap, suffixable)
        if n:
            result.text = " ".join(seg.text for seg in result.segments)
            result.metadata["glossary_replacements"] = (
                result.metadata.get("glossary_replacements", 0) + n
            )
            logger.info(f"Глоссарий: {n} замен")

    @staticmethod
    def _apply_second_opinion(
        result: TranscriptionResult,
        audio_path: Path,
        amap: dict,
        participants: tuple[str, ...] = (),
    ) -> int:
        """L2 «второе мнение» (opt-in): локальный Whisper перечитывает сегменты-кандидаты
        (с латиницей), fusion заменяет ТОЛЬКО латиницу/числа (кириллица verbatim, I1)."""
        if not result.segments:
            return 0
        try:
            from .whisper_asr import apply_second_opinion

            changed = apply_second_opinion(result, audio_path, amap, participants=participants)
        except Exception as e:
            # Часть сегментов могла быть слита до сбоя — пересобрать полный текст,
            # чтобы result.text не разошёлся с segments.
            result.text = " ".join(seg.text for seg in result.segments)
            logger.warning(f"L2 пропущено: {e!r}")
            return 0
        if changed:
            result.text = " ".join(seg.text for seg in result.segments)
            result.metadata["second_opinion_changed"] = (
                result.metadata.get("second_opinion_changed", 0) + changed
            )
            logger.info(f"L2 «второе мнение»: {changed} сегментов исправлено")
        return changed

    @staticmethod
    def discover_route_a_tracks(
        folder: str | Path, speaker_dir: str = "Audio Record"
    ) -> dict[str, str]:
        """Найти per-участниковые дорожки в ``<folder>/<speaker_dir>/*.m4a`` → {имя: путь}.

        Имя — best-effort (strip 'audio'+хвостовые цифры magic/index, camelCase→пробел),
        канонизируется через глоссарий ``people`` если доступен. NFC-нормализация (macOS
        хранит кириллицу в NFD). Caller может передать свой dict в ``transcribe_route_a``."""
        import glob
        import re
        import unicodedata

        base = Path(folder).expanduser()
        d = base / speaker_dir
        d = d if d.is_dir() else base
        files = glob.glob(str(d / "*.m4a"))
        people: dict[str, str] = {}
        try:
            from .glossary import load_glossary

            g = load_glossary()
            people = {
                k.lower(): v for k, v in (g.get("people") or {}).items() if not k.startswith("_")
            }
        except Exception:
            pass
        tracks: dict[str, str] = {}
        for f in files:
            stem = unicodedata.normalize("NFC", Path(f).stem)
            raw = re.sub(r"^audio", "", stem, flags=re.IGNORECASE)  # и CamelCase 'Audio'
            raw = re.sub(r"\d+$", "", raw)  # хвостовые magic+index
            spaced = re.sub(r"(?<=[a-zа-яё])(?=[A-ZА-ЯЁ])", " ", raw).strip()
            name = people.get(spaced.lower(), spaced)
            if name:
                # Коллизия (два «Ivan», или общий канон people) тихо теряла бы дорожку —
                # предупреждаем и пропускаем, чтобы участник не исчез из транскрипта без следа.
                if name in tracks:
                    logger.warning(
                        "Route A discover: коллизия имени %r — оставляю %s, пропускаю %s "
                        "(переименуйте дорожку, иначе участник потеряется)",
                        name,
                        tracks[name],
                        f,
                    )
                    continue
                tracks[name] = f
        return tracks

    def transcribe_route_a(
        self,
        tracks: dict[str, str | Path],
        output_path: str | Path | None = None,
        output_format: OutputFormat = "txt",
        glossary: bool = True,
        second_opinion: bool = False,
        min_segment_gap: float = 0.5,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> TranscriptionResult:
        """Route A (#21, библиотечный путь): дорожки участников → имена ground-truth.

        ``tracks`` = {имя: путь}; каждая дорожка — речь ОДНОГО участника (Zoom Audio Record).
        ASR каждой дорожки → сегменты с speaker=имя (БЕЗ диаризации/voiceprint — имена точные).
        Сегменты сливаются на общий таймлайн (сортировка по времени) + сшивка одного спикера.
        Пост-проходы качества — те же, что у transcribe(): флаги риска текста, глоссарий,
        opt-in L2 «второе мнение» (per-track: whisper читает волну исходной дорожки).
        De-bleed перекрытий — точка роста (кросстолк может попасть на несколько дорожек).

        ``progress_callback(current, total, name)`` (L4, opt-in) вызывается после каждой
        дорожки (1..N) — единственный per-track сигнал для прогресс-бара сервера на основном
        пути (внутренний цикл иначе не виден воркеру). Изоляция ошибок по дорожкам (L2):
        битая/пустая/повреждённая дорожка не валит весь митинг — помечается в
        ``metadata['failed_tracks']``, остальные выживают (частичный транскрипт)."""
        start_time = time.time()
        # L1: репарация sticky GPU→CPU fallback на входе джобы (Route A — основной путь).
        self._repair_device()
        # Дефолтные опции декода: torch, без ONNX/word-timestamps; per-track cb — свой.
        opts = DecodeOptions()

        # Глоссарий-runtime один раз на весь митинг (канонизация + прайминг L2).
        gloss_amap, gloss_suffixable = (
            self._load_glossary_runtime() if (glossary or second_opinion) else ({}, set())
        )
        participants = tuple(tracks.keys())

        all_segments: list[TranscriptionSegment] = []
        failed_tracks: list[dict[str, str]] = []
        second_opinion_changed = 0
        # Агрегат счётчиков L2 по дорожкам — per-track метаданные иначе теряются
        # при слиянии в общий результат, и запуск L2 был бы невидим при 0 правок.
        second_opinion_stats: dict[str, int] = {}
        total = len(tracks)
        for idx, (name, path) in enumerate(tracks.items()):
            try:
                r = self._transcribe_audio(Path(path), diarization="none", opts=opts)
            except Exception as e:
                # L2: изоляция ошибок по дорожкам — одна битая дорожка не должна валить весь
                # митинг (в авто-ingest нет человека в цикле). Помечаем и продолжаем.
                logger.warning(f"Route A: дорожка '{name}' пропущена ({e!r})")
                failed_tracks.append({"name": name, "path": str(path), "error": type(e).__name__})
                if progress_callback is not None:
                    progress_callback(idx + 1, total, name)
                continue
            # Флаги качества — на сырых per-track сегментах (до слияния таймлайнов).
            self._flag_text_quality(r.segments)
            # L2 per-track (как в прод-репо): кандидаты с латиницей перечитываются whisper'ом
            # по волне ИСХОДНОЙ дорожки; прайминг — глоссарий + имена участников.
            if second_opinion:
                second_opinion_changed += self._apply_second_opinion(
                    r, Path(path), gloss_amap, participants=participants
                )
                for k, v in (r.metadata.get("second_opinion") or {}).items():
                    second_opinion_stats[k] = second_opinion_stats.get(k, 0) + v
            for seg in r.segments:
                seg.speaker = name
            all_segments.extend(r.segments)
            if progress_callback is not None:
                progress_callback(idx + 1, total, name)
        all_segments.sort(key=lambda s: (s.start, s.end))
        merger = SegmentMerger(MergeConfig(max_gap=min_segment_gap))
        all_segments = merger.merge_same_speaker_segments(all_segments, max_gap=min_segment_gap)

        from .versions import pipeline_versions

        result = TranscriptionResult(
            text=" ".join(s.text for s in all_segments),
            segments=all_segments,
            duration=max((s.end for s in all_segments), default=0.0),
            language="ru",
            model_name=self.model_name,
            processing_time=time.time() - start_time,
            metadata={
                "route": "A",
                "tracks": list(tracks.keys()),
                "layer_versions": pipeline_versions(),
            },
        )
        # L2: не-обработанные дорожки → частичный транскрипт + предупреждение в UI.
        if failed_tracks:
            result.metadata["failed_tracks"] = failed_tracks
        if second_opinion_stats:
            result.metadata["second_opinion"] = second_opinion_stats
        if second_opinion_changed:
            result.metadata["second_opinion_changed"] = second_opinion_changed
        # L3: device_fallback на основном пути Route A (зеркало single-file ветки) —
        # иначе GPU→CPU откат на главном сценарии невидим, и пользователь видит «зависание».
        if self._device_fell_back:
            result.metadata["device_fallback"] = self.device
        if glossary:
            try:
                self._apply_glossary(result, gloss_amap, gloss_suffixable)
            except Exception as e:
                logger.warning(f"Глоссарий пропущен (Route A): {e!r}")
        if output_path:
            result.save(output_path, output_format)
        return result

    def _transcribe_audio(
        self,
        audio_path: Path,
        diarization: DiarizationMode = "none",
        preclean_filter: str | None = None,
        opts: DecodeOptions | None = None,
        **diarization_kwargs,
    ) -> TranscriptionResult:
        """Внутренний метод транскрипции аудио."""
        opts = opts or DecodeOptions()
        # Подготовка аудио (конвертация в нужный формат)
        temp_audio = None
        try:
            if preclean_filter:
                # Preclean (#17, opt-in): highpass+loudnorm перед ASR. МЕНЯЕТ вход → меняет
                # текст (НЕ I1-neutral) — строго по флагу, под A/B-сравнение.
                temp_audio = self.audio_processor.normalize(
                    audio_path, audio_filter=preclean_filter
                )
                working_audio = temp_audio
            else:
                # prepare_for_gigaam сам решает: wav 16kHz mono → вернуть как есть,
                # иначе конвертировать во временный wav.
                working_audio = self.audio_processor.prepare_for_gigaam(audio_path)
                if working_audio != audio_path:
                    temp_audio = working_audio

            # Получаем длительность
            duration = self.audio_processor.get_duration(working_audio)

            # Транскрипция
            if opts.backend == "onnx":
                sessions, cfg = self._get_onnx(opts.onnx_int8)
                segments = decode_onnx(sessions, cfg, working_audio, opts.progress_cb)
            elif duration <= self.MAX_SHORT_DURATION:
                segments = decode_short(self.model, working_audio, duration)
            else:
                segments = self._decode_long(working_audio, opts)

            if not segments:
                raise EmptyAudioError(str(audio_path))

            # Диаризация
            if diarization != "none":
                segments = self._apply_diarization(
                    working_audio,
                    segments,
                    mode=diarization,
                    **diarization_kwargs,
                )

            # Формирование результата
            full_text = " ".join(seg.text for seg in segments)

            return TranscriptionResult(
                text=full_text,
                segments=segments,
                duration=duration,
                language="ru",
                model_name=self.model_name,
                processing_time=0,  # Будет обновлено в transcribe()
                metadata={"source": str(audio_path)},
            )

        finally:
            # Удаление временного файла
            if temp_audio and temp_audio != audio_path and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass

    def _transcribe_video(
        self,
        video_path: Path,
        keep_temp_audio: bool = False,
        **kwargs,
    ) -> tuple[TranscriptionResult, Path | None]:
        """Внутренний метод транскрипции видео. Возвращает ``(result, temp_audio)``.

        При ``keep_temp_audio=True`` извлечённый wav НЕ удаляется, а его путь
        возвращается — пост-проходы ``transcribe()`` (voiceprint/second_opinion),
        читающие волну через ``torchaudio.load``, используют его вместо видео-контейнера
        (дефолтный бэкенд torchaudio видео не декодирует). Владение temp передаётся
        вызывающему. Иначе temp удаляется и возвращается ``None``."""
        temp_audio = None
        try:
            # Извлечение аудио
            logger.info(f"Извлечение аудио из видео: {video_path}")
            temp_audio = self.audio_processor.extract_audio_from_video(video_path)

            # Транскрипция извлечённого аудио
            result = self._transcribe_audio(temp_audio, **kwargs)
            result.metadata["source"] = str(video_path)
            result.metadata["source_type"] = "video"

            if keep_temp_audio:
                kept, temp_audio = temp_audio, None  # передаём владение вызывающему
                return result, kept
            return result, None

        finally:
            if temp_audio and temp_audio.exists():
                try:
                    temp_audio.unlink()
                except Exception:
                    pass

    def _decode_long(self, audio_path: Path, opts: DecodeOptions) -> list[TranscriptionSegment]:
        """Longform-декод с оркестрацией fallback'ов.

        Confidence-путь (greedy RNN-T, decode.decode_long_with_confidence) → при GPU-сбое
        перенос модели на CPU и повтор (#14) → при прочих сбоях (или повторном сбое уже
        на CPU) — высокоуровневый ``model.transcribe_longform`` без confidence."""
        logger.debug(f"Транскрипция длинного аудио: {audio_path}")
        onnx_enc = self._get_onnx_encoder() if opts.onnx_encoder else None
        try:
            return decode_long_with_confidence(self.model, audio_path, opts, onnx_enc)
        except (RuntimeError, MemoryError) as e:
            # GPU OOM / сбой ядра на MPS/CUDA → перенести модель на CPU и повторить (#14).
            if self.device in ("mps", "cuda") and self._gpu_to_cpu_fallback(e):
                logger.warning(f"GPU-сбой декода ({e!r}); повтор на CPU")
                try:
                    return decode_long_with_confidence(self.model, audio_path, opts, onnx_enc)
                except Exception as e2:
                    # Вторая ошибка уже на CPU → деградируем на plain-путь, как и при
                    # ошибке без GPU-сбоя (лучше текст без confidence, чем упавшая джоба).
                    logger.warning(
                        f"Confidence-путь упал и на CPU ({e2!r}); fallback на transcribe_longform"
                    )
                    return decode_long_plain(self.model, audio_path)
            logger.warning(f"Confidence-путь упал ({e!r}); fallback на transcribe_longform")
            return decode_long_plain(self.model, audio_path)
        except Exception as e:
            logger.warning(
                f"Per-chunk confidence недоступен ({e!r}); "
                "fallback на model.transcribe_longform без confidence"
            )
            return decode_long_plain(self.model, audio_path)

    def _get_onnx(self, int8: bool):
        """Лениво: экспорт+загрузка ONNX-сессий GigaAM (кэш по int8-флагу). → (sessions, cfg)."""
        if int8 not in self._onnx_sessions:
            from .onnx_backend import ensure_onnx, load_sessions

            onnx_dir, version = ensure_onnx(self.model_name, int8=int8)
            self._onnx_sessions[int8] = load_sessions(onnx_dir, version, "cpu")
        return self._onnx_sessions[int8]

    def _get_onnx_encoder(self):
        """Лениво: ONNX-энкодер split-device (encoder ORT-CPU + torch RNN-T голова → сохраняет
        confidence). None при ЛЮБОМ сбое → откат на torch model.forward (пробуем один раз);
        не бросает — иначе опциональное ускорение роняло бы джобу целиком."""
        if not self._onnx_enc_tried:
            self._onnx_enc_tried = True
            try:
                from .onnx_encoder import load_onnx_encoder

                self._onnx_enc = load_onnx_encoder(self.model, self.model.cfg.model_name)
            except Exception as e:
                logger.warning(f"ONNX-энкодер недоступен ({e!r}); torch-энкодер")
                self._onnx_enc = None
        return self._onnx_enc

    def _apply_diarization(
        self,
        audio_path: Path,
        segments: list[TranscriptionSegment],
        mode: DiarizationMode,
        **kwargs,
    ) -> list[TranscriptionSegment]:
        """Применение диаризации к сегментам."""
        logger.info(f"Применение диаризации: mode={mode}")

        try:
            if mode == "pyannote":
                speaker_segments = self.diarization_manager.diarize(audio_path, **kwargs)
            elif mode == "hybrid":
                # Для гибридного режима используем VAD сегменты
                from .diarization import HybridDiarization

                hybrid = HybridDiarization(
                    hf_token=self.hf_token,
                    device=self.device,
                )
                speech_segments = [(s.start, s.end) for s in segments]
                speaker_segments = hybrid.diarize(
                    audio_path,
                    speech_segments,
                    num_speakers=kwargs.get("num_speakers"),
                )
            else:
                return segments

            # Сопоставление спикеров с транскрипцией
            segments = self.diarization_manager.map_speakers_to_transcription(
                segments,
                speaker_segments,
            )

            return segments

        except HFTokenMissingError:
            warnings.warn("HF_TOKEN не установлен, диаризация пропущена.", stacklevel=2)
            return segments
        except Exception as e:
            logger.error(f"Ошибка диаризации: {e}")
            warnings.warn(f"Ошибка диаризации, продолжаем без неё: {e}", stacklevel=2)
            return segments

    def transcribe_batch(
        self,
        input_paths: list[str | Path],
        output_dir: str | Path | None = None,
        diarization: DiarizationMode = "none",
        progress_callback: Callable[[int, int, str], None] | None = None,
        **kwargs,
    ) -> list[TranscriptionResult]:
        """Пакетная обработка файлов — последовательно (GPU не параллелится).

        progress_callback(current, total, filename) зовётся после каждого файла;
        ошибка одного файла не прерывает пакет (в результат кладётся пустой
        TranscriptionResult с metadata['error'])."""
        results = []
        total = len(input_paths)

        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        # Последовательная обработка (GPU не параллелится)
        for i, input_path in enumerate(input_paths):
            input_path = Path(input_path)
            logger.info(f"Обработка {i+1}/{total}: {input_path.name}")

            # Определение выходного пути (расширение по формату, не хардкод .txt)
            output_path = None
            if output_dir:
                ext = kwargs.get("output_format", "txt")
                output_path = output_dir / f"{input_path.stem}.{ext}"

            try:
                result = self.transcribe(
                    input_path,
                    output_path=output_path,
                    diarization=diarization,
                    **kwargs,
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Ошибка при обработке {input_path}: {e}")
                # Создаём пустой результат с ошибкой
                results.append(
                    TranscriptionResult(
                        text="",
                        segments=[],
                        duration=0,
                        language="ru",
                        model_name=self.model_name,
                        processing_time=0,
                        metadata={"source": str(input_path), "error": str(e)},
                    )
                )

            # После файла (не до): current = число завершённых, как в transcribe_route_a —
            # единый контракт для прогресс-адаптеров обёрток.
            if progress_callback:
                progress_callback(i + 1, total, input_path.name)

        return results

    # =========================================================================
    # Вспомогательные методы
    # =========================================================================

    def get_model_info(self) -> dict[str, Any]:
        """Получить информацию о модели."""
        return {
            "model_name": self.model_name,
            "device": self.device,
            "loaded": self._model is not None,
            "hf_token_set": self.hf_token is not None,
            "cache_dir": str(self.cache_dir),
        }

    def preload(self) -> None:
        """Предзагрузка модели для ускорения первого запроса (тёплый старт воркера).

        Фиксирует ``_intended_device`` — устройство, на которое L1-репарация вернёт
        модель на границе джобы после возможного GPU→CPU fallback."""
        self._intended_device = self.device
        _ = self.model
        logger.info("Модель предзагружена")
