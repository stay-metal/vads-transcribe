"""
Тесты полей provenance/flags на TranscriptionSegment (инкремент 2 переноса из custom).

Поля аддитивны и инертны (дефолт provenance="gigaam", flags=[]) — их проставляют
будущие пост-проходы (глоссарий, L2, voiceprint, детектор галлюцинаций). Проверяем
дефолты, сериализацию (опускаем дефолтные), round-trip и корректную протяжку через сшивку.
"""

from gigaam_transcriber.data_models import (
    DEFAULT_PROVENANCE,
    TranscriptionSegment,
    merge_provenance,
)
from gigaam_transcriber.segment_merger import MergeConfig, SegmentMerger


def test_defaults():
    s = TranscriptionSegment(text="x", start=0, end=1)
    assert s.provenance == DEFAULT_PROVENANCE == "gigaam"
    assert s.flags == []


def test_to_dict_omits_default_provenance_and_empty_flags():
    d = TranscriptionSegment(text="x", start=0, end=1).to_dict()
    assert "provenance" not in d
    assert "flags" not in d


def test_to_dict_includes_nondefault():
    s = TranscriptionSegment(
        text="x", start=0, end=1, provenance="second-opinion", flags=["hallucination_suspect"]
    )
    d = s.to_dict()
    assert d["provenance"] == "second-opinion"
    assert d["flags"] == ["hallucination_suspect"]


def test_from_dict_roundtrip():
    s = TranscriptionSegment(text="x", start=0, end=1, provenance="voiceprint", flags=["a", "b"])
    s2 = TranscriptionSegment.from_dict(s.to_dict())
    assert s2.provenance == "voiceprint"
    assert s2.flags == ["a", "b"]


def test_from_dict_defaults_when_absent():
    s2 = TranscriptionSegment.from_dict({"text": "x", "start": 0, "end": 1})
    assert s2.provenance == "gigaam"
    assert s2.flags == []


def test_merge_provenance_precedence():
    assert merge_provenance("gigaam", "second-opinion") == "second-opinion"
    assert merge_provenance("human", "gigaam") == "human"
    assert merge_provenance("gigaam", "gigaam") == "gigaam"
    assert merge_provenance("voiceprint", "glossary") == "voiceprint"


def test_merge_unions_flags_and_picks_provenance():
    s1 = TranscriptionSegment(text="а", start=0, end=4, speaker="A", flags=["x"], provenance="gigaam")
    s2 = TranscriptionSegment(
        text="б", start=4, end=6, speaker="A", flags=["y"], provenance="second-opinion"
    )
    out = SegmentMerger(MergeConfig(max_gap=1.0)).merge_same_speaker_segments([s1, s2])
    assert len(out) == 1
    assert set(out[0].flags) == {"x", "y"}
    assert out[0].provenance == "second-opinion"


def test_merge_copy_does_not_alias_flags():
    # Один сегмент проходит через копирующую ветку — его flags не должны делить
    # ссылку с исходным объектом.
    s1 = TranscriptionSegment(text="а", start=0, end=4, speaker="A", flags=["x"])
    out = SegmentMerger(MergeConfig()).merge_same_speaker_segments([s1])
    out[0].flags.append("y")
    assert s1.flags == ["x"]
