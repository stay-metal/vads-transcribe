"""
Структуры данных для GigaAM Transcriber.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

OutputFormat = Literal["txt", "json", "srt", "vtt"]
DiarizationMode = Literal["none", "pyannote", "hybrid"]

# Происхождение текста сегмента (по убыванию «сырья» модель→правки), как в custom l0.py.
# Дефолт — "gigaam" (verbatim-вывод модели, инвариант I1). Прочие значения проставляют
# будущие пост-проходы: глоссарий, L2 «второе мнение», voiceprint, ручная правка.
DEFAULT_PROVENANCE = "gigaam"
PROVENANCE_VALUES = ("gigaam", "glossary", "second-opinion", "voiceprint", "human")


def merge_provenance(p1: str, p2: str) -> str:
    """При слиянии сегментов побеждает более «обработанный» провенанс."""
    order = {v: i for i, v in enumerate(PROVENANCE_VALUES)}
    return p1 if order.get(p1, 0) >= order.get(p2, 0) else p2


@dataclass
class WordSegment:
    """Слово с временными метками."""

    word: str
    start: float
    end: float
    confidence: float | None = None

    @property
    def duration(self) -> float:
        """Длительность слова в секундах."""
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        """Преобразование в словарь."""
        result = {
            "word": self.word,
            "start": self.start,
            "end": self.end,
        }
        if self.confidence is not None:
            result["confidence"] = self.confidence
        return result


@dataclass
class TranscriptionSegment:
    """Сегмент транскрипции с метаданными."""

    text: str
    start: float
    end: float
    speaker: str | None = None
    confidence: float | None = None
    speaker_confidence: float | None = None
    provenance: str = DEFAULT_PROVENANCE
    flags: list[str] = field(default_factory=list)
    words: list[WordSegment] | None = None

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        """Преобразование в словарь."""
        result = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.speaker is not None:
            result["speaker"] = self.speaker
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.speaker_confidence is not None:
            result["speaker_confidence"] = self.speaker_confidence
        if self.provenance and self.provenance != DEFAULT_PROVENANCE:
            result["provenance"] = self.provenance
        if self.flags:
            result["flags"] = list(self.flags)
        if self.words:
            result["words"] = [w.to_dict() for w in self.words]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranscriptionSegment":
        """Создание из словаря."""
        words = None
        if "words" in data and data["words"]:
            words = [WordSegment(**w) if isinstance(w, dict) else w for w in data["words"]]
        return cls(
            text=data["text"],
            start=data["start"],
            end=data["end"],
            speaker=data.get("speaker"),
            confidence=data.get("confidence"),
            speaker_confidence=data.get("speaker_confidence"),
            provenance=data.get("provenance", DEFAULT_PROVENANCE),
            flags=list(data.get("flags") or []),
            words=words,
        )


@dataclass
class SpeakerSegment:
    """Сегмент диаризации - информация о спикере."""

    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start


def _format_time_srt(seconds: float) -> str:
    """Форматирование времени для SRT (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    full_secs = int(secs)
    millis = int((secs - full_secs) * 1000)
    return f"{hours:02d}:{minutes:02d}:{full_secs:02d},{millis:03d}"


def _format_time_vtt(seconds: float) -> str:
    """Форматирование времени для VTT (HH:MM:SS.mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    full_secs = int(secs)
    millis = int((secs - full_secs) * 1000)
    return f"{hours:02d}:{minutes:02d}:{full_secs:02d}.{millis:03d}"


def _format_time_txt(seconds: float) -> str:
    """Форматирование времени для TXT (HH:MM:SS:cc)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    full_secs = int(secs)
    centis = int((secs - full_secs) * 100)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{full_secs:02d}:{centis:02d}"
    return f"{minutes:02d}:{full_secs:02d}:{centis:02d}"


def _atomic_write_text(path: Path, content: str) -> None:
    """Атомарная запись текста: tmp-файл в той же папке + ``os.replace``.

    Прерывание/краш посреди записи не оставляет полу-записанный артефакт (читатель
    видит либо старую версию, либо целиком новую). Фундамент под manifest/L0/resume."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        # mkstemp создаёт файл с режимом 0600; os.replace сохранит его → выход станет
        # нечитаем для group/other (регрессия vs write_text). Возвращаем honor-umask 0644.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class TranscriptionResult:
    """Результат транскрипции."""

    text: str
    segments: list[TranscriptionSegment]
    duration: float
    language: str
    model_name: str
    processing_time: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_txt(self, include_timestamps: bool = True, include_speakers: bool = True) -> str:
        """
        Форматирование в текстовый формат.

        Пример с диаризацией:
        [00:00:00 - 00:17:41] Спикер №1: текст...

        Пример без диаризации:
        [00:00:00 - 00:17:41]: текст...
        """
        if not include_timestamps:
            if include_speakers and any(s.speaker for s in self.segments):
                lines = []
                for seg in self.segments:
                    if seg.speaker:
                        lines.append(f"{seg.speaker}: {seg.text}")
                    else:
                        lines.append(seg.text)
                return "\n".join(lines)
            return self.text

        lines = []
        for seg in self.segments:
            start_str = _format_time_txt(seg.start)
            end_str = _format_time_txt(seg.end)

            if include_speakers and seg.speaker:
                lines.append(f"[{start_str} - {end_str}] {seg.speaker}: {seg.text}")
            else:
                lines.append(f"[{start_str} - {end_str}]: {seg.text}")

        return "\n".join(lines)

    def to_json(self, indent: int = 2) -> str:
        """Форматирование в JSON."""
        data = {
            "metadata": {
                "source": self.metadata.get("source", "unknown"),
                "duration": self.duration,
                "language": self.language,
                "model": self.model_name,
                "processing_time": self.processing_time,
                "speakers_count": len({s.speaker for s in self.segments if s.speaker}),
                **{k: v for k, v in self.metadata.items() if k != "source"},
            },
            "segments": [seg.to_dict() for seg in self.segments],
            "full_text": self.text,
        }
        return json.dumps(data, ensure_ascii=False, indent=indent)

    def to_srt(self) -> str:
        """
        Форматирование в SRT (SubRip) формат субтитров.

        Пример:
        1
        00:00:00,000 --> 00:00:17,410
        [Спикер №1] текст...
        """
        lines = []
        for i, seg in enumerate(self.segments, start=1):
            start_str = _format_time_srt(seg.start)
            end_str = _format_time_srt(seg.end)

            text = seg.text
            if seg.speaker:
                text = f"[{seg.speaker}] {text}"

            lines.append(str(i))
            lines.append(f"{start_str} --> {end_str}")
            lines.append(text)
            lines.append("")  # Пустая строка между субтитрами

        return "\n".join(lines)

    def to_vtt(self) -> str:
        """
        Форматирование в WebVTT формат субтитров.

        Пример:
        WEBVTT

        00:00:00.000 --> 00:00:17.410
        [Спикер №1] текст...
        """
        lines = ["WEBVTT", ""]

        for seg in self.segments:
            start_str = _format_time_vtt(seg.start)
            end_str = _format_time_vtt(seg.end)

            text = seg.text
            if seg.speaker:
                text = f"[{seg.speaker}] {text}"

            lines.append(f"{start_str} --> {end_str}")
            lines.append(text)
            lines.append("")  # Пустая строка между субтитрами

        return "\n".join(lines)

    def save(self, path: Path | str, format: OutputFormat | str = "auto") -> Path:
        """
        Сохранение результата в файл.

        Args:
            path: Путь к файлу
            format: Формат вывода ("txt", "json", "srt", "vtt" или "auto")
                   При "auto" определяется по расширению файла

        Returns:
            Путь к сохранённому файлу
        """
        path = Path(path)

        # Автоопределение формата по расширению
        if format == "auto":
            ext = path.suffix.lower()
            format_map = {
                ".txt": "txt",
                ".json": "json",
                ".srt": "srt",
                ".vtt": "vtt",
            }
            format = format_map.get(ext, "txt")

        # Генерация контента
        if format == "json":
            content = self.to_json()
        elif format == "srt":
            content = self.to_srt()
        elif format == "vtt":
            content = self.to_vtt()
        else:  # txt
            content = self.to_txt()

        # Сохранение (атомарно: tmp + os.replace — краш-безопасно)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, content)

        return path

    def get_speakers(self) -> list[str]:
        """Получить список уникальных спикеров."""
        speakers = set()
        for seg in self.segments:
            if seg.speaker:
                speakers.add(seg.speaker)
        return sorted(speakers)

    def filter_by_speaker(self, speaker: str) -> "TranscriptionResult":
        """Фильтрация по спикеру."""
        filtered_segments = [s for s in self.segments if s.speaker == speaker]
        filtered_text = " ".join(s.text for s in filtered_segments)

        return TranscriptionResult(
            text=filtered_text,
            segments=filtered_segments,
            duration=self.duration,
            language=self.language,
            model_name=self.model_name,
            processing_time=self.processing_time,
            metadata={**self.metadata, "filtered_speaker": speaker},
        )
