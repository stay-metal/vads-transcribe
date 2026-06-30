"""Тесты voiceprint-именования (инкремент 19) — чистая математика, без ECAPA-модели."""

import numpy as np

from gigaam_transcriber.voiceprint import (
    cosine,
    load_gallery,
    name_speaker,
    save_gallery,
    vote_speaker,
)


def _v(*x):
    return np.array(x, dtype=float)


def test_cosine():
    assert abs(cosine(_v(1, 0), _v(1, 0)) - 1.0) < 1e-9
    assert abs(cosine(_v(1, 0), _v(0, 1))) < 1e-9
    assert cosine(_v(0, 0), _v(1, 1)) == 0.0  # нулевой вектор → 0


def test_name_speaker_picks_top():
    refs = {"A": _v(1, 0, 0), "B": _v(0, 1, 0)}
    assert name_speaker(_v(0.99, 0.01, 0), refs, thr=0.5, margin=0.1) == "A"


def test_name_speaker_abstains_low_cos():
    refs = {"A": _v(1, 0, 0), "B": _v(0, 1, 0)}
    assert name_speaker(_v(0, 0, 1), refs, thr=0.5, margin=0.1) is None


def test_name_speaker_abstains_low_margin():
    refs = {"A": _v(1, 0.0, 0), "B": _v(1, 0.01, 0)}  # почти одинаковые референсы
    assert name_speaker(_v(1, 0.005, 0), refs, thr=0.5, margin=0.1) is None


def test_single_ref_stricter_threshold():
    refs = {"A": _v(1, 0, 0)}
    q = _v(1, 1.3, 0)  # cos ≈ 0.61: между 0.55 и 0.70
    assert name_speaker(q, refs, thr=0.55, margin=0.1) is None  # один реф → порог 0.70


def test_empty_refs():
    assert name_speaker(_v(1, 0), {}) is None


def test_vote_speaker_majority():
    refs = {"A": _v(1, 0, 0), "B": _v(0, 1, 0)}
    windows = [_v(0.99, 0.01, 0)] * 5
    assert vote_speaker(windows, refs, thr=0.5, margin=0.1, min_windows=3) == "A"


def test_vote_speaker_abstains_split():
    refs = {"A": _v(1, 0, 0), "B": _v(0, 1, 0)}
    windows = [_v(0.99, 0.01, 0), _v(0.01, 0.99, 0)]  # раскол, мало голосов
    assert vote_speaker(windows, refs, thr=0.5, margin=0.1, min_windows=3) is None


def test_gallery_roundtrip(tmp_path):
    refs = {"Алексей Педан": _v(1, 0, 0), "Иван Крючков": _v(0, 1, 0)}
    p = save_gallery(refs, tmp_path / "g.json", theta=0.6, margin=0.1)
    refs2, theta, margin = load_gallery(p)
    assert set(refs2) == {"Алексей Педан", "Иван Крючков"}
    assert theta == 0.6 and margin == 0.1
    assert np.allclose(refs2["Алексей Педан"], [1, 0, 0])


def test_load_missing_gallery(tmp_path):
    refs, theta, margin = load_gallery(tmp_path / "nope.json")
    assert refs == {} and theta is None
