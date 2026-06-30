"""Тесты глоссария-канонизации (инкремент 9 переноса из custom).

Покрывают I1-страж (lint ru/en), longest-first, падежный добор + морфо-фильтр
(существительное канонизируем, глагол оставляем verbatim), идемпотентность,
provenance, и чистоту реального config/glossary.json.
"""

from gigaam_transcriber.data_models import TranscriptionSegment
from gigaam_transcriber.glossary import (
    alias_map,
    apply_glossary,
    apply_to_segments,
    lint,
    load_glossary,
    load_en_words,
    load_ru_words,
    suffixable_aliases,
)


def test_lint_blocks_real_russian_word():
    g = {"terms": {"понимаю": "Ponimaiu", "харнес": "Harness"}}
    v = lint(g, ru_words={"понимаю"}, en_words=set())
    assert "понимаю" in v and "харнес" not in v


def test_lint_blocks_real_english_word():
    g = {"terms": {"date": "Date", "ютрек": "YouTrack"}}
    assert lint(g, ru_words=set(), en_words={"date"}) == ["date"]


def test_alias_map_skips_single_cyrillic_initial_people():
    g = {"people": {"дмитрий в": "Дмитрий Власов", "alex pedan": "Алексей Педан"}, "terms": {}}
    m = alias_map(g)
    assert "дмитрий в" not in m  # усечённый инициал — не для текст-замены (I1)
    assert m["alex pedan"] == "Алексей Педан"


def test_apply_basic_term():
    out, n = apply_glossary("у нас харнес упал", {"харнес": "Harness"})
    assert out == "у нас Harness упал" and n == 1


def test_apply_longest_first():
    m = {"open": "X", "open-roter": "OpenRouter"}
    out, n = apply_glossary("сломался open-roter", m)
    assert "OpenRouter" in out and n == 1


def test_word_boundary_latin_not_in_cyrillic():
    out, n = apply_glossary("реакция", {"react": "React"})
    assert out == "реакция" and n == 0


def test_suffixable_declension_dative():
    g = {"terms": {"харнес": "Harness"}}
    suf, m = suffixable_aliases(g), alias_map(g)
    assert "харнес" in suf
    out, n = apply_glossary("по харнесу вопрос", m, suf)
    assert out == "по Harness вопрос" and n == 1


def test_noun_case_filter_keeps_verb():
    g = {"terms": {"коммит": "commit"}}
    suf, m = suffixable_aliases(g), alias_map(g)
    out_noun, n1 = apply_glossary("сделал коммит вчера", m, suf)
    out_verb, n2 = apply_glossary("надо коммитить", m, suf)
    assert "commit" in out_noun and n1 == 1
    assert out_verb == "надо коммитить" and n2 == 0  # глагол verbatim (I1)


def test_idempotent():
    m = {"харнес": "Harness"}
    out1, n1 = apply_glossary("харнес", m)
    out2, n2 = apply_glossary(out1, m)
    assert n1 == 1 and n2 == 0 and out2 == "Harness"


def test_apply_to_segments_sets_provenance_only_when_changed():
    segs = [
        TranscriptionSegment(text="наш харнес", start=0, end=1),
        TranscriptionSegment(text="без терминов", start=1, end=2),
    ]
    n = apply_to_segments(segs, {"харнес": "Harness"})
    assert n == 1
    assert segs[0].text == "наш Harness" and segs[0].provenance == "glossary"
    assert segs[1].provenance == "gigaam"  # не тронут


def test_apply_to_segments_drops_stale_words_on_change():
    """seg.words устаревают после замены текста → сбрасываются (рассинхрон в JSON, bug_009)."""
    from gigaam_transcriber.data_models import WordSegment
    changed = TranscriptionSegment(
        text="наш харнес",
        start=0,
        end=1,
        words=[
            WordSegment(word="наш", start=0.0, end=0.4),
            WordSegment(word="харнес", start=0.4, end=1.0),
        ],
    )
    untouched = TranscriptionSegment(
        text="без терминов",
        start=1,
        end=2,
        words=[WordSegment(word="без", start=1.0, end=1.4)],
    )
    apply_to_segments([changed, untouched], {"харнес": "Harness"})
    assert changed.text == "наш Harness"
    assert changed.words is None              # сброшены — текст изменён
    assert untouched.words is not None        # не тронут — тайминги сохранены


def test_real_config_lints_clean():
    g = load_glossary()
    if g:
        v = lint(g, load_ru_words(), load_en_words())
        assert v == [], f"config/glossary.json нарушает lint (переписал бы слова): {v}"


def test_load_glossary_honors_env_override_after_import(monkeypatch, tmp_path):
    """config_dir() резолвится при ВЫЗОВЕ → env-override, выставленный после import, работает (bug_014)."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "glossary.json").write_text('{"terms": {"foo": "Bar"}}', encoding="utf-8")
    monkeypatch.setenv("GIGAAM_TRANSCRIBER_CONFIG", str(cfg))
    assert load_glossary() == {"terms": {"foo": "Bar"}}
