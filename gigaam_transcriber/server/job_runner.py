"""Обработчик джобы на тёплом singleton (спека §4, §7).

Чистая функция `process_job(settings, job_id, transcriber)` — её зовёт и gpu-task
huey (с WARM_TRANSCRIBER), и тесты (с fake-транскрайбером). Пишет стадии
(state, stage_pct), на финале — result.json + ffmpeg-downmix для плеера. Ошибки
исполнения → state='error' + санитизированный error_code (без утечки путей).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import media
from .config import Settings
from .repository import (
    claim_job,
    fail_job,
    finish_job_ok,
    get_job,
    get_recording,
    update_job_progress,
)

logger = logging.getLogger("dialogscribe.jobs")

# Карта классов исключений библиотеки → (error_code, безопасное сообщение).
_ERROR_MAP = {
    "AudioTooShortError": ("audio_too_short", "Аудио слишком короткое"),
    "AudioTooLongError": ("audio_too_long", "Аудио длиннее лимита"),
    "UnsupportedFormatError": ("unsupported_format", "Неподдерживаемый формат"),
    "EmptyAudioError": ("empty_audio", "Речь не обнаружена"),
    "EmptyFileError": ("empty_file", "Пустой файл"),
    "FileNotFoundError": ("file_not_found", "Файл не найден"),
    "ModelLoadError": ("model_load", "Не удалось загрузить модель"),
    "DiarizationError": ("diarization_failed", "Ошибка диаризации"),
    "FFmpegNotFoundError": ("ffmpeg_missing", "ffmpeg недоступен"),
    "AudioProcessingError": ("audio_processing", "Ошибка обработки аудио"),
}


def classify_error(exc: Exception) -> tuple[str, str]:
    return _ERROR_MAP.get(type(exc).__name__, ("internal_error", "Внутренняя ошибка обработки"))


def _stage_callback(settings: Settings, job_id: str):
    """progress_callback(current,total,name) → stage_pct в диапазоне ASR 45..85."""

    def cb(current: int, total: int, name: str) -> None:
        frac = (current / total) if total else 0.0
        pct = 45 + int(frac * 40)
        update_job_progress(settings.db_path, job_id, "asr", min(pct, 85))

    return cb


def _single_stage_callback(settings: Settings, job_id: str):
    """progress_callback(current,total) для single — per-VAD-сегмент → ASR 45..85."""

    def cb(current: int, total: int) -> None:
        frac = (current / total) if total else 0.0
        update_job_progress(settings.db_path, job_id, "asr", min(45 + int(frac * 40), 85))

    return cb


def process_job(settings: Settings, job_id: str, transcriber) -> None:
    db = settings.db_path
    if transcriber is None:
        fail_job(db, job_id, "worker_not_ready", "Модель ещё не прогрета")
        return
    job = get_job(db, job_id)
    if job is None:
        return
    # Атомарный захват queued→asr: проигрыш гонке (cancel/чужой воркер) → выход.
    if not claim_job(db, job_id):
        return
    rec = get_recording(db, job["recording_id"]) if job["recording_id"] else None
    if rec is None or not rec["tracks"]:
        fail_job(db, job_id, "no_tracks", "У записи нет дорожек")
        return

    params: dict[str, Any] = job["params"]
    output_dir = Path(job["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        cb = _stage_callback(settings, job_id)
        if job["mode"] == "route_a":
            tracks = {t["name"]: t["path"] for t in rec["tracks"]}
            result = transcriber.transcribe_route_a(
                tracks,
                glossary=params.get("glossary", True),
                min_segment_gap=params.get("min_segment_gap", 0.5),
                progress_callback=cb,
            )
        else:  # single
            # Грубая стадия диаризации перед ASR (точный per-сегментный % — v1.x).
            if params.get("diarization", "pyannote") != "none":
                update_job_progress(db, job_id, "diarization", 35)
            update_job_progress(db, job_id, "asr", 45)
            result = transcriber.transcribe(
                rec["tracks"][0]["path"],
                diarization=params.get("diarization", "pyannote"),
                num_speakers=params.get("num_speakers"),
                min_speakers=params.get("min_speakers"),
                max_speakers=params.get("max_speakers"),
                glossary=params.get("glossary", True),
                second_opinion=params.get("second_opinion", False),
                word_timestamps=params.get("word_timestamps", False),
                preclean=params.get("preclean", False),
                backend=params.get("backend", "torch"),
                onnx_int8=params.get("onnx_int8", False),
                voiceprint=params.get("voiceprint", False),
                voiceprint_gallery=params.get("voiceprint_gallery"),
                resume=True,
                manifest_path=job["manifest_path"],
                progress_callback=_single_stage_callback(settings, job_id),
            )

        update_job_progress(db, job_id, "formatting", 95)
        # Не утекать абсолютные серверные пути клиенту (result.json отдаётся как есть).
        if isinstance(result.metadata, dict):
            result.metadata.pop("source", None)
            # Зафиксировать фактический бэкенд диаризации (иначе UI-хедер пуст).
            if job["mode"] == "single":
                result.metadata.setdefault("diarization", params.get("diarization", "pyannote"))

        # L0-субстрат (opt-in): пишем сами — transcribe() без output_path его пропускает.
        # sha256 кладём в metadata ДО to_json() как verifiable-признак «L0 создан» для UI.
        l0_records = None
        if params.get("emit_l0"):
            try:
                from gigaam_transcriber.l0 import build_l0, l0_sha256

                l0_records = build_l0(result)
                if isinstance(result.metadata, dict):
                    result.metadata["l0_sha256"] = l0_sha256(l0_records)
            except Exception as l0e:  # noqa: BLE001 — L0 best-effort, не роняем джобу
                logger.warning("L0 build не удался для джобы %s: %s", job_id, l0e)
                l0_records = None

        result_json = output_dir / "result.json"
        result_json.write_text(result.to_json(), encoding="utf-8")

        # Локальный watch-конвейер: транскрипты сразу на диск в папку встречи
        # (transcripts/dialogscribe) — потребитель читает их без web-UI.
        if job["source"] == "local":
            for fmt, render in (
                ("md", result.to_md),
                ("txt", result.to_txt),
                ("srt", result.to_srt),
                ("vtt", result.to_vtt),
            ):
                try:
                    (output_dir / f"transcript.{fmt}").write_text(render(), encoding="utf-8")
                except Exception as fe:  # noqa: BLE001 — формат best-effort
                    logger.warning("формат %s не записан для джобы %s: %s", fmt, job_id, fe)

        if l0_records is not None:
            try:
                from gigaam_transcriber.l0 import write_l0

                write_l0(l0_records, output_dir / "transcript.v1.jsonl")
            except Exception as l0e:  # noqa: BLE001
                logger.warning("L0 write не удался для джобы %s: %s", job_id, l0e)

        # FINALIZE: downmix дорожек в один воспроизводимый файл (не критично для done).
        audio_out = None
        try:
            if media.ffmpeg_available():
                dst = output_dir / "audio.m4a"
                media.downmix_tracks([t["path"] for t in rec["tracks"]], dst)
                audio_out = str(dst)
        except Exception as dmx:  # noqa: BLE001 — downmix best-effort, но не молча
            logger.warning("downmix не удался для джобы %s: %s", job_id, dmx)
            audio_out = None

        finish_job_ok(
            db,
            job_id,
            result_json_path=str(result_json),
            audio_path=audio_out,
            duration_sec=getattr(result, "duration", None),
            processing_time_sec=getattr(result, "processing_time", None),
            device_fallback=bool(result.metadata.get("device_fallback")),
        )
    except Exception as exc:  # noqa: BLE001 — любая ошибка исполнения → state=error
        code, message = classify_error(exc)
        fail_job(db, job_id, code, message)
