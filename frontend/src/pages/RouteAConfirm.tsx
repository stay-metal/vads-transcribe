import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { TrackRef } from "@/api/types";
import { Button, Card, Input, Loading, SectionTitle } from "@/components/ui";
import { IconTrash, IconUsers } from "@/components/icons";
import { SPEAKER_COLORS } from "@/lib/utils";

export default function RouteAConfirm() {
  const { recId } = useParams<{ recId: string }>();
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
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
    <div className="space-y-6">
      <SectionTitle
        eyebrow="По дорожкам"
        title="Участники"
        desc="Имена дорожек станут метками спикеров — точными и без диаризации. Поправьте, если нужно."
      />

      {isLoading && <Loading label="Читаем дорожки…" />}

      <Card className="divide-y divide-line/70 overflow-hidden">
        {tracks.map((t, i) => (
          <div key={t.id} className="flex items-center gap-3 px-4 py-3">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-white"
              style={{ background: SPEAKER_COLORS[i % SPEAKER_COLORS.length] }}
            />
            <Input
              value={t.name}
              placeholder="Имя участника"
              onChange={(e) =>
                setTracks((ts) => ts.map((x) => (x.id === t.id ? { ...x, name: e.target.value } : x)))
              }
            />
            <button
              onClick={() => setTracks((ts) => ts.filter((x) => x.id !== t.id))}
              className="shrink-0 rounded-control p-2 text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
              aria-label="Убрать дорожку"
            >
              <IconTrash size={16} />
            </button>
          </div>
        ))}
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={submit} disabled={busy || tracks.length === 0}>
          <IconUsers size={17} />
          {busy ? "Запускаем…" : "Запустить транскрипцию"}
        </Button>
        <span className="text-xs text-ink-muted">{tracks.length} участников</span>
      </div>
    </div>
  );
}
