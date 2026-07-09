"""L0 evidence-субстрат — перенос из custom (адаптирован под TranscriptionResult).

Плоский ``transcript.v1.jsonl``: по записи на сегмент
``{id, meeting, speaker, start, end, text, confidence, speaker_confidence, provenance, flags}``
+ sidecar ``.sha256`` (целостность) — машинный субстрат для downstream (RAG и пр.).

В custom L0 строится из ``manifest.json`` (чанки нескольких дорожек/коллов Route A/B);
в DialogScribe источник — ``TranscriptionResult.segments`` (один аудиопоток), поэтому id
проще: ``<meeting>:<start.3f>:<speaker>:<ordinal>`` (без call_index/time_offset).

I1 (verbatim): текст переносится дословно (``str(...)`` без нормализации). Чистое ядро
(`build_l0`/`l0_sha256`) — без I/O; единственный сайд-эффект — `write_l0`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .data_models import DEFAULT_PROVENANCE, TranscriptionResult

# Ключи L0-записи в каноническом порядке (стабильный diff/хэш).
L0_FIELDS = (
    "id",
    "meeting",
    "speaker",
    "start",
    "end",
    "text",
    "confidence",
    "speaker_confidence",
    "provenance",
    "flags",
)


def _slug(value: object) -> str:
    """Свести значение к компактному id-сегменту (`:`/пробелы → `_`; пустое → `_`)."""
    text = str(value or "").strip()
    if not text:
        return "_"
    return "_".join(text.replace(":", "_").split())


def _meeting_name(result: TranscriptionResult, meeting: str | None = None) -> str:
    if meeting:
        return str(meeting)
    src = (result.metadata or {}).get("source")
    if src:
        return Path(str(src)).stem
    return "meeting"


def build_l0(result: TranscriptionResult, meeting: str | None = None) -> list[dict[str, Any]]:
    """Построить L0-записи из сегментов — по одной на сегмент с непустым текстом. Чистая функция.

    ``confidence`` — акустический chunk-level (приоритет), иначе ``speaker_confidence``, иначе None.
    ``ordinal`` разводит сегменты с одинаковым ``start`` (устойчивость id). I1: text дословно."""
    name = _meeting_name(result, meeting)
    records: list[dict[str, Any]] = []
    ordinals: dict[float, int] = {}
    for seg in result.segments:
        text = str(seg.text or "")
        if not text.strip():
            continue
        start = round(float(seg.start), 3)
        end = round(float(seg.end), 3)
        ordinal = ordinals.get(start, 0)
        ordinals[start] = ordinal + 1
        conf = seg.confidence if seg.confidence is not None else seg.speaker_confidence
        records.append(
            {
                "id": f"{name}:{start:.3f}:{_slug(seg.speaker)}:{ordinal}",
                "meeting": name,
                "speaker": seg.speaker,
                "start": start,
                "end": end,
                "text": text,
                "confidence": float(conf) if conf is not None else None,
                "speaker_confidence": seg.speaker_confidence,
                "provenance": seg.provenance or DEFAULT_PROVENANCE,
                "flags": list(seg.flags or []),
            }
        )
    return records


def _canonical_json(records: list[dict[str, Any]]) -> str:
    """Канонизированный JSON для хэша: sort_keys + компактные разделители, кириллица как есть."""
    return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def l0_sha256(records: list[dict[str, Any]]) -> str:
    """sha256 (hex) над канонизированным JSON L0 — детерминированный отпечаток целостности."""
    return hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest()


def write_l0(records: list[dict[str, Any]], out_path: Path | str) -> Path:
    """Записать L0 в ``<out>`` (jsonl, по объекту на строку) + sidecar ``<out>.sha256``.

    Атомарно (tmp + os.replace) — краш посреди записи не оставит усечённый L0."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
    tmp = out_path.parent / (out_path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    sidecar = out_path.with_name(out_path.name + ".sha256")
    sidecar_tmp = out_path.parent / (sidecar.name + ".tmp")
    sidecar_tmp.write_text(l0_sha256(records) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)
    os.replace(sidecar_tmp, sidecar)
    return out_path
