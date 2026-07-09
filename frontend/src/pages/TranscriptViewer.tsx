import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  Loading,
  Spinner,
  StageBar,
  StatusPill,
  Mono,
  Toggle,
} from "@/components/ui";
import { IconPlay, IconPause, IconDownload, IconCheck, IconX } from "@/components/icons";
import { cn, fmtTime, parseRecordingTitle, SPEAKER_COLORS, ACTIVE_STATES, STATUS_META } from "@/lib/utils";

/** provenance → русская метка + тон бейджа. */
const PROV: Record<string, { label: string; tone: "neutral" | "azure" | "violet" | "green" | "coral" }> = {
  gigaam: { label: "модель", tone: "neutral" },
  glossary: { label: "словарь", tone: "azure" },
  "second-opinion": { label: "2-е мнение", tone: "violet" },
  voiceprint: { label: "голос", tone: "green" },
  human: { label: "правка", tone: "coral" },
};

const EXPORTS = [
  { fmt: "md", label: "Markdown", hint: "Протокол (по умолчанию)" },
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

  const qc = useQueryClient();
  React.useEffect(() => {
    if (!jobId || done) return;
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    es.addEventListener("job", (e) => {
      try {
        // SSE-payload — сырая строка jobs (title/track_count в ней null, без
        // обогащения из recordings) — переносим только НЕпустые поля, иначе
        // spread затирал бы обогащённый кэш null-ами.
        const raw = JSON.parse((e as MessageEvent).data) as Partial<Job>;
        const payload = Object.fromEntries(
          Object.entries(raw).filter(([, v]) => v !== null && v !== undefined),
        ) as Partial<Job>;
        qc.setQueryData<Job>(["job", jobId], (old) => (old ? { ...old, ...payload } : (raw as Job)));
        // На терминальном статусе перечитываем полную запись (с именем встречи).
        if (payload.state && !ACTIVE_STATES.includes(payload.state)) {
          qc.invalidateQueries({ queryKey: ["job", jobId] });
        }
      } catch {
        /* игнорируем битое событие */
      }
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId, done, qc]);

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

/* ─── Просмотрщик ────────────────────────────────────────────────────── */
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
  const [px, setPx] = React.useState(0); // px/сек зума (0 — вписано)
  const [writeMsg, setWriteMsg] = React.useState<string | null>(null);
  const [sel, setSel] = React.useState<{ text: string; x: number; y: number } | null>(null);
  const [popup, setPopup] = React.useState<string | null>(null); // выделенный текст для попапа словаря

  React.useEffect(() => {
    segsRef.current = segments;
  });

  // wavesurfer создаётся ОДИН раз на jobId (правки не пересоздают плеер).
  React.useEffect(() => {
    if (!waveRef.current) return;
    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: waveRef.current,
      height: 72,
      waveColor: "#c3ccd6",
      progressColor: "#e75740",
      cursorColor: "#e64f37",
      cursorWidth: 2,
      barWidth: 2,
      barGap: 1,
      barRadius: 3,
      autoScroll: false, // не дёргать вид назад к плейхеду при ручной прокрутке волны
      url: api.audioUrl(jobId),
      plugins: [regions, TimelinePlugin.create()],
    });
    wsRef.current = ws;
    // Доскролл списка реплик к фразе на волне (по времени клика/перехода).
    const scrollToTime = (t: number) => {
      const i = segsRef.current.findIndex((s) => t >= s.start && t < s.end);
      if (i >= 0) {
        lastWheel.current = 0; // явный переход по волне → возобновляем авто-follow
        document.getElementById(`seg-${i}`)?.scrollIntoView({ block: "center" });
      }
    };
    ws.on("decode", () => {
      const uniq = Array.from(new Set(segsRef.current.map((s) => s.original_speaker ?? s.speaker).filter(Boolean)));
      segsRef.current.forEach((s) => {
        const key = s.original_speaker ?? s.speaker;
        const idx = key ? uniq.indexOf(key) : -1;
        const hex = idx >= 0 ? SPEAKER_COLORS[idx % SPEAKER_COLORS.length] : "#c3ccd6";
        const reg = regions.addRegion({ start: s.start, end: s.end, drag: false, resize: false, color: `${hex}1f` });
        // Курсор-указатель + тултип говорящего на куске волны.
        const el = (reg as unknown as { element?: HTMLElement }).element;
        if (el) {
          el.style.cursor = "pointer";
          el.title = `${s.speaker || "без имени"} · ${fmtTime(s.start)}–${fmtTime(s.end)}`;
        }
      });
    });
    regions.on("region-clicked", (region, e) => {
      e.stopPropagation();
      region.play();
      scrollToTime(region.start + 0.001);
    });
    ws.on("play", () => setPlaying(true));
    ws.on("pause", () => setPlaying(false));
    ws.on("finish", () => setPlaying(false));
    ws.on("timeupdate", (t) => {
      setNow(t);
      setCurrent(segsRef.current.findIndex((s) => t >= s.start && t < s.end));
    });
    // Клик/перемотка по самой волне — тоже доскроллить к нужной реплике.
    ws.on("interaction", (t: number) => scrollToTime(t));
    return () => ws.destroy();
  }, [jobId]);

  // Ручная прокрутка (wheel/touch) временно отключает авто-follow, чтобы не «дёргало».
  const lastWheel = React.useRef(0);
  React.useEffect(() => {
    const mark = () => (lastWheel.current = Date.now());
    window.addEventListener("wheel", mark, { passive: true });
    window.addEventListener("touchmove", mark, { passive: true });
    return () => {
      window.removeEventListener("wheel", mark);
      window.removeEventListener("touchmove", mark);
    };
  }, []);

  React.useEffect(() => {
    if (current < 0 || !playing) return;
    if (Date.now() - lastWheel.current < 3000) return; // недавно листали руками — не мешаем
    document.getElementById(`seg-${current}`)?.scrollIntoView({ block: "nearest" });
  }, [current, playing]);

  function playFrom(s: Segment) {
    const ws = wsRef.current;
    if (!ws || !ws.getDuration()) return;
    ws.setTime(s.start);
    ws.play();
  }
  function zoom(factor: number) {
    const ws = wsRef.current;
    if (!ws || !ws.getDuration() || !waveRef.current) return;
    const fit = waveRef.current.clientWidth / ws.getDuration();
    const base = px || fit;
    const next = Math.min(600, Math.max(Math.max(1, fit), base * factor));
    setPx(next);
    ws.zoom(next);
  }

  // Правки спикеров/текста — через мутации; mutateAsync пробрасывает ошибку в
  // редактор, чтобы тот показал её и НЕ потерял черновик (баг: раньше глотали).
  const speakersMut = useMutation({
    mutationFn: (edits: Record<string, string>) => api.putSpeakers(jobId, edits),
    onSuccess: () => refetch(),
  });
  const segmentMut = useMutation({
    mutationFn: ({ index, text }: { index: number; text: string }) => api.putSegmentText(jobId, index, text),
    onSuccess: () => refetch(),
  });
  const renameSpeaker = (original: string, value: string) => speakersMut.mutateAsync({ [original]: value });
  const editText = (index: number, text: string) => segmentMut.mutateAsync({ index, text });
  async function writeFile() {
    setWriteMsg(null);
    try {
      const r = await api.writeTranscript(jobId);
      setWriteMsg(`Файл транскрипта обновлён: ${r.written}`);
    } catch {
      setWriteMsg("Не удалось обновить файл транскрипта");
    }
  }

  // Выделение текста в репликах → плавающая кнопка «Внести в словарь».
  function onMouseUp() {
    const s = window.getSelection();
    const text = s?.toString().trim() ?? "";
    if (text && text.length <= 90 && s && s.rangeCount) {
      const r = s.getRangeAt(0).getBoundingClientRect();
      setSel({ text, x: r.left + r.width / 2, y: r.top });
    } else {
      setSel(null);
    }
  }

  // Спикеры, ключуемые СЫРЫМ ярлыком (стабильно для повторной правки/цвета).
  const speakerMap = new Map<string, string>();
  segments.forEach((s) => {
    const orig = s.original_speaker ?? s.speaker;
    if (orig) speakerMap.set(orig, s.speaker ?? orig);
  });
  const originals = Array.from(speakerMap.keys());
  const colorOfOrig = (orig?: string | null) =>
    orig && originals.includes(orig) ? SPEAKER_COLORS[originals.indexOf(orig) % SPEAKER_COLORS.length] : "#c3ccd6";
  const dur = meta.duration != null ? Number(meta.duration) : job.duration_sec ?? undefined;

  return (
    <div className="space-y-4">
      {/* Липкий верх: шапка + действия + волна + контролы (реплики скроллятся под ним) */}
      <div className="sticky top-0 z-20 -mx-4 space-y-3 border-b border-line bg-canvas/95 px-4 py-3 backdrop-blur md:-mx-8 md:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5 font-mono text-[11px] uppercase tracking-[0.14em]">
              <Link
                to="/"
                className="flex items-center gap-1 text-ink-muted transition-colors hover:text-coral-500"
              >
                ← К записям
              </Link>
              <span className="text-coral-500">Транскрипт</span>
            </div>
            <h1 className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-lg font-semibold tracking-tightest text-ink">
              <span className="truncate" title={job.title ?? undefined}>{parseRecordingTitle(job.title).name || "Запись"}</span>
              <span className="flex items-center gap-x-2 text-xs font-normal text-ink-muted">
                {dur != null && <Mono>{fmtTime(dur)}</Mono>}
                <Mono>{segments.length} реплик</Mono>
                {meta.device_fallback && <Badge tone="amber">GPU→CPU</Badge>}
              </span>
            </h1>
          </div>
          {/* Скачать + обновить файл — в одной плашке */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-[0.1em] text-ink-muted">
              <IconDownload size={13} /> Скачать
            </span>
            {EXPORTS.map((e) => (
              <a key={e.fmt} href={api.downloadUrl(jobId, e.fmt)} title={e.hint}>
                <Button variant="outline" size="sm">
                  {e.label}
                </Button>
              </a>
            ))}
            <Button size="sm" variant="subtle" onClick={writeFile} title="Записать правки в файл транскрипта на диске">
              Обновить файл
            </Button>
          </div>
        </div>

        {/* Волна */}
        <div ref={waveRef} className="viewer-wave" />

        {/* Контролы */}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => wsRef.current?.playPause()}>
            {playing ? <IconPause size={16} /> : <IconPlay size={16} />}
            {playing ? "Пауза" : "Играть"}
          </Button>
          <Mono>
            {fmtTime(now)}
            {dur != null ? ` / ${fmtTime(dur)}` : ""}
          </Mono>
          <div className="ml-1 inline-flex overflow-hidden rounded-control border border-line">
            <button
              onClick={() => zoom(1 / 1.6)}
              className="px-2.5 py-1 text-sm text-ink-muted hover:bg-canvas hover:text-ink"
              title="Отдалить"
            >
              −
            </button>
            <span className="border-x border-line px-2 py-1 text-[11px] text-ink-muted">масштаб</span>
            <button
              onClick={() => zoom(1.6)}
              className="px-2.5 py-1 text-sm text-ink-muted hover:bg-canvas hover:text-ink"
              title="Приблизить"
            >
              +
            </button>
          </div>
          <div className="ml-auto">
            <Toggle
              checked={heatmap}
              onChange={setHeatmap}
              label={<span className="text-[13px]">Подсветка сомнительных мест</span>}
            />
          </div>
        </div>
        {writeMsg && <p className="text-xs text-emerald-600">{writeMsg}</p>}
      </div>

      {/* Голоса — глобальное переименование по всей записи */}
      {originals.length > 0 && (
        <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">Голоса</span>
          {originals.map((orig) => (
            <SpeakerEditor
              key={orig}
              original={orig}
              value={speakerMap.get(orig) ?? orig}
              color={colorOfOrig(orig)}
              onRename={renameSpeaker}
            />
          ))}
        </Card>
      )}

      {/* Реплики */}
      <Card className={cn("divide-y divide-line/70 overflow-hidden", heatmap && "heatmap")} onMouseUp={onMouseUp}>
        {segments.map((s, i) => (
          <SegmentRow
            key={i}
            idx={i}
            seg={s}
            color={colorOfOrig(s.original_speaker ?? s.speaker)}
            active={i === current}
            onPlay={() => playFrom(s)}
            onEditText={editText}
          />
        ))}
      </Card>

      {/* Плавающая подсказка «Внести в словарь» над выделением (с хвостиком). */}
      {sel && !popup && (
        <div
          className="fixed z-40 -translate-x-1/2 -translate-y-full animate-fade-up"
          style={{ left: sel.x, top: sel.y - 12 }}
        >
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setPopup(sel.text);
              setSel(null);
            }}
            className="block whitespace-nowrap rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white shadow-lift transition-colors hover:bg-ink/90"
          >
            Внести в словарь
          </button>
          {/* хвостик вниз */}
          <span className="absolute left-1/2 top-full h-0 w-0 -translate-x-1/2 border-x-[6px] border-t-[7px] border-x-transparent border-t-ink" />
        </div>
      )}
      {popup && <GlossaryPopup text={popup} onClose={() => setPopup(null)} />}
    </div>
  );
}

/* Редактор имени спикера (глобально по записи, ключ — сырой ярлык). */
function SpeakerEditor({
  original,
  value,
  color,
  onRename,
}: {
  original: string;
  value: string;
  color: string;
  onRename: (original: string, value: string) => Promise<unknown>;
}) {
  const [draft, setDraft] = React.useState(value);
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setDraft(value), [value]);
  async function commit() {
    const next = draft.trim();
    if (!next || next === value) return;
    setFailed(false);
    try {
      await onRename(original, next);
    } catch {
      setFailed(true); // черновик не теряем — подсвечиваем поле и даём повторить
    }
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-white" style={{ background: color }} />
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        title={failed ? "Не удалось сохранить имя — попробуйте ещё раз" : undefined}
        className={cn(
          "h-7 w-36 rounded-chip border bg-transparent px-1.5 text-[13px] font-medium text-ink outline-none transition-colors focus:bg-white",
          failed
            ? "border-coral-500/70 focus:border-coral-500"
            : "border-transparent hover:border-line focus:border-azure/60",
        )}
      />
    </span>
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
  onPlay,
  onEditText,
}: {
  idx: number;
  seg: Segment;
  color: string;
  active: boolean;
  onPlay: () => void;
  onEditText: (index: number, text: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(seg.text);
  const [saving, setSaving] = React.useState(false);
  const [saveErr, setSaveErr] = React.useState<string | null>(null);
  React.useEffect(() => setDraft(seg.text), [seg.text]);
  const prov = seg.provenance ? PROV[seg.provenance] ?? { label: seg.provenance, tone: "neutral" as const } : PROV.gigaam;

  // При ошибке сохранения — не закрываем правку и показываем причину (черновик цел).
  async function commit() {
    const next = draft.trim();
    if (!next || next === seg.text) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setSaveErr(null);
    try {
      await onEditText(idx, next);
      setEditing(false);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Не удалось сохранить правку");
    } finally {
      setSaving(false);
    }
  }

  // Клик по реплике — перемотка + проигрывание (но не мешаем выделению текста и правке).
  function onRowClick() {
    if (editing) return;
    if (window.getSelection()?.toString().trim()) return;
    onPlay();
  }

  return (
    <div
      id={`seg-${idx}`}
      onClick={onRowClick}
      className={cn(
        "group scroll-mt-64 px-4 py-3 transition-colors",
        !editing && "cursor-pointer hover:bg-coral-soft/30",
        active && "seg-active",
        confClass(seg.confidence),
      )}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPlay();
          }}
          className="tabular font-mono text-[12.5px] text-coral-500 transition-colors hover:text-coral-600"
          title="Проиграть с этого места"
        >
          {fmtTime(seg.start)}
        </button>
        <span className="inline-block h-2 w-2 shrink-0 rounded-full ring-2 ring-white" style={{ background: color }} />
        <span className="text-[13px] font-medium text-ink">{seg.speaker || "без имени"}</span>
        <Badge tone={prov.tone}>{prov.label}</Badge>
        {seg.confidence != null && <Mono className="text-ink-muted/70">{seg.confidence.toFixed(2)}</Mono>}
        {seg.flags?.includes("hallucination_suspect") && <Badge tone="coral">галлюцинация?</Badge>}
        {seg.flags?.includes("loop_suspect") && <Badge tone="amber">повтор?</Badge>}
        {!editing && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setEditing(true);
            }}
            className="ml-auto text-xs text-ink-muted opacity-0 transition-opacity hover:text-coral-500 group-hover:opacity-100"
          >
            Изменить
          </button>
        )}
      </div>

      {editing ? (
        <div className="pl-1">
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.max(2, Math.ceil(draft.length / 90))}
            className="w-full rounded-control border border-azure/60 bg-white px-3 py-2 text-[15px] leading-relaxed text-ink outline-none"
          />
          <div className="mt-2 flex items-center gap-2">
            <Button size="sm" onClick={commit} disabled={saving}>
              <IconCheck size={15} /> {saving ? "Сохраняем…" : "Сохранить"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={() => {
                setDraft(seg.text);
                setSaveErr(null);
                setEditing(false);
              }}
            >
              <IconX size={15} /> Отмена
            </Button>
          </div>
          {saveErr && <p className="mt-2 text-xs text-coral-600">{saveErr}</p>}
        </div>
      ) : (
        <p className="select-text pl-1 text-[15px] leading-relaxed text-ink">{seg.text}</p>
      )}
    </div>
  );
}

/* Попап «Внести в словарь»: выбор словаря (Имена/Термины) + каноничное написание. */
function GlossaryPopup({ text, onClose }: { text: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [which, setWhich] = React.useState<"people" | "terms">("terms");
  const [heard, setHeard] = React.useState(text);
  const [canon, setCanon] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  async function save() {
    if (!heard.trim() || !canon.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      // Свежий снэпшот прямо перед PUT — окно гонки read-modify-write сужается.
      const g = await api.getGlossary();
      const next = {
        people: { ...g.people },
        terms: { ...g.terms },
      };
      next[which][heard.trim()] = canon.trim();
      await api.putGlossary(next);
      qc.invalidateQueries({ queryKey: ["glossary"] });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/20 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md space-y-4 rounded-card border border-line bg-white p-5 shadow-lift"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-semibold text-ink">Внести в словарь</div>
        <div className="inline-flex rounded-full border border-line bg-white p-1">
          {(
            [
              ["terms", "Термины"],
              ["people", "Имена"],
            ] as const
          ).map(([w, label]) => (
            <button
              key={w}
              onClick={() => setWhich(w)}
              className={cn(
                "rounded-full px-3.5 py-1 text-sm transition-colors",
                which === w ? "bg-coral-soft font-medium text-coral-600" : "text-ink-muted hover:text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-[11px] text-ink-muted">Как слышится</span>
            <textarea
              value={heard}
              rows={1}
              onChange={(e) => setHeard(e.target.value)}
              className="min-h-[38px] w-full resize-y rounded-control border border-line bg-white px-3 py-2 text-sm leading-snug text-ink outline-none transition-colors focus:border-azure/70"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] text-ink-muted">Как писать</span>
            <textarea
              value={canon}
              rows={1}
              autoFocus
              placeholder="каноничное написание"
              onChange={(e) => setCanon(e.target.value)}
              className="min-h-[38px] w-full resize-y rounded-control border border-line bg-white px-3 py-2 text-sm leading-snug text-ink outline-none transition-colors placeholder:text-ink-muted/60 focus:border-azure/70"
            />
          </label>
        </div>
        {err && <p className="text-xs text-coral-600">{err}</p>}
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Отмена
          </Button>
          <Button size="sm" onClick={save} disabled={busy || !heard.trim() || !canon.trim()}>
            {busy ? "Сохраняем…" : "Добавить"}
          </Button>
        </div>
      </div>
    </div>
  );
}
