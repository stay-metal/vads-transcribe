"""Общие фикстуры pytest (маркеры объявлены в pyproject.toml)."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest


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
