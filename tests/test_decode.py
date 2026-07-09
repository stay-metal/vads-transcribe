"""Тесты декод-бэкендов decode.py (перенос из transcriber) — без загрузки модели.

Покрывают конвертацию ответов GigaAM в TranscriptionSegment для обоих API
(main-объекты и легаси dict/str) — то, что longform-регрессия сломала бы первой.
decode_long_with_confidence/decode_onnx тянут gigaam внутрь функции и покрываются
модельными прогонами (requires_model) и e2e-смоуком."""

import pytest

from gigaam_transcriber.decode import DecodeOptions, decode_long_plain, decode_short
from gigaam_transcriber.exceptions import AudioProcessingError


class _NewApiResult:
    def __init__(self, text):
        self.text = text


class _Seg:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class _LongformResult:
    def __init__(self, segments):
        self.segments = segments


class _FakeModel:
    def __init__(self, transcribe_ret=None, longform_ret=None, longform_exc=None):
        self._transcribe_ret = transcribe_ret
        self._longform_ret = longform_ret
        self._longform_exc = longform_exc

    def transcribe(self, path):
        return self._transcribe_ret

    def transcribe_longform(self, path):
        if self._longform_exc:
            raise self._longform_exc
        return self._longform_ret


def test_decode_short_new_api_object(tmp_path):
    model = _FakeModel(transcribe_ret=_NewApiResult(" привет мир "))
    segs = decode_short(model, tmp_path / "a.wav", duration=3.5)
    assert len(segs) == 1
    assert segs[0].text == "привет мир"  # strip, кириллица verbatim
    assert (segs[0].start, segs[0].end) == (0.0, 3.5)


def test_decode_short_legacy_str_api(tmp_path):
    model = _FakeModel(transcribe_ret="текст строкой")
    segs = decode_short(model, tmp_path / "a.wav", duration=1.0)
    assert segs[0].text == "текст строкой"


def test_decode_short_empty_gives_no_segments(tmp_path):
    model = _FakeModel(transcribe_ret=_NewApiResult("   "))
    assert decode_short(model, tmp_path / "a.wav", duration=1.0) == []


def test_decode_long_plain_new_api(tmp_path):
    model = _FakeModel(
        longform_ret=_LongformResult(
            [_Seg("первый", 0.0, 5.0), _Seg("  ", 5.0, 6.0), _Seg("второй", 6.0, 10.0)]
        )
    )
    segs = decode_long_plain(model, tmp_path / "a.wav")
    assert [(s.text, s.start, s.end) for s in segs] == [
        ("первый", 0.0, 5.0),
        ("второй", 6.0, 10.0),  # пустой сегмент выброшен
    ]


def test_decode_long_plain_legacy_dict_api(tmp_path):
    model = _FakeModel(
        longform_ret=[
            {"transcription": "раз", "boundaries": (0.0, 4.0)},
            {"transcription": "два", "boundaries": (4.0, 8.0)},
        ]
    )
    segs = decode_long_plain(model, tmp_path / "a.wav")
    assert [s.text for s in segs] == ["раз", "два"]
    assert segs[1].start == 4.0


def test_decode_long_plain_wraps_errors(tmp_path):
    model = _FakeModel(longform_exc=RuntimeError("boom"))
    with pytest.raises(AudioProcessingError):
        decode_long_plain(model, tmp_path / "a.wav")


def test_decode_options_defaults_are_torch_without_extras():
    opts = DecodeOptions()
    assert opts.backend == "torch"
    assert not opts.onnx_int8 and not opts.onnx_encoder and not opts.word_timestamps
    assert opts.progress_cb is None
