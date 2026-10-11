/**
 * `GET /login/local` — otwarcie sesji właściciela bez hasła, gdy włączony jest tryb lokalny.
 *
 * GET, a nie POST, świadomie: na tę trasę kieruje ekran logowania, a nie formularz, i ma
 * zadziałać także bez JavaScriptu. Ryzyko GET-a, przed którym chroni się `/logout` (prefetch,
 * podgląd linku, robot), nie występuje tam, gdzie żądanie i tak musi przyjść z pętli zwrotnej na
 * maszynie właściciela: otwarcie sesji dla samego siebie nie jest skutkiem ubocznym.
 *
 * DWA WARUNKI SPRAWDZANE TUTAJ, dwa kolejne po stronie control plane. Tutaj: przełącznik i adres,
 * po którym przyszła przeglądarka. Tam: brak skonfigurowanych drzwi z internetu i lokalne adresy
 * obu końców rozmowy. Odmowa po którejkolwiek stronie kończy się zwykłym ekranem logowania,
 * a nie błędem: człowiek ma wtedy wpisać hasło, tak jak zawsze.
 */
import { loginNext } from "../../../lib/login-next";
import { openLocalSession } from "../../../lib/control-plane";
import { hostIsLoopback, localModeWho } from "../../../lib/local-access";
import { LOGIN_PATH } from "../../../lib/routes";
import { dashboardSessionCookie, openDashboardSession } from "../../../lib/session";

export const dynamic = "force-dynamic";

function seeOther(location: string, setCookie?: string): Response {
  const headers = new Headers({ location, "cache-control": "no-store" });
  if (setCookie !== undefined) headers.append("set-cookie", setCookie);
  return new Response(null, { status: 303, headers });
}

/** Ścieżka w tym samym pochodzeniu albo nic. Ta sama reguła, co w `app/studio/_handler.ts`. */

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (localModeWho() === "" || !hostIsLoopback(request.headers.get("host"))) {
    return seeOther(LOGIN_PATH);
  }
  const opened = await openLocalSession();
  if (!opened.ok) {
    return seeOther(`${LOGIN_PATH}?blad=${opened.unauthorized ? "odmowa" : "niedostepne"}`);
  }
  const handle = openDashboardSession(opened.value);
  return seeOther(loginNext(url.searchParams.get("next")), dashboardSessionCookie(handle));
}
