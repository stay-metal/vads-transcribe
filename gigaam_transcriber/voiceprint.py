"""Voiceprint-именование спикеров через SpeechBrain ECAPA-TDNN — перенос из custom.

Превращает анонимных «Спикер №N» (выход диаризации) в реальные имена по эталонной
галерее голосов. Precision-first: имя присваивается ТОЛЬКО при
    cos(top1) >= θ  И  cos(top1) − cos(top2) >= Δ        (θ=0.55, Δ=0.10)
иначе спикер остаётся «Спикер №N» (абстенция честнее ложного имени).

Галерея {имя: 192-центроид} строится из подписанных дорожек (Route A: Audio Record/*.m4a,
имя = имя участника). Эмбеддер SpeechBrain грузится лениво — `import voiceprint` и тесты
`name_speaker`/`cosine` НЕ требуют speechbrain.

Ядро (`name_speaker`/`vote_speaker`/`cosine`) перенесено из custom без изменений логики.
ОТЛИЧИЕ от custom: voiceprint меняет МЕТКУ спикера, а не текст, поэтому `seg.provenance`
(происхождение ТЕКСТА) НЕ трогаем (custom ставил provenance='voiceprint' — здесь это была бы
путаница scope: текст остаётся gigaam). Калибровка θ (LOO) и резюмируемая корпус-галерея не
портированы — используем дефолтные пороги (можно поднять при росте галереи).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .data_models import TranscriptionResult

ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
EMBED_DIM = 192
SAMPLE_RATE = 16000

DEFAULT_THRESHOLD = 0.55
DEFAULT_MARGIN = 0.10
SINGLE_REF_THRESHOLD = 0.70  # единственный референс → строже (margin-гейт тривиален)

WINDOW_SEC = 3.0
WINDOW_HOP_SEC = 1.5
SILENCE_RMS = 1e-3

VOTE_MIN_WINDOWS = 3
VOTE_MAJORITY = 0.67

GALLERY_VERSION = 1


def _as_vector(emb) -> np.ndarray:
    return np.asarray(emb, dtype=np.float64).reshape(-1)


def cosine(a, b) -> float:
    """Косинусная близость ∈ [−1, 1]; нулевой вектор → 0.0 (без деления на ноль)."""
    va, vb = _as_vector(a), _as_vector(b)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def name_speaker(
    segment_emb,
    refs: dict[str, np.ndarray],
    thr: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
) -> str | None:
    """Имя спикера по voiceprint или None (precision-first).

    Имя топ-референса только при ``cos(top1) >= thr`` И ``cos(top1) − cos(top2) >= margin``.
    Единственный референс → порог ``max(thr, SINGLE_REF_THRESHOLD)``. Чистая математика."""
    if not refs:
        return None
    scored = sorted(
        ((cosine(segment_emb, ref), name) for name, ref in refs.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    top_cos, top_name = scored[0]
    effective_thr = max(thr, SINGLE_REF_THRESHOLD) if len(refs) == 1 else thr
    if top_cos < effective_thr:
        return None
    second_cos = scored[1][0] if len(scored) > 1 else -1.0
    if top_cos - second_cos < margin:
        return None
    return top_name


def vote_speaker(
    window_embs,
    refs: dict[str, np.ndarray],
    thr: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
    min_windows: int = VOTE_MIN_WINDOWS,
    majority: float = VOTE_MAJORITY,
) -> str | None:
    """Имя по голосованию пер-оконных эмбеддингов (recovery), precision-first.

    Каждое окно голосует через тот же gate ``name_speaker``. Имя X — только если за него
    ``>= min_windows`` окон И доля среди не-воздержавшихся ``>= majority``. Иначе None."""
    from collections import Counter

    votes = [name_speaker(e, refs, thr=thr, margin=margin) for e in window_embs]
    named = [v for v in votes if v is not None]
    if not named:
        return None
    top_name, top_n = Counter(named).most_common(1)[0]
    if top_n < min_windows or top_n / len(named) < majority:
        return None
    return top_name


# --------------------------------------------------------------------------------------
# ECAPA-эмбеддер (требует speechbrain; грузится лениво).
# --------------------------------------------------------------------------------------

_EMBEDDER = None


def _load_embedder():
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    from speechbrain.inference.speaker import EncoderClassifier

    savedir = Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa-voxceleb"
    _EMBEDDER = EncoderClassifier.from_hparams(source=ECAPA_SOURCE, savedir=str(savedir))
    return _EMBEDDER


def _load_waveform_16k_mono(audio_path) -> np.ndarray:
    import torchaudio

    wav, sr = torchaudio.load(str(audio_path))
    if sr != SAMPLE_RATE:
        wav = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(wav)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    return wav[0].numpy().astype(np.float32)


def window_vectors(audio, embedder=None) -> list[np.ndarray]:
    """Пер-оконные ECAPA-эмбеддинги (без усреднения). Тихие/короткие окна пропускаются."""
    import torch

    emb = embedder if embedder is not None else _load_embedder()
    wave = torch.as_tensor(np.asarray(audio, dtype=np.float32)).reshape(-1)
    win = int(WINDOW_SEC * SAMPLE_RATE)
    hop = int(WINDOW_HOP_SEC * SAMPLE_RATE)
    n = wave.numel()
    vectors: list[np.ndarray] = []
    starts = range(0, max(1, n - win + 1), hop) if n >= win else [0]
    for s in starts:
        seg = wave[s : s + win]
        if seg.numel() < SAMPLE_RATE:
            continue
        if float(torch.sqrt(torch.mean(seg.float() ** 2))) < SILENCE_RMS:
            continue
        with torch.no_grad():
            vec = emb.encode_batch(seg.unsqueeze(0))  # [1, 1, 192]
        vectors.append(_as_vector(vec.squeeze().cpu().numpy()))
    return vectors


def embed_windows(audio, embedder=None) -> np.ndarray:
    """Нормированный центроид ECAPA-эмбеддингов по окнам (или нулевой вектор, если речи нет)."""
    vectors = window_vectors(audio, embedder)
    if not vectors:
        return np.zeros(EMBED_DIM, dtype=np.float64)
    centroid = np.mean(np.stack(vectors, axis=0), axis=0)
    norm = float(np.linalg.norm(centroid))
    return centroid / norm if norm > 0 else centroid


# --------------------------------------------------------------------------------------
# Галерея: построение из дорожек + сохранение/загрузка.
# --------------------------------------------------------------------------------------


def build_gallery_from_tracks(tracks: dict[str, str], embedder=None) -> dict[str, np.ndarray]:
    """{имя: путь_к_дорожке} → {имя: 192-центроид}. Дорожки с одной речью одного спикера."""
    emb = embedder if embedder is not None else _load_embedder()
    refs: dict[str, np.ndarray] = {}
    for name, path in tracks.items():
        wave = _load_waveform_16k_mono(path)
        centroid = embed_windows(wave, emb)
        if float(np.linalg.norm(centroid)) > 0:
            refs[name] = centroid
    return refs


def save_gallery(
    refs: dict[str, np.ndarray], path, *, theta: float | None = None, margin: float = DEFAULT_MARGIN
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "version": GALLERY_VERSION,
        "theta": theta,
        "margin": margin,
        "refs": {name: _as_vector(vec).tolist() for name, vec in refs.items()},
    }
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    import os

    os.replace(tmp, path)
    return path


def load_gallery(path):
    """→ (refs {имя: np.array}, theta, margin). Нет файла → ({}, None, DEFAULT_MARGIN)."""
    path = Path(path)
    if not path.exists():
        return {}, None, DEFAULT_MARGIN
    obj = json.loads(path.read_text(encoding="utf-8"))
    refs = {name: _as_vector(vec) for name, vec in obj.get("refs", {}).items()}
    return refs, obj.get("theta"), float(obj.get("margin", DEFAULT_MARGIN))


# --------------------------------------------------------------------------------------
# Оркестрация: переименовать диаризованных «Спикер №N» по галерее.
# --------------------------------------------------------------------------------------


def name_diarized_speakers(
    result: TranscriptionResult,
    audio_path,
    refs: dict[str, np.ndarray],
    *,
    thr: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
) -> int:
    """Переименовать анонимных спикеров по галерее (precision-first). Возвращает число имён.

    Для каждой метки спикера собираем её аудио (слайсы сегментов), считаем пер-оконные
    эмбеддинги → центроид → ``name_speaker`` (иначе голосование). Только метка меняется
    (текст/provenance не трогаем). Сомнение → метка остаётся «Спикер №N»."""
    if not refs:
        return 0
    labels = [s.speaker for s in result.segments if s.speaker]
    if not labels:
        return 0
    waveform = _load_waveform_16k_mono(audio_path)
    embedder = _load_embedder()
    by_label: dict[str, list[np.ndarray]] = {}
    for label in dict.fromkeys(labels):  # уникальные, сохраняя порядок
        chunks = [
            waveform[int(s.start * SAMPLE_RATE) : int(s.end * SAMPLE_RATE)]
            for s in result.segments
            if s.speaker == label
        ]
        chunks = [c for c in chunks if c.size > 0]
        if not chunks:
            continue
        windows = window_vectors(np.concatenate(chunks), embedder)
        if windows:
            by_label[label] = windows

    rename: dict[str, str] = {}
    for label, windows in by_label.items():
        centroid = np.mean(np.stack(windows, axis=0), axis=0)
        name = name_speaker(centroid, refs, thr=thr, margin=margin)
        if name is None:
            name = vote_speaker(windows, refs, thr=thr, margin=margin)
        if name is not None and name != label:
            rename[label] = name

    if not rename:
        return 0
    for seg in result.segments:
        if seg.speaker in rename:
            seg.speaker = rename[seg.speaker]
    return len(rename)
