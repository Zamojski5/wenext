/**
 * `POST /studio/people` — the four writes of "Profile i konta".
 *
 * `create_profile`, `add_account`, `attach_account`, `detach_account`, `save_voice`. One closed
 * set, one refusing default; a form field must not be able to name an operation nobody declared.
 *
 * `save_voice` ALWAYS WRITES A NEW VERSION. There is no update here and there cannot be one: the
 * control plane refuses it and the database refuses it under that. The same action serves the
 * "Zapisz" button and the "Przejmij treść profilu pilota" button, because both send the same
 * four fields and differ only in where the person got the text from.
 *
 * `detach_account` is the one that must NOT delete anything: it calls the control plane's DELETE
 * on the LINK, which sets `detached_at` and leaves the row. The message the user gets back says
 * so, because "odpięte" and "usunięte" are two different promises about their history.
 */
import {
  attachAccount,
  createPersonProfile,
  detachAccount,
  registerAccount,
  savePersonVoice,
} from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = [
  "create_profile",
  "add_account",
  "attach_account",
  "detach_account",
  "save_voice",
] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  const workspaceId = field(form, "workspace_id");
  if (!ACTIONS.some((known) => known === action) || workspaceId === "") {
    return backTo(back, "refused");
  }

  const profileId = field(form, "person_profile_id");
  const accountId = field(form, "social_account_id");

  if (action === "create_profile") {
    const name = field(form, "name");
    const kind = field(form, "kind");
    if (name === "" || kind === "") return backTo(back, "refused");
    return verdictOf(
      await createPersonProfile(auth.token, workspaceId, { name, kind }),
      "profile_created",
      back,
    );
  }

  if (action === "add_account") {
    const platform = field(form, "platform");
    const handle = field(form, "external_account_id");
    if (platform === "" || handle === "") return backTo(back, "refused");
    return verdictOf(
      await registerAccount(auth.token, workspaceId, {
        platform,
        external_account_id: handle,
        // Adding the account under a profile is ONE write: a two-step version leaves a
        // half-added account behind whenever the second step fails.
        ...(profileId === "" ? {} : { person_profile_id: profileId }),
      }),
      "account_added",
      back,
    );
  }

  if (action === "save_voice") {
    const knowsDeeply = field(form, "knows_deeply");
    const positions = field(form, "positions");
    const wouldNeverClaim = field(form, "would_never_claim");
    // Próbki mogą być puste: próbek cudzego pisania można nie mieć. Trzech pozostałych
    // pustych nie przyjmujemy, bo to byłby głos, który nic nie mówi.
    if (profileId === "" || knowsDeeply === "" || positions === "" || wouldNeverClaim === "") {
      return backTo(back, "refused");
    }
    return verdictOf(
      await savePersonVoice(auth.token, workspaceId, profileId, {
        knows_deeply: knowsDeeply,
        positions,
        would_never_claim: wouldNeverClaim,
        voice_samples: field(form, "voice_samples"),
      }),
      "voice_saved",
      back,
    );
  }

  if (profileId === "" || accountId === "") return backTo(back, "refused");
  if (action === "attach_account") {
    return verdictOf(
      await attachAccount(auth.token, workspaceId, profileId, accountId),
      "account_attached",
      back,
    );
  }
  return verdictOf(
    await detachAccount(auth.token, workspaceId, profileId, accountId),
    "account_detached",
    back,
  );
}
