---
paths:
  - "**/*.py"
---

# Python-конвенции и архитектура

> Часть рулов BloodTranscripts. Грузится при работе с любым `.py`. Инварианты — в `CLAUDE.md`.

- Lib-as-truth: CLI/server — тонкие обёртки; зови методы `GigaAMTranscriber` без своего декод-цикла.
- stdout — только машинный результат (`result.to_*`); весь декор/summary/прогресс/warnings — в stderr
  (`_eecho`/`_esecho`), чтобы `-f json > out.json` оставался валиден.
- Общие opt-in флаги transcribe и batch — в едином декораторе `quality_options` (паритет); новый флаг
  добавляй туда.
- Команды оборачивай `@guarded`: `TranscriberError`→1, Ctrl-C→130, `ClickException` пробрасывай
  (`UsageError`→2). Ошибки ввода — `click.UsageError`; traceback только при `-v`/`BLOODTRANSCRIPTS_TRACEBACK`.
- Точки входа — `[project.scripts]`: только `bloodtranscripts` и `bloodtranscripts-gpu-worker`
  (легаси-алиасы `gigaam-*` удалены; новые команды — сабкоманды `bloodtranscripts`).
- `os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK","1")` ставь вверху модуля ДО `import torch`;
  тяжёлые импорты (rich/uvicorn/voiceprint) — лениво внутри команд.
- Секреты только из env/`.env` (`os.getenv`/`Settings.from_env()`, `.env` с `override=False`); не хардкодь
  и не логируй `HF_TOKEN`/session/fernet-ключи.
- Логируй через `logging.getLogger(__name__)` (сервер — `"bloodtranscripts.jobs"`), lazy `%s`-формат; пиши
  `job_id`, не пути/имена файлов/PII. Перед записью `result.json` — `metadata.pop("source", None)`.
- black/ruff `line-length=100`, target py310+; ruff `select=E,F,W,I,N,B,C4,UP`, `ignore=E501,B008`;
  ruff+black+mypy перед коммитом. Публичные сигнатуры типизируй; комментарии/docstring — по-русски в стиле
  окружающего кода.
