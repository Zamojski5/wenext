/**
 * The parts of the visual contract that a served page can prove without a browser.
 *
 * THE RENDER REVIEW (apps/dashboard/tools/render_review.py) drives a real Chromium and measures
 * what a browser paints: contrast from pixels, horizontal scrolling at 390 px, the panel
 * contract, focus. That run is a session somebody starts; this file is the part that has to hold
 * on EVERY e2e run, so it checks what plain HTML can answer:
 *
 *   * no long dash (U+2014, U+2013) in the rendered HTML of any screen. The founder's rule since
 *     2026-09-04 is about what a person reads, so scanning the source is not enough: a dash can
 *     arrive from the database, and one did — the e2e seed itself had "Hacker News — Ask HN",
 *     found by the review on 2026-09-19;
 *   * every studio screen carries the studio's own chrome (`data-st-route`), so a page added
 *     later cannot quietly render outside the shell;
 *   * the phone width has a viewport declaration, without which 390 px means nothing.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};

function required(name: Required): string {
  const value = environment[name];
  if (value === undefined) throw new Error(`brak ${name}`);
  return value;
}

function url(pathname: string): string {
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

async function logIn(): Promise<string> {
  const response = await fetch(url("/login/submit"), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      password: required("WENEXT_E2E_OPERATOR_PASSWORD"),
    }).toString(),
    redirect: "manual",
  });
  const header = response.headers
    .getSetCookie()
    .find((value) => value.startsWith("wenext_dashboard_handle="));
  if (header === undefined) throw new Error("logowanie nie odesłało ciasteczka");
  return header.split(";")[0] ?? "";
}

async function page(pathname: string, cookie?: string): Promise<string> {
  const response = await fetch(url(pathname), {
    headers: cookie === undefined ? {} : { cookie },
    redirect: "follow",
  });
  return await response.text();
}

const LONG_DASHES: readonly [string, string][] = [
  ["—", "U+2014 (em)"],
  ["–", "U+2013 (en)"],
];

let cookie = "";
let workspace = "";
let screens: string[] = [];

beforeAll(async () => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") missing.push(name);
    else environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(
      `BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie.`,
    );
  }
  cookie = await logIn();
  workspace = required("WENEXT_E2E_WORKSPACE_ID");
  screens = [
    "/login",
    "/register",
    "/invitation?token=podglad",
    `/w/${workspace}`,
    `/w/${workspace}/people`,
    `/w/${workspace}/codex`,
    `/w/${workspace}/organization`,
  ];
  // The project screens are found, not configured: the workspace page links to whatever project
  // the seed made, and a project added later is covered without anybody remembering to add an
  // environment variable here. When there is no project, the list simply stays at seven.
  //
  // Lista pozycji projektu jest tu WPISANA, a nie brana z paska, i to jest świadome: gdyby
  // pochodziła z `lib/routes.ts`, ekran wypadnięty z tamtej listy wypadłby też stąd i przestałby
  // być sprawdzany dokładnie w chwili, w której ktoś go zepsuł. Nowy ekran dopisuje się tutaj
  // ręcznie, a `layout_routes` pilnuje, że żaden nie powstał bez deklaracji.
  const overview = await page(`/w/${workspace}`, cookie);
  const link = new RegExp(`/w/${workspace}/p/([0-9a-f-]{36})`).exec(overview);
  if (link !== null) {
    const project = link[1]!;
    screens.push(
      `/w/${workspace}/p/${project}`,
      `/w/${workspace}/p/${project}/publications`,
      `/w/${workspace}/p/${project}/communities`,
      `/w/${workspace}/p/${project}/presence`,
      `/w/${workspace}/p/${project}/knowledge`,
      // D-055: „Pokój". Na serwerze bez trasy `/room` ekran rysuje odczyt nieudany, i on też ma
      // nie zawierać długiej pauzy.
      `/w/${workspace}/p/${project}/room`,
    );
  }
});

describe("kontrakt wizualny studia", () => {
  it("no_screen_renders_a_long_dash", async () => {
    // Counted first: an empty list would make the loop below prove nothing.
    expect(screens.length).toBeGreaterThanOrEqual(7);
    const offenders: string[] = [];
    for (const pathname of screens) {
      const html = await page(pathname, cookie);
      for (const [dash, name] of LONG_DASHES) {
        const at = html.indexOf(dash);
        if (at >= 0) {
          offenders.push(`${pathname}: ${name} w „…${html.slice(Math.max(0, at - 50), at + 50)}…”`);
        }
      }
    }
    expect(offenders, "długie pauzy w renderze").toEqual([]);
  });

  it("every_studio_screen_renders_inside_the_studio_chrome", async () => {
    for (const pathname of screens.slice(3)) {
      // Login, register and invitation are outside the shell on purpose: no workspace, no nav.
      const html = await page(pathname, cookie);
      expect(html, pathname).toContain("data-st-route=");
    }
  });

  it("the_phone_width_is_declared_or_390px_means_nothing", async () => {
    const html = await page(`/w/${workspace}`, cookie);
    expect(html).toContain('name="viewport"');
    expect(html).toContain("width=device-width");
  });
});
