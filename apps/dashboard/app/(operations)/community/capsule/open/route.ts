/**
 * `POST /community/capsule/open` — mint a ticket and send THIS TAB to the capsule's viewer.
 *
 * The form that posts here carries `target="_blank"`, so this handler runs in a NEW tab: the 303
 * below lands that tab on the capsule's own browser while the dashboard stays where it was. That
 * is how "open in a new tab" happens without a line of JavaScript.
 *
 * THE REDIRECT LEAVES THIS APPLICATION, and that is the one place in this package where it does.
 * The address comes from the control plane as `href`, points at the gateway on the loopback
 * interface, and carries a ticket that lives about two minutes — rendering it as a link instead
 * would hand the operator a dead address, which is why this is one click and one redirect.
 *
 * AND IT IS CHECKED AGAINST AN ALLOWLIST BEFORE ANY BROWSER FOLLOWS IT. "The value came from our
 * own API" is the reasoning behind every open redirect ever shipped; one bug upstream and this
 * screen becomes a jump host. `lib/capsule-flow.ts` compares the address with the single
 * configured origin (`WENEXT_CAPSULE_VIEWER_ORIGIN`) and refuses everything else, including a
 * missing configuration — the operator then gets a sentence instead of a jump.
 *
 * A FAILURE COMES BACK AS A SENTENCE, NOT AS A BLANK TAB: the new tab lands on `/community` with a
 * message from the closed set in `app/community/page.tsx`, so the operator learns what happened
 * instead of staring at an error page in a window they did not expect to have.
 */
import { capsuleViewerOrigin, openDecision } from "../../../../../lib/capsule-flow";
import { openCapsuleSession } from "../../../../../lib/control-plane";
import { LOGIN_PATH } from "../../../../../lib/routes";
import { backWith, seeOther, tokenOrLogin } from "../../_handler";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;

  const decision = openDecision(await openCapsuleSession(auth.token), capsuleViewerOrigin());
  if (decision.kind === "login") return seeOther(LOGIN_PATH);
  if (decision.kind === "info") return backWith(decision.info);
  return seeOther(decision.href);
}
