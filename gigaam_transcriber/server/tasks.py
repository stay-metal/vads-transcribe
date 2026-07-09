"""Точка входа Huey-воркеров: инстансы очередей `gpu_huey` / `io_huey` из окружения.

Воркеры запускаются как (см. deploy/docker-compose.yml):
    python -m gigaam_transcriber.server.run_gpu_worker -k process -w 1        # GPU
    huey_consumer gigaam_transcriber.server.tasks.io_huey  -w 2              # I/O

gpu-очередь: тёплый singleton (warm_up) + ready-флаг для /readyz + реконсиль
осиротевших джоб на старте; ASR-джоба — `run_job`. io-очередь: скачивание Я.Диска
(`pull_recording`), сборка галерей, периодика (retention, авто-watch yandex/local).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from huey import crontab

from .queues import make_gpu_huey, make_io_huey

_DATA_DIR = Path(os.getenv("DIALOGSCRIBE_DATA_DIR", str(Path.home() / ".dialogscribe")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

gpu_huey = make_gpu_huey(_DATA_DIR)
io_huey = make_io_huey(_DATA_DIR)

# Тёплый singleton, переиспользуемый ASR-задачами M3 (не пересоздаётся per-call).
WARM_TRANSCRIBER = None


@gpu_huey.on_startup()
def _warm_gpu_singleton() -> None:
    # Импорт тяжёлой библиотеки — только в gpu-воркере, на старте consumer.
    global WARM_TRANSCRIBER
    from .config import Settings
    from .repository import reconcile_orphaned_jobs
    from .workers import warm_up

    settings = Settings.from_env()
    # Воркер только что стартовал → in-flight никого нет: осиротевшие стадии
    # (прерванные прошлым рестартом воркера) честно в error. Api при живом воркере
    # это НЕ делает (см. create_app) — реконсиль тут единственный достоверный.
    reconcile_orphaned_jobs(settings.db_path)
    WARM_TRANSCRIBER = warm_up(settings)


@gpu_huey.task()
def run_job(job_id: str) -> str:
    """gpu-задача: обработать джобу на тёплом singleton (сериализуется -w 1)."""
    from .config import Settings
    from .job_runner import process_job

    process_job(Settings.from_env(), job_id, WARM_TRANSCRIBER)
    return job_id


@io_huey.periodic_task(crontab(hour="3", minute="0"))
def prune_retention_task() -> None:
    """Ночная чистка uploads/outputs по TTL (на io-воркере, без GPU)."""
    from .config import Settings
    from .retention import prune_retention

    prune_retention(Settings.from_env())


@io_huey.periodic_task(crontab(minute="*/5"))
@io_huey.lock_task("ingest-poll")
def poll_yandex_task() -> None:
    """Авто-watch: периодический опрос watch_dir Я.Диска (io, без GPU).

    Крон — */5, но фактический интервал соблюдаем по `last_scan_at` + `poll_interval`
    из настроек источника (как локальный поллер), чтобы кастомный интервал не
    игнорировался. lock_task — чтобы медленный проход не наслаивался на следующий.
    Клеймит только устоявшиеся записи (окно стабильности), дедуп через ingest_seen."""
    from datetime import datetime, timezone

    from .config import Settings
    from .repository import get_ingest_source, set_ingest_last_scan
    from .yandex import build_client_from_settings, poll_ingest_sources

    settings = Settings.from_env()
    try:
        src = get_ingest_source(settings.db_path)
    except sqlite3.OperationalError:
        return  # схема ещё не мигрирована (api не стартовал) — до следующего тика
    if not src or not src["enabled"]:
        return
    last = src.get("last_scan_at")
    if last:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < max(60, int(src["poll_interval"])):
                return
        except ValueError:
            pass
    client = build_client_from_settings(settings)
    if client is None:
        return
    set_ingest_last_scan(settings.db_path, "yandex")
    poll_ingest_sources(settings, client, enqueue_io=lambda s, k, t: pull_recording(s, k, t))


@io_huey.periodic_task(crontab(minute="*"))
@io_huey.lock_task("local-ingest-poll")
def poll_local_task() -> None:
    """Авто-watch локальной папки Zoom-выгрузок (io, без GPU; основной флоу).

    Кроны huey — минутные; фактический интервал соблюдаем по `last_scan_at`
    (poll_interval из настроек). lock_task — против наслоения проходов."""
    from datetime import datetime, timezone

    from .config import Settings
    from .local_watch import poll_local_source
    from .repository import get_ingest_source, set_ingest_last_scan

    settings = Settings.from_env()
    try:
        src = get_ingest_source(settings.db_path, "local")
    except sqlite3.OperationalError:
        return  # схема ещё не мигрирована (api не стартовал) — до следующего тика
    if not src or not src["enabled"]:
        return
    last = src.get("last_scan_at")
    if last:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < max(60, int(src["poll_interval"])):
                return
        except ValueError:
            pass
    set_ingest_last_scan(settings.db_path, "local")
    poll_local_source(settings, enqueue_gpu=lambda job_id: run_job(job_id))


@io_huey.task()
def pull_recording(surrogate_id: str, kind: str, remote_tracks: list) -> str:
    """io-задача: скачать запись с Яндекс.Диска (без GPU) → enqueue gpu run_job."""
    from .config import Settings
    from .yandex import build_client_from_settings, ingest_pull

    settings = Settings.from_env()
    # build_client_from_settings → _valid_access_token: истёкший access-токен
    # обновляется по refresh (OAuth), иначе скачивание шло бы с протухшим токеном.
    client = build_client_from_settings(settings)
    if client is None:
        return surrogate_id
    ingest_pull(
        settings,
        surrogate_id,
        kind,
        remote_tracks,
        client,
        enqueue_gpu=lambda job_id: run_job(job_id),
    )
    return surrogate_id


@io_huey.task()
def build_gallery_task(name: str, tracks: dict) -> str:
    """io-задача: собрать ECAPA-галерею из образцов (без GPU) и сохранить."""
    from .config import Settings
    from .gallery_builder import build_gallery_job

    build_gallery_job(Settings.from_env(), name, tracks)
    return name
