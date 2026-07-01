"""M4 — раздача SPA: catch-all на index.html, /api имеет приоритет, CSP."""

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.security import hash_password
from gigaam_transcriber.server.static import static_dir

PASSWORD = "correct-horse-battery-staple"

spa_built = pytest.mark.skipif(
    not (static_dir() / "index.html").exists(),
    reason="SPA не собрана (frontend/ npm run build)",
)


def _client(tmp_path):
    s = Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="k1aaaaaaaaaaaaaaaa",
        fernet_key="k2bbbbbbbbbbbbbbbb",
        data_dir=tmp_path,
        cookie_secure=False,
        require_https=False,
    )
    return TestClient(create_app(s))


def test_csp_header_present(tmp_path):
    r = _client(tmp_path).get("/healthz")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "media-src 'self' blob:" in csp  # для wavesurfer
    assert "frame-ancestors 'none'" in csp


def test_api_unknown_still_404(tmp_path):
    # catch-all НЕ перехватывает неизвестные /api/* (отдаёт 404, не index.html)
    assert _client(tmp_path).get("/api/does-not-exist").status_code == 404


@spa_built
def test_spa_root_serves_index(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert '<div id="root">' in r.text


@spa_built
def test_spa_client_route_fallback(tmp_path):
    # клиентский маршрут (react-router) → index.html
    r = _client(tmp_path).get("/jobs/some-id")
    assert r.status_code == 200
    assert '<div id="root">' in r.text


@spa_built
def test_spa_assets_served(tmp_path):
    # хотя бы один собранный asset доступен
    assets = static_dir() / "assets"
    first = next(assets.glob("*.js"), None)
    assert first is not None
    r = _client(tmp_path).get(f"/assets/{first.name}")
    assert r.status_code == 200
