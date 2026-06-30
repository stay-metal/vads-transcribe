---
paths:
  - "gigaam_transcriber/*.py"
---

# ASR/ML-пайплайн

> Грузится при работе с библиотечными модулями `gigaam_transcriber/*.py` (`transcriber.py`,
> `onnx_encoder.py`, `fusion.py`, `whisper_asr.py`, `voiceprint.py`, `manifest.py`, …). Инварианты — в `CLAUDE.md`.

- pyannote.audio — РОВНО `==4.0.5` (см. инварианты); pyannote-пайплайн держи 3.1 явно (`diarization.py`).
- I1: кириллица verbatim; glossary/fusion/L2 — только латиница/числа (`_is_replaceable`).
- Первым делом в каждой публичной джобе (`transcribe`/`transcribe_route_a`) зови `self._repair_device()`
  (иначе sticky GPU→CPU fallback тормозит весь тёплый singleton).
- `preload()` фиксирует `_intended_device`; GPU-OOM/RuntimeError на mps/cuda → `_gpu_to_cpu_fallback` +
  повтор на CPU, метить `metadata['device_fallback']`.
- ONNX-пути только CPU/CUDA, не MPS; `backend='onnx'` даёт текст argmax≈torch, но БЕЗ per-chunk
  confidence — для confidence бери `backend='torch'`.
- ONNX-энкодер split-device: ошибка `load_onnx_encoder` → `None` и откат на `model.forward`; `encoded`
  верни на `model._device/_dtype`, иначе joint падает на mps/cuda. Кэш экспорта защищай маркером `.done`
  (пишется только после успеха).
- opt-in: ВСЕ флаги `transcribe()` default off, КРОМЕ `glossary=True`; `preclean` меняет вход (не
  I1-neutral) — строго под флагом, под A/B.
- `glossary load_runtime`: lint выкидывает term-алиас, совпавший с реальным ru/en словом; people с
  одиночным кир. инициалом не идёт в текст-замену.
- После правки `seg.text` обнуляй `seg.words` и поднимай provenance через `merge_provenance` (не понижая
  second-opinion/human).
- Route A: изолируй ошибки по дорожкам (битая → `metadata['failed_tracks']`, остальные выживают); на входе
  сбрось stale `_onnx_encoder`/`_backend`/`_word_timestamps`.
- resume по совпадению `file_hash` пропускает ASR, но `output_path` и L0 всё равно пиши через
  `_write_outputs`; бампай `LAYER_VERSIONS` при смене логики слоя.
