import * as React from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { Button, Card } from "@/components/ui";

export default function Upload() {
  const nav = useNavigate();
  const [files, setFiles] = React.useState<File[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit() {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.upload(files);
      if (r.kind === "route_a") nav(`/recordings/${r.recording_id}/confirm`);
      else nav(`/recordings/${r.recording_id}/single`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Загрузка записи</h2>
      <Card className="p-6">
        <label
          className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 p-10 text-center hover:bg-slate-50"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            setFiles(Array.from(e.dataTransfer.files));
          }}
        >
          <span className="text-slate-600">
            Перетащите файлы или нажмите. Несколько файлов = подорожечно (Route A),
            один = микс.
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
          <ul className="mt-4 space-y-1 text-sm text-slate-700">
            {files.map((f, i) => (
              <li key={i}>• {f.name} ({(f.size / 1e6).toFixed(1)} МБ)</li>
            ))}
          </ul>
        )}
        {error && <div className="mt-3 text-sm text-red-600">{error}</div>}
        <div className="mt-4">
          <Button onClick={submit} disabled={busy || files.length === 0}>
            {busy ? "Загрузка…" : "Загрузить"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
