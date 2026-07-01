"""Глоссарий: канонизация имён/терминов (alias→canon) — перенос из custom.

Детерминированный пост-проход по тексту сегментов: заменяет алиасы (people + terms)
на канонические формы по границам слов, без регистра, longest-first, идемпотентно.
I1-страж (`lint`): отказывается применять term-алиас, совпадающий с реальным русским
ИЛИ английским словом (бренд 'Ponimaiu' не перепишет глагол 'понимаю', алиас 'date'
не перепишет настоящее английское слово).

В отличие от custom (правит *.md-файлы пост-фактум), здесь основной путь —
:func:`apply_to_segments` в самом пайплайне (после сшивки, до сохранения). Кириллица
GigaAM неприкосновенна (I1): меняются лишь курируемые алиасы, не обычные слова;
для term-алиасов действует морфо-фильтр «en-термин по падежу».

Ядро (`lint`/`alias_map`/`suffixable_aliases`/`apply_glossary` и константы) перенесено
из custom/zoom_transcriber/glossary.py без изменений логики.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ._paths import config_dir
from .data_models import TranscriptionSegment, merge_provenance


# Пути резолвятся в момент ВЫЗОВА (config_dir() читает GIGAAM_TRANSCRIBER_CONFIG лениво) —
# иначе биндинг на import-time замораживал бы их, и env-override после import игнорировался.
def load_glossary(path: Path | None = None) -> dict:
    path = path or (config_dir() / "glossary.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_word_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def load_ru_words(path: Path | None = None) -> set[str]:
    return _load_word_set(path or (config_dir() / "russian_words.txt"))


def load_en_words(path: Path | None = None) -> set[str]:
    """Настоящие английские слова — запрещены как term-алиас (страж I1, см. english_words.txt)."""
    return _load_word_set(path or (config_dir() / "english_words.txt"))


def lint(glossary: dict, ru_words: set[str], en_words: set[str] | None = None) -> list[str]:
    """Term-алиасы, совпадающие с настоящим словом (русским ИЛИ английским) — запрещены (I1)."""
    if en_words is None:
        en_words = load_en_words()
    blocked = ru_words | en_words
    return [k for k in glossary.get("terms", {}) if not k.startswith("_") and k.lower() in blocked]


# Одиночная кириллическая буква как ОТДЕЛЬНЫЙ токен people-метки — усечённый инициал Zoom-имени
# («дмитрий в»). Валиден для канонизации личности, но НЕ для текст-замены: одиночная кириллица
# совпадает с предлогом/союзом («в»/«с»/«и»), и «Дмитрий в отпуске» → «...Власов отпуске» (нарушение I1).
_CYR_INITIAL_RE = re.compile(r"^[а-яё]$")


def _people_text_safe(alias: str) -> bool:
    """People-метка пригодна для ТЕКСТ-замены, если ни один её токен не одиночная кир. буква."""
    return not any(_CYR_INITIAL_RE.match(tok) for tok in alias.split())


def alias_map(glossary: dict) -> dict[str, str]:
    m: dict[str, str] = {}
    for k, v in glossary.get("terms", {}).items():
        if not k.startswith("_"):
            m[k.lower()] = v
    for k, v in glossary.get("people", {}).items():
        if not k.startswith("_") and _people_text_safe(k.lower()):
            m[k.lower()] = v  # people побеждают при коллизии (кроме усечённых кир. инициалов)
    return m


# Алиас «склоняемый» (допустим кир. падежный хвост), если одиночное кир. слово len>=4.
_CYR_ALIAS_RE = re.compile(r"^[а-яё]+$")
_MIN_SUFFIX_ALIAS_LEN = 4

# Кириллические term-алиасы, которым добор хвоста ЗАПРЕЩЁН (хвост задел бы реальное слово):
# 'диалого' (префикс «диалогом/диалогов»), 'реакт' (префикс «реактор/реактивный»).
_SUFFIX_EXCLUDED_ALIASES = frozenset({"диалого", "реакт"})

# «en-термин по падежу»: склонённый кир. term-алиас канонизируем в латиницу ТОЛЬКО при ИМЕННОМ
# падежном хвосте (существительное). Глагольный хвост → verbatim (I1). «коммит/коммита/коммитом»
# → canon, «коммитить/коммитят/коммитил» → по-русски.
_NOUN_CASE_TAILS = frozenset(
    {
        "",
        "а",
        "у",
        "е",
        "ы",
        "ом",
        "ем",
        "ей",
        "ов",
        "ев",
        "ам",
        "ах",
        "ами",
    }
)
# Основа на шипящую (ж/ч/ш/щ) или «й»: глагольное 1sg без мутации согласной («мёрж»→«мёржу»),
# хвост «-у»/«-ю» неоднозначен (= дат. падеж) → НЕ канонизируем такие.
_HUSHING_FINAL = ("ж", "ч", "ш", "щ", "й")


def suffixable_aliases(glossary: dict) -> set[str]:
    """Кир. term-алиасы (НЕ people, len>=4), к которым правомерен падежный хвост."""
    out: set[str] = set()
    for k in glossary.get("terms", {}):
        if k.startswith("_"):
            continue
        alias = k.lower()
        if alias in _SUFFIX_EXCLUDED_ALIASES:
            continue
        if len(alias) >= _MIN_SUFFIX_ALIAS_LEN and _CYR_ALIAS_RE.match(alias):
            out.add(alias)
    return out


def apply_glossary(
    text: str,
    amap: dict[str, str],
    suffixable: set[str] | None = None,
) -> tuple[str, int]:
    """Заменить все алиасы на канонические формы за один проход (идемпотентно).

    Латинские алиасы — строго по границе слова. Term-алиасы из ``suffixable`` (кириллица,
    len>=4) дополнительно добирают кир. падежный хвост до 3 букв, ВЕСЬ матч → канон.
    People в ``suffixable`` не входят (инвариант неоднозначных имён). ``suffixable=None`` →
    без добора хвоста."""
    if not amap:
        return text, 0
    suffixable = suffixable or set()
    aliases = sorted(amap.keys(), key=len, reverse=True)  # длинные раньше коротких
    parts = [re.escape(a) + (r"[а-яё]{0,3}" if a in suffixable else "") for a in aliases]
    pattern = re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)
    count = 0

    def repl(mo: re.Match[str]) -> str:
        nonlocal count
        whole = mo.group(0)
        lowered = whole.lower()
        canon = None
        a = None
        for cand in aliases:
            if lowered.startswith(cand):
                a = cand
                canon = amap[cand]
                break
        if canon is None:
            return whole
        if a in suffixable:
            tail = lowered[len(a) :]
            if tail not in _NOUN_CASE_TAILS:
                return whole  # глагольный/иной хвост → verbatim (I1)
            if tail in ("у", "ю") and a.endswith(_HUSHING_FINAL):
                return whole  # шипящая/й-основа: «-у» неоднозначно → verbatim
        # Идемпотентность: скип, только если матч ПОЛНОСТЬЮ внутри уже стоящего канона.
        if len(canon) >= len(whole) and mo.string[mo.start() : mo.start() + len(canon)] == canon:
            return whole
        count += 1
        return canon

    return pattern.sub(repl, text), count


# --------------------------------------------------------------------------------------
# Интеграция в пайплайн DialogScribe (in-memory, по сегментам)
# --------------------------------------------------------------------------------------


class GlossaryLintError(ValueError):
    """Term-алиас совпал с настоящим русским/английским словом (переписал бы verbatim)."""


def load_runtime(strict: bool = False) -> tuple[dict[str, str], set[str]]:
    """Загрузить глоссарий + словари lint и вернуть (alias_map, suffixable).

    Прогоняет lint: при нарушении в ``strict`` режиме — ``GlossaryLintError``, иначе
    нарушенные алиасы молча выкидываются из карты (precision-first: лучше не канонизировать,
    чем переписать настоящее слово). Нет конфига → пустая карта (глоссарий просто не применяется."""
    glossary = load_glossary()
    if not glossary:
        return {}, set()
    violations = lint(glossary, load_ru_words(), load_en_words())
    if violations:
        if strict:
            raise GlossaryLintError(
                f"term-алиасы совпадают с настоящими словами (нарушили бы I1): {violations}"
            )
        terms = glossary.get("terms", {})
        for v in violations:
            terms.pop(v, None)  # выкинуть опасный алиас
    return alias_map(glossary), suffixable_aliases(glossary)


def apply_to_segments(
    segments: list[TranscriptionSegment],
    amap: dict[str, str],
    suffixable: set[str] | None = None,
) -> int:
    """Применить глоссарий к ``seg.text`` каждого сегмента (in-place). Возвращает число замен.

    Изменённым сегментам поднимает ``provenance`` до 'glossary' (через merge_provenance —
    не понижает 'second-opinion'/'human'). Кириллица вне курируемых алиасов не трогается (I1)."""
    if not amap:
        return 0
    total = 0
    for seg in segments:
        new_text, n = apply_glossary(seg.text, amap, suffixable)
        if n:
            seg.text = new_text
            # Per-word тайминги устарели после замены текста — иначе seg.text/seg.words
            # рассинхронизируются в JSON. Сброс: потребитель откатится на seg.text (to_dict).
            seg.words = None
            seg.provenance = merge_provenance(seg.provenance, "glossary")
            total += n
    return total
