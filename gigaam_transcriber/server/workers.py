"""gpu-worker: boot-guard (L5) + warm-preload тёплого singleton + ready-флаг.

Критический инвариант (спека §2): GPU держит РОВНО ОДИН держатель модели.
`GigaAMTranscriber` не реентерабелен (хранит per-call состояние), поэтому
несколько воркеров = гонки и OOM. Допустимые формы — `-k process -w 1`
(Linux-прод) и `-k thread -w 1` (macOS/F6: Metal/MPS не инициализируется в
форкнутом без exec ребёнке, единственный поток-воркер живёт в основном
процессе). `assert_gpu_worker_config` делает инвариант load-bearing: лаунчер
`run_gpu_worker.py` вызывает его ДО старта consumer, поэтому при `-w 2` или
`-k greenlet` воркер отказывается стартовать (голый `huey_consumer` guard не
вызывал — startup-hook не получает -k/-w).

Этот модуль импортирует тяжёлую библиотеку (нужен только gpu-воркеру) и НЕ
импортируется процессом api — чтобы api оставался без модели.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings


def assert_gpu_worker_config(worker_type: str, workers: int) -> None:
    """Boot-guard L5: разрешён ровно один держатель GPU.

    `process` — прод (Linux/Docker): изоляция + самовосстановление (huey
    перезапускает умершего ребёнка). `thread` — ТОЛЬКО darwin (Metal/MPS не
    живёт в fork; F6): единственный поток-воркер, но native-краш убивает весь
    процесс — гоняй под супервизором. greenlet/gevent чередуют гринлеты ПОСРЕДИ
    задачи — запрещены везде.

    Raises:
        RuntimeError: если конфигурация позволяет >1 держателя GPU.
    """
    if worker_type == "thread" and sys.platform != "darwin":
        raise RuntimeError(
            "-k thread допустим только на macOS (обход Metal/fork); "
            "на Linux используй -k process — он даёт изоляцию и авто-рестарт воркера."
        )
    if worker_type not in ("process", "thread"):
        raise RuntimeError(
            f"gpu-worker должен быть -k process или -k thread (получено -k {worker_type}): "
            "GigaAMTranscriber не реентерабелен, гринлеты разделяют состояние."
        )
    if workers != 1:
        raise RuntimeError(
            f"gpu-worker должен быть -w 1 (получено -w {workers}): "
            "несколько воркеров = несколько копий модели в VRAM = OOM."
        )


def _default_transcriber_factory(settings: Settings):
    # Ленивый импорт: модель тянется только в gpu-воркере, не на уровне модуля.
    import os

    from gigaam_transcriber import GigaAMTranscriber

    return GigaAMTranscriber(
        device=os.getenv("DIALOGSCRIBE_DEVICE", "auto"),
        hf_token=os.getenv("HF_TOKEN"),
    )


# Владелец ready-флага: лаунчер кладёт одноразовый токен в env, warm_up пишет
# его в файл (env переживает fork process-воркера), atexit-очистка снимает флаг
# только со СВОИМ токеном — чужой (нового воркера при перекрывающемся рестарте)
# не трогает. Дефолт "ready" сохраняет поведение вне лаунчера (тесты, ручной warm_up).
READY_TOKEN_ENV = "DIALOGSCRIBE_READY_TOKEN"


def write_ready_flag(ready_flag_path: Path) -> None:
    path = Path(ready_flag_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(os.getenv(READY_TOKEN_ENV, "ready"))


def clear_ready_flag(ready_flag_path: Path, only_token: str | None = None) -> None:
    """Снять флаг; с `only_token` — только если флаг записан этим владельцем."""
    path = Path(ready_flag_path)
    if not path.exists():
        return
    if only_token is not None:
        try:
            if path.read_text() != only_token:
                return
        except OSError:
            return
    path.unlink(missing_ok=True)


def warm_up(
    settings: Settings,
    transcriber_factory: Callable[[Settings], Any] | None = None,
):
    """Прогреть тёплый singleton и выставить ready-флаг (для /readyz).

    Возвращает прогретый транскрайбер, который gpu-воркер переиспользует между
    задачами (никогда не через per-request context-manager).
    """
    factory = transcriber_factory or _default_transcriber_factory
    transcriber = factory(settings)
    transcriber.preload()
    write_ready_flag(settings.ready_flag_path)
    return transcriber
