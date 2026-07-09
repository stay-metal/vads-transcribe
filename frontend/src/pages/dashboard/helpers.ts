import type { ComponentType } from "react";
import type { Job } from "@/api/types";
import { MONTHS_SHORT } from "@/lib/utils";
import { IconCloud, IconFolder, IconUpload } from "@/components/icons";

export const SOURCE_META: Record<
  string,
  { label: string; Icon: ComponentType<{ size?: number; className?: string }> }
> = {
  local: { label: "Локальная папка", Icon: IconFolder },
  yandex: { label: "Яндекс.Диск", Icon: IconCloud },
  upload: { label: "Загрузка", Icon: IconUpload },
};

/** Последний сегмент пути — имя самой папки. */
export function basename(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/* ─── Форматирование времени ─────────────────────────────────────────── */
export function fmtElapsed(sec: number): string {
  if (sec < 60) return `${Math.floor(sec)} с`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} мин ${Math.floor(sec % 60)} с`;
  return `${Math.floor(m / 60)} ч ${m % 60} мин`;
}

/** ETA с честной приблизительностью: без ложной секундной точности. */
export function fmtEta(sec: number): string {
  if (sec < 60) return "меньше минуты";
  const m = Math.ceil(sec / 60);
  if (m <= 10) return `~${m} мин`;
  return `~${Math.round(m / 5) * 5} мин`;
}

/** Длительность транскрибации: finished − started (сек), только для done. */
export function processingSec(job: Job): number | null {
  if (!job.started_at || !job.finished_at) return null;
  const d = (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000;
  return d > 0 ? d : null;
}

/* ─── Диапазон дат: значения <input type="date"> ↔ ISO-границы запроса ─── */
/** «2026-07-08» (локальный день) → ISO-момент начала дня в UTC (для date_from). */
export function dayStartISO(v: string): string {
  return new Date(`${v}T00:00:00`).toISOString();
}
/** date_to исключителен → начало следующего дня, чтобы выбранный день вошёл целиком. */
export function dayEndISO(v: string): string {
  const d = new Date(`${v}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}
export function toDateInput(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
/** «2026-07-08» → «8 июл». */
export function shortDate(v: string): string {
  const [, m, d] = v.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}
/** Компактная подпись активного диапазона для кнопки-триггера. */
export function rangeLabel(from: string, to: string): string {
  if (from && to) return from === to ? shortDate(from) : `${shortDate(from)} – ${shortDate(to)}`;
  if (from) return `с ${shortDate(from)}`;
  return `по ${shortDate(to)}`;
}

/* ─── Оценка прогресса активной джобы ────────────────────────────────── */
export interface Estimate {
  elapsed: number | null; // сек с начала обработки
  remaining: number | null; // сек до конца (null → неизвестно)
  speed: number | null; // × реального времени
}

/** HandBrake-схема: приор по историческому RTF, по ходу — смешивание с
 * наблюдаемой скоростью (вес растёт с долей сделанного). */
export function estimate(job: Job, avgRtf: number | null, now: number): Estimate {
  const started = job.started_at ? new Date(job.started_at).getTime() : null;
  const elapsed = started ? Math.max((now - started) / 1000, 0) : null;
  const dur = job.duration_sec ?? null;
  const prior = dur && avgRtf ? dur * avgRtf : null;
  const p = Math.min(Math.max(job.stage_pct / 100, 0), 0.99);

  let remaining: number | null = null;
  if (elapsed !== null && elapsed > 5) {
    if (p > 0.45 && elapsed > 10) {
      const observedTotal = elapsed / p;
      const w = Math.min(1, (p - 0.45) / 0.4); // доверие наблюдению растёт по ходу ASR
      const total = prior ? prior * (1 - w) + observedTotal * w : observedTotal;
      remaining = Math.max(total - elapsed, 0);
    } else if (prior) {
      // Приор исчерпан (джоба медленнее истории, напр. GPU→CPU) → честное
      // «уточняется» вместо замёрзшего оптимистичного числа.
      remaining = prior - elapsed > 0 ? prior - elapsed : null;
    }
  }
  const speed = elapsed && dur && p > 0.45 ? (p * dur) / elapsed : null;
  return { elapsed, remaining, speed };
}

/** Для queued: примерное время до старта = остатки всех, кто впереди. */
export function queueWait(job: Job, activeJobs: Job[], avgRtf: number | null, now: number): number | null {
  if (!avgRtf) return null;
  let wait = 0;
  for (const other of activeJobs) {
    if (other.id === job.id) continue;
    const ahead =
      other.state !== "queued" ||
      (other.queue_position ?? 0) < (job.queue_position ?? Number.MAX_SAFE_INTEGER);
    if (!ahead) continue;
    if (other.state === "queued") {
      wait += (other.duration_sec ?? 0) * avgRtf;
    } else {
      const est = estimate(other, avgRtf, now);
      wait += est.remaining ?? 0;
    }
  }
  return wait > 0 ? wait : null;
}
