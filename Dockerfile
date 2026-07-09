# DialogScribe — общий образ api / io-worker / gpu-worker.
# База CPU-friendly (python-slim); на linux/amd64 pip-колёса torch включают CUDA,
# поэтому gpu-worker работает из этого же образа при наличии NVIDIA Container Toolkit.
# После запуска проверьте metadata.device джобы: device_fallback='cpu' у всех джоб
# означает, что GPU в контейнер не пробросился.

# --- этап 1: сборка SPA ---
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

# Пакет + все рантайм-экстры: без second-opinion/onnx фичи, видимые в UI
# (L2 «второе мнение», backend=onnx), падали бы в контейнере на ImportError.
# GigaAM (vendored clone) — editable.
RUN pip install --no-cache-dir -e ".[server,diarization,second-opinion,onnx]" \
    && if [ -d GigaAM ]; then pip install --no-cache-dir -e ./GigaAM; fi

ENV DIALOGSCRIBE_DATA_DIR=/data
EXPOSE 8000

# Дефолт — api (uvicorn factory). Воркеры переопределяют command в compose.
CMD ["uvicorn", "gigaam_transcriber.server.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
