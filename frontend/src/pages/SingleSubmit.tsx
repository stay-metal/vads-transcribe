import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import { Button, Card, Field, Input, Select, Toggle, SectionTitle, ErrorCard } from "@/components/ui";
import { IconChevronDown, IconMic } from "@/components/icons";
import { cn } from "@/lib/utils";

export default function SingleSubmit() {
  const { recId } = useParams<{ recId: string }>();
  const nav = useNavigate();

  const [diarization, setDiarization] = React.useState("pyannote");
  const [numSpeakers, setNumSpeakers] = React.useState("");
  const [minSpeakers, setMinSpeakers] = React.useState("");
  const [maxSpeakers, setMaxSpeakers] = React.useState("");
  const [glossary, setGlossary] = React.useState(true);
  const [secondOpinion, setSecondOpinion] = React.useState(false);
  const [wordTimestamps, setWordTimestamps] = React.useState(false);
  const [preclean, setPreclean] = React.useState(false);
  const [voiceprint, setVoiceprint] = React.useState(false);
  const [emitL0, setEmitL0] = React.useState(false);
  const [backend, setBackend] = React.useState("torch");
  const [advanced, setAdvanced] = React.useState(false);

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
        min_speakers: minSpeakers ? Number(minSpeakers) : null,
        max_speakers: maxSpeakers ? Number(maxSpeakers) : null,
        glossary,
        second_opinion: secondOpinion,
        word_timestamps: wordTimestamps,
        preclean,
        voiceprint,
        emit_l0: emitL0,
        backend,
      });
      nav(`/jobs/${job.job_id}`);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 400
          ? "Диаризация требует HF_TOKEN на сервере. Выберите «без диаризации» или задайте токен в настройках."
          : e instanceof Error
            ? e.message
            : "Не удалось запустить",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionTitle eyebrow="Общий микс" title="Параметры распознавания" desc="Один файл со всеми голосами. Спикеров разделит диаризация." />

      <Card className="space-y-5 p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Диаризация" hint="Разделение голосов. Без неё — сплошной текст.">
            <Select value={diarization} onChange={(e) => setDiarization(e.target.value)}>
              <option value="pyannote">pyannote (нужен HF_TOKEN)</option>
              <option value="none">без диаризации</option>
            </Select>
          </Field>
          <Field label="Число спикеров" hint="Точное число, если известно. Иначе оставьте пустым.">
            <Input type="number" min={1} value={numSpeakers} onChange={(e) => setNumSpeakers(e.target.value)} placeholder="авто" />
          </Field>
        </div>

        <div className="space-y-3 border-t border-line pt-4">
          <Toggle checked={glossary} onChange={setGlossary} label="Словарь имён и терминов" hint="Канонизация латиницы/брендов. Кириллица не меняется." />
          <Toggle checked={secondOpinion} onChange={setSecondOpinion} label="Второе мнение" hint="Сверка с faster-whisper на спорных местах." />
          <Toggle checked={wordTimestamps} onChange={setWordTimestamps} label="Пословные таймкоды" hint="Точное время каждого слова." />
          <Toggle checked={preclean} onChange={setPreclean} label="Предочистка звука" hint="Highpass + нормализация громкости перед распознаванием." />
        </div>

        {/* Дополнительно */}
        <div className="border-t border-line pt-2">
          <button
            onClick={() => setAdvanced((v) => !v)}
            className="flex items-center gap-1.5 py-1 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
          >
            <IconChevronDown size={16} className={cn("transition-transform", advanced && "rotate-180")} />
            Дополнительно
          </button>
          {advanced && (
            <div className="mt-3 space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Мин. спикеров" hint="Нижняя граница диапазона.">
                  <Input type="number" min={1} value={minSpeakers} onChange={(e) => setMinSpeakers(e.target.value)} placeholder="—" />
                </Field>
                <Field label="Макс. спикеров" hint="Верхняя граница диапазона.">
                  <Input type="number" min={1} value={maxSpeakers} onChange={(e) => setMaxSpeakers(e.target.value)} placeholder="—" />
                </Field>
              </div>
              <Field label="Бэкенд распознавания" hint="ONNX-энкодер — для CPU-серверов (текст идентичен torch).">
                <Select value={backend} onChange={(e) => setBackend(e.target.value)}>
                  <option value="torch">torch (GPU/MPS)</option>
                  <option value="onnx">onnx-энкодер (CPU)</option>
                </Select>
              </Field>
              <div className="space-y-3">
                <Toggle checked={voiceprint} onChange={setVoiceprint} label="Голосовые отпечатки" hint="Именование спикеров по галерее голосов (ECAPA)." />
                <Toggle checked={emitL0} onChange={setEmitL0} label="Сырой L0-субстрат" hint="Слой transcript.v1.jsonl + sha256 для аудита." />
              </div>
            </div>
          )}
        </div>

        {error && <ErrorCard title={error} />}

        <Button onClick={submit} disabled={busy}>
          <IconMic size={17} />
          {busy ? "Запускаем…" : "Запустить транскрипцию"}
        </Button>
      </Card>
    </div>
  );
}
