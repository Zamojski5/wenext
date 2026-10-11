/**
 * `POST /register/submit` — creates the organization and opens the session.
 *
 * The same shape as `/login/submit` and for the same reasons: a route handler rather than a
 * Server Action so the screen needs no client JavaScript, 303 so the browser follows with GET,
 * and the control plane's session token stays HERE while the browser gets an unrelated handle
 * (D-016, `lib/session.ts`).
 *
 * The refusal sentence from the control plane is passed through in `?powod=`. It is the one the
 * backend wrote precisely so that it says nothing about whether an account exists; rewriting it
 * here would undo that.
 */
import { registerOrganization } from "../../../lib/control-plane";
import { AFTER_LOGIN_PATH } from "../../../lib/routes";
import { dashboardSessionCookie, openDashboardSession } from "../../../lib/session";

export const dynamic = "force-dynamic";

const REGISTER_PATH = "/register";

function seeOther(location: string, setCookie?: string): Response {
  const headers = new Headers({ location, "cache-control": "no-store" });
  if (setCookie !== undefined) headers.append("set-cookie", setCookie);
  return new Response(null, { status: 303, headers });
}

export async function POST(request: Request): Promise<Response> {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return seeOther(`${REGISTER_PATH}?blad=odmowa`);
  }
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const name = String(form.get("organization_name") ?? "").trim();
  if (email === "" || password === "" || name === "") {
    return seeOther(`${REGISTER_PATH}?blad=odmowa`);
  }

  const opened = await registerOrganization(email, password, name);
  if (!opened.ok) {
    if (opened.reason.includes("nie przyjmuje nowych przestrzeni")) {
      return seeOther(`${REGISTER_PATH}?blad=zamkniete`);
    }
    // `unauthorized` means the body did not pass the door at all; anything else is a sentence
    // the person should read, so it travels with the redirect.
    return seeOther(
      opened.unauthorized
        ? `${REGISTER_PATH}?blad=odmowa`
        : `${REGISTER_PATH}?blad=odmowa&powod=${encodeURIComponent(opened.reason)}`,
    );
  }

  const handle = openDashboardSession(opened.value);
  return seeOther(AFTER_LOGIN_PATH, dashboardSessionCookie(handle));
}
