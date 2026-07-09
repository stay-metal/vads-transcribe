"""Расположение конфигов (config/) и пользовательского кэша библиотеки."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    """Папка config/ в корне репозитория; переопределяется $GIGAAM_TRANSCRIBER_CONFIG.

    Перенесено из custom (zoom_transcriber/_paths.py). По умолчанию — config/ рядом
    с пакетом (на уровень выше gigaam_transcriber/); .env (HF_TOKEN и пр.) грузится
    отдельно в __init__."""
    env = os.environ.get("GIGAAM_TRANSCRIBER_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[1] / "config"


def cache_dir() -> Path:
    """Пользовательский кэш (whisper L2, лог L2-правок); $GIGAAM_TRANSCRIBER_CACHE переопределяет.

    Не внутри пакета: при pip-установке site-packages может быть read-only,
    и mkdir рядом с кодом ронял бы весь L2-проход."""
    env = os.environ.get("GIGAAM_TRANSCRIBER_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "bloodtranscripts"
