"""POST /api/uploads — приём записи (спека §6/§8).

Файлы стримятся на диск кусками с проверкой лимитов ДО полного чтения; имя на
диске — из server-uuid (анти-traversal); формат валидируется по magic-bytes (не
по суффиксу); .zip отклоняется. Несколько файлов = одна Route A запись, один файл
= single. Имена участников берутся из имён файлов (правятся на шаге confirm).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from . import media
from .auth import require_session
from .repository import create_recording, new_id

router = APIRouter()

_CHUNK = 1 << 20  # 1 МиБ


def _participant_name(filename: str) -> str:
    stem = Path(filename or "track").stem
    return unicodedata.normalize("NFC", stem) or "track"


@router.post("/api/uploads")
async def upload(
    request: Request,
    files: List[UploadFile] = File(...),
    user: str = Depends(require_session),
) -> dict:
    settings = request.app.state.settings
    if not files:
        raise HTTPException(status_code=400, detail="Нет файлов")

    # Ранний отказ по Content-Length (defense-in-depth; основной предохранитель от
    # больших тел — nginx client_max_body_size, см. deploy/nginx.conf). Per-chunk
    # лимиты ниже всё равно проверяются на случай заниженного/отсутствующего заголовка.
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > settings.max_recording_total:
            raise HTTPException(413, "Запись превышает суммарный лимит")

    upload_dir = Path(settings.data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved: List[dict] = []
    total = 0
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
                    total += len(chunk)
                    if size > settings.max_file_size:
                        dest.unlink(missing_ok=True)
                        raise HTTPException(413, "Файл превышает лимит размера")
                    if total > settings.max_recording_total:
                        dest.unlink(missing_ok=True)
                        raise HTTPException(413, "Запись превышает суммарный лимит")
                    out.write(chunk)
            if media.is_zip(head):
                dest.unlink(missing_ok=True)
                raise HTTPException(415, ".zip не принимается")
            if media.sniff_media(head) is None:
                dest.unlink(missing_ok=True)
                raise HTTPException(415, f"Неподдерживаемый формат: {f.filename!r}")
            saved.append(
                {"name": _participant_name(f.filename), "path": str(dest), "size": size}
            )
    except HTTPException:
        for t in saved:  # откат уже сохранённых дорожек этой записи
            Path(t["path"]).unlink(missing_ok=True)
        raise

    kind = "route_a" if len(saved) > 1 else "single"
    rec_id = create_recording(
        settings.db_path, origin="upload", kind=kind, tracks=saved,
        title=saved[0]["name"] if saved else None,
    )
    return {
        "recording_id": rec_id,
        "kind": kind,
        "tracks": [{"name": t["name"], "size": t["size"]} for t in saved],
    }
