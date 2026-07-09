import * as React from "react";
import { cn } from "@/lib/utils";
import { IconCalendar, IconX } from "@/components/icons";
import { rangeLabel, toDateInput } from "./helpers";

/* ─── Фильтр по датам (диапазон + пресеты) ───────────────────────────── */
function PresetBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-line px-2.5 py-1 text-[12px] text-ink-muted transition-colors hover:border-coral-500/40 hover:bg-coral-soft hover:text-coral-600"
    >
      {children}
    </button>
  );
}

export function DateFilter({
  from,
  to,
  onChange,
}: {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = Boolean(from || to);
  const preset = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (days - 1));
    onChange(toDateInput(start), toDateInput(end));
    setOpen(false);
  };
  const fieldCls =
    "h-9 w-full rounded-control border border-line bg-white px-2 text-[13px] text-ink outline-none focus:border-azure/70";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors",
          active
            ? "border-coral-500/40 bg-coral-soft font-medium text-coral-600"
            : "border-line bg-white text-ink-muted hover:text-ink",
        )}
      >
        <IconCalendar size={14} />
        {active ? rangeLabel(from, to) : "Период"}
        {active && (
          <span
            role="button"
            aria-label="Сбросить период"
            onClick={(e) => {
              e.stopPropagation();
              onChange("", "");
            }}
            className="-mr-1 ml-0.5 grid h-4 w-4 place-items-center rounded-full text-coral-500 hover:bg-coral-500/15"
          >
            <IconX size={11} />
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-72 rounded-card border border-line bg-white p-3 shadow-lift">
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="mb-1 block text-[11px] text-ink-muted">От</span>
              <input
                type="date"
                value={from}
                max={to || undefined}
                onChange={(e) => onChange(e.target.value, to)}
                className={fieldCls}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-ink-muted">До</span>
              <input
                type="date"
                value={to}
                min={from || undefined}
                onChange={(e) => onChange(from, e.target.value)}
                className={fieldCls}
              />
            </label>
          </div>
          <div className="mt-2.5 flex items-center gap-1.5">
            <PresetBtn onClick={() => preset(7)}>7 дней</PresetBtn>
            <PresetBtn onClick={() => preset(30)}>30 дней</PresetBtn>
            <button
              type="button"
              onClick={() => {
                onChange("", "");
                setOpen(false);
              }}
              className="ml-auto text-[12px] font-medium text-ink-muted hover:text-coral-600"
            >
              Сбросить
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
