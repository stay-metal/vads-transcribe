import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type ScanProfileT, type YaEntry } from "@/api/client";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  SectionTitle,
  Select,
  Spinner,
  Toggle,
  Mono,
  ErrorCard,
} from "@/components/ui";
import {
  IconCloud,
  IconFolder,
  IconSearch,
  IconDownload,
  IconTrash,
  IconChevronDown,
  IconChevronRight,
} from "@/components/icons";
import { cn } from "@/lib/utils";

/* ─── Формат времени/интервала для статус-строки ─────────────────────── */
function fmtInterval(sec: number): string {
  if (sec % 3600 === 0) return `${sec / 3600} ч`;
  if (sec % 60 === 0) return `${sec / 60} мин`;
  return `${sec} с`;
}

function fmtAgo(iso?: string | null): string {
  if (!iso) return "ещё не сканировалась";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const DEFAULT_PROFILE: ScanProfileT = {
  layout: "zoom",
  tracks_subdir: "Audio Record",
  track_mode: "combine",
  parts_mode: "merge",
  media_suffixes: [".m4a", ".mp4", ".mov", ".mp3", ".wav"],
  skip_dirs: ["transcripts", "done"],
  output: { mode: "beside", subdir: "transcripts/dialogscribe", dir: null },
};

function profileFromData(sp?: Partial<ScanProfileT>): ScanProfileT {
  if (!sp || Object.keys(sp).length === 0) return DEFAULT_PROFILE;
  return {
    ...DEFAULT_PROFILE,
    ...sp,
    output: { ...DEFAULT_PROFILE.output, ...(sp.output ?? {}) },
  };
}

/* ─── Секция-аккордеон (прогрессивное раскрытие) ─────────────────────── */
function Accordion({
  title,
  summary,
  open,
  onToggle,
  children,
}: {
  title: string;
  summary?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const Chevron = open ? IconChevronDown : IconChevronRight;
  return (
    <div className="rounded-control border border-line">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-ink">{title}</div>
          {summary && !open && (
            <div className="mt-0.5 truncate text-xs text-ink-muted">{summary}</div>
          )}
        </div>
        <Chevron size={16} className="shrink-0 text-ink-muted" />
      </button>
      {open && <div className="border-t border-line px-4 py-4">{children}</div>}
    </div>
  );
}

/* ─── Инлайн-браузер каталогов сервера (паттерн Sonarr/Jellyfin). ─────── */
function FolderPicker({
  start,
  onSelect,
  onClose,
}: {
  start: string;
  onSelect: (p: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = React.useState(start);
  const { data, isLoading } = useQuery({
    queryKey: ["fs-browse", path],
    queryFn: () => api.fsBrowse(path),
  });
  const cur = data?.path ?? path;
  const segments = cur.split("/").filter(Boolean);
  return (
    <div className="rounded-control border border-line bg-canvas">
      {/* Навигация по серверной ФС: Домой + кликабельные хлебные крошки */}
      <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-2 text-xs">
        <button
          type="button"
          onClick={() => setPath("")}
          className="shrink-0 rounded px-1.5 py-0.5 font-medium text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
          title="В начало"
        >
          Домой
        </button>
        <div className="flex min-w-0 flex-1 items-center overflow-x-auto">
          <span className="shrink-0 text-ink-muted/50">/</span>
          {segments.map((seg, i) => {
            const p = "/" + segments.slice(0, i + 1).join("/");
            const last = i === segments.length - 1;
            return (
              <React.Fragment key={p}>
                <button
                  type="button"
                  onClick={() => setPath(p)}
                  className={cn(
                    "shrink-0 rounded px-1 py-0.5 transition-colors hover:text-coral-500",
                    last ? "font-medium text-ink" : "text-ink-muted",
                  )}
                >
                  {seg}
                </button>
                {!last && <span className="shrink-0 text-ink-muted/50">/</span>}
              </React.Fragment>
            );
          })}
        </div>
        {isLoading && <Spinner className="h-3.5 w-3.5 shrink-0" />}
      </div>
      <ul className="max-h-56 overflow-y-auto">
        {data?.parent && (
          <li>
            <button
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-ink hover:bg-canvas"
              onClick={() => setPath(data.parent!)}
            >
              <IconFolder size={14} className="text-ink-muted" /> ..
            </button>
          </li>
        )}
        {data?.denied && <li className="px-3 py-2 text-xs text-ink-muted">Нет доступа к папке.</li>}
        {data && !data.denied && data.dirs.length === 0 && (
          <li className="px-3 py-2 text-xs text-ink-muted">Подпапок нет.</li>
        )}
        {data?.dirs.map((d) => (
          <li key={d.path}>
            <button
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-ink hover:bg-coral-soft/50"
              onClick={() => setPath(d.path)}
            >
              <IconFolder size={14} className="text-ink-muted" />
              <span className="min-w-0 truncate">{d.name}</span>
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2">
        <Button size="sm" onClick={() => data && onSelect(data.path)} disabled={!data}>
          Выбрать эту папку
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Отмена
        </Button>
        <span className="ml-auto text-[11px] text-ink-muted/70">Папка на сервере</span>
      </div>
    </div>
  );
}

/** Поле «путь + Обзор…» с раскрывающимся FolderPicker. */
function PathField({
  label,
  hint,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <Field label={label} hint={hint}>
      <div className="space-y-2">
        <div className="flex gap-2">
          <Input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
          <Button variant="outline" onClick={() => setOpen((v) => !v)}>
            <IconFolder size={15} />
            Обзор…
          </Button>
        </div>
        {open && (
          <FolderPicker
            start={value.trim() || ""}
            onSelect={(p) => {
              onChange(p);
              setOpen(false);
            }}
            onClose={() => setOpen(false)}
          />
        )}
      </div>
    </Field>
  );
}

/* ─── Пресеты раскладки как чипы (именованные профили) ────────────────── */
function PresetChips({
  presets,
  activeId,
  onPick,
  onDelete,
}: {
  presets: { id: string; name: string; builtin: boolean }[];
  activeId: string;
  onPick: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {presets.map((p) => {
        const active = p.id === activeId;
        return (
          <span
            key={p.id}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-colors",
              active
                ? "border-coral-500/40 bg-coral-soft font-medium text-coral-600"
                : "border-line bg-white text-ink-muted hover:text-ink",
            )}
          >
            <button type="button" onClick={() => onPick(p.id)}>
              {p.name}
            </button>
            {!p.builtin && (
              <button
                type="button"
                onClick={() => onDelete(p.id)}
                aria-label="Удалить пресет"
                className="-mr-1 grid h-4 w-4 place-items-center rounded-full text-ink-muted hover:bg-coral-500/15 hover:text-coral-500"
              >
                <IconTrash size={11} />
              </button>
            )}
          </span>
        );
      })}
      {activeId === "" && (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-coral-500/40 bg-coral-soft px-3 py-1 text-[13px] font-medium text-coral-600">
          Свой
        </span>
      )}
    </div>
  );
}

/* ─── Локальная папка (основной источник) ────────────────────────────── */
const cfgKey = (wd: string, en: boolean, pl: number, pr: ScanProfileT) =>
  JSON.stringify({ wd, en, pl, pr });

function LocalSource() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["ingest-source", "local"],
    queryFn: () => api.getIngestSource("local"),
  });
  const { data: presetsData, refetch: refetchPresets } = useQuery({
    queryKey: ["scan-presets"],
    queryFn: api.listScanPresets,
  });
  const presets = presetsData?.presets ?? [];

  const [watchDir, setWatchDir] = React.useState("");
  const [enabled, setEnabled] = React.useState(false);
  const [poll, setPoll] = React.useState(120);
  const [profile, setProfile] = React.useState<ScanProfileT>(DEFAULT_PROFILE);
  const [baseline, setBaseline] = React.useState("");
  const inited = React.useRef(false);

  const [presetId, setPresetId] = React.useState("zoom");
  const [advOpen, setAdvOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);
  const [scanMsg, setScanMsg] = React.useState<string | null>(null);
  const [scanning, setScanning] = React.useState(false);
  const [presetName, setPresetName] = React.useState("");
  const [savingPreset, setSavingPreset] = React.useState(false);

  // Инициализируем черновик ОДИН раз — рефетчи (скан/сохранение) не затирают правки.
  React.useEffect(() => {
    if (inited.current || !data) return;
    const wd = data.configured ? data.watch_dir ?? "" : "";
    const en = data.configured ? !!data.enabled : false;
    const pl = data.configured ? data.poll_interval ?? 120 : 120;
    const pr = data.configured ? profileFromData(data.scan_profile) : DEFAULT_PROFILE;
    setWatchDir(wd);
    setEnabled(en);
    setPoll(pl);
    setProfile(pr);
    setBaseline(cfgKey(wd, en, pl, pr));
    inited.current = true;
  }, [data]);

  // Подсветка активного пресета, если профиль совпал.
  React.useEffect(() => {
    const match = presets.find((p) => JSON.stringify(p.body) === JSON.stringify(profile));
    setPresetId(match ? match.id : "");
  }, [profile, presets]);

  const current = cfgKey(watchDir, enabled, poll, profile);
  const dirty = inited.current && current !== baseline;
  const configured = !!data?.configured;

  function edit(patch: Partial<ScanProfileT>) {
    setProfile((prev) => ({ ...prev, ...patch }));
  }
  function applyPreset(id: string) {
    const p = presets.find((x) => x.id === id);
    if (p) setProfile(p.body);
  }

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await api.putIngestSource({
        watch_dir: watchDir,
        enabled,
        poll_interval: poll,
        source_type: "local",
        scan_profile: { ...profile, track_mode: "combine" },
      });
      setBaseline(current);
      refetch();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    const b = JSON.parse(baseline) as { wd: string; en: boolean; pl: number; pr: ScanProfileT };
    setWatchDir(b.wd);
    setEnabled(b.en);
    setPoll(b.pl);
    setProfile(b.pr);
    setErr(null);
  }

  async function scanNow() {
    setScanning(true);
    setScanMsg(null);
    try {
      const r = await api.localScanNow();
      setScanMsg(
        r.started.length > 0
          ? `Запущена транскрибация: ${r.started.length} — смотрите «Записи».`
          : "Новых записей не найдено — всё уже обработано.",
      );
      refetch();
    } catch (e) {
      setScanMsg(e instanceof ApiError ? e.message : "Не удалось просканировать");
    } finally {
      setScanning(false);
    }
  }

  async function savePreset() {
    setSavingPreset(true);
    setErr(null);
    try {
      await api.createScanPreset(presetName.trim(), profile);
      setPresetName("");
      refetchPresets();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось сохранить пресет");
    } finally {
      setSavingPreset(false);
    }
  }

  async function removePreset(id: string) {
    await api.deleteScanPreset(id);
    refetchPresets();
  }

  if (isLoading) {
    return (
      <Card className="flex items-center gap-2 p-5 text-sm text-ink-muted">
        <Spinner className="h-4 w-4" /> Загрузка источника…
      </Card>
    );
  }

  const layoutSummary = `${
    presets.find((p) => p.id === presetId)?.name ?? "Свой профиль"
  } · ${profile.parts_mode === "merge" ? "склейка частей" : "части отдельно"}`;

  return (
    <Card className="overflow-hidden">
      {/* Заголовок-статус (status-first) */}
      <div className="flex flex-wrap items-start gap-4 border-b border-line px-5 py-4">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-coral-soft text-coral-500">
          <IconFolder size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">Локальная папка</span>
            {configured ? (
              enabled ? (
                <Badge tone="green">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  Наблюдение включено
                </Badge>
              ) : (
                <Badge>Наблюдение выключено</Badge>
              )
            ) : (
              <Badge tone="amber">не настроена</Badge>
            )}
          </div>
          {configured ? (
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-muted">
              <Mono className="truncate">{data?.watch_dir}</Mono>
              <span aria-hidden>·</span>
              <span>каждые {fmtInterval(poll)}</span>
              <span aria-hidden>·</span>
              <span>последний скан {fmtAgo(data?.last_scan_at)}</span>
            </p>
          ) : (
            <p className="mt-1 max-w-lg text-xs leading-snug text-ink-muted">
              Укажите папку, куда сгружаются записи созвонов, — новые встречи будут
              транскрибироваться автоматически.
            </p>
          )}
        </div>
        {configured && (
          <Button variant="outline" onClick={scanNow} disabled={scanning}>
            <IconSearch size={16} />
            {scanning ? "Сканируем…" : "Сканировать сейчас"}
          </Button>
        )}
      </div>

      <div className="space-y-6 px-5 py-5">
        {/* Basic: папка, интервал, наблюдение */}
        <div className="space-y-4">
          <PathField
            label="Путь к папке"
            hint="Папка на сервере, где работает наблюдатель. Впишите путь или откройте «Обзор» (браузер не видит вашу файловую систему напрямую)."
            value={watchDir}
            placeholder="/Users/имя/Documents/Zoom"
            onChange={setWatchDir}
          />
          <div className="grid gap-4 sm:grid-cols-[200px_1fr] sm:items-start">
            <Field label="Интервал проверки (сек)" hint="Минимум 60.">
              <Input
                type="number"
                value={poll}
                min={60}
                onChange={(e) => setPoll(Number(e.target.value) || 120)}
              />
            </Field>
            <div className="sm:pt-1.5">
              <Toggle
                checked={enabled}
                onChange={setEnabled}
                label="Следить автоматически"
                hint="Записи с недокачанными файлами пережидаются — в работу идут только стабильные папки."
              />
            </div>
          </div>
        </div>

        {/* Basic: вывод транскриптов */}
        <div className="space-y-3">
          <div className="text-sm font-medium text-ink">Куда складывать транскрипты</div>
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="output-mode"
              className="mt-1 accent-coral-500"
              checked={profile.output.mode === "beside"}
              onChange={() => edit({ output: { ...profile.output, mode: "beside" } })}
            />
            <span>
              <span className="block text-sm text-ink">Рядом с записью</span>
              <span className="block text-xs text-ink-muted">В подпапке внутри папки каждой встречи.</span>
            </span>
          </label>
          {profile.output.mode === "beside" && (
            <div className="pl-7">
              <Field label="Подпапка внутри записи">
                <Input
                  value={profile.output.subdir}
                  className="max-w-sm"
                  onChange={(e) => edit({ output: { ...profile.output, subdir: e.target.value } })}
                />
              </Field>
            </div>
          )}
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="output-mode"
              className="mt-1 accent-coral-500"
              checked={profile.output.mode === "fixed"}
              onChange={() => edit({ output: { ...profile.output, mode: "fixed" } })}
            />
            <span>
              <span className="block text-sm text-ink">В отдельную папку</span>
              <span className="block text-xs text-ink-muted">
                Для каждой встречи — подпапка по её имени. Будет создана, если не существует.
              </span>
            </span>
          </label>
          {profile.output.mode === "fixed" && (
            <div className="pl-7">
              <PathField
                label="Папка для транскриптов"
                value={profile.output.dir ?? ""}
                placeholder="/Users/имя/Transcripts"
                onChange={(v) => edit({ output: { ...profile.output, dir: v || null } })}
              />
            </div>
          )}
        </div>

        {/* Advanced: раскладка записей (scan-профиль) */}
        <Accordion
          title="Раскладка записей"
          summary={layoutSummary}
          open={advOpen}
          onToggle={() => setAdvOpen((v) => !v)}
        >
          <div className="space-y-4">
            <p className="text-xs leading-snug text-ink-muted">
              Как устроены папки встреч и что делать с дорожками участников. Влияет только на
              будущие записи — уже обработанные встречи не пересканируются.
            </p>
            <div>
              <div className="mb-2 text-[13px] font-medium text-ink">Пресет</div>
              <PresetChips
                presets={presets}
                activeId={presetId}
                onPick={applyPreset}
                onDelete={removePreset}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Тип раскладки">
                <Select
                  value={profile.layout}
                  onChange={(e) => edit({ layout: e.target.value as ScanProfileT["layout"] })}
                >
                  <option value="zoom">Zoom (локальная запись)</option>
                  <option value="plain">Простая папка</option>
                </Select>
              </Field>
              <Field label="Подпапка с дорожками участников" hint="Пусто — подорожек нет.">
                <Input
                  value={profile.tracks_subdir ?? ""}
                  placeholder="Audio Record"
                  onChange={(e) =>
                    edit({ tracks_subdir: e.target.value.trim() ? e.target.value : null })
                  }
                />
              </Field>
            </div>
            <p className="rounded-control bg-canvas px-3 py-2 text-xs leading-snug text-ink-muted">
              Дорожки участников распознаются по отдельности и собираются в один транскрипт —
              имена спикеров берутся из имён файлов. Если дорожек нет, распознаётся общая запись
              с автоматическим разделением голосов.
            </p>
            <div>
              <div className="mb-2 text-[13px] font-medium text-ink">
                Если запись останавливали и в папке несколько записей
              </div>
              <div className="space-y-2">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="radio"
                    name="parts-mode"
                    className="mt-1 accent-coral-500"
                    checked={profile.parts_mode === "merge"}
                    onChange={() => edit({ parts_mode: "merge" })}
                  />
                  <span>
                    <span className="block text-sm text-ink">Склеить в один транскрипт</span>
                    <span className="block text-xs text-ink-muted">
                      Записи идут друг за другом на общей шкале времени.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="radio"
                    name="parts-mode"
                    className="mt-1 accent-coral-500"
                    checked={profile.parts_mode === "separate"}
                    onChange={() => edit({ parts_mode: "separate" })}
                  />
                  <span>
                    <span className="block text-sm text-ink">Отдельный транскрипт на каждую запись</span>
                    <span className="block text-xs text-ink-muted">
                      Вторая и последующие — в подпапках «Часть N».
                    </span>
                  </span>
                </label>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Расширения медиафайлов" hint="Через запятую.">
                <Input
                  value={profile.media_suffixes.join(", ")}
                  onChange={(e) =>
                    edit({
                      media_suffixes: e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </Field>
              <Field label="Игнорировать подпапки" hint="Через запятую.">
                <Input
                  value={profile.skip_dirs.join(", ")}
                  onChange={(e) =>
                    edit({
                      skip_dirs: e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </Field>
            </div>
            {presetId === "" && (
              <div className="flex items-center gap-2 border-t border-line pt-4">
                <Input
                  value={presetName}
                  placeholder="Название пресета"
                  className="h-9 max-w-56"
                  onChange={(e) => setPresetName(e.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={savePreset}
                  disabled={savingPreset || !presetName.trim()}
                >
                  {savingPreset ? "…" : "Сохранить как пресет"}
                </Button>
              </div>
            )}
          </div>
        </Accordion>

        {err && <ErrorCard title="Настройка отклонена" detail={err} />}
        {scanMsg && <p className="text-sm text-ink-muted">{scanMsg}</p>}
      </div>

      {/* Контекстная панель сохранения (появляется при изменениях) */}
      {dirty && (
        <div className="sticky bottom-4 z-20 mx-4 mb-4 flex items-center gap-3 rounded-card border border-line bg-white/95 px-4 py-2.5 shadow-lift backdrop-blur">
          <span className="text-[13px] font-medium text-ink">Есть несохранённые изменения</span>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={cancel} disabled={busy}>
              Отменить
            </Button>
            <Button size="sm" onClick={save} disabled={busy || !watchDir}>
              {busy ? "Сохраняем…" : "Сохранить"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

/* ─── Яндекс.Диск (облачный источник, OAuth-lifecycle) ───────────────── */
function YandexSource() {
  const { data: status, isLoading, refetch } = useQuery({
    queryKey: ["yandex-status"],
    queryFn: api.yandexStatus,
  });
  const [token, setToken] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [tokenOpen, setTokenOpen] = React.useState(false);

  const [path, setPath] = React.useState("/");
  const [entries, setEntries] = React.useState<YaEntry[] | null>(null);
  const [browsing, setBrowsing] = React.useState(false);
  const [pullMsg, setPullMsg] = React.useState<string | null>(null);
  const [oauthMsg, setOauthMsg] = React.useState<string | null>(null);

  React.useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("yandex");
    if (p === "connected") {
      setOauthMsg("Яндекс.Диск подключён.");
      refetch();
    } else if (p === "error") {
      setOauthMsg("Не удалось подключить Яндекс.Диск — попробуйте ещё раз.");
    }
    if (p) window.history.replaceState({}, "", "/sources");
  }, [refetch]);

  async function saveToken() {
    setBusy(true);
    setError(null);
    try {
      await api.putYandexToken(token);
      setToken("");
      setTokenOpen(false);
      refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить токен");
    } finally {
      setBusy(false);
    }
  }

  async function browse(p: string) {
    setBrowsing(true);
    setError(null);
    try {
      const r = await api.yandexBrowse(p);
      setPath(r.path);
      setEntries(r.entries);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось открыть папку");
    } finally {
      setBrowsing(false);
    }
  }

  async function pull(p: string) {
    setPullMsg(null);
    try {
      const r = await api.yandexPull(p);
      setPullMsg(
        r.status === "already_seen" ? "Уже загружалось ранее." : "Загрузка началась — смотрите «Записи».",
      );
    } catch (e) {
      setPullMsg(e instanceof ApiError ? e.message : "Не удалось подтянуть");
    }
  }

  const connected = !!status?.connected;

  return (
    <Card className="overflow-hidden">
      {/* Connect-lifecycle: статус + подключение */}
      <div className="flex flex-wrap items-start gap-4 border-b border-line px-5 py-4">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-azure/10 text-azure-deep">
          <IconCloud size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">Яндекс.Диск</span>
            {connected ? (
              <Badge tone={status?.check_ok ? "green" : "amber"}>
                {status?.check_ok ? "подключён" : "токен недействителен"}
              </Badge>
            ) : (
              <Badge>не подключён</Badge>
            )}
          </div>
          <p className="mt-1 max-w-lg text-xs leading-snug text-ink-muted">
            Необязательный облачный источник: подтягивайте записи с Диска вручную или настройте
            авто-подтягивание.
          </p>
        </div>
        {status?.oauth_available ? (
          <Button
            variant={connected ? "outline" : "default"}
            onClick={() => (window.location.href = "/api/yandex/oauth/start")}
          >
            <IconCloud size={16} />
            {connected ? "Переподключить" : "Подключить"}
          </Button>
        ) : (
          !connected && (
            <Button variant="outline" onClick={() => setTokenOpen((v) => !v)}>
              Ввести токен
            </Button>
          )
        )}
      </div>

      {oauthMsg && (
        <div className="border-b border-line bg-coral-soft px-5 py-2.5 text-sm text-ink">{oauthMsg}</div>
      )}

      {isLoading ? (
        <div className="flex items-center gap-2 px-5 py-4 text-sm text-ink-muted">
          <Spinner className="h-4 w-4" /> Проверяем подключение…
        </div>
      ) : (
        (tokenOpen || connected) && (
          <div className="space-y-5 px-5 py-5">
            {/* Токен вручную (когда OAuth недоступен) */}
            {!status?.oauth_available && tokenOpen && (
              <Field
                label="Токен доступа"
                hint="Хранится зашифрованным (Fernet). Проверяется перед сохранением."
              >
                <div className="flex gap-2">
                  <Input
                    type="password"
                    value={token}
                    placeholder="OAuth-токен"
                    onChange={(e) => setToken(e.target.value)}
                  />
                  <Button onClick={saveToken} disabled={busy || !token}>
                    {busy ? "…" : "Сохранить"}
                  </Button>
                </div>
              </Field>
            )}

            {connected && (
              <>
                {/* Обзор папки + ручное подтягивание */}
                <div>
                  <div className="mb-2 text-sm font-medium text-ink">Обзор папки</div>
                  <div className="flex gap-2">
                    <Input
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                      placeholder="/путь/к/папке"
                    />
                    <Button variant="outline" onClick={() => browse(path)} disabled={browsing}>
                      <IconSearch size={16} />
                      Открыть
                    </Button>
                  </div>
                  {entries && (
                    <ul className="mt-3 divide-y divide-line/70 overflow-hidden rounded-control border border-line">
                      {entries.length === 0 && (
                        <li className="px-3 py-4 text-sm text-ink-muted">Пусто.</li>
                      )}
                      {entries.map((e) => (
                        <li key={e.path} className="flex items-center gap-3 px-3 py-2">
                          <span className="text-ink-muted">
                            {e.type === "dir" ? <IconFolder size={15} /> : <IconDownload size={15} />}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{e.name}</span>
                          {e.type === "dir" ? (
                            <Button size="sm" variant="ghost" onClick={() => browse(e.path)}>
                              Открыть
                            </Button>
                          ) : (
                            <Button size="sm" variant="subtle" onClick={() => pull(e.path)}>
                              <IconDownload size={14} />
                              Подтянуть
                            </Button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {pullMsg && <p className="mt-3 text-sm text-ink-muted">{pullMsg}</p>}
                </div>

                <div className="border-t border-line pt-5">
                  <AutoWatch />
                </div>
              </>
            )}
          </div>
        )
      )}
      {error && (
        <div className="px-5 pb-5">
          <ErrorCard title={error} />
        </div>
      )}
    </Card>
  );
}

function AutoWatch() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["ingest-source", "yandex"],
    queryFn: () => api.getIngestSource("yandex"),
  });
  const [watchDir, setWatchDir] = React.useState("");
  const [enabled, setEnabled] = React.useState(false);
  const [poll, setPoll] = React.useState(300);
  const [busy, setBusy] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (data?.configured) {
      setWatchDir(data.watch_dir ?? "");
      setEnabled(!!data.enabled);
      setPoll(data.poll_interval ?? 300);
    }
  }, [data]);

  async function save() {
    setBusy(true);
    setErr(null);
    setSaved(false);
    try {
      await api.putIngestSource({
        watch_dir: watchDir,
        enabled,
        poll_interval: poll,
        source_type: "yandex",
      });
      setSaved(true);
      refetch();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return null;

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-medium text-ink">Авто-подтягивание</div>
        <p className="text-xs text-ink-muted">
          Периодически проверяет папку и сама заводит записи, когда файлы дозалились.
        </p>
      </div>
      <Field label="Папка наблюдения" hint="Путь на Яндекс.Диске под разрешённой областью.">
        <Input value={watchDir} placeholder="/Записи созвонов" onChange={(e) => setWatchDir(e.target.value)} />
      </Field>
      <Toggle
        checked={enabled}
        onChange={setEnabled}
        label="Включить авто-подтягивание"
        hint="Опрос идёт на фоне (io-очередь), новые записи появятся во «Записях»."
      />
      {err && <ErrorCard title={err} />}
      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={busy || !watchDir}>
          {busy ? "Сохраняем…" : "Сохранить"}
        </Button>
        {saved && <span className="text-sm text-emerald-600">Сохранено</span>}
      </div>
    </div>
  );
}

/* ─── Страница «Источники» ───────────────────────────────────────────── */
export default function Sources() {
  return (
    <div className="space-y-6">
      <SectionTitle
        eyebrow="Приём записей"
        title="Источники"
        desc="Откуда приходят записи и как они автоматически попадают в транскрипцию."
      />
      <div className="space-y-4">
        <LocalSource />
        <YandexSource />
      </div>
    </div>
  );
}
