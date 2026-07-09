"""M3.2 — upload (magic-bytes, лимиты, uuid), discover/confirm Route A."""

import io

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.server.app import create_app
from tests.conftest import PASSWORD, WAV, server_settings

ZIP = b"PK\x03\x04" + b"\x00" * 60
TXT = b"just some text not a media file at all......"


def _settings(tmp_path, **over):
    return server_settings(tmp_path, **over)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(_settings(tmp_path)))


@pytest.fixture
def auth_client(client):
    client.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    return client


def _file(name, data):
    return ("files", (name, io.BytesIO(data), "application/octet-stream"))


def test_upload_requires_auth(client):
    r = client.post("/api/uploads", files=[_file("a.wav", WAV)])
    assert r.status_code == 401


def test_upload_single(auth_client):
    r = auth_client.post("/api/uploads", files=[_file("Алиса.wav", WAV)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "single"
    assert body["tracks"][0]["name"] == "Алиса"


def test_upload_route_a_multi(auth_client):
    r = auth_client.post("/api/uploads", files=[_file("Алиса.wav", WAV), _file("Боб.m4a", WAV)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "route_a"
    assert {t["name"] for t in body["tracks"]} == {"Алиса", "Боб"}


def test_upload_rejects_zip(auth_client):
    r = auth_client.post("/api/uploads", files=[_file("evil.wav", ZIP)])
    assert r.status_code == 415


def test_upload_rejects_unknown_format(auth_client):
    r = auth_client.post("/api/uploads", files=[_file("note.wav", TXT)])
    assert r.status_code == 415


def _uploads_dir_files(tmp_path):
    d = tmp_path / "uploads"
    return list(d.iterdir()) if d.exists() else []


def test_upload_file_too_large_cleans_up(tmp_path):
    c = TestClient(create_app(_settings(tmp_path, max_file_size=8)))
    c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    r = c.post("/api/uploads", files=[_file("a.wav", WAV)])
    assert r.status_code == 413
    assert _uploads_dir_files(tmp_path) == []  # частичный файл удалён


def test_upload_recording_total_limit(tmp_path):
    # каждый файл проходит per-file, но сумма превышает recording_total
    c = TestClient(create_app(_settings(tmp_path, max_recording_total=len(WAV) + 4)))
    c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    r = c.post("/api/uploads", files=[_file("a.wav", WAV), _file("b.wav", WAV)])
    assert r.status_code == 413
    assert _uploads_dir_files(tmp_path) == []  # обе дорожки откатаны


def test_upload_rollback_on_second_file_invalid(tmp_path):
    c = TestClient(create_app(_settings(tmp_path)))
    c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    # первый валидный, второй — zip → весь запрос отклоняется, первый откатан
    r = c.post("/api/uploads", files=[_file("a.wav", WAV), _file("evil.wav", ZIP)])
    assert r.status_code == 415
    assert _uploads_dir_files(tmp_path) == []


@pytest.mark.parametrize(
    "head,ok",
    [
        (b"ID3\x03\x00\x00\x00\x00\x00\x00\x00", True),  # mp3 (ID3)
        (b"\xff\xfb\x90\x00" + b"\x00" * 8, True),  # mp3 frame sync
        (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 4, True),  # wav
        (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4, True),  # mp4/m4a
        (b"OggS\x00\x02\x00\x00\x00\x00\x00\x00", True),  # ogg
        (b"fLaC\x00\x00\x00\x22" + b"\x00" * 4, True),  # flac
        (b"\x1aE\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x00", True),  # mkv/webm
        (b"not a media file at all!!!", False),  # мусор
    ],
)
def test_magic_bytes_per_type(auth_client, head, ok):
    r = auth_client.post("/api/uploads", files=[_file("x.wav", head + b"\x00" * 32)])
    assert (r.status_code == 200) == ok


def test_discover_and_confirm(auth_client):
    up = auth_client.post(
        "/api/uploads", files=[_file("audio1.wav", WAV), _file("audio2.wav", WAV)]
    ).json()
    rec_id = up["recording_id"]

    disc = auth_client.get(f"/api/recordings/{rec_id}/discover-tracks").json()
    assert len(disc["tracks"]) == 2
    # discover отдаёт opaque id, НЕ серверные пути
    assert all("path" not in t and "id" in t for t in disc["tracks"])
    ids = [t["id"] for t in disc["tracks"]]

    r = auth_client.post(
        f"/api/recordings/{rec_id}/discover-tracks",
        json={"tracks": [{"name": "Алиса", "id": ids[0]}]},
    )
    assert r.status_code == 200, r.text
    confirmed = r.json()["tracks"]
    assert len(confirmed) == 1
    assert confirmed[0]["name"] == "Алиса"


def test_confirm_rejects_foreign_id(auth_client):
    up = auth_client.post("/api/uploads", files=[_file("a.wav", WAV)]).json()
    r = auth_client.post(
        f"/api/recordings/{up['recording_id']}/discover-tracks",
        json={"tracks": [{"name": "X", "id": 99}]},
    )
    assert r.status_code == 400


def test_confirm_rejects_duplicate_names(auth_client):
    up = auth_client.post("/api/uploads", files=[_file("a.wav", WAV), _file("b.wav", WAV)]).json()
    disc = auth_client.get(f"/api/recordings/{up['recording_id']}/discover-tracks").json()
    ids = [t["id"] for t in disc["tracks"]]
    r = auth_client.post(
        f"/api/recordings/{up['recording_id']}/discover-tracks",
        json={"tracks": [{"name": "Дубль", "id": ids[0]}, {"name": "Дубль", "id": ids[1]}]},
    )
    assert r.status_code == 400


def test_discover_missing_recording(auth_client):
    assert auth_client.get("/api/recordings/nope/discover-tracks").status_code == 404
