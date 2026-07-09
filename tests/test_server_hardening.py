"""M2 hardening (находки ревью): throttle headroom/backoff/eviction, non-ASCII
username, auto-bump epoch при смене пароля, cookie-флаги."""

from fastapi.testclient import TestClient

from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.auth import LoginThrottle
from gigaam_transcriber.server.security import hash_password
from tests.conftest import PASSWORD, server_settings


def _settings(tmp_path, **over):
    return server_settings(tmp_path, **over)


# --------------------------------------------------------------------------- #
# LoginThrottle: global headroom, экспоненциальный backoff, eviction
# --------------------------------------------------------------------------- #
def test_throttle_one_ip_does_not_lock_others():
    t = LoginThrottle(max_failures=3, lockout_seconds=60, global_max_failures=100)
    for _ in range(3):
        t.record_failure("1.1.1.1", now=1000)
    assert t.retry_after("1.1.1.1", now=1000) > 0  # виновный IP заблокирован
    assert t.retry_after("2.2.2.2", now=1000) == 0  # легитимный — нет (есть запас)


def test_throttle_global_lock_after_distributed_flood():
    t = LoginThrottle(max_failures=100, lockout_seconds=60, global_max_failures=5)
    for i in range(5):
        t.record_failure(f"10.0.0.{i}", now=1000)  # разные IP, общий поток
    assert t.retry_after("fresh", now=1000) > 0  # глобальный предохранитель сработал


def test_throttle_exponential_backoff_grows():
    t = LoginThrottle(max_failures=1, lockout_seconds=10, global_max_failures=10_000)
    t.record_failure("x", now=0)
    w1 = t.retry_after("x", now=0)
    t.record_failure("x", now=100)  # после истечения первого локаута
    w2 = t.retry_after("x", now=100)
    assert w2 > w1  # второй локаут длиннее


def test_throttle_backoff_capped():
    t = LoginThrottle(
        max_failures=1, lockout_seconds=10, global_max_failures=10_000, max_lockout_seconds=15
    )
    now = 0
    for _ in range(10):
        t.record_failure("x", now=now)
        now += 10_000  # каждый раз после истечения
    assert t.retry_after("x", now=now) <= 16  # cap (15) + 1


def test_throttle_evicts_expired_entries():
    t = LoginThrottle(max_failures=1, lockout_seconds=10, global_max_failures=10_000)
    t.record_failure("x", now=0)
    assert "x" in t._per_ip
    t.retry_after("x", now=10_000)  # локаут истёк → вытеснение
    assert "x" not in t._per_ip


# --------------------------------------------------------------------------- #
# non-ASCII username (раньше 500 на каждом защищённом запросе)
# --------------------------------------------------------------------------- #
def test_non_ascii_username_works_end_to_end(tmp_path):
    c = TestClient(create_app(_settings(tmp_path, user="админ")))
    r = c.post("/api/auth/login", data={"username": "админ", "password": PASSWORD})
    assert r.status_code == 200
    r2 = c.get("/api/auth/me")
    assert r2.status_code == 200
    assert r2.json()["user"] == "админ"


# --------------------------------------------------------------------------- #
# §8 auto-bump epoch при смене пароля
# --------------------------------------------------------------------------- #
def test_password_change_invalidates_existing_cookies(tmp_path):
    s1 = _settings(tmp_path, password_hash=hash_password("old-pass"))
    c1 = TestClient(create_app(s1))
    c1.post("/api/auth/login", data={"username": "admin", "password": "old-pass"})
    assert c1.get("/api/auth/me").status_code == 200
    cookie = c1.cookies.get("bt_session")

    # Смена пароля (тот же session_key и data_dir) → reconcile бампит epoch.
    s2 = _settings(tmp_path, password_hash=hash_password("new-pass"))
    c2 = TestClient(create_app(s2))
    c2.cookies.set("bt_session", cookie)
    assert c2.get("/api/auth/me").status_code == 401  # старая cookie мертва


def test_unchanged_password_keeps_sessions(tmp_path):
    # Тот же password_hash (в проде он стабилен — из env), epoch не бампится.
    ph = hash_password(PASSWORD)
    c1 = TestClient(create_app(_settings(tmp_path, password_hash=ph)))
    c1.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    cookie = c1.cookies.get("bt_session")
    c2 = TestClient(create_app(_settings(tmp_path, password_hash=ph)))
    c2.cookies.set("bt_session", cookie)
    assert c2.get("/api/auth/me").status_code == 200


# --------------------------------------------------------------------------- #
# cookie-флаги (HttpOnly / Secure / SameSite=Strict)
# --------------------------------------------------------------------------- #
def test_login_cookie_security_flags(tmp_path):
    s = _settings(tmp_path, cookie_secure=True)
    c = TestClient(create_app(s))
    r = c.post(
        "/api/auth/login",
        data={"username": "admin", "password": PASSWORD},
        headers={"origin": "http://testserver"},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "secure" in set_cookie
