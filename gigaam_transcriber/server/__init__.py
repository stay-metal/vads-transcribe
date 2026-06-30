"""DialogScribe web-сервер (milestone M2+).

Тонкая презентационная оболочка над библиотекой `gigaam_transcriber`:
FastAPI api (auth/REST/SPA, НЕ держит модель) + Huey 2-очереди (io/gpu) +
secure-by-default аутентификация по одним общим кредам.

Критический инвариант: процесс `api` НЕ импортирует и НЕ грузит ASR-модель —
её держит единственный gpu-worker (`huey -q gpu -k process -w 1`). Поэтому здесь
на верхнем уровне НЕ импортируется ни `gigaam`, ни `GigaAMTranscriber`.
"""
