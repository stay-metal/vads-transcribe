"""M3.3/M3.4 — джоб-пайплайн e2e: submit→done, result/overlay/download, cancel, HF_TOKEN.

Модель и ffmpeg не задействованы: транскрайбер — fake, media.* замокан.
Очередь — синхронный enqueue (process_job вызывается сразу).
"""

import io

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.server import media
from gigaam_transcriber.server.app import create_app
from gigaam_transcriber.server.job_runner import process_job
from tests.conftest import WAV, FakeTranscriber, login_client, server_settings


@pytest.fixture(autouse=True)
def _no_real_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)

    def fake_downmix(paths, out_path, **kw):
        from pathlib import Path

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x00\x00")
        return out_path

    monkeypatch.setattr(media, "downmix_tracks", fake_downmix)


def _settings(tmp_path, **over):
    return server_settings(tmp_path, **over)


def _make(tmp_path, sync=True):
    settings = _settings(tmp_path)
    transcriber = FakeTranscriber()

    def enqueue(job_id):
        if sync:
            process_job(settings, job_id, transcriber)
        return "task-" + job_id

    return login_client(create_app(settings, enqueue=enqueue))


def _file(name, data=WAV):
    return ("files", (name, io.BytesIO(data), "application/octet-stream"))


def test_route_a_job_end_to_end_without_hf_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c = _make(tmp_path)
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    rec_id = up["recording_id"]
    assert up["kind"] == "route_a"

    sub = c.post("/api/jobs", json={"recording_id": rec_id})
    assert sub.status_code == 200, sub.text
    job_id = sub.json()["job_id"]

    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "done"
    assert job["stage_pct"] == 100

    res = c.get(f"/api/jobs/{job_id}/result").json()
    speakers = {s["speaker"] for s in res["segments"]}
    assert speakers == {"Алиса", "Боб"}  # ground-truth имена, без диаризации

    for fmt in ("txt", "json", "srt", "vtt"):
        d = c.get(f"/api/jobs/{job_id}/download", params={"format": fmt})
        assert d.status_code == 200, fmt
        assert "attachment" in d.headers.get("content-disposition", "")
    # downmix-аудио доступно
    assert c.get(f"/api/jobs/{job_id}/audio").status_code == 200


def test_speaker_rename_overlay_does_not_mutate_result_json(tmp_path):
    c = _make(tmp_path)
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]

    # файл result.json на диске — до правки
    job_state = c.get(f"/api/jobs/{job_id}").json()
    assert job_state["state"] == "done"

    r = c.put(f"/api/jobs/{job_id}/speakers", json={"edits": {"Алиса": "Алиса Петрова"}})
    assert r.status_code == 200

    res = c.get(f"/api/jobs/{job_id}/result").json()
    renamed = {s["speaker"] for s in res["segments"]}
    assert "Алиса Петрова" in renamed
    assert "Алиса" not in renamed
    # provenance переименованного сегмента — human
    human = [s for s in res["segments"] if s["speaker"] == "Алиса Петрова"]
    assert all(s.get("provenance") == "human" for s in human)
    # скачанный txt отражает новое имя
    txt = c.get(f"/api/jobs/{job_id}/download", params={"format": "txt"}).text
    assert "Алиса Петрова" in txt


def test_single_diarized_requires_hf_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c = _make(tmp_path)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    assert up["kind"] == "single"
    r = c.post("/api/jobs", json={"recording_id": up["recording_id"], "diarization": "pyannote"})
    assert r.status_code == 400
    assert "HF_TOKEN" in r.text


def test_single_none_diarization_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c = _make(tmp_path)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "done"


def test_cancel_only_queued(tmp_path):
    # async-имитация: enqueue не обрабатывает → джоба остаётся queued
    c = _make(tmp_path, sync=False)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "queued"
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "canceled"
    # повторная отмена уже не queued → 409
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_api_restart_with_live_worker_keeps_running_job(tmp_path):
    # Рестарт api при живом gpu-воркере (есть ready-флаг) НЕ убивает 'asr'-джобу.
    from gigaam_transcriber.server import repository as repo
    from gigaam_transcriber.server.db import init_db

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = repo.create_job(settings.db_path, mode="single", source="upload")
    repo.claim_job(settings.db_path, job_id)  # queued→asr
    settings.ready_flag_path.parent.mkdir(parents=True, exist_ok=True)
    settings.ready_flag_path.write_text("ready")
    create_app(settings)
    assert repo.get_job(settings.db_path, job_id)["state"] == "asr"


def test_api_restart_without_worker_reconciles_orphan(tmp_path):
    # Нет ready-флага (воркер мёртв) → осиротевшая in-flight джоба честно в error.
    from gigaam_transcriber.server import repository as repo
    from gigaam_transcriber.server.db import init_db

    settings = _settings(tmp_path)
    init_db(settings.db_path)
    job_id = repo.create_job(settings.db_path, mode="single", source="upload")
    repo.claim_job(settings.db_path, job_id)
    settings.ready_flag_path.unlink(missing_ok=True)
    create_app(settings)
    job = repo.get_job(settings.db_path, job_id)
    assert job["state"] == "error" and job["error_code"] == "worker_restart"


def test_jobs_require_auth(tmp_path):
    settings = _settings(tmp_path)
    c = TestClient(create_app(settings, enqueue=lambda j: None))
    assert c.get("/api/jobs").status_code == 401
    assert c.post("/api/jobs", json={"recording_id": "x"}).status_code == 401


def test_result_not_ready_conflict(tmp_path):
    c = _make(tmp_path, sync=False)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    # результат ещё не готов (джоба queued) → 409
    assert c.get(f"/api/jobs/{job_id}/result").status_code == 409


# --------------------------------------------------------------------------- #
# ревью-фиксы M3
# --------------------------------------------------------------------------- #
class RaisingTranscriber:
    def transcribe_route_a(self, tracks, **kw):
        from gigaam_transcriber.exceptions import EmptyAudioError

        raise EmptyAudioError("/x/a.wav")

    def transcribe(self, input_path, **kw):
        raise RuntimeError("boom")


def _make_with_settings(tmp_path, transcriber, sync=True):
    settings = _settings(tmp_path)

    def enqueue(job_id):
        if sync:
            process_job(settings, job_id, transcriber)
        return "task-" + job_id

    return login_client(create_app(settings, enqueue=enqueue)), settings


def test_execution_error_sets_error_state(tmp_path):
    c, _ = _make_with_settings(tmp_path, RaisingTranscriber())
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "error"
    assert job["error_code"] == "empty_audio"  # классифицировано из EmptyAudioError
    assert "/x/" not in (job["error_message"] or "")  # путь не утёк


def test_result_json_on_disk_not_mutated_by_rename(tmp_path):
    c, settings = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]

    from gigaam_transcriber.server.repository import get_job

    disk_path = get_job(settings.db_path, job_id)["result_json_path"]
    before = open(disk_path, "rb").read()

    c.put(f"/api/jobs/{job_id}/speakers", json={"edits": {"Алиса": "Алиса Петрова"}})
    after = open(disk_path, "rb").read()
    assert before == after  # файл на диске байт-в-байт неизменён
    assert b"\xd0\x90\xd0\xbb\xd0\xb8\xd1\x81\xd0\xb0" in before  # "Алиса" (UTF-8) ещё там


def test_canceled_job_not_revived_by_worker(tmp_path):
    # cancel выигрывает гонку у claim: после отмены process_job не оживляет джобу
    c, settings = _make_with_settings(tmp_path, FakeTranscriber(), sync=False)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    # воркер забрал задачу позже — но claim_job не сработает (state=canceled)
    process_job(settings, job_id, FakeTranscriber())
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "canceled"


def test_result_carries_original_speaker_and_rerename(tmp_path):
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]

    res = c.get(f"/api/jobs/{job_id}/result").json()
    # сырой ярлык доступен как стабильный ключ
    assert all(s.get("original_speaker") for s in res["segments"])

    # первое переименование по сырому ярлыку
    c.put(f"/api/jobs/{job_id}/speakers", json={"edits": {"Алиса": "Алиса П."}})
    res = c.get(f"/api/jobs/{job_id}/result").json()
    seg = next(s for s in res["segments"] if s["original_speaker"] == "Алиса")
    assert seg["speaker"] == "Алиса П."

    # повторное переименование ПО ТОМУ ЖЕ сырому ключу (не по отображаемому) — не теряется
    c.put(f"/api/jobs/{job_id}/speakers", json={"edits": {"Алиса": "Алиса Петрова"}})
    res = c.get(f"/api/jobs/{job_id}/result").json()
    seg = next(s for s in res["segments"] if s["original_speaker"] == "Алиса")
    assert seg["speaker"] == "Алиса Петрова"
    # и в скачанном txt
    txt = c.get(f"/api/jobs/{job_id}/download", params={"format": "txt"}).text
    assert "Алиса Петрова" in txt


def test_jobs_list_date_filter(tmp_path):
    # Диапазон дат по created_at: полуинтервал [date_from, date_to), границы в UTC.
    from gigaam_transcriber.server.db import get_conn

    c, settings = _make_with_settings(tmp_path, FakeTranscriber())
    ids = []
    for _ in range(2):
        up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
        jid = c.post(
            "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
        ).json()["job_id"]
        ids.append(jid)
    # проставляем разные даты создания
    with get_conn(settings.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET created_at=? WHERE id=?", ("2026-01-10T09:00:00+00:00", ids[0])
        )
        conn.execute(
            "UPDATE jobs SET created_at=? WHERE id=?", ("2026-03-20T09:00:00+00:00", ids[1])
        )

    # только январь → первый джоб
    jan = c.get(
        "/api/jobs",
        params={
            "scope": "done",
            "date_from": "2026-01-01T00:00:00Z",
            "date_to": "2026-02-01T00:00:00Z",
        },
    ).json()
    assert {j["id"] for j in jan["jobs"]} == {ids[0]}
    assert jan["total"] == 1

    # широкий диапазон → оба
    both = c.get(
        "/api/jobs",
        params={
            "scope": "done",
            "date_from": "2026-01-01T00:00:00Z",
            "date_to": "2026-12-31T00:00:00Z",
        },
    ).json()
    assert {j["id"] for j in both["jobs"]} == set(ids)

    # верхняя граница исключительна: до 2026-03-20 09:00 → только январь
    upto = c.get(
        "/api/jobs",
        params={"scope": "done", "date_to": "2026-03-20T09:00:00Z"},
    ).json()
    assert {j["id"] for j in upto["jobs"]} == {ids[0]}

    # битая дата → 400
    assert c.get("/api/jobs", params={"date_from": "не-дата"}).status_code == 400


class CapturingTranscriber(FakeTranscriber):
    last_kwargs = None

    def transcribe(self, input_path, **kw):
        CapturingTranscriber.last_kwargs = dict(kw)
        return super().transcribe(input_path, **kw)


def test_single_opt_in_toggles_passthrough(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c, _ = _make_with_settings(tmp_path, CapturingTranscriber())
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    c.post(
        "/api/jobs",
        json={
            "recording_id": up["recording_id"],
            "diarization": "none",
            "second_opinion": True,
            "word_timestamps": True,
            "preclean": True,
            "backend": "onnx",
            "emit_l0": True,
        },
    )
    kw = CapturingTranscriber.last_kwargs
    assert kw["second_opinion"] is True
    assert kw["word_timestamps"] is True
    assert kw["preclean"] is True
    assert kw["backend"] == "onnx"
    assert "emit_l0" not in kw  # L0 пишет job_runner (transcribe без output_path его пропускал)


def test_l0_substrate_written_and_downloadable(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs",
        json={"recording_id": up["recording_id"], "diarization": "none", "emit_l0": True},
    ).json()["job_id"]

    # sha256 попал в metadata как verifiable-признак «L0 создан».
    res = c.get(f"/api/jobs/{job_id}/result").json()
    sha = res["metadata"].get("l0_sha256")
    assert isinstance(sha, str) and len(sha) == 64

    # download l0 → jsonl-файл; sha256 → sidecar с тем же хэшем.
    l0 = c.get(f"/api/jobs/{job_id}/download", params={"format": "l0"})
    assert l0.status_code == 200
    assert "transcript.v1.jsonl" in l0.headers["content-disposition"]
    assert l0.text.strip()  # непустой jsonl
    shafile = c.get(f"/api/jobs/{job_id}/download", params={"format": "sha256"})
    assert shafile.status_code == 200
    assert shafile.text.strip() == sha


def test_l0_absent_without_flag(tmp_path):
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    res = c.get(f"/api/jobs/{job_id}/result").json()
    assert "l0_sha256" not in res.get("metadata", {})
    assert c.get(f"/api/jobs/{job_id}/download", params={"format": "l0"}).status_code == 404


def test_metadata_source_not_leaked(tmp_path):
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    res = c.get(f"/api/jobs/{job_id}/result").json()
    assert "source" not in res.get("metadata", {})


def test_single_metadata_records_diarization(tmp_path):
    # F3: бэкенд диаризации фиксируется в metadata (иначе UI-хедер пуст).
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    res = c.get(f"/api/jobs/{job_id}/result").json()
    assert res["metadata"]["diarization"] == "none"


def test_job_events_sse_stream(tmp_path):
    # SSE: поток отдаёт состояние джобы и закрывается на терминальном (done).
    c = _make(tmp_path)  # sync → джоба сразу done
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    with c.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "done" in body and job_id in body


def test_endpoint_coverage_list_audio_srt(tmp_path):
    c, _ = _make_with_settings(tmp_path, FakeTranscriber())
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]

    listed = c.get("/api/jobs").json()["jobs"]
    assert any(j["id"] == job_id for j in listed)

    # audio Range → 206 (перемотка плеера)
    rng = c.get(f"/api/jobs/{job_id}/audio", headers={"Range": "bytes=0-0"})
    assert rng.status_code == 206

    srt = c.get(f"/api/jobs/{job_id}/download", params={"format": "srt"}).text
    assert "-->" in srt


def test_jobs_list_v2_filters_search_counts(tmp_path, monkeypatch):
    # Страница джоб: title из записи, scope-фильтры, поиск, счётчики, пагинация.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c = _make(tmp_path)
    for name in ("Дейли планёрка", "Синк команды"):
        up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
        # title записи задаётся при создании из имени первой дорожки
        del name
        c.post("/api/jobs", json={"recording_id": up["recording_id"]})

    r = c.get("/api/jobs").json()
    assert r["total"] == 2 and len(r["jobs"]) == 2
    assert r["counts"]["done"] == 2 and r["counts"]["active"] == 0
    assert all(j["title"] for j in r["jobs"])  # название записи доехало
    assert all(j["started_at"] for j in r["jobs"])

    # scope-фильтры
    assert c.get("/api/jobs", params={"scope": "done"}).json()["total"] == 2
    assert c.get("/api/jobs", params={"scope": "error"}).json()["total"] == 0
    assert c.get("/api/jobs", params={"scope": "мусор"}).status_code == 400

    # поиск по подстроке id
    jid = r["jobs"][0]["id"]
    hits = c.get("/api/jobs", params={"q": jid[:8]}).json()
    assert hits["total"] == 1 and hits["jobs"][0]["id"] == jid

    # пагинация
    page = c.get("/api/jobs", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["jobs"]) == 1


def test_jobs_list_avg_rtf_and_duration_upfront(tmp_path, monkeypatch):
    # duration_sec проставляется ДО обработки (ffprobe), avg_rtf считается по done.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(media, "probe_duration", lambda p, **kw: 120.0)
    c = _make(tmp_path, sync=False)  # не обрабатываем — джоба остаётся queued
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    c.post("/api/jobs", json={"recording_id": up["recording_id"]})
    r = c.get("/api/jobs").json()
    job = r["jobs"][0]
    assert job["state"] == "queued"
    assert job["duration_sec"] == 120.0  # известна заранее — UI посчитает ETA
    assert job["queue_position"] == 1
    assert r["counts"]["queued"] == 1


def test_jobs_search_escapes_like_wildcards(tmp_path, monkeypatch):
    # «_» и «%» в поиске — литералы, а не шаблон (иначе «_» матчит всё).
    monkeypatch.delenv("HF_TOKEN", raising=False)
    c = _make(tmp_path)
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    c.post("/api/jobs", json={"recording_id": up["recording_id"]})
    assert c.get("/api/jobs", params={"q": "_"}).json()["total"] == 0
    assert c.get("/api/jobs", params={"q": "%"}).json()["total"] == 0
    assert c.get("/api/jobs", params={"q": "Алиса"}).json()["total"] == 1


# --------------------------------------------------------------------------- #
# Пауза / кооперативная отмена / перетранскрибация
# --------------------------------------------------------------------------- #
def test_pause_resume_queued_job(tmp_path):
    c = _make(tmp_path, sync=False)  # enqueue-заглушка: джоба остаётся queued
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]

    assert c.post(f"/api/jobs/{job_id}/pause").json()["state"] == "paused"
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "paused"
    # Повторная пауза — 409; resume возвращает в очередь (и заново ставит задачу).
    assert c.post(f"/api/jobs/{job_id}/pause").status_code == 409
    assert c.post(f"/api/jobs/{job_id}/resume").json()["state"] == "queued"
    assert c.post(f"/api/jobs/{job_id}/resume").status_code == 409
    # Пауза недоступна для идущей/терминальной — только queued.


def test_pause_then_cancel(tmp_path):
    c = _make(tmp_path, sync=False)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    c.post(f"/api/jobs/{job_id}/pause")
    assert c.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "canceled"
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "canceled"


def test_paused_job_not_claimed_by_stale_huey_task(tmp_path):
    """Уже поставленная huey-задача паузу уважает: claim_job берёт только queued."""
    settings = _settings(tmp_path)
    transcriber = FakeTranscriber()
    pending = []
    c = login_client(create_app(settings, enqueue=lambda jid: (pending.append(jid), "t")[1]))
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    c.post(f"/api/jobs/{job_id}/pause")
    # «Отложенная» задача добегает до воркера уже после паузы.
    process_job(settings, job_id, transcriber)
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "paused"  # не тронута


def test_cancel_running_job_cooperatively(tmp_path):
    """Отмена ИДУЩЕЙ джобы: воркер замечает флаг на тике прогресса и завершает
    её как canceled, результат не пишется."""
    from gigaam_transcriber.server.db import get_conn
    from gigaam_transcriber.server.repository import request_cancel_running

    settings = _settings(tmp_path)

    class CancelMidRun:
        def transcribe_route_a(self, tracks, progress_callback=None, **kw):
            # Пользователь жмёт «Отменить», пока идёт обработка.
            with get_conn(settings.db_path) as conn:
                row = conn.execute("SELECT id FROM jobs WHERE state='asr'").fetchone()
            assert request_cancel_running(settings.db_path, row["id"])
            progress_callback(1, 2, "дорожка")  # тик замечает флаг → JobCanceled
            raise AssertionError("после отмены декод продолжаться не должен")

    trans = CancelMidRun()
    c = login_client(
        create_app(settings, enqueue=lambda jid: (process_job(settings, jid, trans), "t")[1])
    )
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]

    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "canceled"
    assert c.get(f"/api/jobs/{job_id}/result").status_code == 409  # результата нет


def test_cancel_endpoint_escalates_to_running(tmp_path):
    """POST /cancel на идущей джобе → 'canceling' (не 409, как раньше)."""
    from gigaam_transcriber.server.repository import claim_job

    settings = _settings(tmp_path)
    c = login_client(create_app(settings, enqueue=lambda jid: "t"))
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    claim_job(settings.db_path, job_id)  # эмуляция: воркер взял джобу (queued→asr)
    r = c.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "canceling"
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "canceling"


def test_rerun_done_job(tmp_path):
    """«Перетранскрибировать»: клон done-джобы уходит в очередь и завершается,
    manifest удалён (иначе resume вернул бы кэш вместо новой транскрипции)."""
    from pathlib import Path

    from gigaam_transcriber.server.repository import get_job as repo_get_job

    settings = _settings(tmp_path)
    transcriber = FakeTranscriber()
    c = login_client(
        create_app(settings, enqueue=lambda jid: (process_job(settings, jid, transcriber), "t")[1])
    )
    up = c.post("/api/uploads", files=[_file("Алиса.wav"), _file("Боб.wav")]).json()
    job_id = c.post("/api/jobs", json={"recording_id": up["recording_id"]}).json()["job_id"]
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "done"

    old = repo_get_job(settings.db_path, job_id)
    Path(old["manifest_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(old["manifest_path"]).write_text("{}", encoding="utf-8")  # «старый» manifest

    r = c.post(f"/api/jobs/{job_id}/rerun")
    assert r.status_code == 200, r.text
    new_id = r.json()["job_id"]
    assert new_id != job_id
    assert not Path(old["manifest_path"]).exists()  # кэш сброшен
    new = c.get(f"/api/jobs/{new_id}").json()
    assert new["state"] == "done"  # sync-enqueue обработал клон
    # Прежняя джоба осталась в списке.
    assert c.get(f"/api/jobs/{job_id}").json()["state"] == "done"


def test_rerun_requires_terminal_state(tmp_path):
    c = _make(tmp_path, sync=False)
    up = c.post("/api/uploads", files=[_file("mix.wav")]).json()
    job_id = c.post(
        "/api/jobs", json={"recording_id": up["recording_id"], "diarization": "none"}
    ).json()["job_id"]
    assert c.post(f"/api/jobs/{job_id}/rerun").status_code == 409  # ещё queued
