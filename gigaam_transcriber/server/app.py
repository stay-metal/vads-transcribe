"""FastAPI app-factory (M2 каркас).

Процесс api: auth + REST + (в M4) SPA-статика. Модель НЕ держит и НЕ импортирует —
на верхнем уровне нет ни `gigaam`, ни `GigaAMTranscriber` (инвариант «api без модели»).

Middleware:
- доверяем только HTTPS за nginx (отвергаем X-Forwarded-Proto != https);
- CSRF defense-in-depth: Origin-check на мутирующих методах (поверх SameSite=Strict cookie);
- базовые security-заголовки (nosniff / DENY / no-referrer).
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from urllib.parse import urlparse

from .auth import LoginThrottle
from .auth import router as auth_router
from .config import Settings
from .db import init_db, reconcile_password_epoch
from .health import router as health_router
from .repository import reconcile_orphaned_jobs

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "worker-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
}


def _expected_origins(request: Request, settings: Settings) -> set[str]:
    host = request.headers.get("host")
    if not host:
        return set()
    schemes = ("https",) if settings.require_https else ("http", "https")
    return {f"{scheme}://{host}" for scheme in schemes}


def _default_enqueue(job_id: str):
    """Поставить джобу в gpu-очередь (lazy-импорт huey-задачи — api не грузит модель)."""
    from .tasks import run_job

    return run_job(job_id).id


def _default_enqueue_io(surrogate_id: str, kind: str, remote_tracks: list):
    """Поставить скачивание Я.Диска в io-очередь (без GPU)."""
    from .tasks import pull_recording

    return pull_recording(surrogate_id, kind, remote_tracks).id


def create_app(settings: Optional[Settings] = None, enqueue=None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    init_db(settings.db_path)
    # §8 auto-bump: смена DIALOGSCRIBE_PASSWORD_HASH инвалидирует старые cookie.
    if settings.password_hash:
        reconcile_password_epoch(settings.db_path, settings.password_hash)
    # Зависшие при прошлом рестарте in-flight джобы → честный error (не «висят»).
    reconcile_orphaned_jobs(settings.db_path)

    app = FastAPI(title="DialogScribe", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    # Постановка джоб в очередь: по умолчанию huey, в тестах подменяется.
    app.state.enqueue = enqueue if enqueue is not None else _default_enqueue
    app.state.enqueue_io = _default_enqueue_io
    app.state.login_throttle = LoginThrottle(
        settings.login_max_failures,
        settings.login_lockout_seconds,
        global_max_failures=settings.login_global_max_failures,
        max_lockout_seconds=settings.login_max_lockout_seconds,
    )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        # 1) За nginx обслуживаем только HTTPS (доверяем заголовку от прокси).
        if settings.require_https:
            proto = request.headers.get("x-forwarded-proto")
            if proto is not None and proto != "https":
                return JSONResponse({"detail": "HTTPS required"}, status_code=400)
        # 2) CSRF defense-in-depth поверх SameSite: Origin/Referer-check на мутациях.
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin is None:
                referer = request.headers.get("referer")
                if referer:
                    parsed = urlparse(referer)
                    if parsed.scheme and parsed.netloc:
                        origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin is not None and origin not in _expected_origins(request, settings):
                return JSONResponse({"detail": "Bad Origin"}, status_code=403)
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    app.include_router(health_router)
    app.include_router(auth_router)
    from .glossary_api import router as glossary_router
    from .jobs import router as jobs_router
    from .recordings import router as recordings_router
    from .uploads import router as uploads_router
    from .yandex import router as yandex_router

    app.include_router(uploads_router)
    app.include_router(recordings_router)
    app.include_router(jobs_router)
    app.include_router(yandex_router)
    app.include_router(glossary_router)

    # SPA (M4) монтируется ПОСЛЕ /api — catch-all только для клиентских маршрутов.
    from .static import mount_spa

    mount_spa(app)
    return app
