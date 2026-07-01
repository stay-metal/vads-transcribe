"""Галереи голосов (voiceprint) — web-управление: список / создание / удаление (M6 v1.x).

Создание — тяжёлая ML-операция (ECAPA-эмбеддинги): api лишь принимает образцы
(стрим на диск, magic-bytes) и ставит сборку на io-очередь; сам ML не грузит.
Каталог согласован с CLI через `DIALOGSCRIBE_GALLERY_DIR`. Имя — slug (анти-traversal).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from . import media
from .auth import require_session
from .repository import new_id

router = APIRouter()

_SAFE_GALLERY_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_CHUNK = 1 << 20  # 1 МиБ


def _voice_label(filename: str | None) -> str:
    return unicodedata.normalize("NFC", Path(filename or "voice").stem) or "voice"


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


@router.post("/api/galleries")
async def create_gallery(
    request: Request,
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    user: str = Depends(require_session),
) -> dict:
    """Принять образцы голосов (по одному файлу на голос, метка = имя файла) и
    поставить ECAPA-сборку на io-очередь. Возвращает `building` — галерея появится
    в списке, когда сборка завершится."""
    if not _SAFE_GALLERY_NAME.match(name or ""):
        raise HTTPException(400, "Недопустимое имя галереи (буквы/цифры/_/-, без путей)")
    if _gallery_dir().joinpath(f"{name}.json").exists():
        raise HTTPException(409, "Галерея с таким именем уже существует")
    if not files:
        raise HTTPException(400, "Нужны образцы голосов")

    settings = request.app.state.settings
    upload_dir = Path(settings.data_dir) / "gallery_uploads" / name
    upload_dir.mkdir(parents=True, exist_ok=True)

    tracks: dict[str, str] = {}
    saved: list[Path] = []
    try:
        for f in files:
            dest = upload_dir / f"{new_id()}{media.safe_suffix(f.filename)}"
            size = 0
            head = b""
            with open(dest, "wb") as out:
                while True:
                    chunk = await f.read(_CHUNK)
                    if not chunk:
                        break
                    if not head:
                        head = chunk[:64]
                    size += len(chunk)
                    if size > settings.max_file_size:
                        raise HTTPException(413, "Образец превышает лимит размера")
                    out.write(chunk)
            if media.is_zip(head) or media.sniff_media(head) is None:
                raise HTTPException(415, f"Неподдерживаемый формат: {f.filename!r}")
            saved.append(dest)
            tracks[_voice_label(f.filename)] = str(dest)  # метка коллапсит дубли имён
    except HTTPException:
        for p in saved:
            p.unlink(missing_ok=True)
        import shutil

        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    enqueue_gallery = getattr(request.app.state, "enqueue_gallery", None)
    if enqueue_gallery is not None:
        enqueue_gallery(name, tracks)
    return {"building": name, "voices": list(tracks)}


@router.delete("/api/galleries/{name}")
def delete_gallery(name: str, user: str = Depends(require_session)) -> dict:
    path = _gallery_path(name)  # slug-валидация ДО файловой операции
    if not path.exists():
        raise HTTPException(404, "Галерея не найдена")
    path.unlink()
    return {"deleted": name}
