---
paths:
  - "gigaam_transcriber/server/**"
---

# Auth и безопасность

> Грузится при работе с `gigaam_transcriber/server/**` (`security.py`, `auth.py`, `config.py`, `crypto.py`,
> `media.py`, `uploads.py`). Инварианты — в `CLAUDE.md`.

- Логин: пароль — `bcrypt.checkpw`, username — `hmac.compare_digest` на `.encode("utf-8")`; считай
  `user_ok` и `pass_ok` ВСЕГДА без short-circuit (анти-timing).
- Сессия — itsdangerous `URLSafeTimedSerializer` (URL-safe ради кириллицы в username); на КАЖДОМ запросе
  сверяй `max_age` и epoch с БД, user сравнивай на bytes.
- session-epoch бамп гасит все cookie; не убирай `reconcile_password_epoch` — он авто-бампит epoch при
  смене `DIALOGSCRIBE_PASSWORD_HASH`.
- Cookie сессии — только `HttpOnly` + `secure=cookie_secure` + `samesite="strict"` + `path="/"`; ничего
  не понижай.
- РАЗДЕЛЬНЫЕ `session_key` (подпись cookie) и `fernet_key` (шифрование секретов); `validate_for_serve`
  обязан падать при пустом/совпадающем.
- Секреты at-rest (Яндекс-токен) — только через `crypto.encrypt` (Fernet); валидируй `client.check()` ДО
  `set_yandex_token`; НИКОГДА не логируй тело токена.
- Brute-force: `LoginThrottle` global+per-IP+экспон.backoff с eviction; `login_global_max_failures` держи
  с запасом над per-IP (иначе один IP залочит всех — DoS).
- Реальный client-IP — из `X-Real-IP` или ПРАВОГО hop `X-Forwarded-For` и только при `require_https`; в
  nginx ограничь `real_ip` через `set_real_ip_from` доверенными подсетями.
- На POST/PUT/PATCH/DELETE проверяй Origin (или производный от Referer) против host+схемы, чужой → 403
  (defense-in-depth ПОВЕРХ SameSite=Strict).
- Загрузки — по magic-bytes (`media.sniff_media`); `.zip` отклоняй; имя на диске из `new_id()`, суффикс из
  allowlist (`safe_suffix`). Лимиты двухслойно: nginx `client_max_body_size` + серверная per-chunk
  пере-проверка + ранний `Content-Length`; чисти частичные при отказе.
- Пути: NFC-нормализуй имена; раздачу ограничь `resolve()` + root in parents + запрет dotfile-сегментов;
  путь Я.Диска держи под `watch_dir` через `posixpath.normpath` (анти `..`).
- Security-заголовки: статичные (HSTS/X-Frame-Options/nosniff/Referrer-Policy) — в nginx с `always`
  (закрывают и 502/413 самого nginx) + дублируй middleware через `setdefault`; CSP — ТОЛЬКО в
  app-middleware (nginx-дубль при дрейфе ужимал бы политику до пересечения и ломал wavesurfer);
  `require_https` отвергает `X-Forwarded-Proto != https`.
