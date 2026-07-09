"""Джобы (спека §6/§7): сабмит, poll-прогресс, cancel, result/audio/download, speakers.

Сабмит делает синхронную пре-валидацию (ffmpeg, HF_TOKEN для single-diarized) →
реальные 4xx; параметры замораживаются в params_json и ставятся в gpu-очередь.
Ошибки исполнения приходят асинхронно как state='error'. speaker-edits применяются
на чтении/скачивании — result.json на диске не мутируется (revertible).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import media
from .auth import require_session
from .repository import (
    avg_recent_rtf,
    cancel_job_if_queued,
    create_job,
    done_duration_total,
    get_job,
    get_meta,
    get_recording,
    get_speaker_edits,
    get_text_edits,
    jobs_state_counts,
    list_jobs_page,
    queued_positions,
    set_job_dirs,
    set_job_duration,
    set_job_huey_task,
    set_meta,
    set_recording_latest_job,
    set_speaker_edit,
    set_text_edit,
)

_FORMATS = ("md", "txt", "json", "srt", "vtt")

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


class SegmentEditIn(BaseModel):
    index: int
    text: str


class SettingsIn(BaseModel):
    transcript_format: str


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
        "started_at": job.get("started_at"),
        "finished_at": job["finished_at"],
        "source": job.get("source"),
        "title": job.get("title"),
        "track_count": job.get("track_count"),
    }


def _canon_date_bound(s: str) -> str:
    """ISO-8601 (в т.ч. `…Z`) → каноничный UTC-isoformat для сравнения с
    `created_at`. Пустая строка сюда не попадает (её отсекает роут)."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "date_from/date_to: ожидается ISO-8601") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# Активные (нетерминальные) состояния — для scope-фильтра списка.
_ACTIVE_STATES = ("queued", "preclean", "vad", "diarization", "asr", "quality", "formatting")
_SCOPES: dict[str, tuple[str, ...]] = {
    "active": _ACTIVE_STATES,
    "done": ("done",),
    "error": ("error",),
    "canceled": ("canceled",),
    "terminal": ("done", "error", "canceled"),
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

    params: dict[str, Any] = {
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
    # Длительность аудио — заранее (ETA в UI); финиш перезапишет фактической.
    if rec["tracks"]:
        dur = media.probe_duration(Path(rec["tracks"][0]["path"]))
        if dur:
            set_job_duration(db, job_id, dur)
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
def list_all(
    request: Request,
    q: str = "",
    scope: str = "all",
    date_from: str = "",
    date_to: str = "",
    limit: int = 50,
    offset: int = 0,
    user: str = Depends(require_session),
) -> dict:
    """Страница джоб для UI: поиск/фильтр статуса/дат/пагинация + сводка.

    Сводка (counts/avg_rtf/позиции очереди) нужна дашборду для чипов-счётчиков
    и оценки «сколько осталось» (ETA = duration × avg_rtf − прошло). Диапазон
    дат — по `created_at` (границы каноникализируются в UTC), полуинтервал
    `[date_from, date_to)`."""
    if scope not in ("all", *_SCOPES):
        raise HTTPException(400, "scope: all|active|done|error|canceled|terminal")
    settings = request.app.state.settings
    jobs, total = list_jobs_page(
        settings.db_path,
        q=q,
        states=_SCOPES.get(scope),
        date_from=_canon_date_bound(date_from) if date_from else None,
        date_to=_canon_date_bound(date_to) if date_to else None,
        limit=max(1, min(int(limit), 200)),
        offset=max(0, int(offset)),
    )
    positions = queued_positions(settings.db_path)
    payload = []
    for j in jobs:
        pub = _public_job(j)
        pub["queue_position"] = positions.get(j["id"])
        payload.append(pub)
    counts = jobs_state_counts(settings.db_path)
    return {
        "jobs": payload,
        "total": total,
        "counts": {
            "active": sum(counts.get(s, 0) for s in _ACTIVE_STATES),
            "queued": counts.get("queued", 0),
            "done": counts.get("done", 0),
            "error": counts.get("error", 0),
            "canceled": counts.get("canceled", 0),
        },
        "avg_rtf": avg_recent_rtf(settings.db_path),
        "done_duration_sec": done_duration_total(settings.db_path),
    }


@router.get("/api/jobs/{job_id}")
def get_one(job_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    rec = get_recording(settings.db_path, job["recording_id"]) if job["recording_id"] else None
    if rec is not None:
        job["title"] = rec.get("title")
        job["track_count"] = rec.get("track_count")
    pub = _public_job(job)
    pub["queue_position"] = queued_positions(settings.db_path).get(job_id)
    return pub


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, user: str = Depends(require_session)):
    """SSE-проекция прогресса: поток событий вместо клиентского поллинга. Сервер
    поллит строку джобы раз в секунду и шлёт при изменении (state/stage_pct); на
    терминальном состоянии — финальное событие и закрытие. За nginx нужен
    `proxy_buffering off` (см. deploy/nginx.conf), иначе события копятся в буфере."""
    import asyncio

    from sse_starlette.sse import EventSourceResponse

    settings = request.app.state.settings

    async def event_gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            job = get_job(settings.db_path, job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"detail": "Джоба не найдена"})}
                break
            pub = _public_job(job)
            snap = (pub["state"], pub["stage_pct"])
            if snap != last:
                last = snap
                yield {"event": "job", "data": json.dumps(pub, ensure_ascii=False)}
            if pub["state"] in ("done", "error", "canceled"):
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())


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
def _load_result_with_overlay(job: dict, edits: dict, text_edits: dict | None = None) -> dict:
    """Загрузить result.json и наложить speaker/text-edits (без мутации файла).

    Правки хранятся отдельными оверлеями (`speaker_edits`/`text_edits`) и
    применяются на чтении/скачивании/экспорте — сам result.json неизменен (I1)."""
    path = job.get("result_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(409, "Результат ещё не готов")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    text_edits = text_edits or {}
    for i, seg in enumerate(data.get("segments", [])):
        spk = seg.get("speaker")
        # стабильный сырой ярлык — ключ правки (чтобы повторное переименование не терялось)
        seg["original_speaker"] = spk
        if spk in edits:
            seg["speaker"] = edits[spk]
            seg["provenance"] = "human"
        if i in text_edits:
            seg["original_text"] = seg.get("text")
            seg["text"] = text_edits[i]
            seg["provenance"] = "human"
    meta = data.get("metadata")
    if isinstance(meta, dict):
        # не отдаём клиенту серверные пути источника
        meta.pop("source", None)
        # пересчёт после наложения: speakers_count = число фактических меток
        distinct = {s.get("speaker") for s in data.get("segments", []) if s.get("speaker")}
        meta["speakers_count"] = len(distinct)
    if text_edits:
        data["full_text"] = " ".join(s.get("text", "") for s in data.get("segments", []))
    return data


def _overlay_for(settings, job: dict) -> dict:
    return _load_result_with_overlay(
        job,
        get_speaker_edits(settings.db_path, job["id"]),
        get_text_edits(settings.db_path, job["id"]),
    )


@router.get("/api/jobs/{job_id}/result")
def get_result(job_id: str, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    return _overlay_for(settings, job)


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


@router.put("/api/jobs/{job_id}/segments")
def put_segment(
    job_id: str, payload: SegmentEditIn, request: Request, user: str = Depends(require_session)
) -> dict:
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    if payload.index < 0:
        raise HTTPException(400, "Некорректный индекс реплики")
    if not payload.text.strip():
        raise HTTPException(400, "Пустой текст реплики")
    set_text_edit(settings.db_path, job_id, payload.index, payload.text.strip())
    return {"job_id": job_id, "index": payload.index}


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
    if format not in (*_FORMATS, "l0", "sha256"):
        raise HTTPException(400, "Формат: md|txt|json|srt|vtt|l0|sha256")
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

    data = _overlay_for(settings, job)
    body, media_type = _render_body(data, format)
    headers = {"Content-Disposition": f'attachment; filename="transcript.{format}"'}
    return Response(content=body, media_type=media_type, headers=headers)


@router.post("/api/jobs/{job_id}/write")
def write_transcript(
    job_id: str, request: Request, format: str = "", user: str = Depends(require_session)
) -> dict:
    """Записать транскрипт с правками на диск (в output_dir джобы) — тот же
    файл, что создал пайплайн. Формат — из запроса или дефолтный из настроек."""
    settings = request.app.state.settings
    job = get_job(settings.db_path, job_id)
    if job is None:
        raise HTTPException(404, "Джоба не найдена")
    out_dir = job.get("output_dir")
    if not out_dir:
        raise HTTPException(409, "Папка транскрипта недоступна")
    fmt = format or get_meta(settings.db_path, "transcript_format", "md") or "md"
    if fmt not in _FORMATS:
        raise HTTPException(400, "Формат: md|txt|json|srt|vtt")
    data = _overlay_for(settings, job)
    body, _ = _render_body(data, fmt)
    dest = Path(out_dir) / f"transcript.{fmt}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    return {"written": dest.name, "format": fmt}


@router.get("/api/settings")
def get_settings(request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    return {"transcript_format": get_meta(settings.db_path, "transcript_format", "md") or "md"}


@router.put("/api/settings")
def put_settings(
    payload: SettingsIn, request: Request, user: str = Depends(require_session)
) -> dict:
    if payload.transcript_format not in _FORMATS:
        raise HTTPException(400, "Формат: md|txt|json|srt|vtt")
    settings = request.app.state.settings
    set_meta(settings.db_path, "transcript_format", payload.transcript_format)
    return {"transcript_format": payload.transcript_format}


def _format_ts(sec: float) -> str:
    s = int(sec)
    h, m, ss = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


def _render_md(data: dict) -> str:
    """Markdown-протокол созвона: заголовок + реплики «**Спикер** · `тайм`»."""
    meta = data.get("metadata", {}) or {}
    segs = data.get("segments", [])
    lines: list[str] = ["# Транскрипт", ""]
    bits: list[str] = []
    if meta.get("duration"):
        bits.append(_format_ts(float(meta["duration"])))
    if meta.get("model"):
        bits.append(str(meta["model"]))
    bits.append(f"{len(segs)} реплик")
    lines += ["_" + " · ".join(bits) + "_", ""]
    for s in segs:
        ts = _format_ts(float(s.get("start", 0.0)))
        spk = s.get("speaker")
        lines.append(f"**{spk}** · `{ts}`" if spk else f"`{ts}`")
        lines.append("")
        lines.append((s.get("text", "") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_body(data: dict, fmt: str) -> tuple[str, str]:
    """(тело, media_type) для формата экспорта/скачивания."""
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2), "application/json"
    if fmt == "md":
        return _render_md(data), "text/markdown; charset=utf-8"
    return _render(data, fmt), "text/plain; charset=utf-8"


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
