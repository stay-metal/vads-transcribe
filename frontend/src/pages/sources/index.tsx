import { SectionTitle } from "@/components/ui";
import { LocalSource } from "./LocalSource";
import { YandexSource } from "./YandexSource";

/* ─── Страница «Источники» ───────────────────────────────────────────── */
export default function Sources() {
  return (
    <div className="space-y-6">
      <SectionTitle
        eyebrow="Приём записей"
        title="Источники"
        desc="Откуда приходят записи и как они автоматически попадают в транскрипцию."
      />
      <div className="space-y-4">
        <LocalSource />
        <YandexSource />
      </div>
    </div>
  );
}
