"""M1.2 — тюнинг диаризации пробрасывается из GigaAMTranscriber в DiarizationManager.

Менеджер диаризации не создаётся по-настоящему: класс подменяется фейком,
который фиксирует переданные параметры. I1-нейтрально — текст ASR не затрагивается.
"""

import gigaam_transcriber.transcriber as tmod
from gigaam_transcriber import GigaAMTranscriber


class _FakeDM:
    captured = {}

    def __init__(self, **kwargs):
        _FakeDM.captured = dict(kwargs)


def test_diar_tuning_propagates_to_manager(monkeypatch):
    monkeypatch.setattr(tmod, "DiarizationManager", _FakeDM)
    t = GigaAMTranscriber(
        device="cpu",
        diar_device="mps",
        embedding_batch_size=8,
        segmentation_batch_size=16,
        embedding_backend="onnx",
    )
    _ = t.diarization_manager  # триггерит ленивое создание
    assert _FakeDM.captured["device"] == "mps"
    assert _FakeDM.captured["embedding_batch_size"] == 8
    assert _FakeDM.captured["segmentation_batch_size"] == 16
    assert _FakeDM.captured["embedding_backend"] == "onnx"


def test_diar_device_defaults_to_main_device(monkeypatch):
    monkeypatch.setattr(tmod, "DiarizationManager", _FakeDM)
    t = GigaAMTranscriber(device="cpu")  # diar_device=None → совпадает с device
    _ = t.diarization_manager
    assert _FakeDM.captured["device"] == "cpu"
    assert _FakeDM.captured["embedding_batch_size"] is None
    assert _FakeDM.captured["segmentation_batch_size"] is None
    assert _FakeDM.captured["embedding_backend"] == "torch"
