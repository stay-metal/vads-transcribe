"""Тесты Route A (инкремент 21) — discover/парсинг имён без ASR-модели.

Плюс hardening-правки M0 для серверного пути (тёплый singleton):
L2 — изоляция ошибок по дорожкам, L3 — device_fallback в metadata, L4 — progress_callback.
ASR-модель не грузится: ``_transcribe_audio`` подменяется (monkeypatch)."""

from pathlib import Path

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment


def _fake_result(text: str = "привет") -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        segments=[TranscriptionSegment(text=text, start=0.0, end=1.0)],
        duration=1.0,
        language="ru",
        model_name="fake",
        processing_time=0.0,
    )


def test_discover_parses_and_canonicalizes(tmp_path):
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    for fn in [
        "audioAlexPedan51378374725.m4a",
        "audioIvan21378374725.m4a",
        "audioPonimaiuAI11378374725.m4a",
    ]:
        (rec / fn).write_bytes(b"")
    tracks = GigaAMTranscriber.discover_route_a_tracks(tmp_path)
    # имена канонизированы через глоссарий people (config/glossary.json)
    assert "Алексей Педан" in tracks
    assert "Иван Крючков" in tracks
    assert "Павел Шаталов" in tracks
    assert all(p.endswith(".m4a") for p in tracks.values())


def test_discover_empty_folder(tmp_path):
    assert GigaAMTranscriber.discover_route_a_tracks(tmp_path) == {}


def test_discover_camelcase_prefix_and_collision(tmp_path):
    """CamelCase 'Audio'-префикс снимается (IGNORECASE); коллизия имени не плодит
    молчаливую потерю дорожки — остаётся ровно одна запись (bug_015)."""
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    for fn in ["AudioQuux51.m4a", "audioQuux52.m4a"]:
        (rec / fn).write_bytes(b"")
    tracks = GigaAMTranscriber.discover_route_a_tracks(tmp_path)
    assert list(tracks.keys()) == ["Quux"]  # 'Audio' снят несмотря на регистр
    assert "Audio" not in " ".join(tracks.keys())  # префикс не протёк в имя


# --- L2: изоляция ошибок по дорожкам ------------------------------------------


def test_route_a_isolates_failing_track(monkeypatch):
    """Битая дорожка помечается в metadata, остальные дорожки выживают (не падаем)."""
    t = GigaAMTranscriber(device="cpu")

    def fake(audio_path, diarization="none", **kw):
        if "bad" in Path(audio_path).name:
            raise RuntimeError("boom")
        return _fake_result()

    monkeypatch.setattr(t, "_transcribe_audio", fake)
    res = t.transcribe_route_a({"Алексей": "good1.m4a", "Иван": "bad.m4a", "Павел": "good2.m4a"})
    assert {s.speaker for s in res.segments} == {"Алексей", "Павел"}
    failed = res.metadata["failed_tracks"]
    assert [f["name"] for f in failed] == ["Иван"]
    assert failed[0]["error"] == "RuntimeError"


def test_route_a_all_tracks_failing_does_not_raise(monkeypatch):
    """Даже если падают ВСЕ дорожки — возвращаем пустой результат, а не исключение."""
    t = GigaAMTranscriber(device="cpu")
    monkeypatch.setattr(
        t,
        "_transcribe_audio",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    res = t.transcribe_route_a({"A": "a.m4a", "B": "b.m4a"})
    assert res.segments == []
    assert len(res.metadata["failed_tracks"]) == 2


# --- L3: device_fallback на пути Route A ---------------------------------------


def test_route_a_surfaces_device_fallback(monkeypatch):
    """GPU→CPU откол на основном пути виден в metadata.device_fallback."""
    t = GigaAMTranscriber(device="cpu")

    def fake(audio_path, diarization="none", **kw):
        t._device_fell_back = True  # эмуляция отката внутри декода дорожки
        t.device = "cpu"
        return _fake_result()

    monkeypatch.setattr(t, "_transcribe_audio", fake)
    res = t.transcribe_route_a({"A": "a.m4a"})
    assert res.metadata.get("device_fallback") == "cpu"


def test_route_a_no_device_fallback_key_when_healthy(monkeypatch):
    t = GigaAMTranscriber(device="cpu")
    monkeypatch.setattr(t, "_transcribe_audio", lambda *a, **k: _fake_result())
    res = t.transcribe_route_a({"A": "a.m4a"})
    assert "device_fallback" not in res.metadata


def test_route_a_resets_onnx_encoder_flag(monkeypatch):
    """Route A — чистый torch-путь: stale _onnx_encoder=True из прошлого
    transcribe(onnx_encoder=True) сбрасывается (иначе тихо включится split-device ONNX)."""
    t = GigaAMTranscriber(device="cpu")
    t._onnx_encoder = True  # как после transcribe(onnx_encoder=True) на тёплом singleton
    monkeypatch.setattr(t, "_transcribe_audio", lambda *a, **k: _fake_result())
    t.transcribe_route_a({"A": "a.m4a"})
    assert t._onnx_encoder is False


# --- L4: per-track progress_callback ------------------------------------------


def test_route_a_progress_callback(monkeypatch):
    t = GigaAMTranscriber(device="cpu")
    monkeypatch.setattr(t, "_transcribe_audio", lambda *a, **k: _fake_result())
    calls = []
    t.transcribe_route_a(
        {"A": "a.m4a", "B": "b.m4a"},
        progress_callback=lambda c, total, name: calls.append((c, total, name)),
    )
    assert calls == [(1, 2, "A"), (2, 2, "B")]


def test_route_a_progress_callback_fires_for_failed_track(monkeypatch):
    """Прогресс тикает и для пропущенной дорожки — бар не «застревает» на сбое."""
    t = GigaAMTranscriber(device="cpu")
    monkeypatch.setattr(
        t,
        "_transcribe_audio",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    calls = []
    t.transcribe_route_a(
        {"A": "a.m4a"},
        progress_callback=lambda c, total, name: calls.append((c, total, name)),
    )
    assert calls == [(1, 1, "A")]
