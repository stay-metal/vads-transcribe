"""POST /api/uploads — приём записи (спека §6/§8).

Файлы стримятся на диск кусками с проверкой лимитов ДО полного чтения; имя на
диске — из server-uuid (анти-traversal); формат валидируется по magic-bytes (не
по суффиксу); .zip отклоняется. Несколько файлов = одна Route A запись, один файл
= single. Имена участников берутся из имён файлов (правятся на шаге confirm).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from . import media
from .auth import require_session
from .repository import create_recording, new_id
from .upload_stream import stream_to_disk

router = APIRouter()


@router.post("/api/uploads")
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
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

    total = 0

    def _bump_total(delta: int) -> None:
        nonlocal total
        total += delta
        if total > settings.max_recording_total:
            raise HTTPException(413, "Запись превышает суммарный лимит")

    saved: list[dict] = []
    created: list[Path] = []  # все файлы прохода — для отката при ЛЮБОЙ ошибке
    try:
        for f in files:
            dest = upload_dir / f"{new_id()}{media.safe_suffix(f.filename)}"
            created.append(dest)
            head, size = await stream_to_disk(
                f, dest, max_size=settings.max_file_size, on_chunk=_bump_total
            )
            if media.is_zip(head):
                raise HTTPException(415, ".zip не принимается")
            if media.sniff_media(head) is None:
                raise HTTPException(415, f"Неподдерживаемый формат: {f.filename!r}")
            saved.append(
                {"name": media.nfc_label(f.filename, "track"), "path": str(dest), "size": size}
            )
    except Exception:  # HTTPException ИЛИ OSError (диск полон и т.п.) — не оставляем частичное
        for p in created:
            p.unlink(missing_ok=True)
        raise

    kind = "route_a" if len(saved) > 1 else "single"
    rec_id = create_recording(
        settings.db_path,
        origin="upload",
        kind=kind,
        tracks=saved,
        title=saved[0]["name"] if saved else None,
    )
    return {
        "recording_id": rec_id,
        "kind": kind,
        "tracks": [{"name": t["name"], "size": t["size"]} for t in saved],
    }
