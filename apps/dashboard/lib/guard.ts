/**
 * The auth boundary as the dashboard sees it: no session, no screen.
 *
 * D-016 puts the boundary in the control plane, and that is where it is enforced — every route
 * there answers 401 without a session. This function is the SECOND half of the same rule, on this
 * side of the wire, and it exists for a reason the control plane cannot cover: a Next.js page that
 * called the API without a session would get a 401, turn it into a failed read, and render "brak
 * odpowiedzi" — a screen that looks like an outage to an operator who is simply logged out. So a
 * request with no usable session is REDIRECTED to the login screen and renders no data at all.
 *
 * THE THREE OUTCOMES ARE KEPT APART, and that is the whole design:
 *
 *   | situation                                   | outcome                                   |
 *   | ------------------------------------------- | ----------------------------------------- |
 *   | no cookie, unknown handle, expired handle   | redirect to `/login`                      |
 *   | control plane answers 401 for the token     | handle dropped, redirect to `/login`      |
 *   | control plane unreachable / broken response | `unreachable` — the screen says so        |
 *
 * The last row is the one that is easy to get wrong in the comfortable direction. Redirecting to
 * the login screen when the API is DOWN would tell the operator "your session ended" when nothing
 * of the sort was measured — a diagnosis invented to avoid showing an error, which is the same
 * move as a gate reporting NO_GO where the honest answer is NIE_URUCHOMIONO (docs/KONWENCJE.md).
 *
 * WHY THERE IS NO MIDDLEWARE HERE. Next.js middleware runs in a separate, edge-style runtime and
 * cannot see the in-process map in `lib/session.ts`; it could check that a cookie is PRESENT and
 * nothing more, which is authentication by the existence of a string. Every gated page therefore
 * calls this function as its first statement, and `tests/gated_pages.test.ts` walks `app/` and
 * fails if a page file does not — a page added later without the call is the exact hole a
 * hand-written list of protected routes always eventually has.
 */
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { getSession } from "./control-plane";
import { loginNext } from "./login-next";
import { LOGIN_PATH } from "./routes";
import { DASHBOARD_COOKIE_NAME, closeDashboardSession, controlPlaneTokenFor } from "./session";
import type { SessionDescription } from "./schema";

export type Gate =
  | {
      readonly kind: "operator";
      /** The control plane session token. Stays on this side — see `lib/session.ts`. */
      readonly token: string;
      readonly operator: SessionDescription;
    }
  | { readonly kind: "unreachable"; readonly reason: string };

/**
 * Resolve the operator behind this request, or redirect to the login screen.
 *
 * Costs one `GET /api/session` per page view, on loopback. That call is not decoration: without
 * it a page whose own read fails cannot tell "logged out" from "API down", and those two need
 * different screens.
 */
export async function requireOperator(returnTo = "/"): Promise<Gate> {
  const login = returnTo === "/" ? LOGIN_PATH : `${LOGIN_PATH}?next=${encodeURIComponent(loginNext(returnTo))}`;
  const jar = await cookies();
  const handle = jar.get(DASHBOARD_COOKIE_NAME)?.value;
  const token = controlPlaneTokenFor(handle);
  if (token === undefined) {
    redirect(login);
  }

  const session = await getSession(token);
  if (!session.ok) {
    if (session.unauthorized) {
      // The control plane has forgotten this token (its store is in memory too, so its own
      // restart does this). Drop the handle so the next request does not pay for the round trip
      // again, then ask for the password.
      closeDashboardSession(handle);
      redirect(login);
    }
    return { kind: "unreachable", reason: session.reason };
  }

  return { kind: "operator", token, operator: session.value };
}
