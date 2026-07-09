"""Парсер локальной Zoom-папки (watch-flow, спека LOCAL_WATCH_DESIGN §4).

Структура выгрузки Zoom (реальные примеры — `folder_example/`, `transcript/`):

    2026-07-08 12.05.53 Дейли/
    ├─ audio1399019170.m4a          ← микс части 1 («1» + magic_number)
    ├─ video1399019170.mp4
    ├─ recording.conf               ← {"magic_number": "399019170", "items": [...]}
    ├─ zoomver.tag
    └─ Audio Record/                ← подорожки участников (Route A)
       └─ audio<Имя><idx><частьmagic>.m4a   (имена бывают в NFD!)

Правила:
- Всё наружу — в NFC (`unicodedata.normalize`): на диске встречаются NFD-имена.
- `Audio Record/` с ≥1 дорожкой → route_a (ground-truth имена участников);
  иначе → single (микс + диаризация).
- Multi-part (стоп/старт записи): `recording.conf.items[]` + cross-check по
  верхнеуровневым `audio<N><magic>.m4a`; каждая часть — отдельная единица.
- Коллизии имён (перезаход: «Ольга» с idx 4 и 6) → суффикс «Имя (idx)».
- Сигнатура папки — барьер стабильности: `.tmp`/`.part`/`.zoom`-хвосты или
  отсутствие медиа → None (папка ещё синхронизируется/конвертируется).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from gigaam_transcriber.exceptions import UnsupportedFormatError

# Подпапки, которые скан не считает контентом встречи.
_SKIP_DIRS = {"transcripts", "done"}
# Хвосты незавершённой синхронизации (rsync/браузер/Я.Диск-клиент).
_TMP_SUFFIXES = {".tmp", ".part", ".crdownload", ".download"}
# Безобидные dotfiles ОС: НЕ признак идущей синхронизации (в отличие от
# rsync-темпов `.имя.XXXXXX`, которые тоже скрытые).
_BENIGN_DOTFILES = {".DS_Store", ".localized"}
_MEDIA_SUFFIXES = {".m4a", ".mp4", ".mov", ".mp3", ".wav"}

_MIX_RE = re.compile(r"^audio(\d+)$")  # верхнеуровневый микс: audio<часть><magic>


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass(frozen=True)
class ScanProfile:
    """Профиль раскладки источника (пресеты «Zoom»/«Простая папка»/свои).

    Дефолты = текущее поведение Zoom-сканера байт-в-байт: пустой профиль в БД
    ничего не меняет. Regex-ядро Zoom (magic/idx/части) сознательно НЕ
    параметризуется — настраиваются только константы вокруг него.
    Поля `track_mode`/`output_*` потребляет local_watch (здесь — для единого
    объекта без DB-зависимости сканера)."""

    layout: str = "zoom"  # zoom | plain
    tracks_subdir: str | None = "Audio Record"  # None → подорожек нет
    track_mode: str = "combine"  # combine | separate | mix_only
    parts_mode: str = "merge"  # merge | separate — несколько записей (стоп/старт) в папке
    media_suffixes: frozenset = frozenset(_MEDIA_SUFFIXES)
    skip_dirs: frozenset = frozenset(_SKIP_DIRS)
    output_mode: str = "beside"  # beside | fixed
    output_subdir: str = "transcripts/dialogscribe"
    output_dir: str | None = None  # для fixed

    @classmethod
    def from_dict(cls, raw: dict | None) -> ScanProfile:
        """Толерантный разбор JSON-профиля из БД: неизвестные ключи и мусорные
        типы игнорируются, отсутствующие — дефолты (обратная совместимость)."""
        raw = raw if isinstance(raw, dict) else {}
        out_raw = raw.get("output")
        out: dict = out_raw if isinstance(out_raw, dict) else {}

        def _s(v, default):
            return v if isinstance(v, str) and v.strip() else default

        def _list(v, default):
            if isinstance(v, list) and all(isinstance(x, str) for x in v) and v:
                return frozenset(x.strip().lower() for x in v if x.strip())
            return default

        def _suffixes(v, default):
            # Пользователь пишет «ogg» без точки, а Path.suffix всегда с точкой —
            # нормализуем, иначе расширение молча не матчится.
            got = _list(v, default)
            return frozenset(s if s.startswith(".") else f".{s}" for s in got)

        tracks_subdir = raw.get("tracks_subdir", cls.tracks_subdir)
        if tracks_subdir is not None and not (
            isinstance(tracks_subdir, str) and tracks_subdir.strip()
        ):
            tracks_subdir = None
        # Defense-in-depth: даже если traversal-значение просочилось в БД мимо
        # pydantic-валидации, не джойним абсолютный путь/«..» к папке встречи.
        if isinstance(tracks_subdir, str):
            sub = Path(tracks_subdir)
            if sub.is_absolute() or ".." in sub.parts:
                tracks_subdir = None
        layout = _s(raw.get("layout"), cls.layout)
        track_mode = _s(raw.get("track_mode"), cls.track_mode)
        parts_mode = _s(raw.get("parts_mode"), cls.parts_mode)
        output_mode = _s(out.get("mode"), cls.output_mode)
        return cls(
            layout=layout if layout in ("zoom", "plain") else cls.layout,
            tracks_subdir=tracks_subdir,
            track_mode=(
                track_mode if track_mode in ("combine", "separate", "mix_only") else cls.track_mode
            ),
            parts_mode=parts_mode if parts_mode in ("merge", "separate") else cls.parts_mode,
            media_suffixes=_suffixes(raw.get("media_suffixes"), cls.media_suffixes),
            skip_dirs=frozenset(s.lower() for s in _list(raw.get("skip_dirs"), cls.skip_dirs)),
            output_mode=output_mode if output_mode in ("beside", "fixed") else cls.output_mode,
            output_subdir=_s(out.get("subdir"), cls.output_subdir),
            output_dir=out.get("dir") if isinstance(out.get("dir"), str) else None,
        )


DEFAULT_PROFILE = ScanProfile()


@dataclass
class MeetingPart:
    """Одна часть записи (обычно единственная)."""

    index: int  # 1-based номер части
    kind: str  # route_a | single
    tracks: list[dict] = field(default_factory=list)  # [{name, path, size}]
    mix_path: str | None = None  # микс части (для single и downmix)


@dataclass
class Meeting:
    folder: Path
    magic: str  # magic_number (дедуп-идентичность встречи)
    title: str  # имя папки (NFC)
    parts: list[MeetingPart] = field(default_factory=list)


def _read_conf(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "recording.conf").read_text(encoding="utf-8"))
    except Exception:
        return None


def _top_level_mixes(folder: Path) -> dict[str, Path]:
    """Верхнеуровневые миксы: полный номер файла («1399019170») → путь."""
    out: dict[str, Path] = {}
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".m4a":
            m = _MIX_RE.match(_nfc(p.stem))
            if m:
                out[m.group(1)] = p
    return out


def _detect_magic(conf: dict | None, mixes: dict[str, Path]) -> str | None:
    """magic_number: primary — recording.conf; fallback — общий суффикс миксов."""
    if conf and conf.get("magic_number"):
        return str(conf["magic_number"])
    nums = sorted(mixes)
    if not nums:
        return None
    if len(nums) == 1:
        # Единственный микс «<часть><magic>» — часть почти всегда одна цифра.
        return nums[0][1:] or nums[0]
    # Несколько частей: magic — общий хвост номеров.
    suffix = nums[0]
    for n in nums[1:]:
        while suffix and not n.endswith(suffix):
            suffix = suffix[1:]
    return suffix or nums[0]


def _participant_tracks(folder: Path, part_num: str, profile: ScanProfile) -> list[dict]:
    """Подорожки данной части (подпапка из профиля, у Zoom — «Audio Record»):
    имя участника из имени файла.

    Файл: audio<Имя><idx><part_num>.m4a, где part_num — полный номер микса
    («1399019170»). idx — хвостовые цифры после имени (валидируем разумный
    диапазон, имя может само кончаться цифрой — «Alex2»)."""
    if profile.tracks_subdir is None:
        return []
    rec_dir = folder / profile.tracks_subdir
    if not rec_dir.is_dir():
        return []
    raw: list[tuple[str, int | None, Path]] = []
    for p in sorted(rec_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".m4a":
            continue
        stem = _nfc(p.stem)
        if not stem.startswith("audio") or not stem.endswith(part_num):
            continue
        rest = stem[len("audio") : -len(part_num)]
        m = re.match(r"^(?P<name>.*?)(?P<idx>\d{1,2})$", rest)
        if m and m.group("name"):
            raw.append((m.group("name"), int(m.group("idx")), p))
        elif rest:
            raw.append((rest, None, p))
    raw.sort(key=lambda t: (t[1] if t[1] is not None else 999, t[0]))
    # Коллизии имён (перезаход участника) → «Имя (idx)», чтобы дорожки не слились.
    names = [n for n, _, _ in raw]
    tracks = []
    for name, idx, p in raw:
        label = f"{name} ({idx})" if names.count(name) > 1 and idx is not None else name
        tracks.append({"name": label, "base": name, "path": str(p), "size": p.stat().st_size})
    return tracks


# Видео-контейнеры: видео-ДУБЛЬ аудио (тот же stem или общий числовой хвост ≥4
# цифр — конвенция Zoom) не должен становиться второй «дорожкой»; несвязанное
# видео остаётся дорожкой (урок F7). Единая реализация — здесь (чистый парсер),
# её переиспользует и Яндекс-ingest (plain-папка и папка Я.Диска).
# Набор — из библиотечной константы: ingest принимает ВСЕ видео-контейнеры,
# значит и фильтр дублей обязан знать их все (иначе .avi-дубль стал бы «дорожкой»).
_VIDEO_SUFFIXES = frozenset(UnsupportedFormatError.SUPPORTED_VIDEO)


def digit_tail(stem: str) -> str:
    """Хвостовые цифры ≥4 (Zoom-ключ): «audio1791450993» → «1791450993», иначе ''.

    Короткие хвосты (<4 цифр, «track2») — не идентификатор: пустая строка,
    чтобы случайное совпадение цифры не склеило несвязанные файлы."""
    m = re.search(r"(\d{4,})$", stem)
    return m.group(1) if m else ""


def drop_video_duplicates(items: list[dict], *, stem_of, suffix_of) -> list[dict]:
    """Отсеять видео-дубли аудио-дорожек. `stem_of(item)` → имя без расширения,
    `suffix_of(item)` → суффикс (с точкой, нижний регистр). Видео выбрасывается
    ТОЛЬКО при наличии аудио-пары по stem или Zoom-ключу; несвязанное — остаётся."""
    audio_keys: set[str] = set()
    for it in items:
        if suffix_of(it) not in _VIDEO_SUFFIXES:
            stem = stem_of(it)
            audio_keys.add(stem)
            tail = digit_tail(stem)
            if tail:
                audio_keys.add(tail)

    def _is_dup(it: dict) -> bool:
        if suffix_of(it) not in _VIDEO_SUFFIXES:
            return False
        stem = stem_of(it)
        tail = digit_tail(stem)
        return stem in audio_keys or bool(tail and tail in audio_keys)

    return [it for it in items if not _is_dup(it)]


def _scan_plain(folder: Path, profile: ScanProfile) -> Meeting | None:
    """Раскладка «Простая папка»: медиа-файлы в корне папки — дорожки встречи.

    Без частей и magic-конвенций. Идентичность дедупа — отпечаток КОНТЕНТА
    (имена+размеры дорожек), а не имя папки: переиспользование имени с новой
    записью транскрибируется, переименование обработанной папки — нет."""
    # Корень папки + (если задан) подпапка дорожек — один проход по обоим.
    dirs = [folder]
    if profile.tracks_subdir:
        sub = folder / profile.tracks_subdir
        if sub.is_dir():
            dirs.append(sub)
    tracks: list[dict] = []
    for d in dirs:
        for p in sorted(d.iterdir()):
            if p.is_symlink() or not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() in profile.media_suffixes:
                stem = _nfc(p.stem)
                tracks.append(
                    {"name": stem, "base": stem, "path": str(p), "size": p.stat().st_size}
                )
    tracks = drop_video_duplicates(
        tracks, stem_of=lambda t: t["name"], suffix_of=lambda t: Path(t["path"]).suffix.lower()
    )
    if not tracks:
        return None
    kind = "route_a" if len(tracks) > 1 else "single"
    part = MeetingPart(
        index=1,
        kind=kind,
        tracks=tracks,
        mix_path=tracks[0]["path"] if len(tracks) == 1 else None,
    )
    fingerprint = hashlib.sha1(
        "|".join(f"{t['name']}:{t['size']}" for t in tracks).encode("utf-8")
    ).hexdigest()[:16]
    return Meeting(
        folder=folder, magic=f"plain:{fingerprint}", title=_nfc(folder.name), parts=[part]
    )


def scan_meeting(folder: Path, profile: ScanProfile = DEFAULT_PROFILE) -> Meeting | None:
    """Разобрать папку встречи по профилю. None → не (готовая) выгрузка."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    if profile.layout == "plain":
        return _scan_plain(folder, profile)
    conf = _read_conf(folder)
    mixes = _top_level_mixes(folder)
    magic = _detect_magic(conf, mixes)
    if magic is None:
        return None

    # Номера частей: primary — items[] (по порядку: часть N = позиция N),
    # cross-check — фактические миксы «<N><magic>» на диске (Zoom пишет
    # audio1…/audio2… при рестарте записи). Объединяем множества.
    part_indices: set[int] = set()
    if conf and isinstance(conf.get("items"), list):
        part_indices |= set(range(1, len(conf["items"]) + 1))
    for num in mixes:
        if num.endswith(magic) and num[: -len(magic)].isdigit():
            part_indices.add(int(num[: -len(magic)]))
    if not part_indices:
        part_indices = {1}

    parts: list[MeetingPart] = []
    for idx in sorted(part_indices):
        part_num = f"{idx}{magic}"
        mix = mixes.get(part_num)
        tracks = _participant_tracks(folder, part_num, profile)
        if tracks:
            kind = "route_a"
        elif mix is not None:
            kind = "single"
            tracks = [{"name": _nfc(folder.name), "path": str(mix), "size": mix.stat().st_size}]
        else:
            continue  # часть без медиа (недокачана/битая) — пропуск
        parts.append(
            MeetingPart(index=idx, kind=kind, tracks=tracks, mix_path=str(mix) if mix else None)
        )
    if not parts:
        return None
    return Meeting(folder=folder, magic=magic, title=_nfc(folder.name), parts=parts)


def folder_signature(folder: Path, profile: ScanProfile = DEFAULT_PROFILE) -> str | None:
    """Сигнатура стабильности папки встречи (аналог md5/revision Я.Диска).

    None → папка НЕ готова: есть `.tmp`/`.part`-хвосты, недоконвертированные
    `.zoom`-чанки или нет ни одного медиа-файла. Иначе — `local|N|размер|mtime`
    по всем медиа (верхний уровень + подпапки; skip_dirs профиля — не контент).
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    count, total, latest = 0, 0, 0
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            return None
        for p in entries:
            name = _nfc(p.name)
            if p.is_symlink():
                continue  # симлинки не контент: не следуем (циклы/выход из папки)
            if name.startswith("."):
                if p.is_file() and name not in _BENIGN_DOTFILES:
                    # rsync копирует во временный СКРЫТЫЙ файл `.имя.XXXXXX` —
                    # это такой же маркер «синхронизация идёт», как .tmp-хвост.
                    return None
                continue  # скрытые каталоги/служебные файлы ОС — не контент
            if p.is_dir():
                if name.lower() in profile.skip_dirs:
                    continue
                stack.append(p)
                continue
            suffix = p.suffix.lower()
            if suffix in _TMP_SUFFIXES or suffix == ".zoom":
                return None  # синхронизация/конвертация ещё идёт
            if suffix in profile.media_suffixes:
                try:
                    st = p.stat()
                except OSError:
                    return None
                count += 1
                total += st.st_size
                latest = max(latest, st.st_mtime_ns)
    if count == 0:
        return None
    return f"local|{count}|{total}|{latest}"
