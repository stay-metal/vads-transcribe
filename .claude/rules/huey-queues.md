---
paths:
  - "gigaam_transcriber/server/**"
---

# Huey-очереди и воркеры

> Грузится при работе с `gigaam_transcriber/server/**` (`queues.py`, `workers.py`, `tasks.py`,
> `run_gpu_worker.py`, `job_runner.py`). Инварианты — в `CLAUDE.md`.

- Очереди — только через `make_gpu_huey`/`make_io_huey` (SqliteHuey «gpu»/«io») в `huey.sqlite`,
  ОТДЕЛЬНОМ от `app.sqlite` (иначе запись задач лочит прикладные записи).
- gpu-воркер стартуй только `python -m …server.run_gpu_worker -k process -w 1`, не голым `huey_consumer`
  (иначе boot-guard не получит `-k`/`-w`).
- Не ослабляй `assert_gpu_worker_config`: `-w>1`/`-k≠process` → `RuntimeError` ДО загрузки модели.
- Тёплый singleton грей в `@gpu_huey.on_startup()` (`WARM_TRANSCRIBER`) и переиспользуй в `run_job`; не
  создавай транскрайбер per-call/через context-manager на запрос.
- В начале `process_job` делай `claim_job` (CAS `UPDATE … WHERE state='queued'`); проиграл гонке → молча
  `return`, без двойного запуска ASR.
- Апдейты джобы (`update_job_progress`/`finish_job_ok`/`fail_job`) — под guard
  `WHERE state NOT IN ('done','error','canceled')` (терминальное не перетирать).
- На старте `create_app` зови `reconcile_orphaned_jobs`: in-flight стадии → `error 'worker_restart'`
  (SqliteHuey не возобновляет прерванные рестартом задачи — иначе poll висит).
- Периодику (retention) вешай только на `@io_huey.periodic_task(crontab(...))`; ничего GPU/модельного на
  `gpu_huey` периодически.
- cancel честен лишь для `queued` (`cancel_job_if_queued`, CAS); running доводи до конца (HTTP 409) +
  best-effort `revoke(huey_task_id)`.
- Скачивание Я.Диска — io-задача (`pull_recording`/`ingest_pull`); GPU занимай лишь финальным
  `enqueue run_job`, чтобы сетевой I/O не блокировал единственный GPU-слот.
- Дедуп ingest — `claim_ingest` (INSERT OR IGNORE по `key=path:revision`): терминальные не переклеймивай,
  застрявшие — да; наружу — только opaque `surrogate_id`.
