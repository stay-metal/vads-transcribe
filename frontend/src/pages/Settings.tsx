import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type Glossary, type YaEntry } from "@/api/client";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  SectionTitle,
  Spinner,
  Tabs,
  Mono,
  ErrorCard,
} from "@/components/ui";
import { IconBook, IconCloud, IconArchive, IconActivity, IconTrash, IconDownload, IconSearch } from "@/components/icons";

type Tab = "glossary" | "sources" | "retention" | "health";

export default function Settings() {
  const [tab, setTab] = React.useState<Tab>("glossary");
  return (
    <div className="space-y-6">
      <SectionTitle eyebrow="Конфигурация" title="Настройки" desc="Словарь, источники записей и состояние сервиса." />
      <div className="grid gap-6 md:grid-cols-[200px_1fr]">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "glossary", label: "Словарь", icon: <IconBook size={17} /> },
            { value: "sources", label: "Источники", icon: <IconCloud size={17} /> },
            { value: "retention", label: "Хранение", icon: <IconArchive size={17} /> },
            { value: "health", label: "Здоровье", icon: <IconActivity size={17} /> },
          ]}
        />
        <div>
          {tab === "glossary" && <GlossarySection />}
          {tab === "sources" && <SourcesSection />}
          {tab === "retention" && <RetentionSection />}
          {tab === "health" && <HealthSection />}
        </div>
      </div>
    </div>
  );
}

/* ─── Словарь ────────────────────────────────────────────────────────── */
type Pair = { k: string; v: string };
const toPairs = (o: Record<string, string>): Pair[] => Object.entries(o).map(([k, v]) => ({ k, v }));
const toObj = (ps: Pair[]): Record<string, string> =>
  Object.fromEntries(ps.filter((p) => p.k.trim()).map((p) => [p.k.trim(), p.v.trim()]));

function GlossarySection() {
  const { data, isLoading, refetch } = useQuery({ queryKey: ["glossary"], queryFn: api.getGlossary });
  const [people, setPeople] = React.useState<Pair[]>([]);
  const [terms, setTerms] = React.useState<Pair[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (data) {
      setPeople(toPairs(data.people));
      setTerms(toPairs(data.terms));
    }
  }, [data]);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const body: Glossary = { people: toObj(people), terms: toObj(terms) };
      await api.putGlossary(body);
      setSaved(true);
      refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить словарь");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <Loading label="Загрузка словаря…" />;

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <PairEditor
          title="Имена"
          hint="Как писать имена собственные. Слева — как слышится, справа — как писать."
          pairs={people}
          onChange={setPeople}
          leftPh="алиас"
          rightPh="каноничное имя"
        />
      </Card>
      <Card className="p-5">
        <PairEditor
          title="Термины"
          hint="Бренды, аббревиатуры, латиница. Кириллический вывод модели не затрагивается (I1)."
          pairs={terms}
          onChange={setTerms}
          leftPh="как слышится"
          rightPh="как писать"
        />
      </Card>
      {error && <ErrorCard title="Правка отклонена" detail={error} />}
      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={busy}>
          {busy ? "Сохраняем…" : "Сохранить словарь"}
        </Button>
        {saved && <span className="text-sm text-emerald-600">Сохранено</span>}
      </div>
    </div>
  );
}

function PairEditor({
  title,
  hint,
  pairs,
  onChange,
  leftPh,
  rightPh,
}: {
  title: string;
  hint: string;
  pairs: Pair[];
  onChange: (p: Pair[]) => void;
  leftPh: string;
  rightPh: string;
}) {
  return (
    <div>
      <div className="mb-1 text-sm font-medium text-ink">{title}</div>
      <p className="mb-3 text-xs text-ink-muted">{hint}</p>
      <div className="space-y-2">
        {pairs.map((p, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={p.k}
              placeholder={leftPh}
              className="h-9"
              onChange={(e) => onChange(pairs.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))}
            />
            <span className="text-ink-muted">→</span>
            <Input
              value={p.v}
              placeholder={rightPh}
              className="h-9"
              onChange={(e) => onChange(pairs.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))}
            />
            <button
              onClick={() => onChange(pairs.filter((_, j) => j !== i))}
              className="shrink-0 rounded-control p-2 text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
              aria-label="Убрать"
            >
              <IconTrash size={15} />
            </button>
          </div>
        ))}
      </div>
      <button
        onClick={() => onChange([...pairs, { k: "", v: "" }])}
        className="mt-3 text-sm font-medium text-coral-500 transition-colors hover:text-coral-600"
      >
        + Добавить
      </button>
    </div>
  );
}

/* ─── Источники (Яндекс.Диск) ────────────────────────────────────────── */
function SourcesSection() {
  const { data: status, isLoading, refetch } = useQuery({ queryKey: ["yandex-status"], queryFn: api.yandexStatus });
  const [token, setToken] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const [path, setPath] = React.useState("/");
  const [entries, setEntries] = React.useState<YaEntry[] | null>(null);
  const [browsing, setBrowsing] = React.useState(false);
  const [pullMsg, setPullMsg] = React.useState<string | null>(null);

  async function saveToken() {
    setBusy(true);
    setError(null);
    try {
      await api.putYandexToken(token);
      setToken("");
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
      setPullMsg(r.status === "already_seen" ? "Уже загружалось ранее." : "Загрузка началась — смотрите «Записи».");
    } catch (e) {
      setPullMsg(e instanceof ApiError ? e.message : "Не удалось подтянуть");
    }
  }

  if (isLoading) return <Loading label="Проверяем подключение…" />;

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-sm font-medium text-ink">Яндекс.Диск</span>
          {status?.connected ? (
            <Badge tone={status.check_ok ? "green" : "amber"}>{status.check_ok ? "подключён" : "токен недействителен"}</Badge>
          ) : (
            <Badge>не подключён</Badge>
          )}
        </div>
        <Field label="Токен доступа" hint="Хранится зашифрованным (Fernet). Проверяется перед сохранением.">
          <div className="flex gap-2">
            <Input type="password" value={token} placeholder="OAuth-токен" onChange={(e) => setToken(e.target.value)} />
            <Button onClick={saveToken} disabled={busy || !token}>
              {busy ? "…" : "Сохранить"}
            </Button>
          </div>
        </Field>
      </Card>

      {status?.connected && (
        <Card className="p-5">
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium text-ink">Обзор папки</span>
            <Mono className="ml-auto">{path}</Mono>
          </div>
          <div className="mb-3 flex gap-2">
            <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/путь/к/папке" />
            <Button variant="outline" onClick={() => browse(path)} disabled={browsing}>
              <IconSearch size={16} />
              Открыть
            </Button>
          </div>
          {entries && (
            <ul className="divide-y divide-line/70 overflow-hidden rounded-control border border-line">
              {entries.length === 0 && <li className="px-3 py-4 text-sm text-ink-muted">Пусто.</li>}
              {entries.map((e) => (
                <li key={e.path} className="flex items-center gap-3 px-3 py-2">
                  <span className="text-ink-muted">{e.type === "dir" ? "📁" : "🎧"}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{e.name}</span>
                  {e.type === "dir" ? (
                    <Button size="sm" variant="ghost" onClick={() => browse(e.path)}>Открыть</Button>
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
        </Card>
      )}
      {error && <ErrorCard title={error} />}
    </div>
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

function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-muted">
      <Spinner className="h-4 w-4" /> {label}
    </div>
  );
}
