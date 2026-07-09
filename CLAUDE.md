# BloodTranscripts — рулы проекта

Микробиблиотека транскрипции (GigaAM ASR + диаризация) `gigaam_transcriber` + чистый CLI `bloodtranscripts`
+ web-сервер (FastAPI + Huey + SQLite) + SPA (Vite/React). Истина по пайплайну — **библиотека**;
CLI и сервер — тонкие обёртки.

**Стек:** Python 3.11 · torch/torchaudio · GigaAM · pyannote.audio==4.0.5 · faster-whisper · onnxruntime ·
FastAPI + uvicorn · Huey (SqliteHuey, очереди io/gpu) · SQLite (WAL, сырой sqlite3) · Click · pytest ·
Vite + React 18 + TS + Tailwind v3 + TanStack Query v5 + react-router v6 + wavesurfer.js · nginx/Docker.

---

## 🔴 Незыблемые инварианты (не нарушать никогда)

- **I1 — кириллица байт-в-байт.** Кириллический вывод GigaAM неприкосновенен; glossary/fusion/L2 правят
  ТОЛЬКО латиницу/числа (`_is_replaceable`). Любая правка пайплайна проверяется побайтно.
- **pyannote.audio пинь РОВНО `==4.0.5`** — 4.0.6 ломает GigaAM VAD (`'generator' has no get_timeline`);
  pyannote-пайплайн держи 3.1 явно.
- **Lib-as-truth.** CLI и сервер зовут методы `GigaAMTranscriber` (`transcribe`/`transcribe_batch`/
  `transcribe_route_a`) — НИКАКОГО своего декод-цикла в обёртках.
- **api НЕ грузит модель.** В `server/` нет top-level импорта `gigaam`/`GigaAMTranscriber` — только
  lazy-import внутри huey-задач; это закреплено тестом `test_api_does_not_import_asr_model`.
- **GPU держит один воркер.** gpu-воркер строго `-w 1`; `-k process` (Linux-прод) или `-k thread`
  (macOS: Metal/MPS не живёт в fork — лаунчер подменяет сам); `assert_gpu_worker_config` падает ДО
  модели при `-w>1`/greenlet (GigaAMTranscriber не реентерабелен → копии в VRAM = OOM).
- **result.json не мутируется.** Правки спикеров — overlay в памяти при чтении (ключ `original_speaker`).

---

## Где что лежит

- **Стек-рулы** — `.claude/rules/` — грузятся **лениво** по `paths:`-маске, когда трогаешь подходящие файлы:
  - `python-conventions` → `**/*.py`
  - `fastapi-server` · `huey-queues` · `sqlite-repository` · `auth-security` → `gigaam_transcriber/server/**`
  - `asr-ml-pipeline` → `gigaam_transcriber/*.py`
  - `react-frontend` → `frontend/**/*.{ts,tsx}`
  - `testing` → `tests/**`
- **Всегда-загружаемые политики** — `.claude/rules/language-policy.md`, `.claude/rules/git-conventions.md`.
- **Best-practice-скилы** по стеку — `.claude/skills/` (автоподхват по описанию; см. их `SKILL.md`).
