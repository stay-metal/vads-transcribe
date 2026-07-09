"""manifest.json + resume — перенос идеи из custom (адаптировано под DialogScribe).

Кэш результата по хэшу входного файла: повторный прогон уже обработанного файла
пропускает ASR (восстановление из manifest). Это coarse-resume (skip-if-done) — не
пер-чанковый чекпойнт custom (у DialogScribe нет стабильной chunk-единицы как AsrChunk),
но устраняет повторный ASR при ре-прогоне/докатывании. Сегменты сериализуются через
to_dict/from_dict; целостность — по file_hash (несовпадение → ре-транскрипция).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .data_models import TranscriptionResult, TranscriptionSegment
from .utils import get_file_hash
from .versions import pipeline_versions

MANIFEST_VERSION = 1


def manifest_path_for(output_path) -> Path:
    """``<output>.manifest.json`` рядом с выводом."""
    p = Path(output_path)
    return p.with_suffix(p.suffix + ".manifest.json")


def write_manifest(
    result: TranscriptionResult, audio_path, manifest_path, request: dict | None = None
) -> Path:
    """Записать manifest (file_hash + сегменты + метаданные) атомарно (tmp+os.replace).

    ``request`` — сигнатура запрошенных quality-слоёв (diarization/glossary/L2/...);
    resume сверяет её, чтобы кэш без L2 не выдавался за результат с L2."""
    obj = {
        "manifest_version": MANIFEST_VERSION,
        "file_hash": get_file_hash(Path(audio_path)),
        "complete": True,
        "request": request,
        "layer_versions": pipeline_versions(),
        "language": result.language,
        "model_name": result.model_name,
        "duration": result.duration,
        "processing_time": result.processing_time,
        "metadata": dict(result.metadata),
        "segments": [s.to_dict() for s in result.segments],
        "text": result.text,
    }
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.parent / (manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, manifest_path)
    return manifest_path


def load_manifest(manifest_path) -> dict | None:
    p = Path(manifest_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def resume_result(
    manifest_path, audio_path, request: dict | None = None
) -> TranscriptionResult | None:
    """Восстановить result из manifest — только если кэш всё ещё валиден.

    Условия: complete, file_hash совпадает, layer_versions не менялись (бамп
    LAYER_VERSIONS инвалидирует кэш) и сигнатура запроса ``request`` совпадает с
    записанной (иначе, например, запрос с second_opinion=True получил бы кэш без L2).
    Старые manifest'ы без request инвалидируются, если request передан."""
    m = load_manifest(manifest_path)
    if not m or not m.get("complete"):
        return None
    if m.get("layer_versions") != pipeline_versions():
        return None  # логика слоя изменилась → артефакт устарел
    if request is not None and m.get("request") != request:
        return None  # другой состав quality-слоёв → нельзя резюмить
    try:
        if m.get("file_hash") != get_file_hash(Path(audio_path)):
            return None  # файл изменился → нельзя резюмить
        segs = [TranscriptionSegment.from_dict(d) for d in m.get("segments", [])]
    except Exception:
        return None
    return TranscriptionResult(
        text=m.get("text", " ".join(s.text for s in segs)),
        segments=segs,
        duration=m.get("duration", 0.0),
        language=m.get("language", "ru"),
        model_name=m.get("model_name", ""),
        processing_time=m.get("processing_time", 0.0),
        metadata={**m.get("metadata", {}), "resumed": True},
    )
