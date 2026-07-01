"""M2.3 — gpu-worker boot-guard (L5), warm-preload, ready-флаг, конфиг очередей."""

import pytest

from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.queues import (
    huey_db_path,
    make_gpu_huey,
    make_io_huey,
)
from gigaam_transcriber.server.workers import (
    assert_gpu_worker_config,
    clear_ready_flag,
    warm_up,
    write_ready_flag,
)


def test_boot_guard_accepts_single_process():
    assert_gpu_worker_config("process", 1)  # не бросает


@pytest.mark.parametrize(
    "worker_type,workers",
    [("process", 2), ("thread", 1), ("greenlet", 1), ("process", 0), ("thread", 4)],
)
def test_boot_guard_rejects_multi_or_non_process(worker_type, workers):
    with pytest.raises(RuntimeError):
        assert_gpu_worker_config(worker_type, workers)


def test_warm_up_preloads_and_writes_ready_flag(tmp_path):
    settings = Settings(data_dir=tmp_path, session_key="x")
    captured = {}

    class FakeTranscriber:
        def __init__(self):
            self.preloaded = False

        def preload(self):
            self.preloaded = True

    def factory(s):
        captured["settings"] = s
        return FakeTranscriber()

    assert not settings.ready_flag_path.exists()
    transcriber = warm_up(settings, transcriber_factory=factory)
    assert transcriber.preloaded is True
    assert captured["settings"] is settings
    assert settings.ready_flag_path.exists()


def test_clear_ready_flag(tmp_path):
    settings = Settings(data_dir=tmp_path, session_key="x")
    write_ready_flag(settings.ready_flag_path)
    assert settings.ready_flag_path.exists()
    clear_ready_flag(settings.ready_flag_path)
    assert not settings.ready_flag_path.exists()
    clear_ready_flag(settings.ready_flag_path)  # идемпотентно


def test_two_queues_share_db_with_distinct_names(tmp_path):
    gpu = make_gpu_huey(tmp_path)
    io = make_io_huey(tmp_path)
    assert gpu.name == "gpu"
    assert io.name == "io"
    assert huey_db_path(tmp_path).name == "huey.sqlite"


def test_run_gpu_worker_boot_guard_aborts_multi_worker():
    # DoD#3 end-to-end: лаунчер падает ДО старта consumer/модели при -w 2.
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "gigaam_transcriber.server.run_gpu_worker", "-k", "process", "-w", "2"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0
    assert "RuntimeError" in r.stderr


def test_run_gpu_worker_boot_guard_aborts_thread():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "gigaam_transcriber.server.run_gpu_worker", "-k", "thread", "-w", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0
    assert "RuntimeError" in r.stderr


def test_ensure_forkable_forces_fork_on_darwin(monkeypatch):
    # F1: на macOS форсим fork-старт (иначе huey -k process не пиклится при spawn).
    import multiprocessing as mp

    from gigaam_transcriber.server import run_gpu_worker

    calls = []

    def _fake_set(method, force=False):
        calls.append((method, force))

    monkeypatch.setattr(run_gpu_worker.sys, "platform", "darwin")
    monkeypatch.setattr(mp, "set_start_method", _fake_set)
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)

    run_gpu_worker._ensure_forkable_start_method()

    assert calls == [("fork", True)]
    assert run_gpu_worker.os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"


def test_ensure_forkable_noop_on_linux(monkeypatch):
    # Прод (Linux): fork уже дефолт — start-method НЕ трогаем.
    import multiprocessing as mp

    from gigaam_transcriber.server import run_gpu_worker

    calls = []

    def _fake_set(method, force=False):
        calls.append((method, force))

    monkeypatch.setattr(run_gpu_worker.sys, "platform", "linux")
    monkeypatch.setattr(mp, "set_start_method", _fake_set)

    run_gpu_worker._ensure_forkable_start_method()

    assert calls == []


def test_tasks_module_exposes_two_queues_without_model(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "DIALOGSCRIBE_DATA_DIR": str(tmp_path)}
    code = (
        "import sys;"
        "from gigaam_transcriber.server.tasks import gpu_huey, io_huey;"
        "assert gpu_huey.name=='gpu' and io_huey.name=='io';"
        "leaked=[m for m in sys.modules if m=='gigaam' or m.startswith('gigaam.')];"
        "assert not leaked, leaked;"  # импорт tasks не тянет ASR-модель
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
