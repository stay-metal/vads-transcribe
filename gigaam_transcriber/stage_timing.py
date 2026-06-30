"""Постадийный wall-clock тайминг — перенос из custom (stage_timing.py).

Лёгкий контекст-менеджер: ``with timer.measure('decode'): ...`` копит время по стадии;
``as_dict()`` отдаёт суммарный профиль для metadata (baseline и ловля регресса/выигрыша).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Dict, Iterator


class StageTimer:
    """Аккумулятор wall-clock по стадиям пайплайна (потокобезопасность не требуется —
    стадии транскрипции последовательны)."""

    def __init__(self) -> None:
        self._times: Dict[str, float] = {}

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        t0 = time.monotonic()
        try:
            yield
        finally:
            self._times[stage] = self._times.get(stage, 0.0) + (time.monotonic() - t0)

    def add(self, stage: str, seconds: float) -> None:
        self._times[stage] = self._times.get(stage, 0.0) + float(seconds)

    def as_dict(self) -> Dict[str, float]:
        return {k: round(v, 3) for k, v in self._times.items()}

    def total(self) -> float:
        return round(sum(self._times.values()), 3)
