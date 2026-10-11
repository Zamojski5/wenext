import { Signpost } from "../../../components/views/signpost";
import { requireOperator } from "../../../lib/guard";
import { labelOf } from "../../../lib/routes";

/**
 * `GET /approvals` — a SIGNPOST, not a list, and above all NOT a queue with buttons.
 *
 * Two separate reasons, and both matter:
 *
 *   1. **No reader existed at commit 98570ed**, where that was measured. F1 creates the tables of
 *      the approval path (migration 0003: `action_proposals`, `action_proposal_revisions`,
 *      `approval_decisions`, `execution_intents`) but mounted no route that lists them. An empty
 *      table here would claim a read that never happened. `lib/routes.ts` carries the trigger:
 *      if a reader appears in this phase, this screen becomes a list view.
 *   2. **The approval queue AS A PRODUCT, with actions, is E1.F4** (L5299, and the scope note
 *      L5500-5501 forbids spilling it earlier). What F1 owns is the RULE: an approval points at an
 *      exact payload hash and editing the content invalidates the consent (L5218-5219). A button
 *      here would be the product, four phases early, on top of a path with no producer.
 */
export const dynamic = "force-dynamic";

export default async function ApprovalsPage() {
  await requireOperator();
  return (
    <Signpost
      heading={labelOf("approvals")}
      paragraphs={[
        "Tu nie ma nic do zrobienia: ścieżka zgody nie ma jeszcze producenta propozycji.",
        "Ten ekran nie czyta niczego i nie ma tu żadnego przycisku zatwierdzania. Tabele ścieżki " +
          "zgody istnieją (migracja 0003: action_proposals, action_proposal_revisions, " +
          "approval_decisions, execution_intents), ale nie mają trasy odczytu ani trasy decyzji.",
        "Reguła, którą system naprawdę egzekwuje, jest w kontraktach i w schemacie: zgoda wskazuje " +
          "DOKŁADNY payload hash, a edycja treści ją unieważnia (L5218-5219). Działa bez ekranu " +
          "i jest sprawdzana testami pakietu contracts.",
        "Kolejka approvali jako produkt, z listą do przeglądu i decyzjami operatora, powstaje " +
          "dopiero wtedy, gdy pojawi się producent propozycji (D-013). Zbudowanie jej tutaj byłoby " +
          "rozlaniem zakresu, którego L5500-5501 zabrania wprost.",
      ]}
      routeId="approvals"
    />
  );
}
