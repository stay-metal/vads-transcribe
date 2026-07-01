"""CRUD-слой над app.sqlite: recordings / jobs / speaker_edits (M3).

Все имена на диске — из server-uuid (id), не из пользовательских строк. SQL только
параметризованный. Применение speaker-edits — на чтении (result.json не мутируется).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import get_conn

# Состояния джобы (спека §7).
JOB_STATES = (
    "queued",
    "preclean",
    "vad",
    "diarization",
    "asr",
    "quality",
    "formatting",
    "done",
    "error",
    "canceled",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# recordings
# --------------------------------------------------------------------------- #
def create_recording(
    db_path: Path,
    *,
    origin: str,
    kind: str,
    title: str | None = None,
    tracks: list[dict[str, Any]] | None = None,
    ingest_key: str | None = None,
) -> str:
    tracks = tracks or []
    rec_id = new_id()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO recordings(id, origin, title, kind, track_count, tracks_json, "
            "ingest_key, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                rec_id,
                origin,
                title,
                kind,
                len(tracks),
                json.dumps(tracks, ensure_ascii=False),
                ingest_key,
                _now(),
            ),
        )
    return rec_id


def get_recording(db_path: Path, rec_id: str) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
    rec = _row_to_dict(row)
    if rec is not None:
        rec["tracks"] = json.loads(rec.pop("tracks_json") or "[]")
    return rec


def update_recording_tracks(db_path: Path, rec_id: str, tracks: list[dict[str, Any]]) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE recordings SET tracks_json=?, track_count=? WHERE id=?",
            (json.dumps(tracks, ensure_ascii=False), len(tracks), rec_id),
        )


def set_recording_latest_job(db_path: Path, rec_id: str, job_id: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE recordings SET latest_job_id=? WHERE id=?", (job_id, rec_id))


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def create_job(
    db_path: Path,
    *,
    mode: str,
    source: str = "upload",
    recording_id: str | None = None,
    params: dict[str, Any] | None = None,
    work_dir: str | None = None,
    output_dir: str | None = None,
    manifest_path: str | None = None,
) -> str:
    job_id = new_id()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs(id, recording_id, source, mode, state, stage_pct, "
            "params_json, work_dir, output_dir, manifest_path, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                recording_id,
                source,
                mode,
                "queued",
                0,
                json.dumps(params or {}, ensure_ascii=False),
                work_dir,
                output_dir,
                manifest_path,
                _now(),
            ),
        )
    return job_id


def get_job(db_path: Path, job_id: str) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    job = _row_to_dict(row)
    if job is not None:
        job["params"] = json.loads(job.pop("params_json") or "{}")
        job["device_fallback"] = bool(job["device_fallback"])
    return job


def list_jobs(db_path: Path, limit: int = 100) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        job = dict(row)
        job["params"] = json.loads(job.pop("params_json") or "{}")
        job["device_fallback"] = bool(job["device_fallback"])
        out.append(job)
    return out


def set_job_huey_task(db_path: Path, job_id: str, huey_task_id: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE jobs SET huey_task_id=? WHERE id=?", (huey_task_id, job_id))


def set_job_dirs(
    db_path: Path,
    job_id: str,
    *,
    work_dir: str,
    output_dir: str,
    manifest_path: str,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET work_dir=?, output_dir=?, manifest_path=? WHERE id=?",
            (work_dir, output_dir, manifest_path, job_id),
        )


_TERMINAL = ("done", "error", "canceled")


def claim_job(db_path: Path, job_id: str) -> bool:
    """Атомарно захватить queued-джобу (queued→asr). Возвращает True победителю.

    CAS `WHERE state='queued'` исключает гонку cancel↔старт и двойной запуск:
    либо cancel перевёл в canceled (тогда claim вернёт False → воркер выходит),
    либо claim выиграл (тогда cancel получит 409).
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET state='asr', stage_pct=45, started_at=COALESCE(started_at, ?) "
            "WHERE id=? AND state='queued'",
            (_now(), job_id),
        )
        return cur.rowcount > 0


def update_job_progress(db_path: Path, job_id: str, state: str, stage_pct: int) -> None:
    """Обновить стадию/процент; терминальные состояния не перезаписываются."""
    with get_conn(db_path) as conn:
        if state != "queued":
            conn.execute(
                "UPDATE jobs SET started_at=COALESCE(started_at, ?) WHERE id=?",
                (_now(), job_id),
            )
        conn.execute(
            "UPDATE jobs SET state=?, stage_pct=? WHERE id=? AND state NOT IN ('done','error','canceled')",
            (state, stage_pct, job_id),
        )


def reconcile_orphaned_jobs(db_path: Path) -> int:
    """Перевести «зависшие» in-flight джобы в error (рестарт api/воркера).

    При перезапуске воркер теряет выполняемую джобу (SqliteHuey не возобновляет
    in-flight). Помечаем такие честно как error, чтобы poll не «висел» вечно.
    Возвращает число помеченных. (Resume-adopt по manifest — v1.x.)
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET state='error', error_code='worker_restart', "
            "error_message='Обработка прервана перезапуском', finished_at=? "
            "WHERE state IN ('preclean','vad','diarization','asr','quality','formatting')",
            (_now(),),
        )
        return cur.rowcount


def finish_job_ok(
    db_path: Path,
    job_id: str,
    *,
    result_json_path: str | None,
    audio_path: str | None,
    duration_sec: float | None,
    processing_time_sec: float | None,
    device_fallback: bool,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET state='done', stage_pct=100, result_json_path=?, audio_path=?, "
            "duration_sec=?, processing_time_sec=?, device_fallback=?, finished_at=? "
            "WHERE id=? AND state NOT IN ('done','error','canceled')",
            (
                result_json_path,
                audio_path,
                duration_sec,
                processing_time_sec,
                1 if device_fallback else 0,
                _now(),
                job_id,
            ),
        )


def fail_job(db_path: Path, job_id: str, error_code: str, error_message: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET state='error', error_code=?, error_message=?, finished_at=? "
            "WHERE id=? AND state NOT IN ('done','canceled')",
            (error_code, error_message, _now(), job_id),
        )


def cancel_job_if_queued(db_path: Path, job_id: str) -> bool:
    """Отменить ТОЛЬКО джобу в состоянии queued. Возвращает True, если отменена."""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET state='canceled', finished_at=? WHERE id=? AND state='queued'",
            (_now(), job_id),
        )
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# speaker_edits (overlay; result.json не мутируется)
# --------------------------------------------------------------------------- #
def get_speaker_edits(db_path: Path, job_id: str) -> dict[str, str]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT original_label, new_label FROM speaker_edits WHERE job_id=?",
            (job_id,),
        ).fetchall()
    return {r["original_label"]: r["new_label"] for r in rows}


def set_speaker_edit(db_path: Path, job_id: str, original_label: str, new_label: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO speaker_edits(job_id, original_label, new_label) VALUES(?,?,?) "
            "ON CONFLICT(job_id, original_label) DO UPDATE SET new_label=excluded.new_label",
            (job_id, original_label, new_label),
        )


# --------------------------------------------------------------------------- #
# Яндекс.Диск (M5): токен + claim-граница ingest
# --------------------------------------------------------------------------- #
def set_yandex_token(
    db_path: Path,
    token_enc: str,
    check_ok: bool,
    *,
    refresh_token_enc: str | None = None,
    expires_at: str | None = None,
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO yandex_auth(id, token_enc, refresh_token_enc, expires_at, check_ok, "
            "updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "token_enc=excluded.token_enc, refresh_token_enc=excluded.refresh_token_enc, "
            "expires_at=excluded.expires_at, check_ok=excluded.check_ok, "
            "updated_at=excluded.updated_at",
            (token_enc, refresh_token_enc, expires_at, 1 if check_ok else 0, _now()),
        )


def update_yandex_access(
    db_path: Path, token_enc: str, expires_at: str, refresh_token_enc: str | None = None
) -> None:
    """Обновить access-токен (и опц. refresh) после refresh_token-обмена — не трогая
    прочие поля, если refresh не менялся."""
    with get_conn(db_path) as conn:
        if refresh_token_enc is not None:
            conn.execute(
                "UPDATE yandex_auth SET token_enc=?, refresh_token_enc=?, expires_at=?, "
                "check_ok=1, updated_at=? WHERE id=1",
                (token_enc, refresh_token_enc, expires_at, _now()),
            )
        else:
            conn.execute(
                "UPDATE yandex_auth SET token_enc=?, expires_at=?, check_ok=1, updated_at=? "
                "WHERE id=1",
                (token_enc, expires_at, _now()),
            )


def get_yandex_auth(db_path: Path) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM yandex_auth WHERE id=1").fetchone()
    auth = _row_to_dict(row)
    if auth is not None:
        auth["check_ok"] = bool(auth["check_ok"])
    return auth


def claim_ingest(db_path: Path, key: str, resource_id: str | None) -> str | None:
    """Claim по `path:revision`. Возвращает surrogate_id для обработки, иначе None.

    Первый раз → новый surrogate. Если строка уже есть: терминальную
    (downloaded/done) НЕ переклеймиваем (дедуп — без второй джобы), а застрявшую
    (claimed/downloading/error после сбоя/краха) ПЕРЕклеймиваем → re-pull восстановим.
    """
    surrogate = new_id()
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ingest_seen(surrogate_id, key, resource_id, status, created_at) "
            "VALUES(?,?,?,?,?)",
            (surrogate, key, resource_id, "claimed", _now()),
        )
        if cur.rowcount > 0:
            return surrogate
        row = conn.execute(
            "SELECT surrogate_id, status FROM ingest_seen WHERE key=?", (key,)
        ).fetchone()
        if row is None:  # маловероятная гонка
            return None
        if row["status"] in ("downloaded", "done"):
            return None  # действительно уже обработано
        # не-терминальная (сбой/краш) → переклеймить тем же surrogate
        conn.execute("UPDATE ingest_seen SET status='claimed' WHERE key=?", (key,))
        return row["surrogate_id"]


def get_ingest(db_path: Path, surrogate_id: str) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ingest_seen WHERE surrogate_id=?", (surrogate_id,)
        ).fetchone()
    return _row_to_dict(row)


def update_ingest(
    db_path: Path,
    surrogate_id: str,
    *,
    status: str | None = None,
    recording_id: str | None = None,
    job_id: str | None = None,
) -> None:
    sets, vals = [], []
    if status is not None:
        sets.append("status=?")
        vals.append(status)
    if recording_id is not None:
        sets.append("recording_id=?")
        vals.append(recording_id)
    if job_id is not None:
        sets.append("job_id=?")
        vals.append(job_id)
    if not sets:
        return
    vals.append(surrogate_id)
    with get_conn(db_path) as conn:
        conn.execute(f"UPDATE ingest_seen SET {', '.join(sets)} WHERE surrogate_id=?", vals)


# --- Авто-watch: singleton-конфиг источника + окно стабильности ---
def get_ingest_source(db_path: Path) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM ingest_sources WHERE id=1").fetchone()
    src = _row_to_dict(row)
    if src is not None:
        src["enabled"] = bool(src["enabled"])
    return src


def upsert_ingest_source(
    db_path: Path, watch_dir: str, enabled: bool, poll_interval: int, default_params_json: str
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO ingest_sources(id, watch_dir, enabled, poll_interval, default_params, "
            "updated_at) VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "watch_dir=excluded.watch_dir, enabled=excluded.enabled, "
            "poll_interval=excluded.poll_interval, default_params=excluded.default_params, "
            "updated_at=excluded.updated_at",
            (watch_dir, 1 if enabled else 0, int(poll_interval), default_params_json, _now()),
        )


def record_stability(db_path: Path, path: str, signature: str) -> int:
    """Инкремент stable_count при неизменной signature, иначе сброс в 1. Возвращает счётчик.

    Так авто-watch клеймит запись только когда её сигнатура (size|revision|child|md5)
    не менялась ≥N поллингов — файлы дозалились, ревизия устоялась."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT signature, stable_count FROM ingest_stability WHERE path=?", (path,)
        ).fetchone()
        if row is not None and row["signature"] == signature:
            cnt = int(row["stable_count"]) + 1
            conn.execute(
                "UPDATE ingest_stability SET stable_count=?, updated_at=? WHERE path=?",
                (cnt, _now(), path),
            )
            return cnt
        conn.execute(
            "INSERT INTO ingest_stability(path, signature, stable_count, updated_at) "
            "VALUES(?,?,1,?) ON CONFLICT(path) DO UPDATE SET signature=excluded.signature, "
            "stable_count=1, updated_at=excluded.updated_at",
            (path, signature, _now()),
        )
        return 1
