"""Конфиг источников авто-watch (yandex/local): GET/PUT /api/ingest/source + ручной скан.

HTTP-слой над `ingest_sources`: читает/пишет watch_dir, окно опроса, профиль
раскладки. Скан-логику и валидацию держат `local_watch`/`zoom_scan` (сюда — лениво,
чтобы воркерные модули не тянули этот роутер). Яндекс-ingest — в `yandex.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import require_session
from .ingest_common import under_watch_dir
from .repository import get_ingest_source, set_ingest_last_scan, upsert_ingest_source

router = APIRouter()


class IngestSourceIn(BaseModel):
    watch_dir: str
    enabled: bool = False
    poll_interval: int = 300
    # None → «не менять сохранённое» (иначе PUT из UI, не знающего про поле,
    # затирал бы его дефолтом).
    default_params: dict | None = None
    scan_profile: dict | None = None
    source_type: str = "yandex"  # yandex | local (обратная совместимость — Яндекс)


def _source_type_or_400(raw: str) -> str:
    if raw not in ("yandex", "local"):
        raise HTTPException(400, "source_type: yandex|local")
    return raw


@router.get("/api/ingest/source")
def get_source(
    request: Request, source_type: str = "yandex", user: str = Depends(require_session)
) -> dict:
    src = get_ingest_source(request.app.state.settings.db_path, _source_type_or_400(source_type))
    if src is None:
        return {"configured": False, "source_type": source_type}
    return {
        "configured": True,
        "source_type": source_type,
        "watch_dir": src["watch_dir"],
        "enabled": src["enabled"],
        "poll_interval": src["poll_interval"],
        "default_params": json.loads(src["default_params"] or "{}"),
        "scan_profile": json.loads(src.get("scan_profile") or "{}"),
        "last_scan_at": src.get("last_scan_at"),
    }


@router.put("/api/ingest/source")
def put_source(
    payload: IngestSourceIn, request: Request, user: str = Depends(require_session)
) -> dict:
    settings = request.app.state.settings
    source_type = _source_type_or_400(payload.source_type)
    scan_profile_json: str | None = None
    if source_type == "yandex":
        watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
        # Конфигурируемый watch_dir обязан быть под серверным allowlist (анти-обход).
        if not under_watch_dir(payload.watch_dir, watch_dir):
            raise HTTPException(403, "watch_dir вне разрешённой области")
    else:
        from .local_watch import validate_output_profile, validate_watch_dir
        from .zoom_scan import ScanProfile

        problem = validate_watch_dir(settings, payload.watch_dir)
        if problem:
            raise HTTPException(400, problem)
        # Хранить развёрнутый путь: «~» пользователя детерминированно
        # раскрывается здесь, а не в каждом потребителе.
        payload.watch_dir = str(Path(payload.watch_dir).expanduser())
        if payload.scan_profile is not None:
            from pydantic import ValidationError

            from .presets import ScanProfileIn

            try:
                validated = ScanProfileIn(**payload.scan_profile)
            except ValidationError:
                raise HTTPException(400, "Некорректный профиль раскладки")
            problem = validate_output_profile(
                settings, payload.watch_dir, ScanProfile.from_dict(validated.model_dump())
            )
            if problem:
                raise HTTPException(400, problem)
            scan_profile_json = json.dumps(validated.model_dump(), ensure_ascii=False)
        else:
            # scan_profile не прислан («не менять») — но НОВЫЙ watch_dir обязан
            # быть совместим с СОХРАНЁННЫМ профилем (иначе fixed-вывод мог бы
            # оказаться внутри новой наблюдаемой папки).
            from .local_watch import profile_from_source

            saved = get_ingest_source(settings.db_path, "local")
            if saved is not None:
                problem = validate_output_profile(
                    settings, payload.watch_dir, profile_from_source(saved)
                )
                if problem:
                    raise HTTPException(400, f"Сохранённый профиль несовместим: {problem}")
    upsert_ingest_source(
        settings.db_path,
        payload.watch_dir,
        payload.enabled,
        max(60, int(payload.poll_interval)),
        default_params_json=(
            json.dumps(payload.default_params, ensure_ascii=False)
            if payload.default_params is not None
            else None
        ),
        source_type=source_type,
        scan_profile_json=scan_profile_json,
    )
    return {
        "configured": True,
        "source_type": source_type,
        "watch_dir": payload.watch_dir,
        "enabled": payload.enabled,
    }


@router.post("/api/ingest/local/scan")
def local_scan_now(request: Request, user: str = Depends(require_session)) -> dict:
    """Немедленный проход по локальной папке (кнопка «Сканировать сейчас»).

    Локальная ФС — скан быстрый, выполняем в запросе (`wait_stable=False` — без
    sleep-проб). GPU-работа всё равно уходит в очередь через app.state.enqueue.
    ffmpeg-склейку частей (parts_mode=merge) оставляем синхронной в запросе:
    она ограничена таймаутом, а вынос в отдельную io-очередь усложнил бы дедуп
    claim без выигрыша — merge-встречи редки, а обычные записи склейки не требуют."""
    from .local_watch import poll_local_source, validate_watch_dir

    settings = request.app.state.settings
    src = get_ingest_source(settings.db_path, "local")
    if src is None:
        raise HTTPException(400, "Локальная папка не настроена")
    # Папка могла исчезнуть после настройки — честная ошибка вместо
    # ложного «всё обработано» (poll молча вернул бы пустой список).
    problem = validate_watch_dir(settings, src["watch_dir"])
    if problem:
        raise HTTPException(400, problem)
    enqueue = getattr(request.app.state, "enqueue", None)
    set_ingest_last_scan(settings.db_path, "local")
    started = poll_local_source(settings, enqueue, force=True, wait_stable=False)
    return {"scanned": True, "started": started}
