"""Сшивка сегментов: объединение смежных реплик одного спикера.

Общий шов пайплайна — используется и transcribe(), и transcribe_route_a().
При слиянии не теряются метаданные качества: words конкатенируются,
confidence усредняется, speaker_confidence — взвешенно по длительности,
flags объединяются, provenance берёт более «обработанный» (merge_provenance).
"""

import logging
from dataclasses import dataclass, replace

from .data_models import TranscriptionSegment, merge_provenance

logger = logging.getLogger(__name__)


@dataclass
class MergeConfig:
    """Конфигурация сшивки сегментов."""

    # Максимальная пауза между репликами одного спикера для объединения (секунды)
    max_gap: float = 1.0

    # Максимальная длительность объединённого сегмента (секунды)
    max_merged_duration: float = 60.0


class SegmentMerger:
    """Сшивка последовательных сегментов одного спикера."""

    def __init__(self, config: MergeConfig | None = None):
        self.config = config or MergeConfig()

    def merge_same_speaker_segments(
        self,
        segments: list[TranscriptionSegment],
        max_gap: float | None = None,
    ) -> list[TranscriptionSegment]:
        """Объединить последовательные сегменты одного спикера (gap <= max_gap).

        Пример: [0-5] Спикер 1 «Привет» + [5-8] Спикер 1 «как дела»
        → [0-8] Спикер 1 «Привет как дела»."""
        if not segments:
            return []

        max_gap = max_gap if max_gap is not None else self.config.max_gap

        merged = []
        current = None

        for seg in segments:
            if current is None:
                # Первый сегмент. replace() копирует ВСЕ поля (включая
                # speaker_confidence и provenance/flags) — без потери при сшивке.
                current = replace(
                    seg,
                    words=list(seg.words) if seg.words else None,
                    flags=list(seg.flags),
                )
            elif self._should_merge(current, seg, max_gap):
                current = self._merge_two_segments(current, seg)
            else:
                merged.append(current)
                current = replace(
                    seg,
                    words=list(seg.words) if seg.words else None,
                    flags=list(seg.flags),
                )

        if current:
            merged.append(current)

        return merged

    def _should_merge(
        self,
        current: TranscriptionSegment,
        next_seg: TranscriptionSegment,
        max_gap: float,
    ) -> bool:
        """Определить, нужно ли объединять сегменты."""
        if current.speaker != next_seg.speaker:
            return False

        gap = next_seg.start - current.end
        if gap > max_gap:
            return False

        merged_duration = next_seg.end - current.start
        if merged_duration > self.config.max_merged_duration:
            return False

        return True

    def _merge_two_segments(
        self,
        seg1: TranscriptionSegment,
        seg2: TranscriptionSegment,
    ) -> TranscriptionSegment:
        """Объединить два сегмента в один."""
        text = seg1.text.rstrip() + " " + seg2.text.lstrip()

        words = None
        if seg1.words and seg2.words:
            words = list(seg1.words) + list(seg2.words)
        elif seg1.words:
            words = list(seg1.words)
        elif seg2.words:
            words = list(seg2.words)

        confidence = None
        if seg1.confidence is not None and seg2.confidence is not None:
            confidence = (seg1.confidence + seg2.confidence) / 2
        elif seg1.confidence is not None:
            confidence = seg1.confidence
        elif seg2.confidence is not None:
            confidence = seg2.confidence

        # speaker_confidence — взвешенное по длительности среднее (precision-first сигнал)
        speaker_confidence = self._merge_speaker_confidence(seg1, seg2)

        # provenance — побеждает более «обработанный»; flags — объединение (без потери при сшивке)
        flags = sorted(set(seg1.flags) | set(seg2.flags))
        provenance = merge_provenance(seg1.provenance, seg2.provenance)

        # replace на seg1 сохраняет start/speaker, остальные поля задаём явно
        return replace(
            seg1,
            text=text,
            end=seg2.end,
            words=words,
            confidence=confidence,
            speaker_confidence=speaker_confidence,
            provenance=provenance,
            flags=flags,
        )

    @staticmethod
    def _merge_speaker_confidence(
        seg1: TranscriptionSegment,
        seg2: TranscriptionSegment,
    ) -> float | None:
        """Взвешенное по длительности среднее speaker_confidence двух сегментов."""
        c1, c2 = seg1.speaker_confidence, seg2.speaker_confidence
        if c1 is None and c2 is None:
            return None
        if c1 is None:
            return c2
        if c2 is None:
            return c1
        d1 = max(seg1.duration, 1e-9)
        d2 = max(seg2.duration, 1e-9)
        return (c1 * d1 + c2 * d2) / (d1 + d2)
