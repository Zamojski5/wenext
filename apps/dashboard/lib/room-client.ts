/**
 * The read behind the "Pokój" screen, from the dashboard's server.
 *
 * One route, one read: `GET /api/workspaces/{ws}/projects/{project}/room`. The transport is the
 * one `lib/presence-client.ts` uses and for the same reason: `call` and `decode` in
 * `lib/control-plane.ts` are private, and the address, the timeout and the cookie name are
 * imported from there so there is one answer to "where is the control plane" (D-016: the session
 * and the private address never reach browser code).
 *
 * A 404 IS NOT "THERE ARE NO OPPORTUNITIES". The project itself was already read by the page
 * before this is called, so a 404 here means the SERVER has no such route, which is what an older
 * control plane answers. The screen then says the room is not available on this server, and does
 * not draw the empty state: an empty room would promise "nothing is happening" about a read that
 * never happened.
 */
import {
  CONTROL_PLANE_SESSION_COOKIE,
  REQUEST_TIMEOUT_MS,
  controlPlaneUrl,
  type Read,
} from "./control-plane";
import { parseRoom, type Room } from "./room-schema";

export function roomPath(workspaceId: string, projectId: string): string {
  return (
    `/api/workspaces/${encodeURIComponent(workspaceId)}` +
    `/projects/${encodeURIComponent(projectId)}/room`
  );
}

export async function getRoom(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<Room>> {
  let base: string;
  try {
    base = controlPlaneUrl();
  } catch (error) {
    return { ok: false, unauthorized: false, reason: String(error) };
  }
  let status: number;
  let text: string;
  try {
    const response = await fetch(`${base}${roomPath(workspaceId, projectId)}`, {
      method: "GET",
      headers: {
        accept: "application/json",
        cookie: `${CONTROL_PLANE_SESSION_COOKIE}=${token}`,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    status = response.status;
    text = await response.text();
  } catch (error) {
    return {
      ok: false,
      unauthorized: false,
      reason: `control plane nie odpowiedział (${String(error)})`,
    };
  }
  if (status === 401) return { ok: false, unauthorized: true, reason: "sesja panelu wygasła" };
  if (status === 404) {
    return {
      ok: false,
      unauthorized: false,
      reason: "ten serwer nie ma jeszcze Pokoju (trasa /room odpowiedziała 404)",
    };
  }
  if (status !== 200) {
    return {
      ok: false,
      unauthorized: false,
      reason: `pokój: control plane odpowiedział ${status}`,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { ok: false, unauthorized: false, reason: `pokój: ciało nie jest JSON-em (${error})` };
  }
  return parseRoom(parsed);
}
