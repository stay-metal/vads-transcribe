"""Smoke-тесты единого CLI `dialogscribe` (M1).

Модель не грузится: класс `GigaAMTranscriber` подменяется фейком на CLI-модуле
(паттерн проекта). Проверяется маппинг флагов CLI → аргументы библиотеки,
вывод/файловые ветки, маршрут route-a без HF_TOKEN и подкоманды gallery.
"""

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner

import gigaam_transcriber.dialogscribe_cli as dcli
from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment


def _fake_result(text="привет", metadata=None):
    return TranscriptionResult(
        text=text,
        segments=[TranscriptionSegment(text=text, start=0.0, end=1.0)],
        duration=1.0,
        language="ru",
        model_name="fake",
        processing_time=0.0,
        metadata=metadata or {},
    )


class FakeTranscriber:
    """Подмена GigaAMTranscriber: фиксирует init/вызовы для ассертов."""

    last_init = None
    last_transcribe = None
    last_batch = None
    last_route_a = None
    discover_return = {"Алиса": "/x/a.m4a", "Боб": "/x/b.m4a"}

    def __init__(self, **kwargs):
        FakeTranscriber.last_init = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transcribe(self, input_path, **kwargs):
        FakeTranscriber.last_transcribe = {"input_path": input_path, **kwargs}
        return _fake_result()

    def transcribe_batch(self, input_paths, progress_callback=None, **kwargs):
        paths = list(input_paths)
        FakeTranscriber.last_batch = {"input_paths": paths, **kwargs}
        total = len(paths)
        if progress_callback:
            # Контракт библиотеки: (i,total,name) перед каждым файлом + финал.
            for i, p in enumerate(paths):
                progress_callback(i, total, Path(str(p)).name)
            progress_callback(total, total, "Готово")
        return [_fake_result() for _ in paths]

    def transcribe_route_a(self, tracks, progress_callback=None, **kwargs):
        FakeTranscriber.last_route_a = {"tracks": dict(tracks), **kwargs}
        if progress_callback:
            for i, name in enumerate(tracks, 1):
                progress_callback(i, len(tracks), name)
        return _fake_result(metadata={"route": "A", "tracks": list(tracks)})

    @staticmethod
    def discover_route_a_tracks(folder, speaker_dir="Audio Record"):
        return dict(FakeTranscriber.discover_return)


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch):
    FakeTranscriber.last_init = None
    FakeTranscriber.last_transcribe = None
    FakeTranscriber.last_batch = None
    FakeTranscriber.last_route_a = None
    FakeTranscriber.discover_return = {"Алиса": "/x/a.m4a", "Боб": "/x/b.m4a"}
    monkeypatch.setattr(dcli, "GigaAMTranscriber", FakeTranscriber)
    yield


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def audio(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"\x00\x00")
    return str(f)


# --------------------------------------------------------------------------- #
# help / version / структура группы
# --------------------------------------------------------------------------- #
def test_group_help_lists_commands(runner):
    res = runner.invoke(dcli.cli, ["--help"])
    assert res.exit_code == 0
    for cmd in ("transcribe", "batch", "route-a", "gallery", "serve"):
        assert cmd in res.output


def test_version(runner):
    res = runner.invoke(dcli.cli, ["--version"])
    assert res.exit_code == 0
    assert "dialogscribe" in res.output


@pytest.mark.parametrize("cmd", ["transcribe", "batch", "route-a", "gallery", "serve"])
def test_subcommand_help(runner, cmd):
    res = runner.invoke(dcli.cli, [cmd, "--help"])
    assert res.exit_code == 0


# --------------------------------------------------------------------------- #
# transcribe
# --------------------------------------------------------------------------- #
def test_transcribe_default_to_stdout(runner, audio):
    res = runner.invoke(dcli.cli, ["transcribe", audio])
    assert res.exit_code == 0, res.output
    assert "привет" in res.output
    assert "завершена" in res.output
    assert FakeTranscriber.last_transcribe["diarization"] == "none"
    # дефолты библиотеки
    assert FakeTranscriber.last_transcribe["merge_same_speaker"] is True
    assert FakeTranscriber.last_transcribe["glossary"] is True
    assert FakeTranscriber.last_transcribe["backend"] == "torch"


def test_transcribe_flag_mapping(runner, audio):
    res = runner.invoke(
        dcli.cli,
        [
            "transcribe",
            audio,
            "--no-merge",
            "--no-glossary",
            "--diarize",
            "pyannote",
            "--speakers",
            "3",
            "--min-speakers",
            "2",
            "--max-speakers",
            "5",
            "--gap",
            "0.9",
            "--backend",
            "onnx",
            "--onnx-int8",
            "--onnx-encoder",
            "--word-timestamps",
            "--second-opinion",
            "--preclean",
            "--emit-l0",
            "--resume",
            "--manifest",
            "/m/job.json",
            "--format",
            "json",
            "--model",
            "v3_e2e_ctc",
            "--device",
            "cpu",
        ],
    )
    assert res.exit_code == 0, res.output
    t = FakeTranscriber.last_transcribe
    assert t["merge_same_speaker"] is False
    assert t["glossary"] is False
    assert t["diarization"] == "pyannote"
    assert t["num_speakers"] == 3
    assert t["min_speakers"] == 2
    assert t["max_speakers"] == 5
    assert t["min_segment_gap"] == 0.9
    assert t["backend"] == "onnx"
    assert t["onnx_int8"] is True
    assert t["onnx_encoder"] is True
    assert t["word_timestamps"] is True
    assert t["second_opinion"] is True
    assert t["preclean"] is True
    assert t["emit_l0"] is True
    assert t["resume"] is True
    assert t["manifest_path"] == "/m/job.json"
    assert t["output_format"] == "json"
    assert FakeTranscriber.last_init["model_name"] == "v3_e2e_ctc"
    assert FakeTranscriber.last_init["device"] == "cpu"


def test_transcribe_diar_tuning_flags(runner, audio):
    res = runner.invoke(
        dcli.cli,
        [
            "transcribe",
            audio,
            "--diarize",
            "pyannote",
            "--diar-device",
            "mps",
            "--embedding-batch-size",
            "8",
            "--segmentation-batch-size",
            "16",
            "--diar-backend",
            "onnx",
        ],
    )
    assert res.exit_code == 0, res.output
    init = FakeTranscriber.last_init
    assert init["diar_device"] == "mps"
    assert init["embedding_batch_size"] == 8
    assert init["segmentation_batch_size"] == 16
    assert init["embedding_backend"] == "onnx"


def test_transcribe_diar_tuning_defaults(runner, audio):
    res = runner.invoke(dcli.cli, ["transcribe", audio])
    assert res.exit_code == 0, res.output
    init = FakeTranscriber.last_init
    assert init["diar_device"] is None
    assert init["embedding_batch_size"] is None
    assert init["segmentation_batch_size"] is None
    assert init["embedding_backend"] == "torch"


def test_transcribe_voiceprint_gallery(runner, audio):
    res = runner.invoke(
        dcli.cli, ["transcribe", audio, "--voiceprint", "--gallery", "/g/voices.json"]
    )
    assert res.exit_code == 0, res.output
    t = FakeTranscriber.last_transcribe
    assert t["voiceprint"] is True
    assert t["voiceprint_gallery"] == "/g/voices.json"


def test_transcribe_output_file_no_raw_stdout(runner, audio, tmp_path):
    out = str(tmp_path / "out.txt")
    res = runner.invoke(dcli.cli, ["transcribe", audio, "-o", out])
    assert res.exit_code == 0, res.output
    assert FakeTranscriber.last_transcribe["output_path"] == out
    # при -o сырой транскрипт в stdout не дублируется (только summary)
    assert "Сохранено в" in res.output


def test_transcribe_missing_file(runner):
    res = runner.invoke(dcli.cli, ["transcribe", "/nope/missing.wav"])
    assert res.exit_code == 2  # Click: аргумент не существует


def test_transcribe_diarize_warns_without_hf_token(runner, audio, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    res = runner.invoke(dcli.cli, ["transcribe", audio, "--diarize", "pyannote"])
    assert res.exit_code == 0, res.output
    assert "HF_TOKEN" in res.output  # combined output ловит stderr-предупреждение


def test_transcribe_clean_stdout_for_machine_format(runner, audio):
    """stdout несёт только JSON; summary/декор уходят в stderr (валидный `> out.json`)."""
    res = runner.invoke(dcli.cli, ["transcribe", audio, "-f", "json"])
    assert res.exit_code == 0, res.output
    import json

    json.loads(res.stdout)  # stdout — валидный JSON без декора
    assert "завершена" not in res.stdout  # summary не в stdout
    assert "завершена" in res.stderr  # а в stderr


def test_kwargs_match_real_transcribe_signature(runner, audio):
    """Guard от kwarg-drift: набор, который шлёт CLI, принимается реальной transcribe()."""
    res = runner.invoke(dcli.cli, ["transcribe", audio])
    assert res.exit_code == 0, res.output
    sent = dict(FakeTranscriber.last_transcribe)
    sent.pop("input_path")
    sig = inspect.signature(GigaAMTranscriber.transcribe)
    sig.bind_partial(None, **sent)  # бросит TypeError при неизвестном/переименованном kwarg


# --------------------------------------------------------------------------- #
# batch
# --------------------------------------------------------------------------- #
def test_batch_exposes_opt_in_flags(runner, tmp_path):
    f1 = tmp_path / "a.wav"
    f1.write_bytes(b"\x00")
    res = runner.invoke(
        dcli.cli,
        [
            "batch",
            str(f1),
            "-o",
            str(tmp_path / "out"),
            "--backend",
            "onnx",
            "--preclean",
            "--second-opinion",
            "--no-glossary",
            "--diar-device",
            "mps",
        ],
    )
    assert res.exit_code == 0, res.output
    b = FakeTranscriber.last_batch
    assert b["backend"] == "onnx"
    assert b["preclean"] is True
    assert b["second_opinion"] is True
    assert b["glossary"] is False
    # diar-тюнинг идёт в конструктор, не в transcribe_batch kwargs
    assert FakeTranscriber.last_init["diar_device"] == "mps"


# --------------------------------------------------------------------------- #
# batch
# --------------------------------------------------------------------------- #
def test_batch_two_files(runner, tmp_path):
    f1 = tmp_path / "a.wav"
    f2 = tmp_path / "b.wav"
    f1.write_bytes(b"\x00")
    f2.write_bytes(b"\x00")
    res = runner.invoke(
        dcli.cli, ["batch", str(f1), str(f2), "-o", str(tmp_path / "out")]
    )
    assert res.exit_code == 0, res.output
    assert FakeTranscriber.last_batch["input_paths"] == [str(f1), str(f2)]
    assert "Успешно: 2" in res.output


def test_batch_progress_echo_when_verbose(runner, tmp_path):
    f1 = tmp_path / "a.wav"
    f1.write_bytes(b"\x00")
    res = runner.invoke(dcli.cli, ["batch", str(f1), "-v"])
    assert res.exit_code == 0, res.output
    # verbose сворачивает rich-прогресс в построчный echo
    assert "[0/1]" in res.output or "Готово" in res.output


# --------------------------------------------------------------------------- #
# route-a
# --------------------------------------------------------------------------- #
def test_route_a_without_hf_token(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    folder = tmp_path / "rec"
    folder.mkdir()
    res = runner.invoke(dcli.cli, ["route-a", str(folder)])
    assert res.exit_code == 0, res.output
    assert "HF_TOKEN" not in res.output  # route-a токен не требует
    assert set(FakeTranscriber.last_route_a["tracks"]) == {"Алиса", "Боб"}
    assert "Найдено дорожек: 2" in res.output


def test_route_a_no_tracks(runner, tmp_path):
    FakeTranscriber.discover_return = {}
    folder = tmp_path / "rec"
    folder.mkdir()
    res = runner.invoke(dcli.cli, ["route-a", str(folder)])
    assert res.exit_code == 2  # UsageError
    assert "не найдены" in res.output


# --------------------------------------------------------------------------- #
# gallery
# --------------------------------------------------------------------------- #
def test_gallery_build(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "gal"))
    calls = {}

    def fake_build(tracks, embedder=None):
        calls["tracks"] = dict(tracks)
        return {name: [0.1, 0.2] for name in tracks}

    def fake_save(refs, path, **kw):
        calls["saved"] = str(path)
        from pathlib import Path

        Path(path).write_text("{}")
        return path

    monkeypatch.setattr(
        "gigaam_transcriber.voiceprint.build_gallery_from_tracks", fake_build
    )
    monkeypatch.setattr("gigaam_transcriber.voiceprint.save_gallery", fake_save)

    res = runner.invoke(
        dcli.cli,
        ["gallery", "build", "team", "--track", "Алиса=/x/a.m4a", "--track", "Боб=/x/b.m4a"],
    )
    assert res.exit_code == 0, res.output
    assert set(calls["tracks"]) == {"Алиса", "Боб"}
    assert calls["saved"].endswith("team.json")


def test_gallery_build_bad_track(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "gal"))
    res = runner.invoke(dcli.cli, ["gallery", "build", "team", "--track", "broken"])
    assert res.exit_code == 2
    assert "LABEL=PATH" in res.output


def test_gallery_list(runner, tmp_path, monkeypatch):
    gal = tmp_path / "gal"
    gal.mkdir()
    (gal / "team.json").write_text("{}")
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(gal))
    monkeypatch.setattr(
        "gigaam_transcriber.voiceprint.load_gallery",
        lambda p: ({"Алиса": [0.1]}, None, 0.1),
    )
    res = runner.invoke(dcli.cli, ["gallery", "list"])
    assert res.exit_code == 0, res.output
    assert "team" in res.output
    assert "Алиса" in res.output


def test_gallery_list_empty(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "empty"))
    res = runner.invoke(dcli.cli, ["gallery", "list"])
    assert res.exit_code == 0, res.output
    assert "нет" in res.output.lower()


def test_gallery_rm(runner, tmp_path, monkeypatch):
    gal = tmp_path / "gal"
    gal.mkdir()
    (gal / "team.json").write_text("{}")
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(gal))
    res = runner.invoke(dcli.cli, ["gallery", "rm", "team"])
    assert res.exit_code == 0, res.output
    assert not (gal / "team.json").exists()


def test_gallery_rm_missing(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "gal"))
    res = runner.invoke(dcli.cli, ["gallery", "rm", "ghost"])
    assert res.exit_code == 2
    assert "не найдена" in res.output


@pytest.mark.parametrize("bad", ["../evil", "../../etc/passwd", "/abs/path", "a/b", "..", "."])
def test_gallery_rm_rejects_traversal(runner, tmp_path, monkeypatch, bad):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "gal"))
    res = runner.invoke(dcli.cli, ["gallery", "rm", bad])
    assert res.exit_code == 2
    assert "Недопустимое имя" in res.output


def test_gallery_build_rejects_traversal(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_GALLERY_DIR", str(tmp_path / "gal"))
    res = runner.invoke(
        dcli.cli, ["gallery", "build", "/tmp/evil", "--track", "A=/x/a.m4a"]
    )
    assert res.exit_code == 2
    assert "Недопустимое имя" in res.output


# --------------------------------------------------------------------------- #
# serve (заглушка до M2)
# --------------------------------------------------------------------------- #
def test_serve_requires_config(runner, monkeypatch):
    # Без обязательных секретов serve завершается с ошибкой, не поднимая uvicorn.
    monkeypatch.delenv("DIALOGSCRIBE_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("DIALOGSCRIBE_SESSION_KEY", raising=False)
    res = runner.invoke(dcli.cli, ["serve"])
    assert res.exit_code == 1
    assert "переменные окружения" in res.output
