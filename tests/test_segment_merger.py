"""
Тесты для модуля segment_merger.
"""

from gigaam_transcriber import (
    MergeConfig,
    SegmentMerger,
    TranscriptionSegment,
)


class TestSegmentMerger:
    """Тесты для SegmentMerger."""

    def test_merge_same_speaker_segments(self):
        """Тест объединения сегментов одного спикера."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="как дела", start=1.0, end=2.0, speaker="Спикер №1"),
            TranscriptionSegment(text="Отлично", start=2.0, end=3.0, speaker="Спикер №2"),
        ]

        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)

        assert len(merged) == 2
        assert merged[0].text == "Привет как дела"
        assert merged[0].speaker == "Спикер №1"
        assert merged[0].start == 0.0
        assert merged[0].end == 2.0
        assert merged[1].speaker == "Спикер №2"

    def test_merge_with_gap(self):
        """Тест объединения с учётом gap."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(
                text="как дела", start=1.5, end=2.5, speaker="Спикер №1"
            ),  # gap = 0.5
            TranscriptionSegment(
                text="хорошо", start=5.0, end=6.0, speaker="Спикер №1"
            ),  # gap = 2.5
        ]

        # С max_gap=1.0 первые два должны объединиться
        merger = SegmentMerger(MergeConfig(max_gap=1.0))
        merged = merger.merge_same_speaker_segments(segments)

        assert len(merged) == 2
        assert merged[0].text == "Привет как дела"
        assert merged[1].text == "хорошо"

    def test_no_merge_different_speakers(self):
        """Тест: не объединяем разных спикеров."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
            TranscriptionSegment(text="Привет", start=1.0, end=2.0, speaker="Спикер №2"),
        ]

        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)

        assert len(merged) == 2

    def test_empty_segments(self):
        """Тест с пустым списком сегментов."""
        merger = SegmentMerger()

        assert merger.merge_same_speaker_segments([]) == []

    def test_single_segment(self):
        """Тест с одним сегментом."""
        segments = [
            TranscriptionSegment(text="Привет", start=0.0, end=1.0, speaker="Спикер №1"),
        ]

        merger = SegmentMerger()
        merged = merger.merge_same_speaker_segments(segments)

        assert len(merged) == 1
        assert merged[0].text == "Привет"
