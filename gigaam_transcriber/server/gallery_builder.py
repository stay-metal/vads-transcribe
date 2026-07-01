"""Построение галереи голосов (ECAPA) — io-задача, вне api.

api не грузит ML: build идёт на io-воркере. Чистая функция `build_gallery_job`
тестируется с инъектированным fake-эмбеддером (иначе тянет speechbrain). После
сборки временные образцы удаляются.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger("dialogscribe.jobs")

_SAFE_GALLERY_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def build_gallery_job(settings, name: str, tracks: dict[str, str], embedder=None) -> None:
    """`tracks` = {метка: путь_к_образцу}. Строит ECAPA-центроиды и сохраняет
    `gallery_dir/name.json`. Временные образцы чистятся в любом случае.

    `embedder` инъектируется в тестах; в проде — None (грузится лениво speechbrain)."""
    if not _SAFE_GALLERY_NAME.match(name or ""):
        raise ValueError(f"Недопустимое имя галереи: {name!r}")

    from gigaam_transcriber.voiceprint import build_gallery_from_tracks, save_gallery

    from .galleries_api import _gallery_dir

    upload_dir = None
    if tracks:
        first = Path(next(iter(tracks.values())))
        if first.parent.name == name:  # data_dir/gallery_uploads/<name>
            upload_dir = first.parent
    try:
        refs = build_gallery_from_tracks(tracks, embedder=embedder)
        if not refs:
            raise ValueError("Не удалось построить ни одного эмбеддинга (пустые/битые образцы)")
        save_gallery(refs, _gallery_dir() / f"{name}.json")
        logger.info("Галерея '%s' построена: %d голосов", name, len(refs))
    finally:
        for p in tracks.values():
            Path(p).unlink(missing_ok=True)
        if upload_dir and upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
