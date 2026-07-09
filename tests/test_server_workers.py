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


@pytest.mark.parametrize(
    "platform,worker_type",
    [("linux", "process"), ("darwin", "process"), ("darwin", "thread")],
)
def test_boot_guard_accepts_single_holder(monkeypatch, platform, worker_type):
    # process (везде) и thread (только macOS/F6) с -w 1 — один держатель модели.
    from gigaam_transcriber.server import workers

    monkeypatch.setattr(workers.sys, "platform", platform)
    assert_gpu_worker_config(worker_type, 1)  # не бросает


@pytest.mark.parametrize(
    "platform,worker_type,workers",
    [
        ("linux", "process", 2),
        ("linux", "thread", 1),  # thread — только darwin (нет изоляции/авто-рестарта)
        ("linux", "greenlet", 1),
        ("darwin", "gevent", 1),
        ("linux", "process", 0),
        ("darwin", "thread", 4),
    ],
)
def test_boot_guard_rejects_multi_or_greenlet(monkeypatch, platform, worker_type, workers):
    from gigaam_transcriber.server import workers

    monkeypatch.setattr(workers.sys, "platform", platform)
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
        [
            sys.executable,
            "-m",
            "gigaam_transcriber.server.run_gpu_worker",
            "-k",
            "process",
            "-w",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0
    assert "RuntimeError" in r.stderr


def test_run_gpu_worker_boot_guard_aborts_greenlet():
    import subprocess
    import sys

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "gigaam_transcriber.server.run_gpu_worker",
            "-k",
            "greenlet",
            "-w",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0
    assert "RuntimeError" in r.stderr


@pytest.mark.parametrize(
    "platform,requested,expected",
    [
        ("darwin", "process", "thread"),  # F6: Metal/MPS не живёт в fork
        ("darwin", "thread", "thread"),
        ("linux", "process", "process"),  # прод не затронут
        ("linux", "thread", "thread"),
    ],
)
def test_effective_worker_type(monkeypatch, platform, requested, expected):
    from gigaam_transcriber.server import run_gpu_worker

    monkeypatch.setattr(run_gpu_worker.sys, "platform", platform)
    assert run_gpu_worker._effective_worker_type(requested) == expected


def test_main_clears_stale_ready_flag_before_consumer(tmp_path, monkeypatch):
    # F8: stale-флаг убитого воркера лгал «ready» — лаунчер чистит его до старта.
    import sys as real_sys

    import huey.consumer as huey_consumer

    from gigaam_transcriber.server import run_gpu_worker
    from gigaam_transcriber.server.workers import READY_TOKEN_ENV

    monkeypatch.setenv("BLOODTRANSCRIPTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(READY_TOKEN_ENV, "sentinel")  # main перепишет; restore после теста
    monkeypatch.setattr(real_sys, "argv", list(real_sys.argv))  # main нормализует argv
    settings = Settings(data_dir=tmp_path, session_key="x")
    write_ready_flag(settings.ready_flag_path)

    ran = {}

    class FakeConsumer:
        def __init__(self, huey_obj, workers, worker_type):
            ran["workers"] = workers
            ran["worker_type"] = worker_type

        def run(self):
            ran["run"] = True

    monkeypatch.setattr(huey_consumer, "Consumer", FakeConsumer)
    monkeypatch.setattr(run_gpu_worker.sys, "platform", "linux")  # без darwin-подмены

    run_gpu_worker.main(["-k", "process", "-w", "1"])

    assert not settings.ready_flag_path.exists()  # stale-флаг вычищен
    assert ran == {"workers": 1, "worker_type": "process", "run": True}
    # SIGHUP-restart (os.execl python *sys.argv) должен получить -m-форму.
    assert real_sys.argv[:2] == ["-m", "gigaam_transcriber.server.run_gpu_worker"]


def test_clear_ready_flag_respects_owner_token(tmp_path, monkeypatch):
    # Перекрывающийся рестарт: чужой флаг (нового воркера) atexit-очистка не трогает.
    from gigaam_transcriber.server.workers import READY_TOKEN_ENV

    settings = Settings(data_dir=tmp_path, session_key="x")
    monkeypatch.setenv(READY_TOKEN_ENV, "новый-воркер")
    write_ready_flag(settings.ready_flag_path)

    clear_ready_flag(settings.ready_flag_path, only_token="старый-воркер")
    assert settings.ready_flag_path.exists()  # чужой токен — не снят

    clear_ready_flag(settings.ready_flag_path, only_token="новый-воркер")
    assert not settings.ready_flag_path.exists()  # свой — снят


def test_worker_exit_clears_ready_flag(tmp_path):
    # F8: выход процесса воркера снимает ready-флаг (atexit в лаунчере — huey-хук
    # on_shutdown не покрывает SIGTERM: non-graceful stop бросает daemon-треды).
    # Флаг пишем ПОСЛЕ main() (имитация warm_up) — снять его может только atexit.
    import os
    import subprocess
    import sys

    env = {**os.environ, "BLOODTRANSCRIPTS_DATA_DIR": str(tmp_path)}
    code = (
        "import huey.consumer as hc;"
        "hc.Consumer = type('FakeConsumer', (), "
        "{'__init__': lambda self, *a, **kw: None, 'run': lambda self: None});"
        "from gigaam_transcriber.server import run_gpu_worker as rgw;"
        "rgw.main(['-k', 'process', '-w', '1']);"
        "from gigaam_transcriber.server.config import Settings;"
        "from gigaam_transcriber.server.workers import write_ready_flag;"
        "write_ready_flag(Settings.from_env().ready_flag_path);"
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    settings = Settings(data_dir=tmp_path, session_key="x")
    assert not settings.ready_flag_path.exists()  # atexit снял флаг на выходе


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

    env = {**os.environ, "BLOODTRANSCRIPTS_DATA_DIR": str(tmp_path)}
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
