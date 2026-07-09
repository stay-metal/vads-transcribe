import * as React from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { Button, Card, SectionTitle, ErrorCard, Mono } from "@/components/ui";
import { IconUpload, IconUsers, IconMic, IconTrash } from "@/components/icons";
import { plural } from "@/lib/utils";

export default function Upload() {
  const nav = useNavigate();
  const [files, setFiles] = React.useState<File[]>([]);
  const [dragging, setDragging] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const kind = files.length > 1 ? "route_a" : files.length === 1 ? "single" : null;

  async function submit() {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.upload(files);
      nav(r.kind === "route_a" ? `/recordings/${r.recording_id}/confirm` : `/recordings/${r.recording_id}/single`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionTitle eyebrow="Новая запись" title="Загрузить созвон" desc="Аудио или видео. Файлы не покидают сервер." />

      <div className="grid gap-3 sm:grid-cols-2">
        <PathCard
          active={kind === "route_a"}
          icon={<IconUsers size={18} />}
          title="По дорожкам"
          desc="Несколько файлов — по одному на участника. Имена берутся из дорожек: 100% точные, без диаризации."
        />
        <PathCard
          active={kind === "single"}
          icon={<IconMic size={18} />}
          title="Общий микс"
          desc="Один файл со всеми голосами. Спикеры разделяются диаризацией."
        />
      </div>

      <Card className="p-4">
        <label
          className={
            "flex cursor-pointer flex-col items-center justify-center rounded-card border-2 border-dashed px-6 py-12 text-center transition-colors " +
            (dragging ? "border-coral-500 bg-coral-soft" : "border-line hover:bg-canvas")
          }
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            setFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <span className="mb-3 grid h-11 w-11 place-items-center rounded-full bg-coral-soft text-coral-500">
            <IconUpload size={20} />
          </span>
          <span className="text-sm font-medium text-ink">Перетащите файлы или нажмите</span>
          <span className="mt-1 text-xs text-ink-muted">
            Несколько файлов → по дорожкам · один файл → общий микс
          </span>
          <input
            type="file"
            multiple
            className="hidden"
            accept="audio/*,video/*"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>

        {files.length > 0 && (
          <ul className="mt-4 space-y-1.5">
            {files.map((f, i) => (
              <li key={i} className="flex items-center gap-3 rounded-control bg-canvas px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{f.name}</span>
                <Mono>{(f.size / 1e6).toFixed(1)} МБ</Mono>
                <button
                  onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}
                  className="text-ink-muted transition-colors hover:text-coral-500"
                  aria-label="Убрать файл"
                >
                  <IconTrash size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}

        {error && <div className="mt-4"><ErrorCard title={error} /></div>}

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={submit} disabled={busy || files.length === 0}>
            {busy ? "Загружаем…" : "Загрузить"}
          </Button>
          {kind && (
            <span className="text-xs text-ink-muted">
              {files.length} {plural(files.length, ["файл", "файла", "файлов"])} ·{" "}
              {kind === "route_a" ? "по дорожкам" : "общий микс"}
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}

function PathCard({
  active,
  icon,
  title,
  desc,
}: {
  active: boolean;
  icon: React.ReactNode;
  title: string;
  desc: string;
}) {
  return (
    <Card className={"p-4 transition-colors " + (active ? "border-coral-500/40 bg-coral-soft/40" : "")}>
      <div className="mb-2 flex items-center gap-2">
        <span className={"grid h-8 w-8 place-items-center rounded-control " + (active ? "bg-coral-500 text-white" : "bg-coral-soft text-coral-500")}>
          {icon}
        </span>
        <span className="text-sm font-medium text-ink">{title}</span>
      </div>
      <p className="text-[13px] leading-snug text-ink-muted">{desc}</p>
    </Card>
  );
}
