---
paths:
  - "gigaam_transcriber/server/**"
---

# SQLite + repository

> Грузится при работе с `gigaam_transcriber/server/**` (`db.py`, `repository.py`). Инварианты — в `CLAUDE.md`.

- Соединения — только через `db.connect()`/`get_conn` (WAL, `foreign_keys=ON`, `busy_timeout=5000`,
  `isolation_level=None`); не зови `sqlite3.connect` напрямую.
- Весь SQL — только параметризованный (`?`); единственный динамический SET (`update_ingest`) собирай из
  захардкоженного whitelist колонок, значения всё равно через params.
- Захват queued-джобы — атомарный CAS: `UPDATE jobs SET state='asr' WHERE id=? AND state='queued'`,
  решай по `cur.rowcount>0`; проигравший воркер просто `return`.
- Во всех UPDATE прогресса/финиша — `WHERE state NOT IN ('done','error','canceled')`.
- При старте app зови `reconcile_orphaned_jobs` (in-flight → error) и `reconcile_password_epoch`.
- speaker-edits — overlay: НИКОГДА не мутируй `result.json`; накладывай правки в `_load_result_with_overlay`
  при чтении/скачивании. Ключ правки — сырой `original_speaker`; UPSERT
  `ON CONFLICT(job_id, original_label) DO UPDATE SET new_label=excluded.new_label`.
- После overlay пересчитывай `metadata.speakers_count` (distinct speaker) и удаляй `metadata.source`
  (серверные пути) перед отдачей клиенту.
- id/имена на диске — из `new_id()=uuid4().hex`, не из пользовательских строк; в URL — opaque
  `surrogate_id`, не сырой `key` (с `/` и кириллицей).
- JSON-поля (`tracks_json`, `params_json`) пиши `json.dumps(..., ensure_ascii=False)`; BOOL храни
  INTEGER 0/1 и приводи к bool.
