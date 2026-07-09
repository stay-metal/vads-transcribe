import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { YaEntry } from "@/api/types";
import { Badge, Button, Card, Field, Input, Spinner, Toggle, ErrorCard } from "@/components/ui";
import { IconCloud, IconFolder, IconSearch, IconDownload } from "@/components/icons";

/* ─── Яндекс.Диск (облачный источник, OAuth-lifecycle) ───────────────── */
export function YandexSource() {
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
