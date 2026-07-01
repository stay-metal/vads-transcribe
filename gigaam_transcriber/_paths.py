"""Расположение редактируемых конфигов (config/) — для глоссария и словарей lint."""

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
