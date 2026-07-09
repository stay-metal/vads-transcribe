import type { Job } from "@/api/types";
import { cn, SPEAKER_COLORS } from "@/lib/utils";
import { IconMic } from "@/components/icons";

/* ─── Сигнатура: голосовой глиф (мотив «атома») ──────────────────────────
 * route_a → кластер разделённых узлов-спикеров по track_count;
 * single  → цельный атом-хаб в ореоле (весь разговор сведён в один трек).
 * Статус кодируется приглушением (error/canceled), как и левый якорь строки. */
export function VoiceGlyph({
  job,
  size = "sm",
  muted,
}: {
  job: Job;
  size?: "sm" | "lg";
  muted?: boolean;
}) {
  const dot = size === "lg" ? 15 : 13;
  const wrap = cn(
    "flex w-11 shrink-0 items-center",
    size === "lg" && "w-12",
    muted && "opacity-45 saturate-[.35]",
  );

  if (job.mode === "route_a") {
    const n = Math.max(1, job.track_count ?? 2);
    const shown = Math.min(n, 3);
    const extra = n - shown;
    return (
      <div className={wrap}>
        <div className="flex">
          {Array.from({ length: shown }).map((_, i) => (
            <span
              key={i}
              className="rounded-full ring-2 ring-white"
              style={{
                width: dot,
                height: dot,
                background: SPEAKER_COLORS[i % SPEAKER_COLORS.length],
                marginLeft: i === 0 ? 0 : -dot * 0.42,
                zIndex: shown - i,
              }}
            />
          ))}
          {extra > 0 && (
            <span
              className="grid place-items-center rounded-full bg-canvas font-mono font-semibold text-ink-muted ring-2 ring-white"
              style={{ width: dot + 2, height: dot + 2, marginLeft: -dot * 0.42, fontSize: 9 }}
            >
              +{extra}
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={wrap}>
      <span
        className="grid place-items-center rounded-full bg-coral-soft text-coral-500"
        style={{ width: dot + 10, height: dot + 10 }}
      >
        <IconMic size={dot} />
      </span>
    </div>
  );
}
