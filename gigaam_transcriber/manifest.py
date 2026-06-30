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
from typing import Optional

from .data_models import TranscriptionResult, TranscriptionSegment
from .utils import get_file_hash
from .versions import pipeline_versions

MANIFEST_VERSION = 1


def manifest_path_for(output_path) -> Path:
    """``<output>.manifest.json`` рядом с выводом."""
    p = Path(output_path)
    return p.with_suffix(p.suffix + ".manifest.json")


def write_manifest(result: TranscriptionResult, audio_path, manifest_path) -> Path:
    """Записать manifest (file_hash + сегменты + метаданные) атомарно (tmp+os.replace)."""
    obj = {
        "manifest_version": MANIFEST_VERSION,
        "file_hash": get_file_hash(Path(audio_path)),
        "complete": True,
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


def load_manifest(manifest_path) -> Optional[dict]:
    p = Path(manifest_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def resume_result(manifest_path, audio_path) -> Optional[TranscriptionResult]:
    """Восстановить result из manifest, если он complete И file_hash совпадает. Иначе None."""
    m = load_manifest(manifest_path)
    if not m or not m.get("complete"):
        return None
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
