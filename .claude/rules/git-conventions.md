# Git-конвенции

Всегда-загружаемое правило.

## Коммиты
- Conventional-commits со скоупом: `feat(scope): …` / `fix: …` / `refactor: …` / `merge: …`. Описание —
  по-русски (как в истории: `feat(onnx-encoder): split-device …`).
- Скоуп — компонент (`ui`, `onnx-encoder`, `route-a`, `manifest`, `server`, `preclean`). Инкременты помечай
  `— инкр.N`.
- Если правка пайплайна — добавляй строку-подтверждение `Инвариант I1 … сохранён.` перед футером.
- Футер коммита заканчивай строкой `Claude-Session: https://claude.ai/code/session_…`.

## Ветки
- Дефолтная — `main`. Работа — в `feat/<name>` (как `feat/web-ui-v1`, `feat/port-quality-layer`).
- На дефолтной ветке сначала создай ветку, потом коммить.

## Push / merge / PR
- НЕ пушить и НЕ коммитить без явной просьбы пользователя. Merge `feat/*`→`main`, push, PR — по решению
  пользователя (история НЕ пушится автоматически, хотя `origin/main` существует).
- GitHub-операции — через `gh` CLI; тело PR заканчивай ссылкой `https://claude.ai/code/session_…`.

## Гигиена
- Не коммить секреты (`HF_TOKEN`, session/fernet-ключи, Яндекс-токен) и `.env`.
- Артефакты сборки — под `.gitignore` (`frontend/node_modules/`, `frontend/dist/`,
  `gigaam_transcriber/server/static/`); не коммить их вручную.
- Перед коммитом — `ruff` + `black` + `mypy` + зелёный `pytest` (+ `npm run build` при правках фронта).
