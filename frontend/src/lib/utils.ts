import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { JobState } from "@/api/types";

// JobState — единый контракт API (api/types.ts); здесь только реэкспорт.
export type { JobState };

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtTime(sec: number): string {
  if (!Number.isFinite(sec)) return "0:00";
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const mm = m.toString().padStart(2, "0");
  const sss = ss.toString().padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${sss}` : `${m}:${sss}`;
}

/** Человеческая длительность: «9 мин 36 с» / «1 ч 04 мин». */
export function fmtDuration(sec?: number | null): string {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h} ч ${m.toString().padStart(2, "0")} мин`;
  if (m > 0) return `${m} мин ${ss.toString().padStart(2, "0")} с`;
  return `${ss} с`;
}

/** Дата/время из ISO в локальном формате. */
export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/**
 * Относительное время: «только что» / «N мин назад» / «N ч назад», дальше —
 * абсолютная дата. `empty` — текст для пустого значения, `justNowSec` — порог
 * «только что».
 */
export function fmtRelative(iso?: string | null, opts?: { empty?: string; justNowSec?: number }): string {
  if (!iso) return opts?.empty ?? "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < (opts?.justNowSec ?? 90)) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return fmtDateTime(iso);
}

/* ─── Разбор машинного имени записи в человеческий заголовок ──────────── */
export interface ParsedTitle {
  name: string; // «Дейли» без даты-времени и подчёркиваний
  recordedAt: Date | null; // время записи из имени (главный различитель)
}

function cleanName(s: string): string {
  return s.replace(/_+/g, " ").replace(/\s+/g, " ").trim();
}

/** `2026-07-08 12.05.53 Дейли` → { name:"Дейли", recordedAt: <Date> }. */
export function parseRecordingTitle(raw?: string | null): ParsedTitle {
  if (!raw) return { name: "", recordedAt: null };
  const s = raw.trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.:-](\d{2})(?:[.:-](\d{2}))?\s*(.*)$/);
  if (m) {
    const [, y, mo, d, hh, mm, ss, rest] = m;
    const dt = new Date(+y, +mo - 1, +d, +hh, +mm, +(ss ?? "0"));
    return { name: cleanName(rest), recordedAt: Number.isNaN(dt.getTime()) ? null : dt };
  }
  return { name: cleanName(s), recordedAt: null };
}

/** «8 июл, 12:05» — дата записи с точным временем (различает одинаковые «Дейли»). */
export function fmtMeetingTime(d: Date): string {
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}, ${time}`;
}

/** Каноничный JSON: ключи объектов сортируются рекурсивно — сравнение не
 * зависит от их порядка (dirty-детекция, совпадение пресетов). */
export function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_k, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  );
}

/**
 * Палитра узлов-спикеров (мотив «атома»): приглушённые, различимые тона,
 * гармонирующие с брендом. Коралл — первым (голос-хаб).
 */
export const SPEAKER_COLORS = [
  "#E75740", // коралл (бренд)
  "#3E7CB1", // azure
  "#2E9E8F", // teal
  "#C77D2E", // ochre
  "#7A67C9", // violet
  "#479A57", // green
  "#C4587E", // rose
  "#52708A", // steel
] as const;

/** Детерминированный цвет узла по метке спикера. */
export function speakerColor(label?: string | null): string {
  const key = (label ?? "").trim() || "?";
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return SPEAKER_COLORS[hash % SPEAKER_COLORS.length];
}

export const ACTIVE_STATES: JobState[] = [
  "queued",
  "paused",
  "preclean",
  "vad",
  "diarization",
  "asr",
  "quality",
  "formatting",
  "canceling",
];

type StatusTone = "wait" | "run" | "done" | "error";

/**
 * Богатые статусы вместо done/fail: русская метка + тон + короткая подсказка
 * (что происходит / что делать). Тон правит цвет пилюли и прогресса.
 */
export const STATUS_META: Record<JobState, { label: string; tone: StatusTone; hint: string }> = {
  queued: { label: "В очереди", tone: "wait", hint: "Ждёт свободный GPU-слот." },
  paused: { label: "На паузе", tone: "wait", hint: "Снята с очереди — возобновите, когда нужно." },
  canceling: { label: "Отменяется…", tone: "wait", hint: "Ждём безопасную точку остановки." },
  preclean: { label: "Чистим звук", tone: "run", hint: "Фильтруем шум перед распознаванием." },
  vad: { label: "Ищем речь", tone: "run", hint: "Находим участки с голосом." },
  diarization: { label: "Разделяем голоса", tone: "run", hint: "Определяем, кто когда говорит." },
  asr: { label: "Распознаём речь", tone: "run", hint: "Переводим голос в текст." },
  quality: { label: "Наводим качество", tone: "run", hint: "Словарь, второе мнение, метки." },
  formatting: { label: "Собираем результат", tone: "run", hint: "Готовим транскрипт и аудио." },
  done: { label: "Готово", tone: "done", hint: "Транскрипт можно смотреть и скачивать." },
  error: { label: "Ошибка", tone: "error", hint: "Обработка прервалась." },
  canceled: { label: "Отменено", tone: "error", hint: "Задача снята из очереди." },
};

export const MODE_LABEL: Record<string, string> = {
  route_a: "По дорожкам",
  single: "Общий микс",
};

/** Русская плюрализация: `plural(n, ["файл", "файла", "файлов"])`. */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}
