"""Записи: подтверждение участников Route A (спека §4.1, §6).

GET  /api/recordings/{id}/discover-tracks — показать дорожки участник→файл.
POST /api/recordings/{id}/discover-tracks — подтвердить: правка имён, удаление
лишних дорожек. Пути берём ТОЛЬКО из ранее сохранённых дорожек записи (анти-инъекция),
имена — пользовательские (для отображения/ground-truth).
"""

from __future__ import annotations

import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import require_session
from .repository import get_recording, update_recording_tracks

router = APIRouter()


class TrackIn(BaseModel):
    name: str
    id: int  # opaque индекс дорожки (не серверный путь — анти-leak)


class ConfirmTracksIn(BaseModel):
    tracks: list[TrackIn]


@router.get("/api/recordings/{rec_id}/discover-tracks")
def discover_tracks(rec_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    rec = get_recording(settings.db_path, rec_id)
    if rec is None:
        raise HTTPException(404, "Запись не найдена")
    # Возвращаем opaque id (индекс), НЕ абсолютные серверные пути.
    return {
        "recording_id": rec_id,
        "kind": rec["kind"],
        "tracks": [{"id": i, "name": t["name"]} for i, t in enumerate(rec["tracks"])],
    }


@router.post("/api/recordings/{rec_id}/discover-tracks")
def confirm_tracks(
    rec_id: str,
    payload: ConfirmTracksIn,
    request: Request,
    user: str = Depends(require_session),
) -> dict:
    settings = request.app.state.settings
    rec = get_recording(settings.db_path, rec_id)
    if rec is None:
        raise HTTPException(404, "Запись не найдена")

    original = rec["tracks"]
    confirmed: list[dict] = []
    seen_names = set()
    seen_ids = set()
    for t in payload.tracks:
        if not (0 <= t.id < len(original)) or t.id in seen_ids:
            raise HTTPException(400, "Неизвестная или повторённая дорожка")
        seen_ids.add(t.id)
        name = unicodedata.normalize("NFC", t.name).strip()
        if not name:
            raise HTTPException(400, "Пустое имя участника")
        if name in seen_names:
            raise HTTPException(400, f"Дублирующееся имя участника: {name!r}")
        seen_names.add(name)
        merged = dict(original[t.id])
        merged["name"] = name
        confirmed.append(merged)

    if not confirmed:
        raise HTTPException(400, "Нужна хотя бы одна дорожка")

    update_recording_tracks(settings.db_path, rec_id, confirmed)
    return {
        "recording_id": rec_id,
        "tracks": [{"id": i, "name": t["name"]} for i, t in enumerate(confirmed)],
    }
