/**
 * The two reads every studio screen needs before it can draw its own: the workspaces the operator
 * has, and the one this URL names together with its projects.
 *
 * WHY THE SHELL IS FETCHED PER PAGE AND NOT IN A LAYOUT. A Next.js layout only receives the
 * dynamic segments of ITS OWN path, so a layout at `(studio)` would not know the workspace, and
 * one at `w/[workspaceId]` would know the workspace but not which project is open — it could
 * render the sidebar and not mark the active entry. Marking it from CSS was the other option and
 * it does not work here: `aria-current` would have to be chosen by comparing two values the
 * stylesheet cannot see. So each page loads this frame and says what is active, explicitly.
 *
 * The cost is two extra reads per page view, on loopback, alongside the one `GET /api/session`
 * the guard already makes. That is the price of a sidebar that is never wrong about where you
 * are.
 *
 * `null` inside `ok: true` means the control plane answered 404: there is no such workspace in
 * this organization. It is a FACT, not a failed read, and the screen says so in those words.
 */
import { getWorkspace, getWorkspaces, type Read } from "./control-plane";
import type { ProjectSummary, WorkspaceSummary } from "./workspace-schema";

export interface StudioFrame {
  readonly workspaces: readonly WorkspaceSummary[];
  readonly workspace: WorkspaceSummary;
  readonly projects: readonly ProjectSummary[];
}

export async function loadStudioFrame(
  token: string,
  workspaceId: string,
): Promise<Read<StudioFrame | null>> {
  const all = await getWorkspaces(token);
  if (!all.ok) return all;
  const detail = await getWorkspace(token, workspaceId);
  if (!detail.ok) return detail;
  if (detail.value === null) return { ok: true, value: null };
  return {
    ok: true,
    value: {
      workspaces: all.value.workspaces,
      workspace: detail.value.workspace,
      projects: detail.value.projects,
    },
  };
}
