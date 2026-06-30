"""Тесты детектора риска качества текста (инкремент 8 переноса из custom)."""

from gigaam_transcriber.text_quality import detect_quality_flags


def test_clean_text_no_flags():
    assert detect_quality_flags("обычная реплика про задачи") == []


def test_empty():
    assert detect_quality_flags("   ") == []


def test_hallucination_phrase():
    assert "hallucination_suspect" in detect_quality_flags("Спасибо за просмотр!")
    assert "hallucination_suspect" in detect_quality_flags("Субтитры сделал кто-то")


def test_loop_detection():
    flags = detect_quality_flags("да да да да да да да да да да")
    assert "loop_suspect" in flags


def test_varied_long_text_no_loop():
    assert detect_quality_flags(
        "мы обсудили задачи по админке интеграции и рендерингу отчёта врачам"
    ) == []


def test_does_not_modify_text():
    # детектор только возвращает флаги, текст остаётся за вызывающим (I1)
    txt = "да да да да да да да да"
    flags = detect_quality_flags(txt)
    assert isinstance(flags, list)
    assert txt == "да да да да да да да да"  # вход не мутирован
