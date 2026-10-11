/**
 * `GET /studio/stream?workspace_id=…&project_id=…&session_id=…&after_seq=…` — the live trace,
 * proxied.
 *
 * WHY A PROXY AND NOT A DIRECT CONNECTION. D-016: the browser never talks to the control plane,
 * and the control plane's session cookie never reaches browser code. The page opens an
 * `EventSource` against THIS origin; this handler holds the dashboard session, attaches the
 * control plane cookie server-side and passes the bytes through unchanged.
 *
 * THE BODY IS STREAMED, NOT BUFFERED. `response.body` is handed straight to the `Response`
 * constructor, so an event written by the worker reaches the browser as soon as the control
 * plane writes it. `await response.text()` here would collect the whole stream first and deliver
 * the conversation in one lump when the turn ended — which is exactly the behaviour this feature
 * exists to remove, and it would look like it worked.
 *
 * A GET among POST handlers, like the export download: this is a read.
 */
import { controlPlaneUrl } from "../../../lib/control-plane";
import { CONTROL_PLANE_SESSION_COOKIE } from "../../../lib/control-plane";
import { tokenOrLogin } from "../_handler";

export const dynamic = "force-dynamic";

function bad(status: number): Response {
  return new Response(null, { status, headers: { "cache-control": "no-store" } });
}

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const workspaceId = (url.searchParams.get("workspace_id") ?? "").trim();
  const projectId = (url.searchParams.get("project_id") ?? "").trim();
  const sessionId = (url.searchParams.get("session_id") ?? "").trim();
  const afterSeq = (url.searchParams.get("after_seq") ?? "0").trim();
  if (workspaceId === "" || projectId === "" || sessionId === "") {
    return bad(400);
  }

  const auth = await tokenOrLogin("/");
  if ("redirect" in auth) {
    // 401 rather than a redirect: an `EventSource` cannot follow a login page, and a redirect
    // here would show up in the browser as a stream that opens and immediately fails with no
    // reason anybody can read.
    return bad(401);
  }

  // `Last-Event-ID` is how the browser says where its own reconnect left off. Forwarded rather
  // than translated: the control plane prefers it over the query parameter, on purpose.
  const lastEventId = request.headers.get("last-event-id");
  const headers: Record<string, string> = {
    cookie: `${CONTROL_PLANE_SESSION_COOKIE}=${auth.token}`,
    accept: "text/event-stream",
  };
  if (lastEventId !== null) headers["last-event-id"] = lastEventId;

  const target =
    `${controlPlaneUrl()}/api/workspaces/${encodeURIComponent(workspaceId)}` +
    `/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}` +
    `/stream?after_seq=${encodeURIComponent(afterSeq)}`;

  let upstream: Response;
  try {
    upstream = await fetch(target, { headers, signal: request.signal });
  } catch {
    // The control plane did not answer. 503 and not an empty stream: a stream that opens and
    // says nothing is indistinguishable from a session in which nothing is happening.
    return bad(503);
  }
  if (!upstream.ok || upstream.body === null) {
    return bad(upstream.status === 404 ? 404 : 502);
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store",
      "x-accel-buffering": "no",
      connection: "keep-alive",
    },
  });
}
