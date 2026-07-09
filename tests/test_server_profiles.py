"""Профили источника: ScanProfile, track_mode, режимы вывода, fs-браузер, пресеты."""

import json
from pathlib import Path

import pytest

from gigaam_transcriber.server import media
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.job_runner import process_job
from gigaam_transcriber.server.local_watch import poll_local_source
from gigaam_transcriber.server.zoom_scan import ScanProfile, scan_meeting
from tests.conftest import MAGIC, FakeTranscriber, login_client, make_zoom_folder, server_settings


@pytest.fixture(autouse=True)
def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: False)


def _make(tmp_path):
    settings = server_settings(tmp_path, data_dir=tmp_path / "data")
    transcriber = FakeTranscriber()
    app = create_app(
        settings, enqueue=lambda jid: (process_job(settings, jid, transcriber), "gpu")[1]
    )
    return login_client(app), settings, transcriber


def _configure(c, watch_dir: Path, profile: dict | None = None):
    body = {"watch_dir": str(watch_dir), "enabled": True, "source_type": "local"}
    if profile is not None:
        body["scan_profile"] = profile
    r = c.put("/api/ingest/source", json=body)
    assert r.status_code == 200, r.text
    return r


# --------------------------------------------------------------------------- #
# ScanProfile.from_dict — толерантность
# --------------------------------------------------------------------------- #
def test_profile_from_dict_defaults_and_garbage():
    assert ScanProfile.from_dict(None) == ScanProfile()
    assert ScanProfile.from_dict({}) == ScanProfile()
    p = ScanProfile.from_dict(
        {"layout": "мусор", "track_mode": 5, "media_suffixes": [], "unknown": 1}
    )
    assert p.layout == "zoom" and p.track_mode == "combine"
    assert p.media_suffixes == ScanProfile.media_suffixes
    p2 = ScanProfile.from_dict({"tracks_subdir": "", "output": {"mode": "fixed", "dir": "/x"}})
    assert p2.tracks_subdir is None and p2.output_mode == "fixed" and p2.output_dir == "/x"


# --------------------------------------------------------------------------- #
# plain-layout
# --------------------------------------------------------------------------- #
def test_plain_layout_scans_top_level_media(tmp_path):
    folder = tmp_path / "созвон-01"
    folder.mkdir()
    (folder / "интервью.mp4").write_bytes(b"\x00" * 8)
    (folder / "заметка.m4a").write_bytes(b"\x00" * 4)
    (folder / "readme.txt").write_text("x")
    profile = ScanProfile.from_dict({"layout": "plain", "tracks_subdir": None})
    m = scan_meeting(folder, profile)
    assert m is not None and m.title == "созвон-01"
    assert m.magic.startswith("plain:")  # идентичность — контент, не имя папки
    assert m.parts[0].kind == "route_a"
    assert [t["name"] for t in m.parts[0].tracks] == ["заметка", "интервью"]


def test_plain_layout_single_file(tmp_path):
    folder = tmp_path / "лекция"
    folder.mkdir()
    (folder / "запись.m4a").write_bytes(b"\x00" * 8)
    m = scan_meeting(folder, ScanProfile.from_dict({"layout": "plain", "tracks_subdir": None}))
    assert m.parts[0].kind == "single" and m.parts[0].mix_path.endswith("запись.m4a")


# --------------------------------------------------------------------------- #
# track_mode: mix_only / separate (e2e через поллер)
# --------------------------------------------------------------------------- #
def _drain(c, settings, transcriber):
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    poll_local_source(settings, enqueue)
    return poll_local_source(settings, enqueue)


def test_mix_only_ignores_participant_tracks(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch)
    _configure(c, watch, {"track_mode": "mix_only"})
    started = _drain(c, settings, transcriber)
    assert len(started) == 1 and started[0]["kind"] == "single"
    data = json.loads((folder / "transcripts" / "dialogscribe" / "result.json").read_text())
    # single-путь фейка: один сегмент SPEAKER_00 (микс), не route_a-имена.
    assert {s["speaker"] for s in data["segments"]} == {"SPEAKER_00"}


def test_separate_makes_job_per_track_without_diarization(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch, participants=(("Alice", 1), ("Bob", 2)))
    _configure(c, watch, {"track_mode": "separate"})
    started = _drain(c, settings, transcriber)
    assert len(started) == 2 and all(s["kind"] == "single" for s in started)
    out = folder / "transcripts" / "dialogscribe"
    assert (out / "Alice" / "result.json").exists()
    assert (out / "Bob" / "result.json").exists()
    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 2 and all(j["state"] == "done" for j in jobs)
    # Диаризация выключена принудительно (одна дорожка = один голос).
    for j in jobs:
        detail = c.get(f"/api/jobs/{j['id']}").json()
        assert detail["state"] == "done"


@pytest.mark.parametrize(
    "first,second",
    [("mix_only", "combine"), ("combine", "separate"), ("separate", "mix_only")],
)
def test_track_mode_change_does_not_reingest(tmp_path, first, second):
    # Все режимы делят ключ дедупа: смена НЕ пере-ингестит обработанный архив.
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    make_zoom_folder(watch)
    _configure(c, watch, {"track_mode": first})
    assert len(_drain(c, settings, transcriber)) >= 1
    _configure(c, watch, {"track_mode": second})
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []


def test_failed_job_not_retried_by_auto_poll_only_by_force(tmp_path):
    # Битый файл не пережёвывается кроном бесконечно; ручной скан переклеймивает.
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    make_zoom_folder(watch)
    _configure(c, watch, None)

    class Broken(FakeTranscriber):
        def transcribe_route_a(self, tracks, **kw):
            raise FileNotFoundError("битый файл")

    fail = lambda jid: process_job(settings, jid, Broken())  # noqa: E731
    _drain(c, settings, Broken())  # прогрев окна (Broken → джоба упадёт)
    poll_local_source(settings, fail)
    # Джоба упала; авто-поллер больше НЕ создаёт новых джоб.
    n_jobs = len(c.get("/api/jobs").json()["jobs"])
    assert poll_local_source(settings, fail) == []
    assert len(c.get("/api/jobs").json()["jobs"]) == n_jobs
    # Ручной скан (force) переклеймивает.
    ok = lambda jid: process_job(settings, jid, FakeTranscriber())  # noqa: E731
    assert len(poll_local_source(settings, ok, force=True)) == 1


def test_plain_dedup_by_content_not_name(tmp_path):
    # plain: замена контента в папке с тем же именем → новая обработка;
    # переименование обработанной папки → дедуп (контент тот же).
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "plain"
    folder = watch / "Встреча"
    folder.mkdir(parents=True)
    (folder / "запись.m4a").write_bytes(b"\x00" * 8)
    _configure(c, watch, {"layout": "plain", "tracks_subdir": None})
    assert len(_drain(c, settings, transcriber)) == 1
    # Новая запись в папке с тем же именем (другой контент).
    (folder / "запись.m4a").unlink()
    (folder / "новая.m4a").write_bytes(b"\x00" * 16)
    import shutil

    shutil.rmtree(folder / "transcripts", ignore_errors=True)
    assert len(_drain(c, settings, transcriber)) == 1  # обработана заново
    # Переименование обработанной папки — контент тот же → дедуп.
    folder.rename(watch / "Встреча-архив")
    assert _drain(c, settings, transcriber) == []


def test_plain_video_duplicate_dropped(tmp_path):
    # plain: audio+video одной записи (общий цифровой хвост) — одна дорожка.
    folder = tmp_path / "встреча"
    folder.mkdir()
    (folder / "audio1399019170.m4a").write_bytes(b"\x00" * 8)
    (folder / "video1399019170.mp4").write_bytes(b"\x00" * 90)
    m = scan_meeting(folder, ScanProfile.from_dict({"layout": "plain", "tracks_subdir": None}))
    assert m.parts[0].kind == "single"
    assert m.parts[0].tracks[0]["path"].endswith(".m4a")


def test_suffixes_normalized_without_dot():
    p = ScanProfile.from_dict({"media_suffixes": ["ogg", ".M4A"]})
    assert p.media_suffixes == frozenset({".ogg", ".m4a"})


# --------------------------------------------------------------------------- #
# Вывод в отдельную папку (fixed)
# --------------------------------------------------------------------------- #
def test_fixed_output_dir(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch)
    dest = tmp_path / "транскрипты"
    dest.mkdir()
    _configure(c, watch, {"output": {"mode": "fixed", "dir": str(dest)}})
    started = _drain(c, settings, transcriber)
    assert len(started) == 1
    out = dest / folder.name
    assert (out / "result.json").exists() and (out / "transcript.txt").exists()
    assert not (folder / "transcripts" / "dialogscribe" / "result.json").exists()


def test_fixed_output_inside_watch_rejected(tmp_path):
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    r = c.put(
        "/api/ingest/source",
        json={
            "watch_dir": str(watch),
            "source_type": "local",
            "scan_profile": {"output": {"mode": "fixed", "dir": str(watch / "out")}},
        },
    )
    assert r.status_code == 400  # вывод внутри watch пере-детектился бы


def test_fixed_output_outside_allowlist_rejected(tmp_path, monkeypatch):
    # fixed output.dir подчиняется тому же allowlist, что watch_dir: воркер
    # делает mkdir и пишет файлы — иначе примитив записи в произвольный путь.
    root = tmp_path / "root"
    watch = root / "zoom"
    watch.mkdir(parents=True)
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(root))
    c, settings, _ = _make(tmp_path)
    r = c.put(
        "/api/ingest/source",
        json={
            "watch_dir": str(watch),
            "source_type": "local",
            "scan_profile": {"output": {"mode": "fixed", "dir": "/srv/backups"}},
        },
    )
    assert r.status_code == 400


def test_new_watch_dir_revalidated_against_saved_profile(tmp_path):
    # PUT только с watch_dir не должен обходить валидацию сохранённого fixed-вывода.
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    dest = tmp_path / "выход"
    dest.mkdir()
    _configure(c, watch, {"output": {"mode": "fixed", "dir": str(dest)}})
    # Теперь наблюдаемой папкой делают САМ каталог вывода → конфликт.
    r = c.put(
        "/api/ingest/source",
        json={"watch_dir": str(tmp_path), "enabled": True, "source_type": "local"},
    )
    assert r.status_code == 400


def test_watch_dir_allowlist_checked_before_existence(tmp_path, monkeypatch):
    # Вне allowlist ответ одинаков для существующего и несуществующего пути
    # (нет оракула серверных путей).
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(root))
    from gigaam_transcriber.server.local_watch import validate_watch_dir

    settings = _make(tmp_path)[1]
    existing = tmp_path / "существует"
    existing.mkdir()
    a = validate_watch_dir(settings, str(existing))
    b = validate_watch_dir(settings, str(tmp_path / "нет-такой"))
    assert a == b  # оба «вне разрешённой области», существование не различимо


def test_invalid_profile_rejected(tmp_path):
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    r = c.put(
        "/api/ingest/source",
        json={
            "watch_dir": str(watch),
            "source_type": "local",
            "scan_profile": {"track_mode": "чушь"},
        },
    )
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["/etc", "../../secret", "a/../../b"])
def test_tracks_subdir_traversal_rejected(tmp_path, bad):
    # tracks_subdir джойнится к папке встречи — абсолютный/«..» = traversal-чтение.
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    r = c.put(
        "/api/ingest/source",
        json={
            "watch_dir": str(watch),
            "source_type": "local",
            "scan_profile": {"tracks_subdir": bad},
        },
    )
    assert r.status_code == 400


def test_put_without_profile_keeps_saved_profile_and_params(tmp_path):
    # None-семантика: PUT без scan_profile/default_params не затирает сохранённое.
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    _configure(c, watch, {"track_mode": "separate"})
    c.put(
        "/api/ingest/source",
        json={"watch_dir": str(watch), "enabled": False, "source_type": "local"},
    )
    got = c.get("/api/ingest/source", params={"source_type": "local"}).json()
    assert got["scan_profile"]["track_mode"] == "separate"


# --------------------------------------------------------------------------- #
# /api/fs/browse
# --------------------------------------------------------------------------- #
def test_fs_browse_lists_dirs_hides_dotfiles(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(tmp_path))
    c, _, _ = _make(tmp_path)
    (tmp_path / "Папка").mkdir()
    (tmp_path / ".скрытая").mkdir()
    (tmp_path / "файл.txt").write_text("x")
    r = c.get("/api/fs/browse", params={"path": str(tmp_path)}).json()
    assert [d["name"] for d in r["dirs"] if d["name"] in ("Папка", ".скрытая")] == ["Папка"]
    assert r["parent"] is None  # корень allowlist — выше нельзя


def test_fs_browse_outside_root_403(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    c, _, _ = _make(tmp_path)
    assert c.get("/api/fs/browse", params={"path": str(tmp_path)}).status_code == 403
    # ..-траверс тоже закрыт (resolve).
    sneaky = str(tmp_path / "root" / ".." / "секрет")
    assert c.get("/api/fs/browse", params={"path": sneaky}).status_code == 403


def test_fs_browse_permission_error_is_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(tmp_path))
    c, _, _ = _make(tmp_path)
    locked = tmp_path / "закрыто"
    locked.mkdir(mode=0o000)
    try:
        r = c.get("/api/fs/browse", params={"path": str(locked)})
        assert r.status_code == 200
        body = r.json()
        assert body["denied"] is True and body["dirs"] == [] and body["parent"]
    finally:
        locked.chmod(0o755)


def test_fs_browse_requires_auth(tmp_path):
    c, _, _ = _make(tmp_path)
    c.post("/api/auth/logout")
    assert c.get("/api/fs/browse").status_code == 401


# --------------------------------------------------------------------------- #
# Пресеты
# --------------------------------------------------------------------------- #
def test_presets_builtin_and_crud(tmp_path):
    c, _, _ = _make(tmp_path)
    presets = c.get("/api/scan-presets").json()["presets"]
    names = [p["name"] for p in presets]
    assert names[:2] == ["Zoom", "Простая папка"]
    assert all(p["builtin"] for p in presets[:2])

    r = c.post(
        "/api/scan-presets",
        json={"name": "Подкаст", "body": {"layout": "plain", "track_mode": "separate"}},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    presets = c.get("/api/scan-presets").json()["presets"]
    mine = next(p for p in presets if p["id"] == pid)
    assert mine["body"]["track_mode"] == "separate" and not mine["builtin"]

    # Дубликат имени → 409; встроенное имя → 409; удаление встроенного → 400.
    assert c.post("/api/scan-presets", json={"name": "Подкаст", "body": {}}).status_code == 409
    assert c.post("/api/scan-presets", json={"name": "zoom", "body": {}}).status_code == 409
    assert c.delete("/api/scan-presets/zoom").status_code == 400

    assert c.delete(f"/api/scan-presets/{pid}").status_code == 204
    assert c.delete(f"/api/scan-presets/{pid}").status_code == 404


def test_preset_body_validated(tmp_path):
    c, _, _ = _make(tmp_path)
    r = c.post("/api/scan-presets", json={"name": "Кривой", "body": {"layout": "bad"}})
    assert r.status_code == 422  # pydantic Literal


# --------------------------------------------------------------------------- #
# parts_mode: склейка нескольких записей (стоп/старт) в один транскрипт
# --------------------------------------------------------------------------- #
def _add_second_part(folder: Path, participants=(("ТимурЯйк", 15), ("АнтонУст", 13))):
    """Дописать в Zoom-папку вторую запись (часть 2) с её подорожками."""
    (folder / f"audio2{MAGIC}.m4a").write_bytes(b"\x00" * 8)
    rec = folder / "Audio Record"
    rec.mkdir(exist_ok=True)
    for pname, idx in participants:
        (rec / f"audio{pname}{idx}2{MAGIC}.m4a").write_bytes(b"\x00" * 4)


def _enable_merge(monkeypatch, fail=False):
    """Смоделировать доступный ffmpeg и дешёвую «склейку» без реального ffmpeg."""
    from gigaam_transcriber.server import local_watch, media

    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)

    def fake_merge(meeting, parts, work):
        if fail:
            raise RuntimeError("ffmpeg упал")
        names = []
        for p in parts:
            for t in p.tracks:
                base = t.get("base", t["name"])
                if base not in names:
                    names.append(base)
        tracks = []
        for name in names:
            dst = work / f"{name}.m4a"
            dst.write_bytes(b"\x00" * 4)
            tracks.append({"name": name, "path": str(dst), "size": 4})
        return ("route_a" if len(tracks) > 1 else "single"), tracks

    monkeypatch.setattr(local_watch, "_build_merged_tracks", fake_merge)


def test_merge_parts_into_single_job(tmp_path, monkeypatch):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch, participants=(("ТимурЯйк", 8), ("AlexPedan", 7)))
    _add_second_part(folder)
    _enable_merge(monkeypatch)
    _configure(c, watch, {"parts_mode": "merge"})
    started = _drain(c, settings, transcriber)
    # Обе части → ОДНА джоба, вывод в корень dialogscribe (без «Часть N»).
    assert len(started) == 1 and started[0]["kind"] == "route_a"
    out = folder / "transcripts" / "dialogscribe"
    assert (out / "result.json").exists()
    assert not (out / "Часть 2").exists()
    data = json.loads((out / "result.json").read_text())
    speakers = {s["speaker"] for s in data["segments"]}
    assert "АнтонУст" in speakers and "ТимурЯйк" in speakers
    # Оба ключа частей терминальны: повторные проходы молчат.
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []


def test_parts_mode_switch_does_not_reingest(tmp_path, monkeypatch):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch, participants=(("A", 1),))
    _add_second_part(folder, participants=(("B", 2),))
    _enable_merge(monkeypatch)
    _configure(c, watch, {"parts_mode": "merge"})
    assert len(_drain(c, settings, transcriber)) == 1
    _configure(c, watch, {"parts_mode": "separate"})
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []  # ключи частей общие


def test_late_part_gets_own_job_in_merge_mode(tmp_path, monkeypatch):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch, participants=(("A", 1),))
    _enable_merge(monkeypatch)
    _configure(c, watch, {"parts_mode": "merge"})
    assert len(_drain(c, settings, transcriber)) == 1  # часть 1 (одна → без склейки)
    _add_second_part(folder, participants=(("B", 2),))
    started = _drain(c, settings, transcriber)  # часть 2 доехала позже
    assert len(started) == 1 and started[0]["part"] == 2
    assert (folder / "transcripts" / "dialogscribe" / "Часть 2" / "result.json").exists()


def test_merge_failure_is_retryable(tmp_path, monkeypatch):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch, participants=(("A", 1),))
    _add_second_part(folder, participants=(("B", 2),))
    _enable_merge(monkeypatch, fail=True)
    _configure(c, watch, {"parts_mode": "merge"})
    assert _drain(c, settings, transcriber) == []  # склейка упала — джоб нет
    assert len(c.get("/api/jobs").json()["jobs"]) == 0
    # Упавшая склейка (status='error') авто-поллером НЕ ретраится (не пережёвываем
    # битый файл каждый тик), но ручной скан (force → allow_reclaim) повторяет её.
    _enable_merge(monkeypatch, fail=False)
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []  # авто-поллер молчит
    assert len(poll_local_source(settings, enqueue, force=True)) == 1  # ручной скан


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="нужен ffmpeg")
def test_concat_track_parts_real_ffmpeg(tmp_path):
    # Реальная склейка: файл + тишина отсутствующей части ≈ сумма длительностей.
    import subprocess

    from gigaam_transcriber.server.media import concat_track_parts, probe_duration

    a = tmp_path / "a.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "aac",
            str(a),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "merged.m4a"
    concat_track_parts([([a], 2.0), ([], 3.0)], out)
    dur = probe_duration(out)
    assert dur is not None and abs(dur - 5.0) < 0.6
