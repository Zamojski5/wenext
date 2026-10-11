/**
 * The three buttons of the "Pokój" and what each answer of the control plane means for them.
 *
 * NO ROUTE OF THEIR OWN. "Opublikuj" and "Popraw" are the approve and revise routes the
 * conversation cards already use, and "Pomiń" is `.../opportunities/{id}/dismiss`; all three go
 * through `/studio/card`, which checks the path against the workspace of the form. Two ways to
 * approve one draft would be two definitions of what may be published (`project_approvals.py`
 * opens with the same sentence about its own two routes).
 *
 * WHY THIS FILE EXISTS AT ALL. `/studio/card` answers every refusal with "card_refused", whose
 * sentence points at "the card in the conversation". On the Room there is no card, and the most
 * common refusal has a precise remedy: a project with no signed scope answers 409 and the person
 * has to sign it on the "Obecność" page. Telling them only "nie udało się" is how a founder tries
 * the demo, clicks "Opublikuj" and decides the product does not work. The handler lives in a route
 * file, which may export only HTTP methods, so the decision is here where a test can reach it.
 *
 * THE 409s ARE TOLD APART BY THE SENTENCE THE ROUTE SENDS (`project_approvals.py`), the only
 * thing that distinguishes them: scope not signed, daily budget spent, approval window passed, and
 * "this version is no longer the one waiting". That is a match on Polish text and is as brittle as
 * that sounds, so every pattern falls back to the generic refusal, which is true of all of them.
 */

export type RoomAction = "approve" | "revise" | "dismiss";

const APPROVE = /\/approvals\/[^/]+\/approve$/;
const REVISE = /\/approvals\/[^/]+\/revise$/;
const DISMISS = /\/opportunities\/[^/]+\/dismiss$/;

/** Which Room button this card route is, or `null` for any other route. */
export function roomActionOf(path: string): RoomAction | null {
  if (APPROVE.test(path)) return "approve";
  if (REVISE.test(path)) return "revise";
  if (DISMISS.test(path)) return "dismiss";
  return null;
}

/**
 * Whether the browser is sent back to a Room. The same approve route answers on a conversation
 * card, and its sentences are about the card there, so the Room's words are used only when the
 * person came from the Room.
 */
export function isRoomReturn(back: string): boolean {
  const pathname = back.split("?")[0] ?? "";
  return /\/p\/[^/]+\/room$/.test(pathname);
}

/** The `STUDIO_INFO` key for a refused room action. */
export function roomRefusalKey(action: RoomAction, status: number, detail: string): string {
  const said = detail.toLowerCase();
  if (action === "approve" && status === 409) {
    if (said.includes("powierzeni")) return "room_scope_unsigned";
    if (said.includes("budżet") || said.includes("budzet")) return "room_budget_spent";
    if (said.includes("okno zgody")) return "room_window_passed";
    if (
      said.includes("nie czeka już") ||
      said.includes("czeka na zgodę") ||
      said.includes("nie jest już aktualna")
    ) {
      return "room_draft_stale";
    }
  }
  if (action === "revise" && status === 409) {
    // The likeliest misclick of a demo: "Popraw", nothing changed, "Zapisz poprawkę". The route
    // answers 409 "poprawka nie zmienia treści", and "odśwież Pokój" would be the wrong advice.
    if (said.includes("nie zmienia treści")) return "room_revise_unchanged";
    if (said.includes("nie jest już aktualna") || said.includes("nie czeka")) {
      return "room_draft_stale";
    }
  }
  return "room_refused";
}

/** The `STUDIO_INFO` key for an accepted room action. "Accepted" is not "published". */
export function roomSuccessKey(action: RoomAction): string {
  switch (action) {
    case "approve":
      return "room_published";
    case "revise":
      return "room_revised";
    case "dismiss":
      return "room_dismissed";
  }
}
