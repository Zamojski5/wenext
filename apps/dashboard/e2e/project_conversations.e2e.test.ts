/**
 * Projekt jako ZBIÓR ROZMÓW, przez prawdziwy stack (D-045).
 *
 *   przeglądarka → strona projektu i trasa rozmowy → control plane → PostgreSQL
 *
 * CZEGO NIE SPRAWDZI TEST KOMPONENTU. `tests/studio_shell.test.tsx` i
 * `tests/conversation_list.test.tsx` renderują listy z podanych danych, więc przechodzą także
 * wtedy, gdy żadna strona o te rozmowy nie zapyta, gdy trasa `…/c/<id>` nie istnieje, albo gdy
 * obie rozmowy prowadzą pod ten sam adres. Tutaj rozmowy są w bazie, a jedyną drogą na ekran
 * jest control plane.
 *
 * DWIE ROZMOWY W JEDNYM PROJEKCIE SĄ CAŁYM SENSEM TEGO PLIKU. Do 20.09 strona projektu pokazywała
 * najnowszą sesję i tylko ją: starsza znikała z ekranu w chwili, gdy ktoś zaczął nową, i nie było
 * jej jak otworzyć. Z jedną rozmową ta usterka jest niewidoczna, bo „najnowsza" i „ta jedna" to
 * to samo.
 *
 * POLE NA PUSTYM PROJEKCIE JEST TU WYŁĄCZONE I TO JEST STAN MIERZONY, nie omijany: w zasianej
 * przestrzeni nie ma połączenia Codex, więc rozmowa nie miałaby na czym ruszyć, a pole mówi to
 * wprost, zamiast przyjąć zdanie, na które nic nie odpowie.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  "WENEXT_E2E_LIVE_PROJECT_ID",
  "WENEXT_E2E_LIVE_SESSION_ID",
  "WENEXT_E2E_DONE_SESSION_ID",
  "WENEXT_E2E_DONE_SESSION_TITLE",
  "WENEXT_E2E_DONE_SESSION_RESULT",
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

/**
 * Długie pauzy, zapisane KODAMI, a nie znakami.
 *
 * KONWENCJE zabraniają ich w tekstach produktu, a skan spójności szuka tych znaków w
 * repozytorium. Test, który wpisuje je dosłownie, sam wpada w to, czego pilnuje.
 */
const LONG_DASHES = ["\u2014", "\u2013"] as const;

let cookie = "";
let workspaceId = "";
let projectId = "";

async function page(pathname: string): Promise<string> {
  const response = await fetch(url(pathname), { headers: { cookie }, redirect: "manual" });
  expect(response.status, pathname).toBe(200);
  return await response.text();
}

/**
 * Sama kolumna treści. POTRZEBNA, bo pasek boczny niesie tytuły i wyniki INNYCH rozmów (w dymku
 * i w sekcji „Ostatnie"), i to jest jego zadanie. Szukanie „czy ślad jednej rozmowy wyciekł do
 * drugiej" w całym dokumencie brałoby pasek za wyciek.
 */
/**
 * `st__main"` a nie `class="st__main"`: od E2 klasa jedzie razem z klasą ramy z referencji
 * (`class="wm-main st__main"`), więc szukanie całego atrybutu nie trafia w nic. Kotwicą jest
 * nazwa klasy z domknięciem cudzysłowu, bo ona zostaje niezależnie od tego, co stanie przed nią.
 */
function main(body: string): string {
  const start = body.indexOf('st__main"');
  expect(start).toBeGreaterThan(-1);
  const end = body.indexOf("</main>", start);
  expect(end).toBeGreaterThan(start);
  return body.slice(start, end);
}

function sidebar(body: string): string {
  const start = body.indexOf('st__side"');
  expect(start).toBeGreaterThan(-1);
  const end = body.indexOf("</aside>", start);
  expect(end).toBeGreaterThan(start);
  return body.slice(start, end);
}

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
  workspaceId = required("WENEXT_E2E_WORKSPACE_ID");
  projectId = required("WENEXT_E2E_LIVE_PROJECT_ID");
});

describe("D-045: projekt jest zbiorem rozmów", () => {
  it("both_conversations_of_the_project_are_in_the_sidebar_under_it", async () => {
    const side = sidebar(await page(`/w/${workspaceId}/p/${projectId}`));
    const live = required("WENEXT_E2E_LIVE_SESSION_ID");
    const done = required("WENEXT_E2E_DONE_SESSION_ID");

    expect(side).toContain(`/w/${workspaceId}/p/${projectId}/c/${live}`);
    expect(side).toContain(`/w/${workspaceId}/p/${projectId}/c/${done}`);
    // Dwie rozmowy, dwa różne adresy. Jeden adres na obie znaczyłby, że „otwarta" jest zawsze
    // ta sama, czyli dokładnie zachowanie sprzed D-045.
    expect(live).not.toBe(done);
    expect(side).toContain("Nowa rozmowa");
  });

  it("the_empty_project_asks_what_we_are_doing_and_names_itself", async () => {
    const body = main(await page(`/w/${workspaceId}/p/${projectId}`));
    expect(body).toContain("Co robimy w");
    expect(body).toContain("Nadaj kierunek albo zapytaj o postępy");
    // Pole startu jest formularzem POST albo niczym. GET tworzyłby rozmowę przy samym podglądzie
    // linku, na przykład przez skaner w przeglądarce.
    expect(body).not.toContain('action="/studio/session" method="get"');
    if (body.includes('action="/studio/session"')) {
      expect(body).toContain('value="start"');
    }
    // CZEGO TU NIE MA I DLACZEGO: asercji „pole jest wyłączone, bo nie ma połączenia Codex".
    // Ta suita dzieli organizację z `workspace_writes_postgres.e2e.test.ts`, które połączenie
    // ZAKŁADA, więc stan pola zależy od kolejności plików, a nie od produktu. Odmowa z podaniem
    // powodu jest mierzona tam, gdzie zależy tylko od siebie: w tamtym pliku, przed założeniem
    // połączenia, i w `tests/conversation.test.tsx`.
  });

  it("the_list_on_the_project_carries_the_result_of_a_finished_conversation", async () => {
    // Po to wraca się do listy rozmów: żeby wybrać jedną, nie czytając wszystkich.
    const body = await page(`/w/${workspaceId}/p/${projectId}`);
    expect(body).toContain('data-block="rozmowy"');
    expect(body).toContain(required("WENEXT_E2E_DONE_SESSION_TITLE"));
    expect(body).toContain(required("WENEXT_E2E_DONE_SESSION_RESULT"));
  });

  it("each_conversation_opens_at_its_own_address_and_shows_its_own_trace", async () => {
    const done = required("WENEXT_E2E_DONE_SESSION_ID");
    const live = required("WENEXT_E2E_LIVE_SESSION_ID");

    const closed = await page(`/w/${workspaceId}/p/${projectId}/c/${done}`);
    expect(closed).toContain(required("WENEXT_E2E_DONE_SESSION_RESULT"));
    // Zamknięta rozmowa prowadzi do NASTĘPNEJ, a nie kończy się komunikatem bez wyjścia.
    expect(closed).toContain('value="continue"');
    expect(closed).toContain("Nowa rozmowa");

    const running = main(await page(`/w/${workspaceId}/p/${projectId}/c/${live}`));
    expect(running).toContain("czytam watek, zaraz odpowiem (e2e)");
    // Ślad jednej rozmowy nie wycieka do drugiej. Sprawdzane w KOLUMNIE TREŚCI: pasek pokazuje
    // wyniki innych rozmów z rozmysłem i tam nie jest to wyciek, tylko powód, dla którego się
    // do nich wraca.
    expect(running).not.toContain(required("WENEXT_E2E_DONE_SESSION_RESULT"));
  });

  it("the_open_conversation_is_marked_in_the_sidebar_and_the_other_is_not", async () => {
    const done = required("WENEXT_E2E_DONE_SESSION_ID");
    const side = sidebar(await page(`/w/${workspaceId}/p/${projectId}/c/${done}`));
    // Liczone po WIERSZACH ROZMÓW, nie po całym pasku: `aria-current` noszą też przestrzeń w
    // przełączniku i otwarty projekt, a jedna rozmowa stoi w dwóch listach naraz (pod projektem
    // i w „Ostatnie"), więc suma z całego paska nie jest liczbą, która cokolwiek znaczy.
    const rows = side.match(/<a[^>]*class="st-conv"[^>]*>/g) ?? [];
    expect(rows.length).toBeGreaterThan(1);
    const marked = rows.filter((row) => row.includes('aria-current="page"'));
    expect(marked.length).toBeGreaterThan(0);
    for (const row of marked) expect(row).toContain(`/c/${done}`);
    // I żaden wiersz innej rozmowy nie jest zaznaczony.
    for (const row of rows) {
      if (!row.includes(`/c/${done}`)) expect(row).not.toContain('aria-current="page"');
    }
  });

  it("no_screen_of_this_flow_shows_an_identifier_or_a_long_dash", async () => {
    // Identyfikator na ekranie znaczy, że komuś zabrakło nazwy; długa pauza jest zakazana w
    // tekstach produktu (KONWENCJE). Sprawdzane na TREŚCI, nie na całym HTML-u, bo w atrybutach
    // `href` i `data-` identyfikatory są konieczne.
    const done = required("WENEXT_E2E_DONE_SESSION_ID");
    for (const path of [
      `/w/${workspaceId}/p/${projectId}`,
      `/w/${workspaceId}/p/${projectId}/c/${done}`,
    ]) {
      const body = await page(path);
      // SKRYPTY WYCINANE PIERWSZE. Next.js wstawia w `<script>` swój payload komponentów
      // serwerowych i są w nim identyfikatory wszystkiego, co strona wyrenderowała. Samo zdjęcie
      // znaczników zostawiłoby tamten tekst i test twierdziłby, że uuid jest „na ekranie",
      // choć nikt go nie widzi.
      const visible = body
        .replace(/<script[\s\S]*?<\/script>/g, " ")
        .replace(/<[^>]*>/g, " ");
      expect(visible, path).not.toContain(done);
      for (const dash of LONG_DASHES) expect(visible, `${path} ${dash}`).not.toContain(dash);
    }
  });
});
