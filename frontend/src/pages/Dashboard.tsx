import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { api } from "@/api/client";
import type { Job, JobsPage } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  SectionTitle,
  StageBar,
  StatusPill,
  Mono,
  ErrorCard,
} from "@/components/ui";
import {
  IconMic,
  IconSearch,
  IconCalendar,
  IconX,
  IconChevronDown,
  IconChevronRight,
  IconFolder,
  IconCloud,
  IconUpload,
} from "@/components/icons";
import {
  cn,
  MODE_LABEL,
  STATUS_META,
  SPEAKER_COLORS,
  fmtDuration,
  fmtDateTime,
} from "@/lib/utils";

const PAGE = 25;

const SOURCE_META: Record<
  string,
  { label: string; Icon: React.ComponentType<{ size?: number; className?: string }> }
> = {
  local: { label: "Локальная папка", Icon: IconFolder },
  yandex: { label: "Яндекс.Диск", Icon: IconCloud },
  upload: { label: "Загрузка", Icon: IconUpload },
};

/** Последний сегмент пути — имя самой папки. */
function basename(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/* ─── Форматирование времени ─────────────────────────────────────────── */
function fmtElapsed(sec: number): string {
  if (sec < 60) return `${Math.floor(sec)} с`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} мин ${Math.floor(sec % 60)} с`;
  return `${Math.floor(m / 60)} ч ${m % 60} мин`;
}

/** ETA с честной приблизительностью: без ложной секундной точности. */
function fmtEta(sec: number): string {
  if (sec < 60) return "меньше минуты";
  const m = Math.ceil(sec / 60);
  if (m <= 10) return `~${m} мин`;
  return `~${Math.round(m / 5) * 5} мин`;
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 90) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return fmtDateTime(iso);
}

/* ─── Разбор машинного имени файла в человеческий заголовок ───────────── */
interface ParsedTitle {
  name: string; // «Дейли» без даты-времени и подчёркиваний
  recordedAt: Date | null; // время записи из имени (главный различитель)
}

function cleanName(s: string): string {
  return s.replace(/_+/g, " ").replace(/\s+/g, " ").trim();
}

/** `2026-07-08 12.05.53 Дейли` → { name:"Дейли", recordedAt: <Date> }. */
function parseRecordingTitle(raw?: string | null): ParsedTitle {
  if (!raw) return { name: "", recordedAt: null };
  const s = raw.trim();
  const m = s.match(
    /^(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.:-](\d{2})(?:[.:-](\d{2}))?\s*(.*)$/,
  );
  if (m) {
    const [, y, mo, d, hh, mm, ss, rest] = m;
    const dt = new Date(+y, +mo - 1, +d, +hh, +mm, +(ss ?? "0"));
    return {
      name: cleanName(rest),
      recordedAt: Number.isNaN(dt.getTime()) ? null : dt,
    };
  }
  return { name: cleanName(s), recordedAt: null };
}

const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** «8 июл, 12:05» — дата записи с точным временем (различает одинаковые «Дейли»). */
function fmtMeetingTime(d: Date): string {
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}, ${time}`;
}

/** Длительность транскрибации: finished − started (сек), только для done. */
function processingSec(job: Job): number | null {
  if (!job.started_at || !job.finished_at) return null;
  const d = (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000;
  return d > 0 ? d : null;
}

/* ─── Диапазон дат: значения <input type="date"> ↔ ISO-границы запроса ─── */
/** «2026-07-08» (локальный день) → ISO-момент начала дня в UTC (для date_from). */
function dayStartISO(v: string): string {
  return new Date(`${v}T00:00:00`).toISOString();
}
/** date_to исключителен → начало следующего дня, чтобы выбранный день вошёл целиком. */
function dayEndISO(v: string): string {
  const d = new Date(`${v}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}
function toDateInput(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
/** «2026-07-08» → «8 июл». */
function shortDate(v: string): string {
  const [, m, d] = v.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}
/** Компактная подпись активного диапазона для кнопки-триггера. */
function rangeLabel(from: string, to: string): string {
  if (from && to) return from === to ? shortDate(from) : `${shortDate(from)} – ${shortDate(to)}`;
  if (from) return `с ${shortDate(from)}`;
  return `по ${shortDate(to)}`;
}

/* ─── Сигнатура: голосовой глиф (мотив «атома») ──────────────────────────
 * route_a → кластер разделённых узлов-спикеров по track_count;
 * single  → цельный атом-хаб в ореоле (весь разговор сведён в один трек).
 * Статус кодируется приглушением (error/canceled), как и левый якорь строки. */
function VoiceGlyph({
  job,
  size = "sm",
  muted,
}: {
  job: Job;
  size?: "sm" | "lg";
  muted?: boolean;
}) {
  const dot = size === "lg" ? 15 : 13;
  const wrap = cn(
    "flex w-11 shrink-0 items-center",
    size === "lg" && "w-12",
    muted && "opacity-45 saturate-[.35]",
  );

  if (job.mode === "route_a") {
    const n = Math.max(1, job.track_count ?? 2);
    const shown = Math.min(n, 3);
    const extra = n - shown;
    return (
      <div className={wrap}>
        <div className="flex">
          {Array.from({ length: shown }).map((_, i) => (
            <span
              key={i}
              className="rounded-full ring-2 ring-white"
              style={{
                width: dot,
                height: dot,
                background: SPEAKER_COLORS[i % SPEAKER_COLORS.length],
                marginLeft: i === 0 ? 0 : -dot * 0.42,
                zIndex: shown - i,
              }}
            />
          ))}
          {extra > 0 && (
            <span
              className="grid place-items-center rounded-full bg-canvas font-mono font-semibold text-ink-muted ring-2 ring-white"
              style={{ width: dot + 2, height: dot + 2, marginLeft: -dot * 0.42, fontSize: 9 }}
            >
              +{extra}
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={wrap}>
      <span
        className="grid place-items-center rounded-full bg-coral-soft text-coral-500"
        style={{ width: dot + 10, height: dot + 10 }}
      >
        <IconMic size={dot} />
      </span>
    </div>
  );
}

/* ─── Оценка прогресса активной джобы ────────────────────────────────── */
interface Estimate {
  elapsed: number | null; // сек с начала обработки
  remaining: number | null; // сек до конца (null → неизвестно)
  speed: number | null; // × реального времени
}

/** HandBrake-схема: приор по историческому RTF, по ходу — смешивание с
 * наблюдаемой скоростью (вес растёт с долей сделанного). */
function estimate(job: Job, avgRtf: number | null, now: number): Estimate {
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
function queueWait(job: Job, activeJobs: Job[], avgRtf: number | null, now: number): number | null {
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

/* ─── Активная запись: карточка с прогрессом и деталями по клику ─────── */
function ActiveJobCard({ job, page, now }: { job: Job; page: JobsPage; now: number }) {
  const [open, setOpen] = React.useState(false);
  const meta = STATUS_META[job.state];
  const title = parseRecordingTitle(job.title).name || MODE_LABEL[job.mode] || job.mode;
  const est = estimate(job, page.avg_rtf, now);
  const wait = job.state === "queued" ? queueWait(job, page.jobs, page.avg_rtf, now) : null;
  const Chevron = open ? IconChevronDown : IconChevronRight;

  const statusBits: string[] = [];
  if (job.state === "queued") {
    if (job.queue_position && job.queue_position > 1)
      statusBits.push(`${job.queue_position}-я в очереди`);
    statusBits.push(wait ? `старт через ${fmtEta(wait)}` : "ждёт свободный слот");
  } else {
    if (est.elapsed !== null) statusBits.push(`идёт ${fmtElapsed(est.elapsed)}`);
    if (est.remaining !== null) statusBits.push(`осталось ${fmtEta(est.remaining)}`);
    else statusBits.push("оценка уточнится по ходу");
    if (est.speed && est.speed > 1.5) statusBits.push(`${Math.round(est.speed)}× реального времени`);
  }

  return (
    <Card className="overflow-hidden transition-shadow hover:shadow-lift">
      <button className="flex w-full items-start gap-3 p-4 text-left" onClick={() => setOpen((v) => !v)}>
        <div className="mt-0.5">
          <VoiceGlyph job={job} size="lg" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-ink">{title}</span>
            <StatusPill state={job.state} />
            {job.device_fallback && <Badge tone="amber">GPU→CPU</Badge>}
          </div>
          <div className="mt-2 max-w-xl space-y-1.5">
            <StageBar pct={job.stage_pct} state={job.state} />
            <p className="text-xs text-ink-muted">
              {meta.label} · {statusBits.join(" · ")}
            </p>
          </div>
        </div>
        <Chevron size={16} className="mt-1 shrink-0 text-ink-muted" />
      </button>

      {open && (
        <div className="border-t border-line bg-canvas/50 px-4 py-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 text-[13px] sm:grid-cols-3">
            <MetricRow k="Длительность аудио" v={fmtDuration(job.duration_sec)} />
            <MetricRow k="Дорожек" v={job.track_count ? String(job.track_count) : "—"} />
            <MetricRow k="Источник" v={SOURCE_META[job.source ?? ""]?.label ?? job.source ?? "—"} />
            <MetricRow k="Прошло" v={est.elapsed !== null ? fmtElapsed(est.elapsed) : "—"} />
            <MetricRow
              k="Осталось"
              v={
                job.state === "queued"
                  ? wait
                    ? `старт через ${fmtEta(wait)}`
                    : "—"
                  : est.remaining !== null
                    ? fmtEta(est.remaining)
                    : "уточняется"
              }
            />
            <MetricRow
              k="Скорость"
              v={est.speed && est.speed > 1.5 ? `${Math.round(est.speed)}× реального времени` : "—"}
            />
            <MetricRow k="Стадия" v={`${meta.label} · ${job.stage_pct}%`} />
            <MetricRow k="Добавлена" v={fmtDateTime(job.created_at)} />
            <MetricRow k="Задача" v={<Mono>#{job.id.slice(0, 8)}</Mono>} />
          </dl>
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-xs text-ink-muted">
              Можно закрыть страницу — обработка продолжится на сервере.
            </p>
            {job.state === "queued" && (
              <Button variant="outline" size="sm" onClick={() => api.cancelJob(job.id).catch(() => {})}>
                Отменить
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function MetricRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-muted">{k}</dt>
      <dd className="mt-0.5 text-ink">{v}</dd>
    </div>
  );
}

/* ─── Ячейки таблицы записей ─────────────────────────────────────────── */
function rowIsMuted(job: Job): boolean {
  return job.state === "error" || job.state === "canceled";
}

function TitleCell({ job }: { job: Job }) {
  const { name } = parseRecordingTitle(job.title);
  const display = name || MODE_LABEL[job.mode] || job.mode;
  return (
    <div className="min-w-0">
      <div
        className="truncate font-medium text-ink transition-colors group-hover:text-coral-600"
        title={job.title ?? undefined}
      >
        {display}
      </div>
      {job.state === "error" && (
        <div className="truncate text-xs text-coral-600">
          {job.error_message || "ошибка обработки"}
        </div>
      )}
      {job.state === "canceled" && <div className="text-xs text-ink-muted/70">отменено</div>}
    </div>
  );
}

function RecordedCell({ job }: { job: Job }) {
  const { recordedAt } = parseRecordingTitle(job.title);
  const when = recordedAt ? fmtMeetingTime(recordedAt) : fmtRelative(job.created_at);
  return <span className="whitespace-nowrap tabular text-[13px] text-ink-muted">{when}</span>;
}

function SourceBadge({ source }: { source?: string | null }) {
  // watch_dir локального источника — чтобы показать имя папки вместо «Локальная папка».
  // Один общий кэш на все строки (React Query дедуплицирует).
  const { data: local } = useQuery({
    queryKey: ["ingest-source", "local"],
    queryFn: () => api.getIngestSource("local"),
    staleTime: 5 * 60_000,
  });
  const meta = SOURCE_META[source ?? ""];
  if (!meta) return <span className="text-[13px] text-ink-muted">—</span>;
  const { Icon } = meta;
  const label = source === "local" && local?.watch_dir ? basename(local.watch_dir) : meta.label;
  return (
    <Badge tone="neutral" className="whitespace-nowrap">
      <Icon size={12} className="shrink-0 text-ink-muted" />
      {label}
    </Badge>
  );
}

/* ─── Скелетон-строки под двухстрочную раскладку (вместо спиннера) ─────── */
function SkeletonRows() {
  return (
    <Card className="divide-y divide-line/60 overflow-hidden">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3">
          <div className="h-6 w-6 shrink-0 animate-pulse rounded-full bg-line/60" />
          <div className="min-w-0 flex-1 space-y-2">
            <div className="h-3 w-40 max-w-[60%] animate-pulse rounded bg-line/60" />
            <div className="h-2.5 w-24 animate-pulse rounded bg-line/50" />
          </div>
          <div className="hidden h-3 w-12 animate-pulse rounded bg-line/60 sm:block" />
        </div>
      ))}
    </Card>
  );
}

/* ─── Таблица записей (TanStack Table, строка-ссылка на done) ─────────── */
const columnHelper = createColumnHelper<Job>();
const RECORD_COLUMNS = [
  columnHelper.display({
    id: "glyph",
    header: () => null,
    cell: ({ row }) => <VoiceGlyph job={row.original} muted={rowIsMuted(row.original)} />,
  }),
  columnHelper.accessor("title", {
    header: "Название",
    cell: ({ row }) => <TitleCell job={row.original} />,
  }),
  columnHelper.display({
    id: "recorded",
    header: "Дата",
    cell: ({ row }) => <RecordedCell job={row.original} />,
  }),
  columnHelper.accessor("duration_sec", {
    header: "Длительность",
    cell: ({ getValue }) => (
      <span className="whitespace-nowrap tabular text-[13px] text-ink">
        {fmtDuration(getValue())}
      </span>
    ),
  }),
  columnHelper.display({
    id: "processing",
    header: "Транскрибация",
    cell: ({ row }) => {
      const p = processingSec(row.original);
      return (
        <span className="whitespace-nowrap tabular text-[13px] text-ink-muted">
          {p != null ? fmtDuration(p) : "—"}
        </span>
      );
    },
  }),
  columnHelper.display({
    id: "source",
    header: "Источник",
    cell: ({ row }) => <SourceBadge source={row.original.source} />,
  }),
  columnHelper.display({
    id: "chevron",
    header: () => null,
    cell: ({ row }) =>
      row.original.state === "done" ? (
        <IconChevronRight
          size={16}
          className="text-line transition-all group-hover:translate-x-0.5 group-hover:text-coral-500 group-focus-visible:translate-x-0.5 group-focus-visible:text-coral-500"
        />
      ) : null,
  }),
];

const RIGHT_COLS = new Set(["duration_sec", "processing"]);
const META_COLS = new Set(["glyph", "chevron"]);

function RecordsTable({ data }: { data: Job[] }) {
  const navigate = useNavigate();
  const table = useReactTable({
    data,
    columns: RECORD_COLUMNS,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line">
              {table.getHeaderGroups()[0].headers.map((h) => (
                <th
                  key={h.id}
                  className={cn(
                    "px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-ink-muted/80",
                    RIGHT_COLS.has(h.column.id) ? "text-right" : "text-left",
                    META_COLS.has(h.column.id) && "w-px",
                  )}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map((r) => {
              const job = r.original;
              const clickable = job.state === "done";
              const open = () => navigate(`/jobs/${job.id}`);
              return (
                <tr
                  key={r.id}
                  onClick={clickable ? open : undefined}
                  onKeyDown={clickable ? (e) => e.key === "Enter" && open() : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  role={clickable ? "link" : undefined}
                  aria-label={
                    clickable
                      ? `Открыть: ${parseRecordingTitle(job.title).name || MODE_LABEL[job.mode]}`
                      : undefined
                  }
                  className={cn(
                    "group border-b border-line/60 outline-none transition-colors last:border-0",
                    clickable &&
                      "cursor-pointer hover:bg-coral-soft/50 focus-visible:bg-coral-soft/50",
                  )}
                >
                  {r.getVisibleCells().map((c) => (
                    <td
                      key={c.id}
                      className={cn(
                        "px-4 py-3 align-middle",
                        RIGHT_COLS.has(c.column.id) && "text-right",
                        META_COLS.has(c.column.id) && "w-px",
                      )}
                    >
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ─── Фильтр по датам (диапазон + пресеты) ───────────────────────────── */
function PresetBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-line px-2.5 py-1 text-[12px] text-ink-muted transition-colors hover:border-coral-500/40 hover:bg-coral-soft hover:text-coral-600"
    >
      {children}
    </button>
  );
}

function DateFilter({
  from,
  to,
  onChange,
}: {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = Boolean(from || to);
  const preset = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (days - 1));
    onChange(toDateInput(start), toDateInput(end));
    setOpen(false);
  };
  const fieldCls =
    "h-9 w-full rounded-control border border-line bg-white px-2 text-[13px] text-ink outline-none focus:border-azure/70";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors",
          active
            ? "border-coral-500/40 bg-coral-soft font-medium text-coral-600"
            : "border-line bg-white text-ink-muted hover:text-ink",
        )}
      >
        <IconCalendar size={14} />
        {active ? rangeLabel(from, to) : "Период"}
        {active && (
          <span
            role="button"
            aria-label="Сбросить период"
            onClick={(e) => {
              e.stopPropagation();
              onChange("", "");
            }}
            className="-mr-1 ml-0.5 grid h-4 w-4 place-items-center rounded-full text-coral-500 hover:bg-coral-500/15"
          >
            <IconX size={11} />
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-72 rounded-card border border-line bg-white p-3 shadow-lift">
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="mb-1 block text-[11px] text-ink-muted">От</span>
              <input
                type="date"
                value={from}
                max={to || undefined}
                onChange={(e) => onChange(e.target.value, to)}
                className={fieldCls}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-ink-muted">До</span>
              <input
                type="date"
                value={to}
                min={from || undefined}
                onChange={(e) => onChange(from, e.target.value)}
                className={fieldCls}
              />
            </label>
          </div>
          <div className="mt-2.5 flex items-center gap-1.5">
            <PresetBtn onClick={() => preset(7)}>7 дней</PresetBtn>
            <PresetBtn onClick={() => preset(30)}>30 дней</PresetBtn>
            <button
              type="button"
              onClick={() => {
                onChange("", "");
                setOpen(false);
              }}
              className="ml-auto text-[12px] font-medium text-ink-muted hover:text-coral-600"
            >
              Сбросить
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Чипы-фильтры архива ────────────────────────────────────────────── */
type Scope = "terminal" | "done" | "error" | "canceled";

function FilterChip({
  label,
  count,
  active,
  alert,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  alert?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-colors",
        active
          ? "border-coral-500/40 bg-coral-soft font-medium text-coral-600"
          : "border-line bg-white text-ink-muted hover:text-ink",
      )}
    >
      {label}
      <span className={cn("text-xs", alert && count > 0 && !active ? "font-semibold text-coral-500" : "opacity-70")}>
        {count}
      </span>
    </button>
  );
}

/* ─── Страница ───────────────────────────────────────────────────────── */
export default function Dashboard() {
  const qc = useQueryClient();
  const [scope, setScope] = React.useState<Scope>("terminal");
  const [query, setQuery] = React.useState("");
  const [q, setQ] = React.useState(""); // debounced
  const [dateFrom, setDateFrom] = React.useState(""); // «YYYY-MM-DD» из <input type=date>
  const [dateTo, setDateTo] = React.useState("");
  const [now, setNow] = React.useState(() => Date.now());

  // Debounce поиска.
  React.useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  // Активные: частый поллинг, пока что-то обрабатывается.
  const active = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () => api.listJobs({ scope: "active", limit: 50 }),
    refetchInterval: (qy) => ((qy.state.data?.counts.active ?? 0) > 0 ? 1500 : 15000),
  });
  const activeJobs = active.data?.jobs ?? [];
  const counts = active.data?.counts;

  // Тикающий elapsed/ETA — раз в секунду, только пока есть активные.
  React.useEffect(() => {
    if (!activeJobs.length) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [activeJobs.length]);

  // Завершение активной джобы → обновить архив.
  const prevActive = React.useRef(0);
  React.useEffect(() => {
    const n = counts?.active ?? 0;
    if (n < prevActive.current) qc.invalidateQueries({ queryKey: ["jobs", "archive"] });
    prevActive.current = n;
  }, [counts?.active, qc]);

  // ISO-границы запроса из выбранных локальных дней (date_to — полуоткрытый).
  const dateFromISO = dateFrom ? dayStartISO(dateFrom) : undefined;
  const dateToISO = dateTo ? dayEndISO(dateTo) : undefined;

  // Архив: offset-подгрузка страницами (ленивая), без поллинга.
  const archive = useInfiniteQuery({
    queryKey: ["jobs", "archive", scope, q, dateFrom, dateTo],
    queryFn: ({ pageParam }) =>
      api.listJobs({ scope, q, date_from: dateFromISO, date_to: dateToISO, limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.jobs.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const rows = React.useMemo(
    () => archive.data?.pages.flatMap((p) => p.jobs) ?? [],
    [archive.data],
  );
  const pages = archive.data?.pages;
  const total = pages && pages.length > 0 ? pages[pages.length - 1].total : 0;

  // Автоподгрузка следующей страницы при доскролле (IntersectionObserver-сентинел).
  const loadMoreRef = React.useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = archive;
  React.useEffect(() => {
    const el = loadMoreRef.current;
    if (!el || !hasNextPage) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !isFetchingNextPage) fetchNextPage();
      },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const hasFilters = Boolean(q || dateFrom || dateTo || scope !== "terminal");
  const resetFilters = () => {
    setQuery("");
    setScope("terminal");
    setDateFrom("");
    setDateTo("");
  };
  const isEmptyInstall =
    !active.isLoading &&
    !active.isError &&
    activeJobs.length === 0 &&
    !archive.isLoading &&
    !archive.isError &&
    total === 0 &&
    !hasFilters;

  return (
    <div className="space-y-6">
      <SectionTitle
        eyebrow="Обработка"
        title="Записи"
        right={
          <Link to="/upload">
            <Button>
              <IconMic size={17} />
              Новая запись
            </Button>
          </Link>
        }
      />

      {active.isError && (
        <ErrorCard title="Не удалось получить состояние обработки" detail="Список обновится автоматически, когда сервер станет доступен." />
      )}

      {/* В работе */}
      {activeJobs.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-ink">
            В работе <span className="text-ink-muted">— {activeJobs.length}</span>
          </h2>
          {activeJobs.map((j) => (
            <ActiveJobCard key={j.id} job={j} page={active.data!} now={now} />
          ))}
        </section>
      )}

      {isEmptyInstall ? (
        <EmptyState
          icon={<IconMic size={22} />}
          title="Пока нет записей"
          desc="Загрузите созвон или настройте наблюдение за папкой — транскрипты появятся здесь."
          action={
            <Link to="/upload">
              <Button>Загрузить запись</Button>
            </Link>
          }
        />
      ) : (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {counts && (
              <>
                <FilterChip
                  label="Все"
                  count={counts.done + counts.error + counts.canceled}
                  active={scope === "terminal"}
                  onClick={() => setScope("terminal")}
                />
                <FilterChip
                  label="Готово"
                  count={counts.done}
                  active={scope === "done"}
                  onClick={() => setScope("done")}
                />
                <FilterChip
                  label="Ошибки"
                  count={counts.error}
                  active={scope === "error"}
                  alert
                  onClick={() => setScope("error")}
                />
                {counts.canceled > 0 && (
                  <FilterChip
                    label="Отменено"
                    count={counts.canceled}
                    active={scope === "canceled"}
                    onClick={() => setScope("canceled")}
                  />
                )}
              </>
            )}
            <div className="ml-auto flex items-center gap-2">
              <DateFilter
                from={dateFrom}
                to={dateTo}
                onChange={(f, t) => {
                  setDateFrom(f);
                  setDateTo(t);
                }}
              />
              <div className="relative w-44 sm:w-56">
                <IconSearch
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
                />
                <Input
                  value={query}
                  placeholder="Поиск по названию…"
                  className="h-9 pl-9"
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            </div>
          </div>

          {archive.isError ? (
            <ErrorCard title="Не удалось загрузить архив" detail="Проверьте, что сервер запущен, и обновите страницу." />
          ) : archive.isLoading ? (
            <SkeletonRows />
          ) : rows.length === 0 ? (
            <Card className="px-5 py-10 text-center text-sm text-ink-muted">
              Ничего не найдено.{" "}
              {hasFilters && (
                <button
                  className="font-medium text-coral-500 hover:text-coral-600"
                  onClick={resetFilters}
                >
                  Сбросить фильтры
                </button>
              )}
            </Card>
          ) : (
            <div className="space-y-3">
              <RecordsTable data={rows} />
              <div ref={loadMoreRef} className="h-1" />
              <p className="text-xs text-ink-muted">
                {archive.isFetchingNextPage
                  ? "Загрузка…"
                  : `Показано ${rows.length} из ${total}`}
              </p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
