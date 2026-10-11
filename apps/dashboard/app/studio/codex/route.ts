/**
 * `POST /studio/codex` — the five writes of the "Połączenia Codex" screen.
 *
 * `adopt_root`, `create`, `refresh`, `login`, `disconnect`. One closed set, one refusing default,
 * exactly like the other studio handlers: a hidden field must not be able to name an operation
 * nobody declared.
 *
 * `disconnect` DOES NOT LOG THE ACCOUNT OUT, and that is a product decision rather than an
 * omission. Signing the account out invalidates the session for everything else using it, and
 * this button means "stop using it here". The control plane has no route that sends
 * `account/logout`, so this handler could not do it even by mistake.
 */
import {
  adoptCodexRoot,
  createCodexConnection,
  disconnectCodexConnection,
  refreshCodexConnection,
  restartCodexLogin,
} from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = ["adopt_root", "create", "refresh", "login", "disconnect"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  if (!ACTIONS.some((known) => known === action)) return backTo(back, "refused");

  if (action === "adopt_root") {
    const label = field(form, "label");
    if (label === "") return backTo(back, "refused");
    return verdictOf(await adoptCodexRoot(auth.token, label), "codex_adopted", back);
  }

  if (action === "create") {
    const label = field(form, "label");
    if (label === "") return backTo(back, "refused");
    const workspaceId = field(form, "workspace_id");
    return verdictOf(
      await createCodexConnection(auth.token, {
        label,
        ...(workspaceId === "" ? {} : { workspace_id: workspaceId }),
      }),
      "codex_created",
      back,
    );
  }

  const connectionId = field(form, "connection_id");
  if (connectionId === "") return backTo(back, "refused");
  if (action === "refresh") {
    return verdictOf(await refreshCodexConnection(auth.token, connectionId), "codex_refreshed", back);
  }
  if (action === "login") {
    return verdictOf(await restartCodexLogin(auth.token, connectionId), "codex_login_started", back);
  }
  return verdictOf(
    await disconnectCodexConnection(auth.token, connectionId),
    "codex_disconnected",
    back,
  );
}
