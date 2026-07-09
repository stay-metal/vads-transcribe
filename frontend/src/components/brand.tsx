import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Брендовый знак «атом» Ponimaiu — участники диалога как узлы вокруг центра.
 * Пути извлечены бит-в-бит из брендбука (ponimaiu_docs/components/Brand.tsx),
 * viewBox `300 615 120 120`, заливка currentColor: 5 капсул-лучей + точка (396,675).
 */
export function AtomMark({ className, ...rest }: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      className={className}
      viewBox="300 615 120 120"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      {...rest}
    >
      <path d="M 396 699 C 409.253906 699 420 688.253906 420 675 C 420 661.746094 409.253906 651 396 651 C 382.746094 651 372 661.746094 372 675 C 372 688.253906 382.746094 699 396 699 Z" />
      <path d="M 351 642 C 351 646.980469 355.019531 651 360 651 C 364.980469 651 369 646.980469 369 642 L 369 615 L 351 615 Z" />
      <path d="M 323.941406 626.21875 L 311.21875 638.941406 L 330.300781 658.019531 C 333.808594 661.53125 339.511719 661.53125 343.019531 658.019531 C 346.53125 654.511719 346.53125 648.808594 343.019531 645.300781 Z" />
      <path d="M 336 675 C 336 670.019531 331.980469 666 327 666 L 300 666 L 300 684 L 327 684 C 331.980469 684 336 679.980469 336 675 Z" />
      <path d="M 330.300781 691.980469 L 311.21875 711.058594 L 323.941406 723.78125 L 343.019531 704.699219 C 346.53125 701.191406 346.53125 695.488281 343.019531 691.980469 C 339.511719 688.46875 333.808594 688.46875 330.300781 691.980469 Z" />
      <path d="M 360 699 C 355.019531 699 351 703.019531 351 708 L 351 735 L 369 735 L 369 708 C 369 703.019531 364.980469 699 360 699 Z" />
    </svg>
  );
}

/**
 * Знак в скруглённом коралловом квадрате (как favicon бренда, rx≈20%).
 * `size` — сторона в px. Белый знак на коралле.
 */
export function AtomBadge({ size = 36, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cn("inline-grid place-items-center bg-coral text-white shadow-soft", className)}
      style={{ width: size, height: size, borderRadius: size * 0.28 }}
    >
      <AtomMark style={{ width: size * 0.62, height: size * 0.62 }} />
    </span>
  );
}

/**
 * Wordmark продукта — строчными в брендовой трактовке (вес 600, плотный трекинг).
 * `eyebrow` — надпись-подпись зонтичного бренда «Понимаю AI».
 */
export function Wordmark({ eyebrow = false }: { eyebrow?: boolean }) {
  return (
    <span className="flex flex-col leading-none">
      <span className="text-[15px] font-semibold tracking-tightest text-ink">bloodtranscripts</span>
      {eyebrow && (
        <span className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted">
          Понимаю AI
        </span>
      )}
    </span>
  );
}
