"""Тесты helper'ов L2 «второго мнения» (инкремент 18) — без загрузки модели."""

from gigaam_transcriber.whisper_asr import _build_context, _prompt_text, is_candidate


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
