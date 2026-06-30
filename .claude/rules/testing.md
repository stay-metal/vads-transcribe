---
paths:
  - "tests/**/*.py"
  - "**/conftest.py"
  - "**/test_*.py"
---

# Тестирование

> Грузится при работе с `tests/**`. Инварианты — в `CLAUDE.md`. Фронт-гейт см. ниже.

- Юнит-тесты не грузят ML-модель и не ходят в сеть: monkeypatch `_transcribe_audio`/`GigaAMTranscriber`→
  Fake, `FakeYandex`; модельные тесты — `@pytest.mark.requires_model` (skip по умолчанию).
- Зависимости инъектируй через фабрики/`app.state` (`create_app(settings, enqueue=...)`,
  `app.state.yandex_factory`); для e2e — sync-enqueue (`process_job` вызывается сразу).
- Пиши только в `tmp_path`/`temp_dir`; фейк-медиа — `path.write_bytes(b'\x00')`; реальный ffmpeg глуши
  monkeypatch `media.ffmpeg_available` + fake `downmix_tracks`.
- После каждого шага гоняй весь набор `.venv/bin/python -m pytest` и держи зелёным (сейчас 333); новый код
  — новые тесты, счётчик не уменьшается.
- I1/кириллицу проверяй побайтно и на немутацию (`read_bytes()`/`read_text('utf-8')`, латиница-в-кириллице
  по word-boundary, вход `detect_quality_flags`/`fuse` не меняется).
- Route A и CLI тестируй без HF_TOKEN (`monkeypatch.delenv('HF_TOKEN', raising=False)`): route-a токен не
  требует, diarized-путь явно ругается на отсутствие.
- Покрывай error-path и лимиты: битая дорожка → `failed_tracks`, все упали → пустой результат без
  исключения, сбой download → claim освобождается для re-pull.
- Сервер — через `TestClient` с cookie-логином; проверяй 401/403, CSP, cookie-флаги, magic-bytes аплоада.
  Варианты/границы — через `@pytest.mark.parametrize`.
- Перед заявлением о готовности UI прогоняй фронт-гейт `cd frontend && npm run build` (= `tsc -b && vite
  build`); SPA-тесты гейть `skipif(not (static_dir()/'index.html').exists())`.
- Верифицируй фактический вывод pytest/сборки своими глазами ДО заявления о готовности.
- Крипто/at-rest проверяй явно: `crypto.encrypt/decrypt` роундтрип, токен НЕ в открытом виде в БД
  (`VALID not in auth['token_enc']`), неверный ключ → `None`.
