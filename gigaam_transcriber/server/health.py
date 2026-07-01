"""Health-эндпоинты: liveness (`/healthz`) и readiness (`/readyz`).

`/readyz` отдаёт 503, пока gpu-worker не прогрел модель и не выставил ready-флаг
(файл `settings.ready_flag_path`). nginx/compose гейтят трафик по `/readyz`.
Ни один из эндпоинтов модель НЕ грузит.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """Liveness: процесс api жив (модель не требуется)."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict:
    """Readiness: 200 только когда тёплая модель доступна (ready-флаг существует)."""
    settings = request.app.state.settings
    if settings.ready_flag_path.exists():
        return {"status": "ready"}
    response.status_code = 503
    return {"status": "not_ready"}
