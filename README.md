# DialogScribe

Транскрипция созвонов на русском языке — поверх [GigaAM v3](https://github.com/salute-developers/GigaAM)
(ASR от Salute) с диаризацией спикеров, слоем качества и web-интерфейсом.

Проект состоит из трёх слоёв с одним источником истины:

- **библиотека `gigaam_transcriber`** — весь пайплайн (ASR → спикеры → пост-проходы качества);
- **CLI `dialogscribe`** — тонкая обёртка для работы из терминала;
- **web-сервер** (FastAPI + Huey + SQLite) со SPA (React) — загрузка записей, авто-наблюдение за
  папками, очередь задач, просмотрщик транскриптов с плеером и правками.

## Возможности

- **ASR GigaAM v3** (RNN-T/CTC) — файлы любой длительности, per-chunk confidence,
  пословные таймкоды (`--word-timestamps`).
- **Спикеры двумя путями:**
  - *Route A* — подорожечные записи Zoom (`Audio Record/*.m4a`): имя дорожки = точное имя
    участника, диаризация не нужна;
  - *Route B* — диаризация [pyannote 3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
    для микс-записей (+опц. voiceprint: именование «Спикер №N» по галерее голосов ECAPA).
- **Слой качества** (кириллический вывод модели неприкосновенен — инвариант I1, правится только
  латиница/числа):
  - глоссарий-канонизация имён и терминов (`config/glossary.json`, редактируется из UI);
  - L2 «второе мнение» — локальный multilingual Whisper перечитывает сегменты с латиницей и
    чинит английские термины/бренды, которые GigaAM коверкает (`Funcion Hells` → `Function Health`);
  - флаги риска текста (подозрение на галлюцинацию/зацикливание) — пометка, не правка;
  - самообучение глоссария: устойчивые L2-правки копятся в лог → `dialogscribe glossary harvest`.
- **Форматы вывода:** `txt`, `json`, `srt`, `vtt`, `md` + машинный L0-субстрат
  (`transcript.v1.jsonl` + sha256) для downstream-обработки (RAG и т.п.).
- **Скорость:** MPS (Apple Silicon) и CUDA; на CPU-серверах — ONNX-бэкенды
  (`--backend onnx --onnx-int8` ≈ 3× к декоду; `--onnx-encoder` — 2× с сохранением confidence);
  resume по manifest — повторный прогон файла пропускает ASR.
- **Web:** ручная загрузка (микс или дорожки), авто-наблюдение за локальной папкой Zoom и за
  Яндекс.Диском (OAuth), очередь с прогрессом (SSE), просмотрщик с волной/плеером, правкой
  имён спикеров и текста (оверлеем — исходный `result.json` не мутируется), словарь, галереи голосов.

## Требования

- Python **3.11+** (минимум 3.10), [FFmpeg](https://ffmpeg.org/download.html) в `PATH`;
- **HF-токен** (read) — нужен даже без диаризации: файлы длиннее 25 с идут через pyannote-VAD.
  Примите условия моделей тем же аккаунтом:
  [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) (всегда) и
  [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) (для диаризации);
- 8+ ГБ RAM; GPU опционален (MPS/CUDA ускоряют диаризацию ~9×). Модели GigaAM качаются с CDN Сбера
  без токена.

> ⚠️ **Пин `pyannote.audio==4.0.5`** зафиксирован в extras пакета — 4.0.6 ломает VAD GigaAM.
> Не обновляйте pyannote вручную.

## Установка

```bash
git clone https://github.com/stay-metal/vads-transcribe.git DialogScribe
cd DialogScribe

python3.11 -m venv .venv && source .venv/bin/activate
pip install -U pip

# GigaAM (ветка main — v3-модели) кладётся рядом и ставится editable
git clone https://github.com/salute-developers/GigaAM.git
pip install -e ./GigaAM

# Пакет + нужные extras:
#   diarization    — спикеры (pyannote 4.0.5 + speechbrain)
#   second-opinion — L2 «второе мнение» (faster-whisper)
#   onnx           — ONNX-бэкенды для CPU
#   server         — web-сервер
pip install -e ".[diarization,second-opinion]"

# токен HuggingFace
cp .env.example .env   # впишите HF_TOKEN=hf_...
```

Проверка доступа к gated-моделям:

```bash
# import gigaam_transcriber первым — он автозагружает HF_TOKEN из .env
python -c "import gigaam_transcriber; from huggingface_hub import auth_check; \
[auth_check(r) for r in ('pyannote/segmentation-3.0','pyannote/speaker-diarization-3.1')]; \
print('доступ ок')"
```

## Быстрый старт (CLI)

```bash
# Один файл (аудио или видео), спикеры через диаризацию, вывод в markdown
dialogscribe transcribe meeting.m4a -d pyannote -f md -o meeting.md

# Подорожечная запись Zoom: имена спикеров = имена дорожек, диаризация не нужна
dialogscribe route-a "~/Zoom/2026-07-08 12.05 Дейли" -f md -o daily.md

# Пакет файлов
dialogscribe batch *.mp3 -o transcripts/ -d pyannote

# Машинный вывод в stdout (баннеры и прогресс идут в stderr)
dialogscribe transcribe a.m4a -f json > out.json
```

На Apple Silicon добавьте `--device mps` (диаризация ускоряется ~9×, текст не меняется);
на NVIDIA — `--device cuda`; `--device auto` сам берёт CUDA при наличии.

### Слой качества

```bash
# Глоссарий включён по умолчанию (--no-glossary отключает).
# L2 «второе мнение»: локальный Whisper чинит английские термины (модель small качается при первом запуске)
dialogscribe transcribe meeting.m4a -d pyannote --second-opinion -o out.txt
dialogscribe route-a ./запись --second-opinion -o out.md   # per-track

# Voiceprint: назвать анонимных «Спикер №N» по галерее голосов
dialogscribe gallery build team --track "Алиса=alice.m4a" --track "Борис=boris.m4a"
dialogscribe transcribe mix.m4a -d pyannote --voiceprint --gallery ~/.cache/gigaam_transcriber/galleries/team.json

# Самообучение глоссария: свернуть накопленные L2-правки в кандидатов-terms
dialogscribe glossary harvest            # показать
dialogscribe glossary harvest --apply    # дописать в config/glossary.json
```

Все флаги: `dialogscribe <команда> --help`.

### Python API

```python
from gigaam_transcriber import GigaAMTranscriber

with GigaAMTranscriber(device="auto") as t:
    result = t.transcribe("meeting.mp4", diarization="pyannote", second_opinion=True)
    result.save("meeting.md", format="md")
    for seg in result.segments:
        print(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.speaker}: {seg.text}")

    # Подорожечная запись Zoom
    tracks = GigaAMTranscriber.discover_route_a_tracks("~/Zoom/встреча")
    result = t.transcribe_route_a(tracks, second_opinion=True)
```

## Web-сервер

Архитектура: процесс **api** (FastAPI, модель не грузит) + **gpu-worker** (Huey, очередь `gpu`,
строго один процесс с тёплой моделью) + **io-worker** (очередь `io`: скачивания, поллеры) +
SQLite (WAL). SPA собирается в статику и раздаётся FastAPI.

### Прод: docker compose

```bash
cd deploy
cp .env.example .env        # заполните хэш пароля, ключи, HF_TOKEN (см. комментарии в файле)
mkdir -p certs              # TLS: положите fullchain.pem и privkey.pem (nginx ждёт их здесь)
docker compose up -d --build
```

Наружу смотрит только nginx (TLS, порты 80/443); api и воркеры не публикуются.
Для GPU нужен NVIDIA Container Toolkit.

### Локальная разработка

```bash
pip install -e ".[server,diarization,second-opinion]"

# окружение (dev по HTTP)
python -c "from gigaam_transcriber.server.security import hash_password; print(hash_password('ФРАЗА'))"
export DIALOGSCRIBE_PASSWORD_HASH='<хэш>' \
       DIALOGSCRIBE_SESSION_KEY='<48+ случайных символов>' \
       DIALOGSCRIBE_FERNET_KEY='<другие 48+ символов>' \
       DIALOGSCRIBE_COOKIE_SECURE=0 DIALOGSCRIBE_REQUIRE_HTTPS=0

dialogscribe serve --port 8000                       # api
python -m gigaam_transcriber.server.run_gpu_worker \
       -k process -w 1                               # gpu-воркер (на macOS сам переключится в -k thread)
huey_consumer gigaam_transcriber.server.tasks.io_huey -w 2   # io-воркер (нужен для watch/Я.Диска)

cd frontend && npm install && npm run dev            # SPA c прокси на :8000
# или прод-сборка, которую раздаст FastAPI: npm run build
```

Apple GPU для воркера: `export DIALOGSCRIBE_DEVICE=mps`.

### Что умеет UI

- **Записи** — очередь и архив джоб с прогрессом стадий, поиск/фильтры по датам;
  управление задачами: пауза/возобновление в очереди, отмена (в том числе уже
  идущей обработки — кооперативно, на ближайшей безопасной точке) и
  «Заново» — перетранскрибация завершённой записи с теми же параметрами;
- **Загрузка** — микс-файл (диаризация) или несколько дорожек (Route A с подтверждением имён);
- **Источники** — авто-наблюдение: локальная папка Zoom (профили раскладки, дедуп по
  magic-bytes, транскрипты складываются рядом с записью) и Яндекс.Диск (OAuth, окно
  стабильности, exactly-once ingest). Прескан: если в папке встречи уже лежит готовая
  транскрибация (`result.json` по раскладке профиля), она заносится в базу как
  выполненная — переустановка или перенос архива не пережёвывают GPU уже сделанное;
- **Просмотрщик** — волна/плеер (wavesurfer), клик по реплике = перемотка, правка имён
  спикеров и текста (оверлей, `result.json` не мутируется), бейджи качества
  (низкий confidence, подозрение на галлюцинацию), «выделить → в словарь», скачивание
  md/txt/json/srt/vtt и L0;
- **Словарь** — редактирование глоссария с lint-стражем (алиас, совпадающий с настоящим
  словом, отклоняется);
- **Настройки** — формат по умолчанию, галереи голосов, ретенция.

## Конфигурация

Все настройки — через переменные окружения (`.env` автозагружается):

| Переменная | Что делает |
|---|---|
| `HF_TOKEN` | HuggingFace-токен (VAD + диаризация) |
| `DIALOGSCRIBE_PASSWORD_HASH` / `_USER` | Вход в web (bcrypt-хэш; user по умолчанию `admin`) |
| `DIALOGSCRIBE_SESSION_KEY` / `_FERNET_KEY` | Раздельные ключи: подпись cookie / шифрование секретов в БД |
| `DIALOGSCRIBE_DATA_DIR` | Каталог данных сервера (по умолчанию `~/.dialogscribe`) |
| `DIALOGSCRIBE_DEVICE` | Устройство gpu-воркера: `cuda`/`mps`/`cpu` |
| `DIALOGSCRIBE_LOCAL_WATCH_ROOT` | Корень-allowlist для локального watch |
| `DIALOGSCRIBE_YANDEX_WATCH_DIR`, `YANDEX_OAUTH_CLIENT_ID/SECRET` | Интеграция с Яндекс.Диском |
| `GIGAAM_WHISPER_MODEL` / `_COMPUTE` | Модель L2 «второго мнения» (по умолчанию `small`/`int8`) |
| `GIGAAM_TRANSCRIBER_CONFIG` / `_CACHE` | Переопределение папки `config/` и кэша (`~/.cache/dialogscribe`) |

Полные списки с комментариями: [`.env.example`](.env.example) (CLI/dev) и
[`deploy/.env.example`](deploy/.env.example) (сервер, включая лимиты загрузки и анти-brute-force).

## Производительность

Замеры на записи 1.5 ч (Apple M-серия): all-CPU ≈ 70 мин → **всё на MPS ≈ 8–10 мин**
(узкое место — диаризация, на GPU она «схлопывается» ~9×). Текст от устройства не зависит.

| Железо | Рекомендация |
|---|---|
| Apple Silicon | `--device mps` (или хотя бы `--diar-device mps`) |
| NVIDIA | `--device cuda` |
| CPU-only сервер | `--backend onnx --onnx-int8` (≈3× декод, без confidence) или `--onnx-encoder` (≈2×, confidence сохраняется) |

При GPU-сбое (OOM) декод автоматически откатывается на CPU и помечает
`metadata.device_fallback`; следующая джоба возвращается на GPU.

## Структура репозитория

```
gigaam_transcriber/        # библиотека — источник истины по пайплайну
├── transcriber.py         #   фасад GigaAMTranscriber (оркестрация, устройство, пост-проходы)
├── decode.py              #   декод-бэкенды: short/longform+confidence/ONNX
├── audio_processor.py     #   ffmpeg: нормализация, извлечение аудио из видео
├── diarization.py         #   pyannote 3.1 + гибридный режим
├── speaker_mapping.py     #   спикер↔текст по max суммарному overlap
├── segment_merger.py      #   сшивка реплик одного спикера
├── confidence.py          #   per-chunk confidence греди-декода RNN-T
├── glossary.py            #   канонизация имён/терминов + двухъязычный lint (I1)
├── whisper_asr.py         #   L2 «второе мнение» (faster-whisper) + кэш
├── fusion.py              #   слияние L2: только латиница/числа (I1)
├── voiceprint.py          #   ECAPA-галереи, precision-first именование
├── text_quality.py        #   флаги риска (галлюцинация/зацикливание)
├── l0.py, manifest.py     #   L0-субстрат; resume по хэшу файла
├── onnx_backend.py, onnx_encoder.py  # ONNX-пути (CPU)
├── data_models.py         #   TranscriptionResult/Segment, форматы вывода
├── dialogscribe_cli.py    #   CLI
└── server/                #   FastAPI + Huey + SQLite (api модель не грузит)
frontend/                  # SPA: Vite + React + TS + Tailwind + TanStack Query
config/                    # глоссарий, словари lint, it_ai_terms (прайминг L2)
deploy/                    # docker-compose (nginx TLS + api + 2 воркера) + .env.example
tests/                     # pytest (~420 тестов, без загрузки ML-моделей)
```

Ключевые инварианты (подробнее — в `CLAUDE.md`):

- **I1** — кириллический вывод GigaAM байт-в-байт неприкосновенен; глоссарий/L2 правят только
  латиницу/числа;
- **lib-as-truth** — CLI и сервер зовут только методы библиотеки, без своих декод-циклов;
- api-процесс никогда не грузит модель; GPU держит ровно один воркер (`-w 1`);
- `result.json` не мутируется — правки спикеров/текста накладываются оверлеем при чтении.

## Устранение неполадок

| Симптом | Причина / решение |
|---|---|
| `403`/`gated` при загрузке моделей pyannote | Примите условия моделей на HuggingFace тем же аккаунтом, что и токен (ссылки выше) |
| `'generator' has no get_timeline` на длинных файлах | Установлен pyannote.audio ≠ 4.0.5 — переустановите `pip install -e ".[diarization]"` |
| Файл с кириллицей «не находится» на macOS | APFS хранит имена в NFD; CLI нормализует пути сам — передавайте путь как есть, без ручной перекодировки |
| Джобы медленные, `device_fallback: cpu` в metadata | GPU недоступен процессу (проверьте `DIALOGSCRIBE_DEVICE`, для Docker — проброс GPU) |
| `dialogscribe serve` падает по конфигурации | Не заданы `DIALOGSCRIBE_PASSWORD_HASH`/`SESSION_KEY`/`FERNET_KEY` — см. раздел «Web-сервер» |
| L2 «второе мнение» ничего не меняет | Это precision-first: неуверенные прочтения Whisper отбрасываются; см. счётчики в `metadata.second_opinion` |

## Разработка

```bash
pip install -e ".[all]"                 # + server и dev-инструменты
.venv/bin/python -m pytest              # ~420 тестов, ML-модели не грузятся
ruff check gigaam_transcriber tests && black gigaam_transcriber tests && mypy gigaam_transcriber
cd frontend && npm run build            # фронт-гейт (tsc + vite)
```

Тесты, требующие живую модель GigaAM, помечены `requires_model` и по умолчанию пропускаются
(`pytest -m requires_model` — запустить явно).

## Лицензия

MIT. Использует [GigaAM](https://github.com/salute-developers/GigaAM) (MIT) и
[pyannote.audio](https://github.com/pyannote/pyannote-audio) (MIT; модели — по условиям HuggingFace).
