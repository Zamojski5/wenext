import { Signpost } from "../../../components/views/signpost";
import { getJudgments } from "../../../lib/control-plane";
import { requireOperator } from "../../../lib/guard";
import { labelOf } from "../../../lib/routes";

/**
 * `GET /` — one decision on the surface, and where to look for the rest.
 *
 * THE ONE NUMBER HERE IS READ, NOT INVENTED. The tile says how many judgments are waiting for the
 * operator, and it comes from `GET /api/judgments?decision=ENGAGE` — the same read `/community`
 * makes. When that read FAILS the tile says "nie odczytano" with the reason; it never says `0`,
 * because "0 waiting" over a queue nobody read is `all([]) == True` with a number in front of it
 * (D-011), which is exactly the defect this package was built to remove. That is also why this is
 * the ONLY number on the screen: every other one would be a second chance to make that mistake.
 *
 * `reads: true` in `lib/routes.ts` follows from this: the flag says whether a screen touches the
 * control plane, and now this one does.
 *
 * The prose is one human sentence plus a collapsed "Dlaczego" — the rules about retries and empty
 * screens are true and worth stating, and they are not what somebody opening the panel needs to
 * read first (docs/DASHBOARD-DESIGN.md §1, zasada nr 1).
 */
export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const gate = await requireOperator();

  let waiting: number | null = null;
  let reason: string | undefined;
  if (gate.kind === "unreachable") {
    reason = gate.reason;
  } else {
    const engage = await getJudgments(gate.token, "ENGAGE");
    if (engage.ok) {
      waiting = engage.value.rows.length;
    } else {
      reason = engage.reason;
    }
  }

  return (
    <Signpost
      destinations={[
        {
          href: "/community",
          label: labelOf("community"),
          note: "Co się dzieje w obserwowanym community i gdzie agent widzi miejsce dla Ciebie.",
        },
        {
          href: "/jobs",
          label: labelOf("jobs"),
          note: "Co agent robi teraz, co czeka i co się nie udało.",
        },
        {
          href: "/audit",
          label: labelOf("audit"),
          note: "Co system zrobił, po kolei, bez pomijania niczego.",
        },
      ]}
      heading={labelOf("overview")}
      highlight={{ href: "/community", label: "do przejrzenia", reason, value: waiting }}
      paragraphs={[
        "Agent obserwuje community w Twoim imieniu i nic nie publikuje.",
        "Panel nie ma żadnej akcji zmieniającej zadanie agenta. Nieudane próby ponawia sam " +
          "mechanizm kolejki, bez człowieka. Po wysłaniu czegokolwiek na platformę ponowienie jest " +
          "zabronione, bo skutek mógł się już wykonać (L2673).",
        "Pusty ekran jest tu stanem normalnym. Panel rozróżnia „brak danych” (odczyt się udał, nic " +
          "nie ma) od „brak odpowiedzi” (odczytu nie było) i nigdy nie zamienia jednego na drugie.",
      ]}
      routeId="overview"
    />
  );
}
