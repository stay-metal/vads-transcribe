"""Общие утилиты библиотеки: хэш файла (manifest/resume) и загрузка волны 16kHz mono."""

import hashlib
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def get_file_hash(file_path: Path, algorithm: str = "md5") -> str:
    """Хэш файла (hex) потоковым чтением — для manifest/resume."""
    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_waveform_16k_mono(audio_path: Path | str) -> np.ndarray:
    """Волна float32 16kHz mono — общий вход для whisper L2 и voiceprint."""
    # torchaudio лениво: тянет torch, а get_file_hash нужен и без него.
    import torchaudio

    wav, sr = torchaudio.load(str(audio_path))
    if sr != SAMPLE_RATE:
        wav = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(wav)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    return wav[0].numpy().astype(np.float32)
