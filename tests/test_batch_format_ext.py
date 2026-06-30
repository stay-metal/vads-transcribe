"""Fix C — transcribe_batch пишет выходной файл с расширением по output_format.

Раньше расширение было захардкожено `.txt`, из-за чего `-f srt` писал SRT-контент
в файл `<stem>.txt`. ASR-модель не грузится: `_transcribe_audio` подменяется.
"""

import pytest

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment


def _fake_result():
    return TranscriptionResult(
        text="привет",
        segments=[TranscriptionSegment(text="привет", start=0.0, end=1.0)],
        duration=1.0,
        language="ru",
        model_name="fake",
        processing_time=0.0,
    )


@pytest.mark.parametrize("fmt", ["txt", "json", "srt", "vtt"])
def test_batch_writes_extension_matching_format(monkeypatch, tmp_path, fmt):
    t = GigaAMTranscriber(device="cpu")
    monkeypatch.setattr(t, "_validate_input", lambda p: None)
    monkeypatch.setattr(
        t, "_transcribe_audio", lambda audio_path, diarization="none", **kw: _fake_result()
    )
    f = tmp_path / "rec.wav"
    f.write_bytes(b"\x00")
    out = tmp_path / "out"

    t.transcribe_batch([f], output_dir=out, output_format=fmt, glossary=False)

    # основной выходной файл имеет расширение по формату (а не захардкоженный .txt)
    assert (out / f"rec.{fmt}").exists(), f"ожидался rec.{fmt}"
    if fmt != "txt":
        assert not (out / "rec.txt").exists(), "не должно быть rec.txt при -f " + fmt
