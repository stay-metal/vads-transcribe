import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Job } from "@/api/types";
import { Badge, Button, Card, StageBar } from "@/components/ui";

const ACTIVE: Job["state"][] = [
  "queued", "preclean", "vad", "diarization", "asr", "quality", "formatting",
];

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
    // poll, пока есть активные джобы (грубый стадийный прогресс — v1)
    refetchInterval: (q) =>
      q.state.data?.jobs.some((j) => ACTIVE.includes(j.state)) ? 1500 : false,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Джобы</h2>
        <Link to="/upload">
          <Button>Новая запись</Button>
        </Link>
      </div>
      {isLoading && <div className="text-slate-500">Загрузка…</div>}
      {data && data.jobs.length === 0 && (
        <Card className="p-6 text-slate-500">Пока нет джоб. Загрузите запись.</Card>
      )}
      <div className="space-y-2">
        {data?.jobs.map((j) => (
          <Card key={j.id} className="flex items-center gap-4 p-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Badge tone={j.mode === "route_a" ? "violet" : "blue"}>
                  {j.mode === "route_a" ? "Route A" : "Микс"}
                </Badge>
                {j.device_fallback && <Badge tone="amber">GPU→CPU</Badge>}
                <span className="truncate font-mono text-xs text-slate-400">{j.id.slice(0, 8)}</span>
              </div>
              <div className="mt-2 max-w-md">
                <StageBar pct={j.stage_pct} state={j.state} />
              </div>
              {j.state === "error" && (
                <div className="mt-1 text-sm text-red-600">{j.error_message}</div>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              {j.state === "queued" && (
                <Button
                  variant="outline"
                  onClick={() => api.cancelJob(j.id).catch(() => {})}
                >
                  Отменить
                </Button>
              )}
              {j.state === "done" && (
                <Link to={`/jobs/${j.id}`}>
                  <Button>Открыть</Button>
                </Link>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
