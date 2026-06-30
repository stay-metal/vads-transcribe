"""Тесты infra: StageTimer (#6) + versioning (#11)."""

from gigaam_transcriber.stage_timing import StageTimer
from gigaam_transcriber.versions import LAYER_VERSIONS, pipeline_versions


def test_stage_timer_accumulates():
    t = StageTimer()
    with t.measure("a"):
        pass
    t.add("a", 1.0)
    t.add("b", 2.0)
    d = t.as_dict()
    assert d["a"] >= 1.0 and d["b"] == 2.0
    assert t.total() >= 3.0


def test_pipeline_versions_is_copy():
    v = pipeline_versions()
    assert v == LAYER_VERSIONS
    v["asr"] = "mutated"
    assert LAYER_VERSIONS["asr"] != "mutated"  # копия не мутирует оригинал
