"""
Сопоставление спикеров с сегментами транскрипции (overlap-primary).

Чистый модуль: зависит только от ``data_models`` (без torch/pyannote), поэтому
юнит-тестируется автономно (без тяжёлого окружения).

Перенесено из custom (zoom_transcriber/transcribe.py: apply_diarization, max-overlap)
и согласовано с best practice (WhisperX ``assign_word_speakers``, pyannoteAI
ASR+diarization tutorial): основной критерий атрибуции — МАКСИМАЛЬНАЯ СУММАРНАЯ
доля пересечения интервала реплики с turn'ами спикера, а не спикер в середине
сегмента (midpoint). Прежний midpoint-first в DialogScribe был субоптимален:
если середина длинного сегмента попадала в короткую вставку соседа, весь сегмент
уходил не тому спикеру, и overlap-ветка (срабатывавшая только при ``speaker is None``)
этого не исправляла.
"""

from .data_models import SpeakerSegment, TranscriptionSegment

# Порог «слабой» атрибуции спикера: ниже него метка считается неуверенной
# (precision-first сигнал; зеркалит DIAR_WEAK_CONFIDENCE из custom).
DIAR_WEAK_CONFIDENCE = 0.5


def assign_speakers_by_overlap(
    transcription_segments: list[TranscriptionSegment],
    speaker_segments: list[SpeakerSegment],
    fill_nearest: bool = True,
) -> list[TranscriptionSegment]:
    """
    Назначить каждому сегменту транскрипции спикера по максимальному СУММАРНОМУ
    пересечению с turn'ами диаризации.

    Для каждого сегмента:
      1. Суммируем длину пересечения по каждому спикеру (одного спикера могут
         представлять несколько turn'ов — суммируем по спикеру, затем argmax).
      2. ``speaker_confidence`` = доля сегмента, покрытая победившим спикером
         (sum_overlap / duration), в диапазоне [0, 1].
      3. Если пересечения нет ни с кем:
         - ``fill_nearest=True`` → ближайший по середине turn, confidence = 0.0;
         - иначе ``speaker = None``.

    Сегменты мутируются на месте (как и прежняя реализация) и возвращаются.
    Текст (инвариант I1) не трогается — меняются только метка спикера и
    ``speaker_confidence``.
    """
    if not speaker_segments:
        return transcription_segments

    for seg in transcription_segments:
        duration = seg.end - seg.start
        if duration <= 0:
            duration = 1e-9

        overlap_by_speaker: dict[str, float] = {}
        for sp in speaker_segments:
            overlap = min(seg.end, sp.end) - max(seg.start, sp.start)
            if overlap > 0:
                overlap_by_speaker[sp.speaker] = overlap_by_speaker.get(sp.speaker, 0.0) + overlap

        if overlap_by_speaker:
            best_speaker = max(overlap_by_speaker, key=overlap_by_speaker.get)
            seg.speaker = best_speaker
            seg.speaker_confidence = min(overlap_by_speaker[best_speaker] / duration, 1.0)
        elif fill_nearest:
            seg_mid = (seg.start + seg.end) / 2
            nearest = min(
                speaker_segments,
                key=lambda sp: abs((sp.start + sp.end) / 2 - seg_mid),
            )
            seg.speaker = nearest.speaker
            seg.speaker_confidence = 0.0
        else:
            seg.speaker = None
            seg.speaker_confidence = 0.0

    return transcription_segments


def is_weak_speaker(
    seg: TranscriptionSegment,
    threshold: float = DIAR_WEAK_CONFIDENCE,
) -> bool:
    """Слабая ли атрибуция спикера (для опционального маркера '?')."""
    return seg.speaker_confidence is not None and seg.speaker_confidence < threshold
