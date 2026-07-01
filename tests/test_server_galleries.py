"""M6 v1.x — веб-управление галереями голосов (list/delete, slug-валидация)."""

import json

from fastapi.testclient import TestClient

from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.security import hash_password

PASSWORD = "correct-horse-battery-staple"


def _client(tmp_path, monkeypatch):
    gdir = tmp_path / "galleries"
    gdir.mkdir()
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(gdir))
    s = Settings(
        user="admin", password_hash=hash_password(PASSWORD),
        session_key="s" * 20, fernet_key="f" * 20, data_dir=tmp_path,
        cookie_secure=False, require_https=False,
    )
    c = TestClient(create_app(s))
    c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    return c, gdir


def test_galleries_list_and_delete(tmp_path, monkeypatch):
    c, gdir = _client(tmp_path, monkeypatch)
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
    c, _ = _client(tmp_path, monkeypatch)
    assert c.delete("/api/galleries/nope").status_code == 404


def test_gallery_bad_name_rejected(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    # slug-валидация (анти-traversal): точки/спецсимволы → 400
    assert c.delete("/api/galleries/bad..name").status_code == 400


def test_galleries_requires_auth(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    c.post("/api/auth/logout")
    assert c.get("/api/galleries").status_code == 401
