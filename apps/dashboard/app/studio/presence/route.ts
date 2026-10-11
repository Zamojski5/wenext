/**
 * `POST /studio/presence`: what a person does with a delegation, and with a promised return.
 *
 * Sign it, pause it, resume it, revoke it, and call off one scheduled return (0023). Five
 * actions behind one handler, dispatched on a CLOSED set with a refusing default, like every
 * other studio handler: a permissive default would turn a misspelled hidden field into "do
 * whatever the first branch does", and the first branch here would be a pause.
 *
 * CANCELLING A RETURN IS NOT PAUSING THE AGENT, and it is in this handler only because it is
 * done from this screen. It touches one wake-up row and leaves the signature alone: the agent
 * keeps working, it just does not come back to that one thing at that one hour.
 *
 * REVOKING IS NOT A SPELLING VARIANT OF PAUSING, so it is its own value in that set and its own
 * button behind a confirmation on the screen. A pause comes back on the same signature; a
 * revocation never comes back, and no handler should be able to reach it by accident.
 *
 * NONE OF THESE WAITS FOR THE AGENT. Each is one control plane write, and then a 303 back to the
 * presence screen. Pausing also interrupts whatever the agent is doing, but the interrupt is an
 * event the running turn reads from its own trace (`api/sessions.py` explains why), so this
 * handler does not wait for the turn to notice.
 */
import { cancelReturn, changeDelegation, signDelegation } from "../../../lib/presence-client";
import { delegationExpiresAt } from "../../../lib/presence-schema";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = ["sign", "pause", "resume", "revoke", "cancel_return"] as const;

/** What a scope may permit. Checked here as well, so a hand-made POST cannot widen the set. */
const KNOWN_ACTIONS = ["REPLY", "OWN_POST", "RESEARCH"] as const;
const KNOWN_MODES = ["AUTONOMOUS", "REVIEW", "RESEARCH"] as const;

/**
 * A budget field: a whole non-negative count, or absent.
 *
 * ABSENT IS NOT ZERO, and that is the whole reason this returns `undefined` rather than 0. Zero
 * publications a day is a delegation that may never publish; an absent key means "I did not set
 * one", and the control plane then applies its conservative default. Turning a blank field into
 * a zero would silently sign the first of those while the person meant the second.
 */
function limit(form: FormData, name: string): number | undefined {
  const raw = field(form, name);
  if (raw === "") return undefined;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) return undefined;
  return value;
}

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  const workspaceId = field(form, "workspace_id");
  const projectId = field(form, "project_id");
  const known = ACTIONS.find((one) => one === action);
  if (known === undefined || workspaceId === "" || projectId === "") {
    return backTo(back, "refused");
  }

  if (known === "sign") {
    const mode = field(form, "mode");
    if (!KNOWN_MODES.some((one) => one === mode)) return backTo(back, "refused");
    // THE DEADLINE IS REQUIRED and comes from a closed list (26.09). A missing or hand-made value
    // is refused rather than defaulted: a default chosen here would be a deadline the person
    // never saw, on the one screen whose purpose is to show what they are signing.
    const expiresAt = delegationExpiresAt(Number(field(form, "expires_in_hours")), new Date());
    if (expiresAt === null) return backTo(back, "refused");
    // Communities arrive as one line the person typed. Split, trimmed, and the empties dropped:
    // a blank entry would be a community key that matches nothing and reads like a mistake.
    const communities = field(form, "communities")
      .split(",")
      .map((one) => one.trim())
      .filter((one) => one !== "");
    const accounts = form
      .getAll("accounts")
      .filter((one): one is string => typeof one === "string" && one !== "");
    const actions = KNOWN_ACTIONS.filter((one) => form.getAll("actions").includes(one));
    const budget: Record<string, number> = {};
    for (const [fieldName, key] of [
      ["publications_per_day", "publications_per_day"],
      ["publications_per_hour", "publications_per_hour"],
      // 0023: własne posty mają swój sufit, więc i swoje pole.
      ["own_posts_per_day", "own_posts_per_day"],
      ["model_turns_per_day", "model_turns_per_day"],
    ] as const) {
      const value = limit(form, fieldName);
      if (value !== undefined) budget[key] = value;
    }
    return verdictOf(
      await signDelegation(auth.token, workspaceId, projectId, {
        mode,
        accounts,
        communities,
        actions,
        budget,
        expires_at: expiresAt,
      }),
      "delegation_signed",
      back,
    );
  }

  if (known === "cancel_return") {
    // The row's own identifier, from the hidden field of the row's own form. Missing means a
    // hand-made POST or a broken screen, and either way there is nothing to cancel.
    const wakeupId = field(form, "wakeup_id");
    if (wakeupId === "") return backTo(back, "refused");
    return verdictOf(
      await cancelReturn(auth.token, workspaceId, projectId, wakeupId),
      "return_cancelled",
      back,
    );
  }

  return verdictOf(
    await changeDelegation(auth.token, workspaceId, projectId, known),
    `delegation_${known}d`,
    back,
  );
}
