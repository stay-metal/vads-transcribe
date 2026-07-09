"""Лаунчер gpu-воркера: boot-guard L5 ДО старта consumer (спека §2.5).

Запуск (см. deploy/docker-compose.yml):
    python -m gigaam_transcriber.server.run_gpu_worker -k process -w 1

Парсит -k/-w и вызывает assert_gpu_worker_config ПЕРЕД созданием Consumer и
загрузкой модели. При `-w 2` или `-k greenlet` процесс падает с ненулевым
кодом, не тронув GPU — делает инвариант «ровно один держатель GPU»
load-bearing (голый `huey_consumer` обойти guard не давал — startup-hook не
видит -k/-w).

macOS (F6): `-k process` автоматически подменяется на `-k thread` — Metal/MPS
не инициализируется в форкнутом без exec ребёнке (`MTLCompilerService`
недоступен), fork-воркер с `BLOODTRANSCRIPTS_DEVICE=mps` уходил в крашлуп
«Worker 1 died». Единственный поток-воркер в основном процессе сохраняет
инвариант одного держателя модели, но native-краш убивает весь процесс —
на macOS гоняй воркер под супервизором (launchd/`while true`). Linux (прод) —
без изменений.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import sys
import uuid

from .config import Settings
from .workers import READY_TOKEN_ENV, assert_gpu_worker_config, clear_ready_flag

logger = logging.getLogger(__name__)


def _effective_worker_type(worker_type: str) -> str:
    """macOS: process→thread (Metal/MPS не живёт в fork, F6); остальное — как есть."""
    if sys.platform == "darwin" and worker_type == "process":
        logger.warning(
            "gpu-worker: macOS — '-k process' заменён на '-k thread' "
            "(Metal/MPS не инициализируется в fork-процессе)."
        )
        return "thread"
    return worker_type


def _ensure_forkable_start_method() -> None:
    """process-воркер на macOS: huey не стартует при дефолтном 'spawn' — он
    пиклит объект процесса, а `Consumer._create_process` держит непиклящееся
    замыкание `_run` → `AttributeError: Can't pickle local object` ДО загрузки
    модели. Форсим 'fork' + страхуем Obj-C-раннтайм. На Linux (прод/Docker)
    fork и так дефолт, вызов идемпотентен → прод не затронут.

    Вызывается ТОЛЬКО для effective worker_type == 'process': при thread-воркере
    (дефолт после F6 на macOS) модель греется в основном процессе, и глобальный
    форс fork лишь подставил бы под Metal-краш форки внутрибиблиотечного
    multiprocessing (DataLoader и т.п.) — дефолтный spawn там безопаснее.
    """
    if sys.platform != "darwin":
        return

    import multiprocessing as mp

    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    try:
        mp.set_start_method("fork", force=True)
    except (RuntimeError, ValueError):  # уже сконфигурировано/недоступно — не фатально
        pass


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="bloodtranscripts-gpu-worker")
    parser.add_argument("-w", "--workers", type=int, default=1)
    parser.add_argument(
        "-k",
        "--worker-type",
        dest="worker_type",
        default="process",
        choices=["process", "thread", "greenlet", "gevent"],
    )
    args, _unknown = parser.parse_known_args(argv)

    worker_type = _effective_worker_type(args.worker_type)

    # Boot-guard: при -w>1 / -k∉{process,thread} бросает RuntimeError → ненулевой
    # выход ДО импорта очереди и загрузки модели.
    assert_gpu_worker_config(worker_type, args.workers)

    # SIGHUP-restart huey делает `os.execl(python, python, *sys.argv)`; при
    # запуске `python -m …` sys.argv[0] — путь к ФАЙЛУ модуля, и exec уронил бы
    # новый процесс на relative-import. Нормализуем argv в -m-форму, чтобы
    # рестарт был эквивалентен исходному запуску.
    sys.argv = ["-m", "gigaam_transcriber.server.run_gpu_worker", *argv]

    # F8: ready-флаг живёт ровно пока жив тёплый воркер. На старте чистим
    # stale-флаг убитого предшественника (модель точно НЕ тёплая — warm_up
    # перепишет), на выходе снимаем через atexit: huey-хук on_shutdown не
    # покрывает SIGTERM (non-graceful stop бросает daemon-треды без finally),
    # а atexit срабатывает на TERM/INT/нормальном выходе. Снятие — только со
    # своим токеном (env переживает fork process-воркера): флаг, переписанный
    # новым воркером при перекрывающемся рестарте, не трогаем. kill -9 оставит
    # stale-флаг — его уберёт следующий старт.
    ready_token = uuid.uuid4().hex
    os.environ[READY_TOKEN_ENV] = ready_token
    ready_flag = Settings.from_env().ready_flag_path
    clear_ready_flag(ready_flag)
    atexit.register(clear_ready_flag, ready_flag, only_token=ready_token)

    if worker_type == "process":
        # macOS: fork-старт до создания Consumer (spawn не пиклит huey-воркеры).
        _ensure_forkable_start_method()

    from huey.consumer import Consumer

    from .tasks import gpu_huey

    consumer = Consumer(gpu_huey, workers=args.workers, worker_type=worker_type)
    consumer.run()


if __name__ == "__main__":
    main()
