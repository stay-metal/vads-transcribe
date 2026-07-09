import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type Glossary as GlossaryT } from "@/api/client";
import { Button, Card, Input, SectionTitle, Spinner, ErrorCard } from "@/components/ui";
import { IconTrash, IconSearch } from "@/components/icons";
import { cn } from "@/lib/utils";

type Pair = { k: string; v: string };
type Which = "people" | "terms";

const toPairs = (o: Record<string, string>): Pair[] => Object.entries(o).map(([k, v]) => ({ k, v }));
const toObj = (ps: Pair[]): Record<string, string> =>
  Object.fromEntries(ps.filter((p) => p.k.trim()).map((p) => [p.k.trim(), p.v.trim()]));

const PAGE = 50; // рендерим порциями — список не тормозит на сотнях/тысячах записей

const TAB_META: Record<Which, { label: string; hint: string; leftPh: string; rightPh: string }> = {
  people: {
    label: "Имена",
    hint: "Имена собственные: слева — как слышится, справа — как писать.",
    leftPh: "алиас",
    rightPh: "каноничное имя",
  },
  terms: {
    label: "Термины",
    hint: "Бренды, аббревиатуры, латиница. Кириллический вывод модели не затрагивается (I1).",
    leftPh: "как слышится",
    rightPh: "как писать",
  },
};

export default function Glossary() {
  const { data, isLoading, refetch } = useQuery({ queryKey: ["glossary"], queryFn: api.getGlossary });
  const [people, setPeople] = React.useState<Pair[]>([]);
  const [terms, setTerms] = React.useState<Pair[]>([]);
  const [baseline, setBaseline] = React.useState("");
  const inited = React.useRef(false);

  const [active, setActive] = React.useState<Which>("people");
  const [query, setQuery] = React.useState("");
  const [limit, setLimit] = React.useState(PAGE);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  // Инициализируем черновик один раз; baseline — для отслеживания несохранённых правок.
  React.useEffect(() => {
    if (inited.current || !data) return;
    const p = toPairs(data.people);
    const t = toPairs(data.terms);
    setPeople(p);
    setTerms(t);
    setBaseline(JSON.stringify({ p, t }));
    inited.current = true;
  }, [data]);

  // Сброс порции при переключении словаря/поиске.
  React.useEffect(() => setLimit(PAGE), [active, query]);

  const dirty = inited.current && JSON.stringify({ p: people, t: terms }) !== baseline;
  const list = active === "people" ? people : terms;
  const setList = active === "people" ? setPeople : setTerms;
  const meta = TAB_META[active];

  const q = query.trim().toLowerCase();
  const matched = list
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => !q || p.k.toLowerCase().includes(q) || p.v.toLowerCase().includes(q));
  const shown = q ? matched : matched.slice(0, limit);
  const hasMore = !q && matched.length > limit;

  function editAt(i: number, patch: Partial<Pair>) {
    setSaved(false);
    setList((prev) => prev.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }
  function removeAt(i: number) {
    setSaved(false);
    setList((prev) => prev.filter((_, j) => j !== i));
  }
  function addRow() {
    setSaved(false);
    setQuery(""); // чтобы новая (пустая) строка не отфильтровалась
    setList((prev) => [{ k: "", v: "" }, ...prev]); // новая строка — сверху, листать не нужно
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body: GlossaryT = { people: toObj(people), terms: toObj(terms) };
      await api.putGlossary(body);
      setBaseline(JSON.stringify({ p: people, t: terms }));
      setSaved(true);
      refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить словарь");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-muted">
        <Spinner className="h-4 w-4" /> Загрузка словаря…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <SectionTitle
        eyebrow="Канонизация"
        title="Словарь"
        desc="Как писать имена и термины. Правятся только латиница и числа — кириллический вывод модели неприкосновенен."
      />

      {/* Липкий тулбар: переключатель словаря + поиск + действия (всегда под рукой) */}
      <div className="sticky top-0 z-20 -mx-4 border-b border-line bg-canvas/90 px-4 py-3 backdrop-blur md:-mx-8 md:px-8">
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex rounded-full border border-line bg-white p-1">
            {(["people", "terms"] as Which[]).map((w) => {
              const on = active === w;
              const count = w === "people" ? people.length : terms.length;
              return (
                <button
                  key={w}
                  type="button"
                  onClick={() => setActive(w)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm transition-colors",
                    on ? "bg-coral-soft font-medium text-coral-600" : "text-ink-muted hover:text-ink",
                  )}
                >
                  {TAB_META[w].label}
                  <span className={cn("tabular text-xs", on ? "text-coral-500/80" : "text-ink-muted/60")}>
                    {count}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-56 sm:w-56">
              <IconSearch
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
              />
              <Input
                value={query}
                placeholder={`Поиск: ${meta.label.toLowerCase()}…`}
                className="h-9 pl-9"
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <Button variant="outline" size="sm" onClick={addRow}>
              + Добавить
            </Button>
            <Button size="sm" onClick={save} disabled={!dirty || busy}>
              {busy ? "Сохраняем…" : dirty ? "Сохранить" : saved ? "Сохранено" : "Сохранить"}
            </Button>
          </div>
        </div>
        {error && (
          <div className="mt-3">
            <ErrorCard title="Правка отклонена" detail={error} />
          </div>
        )}
      </div>

      <p className="px-1 text-xs text-ink-muted">{meta.hint}</p>

      <Card className="divide-y divide-line/60 overflow-hidden">
        {shown.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-ink-muted">
            {q ? "Ничего не найдено." : "Пока пусто — добавьте первую пару."}
          </div>
        ) : (
          shown.map(({ p, i }) => (
            <div key={i} className="flex items-center gap-2 px-3 py-2 sm:px-4">
              <Input
                value={p.k}
                placeholder={meta.leftPh}
                className="h-9"
                onChange={(e) => editAt(i, { k: e.target.value })}
              />
              <span className="shrink-0 text-ink-muted">→</span>
              <Input
                value={p.v}
                placeholder={meta.rightPh}
                className="h-9"
                onChange={(e) => editAt(i, { v: e.target.value })}
              />
              <button
                onClick={() => removeAt(i)}
                className="shrink-0 rounded-control p-2 text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
                aria-label="Убрать"
              >
                <IconTrash size={15} />
              </button>
            </div>
          ))
        )}
      </Card>

      <div className="flex items-center gap-3 px-1">
        {hasMore && (
          <Button variant="outline" size="sm" onClick={() => setLimit((l) => l + PAGE)}>
            Показать ещё
          </Button>
        )}
        <span className="text-xs text-ink-muted">
          {q
            ? `Найдено ${matched.length} из ${list.length}`
            : `Показано ${shown.length} из ${list.length}`}
        </span>
      </div>
    </div>
  );
}
