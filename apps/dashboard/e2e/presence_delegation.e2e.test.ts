/**
 * THE PROOF THAT P4's DELEGATION REALLY GOVERNS, END TO END.
 *
 *   form POST → Next.js route handler → control plane → PostgreSQL → control plane → HTML
 *
 * Nothing here is seeded behind the screens and nothing is stubbed: the delegation is signed
 * through the ordinary API, paused and revoked through the studio's own form handler, and every
 * assertion is made against the HTML a browser would receive.
 *
 * WHAT THIS FILE IS ACTUALLY GUARDING. P4 lets the product act with nobody watching, so the
 * dangerous failure is not a crash: it is a screen that says the agent is idle while it is
 * working, or a stop button that stops nothing. The cases are chosen for exactly that:
 *
 *   1. a project with no delegation SAYS SO and shows no state it has not measured;
 *   2. signing shows the mode, the scope and the limits that BIND, including the ones nobody
 *      chose (the backend applies a conservative default, and hiding it would let somebody sign
 *      a scope whose real pace is invisible);
 *   3. pausing changes the sentence on the screen, not only a row in a table;
 *   4. a pause comes back on the SAME signature: the digest after resuming is the one from
 *      before pausing;
 *   5. revoking is refused a second time and leaves the project saying it is not delegated;
 *   6. a project of another workspace is not reachable by guessing its URL.
 *
 * THE MODEL NEVER RUNS HERE. The tick is a job; this suite measures the delegation, the screens
 * and the stop buttons. `services/control_plane/tests/test_presence_tick.py` measures the cycle.
 *
 * CASES SHARE STATE AND RUN IN ORDER: this is one scenario, not six.
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
  if (value === undefined) {
    throw new Error(`brak ${name} — harness nie przekazał danych do testu`);
  }
  return value;
}

function url(pathname: string): string {
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

interface Fetched {
  readonly status: number;
  readonly location: string | null;
  readonly body: string;
}

async function get(pathname: string, cookie: string): Promise<Fetched> {
  const response = await fetch(url(pathname), { headers: { cookie }, redirect: "manual" });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: await response.text(),
  };
}

async function post(
  pathname: string,
  fields: Record<string, string>,
  cookie: string,
): Promise<Fetched> {
  const response = await fetch(url(pathname), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", cookie },
    body: new URLSearchParams(fields).toString(),
    redirect: "manual",
  });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: await response.text(),
  };
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
  if (header === undefined) throw new Error("logowanie nie odesłało ciasteczka uchwytu panelu");
  return header.split(";")[0] ?? "";
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

function projectIdFor(body: string, name: string): string {
  for (const match of body.matchAll(
    new RegExp(`href="/w/${UUID}/p/(${UUID})"[^>]*>(?:(?!</a>).)*?${name}`, "gs"),
  )) {
    return match[1] as string;
  }
  throw new Error(`nie znaleziono w HTML-u linku do projektu "${name}"`);
}

function digestOn(body: string): string {
  const match = body.match(/[0-9a-f]{64}/);
  if (match === null) throw new Error("na ekranie nie ma odcisku podpisanego zakresu");
  return match[0];
}

const STAMP = Math.random().toString(36).slice(2, 10);
const PROJECT_NAME = `ProjektP4${STAMP}`;

let cookie = "";
let workspaceId = "";
let projectId = "";
let presencePath = "";
let signedDigest = "";

beforeAll(async () => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") {
      missing.push(name);
      continue;
    }
    environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(
      `BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie — test ` +
        "pominięty po cichu przechodzi w podsumowaniu za zielony. Uruchom: " +
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
      name: PROJECT_NAME,
      goal: "Bądź obecny, gdy nikogo nie ma",
      mode: "AUTONOMOUS",
      return_to: `/w/${workspaceId}`,
    },
    cookie,
  );
  expect([302, 303], `utworzenie projektu zwróciło ${created.status}`).toContain(created.status);
  const workspace = await get(`/w/${workspaceId}`, cookie);
  projectId = projectIdFor(workspace.body, PROJECT_NAME);
  presencePath = `/w/${workspaceId}/p/${projectId}/presence`;
});

describe("P4: powierzenie rządzi, a przyciski naprawdę zatrzymują", () => {
  it("a_project_without_a_delegation_says_so_and_claims_no_state", async () => {
    const screen = await get(presencePath, cookie);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain("Obecność");
    // Not "agent czuwa" and not an empty table of zeros: the honest sentence.
    expect(screen.body).toContain("nie jest powierzony agentowi");
    expect(screen.body).toContain("obudzenia: 0");
  });

  it("the_project_screen_links_to_it", async () => {
    // A screen nobody can reach is not a feature. Measured on the HTML, not on the route table.
    const project = await get(`/w/${workspaceId}/p/${projectId}`, cookie);
    expect(project.status).toBe(200);
    expect(project.body).toContain(`href="${presencePath}"`);
  });

  it("signing_shows_the_mode_the_scope_and_the_limits_that_bind", async () => {
    const signed = await post(
      "/studio/presence",
      {
        action: "sign",
        workspace_id: workspaceId,
        project_id: projectId,
        mode: "AUTONOMOUS",
        communities: "r/kubernetes",
        actions: "REPLY",
        // NO publication limit on purpose: the fields are left blank, the backend applies a
        // conservative default, and the screen has to show it. A founder must never sign a scope
        // whose real pace is hidden from them.
        publications_per_day: "",
        publications_per_hour: "",
        model_turns_per_day: "20",
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303], `podpis zwrócił ${signed.status}`).toContain(signed.status);
    expect(signed.location ?? "").toContain("delegation_signed");

    const screen = await get(presencePath, cookie);
    expect(screen.body).toContain("Agent czuwa");
    expect(screen.body).toContain("Tryb AUTONOMOUS");
    expect(screen.body).toContain("r/kubernetes");
    expect(screen.body).toContain("tury modelu: 20/dobę");
    // The default pace, shown AND marked as something nobody chose.
    expect(screen.body).toContain("publikacje: 1/h, 6/dobę");
    expect(screen.body).toContain("domyślne, bo nie wpisano ich w podpisie");
    signedDigest = digestOn(screen.body);
    expect(signedDigest.length).toBe(64);
  });

  it("signing_again_over_a_live_delegation_is_refused", async () => {
    const again = await post(
      "/studio/presence",
      {
        action: "sign",
        workspace_id: workspaceId,
        project_id: projectId,
        mode: "REVIEW",
        communities: "r/kubernetes",
        actions: "REPLY",
        return_to: presencePath,
      },
      cookie,
    );
    // The control plane answers 409 and the handler turns that into `info=refused`: revoking
    // first is a deliberate step, not a formality the UI can skip.
    expect([302, 303]).toContain(again.status);
    expect(again.location ?? "").toContain("refused");

    const screen = await get(presencePath, cookie);
    expect(screen.body, "poprzedni podpis nie został podmieniony").toContain("Tryb AUTONOMOUS");
  });

  it("pausing_changes_the_sentence_on_the_screen", async () => {
    const paused = await post(
      "/studio/presence",
      {
        action: "pause",
        workspace_id: workspaceId,
        project_id: projectId,
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303], `wstrzymanie zwróciło ${paused.status}`).toContain(paused.status);

    const screen = await get(presencePath, cookie);
    expect(screen.body).toContain("Powierzenie jest wstrzymane");
    expect(screen.body).toContain("Wznów");
    expect(screen.body).not.toContain("Agent czuwa");
  });

  it("resuming_brings_back_the_same_signature", async () => {
    const resumed = await post(
      "/studio/presence",
      {
        action: "resume",
        workspace_id: workspaceId,
        project_id: projectId,
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(resumed.status);

    const screen = await get(presencePath, cookie);
    expect(screen.body).toContain("Agent czuwa");
    // THE POINT OF PAUSING RATHER THAN REVOKING: the same digest, so the same thing was agreed
    // to. A resume that produced a new signature would be a re-signature nobody read.
    expect(digestOn(screen.body)).toBe(signedDigest);
  });

  it("revoking_ends_it_and_cannot_be_done_twice", async () => {
    const revoked = await post(
      "/studio/presence",
      {
        action: "revoke",
        workspace_id: workspaceId,
        project_id: projectId,
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(revoked.status);

    const screen = await get(presencePath, cookie);
    expect(screen.body).toContain("nie jest powierzony agentowi");
    // And the screen offers the way back in, rather than being a dead end.
    expect(screen.body).toContain("Powierz projekt agentowi");

    const again = await post(
      "/studio/presence",
      {
        action: "revoke",
        workspace_id: workspaceId,
        project_id: projectId,
        return_to: presencePath,
      },
      cookie,
    );
    // The handler answers 303 back to the screen with `info=refused`: the control plane said 404,
    // because there is no delegation to revoke any more.
    expect([302, 303]).toContain(again.status);
    expect(again.location ?? "").toContain("refused");
  });

  it("a_new_signature_after_revoking_is_a_new_delegation", async () => {
    const signed = await post(
      "/studio/presence",
      {
        action: "sign",
        workspace_id: workspaceId,
        project_id: projectId,
        mode: "REVIEW",
        communities: "r/kubernetes",
        actions: "REPLY",
        publications_per_day: "2",
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(signed.status);

    const screen = await get(presencePath, cookie);
    expect(screen.body).toContain("Tryb REVIEW");
    expect(screen.body).toContain("publikacje: 1/h, 2/dobę");
    expect(digestOn(screen.body)).not.toBe(signedDigest);
  });

  it("an_unknown_action_on_the_handler_is_refused", async () => {
    // The closed set with a refusing default: a misspelled hidden field must not fall through to
    // the first branch, which here would be a pause.
    const bogus = await post(
      "/studio/presence",
      {
        action: "wstrzymaj",
        workspace_id: workspaceId,
        project_id: projectId,
        return_to: presencePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(bogus.status);
    expect(bogus.location ?? "").toContain("refused");

    const screen = await get(presencePath, cookie);
    expect(screen.body, "nic się nie zmieniło").toContain("Tryb REVIEW");
  });

  it("the_screen_of_a_workspace_that_is_not_ours_is_an_absence_not_a_leak", async () => {
    // A workspace id that belongs to nobody. Deliberately NOT a second real workspace: this
    // suite shares a database with the P1 one, which counts the workspaces it can see, and a
    // test that quietly adds one to somebody else's count is a test that breaks another file.
    const stranger = "00000000-0000-4000-8000-000000000001";
    const guessed = await get(`/w/${stranger}/p/${projectId}/presence`, cookie);
    expect(guessed.status).toBe(200);
    // Absent, not forbidden: the same answer an id from another organization gets.
    expect(guessed.body).toContain("Nie ma takiego zasobu");
    expect(guessed.body).not.toContain("Tryb REVIEW");
  });
});
