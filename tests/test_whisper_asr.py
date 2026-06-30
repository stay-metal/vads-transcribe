"""Тесты helper'ов L2 «второго мнения» (инкремент 18) — без загрузки модели."""

from gigaam_transcriber.whisper_asr import (
    _build_context,
    _cache_path,
    _prompt_text,
    is_candidate,
)


def test_cache_path_varies_with_lang_hint():
    """Ключ L2-кэша зависит от lang_hint — иначе ru/en коллидируют по хэшу (bug_003)."""
    ru = _cache_path(b"audio", "small", "", "ru")
    en = _cache_path(b"audio", "small", "", "en")
    assert ru != en
    # одинаковые параметры → один и тот же ключ (кэш всё ещё попадает)
    assert _cache_path(b"audio", "small", "", "ru") == ru


def test_is_candidate_latin():
    assert is_candidate("сломался Function Health") is True
    assert is_candidate("только русский текст") is False
    assert is_candidate("") is False


def test_build_context_from_alias_map():
    ctx = _build_context({"харнес": "Harness", "постгрес": "Postgres", "пусто": ""})
    assert "Harness" in ctx and "Postgres" in ctx
    assert "пусто" not in ctx  # пустые каноны выкинуты


def test_prompt_text_compacts_and_truncates():
    assert _prompt_text(None) == ""
    assert _prompt_text("Harness  Postgres") == "Harness Postgres"
    long = " ".join(["слово"] * 500)
    out = _prompt_text(long)
    assert len(out) <= 900
