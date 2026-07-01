"""Галереи голосов (voiceprint) — web-управление: список и удаление (M6 v1.x).

Создание галереи — тяжёлая ML-операция (ECAPA-эмбеддинги), делается CLI
`dialogscribe gallery build ...`. Веб отдаёт список готовых галерей (для выбора в
submit) и позволяет удалить. Каталог согласован с CLI через `DIALOGSCRIBE_GALLERY_DIR`.
Имя галереи — slug (анти-traversal), как в CLI.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_session

router = APIRouter()

_SAFE_GALLERY_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _gallery_dir() -> Path:
    """Каталог галерей (env DIALOGSCRIBE_GALLERY_DIR или ~/.cache) — как в CLI."""
    env = os.getenv("DIALOGSCRIBE_GALLERY_DIR")
    base = Path(env) if env else Path.home() / ".cache" / "gigaam_transcriber" / "galleries"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _gallery_path(name: str) -> Path:
    """Безопасный путь: имя — slug, без разделителей/`..`/абсолютных путей."""
    if not _SAFE_GALLERY_NAME.match(name or ""):
        raise HTTPException(400, "Недопустимое имя галереи (буквы/цифры/_/-, без путей)")
    return _gallery_dir() / f"{name}.json"


@router.get("/api/galleries")
def list_galleries(user: str = Depends(require_session)) -> dict:
    out = []
    for f in sorted(_gallery_dir().glob("*.json")):
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            voices = list(obj.get("refs", {}).keys())
        except Exception:
            voices = []
        out.append({"name": f.stem, "voices": voices})
    return {"galleries": out}


@router.delete("/api/galleries/{name}")
def delete_gallery(name: str, user: str = Depends(require_session)) -> dict:
    path = _gallery_path(name)  # slug-валидация ДО файловой операции
    if not path.exists():
        raise HTTPException(404, "Галерея не найдена")
    path.unlink()
    return {"deleted": name}
