#!/usr/bin/env python3
"""
dialogscribe — единый CLI поверх библиотеки ``gigaam_transcriber`` (milestone M1).

Тонкая презентационная оболочка: каждая команда зовёт ровно методы библиотеки
(`transcribe`, `transcribe_batch`, `discover_route_a_tracks`/`transcribe_route_a`,
voiceprint-галереи). Никакого собственного декод-цикла — единый источник истины
по пайплайну остаётся в `gigaam_transcriber`.

Контракт потоков: stdout несёт ТОЛЬКО машинный результат (txt/json/srt/vtt при
отсутствии `-o`); всё декоративное — баннеры, summary, предупреждения, прогресс —
идёт в stderr. Это позволяет `dialogscribe transcribe a.m4a -f json > out.json`
и `route-a … -f json > out.json` давать валидный файл.

Команды:
    dialogscribe transcribe <input> [...]      # один файл
    dialogscribe batch <inputs...> -o OUTDIR   # пакет (progress_callback)
    dialogscribe route-a <folder> [...]        # подорожечно, без HF_TOKEN
    dialogscribe gallery build|list|rm         # голосовые галереи (voiceprint)
    dialogscribe serve [--host --port]         # web-сервер (появится в M2)

Заменяет три легаси-точки (gigaam-transcribe / -batch / -ui), которые на один
релиз остаются алиасами.
"""

import os

# MPS: неподдержанные ops уходят на CPU вместо краша. Ставим до импорта torch.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import functools
import re
import sys
import unicodedata
from pathlib import Path

import click

# Импорт пакета триггерит автозагрузку .env (HF_TOKEN для VAD/диаризации).
from gigaam_transcriber import GigaAMTranscriber, TranscriberError, __version__


# --------------------------------------------------------------------------- #
# Утилиты путей (NFC/NFD — macOS HFS+) и форматирование
# --------------------------------------------------------------------------- #
def normalize_path(path: str) -> Path:
    """Нормализация пути для обхода NFD/NFC-расхождений (macOS HFS+)."""
    p = Path(unicodedata.normalize("NFC", str(path)))
    if p.exists():
        return p
    parent = p.parent
    if parent.exists():
        for f in parent.iterdir():
            if unicodedata.normalize("NFC", f.name) == p.name:
                return f
            if unicodedata.normalize("NFD", f.name) == unicodedata.normalize("NFD", p.name):
                return f
    return p  # не нашли — вернём как есть, Click сам выдаст ошибку существования


class UnicodePathType(click.Path):
    """Click-Path с предварительной NFC/NFD-нормализацией пути."""

    def convert(self, value, param, ctx):
        normalized = normalize_path(value)
        if normalized.exists():
            return str(normalized)
        return super().convert(value, param, ctx)


def format_duration(seconds: float) -> str:
    """Секунды → человекочитаемая длительность."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}ч {minutes}м {secs}с"
    if minutes > 0:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


# --------------------------------------------------------------------------- #
# Вывод: всё декоративное — в stderr; stdout зарезервирован под машинный результат
# --------------------------------------------------------------------------- #
def _eecho(message: str = "", **kw) -> None:
    click.echo(message, err=True, **kw)


def _esecho(message: str, **kw) -> None:
    click.secho(message, err=True, **kw)


def _err_console():
    from rich.console import Console

    return Console(stderr=True)


def _progress_enabled(verbose: bool) -> bool:
    """Живой rich-прогресс уместен только в интерактивном (tty) не-verbose режиме."""
    return not verbose and sys.stderr.isatty()


# --------------------------------------------------------------------------- #
# Прогресс (общий ProgressHook поверх progress_callback L4) — рендер в stderr
# --------------------------------------------------------------------------- #
class _RichProgress:
    """Адаптер rich.Progress под `progress_callback(current, total, name)`.

    Используется на batch/route-a путях (единственный sub-сигнал прогресса,
    который библиотека отдаёт через L4). В не-tty/verbose сворачивается в
    построчный echo на stderr — чтобы пайпы/CI всё же видели ход, а stdout
    оставался чистым каналом данных.
    """

    def __init__(self, description: str, use_rich: bool):
        self.description = description
        self.use_rich = use_rich
        self._progress = None
        self._task = None

    def __enter__(self):
        if self.use_rich:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
            )

            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=_err_console(),
            )
            self._progress.start()
        return self

    def __exit__(self, *exc):
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        return False

    def callback(self, current: int, total: int, name: str) -> None:
        if self._progress is None:
            _eecho(f"[{current}/{total}] {name}")  # построчно, в stderr
            return
        if self._task is None:
            self._task = self._progress.add_task(self.description, total=total)
        self._progress.update(
            self._task, completed=current, description=f"{self.description}: {name}"
        )


def _spinner(message: str, use_rich: bool):
    """Spinner для single-file пути (у transcribe() нет progress_callback)."""
    if not use_rich:
        from contextlib import nullcontext

        return nullcontext()
    return _err_console().status(message, spinner="dots")


# --------------------------------------------------------------------------- #
# Обёртка обработки ошибок (единый exit-контракт всех команд)
# --------------------------------------------------------------------------- #
def guarded(func):
    """Декоратор: TranscriberError→1, Ctrl-C→130, прочее→1 (+traceback при -v / env).

    Traceback включается флагом -v у команд, которые его имеют, либо переменной
    окружения DIALOGSCRIBE_TRACEBACK (для команд без -v, напр. gallery).
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        verbose = bool(kwargs.get("verbose")) or bool(os.getenv("DIALOGSCRIBE_TRACEBACK"))
        try:
            return func(*args, **kwargs)
        except (click.ClickException, click.Abort):
            raise  # Click сам печатает и выставляет код выхода (UsageError → 2)
        except TranscriberError as e:
            _esecho(f"❌ Ошибка: {e}", fg="red")
            sys.exit(1)
        except KeyboardInterrupt:
            _esecho("\n⚠️  Прервано пользователем", fg="yellow")
            sys.exit(130)
        except Exception as e:  # noqa: BLE001 — финальный барьер CLI
            _esecho(f"❌ Неожиданная ошибка: {e}", fg="red")
            if verbose:
                import traceback

                traceback.print_exc()
            sys.exit(1)

    return wrapper


def _warn_missing_hf_token(diarize: str) -> None:
    if diarize != "none" and not os.getenv("HF_TOKEN"):
        _esecho(
            "⚠️  HF_TOKEN не установлен — диаризация требует токен HuggingFace "
            "(export HF_TOKEN=<токен>).",
            fg="yellow",
        )


def _print_result_summary(result, output, quiet: bool) -> None:
    if quiet:
        return
    _eecho("")
    _esecho("✅ Транскрипция завершена!", fg="green")
    _eecho(f"⏱️  Время обработки: {result.processing_time:.1f} сек")
    _eecho(f"📊 Длительность аудио: {format_duration(result.duration)}")
    _eecho(f"📝 Сегментов: {len(result.segments)}")
    speakers = result.get_speakers()
    if speakers:
        _eecho(f"👥 Спикеры: {', '.join(speakers)}")
    if result.metadata.get("device_fallback"):
        _esecho("⚠️  Устройство откатилось GPU→CPU (медленно).", fg="yellow")
    if output:
        _eecho(f"💾 Сохранено в: {output}")


def _emit_to_stdout(result, output_format: str) -> None:
    """Машинный результат — в stdout (единственное, что туда пишется)."""
    if output_format == "txt":
        click.echo(result.to_txt())
    elif output_format == "json":
        click.echo(result.to_json())
    elif output_format == "srt":
        click.echo(result.to_srt())
    elif output_format == "vtt":
        click.echo(result.to_vtt())


# --------------------------------------------------------------------------- #
# Общие опции качества/бэкендов — единый источник для transcribe и batch (паритет)
# --------------------------------------------------------------------------- #
def quality_options(func):
    """Навешивает общий набор opt-in флагов на transcribe и batch.

    Гарантирует, что обе команды выставляют один и тот же набор флагов слоя
    качества/бэкендов/тюнинга диаризации (DoD M1). resume/manifest специфичны
    для одиночного файла и живут только на `transcribe`.
    """
    options = [
        click.option("-m", "--model", default="v3_e2e_rnnt", help="Модель GigaAM"),
        click.option(
            "-d",
            "--diarize",
            type=click.Choice(["none", "pyannote", "hybrid"]),
            default="none",
            help="Режим диаризации спикеров",
        ),
        click.option("--speakers", type=int, default=None, help="Точное число спикеров"),
        click.option("--min-speakers", type=int, default=None, help="Минимум спикеров"),
        click.option("--max-speakers", type=int, default=None, help="Максимум спикеров"),
        click.option(
            "-f",
            "--format",
            "output_format",
            type=click.Choice(["txt", "json", "srt", "vtt"]),
            default="txt",
            help="Формат вывода",
        ),
        click.option("--no-merge", is_flag=True, help="Не склеивать сегменты одного спикера"),
        click.option("--gap", type=float, default=0.5, help="Макс. пауза склейки (сек)"),
        click.option(
            "--glossary/--no-glossary",
            default=True,
            help="Канонизация имён/терминов (по умолчанию включена)",
        ),
        click.option("--second-opinion", is_flag=True, help="L2 faster-whisper fusion (opt-in)"),
        click.option("--voiceprint", is_flag=True, help="Именование спикеров по галерее ECAPA"),
        click.option(
            "--gallery",
            "voiceprint_gallery",
            type=click.Path(),
            default=None,
            help="Путь к галерее голосов (для --voiceprint)",
        ),
        click.option("--preclean", is_flag=True, help="highpass+loudnorm перед ASR"),
        click.option(
            "--backend",
            type=click.Choice(["torch", "onnx"]),
            default="torch",
            help="Бэкенд декода (onnx = CPU/CUDA, без confidence)",
        ),
        click.option("--onnx-int8", is_flag=True, help="int8-квантизация ONNX (CPU-рычаг)"),
        click.option(
            "--onnx-encoder",
            is_flag=True,
            help="split-device: ONNX-энкодер CPU + torch-голова",
        ),
        click.option("--word-timestamps", is_flag=True, help="Пословные таймкоды"),
        click.option("--emit-l0", is_flag=True, help="L0-субстрат transcript.v1.jsonl + sha256"),
        click.option(
            "--device",
            type=click.Choice(["auto", "cuda", "cpu", "mps"]),
            default="auto",
            help="Устройство (mps — Apple GPU, экспериментально)",
        ),
        click.option(
            "--diar-device",
            type=click.Choice(["auto", "cuda", "cpu", "mps"]),
            default=None,
            help="Отдельное устройство для диаризации (mps ускоряет эмбеддинги ~10× на Apple Silicon)",
        ),
        click.option(
            "--embedding-batch-size",
            type=int,
            default=None,
            help="Размер батча извлечения эмбеддингов диаризации",
        ),
        click.option(
            "--segmentation-batch-size",
            type=int,
            default=None,
            help="Размер батча сегментации диаризации",
        ),
        click.option(
            "--diar-backend",
            type=click.Choice(["torch", "onnx"]),
            default="torch",
            help="Бэкенд эмбеддера диаризации (onnx форсит эмбеддер на CPU)",
        ),
        click.option("-v", "--verbose", is_flag=True, help="Подробный вывод"),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _make_transcriber(model, device, verbose, diar_device, embedding_batch_size,
                      segmentation_batch_size, diar_backend):
    return GigaAMTranscriber(
        model_name=model,
        device=device,
        verbose=verbose,
        diar_device=diar_device,
        embedding_batch_size=embedding_batch_size,
        segmentation_batch_size=segmentation_batch_size,
        embedding_backend=diar_backend,
    )


# --------------------------------------------------------------------------- #
# Группа
# --------------------------------------------------------------------------- #
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="dialogscribe")
def cli():
    """DialogScribe — транскрипция диалогов поверх GigaAM (CLI)."""


# --------------------------------------------------------------------------- #
# transcribe — один файл
# --------------------------------------------------------------------------- #
@cli.command()
@click.argument("input_file", type=UnicodePathType(exists=True))
@click.option("-o", "--output", type=click.Path(), help="Путь к выходному файлу")
@quality_options
@click.option("--resume", is_flag=True, help="Пропуск ASR по хэшу файла (resume)")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(),
    default=None,
    help="Путь манифеста (иначе рядом с output)",
)
@click.option("-q", "--quiet", is_flag=True, help="Тихий режим — только результат")
@guarded
def transcribe(
    input_file,
    output,
    model,
    diarize,
    speakers,
    min_speakers,
    max_speakers,
    output_format,
    no_merge,
    gap,
    glossary,
    second_opinion,
    voiceprint,
    voiceprint_gallery,
    preclean,
    backend,
    onnx_int8,
    onnx_encoder,
    word_timestamps,
    emit_l0,
    device,
    diar_device,
    embedding_batch_size,
    segmentation_batch_size,
    diar_backend,
    verbose,
    resume,
    manifest_path,
    quiet,
):
    """Транскрибировать один аудио/видео файл."""
    _warn_missing_hf_token(diarize)
    with _make_transcriber(
        model, device, verbose, diar_device, embedding_batch_size,
        segmentation_batch_size, diar_backend,
    ) as transcriber:
        with _spinner("Транскрипция…", use_rich=not quiet and _progress_enabled(verbose)):
            result = transcriber.transcribe(
                input_file,
                output_path=output,
                diarization=diarize,
                num_speakers=speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                output_format=output_format,
                merge_same_speaker=not no_merge,
                min_segment_gap=gap,
                glossary=glossary,
                second_opinion=second_opinion,
                voiceprint=voiceprint,
                voiceprint_gallery=voiceprint_gallery,
                preclean=preclean,
                backend=backend,
                onnx_int8=onnx_int8,
                onnx_encoder=onnx_encoder,
                word_timestamps=word_timestamps,
                resume=resume,
                manifest_path=manifest_path,
                emit_l0=emit_l0,
            )
    _print_result_summary(result, output, quiet)
    if not output:
        _emit_to_stdout(result, output_format)


# --------------------------------------------------------------------------- #
# batch — пакет файлов (паритет opt-in флагов с transcribe)
# --------------------------------------------------------------------------- #
@cli.command()
@click.argument("input_files", nargs=-1, type=UnicodePathType(exists=True))
@click.option(
    "-o", "--output-dir", type=click.Path(), help="Директория для результатов"
)
@quality_options
@guarded
def batch(
    input_files,
    output_dir,
    model,
    diarize,
    speakers,
    min_speakers,
    max_speakers,
    output_format,
    no_merge,
    gap,
    glossary,
    second_opinion,
    voiceprint,
    voiceprint_gallery,
    preclean,
    backend,
    onnx_int8,
    onnx_encoder,
    word_timestamps,
    emit_l0,
    device,
    diar_device,
    embedding_batch_size,
    segmentation_batch_size,
    diar_backend,
    verbose,
):
    """Пакетная транскрипция нескольких файлов (resume/manifest — только в transcribe)."""
    if not input_files:
        raise click.UsageError("Не указаны файлы для обработки.")
    _warn_missing_hf_token(diarize)
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    with _make_transcriber(
        model, device, verbose, diar_device, embedding_batch_size,
        segmentation_batch_size, diar_backend,
    ) as transcriber:
        with _RichProgress("Пакет", use_rich=_progress_enabled(verbose)) as progress:
            results = transcriber.transcribe_batch(
                list(input_files),
                output_dir=output_dir,
                diarization=diarize,
                output_format=output_format,
                num_speakers=speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                merge_same_speaker=not no_merge,
                min_segment_gap=gap,
                glossary=glossary,
                second_opinion=second_opinion,
                voiceprint=voiceprint,
                voiceprint_gallery=voiceprint_gallery,
                preclean=preclean,
                backend=backend,
                onnx_int8=onnx_int8,
                onnx_encoder=onnx_encoder,
                word_timestamps=word_timestamps,
                emit_l0=emit_l0,
                progress_callback=progress.callback,
            )
    successful = sum(1 for r in results if r.text)
    failed = len(results) - successful
    _eecho("")
    _esecho(f"✅ Успешно: {successful}", fg="green")
    if failed:
        _esecho(f"❌ Ошибок: {failed}", fg="red")


# --------------------------------------------------------------------------- #
# route-a — подорожечно (ground-truth имена, без HF_TOKEN)
# --------------------------------------------------------------------------- #
@cli.command("route-a")
@click.argument("folder", type=UnicodePathType(exists=True))
@click.option("-o", "--output", type=click.Path(), help="Путь к выходному файлу")
@click.option(
    "--speaker-dir",
    default="Audio Record",
    help="Поддиректория с дорожками участников",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    default="txt",
    help="Формат вывода",
)
@click.option("--glossary/--no-glossary", default=True, help="Канонизация имён/терминов")
@click.option("--gap", type=float, default=0.5, help="Макс. пауза склейки (сек)")
@click.option("-m", "--model", default="v3_e2e_rnnt", help="Модель GigaAM")
@click.option(
    "--device",
    type=click.Choice(["auto", "cuda", "cpu", "mps"]),
    default="auto",
    help="Устройство",
)
@click.option("-q", "--quiet", is_flag=True, help="Тихий режим — только результат")
@click.option("-v", "--verbose", is_flag=True, help="Подробный вывод")
@guarded
def route_a(folder, output, speaker_dir, output_format, glossary, gap, model, device, quiet, verbose):
    """Транскрипция подорожечной записи: спикер = имя дорожки (без диаризации)."""
    tracks = GigaAMTranscriber.discover_route_a_tracks(folder, speaker_dir=speaker_dir)
    if not tracks:
        raise click.UsageError(
            f"Дорожки участников не найдены в {folder!r} "
            f"(ожидалась поддиректория {speaker_dir!r} с *.m4a)."
        )
    if not quiet:
        _eecho(f"🎚️  Найдено дорожек: {len(tracks)} — {', '.join(tracks)}")
    with GigaAMTranscriber(model_name=model, device=device, verbose=verbose) as transcriber:
        with _RichProgress("Дорожки", use_rich=not quiet and _progress_enabled(verbose)) as progress:
            result = transcriber.transcribe_route_a(
                tracks,
                output_path=output,
                output_format=output_format,
                glossary=glossary,
                min_segment_gap=gap,
                progress_callback=progress.callback,
            )
    failed = result.metadata.get("failed_tracks") or []
    if failed and not quiet:
        _esecho(
            f"⚠️  Пропущено дорожек: {len(failed)} "
            f"({', '.join(f['name'] for f in failed)})",
            fg="yellow",
        )
    _print_result_summary(result, output, quiet)
    if not output:
        _emit_to_stdout(result, output_format)


# --------------------------------------------------------------------------- #
# gallery — голосовые галереи (voiceprint)
# --------------------------------------------------------------------------- #
def _gallery_dir() -> Path:
    """Каталог хранения галерей (env DIALOGSCRIBE_GALLERY_DIR или ~/.cache)."""
    env = os.getenv("DIALOGSCRIBE_GALLERY_DIR")
    base = (
        Path(env)
        if env
        else Path.home() / ".cache" / "gigaam_transcriber" / "galleries"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


_SAFE_GALLERY_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _gallery_path(name: str) -> Path:
    """Безопасный путь галереи: имя — слаг, без разделителей/`..`/абсолютных путей."""
    if not _SAFE_GALLERY_NAME.match(name or ""):
        raise click.UsageError(
            f"Недопустимое имя галереи: {name!r} "
            "(разрешены буквы/цифры/_/-, без путей и точек)."
        )
    return _gallery_dir() / f"{name}.json"


@cli.group()
def gallery():
    """Управление галереями голосов для --voiceprint."""


@gallery.command("build")
@click.argument("name")
@click.option(
    "--track",
    "tracks_raw",
    multiple=True,
    required=True,
    metavar="LABEL=PATH",
    help="Дорожка участника (повторяемо): метка=путь",
)
@guarded
def gallery_build(name, tracks_raw):
    """Построить галерею из дорожек: gallery build team --track Алиса=a.m4a ..."""
    from gigaam_transcriber.voiceprint import build_gallery_from_tracks, save_gallery

    out = _gallery_path(name)  # валидирует имя ДО тяжёлой работы
    tracks = {}
    for item in tracks_raw:
        if "=" not in item:
            raise click.UsageError(f"Ожидался формат LABEL=PATH, получено: {item!r}")
        label, _, path = item.partition("=")
        label = label.strip()
        if not label:
            raise click.UsageError(f"Пустая метка в {item!r}")
        tracks[label] = str(normalize_path(path.strip()))
    refs = build_gallery_from_tracks(tracks)
    if not refs:
        raise click.UsageError(
            "Не удалось построить ни одного эмбеддинга (пустые/битые дорожки)."
        )
    save_gallery(refs, out)
    _esecho(f"✅ Галерея '{name}' сохранена: {out}", fg="green")
    _eecho(f"👥 Голоса: {', '.join(refs)}")


@gallery.command("list")
@guarded
def gallery_list():
    """Список доступных галерей."""
    from gigaam_transcriber.voiceprint import load_gallery

    files = sorted(_gallery_dir().glob("*.json"))
    if not files:
        _eecho("Галерей нет.")
        return
    for f in files:
        refs, _theta, _margin = load_gallery(f)
        _eecho(f"  {f.stem}  ({len(refs)} голосов: {', '.join(refs)})")


@gallery.command("rm")
@click.argument("name")
@guarded
def gallery_rm(name):
    """Удалить галерею по имени."""
    target = _gallery_path(name)
    if not target.exists():
        raise click.UsageError(f"Галерея '{name}' не найдена ({target}).")
    target.unlink()
    _esecho(f"🗑️  Галерея '{name}' удалена.", fg="green")


# --------------------------------------------------------------------------- #
# serve — заглушка (реальный сервер в M2)
# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--host", default="127.0.0.1", help="Хост для прослушивания")
@click.option("--port", default=8000, type=int, help="Порт")
@click.option("--reload", is_flag=True, help="Авто-перезапуск при изменениях (dev)")
def serve(host, port, reload):
    """Dev-лаунчер web-API (uvicorn). Прод — через nginx+compose (deploy/).

    Поднимает только процесс api (без модели). gpu/io-воркеры запускаются
    отдельно (см. deploy/docker-compose.yml). Web-SPA подключится в M4.
    """
    try:
        import uvicorn  # noqa: F401

        from gigaam_transcriber.server.config import Settings
    except ImportError:
        _esecho(
            "Серверные зависимости не установлены. Установите: "
            "pip install -e '.[server]'",
            fg="red",
        )
        sys.exit(1)
    settings = Settings.from_env()
    problems = settings.validate_for_serve()
    if problems:
        for p in problems:
            _esecho(f"⚠️  {p}", fg="yellow")
        _esecho(
            "Задайте обязательные переменные окружения (см. deploy/.env.example) "
            "перед запуском.",
            fg="red",
        )
        sys.exit(1)
    _eecho(
        f"DialogScribe API → http://{host}:{port}  "
        "(dev-режим; прод — за nginx по TLS через compose)"
    )
    import uvicorn

    uvicorn.run(
        "gigaam_transcriber.server.app:create_app",
        host=host,
        port=port,
        factory=True,
        reload=reload,
    )


def main():
    """Entry point для console-script `dialogscribe`."""
    cli()


if __name__ == "__main__":
    cli()
