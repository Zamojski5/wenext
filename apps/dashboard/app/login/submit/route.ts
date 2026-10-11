/**
 * `POST /login/submit` — the credential route of the dashboard.
 *
 * It is a route handler and not a Server Action so that the login screen needs no client
 * JavaScript at all (see `app/login/page.tsx`), and it lives one segment below `/login` because
 * Next.js will not host a page and a route handler on the same path.
 *
 * THE THING THIS FILE EXISTS TO DO, in one line: take the control plane's session token, KEEP IT
 * HERE, and give the browser an unrelated handle. `lib/session.ts` holds the map; this handler is
 * the only place a token enters it. Forwarding the control plane's own `Set-Cookie` would have
 * been one line shorter and would have put an operator session token in the browser's cookie jar —
 * the sentence D-016 and this package's brief both forbid.
 *
 * 303 See Other, not 302: the browser must follow with GET. A 302 after a POST is allowed to
 * repeat the POST, and repeating a login POST means presenting the password again on a redirect
 * nobody asked for.
 *
 * NO RATE LIMITING HERE, AND THAT IS A GAP, NOT A DESIGN. `api/session.py` says the same about the
 * control plane's side: "Rate limiting the credential route is NOT part of this package and is not
 * solved elsewhere either — it is reported as a gap rather than implied." The same is true here,
 * and this comment is the report. The mitigating facts are that the port is loopback-only (D-014)
 * and that Argon2id verification costs the attacker real time — neither of which is a rate limit.
 */
import { loginNext } from "../../../lib/login-next";
import { openControlPlaneSession, openUserSession } from "../../../lib/control-plane";
import { LOGIN_PATH } from "../../../lib/routes";
import { dashboardSessionCookie, openDashboardSession } from "../../../lib/session";

export const dynamic = "force-dynamic";

function seeOther(location: string, setCookie?: string): Response {
  const headers = new Headers({ location, "cache-control": "no-store" });
  if (setCookie !== undefined) {
    headers.append("set-cookie", setCookie);
  }
  return new Response(null, { status: 303, headers });
}

export async function POST(request: Request): Promise<Response> {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    // A body that is not a form is a refusal, not a 500 with a description of the expected shape.
    return seeOther(`${LOGIN_PATH}?blad=odmowa`);
  }
  const password = form.get("password");
  const email = form.get("email");

  if (typeof password !== "string" || password === "") {
    return seeOther(`${LOGIN_PATH}?blad=odmowa`);
  }

  // P7: two doors behind one handler. WHICH ONE is decided by the presence of an address and
  // not by a hidden field the browser sends, because a hidden field is something anybody can
  // change: with an email the user login is used, without one the founder's operator password.
  // Both refusals come back as the same `?blad=odmowa`, since the control plane refuses both
  // the same way and for the same reason.
  const opened =
    typeof email === "string" && email.trim() !== ""
      ? await openUserSession(email.trim(), password)
      : await openControlPlaneSession(password);
  if (!opened.ok) {
    // `unauthorized` is the control plane's 401 — a refusal of the credential. Anything else means
    // the exchange did not happen, and saying "wrong password" then would be inventing a
    // diagnosis (`app/login/page.tsx` explains why the two messages differ).
    return seeOther(`${LOGIN_PATH}?blad=${opened.unauthorized ? "odmowa" : "niedostepne"}`);
  }

  const handle = openDashboardSession(opened.value);
  return seeOther(loginNext(form.get("next")), dashboardSessionCookie(handle));
}
