"""Медиа-утилиты: magic-bytes sniffing (не по суффиксу) и ffmpeg-downmix.

Валидация формата по сигнатуре (спека §8: untrusted media). .zip отклоняется
(без авто-распаковки). Downmix Route A дорожек → один воспроизводимый файл.
"""

from __future__ import annotations

import shutil
import subprocess
import unicodedata
from pathlib import Path

from ..exceptions import UnsupportedFormatError

# Расширения, которые разрешаем сохранять — единый источник из библиотечной
# константы (любой поддерживаемый аудио/видео-контейнер); имя на диске из uuid.
SUPPORTED_SUFFIXES = UnsupportedFormatError.SUPPORTED_AUDIO | UnsupportedFormatError.SUPPORTED_VIDEO


def nfc_label(filename: str | None, fallback: str) -> str:
    """NFC-нормализованный stem имени файла или `fallback` (на диске бывают NFD-имена)."""
    return unicodedata.normalize("NFC", Path(filename or fallback).stem) or fallback


def sniff_media(head: bytes) -> str | None:
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


def safe_suffix(filename: str | None) -> str:
    """Расширение из имени, только из allowlist; иначе пусто (имя файла — из uuid)."""
    if not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in SUPPORTED_SUFFIXES else ""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_duration(path: Path, *, timeout: int = 60) -> float | None:
    """Длительность медиафайла в секундах через ffprobe; None при любой ошибке."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout.strip()
        return float(out)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def concat_track_parts(
    parts: list[tuple[list[Path], float]], out_path: Path, *, timeout: int = 1800
) -> Path:
    """Склеить дорожку участника из ЧАСТЕЙ записи (стоп/старт Zoom) в один файл.

    Каждая часть — (files, duration): пусто → тишина длительностью части
    (участник отсутствовал; дорожки Zoom выровнены к началу части и имеют её
    полную длительность), несколько файлов → amix (перезаходы участника,
    каждый файл полной длительности). Части идут встык — глобальный таймлайн
    транскрипта. Всё приводится к 16 кГц mono (вход ASR), кодек AAC.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["ffmpeg", "-nostdin", "-y"]
    filters: list[str] = []
    seg_labels: list[str] = []
    in_idx = 0
    norm = "aresample=16000,aformat=sample_fmts=fltp:channel_layouts=mono"
    for i, (files, duration) in enumerate(parts):
        if not files:
            filters.append(f"anullsrc=r=16000:cl=mono:d={max(duration, 0.1):.3f}[p{i}]")
        elif len(files) == 1:
            cmd += ["-i", str(files[0])]
            filters.append(f"[{in_idx}:a]{norm}[p{i}]")
            in_idx += 1
        else:
            for f in files:
                cmd += ["-i", str(f)]
            ins = "".join(f"[{in_idx + j}:a]" for j in range(len(files)))
            filters.append(
                f"{ins}amix=inputs={len(files)}:duration=longest:normalize=0,{norm}[p{i}]"
            )
            in_idx += len(files)
        seg_labels.append(f"[p{i}]")
    filters.append(f"{''.join(seg_labels)}concat=n={len(parts)}:v=0:a=1[out]")
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return out_path


def downmix_tracks(paths: list[Path], out_path: Path, *, timeout: int = 600) -> Path:
    """Свести дорожки в один воспроизводимый AAC/M4A-файл (ffmpeg amix).

    Один вход — транскод в браузерный контейнер; несколько — amix на общем
    таймлайне. `-nostdin` + timeout (untrusted media sandbox — спека §8).
    """
    paths = [Path(p) for p in paths]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["ffmpeg", "-nostdin", "-y"]
    for p in paths:
        cmd += ["-i", str(p)]
    n = len(paths)
    if n == 1:
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += [
            "-filter_complex",
            f"amix=inputs={n}:duration=longest:normalize=0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return out_path
