import { IconTrash } from "@/components/icons";
import { cn } from "@/lib/utils";

/* ─── Пресеты раскладки как чипы (именованные профили) ────────────────── */
export function PresetChips({
  presets,
  activeId,
  onPick,
  onDelete,
}: {
  presets: { id: string; name: string; builtin: boolean }[];
  activeId: string;
  onPick: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {presets.map((p) => {
        const active = p.id === activeId;
        return (
          <span
            key={p.id}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] transition-colors",
              active
                ? "border-coral-500/40 bg-coral-soft font-medium text-coral-600"
                : "border-line bg-white text-ink-muted hover:text-ink",
            )}
          >
            <button type="button" onClick={() => onPick(p.id)}>
              {p.name}
            </button>
            {!p.builtin && (
              <button
                type="button"
                onClick={() => onDelete(p.id)}
                aria-label="Удалить пресет"
                className="-mr-1 grid h-4 w-4 place-items-center rounded-full text-ink-muted hover:bg-coral-500/15 hover:text-coral-500"
              >
                <IconTrash size={11} />
              </button>
            )}
          </span>
        );
      })}
      {activeId === "" && (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-coral-500/40 bg-coral-soft px-3 py-1 text-[13px] font-medium text-coral-600">
          Свой
        </span>
      )}
    </div>
  );
}
