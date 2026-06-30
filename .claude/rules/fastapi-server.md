---
paths:
  - "gigaam_transcriber/server/**"
---

# FastAPI-сервер

> Грузится при работе с `gigaam_transcriber/server/**`. Инварианты — в `CLAUDE.md`.

- В `server/` НЕ импортируй `gigaam`/`GigaAMTranscriber` на верхнем уровне — только lazy-import внутри
  функций/huey-задач (`gigaam_transcriber.*` — лёгкий, можно).
- ML/GPU-работу ставь в очередь через `app.state.enqueue`/`enqueue_io` (lazy `from .tasks import …`);
  из роута дёргай `getattr(request.app.state,'enqueue',None)` — не импортируй задачу-модель в роут.
- Порядок в `create_app()`: security-middleware → `include_router` всех `/api` → `mount_spa(app)`
  ПОСЛЕДНИМ; SPA catch-all `@app.get('/{full_path:path}', include_in_schema=False)` — после API-роутов.
- `/healthz` всегда 200 (liveness, без модели); `/readyz` — 503 `not_ready` пока нет тёплой модели, 200
  только по `settings.ready_flag_path`. Ни один эндпоинт не грузит ASR.
- В submit-роутах синхронная пре-валидация (recording 404, `media.ffmpeg_available()` 503, HF_TOKEN для
  single+diarization 400) и 4xx/503 ДО `create_job`/enqueue.
- Ошибки исполнения → `state='error'` + `error_code` + санитизированное сообщение (`classify_error`/
  `fail_job`); клиенту НЕ отдавай stacktrace/абсолютные пути — вычищай `metadata['source']`.
- CSP не ужимай: `blob:` в `media-src`/`worker-src`/`img-src` (wavesurfer), `script-src 'self'` без
  unsafe-inline; security-заголовки через `response.headers.setdefault` в `@app.middleware('http')`.
- Аудио/файлы — `FileResponse` (нативный Range/перемотка); скачиваемый текст — `Response` с
  `Content-Disposition: attachment`; не читай большие файлы в память руками.
- Каждый `/api`-роут защищай `user: str = Depends(require_session)` (401 без свежей cookie); публичны
  только `/healthz`, `/readyz` и SPA-статика.
- Тела запросов — pydantic `BaseModel`; перечислимые query/path-параметры — явный allowlist →
  `HTTPException(400)` (как `format in ('txt','json','srt','vtt')`).
- Аплоады стримь кусками с проверкой `max_file_size`/`max_recording_total` ДО полного чтения; формат — по
  magic-bytes (`media.sniff_media`, не по суффиксу); `.zip` → 415; чисти частичные файлы при ошибке.
- Не утекай серверные пути: opaque-индексы дорожек (`TrackIn.id`), `FastAPI(docs_url=None, redoc_url=None,
  openapi_url=None)`, CSRF Origin/Referer-check + `X-Forwarded-Proto != https → 400` в security-middleware.
