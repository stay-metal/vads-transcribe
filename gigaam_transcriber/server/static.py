"""Раздача SPA (M4): статика из dist + catch-all на index.html для client-routing.

Монтируется ПОСЛЕ /api, /healthz, /readyz — те имеют приоритет. Отдаётся только
содержимое каталога static (dist), без dotfiles/source-maps. Если каталог ещё не
собран (dev/тесты), SPA не монтируется — api работает как есть.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def mount_spa(app: FastAPI) -> bool:
    """Подключить SPA, если каталог собран. Возвращает True, если подключено."""
    root = static_dir()
    index = root / "index.html"
    if not index.exists():
        return False

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request):
        # /api,/healthz,/readyz уже смэтчены ранее; сюда попадают только SPA-маршруты.
        candidate = (root / full_path).resolve()
        # Внутри static, реальный файл, и НИ ОДИН сегмент пути не dotfile/dotdir.
        if root in candidate.parents and candidate.is_file():
            rel_parts = candidate.relative_to(root).parts
            if not any(part.startswith(".") for part in rel_parts):
                return FileResponse(str(candidate))
        if full_path.startswith(("api/", "healthz", "readyz")):
            raise HTTPException(404)
        return FileResponse(str(index))  # client-side routing fallback

    return True
