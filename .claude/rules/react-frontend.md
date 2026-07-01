---
paths:
  - "frontend/**/*.{ts,tsx}"
---

# Фронтенд React/Vite/TS/Tailwind/TanStack/wavesurfer

> Грузится при работе с `frontend/**/*.{ts,tsx}`. Инварианты — в `CLAUDE.md`.

- Все HTTP — только через `api` и обёртку `req<T>()` в `src/api/client.ts` (`credentials:"same-origin"`),
  ошибки кидай как `ApiError(status, detail)`; не вызывай `fetch` в компонентах.
- Глобальный signout на 401: `req` зовёт `onUnauthorized?.()` на любом `/api` кроме `/auth/me`; обработчик
  регистрируй только через `setUnauthorizedHandler` в `AuthProvider`.
- `queryKey` держи стабильным и сериализуемым (`["job",jobId]`/`["result",jobId]`/`["jobs"]`/
  `["tracks",recId]`); зависимые запросы гейти `enabled:!!id`, не условным вызовом хука.
- Поллинг статуса: `refetchInterval:(q)=> q.state.data && ACTIVE.includes(q.state.data.state) ? 1500 :
  false`; останавливай на done/error/canceled.
- Результат тяни отдельным `useQuery(["result",jobId])` с `enabled:!!jobId && done`; не запрашивай
  `/result` пока статус ≠ `"done"`.
- wavesurfer создавай ОДИН раз в `useEffect` deps `[jobId]` + `ws.destroy()` в cleanup; правка имён НЕ
  должна пересоздавать плеер. Сегменты для регионов/`timeupdate` читай через `segsRef` (ref без deps).
- Переименование спикера ключуй СЫРЫМ ярлыком `seg.original_speaker` (fallback `speaker`), шли в
  `api.putSpeakers` → `refetch()`.
- Не трогай CSP `blob:` в `server/app.py` (wavesurfer-воркерам/аудио нужны `worker-src`/`media-src 'self'
  blob:`).
- SPA билдь `npm run build` → `../gigaam_transcriber/server/static`; всё same-origin без CORS, cookie
  HttpOnly+SameSite=Strict ходит сама — не добавляй абсолютные URL/CORS.
- Не показывай вечный спиннер: явные ветки `isError→ErrorCard`, `error/canceled`, `!done→StageBar`,
  result-not-ready (образец — `TranscriptViewer`). Лови `ApiError` и давай статус-специфичные сообщения
  (400→подсказка про HF_TOKEN); импорты через alias `@/`, классы — `cn()`.
