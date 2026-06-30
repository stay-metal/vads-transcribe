"""L2 «второе мнение» — ЛОКАЛЬНЫЙ multilingual Whisper (faster-whisper, CPU/int8). Бесплатно.

Перенос из custom (zoom_transcriber/whisper_asr.py), адаптирован под DialogScribe:
вход — numpy-сегмент (а не wav-путь; faster-whisper принимает массив напрямую), убрана
ветка legacy gemini-кэша. Перечитывает сегмент-кандидат (с латиницей) маленькой
многоязычной моделью, чтобы fusion.py поправил ровно то, что greedy RNN-T GigaAM путает
(латиница/бренды/числа). Кириллица verbatim (I1) — её правит не whisper, а слияние, и только
латиницу/цифры.

Прайминг: канонические написания из глоссария подаются как whisper ``initial_prompt`` —
ersatz-biasing к канону (Function Health / SuperPower), которого у GigaAM нет.
Гейт точности (precision-first): низкий ``avg_logprob`` / высокий ``no_speech_prob`` →
«нет мнения», оставляем GigaAM. Кэш по sha256(байты сегмента + модель + prompt).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .data_models import TranscriptionResult, merge_provenance

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "local_whisper"

# Маленькая многоязычная модель (small int8 на CPU ≈ 1.2 ГБ, ~3с/30с-окно). Переопределяемо env.
DEFAULT_MODEL = os.environ.get("GIGAAM_WHISPER_MODEL", "small").strip() or "small"
_COMPUTE_TYPE = os.environ.get("GIGAAM_WHISPER_COMPUTE", "int8").strip() or "int8"
_PROMPT_MAX_CHARS = 900
_SAMPLE_RATE = 16000


def _gate_min_logprob() -> float:
    try:
        return float(os.environ.get("GIGAAM_WHISPER_MIN_LOGPROB", "-1.0"))
    except ValueError:
        return -1.0


def _gate_max_no_speech() -> float:
    try:
        return float(os.environ.get("GIGAAM_WHISPER_MAX_NO_SPEECH", "0.6"))
    except ValueError:
        return 0.6


_model = None
_model_key: Optional[tuple] = None
_model_lock = threading.Lock()


def _get_model(model: str):
    global _model, _model_key
    key = (model, _COMPUTE_TYPE)
    if _model is not None and _model_key == key:
        return _model
    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        from faster_whisper import WhisperModel  # тяжёлый импорт — только при первом мнении
        _model = WhisperModel(model, device="cpu", compute_type=_COMPUTE_TYPE)
        _model_key = key
        return _model


def _prompt_text(context: Optional[str]) -> str:
    """Компактная словарь-подсказка для whisper initial_prompt (канонические написания)."""
    if not context:
        return ""
    text = " ".join(context.split())
    if len(text) > _PROMPT_MAX_CHARS:
        text = text[-_PROMPT_MAX_CHARS:]
        if " " in text:
            text = text.split(" ", 1)[1]
    return text


def _cache_path(audio_bytes: bytes, model: str, prompt: str = "") -> Path:
    h = hashlib.sha256(audio_bytes + b"\x00" + model.encode("utf-8"))
    if prompt:
        h.update(b"\x00" + prompt.encode("utf-8"))
    return CACHE_DIR / f"{h.hexdigest()}.json"


def _assess_confidence(segs: list) -> bool:
    """Precision-first гейт: длительность-взвешенный avg_logprob и доля тишины. Пусто → не уверены."""
    if not segs:
        return False
    total_dur = weighted_logprob = 0.0
    max_no_speech = 0.0
    for s in segs:
        dur = max(0.0, float(getattr(s, "end", 0.0)) - float(getattr(s, "start", 0.0)))
        lp = float(getattr(s, "avg_logprob", 0.0))
        total_dur += dur
        weighted_logprob += lp * dur
        max_no_speech = max(max_no_speech, float(getattr(s, "no_speech_prob", 0.0)))
    avg_logprob = (weighted_logprob / total_dur) if total_dur > 0 else (
        sum(float(getattr(s, "avg_logprob", 0.0)) for s in segs) / len(segs))
    return avg_logprob >= _gate_min_logprob() and max_no_speech <= _gate_max_no_speech()


def second_opinion(
    audio: np.ndarray,
    model: str = DEFAULT_MODEL,
    *,
    lang_hint: str = "ru",
    context: Optional[str] = None,
) -> dict:
    """Локальное «второе мнение» по numpy-сегменту (float32, 16кГц моно).

    Возврат: ``{text, model, cached, confident}``. ``confident`` — прошёл ли гейт точности
    (вызывающий fuse-ит только уверенные). Кэш по sha256(байты+модель+prompt)."""
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    prompt = _prompt_text(context)
    cache = _cache_path(audio.tobytes(), model, prompt)
    if cache.exists():
        try:
            hit = json.loads(cache.read_text(encoding="utf-8"))
            text, confident = hit["text"], bool(hit.get("confident", True))
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            text, confident = None, False
        if isinstance(text, str):
            return {"text": text, "model": model, "cached": True, "confident": confident}

    whisper = _get_model(model)
    with _model_lock:  # CPU-bound декод сериализуем (один инстанс на процесс)
        segments, _info = whisper.transcribe(
            audio,
            language=lang_hint,
            beam_size=5,
            initial_prompt=prompt or None,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        segs = list(segments)

    text = " ".join(s.text.strip() for s in segs).strip()
    confident = _assess_confidence(segs)

    if text:  # пустое не кэшируем (отравило бы resume)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"text": text, "confident": confident}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, cache)
    return {"text": text, "model": model, "cached": False, "confident": confident}


_LATIN_RE = re.compile(r"[A-Za-z]")


def is_candidate(text: str) -> bool:
    """Сегмент-кандидат на «второе мнение»: содержит латиницу (потенц. бренд-мангл)."""
    return bool(_LATIN_RE.search(text or ""))


def _build_context(alias_map: Dict[str, str]) -> str:
    """Подсказка-прайминг: канонические написания терминов/имён из глоссария."""
    return " ".join(sorted({v for v in alias_map.values() if v}))


def _load_waveform_16k_mono(audio_path) -> np.ndarray:
    import torchaudio
    wav, sr = torchaudio.load(str(audio_path))
    if sr != _SAMPLE_RATE:
        wav = torchaudio.transforms.Resample(sr, _SAMPLE_RATE)(wav)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    return wav[0].numpy().astype(np.float32)


def apply_second_opinion(
    result: TranscriptionResult,
    audio_path,
    alias_map: Optional[Dict[str, str]] = None,
    *,
    model: str = DEFAULT_MODEL,
) -> int:
    """Перечитать сегменты-кандидаты (с латиницей) локальным Whisper и слить под I1.

    Возвращает число изменённых сегментов. Кириллица не трогается (fusion меняет лишь
    латиницу/числа). Уверенные прочтения сливаются, неуверенные → GigaAM (precision-first).
    На изменённых сегментах provenance → 'second-opinion'."""
    from .fusion import fuse

    alias_map = alias_map or {}
    candidates = [s for s in result.segments if is_candidate(s.text)]
    if not candidates:
        return 0
    waveform = _load_waveform_16k_mono(audio_path)
    context = _build_context(alias_map)
    changed = 0
    for seg in candidates:
        a = waveform[int(seg.start * _SAMPLE_RATE): int(seg.end * _SAMPLE_RATE)]
        if a.size == 0:
            continue
        res = second_opinion(a, model=model, context=context)
        if not res["confident"] or not res["text"]:
            continue
        new_text = fuse(seg.text, res["text"], alias_map)
        if new_text != seg.text:
            seg.text = new_text
            seg.provenance = merge_provenance(seg.provenance, "second-opinion")
            changed += 1
    return changed
