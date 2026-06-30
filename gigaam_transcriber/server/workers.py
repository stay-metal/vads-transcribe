"""gpu-worker: boot-guard (L5) + warm-preload тёплого singleton + ready-флаг.

Критический инвариант (спека §2): GPU держит РОВНО ОДИН процесс —
`huey_consumer … -q gpu -k process -w 1`. `GigaAMTranscriber` не реентерабелен
(хранит per-call состояние), поэтому несколько воркеров/тредов = гонки и OOM.
`assert_gpu_worker_config` делает инвариант load-bearing: лаунчер
`run_gpu_worker.py` вызывает его ДО старта consumer, поэтому при `-w 2` или
`-k thread` воркер отказывается стартовать (голый `huey_consumer` guard не
вызывал — startup-hook не получает -k/-w).

Этот модуль импортирует тяжёлую библиотеку (нужен только gpu-воркеру) и НЕ
импортируется процессом api — чтобы api оставался без модели.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .config import Settings


def assert_gpu_worker_config(worker_type: str, workers: int) -> None:
    """Boot-guard L5: разрешён только один процессный воркер.

    Raises:
        RuntimeError: если конфигурация позволяет >1 держателя GPU.
    """
    if worker_type != "process":
        raise RuntimeError(
            f"gpu-worker должен быть -k process (получено -k {worker_type}): "
            "GigaAMTranscriber не реентерабелен, потоки/гринлеты разделяют состояние."
        )
    if workers != 1:
        raise RuntimeError(
            f"gpu-worker должен быть -w 1 (получено -w {workers}): "
            "несколько процессов = несколько копий модели в VRAM = OOM."
        )


def _default_transcriber_factory(settings: Settings):
    # Ленивый импорт: модель тянется только в gpu-воркере, не на уровне модуля.
    from gigaam_transcriber import GigaAMTranscriber

    import os

    return GigaAMTranscriber(
        device=os.getenv("DIALOGSCRIBE_DEVICE", "auto"),
        hf_token=os.getenv("HF_TOKEN"),
    )


def write_ready_flag(ready_flag_path: Path) -> None:
    path = Path(ready_flag_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ready")


def clear_ready_flag(ready_flag_path: Path) -> None:
    path = Path(ready_flag_path)
    if path.exists():
        path.unlink()


def warm_up(
    settings: Settings,
    transcriber_factory: Optional[Callable[[Settings], object]] = None,
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
