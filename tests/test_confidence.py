"""
Тесты per-chunk confidence (инкремент 4 переноса из custom).

chunk_confidence — чистая функция (без модели): exp(mean(logprob)) = геом. среднее
вероятностей. Интеграционная проверка декодера (текст бит-в-бит + confidence заполнен)
выполняется отдельно на живом аудио (tmp/bench/out_inc4_*).
"""

import math

from gigaam_transcriber.confidence import chunk_confidence


def test_empty_returns_none():
    assert chunk_confidence([]) is None
    assert chunk_confidence(None) is None


def test_single_token_equals_prob():
    # exp(mean([ln 0.9])) = 0.9
    assert abs(chunk_confidence([math.log(0.9)]) - 0.9) < 1e-9


def test_uniform_tokens():
    assert abs(chunk_confidence([math.log(0.5), math.log(0.5)]) - 0.5) < 1e-9


def test_geometric_not_arithmetic_mean():
    # geom-среднее 0.9 и 0.1 = sqrt(0.09) = 0.3 (а НЕ арифметическое 0.5)
    assert abs(chunk_confidence([math.log(0.9), math.log(0.1)]) - 0.3) < 1e-9


def test_range_0_1():
    c = chunk_confidence([math.log(0.99)] * 50)
    assert 0.0 < c <= 1.0
    # длинонормировка: 50 уверенных токенов не «топят» балл к нулю
    assert c > 0.9
