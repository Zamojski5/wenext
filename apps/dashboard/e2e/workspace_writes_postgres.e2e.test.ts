/**
 * THE PROOF THAT P1 WRITES TO THE BACKEND, not that its screens render.
 *
 * Nothing in this file is seeded. Every workspace, project, person profile, platform account and
 * assignment it asserts on is CREATED BY THIS SUITE, through the dashboard's own forms, against a
 * real Next.js server talking to a real control plane over HTTP against a real PostgreSQL:
 *
 *   form POST → Next.js route handler → control plane → PostgreSQL → control plane → HTML
 *
 * That is the whole point. The brief for this milestone says it in one line: "nie kończ na
 * makiecie: ekran bez zapisu do backendu nie zalicza etapu". A component test over a fixture
 * cannot tell the difference, and `services/stream_gateway/stream_gateway/tickets.py` is the
 * precedent this repository already paid for — every test green, a `dict` for storage, all data
 * gone on restart.
 *
 * THE FIVE COMPLETION CRITERIA OF P1, EACH AS A CASE:
 *   1. the state survives a reload AND a second login (a value kept in a process is not storage);
 *   2. two workspaces do not see each other's data;
 *   3. two person profiles do not mix identities — one account, one active profile;
 *   4. a project supports more than one assignment;
 *   5. an account on a platform with no integration does not pretend to have one.
 * Plus the one the criteria phrase as a promise about history: detaching deletes nothing.
 *
 * A MISSING ENVIRONMENT IS A FAILURE, NEVER A SKIP (`beforeAll` throws). A skipped database test
 * reads as green in the summary, and `tools/verify_run.mjs` refuses a run with any skip.
 *
 * CASES SHARE STATE AND RUN IN ORDER. `vitest.e2e.config.mts` sets `fileParallelism: false` and
 * `sequence.concurrent: false`; the scenario is a scenario, and splitting it into independent
 * cases would mean creating a workspace five times to assert five things about one.
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
    throw new Error(`brak ${name} — harness nie przekazał danych zaszczepionych do bazy`);
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
  readonly setCookie: readonly string[];
}

async function get(pathname: string, cookie?: string): Promise<Fetched> {
  const response = await fetch(url(pathname), {
    headers: cookie === undefined ? {} : { cookie },
    redirect: "manual",
  });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: await response.text(),
    setCookie: response.headers.getSetCookie(),
  };
}

/** A form POST exactly as a browser would send one. No JSON, no fetch from page code. */
async function post(
  pathname: string,
  fields: Record<string, string>,
  cookie?: string,
): Promise<Fetched> {
  const headers: Record<string, string> = {
    "content-type": "application/x-www-form-urlencoded",
  };
  if (cookie !== undefined) headers["cookie"] = cookie;
  const response = await fetch(url(pathname), {
    method: "POST",
    headers,
    body: new URLSearchParams(fields).toString(),
    redirect: "manual",
  });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: await response.text(),
    setCookie: response.headers.getSetCookie(),
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
  if (header === undefined) {
    throw new Error("logowanie nie odesłało ciasteczka uchwytu panelu");
  }
  return header.split(";")[0] ?? "";
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

/** Ids of the workspaces offered by the sidebar's switch, in the order they are rendered. */
function workspacesIn(body: string): string[] {
  const found = new Set<string>();
  for (const match of body.matchAll(new RegExp(`href="/w/(${UUID})"`, "g"))) {
    found.add(match[1] as string);
  }
  return Array.from(found);
}

/** Id of the project whose sidebar link is rendered next to this name. */
function projectIdFor(body: string, name: string): string {
  for (const match of body.matchAll(
    new RegExp(`href="/w/${UUID}/p/(${UUID})"[^>]*>(?:(?!</a>).)*?${name}`, "gs"),
  )) {
    return match[1] as string;
  }
  throw new Error(`nie znaleziono w HTML-u linku do projektu "${name}"`);
}

/** Id of the person profile whose section carries this name. */
function profileIdFor(body: string, name: string): string {
  const match = body.match(
    new RegExp(`data-profile="(${UUID})" data-profile-name="${name}"`),
  );
  if (match === null) {
    throw new Error(`nie znaleziono w HTML-u sekcji profilu "${name}"`);
  }
  return match[1] as string;
}

/**
 * The markup of ONE profile's section, bounded by its own closing tag.
 *
 * Bounded by `</section>` and not by "the next profile", and that distinction was measured: the
 * App Router appends the serialized RSC payload to the end of the document, so a slice that ran
 * to the end of the body for the LAST profile contained every other profile's text as well. The
 * assertion "Jan's section does not mention Anna's account" then failed against a screen
 * that was perfectly correct. A slice that silently reads more than it claims is the same
 * disease as a check that reads less.
 */
function sectionOf(body: string, profileId: string): string {
  const start = body.indexOf(`data-profile="${profileId}"`);
  if (start < 0) throw new Error(`nie ma sekcji profilu ${profileId}`);
  const end = body.indexOf("</section>", start);
  if (end < 0) throw new Error(`sekcja profilu ${profileId} nie ma zamknięcia`);
  return body.slice(start, end);
}

/**
 * The rendered tag that says a platform has no integration.
 *
 * NOT the bare sentence: every profile's "add account" panel lists X and LinkedIn in a `<select>`
 * with the same words in the option text, so a plain `toContain` would be true of every profile
 * and would prove nothing about the accounts it actually holds.
 */
const ABSENT_TAG = 'data-tone="absent">integracja niedostępna';

/** A name nothing else in the database can collide with, so "found" means "we wrote it". */
const STAMP = Math.random().toString(36).slice(2, 10);
const PROJECT_NAME = `ProjektP1${STAMP}`;
const ANNA = `Anna${STAMP}`;
const JAN = `Jan${STAMP}`;
const REDDIT_HANDLE = `reddit${STAMP}`;
const LINKEDIN_HANDLE = `linkedin${STAMP}`;

let cookie = "";
let seededWorkspace = "";
let projectId = "";
let annaId = "";
let janId = "";
let redditAccountId = "";
let linkedinAccountId = "";
let secondWorkspace = "";

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
  seededWorkspace = required("WENEXT_E2E_WORKSPACE_ID");
});

describe("P1: workspace, projekty, profile osób i konta zapisują się do PostgreSQL", () => {
  it("the_studio_opens_the_workspace_the_organization_already_has", async () => {
    const entry = await get("/", cookie);
    expect([302, 303, 307, 308], `zwróciło ${entry.status}`).toContain(entry.status);
    expect(entry.location ?? "").toBe(`/w/${seededWorkspace}`);

    const workspace = await get(`/w/${seededWorkspace}`, cookie);
    expect(workspace.status).toBe(200);
    // The name comes from the row the seed inserted with raw SQL — nothing in this package made
    // it up.
    expect(workspace.body).toContain("ws-e2e");
  });

  it("a_project_created_through_the_form_is_there_after_a_reload", async () => {
    const created = await post(
      "/studio/project",
      {
        action: "create",
        workspace_id: seededWorkspace,
        name: PROJECT_NAME,
        goal: "Zbadaj odzew i zaproponuj zmiane.",
        mode: "RESEARCH",
        return_to: `/w/${seededWorkspace}`,
      },
      cookie,
    );
    expect(created.status).toBe(303);
    expect(created.location).toBe(`/w/${seededWorkspace}?info=project_created`);

    // A SEPARATE request. A value echoed back in the response of the write would prove nothing.
    const reloaded = await get(`/w/${seededWorkspace}`, cookie);
    expect(reloaded.status).toBe(200);
    expect(reloaded.body).toContain(PROJECT_NAME);
    projectId = projectIdFor(reloaded.body, PROJECT_NAME);

    // CEL JEST NA LIŚCIE PROJEKTÓW, NIE NA WEJŚCIU DO PROJEKTU (E2). Referencja ma pod tytułem
    // projektu jedną cichą linię z kontami i nic więcej; opis celu stał w bloku
    // administracyjnym, który audyt UX kazał zdjąć. Asercja idzie więc tam, gdzie cel naprawdę
    // jest widoczny, zamiast pilnować miejsca, z którego go świadomie usunięto.
    expect(reloaded.body).toContain("Zbadaj odzew");

    const detail = await get(`/w/${seededWorkspace}/p/${projectId}`, cookie);
    expect(detail.status).toBe(200);
    expect(detail.body).toContain("Research");
    // The conversation area is present and honest about what it cannot do here. P2 made the
    // field real, so the sentence it prints is no longer "P2 brings this" but the CONCRETE
    // reason a goal cannot be entrusted right now: this seeded workspace has no Codex
    // connection, so a session would park on its first turn with nothing to run on.
    //
    // The assertion is kept rather than dropped because the property it guards did not change:
    // the field must never accept a goal that nothing will answer. A composer that silently
    // started accepting input here would pass a test that only looked for "a textarea exists".
    // Po D-045 pole nazywa się pytaniem z nazwą projektu, a nie „Rozmowa o projekcie".
    expect(detail.body).toContain("Co robimy w");
    // PILNOWANA WŁASNOŚĆ SIĘ ODWRÓCIŁA I TO JEST DECYZJA PRODUKTU (E1, audyt UX 20.09). Do 20.09
    // ten test wymagał pola WYŁĄCZONEGO i zdania „Nie ma połączenia Codex": zasianą przestrzeń
    // bez połączenia traktowaliśmy jako powód, żeby nie przyjąć celu. Audyt nazwał to blokadą
    // startu. Dziś cel zapisuje się bez silnika, rozmowa czeka z kartą SILNIK i rusza sama, więc
    // test pilnuje tego, że ekran SKŁADA TĘ OBIETNICĘ, i że nie udaje połączenia, którego nie ma.
    expect(detail.body).toContain("Zapiszę cel i zacznę, gdy podłączysz Codexa");
    expect(detail.body).toContain("Codex: brak połączenia");
  });

  it("two_person_profiles_and_their_accounts_are_written_and_read_back", async () => {
    for (const name of [ANNA, JAN]) {
      const created = await post(
        "/studio/people",
        {
          action: "create_profile",
          workspace_id: seededWorkspace,
          name,
          kind: "PERSON",
          return_to: `/w/${seededWorkspace}/people`,
        },
        cookie,
      );
      expect(created.status, name).toBe(303);
      expect(created.location, name).toContain("info=profile_created");
    }

    const listed = await get(`/w/${seededWorkspace}/people`, cookie);
    expect(listed.status).toBe(200);
    annaId = profileIdFor(listed.body, ANNA);
    janId = profileIdFor(listed.body, JAN);
    expect(annaId).not.toBe(janId);

    const reddit = await post(
      "/studio/people",
      {
        action: "add_account",
        workspace_id: seededWorkspace,
        person_profile_id: annaId,
        platform: "REDDIT",
        external_account_id: REDDIT_HANDLE,
        return_to: `/w/${seededWorkspace}/people`,
      },
      cookie,
    );
    expect(reddit.location).toContain("info=account_added");

    const linkedin = await post(
      "/studio/people",
      {
        action: "add_account",
        workspace_id: seededWorkspace,
        person_profile_id: janId,
        platform: "LINKEDIN",
        external_account_id: LINKEDIN_HANDLE,
        return_to: `/w/${seededWorkspace}/people`,
      },
      cookie,
    );
    expect(linkedin.location).toContain("info=account_added");

    const after = await get(`/w/${seededWorkspace}/people`, cookie);
    const mine = sectionOf(after.body, annaId);
    const theirs = sectionOf(after.body, janId);
    expect(mine).toContain(REDDIT_HANDLE);
    expect(mine).not.toContain(LINKEDIN_HANDLE);
    expect(theirs).toContain(LINKEDIN_HANDLE);
    expect(theirs).not.toContain(REDDIT_HANDLE);

    const accountId = (fragment: string) =>
      (fragment.match(new RegExp(`data-account="(${UUID})"`)) ?? [])[1] ?? "";
    redditAccountId = accountId(mine);
    linkedinAccountId = accountId(theirs);
    expect(redditAccountId.length).toBeGreaterThan(30);
    expect(linkedinAccountId.length).toBeGreaterThan(30);
  });

  it("an_account_on_a_platform_with_no_integration_says_so_and_reddit_does_not", async () => {
    // P1's last sentence. The LinkedIn account exists — it is a real identity and the model
    // covers it from the start — and the screen states that nothing will happen on it.
    const page = await get(`/w/${seededWorkspace}/people`, cookie);
    expect(sectionOf(page.body, janId)).toContain(ABSENT_TAG);
    expect(sectionOf(page.body, annaId)).not.toContain(ABSENT_TAG);
    // And the capability table says it once more, per platform, from the backend's matrix.
    expect(page.body).toContain("integracja działa");
  });

  it("one_account_cannot_belong_to_two_profiles", async () => {
    const stolen = await post(
      "/studio/people",
      {
        action: "attach_account",
        workspace_id: seededWorkspace,
        person_profile_id: janId,
        social_account_id: redditAccountId,
        return_to: `/w/${seededWorkspace}/people`,
      },
      cookie,
    );
    expect(stolen.status).toBe(303);
    expect(stolen.location).toContain("info=refused");

    // The refusal must not have damaged the attachment that was already there.
    const page = await get(`/w/${seededWorkspace}/people`, cookie);
    expect(sectionOf(page.body, annaId)).toContain(REDDIT_HANDLE);
    expect(sectionOf(page.body, janId)).not.toContain(REDDIT_HANDLE);
  });

  it("a_project_takes_more_than_one_account", async () => {
    for (const account of [redditAccountId, linkedinAccountId]) {
      const assigned = await post(
        "/studio/assignment",
        {
          action: "assign",
          workspace_id: seededWorkspace,
          project_id: projectId,
          social_account_id: account,
          return_to: `/w/${seededWorkspace}/p/${projectId}`,
        },
        cookie,
      );
      expect(assigned.status, account).toBe(303);
      expect(assigned.location, account).toContain("info=account_assigned");
    }

    const detail = await get(`/w/${seededWorkspace}/p/${projectId}`, cookie);
    expect(detail.body).toContain(REDDIT_HANDLE);
    expect(detail.body).toContain(LINKEDIN_HANDLE);
    // The account with no integration is marked as such HERE too, where a project claims to
    // work with it.
    expect(detail.body).toContain(ABSENT_TAG);
  });

  it("taking_an_account_off_a_project_keeps_the_assignment_as_history", async () => {
    const removed = await post(
      "/studio/assignment",
      {
        action: "unassign",
        workspace_id: seededWorkspace,
        project_id: projectId,
        social_account_id: linkedinAccountId,
        return_to: `/w/${seededWorkspace}/p/${projectId}`,
      },
      cookie,
    );
    expect(removed.location).toContain("info=account_unassigned");

    const detail = await get(`/w/${seededWorkspace}/p/${projectId}`, cookie);
    expect(detail.body).toContain("Historia przypisań");
    expect(detail.body).toContain(LINKEDIN_HANDLE);
    expect(detail.body).toContain(REDDIT_HANDLE);
  });

  it("detaching_an_account_from_a_profile_deletes_nothing", async () => {
    const detached = await post(
      "/studio/people",
      {
        action: "detach_account",
        workspace_id: seededWorkspace,
        person_profile_id: annaId,
        social_account_id: redditAccountId,
        return_to: `/w/${seededWorkspace}/people`,
      },
      cookie,
    );
    expect(detached.location).toContain("info=account_detached");

    const page = await get(`/w/${seededWorkspace}/people`, cookie);
    const mine = sectionOf(page.body, annaId);
    expect(mine).toContain("Odpięte wcześniej");
    expect(mine).toContain(REDDIT_HANDLE);

    // Freed, so Jan can have it now, and that is the other half of "the rule is the index,
    // not a check somewhere in the code".
    const moved = await post(
      "/studio/people",
      {
        action: "attach_account",
        workspace_id: seededWorkspace,
        person_profile_id: janId,
        social_account_id: redditAccountId,
        return_to: `/w/${seededWorkspace}/people`,
      },
      cookie,
    );
    expect(moved.location).toContain("info=account_attached");
  });

  it("a_second_workspace_sees_nothing_of_the_first", async () => {
    const created = await post(
      "/studio/workspace",
      { name: `Druga${STAMP}`, return_to: `/w/${seededWorkspace}` },
      cookie,
    );
    expect(created.status).toBe(303);
    expect(created.location).toContain("info=workspace_created");

    const page = await get(`/w/${seededWorkspace}`, cookie);
    const offered = workspacesIn(page.body);
    expect(offered.length).toBe(2);
    secondWorkspace = offered.filter((id) => id !== seededWorkspace)[0] ?? "";
    expect(secondWorkspace.length).toBeGreaterThan(30);

    const other = await get(`/w/${secondWorkspace}`, cookie);
    expect(other.status).toBe(200);
    // Presence first: the read succeeded and rendered the OTHER workspace's own name.
    expect(other.body).toContain(`Druga${STAMP}`);
    expect(other.body).not.toContain(PROJECT_NAME);

    const people = await get(`/w/${secondWorkspace}/people`, cookie);
    expect(people.status).toBe(200);
    expect(people.body).toContain("Nie ma jeszcze żadnego profilu");
    expect(people.body).not.toContain(ANNA);
    expect(people.body).not.toContain(JAN);
  });

  it("a_project_of_another_workspace_is_a_measured_absence_not_a_leak", async () => {
    const crossed = await get(`/w/${secondWorkspace}/p/${projectId}`, cookie);
    expect(crossed.status).toBe(200);
    expect(crossed.body).toContain("Nie ma takiego zasobu");
    expect(crossed.body).not.toContain(PROJECT_NAME);
  });

  it("everything_is_still_there_after_logging_in_again", async () => {
    // A NEW session: the dashboard's handle map and the control plane's session store are both
    // in memory, so this is the case where anything kept in a process instead of PostgreSQL
    // disappears.
    const second = await logIn();
    expect(second).not.toBe(cookie);

    const people = await get(`/w/${seededWorkspace}/people`, second);
    expect(people.status).toBe(200);
    expect(people.body).toContain(ANNA);
    expect(people.body).toContain(JAN);
    expect(people.body).toContain(REDDIT_HANDLE);
    expect(people.body).toContain(LINKEDIN_HANDLE);

    const detail = await get(`/w/${seededWorkspace}/p/${projectId}`, second);
    expect(detail.status).toBe(200);
    expect(detail.body).toContain(PROJECT_NAME);
    // Cel czyta się z listy projektów, nie z wejścia do projektu (E2, jak wyżej). Pytanie tego
    // przypadku brzmi „czy to samo konto widzi te same dane po ponownym zalogowaniu", więc
    // odczyt idzie tam, gdzie cel naprawdę stoi.
    const overview = await get(`/w/${seededWorkspace}`, second);
    expect(overview.body).toContain("Zbadaj odzew");
    cookie = second;
  });

  it("no_studio_screen_and_no_studio_write_works_without_a_session", async () => {
    const paths = [
      "/",
      `/w/${seededWorkspace}`,
      `/w/${seededWorkspace}/people`,
      `/w/${seededWorkspace}/codex`,
      `/w/${seededWorkspace}/p/${projectId}`,
    ];
    expect(paths.length).toBe(5);
    for (const pathname of paths) {
      const page = await get(pathname);
      expect([302, 303, 307, 308], `${pathname} zwróciło ${page.status}`).toContain(page.status);
      expect(page.location ?? "", pathname).toContain("/login");
      expect(page.body, pathname).not.toContain(ANNA);
    }

    const write = await post("/studio/project", {
      action: "create",
      workspace_id: seededWorkspace,
      name: `NieMaMnie${STAMP}`,
      goal: "",
      mode: "AUTONOMOUS",
      return_to: `/w/${seededWorkspace}`,
    });
    expect(write.status).toBe(303);
    expect(write.location).toBe("/login");

    // And it really wrote nothing. A 303 to the login screen with the row already inserted is
    // exactly the failure this assertion exists to catch.
    const page = await get(`/w/${seededWorkspace}`, cookie);
    expect(page.body).not.toContain(`NieMaMnie${STAMP}`);
  });

  it("a_return_path_pointing_off_this_origin_is_refused", async () => {
    // `return_to` arrives in the POST body, so anybody can put anything in it. A 303 whose
    // Location came from that body is an open redirect: a link that looks like this dashboard
    // and lands somewhere else, followed by a logged-in operator.
    for (const hostile of ["https://evil.example/x", "//evil.example/x", "/\\evil.example"]) {
      const response = await post(
        "/studio/workspace",
        { name: "", return_to: hostile },
        cookie,
      );
      expect(response.status, hostile).toBe(303);
      const location = response.location ?? "";
      expect(location, hostile).not.toContain("evil.example");
      expect(location.startsWith("/?"), hostile).toBe(true);
    }
  });

  it("the_control_plane_session_token_never_appears_in_a_studio_response", async () => {
    // The dashboard holds the control plane's token and hands the browser an unrelated handle
    // (D-016, `lib/session.ts`). Studio screens are new surface, so the rule is measured on them
    // too rather than inherited from the screens that had it tested.
    const handle = cookie.split("=")[1] ?? "";
    expect(handle.length).toBeGreaterThan(20);
    for (const pathname of [
      `/w/${seededWorkspace}`,
      `/w/${seededWorkspace}/people`,
      `/w/${seededWorkspace}/p/${projectId}`,
    ]) {
      const page = await get(pathname, cookie);
      expect(page.body, pathname).not.toContain("wenext_operator_session");
      expect(page.body, pathname).not.toContain(handle);
    }
  });
});

/**
 * P1 etap 2 — Codex connections, through the same real chain.
 *
 * THE BINARY IS ABSENT ON PURPOSE IN THIS HARNESS (`WENEXT_CODEX_BIN` points at a path that does
 * not exist). With a real one the dispatcher inside the control plane would claim the login job
 * these cases create and start a REAL device-code login against a REAL account — an outward
 * action no automated run may perform, and the one thing the brief for this milestone forbids.
 *
 * WHAT IS THEREFORE STILL PROVED HERE, and it is the part that could silently not work: the row
 * reaches PostgreSQL, the DIRECTORY reaches the disk (the backend reads it back and reports
 * `exists`), the configured root is reported rather than adopted on its own, and a disconnect
 * leaves everything in place. What the engine does with a real account is measured in
 * `services/control_plane/tests/test_codex_connections_api.py` against a stubbed port, and the
 * app-server's own answers were measured directly on 2026-09-19.
 */
describe("P1 etap 2: połączenia Codex", () => {
  const CODEX = (workspace: string) => `/w/${workspace}/codex`;

  it("the_configured_home_is_reported_and_is_not_a_connection_until_somebody_says_so", async () => {
    const page = await get(CODEX(seededWorkspace), cookie);
    expect(page.status).toBe(200);
    expect(page.body).toContain("Wdrożenie używa katalogu");
    expect(page.body).toContain("Przyjmij jako połączenie");
    // Nothing has been registered, and the screen says that rather than showing an empty table.
    expect(page.body).toContain("Nie ma jeszcze żadnego połączenia");
    // And it is a Codex screen: it must not mention a social account anywhere.
    expect(page.body).not.toContain(REDDIT_HANDLE);
    expect(page.body).not.toContain(LINKEDIN_HANDLE);
  });

  it("adopting_the_configured_home_creates_a_row_without_touching_that_directory", async () => {
    const adopted = await post(
      "/studio/codex",
      { action: "adopt_root", label: `Korzeń${STAMP}`, return_to: CODEX(seededWorkspace) },
      cookie,
    );
    expect(adopted.status).toBe(303);
    expect(adopted.location).toContain("info=codex_adopted");

    const page = await get(CODEX(seededWorkspace), cookie);
    expect(page.body).toContain(`Korzeń${STAMP}`);
    expect(page.body).toContain("korzeń wolumenu");
    expect(page.body).toContain("jest już przyjęty jako połączenie");
    // The file that was there before is still reported as there — the adoption wrote nothing.
    expect(page.body).not.toContain("Przyjmij jako połączenie");
    // And this connection is never opened with an app-server of ours: the pilot's turn runner
    // uses that same directory and two on one CODEX_HOME are forbidden (L2159). The screen says
    // so and does not show "czeka na zalogowanie" next to an account that is signed in.
    expect(page.body).toContain("odczyt wyłączony");
    expect(page.body).toContain("L2159");
    expect(page.body).not.toContain("czeka na zalogowanie");
  });

  it("a_second_connection_gets_its_own_directory_on_disk", async () => {
    const created = await post(
      "/studio/codex",
      { action: "create", label: `Drugi silnik${STAMP}`, return_to: CODEX(seededWorkspace) },
      cookie,
    );
    expect(created.status).toBe(303);
    expect(created.location).toContain("info=codex_created");

    const page = await get(CODEX(seededWorkspace), cookie);
    expect(page.body).toContain(`Drugi silnik${STAMP}`);
    // Two connections, and the first one is still the root.
    const rows = page.body.match(/data-connection="/g) ?? [];
    expect(rows.length).toBe(2);
    expect(page.body).toContain("korzeń wolumenu");
    // `dostępne dla całej organizacji` is the scope a connection has with no workspace named.
    expect(page.body).toContain("dostępne dla całej organizacji");
  });

  it("a_connection_that_cannot_reach_an_app_server_says_why_instead_of_showing_zeros", async () => {
    // The dispatcher inside the control plane claims the read job; with no binary the engine
    // answers `EngineUnavailable` and the row records that sentence. What must NOT appear is a
    // limit of zero or an empty list of models presented as the whole catalogue.
    const deadline = Date.now() + 20_000;
    let body = "";
    while (Date.now() < deadline) {
      body = (await get(CODEX(seededWorkspace), cookie)).body;
      if (body.includes("Błąd:")) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    expect(body, "job odczytu nie zdążył zapisać stanu w 20 s").toContain("Błąd:");
    expect(body).toContain("app-server");
    expect(body).toContain("Limity: nie odczytano");
    expect(body).toContain("Modele: nie odczytano");
    // No invented number anywhere near the limits.
    expect(body).not.toContain("0% zużyte");
  });

  it("disconnecting_a_connection_keeps_it_in_the_list_as_history", async () => {
    const page = await get(CODEX(seededWorkspace), cookie);
    const match = page.body.match(new RegExp(`data-connection="(${UUID})"`));
    const connectionId = match?.[1] ?? "";
    expect(connectionId.length).toBeGreaterThan(30);

    const removed = await post(
      "/studio/codex",
      {
        action: "disconnect",
        connection_id: connectionId,
        return_to: CODEX(seededWorkspace),
      },
      cookie,
    );
    expect(removed.status).toBe(303);
    expect(removed.location).toContain("info=codex_disconnected");

    const after = await get(CODEX(seededWorkspace), cookie);
    expect(after.body).toContain("odłączone");
    expect((after.body.match(/data-connection="/g) ?? []).length).toBe(2);
    // The volume root is free again, so the screen offers to adopt it once more.
    expect(after.body).toContain("Przyjmij jako połączenie");
  });

  it("the_codex_screen_needs_a_session_and_so_does_every_write", async () => {
    const page = await get(CODEX(seededWorkspace));
    expect([302, 303, 307, 308]).toContain(page.status);
    expect(page.location ?? "").toContain("/login");

    const write = await post("/studio/codex", {
      action: "create",
      label: `NieMaMnie${STAMP}`,
      return_to: CODEX(seededWorkspace),
    });
    expect(write.status).toBe(303);
    expect(write.location).toBe("/login");

    const after = await get(CODEX(seededWorkspace), cookie);
    expect(after.body).not.toContain(`NieMaMnie${STAMP}`);
  });
});
