"""Серверный браузер каталогов для выбора папки из UI (паттерн Radarr/Jellyfin).

Браузер не может отдать абсолютный путь локальной ФС — листает сервер:
плоский список подпапок + parent (считает сервер), ручной ввод пути в UI
равноправен. Безопасность: allowlist-корень `BLOODTRANSCRIPTS_LOCAL_WATCH_ROOT`
(`resolve()` + проверка предков закрывает `..` и симлинк-побег), только
каталоги, dot-папки и симлинки скрыты, `PermissionError` → 200 с пустым
списком (пользователь не вылетает из диалога), НЕ 500.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import require_session
from .local_watch import LOCAL_WATCH_ROOT_ENV

router = APIRouter()


def _root() -> Path:
    # Дефолт БЕЗ env — домашняя папка, не «/»: браузер не должен по умолчанию
    # перечислять всю серверную ФС (у Яндекс-близнеца такой возможности нет).
    raw = os.getenv(LOCAL_WATCH_ROOT_ENV)
    return (Path(raw) if raw else Path.home()).expanduser().resolve()


def _under_root(target: Path, root: Path) -> bool:
    return target == root or root in target.parents


@router.get("/api/fs/browse")
def browse_dirs(request: Request, path: str = "", user: str = Depends(require_session)) -> dict:
    """Листинг подпапок серверного каталога. Пустой `path` — стартовая папка
    (корень allowlist; при allowlist «/» — домашняя папка)."""
    root = _root()
    if not path.strip():
        target = root if root != Path("/") else Path.home()
    else:
        target = Path(path).expanduser()
        if not target.is_absolute():
            raise HTTPException(400, "Путь должен быть абсолютным")
        target = target.resolve()
    if not _under_root(target, root):
        raise HTTPException(403, "Путь вне разрешённой области")

    parent = str(target.parent) if target != root and target != Path("/") else None
    dirs: list[dict] = []
    denied = False
    try:
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            name = unicodedata.normalize("NFC", entry.name)
            if name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            dirs.append({"name": name, "path": str(entry)})
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        # Не роняем диалог: пустой список + валидный parent, юзер уходит вверх.
        denied = True
    return {"path": str(target), "parent": parent, "dirs": dirs, "denied": denied}
