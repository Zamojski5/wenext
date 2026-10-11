/**
 * `POST /studio/assignment` — put an account on a project, or take it off.
 *
 * Taking it off does not delete the assignment: the control plane sets `unassigned_at` and the
 * row stays, so "który projekt pracował pod jaką tożsamością" survives the change.
 */
import { assignAccount, unassignAccount } from "../../../lib/control-plane";
import { backTo, field, formOf, safeReturn, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

const ACTIONS = ["assign", "unassign"] as const;

export async function POST(request: Request): Promise<Response> {
  const form = await formOf(request);
  if (form === null) return backTo("/", "refused");
  const back = safeReturn(form);

  const auth = await tokenOrLogin(back);
  if ("redirect" in auth) return auth.redirect;

  const action = field(form, "action");
  const workspaceId = field(form, "workspace_id");
  const projectId = field(form, "project_id");
  const accountId = field(form, "social_account_id");
  if (
    !ACTIONS.some((known) => known === action) ||
    workspaceId === "" ||
    projectId === "" ||
    accountId === ""
  ) {
    return backTo(back, "refused");
  }

  if (action === "assign") {
    return verdictOf(
      await assignAccount(auth.token, workspaceId, projectId, accountId),
      "account_assigned",
      back,
    );
  }
  return verdictOf(
    await unassignAccount(auth.token, workspaceId, projectId, accountId),
    "account_unassigned",
    back,
  );
}
