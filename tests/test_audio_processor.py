"""
Тесты для модуля audio_processor.
"""

from pathlib import Path

import pytest

from gigaam_transcriber import (
    AudioProcessor,
    FFmpegNotFoundError,
)


class TestAudioProcessor:
    """Тесты для AudioProcessor."""

    def test_is_audio_file(self):
        """Тест определения аудио файлов."""
        assert AudioProcessor.is_audio_file("test.wav")
        assert AudioProcessor.is_audio_file("test.mp3")
        assert AudioProcessor.is_audio_file("test.flac")
        assert AudioProcessor.is_audio_file(Path("test.ogg"))

        assert not AudioProcessor.is_audio_file("test.mp4")
        assert not AudioProcessor.is_audio_file("test.txt")

    def test_is_video_file(self):
        """Тест определения видео файлов."""
        assert AudioProcessor.is_video_file("test.mp4")
        assert AudioProcessor.is_video_file("test.mkv")
        assert AudioProcessor.is_video_file("test.avi")
        assert AudioProcessor.is_video_file(Path("test.webm"))

        assert not AudioProcessor.is_video_file("test.wav")
        assert not AudioProcessor.is_video_file("test.txt")

    def test_is_supported_file(self):
        """Тест определения поддерживаемых файлов."""
        # Аудио
        assert AudioProcessor.is_supported_file("test.wav")
        assert AudioProcessor.is_supported_file("test.mp3")

        # Видео
        assert AudioProcessor.is_supported_file("test.mp4")
        assert AudioProcessor.is_supported_file("test.mkv")

        # Не поддерживается
        assert not AudioProcessor.is_supported_file("test.txt")
        assert not AudioProcessor.is_supported_file("test.pdf")

    def test_initialization(self):
        """Тест инициализации процессора."""
        try:
            processor = AudioProcessor()
            assert processor.ffmpeg_path is not None
        except FFmpegNotFoundError:
            pytest.skip("FFmpeg не установлен")
