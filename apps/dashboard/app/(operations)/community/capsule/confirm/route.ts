/**
 * `POST /community/capsule/confirm` — the human says they are signed in; the control plane checks.
 *
 * This is the slowest write in the dashboard: the control plane switches the capsule's mode and
 * reads the platform twice, which its own session measured at up to a minute
 * (`CAPSULE_CONFIRM_TIMEOUT_MS`). It is still a plain form POST answered with 303, like every other
 * write here — no JavaScript, no polling, no spinner that would have to lie about progress.
 *
 * WHAT THE OPERATOR IS TOLD, and why it is a closed set of sentences rather than the numbers that
 * came back: `?info=` is text the SERVER chose (see `app/community/page.tsx`), so a link somebody
 * else crafts cannot put words on this screen. The measurement itself is not lost — the card
 * re-reads `GET /api/capsule` on the next render and shows the state the control plane now holds,
 * and the resumed jobs are visible on the queue screen where jobs live.
 *
 * A TIMEOUT IS NOT A FAILURE, and this is the correction the review caught: when the dashboard
 * stops waiting after 90 s, the capsule may already have switched modes and the parked jobs may
 * already be back in the queue. Reporting "nic nie zostało zmienione" there would be a claim about
 * a state nobody measured, so a timeout gets its own sentence and sends the operator to the card,
 * which re-reads the state on the next render.
 *
 * A REFUSAL IS NOT A SIGN-IN. When the control plane answers `SIGNED_OUT`, the operator is told the
 * check found no session — never "done" — because the whole point of this button is that somebody
 * measured, and a confirmation that reported success regardless would make the measurement
 * decorative.
 */
import { confirmDecision } from "../../../../../lib/capsule-flow";
import { confirmCapsuleSession } from "../../../../../lib/control-plane";
import { LOGIN_PATH } from "../../../../../lib/routes";
import { backWith, seeOther, tokenOrLogin } from "../../_handler";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;

  const decision = confirmDecision(await confirmCapsuleSession(auth.token));
  if (decision.kind === "login") return seeOther(LOGIN_PATH);
  return backWith(decision.info);
}
