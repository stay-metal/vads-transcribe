"""M2 — каркас сервера: health, auth, сессии, epoch, throttle, Origin/HTTPS.

Модель не задействована: эндпоинты api её не грузят. Используется FastAPI TestClient.
"""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.server import db as dbmod
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.security import hash_password

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="session-key-aaaaaaaaaaaaaaaa",
        fernet_key="fernet-key-bbbbbbbbbbbbbbbb",
        data_dir=tmp_path,
        cookie_secure=False,
        require_https=False,
        login_max_failures=3,
        login_lockout_seconds=60,
    )


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def _login(client, username="admin", password=PASSWORD):
    return client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz_503_until_flag(client, settings):
    assert client.get("/readyz").status_code == 503
    settings.ready_flag_path.write_text("ready")
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


# --------------------------------------------------------------------------- #
# auth happy-path (DoD: login → cookie → защищённый echo)
# --------------------------------------------------------------------------- #
def test_login_sets_cookie_and_unlocks_echo(client):
    assert client.get("/api/echo").status_code == 401
    r = _login(client)
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "user": "admin"}
    assert "ds_session" in client.cookies
    r2 = client.get("/api/echo", params={"msg": "hi"})
    assert r2.status_code == 200
    assert r2.json() == {"echo": "hi", "user": "admin"}


def test_me_returns_user(client):
    _login(client)
    assert client.get("/api/auth/me").json() == {"user": "admin"}


def test_logout_clears_session(client):
    _login(client)
    assert client.get("/api/echo").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/echo").status_code == 401


# --------------------------------------------------------------------------- #
# auth negative
# --------------------------------------------------------------------------- #
def test_bad_password_rejected(client):
    assert _login(client, password="nope").status_code == 401


def test_bad_username_rejected(client):
    assert _login(client, username="root").status_code == 401


def test_protected_requires_session(client):
    assert client.get("/api/echo").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_cookie_rejected(client):
    _login(client)
    client.cookies.set("ds_session", "forged.value.zzz")
    assert client.get("/api/echo").status_code == 401


def test_epoch_bump_invalidates_all_sessions(client, settings):
    _login(client)
    assert client.get("/api/echo").status_code == 200
    dbmod.bump_session_epoch(settings.db_path)
    assert client.get("/api/echo").status_code == 401


# --------------------------------------------------------------------------- #
# brute-force throttle
# --------------------------------------------------------------------------- #
def test_throttle_locks_after_failures(client):
    for _ in range(3):
        assert _login(client, password="wrong").status_code == 401
    # после порога — локаут даже на корректный пароль
    assert _login(client, password="wrong").status_code == 429
    assert _login(client).status_code == 429


# --------------------------------------------------------------------------- #
# CSRF / транспорт
# --------------------------------------------------------------------------- #
def test_spoofed_xff_does_not_evade_throttle(tmp_path):
    # За nginx (require_https) IP берётся из X-Real-IP / правого XFF, а не из
    # клиент-контролируемого левого XFF → спуфинг не сбрасывает per-IP счётчик.
    s = Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="k1aaaaaaaaaaaaaaaa",
        fernet_key="k2bbbbbbbbbbbbbbbb",
        data_dir=tmp_path,
        cookie_secure=True,
        require_https=True,
        login_max_failures=3,
        login_lockout_seconds=60,
    )
    c = TestClient(create_app(s))
    real_ip_headers = {"x-real-ip": "203.0.113.7", "x-forwarded-proto": "https"}
    for i in range(3):
        r = c.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong"},
            headers={**real_ip_headers, "x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"},
        )
        assert r.status_code == 401
    # 4-я попытка с другим поддельным левым XFF — всё равно локаут по реальному IP
    r = c.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong"},
        headers={**real_ip_headers, "x-forwarded-for": "8.8.8.8, 203.0.113.7"},
    )
    assert r.status_code == 429


def test_origin_check_blocks_foreign_origin(client):
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": PASSWORD},
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_same_origin_allowed(client):
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": PASSWORD},
        headers={"origin": "http://testserver"},
    )
    assert r.status_code == 200


def test_non_https_rejected_behind_proxy(tmp_path):
    s = Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="k1aaaaaaaaaaaaaaaa",
        fernet_key="k2bbbbbbbbbbbbbbbb",
        data_dir=tmp_path,
        cookie_secure=True,
        require_https=True,
    )
    c = TestClient(create_app(s))
    assert c.get("/healthz", headers={"x-forwarded-proto": "http"}).status_code == 400
    assert c.get("/healthz", headers={"x-forwarded-proto": "https"}).status_code == 200


def test_security_headers_present(client):
    r = client.get("/healthz")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"


# --------------------------------------------------------------------------- #
# инвариант: процесс api не импортирует ASR-пакет gigaam (модель не в памяти)
# --------------------------------------------------------------------------- #
def test_api_does_not_import_asr_model():
    code = (
        "import sys;"
        "from gigaam_transcriber.server.app import create_app;"
        "from gigaam_transcriber.server.config import Settings;"
        "import tempfile;"
        "create_app(Settings(data_dir=tempfile.mkdtemp(), session_key='x', password_hash=''));"
        "leaked=[m for m in sys.modules if m=='gigaam' or m.startswith('gigaam.')];"
        "assert not leaked, leaked;"
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
