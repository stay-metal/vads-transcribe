"""Per-layer version-штампы — перенос из custom (versions.py).

Diffable roll-up версий слоёв пайплайна в metadata: трассируемость и фундамент под
выборочный реплей (manifest/resume). Меняешь логику слоя — бампаешь его версию, тогда
downstream видит, что артефакт устарел именно по этому слою.
"""

from __future__ import annotations

# Версии слоёв (бампать при изменении логики соответствующего слоя).
LAYER_VERSIONS: dict[str, str] = {
    "asr": "gigaam-v3-rnnt-1",
    "vad": "gigaam-segment-1",
    "confidence": "rnnt-greedy-1",
    "diarize": "pyannote-3.1-1",
    "speaker_map": "overlap-1",
    # 2: одиночные people-алиасы-омонимы нарицательных исключены из текст-замены (I1)
    "glossary": "2",
    # 2: прайминг обогащён it_ai_terms.txt и именами участников; per-segment изоляция
    "second_opinion": "faster-whisper-small-2",
    "voiceprint": "ecapa-1",
    "l0": "v1",
    "render": "v1",
}


def pipeline_versions() -> dict[str, str]:
    """Снимок версий слоёв (копия — безопасно класть в metadata)."""
    return dict(LAYER_VERSIONS)
