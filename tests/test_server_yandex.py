"""M5 — Яндекс.Диск ручной ingestion: token/status/browse/pull, дедуп, шифрование."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.server import crypto, media
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.job_runner import process_job
from gigaam_transcriber.server.repository import get_yandex_auth
from gigaam_transcriber.server.security import hash_password
from gigaam_transcriber.server.yandex import ingest_pull

PASSWORD = "correct-horse-battery-staple"
VALID = "valid-token"


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
        Path(local).write_bytes(b"\x00\x00")


class FakeTranscriber:
    def transcribe_route_a(self, tracks, progress_callback=None, **kw):
        segs = [TranscriptionSegment(text="реплика", start=0.0, end=1.0, speaker=n) for n in tracks]
        return TranscriptionResult(
            text="x",
            segments=segs,
            duration=5.0,
            language="ru",
            model_name="fake",
            processing_time=1.0,
            metadata={"route": "A"},
        )

    def transcribe(self, input_path, **kw):
        segs = [TranscriptionSegment(text="привет", start=0.0, end=1.0, speaker="SPEAKER_00")]
        return TranscriptionResult(
            text="привет",
            segments=segs,
            duration=5.0,
            language="ru",
            model_name="fake",
            processing_time=1.0,
            metadata={},
        )


@pytest.fixture(autouse=True)
def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: False)


def _settings(tmp_path):
    return Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="session-key-aaaaaaaaaaaaaaaa",
        fernet_key="fernet-key-bbbbbbbbbbbbbbbb",
        data_dir=tmp_path,
        cookie_secure=False,
        require_https=False,
    )


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
    c = TestClient(app)
    c.post("/api/auth/login", data={"username": "admin", "password": PASSWORD})
    return c, settings


def test_crypto_roundtrip():
    enc = crypto.encrypt("k", "секрет-токен")
    assert enc != "секрет-токен"
    assert crypto.decrypt("k", enc) == "секрет-токен"
    assert crypto.decrypt("wrong-key", enc) is None


def test_status_without_token(tmp_path):
    c, _ = _make(tmp_path)
    assert c.get("/api/yandex/status").json() == {"connected": False, "check_ok": False}


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
    from gigaam_transcriber.server.repository import claim_ingest, get_ingest
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
    assert get_ingest(settings.db_path, sur)["status"] == "error"

    # повторный claim той же ревизии — НЕ дедуплицируется (была ошибка) → переклейм
    sur2 = claim_ingest(settings.db_path, key, "rd")
    assert sur2 is not None  # re-pull возможен (не «already_seen» навсегда)

    # успешное скачивание → запись/джоба создаются
    job = ingest_pull(settings, sur2, "route_a", tracks, FakeYandex(VALID), enqueue_gpu=None)
    assert job is not None
    assert get_ingest(settings.db_path, sur2)["status"] == "downloaded"


def test_downloaded_claim_is_not_reclaimed(tmp_path):
    from gigaam_transcriber.server.db import init_db
    from gigaam_transcriber.server.repository import claim_ingest, update_ingest

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    sur = claim_ingest(settings.db_path, "/x:1", None)
    update_ingest(settings.db_path, sur, status="downloaded")
    assert claim_ingest(settings.db_path, "/x:1", None) is None  # дедуп держится


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
    s = Settings(
        user="admin",
        password_hash=hash_password(PASSWORD),
        session_key="s" * 20,
        fernet_key="",
        data_dir=tmp_path,
    )
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
