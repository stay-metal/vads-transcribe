"""Лаунчер gpu-воркера: boot-guard L5 ДО старта consumer (спека §2.5).

Запуск (см. deploy/docker-compose.yml):
    python -m gigaam_transcriber.server.run_gpu_worker -k process -w 1

Парсит -k/-w и вызывает assert_gpu_worker_config ПЕРЕД созданием Consumer и
загрузкой модели. При `-w 2` или `-k thread` процесс падает с ненулевым кодом,
не тронув GPU — делает инвариант «ровно один процесс держит GPU» load-bearing
(голый `huey_consumer` обойти guard не давал — startup-hook не видит -k/-w).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from .workers import assert_gpu_worker_config


def main(argv: Optional[list] = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="dialogscribe-gpu-worker")
    parser.add_argument("-w", "--workers", type=int, default=1)
    parser.add_argument(
        "-k",
        "--worker-type",
        dest="worker_type",
        default="process",
        choices=["process", "thread", "greenlet", "gevent"],
    )
    args, _unknown = parser.parse_known_args(argv)

    # Boot-guard: при -w>1 / -k!=process бросает RuntimeError → ненулевой выход
    # ДО импорта очереди и загрузки модели.
    assert_gpu_worker_config(args.worker_type, args.workers)

    from huey.consumer import Consumer

    from .tasks import gpu_huey

    consumer = Consumer(gpu_huey, workers=args.workers, worker_type=args.worker_type)
    consumer.run()


if __name__ == "__main__":
    main()
