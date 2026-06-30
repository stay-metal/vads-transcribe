"""M3.1 — repository: recordings / jobs / speaker_edits CRUD."""

from gigaam_transcriber.server import repository as repo
from gigaam_transcriber.server.db import init_db


def _db(tmp_path):
    p = tmp_path / "app.sqlite"
    init_db(p)
    return p


def test_recording_roundtrip(tmp_path):
    db = _db(tmp_path)
    tracks = [{"name": "Алиса", "path": "/x/a.m4a"}, {"name": "Боб", "path": "/x/b.m4a"}]
    rec_id = repo.create_recording(db, origin="upload", kind="route_a", title="rec1", tracks=tracks)
    rec = repo.get_recording(db, rec_id)
    assert rec["kind"] == "route_a"
    assert rec["track_count"] == 2
    assert rec["tracks"][0]["name"] == "Алиса"

    repo.update_recording_tracks(db, rec_id, tracks[:1])
    assert repo.get_recording(db, rec_id)["track_count"] == 1


def test_job_lifecycle(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="route_a", params={"glossary": True}, output_dir="/o")
    job = repo.get_job(db, job_id)
    assert job["state"] == "queued"
    assert job["stage_pct"] == 0
    assert job["params"]["glossary"] is True
    assert job["started_at"] is None

    repo.update_job_progress(db, job_id, "asr", 60)
    job = repo.get_job(db, job_id)
    assert job["state"] == "asr"
    assert job["stage_pct"] == 60
    assert job["started_at"] is not None  # проставлен при первом уходе из queued

    repo.finish_job_ok(
        db, job_id, result_json_path="/o/r.json", audio_path="/o/a.wav",
        duration_sec=12.0, processing_time_sec=3.0, device_fallback=True,
    )
    job = repo.get_job(db, job_id)
    assert job["state"] == "done"
    assert job["stage_pct"] == 100
    assert job["device_fallback"] is True
    assert job["finished_at"] is not None


def test_fail_job(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    repo.fail_job(db, job_id, "audio_too_long", "Запись длиннее лимита")
    job = repo.get_job(db, job_id)
    assert job["state"] == "error"
    assert job["error_code"] == "audio_too_long"


def test_cancel_only_queued(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    assert repo.cancel_job_if_queued(db, job_id) is True
    assert repo.get_job(db, job_id)["state"] == "canceled"

    # running джоба не отменяется
    job2 = repo.create_job(db, mode="single")
    repo.update_job_progress(db, job2, "asr", 50)
    assert repo.cancel_job_if_queued(db, job2) is False
    assert repo.get_job(db, job2)["state"] == "asr"


def test_speaker_edits_overlay(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    assert repo.get_speaker_edits(db, job_id) == {}
    repo.set_speaker_edit(db, job_id, "SPEAKER_00", "Алиса")
    repo.set_speaker_edit(db, job_id, "SPEAKER_01", "Боб")
    repo.set_speaker_edit(db, job_id, "SPEAKER_00", "Алиса Петрова")  # upsert
    edits = repo.get_speaker_edits(db, job_id)
    assert edits == {"SPEAKER_00": "Алиса Петрова", "SPEAKER_01": "Боб"}


def test_claim_job_atomic(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    assert repo.claim_job(db, job_id) is True  # queued→asr
    assert repo.get_job(db, job_id)["state"] == "asr"
    assert repo.claim_job(db, job_id) is False  # повторный захват не проходит


def test_claim_loses_to_cancel(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    assert repo.cancel_job_if_queued(db, job_id) is True
    assert repo.claim_job(db, job_id) is False  # уже canceled
    assert repo.get_job(db, job_id)["state"] == "canceled"


def test_reconcile_orphaned_jobs(tmp_path):
    db = _db(tmp_path)
    queued = repo.create_job(db, mode="single")
    running = repo.create_job(db, mode="route_a")
    repo.update_job_progress(db, running, "asr", 50)
    done = repo.create_job(db, mode="single")
    repo.update_job_progress(db, done, "asr", 50)
    repo.finish_job_ok(db, done, result_json_path=None, audio_path=None,
                       duration_sec=1, processing_time_sec=1, device_fallback=False)

    n = repo.reconcile_orphaned_jobs(db)
    assert n == 1  # только running помечен
    assert repo.get_job(db, running)["state"] == "error"
    assert repo.get_job(db, running)["error_code"] == "worker_restart"
    assert repo.get_job(db, queued)["state"] == "queued"  # очередь не трогаем
    assert repo.get_job(db, done)["state"] == "done"


def test_terminal_state_not_overwritten(tmp_path):
    db = _db(tmp_path)
    job_id = repo.create_job(db, mode="single")
    repo.cancel_job_if_queued(db, job_id)
    # попытка прогресса/финала поверх canceled — игнорируется
    repo.update_job_progress(db, job_id, "asr", 50)
    repo.finish_job_ok(db, job_id, result_json_path=None, audio_path=None,
                       duration_sec=1, processing_time_sec=1, device_fallback=False)
    assert repo.get_job(db, job_id)["state"] == "canceled"


def test_list_jobs_orders_recent_first(tmp_path):
    db = _db(tmp_path)
    ids = [repo.create_job(db, mode="single") for _ in range(3)]
    listed = repo.list_jobs(db)
    assert len(listed) == 3
    assert set(j["id"] for j in listed) == set(ids)
