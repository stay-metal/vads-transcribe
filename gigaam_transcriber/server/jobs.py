"""Джобы (спека §6/§7): сабмит, poll-прогресс, cancel, result/audio/download, speakers.

Сабмит делает синхронную пре-валидацию (ffmpeg, HF_TOKEN для single-diarized) →
реальные 4xx; параметры замораживаются в params_json и ставятся в gpu-очередь.
Ошибки исполнения приходят асинхронно как state='error'. speaker-edits применяются
на чтении/скачивании — result.json на диске не мутируется (revertible).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import media
from .auth import require_session
from .repository import (
    cancel_job_if_queued,
    create_job,
    get_job,
    get_recording,
    get_speaker_edits,
    list_jobs,
    set_job_dirs,
    set_job_huey_task,
    set_recording_latest_job,
    set_speaker_edit,
)

router = APIRouter()


class CreateJobIn(BaseModel):
    recording_id: str
    diarization: str = "pyannote"  # для single; route_a игнорирует
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    glossary: bool = True
    min_segment_gap: float = 0.5
    # opt-in качество/бэкенд — ТОЛЬКО single-путь (route_a их форсит/игнорирует, спека §4.1)
    second_opinion: bool = False
    word_timestamps: bool = False
    preclean: bool = False
    backend: str = "torch"
    onnx_int8: bool = False
    voiceprint: bool = False
    voiceprint_gallery: str | None = None
    emit_l0: bool = False


class SpeakersIn(BaseModel):
    edits: dict  # {original_label: new_label}


def _public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "mode": job["mode"],
        "state": job["state"],
        "stage_pct": job["stage_pct"],
        "error_code": job["error_code"],
        "error_message": job["error_message"],
        "device_fallback": job["device_fallback"],
        "duration_sec": job["duration_sec"],
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
    }


# --------------------------------------------------------------------------- #
# submit
# --------------------------------------------------------------------------- #
@router.post("/api/jobs")
def submit_job(
    payload: CreateJobIn, request: Request, user: str = Depends(require_session)
) -> dict:
    settings = request.app.state.settings
    db = settings.db_path
    rec = get_recording(db, payload.recording_id)
    if rec is None:
        raise HTTPException(404, "Запись не найдена")
    if not media.ffmpeg_available():
        raise HTTPException(503, "ffmpeg недоступен на сервере")

    mode = rec["kind"]  # route_a | single
    # single-diarized требует HF_TOKEN upfront — иначе библиотека молча деградирует.
    if mode == "single" and payload.diarization != "none" and not os.getenv("HF_TOKEN"):
        raise HTTPException(400, "Диаризация требует HF_TOKEN")

    params = {
        "glossary": payload.glossary,
        "min_segment_gap": payload.min_segment_gap,
    }
    if mode == "single":
        params.update(
            diarization=payload.diarization,
            num_speakers=payload.num_speakers,
            min_speakers=payload.min_speakers,
            max_speakers=payload.max_speakers,
            second_opinion=payload.second_opinion,
            word_timestamps=payload.word_timestamps,
            preclean=payload.preclean,
            backend=payload.backend,
            onnx_int8=payload.onnx_int8,
            voiceprint=payload.voiceprint,
            voiceprint_gallery=payload.voiceprint_gallery,
            emit_l0=payload.emit_l0,
        )

    job_id = create_job(db, mode=mode, source="upload", recording_id=rec["id"], params=params)
    data_dir = Path(settings.data_dir)
    output_dir = data_dir / "outputs" / job_id
    work_dir = data_dir / "work" / job_id
    set_job_dirs(
        db,
        job_id,
        work_dir=str(work_dir),
        output_dir=str(output_dir),
        manifest_path=str(output_dir / "manifest.json"),
    )
    set_recording_latest_job(db, rec["id"], job_id)

    enqueue = getattr(request.app.state, "enqueue", None)
    if enqueue is not None:
        task_id = enqueue(job_id)
        if task_id:
            set_job_huey_task(db, job_id, str(task_id))

    return {"job_id": job_id, "state": "queued", "mode": mode}


# --------------------------------------------------------------------------- #
# read / progress
# --------------------------------------------------------------------------- #
@router.get("/api/jobs")
def list_all(request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    return {"jobs": [_public_job(j) for j in list_jobs(settings.db_path)]}


@router.get("/api/jobs/{job_id}")
def get_one(job_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    return _public_job(job)


@router.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    # Cancel честно только для queued (running доходит до конца — спека §7).
    if not cancel_job_if_queued(settings.db_path, job_id):
        raise HTTPException(409, "Отмена возможна только для джобы в очереди")
    revoke = getattr(request.app.state, "revoke", None)
    if revoke is not None and job["huey_task_id"]:
        revoke(job["huey_task_id"])
    return {"job_id": job_id, "state": "canceled"}


# --------------------------------------------------------------------------- #
# result / speakers / download / audio
# --------------------------------------------------------------------------- #
def _load_result_with_overlay(job: dict, edits: dict) -> dict:
    """Загрузить result.json и наложить speaker-edits (без мутации файла)."""
    path = job.get("result_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(409, "Результат ещё не готов")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for seg in data.get("segments", []):
        spk = seg.get("speaker")
        # стабильный сырой ярлык — ключ правки (чтобы повторное переименование не терялось)
        seg["original_speaker"] = spk
        if spk in edits:
            seg["speaker"] = edits[spk]
            seg["provenance"] = "human"
    meta = data.get("metadata")
    if isinstance(meta, dict):
        # не отдаём клиенту серверные пути источника
        meta.pop("source", None)
        # пересчёт после наложения: speakers_count = число фактических меток
        distinct = {s.get("speaker") for s in data.get("segments", []) if s.get("speaker")}
        meta["speakers_count"] = len(distinct)
    return data


@router.get("/api/jobs/{job_id}/result")
def get_result(job_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    edits = get_speaker_edits(settings.db_path, job_id)
    return _load_result_with_overlay(job, edits)


@router.put("/api/jobs/{job_id}/speakers")
def put_speakers(
    job_id: str, payload: SpeakersIn, request: Request, user: str = Depends(require_session)
) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    for original, new in payload.edits.items():
        if not str(new).strip():
            raise HTTPException(400, "Пустое имя спикера")
        set_speaker_edit(settings.db_path, job_id, original, str(new).strip())
    return {"job_id": job_id, "edits": get_speaker_edits(settings.db_path, job_id)}


@router.get("/api/jobs/{job_id}/audio")
def get_audio(job_id: str, request: Request, user: str = Depends(require_session)):
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    audio = job.get("audio_path")
    if not audio or not Path(audio).exists():
        raise HTTPException(404, "Аудио недоступно")
    # FileResponse поддерживает Range-запросы (перемотка плеера).
    return FileResponse(audio, media_type="audio/mp4", filename="audio.m4a")


@router.get("/api/jobs/{job_id}/download")
def download(
    job_id: str,
    request: Request,
    format: str = "txt",
    user: str = Depends(require_session),
):
    if format not in ("txt", "json", "srt", "vtt", "l0", "sha256"):
        raise HTTPException(400, "Формат: txt|json|srt|vtt|l0|sha256")
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")

    # L0-субстрат — сырой evidence-файл (speaker-overlay НЕ применяется).
    if format in ("l0", "sha256"):
        out_dir = job.get("output_dir")
        if not out_dir:
            raise HTTPException(404, "L0-субстрат недоступен")
        fname = "transcript.v1.jsonl" if format == "l0" else "transcript.v1.jsonl.sha256"
        fpath = Path(out_dir) / fname
        if not fpath.exists():
            raise HTTPException(404, "L0-субстрат не создавался для этой джобы")
        media_type = "application/x-ndjson" if format == "l0" else "text/plain; charset=utf-8"
        return FileResponse(fpath, media_type=media_type, filename=fname)

    edits = get_speaker_edits(settings.db_path, job_id)
    data = _load_result_with_overlay(job, edits)

    if format == "json":
        body = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        body = _render(data, format)
        media_type = "text/plain; charset=utf-8"
    headers = {"Content-Disposition": f'attachment; filename="transcript.{format}"'}
    return Response(content=body, media_type=media_type, headers=headers)


def _render(data: dict, fmt: str) -> str:
    """Рендер txt/srt/vtt из overlay-данных через библиотечный TranscriptionResult."""
    from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment

    segs = [
        TranscriptionSegment(
            text=s.get("text", ""),
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", 0.0)),
            speaker=s.get("speaker"),
        )
        for s in data.get("segments", [])
    ]
    meta = data.get("metadata", {})
    result = TranscriptionResult(
        text=data.get("full_text", " ".join(s.text for s in segs)),
        segments=segs,
        duration=float(meta.get("duration", 0.0) or 0.0),
        language=meta.get("language", "ru"),
        model_name=meta.get("model", meta.get("model_name", "")),
        processing_time=0.0,
    )
    if fmt == "txt":
        return result.to_txt()
    if fmt == "srt":
        return result.to_srt()
    return result.to_vtt()
