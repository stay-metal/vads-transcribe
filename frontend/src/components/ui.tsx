import * as React from "react";
import { cn, speakerColor, STATUS_META, type JobState } from "@/lib/utils";

/* ─── Кнопка ─────────────────────────────────────────────────────────── */
export function Button({
  className,
  variant = "default",
  size = "md",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost" | "subtle" | "danger";
  size?: "sm" | "md";
}) {
  const variants = {
    default: "bg-coral-500 text-white hover:bg-coral-600 shadow-soft",
    outline: "border border-line bg-white text-ink hover:bg-canvas hover:border-ink-muted/40",
    ghost: "text-ink-muted hover:bg-coral-soft hover:text-coral-500",
    subtle: "bg-coral-soft text-coral-500 hover:bg-coral-500 hover:text-white",
    danger: "bg-white border border-line text-coral-600 hover:bg-coral-soft",
  };
  const sizes = { sm: "h-8 px-3 text-[13px]", md: "h-10 px-4 text-sm" };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-control font-medium transition-colors",
        "disabled:opacity-45 disabled:pointer-events-none",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}

/* ─── Поля ───────────────────────────────────────────────────────────── */
const fieldBase =
  "w-full rounded-control border border-line bg-white px-3 text-sm text-ink placeholder:text-ink-muted/60 " +
  "outline-none transition-colors focus:border-azure/70 disabled:opacity-50";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldBase, "h-10", className)} {...props} />;
}

export function Select({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select className={cn(fieldBase, "h-10 appearance-none pr-9", className)} {...props}>
        {children}
      </select>
      <svg
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted"
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round"
      >
        <path d="M5 9l7 7 7-7" />
      </svg>
    </div>
  );
}

/** Поле формы: подпись сверху, подсказка снизу. */
export function Field({
  label,
  hint,
  htmlFor,
  className,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  htmlFor?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={htmlFor} className="block text-[13px] font-medium text-ink">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs leading-snug text-ink-muted">{hint}</p>}
    </div>
  );
}

/* ─── Переключатель ──────────────────────────────────────────────────── */
export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: React.ReactNode;
  hint?: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cn("flex items-start gap-3", disabled ? "opacity-50" : "cursor-pointer")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors",
          checked ? "bg-coral-500" : "bg-line",
        )}
      >
        <span
          className={cn(
            "block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
            checked ? "translate-x-4" : "translate-x-0",
          )}
        />
      </button>
      <span className="space-y-0.5">
        <span className="block text-sm text-ink">{label}</span>
        {hint && <span className="block text-xs leading-snug text-ink-muted">{hint}</span>}
      </span>
    </label>
  );
}

/* ─── Контейнеры ─────────────────────────────────────────────────────── */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-card border border-line bg-white shadow-card", className)}
      {...props}
    />
  );
}

/** Заголовок секции с eyebrow (mono-подпись в духе бренда). */
export function SectionTitle({
  eyebrow,
  title,
  desc,
  right,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  desc?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4">
      <div>
        {eyebrow && (
          <div className="mb-1 font-mono text-[11px] uppercase tracking-[0.14em] text-coral-500">
            {eyebrow}
          </div>
        )}
        <h1 className="text-xl font-semibold tracking-tightest text-ink">{title}</h1>
        {desc && <p className="mt-1 text-sm text-ink-muted">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

/* ─── Бейджи / данные ────────────────────────────────────────────────── */
export function Badge({
  className,
  tone = "neutral",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "coral" | "azure" | "green" | "amber" | "violet";
}) {
  const tones = {
    neutral: "bg-canvas text-ink-muted border-line",
    coral: "bg-coral-soft text-coral-600 border-coral-500/20",
    azure: "bg-azure/10 text-azure-deep border-azure/25",
    green: "bg-emerald-50 text-emerald-700 border-emerald-200",
    amber: "bg-amber-50 text-amber-700 border-amber-200",
    violet: "bg-violet-50 text-violet-700 border-violet-200",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

/** Mono-«чип» для данных: таймкоды, ID, метрики (бренд-приём код-чипа). */
export function Mono({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn("tabular font-mono text-[12.5px] text-ink-muted", className)} {...props} />
  );
}

/** Узел-спикер: цветная точка (мотив «атома») + опциональное имя. */
export function SpeakerNode({
  name,
  size = 9,
  className,
  showName = true,
  color,
}: {
  name?: string | null;
  size?: number;
  className?: string;
  showName?: boolean;
  color?: string;
}) {
  const c = color ?? speakerColor(name);
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span
        className="inline-block shrink-0 rounded-full ring-2 ring-white"
        style={{ width: size, height: size, background: c }}
      />
      {showName && (
        <span className="text-[13px] font-medium text-ink">{name || "Без имени"}</span>
      )}
    </span>
  );
}

/* ─── Статус джобы (богатый) ─────────────────────────────────────────── */
const TONE_DOT: Record<string, string> = {
  wait: "bg-ink-muted",
  run: "bg-azure",
  done: "bg-emerald-500",
  error: "bg-coral-500",
};
const TONE_PILL: Record<string, string> = {
  wait: "bg-canvas text-ink-muted border-line",
  run: "bg-azure/10 text-azure-deep border-azure/25",
  done: "bg-emerald-50 text-emerald-700 border-emerald-200",
  error: "bg-coral-soft text-coral-600 border-coral-500/20",
};

export function StatusPill({ state, className }: { state: JobState; className?: string }) {
  const meta = STATUS_META[state];
  const running = meta.tone === "run";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium",
        TONE_PILL[meta.tone],
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", TONE_DOT[meta.tone], running && "animate-pulse-node")} />
      {meta.label}
    </span>
  );
}

/** Тонкая полоса прогресса в тон статуса. */
export function StageBar({ pct, state }: { pct: number; state: JobState }) {
  const tone = STATUS_META[state].tone;
  const fill =
    tone === "error" ? "bg-coral-500" : tone === "done" ? "bg-emerald-500" : "bg-azure";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line/70">
      <div
        className={cn("h-full rounded-full transition-all duration-500", fill)}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

/* ─── Табы ───────────────────────────────────────────────────────────── */
export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: React.ReactNode; icon?: React.ReactNode }[];
}) {
  return (
    <div className="flex flex-col gap-1">
      {tabs.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            onClick={() => onChange(t.value)}
            className={cn(
              "flex items-center gap-2.5 rounded-control px-3 py-2 text-left text-sm transition-colors",
              active
                ? "bg-coral-soft font-medium text-coral-600"
                : "text-ink-muted hover:bg-canvas hover:text-ink",
            )}
          >
            <span className={cn(active ? "text-coral-500" : "text-ink-muted")}>{t.icon}</span>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

/* ─── Состояния ──────────────────────────────────────────────────────── */
export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn("animate-spin text-coral-500", className)} width="20" height="20" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** Инлайн-индикатор загрузки: спиннер + подпись. */
export function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-muted">
      <Spinner className="h-4 w-4" /> {label}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  desc,
  action,
}: {
  icon?: React.ReactNode;
  title: React.ReactNode;
  desc?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-card border border-dashed border-line bg-white/60 px-6 py-16 text-center">
      {icon && (
        <div className="mb-4 grid h-12 w-12 place-items-center rounded-full bg-coral-soft text-coral-500">
          {icon}
        </div>
      )}
      <p className="text-base font-medium text-ink">{title}</p>
      {desc && <p className="mt-1 max-w-sm text-sm text-ink-muted">{desc}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** Инлайн-карточка ошибки. */
export function ErrorCard({ title, detail }: { title: string; detail?: React.ReactNode }) {
  return (
    <div className="rounded-card border border-coral-500/25 bg-coral-soft px-4 py-3">
      <p className="text-sm font-medium text-coral-600">{title}</p>
      {detail && <p className="mt-1 text-[13px] text-ink-muted">{detail}</p>}
    </div>
  );
}
