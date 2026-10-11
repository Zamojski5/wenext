import { Signpost } from "../../../components/views/signpost";
import { requireOperator } from "../../../lib/guard";
import { labelOf } from "../../../lib/routes";

/**
 * `GET /transitions` — a SIGNPOST, not a list. See `components/views/signpost.tsx` for why.
 *
 * Short version: E1.F1 records state transitions (`control_plane.locking.apply_transition` writes
 * them and `AuditWriter` logs them) but exposed NO READER for them at commit 98570ed, where that
 * was measured. An empty table here would therefore say "brak danych" — a claim about a read that
 * never happened — which is exactly the defect this package was built to remove. So the screen says
 * where those facts are visible today. `lib/routes.ts` carries the trigger for revisiting this.
 */
export const dynamic = "force-dynamic";

export default async function TransitionsPage() {
  await requireOperator();
  return (
    <Signpost
      destinations={[
        {
          href: "/audit",
          label: labelOf("audit"),
          note: "Każde przejście stanu jest tam zdarzeniem, zapisanym w tej samej transakcji co zmiana.",
        },
      ]}
      heading={labelOf("transitions")}
      paragraphs={[
        "Przejścia stanów widać dziś w dzienniku. Kliknij poniżej.",
        "Ten ekran nie czyta niczego, dlatego nie pokazuje listy. Control plane zapisuje przejścia " +
          "maszyn stanów razem z optimistic locking (L5191), ale nie wystawia dla nich trasy odczytu.",
        "Pusta tabela w tym miejscu twierdziłaby, że odczyt się udał i nic nie zwrócił. Żadnego " +
          "odczytu nie było, więc byłoby to fałszywe zaliczenie narysowane w HTML-u.",
      ]}
      routeId="transitions"
    />
  );
}
