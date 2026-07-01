import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Job } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionTitle,
  Spinner,
  StageBar,
  StatusPill,
  Mono,
} from "@/components/ui";
import { IconMic, IconUsers } from "@/components/icons";
import { ACTIVE_STATES, MODE_LABEL, STATUS_META, fmtDuration, fmtDateTime } from "@/lib/utils";

function JobCard({ job }: { job: Job }) {
  const meta = STATUS_META[job.state];
  const active = ACTIVE_STATES.includes(job.state);
  const RouteIcon = job.mode === "route_a" ? IconUsers : IconMic;
  return (
    <Card className="p-4 transition-shadow hover:shadow-lift">
      <div className="flex items-start gap-4">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-control bg-coral-soft text-coral-500">
          <RouteIcon size={20} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-ink">{MODE_LABEL[job.mode] ?? job.mode}</span>
            <StatusPill state={job.state} />
            {job.device_fallback && <Badge tone="amber">GPU→CPU</Badge>}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-ink-muted">
            <Mono>#{job.id.slice(0, 8)}</Mono>
            <Mono>{fmtDuration(job.duration_sec)}</Mono>
            <span className="text-xs">{fmtDateTime(job.created_at)}</span>
          </div>

          {active && (
            <div className="mt-3 max-w-md space-y-1.5">
              <StageBar pct={job.stage_pct} state={job.state} />
              <p className="text-xs text-ink-muted">
                {meta.hint} <Mono className="text-ink-muted/80">{job.stage_pct}%</Mono>
              </p>
            </div>
          )}

          {job.state === "error" && (
            <p className="mt-2 text-[13px] text-coral-600">
              {job.error_message || meta.hint}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {job.state === "queued" && (
            <Button variant="outline" size="sm" onClick={() => api.cancelJob(job.id).catch(() => {})}>
              Отменить
            </Button>
          )}
          {job.state === "done" && (
            <Link to={`/jobs/${job.id}`}>
              <Button size="sm">Открыть</Button>
            </Link>
          )}
        </div>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
    refetchInterval: (q) =>
      q.state.data?.jobs.some((j) => ACTIVE_STATES.includes(j.state)) ? 1500 : false,
  });

  const jobs = data?.jobs ?? [];

  return (
    <div className="space-y-6">
      <SectionTitle
        eyebrow="Обработка"
        title="Записи"
        desc="Загруженные созвоны и их транскрипты."
        right={
          <Link to="/upload">
            <Button>
              <IconMic size={17} />
              Новая запись
            </Button>
          </Link>
        }
      />

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-ink-muted">
          <Spinner className="h-4 w-4" /> Загрузка…
        </div>
      )}

      {data && jobs.length === 0 && (
        <EmptyState
          icon={<IconMic size={22} />}
          title="Пока нет записей"
          desc="Загрузите созвон подорожечно или общим миксом — и получите транскрипт с именами участников."
          action={
            <Link to="/upload">
              <Button>Загрузить запись</Button>
            </Link>
          }
        />
      )}

      <div className="space-y-3">
        {jobs.map((j) => (
          <JobCard key={j.id} job={j} />
        ))}
      </div>
    </div>
  );
}
