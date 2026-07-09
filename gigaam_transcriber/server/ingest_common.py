"""Общие для авто-watch константы/хелперы, не завязанные на HTTP-слой.

Живёт отдельно от `yandex.py`/`ingest_api.py`, чтобы воркерные модули
(`local_watch`, поллеры) не тянули HTTP-роутер ради пары значений.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Клеймим запись только когда её сигнатура неизменна ≥ N поллингов (окно
# стабильности: файлы дозалились, ревизия/размер устоялись).
STABILITY_THRESHOLD = 2


def register_job(
    settings,
    *,
    origin: str,
    kind: str,
    tracks: list[dict],
    title: str | None,
    params: dict[str, Any],
    output_dir: str | Path | None = None,
    work_dir: str,
    enqueue_gpu: Callable[[str], Any] | None = None,
) -> tuple[str, str]:
    """Общий хвост ingestion: recording → job → длительность → dirs → latest_job.

    Возвращает `(recording_id, job_id)`. `origin` — источник (`yandex`/`local`),
    он же `source` джобы. `output_dir=None` → `data_dir/outputs/<job_id>` (известен
    только после создания джобы). `enqueue_gpu` (если задан) ставит джобу в gpu-очередь.
    """
    from . import media
    from .repository import (
        create_job,
        create_recording,
        set_job_dirs,
        set_job_duration,
        set_recording_latest_job,
    )

    db = settings.db_path
    rec_id = create_recording(db, origin=origin, kind=kind, tracks=tracks, title=title)
    job_id = create_job(db, mode=kind, source=origin, recording_id=rec_id, params=params)
    if tracks:
        # Длительность аудио — заранее (ETA в UI); финиш перезапишет фактической.
        dur = media.probe_duration(Path(tracks[0]["path"]))
        if dur:
            set_job_duration(db, job_id, dur)
    out = (
        Path(output_dir) if output_dir is not None else Path(settings.data_dir) / "outputs" / job_id
    )
    set_job_dirs(
        db,
        job_id,
        work_dir=work_dir,
        output_dir=str(out),
        manifest_path=str(out / "manifest.json"),
    )
    set_recording_latest_job(db, rec_id, job_id)
    if enqueue_gpu is not None:
        enqueue_gpu(job_id)
    return rec_id, job_id


def under_watch_dir(path: str, watch_dir: str) -> bool:
    """allowlist: путь обязан быть внутри watch_dir (анти-traversal по Я.Диску).

    Нормализуем `..`/двойные слэши перед сравнением, иначе "/watch/../secret" или
    "/watchEVIL" обошли бы наивный prefix-match.
    """
    if not watch_dir or watch_dir == "/":
        return True
    norm = posixpath.normpath(path)
    base = posixpath.normpath(watch_dir)
    return norm == base or norm.startswith(base + "/")
