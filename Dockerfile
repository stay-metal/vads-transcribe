# DialogScribe — образ api / io-worker (CPU-база).
# gpu-worker'у нужна CUDA-база (torch+cuda, GigaAM на GPU) — отдельный Dockerfile.gpu
# в деплое; здесь общий образ для api и io-worker (модель не держат).

# --- этап 1: сборка SPA (M4) ---
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
# Vite билдит в ../gigaam_transcriber/server/static — переопределяем на локальный dist.
RUN npx vite build --outDir /fe/dist --emptyOutDir

# --- этап 2: python-приложение ---
FROM python:3.11-slim

# ffmpeg — для декода аудио/видео; git — для editable-зависимости GigaAM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
# Собранная SPA → раздаётся FastAPI (catch-all после /api).
COPY --from=frontend /fe/dist /app/gigaam_transcriber/server/static

# Пакет + серверный экстра + диаризация. GigaAM (vendored submodule) — editable.
RUN pip install --no-cache-dir -e ".[server,diarization]" \
    && if [ -d GigaAM ]; then pip install --no-cache-dir -e ./GigaAM; fi

ENV DIALOGSCRIBE_DATA_DIR=/data
EXPOSE 8000

# Дефолт — api (uvicorn factory). Воркеры переопределяют command в compose.
CMD ["uvicorn", "gigaam_transcriber.server.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
