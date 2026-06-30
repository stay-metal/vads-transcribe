"""Тесты самообучения глоссария (инкремент 20)."""

from gigaam_transcriber.glossary_grow import (
    harvest_corrections,
    harvest_log,
    log_corrections,
)


def test_frequent_latin_mangle_grows():
    pairs = [("reakkt", "React")] * 3 + [("roater", "OpenRouter")] * 2
    grown = harvest_corrections(pairs, min_count=3, ru_words=set(), en_words=set())
    assert grown == {"reakkt": "React"}  # roater только 2 < 3


def test_cyrillic_mangle_skipped():
    # кириллический ключ неприкосновенен (I1) — не латиница → не в terms
    assert harvest_corrections([("рак", "RAG")] * 5, min_count=3) == {}


def test_lint_blocks_real_english_word():
    grown = harvest_corrections([("date", "Date")] * 5, min_count=3, en_words={"date"})
    assert grown == {}


def test_dominant_canon_chosen():
    pairs = [("appload", "upload")] * 4 + [("appload", "Upload")] * 1
    assert harvest_corrections(pairs, min_count=3, en_words=set()) == {"appload": "upload"}


def test_no_op_pairs_ignored():
    assert harvest_corrections([("React", "React")] * 5, min_count=3) == {}


def test_log_and_harvest_roundtrip(tmp_path):
    log = tmp_path / "corr.jsonl"
    log_corrections([("reakkt", "React")] * 3, log)
    grown = harvest_log(log, min_count=3)
    assert grown.get("reakkt") == "React"


def test_harvest_missing_log(tmp_path):
    assert harvest_log(tmp_path / "nope.jsonl") == {}
