/**
 * Shared shape of the four form handlers under `app/community/`: resolve the operator from the
 * dashboard cookie, run ONE control plane write server-side, and send the browser back to
 * `/community` with a sentence chosen from a closed set (`?info=`). 303 See Other, like the login
 * handler, so the browser follows with GET and never repeats the POST.
 *
 * NOT a page, so `layout_routes.test.tsx` does not expect a `page.tsx` here; the four `route.ts`
 * files that import this are listed in `tests/source_scan.test.ts::ALLOWED_ROUTE_HANDLERS`.
 */
import { cookies } from "next/headers";

import { getSession, type Read, type WriteOutcome } from "../../../lib/control-plane";
import { LOGIN_PATH } from "../../../lib/routes";
import { DASHBOARD_COOKIE_NAME, controlPlaneTokenFor } from "../../../lib/session";

export const COMMUNITY_PATH = "/community";

export function seeOther(location: string): Response {
  return new Response(null, { status: 303, headers: { location, "cache-control": "no-store" } });
}

export function backWith(info: string): Response {
  return seeOther(`${COMMUNITY_PATH}?info=${encodeURIComponent(info)}`);
}

/** The control plane token for this request, or a redirect to the login screen. */
export async function tokenOrLogin(): Promise<{ readonly token: string } | { readonly redirect: Response }> {
  const jar = await cookies();
  const token = controlPlaneTokenFor(jar.get(DASHBOARD_COOKIE_NAME)?.value);
  if (token === undefined) {
    return { redirect: seeOther(LOGIN_PATH) };
  }
  const session = await getSession(token);
  if (!session.ok) {
    return { redirect: session.unauthorized ? seeOther(LOGIN_PATH) : backWith("unreachable") };
  }
  return { token };
}

/** Read the form, refusing anything that is not a form. */
export async function formOf(request: Request): Promise<FormData | null> {
  try {
    return await request.formData();
  } catch {
    return null;
  }
}

export function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

/** Map a write's outcome onto the two sentences a handler can say about it. */
export function verdictOf(outcome: Read<WriteOutcome>, ok: string, refused: string): Response {
  if (!outcome.ok) {
    // A 401 mid-write means the control plane forgot the session: ask for the password again.
    // Anything else means the write did not happen, and the screen must say exactly that.
    return outcome.unauthorized ? seeOther(LOGIN_PATH) : backWith("unreachable");
  }
  return backWith(outcome.value.status >= 200 && outcome.value.status < 300 ? ok : refused);
}
