"""Тест L1 (M0): репарация sticky GPU→CPU fallback на границе джобы.

Тёплый singleton сервера переиспользуется между джобами; ``_gpu_to_cpu_fallback``
после ОДНОГО GPU-сбоя защёлкивает модель на CPU навсегда. ``_repair_device`` на входе
каждой публичной джобы должен вернуть модель на исходное устройство. Реальная модель
не нужна — подменяем ``_model`` фейком, фиксирующим вызовы ``.to(device)``."""

from gigaam_transcriber import GigaAMTranscriber


class _FakeModel:
    def __init__(self):
        self.moved_to = []

    def to(self, device):
        self.moved_to.append(device)
        return self


def test_repair_restores_gpu_after_sticky_fallback():
    t = GigaAMTranscriber(device="cpu")  # на тест-машине нет CUDA → device='cpu'
    fake = _FakeModel()
    t._model = fake
    # эмулируем состояние после аварийного отката тёплого singleton на CPU
    t._intended_device = "cuda"
    t.device = "cpu"
    t._device_fell_back = True

    t._repair_device()

    assert fake.moved_to == ["cuda"]
    assert t.device == "cuda"
    assert t._device_fell_back is False


def test_repair_is_noop_on_cpu_intended():
    t = GigaAMTranscriber(device="cpu")
    fake = _FakeModel()
    t._model = fake
    t._device_fell_back = True  # на CPU отката не бывает, но пометка должна сброситься

    t._repair_device()

    assert fake.moved_to == []           # модель никуда не двигаем
    assert t.device == "cpu"
    assert t._device_fell_back is False  # пер-джобовая пометка сброшена


def test_repair_keeps_cpu_if_move_fails():
    """Если вернуть модель на GPU не удалось — остаёмся на CPU (лучше медленно, чем падать)."""
    t = GigaAMTranscriber(device="cpu")

    class _BrokenModel:
        def to(self, device):
            raise RuntimeError("cuda gone")

    t._model = _BrokenModel()
    t._intended_device = "cuda"
    t.device = "cpu"
    t._device_fell_back = True

    t._repair_device()  # не должно бросать

    assert t.device == "cpu"
    # пометка остаётся → текущая джоба честно отметит device_fallback
    assert t._device_fell_back is True


def test_init_sets_intended_device():
    t = GigaAMTranscriber(device="cpu")
    assert t._intended_device == "cpu"
    assert t._device_fell_back is False


def test_preload_pins_intended_device(monkeypatch):
    t = GigaAMTranscriber(device="cpu")
    # preload не должен грузить реальную модель в тесте — подменяем свойство model
    monkeypatch.setattr(type(t), "model", property(lambda self: object()))
    t.device = "cuda"  # как будто резолв дал GPU
    t.preload()
    assert t._intended_device == "cuda"
