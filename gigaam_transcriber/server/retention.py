"""Retention (M6, спека §11): периодическая чистка uploads/outputs по TTL.

v1 = prune work_dir сразу после успеха + документированный cron; здесь — автоматизация
v1.x: uploads TTL 7 дней, outputs/manifests 30 дней. Чистая функция тестируема
(время инъектируется), периодическая huey-задача — тонкая обёртка.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import Settings

UPLOADS_TTL = 7 * 24 * 3600
OUTPUTS_TTL = 30 * 24 * 3600


def _prune_dir(root: Path, ttl: float, now: float) -> int:
    """Удалить записи верхнего уровня в root старше ttl (по mtime). Возвращает число."""
    if not root.exists():
        return 0
    removed = 0
    for entry in root.iterdir():
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age <= ttl:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


def prune_retention(settings: Settings, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    data = Path(settings.data_dir)
    return {
        "uploads": _prune_dir(data / "uploads", UPLOADS_TTL, now),
        "outputs": _prune_dir(data / "outputs", OUTPUTS_TTL, now),
        "work": _prune_dir(data / "work", UPLOADS_TTL, now),
    }
