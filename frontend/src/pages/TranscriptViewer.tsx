import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.esm.js";
import TimelinePlugin from "wavesurfer.js/dist/plugins/timeline.esm.js";
import { api } from "@/api/client";
import type { Job, Segment } from "@/api/types";
import { Badge, Button, Card, StageBar } from "@/components/ui";
import { cn, fmtTime } from "@/lib/utils";

const PROV_TONE: Record<string, "slate" | "green" | "amber" | "blue" | "violet" | "red"> = {
  gigaam: "slate",
  glossary: "blue",
  "second-opinion": "violet",
  voiceprint: "green",
  human: "amber",
};
const ACTIVE: Job["state"][] = [
  "queued", "preclean", "vad", "diarization", "asr", "quality", "formatting",
];

export default function TranscriptViewer() {
  const { jobId } = useParams<{ jobId: string }>();

  // 1) Статус джобы: поллим, пока она активна (после submit она ещё queued).
  const jobQ = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.state) ? 1500 : false),
  });
  const job = jobQ.data;
  const done = job?.state === "done";

  // 2) Результат — только когда джоба завершена.
  const resultQ = useQuery({
    queryKey: ["result", jobId],
    queryFn: () => api.result(jobId!),
    enabled: !!jobId && done,
  });

  if (jobQ.isError) return <ErrorCard msg="Джоба не найдена" />;
  if (!job) return <div className="text-slate-500">Загрузка…</div>;
  if (job.state === "error")
    return <ErrorCard msg={job.error_message ?? "Ошибка обработки"} />;
  if (job.state === "canceled") return <ErrorCard msg="Джоба отменена" />;
  if (!done)
    return (
      <Card className="space-y-3 p-6">
        <h2 className="text-lg font-semibold">Обработка…</h2>
        <StageBar pct={job.stage_pct} state={job.state} />
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          ← к списку джоб
        </Link>
      </Card>
    );
  if (resultQ.isError) return <ErrorCard msg="Не удалось загрузить транскрипт" />;
  if (!resultQ.data) return <div className="text-slate-500">Загрузка транскрипта…</div>;

  return <Viewer jobId={jobId!} segments={resultQ.data.segments} meta={resultQ.data.metadata} refetch={resultQ.refetch} />;
}

function ErrorCard({ msg }: { msg: string }) {
  return (
    <Card className="space-y-2 p-6">
      <div className="text-red-600">{msg}</div>
      <Link to="/" className="text-sm text-blue-600 hover:underline">← к списку джоб</Link>
    </Card>
  );
}

function Viewer({
  jobId,
  segments,
  meta,
  refetch,
}: {
  jobId: string;
  segments: Segment[];
  meta: Record<string, unknown> & { duration?: number; model?: string; device_fallback?: boolean };
  refetch: () => void;
}) {
  const waveRef = React.useRef<HTMLDivElement>(null);
  const wsRef = React.useRef<WaveSurfer | null>(null);
  const segsRef = React.useRef<Segment[]>(segments);
  const [current, setCurrent] = React.useState(-1);

  // segsRef всегда актуален — wavesurfer его читает, не пересоздаваясь.
  React.useEffect(() => {
    segsRef.current = segments;
  });

  // wavesurfer создаётся ОДИН раз на jobId (правка имён не трогает плеер).
  React.useEffect(() => {
    if (!waveRef.current) return;
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: waveRef.current,
      height: 80,
      waveColor: "#cbd5e1",
      progressColor: "#3b82f6",
      url: api.audioUrl(jobId),
      plugins: [regions, TimelinePlugin.create()],
    });
    wsRef.current = ws;
    ws.on("decode", () => {
      segsRef.current.forEach((s, i) =>
        regions.addRegion({
          start: s.start,
          end: s.end,
          drag: false,
          resize: false,
          color: i % 2 ? "rgba(59,130,246,0.08)" : "rgba(148,163,184,0.10)",
        }),
      );
    });
    regions.on("region-clicked", (region, e) => {
      e.stopPropagation();
      region.play();
    });
    ws.on("timeupdate", (t) => {
      setCurrent(segsRef.current.findIndex((s) => t >= s.start && t < s.end));
    });
    return () => ws.destroy();
  }, [jobId]);

  function seekTo(s: Segment) {
    const ws = wsRef.current;
    if (ws && ws.getDuration()) ws.setTime(s.start);
  }

  async function rename(original: string, value: string) {
    await api.putSpeakers(jobId, { [original]: value }).catch(() => {});
    refetch();
  }

  const speakers = Array.from(new Set(segments.map((s) => s.speaker).filter(Boolean))) as string[];
  const legendTone = (i: number) =>
    (["blue", "violet", "green", "amber", "slate", "red"] as const)[i % 6];

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">Транскрипт</h2>
          {meta.duration != null && <Badge>{fmtTime(Number(meta.duration))}</Badge>}
          {meta.model && <Badge tone="slate">{String(meta.model)}</Badge>}
          {meta.device_fallback && <Badge tone="amber">GPU→CPU, медленно</Badge>}
          <div className="ml-auto flex gap-2">
            {(["txt", "json", "srt", "vtt"] as const).map((f) => (
              <a key={f} href={api.downloadUrl(jobId, f)}>
                <Button variant="outline">{f}</Button>
              </a>
            ))}
          </div>
        </div>
        {speakers.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {speakers.map((sp, i) => (
              <Badge key={sp} tone={legendTone(i)}>{sp}</Badge>
            ))}
          </div>
        )}
      </Card>

      <Card className="p-4">
        <div ref={waveRef} />
        <div className="mt-2">
          <Button variant="outline" onClick={() => wsRef.current?.playPause()}>▶ / ⏸</Button>
        </div>
      </Card>

      <Card className="divide-y divide-slate-100">
        {segments.map((s, i) => (
          <SegmentRow
            key={i}
            seg={s}
            active={i === current}
            onSeek={() => seekTo(s)}
            onRename={rename}
          />
        ))}
      </Card>
    </div>
  );
}

function SegmentRow({
  seg,
  active,
  onSeek,
  onRename,
}: {
  seg: Segment;
  active: boolean;
  onSeek: () => void;
  onRename: (original: string, value: string) => void;
}) {
  const [draft, setDraft] = React.useState(seg.speaker ?? "");
  React.useEffect(() => setDraft(seg.speaker ?? ""), [seg.speaker]);
  // ключ правки — стабильный сырой ярлык (иначе повторное переименование теряется)
  const key = seg.original_speaker ?? seg.speaker;
  const editable = !!key;

  return (
    <div className={cn("p-3", active && "bg-blue-50")}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <button onClick={onSeek} className="font-mono text-xs text-blue-600 hover:underline">
          {fmtTime(seg.start)}
        </button>
        <input
          className={cn(
            "h-7 w-44 rounded-md border border-slate-300 px-2 text-xs outline-none focus:ring-2 focus:ring-slate-400",
            !editable && "bg-slate-100 text-slate-400",
          )}
          value={draft}
          disabled={!editable}
          placeholder={editable ? "" : "без спикера"}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => editable && draft && draft !== seg.speaker && onRename(key!, draft)}
        />
        {seg.provenance ? (
          <Badge tone={PROV_TONE[seg.provenance] ?? "slate"}>{seg.provenance}</Badge>
        ) : (
          <Badge tone="slate">gigaam</Badge>
        )}
        {seg.confidence != null && (
          <span className="text-xs text-slate-400">conf {seg.confidence.toFixed(2)}</span>
        )}
        {seg.speaker_confidence != null && (
          <span className="text-xs text-slate-400">spk {seg.speaker_confidence.toFixed(2)}</span>
        )}
        {seg.flags?.includes("hallucination_suspect") && <Badge tone="red">галлюцинация?</Badge>}
        {seg.flags?.includes("loop_suspect") && <Badge tone="amber">повтор?</Badge>}
      </div>
      <div className="text-sm text-slate-800">{seg.text}</div>
    </div>
  );
}
