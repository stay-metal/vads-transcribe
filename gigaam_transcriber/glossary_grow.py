"""Самообучение глоссария: повторяющиеся L2-правки → новые terms — перенос из custom.

L2-корректор (fusion) раз за разом чинит одни и те же латинские манглы GigaAM
(`reakkt`→`React`, `roater`→`OpenRouter`). Эта чистая функция собирает такие пары и
частые превращает в `terms` глоссария — бесплатный пост-проход учится у L2 и в следующий
раз правит мангл сам, без whisper-декода.

Фильтры (I1-safe): ключ — только латиница/смесь (кириллица неприкосновенна → не в terms);
ключ не настоящее русское/английское слово (тот же `glossary.lint`); правка повторилась
≥ `min_count` раз; для ключа берётся доминирующая каноника. Авто-мёрж в glossary.json
НЕ делаем — возвращаем кандидаты для ручной курации (precision-first).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ._paths import cache_dir, config_dir
from .glossary import lint, load_en_words, load_ru_words


# Лог корректировок L2 (для последующего harvest). Резолвится в момент вызова
# (cache_dir() лениво читает GIGAAM_TRANSCRIBER_CACHE) — не на import-time.
def _corrections_log() -> Path:
    return cache_dir() / "corrections.jsonl"


def _legacy_corrections_log() -> Path:
    """Старое расположение (<config>/../.cache) — накопленное там не должно пропасть."""
    return config_dir().parent / ".cache" / "corrections.jsonl"


_LATIN = re.compile(r"[A-Za-z]")


def _is_latin_mangle(token: str) -> bool:
    """Ключ-кандидат: непустой одиночный токен с латиницей (не кириллица-only)."""
    token = token.strip()
    return bool(token) and " " not in token and bool(_LATIN.search(token))


def harvest_corrections(
    pairs: list[tuple[str, str]],
    min_count: int = 3,
    *,
    ru_words: set[str] | None = None,
    en_words: set[str] | None = None,
) -> dict[str, str]:
    """Свернуть повторяющиеся (мангл→каноника) в `terms`: latin-only, count≥min_count, под lint."""
    ru_words = ru_words or set()
    by_key: dict[str, Counter[str]] = defaultdict(Counter)
    for mangle, canon in pairs:
        if not _is_latin_mangle(mangle) or not canon.strip():
            continue
        if mangle.strip() == canon.strip():
            continue
        by_key[mangle.strip().lower()][canon.strip()] += 1
    grown: dict[str, str] = {}
    for key, canon_counts in by_key.items():
        canon, count = canon_counts.most_common(1)[0]
        if count >= min_count:
            grown[key] = canon
    blocked = set(lint({"terms": grown}, ru_words, en_words))
    return {key: canon for key, canon in grown.items() if key not in blocked}


def log_corrections(pairs: list[tuple[str, str]], log_path: Path | None = None) -> None:
    """Дописать пары (мангл, каноника) в jsonl-лог (накопление между прогонами)."""
    if not pairs:
        return
    log_path = Path(log_path) if log_path else _corrections_log()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        for mangle, canon in pairs:
            f.write(json.dumps({"mangle": mangle, "canon": canon}, ensure_ascii=False) + "\n")


def harvest_log(log_path: Path | None = None, min_count: int = 3) -> dict[str, str]:
    """Прочитать лог корректировок и свернуть в кандидаты-terms (под двухъязычным lint).

    Читает и легаси-лог из прежнего расположения (переезд в ~/.cache/dialogscribe) —
    накопленные до переезда правки продолжают учитываться в счётчиках повторов.
    Возвращает {мангл: каноника} для ручной курации — НЕ пишет glossary.json (precision-first)."""
    paths = [Path(log_path)] if log_path else [_corrections_log(), _legacy_corrections_log()]
    pairs: list[tuple[str, str]] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                pairs.append((str(d["mangle"]), str(d["canon"])))
            except Exception:
                continue
    if not pairs:
        return {}
    return harvest_corrections(pairs, min_count, ru_words=load_ru_words(), en_words=load_en_words())
