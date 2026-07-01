"""Точка входа Huey-воркеров: инстансы очередей `gpu_huey` / `io_huey` из окружения.

Воркеры запускаются как (см. deploy/docker-compose.yml):
    huey_consumer gigaam_transcriber.server.tasks.gpu_huey -k process -w 1   # GPU
    huey_consumer gigaam_transcriber.server.tasks.io_huey  -w 2              # I/O

При старте gpu-воркера прогревается тёплый singleton (warm_up) и выставляется
ready-флаг для /readyz. Прикладные задачи (ASR-джобы) добавятся в M3.
"""

from __future__ import annotations

import os
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
    from .workers import warm_up

    WARM_TRANSCRIBER = warm_up(Settings.from_env())


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

    lock_task — чтобы медленный проход не наслаивался на следующий. Клеймит
    только устоявшиеся записи (окно стабильности), дедуп через ingest_seen."""
    from .config import Settings
    from .repository import get_ingest_source
    from .yandex import build_client_from_settings, poll_ingest_sources

    settings = Settings.from_env()
    src = get_ingest_source(settings.db_path)
    if not src or not src["enabled"]:
        return
    client = build_client_from_settings(settings)
    if client is None:
        return
    poll_ingest_sources(settings, client, enqueue_io=lambda s, k, t: pull_recording(s, k, t))


@io_huey.task()
def pull_recording(surrogate_id: str, kind: str, remote_tracks: list) -> str:
    """io-задача: скачать запись с Яндекс.Диска (без GPU) → enqueue gpu run_job."""
    from .config import Settings
    from .crypto import decrypt
    from .repository import get_yandex_auth
    from .yandex import YaDiskClient, ingest_pull

    settings = Settings.from_env()
    auth = get_yandex_auth(settings.db_path)
    token = decrypt(settings.fernet_key, auth["token_enc"]) if auth else None
    if not token:
        return surrogate_id
    ingest_pull(
        settings,
        surrogate_id,
        kind,
        remote_tracks,
        YaDiskClient(token),
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
