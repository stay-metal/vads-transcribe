import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Button, Field, Input, Spinner } from "@/components/ui";
import { IconFolder } from "@/components/icons";
import { cn } from "@/lib/utils";

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
export function PathField({
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
