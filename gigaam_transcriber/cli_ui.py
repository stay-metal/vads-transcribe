#!/usr/bin/env python3
"""
gigaam-ui — CLI с живым UI (rich): реальный прогресс по стадиям + метрики.

В отличие от обычного gigaam-transcribe (анимированная заглушка прогресса),
здесь longform-цикл GigaAM переписан с настоящим побатчевым прогрессом ASR,
а диаризация показывает шаги pyannote через hook. Метрики обновляются в реальном
времени: прошло времени, обработано аудио, скорость (×реального времени), ETA,
число сегментов и спикеров.

Использование:
    gigaam-ui audio.m4a -m v3_e2e_ctc
    gigaam-ui meeting.mp4 -d pyannote --speakers 4 -o out.txt
    gigaam-ui audio.m4a --device mps         # эксперим. Apple GPU
"""

import os
# MPS: неподдержанные ops уходят на CPU вместо краша. Ставим до импорта torch.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import time
import unicodedata
from pathlib import Path
from typing import List, Optional

import click

# Импорт пакета триггерит автозагрузку .env (HF_TOKEN)
import gigaam_transcriber  # noqa: F401
from gigaam_transcriber.audio_processor import AudioProcessor
from gigaam_transcriber.data_models import TranscriptionResult, TranscriptionSegment
from gigaam_transcriber.diarization import DiarizationManager
from gigaam_transcriber.formatters import save_result
from gigaam_transcriber.segment_merger import MergeConfig, SegmentMerger

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()


def fmt_time(sec: float) -> str:
    """Секунды → Ч:ММ:СС."""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def normalize_path(path: str) -> Path:
    """NFC/NFD-нормализация пути (macOS HFS)."""
    p = Path(unicodedata.normalize("NFC", str(path)))
    if p.exists():
        return p
    parent = p.parent
    if parent.exists():
        for f in parent.iterdir():
            if unicodedata.normalize("NFD", f.name) == unicodedata.normalize("NFD", p.name):
                return f
    return p


def resolve_device(device: str) -> str:
    import torch

    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device == "mps" and not torch.backends.mps.is_available():
        console.print("[yellow]MPS недоступен, откат на CPU[/]")
        return "cpu"
    return device


class Metrics:
    """Состояние метрик для дашборда."""

    def __init__(self, model: str, device: str, diarize: str):
        self.t0 = time.monotonic()
        self.stage = "Инициализация"
        self.model = model
        self.device = device
        self.diarize = diarize
        self.audio_dur = 0.0
        self.processed = 0.0      # обработано секунд аудио (ASR)
        self.n_segments = 0
        self.done_segments = 0
        self.speakers = 0
        self.asr_t0: Optional[float] = None
        self.asr_end: Optional[float] = None  # фиксируем конец ASR, чтобы метрика скорости не «текла»

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    @property
    def asr_elapsed(self) -> float:
        if not self.asr_t0:
            return 0.0
        end = self.asr_end or time.monotonic()
        return end - self.asr_t0


def make_dashboard(progress: Progress, m: Metrics) -> Group:
    t = Table.grid(padding=(0, 3))
    t.add_column(justify="right", style="bold cyan", no_wrap=True)
    t.add_column(style="white")
    t.add_row("Стадия", f"[bold]{m.stage}[/]")
    t.add_row("Прошло", fmt_time(m.elapsed))
    t.add_row("Длительность аудио", fmt_time(m.audio_dur))
    if m.audio_dur:
        pct = 100 * m.processed / m.audio_dur
        t.add_row("Обработано аудио", f"{fmt_time(m.processed)}  ([green]{pct:.0f}%[/])")
    if m.asr_elapsed > 0 and m.processed > 0:
        speed = m.processed / m.asr_elapsed
        t.add_row("Скорость ASR", f"[bold green]{speed:.1f}×[/] реального времени")
    if m.n_segments:
        t.add_row("Сегментов (ASR)", f"{m.done_segments}/{m.n_segments}")
    if m.diarize != "none":
        t.add_row("Спикеров найдено", str(m.speakers) if m.speakers else "—")
    t.add_row("Модель / Устройство", f"[magenta]{m.model}[/] / [magenta]{m.device}[/]")
    meta = Panel(t, title="📊 Метрики", border_style="green", box=box.ROUNDED, padding=(0, 1))
    return Group(progress, meta)


class Dashboard:
    """Живой renderable: rich перерисовывает его на каждом тике auto-refresh,
    поэтому таймеры/метрики обновляются плавно даже во время «немых» стадий (VAD)."""

    def __init__(self, progress: Progress, m: "Metrics"):
        self.progress = progress
        self.m = m

    def __rich__(self):
        return make_dashboard(self.progress, self.m)


class RichDiarHook:
    """Хук прогресса pyannote → обновляет rich-прогресс диаризации."""

    def __init__(self, progress: Progress, task_id, m: Metrics, live: Live):
        self.progress = progress
        self.task = task_id
        self.m = m
        self.live = live

    def __call__(self, step_name=None, step_artifact=None, *args,
                 file=None, total=None, completed=None, **kwargs):
        desc = f"Диаризация: {step_name}" if step_name else "Диаризация"
        if total:
            self.progress.update(self.task, total=total, completed=completed or 0, description=desc)
        else:
            self.progress.update(self.task, description=desc)
        self.m.stage = desc
        self.live.refresh()


@click.command()
@click.argument("input_file")
@click.option("-o", "--output", type=click.Path(), help="Путь к выходному файлу")
@click.option("-m", "--model", default="v3_e2e_ctc", show_default=True,
              help="Модель GigaAM (v3_e2e_ctc — быстро, v3_e2e_rnnt — точнее)")
@click.option("-d", "--diarize", type=click.Choice(["none", "pyannote"]),
              default="none", show_default=True, help="Диаризация спикеров")
@click.option("--speakers", type=int, default=None, help="Ожидаемое число спикеров")
@click.option("--min-speakers", type=int, default=None)
@click.option("--max-speakers", type=int, default=None)
@click.option("--device", type=click.Choice(["auto", "cuda", "cpu", "mps"]),
              default="auto", show_default=True,
              help="Устройство ASR (mps — Apple GPU, экспериментально)")
@click.option("--diar-device", type=click.Choice(["auto", "cuda", "cpu", "mps"]),
              default=None,
              help="Устройство диаризации (по умолч. = --device). На Apple Silicon "
                   "mps ускоряет эмбеддинги ~10×+ — главный рычаг для диаризации")
@click.option("--embedding-batch-size", type=int, default=None,
              help="Батч эмбеддингов диаризации (по умолч. 32)")
@click.option("--segmentation-batch-size", type=int, default=None,
              help="Батч сегментации диаризации (по умолч. 32)")
@click.option("-f", "--format", "output_format",
              type=click.Choice(["txt", "json", "srt", "vtt"]), default="txt", show_default=True)
@click.option("--batch", "fr_batch_size", type=int, default=16, show_default=True,
              help="Размер батча ASR")
@click.option("--gap", type=float, default=0.5, show_default=True,
              help="Макс. gap для склейки сегментов одного спикера (сек)")
@click.option("--backend", type=click.Choice(["torch", "onnx"]), default="torch",
              show_default=True,
              help="Бэкенд ASR: torch (cpu/mps/cuda) или onnx (cpu/cuda, НЕ mps)")
@click.option("--onnx-int8", is_flag=True,
              help="ONNX с динамической int8-квантизацией (рычаг для CPU-only)")
@click.option("--diar-backend", type=click.Choice(["torch", "onnx"]), default="torch",
              show_default=True,
              help="Бэкенд эмбеддера диаризации: onnx запускает его на CPU "
                   "(сегментация всегда torch). Полезно для CPU-only сервера")
def main(input_file, output, model, diarize, speakers, min_speakers, max_speakers,
         device, diar_device, embedding_batch_size, segmentation_batch_size,
         output_format, fr_batch_size, gap, backend, onnx_int8, diar_backend):
    """GigaAM транскрипция с живым UI и метриками."""
    import torch

    import gigaam
    from gigaam.model import LONGFORM_THRESHOLD
    from gigaam.preprocess import SAMPLE_RATE
    from gigaam.utils import AudioDataset
    from gigaam.vad_utils import segment_audio_file
    from torch.utils.data import DataLoader

    in_path = normalize_path(input_file)
    if not in_path.exists():
        console.print(f"[red]Файл не найден:[/] {input_file}")
        raise SystemExit(1)

    dev = resolve_device(device)
    diar_dev = resolve_device(diar_device) if diar_device else dev
    fp16 = device != "cpu" and dev not in ("cpu", "mps")  # fp16 только на cuda
    if backend == "onnx":
        onnx_dev = "cuda" if dev == "cuda" else "cpu"   # onnxruntime не умеет mps
        asr_label = f"onnx-{onnx_dev}" + ("-int8" if onnx_int8 else "")
    else:
        asr_label = dev
    m = Metrics(model=model, device=asr_label, diarize=diarize)
    ap = AudioProcessor()

    diar_info = f"диаризация: [magenta]{diarize}[/]"
    if diarize != "none":
        diar_info += f" на [magenta]{diar_dev}[/]"
    console.print(Panel.fit(
        f"[bold cyan]GigaAM-UI[/]  ·  файл: [white]{in_path.name}[/]\n"
        f"модель: [magenta]{model}[/]  ·  ASR: [magenta]{asr_label}[/]  ·  "
        f"{diar_info}  ·  формат: [magenta]{output_format}[/]",
        border_style="cyan", box=box.ROUNDED))
    if backend == "onnx" and dev == "mps":
        console.print("[yellow]ONNX Runtime не поддерживает MPS → ASR пойдёт на CPU "
                      "(VAD/диаризация могут использовать MPS отдельно).[/]")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("·"),
        TimeElapsedColumn(),
        TextColumn("ост:"),
        TimeRemainingColumn(),
        console=console,
    )

    temp_audio = None
    try:
        with Live(Dashboard(progress, m), console=console, refresh_per_second=8) as live:
            def refresh():
                live.refresh()

            # --- 1. Подготовка аудио ---
            m.stage = "Подготовка аудио (ffmpeg → WAV 16k mono)"
            t_prep = progress.add_task("Подготовка аудио", total=1)
            refresh()
            if in_path.suffix.lower() != ".wav":
                temp_audio = ap.prepare_for_gigaam(in_path)
                wav = temp_audio
            else:
                wav = in_path
            m.audio_dur = ap.get_duration(wav)
            progress.update(t_prep, completed=1)
            refresh()

            # --- 2. Загрузка модели / подготовка бэкенда ---
            gmodel = onnx_sessions = onnx_cfg = None
            if backend == "onnx":
                from gigaam.onnx_utils import infer_onnx
                from .onnx_backend import ensure_onnx, load_sessions
                m.stage = "ONNX: экспорт/квантизация (разово) + загрузка"
                t_load = progress.add_task("ONNX", total=None)
                refresh()
                onnx_dir, version = ensure_onnx(model, int8=onnx_int8)
                onnx_sessions, onnx_cfg = load_sessions(onnx_dir, version, onnx_dev)
                progress.update(t_load, total=1, completed=1)
                refresh()
            else:
                m.stage = "Загрузка модели GigaAM"
                t_load = progress.add_task("Загрузка модели", total=1)
                refresh()
                gmodel = gigaam.load_model(model, device=dev, fp16_encoder=fp16)
                progress.update(t_load, completed=1)
                refresh()

            trans_segments: List[TranscriptionSegment] = []
            short = m.audio_dur * SAMPLE_RATE <= LONGFORM_THRESHOLD

            if short:
                # --- короткое аудио (<25с) ---
                m.stage = "ASR (короткое аудио)"
                t_asr = progress.add_task("ASR", total=1)
                m.asr_t0 = time.monotonic()
                refresh()
                if backend == "onnx":
                    texts = infer_onnx([str(wav)], onnx_cfg, onnx_sessions,
                                       batch_size=1, progress=False)
                    text = texts[0] if texts else ""
                else:
                    res = gmodel.transcribe(str(wav))
                    text = res.text if hasattr(res, "text") else res
                if text and text.strip():
                    trans_segments.append(TranscriptionSegment(
                        text=text.strip(), start=0.0, end=m.audio_dur))
                m.processed = m.audio_dur
                m.n_segments = m.done_segments = 1
                progress.update(t_asr, completed=1)
                refresh()
            else:
                # --- 3. VAD ---
                m.stage = "VAD: поиск речевых сегментов (pyannote)"
                t_vad = progress.add_task("VAD", total=1)
                refresh()
                vad_dev = "cpu" if (backend == "onnx" and dev == "mps") else dev
                segments, boundaries = segment_audio_file(
                    str(wav), SAMPLE_RATE, device=torch.device(vad_dev))
                progress.update(t_vad, completed=1)
                m.n_segments = len(segments)
                refresh()

                # --- 4. ASR с реальным прогрессом ---
                m.stage = "ASR: распознавание речи"
                t_asr = progress.add_task("ASR", total=max(1, len(segments)))
                m.asr_t0 = time.monotonic()
                refresh()
                idx = 0
                if backend == "onnx":
                    # ONNX: декодирование батчами через infer_onnx (CTC/RNNT внутри)
                    for i in range(0, len(segments), fr_batch_size):
                        chunk = segments[i:i + fr_batch_size]
                        texts = infer_onnx(chunk, onnx_cfg, onnx_sessions,
                                           batch_size=len(chunk), progress=False)
                        for text in texts:
                            s, e = boundaries[idx]
                            idx += 1
                            if text and text.strip():
                                trans_segments.append(TranscriptionSegment(
                                    text=text.strip(), start=s, end=e))
                            m.done_segments = idx
                            m.processed = e
                            progress.update(t_asr, completed=idx)
                        refresh()
                else:
                    ds = AudioDataset(segments, tokenizer=None)
                    dl = DataLoader(ds, batch_size=fr_batch_size, shuffle=False,
                                    collate_fn=AudioDataset.collate, num_workers=0)
                    with torch.inference_mode():
                        for wav_pad, wav_lens in dl:
                            wav_pad = wav_pad.to(gmodel._device).to(gmodel._dtype)
                            wav_lens = wav_lens.to(gmodel._device)
                            encoded, encoded_len = gmodel.forward(wav_pad, wav_lens)
                            for text, _ in gmodel._decode(encoded, encoded_len, wav_lens, False):
                                s, e = boundaries[idx]
                                idx += 1
                                if text and text.strip():
                                    trans_segments.append(TranscriptionSegment(
                                        text=text.strip(), start=s, end=e))
                                m.done_segments = idx
                                m.processed = e
                                progress.update(t_asr, completed=idx)
                            refresh()

            # ASR завершён (для обеих веток) — фиксируем конец для корректной метрики скорости
            m.asr_end = time.monotonic()

            # --- 5. Диаризация (опционально) ---
            if diarize == "pyannote" and trans_segments:
                m.stage = "Диаризация спикеров (pyannote)"
                t_diar = progress.add_task("Диаризация", total=None)
                refresh()
                dm = DiarizationManager(
                    device=diar_dev,
                    embedding_batch_size=embedding_batch_size,
                    segmentation_batch_size=segmentation_batch_size,
                    embedding_backend=diar_backend,
                )
                hook = RichDiarHook(progress, t_diar, m, live)
                spk_kwargs = {}
                if speakers is not None:
                    spk_kwargs["num_speakers"] = speakers
                else:
                    if min_speakers is not None:
                        spk_kwargs["min_speakers"] = min_speakers
                    if max_speakers is not None:
                        spk_kwargs["max_speakers"] = max_speakers
                speaker_segments = dm.diarize(wav, hook=hook, **spk_kwargs)
                trans_segments = dm.map_speakers_to_transcription(
                    trans_segments, speaker_segments)
                m.speakers = len({s.speaker for s in speaker_segments if s.speaker})
                progress.update(t_diar, total=1, completed=1, description="Диаризация: готово")
                refresh()

            # --- 6. Склейка + сохранение ---
            m.stage = "Склейка сегментов и сохранение"
            refresh()
            if trans_segments:
                merger = SegmentMerger(MergeConfig(max_gap=gap))
                trans_segments = merger.merge_same_speaker_segments(trans_segments, max_gap=gap)

            result = TranscriptionResult(
                text=" ".join(s.text for s in trans_segments),
                segments=trans_segments,
                duration=m.audio_dur,
                language="ru",
                model_name=model,
                processing_time=m.elapsed,
                metadata={"source": str(in_path),
                          "device": dev, "diarization": diarize},
            )
            if output:
                save_result(result, output, output_format)
            m.stage = "Готово ✓"
            refresh()

    finally:
        if temp_audio and Path(temp_audio).exists() and temp_audio != in_path:
            try:
                Path(temp_audio).unlink()
            except Exception:
                pass

    # --- Финальная сводка ---
    speed = (m.audio_dur / m.elapsed) if m.elapsed else 0
    summary = Table(title="✅ Транскрипция завершена", box=box.ROUNDED, show_header=False)
    summary.add_column(style="bold cyan", justify="right")
    summary.add_column(style="white")
    summary.add_row("Время обработки", f"{fmt_time(m.elapsed)} ({m.elapsed:.0f} с)")
    summary.add_row("Длительность аудио", fmt_time(m.audio_dur))
    summary.add_row("Скорость", f"{speed:.2f}× реального времени  (RTF={1/speed:.2f})" if speed else "—")
    summary.add_row("Сегментов", str(len(result.segments)))
    if diarize != "none":
        spk = result.get_speakers()
        summary.add_row("Спикеры", ", ".join(spk) if spk else "—")
    if output:
        summary.add_row("Сохранено", str(output))
    console.print(summary)

    # Превью
    preview = result.to_txt()[:800]
    console.print(Panel(preview + ("…" if len(result.to_txt()) > 800 else ""),
                        title="Превью", border_style="dim"))


if __name__ == "__main__":
    main()
