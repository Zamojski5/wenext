/**
 * `POST /studio/organization` — the five writes of the space settings screen (P7 etap A).
 *
 * `invite`, `revoke`, `set_email`, `export`, `delete`. One closed set and a refusing default,
 * like every other studio handler.
 *
 * TWO OF THEM ARE NOT LIKE THE OTHERS AND THE CODE SAYS SO:
 *
 *   * `invite` gets a TOKEN back and puts it in the redirect, because etap A sends no mail and
 *     the person who invited has to be able to copy the link. It is a one-time credential, so it
 *     travels once, to the screen of the person who just created it, and is stored nowhere;
 *   * `delete` carries a password and destroys somebody's work. It is the one action here with a
 *     confirmation that cannot be clicked through by accident.
 */
import {
  inviteMember,
  requestDeletion,
  requestExport,
  revokeMember,
  setUserEmail,
} from "../../../lib/control-plane";
import { getOrganizations } from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = ["invite", "revoke", "set_email", "export", "delete"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  if (!ACTIONS.some((known) => known === action)) return backTo(back, "refused");

  if (action === "set_email") {
    const email = field(form, "email");
    if (email === "") return backTo(back, "refused");
    return verdictOf(await setUserEmail(auth.token, email), "email_saved", back);
  }

  // The remaining four act on the organization this session is in. It is read from the control
  // plane rather than taken from the form: an organization in a hidden field is an organization
  // anybody can change.
  const spaces = await getOrganizations(auth.token);
  if (!spaces.ok) return backTo(back, "unreachable");
  const organizationId = spaces.value.current;

  if (action === "invite") {
    const email = field(form, "email");
    if (email === "") return backTo(back, "refused");
    const invited = await inviteMember(auth.token, organizationId, email);
    if (!invited.ok) return backTo(back, invited.unauthorized ? "refused" : "refused");
    // The link, once, on the screen of the person who made it.
    const separator = back.includes("?") ? "&" : "?";
    const link = `/invitation?token=${encodeURIComponent(invited.value.token)}`;
    return new Response(null, {
      status: 303,
      headers: {
        location: `${back}${separator}info=member_invited&link=${encodeURIComponent(link)}`,
        "cache-control": "no-store",
      },
    });
  }

  if (action === "revoke") {
    const userId = field(form, "user_id");
    if (userId === "") return backTo(back, "refused");
    return verdictOf(await revokeMember(auth.token, organizationId, userId), "member_revoked", back);
  }

  if (action === "export") {
    return verdictOf(await requestExport(auth.token, organizationId), "export_requested", back);
  }

  const password = field(form, "password");
  if (password === "") return backTo(back, "refused");
  return verdictOf(
    await requestDeletion(auth.token, organizationId, password),
    "organization_deleted",
    back,
  );
}
