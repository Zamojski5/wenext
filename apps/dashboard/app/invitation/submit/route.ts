/**
 * `POST /invitation/submit` — spends the invitation token and opens the session.
 *
 * Same shape as the other two credential handlers: no client JavaScript, 303, and the control
 * plane's token kept server-side. The invitation token is read from the form and forwarded once;
 * this package stores it nowhere.
 */
import { acceptInvitation } from "../../../lib/control-plane";
import { AFTER_LOGIN_PATH } from "../../../lib/routes";
import { dashboardSessionCookie, openDashboardSession } from "../../../lib/session";

export const dynamic = "force-dynamic";

const INVITATION_PATH = "/invitation";

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
    return seeOther(`${INVITATION_PATH}?blad=odmowa`);
  }
  const token = String(form.get("token") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (token === "" || password === "") {
    return seeOther(`${INVITATION_PATH}?blad=odmowa`);
  }

  const opened = await acceptInvitation(token, password);
  if (!opened.ok) {
    // The token travels back in the redirect so the person can try a different password without
    // digging the link out of their mail again.
    const back = `${INVITATION_PATH}?token=${encodeURIComponent(token)}`;
    return seeOther(
      opened.unauthorized
        ? `${back}&blad=odmowa`
        : `${back}&blad=odmowa&powod=${encodeURIComponent(opened.reason)}`,
    );
  }

  const handle = openDashboardSession(opened.value);
  return seeOther(AFTER_LOGIN_PATH, dashboardSessionCookie(handle));
}
