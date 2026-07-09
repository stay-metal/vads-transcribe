import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Брендовый знак BloodAgents — «орбита»: ядро и шесть узлов на окружности
 * (участники диалога вокруг центра). Белый на коралле, currentColor.
 */
export function AtomMark({ className, ...rest }: React.SVGProps<SVGSVGElement>) {
  const r = 7.4; // радиус орбиты
  const dots = [0, 60, 120, 180, 240, 300].map((deg) => {
    const a = ((deg - 90) * Math.PI) / 180;
    return { cx: 12 + r * Math.cos(a), cy: 12 + r * Math.sin(a) };
  });
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      {...rest}
    >
      <circle cx="12" cy="12" r={r} fill="none" stroke="currentColor" strokeWidth="0.9" />
      {dots.map((d, i) => (
        <circle key={i} cx={d.cx} cy={d.cy} r="1.55" fill="currentColor" />
      ))}
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
    </svg>
  );
}

/**
 * Знак в скруглённом коралловом квадрате (как favicon бренда, rx≈28%).
 * `size` — сторона в px. Белый знак на коралле.
 */
export function AtomBadge({ size = 36, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cn("inline-grid place-items-center bg-coral text-white shadow-soft", className)}
      style={{ width: size, height: size, borderRadius: size * 0.28 }}
    >
      <AtomMark style={{ width: size * 0.86, height: size * 0.86 }} />
    </span>
  );
}

/** Wordmark: домен BloodAgents + название продукта «Транскрибация» мельче. */
export function Wordmark() {
  return (
    <span className="flex flex-col leading-none">
      <span className="text-[15px] font-semibold tracking-tightest text-ink">BloodAgents</span>
      <span className="mt-[3px] text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted">
        Транскрибация
      </span>
    </span>
  );
}
