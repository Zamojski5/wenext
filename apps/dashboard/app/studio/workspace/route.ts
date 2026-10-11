/** `POST /studio/workspace` — create one workspace (`POST /api/workspaces`). */
import { createWorkspace } from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const name = field(form, "name");
  // An empty name is refused HERE as well as in the control plane. Both checks are wanted: this
  // one keeps a pointless round trip out of the way, the other one is the rule.
  if (name === "") return backTo(back, "refused");

  return verdictOf(await createWorkspace(auth.token, name), "workspace_created", back);
}
