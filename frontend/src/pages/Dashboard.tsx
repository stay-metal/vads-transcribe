import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { api } from "@/api/client";
import type { Job } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  SectionTitle,
  ErrorCard,
} from "@/components/ui";
import { IconMic, IconSearch, IconChevronRight, IconRefresh } from "@/components/icons";
import { cn, MODE_LABEL, fmtDuration, fmtRelative, fmtMeetingTime, parseRecordingTitle } from "@/lib/utils";
import { SOURCE_META, basename, processingSec, dayStartISO, dayEndISO } from "./dashboard/helpers";
import { VoiceGlyph } from "./dashboard/VoiceGlyph";
import { ActiveJobCard } from "./dashboard/ActiveJobCard";
import { DateFilter } from "./dashboard/DateFilter";

const PAGE = 25;

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
    id: "actions",
    header: () => null,
    cell: ({ row }) => <RerunCell job={row.original} />,
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
const META_COLS = new Set(["glyph", "actions", "chevron"]);

/** «Перетранскрибировать» для завершённой джобы: клон в очередь (без resume-кэша). */
function RerunCell({ job }: { job: Job }) {
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: () => api.rerunJob(job.id),
    // Новая джоба появляется в активной секции; архив обновится по завершении.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
  if (!["done", "error", "canceled"].includes(job.state)) return null;
  return (
    // stopPropagation на обёртке: строка таблицы кликабельна (переход к транскрипту)
    <span className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
      <IconButton
        label={mut.isError ? "Не удалось — попробовать ещё раз" : "Перезапустить"}
        onClick={() => mut.mutate()}
        disabled={mut.isPending}
      >
        <IconRefresh size={16} className={mut.isPending ? "animate-spin" : undefined} />
      </IconButton>
    </span>
  );
}

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
                        // Название поглощает свободную ширину и обрезается — иначе
                        // длинное имя распирает таблицу шире контейнера (гориз. скролл,
                        // колонки действий уезжают за край).
                        c.column.id === "title" && "w-full max-w-0",
                        c.column.id !== "title" && "whitespace-nowrap",
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
