"""Пресеты раскладки источника: встроенные (в коде) + пользовательские (БД).

Пресет — UI/API-удобство: сканер читает только `ingest_sources.scan_profile`;
выбор пресета в UI заполняет форму, «Сохранить» пишет развёрнутый JSON в
профиль источника. Встроенные пресеты read-only: «Zoom» (дефолт, локальная
запись Zoom) и «Простая папка» (медиа-файлы папки — дорожки одной встречи).
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .auth import require_session
from .repository import create_scan_preset, delete_scan_preset, list_scan_presets

router = APIRouter()


class OutputIn(BaseModel):
    mode: Literal["beside", "fixed"] = "beside"
    subdir: str = "transcripts/dialogscribe"
    dir: str | None = None


class ScanProfileIn(BaseModel):
    """Валидируемая схема профиля (та же, что читает ScanProfile.from_dict)."""

    layout: Literal["zoom", "plain"] = "zoom"
    tracks_subdir: str | None = "Audio Record"
    track_mode: Literal["combine", "separate", "mix_only"] = "combine"
    # Несколько записей в папке (запись останавливали): склеить в один
    # транскрипт или отдельный на каждую запись.
    parts_mode: Literal["merge", "separate"] = "merge"
    media_suffixes: list[str] = Field(
        default_factory=lambda: [".m4a", ".mp4", ".mov", ".mp3", ".wav"]
    )
    skip_dirs: list[str] = Field(default_factory=lambda: ["transcripts", "done"])
    output: OutputIn = Field(default_factory=OutputIn)

    @field_validator("media_suffixes")
    @classmethod
    def _normalize_suffixes(cls, v: list[str]) -> list[str]:
        # «ogg» без точки не совпал бы с Path.suffix — нормализуем на входе.
        out = []
        for s in v:
            s = s.strip().lower()
            if s:
                out.append(s if s.startswith(".") else f".{s}")
        return out


# Встроенные пресеты: тела — полные (UI заполняет форму значениями).
BUILTIN_PRESETS: list[dict] = [
    {
        "id": "zoom",
        "name": "Zoom",
        "builtin": True,
        "body": ScanProfileIn().model_dump(),
    },
    {
        "id": "plain",
        "name": "Простая папка",
        "builtin": True,
        "body": ScanProfileIn(layout="plain", tracks_subdir=None).model_dump(),
    },
]
_BUILTIN_IDS = {p["id"] for p in BUILTIN_PRESETS}
_BUILTIN_NAMES = {p["name"].lower() for p in BUILTIN_PRESETS}


class PresetIn(BaseModel):
    name: str
    body: ScanProfileIn


@router.get("/api/scan-presets")
def get_presets(request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    custom = [
        {"id": p["id"], "name": p["name"], "builtin": False, "body": json.loads(p["body"])}
        for p in list_scan_presets(settings.db_path)
    ]
    return {"presets": BUILTIN_PRESETS + custom}


@router.post("/api/scan-presets", status_code=201)
def post_preset(payload: PresetIn, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    name = payload.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "Имя пресета: 1–60 символов")
    if name.lower() in _BUILTIN_NAMES:
        raise HTTPException(409, "Имя занято встроенным пресетом")
    preset_id = create_scan_preset(
        settings.db_path, name, json.dumps(payload.body.model_dump(), ensure_ascii=False)
    )
    if preset_id is None:
        raise HTTPException(409, "Пресет с таким именем уже есть")
    return {"id": preset_id, "name": name}


@router.delete("/api/scan-presets/{preset_id}", status_code=204)
def remove_preset(preset_id: str, request: Request, user: str = Depends(require_session)) -> None:
    if preset_id in _BUILTIN_IDS:
        raise HTTPException(400, "Встроенный пресет нельзя удалить")
    settings = request.app.state.settings
    if not delete_scan_preset(settings.db_path, preset_id):
        raise HTTPException(404, "Пресет не найден")
