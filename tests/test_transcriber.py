"""
Тесты для модуля transcriber (основной класс).

Примечание: тесты, требующие загрузки модели GigaAM, помечены как @pytest.mark.requires_model
и по умолчанию пропускаются. Для запуска используйте: pytest -m requires_model
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from gigaam_transcriber import (
    GigaAMTranscriber,
    TranscriptionResult,
    TranscriptionSegment,
    UnsupportedFormatError,
)


class TestGigaAMTranscriberInit:
    """Тесты инициализации GigaAMTranscriber."""

    def test_default_init(self):
        """Тест инициализации с параметрами по умолчанию."""
        # Не загружаем модель при инициализации (lazy loading)
        transcriber = GigaAMTranscriber()

        assert transcriber.model_name == "v3_e2e_rnnt"
        assert transcriber._model is None  # Lazy loading

    def test_custom_model_name(self):
        """Тест с кастомным именем модели."""
        transcriber = GigaAMTranscriber(model_name="v3_e2e_ctc")

        assert transcriber.model_name == "v3_e2e_ctc"

    def test_device_resolution_auto(self):
        """Тест автоопределения устройства."""
        transcriber = GigaAMTranscriber(device="auto")

        # Должно быть либо cuda, либо cpu
        assert transcriber.device in ["cuda", "cpu"]

    def test_device_explicit(self):
        """Тест с явным указанием устройства."""
        transcriber = GigaAMTranscriber(device="cpu")

        assert transcriber.device == "cpu"

    def test_hf_token_from_env(self):
        """Тест получения HF_TOKEN из переменной окружения."""
        with patch.dict(os.environ, {"HF_TOKEN": "test_token"}):
            transcriber = GigaAMTranscriber()

            assert transcriber.hf_token == "test_token"

    def test_hf_token_explicit(self):
        """Тест с явным указанием токена."""
        transcriber = GigaAMTranscriber(hf_token="explicit_token")

        assert transcriber.hf_token == "explicit_token"

    def test_cache_dir_default(self, monkeypatch, tmp_path):
        """Дефолтный кэш — в $HOME; тест не сорит в реальный дом (HOME → tmp)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        transcriber = GigaAMTranscriber()

        assert transcriber.cache_dir.exists()
        assert str(transcriber.cache_dir).startswith(str(tmp_path))
        assert "gigaam_transcriber" in str(transcriber.cache_dir)

    def test_cache_dir_custom(self, temp_dir):
        """Тест с кастомной директорией кэша."""
        cache_dir = temp_dir / "custom_cache"
        transcriber = GigaAMTranscriber(cache_dir=cache_dir)

        assert transcriber.cache_dir == cache_dir
        assert cache_dir.exists()


class TestGigaAMTranscriberContextManager:
    """Тесты контекстного менеджера."""

    def test_context_manager_enter(self):
        """Тест входа в контекст."""
        with GigaAMTranscriber() as transcriber:
            assert isinstance(transcriber, GigaAMTranscriber)

    def test_context_manager_cleanup(self):
        """Тест очистки при выходе из контекста."""
        transcriber = GigaAMTranscriber()
        transcriber._model = Mock()  # Симуляция загруженной модели

        transcriber.cleanup()

        assert transcriber._model is None


class TestGigaAMTranscriberGetModelInfo:
    """Тесты метода get_model_info."""

    def test_get_model_info(self):
        """Тест получения информации о модели."""
        transcriber = GigaAMTranscriber()
        info = transcriber.get_model_info()

        assert "model_name" in info
        assert "device" in info
        assert "loaded" in info
        assert info["model_name"] == "v3_e2e_rnnt"
        assert info["loaded"] is False  # Модель ещё не загружена


class TestGigaAMTranscriberValidation:
    """Тесты валидации входных данных."""

    def test_validate_unsupported_format(self, temp_dir):
        """Тест с неподдерживаемым форматом."""
        transcriber = GigaAMTranscriber()

        # Создаём файл с неподдерживаемым расширением
        bad_file = temp_dir / "test.xyz"
        bad_file.write_text("test")

        with pytest.raises(UnsupportedFormatError):
            transcriber._validate_input(bad_file)

    def test_validate_nonexistent_file(self):
        """Тест с несуществующим файлом."""
        transcriber = GigaAMTranscriber()

        with pytest.raises(FileNotFoundError):
            transcriber._validate_input(Path("/nonexistent/file.wav"))


@pytest.mark.requires_model
class TestGigaAMTranscriberWithModel:
    """
    Тесты, требующие загрузки модели GigaAM.

    Запуск: pytest -m requires_model
    """

    @pytest.fixture(scope="class")
    def transcriber(self):
        """Фикстура транскрибера с загруженной моделью."""
        t = GigaAMTranscriber(device="cpu")
        t.preload()
        yield t
        t.cleanup()

    def test_model_loaded(self, transcriber):
        """Тест загрузки модели."""
        assert transcriber._model is not None

    def test_preload(self):
        """Тест предзагрузки модели."""
        transcriber = GigaAMTranscriber(device="cpu")

        assert transcriber._model is None
        transcriber.preload()
        assert transcriber._model is not None

        transcriber.cleanup()


def _fake_result(text: str = "привет") -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        segments=[TranscriptionSegment(text=text, start=0.0, end=1.0)],
        duration=1.0,
        language="ru",
        model_name="fake",
        processing_time=0.0,
    )


class TestTranscribePostProcessing:
    """Регрессии пост-проходов transcribe() (resume/видео) — без загрузки модели."""

    def test_resume_writes_output_file(self, monkeypatch, tmp_path):
        """resume=True пропускает только ASR — output_path всё равно пишется на диск (bug_005)."""
        import gigaam_transcriber.manifest as manifest_mod

        t = GigaAMTranscriber(device="cpu")
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr(t, "_validate_input", lambda p: None)

        cached = _fake_result("кэшированный текст")
        monkeypatch.setattr(manifest_mod, "resume_result", lambda mp, ip, request=None: cached)

        out = tmp_path / "out.txt"
        res = t.transcribe(audio, output_path=out, resume=True)

        assert res is cached
        assert out.exists()
        assert "кэшированный текст" in out.read_text(encoding="utf-8")

    def test_video_threads_preclean_and_post_audio(self, monkeypatch, tmp_path):
        """Видео: preclean доходит до ASR, а L2 читает извлечённый wav, не видео-контейнер;
        temp убирается в конце (bug_001)."""
        import gigaam_transcriber.whisper_asr as whisper_mod

        t = GigaAMTranscriber(device="cpu")
        video = tmp_path / "meeting.mp4"
        video.write_bytes(b"x")
        extracted = tmp_path / "extracted.wav"
        extracted.write_bytes(b"x")

        proc = MagicMock()
        proc.is_video_file.return_value = True
        proc.is_supported_file.return_value = True
        proc.extract_audio_from_video.return_value = extracted
        t._audio_processor = proc

        captured = {}

        def fake_audio(audio_path, diarization="none", preclean_filter=None, **kw):
            captured["preclean_filter"] = preclean_filter
            captured["asr_path"] = audio_path
            return _fake_result()

        monkeypatch.setattr(t, "_transcribe_audio", fake_audio)

        def fake_l2(result, audio_path, amap, **kw):
            captured["l2_path"] = audio_path
            return 0

        monkeypatch.setattr(whisper_mod, "apply_second_opinion", fake_l2)

        t.transcribe(video, preclean=True, second_opinion=True, glossary=False)

        assert captured["preclean_filter"]  # preclean дошёл до ASR на видео-ветке
        assert captured["asr_path"] == extracted  # ASR — на извлечённом wav
        assert captured["l2_path"] == extracted  # L2 читает wav, а не .mp4
        assert not extracted.exists()  # temp убран в конце
