"""Локальный watch-конвейер: zoom_scan, поллер, вывод в папку встречи, API, миграция."""

import json
import unicodedata
from pathlib import Path

import pytest

from gigaam_transcriber.server import media
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.db import get_conn, init_db
from gigaam_transcriber.server.job_runner import process_job
from gigaam_transcriber.server.local_watch import poll_local_source, validate_watch_dir
from gigaam_transcriber.server.zoom_scan import folder_signature, scan_meeting
from tests.conftest import (
    MAGIC,
    FakeTranscriber,
    login_client,
    make_zoom_folder,
    server_settings,
)


@pytest.fixture(autouse=True)
def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: False)


def _settings(tmp_path):
    # watch_dir тестов лежит в tmp_path (напр. tmp_path/"zoom") — data_dir выносим
    # в отдельную подпапку, иначе они пересекутся (validate_watch_dir отвергнет).
    return server_settings(tmp_path, data_dir=tmp_path / "data")


def _make(tmp_path):
    settings = _settings(tmp_path)
    transcriber = FakeTranscriber()
    app = create_app(
        settings, enqueue=lambda jid: (process_job(settings, jid, transcriber), "gpu")[1]
    )
    return login_client(app), settings, transcriber


# --------------------------------------------------------------------------- #
# zoom_scan
# --------------------------------------------------------------------------- #
def test_scan_route_a_with_name_collisions(tmp_path):
    folder = make_zoom_folder(tmp_path)
    m = scan_meeting(folder)
    assert m is not None and m.magic == MAGIC and len(m.parts) == 1
    part = m.parts[0]
    assert part.kind == "route_a"
    # Перезаход «Ольга» (idx 4 и 6) → уникальные ярлыки, порядок по idx.
    assert [t["name"] for t in part.tracks] == [
        "ТимурЯйк",
        "PonimaiuAI",
        "Ольга (4)",
        "Ольга (6)",
    ]


def test_scan_single_without_audio_record(tmp_path):
    folder = make_zoom_folder(tmp_path, participants=())
    m = scan_meeting(folder)
    assert m is not None and m.parts[0].kind == "single"
    assert m.parts[0].tracks[0]["path"].endswith(f"audio1{MAGIC}.m4a")


def test_scan_normalizes_nfd_names(tmp_path):
    nfd = unicodedata.normalize("NFD", "ТимурЯйк")
    folder = make_zoom_folder(tmp_path, participants=((nfd, 1),))
    m = scan_meeting(folder)
    assert m.parts[0].tracks[0]["name"] == "ТимурЯйк"  # NFC наружу


def test_scan_magic_fallback_without_conf(tmp_path):
    folder = make_zoom_folder(tmp_path, with_conf=False, participants=())
    m = scan_meeting(folder)
    assert m is not None and m.magic == MAGIC  # из имени микса «1<magic>»


def test_scan_multipart_from_mixes(tmp_path):
    folder = make_zoom_folder(tmp_path, participants=())
    (folder / f"audio2{MAGIC}.m4a").write_bytes(b"\x00" * 8)  # рестарт записи
    m = scan_meeting(folder)
    assert [p.index for p in m.parts] == [1, 2]


def test_signature_barriers(tmp_path):
    folder = make_zoom_folder(tmp_path)
    sig = folder_signature(folder)
    assert sig and sig.startswith("local|")
    # .tmp-хвост (докачка) → None; после докачки сигнатура меняется.
    tmp_file = folder / "video.mp4.tmp"
    tmp_file.write_bytes(b"\x00")
    assert folder_signature(folder) is None
    tmp_file.unlink()
    assert folder_signature(folder) == sig
    # .zoom-чанки (конвертация не завершена) → None.
    zoom_chunk = folder / "double_click_to_convert_01.zoom"
    zoom_chunk.write_bytes(b"\x00")
    assert folder_signature(folder) is None
    zoom_chunk.unlink()
    # Файлы в transcripts/ — не контент встречи (свой вывод не пере-детектится).
    (folder / "transcripts" / "новое.m4a").write_bytes(b"\x00" * 100)
    assert folder_signature(folder) == sig


def test_signature_empty_folder_is_none(tmp_path):
    d = tmp_path / "пусто"
    d.mkdir()
    assert folder_signature(d) is None


# --------------------------------------------------------------------------- #
# Поллер: стабильность → джоба → вывод в папку встречи → дедуп
# --------------------------------------------------------------------------- #
def _configure_local(c, watch_dir: Path, enabled: bool = True):
    r = c.put(
        "/api/ingest/source",
        json={"watch_dir": str(watch_dir), "enabled": enabled, "source_type": "local"},
    )
    assert r.status_code == 200, r.text


def test_poll_transcribes_into_meeting_subfolder(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch)
    _configure_local(c, watch)

    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    # Окно стабильности: первый проход только записывает сигнатуру.
    assert poll_local_source(settings, enqueue) == []
    started = poll_local_source(settings, enqueue)
    assert len(started) == 1 and started[0]["kind"] == "route_a"

    out = folder / "transcripts" / "dialogscribe"
    assert (out / "result.json").exists()
    for fmt in ("txt", "srt", "vtt"):
        assert (out / f"transcript.{fmt}").exists(), fmt
    # Кириллица имён дорожек доехала до результата (NFC).
    data = json.loads((out / "result.json").read_text())
    assert "Ольга (4)" in {s["speaker"] for s in data["segments"]}
    # Чужой transcripts/ не тронут.
    assert (folder / "transcripts" / "чужое.md").read_text() == "x"

    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["state"] == "done"

    # Дедуп: повторные проходы (тот же magic) джоб не плодят.
    assert poll_local_source(settings, enqueue) == []
    assert len(c.get("/api/jobs").json()["jobs"]) == 1


def test_poll_disabled_source_noop_but_force_scans(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    make_zoom_folder(watch)
    _configure_local(c, watch, enabled=False)
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []
    # force (ручной скан) работает при выключенном тумблере и не ждёт окна.
    assert len(poll_local_source(settings, enqueue, force=True)) == 1


def test_scan_endpoint_starts_jobs(tmp_path):
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    make_zoom_folder(watch)
    _configure_local(c, watch, enabled=False)
    r = c.post("/api/ingest/local/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] is True and len(body["started"]) == 1
    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["state"] == "done"


def test_unstable_folder_not_claimed(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    folder = make_zoom_folder(watch)
    (folder / f"audio1{MAGIC}.m4a.tmp").write_bytes(b"\x00")  # докачивается
    _configure_local(c, watch)
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []
    assert poll_local_source(settings, enqueue) == []  # барьер держит и второй проход


def test_hidden_rsync_temp_blocks_signature(tmp_path):
    # rsync копирует в скрытый темп `.имя.XXXXXX` — папка НЕ стабильна.
    folder = make_zoom_folder(tmp_path)
    hidden = folder / "Audio Record" / ".audioНовый71399019170.m4a.Gx3aBc"
    hidden.write_bytes(b"\x00")
    assert folder_signature(folder) is None
    hidden.unlink()
    assert folder_signature(folder) is not None
    # .DS_Store — безобиден.
    (folder / ".DS_Store").write_bytes(b"\x00")
    assert folder_signature(folder) is not None


def test_failed_job_can_be_reingested(tmp_path):
    # Джоба упала → пользователь чинит файл → повторный скан переклеймивает.
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    make_zoom_folder(watch)
    _configure_local(c, watch)

    class BrokenTranscriber(FakeTranscriber):
        def transcribe_route_a(self, tracks, **kw):
            raise FileNotFoundError("битый файл")

    fail_enqueue = lambda jid: process_job(settings, jid, BrokenTranscriber())  # noqa: E731
    poll_local_source(settings, fail_enqueue)
    assert len(poll_local_source(settings, fail_enqueue)) == 1
    jobs = c.get("/api/jobs").json()["jobs"]
    assert jobs[0]["state"] == "error"

    ok_enqueue = lambda jid: process_job(settings, jid, FakeTranscriber())  # noqa: E731
    # Авто-поллер упавшую джобу НЕ пережёвывает; переклейм — только ручной скан.
    assert poll_local_source(settings, ok_enqueue) == []
    assert len(poll_local_source(settings, ok_enqueue, force=True)) == 1
    jobs = c.get("/api/jobs").json()["jobs"]
    assert {j["state"] for j in jobs} == {"error", "done"}
    # Успешная джоба терминальна окончательно — следующий force-скан молчит.
    assert poll_local_source(settings, ok_enqueue, force=True) == []


def test_symlinked_dirs_are_skipped(tmp_path):
    c, settings, transcriber = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    outside = make_zoom_folder(tmp_path / "outside")
    (watch / "ссылка").symlink_to(outside)
    _configure_local(c, watch)
    enqueue = lambda jid: process_job(settings, jid, transcriber)  # noqa: E731
    assert poll_local_source(settings, enqueue) == []
    assert poll_local_source(settings, enqueue) == []  # симлинк не контент


def test_part_layout_is_stable_regardless_of_part_count(tmp_path):
    # Часть 1 — всегда корень dialogscribe/, часть N≥2 — всегда подпапка:
    # появление части 2 после ингеста части 1 не сдвигает её вывод.
    from gigaam_transcriber.server.local_watch import _base_output_dir
    from gigaam_transcriber.server.zoom_scan import DEFAULT_PROFILE
    from gigaam_transcriber.server.zoom_scan import scan_meeting as scan

    folder = make_zoom_folder(tmp_path, participants=())
    one_part = scan(folder)
    where_part1_alone = _base_output_dir(one_part, 1, DEFAULT_PROFILE)
    (folder / f"audio2{MAGIC}.m4a").write_bytes(b"\x00" * 8)
    two_parts = scan(folder)
    assert _base_output_dir(two_parts, 1, DEFAULT_PROFILE) == where_part1_alone
    assert _base_output_dir(two_parts, 2, DEFAULT_PROFILE).name == "Часть 2"


def test_validate_expands_tilde(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    home = tmp_path / "home"
    (home / "Zoom").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert validate_watch_dir(settings, "~/Zoom") is None  # «~» из UI-подсказки


def test_local_watch_root_allowlist(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    allowed = tmp_path / "allowed" / "zoom"
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("DIALOGSCRIBE_LOCAL_WATCH_ROOT", str(tmp_path / "allowed"))
    assert validate_watch_dir(settings, str(allowed)) is None
    assert validate_watch_dir(settings, str(outside)) is not None


def test_validate_error_is_not_a_path_oracle(tmp_path):
    # «Не существует» и «нет прав» — одно сообщение (нет оракула серверных путей).
    settings = _settings(tmp_path)
    missing = validate_watch_dir(settings, str(tmp_path / "нет"))
    locked = tmp_path / "закрыто"
    locked.mkdir(mode=0o000)
    try:
        no_access = validate_watch_dir(settings, str(locked))
    finally:
        locked.chmod(0o755)
    assert missing == no_access


def test_scan_endpoint_errors_when_dir_vanished(tmp_path):
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    _configure_local(c, watch, enabled=False)
    watch.rmdir()  # папка исчезла после настройки
    r = c.post("/api/ingest/local/scan")
    assert r.status_code == 400  # не ложное «всё обработано»


# --------------------------------------------------------------------------- #
# API-валидация и конфиг источников
# --------------------------------------------------------------------------- #
def test_put_local_source_validates_dir(tmp_path):
    c, settings, _ = _make(tmp_path)
    r = c.put(
        "/api/ingest/source",
        json={"watch_dir": str(tmp_path / "нет-такой"), "source_type": "local"},
    )
    assert r.status_code == 400
    # Пересечение с data_dir запрещено (свой вывод пере-детектился бы).
    r = c.put(
        "/api/ingest/source",
        json={"watch_dir": str(settings.data_dir), "source_type": "local"},
    )
    assert r.status_code == 400
    r = c.put("/api/ingest/source", json={"watch_dir": "относительный", "source_type": "local"})
    assert r.status_code == 400


def test_sources_are_independent(tmp_path):
    c, settings, _ = _make(tmp_path)
    watch = tmp_path / "zoom"
    watch.mkdir()
    _configure_local(c, watch)
    # Яндекс-источник не настроен и не затронут локальным.
    assert c.get("/api/ingest/source").json()["configured"] is False
    got = c.get("/api/ingest/source", params={"source_type": "local"}).json()
    assert got["configured"] is True and got["watch_dir"] == str(watch)


def test_validate_watch_dir_rules(tmp_path):
    settings = _settings(tmp_path)
    ok = tmp_path / "ok"
    ok.mkdir()
    assert validate_watch_dir(settings, str(ok)) is None
    assert validate_watch_dir(settings, "rel/path") is not None
    assert validate_watch_dir(settings, str(tmp_path / "missing")) is not None
    assert validate_watch_dir(settings, str(settings.data_dir)) is not None


# --------------------------------------------------------------------------- #
# Миграция старого singleton-формата ingest_sources
# --------------------------------------------------------------------------- #
def test_ingest_sources_migration_from_singleton(tmp_path):
    db_path = tmp_path / "app.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE ingest_sources (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            watch_dir TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            poll_interval INTEGER NOT NULL DEFAULT 300,
            default_params TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        INSERT INTO ingest_sources VALUES (1, '/Записи', 1, 300, '{}', '2026-07-01T00:00:00');
        """)
    conn.close()
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = list(conn.execute("SELECT source_type, watch_dir, enabled FROM ingest_sources"))
    assert [(r["source_type"], r["watch_dir"], r["enabled"]) for r in rows] == [
        ("yandex", "/Записи", 1)
    ]
    init_db(db_path)  # идемпотентно
