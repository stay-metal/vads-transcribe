"""
GigaAM Transcriber - микробиблиотека для транскрипции аудио и видео.

Основан на GigaAM (https://github.com/salute-developers/GigaAM)
с поддержкой диаризации спикеров через pyannote.

Примеры использования:

    >>> from gigaam_transcriber import GigaAMTranscriber

    >>> # Простая транскрипция
    >>> transcriber = GigaAMTranscriber()
    >>> result = transcriber.transcribe("audio.wav")
    >>> print(result.text)

    >>> # С диаризацией
    >>> result = transcriber.transcribe("meeting.mp4", diarization="pyannote")
    >>> for seg in result.segments:
    ...     print(f"{seg.speaker}: {seg.text}")

    >>> # Сохранение в файл
    >>> result.save("transcript.json", format="json")

    >>> # Контекстный менеджер для освобождения ресурсов
    >>> with GigaAMTranscriber() as transcriber:
    ...     result = transcriber.transcribe("audio.wav")

Модели GigaAM:
- v3_e2e_rnnt (рекомендуется) - с пунктуацией и нормализацией
- v3_e2e_ctc - альтернативный декодер
- v3_rnnt, v3_ctc, v2_rnnt, v2_ctc, v1_rnnt, v1_ctc - без пунктуации

Режимы диаризации:
- "none" - без диаризации
- "pyannote" - полная диаризация через pyannote/speaker-diarization-3.1
- "hybrid" - легковесный подход: VAD + эмбеддинги + кластеризация

Форматы вывода:
- "txt" - текстовый формат с временными метками
- "json" - полный JSON с метаданными
- "srt" - субтитры SubRip
- "vtt" - субтитры WebVTT
- "md" - Markdown-протокол созвона
"""

__version__ = "0.1.0"
__author__ = "GigaAM Transcriber"

# Автозагрузка переменных окружения из .env (HF_TOKEN для VAD-модели и диаризации).
# Выполняется при импорте пакета — до того, как GigaAM прочитает HF_TOKEN в рантайме.
# Реальные переменные окружения имеют приоритет над .env (override=False).
from pathlib import Path as _Path


def _load_env_files() -> None:
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return  # python-dotenv не установлен — используем только переменные окружения
    # 1) .env, найденный от текущей рабочей директории и выше по дереву
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)
    # 2) .env в корне проекта (рядом с пакетом) — на случай запуска из другого каталога
    project_env = _Path(__file__).resolve().parent.parent / ".env"
    if project_env.is_file():
        load_dotenv(project_env, override=False)


_load_env_files()

from .audio_processor import AudioProcessor
from .data_models import (
    DiarizationMode,
    OutputFormat,
    SpeakerSegment,
    TranscriptionResult,
    TranscriptionSegment,
    WordSegment,
)
from .diarization import DiarizationManager
from .exceptions import (
    AudioProcessingError,
    DiarizationError,
    EmptyAudioError,
    EmptyFileError,
    FFmpegNotFoundError,
    HFTokenMissingError,
    ModelLoadError,
    TranscriberError,
    UnsupportedFormatError,
)
from .segment_merger import MergeConfig, SegmentMerger
from .transcriber import GigaAMTranscriber

__all__ = [
    # Версия
    "__version__",
    # Основной класс
    "GigaAMTranscriber",
    # Структуры данных
    "DiarizationMode",
    "OutputFormat",
    "TranscriptionResult",
    "TranscriptionSegment",
    "WordSegment",
    "SpeakerSegment",
    # Исключения
    "TranscriberError",
    "UnsupportedFormatError",
    "DiarizationError",
    "HFTokenMissingError",
    "ModelLoadError",
    "AudioProcessingError",
    "FFmpegNotFoundError",
    "EmptyAudioError",
    "EmptyFileError",
    # Вспомогательные классы
    "AudioProcessor",
    "DiarizationManager",
    "SegmentMerger",
    "MergeConfig",
]
