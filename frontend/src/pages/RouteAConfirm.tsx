import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { TrackRef } from "@/api/types";
import { Button, Card, Input } from "@/components/ui";

export default function RouteAConfirm() {
  const { recId } = useParams<{ recId: string }>();
  const nav = useNavigate();
  const { data } = useQuery({
    queryKey: ["tracks", recId],
    queryFn: () => api.discoverTracks(recId!),
    enabled: !!recId,
  });
  const [tracks, setTracks] = React.useState<TrackRef[]>([]);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (data) setTracks(data.tracks);
  }, [data]);

  async function submit() {
    setBusy(true);
    try {
      await api.confirmTracks(recId!, tracks);
      const job = await api.submitJob({ recording_id: recId });
      nav(`/jobs/${job.job_id}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Участники (Route A)</h2>
      <p className="text-sm text-slate-500">
        Имена дорожек станут метками спикеров (ground-truth, без HF_TOKEN).
      </p>
      <Card className="divide-y divide-slate-100">
        {tracks.map((t, i) => (
          <div key={t.id} className="flex items-center gap-3 p-3">
            <span className="w-8 text-sm text-slate-400">#{i + 1}</span>
            <Input
              value={t.name}
              onChange={(e) =>
                setTracks((ts) =>
                  ts.map((x) => (x.id === t.id ? { ...x, name: e.target.value } : x)),
                )
              }
            />
            <Button
              variant="ghost"
              onClick={() => setTracks((ts) => ts.filter((x) => x.id !== t.id))}
            >
              Убрать
            </Button>
          </div>
        ))}
      </Card>
      <Button onClick={submit} disabled={busy || tracks.length === 0}>
        {busy ? "Запуск…" : "Запустить транскрипцию"}
      </Button>
    </div>
  );
}
