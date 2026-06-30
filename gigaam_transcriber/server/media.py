"""Медиа-утилиты: magic-bytes sniffing (не по суффиксу) и ffmpeg-downmix.

Валидация формата по сигнатуре (спека §8: untrusted media). .zip отклоняется
(без авто-распаковки). Downmix Route A дорожек → один воспроизводимый файл.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

# Расширения, которые разрешаем сохранять (имя на диске всё равно из uuid).
SUPPORTED_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".ogg", ".oga", ".opus",
    ".flac", ".webm", ".mkv", ".aac",
}


def sniff_media(head: bytes) -> Optional[str]:
    """Тип контейнера по сигнатуре или None. `head` — первые ≥12 байт файла."""
    if len(head) < 12:
        return None
    if head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":  # mp4 / m4a / mov / 3gp
        return "mp4"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"\x1aE\xdf\xa3":  # EBML → webm / mkv
        return "matroska"
    return None


def is_zip(head: bytes) -> bool:
    return head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def safe_suffix(filename: Optional[str]) -> str:
    """Расширение из имени, только из allowlist; иначе пусто (имя файла — из uuid)."""
    if not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in SUPPORTED_SUFFIXES else ""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def downmix_tracks(
    paths: List[Path], out_path: Path, *, timeout: int = 600
) -> Path:
    """Свести дорожки в один воспроизводимый AAC/M4A-файл (ffmpeg amix).

    Один вход — транскод в браузерный контейнер; несколько — amix на общем
    таймлайне. `-nostdin` + timeout (untrusted media sandbox — спека §8).
    """
    paths = [Path(p) for p in paths]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: List[str] = ["ffmpeg", "-nostdin", "-y"]
    for p in paths:
        cmd += ["-i", str(p)]
    n = len(paths)
    if n == 1:
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += [
            "-filter_complex",
            f"amix=inputs={n}:duration=longest:normalize=0",
            "-c:a", "aac", "-b:a", "128k",
        ]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return out_path
