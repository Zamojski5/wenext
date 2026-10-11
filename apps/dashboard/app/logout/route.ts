/**
 * `POST /logout` — end the session on BOTH sides, in that order.
 *
 * A logout that only asks the browser to drop the cookie leaves the control plane session valid
 * for anybody holding a copy of the token — and the copy is the entire reason to log out
 * (`api/session.py` says exactly this about its own DELETE). So: the local handle is dropped
 * first, which yields the token, and the token is then spent on `DELETE /api/session`.
 *
 * THE UPSTREAM CALL IS BEST EFFORT AND THE LOCAL DROP IS NOT. If the control plane cannot be
 * reached, the handle is already gone, so this browser cannot use the session any more; the
 * upstream session then expires on its own TTL (8 h, `auth.SESSION_TTL`). The reverse order would
 * be worse: a failed upstream call would leave the handle in place and the operator logged in
 * against their will.
 *
 * POST, never GET. A logout reachable by GET is a logout any prefetch, link preview or crawler can
 * perform on the operator's behalf — which is also why this package uses plain `<a>` rather than
 * `next/link` and its prefetching.
 */
import { cookies } from "next/headers";

import { closeControlPlaneSession } from "../../lib/control-plane";
import { LOGIN_PATH } from "../../lib/routes";
import {
  DASHBOARD_COOKIE_NAME,
  clearedDashboardSessionCookie,
  closeDashboardSession,
} from "../../lib/session";

export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  const jar = await cookies();
  const handle = jar.get(DASHBOARD_COOKIE_NAME)?.value;
  const token = closeDashboardSession(handle);

  if (token !== undefined) {
    // Best effort, and its failure is deliberately not reported to the browser: the session is
    // already unusable from here, and an error page on logout is how people stay logged in.
    await closeControlPlaneSession(token);
  }

  // `?wylogowano=1` zatrzymuje tryb lokalny na jedno wejście. Bez tego ekran logowania
  // natychmiast otworzyłby sesję z powrotem i przycisk „wyloguj" nie robiłby nic widocznego.
  const headers = new Headers({
    location: `${LOGIN_PATH}?wylogowano=1`,
    "cache-control": "no-store",
  });
  headers.append("set-cookie", clearedDashboardSessionCookie());
  return new Response(null, { status: 303, headers });
}
