import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Job, JobsPage } from "@/api/types";
import { Badge, Button, Card, StageBar, StatusPill, Mono } from "@/components/ui";
import { IconChevronDown, IconChevronRight } from "@/components/icons";
import { MODE_LABEL, STATUS_META, fmtDuration, fmtDateTime, parseRecordingTitle } from "@/lib/utils";
import { VoiceGlyph } from "./VoiceGlyph";
import { SOURCE_META, estimate, queueWait, fmtElapsed, fmtEta } from "./helpers";

/* ─── Активная запись: карточка с прогрессом и деталями по клику ─────── */
export function ActiveJobCard({ job, page, now }: { job: Job; page: JobsPage; now: number }) {
  const [open, setOpen] = React.useState(false);
  const qc = useQueryClient();
  // Отмена: раньше ошибка глоталась — теперь показываем сбой и обновляем список.
  const cancelMut = useMutation({
    mutationFn: () => api.cancelJob(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", "active"] }),
  });
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
              <div className="flex flex-col items-end gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cancelMut.mutate()}
                  disabled={cancelMut.isPending}
                >
                  {cancelMut.isPending ? "Отменяем…" : "Отменить"}
                </Button>
                {cancelMut.isError && <span className="text-xs text-coral-600">Не удалось отменить</span>}
              </div>
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
