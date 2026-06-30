"""Слияние GigaAM ↔ «второе мнение» (локальный Whisper) под I1 — чистая функция, без I/O.

Перенос из custom (zoom_transcriber/fusion.py) без изменений логики. GigaAM выдаёт
нерушимую русскую базу (I1), а второй ASR-читатель (multilingual Whisper) чинит ровно то,
что greedy RNN-T путает — латинские термины, иностранные бренды/имена, числа.

Правило: **кириллица GigaAM неприкосновенна (I1)**. Каждый кириллический токен входа
дословно присутствует в выходе. Заменяем только токены, которые GigaAM выдал латиницей или
числом, предпочитая форму из «второго мнения» (или канонический алиас глоссария).

Выравнивание — нечётким текстовым матчем (difflib). Блоки 'equal'/'replace' одинаковой длины
(≤3) → 1-в-1; 'replace' разной длины, где ВСЕ giga-токены заменяемы → слияние N→M (`Inside
Traker`→`InsideTracker`). При неуверенности оставляем GigaAM.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

# Токен = слово (буквы/цифры/апостроф/дефис) ИЛИ один не-словесный символ (пунктуация/пробелы сохраняются).
_TOKEN = re.compile(r"\w+(?:[-'’]\w+)*|\s+|[^\w\s]", re.UNICODE)
_CYRILLIC = re.compile(r"[Ѐ-ӿԀ-ԯⷠ-ⷿꙀ-ꚟᲀ-ᲈ𞀰-𞁉]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")

# Длиннее этого 1:1-замена/слияние считается «разъехавшимся» куском, а не правкой слов.
_MAX_REPLACE_RUN = 3


def _is_word(tok: str) -> bool:
    return bool(tok) and (tok[0].isalnum() or tok[0] in "-'’")


def _has_cyrillic(tok: str) -> bool:
    return bool(_CYRILLIC.search(tok))


def _is_replaceable(tok: str) -> bool:
    """Токен GigaAM, который МОЖНО заменить: латиница или число (но НЕ кириллица).

    Кириллический токен неприкосновенен (I1) и сюда не попадает — даже смешанный
    кир+лат считается кириллическим и остаётся как есть."""
    if not _is_word(tok) or _has_cyrillic(tok):
        return False
    return bool(_LATIN.search(tok)) or bool(_DIGIT.search(tok))


def _words(tokens: List[str]) -> List[str]:
    return [t for t in tokens if _is_word(t)]


def fuse(giga_text: str, second_text: str, alias_map: Dict[str, str]) -> str:
    """Слить текст GigaAM со «вторым мнением», не трогая кириллицу GigaAM (I1)."""
    fused_text, _corrections = fuse_with_corrections(giga_text, second_text, alias_map)
    return fused_text


def _fuse_token(giga_word: str, second_word: Optional[str], alias_map: Dict[str, str]) -> str:
    """Выбрать форму одного слова GigaAM (1:1). Кириллица и неуверенность → GigaAM."""
    if not _is_replaceable(giga_word):
        return giga_word  # кириллица/прочее неприкосновенно (I1)
    canon = alias_map.get(giga_word.lower())
    if canon is not None:
        return canon  # известный мангл из глоссария — каноника побеждает
    if second_word and not _has_cyrillic(second_word):
        return second_word  # форма второго мнения (кириллицу в обход I1 не протаскиваем)
    return giga_word


def _build_plan(
    giga_words: List[str], second_words: List[str], alias_map: Dict[str, str]
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """План замены по словам + пары правок (мангл→каноника) для 1:1-замен. Кириллица не в парах (I1)."""
    plan: List[Tuple[str, str]] = [("keep", w) for w in giga_words]
    corrections: List[Tuple[str, str]] = []

    def set_one(gi: int, second_word: Optional[str]) -> None:
        w = giga_words[gi]
        chosen = _fuse_token(w, second_word, alias_map)
        if chosen != w:
            plan[gi] = ("replace", chosen)
            if _is_replaceable(w):
                corrections.append((w.lower(), chosen))

    if not second_words:
        for gi in range(len(giga_words)):
            set_one(gi, None)  # только алиас-проход
        return plan, corrections

    sm = SequenceMatcher(
        a=[w.lower() for w in giga_words],
        b=[w.lower() for w in second_words],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                set_one(i1 + off, second_words[j1 + off])
        elif tag == "replace":
            gn, gm = i2 - i1, j2 - j1
            if gn == gm and gn <= _MAX_REPLACE_RUN:
                for off in range(gn):
                    set_one(i1 + off, second_words[j1 + off])
            elif gn <= _MAX_REPLACE_RUN and all(
                _is_replaceable(giga_words[k]) for k in range(i1, i2)
            ):
                # Слияние N→M: вся giga-пачка заменяема (латиница/число) → объединённый второй текст.
                merged = " ".join(second_words[j1:j2]).strip()
                if merged and not _has_cyrillic(merged):
                    plan[i1] = ("replace", merged)
                    for off in range(1, gn):
                        plan[i1 + off] = ("drop", "")
            # else: блок с кириллицей или слишком длинный → keep (I1)
        # 'delete' → keep; 'insert' → игнор
    return plan, corrections


def fuse_with_corrections(
    giga_text: str, second_text: str, alias_map: Dict[str, str]
) -> Tuple[str, List[Tuple[str, str]]]:
    """Как :func:`fuse`, но дополнительно отдаёт пары применённых правок (для самораздува глоссария)."""
    giga_tokens = _TOKEN.findall(giga_text)
    giga_words = _words(giga_tokens)
    second_words = _words(_TOKEN.findall(second_text))
    plan, corrections = _build_plan(giga_words, second_words, alias_map)

    out: List[str] = []
    pending: List[str] = []
    word_idx = -1
    for tok in giga_tokens:
        if not _is_word(tok):
            pending.append(tok)
            continue
        word_idx += 1
        action, text = plan[word_idx]
        if action == "drop":
            out.extend(p for p in pending if not p.isspace())
            pending = []
            continue
        out.extend(pending)
        pending = []
        out.append(text)
    out.extend(pending)
    return "".join(out), corrections
