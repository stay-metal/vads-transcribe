"""M6 v1.x — веб-управление галереями голосов (list/create/delete, сборка)."""

import json
from pathlib import Path

from gigaam_transcriber.server.app import create_app
from tests.conftest import WAV, login_client, server_settings


def _client(tmp_path, monkeypatch):
    gdir = tmp_path / "galleries"
    gdir.mkdir()
    monkeypatch.setenv("BLOODTRANSCRIPTS_GALLERY_DIR", str(gdir))
    app = create_app(server_settings(tmp_path))
    calls: list = []
    app.state.enqueue_gallery = lambda name, tracks: calls.append((name, tracks))
    return login_client(app), gdir, calls


def test_galleries_list_and_delete(tmp_path, monkeypatch):
    c, gdir, _ = _client(tmp_path, monkeypatch)
    (gdir / "team.json").write_text(
        json.dumps({"version": 1, "refs": {"Алиса": [0.1], "Боб": [0.2]}}), encoding="utf-8"
    )
    lst = c.get("/api/galleries").json()["galleries"]
    assert len(lst) == 1 and lst[0]["name"] == "team"
    assert set(lst[0]["voices"]) == {"Алиса", "Боб"}

    assert c.delete("/api/galleries/team").status_code == 200
    assert not (gdir / "team.json").exists()
    assert c.get("/api/galleries").json()["galleries"] == []


def test_gallery_delete_missing_404(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    assert c.delete("/api/galleries/nope").status_code == 404


def test_gallery_bad_name_rejected(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    # slug-валидация (анти-traversal): точки/спецсимволы → 400
    assert c.delete("/api/galleries/bad..name").status_code == 400


def test_galleries_requires_auth(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    c.post("/api/auth/logout")
    assert c.get("/api/galleries").status_code == 401


def test_create_gallery_uploads_and_enqueues(tmp_path, monkeypatch):
    c, _, calls = _client(tmp_path, monkeypatch)
    r = c.post(
        "/api/galleries",
        data={"name": "team"},
        files=[
            ("files", ("Иван.wav", WAV, "audio/wav")),
            ("files", ("Оля.wav", WAV, "audio/wav")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["building"] == "team"
    assert set(r.json()["voices"]) == {"Иван", "Оля"}
    # ECAPA-сборка поставлена в очередь с метками из имён файлов
    assert len(calls) == 1
    name, tracks = calls[0]
    assert name == "team" and set(tracks) == {"Иван", "Оля"}
    for path in tracks.values():
        assert Path(path).exists()  # образцы сохранены на диск


def test_create_gallery_bad_name(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    r = c.post(
        "/api/galleries",
        data={"name": "bad name"},
        files=[("files", ("a.wav", WAV, "audio/wav"))],
    )
    assert r.status_code == 400


def test_create_gallery_duplicate_409(tmp_path, monkeypatch):
    c, gdir, _ = _client(tmp_path, monkeypatch)
    (gdir / "team.json").write_text("{}", encoding="utf-8")
    r = c.post(
        "/api/galleries",
        data={"name": "team"},
        files=[("files", ("a.wav", WAV, "audio/wav"))],
    )
    assert r.status_code == 409


def test_create_gallery_rejects_non_media(tmp_path, monkeypatch):
    c, _, _ = _client(tmp_path, monkeypatch)
    r = c.post(
        "/api/galleries",
        data={"name": "team"},
        files=[("files", ("a.wav", b"not-audio-bytes", "audio/wav"))],
    )
    assert r.status_code == 415


def test_build_gallery_job_saves_and_cleans(tmp_path, monkeypatch):
    import numpy as np

    from gigaam_transcriber import voiceprint

    monkeypatch.setenv("BLOODTRANSCRIPTS_GALLERY_DIR", str(tmp_path / "g"))
    monkeypatch.setattr(
        voiceprint,
        "build_gallery_from_tracks",
        lambda tracks, embedder=None: {n: np.ones(192, dtype=np.float32) for n in tracks},
    )
    from gigaam_transcriber.server.config import Settings
    from gigaam_transcriber.server.galleries_api import _gallery_dir
    from gigaam_transcriber.server.gallery_builder import build_gallery_job

    s = Settings(data_dir=tmp_path, session_key="x")
    updir = tmp_path / "gallery_uploads" / "team"
    updir.mkdir(parents=True)
    sample = updir / "a.wav"
    sample.write_bytes(b"x")

    build_gallery_job(s, "team", {"Иван": str(sample)})

    assert (_gallery_dir() / "team.json").exists()  # галерея сохранена
    assert not sample.exists()  # образец подчищен
    assert not updir.exists()  # папка образцов удалена
