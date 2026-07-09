"""Локальный watch-конвейер: папка Zoom-выгрузок → авто-транскрипция.

Близнец Яндекс-авто-watch поверх локальной ФС (LOCAL_WATCH_DESIGN §3, §5):
тот же каркас claim/stability/recording/job, но БЕЗ download — дорожки
остаются в папке встречи. Раскладка источника и место вывода настраиваются
профилем (`ScanProfile`, пресеты «Zoom»/«Простая папка»/свои): вывод — рядом
с записью (`<встреча>/transcripts/bloodtranscripts[/Часть N]`, namespace не
затирает чужой `transcripts/`) или в отдельную папку.

Дедуп — по `magic_number` встречи (`local:<magic>#p<N>[#t:<имя>]`): устойчив
к переименованию папки и mtime-джиттеру повторного rsync (сигнатура папки
служит только барьером стабильности, НЕ идентичностью — урок F7). Смена
профиля НЕ пере-ингестит обработанные встречи (ключ профиль не включает);
упавшая джоба переклеймивается (`reclaim_ingest_if_job_failed`).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import media
from .config import Settings
from .ingest_common import STABILITY_THRESHOLD, register_job
from .repository import (
    claim_ingest,
    finish_job_ok,
    get_ingest_source,
    new_id,
    reclaim_ingest_if_job_failed,
    record_stability,
    update_ingest,
)
from .zoom_scan import (
    DEFAULT_PROFILE,
    Meeting,
    MeetingPart,
    ScanProfile,
    folder_signature,
    scan_meeting,
)

logger = logging.getLogger("bloodtranscripts.jobs")

# Серверный allowlist локального watch (аналог BLOODTRANSCRIPTS_YANDEX_WATCH_DIR):
# "/" — без ограничения (dev); в проде сузить до корня с записями.
LOCAL_WATCH_ROOT_ENV = "BLOODTRANSCRIPTS_LOCAL_WATCH_ROOT"


def profile_from_source(src: dict | None) -> ScanProfile:
    """Профиль из строки источника: `scan_profile` — JSON, пусто → дефолты Zoom."""
    if not src:
        return DEFAULT_PROFILE
    try:
        return ScanProfile.from_dict(json.loads(src.get("scan_profile") or "{}"))
    except ValueError:
        return DEFAULT_PROFILE


def _watch_root() -> Path:
    return Path(os.getenv(LOCAL_WATCH_ROOT_ENV, "/")).expanduser().resolve()


def _outside_root(target: Path, root: Path) -> bool:
    return root != Path("/") and target != root and root not in target.parents


def validate_watch_dir(settings: Settings, watch_dir: str) -> str | None:
    """Ошибка конфигурации локальной папки или None, если всё в порядке."""
    p = Path(watch_dir).expanduser()
    if not p.is_absolute():
        return "Путь должен быть абсолютным"
    target = p.resolve()
    # Allowlist — ДО обращений к ФС: ответ про путь вне разрешённой области не
    # должен зависеть от его существования (иначе оракул серверных путей).
    if _outside_root(target, _watch_root()):
        return "Папка вне разрешённой области наблюдения"
    # Единое сообщение для «нет»/«нет прав» — тоже анти-оракул.
    if not p.is_dir() or not os.access(p, os.R_OK):
        return "Папка недоступна для чтения"
    data_dir = Path(settings.data_dir).resolve()
    # watch_dir не должен пересекаться с data_dir: свой вывод/загрузки
    # пере-детектились бы как новые встречи.
    if target == data_dir or data_dir in target.parents or target in data_dir.parents:
        return "Папка не должна пересекаться с рабочей директорией сервера"
    return None


def validate_output_profile(settings: Settings, watch_dir: str, profile: ScanProfile) -> str | None:
    """Ошибка настройки вывода профиля или None. Для fixed-режима папка может
    не существовать (будет создана), но путь обязан быть валидным и не
    пересекаться с watch_dir (вывод пере-детектился бы как встречи)."""
    if profile.output_mode == "beside":
        sub = Path(profile.output_subdir)
        if sub.is_absolute() or ".." in sub.parts:
            return "Подпапка вывода — относительный путь без «..»"
        return None
    if not profile.output_dir:
        return "Укажите папку для транскриптов"
    out = Path(profile.output_dir).expanduser()
    if not out.is_absolute():
        return "Путь к папке транскриптов должен быть абсолютным"
    out = out.resolve()
    # Тот же allowlist, что для watch_dir: воркер делает mkdir и пишет файлы —
    # без гарда это примитив записи в произвольный путь серверной ФС.
    if _outside_root(out, _watch_root()):
        return "Папка транскриптов вне разрешённой области"
    watch = Path(watch_dir).expanduser().resolve()
    if out == watch or watch in out.parents:
        return "Папка транскриптов не должна лежать внутри наблюдаемой папки"
    data_dir = Path(settings.data_dir).resolve()
    if out == data_dir or data_dir in out.parents:
        return "Папка транскриптов не должна пересекаться с рабочей директорией сервера"
    # Пробное создание СЕЙЧАС: молча принятый несоздаваемый путь (read-only корень
    # «/transcripts» на macOS, выбитый диск) ронял бы каждую джобу на mkdir.
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        return f"Не удалось создать папку транскриптов: {out} (нет прав или read-only)"
    return None


@dataclass
class _Unit:
    """Единица обработки после применения track_mode: одна будущая джоба."""

    kind: str  # route_a | single
    tracks: list[dict]
    title: str
    out_dir: Path
    force_no_diarization: bool = False  # separate: одна дорожка = один голос


def _base_output_dir(meeting: Meeting, part_index: int, profile: ScanProfile) -> Path:
    """Раскладка НЕ зависит от числа частей на момент скана: часть 1 — всегда
    корень, часть N≥2 — всегда подпапка (иначе часть 2, появившаяся ПОСЛЕ
    ингеста части 1, сдвигала бы уже написанный вывод)."""
    if profile.output_mode == "fixed" and profile.output_dir:
        base = Path(profile.output_dir).expanduser() / meeting.title
    else:
        base = meeting.folder / Path(profile.output_subdir)
    if part_index > 1:
        base = base / f"Часть {part_index}"
    return base


def _part_title(meeting: Meeting, part_index: int) -> str:
    return meeting.title if part_index == 1 else f"{meeting.title} — Часть {part_index}"


def _units_for_part(meeting: Meeting, part: MeetingPart, profile: ScanProfile) -> list[_Unit]:
    """Применить track_mode к части: во что она разворачивается по джобам.

    ВСЕ режимы делят один ключ дедупа `local:<magic>#p<N>` — переключение
    режима не пере-ингестит уже обработанные встречи (обещание докстринга и UI).
    separate создаёт несколько джоб под ОДНИМ claim."""
    base_out = _base_output_dir(meeting, part.index, profile)
    title = _part_title(meeting, part.index)

    has_participants = part.kind == "route_a" and len(part.tracks) > 0
    mode = profile.track_mode

    if mode == "mix_only" and part.mix_path:
        mix = Path(part.mix_path)
        track = {"name": title, "path": str(mix), "size": mix.stat().st_size}
        return [_Unit(kind="single", tracks=[track], title=title, out_dir=base_out)]
    # mix_only без микса деградирует до combine (лучше транскрибировать, чем молчать).

    if mode == "separate" and has_participants:
        units = []
        for t in part.tracks:
            units.append(
                _Unit(
                    kind="single",
                    tracks=[t],
                    title=f"{title} — {t['name']}",
                    out_dir=base_out / t["name"],
                    force_no_diarization=True,  # одна дорожка = один голос
                )
            )
        return units

    # combine (дефолт): route_a при подорожках, иначе single-микс.
    return [_Unit(kind=part.kind, tracks=part.tracks, title=title, out_dir=base_out)]


def _part_duration(part: MeetingPart) -> float:
    """Длительность части: по миксу (надёжно), иначе по самой длинной дорожке."""
    if part.mix_path:
        d = media.probe_duration(Path(part.mix_path))
        if d:
            return d
    durs = [media.probe_duration(Path(t["path"])) or 0.0 for t in part.tracks]
    return max(durs, default=0.0)


def _build_merged_tracks(
    meeting: Meeting, parts: list[MeetingPart], work: Path
) -> tuple[str, list[dict]]:
    """Склеить части встречи в единый набор дорожек (глобальный таймлайн).

    Дорожки Zoom выровнены к началу части и имеют её полную длительность,
    поэтому конкатенация частей по УЧАСТНИКУ корректна: отсутствие участника в
    части → тишина её длительности, перезаходы (несколько файлов) → amix.
    Все части с подорожками → route_a; иначе склеиваются миксы → single."""
    durations = [_part_duration(p) for p in parts]
    if all(p.kind == "route_a" and p.tracks for p in parts):
        names: list[str] = []  # порядок первого появления участника
        for p in parts:
            for t in p.tracks:
                base = t.get("base", t["name"])
                if base not in names:
                    names.append(base)
        tracks = []
        for name in names:
            seq: list[tuple[list[Path], float]] = []
            for p, dur in zip(parts, durations, strict=True):
                files = [Path(t["path"]) for t in p.tracks if t.get("base", t["name"]) == name]
                seq.append((files, dur))
            dst = work / f"{name}.m4a"
            media.concat_track_parts(seq, dst)
            tracks.append({"name": name, "path": str(dst), "size": dst.stat().st_size})
        return ("route_a" if len(tracks) > 1 else "single"), tracks
    seq = [
        ([Path(p.mix_path)] if p.mix_path else [], d) for p, d in zip(parts, durations, strict=True)
    ]
    dst = work / "mix.m4a"
    media.concat_track_parts(seq, dst)
    return "single", [{"name": meeting.title, "path": str(dst), "size": dst.stat().st_size}]


def _create_job_for_unit(
    settings: Settings, unit: _Unit, default_params: dict, enqueue_gpu, work_dir: str
) -> str:
    """Общий хвост ingestion-а: recording → job → dirs → enqueue (см. register_job)."""
    params: dict = {"glossary": True, "emit_l0": True, **default_params}
    if unit.kind == "single":
        if unit.force_no_diarization:
            params["diarization"] = "none"
        else:
            params.setdefault("diarization", "pyannote" if os.getenv("HF_TOKEN") else "none")
    _rec_id, job_id = register_job(
        settings,
        origin="local",
        kind=unit.kind,
        tracks=unit.tracks,
        title=unit.title,
        params=params,
        output_dir=unit.out_dir,
        work_dir=work_dir,
        enqueue_gpu=enqueue_gpu,
    )
    return job_id


def _import_existing_transcript(
    settings: Settings,
    meeting: Meeting,
    part: MeetingPart,
    profile: ScanProfile,
    default_params: dict,
) -> str | None:
    """Прескан: готовая транскрибация в папке вывода → регистрация как done-джоба.

    Паттерн — result.json по раскладке профиля источника (_base_output_dir:
    transcripts/bloodtranscripts[/Часть N] или fixed-папка). Найден и читается →
    recording + job(state=done) без транскрипции (переустановка/перенос архива
    не пережёвывает GPU уже сделанное). Битый/нечитаемый result.json → None
    (честная транскрипция). Возвращает job_id или None."""

    # Кандидаты-раскладки по убыванию приоритета: текущий профиль, затем известные
    # раскладки прежних версий/инструментов В ПАПКЕ ВСТРЕЧИ — прескан не должен
    # зависеть от того, как сейчас настроен вывод (fixed-профиль искал бы только
    # в своей папке и пере-жёвывал архив, транскрибированный по-старому).
    def _with_part(base: Path) -> Path:
        return base / f"Часть {part.index}" if part.index > 1 else base

    candidates = [
        _base_output_dir(meeting, part.index, profile),
        _with_part(meeting.folder / "transcripts" / "bloodtranscripts"),
        _with_part(meeting.folder / "transcripts" / "dialogscribe"),  # до ребрендинга
        _with_part(meeting.folder / "transcripts"),  # плоская раскладка ранних прогонов
    ]
    out_dir = next((d for d in candidates if (d / "result.json").exists()), None)
    if out_dir is None:
        return None
    result_json = out_dir / "result.json"
    try:
        meta = (json.loads(result_json.read_text(encoding="utf-8")) or {}).get("metadata") or {}
    except (OSError, ValueError):
        logger.warning("прескан: битый result.json в %s — встреча уйдёт в транскрипцию", out_dir)
        return None

    title = _part_title(meeting, part.index)
    if part.kind == "route_a" and part.tracks:
        kind, tracks = "route_a", part.tracks
    elif part.mix_path:
        mix = Path(part.mix_path)
        kind = "single"
        tracks = [{"name": title, "path": str(mix), "size": mix.stat().st_size}]
    else:
        return None  # нет исходников — регистрировать нечего (job без дорожек бесполезна)

    _rec_id, job_id = register_job(
        settings,
        origin="local",
        kind=kind,
        tracks=tracks,
        title=title,
        params={**default_params, "imported": True},
        output_dir=out_dir,
        work_dir=str(meeting.folder),
        enqueue_gpu=None,  # без очереди: результат уже на диске
    )
    audio = out_dir / "audio.m4a"
    finish_job_ok(
        settings.db_path,
        job_id,
        result_json_path=str(result_json),
        audio_path=str(audio) if audio.exists() else None,
        duration_sec=float(meta.get("duration") or 0.0) or None,
        processing_time_sec=float(meta.get("processing_time") or 0.0) or None,
        device_fallback=False,
    )
    logger.info(
        "прескан: часть %s встречи '%s' импортирована как done (job %s)",
        part.index,
        meeting.title,
        job_id,
    )
    return job_id


def ingest_meeting(
    settings: Settings,
    meeting: Meeting,
    enqueue_gpu,
    profile: ScanProfile = DEFAULT_PROFILE,
    allow_reclaim: bool = False,
) -> list[dict]:
    """Заклеймить и поставить в очередь части встречи. Без download — треки локальные.

    Claim — один на ЧАСТЬ (`local:<magic>#p<N>`, ключи ОДИНАКОВЫ для всех
    режимов — переключение настроек не пере-ингестит архив). `parts_mode=merge`
    склеивает СВЕЖЕзаклеймленные части в один транскрипт (поздно доехавшая
    часть обработается отдельной джобой — лучше, чем никак). `allow_reclaim` —
    переклейм упавшей/отменённой джобы разрешён только ручному скану: авто-
    поллер иначе пережёвывал бы детерминированно битый файл каждый тик."""
    db = settings.db_path
    src = get_ingest_source(db, "local")
    default_params: dict = {}
    try:
        default_params = json.loads((src or {}).get("default_params") or "{}")
    except ValueError:
        pass

    # Единый клейм по частям — до ветвления по режиму.
    claimed: list[tuple[MeetingPart, str]] = []
    for part in meeting.parts:
        key = f"local:{meeting.magic}#p{part.index}"
        surrogate = claim_ingest(db, key, None, allow_reclaim=allow_reclaim)
        if surrogate is None:
            # Терминальный `downloaded` claim: дедуп. Ручной скан может переклеймить
            # упавшую джобу (файл могли починить/дозалить); упавшую склейку (`error`)
            # уже переклеймил claim_ingest выше при allow_reclaim.
            surrogate = reclaim_ingest_if_job_failed(db, key) if allow_reclaim else None
        if surrogate is not None:
            claimed.append((part, surrogate))
    if not claimed:
        return []

    out: list[dict] = []
    # Прескан: части с уже готовым выводом импортируются как done (GPU не занимается);
    # merge-решение принимается только по оставшимся «свежим» частям.
    fresh: list[tuple[MeetingPart, str]] = []
    for part, surrogate in claimed:
        imported_job = _import_existing_transcript(settings, meeting, part, profile, default_params)
        if imported_job is not None:
            update_ingest(db, surrogate, status="downloaded", job_id=imported_job)
            out.append({"job_id": imported_job, "kind": "imported", "part": part.index})
        else:
            fresh.append((part, surrogate))
    claimed = fresh
    if not claimed:
        return out

    merge = profile.parts_mode == "merge" and len(claimed) > 1
    if merge and not media.ffmpeg_available():
        logger.warning("склейка частей недоступна без ffmpeg — части идут отдельно")
        merge = False

    if merge:
        parts = [p for p, _ in claimed]
        work = Path(settings.data_dir) / "work" / new_id()
        work.mkdir(parents=True, exist_ok=True)
        try:
            kind, tracks = _build_merged_tracks(meeting, parts, work)
        except Exception:  # noqa: BLE001 — claim НЕтерминальный → следующий проход повторит
            logger.exception("склейка частей встречи не удалась")
            shutil.rmtree(work, ignore_errors=True)
            for _, surrogate in claimed:
                update_ingest(db, surrogate, status="error")
            return []
        unit = _Unit(
            kind=kind,
            tracks=tracks,
            title=meeting.title,
            out_dir=_base_output_dir(meeting, 1, profile),
        )
        job_id = _create_job_for_unit(settings, unit, default_params, enqueue_gpu, str(work))
        for _part, surrogate in claimed:
            update_ingest(db, surrogate, status="downloaded", job_id=job_id)
        logger.info("local ingest: %s частей склеены в одну джобу %s", len(parts), job_id)
        out.append({"job_id": job_id, "kind": kind, "part": 0})
        return out

    for part, surrogate in claimed:
        units = _units_for_part(meeting, part, profile)
        if not units:
            continue
        first_job = None
        for unit in units:
            job_id = _create_job_for_unit(
                settings, unit, default_params, enqueue_gpu, str(meeting.folder)
            )
            logger.info("local ingest: часть %s встречи заклеймлена, job %s", part.index, job_id)
            out.append({"job_id": job_id, "kind": unit.kind, "part": part.index})
            first_job = first_job or job_id
        update_ingest(db, surrogate, status="downloaded", job_id=first_job)
    return out


# «Тишина» по mtime для HTTP-скана: правки свежее этого возраста → папка ещё пишется.
HTTP_SCAN_QUIESCENCE_SEC = 5.0


def _probe_stable(folder: Path, profile: ScanProfile, delay: float = 1.0) -> str | None:
    """Проба стабильности для force-скана: ловит файл, растущий под финальным
    именем (cp/Finder — без .tmp-маркеров). Крон-путь (`delay>0`) — двойное чтение
    сигнатуры с паузой. HTTP-скан (`delay=0`) не спит в запросе — вместо паузы
    требует «тишину» по mtime: две мгновенные пробы попали бы в паузу записи и
    пропустили бы растущий файл."""
    first = folder_signature(folder, profile)
    if first is None:
        return None
    if delay > 0:
        time.sleep(delay)
        second = folder_signature(folder, profile)
        return second if second == first else None
    try:
        newest = max((f.stat().st_mtime for f in folder.rglob("*") if f.is_file()), default=0.0)
    except OSError:
        return None
    if time.time() - newest < HTTP_SCAN_QUIESCENCE_SEC:
        return None  # папка ещё пишется — возьмёт следующий скан
    return first


def poll_local_source(
    settings: Settings, enqueue_gpu, force: bool = False, wait_stable: bool = True
) -> list[dict]:
    """Один проход по watch_dir: стабильные папки встреч → ingest_meeting.

    Идемпотентно: дедуп по `local:<magic>#p<N>` в ingest_seen, окно стабильности
    (`record_stability`) пережидает rsync/конвертацию. Ошибки одной папки не
    роняют проход. `force` — ручной скан из UI работает и при выключенном
    авто-тумблере; вместо окна стабильности — немедленная двойная проба.
    `wait_stable=False` (HTTP-скан) — проба без sleep, чтобы не спать в запросе."""
    src = get_ingest_source(settings.db_path, "local")
    if not src or (not src["enabled"] and not force):
        return []
    profile = profile_from_source(src)
    root = Path(src["watch_dir"]).expanduser()
    if not root.is_dir():
        logger.warning("local watch: папка наблюдения недоступна")
        return []
    results: list[dict] = []
    for entry in sorted(root.iterdir()):
        # Симлинкам не следуем: цикл или выход за пределы watch_dir (гард — как
        # `_under_watch_dir` у Яндекс-близнеца).
        if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name.lower() in profile.skip_dirs:
            continue
        try:
            sig = folder_signature(entry, profile)
            if sig is None:
                continue  # синхронизируется/пусто
            stable = record_stability(settings.db_path, f"local:{entry}", sig)
            if stable < STABILITY_THRESHOLD:
                # Ручной скан (force) не ждёт окна, но защищается двойной
                # пробой — файл, растущий под финальным именем, не клеймим.
                probe_delay = 1.0 if wait_stable else 0.0
                if not (force and _probe_stable(entry, profile, probe_delay) == sig):
                    continue
            meeting = scan_meeting(entry, profile)
            if meeting is None:
                continue  # не выгрузка по текущему профилю — молча пропускаем
            results.extend(
                ingest_meeting(settings, meeting, enqueue_gpu, profile, allow_reclaim=force)
            )
        except Exception:  # noqa: BLE001 — одна битая папка не роняет проход
            logger.exception("local watch: ошибка обработки папки (пропущена)")
    return results
