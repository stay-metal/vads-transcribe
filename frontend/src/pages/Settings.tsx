import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import {
  Button,
  Card,
  Field,
  Input,
  Loading,
  SectionTitle,
  Select,
  Spinner,
  Tabs,
  SpeakerNode,
  ErrorCard,
} from "@/components/ui";
import { IconArchive, IconActivity, IconBook, IconTrash, IconUsers } from "@/components/icons";

type Tab = "format" | "voices" | "retention" | "health";

export default function Settings() {
  const [tab, setTab] = React.useState<Tab>("format");
  return (
    <div className="space-y-6">
      <SectionTitle
        eyebrow="Конфигурация"
        title="Настройки"
        desc="Формат транскрипта, галереи голосов, хранение и состояние сервиса."
      />
      <div className="grid gap-6 md:grid-cols-[200px_1fr]">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "format", label: "Формат", icon: <IconBook size={17} /> },
            { value: "voices", label: "Голоса", icon: <IconUsers size={17} /> },
            { value: "retention", label: "Хранение", icon: <IconArchive size={17} /> },
            { value: "health", label: "Здоровье", icon: <IconActivity size={17} /> },
          ]}
        />
        <div>
          {tab === "format" && <FormatSection />}
          {tab === "voices" && <VoicesSection />}
          {tab === "retention" && <RetentionSection />}
          {tab === "health" && <HealthSection />}
        </div>
      </div>
    </div>
  );
}

/* ─── Формат транскрипта ─────────────────────────────────────────────── */
function FormatSection() {
  const { data, isLoading, refetch } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const [fmt, setFmt] = React.useState("md");
  const [busy, setBusy] = React.useState(false);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (data) setFmt(data.transcript_format);
  }, [data]);

  async function save() {
    setBusy(true);
    setSaved(false);
    try {
      await api.putSettings(fmt);
      setSaved(true);
      refetch();
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <Loading label="Загрузка…" />;

  return (
    <Card className="space-y-4 p-5">
      <div>
        <div className="text-sm font-medium text-ink">Формат транскрипта</div>
        <p className="mt-1 text-xs leading-snug text-ink-muted">
          Формат по умолчанию для файла транскрипта (пайплайн пишет его на диск, и им же
          «Обновить файл» сохраняет правки). Скачать в любом формате можно всегда.
        </p>
      </div>
      <Field label="Формат по умолчанию">
        <Select value={fmt} onChange={(e) => setFmt(e.target.value)} className="max-w-64">
          <option value="md">Markdown (.md) — по умолчанию</option>
          <option value="txt">Текст (.txt)</option>
          <option value="json">JSON (.json)</option>
          <option value="srt">Субтитры (.srt)</option>
          <option value="vtt">Веб-субтитры (.vtt)</option>
        </Select>
      </Field>
      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={busy}>
          {busy ? "Сохраняем…" : "Сохранить"}
        </Button>
        {saved && <span className="text-sm text-emerald-600">Сохранено</span>}
      </div>
    </Card>
  );
}

/* ─── Голоса (галереи voiceprint) ────────────────────────────────────── */
function VoicesSection() {
  const [building, setBuilding] = React.useState<string | null>(null);
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["galleries"],
    queryFn: api.listGalleries,
    // Пока идёт сборка — поллим, пока галерея не появится в списке.
    refetchInterval: (q) =>
      building && !q.state.data?.galleries.some((g) => g.name === building) ? 3000 : false,
  });
  const galleries = data?.galleries ?? [];

  // Сборка завершилась → снять индикатор.
  React.useEffect(() => {
    if (building && galleries.some((g) => g.name === building)) setBuilding(null);
  }, [galleries, building]);

  const [busy, setBusy] = React.useState<string | null>(null);
  async function remove(name: string) {
    setBusy(name);
    try {
      await api.deleteGallery(name);
      refetch();
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) return <Loading label="Загрузка галерей…" />;

  return (
    <div className="space-y-4">
      <CreateGalleryCard existing={galleries.map((g) => g.name)} onBuilding={setBuilding} onDone={refetch} />

      {building && (
        <Card className="flex items-center gap-3 px-5 py-3">
          <Spinner className="h-4 w-4" />
          <span className="text-sm text-ink-muted">
            Собираем галерею <span className="font-medium text-ink">{building}</span> — появится через минуту.
          </span>
        </Card>
      )}

      {galleries.length === 0 && !building ? (
        <Card className="px-5 py-8 text-center text-sm text-ink-muted">Пока нет галерей.</Card>
      ) : (
        galleries.length > 0 && (
          <Card className="divide-y divide-line/70 overflow-hidden">
            {galleries.map((g) => (
              <div key={g.name} className="flex items-center gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink">{g.name}</div>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {g.voices.map((v) => (
                      <SpeakerNode key={v} name={v} size={7} />
                    ))}
                  </div>
                </div>
                <button
                  onClick={() => remove(g.name)}
                  disabled={busy === g.name}
                  className="shrink-0 rounded-control p-2 text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
                  aria-label="Удалить галерею"
                >
                  <IconTrash size={16} />
                </button>
              </div>
            ))}
          </Card>
        )
      )}
    </div>
  );
}

function CreateGalleryCard({
  existing,
  onBuilding,
  onDone,
}: {
  existing: string[];
  onBuilding: (name: string) => void;
  onDone: () => void;
}) {
  const [name, setName] = React.useState("");
  const [files, setFiles] = React.useState<File[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  const nameOk = /^[A-Za-z0-9_-]+$/.test(name);
  const dup = existing.includes(name);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.createGallery(name, files);
      onBuilding(name);
      setName("");
      setFiles([]);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось запустить сборку");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-4 p-5">
      <div>
        <div className="text-sm font-medium text-ink">Новая галерея</div>
        <p className="mt-1 text-xs leading-snug text-ink-muted">
          Именует спикеров в общем миксе по голосу. Загрузите по одному образцу на человека —
          имя файла станет именем голоса.
        </p>
      </div>
      <Field label="Название" hint="Латиница, цифры, дефис и подчёркивание.">
        <Input value={name} placeholder="nasha-komanda" onChange={(e) => setName(e.target.value)} />
      </Field>
      <div>
        <label className="flex cursor-pointer items-center justify-between rounded-control border border-dashed border-line px-4 py-3 text-sm transition-colors hover:bg-canvas">
          <span className="text-ink-muted">
            {files.length ? `Выбрано образцов: ${files.length}` : "Выбрать образцы голосов"}
          </span>
          <span className="text-coral-500">
            <IconUsers size={18} />
          </span>
          <input
            type="file"
            multiple
            accept="audio/*,video/*"
            className="hidden"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        {files.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {files.map((f, i) => (
              <SpeakerNode key={i} name={f.name.replace(/\.[^.]+$/, "")} size={7} />
            ))}
          </div>
        )}
      </div>
      {name && !nameOk && <p className="text-xs text-coral-600">Только латиница/цифры/-/_.</p>}
      {dup && <p className="text-xs text-coral-600">Галерея с таким именем уже есть.</p>}
      {err && <ErrorCard title={err} />}
      <Button onClick={submit} disabled={busy || !nameOk || dup || files.length === 0}>
        {busy ? "Запускаем…" : "Собрать галерею"}
      </Button>
    </Card>
  );
}

/* ─── Хранение ───────────────────────────────────────────────────────── */
function RetentionSection() {
  const rows = [
    { k: "Рабочие файлы", v: "удаляются сразу после обработки" },
    { k: "Загрузки", v: "хранятся 7 дней" },
    { k: "Результаты и транскрипты", v: "хранятся 30 дней" },
  ];
  return (
    <Card className="divide-y divide-line/70 overflow-hidden">
      {rows.map((r) => (
        <div key={r.k} className="flex items-center justify-between px-5 py-3.5">
          <span className="text-sm text-ink">{r.k}</span>
          <span className="text-[13px] text-ink-muted">{r.v}</span>
        </div>
      ))}
      <p className="px-5 py-3 text-xs text-ink-muted">Очистка идёт по расписанию автоматически.</p>
    </Card>
  );
}

/* ─── Здоровье ───────────────────────────────────────────────────────── */
function HealthSection() {
  const { data: ready } = useQuery({
    queryKey: ["ready"],
    queryFn: api.ready,
    refetchInterval: (q) => (q.state.data ? 30000 : 4000),
  });
  const ok = ready === true;
  return (
    <Card className="p-5">
      <div className="flex items-center gap-3">
        <span className={"h-2.5 w-2.5 rounded-full " + (ok ? "bg-emerald-500" : "animate-pulse-node bg-amber-400")} />
        <div>
          <div className="text-sm font-medium text-ink">{ok ? "Модель готова" : "Модель прогревается"}</div>
          <p className="text-[13px] text-ink-muted">
            {ok ? "GPU-воркер держит тёплую модель — задачи стартуют сразу." : "Первый запуск после старта сервера занимает время."}
          </p>
        </div>
      </div>
    </Card>
  );
}
