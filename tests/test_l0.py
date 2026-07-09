"""Тесты L0 evidence-субстрата (инкремент 10 переноса из custom)."""

import json

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.l0 import build_l0, l0_sha256, write_l0


def _result(segs, source="/path/Дейли.m4a"):
    return TranscriptionResult(
        text=" ".join(s.text for s in segs),
        segments=segs,
        duration=10,
        language="ru",
        model_name="v3_e2e_rnnt",
        processing_time=1.0,
        metadata={"source": source},
    )


def test_build_l0_fields_and_meeting_from_source():
    r = _result(
        [
            TranscriptionSegment(
                text="привет",
                start=0,
                end=1,
                speaker="Спикер №1",
                confidence=0.9,
                speaker_confidence=0.8,
                provenance="glossary",
                flags=["x"],
            )
        ]
    )
    recs = build_l0(r)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["meeting"] == "Дейли"
    assert rec["text"] == "привет"
    assert rec["confidence"] == 0.9  # акустический приоритетнее speaker_confidence
    assert rec["speaker_confidence"] == 0.8
    assert rec["provenance"] == "glossary"
    assert rec["flags"] == ["x"]
    assert rec["id"].startswith("Дейли:0.000:")


def test_confidence_fallback_to_speaker_confidence():
    r = _result(
        [TranscriptionSegment(text="а", start=0, end=1, speaker="S", speaker_confidence=0.6)]
    )
    assert build_l0(r)[0]["confidence"] == 0.6


def test_skip_empty_text():
    r = _result(
        [
            TranscriptionSegment(text="  ", start=0, end=1),
            TranscriptionSegment(text="есть", start=1, end=2),
        ]
    )
    assert len(build_l0(r)) == 1


def test_ordinal_disambiguates_same_start():
    r = _result(
        [
            TranscriptionSegment(text="а", start=0, end=1, speaker="S"),
            TranscriptionSegment(text="б", start=0, end=1, speaker="S"),
        ]
    )
    recs = build_l0(r)
    assert recs[0]["id"] != recs[1]["id"]  # ordinal различает


def test_sha256_deterministic_and_sensitive():
    r1 = _result([TranscriptionSegment(text="привет", start=0, end=1)])
    r2 = _result([TranscriptionSegment(text="привет", start=0, end=1)])
    r3 = _result([TranscriptionSegment(text="пока", start=0, end=1)])
    assert l0_sha256(build_l0(r1)) == l0_sha256(build_l0(r2))
    assert l0_sha256(build_l0(r1)) != l0_sha256(build_l0(r3))


def test_build_l0_preserves_text_i1():
    r = _result([TranscriptionSegment(text="кириллица verbatim", start=0, end=1, speaker="S")])
    recs = build_l0(r)
    assert recs[0]["text"] == "кириллица verbatim" and recs[0]["speaker"] == "S"


def test_write_l0_jsonl_and_sidecar(tmp_path):
    r = _result([TranscriptionSegment(text="привет", start=0, end=1, speaker="S", confidence=0.9)])
    recs = build_l0(r)
    out = write_l0(recs, tmp_path / "t.v1.jsonl")
    assert out.exists()
    sidecar = out.with_name(out.name + ".sha256")
    assert sidecar.exists()
    assert sidecar.read_text().strip() == l0_sha256(recs)
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0]["text"] == "привет"
