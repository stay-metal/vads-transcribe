"""
Кастомные исключения для GigaAM Transcriber.
"""


class TranscriberError(Exception):
    """Базовое исключение для всех ошибок транскрибера."""

    pass


class UnsupportedFormatError(TranscriberError):
    """Неподдерживаемый формат файла.

    Наборы расширений здесь — единый источник истины: их импортируют
    AudioProcessor (валидация входа) и сервер (фильтры ingest); модуль
    exceptions лёгкий, поэтому его можно тянуть из любого слоя.
    """

    SUPPORTED_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}
    SUPPORTED_VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".mpeg", ".mpg"}

    def __init__(self, file_format: str):
        self.file_format = file_format
        all_supported = self.SUPPORTED_AUDIO | self.SUPPORTED_VIDEO
        super().__init__(
            f"Формат '{file_format}' не поддерживается. "
            f"Поддерживаемые форматы: {', '.join(sorted(all_supported))}"
        )


class DiarizationError(TranscriberError):
    """Ошибка при диаризации спикеров."""

    def __init__(self, message: str, cause: Exception | None = None):
        self.cause = cause
        full_message = f"Ошибка диаризации: {message}"
        if cause:
            full_message += f" (причина: {cause})"
        super().__init__(full_message)


class HFTokenMissingError(DiarizationError):
    """HuggingFace токен не установлен для pyannote."""

    def __init__(self):
        super().__init__(
            "HF_TOKEN не установлен. Для диаризации необходим токен HuggingFace. "
            "Установите переменную окружения HF_TOKEN или передайте параметр hf_token."
        )


class ModelLoadError(TranscriberError):
    """Ошибка при загрузке модели."""

    def __init__(self, model_name: str, cause: Exception | None = None):
        self.model_name = model_name
        self.cause = cause
        message = f"Не удалось загрузить модель '{model_name}'"
        if cause:
            message += f": {cause}"
        super().__init__(message)


class AudioProcessingError(TranscriberError):
    """Ошибка при обработке аудио."""

    def __init__(self, message: str, file_path: str | None = None, cause: Exception | None = None):
        self.file_path = file_path
        self.cause = cause
        full_message = "Ошибка обработки аудио"
        if file_path:
            full_message += f" ({file_path})"
        full_message += f": {message}"
        if cause:
            full_message += f" (причина: {cause})"
        super().__init__(full_message)


class FFmpegNotFoundError(AudioProcessingError):
    """FFmpeg не найден в системе."""

    def __init__(self):
        super().__init__(
            "FFmpeg не найден. Установите ffmpeg и добавьте в PATH. "
            "Инструкции: https://ffmpeg.org/download.html"
        )


class EmptyFileError(TranscriberError):
    """Файл пустой."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        super().__init__(f"Файл пустой: {file_path}")


class EmptyAudioError(TranscriberError):
    """Аудио не содержит речи или полностью тихое."""

    def __init__(self, file_path: str | None = None):
        self.file_path = file_path
        message = "Аудио не содержит распознаваемой речи"
        if file_path:
            message += f": {file_path}"
        super().__init__(message)
