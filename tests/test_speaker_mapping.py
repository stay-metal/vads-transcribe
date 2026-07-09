"""
Тесты overlap-primary маппинга спикер↔сегмент (инкремент 1 переноса из custom).

Покрывают сценарии из ресёрча по speaker-assignment: сегмент на стыке turn'ов,
midpoint в короткой вставке соседа, суммирование overlap по спикеру, nearest-фолбэк,
speaker_confidence, неприкосновенность текста (I1), протяжка confidence через сшивку.
"""

from gigaam_transcriber.data_models import SpeakerSegment, TranscriptionSegment
from gigaam_transcriber.segment_merger import MergeConfig, SegmentMerger
from gigaam_transcriber.speaker_mapping import assign_speakers_by_overlap


def _seg(text, start, end):
    return TranscriptionSegment(text=text, start=start, end=end)


def _sp(speaker, start, end):
    return SpeakerSegment(start=start, end=end, speaker=speaker)


def test_overlap_dominant_wins_over_midpoint():
    # Середина сегмента (5.0) попадает в короткую вставку B (4.5..5.5),
    # но доминирует A (0..4.5 + 5.5..10 = 9с). Прежний midpoint-first дал бы B.
    seg = _seg("привет коллеги как дела", 0, 10)
    speakers = [_sp("A", 0, 4.5), _sp("B", 4.5, 5.5), _sp("A", 5.5, 10)]
    assign_speakers_by_overlap([seg], speakers)
    assert seg.speaker == "A"
    assert abs(seg.speaker_confidence - 0.9) < 1e-6  # 9/10


def test_summed_overlap_beats_single_long_turn():
    # A раздроблён на два turn'а (3+3=6с), B один turn (5с). Победить должен A.
    # Прежняя overlap-ветка (макс по одному turn'у) ошибочно выбрала бы B.
    seg = _seg("текст", 0, 11)
    speakers = [_sp("A", 0, 3), _sp("B", 3, 8), _sp("A", 8, 11)]
    assign_speakers_by_overlap([seg], speakers)
    assert seg.speaker == "A"


def test_no_overlap_nearest_fallback():
    seg = _seg("реплика", 100, 102)
    speakers = [_sp("A", 0, 10), _sp("B", 50, 60)]
    assign_speakers_by_overlap([seg], speakers, fill_nearest=True)
    assert seg.speaker == "B"  # ближайший по середине
    assert seg.speaker_confidence == 0.0


def test_no_overlap_no_fill_gives_none():
    seg = _seg("реплика", 100, 102)
    speakers = [_sp("A", 0, 10)]
    assign_speakers_by_overlap([seg], speakers, fill_nearest=False)
    assert seg.speaker is None
    assert seg.speaker_confidence == 0.0


def test_speaker_confidence_partial_coverage():
    seg = _seg("текст", 0, 10)
    speakers = [_sp("A", 0, 4)]  # покрывает 4/10
    assign_speakers_by_overlap([seg], speakers)
    assert seg.speaker == "A"
    assert abs(seg.speaker_confidence - 0.4) < 1e-6


def test_empty_speakers_noop():
    seg = _seg("текст", 0, 10)
    assign_speakers_by_overlap([seg], [])
    assert seg.speaker is None
    assert seg.speaker_confidence is None


def test_text_not_mutated_invariant_i1():
    seg = _seg("кириллица verbatim 123", 0, 10)
    assign_speakers_by_overlap([seg], [_sp("A", 0, 10)])
    assert seg.text == "кириллица verbatim 123"


def test_merge_threads_speaker_confidence():
    # Взвешенное по длительности: (1.0*4 + 0.4*2)/6 = 4.8/6 = 0.8
    s1 = TranscriptionSegment(text="а", start=0, end=4, speaker="A", speaker_confidence=1.0)
    s2 = TranscriptionSegment(text="б", start=4, end=6, speaker="A", speaker_confidence=0.4)
    merger = SegmentMerger(MergeConfig(max_gap=1.0))
    out = merger.merge_same_speaker_segments([s1, s2])
    assert len(out) == 1
    assert abs(out[0].speaker_confidence - 0.8) < 1e-6
    assert out[0].text == "а б"


def test_merge_preserves_single_segment_confidence():
    s1 = TranscriptionSegment(text="а", start=0, end=4, speaker="A", speaker_confidence=0.7)
    out = SegmentMerger(MergeConfig()).merge_same_speaker_segments([s1])
    assert out[0].speaker_confidence == 0.7
