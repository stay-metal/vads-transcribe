"""Детекция риска качества текста ASR — ПОМЕТКА (не правка). Перенос из custom.

Помечает сегменты с известными GigaAM-галлюцинациями (титры/«спасибо за просмотр»,
всплывают в тишине) и зацикливанием (много повторов при большой длине).

Отличие от custom (`clean_transcript_text` схлопывает подряд идущие повторы слова):
здесь текст НЕ меняется — строго surface-not-drop, кириллица verbatim (I1). Флаг —
наблюдение для downstream/триажа, а не правка вывода GigaAM.
"""
from __future__ import annotations

from typing import List

# Известные фразы-галлюцинации GigaAM (титры/реклама, всплывают на тишине/музыке).
HALLUCINATION_PHRASES = (
    "субтитры сделал", "субтитры создавал", "продолжение следует",
    "редактор субтитров", "корректор", "спасибо за просмотр",
    "подписывайтесь на канал", "ставьте лайк",
)


def detect_quality_flags(text: str) -> List[str]:
    """Флаги риска качества сегмента (НЕ меняет текст). Пустой/чистый текст → []."""
    low = text.strip().lower()
    if not low:
        return []
    flags: List[str] = []
    if any(low.startswith(p) or low == p for p in HALLUCINATION_PHRASES):
        flags.append("hallucination_suspect")
    words = low.split()
    # Зацикливание: >=8 слов и уникальных <= 20% (напр. «да да да да …»).
    if len(words) >= 8 and len(set(words)) <= max(1, len(words) // 5):
        flags.append("loop_suspect")
    return flags
