/**
 * `GET /studio/organization/export?workspace_id=…&job_id=…` — hands the export file to the browser.
 *
 * A GET, unlike every other handler under `app/studio/`, because a download is a read: the file
 * already exists and this route only moves it. It streams through THIS server rather than
 * redirecting the browser to the control plane, for the reason the whole package exists: the
 * browser never talks to the control plane, and the session token never leaves this process
 * (D-016).
 *
 * The file is an organization's whole export, so it is served with `content-disposition:
 * attachment` and `cache-control: no-store`: a copy of a tenant sitting in a browser cache is a
 * copy nobody decided to keep.
 */
import { controlPlaneUrl } from "../../../../lib/control-plane";
import { LOGIN_PATH } from "../../../../lib/routes";
import { tokenOrLogin } from "../../_handler";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const jobId = (url.searchParams.get("job_id") ?? "").trim();
  const workspaceId = (url.searchParams.get("workspace_id") ?? "").trim();
  if (jobId === "" || workspaceId === "") {
    return new Response(null, { status: 303, headers: { location: "/" } });
  }

  const auth = await tokenOrLogin("/");
  if ("redirect" in auth) return auth.redirect;

  const organizations = await fetchJson(auth.token, "/api/organizations");
  if (organizations === null) {
    return new Response(null, { status: 303, headers: { location: LOGIN_PATH } });
  }
  const organizationId = String(organizations["current"] ?? "");
  if (organizationId === "") {
    return new Response(null, { status: 303, headers: { location: "/" } });
  }

  const response = await fetch(
    `${controlPlaneUrl()}/api/organizations/${encodeURIComponent(organizationId)}` +
      `/data/exports/${encodeURIComponent(jobId)}/file`,
    { headers: { cookie: `wenext_operator_session=${auth.token}` } },
  );
  if (!response.ok) {
    // Not ready, or gone with its retention. Back to the screen, which shows the state.
    return new Response(null, {
      status: 303,
      headers: { location: `/w/${encodeURIComponent(workspaceId)}/organization?info=refused` },
    });
  }
  return new Response(await response.arrayBuffer(), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "content-disposition": `attachment; filename="wenext-export-${jobId}.json"`,
      "cache-control": "no-store",
    },
  });
}

async function fetchJson(token: string, path: string): Promise<Record<string, unknown> | null> {
  const response = await fetch(`${controlPlaneUrl()}${path}`, {
    headers: { cookie: `wenext_operator_session=${token}` },
  });
  if (!response.ok) return null;
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}
