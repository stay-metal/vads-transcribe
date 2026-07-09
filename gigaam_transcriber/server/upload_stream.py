"""Стриминг UploadFile на диск кусками с лимитом размера (общее для uploads/galleries).

Тело пишется на диск порциями — не читаем большой файл в память; per-file лимит
проверяется ДО дозаписи. Формат (`sniff_media`/`is_zip`) и суммарные лимиты решает
вызывающий; частичный файл при любой ошибке чистит вызывающий (общий `except`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException, UploadFile

CHUNK = 1 << 20  # 1 МиБ


async def stream_to_disk(
    f: UploadFile,
    dest: Path,
    *,
    max_size: int,
    on_chunk: Callable[[int], None] | None = None,
) -> tuple[bytes, int]:
    """Записать `f` в `dest` кусками; вернуть `(первые 64 байта, размер)`.

    Превышение `max_size` → HTTPException 413. `on_chunk(delta)` — крючок для
    суммарного лимита записи у вызывающего (тоже может бросить 413).
    """
    size = 0
    head = b""
    with open(dest, "wb") as out:
        while True:
            piece = await f.read(CHUNK)
            if not piece:
                break
            if not head:
                head = piece[:64]
            size += len(piece)
            if size > max_size:
                raise HTTPException(413, "Файл превышает лимит размера")
            if on_chunk is not None:
                on_chunk(len(piece))
            out.write(piece)
    return head, size
