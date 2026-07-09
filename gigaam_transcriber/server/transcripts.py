"""Оверлей правок (speaker/text) на result.json + рендер в форматы экспорта.

result.json на диске НЕ мутируется (I1): правки (`speaker_edits`/`text_edits`)
накладываются на чтении/скачивании/экспорте. Здесь — чистая логика без HTTP;
404/409 и прочие детали остаются в jobs.py. Рендер идёт через библиотечный
`TranscriptionResult.to_*`, поэтому «Скачать» и файлы пайплайна форматно едины.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_result_with_overlay(
    job: dict, edits: dict, text_edits: dict | None = None
) -> dict | None:
    """result.json с наложенными speaker/text-правками, или None если его ещё нет.

    Стабильный сырой ярлык спикера сохраняется в `original_speaker` (ключ правки —
    чтобы повторное переименование не терялось). `metadata.source` (серверные
    пути) вычищается, `speakers_count` пересчитывается по фактическим меткам.
    """
    path = job.get("result_json_path")
    if not path or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    text_edits = text_edits or {}
    for i, seg in enumerate(data.get("segments", [])):
        spk = seg.get("speaker")
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
        meta.pop("source", None)  # не отдаём клиенту серверные пути источника
        distinct = {s.get("speaker") for s in data.get("segments", []) if s.get("speaker")}
        meta["speakers_count"] = len(distinct)
    if text_edits:
        data["full_text"] = " ".join(s.get("text", "") for s in data.get("segments", []))
    return data


def _result_from_overlay(data: dict):
    """Реконструировать библиотечный TranscriptionResult из overlay-данных."""
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
    meta = data.get("metadata", {}) or {}
    return TranscriptionResult(
        text=data.get("full_text", " ".join(s.text for s in segs)),
        segments=segs,
        duration=float(meta.get("duration", 0.0) or 0.0),
        language=meta.get("language", "ru"),
        model_name=meta.get("model", meta.get("model_name", "")),
        processing_time=0.0,
    )


def render_body(data: dict, fmt: str) -> tuple[str, str]:
    """(тело, media_type) для формата экспорта/скачивания.

    md/txt/srt/vtt — через библиотечный `TranscriptionResult.to_*` (единый формат
    с файлами, что пишет пайплайн); json — сериализация overlay-данных как есть."""
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2), "application/json"
    result = _result_from_overlay(data)
    if fmt == "md":
        return result.to_md(), "text/markdown; charset=utf-8"
    if fmt == "txt":
        return result.to_txt(), "text/plain; charset=utf-8"
    if fmt == "srt":
        return result.to_srt(), "text/plain; charset=utf-8"
    return result.to_vtt(), "text/plain; charset=utf-8"
