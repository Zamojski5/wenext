/**
 * Tryb lokalny przez prawdziwy stack: co się dzieje, gdy przełącznika NIE MA.
 *
 * CO TEN PLIK DOWODZI I CZEGO NIE DOWODZI, powiedziane wprost, bo to jest funkcja o wpuszczaniu
 * bez hasła i połowiczny dowód byłby tu gorszy niż żaden.
 *
 * DOWODZI: na wdrożeniu bez `WENEXT_LOCAL_AUTOLOGIN` (czyli domyślnym, takim jak ten harness)
 * studio nadal odsyła na ekran logowania, a trasa wejścia bez hasła nie otwiera niczego.
 *
 * NIE DOWODZI: zachowania z WŁĄCZONYM przełącznikiem. Ustawienie go dla całego przebiegu
 * zepsułoby każdy inny test tej suity, bo wszystkie sprawdzają, że bez sesji nie ma danych,
 * a postawienie drugiego dashboardu tylko dla jednego przypadku kosztuje więcej, niż daje.
 * Włączoną ścieżkę pokrywają: testy jednostkowe studia (nagłówek `Host`, warunki trasy),
 * `services/control_plane/tests/test_local_access.py` (kiedy wolno, a kiedy odmowa) oraz
 * `test_user_password_cli.py` (trasa naprawdę otwiera sesję i zostawia wpis w dzienniku).
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = ["WENEXT_E2E_BASE_URL", "WENEXT_E2E_WORKSPACE_ID"] as const;

const environment: Record<string, string> = {};

function required(name: (typeof REQUIRED)[number]): string {
  const value = environment[name];
  if (value === undefined) throw new Error(`brak ${name}`);
  return value;
}

function url(pathname: string): string {
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

beforeAll(() => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") missing.push(name);
    else environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(`BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie.`);
  }
});

describe("tryb lokalny wyłączony, czyli domyślny", () => {
  it("the_studio_still_asks_for_a_password", async () => {
    const response = await fetch(url(`/w/${required("WENEXT_E2E_WORKSPACE_ID")}`), {
      redirect: "manual",
    });
    expect([303, 307]).toContain(response.status);
    expect(response.headers.get("location")).toContain("/login");
  });

  it("the_no_password_door_opens_nothing_when_the_switch_is_off", async () => {
    const response = await fetch(url("/login/local"), { redirect: "manual" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/login");
    // Żadnego ciasteczka sesji: odmowa ma nie zostawiać po sobie uchwytu.
    expect(response.headers.getSetCookie().join(";")).not.toContain("wenext_dashboard_handle=");
  });

  it("the_login_screen_does_not_advertise_a_mode_that_is_off", async () => {
    const body = await (await fetch(url("/login"))).text();
    expect(body).not.toContain("tryb lokalny");
    expect(body).not.toContain('data-local-mode');
  });
});
