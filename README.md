# DialogScribe

Обёртка над [GigaAM](https://github.com/salute-developers/GigaAM) (ASR от Salute) для транскрипции аудио/видео любой длительности с опциональной диаризацией спикеров через [pyannote](https://github.com/pyannote/pyannote-audio).

## CLI `dialogscribe` (рекомендуется)

Единый CLI — тонкая обёртка над библиотекой `gigaam_transcriber` (один источник истины,
без дублирующего декод-цикла):

```bash
dialogscribe transcribe <файл> [опции]          # один файл
dialogscribe batch <файлы...> -o OUTDIR [опции]  # пакет
dialogscribe route-a <папка> [-o OUT] [-f txt]   # подорожечно (имена дорожек, без HF_TOKEN)
dialogscribe gallery build <имя> --track Метка=ПУТЬ ...   # голосовая галерея (--voiceprint)
dialogscribe gallery list | dialogscribe gallery rm <имя>
dialogscribe serve [--host --port]               # web-сервер (milestone M2, в разработке)
```

Команды `transcribe` и `batch` выставляют один и тот же набор opt-in флагов слоя
качества и бэкендов: `--diarize`, `--glossary/--no-glossary`, `--second-opinion`,
`--voiceprint --gallery P`, `--preclean`, `--backend torch|onnx`, `--onnx-int8`,
`--onnx-encoder`, `--word-timestamps`, `--emit-l0`, а также тюнинг диаризации
`--diar-device/--diar-backend/--embedding-batch-size/--segmentation-batch-size`.
Флаги `--resume --manifest P` специфичны для одиночного файла и есть только в
`transcribe`. `dialogscribe <команда> --help` — полный список.

Поток вывода: stdout несёт **только** машинный результат (когда `-o` не задан),
поэтому `dialogscribe transcribe a.m4a -f json > out.json` и
`dialogscribe route-a ./rec -f json > out.json` дают валидный файл — баннеры,
summary, предупреждения и прогресс идут в stderr. У `transcribe`/`route-a` есть
`-q/--quiet` для подавления summary.

> Легаси-точки `gigaam-ui` / `gigaam-transcribe` / `gigaam-batch` оставлены алиасами
> на один релиз (живой rich-UI `gigaam-ui` с per-сегментным ASR-прогрессом пока богаче
> по прогрессу single-file; в `dialogscribe` истинный single-file % придёт в v1.x).

---

## Web-сервер и интерфейс (M2–M4)

Сервер: FastAPI api (auth по общим кредам + REST + раздача SPA) · Huey 2 очереди
(`gpu` — транскрипция, `io` — скачивание) · единственный gpu-worker держит тёплую модель.
SPA: Vite+React+Tailwind+wavesurfer — загрузка записей (подорожечно/микс), подтверждение
участников, очередь джоб со стадийным прогрессом, просмотрщик транскрипта с аудио-синхронизацией,
правкой имён спикеров, бейджами качества и скачиванием форматов.

```bash
# Локальная разработка
pip install -e ".[server,diarization]"
python -c "from gigaam_transcriber.server.security import hash_password; print(hash_password('ВАША-ФРАЗА'))"
export DIALOGSCRIBE_PASSWORD_HASH=<хэш> DIALOGSCRIBE_SESSION_KEY=<48+ симв.> DIALOGSCRIBE_FERNET_KEY=<...>
export DIALOGSCRIBE_COOKIE_SECURE=0 DIALOGSCRIBE_REQUIRE_HTTPS=0   # только для dev по HTTP

# api (uvicorn)
dialogscribe serve --port 8000
# gpu-worker (отдельный процесс, boot-guard -k process -w 1)
python -m gigaam_transcriber.server.run_gpu_worker -k process -w 1
# frontend (dev-сервер с прокси на :8000) ИЛИ сборка в static
cd frontend && npm install && npm run dev      # dev
cd frontend && npm run build                   # прод-сборка → раздаётся FastAPI

# Прод: один docker compose (nginx TLS + api + gpu-worker + io-worker)
cd deploy && cp .env.example .env && docker compose up -d --build
```

---

## ⚠️ Важно: версии и совместимость

Этот форк настроен под **GigaAM `main`** (v3-модели) и **pyannote.audio 4.0**. Ключевые моменты, которые легко сломать:

- **GigaAM:** ставим ветку `main` (в ней есть `v3_e2e_ctc` / `v3_e2e_rnnt`). Обёртка пропатчена под её API (`TranscriptionResult` / `LongformTranscriptionResult`).
- **pyannote.audio == 4.0.\*** — обязательно. На 3.x диаризация падает (`Model.from_pretrained` иначе трактует локальный путь).
- **Python ≥ 3.10** (используем 3.11). Системный python 3.9 не подойдёт.
- **FFmpeg** в `PATH`.
- **HF-токен нужен даже без диаризации:** длинные файлы (>25 с) идут через `transcribe_longform`, а он использует pyannote-VAD (`segmentation-3.0`).

---

## Установка (macOS, Apple Silicon)

```bash
# 1. Python 3.11 (системный 3.9 не годится)
brew install python@3.11

# 2. venv
cd DialogScribe
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip

# 3. GigaAM (ветка main, только core — без тяжёлых [longform]-пинов)
git clone https://github.com/salute-developers/GigaAM.git
pip install -e ./GigaAM

# 4. pyannote 4.0 (тянет совместимые torch/torchaudio/torchcodec) + пакет-обёртка
pip install 'pyannote.audio==4.0.*'
pip install -e .
```

> FFmpeg: `brew install ffmpeg` (если ещё нет).

### HuggingFace токен

Токен читается из `.env` (автозагрузка) или переменной окружения `HF_TOKEN`.

```bash
cp .env.example .env
# впишите в .env:  HF_TOKEN=hf_ваш_токен   (read-scope: https://huggingface.co/settings/tokens)
```

Один раз примите условия gated-моделей **тем же аккаунтом**, что у токена:
- https://huggingface.co/pyannote/segmentation-3.0 — нужна всегда (VAD для длинных файлов)
- https://huggingface.co/pyannote/speaker-diarization-3.1 — для диаризации
- https://huggingface.co/pyannote/speaker-diarization-community-1 — для диаризации (pyannote 4.0 тянет её под капотом)

Проверить доступ:
```bash
python -c "import os,gigaam_transcriber; from huggingface_hub import auth_check; \
[auth_check(r) for r in ['pyannote/segmentation-3.0','pyannote/speaker-diarization-3.1','pyannote/speaker-diarization-community-1']]; \
print('доступ ок')"
```

Модели самого GigaAM качаются с CDN Сбера и токена **не требуют**.

---

## Использование: `gigaam-ui`

```bash
gigaam-ui <файл> [опции]
```

| Опция | Описание |
|---|---|
| `-m, --model` | Модель GigaAM (по умолч. `v3_e2e_ctc`) |
| `-d, --diarize` | `none` / `pyannote` (метки спикеров) |
| `--device` | Устройство ASR/VAD: `auto` / `cpu` / `cuda` / `mps` |
| `--diar-device` | Устройство диаризации (по умолч. = `--device`) |
| `--speakers` / `--min-speakers` / `--max-speakers` | Число спикеров (иначе авто) |
| `-f, --format` | `txt` / `json` / `srt` / `vtt` |
| `-o, --output` | Путь к выходному файлу |
| `--embedding-batch-size` / `--segmentation-batch-size` | Батчи диаризации (по умолч. 32) |
| `--batch` | Батч ASR (по умолч. 16) |
| `--gap` | Макс. пауза для склейки реплик одного спикера, сек (0.5) |
| `--backend` | Бэкенд ASR: `torch` (cpu/mps/cuda) или `onnx` (cpu/cuda, **не** mps) |
| `--onnx-int8` | ONNX с int8-квантизацией (CPU-рычаг для ASR) |
| `--diar-backend` | Эмбеддер диаризации: `torch` или `onnx` (CPU; сегментация всегда torch) |

`gigaam-ui --help` — полный список.

---

## Команды под наши задачи

Тестовый файл: `transcript/2026-01-20 19.48.51 Конференция Zoom Ponimaiu AI/audio1715906680.m4a` (1 ч 33 мин).

```bash
source .venv/bin/activate
BASE="transcript/2026-01-20 19.48.51 Конференция Zoom Ponimaiu AI"
```

**1. Рекомендуется — RNNT, всё на MPS, с диаризацией** *(лучшее качество, ~8–10 мин на 1.5 ч)*
```bash
gigaam-ui "$BASE/audio1715906680.m4a" \
  -m v3_e2e_rnnt -d pyannote --device mps \
  -o "$BASE/transcripts/gigaam_diar.txt"
```
> Замерено: RNNT на MPS даёт **байт-в-байт тот же текст**, что и на CPU, но ASR ~7× быстрее — держать ASR на CPU смысла нет.

**2. Чуть быстрее — CTC, всё на MPS, с диаризацией** *(~7–9 мин, качество текста немного ниже)*
```bash
gigaam-ui "$BASE/audio1715906680.m4a" \
  -m v3_e2e_ctc -d pyannote --device mps \
  -o "$BASE/transcripts/gigaam_ctc_diar.txt"
```

**3. Только текст, без спикеров** *(самый быстрый, ~4–5 мин на MPS)*
```bash
gigaam-ui "$BASE/audio1715906680.m4a" \
  -m v3_e2e_ctc --device mps \
  -o "$BASE/transcripts/gigaam.txt"
```

**4. Указать число спикеров / другой формат**
```bash
gigaam-ui "$BASE/audio1715906680.m4a" \
  -m v3_e2e_rnnt -d pyannote --device cpu --diar-device mps \
  --speakers 4 -f json -o "$BASE/transcripts/gigaam.json"
```

### Простой CLI (из оригинала)
```bash
gigaam-transcribe audio.wav                          # простая транскрипция
gigaam-transcribe meeting.mp4 -d pyannote -o out.txt # с диаризацией
gigaam-transcribe video.mp4 -f srt -o subs.srt       # субтитры
gigaam-batch *.mp3 -o transcripts/ -d pyannote       # пакетно
```

### Python API
```python
from gigaam_transcriber import GigaAMTranscriber

with GigaAMTranscriber(model_name="v3_e2e_ctc", device="cpu") as t:
    result = t.transcribe("meeting.mp4", diarization="pyannote", output_format="json")
    result.save("out.json", format="json")
    for seg in result.segments:
        print(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.speaker}: {seg.text}")
```

---

## Оптимизация и производительность

### Где узкое место
На CPU тормозит **не ASR, а pyannote-этапы** (VAD и диаризация). Это feedforward-инференс нейросети по сотням окон — идеально параллельная нагрузка, которая на CPU страдает сильнее всего, а на GPU «схлопывается».

- ASR `v3_e2e_ctc` сам по себе очень быстрый (на CPU ~38× реалтайма).
- ASR `v3_e2e_rnnt` на CPU медленнее (декодер частично последовательный), но точнее; **на MPS разрыв исчезает** (~58× реалтайма) — энкодер feedforward доминирует.
- VAD + диаризация — основная стоимость на CPU.

### Замеры (этот Mac, Apple M5 Pro, 16 GPU-ядер)

| Что | CPU | MPS | Выигрыш |
|---|---|---|---|
| Эмбеддер диаризации (`WeSpeakerResNet34`) | ~21–27 окон/с | ~337 окон/с | **≈14×** |
| Диаризация end-to-end (5-мин клип) | 208 с | 71 с | **≈9× на диаризации** |
| ASR `v3_e2e_rnnt` (5-мин клип) | 7.9× реалтайма | 58× реалтайма | **≈7×** (текст идентичен) |
| Размер батча эмбеддингов на CPU | 32→128 даёт лишь ~1.25× | — | слабый рычаг |

Полный файл 1.5 ч (`rnnt` + диаризация): ~70 мин all-CPU → **~8–10 мин всё на MPS**. RNNT на MPS даёт тот же текст, что на CPU, но ASR ~7× быстрее — **держать ASR на CPU смысла нет**, выгоднее `--device mps` целиком.

### Рычаги — зависят от железа

| Машина | Устройство | Команда | Лучший рычаг |
|---|---|---|---|
| **Apple Silicon** (этот Mac) | MPS | `--device mps` (или `--diar-device mps`) | **MPS** — главный выигрыш, уже внедрён |
| **Сервер с NVIDIA** | CUDA | `--device cuda` | **CUDA** — диаризация в единицы минут |
| **CPU-only сервер** | CPU | `--device cpu --backend onnx --onnx-int8` | **ONNX int8** для ASR — внедрено (см. ниже). Диаризацию ускорит только GPU или отдельный int8 эмбеддера |

Замечания:
- `--device auto` выбирает `cuda`, если доступна, иначе `cpu`. `mps` **не** выбирается автоматически — указывайте явно.
- На MPS неподдержанные ops уходят на CPU (`PYTORCH_ENABLE_MPS_FALLBACK=1` ставится автоматически).
- Результат транскрипта от устройства **не зависит** — меняется только скорость.
- Мелкое дублирование: VAD (GigaAM) и диаризация (pyannote) считают сегментацию дважды (~3.5 мин на CPU) — потенциал для будущей оптимизации.

### ONNX-бэкенд для ASR (`--backend onnx`)

GigaAM-ASR можно гонять через ONNX Runtime (CPU или CUDA, **не** MPS). Первый запуск разово экспортирует и кэширует граф (`~/.cache/gigaam_onnx/`).

Замер (CTC, 5-мин клип, CPU):

| ASR-бэкенд | Скорость | Размер графа |
|---|---|---|
| `torch` cpu | ~8–40× (шумно) | — |
| `onnx` cpu (fp32) | ~36× | 845 МБ |
| `onnx` cpu (`--onnx-int8`) | **~72×** | 305 МБ |

- **int8 даёт ~2× над onnx-fp32** и сжимает граф ~2.8×; текст остаётся связным (мелкие отличия — норма для квантизации). Фикс: квантизуем только `MatMul` (Conv→ConvInteger у onnxruntime-CPU без ядра).
- RNNT через ONNX (fp32) даёт текст **байт-в-байт как torch**.

**Когда полезно:** на **CPU-only сервере** (нет MPS/CUDA) это лучший рычаг для ASR. На Apple Silicon удобнее MPS (освобождает CPU).

```bash
# CPU-only, максимально быстрый ASR через ONNX int8
gigaam-ui audio.m4a -m v3_e2e_ctc --backend onnx --onnx-int8 --device cpu -o out.txt
```

### Диаризация на ONNX (`--diar-backend onnx`)

Эмбеддер диаризации (WeSpeaker, узкое место) можно тоже гонять на ONNX (скачивает `speaker-embedding.onnx` из `hbredin/wespeaker-voxceleb-resnet34-LM`). **Сегментация** диаризации остаётся torch — у неё ONNX-пути нет, поэтому «вся диаризация на ONNX» недостижима. ONNX Runtime — только CPU.

```bash
# Максимально полный ONNX-прогон (ASR + эмбеддер диаризации), всё на CPU
gigaam-ui audio.m4a -m v3_e2e_rnnt --backend onnx --onnx-int8 \
  -d pyannote --diar-backend onnx --device cpu -o out.txt
```

**Важно:** на этом Mac это **медленнее** MPS (всё на CPU). `--diar-backend onnx` имеет смысл только на **CPU-only сервере**; при наличии GPU быстрее `--diar-device cuda`/`mps` (torch).

---

## Перенос на GPU-сервер (Selectel / NVIDIA)

Для регулярного потока самое радикальное ускорение — NVIDIA GPU: диаризация уходит с ~53 мин в единицы минут (она параллельнее ASR), а ASR/VAD тоже ускоряются.

**Шаги на инстансе Selectel с GPU (Ubuntu + NVIDIA-драйвер + CUDA):**

```bash
# 1. Системное
sudo apt update && sudo apt install -y python3.11 python3.11-venv ffmpeg git
python3.11 -m venv .venv && source .venv/bin/activate && pip install -U pip

# 2. PyTorch с CUDA (выберите версию CUDA под драйвер; пример cu124)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

# 3. GigaAM + pyannote + обёртка (как на Mac, но torch уже стоит)
git clone https://github.com/salute-developers/GigaAM.git
pip install -e ./GigaAM
pip install 'pyannote.audio==4.0.*'
pip install -e .

# 4. Токен и gated-модели — те же, что выше (.env + accept на HuggingFace)

# 5. Запуск на GPU
gigaam-ui audio.m4a -m v3_e2e_rnnt -d pyannote --device cuda -o out.txt
```

На CUDA `--device auto` сам выберет GPU; `fp16` для энкодера GigaAM включается автоматически (быстрее). На GPU имеет смысл использовать `v3_e2e_rnnt` (лучшее качество) — скорость уже не проблема.

---

## Модели GigaAM

| Модель | Описание | Когда |
|---|---|---|
| `v3_e2e_ctc` | CTC, с пунктуацией/нормализацией | **Быстро** (рекоменд. для CPU/MPS) |
| `v3_e2e_rnnt` | RNNT, с пунктуацией/нормализацией | **Точнее** (рекоменд. на GPU) |
| `v3_ctc` / `v3_rnnt` | без пунктуации | — |
| `v2_*`, `v1_*` | старые версии | — |

## Форматы вывода

`txt` (с таймкодами и спикерами), `json` (метаданные + сегменты), `srt`, `vtt` (субтитры).

> Таймкоды в `txt` — формат **ММ:СС:сотые** (напр. `00:32:92` = 32.92 с).

---

## Что изменено в этом форке

- Совместимость с GigaAM `main` (новый API) и pyannote.audio 4.0.
- Новый CLI `gigaam-ui` с живым UI и метриками.
- Автозагрузка `.env` (`python-dotenv`).
- Опции `--device mps`, `--diar-device`, `--embedding-batch-size`, `--segmentation-batch-size`.
- ONNX-бэкенд для ASR: `--backend onnx` / `--onnx-int8` (модуль `onnx_backend.py`).
- ONNX-эмбеддер диаризации: `--diar-backend onnx` (сегментация остаётся torch).
- Убран бесполезный fallback диаризации на устаревшую `pyannote/speaker-diarization` (маскировал реальные 403).
- Фикс `pyproject.toml` (пустой email ломал установку).

## Структура

```
gigaam_transcriber/
├── cli_ui.py          # CLI с живым UI (gigaam-ui)
├── onnx_backend.py    # экспорт/квантизация ONNX для ASR
├── cli.py             # простой CLI (gigaam-transcribe / gigaam-batch)
├── transcriber.py     # фасад GigaAMTranscriber
├── audio_processor.py # ffmpeg: конвертация/извлечение аудио
├── diarization.py     # pyannote-диаризация
├── segment_merger.py  # склейка сегментов
├── formatters.py      # txt/json/srt/vtt
└── data_models.py     # структуры данных
```

## Ссылки
- [GigaAM](https://github.com/salute-developers/GigaAM) · [pyannote.audio](https://github.com/pyannote/pyannote-audio)

## Лицензия
MIT
