"""Huey-очереди (спека §2): две очереди в отдельной huey.sqlite.

- `gpu` — транскрипция (единственный держатель GPU);
- `io`  — скачивание Я.Диска / поллер (без GPU), чтобы сетевой I/O не занимал
  единственный GPU-слот (head-of-line блокировка).

Фабрики; module-level задачи появятся в M3 вместе с джоб-пайплайном.
"""

from __future__ import annotations

from pathlib import Path

from huey import SqliteHuey


def huey_db_path(data_dir: Path) -> Path:
    return Path(data_dir) / "huey.sqlite"


def make_gpu_huey(data_dir: Path) -> SqliteHuey:
    return SqliteHuey(name="gpu", filename=str(huey_db_path(data_dir)))


def make_io_huey(data_dir: Path) -> SqliteHuey:
    return SqliteHuey(name="io", filename=str(huey_db_path(data_dir)))
