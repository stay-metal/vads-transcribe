import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.esm.js";
import TimelinePlugin from "wavesurfer.js/dist/plugins/timeline.esm.js";
import { api } from "@/api/client";
import type { Job, Segment } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  ErrorCard,
  SectionTitle,
  Spinner,
  StageBar,
  StatusPill,
  Mono,
  Toggle,
  SpeakerNode,
} from "@/components/ui";
import { IconPlay, IconPause, IconDownload } from "@/components/icons";
import { cn, fmtTime, SPEAKER_COLORS, ACTIVE_STATES, STATUS_META } from "@/lib/utils";

/** provenance → русская метка + тон бейджа. */
const PROV: Record<string, { label: string; tone: "neutral" | "azure" | "violet" | "green" | "coral" }> = {
  gigaam: { label: "модель", tone: "neutral" },
  glossary: { label: "словарь", tone: "azure" },
  "second-opinion": { label: "2-е мнение", tone: "violet" },
  voiceprint: { label: "голос", tone: "green" },
  human: { label: "правка", tone: "coral" },
};

const EXPORTS = [
  { fmt: "txt", label: "Текст", hint: "Протокол с именами" },
  { fmt: "json", label: "JSON", hint: "С метаданными" },
  { fmt: "srt", label: "SRT", hint: "Субтитры" },
  { fmt: "vtt", label: "VTT", hint: "Веб-субтитры" },
] as const;

export default function TranscriptViewer() {
  const { jobId } = useParams<{ jobId: string }>();

  const jobQ = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ACTIVE_STATES.includes(q.state.data.state) ? 1500 : false),
  });
  const job = jobQ.data;
  const done = job?.state === "done";

  const resultQ = useQuery({
    queryKey: ["result", jobId],
    queryFn: () => api.result(jobId!),
    enabled: !!jobId && done,
  });

  if (jobQ.isError) return <ViewerError title="Запись не найдена" />;
  if (!job) return <Loading label="Загрузка…" />;
  if (job.state === "error") return <ViewerError title="Обработка прервалась" detail={job.error_message} />;
  if (job.state === "canceled") return <ViewerError title="Задача отменена" />;
  if (!done) return <Processing job={job} />;
  if (resultQ.isError) return <ViewerError title="Не удалось загрузить транскрипт" />;
  if (!resultQ.data) return <Loading label="Загрузка транскрипта…" />;

  return (
    <Viewer
      jobId={jobId!}
      job={job}
      segments={resultQ.data.segments}
      meta={resultQ.data.metadata}
      refetch={resultQ.refetch}
    />
  );
}

function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-muted">
      <Spinner className="h-4 w-4" /> {label}
    </div>
  );
}

function ViewerError({ title, detail }: { title: string; detail?: string | null }) {
  return (
    <div className="space-y-4">
      <ErrorCard title={title} detail={detail} />
      <Link to="/" className="text-sm text-coral-500 hover:text-coral-600">← к списку записей</Link>
    </div>
  );
}

function Processing({ job }: { job: Job }) {
  const meta = STATUS_META[job.state];
  return (
    <Card className="space-y-4 p-6">
      <div className="flex items-center gap-3">
        <Spinner />
        <div>
          <StatusPill state={job.state} />
          <p className="mt-1.5 text-sm text-ink-muted">{meta.hint}</p>
        </div>
      </div>
      <StageBar pct={job.stage_pct} state={job.state} />
      <Link to="/" className="inline-block text-sm text-coral-500 hover:text-coral-600">← к списку записей</Link>
    </Card>
  );
}

function Viewer({
  jobId,
  job,
  segments,
  meta,
  refetch,
}: {
  jobId: string;
  job: Job;
  segments: Segment[];
  meta: Record<string, unknown> & { duration?: number; model?: string; device_fallback?: boolean };
  refetch: () => void;
}) {
  const waveRef = React.useRef<HTMLDivElement>(null);
  const wsRef = React.useRef<WaveSurfer | null>(null);
  const segsRef = React.useRef<Segment[]>(segments);
  const [current, setCurrent] = React.useState(-1);
  const [playing, setPlaying] = React.useState(false);
  const [now, setNow] = React.useState(0);
  const [heatmap, setHeatmap] = React.useState(false);

  React.useEffect(() => {
    segsRef.current = segments;
  });

  // wavesurfer создаётся ОДИН раз на jobId (правка имён не пересоздаёт плеер).
  React.useEffect(() => {
    if (!waveRef.current) return;
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: waveRef.current,
      height: 76,
      waveColor: "#c3ccd6",
      progressColor: "#e75740",
      cursorColor: "#e64f37",
      cursorWidth: 2,
      barWidth: 2,
      barGap: 1,
      barRadius: 3,
      url: api.audioUrl(jobId),
      plugins: [regions, TimelinePlugin.create()],
    });
    wsRef.current = ws;
    ws.on("decode", () => {
      const uniq = Array.from(new Set(segsRef.current.map((s) => s.speaker).filter(Boolean)));
      segsRef.current.forEach((s) => {
        const idx = s.speaker ? uniq.indexOf(s.speaker) : -1;
        const hex = idx >= 0 ? SPEAKER_COLORS[idx % SPEAKER_COLORS.length] : "#c3ccd6";
        regions.addRegion({ start: s.start, end: s.end, drag: false, resize: false, color: `${hex}14` });
      });
    });
    regions.on("region-clicked", (region, e) => {
      e.stopPropagation();
      region.play();
    });
    ws.on("play", () => setPlaying(true));
    ws.on("pause", () => setPlaying(false));
    ws.on("finish", () => setPlaying(false));
    ws.on("timeupdate", (t) => {
      setNow(t);
      setCurrent(segsRef.current.findIndex((s) => t >= s.start && t < s.end));
    });
    return () => ws.destroy();
  }, [jobId]);

  // auto-scroll активной реплики в видимую область во время проигрывания.
  React.useEffect(() => {
    if (current < 0 || !playing) return;
    document.getElementById(`seg-${current}`)?.scrollIntoView({ block: "nearest" });
  }, [current, playing]);

  function seekTo(s: Segment) {
    const ws = wsRef.current;
    if (ws && ws.getDuration()) ws.setTime(s.start);
  }

  async function rename(original: string, value: string) {
    await api.putSpeakers(jobId, { [original]: value }).catch(() => {});
    refetch();
  }

  const speakers = Array.from(new Set(segments.map((s) => s.speaker).filter(Boolean))) as string[];
  const colorOf = (sp?: string | null) =>
    sp && speakers.includes(sp)
      ? SPEAKER_COLORS[speakers.indexOf(sp) % SPEAKER_COLORS.length]
      : "#c3ccd6";
  const dur = meta.duration != null ? Number(meta.duration) : job.duration_sec ?? undefined;

  return (
    <div className="space-y-5">
      <SectionTitle
        eyebrow="Транскрипт"
        title="Разбор созвона"
        desc={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {dur != null && <Mono>{fmtTime(dur)}</Mono>}
            {meta.model && <Mono>{String(meta.model)}</Mono>}
            <Mono>{segments.length} реплик</Mono>
            {meta.device_fallback && <Badge tone="amber">GPU→CPU, медленно</Badge>}
          </span>
        }
      />

      {/* Легенда голосов — «кольцо» узлов-спикеров. */}
      {speakers.length > 0 && (
        <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">Голоса</span>
          {speakers.map((sp) => (
            <SpeakerNode key={sp} name={sp} color={colorOf(sp)} />
          ))}
        </Card>
      )}

      {/* Плеер */}
      <Card className="p-4">
        <div ref={waveRef} />
        <div className="mt-3 flex items-center gap-3">
          <Button size="sm" onClick={() => wsRef.current?.playPause()}>
            {playing ? <IconPause size={16} /> : <IconPlay size={16} />}
            {playing ? "Пауза" : "Играть"}
          </Button>
          <Mono>{fmtTime(now)}{dur != null ? ` / ${fmtTime(dur)}` : ""}</Mono>
          <div className="ml-auto">
            <Toggle
              checked={heatmap}
              onChange={setHeatmap}
              label={<span className="text-[13px]">Подсветка сомнительных мест</span>}
            />
          </div>
        </div>
      </Card>

      {/* Экспорт-пресеты */}
      <Card className="flex flex-wrap items-center gap-2 p-3">
        <span className="mr-1 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">
          <IconDownload size={14} /> Скачать
        </span>
        {EXPORTS.map((e) => (
          <a key={e.fmt} href={api.downloadUrl(jobId, e.fmt)} title={e.hint}>
            <Button variant="outline" size="sm">
              {e.label}
              <Mono className="text-ink-muted/70">.{e.fmt}</Mono>
            </Button>
          </a>
        ))}
        {typeof meta.l0_sha256 === "string" && (
          <a href={api.downloadUrl(jobId, "l0")} title="Сырой L0-субстрат (evidence) + sha256">
            <Button variant="outline" size="sm">
              L0
              <Mono className="text-ink-muted/70">.v1.jsonl</Mono>
            </Button>
          </a>
        )}
      </Card>

      {/* Реплики */}
      <Card className={cn("divide-y divide-line/70 overflow-hidden", heatmap && "heatmap")}>
        {segments.map((s, i) => (
          <SegmentRow
            key={i}
            idx={i}
            seg={s}
            color={colorOf(s.speaker)}
            active={i === current}
            onSeek={() => seekTo(s)}
            onRename={rename}
          />
        ))}
      </Card>
    </div>
  );
}

function confClass(c?: number | null): string {
  if (c == null) return "";
  if (c < 0.55) return "seg-low";
  if (c < 0.72) return "seg-mid";
  return "";
}

function SegmentRow({
  idx,
  seg,
  color,
  active,
  onSeek,
  onRename,
}: {
  idx: number;
  seg: Segment;
  color: string;
  active: boolean;
  onSeek: () => void;
  onRename: (original: string, value: string) => void;
}) {
  const [draft, setDraft] = React.useState(seg.speaker ?? "");
  React.useEffect(() => setDraft(seg.speaker ?? ""), [seg.speaker]);
  const key = seg.original_speaker ?? seg.speaker;
  const editable = !!key;
  const prov = seg.provenance ? PROV[seg.provenance] ?? { label: seg.provenance, tone: "neutral" as const } : PROV.gigaam;

  return (
    <div id={`seg-${idx}`} className={cn("px-4 py-3 transition-colors", active && "seg-active", confClass(seg.confidence))}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <button
          onClick={onSeek}
          className="tabular font-mono text-[12.5px] text-coral-500 transition-colors hover:text-coral-600"
        >
          {fmtTime(seg.start)}
        </button>

        <span
          className="inline-block h-2 w-2 shrink-0 rounded-full ring-2 ring-white"
          style={{ background: color }}
        />
        <input
          className={cn(
            "h-7 w-40 rounded-chip border border-transparent bg-transparent px-1.5 text-[13px] font-medium text-ink outline-none transition-colors",
            "hover:border-line focus:border-azure/60 focus:bg-white",
            !editable && "text-ink-muted/60",
          )}
          value={draft}
          disabled={!editable}
          placeholder={editable ? "" : "без имени"}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => editable && draft && draft !== seg.speaker && onRename(key!, draft)}
        />

        <Badge tone={prov.tone}>{prov.label}</Badge>
        {seg.confidence != null && <Mono className="text-ink-muted/70">{seg.confidence.toFixed(2)}</Mono>}
        {seg.flags?.includes("hallucination_suspect") && <Badge tone="coral">галлюцинация?</Badge>}
        {seg.flags?.includes("loop_suspect") && <Badge tone="amber">повтор?</Badge>}
      </div>
      <p className="pl-1 text-[15px] leading-relaxed text-ink">{seg.text}</p>
    </div>
  );
}
