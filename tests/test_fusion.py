"""Тесты слияния GigaAM↔L2 под I1 (инкремент 18). Кириллица неприкосновенна."""

from gigaam_transcriber.fusion import fuse


def test_cyrillic_never_replaced_even_if_whisper_differs():
    # whisper говорит «hello world», но giga-кириллица остаётся дословно (I1)
    assert fuse("привет мир", "hello world", {}) == "привет мир"
    assert fuse("привет", "hello", {}) == "привет"


def test_latin_token_replaced_by_second():
    assert fuse("я functon", "я Function", {}) == "я Function"


def test_mixed_segment_only_latin_changes():
    # «хелс» кириллица → verbatim; «functon» латиница → Function
    out = fuse("я люблю functon хелс", "я люблю Function Health", {})
    assert out == "я люблю Function хелс"


def test_nm_merge_multitoken_latin():
    assert fuse("Inside Traker", "InsideTracker", {}) == "InsideTracker"


def test_alias_canon_wins():
    assert fuse("paypline упал", "pipeline упал", {"paypline": "pipeline"}) == "pipeline упал"


def test_alias_canon_without_second():
    # без второго мнения работает только алиас-проход
    assert fuse("paypline", "", {"paypline": "pipeline"}) == "pipeline"


def test_digit_replaced():
    assert fuse("версия 50", "версия 15", {}) == "версия 15"


def test_punctuation_and_spaces_preserved():
    assert fuse("привет, мир!", "hello, world!", {}) == "привет, мир!"


def test_empty_second_keeps_giga():
    assert fuse("чистая кириллица", "", {}) == "чистая кириллица"
