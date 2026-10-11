/**
 * The live conversation, through the dashboard's own origin (P2 etap B).
 *
 *   browser → /studio/stream (this server) → control plane SSE → PostgreSQL
 *
 * WHAT THIS PROVES that the component test cannot: the proxy really streams. A handler that read
 * the whole upstream body and returned it would pass every unit test and deliver the whole
 * conversation in one lump when the turn ended — which is the behaviour the feature exists to
 * remove, and it would look like it worked.
 *
 * THE DEADLINE CASE COMES FROM AN INDEPENDENT AUDIT (19.09.2026). One connection lives a couple
 * of minutes and then ends; the question is what it SAYS when it ends. It used to say `koniec`,
 * which the browser script reads as "the work is over", so a session still RUNNING went quiet on
 * the screen. Here the deadline is shortened by the harness to a couple of seconds, and the test
 * reads what a browser would read: `wznow`, no `koniec`, and a reconnect that keeps delivering.
 *
 * WHAT IT DELIBERATELY DOES NOT DO: run a browser. There is no headless browser in this harness
 * and adding one for this would be a dependency bigger than the feature. The script that
 * consumes these frames is covered by `tests/conversation_live.test.tsx`; here the frames
 * themselves are read with `fetch`, exactly as an `EventSource` reads them.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  // Sesja, która NAPRAWDĘ jest RUNNING, i limit jednego połączenia skrócony przez harness.
  // Zasiane, bo w tej atrapie nie ma Codexa: sesja założona przez ekran kończy się porażką.
  "WENEXT_E2E_LIVE_PROJECT_ID",
  "WENEXT_E2E_LIVE_SESSION_ID",
  "WENEXT_E2E_STREAM_SECONDS",
] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};
const STAMP = Math.random().toString(36).slice(2, 8);

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

async function post(
  pathname: string,
  fields: Record<string, string>,
  cookie: string,
): Promise<{ status: number; location: string | null }> {
  const response = await fetch(url(pathname), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", cookie },
    body: new URLSearchParams(fields).toString(),
    redirect: "manual",
  });
  return { status: response.status, location: response.headers.get("location") };
}

async function get(pathname: string, cookie: string): Promise<{ status: number; body: string }> {
  const response = await fetch(url(pathname), { headers: { cookie }, redirect: "manual" });
  return { status: response.status, body: await response.text() };
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

let cookie = "";
let workspaceId = "";
let projectId = "";
let sessionId = "";

beforeAll(async () => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") missing.push(name);
    else environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(
      `BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie. Uruchom: ` +
        "REPO_ROOT=$PWD bash apps/dashboard/tools/e2e_db_render.sh",
    );
  }
  cookie = await logIn();
  workspaceId = required("WENEXT_E2E_WORKSPACE_ID");

  const created = await post(
    "/studio/project",
    {
      action: "create",
      workspace_id: workspaceId,
      name: `Strumien ${STAMP}`,
      goal: "sprawdź strumień",
      mode: "RESEARCH",
      return_to: `/w/${workspaceId}`,
    },
    cookie,
  );
  expect([302, 303]).toContain(created.status);
  const workspace = await get(`/w/${workspaceId}`, cookie);
  const match = workspace.body.match(
    new RegExp(`href="/w/${UUID}/p/(${UUID})"[^>]*>(?:(?!</a>).)*?Strumien ${STAMP}`, "s"),
  );
  if (match === null) throw new Error("nie znaleziono projektu utworzonego przez ten test");
  projectId = match[1] as string;
});

describe("P2 etap B: rozmowa dociera na żywo", () => {
  it("entrusting a goal creates a session whose trace the page already shows", async () => {
    const started = await post(
      "/studio/session",
      {
        action: "start",
        workspace_id: workspaceId,
        project_id: projectId,
        goal: `Zadanie strumieniowe ${STAMP}`,
        return_to: `/w/${workspaceId}/p/${projectId}`,
      },
      cookie,
    );
    expect([302, 303], `powierzenie celu zwróciło ${started.status}`).toContain(started.status);

    // D-045: PO POWIERZENIU CELU PRZEGLĄDARKA IDZIE DO NOWEJ ROZMOWY, a nie z powrotem na
    // projekt. Identyfikator bierzemy stąd, a nie ze strony projektu, i to jest mocniejsze niż
    // przedtem: przekierowanie pod adres rozmowy dowodzi, że rozmowa naprawdę powstała i że
    // control plane odesłał jej identyfikator.
    // `?info=session_started` dokleja `backTo`, więc wzorzec kończy się na identyfikatorze, a nie
    // na końcu napisu.
    const landed = new RegExp(`^/w/${UUID}/p/${UUID}/c/(${UUID})(\\?|$)`).exec(
      started.location ?? "",
    );
    expect(landed, `start odesłał na ${started.location}`).not.toBeNull();
    sessionId = (landed as RegExpExecArray)[1] as string;

    const screen = await get(started.location as string, cookie);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain(`Zadanie strumieniowe ${STAMP}`);
    // The live block is on the page, pointed at this origin, and starts from what the server
    // already rendered.
    expect(screen.body).toContain('data-live-root="1"');
    expect(screen.body).toContain("/studio/stream?workspace_id=");
    expect(screen.body).toContain(`session_id=${sessionId}`);
  });

  it("the proxy streams server-sent events and ends the stream itself", async () => {
    const response = await fetch(
      url(
        `/studio/stream?workspace_id=${workspaceId}&project_id=${projectId}` +
          `&session_id=${sessionId}&after_seq=0`,
      ),
      { headers: { cookie } },
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    // No buffering anywhere between here and the control plane.
    expect(response.headers.get("cache-control")).toContain("no-store");

    const text = await response.text();
    expect(text).toContain("event: status");
    expect(text).toContain(`Zadanie strumieniowe ${STAMP}`);

    // Strumień ZAMYKA SIĘ SAM, zamiast wisieć, i mówi, którym z dwóch powodów. To jest miejsce,
    // w którym ta suita nauczyła się rozróżnienia z audytu 19.09: wcześniej stało tu twarde
    // `event: koniec`, bo „koniec" był jedynym zakończeniem, jakie strumień znał. Odkąd limit
    // czasu prosi o wznowienie, sesja jeszcze w kolejce kończy TO POŁĄCZENIE słowem `wznow`,
    // a `koniec` należy się dopiero sesji, która wyszła ze stanu pracującego. Sprawdzamy więc
    // ostatnią ramkę PRZECIWKO ostatniemu stanowi, a nie jedną z nich w oderwaniu.
    // STAN CZYTAMY WYŁĄCZNIE Z RAMEK `status`, nie z całego tekstu. Ślad sesji zawiera zdarzenia,
    // których payload ma własne pole `status` (np. STATUS z powodem zatrzymania), i szukanie
    // `"status": "..."` w całym strumieniu bierze je za stan sesji. Pierwsza wersja tego testu
    // dokładnie tak się pomyliła i kazała szukać `koniec` na sesji, która była w kolejce.
    const frames = text.split("\n\n").filter((frame) => frame.trim() !== "");
    const statusFrames = frames.filter((frame) => frame.includes("event: status"));
    expect(statusFrames.length).toBeGreaterThan(0);
    const last = statusFrames[statusFrames.length - 1] as string;
    const live = last.includes('"status": "RUNNING"') || last.includes('"status": "QUEUED"');
    expect(text).toContain(live ? "event: wznow" : "event: koniec");
    expect(text).not.toContain(live ? "event: koniec" : "event: wznow");
  });

  it("what the stream showed is what a reload shows", async () => {
    const response = await fetch(
      url(
        `/studio/stream?workspace_id=${workspaceId}&project_id=${projectId}` +
          `&session_id=${sessionId}`,
      ),
      { headers: { cookie } },
    );
    const streamed = await response.text();
    const ids = Array.from(streamed.matchAll(/^id: (\d+)$/gm)).map((match) => Number(match[1]));
    expect(ids.length).toBeGreaterThan(0);

    // Przeładowanie ROZMOWY, nie projektu: po D-045 ślad mieszka pod własnym adresem, a strona
    // projektu jest listą rozmów i pytaniem „co robimy".
    const reloaded = await get(
      `/w/${workspaceId}/p/${projectId}/c/${sessionId}`,
      cookie,
    );
    expect(reloaded.status).toBe(200);
    // The history contains the goal the stream delivered, and the live list is still empty on
    // the server: the stream adds nothing to the page that a reload would not show.
    expect(reloaded.body).toContain(`Zadanie strumieniowe ${STAMP}`);
    expect(reloaded.body).toContain('data-live-count="0"');
  });

  it("a_running_session_asks_for_a_reconnect_instead_of_announcing_the_end", async () => {
    const liveProject = required("WENEXT_E2E_LIVE_PROJECT_ID");
    const liveSession = required("WENEXT_E2E_LIVE_SESSION_ID");
    const limit = Number(required("WENEXT_E2E_STREAM_SECONDS"));
    expect(limit).toBeGreaterThan(0);

    const opened = Date.now();
    const response = await fetch(
      url(
        `/studio/stream?workspace_id=${workspaceId}&project_id=${liveProject}` +
          `&session_id=${liveSession}&after_seq=0`,
      ),
      { headers: { cookie } },
    );
    expect(response.status).toBe(200);
    const text = await response.text();
    const elapsed = (Date.now() - opened) / 1000;

    // Połączenie ŻYŁO, a nie zamknęło się od razu: inaczej „wznow" niczego by nie dowodziło.
    expect(elapsed).toBeGreaterThanOrEqual(limit * 0.5);
    expect(text).toContain("event: wznow");
    expect(text).not.toContain("event: koniec");
    expect(text).toContain("czytam watek, zaraz odpowie");
    // Ostatnie, co ekran usłyszał o stanie, to RUNNING. To jest ta różnica, której skrypt nie
    // mógł wcześniej zobaczyć, i przez którą przestawał odświeżać pracującą turę.
    const statuses = Array.from(text.matchAll(/^data: (\{"status".*)$/gm)).map((m) => m[1] ?? "");
    expect(statuses.length).toBeGreaterThan(0);
    expect(statuses[statuses.length - 1]).toContain("RUNNING");
  });

  it("after_the_reconnect_the_page_keeps_getting_new_events", async () => {
    const liveProject = required("WENEXT_E2E_LIVE_PROJECT_ID");
    const liveSession = required("WENEXT_E2E_LIVE_SESSION_ID");
    const streamUrl = url(
      `/studio/stream?workspace_id=${workspaceId}&project_id=${liveProject}` +
        `&session_id=${liveSession}`,
    );

    const first = await (await fetch(`${streamUrl}&after_seq=0`, { headers: { cookie } })).text();
    const identifiers = Array.from(first.matchAll(/^id: (\d+)$/gm)).map((match) => Number(match[1]));
    expect(identifiers.length).toBeGreaterThan(0);
    const seen = Math.max(...identifiers);

    // Człowiek dopisuje zdanie do trwającej rozmowy PODCZAS drugiego połączenia. To jest ten
    // dowód, o który chodzi: po wznowieniu ekran nadal dostaje nowe zdarzenia.
    const said = `Dopisane po wznowieniu ${STAMP}`;
    const reading = fetch(`${streamUrl}&after_seq=${seen}`, {
      headers: { cookie, "last-event-id": String(seen) },
    }).then((response) => response.text());
    await new Promise((resolve) => setTimeout(resolve, 300));
    const message = await post(
      "/studio/session",
      {
        action: "message",
        workspace_id: workspaceId,
        project_id: liveProject,
        session_id: liveSession,
        text: said,
        return_to: `/w/${workspaceId}/p/${liveProject}`,
      },
      cookie,
    );
    expect([302, 303]).toContain(message.status);

    const resumed = await reading;
    expect(resumed).toContain(said);
    expect(resumed).toContain("event: wznow");
    // Nic się nie powtórzyło: wznowienie zaczyna od numeru, na którym stanęło poprzednie.
    expect(resumed).not.toContain("czytam watek, zaraz odpowie");
  });

  it("the stream is refused without a session, like every other route", async () => {
    const response = await fetch(
      url(
        `/studio/stream?workspace_id=${workspaceId}&project_id=${projectId}` +
          `&session_id=${sessionId}`,
      ),
      { redirect: "manual" },
    );
    // 401 and not a redirect: an EventSource cannot follow a login page, and a redirect would
    // show up as a stream that opens and dies with no reason anybody can read.
    expect(response.status).toBe(401);
  });
});
