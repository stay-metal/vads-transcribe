"""Тесты manifest/resume (инкремент 16)."""

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.manifest import load_manifest, resume_result, write_manifest


def _result():
    return TranscriptionResult(
        text="привет мир",
        segments=[
            TranscriptionSegment(
                text="привет мир",
                start=0,
                end=1,
                speaker="S",
                confidence=0.9,
                provenance="glossary",
                flags=["x"],
            ),
        ],
        duration=1.0,
        language="ru",
        model_name="v3_e2e_rnnt",
        processing_time=2.0,
        metadata={"source": "a.wav"},
    )


def test_roundtrip_and_resume(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fakeaudio-bytes")
    mp = tmp_path / "a.json.manifest.json"
    write_manifest(_result(), audio, mp)
    assert load_manifest(mp)["complete"] is True
    res = resume_result(mp, audio)
    assert res is not None
    assert res.text == "привет мир"
    assert res.metadata["resumed"] is True
    assert res.segments[0].confidence == 0.9
    assert res.segments[0].provenance == "glossary"
    assert res.segments[0].flags == ["x"]


def test_resume_rejects_changed_file(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"version-1")
    mp = tmp_path / "m.json"
    write_manifest(_result(), audio, mp)
    audio.write_bytes(b"version-2-changed")  # файл изменился → hash не совпадёт
    assert resume_result(mp, audio) is None


def test_resume_missing_manifest(tmp_path):
    assert resume_result(tmp_path / "nope.json", tmp_path / "a.wav") is None
