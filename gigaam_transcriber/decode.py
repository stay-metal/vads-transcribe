"""Декод-бэкенды GigaAM: короткий, longform (torch, с confidence) и полнографовый ONNX.

Чистые функции над загруженной моделью — перенесены из transcriber.py без изменения
ML-логики (текст argmax-идентичен, I1). Оркестрация (выбор бэкенда, GPU→CPU fallback,
диаризация, пост-проходы) остаётся в GigaAMTranscriber.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .data_models import TranscriptionSegment, WordSegment
from .exceptions import AudioProcessingError

logger = logging.getLogger(__name__)

BATCH_SIZE = 16


@dataclass
class DecodeOptions:
    """Пер-джобовые опции декода.

    Передаются явно по цепочке transcribe → _transcribe_audio → decode_* —
    вместо мутабельных атрибутов инстанса (stale-состояние тёплого singleton
    между джобами было источником багов)."""

    # "torch" (дефолт, даёт per-chunk confidence) | "onnx" (CPU/CUDA, int8-ускорение,
    # БЕЗ confidence — ONNX argmax не отдаёт logprob; текст argmax-идентичен torch).
    backend: str = "torch"
    onnx_int8: bool = False
    # split-device: Conformer в ORT-CPU, RNN-T голова в torch (confidence сохраняется).
    onnx_encoder: bool = False
    word_timestamps: bool = False
    # Прогресс по VAD-сегментам (opt-in): cb(current, total). Только сайд-эффект —
    # текст/argmax/декод не трогает (I1).
    progress_cb: Callable[[int, int], None] | None = None


def _tick(cb: Callable[[int, int], None] | None, current: int, total: int) -> None:
    if cb is None:
        return
    try:
        cb(current, total)
    except Exception:
        pass


def decode_short(model, audio_path: Path, duration: float) -> list[TranscriptionSegment]:
    """Короткое аудио (≤25с): один вызов model.transcribe → один сегмент."""
    result = model.transcribe(str(audio_path))
    # Совместимость API GigaAM: main → TranscriptionResult(.text); 0.1.0 → str
    text = result.text if hasattr(result, "text") else result

    if not text or not text.strip():
        return []

    return [TranscriptionSegment(text=text.strip(), start=0.0, end=duration)]


def decode_long_with_confidence(
    model, audio_path: Path, opts: DecodeOptions, onnx_encoder=None
) -> list[TranscriptionSegment]:
    """Низкоуровневый longform-декод с per-chunk confidence (greedy RNN-T).

    Воспроизводит ``model.transcribe_longform`` (тот же ``segment_audio_file`` +
    ``forward`` + greedy-декод), но через ``decode_with_confidence``: текст
    **бит-в-бит** идентичен (argmax по log-softmax == argmax по логитам, I1),
    дополнительно — ``confidence`` на каждый чанк. Требует GigaAM main API.
    ``onnx_encoder`` (опц.) — загруженный split-device энкодер вместо model.forward."""
    import torch
    from gigaam.preprocess import SAMPLE_RATE
    from gigaam.utils import AudioDataset
    from gigaam.vad_utils import segment_audio_file
    from torch.utils.data import DataLoader

    from .confidence import decode_with_confidence

    seg_audios, boundaries = segment_audio_file(str(audio_path), SAMPLE_RATE, device=model._device)
    if not seg_audios:
        return []

    ds = AudioDataset(seg_audios, tokenizer=None)
    dl = DataLoader(
        ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
        collate_fn=AudioDataset.collate,
        num_workers=0,
    )

    segments: list[TranscriptionSegment] = []
    idx = 0
    with torch.inference_mode():
        for wav_pad, wav_lens in dl:
            wav_pad = wav_pad.to(model._device).to(model._dtype)
            wav_lens = wav_lens.to(model._device)
            if onnx_encoder is not None:
                encoded, encoded_len = onnx_encoder(model, wav_pad, wav_lens)
            else:
                encoded, encoded_len = model.forward(wav_pad, wav_lens)
            for text, conf, words in decode_with_confidence(
                model, encoded, encoded_len, wav_lens, word_timestamps=opts.word_timestamps
            ):
                seg_start, seg_end = boundaries[idx]
                idx += 1
                _tick(opts.progress_cb, idx, len(boundaries))
                if text and text.strip():
                    word_segs = None
                    if words:
                        # Времена слов — относительно начала чанка → глобализуем (+seg_start).
                        word_segs = [
                            WordSegment(
                                word=w.text,
                                start=round(w.start + seg_start, 3),
                                end=round(w.end + seg_start, 3),
                            )
                            for w in words  # type: ignore[attr-defined]
                        ]
                    segments.append(
                        TranscriptionSegment(
                            text=text.strip(),
                            start=seg_start,
                            end=seg_end,
                            confidence=conf,
                            words=word_segs,
                        )
                    )
    return segments


def decode_long_plain(model, audio_path: Path) -> list[TranscriptionSegment]:
    """Высокоуровневый longform без confidence (fallback / GigaAM 0.1.0)."""
    try:
        result = model.transcribe_longform(str(audio_path))
    except Exception as e:
        logger.error(f"Ошибка transcribe_longform: {e}")
        raise AudioProcessingError(
            f"Ошибка при транскрипции длинного файла: {e}",
            file_path=str(audio_path),
            cause=e,
        )

    # Совместимость API GigaAM:
    #   main  → LongformTranscriptionResult(.segments[].text/.start/.end)
    #   0.1.0 → List[dict] с ключами 'transcription'/'boundaries'
    utterances = getattr(result, "segments", result)

    segments = []
    for utt in utterances:
        if hasattr(utt, "text"):  # новый Segment (GigaAM main)
            text = utt.text
            start, end = utt.start, utt.end
        else:  # старый dict-формат (GigaAM 0.1.0)
            text = utt["transcription"]
            start, end = utt["boundaries"]

        if text and text.strip():
            segments.append(TranscriptionSegment(text=text.strip(), start=start, end=end))

    return segments


def decode_onnx(
    sessions, cfg, audio_path: Path, progress_cb: Callable[[int, int], None] | None = None
) -> list[TranscriptionSegment]:
    """ONNX-декод (#13): segment_audio_file + infer_onnx. БЕЗ per-chunk confidence
    (ONNX argmax не отдаёт logprob — для confidence используйте backend='torch').
    Текст argmax-идентичен torch; int8 ускоряет на CPU-сервере."""
    from gigaam.onnx_utils import infer_onnx
    from gigaam.preprocess import SAMPLE_RATE
    from gigaam.vad_utils import segment_audio_file

    seg_audios, boundaries = segment_audio_file(str(audio_path), SAMPLE_RATE)
    if not seg_audios:
        return []
    segments: list[TranscriptionSegment] = []
    idx = 0
    for i in range(0, len(seg_audios), BATCH_SIZE):
        chunk = seg_audios[i : i + BATCH_SIZE]
        texts = infer_onnx(chunk, cfg, sessions, batch_size=len(chunk), progress=False)
        for text in texts:
            seg_start, seg_end = boundaries[idx]
            idx += 1
            _tick(progress_cb, idx, len(boundaries))
            if text and str(text).strip():
                segments.append(
                    TranscriptionSegment(text=str(text).strip(), start=seg_start, end=seg_end)
                )
    return segments
