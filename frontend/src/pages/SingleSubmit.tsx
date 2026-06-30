import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { ApiError } from "@/api/client";
import { Button, Card, Input } from "@/components/ui";

export default function SingleSubmit() {
  const { recId } = useParams<{ recId: string }>();
  const nav = useNavigate();
  const [diarization, setDiarization] = React.useState("pyannote");
  const [numSpeakers, setNumSpeakers] = React.useState("");
  const [glossary, setGlossary] = React.useState(true);
  const [secondOpinion, setSecondOpinion] = React.useState(false);
  const [wordTimestamps, setWordTimestamps] = React.useState(false);
  const [preclean, setPreclean] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const job = await api.submitJob({
        recording_id: recId,
        diarization,
        num_speakers: numSpeakers ? Number(numSpeakers) : null,
        glossary,
        second_opinion: secondOpinion,
        word_timestamps: wordTimestamps,
        preclean,
      });
      nav(`/jobs/${job.job_id}`);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 400
          ? "Диаризация требует HF_TOKEN на сервере. Выберите «без диаризации» или настройте токен."
          : e instanceof Error
            ? e.message
            : "Ошибка",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Одиночный микс</h2>
      <Card className="space-y-4 p-6">
        <label className="block">
          <span className="text-sm text-slate-600">Диаризация</span>
          <select
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            value={diarization}
            onChange={(e) => setDiarization(e.target.value)}
          >
            <option value="pyannote">pyannote (нужен HF_TOKEN)</option>
            <option value="none">без диаризации</option>
          </select>
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">Число спикеров (опц.)</span>
          <Input
            type="number"
            value={numSpeakers}
            onChange={(e) => setNumSpeakers(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={glossary}
            onChange={(e) => setGlossary(e.target.checked)}
          />
          Канонизация имён/терминов (глоссарий)
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={secondOpinion}
            onChange={(e) => setSecondOpinion(e.target.checked)}
          />
          Второе мнение (L2 faster-whisper)
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={wordTimestamps}
            onChange={(e) => setWordTimestamps(e.target.checked)}
          />
          Пословные таймкоды
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={preclean}
            onChange={(e) => setPreclean(e.target.checked)}
          />
          Аудио-предочистка (highpass + loudnorm)
        </label>
        {error && <div className="text-sm text-red-600">{error}</div>}
        <Button onClick={submit} disabled={busy}>
          {busy ? "Запуск…" : "Запустить"}
        </Button>
      </Card>
    </div>
  );
}
