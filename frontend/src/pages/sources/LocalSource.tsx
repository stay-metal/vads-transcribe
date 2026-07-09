import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { ScanProfileT } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Loading,
  Select,
  Toggle,
  Mono,
  ErrorCard,
} from "@/components/ui";
import { IconFolder, IconSearch, IconChevronDown, IconChevronRight } from "@/components/icons";
import { fmtRelative, stableStringify } from "@/lib/utils";
import { PathField } from "./FolderPicker";
import { PresetChips } from "./PresetChips";

/* ─── Формат интервала для статус-строки ─────────────────────────────── */
function fmtInterval(sec: number): string {
  if (sec % 3600 === 0) return `${sec / 3600} ч`;
  if (sec % 60 === 0) return `${sec / 60} мин`;
  return `${sec} с`;
}

const DEFAULT_PROFILE: ScanProfileT = {
  layout: "zoom",
  tracks_subdir: "Audio Record",
  track_mode: "combine",
  parts_mode: "merge",
  media_suffixes: [".m4a", ".mp4", ".mov", ".mp3", ".wav"],
  skip_dirs: ["transcripts", "done"],
  output: { mode: "beside", subdir: "transcripts", dir: null },
};

function profileFromData(sp?: Partial<ScanProfileT>): ScanProfileT {
  if (!sp || Object.keys(sp).length === 0) return DEFAULT_PROFILE;
  return {
    ...DEFAULT_PROFILE,
    ...sp,
    output: { ...DEFAULT_PROFILE.output, ...(sp.output ?? {}) },
  };
}

const cfgKey = (wd: string, en: boolean, pl: number, pr: ScanProfileT) =>
  stableStringify({ wd, en, pl, pr });

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

/* ─── Радио-опция с подписью (общий блок для output/parts) ────────────── */
function RadioOption({
  name,
  checked,
  onChange,
  title,
  desc,
}: {
  name: string;
  checked: boolean;
  onChange: () => void;
  title: string;
  desc: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="radio"
        name={name}
        className="mt-1 accent-coral-500"
        checked={checked}
        onChange={onChange}
      />
      <span>
        <span className="block text-sm text-ink">{title}</span>
        <span className="block text-xs text-ink-muted">{desc}</span>
      </span>
    </label>
  );
}

/* ─── Локальная папка (основной источник) ────────────────────────────── */
export function LocalSource() {
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
  const [pollDraft, setPollDraft] = React.useState("120"); // свободный ввод; клампим на blur/save
  const [profile, setProfile] = React.useState<ScanProfileT>(DEFAULT_PROFILE);
  const clampPoll = (s: string) => Math.max(60, Math.floor(Number(s)) || 60);
  const poll = clampPoll(pollDraft); // committed-значение для dirty/сохранения
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
    setPollDraft(String(pl));
    setProfile(pr);
    setBaseline(cfgKey(wd, en, pl, pr));
    inited.current = true;
  }, [data]);

  // Подсветка активного пресета, если профиль совпал (сравнение канонично —
  // не зависит от порядка ключей в присланном сервером пресете).
  React.useEffect(() => {
    const match = presets.find((p) => stableStringify(p.body) === stableStringify(profile));
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
      setPollDraft(String(poll)); // нормализуем поле под сохранённое (сервер клампит до 60)
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
    setPollDraft(String(b.pl));
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
    setErr(null);
    try {
      await api.deleteScanPreset(id);
      refetchPresets();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось удалить пресет");
    }
  }

  if (isLoading) {
    return (
      <Card className="p-5">
        <Loading label="Загрузка источника…" />
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
              <span>каждые {fmtInterval(data?.poll_interval ?? 120)}</span>
              <span aria-hidden>·</span>
              <span>
                последний скан{" "}
                {fmtRelative(data?.last_scan_at, { empty: "ещё не сканировалась", justNowSec: 60 })}
              </span>
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
                value={pollDraft}
                min={60}
                onChange={(e) => setPollDraft(e.target.value)}
                onBlur={() => setPollDraft(String(poll))}
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
          <RadioOption
            name="output-mode"
            checked={profile.output.mode === "beside"}
            onChange={() => edit({ output: { ...profile.output, mode: "beside" } })}
            title="Рядом с записью"
            desc="В подпапке внутри папки каждой встречи."
          />
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
          <RadioOption
            name="output-mode"
            checked={profile.output.mode === "fixed"}
            onChange={() => edit({ output: { ...profile.output, mode: "fixed" } })}
            title="В отдельную папку"
            desc="Для каждой встречи — подпапка по её имени. Будет создана, если не существует."
          />
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
                <RadioOption
                  name="parts-mode"
                  checked={profile.parts_mode === "merge"}
                  onChange={() => edit({ parts_mode: "merge" })}
                  title="Склеить в один транскрипт"
                  desc="Записи идут друг за другом на общей шкале времени."
                />
                <RadioOption
                  name="parts-mode"
                  checked={profile.parts_mode === "separate"}
                  onChange={() => edit({ parts_mode: "separate" })}
                  title="Отдельный транскрипт на каждую запись"
                  desc="Вторая и последующие — в подпапках «Часть N»."
                />
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
