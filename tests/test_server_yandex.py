"""M5 — Яндекс.Диск ручной ingestion: token/status/browse/pull, дедуп, шифрование."""

from pathlib import Path

import pytest

from gigaam_transcriber.server import crypto, media
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.job_runner import process_job
from gigaam_transcriber.server.repository import get_yandex_auth
from gigaam_transcriber.server.yandex import ingest_pull
from tests.conftest import FakeTranscriber, login_client, server_settings

VALID = "valid-token"


def _ingest_status(db_path, surrogate_id):
    """Статус ingest-claim по surrogate_id (прямой SQL вместо repo.get_ingest)."""
    from gigaam_transcriber.server.db import get_conn

    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM ingest_seen WHERE surrogate_id=?", (surrogate_id,)
        ).fetchone()
    return row["status"] if row else None


class FakeYandex:
    def __init__(self, token):
        self.token = token

    def check(self):
        return self.token == VALID

    def get_meta(self, path):
        if path.endswith(".mp3"):
            return {
                "name": "mix.mp3",
                "path": path,
                "type": "file",
                "revision": 7,
                "resource_id": "rf",
            }
        return {"name": "meeting", "path": path, "type": "dir", "revision": 5, "resource_id": "rd"}

    def listdir(self, path):
        if "zoom" in path:  # папка Zoom: аудио + видео-дубль той же встречи (F7)
            return [
                {
                    "name": "audio1791450993.m4a",
                    "path": f"{path}/audio1791450993.m4a",
                    "type": "file",
                    "revision": 9,
                    "resource_id": "za",
                    "size": 10,
                    "md5": "za",
                },
                {
                    "name": "video1791450993.mp4",
                    "path": f"{path}/video1791450993.mp4",
                    "type": "file",
                    "revision": 9,
                    "resource_id": "zv",
                    "size": 90,
                    "md5": "zv",
                },
                {
                    "name": "recording.conf",
                    "path": f"{path}/recording.conf",
                    "type": "file",
                    "revision": 9,
                    "resource_id": "zc",
                    "size": 1,
                    "md5": "zc",
                },
            ]
        if "screencast" in path:  # только видео — деградация до видео-дорожки
            return [
                {
                    "name": "запись.mp4",
                    "path": f"{path}/запись.mp4",
                    "type": "file",
                    "revision": 4,
                    "resource_id": "v",
                    "size": 50,
                    "md5": "v",
                },
            ]
        if "смешанная" in path:  # несвязанное видео НЕ дубль аудио — остаётся дорожкой
            return [
                {
                    "name": "интервью.mp4",
                    "path": f"{path}/интервью.mp4",
                    "type": "file",
                    "revision": 6,
                    "resource_id": "iv",
                    "size": 70,
                    "md5": "iv",
                },
                {
                    "name": "заметка.m4a",
                    "path": f"{path}/заметка.m4a",
                    "type": "file",
                    "revision": 6,
                    "resource_id": "zm",
                    "size": 10,
                    "md5": "zm",
                },
            ]
        return [
            {
                "name": "Алиса.m4a",
                "path": f"{path}/Алиса.m4a",
                "type": "file",
                "revision": 5,
                "resource_id": "a",
                "size": 10,
                "md5": "x",
            },
            {
                "name": "Боб.m4a",
                "path": f"{path}/Боб.m4a",
                "type": "file",
                "revision": 5,
                "resource_id": "b",
                "size": 10,
                "md5": "y",
            },
        ]

    def download(self, remote, local):
        # Валидная mp4/m4a-сигнатура (ftyp@4): ingest_pull проверяет magic-bytes.
        Path(local).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16)


@pytest.fixture(autouse=True)
def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: False)


def _settings(tmp_path):
    return server_settings(tmp_path)


def _make(tmp_path):
    settings = _settings(tmp_path)
    transcriber = FakeTranscriber()
    app = create_app(
        settings, enqueue=lambda jid: (process_job(settings, jid, transcriber), "gpu")[1]
    )
    app.state.yandex_factory = lambda token: FakeYandex(token)

    def sync_io(surrogate, kind, tracks):
        ingest_pull(
            settings,
            surrogate,
            kind,
            tracks,
            FakeYandex(VALID),
            enqueue_gpu=lambda jid: process_job(settings, jid, transcriber),
        )
        return "io"

    app.state.enqueue_io = sync_io
    return login_client(app), settings


def test_crypto_roundtrip():
    enc = crypto.encrypt("k", "секрет-токен")
    assert enc != "секрет-токен"
    assert crypto.decrypt("k", enc) == "секрет-токен"
    assert crypto.decrypt("wrong-key", enc) is None


def test_status_without_token(tmp_path):
    c, _ = _make(tmp_path)
    r = c.get("/api/yandex/status").json()
    assert r["connected"] is False and r["check_ok"] is False


def test_put_token_invalid(tmp_path):
    c, _ = _make(tmp_path)
    assert c.put("/api/yandex/token", json={"token": "bad"}).status_code == 400


def test_put_token_valid_encrypts_at_rest(tmp_path):
    c, settings = _make(tmp_path)
    r = c.put("/api/yandex/token", json={"token": VALID})
    assert r.status_code == 200
    auth = get_yandex_auth(settings.db_path)
    assert auth["check_ok"] is True
    assert VALID not in auth["token_enc"]  # at-rest зашифрован
    assert crypto.decrypt(settings.fernet_key, auth["token_enc"]) == VALID
    assert c.get("/api/yandex/status").json()["connected"] is True


def test_browse_requires_token(tmp_path):
    c, _ = _make(tmp_path)
    assert c.get("/api/yandex/browse", params={"path": "/x"}).status_code == 400


def test_browse_lists_folder(tmp_path):
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    entries = c.get("/api/yandex/browse", params={"path": "/Записи/meeting"}).json()["entries"]
    assert {e["name"] for e in entries} == {"Алиса.m4a", "Боб.m4a"}


def test_pull_route_a_creates_job_and_downloads(tmp_path):
    c, settings = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Записи/meeting"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pulling"
    assert r.json()["kind"] == "route_a"

    # io-задача (sync) скачала, создала запись/джобу, прогнала gpu → done
    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["state"] == "done"
    res = c.get(f"/api/jobs/{jobs[0]['id']}/result").json()
    assert {s["speaker"] for s in res["segments"]} == {"Алиса", "Боб"}


def test_pull_dedup_same_revision(tmp_path):
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r1 = c.post("/api/yandex/pull", json={"path": "/Записи/meeting"})
    assert r1.json()["status"] == "pulling"
    r2 = c.post("/api/yandex/pull", json={"path": "/Записи/meeting"})
    assert r2.json()["status"] == "already_seen"  # дедуп по path:revision
    assert len(c.get("/api/jobs").json()["jobs"]) == 1  # вторая джоба не создана


def test_pull_outside_watch_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/Записи")
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Другое/секрет"})
    assert r.status_code == 403


def test_pull_single_file(tmp_path):
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Записи/mix.mp3"})
    assert r.status_code == 200
    assert r.json()["kind"] == "single"


def test_pull_zoom_dir_prefers_audio_over_video(tmp_path):
    # F7: audio*.m4a + video*.mp4 одной встречи — НЕ два «участника» route_a.
    c, settings = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Записи/zoom-встреча"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "single"
    downloaded = list((Path(settings.data_dir) / "uploads" / r.json()["surrogate_id"]).iterdir())
    assert [p.suffix for p in downloaded] == [".m4a"]  # скачано только аудио
    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["mode"] == "single" and jobs[0]["state"] == "done"


def test_pull_video_only_dir_still_ingests(tmp_path):
    # Папка без чисто-аудио файлов: видео-контейнер остаётся валидным входом.
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Записи/screencast"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "single"


def test_pull_mixed_dir_keeps_unrelated_video(tmp_path):
    # Несвязанное видео (нет аудио-пары по stem/Zoom-ключу) — полноценная дорожка,
    # как и до F7: интервью.mp4 не должен молча выпадать из ingest.
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    r = c.post("/api/yandex/pull", json={"path": "/Записи/смешанная"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "route_a"  # обе дорожки, ничего не потеряно


def test_signature_waits_for_uploading_video_but_counts_audio_only():
    # F7 в авто-watch: пока видео-дубль дозаливается (md5=None) — папка НЕ
    # стабильна (ранний клейм терял бы поздние дорожки); после доливки len/rev
    # считаются только по клеймимому аудио.
    from gigaam_transcriber.server.yandex import _signature

    class Client:
        def __init__(self, video_md5):
            self.video_md5 = video_md5

        def listdir(self, path):
            return [
                {
                    "name": "audio1791450993.m4a",
                    "path": f"{path}/a",
                    "type": "file",
                    "revision": 3,
                    "size": 10,
                    "md5": "ok",
                },
                {
                    "name": "video1791450993.mp4",
                    "path": f"{path}/v",
                    "type": "file",
                    "revision": 8,
                    "size": 99,
                    "md5": self.video_md5,
                },
            ]

    entry = {"type": "dir", "path": "/w/встреча", "name": "встреча"}
    assert _signature(Client(video_md5=None), entry) is None  # видео ещё грузится
    assert _signature(Client(video_md5="done"), entry) == "dir|1|3"  # только аудио


def test_ingest_key_ignores_folder_revision_bump_from_video(tmp_path):
    # Дедуп-ключ — от ревизий клеймимых файлов: доливка видео-дубля бампает
    # ревизию ПАПКИ, но не должна порождать второй ingest той же встречи.
    from gigaam_transcriber.server.yandex import ingest_path

    _, settings = _make(tmp_path)  # инициализирует БД

    class Client:
        def __init__(self, folder_rev):
            self.folder_rev = folder_rev

        def get_meta(self, path):
            return {
                "name": "m",
                "path": path,
                "type": "dir",
                "revision": self.folder_rev,
                "resource_id": "rd",
            }

        def listdir(self, path):
            return [
                {
                    "name": "audio1791450993.m4a",
                    "path": f"{path}/audio1791450993.m4a",
                    "type": "file",
                    "revision": 3,
                    "size": 10,
                    "md5": "ok",
                },
            ]

    from gigaam_transcriber.server.repository import update_ingest

    r1 = ingest_path(settings, Client(folder_rev=100), "/Записи/m", None)
    assert r1["status"] == "pulling"
    # Скачивание завершилось (терминальный статус — иначе застрявший claim
    # переклеймивается по дизайну).
    update_ingest(settings.db_path, r1["surrogate_id"], status="downloaded")
    # Видео долилось → ревизия папки 200, аудио не изменилось → дедуп.
    r2 = ingest_path(settings, Client(folder_rev=200), "/Записи/m", None)
    assert r2["status"] == "already_seen"


def test_yandex_requires_auth(tmp_path):
    c, _ = _make(tmp_path)
    c.post("/api/auth/logout")
    assert c.get("/api/yandex/status").status_code == 401
    assert c.post("/api/yandex/pull", json={"path": "/x"}).status_code == 401


def test_single_pull_produces_done_job(tmp_path):
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    c.post("/api/yandex/pull", json={"path": "/Записи/mix.mp3"})
    jobs = c.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["mode"] == "single" and jobs[0]["state"] == "done"


def test_failed_download_releases_claim_for_repull(tmp_path):
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest
    from gigaam_transcriber.server.yandex import ingest_pull

    settings = _settings(tmp_path)
    init_db(settings.db_path)

    class Failing:
        def download(self, remote, local):
            raise RuntimeError("network blip")

    # первый claim + неудачное скачивание → status=error, джоба не создана
    key = "/Записи/meeting:5"
    sur = claim_ingest(settings.db_path, key, "rd")
    assert sur is not None
    tracks = [{"name": "Алиса", "remote": "/Записи/meeting/Алиса.m4a"}]
    assert ingest_pull(settings, sur, "route_a", tracks, Failing(), enqueue_gpu=None) is None
    assert _ingest_status(settings.db_path, sur) == "error"

    # авто-поллер (без allow_reclaim) НЕ пережёвывает упавший claim каждый тик
    assert claim_ingest(settings.db_path, key, "rd") is None
    # ручной re-pull (allow_reclaim) той же ревизии переклеймивает error → re-pull
    sur2 = claim_ingest(settings.db_path, key, "rd", allow_reclaim=True)
    assert sur2 is not None  # re-pull возможен (не «already_seen» навсегда)

    # успешное скачивание → запись/джоба создаются
    job = ingest_pull(settings, sur2, "route_a", tracks, FakeYandex(VALID), enqueue_gpu=None)
    assert job is not None
    assert _ingest_status(settings.db_path, sur2) == "downloaded"


def test_downloaded_claim_is_not_reclaimed(tmp_path):
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest, update_ingest

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    sur = claim_ingest(settings.db_path, "/x:1", None)
    update_ingest(settings.db_path, sur, status="downloaded")
    assert claim_ingest(settings.db_path, "/x:1", None) is None  # дедуп держится


@pytest.mark.parametrize("status", ["claimed", "downloading"])
def test_active_claim_is_not_reclaimed(tmp_path, status):
    # Загрузка идёт прямо сейчас: повторный клейм породил бы второе скачивание
    # и дубль-джобу — даже ручной pull (allow_reclaim) не трогает активную.
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest, update_ingest

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    sur = claim_ingest(settings.db_path, "/x:1", None)
    update_ingest(settings.db_path, sur, status=status)
    assert claim_ingest(settings.db_path, "/x:1", None) is None
    assert claim_ingest(settings.db_path, "/x:1", None, allow_reclaim=True) is None


def test_error_claim_reclaimed_only_with_permission(tmp_path):
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest, update_ingest

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    sur = claim_ingest(settings.db_path, "/x:1", None)
    update_ingest(settings.db_path, sur, status="error")
    assert claim_ingest(settings.db_path, "/x:1", None) is None  # авто-поллер не ретраит
    assert claim_ingest(settings.db_path, "/x:1", None, allow_reclaim=True) == sur


def test_single_file_bad_extension_rejected(tmp_path):
    # Ручной pull одиночного файла с не-аудио расширением → 415 ДО клейма.
    from gigaam_transcriber.server.yandex import IngestError, ingest_path

    _, settings = _make(tmp_path)

    class Meta:
        def get_meta(self, path):
            return {"name": "notes.txt", "path": path, "type": "file", "revision": 1}

    with pytest.raises(IngestError) as ei:
        ingest_path(settings, Meta(), "/Записи/notes.txt", None)
    assert ei.value.status == 415


def test_downloaded_bad_magic_bytes_fails_without_job(tmp_path):
    # Скачанное оказалось не медиа (HTML-заглушка): ingest → error, файл удалён,
    # запись/джоба НЕ создаются.
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest
    from gigaam_transcriber.server.yandex import ingest_pull

    settings = _settings(tmp_path)
    init_db(settings.db_path)

    class HtmlStub:
        def download(self, remote, local):
            Path(local).write_bytes(b"<html>not media</html>")

    sur = claim_ingest(settings.db_path, "/Записи/mix.mp3:7", "r")
    tracks = [{"name": "mix", "remote": "/Записи/mix.mp3"}]
    assert ingest_pull(settings, sur, "single", tracks, HtmlStub(), enqueue_gpu=None) is None
    assert _ingest_status(settings.db_path, sur) == "error"
    assert not (Path(settings.data_dir) / "uploads" / sur).exists()  # частичное удалено


def test_browse_respects_watch_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/Записи")
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    assert c.get("/api/yandex/browse", params={"path": "/Другое"}).status_code == 403


def test_watch_dir_traversal_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/Записи")
    c, _ = _make(tmp_path)
    c.put("/api/yandex/token", json={"token": VALID})
    # textual prefix-обход блокируется нормализацией
    assert c.post("/api/yandex/pull", json={"path": "/Записи/../секрет"}).status_code == 403
    assert c.post("/api/yandex/pull", json={"path": "/ЗаписиEVIL/x.mp3"}).status_code == 403


def test_fernet_key_required_for_serve(tmp_path):
    s = server_settings(tmp_path, fernet_key="")
    problems = s.validate_for_serve()
    assert any("FERNET_KEY" in p for p in problems)


# --------------------------------------------------------------------------- #
# Авто-watch (M6 v1.x)
# --------------------------------------------------------------------------- #
class WatchFake:
    """Клиент для авто-watch: watch_dir содержит одну папку-запись с 2 дорожками."""

    def __init__(self, rev=5, md5="x"):
        self.rev = rev
        self.md5 = md5

    def get_meta(self, path):
        return {
            "name": Path(path).name,
            "path": path,
            "type": "dir",
            "revision": self.rev,
            "resource_id": "m",
        }

    def listdir(self, path):
        if path == "/watch":
            return [
                {
                    "name": "meeting",
                    "path": "/watch/meeting",
                    "type": "dir",
                    "revision": self.rev,
                    "resource_id": "m",
                }
            ]
        return [
            {
                "name": "Алиса.m4a",
                "path": f"{path}/Алиса.m4a",
                "type": "file",
                "revision": self.rev,
                "md5": self.md5,
                "size": 10,
            },
            {
                "name": "Боб.m4a",
                "path": f"{path}/Боб.m4a",
                "type": "file",
                "revision": self.rev,
                "md5": self.md5,
                "size": 10,
            },
        ]


def test_ingest_source_get_put(tmp_path):
    c, _ = _make(tmp_path)
    assert c.get("/api/ingest/source").json()["configured"] is False
    r = c.put(
        "/api/ingest/source", json={"watch_dir": "/watch", "enabled": True, "poll_interval": 120}
    )
    assert r.status_code == 200
    got = c.get("/api/ingest/source").json()
    assert got["configured"] is True and got["watch_dir"] == "/watch" and got["enabled"] is True
    assert got["poll_interval"] == 120


def test_auto_watch_stability_window_then_claim(tmp_path):
    c, settings = _make(tmp_path)
    c.put("/api/ingest/source", json={"watch_dir": "/watch", "enabled": True})
    from gigaam_transcriber.server.repository import update_ingest
    from gigaam_transcriber.server.yandex import poll_ingest_sources

    enq = []

    def enq_io(s, k, t):
        enq.append((s, k, t))
        update_ingest(settings.db_path, s, status="downloaded")  # симуляция download

    fake = WatchFake()
    # 1-й проход — сигнатура впервые, ещё не устоялось (cnt=1 < 2) → без клейма
    assert poll_ingest_sources(settings, fake, enq_io) == []
    assert enq == []
    # 2-й проход — сигнатура неизменна → клейм + enqueue (route_a: 2 дорожки)
    res2 = poll_ingest_sources(settings, fake, enq_io)
    assert any(x["status"] == "pulling" for x in res2)
    assert len(enq) == 1 and enq[0][1] == "route_a"
    # 3-й проход — запись уже скачана → дедуп по path:revision, без второго enqueue
    poll_ingest_sources(settings, fake, enq_io)
    assert len(enq) == 1


def test_auto_watch_unstable_never_claims(tmp_path):
    c, settings = _make(tmp_path)
    c.put("/api/ingest/source", json={"watch_dir": "/watch", "enabled": True})
    from gigaam_transcriber.server.yandex import poll_ingest_sources

    enq = []
    fake = WatchFake(md5=None)  # файлы ещё дозаливаются → сигнатура None
    for _ in range(3):
        assert poll_ingest_sources(settings, fake, lambda s, k, t: enq.append((s, k, t))) == []
    assert enq == []


def test_auto_watch_disabled_noop(tmp_path):
    c, settings = _make(tmp_path)
    c.put("/api/ingest/source", json={"watch_dir": "/watch", "enabled": False})
    from gigaam_transcriber.server.yandex import poll_ingest_sources

    assert poll_ingest_sources(settings, WatchFake(), lambda *a: None) == []


# --------------------------------------------------------------------------- #
# OAuth (Authorization Code + refresh)
# --------------------------------------------------------------------------- #
def _oauth_env(monkeypatch):
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_SECRET", "secret")


def test_oauth_start_without_config_400(tmp_path, monkeypatch):
    monkeypatch.delenv("YANDEX_OAUTH_CLIENT_ID", raising=False)
    c, _ = _make(tmp_path)
    assert c.get("/api/yandex/oauth/start", follow_redirects=False).status_code == 400


def test_oauth_start_and_callback_stores_tokens(tmp_path, monkeypatch):
    _oauth_env(monkeypatch)
    c, settings = _make(tmp_path)

    r = c.get("/api/yandex/oauth/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert "oauth.yandex.ru/authorize" in loc and "cloud_api" in loc
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(loc).query)["state"][0]

    from gigaam_transcriber.server import yandex

    monkeypatch.setattr(
        yandex,
        "_token_request",
        lambda data: {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600},
    )
    cb = c.get(f"/api/yandex/oauth/callback?code=CODE&state={state}", follow_redirects=False)
    assert cb.status_code == 303 and "yandex=connected" in cb.headers["location"]

    assert c.get("/api/yandex/status").json()["connected"] is True
    auth = get_yandex_auth(settings.db_path)
    assert crypto.decrypt(settings.fernet_key, auth["token_enc"]) == "AT"
    assert crypto.decrypt(settings.fernet_key, auth["refresh_token_enc"]) == "RT"


def test_oauth_callback_bad_state_400(tmp_path, monkeypatch):
    _oauth_env(monkeypatch)
    c, _ = _make(tmp_path)
    c.get("/api/yandex/oauth/start", follow_redirects=False)  # ставит state-cookie
    cb = c.get("/api/yandex/oauth/callback?code=CODE&state=подделка", follow_redirects=False)
    assert cb.status_code == 400


def test_oauth_refresh_on_expiry(tmp_path, monkeypatch):
    _oauth_env(monkeypatch)
    c, settings = _make(tmp_path)
    from datetime import datetime, timedelta, timezone

    from gigaam_transcriber.server import yandex
    from gigaam_transcriber.server.repository import set_yandex_token

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    set_yandex_token(
        settings.db_path,
        crypto.encrypt(settings.fernet_key, "OLD"),
        check_ok=True,
        refresh_token_enc=crypto.encrypt(settings.fernet_key, "RT"),
        expires_at=past,
    )
    monkeypatch.setattr(
        yandex, "_token_request", lambda data: {"access_token": "NEW", "expires_in": 3600}
    )
    assert yandex._valid_access_token(settings) == "NEW"  # refresh сработал


def test_build_client_refreshes_expired_token(tmp_path, monkeypatch):
    # pull_recording ходит через build_client_from_settings → истёкший access
    # обновляется по refresh (иначе скачивание шло бы с протухшим токеном).
    _oauth_env(monkeypatch)
    _, settings = _make(tmp_path)
    from datetime import datetime, timedelta, timezone

    from gigaam_transcriber.server import yandex
    from gigaam_transcriber.server.repository import set_yandex_token

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    set_yandex_token(
        settings.db_path,
        crypto.encrypt(settings.fernet_key, "OLD"),
        check_ok=True,
        refresh_token_enc=crypto.encrypt(settings.fernet_key, "RT"),
        expires_at=past,
    )
    monkeypatch.setattr(
        yandex, "_token_request", lambda data: {"access_token": "NEW", "expires_in": 3600}
    )
    client = yandex.build_client_from_settings(settings, factory=lambda tok: tok)
    assert client == "NEW"


def test_expired_token_without_refresh_needs_reauth(tmp_path):
    # Истёк, refresh недоступен (нет OAuth-config/refresh) → browse 401, status reason.
    from datetime import datetime, timedelta, timezone

    from gigaam_transcriber.server.repository import set_yandex_token

    c, settings = _make(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    set_yandex_token(
        settings.db_path,
        crypto.encrypt(settings.fernet_key, "OLD"),
        check_ok=True,
        expires_at=past,
    )
    assert c.get("/api/yandex/browse", params={"path": "/x"}).status_code == 401
    st = c.get("/api/yandex/status").json()
    assert st["connected"] is True and st["check_ok"] is False
    assert "переавтор" in st["reason"].lower()


def test_claim_ingest_stale_active_reclaimable_manually(tmp_path):
    """SIGKILL io-воркера оставляет claim в 'downloading' навсегда — ручной pull/скан
    (allow_reclaim) переклеймивает такой claim по возрасту; свежий активный — нет."""
    from datetime import datetime, timedelta, timezone

    from gigaam_transcriber.server.db import get_conn, init_db
    from gigaam_transcriber.server.repository import claim_ingest

    db = tmp_path / "app.sqlite"
    init_db(db)
    s1 = claim_ingest(db, "disk:/a.m4a:rev1", None)
    assert s1 is not None
    # Свежий активный claim не крадётся даже вручную (загрузка может идти).
    assert claim_ingest(db, "disk:/a.m4a:rev1", None, allow_reclaim=True) is None
    # Состарим claim за порог зависания.
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with get_conn(db) as conn:
        conn.execute("UPDATE ingest_seen SET created_at=? WHERE key=?", (stale, "disk:/a.m4a:rev1"))
    # Авто-поллер (без allow_reclaim) по-прежнему молчит…
    assert claim_ingest(db, "disk:/a.m4a:rev1", None) is None
    # …а ручное действие возвращает тот же surrogate для повторной обработки.
    assert claim_ingest(db, "disk:/a.m4a:rev1", None, allow_reclaim=True) == s1
