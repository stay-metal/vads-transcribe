"""Слой app.sqlite (WAL). В M2 — только `meta` (session-epoch); jobs/recordings — M3.

Очередь Huey живёт в отдельной БД (huey.sqlite), чтобы запись задач не конфликтовала
с прикладными записями под конкуренцией (спека §13, риск SQLite-lock).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# DDL источников авто-watch — отдельной константой: используется и в _SCHEMA,
# и в миграции старого singleton-формата (id=1 — только Яндекс).
_INGEST_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS ingest_sources (
    source_type    TEXT PRIMARY KEY CHECK (source_type IN ('yandex', 'local')),
    watch_dir      TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 0,
    poll_interval  INTEGER NOT NULL DEFAULT 300,        -- сек между поллингами
    default_params TEXT NOT NULL DEFAULT '{}',          -- json-параметры авто-джоб
    scan_profile   TEXT NOT NULL DEFAULT '{}',          -- json-профиль раскладки (local)
    last_scan_at   TEXT,                                -- ISO последнего скана (статус в UI)
    updated_at     TEXT NOT NULL
);
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recordings (
    id           TEXT PRIMARY KEY,
    origin       TEXT NOT NULL,                 -- upload | yandex
    title        TEXT,
    kind         TEXT NOT NULL,                 -- route_a | single
    track_count  INTEGER NOT NULL DEFAULT 0,
    tracks_json  TEXT NOT NULL DEFAULT '[]',    -- [{name,path,size,...}]
    ingest_key   TEXT UNIQUE,                   -- (Я.Диск, M5) path:revision
    latest_job_id TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    recording_id  TEXT,
    source        TEXT NOT NULL,                -- upload | yandex
    mode          TEXT NOT NULL,                -- route_a | single
    state         TEXT NOT NULL,                -- queued|preclean|vad|diarization|asr|quality|formatting|done|error|canceled
    stage_pct     INTEGER NOT NULL DEFAULT 0,
    error_code    TEXT,
    error_message TEXT,                         -- санитизированное
    params_json   TEXT NOT NULL DEFAULT '{}',   -- заморожено на сабмите
    work_dir      TEXT,
    output_dir    TEXT,
    manifest_path TEXT,
    result_json_path TEXT,
    audio_path    TEXT,                         -- downmix для плеера
    duration_sec  REAL,
    processing_time_sec REAL,
    device_fallback INTEGER NOT NULL DEFAULT 0,
    huey_task_id  TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS speaker_edits (
    job_id         TEXT NOT NULL,
    original_label TEXT NOT NULL,
    new_label      TEXT NOT NULL,
    UNIQUE(job_id, original_label)
);

CREATE TABLE IF NOT EXISTS text_edits (
    job_id     TEXT NOT NULL,
    seg_index  INTEGER NOT NULL,
    new_text   TEXT NOT NULL,
    UNIQUE(job_id, seg_index)
);

-- Яндекс.Диск (M5): singleton-токен (Fernet) + claim-граница ingest.
CREATE TABLE IF NOT EXISTS yandex_auth (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    token_enc         TEXT NOT NULL,
    refresh_token_enc TEXT,               -- OAuth refresh-токен (Fernet), M6 v1.x
    expires_at        TEXT,               -- ISO-время истечения access_token
    check_ok          INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_seen (
    surrogate_id TEXT PRIMARY KEY,        -- opaque (в URL — не сырой ключ с / и кириллицей)
    key          TEXT UNIQUE NOT NULL,    -- path:revision — exactly-once гейт
    resource_id  TEXT,
    status       TEXT NOT NULL,           -- claimed|downloading|downloaded|done|error
    recording_id TEXT,
    job_id       TEXT,
    created_at   TEXT NOT NULL
);

-- Пользовательские пресеты раскладки источника (встроенные zoom/plain — в коде).
CREATE TABLE IF NOT EXISTS scan_presets (
    id         TEXT PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    body       TEXT NOT NULL,               -- json-профиль (та же схема, что scan_profile)
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_stability (
    path         TEXT PRIMARY KEY,        -- элемент верхнего уровня watch_dir
    signature    TEXT NOT NULL,           -- (size|revision|child_count|md5_ready)
    stable_count INTEGER NOT NULL DEFAULT 0,  -- сколько поллингов подряд неизменно
    updated_at   TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Открыть соединение с WAL-режимом и внешними ключами."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: Path) -> None:
    """Создать схему, если её нет, и накатить лёгкие миграции (ADD COLUMN)."""
    conn = connect(db_path)
    try:
        # Миграция ingest_sources — ДО _SCHEMA: старая таблица (PK id=1) должна
        # быть пересобрана раньше, чем CREATE IF NOT EXISTS её «узаконит».
        _migrate_ingest_sources(conn)
        conn.executescript(_SCHEMA)
        conn.executescript(_INGEST_SOURCES_DDL)
        _migrate(conn)
    finally:
        conn.close()


def _migrate_ingest_sources(conn: sqlite3.Connection) -> None:
    """Старый singleton-формат (id=1, только Яндекс) → PK по source_type.

    Пересборка таблицы (SQLite не меняет PK через ALTER): существующая
    строка id=1 переезжает как source_type='yandex'. Явная транзакция —
    соединение в autocommit (isolation_level=None), и крах между RENAME и
    INSERT молча терял бы Яндекс-конфиг."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ingest_sources)")}
    if not cols or "id" not in cols:
        # Таблицы нет (создаст _INGEST_SOURCES_DDL) или уже формат source_type —
        # докатываем только новые колонки.
        if cols and "scan_profile" not in cols:
            conn.execute(
                "ALTER TABLE ingest_sources ADD COLUMN scan_profile TEXT NOT NULL DEFAULT '{}'"
            )
        return  # старой пересборки не требуется
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE ingest_sources RENAME TO ingest_sources_old")
        conn.execute(_INGEST_SOURCES_DDL)
        conn.execute(
            "INSERT INTO ingest_sources(source_type, watch_dir, enabled, poll_interval, "
            "default_params, updated_at) SELECT 'yandex', watch_dir, enabled, poll_interval, "
            "default_params, updated_at FROM ingest_sources_old"
        )
        conn.execute("DROP TABLE ingest_sources_old")
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентные ADD COLUMN для БД, созданных до появления новых колонок."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(yandex_auth)")}
    if "refresh_token_enc" not in cols:
        conn.execute("ALTER TABLE yandex_auth ADD COLUMN refresh_token_enc TEXT")
    if "expires_at" not in cols:
        conn.execute("ALTER TABLE yandex_auth ADD COLUMN expires_at TEXT")


@contextmanager
def get_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# session-epoch: целое в БД, подмешивается в подпись cookie. Бамп → мгновенная
# инвалидация всех выданных cookie без потери Fernet-секретов (спека §8).
# --------------------------------------------------------------------------- #
def get_session_epoch(db_path: Path) -> int:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='session_epoch'").fetchone()
        if row is None:
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('session_epoch', '1')")
            return 1
        return int(row["value"])


def bump_session_epoch(db_path: Path) -> int:
    """Увеличить epoch (напр. при смене пароля) и вернуть новое значение."""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='session_epoch'").fetchone()
        current = int(row["value"]) if row else 1
        new = current + 1
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('session_epoch', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(new),),
        )
        return new


def reconcile_password_epoch(db_path: Path, password_hash: str) -> bool:
    """Авто-бамп epoch при смене пароля (спека §8).

    Хранит отпечаток текущего `password_hash` в meta; при его изменении между
    запусками бампит epoch → все ранее выданные cookie мгновенно инвалидируются.
    Возвращает True, если бамп произошёл.
    """
    import hashlib

    fingerprint = hashlib.sha256(password_hash.encode("utf-8")).hexdigest()
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='pw_fingerprint'").fetchone()
        if row is None:
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('pw_fingerprint', ?)",
                (fingerprint,),
            )
            return False
        if row["value"] == fingerprint:
            return False
        conn.execute("UPDATE meta SET value=? WHERE key='pw_fingerprint'", (fingerprint,))
    bump_session_epoch(db_path)
    return True
