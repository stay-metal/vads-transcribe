"""Общие фикстуры pytest (маркеры объявлены в pyproject.toml).

Серверная обвязка (креды, WAV-сигнатура, фабрики Settings/клиента, FakeTranscriber,
Zoom-папка) — здесь, чтобы не дублировать её по test_server_*.py. Импортируется как
`from conftest import PASSWORD, server_settings, login_client, FakeTranscriber, ...`.
"""

import json
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment

# --- Общие серверные константы ---
PASSWORD = "correct-horse-battery-staple"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32  # валидная WAV-сигнатура
MAGIC = "399019170"  # Zoom magic_number синтетической папки


def server_settings(tmp_path: Path, **overrides):
    """Settings для серверных тестов; overrides перекрывают дефолты (напр. data_dir)."""
    from gigaam_transcriber.server.config import Settings
    from gigaam_transcriber.server.security import hash_password

    base = {
        "user": "admin",
        "password_hash": hash_password(PASSWORD),
        "session_key": "session-key-aaaaaaaaaaaaaaaa",
        "fernet_key": "fernet-key-bbbbbbbbbbbbbbbb",
        "data_dir": tmp_path,
        "cookie_secure": False,
        "require_https": False,
    }
    base.update(overrides)
    return Settings(**base)


def login_client(app, username: str = "admin", password: str = PASSWORD):
    """TestClient с выполненным form-login (cookie сессии установлена)."""
    from fastapi.testclient import TestClient

    c = TestClient(app)
    c.post("/api/auth/login", data={"username": username, "password": password})
    return c


class FakeTranscriber:
    """Единый фейк-транскрайбер: route_a + single, `**kwargs` терпимы к любым опциям.

    route_a → по спикеру на дорожку (метка = имя дорожки); single → один SPEAKER_00.
    Прогресс route_a прокидывается в `progress_callback` (стадия ASR в тестах)."""

    def transcribe_route_a(self, tracks, progress_callback=None, **kwargs):
        names = list(tracks)
        if progress_callback:
            for i, n in enumerate(names, 1):
                progress_callback(i, len(names), n)
        segs = [
            TranscriptionSegment(text=f"реплика {n}", start=float(i), end=float(i) + 1, speaker=n)
            for i, n in enumerate(names)
        ]
        return TranscriptionResult(
            text=" ".join(s.text for s in segs),
            segments=segs,
            duration=10.0,
            language="ru",
            model_name="fake",
            processing_time=1.0,
            metadata={"route": "A", "tracks": names},
        )

    def transcribe(self, input_path, diarization="pyannote", **kwargs):
        segs = [TranscriptionSegment(text="привет", start=0.0, end=1.0, speaker="SPEAKER_00")]
        return TranscriptionResult(
            text="привет",
            segments=segs,
            duration=5.0,
            language="ru",
            model_name="fake",
            processing_time=0.5,
            metadata={},
        )


def make_zoom_folder(
    root: Path,
    name: str = "2026-07-08 12.05.53 Дейли",
    participants: tuple = (("ТимурЯйк", 1), ("PonimaiuAI", 2), ("Ольга", 4), ("Ольга", 6)),
    magic: str = MAGIC,
    with_conf: bool = True,
) -> Path:
    """Синтетическая Zoom-папка (структура — как в реальной выгрузке folder_example)."""
    folder = root / name
    folder.mkdir(parents=True)
    (folder / f"audio1{magic}.m4a").write_bytes(b"\x00" * 8)
    (folder / f"video1{magic}.mp4").write_bytes(b"\x00" * 8)
    (folder / "zoomver.tag").write_text("tag")
    if with_conf:
        (folder / "recording.conf").write_text(
            json.dumps({"magic_number": magic, "items": [{"process": 100}]})
        )
    if participants:
        rec = folder / "Audio Record"
        rec.mkdir()
        for pname, idx in participants:
            (rec / f"audio{pname}{idx}1{magic}.m4a").write_bytes(b"\x00" * 4)
    # Чужой вывод оунера — не контент встречи.
    (folder / "transcripts").mkdir()
    (folder / "transcripts" / "чужое.md").write_text("x")
    return folder


@pytest.fixture(scope="function")
def temp_dir() -> Generator[Path, None, None]:
    """Временная директория для тестов."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="function")
def sample_transcription_segment():
    """Пример сегмента транскрипции."""
    from gigaam_transcriber import TranscriptionSegment

    return TranscriptionSegment(
        text="Привет, как дела?",
        start=0.0,
        end=2.5,
        speaker="Спикер №1",
    )


@pytest.fixture(scope="function")
def sample_transcription_result(sample_transcription_segment):
    """Пример результата транскрипции."""
    from gigaam_transcriber import TranscriptionResult, TranscriptionSegment

    segments = [
        sample_transcription_segment,
        TranscriptionSegment(
            text="Отлично, спасибо!",
            start=2.5,
            end=4.0,
            speaker="Спикер №2",
        ),
        TranscriptionSegment(
            text="А у тебя как?",
            start=4.0,
            end=5.5,
            speaker="Спикер №2",
        ),
    ]

    return TranscriptionResult(
        text="Привет, как дела? Отлично, спасибо! А у тебя как?",
        segments=segments,
        duration=5.5,
        language="ru",
        model_name="v3_e2e_rnnt",
        processing_time=1.5,
        metadata={"source": "test.wav"},
    )
