/**
 * `POST /studio/project` — create a project, or change one.
 *
 * Two actions behind one handler, dispatched on a CLOSED set with a refusing default. A permissive
 * default would turn a misspelled hidden field into "do whatever the first branch does".
 *
 * The update sends `paused` on every submit, in both directions, because an unchecked checkbox is
 * simply absent from the body: a handler that only ever sent `paused: true` would give the user a
 * pause with no way out.
 */
import { projectHref } from "../../../lib/routes";
import { createProject, updateProject } from "../../../lib/control-plane";
import {
  backTo,
  checkbox,
  field,
  formOf,
  safeReturn,
  tokenOrLogin,
  verdictOf,
} from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = ["create", "update"] as const;

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

  const name = field(form, "name");
  const goal = field(form, "goal");
  const mode = field(form, "mode");

  if (action === "create") {
    if (name === "" || mode === "") return backTo(back, "refused");
    const outcome = await createProject(auth.token, workspaceId, { name, goal, mode });
    const created = outcome.ok && outcome.value.status >= 200 && outcome.value.status < 300
      ? outcome.value.createdProjectId : undefined;
    return verdictOf(outcome, "project_created", created ? projectHref(workspaceId, created) : back);
  }

  const projectId = field(form, "project_id");
  if (projectId === "" || name === "" || mode === "") return backTo(back, "refused");
  // 0026: limit rozmowy. PUSTE POLE ZNACZY „WRÓĆ DO DOMYŚLNEGO", nie „zero" i nie „bez limitu",
  // więc gdy oba pola są puste, wysyłamy jawną prośbę o powrót do wartości domyślnej. Bez tego
  // nie dałoby się cofnąć własnego ustawienia, bo brak pola znaczy „nie ruszaj".
  const budget: Record<string, number> = {};
  for (const key of ["max_turns", "max_tool_calls"] as const) {
    const raw = field(form, key);
    if (raw === "") continue;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < 1) return backTo(back, "refused");
    budget[key] = value;
  }
  const empty = Object.keys(budget).length === 0;
  return verdictOf(
    await updateProject(auth.token, workspaceId, projectId, {
      name,
      goal,
      mode,
      paused: checkbox(form, "paused"),
      ...(empty ? { conversation_budget_default: true } : { conversation_budget: budget }),
    }),
    "project_updated",
    back,
  );
}
