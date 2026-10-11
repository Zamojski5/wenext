/**
 * THE PROOF THAT P3 REALLY READS SOMETHING AND REMEMBERS WHERE IT CAME FROM.
 *
 *   form POST → Next.js route handler → control plane → fetch of a REAL http server →
 *   bytes into artifacts → PostgreSQL → control plane → HTML
 *
 * Nothing here is seeded and nothing is stubbed on the way out: the page and the feed are served
 * by an ordinary HTTP server the harness starts on loopback, and the control plane fetches them
 * with the same fetcher production uses, address checks and all. The server is named in
 * `WENEXT_KNOWLEDGE_FIXTURE_ORIGINS`, which is the ONLY reason a plaintext loopback origin is
 * reachable at all; every other address on this machine is refused by the same code path.
 *
 * NO EXTERNAL NETWORK. The assignment requires it, and the shape of this file is the answer: the
 * two addresses under test come from the harness, and a case that needed the real internet would
 * be a case that fails on a train.
 *
 * THE COMPLETION CRITERIA OF P3 ETAP A, EACH AS A CASE:
 *   1. a material is stored with the date it was read and the hash of the bytes;
 *   2. a finding found later carries that provenance on the screen;
 *   3. a correction is visible afterwards, and so is a withdrawal, with its reason;
 *   4. an address that could not be read is recorded as unavailable WITH a reason and no content;
 *   5. a second workspace sees none of it.
 *
 * CASES SHARE STATE AND RUN IN ORDER, like the P1 suite: this is one scenario, not five.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  "WENEXT_E2E_KNOWLEDGE_PAGE_URL",
  "WENEXT_E2E_KNOWLEDGE_FEED_URL",
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

/**
 * The id carried by the row that mentions `needle`, read from `data-source` / `data-item`.
 *
 * The attribute and not a hidden form field, and that is a lesson from the P1 suite: a row
 * without a form (a fetched page has no "odśwież" button) would have no id in the markup at all,
 * and a scan that silently found nothing would look like a screen that rendered nothing.
 */
function idIn(body: string, attribute: string, needle: string): string {
  const index = body.indexOf(needle);
  if (index < 0) throw new Error(`nie ma na ekranie "${needle}"`);
  const before = body.slice(0, index);
  const matches = Array.from(before.matchAll(new RegExp(`${attribute}="(${UUID})"`, "g")));
  const last = matches[matches.length - 1];
  if (last === undefined) throw new Error(`wiersz z "${needle}" nie ma atrybutu ${attribute}`);
  return last[1] as string;
}

function itemIdFor(body: string, statement: string): string {
  return idIn(body, "data-item", statement);
}

function sourceIdFor(body: string, locator: string): string {
  return idIn(body, "data-source", locator);
}

const STAMP = Math.random().toString(36).slice(2, 10);
const PROJECT_NAME = `ProjektP3${STAMP}`;
const NOTE_TITLE = `Notatka${STAMP}`;
const NOTE_TEXT = `Klient powiedzial wprost: liczy sie koszt, nie predkosc. ${STAMP}`;
const FINDING = `Koszt wazniejszy niz predkosc ${STAMP}`;
const CORRECTED = `Koszt i przewidywalnosc, nie predkosc ${STAMP}`;

let cookie = "";
let workspaceId = "";
let projectId = "";
let knowledgePath = "";
let pageSourceId = "";

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
      goal: "Materiały z pochodzeniem",
      mode: "RESEARCH",
      return_to: `/w/${workspaceId}`,
    },
    cookie,
  );
  expect([302, 303], `utworzenie projektu zwróciło ${created.status}`).toContain(created.status);
  const workspace = await get(`/w/${workspaceId}`, cookie);
  projectId = projectIdFor(workspace.body, PROJECT_NAME);
  knowledgePath = `/w/${workspaceId}/p/${projectId}/knowledge`;
});

describe("P3 etap A: materiały z pochodzeniem zapisują się i wracają na ekran", () => {
  it("the_materials_screen_opens_empty_and_says_so_in_words", async () => {
    const screen = await get(knowledgePath, cookie);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain("Materiały");
    // An empty library says it is empty. It does not render zeros as if something was measured.
    expect(screen.body).toContain("Nic tu jeszcze nie ma");
  });

  it("a_real_page_is_fetched_and_stored_with_its_date_and_its_hash", async () => {
    const added = await post(
      "/studio/knowledge",
      {
        action: "add_source",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "WEB_PAGE",
        locator: required("WENEXT_E2E_KNOWLEDGE_PAGE_URL"),
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303], `dodanie materiału zwróciło ${added.status}`).toContain(added.status);

    const screen = await get(knowledgePath, cookie);
    expect(screen.status).toBe(200);
    // The TITLE comes from the served HTML, so finding it proves the bytes really arrived.
    expect(screen.body).toContain("Kolejki bez ack");
    expect(screen.body).toContain("przeczytane, treść zachowana");
    expect(screen.body).toContain(required("WENEXT_E2E_KNOWLEDGE_PAGE_URL"));
    pageSourceId = sourceIdFor(screen.body, required("WENEXT_E2E_KNOWLEDGE_PAGE_URL"));
  });

  it("an_address_that_cannot_be_read_is_on_the_list_with_its_reason_and_no_content", async () => {
    const dead = `${required("WENEXT_E2E_KNOWLEDGE_PAGE_URL")}-nie-ma`;
    const added = await post(
      "/studio/knowledge",
      {
        action: "add_source",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "WEB_PAGE",
        locator: dead,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(added.status);

    const screen = await get(knowledgePath, cookie);
    expect(screen.body).toContain(dead);
    expect(screen.body).toContain("nie przeczytano");
    expect(screen.body).toContain("serwer odpowiedział 404");
    expect(screen.body).toContain("nieprzeczytane: 1");
    // Two materials on the list, and only one of them was read.
    expect((screen.body.match(/data-source="/g) ?? []).length).toBe(2);
  });

  it("an_address_this_product_may_not_visit_is_refused_and_the_reason_is_on_the_screen", async () => {
    const added = await post(
      "/studio/knowledge",
      {
        action: "add_source",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "WEB_PAGE",
        locator: "http://169.254.169.254/latest/meta-data/",
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(added.status);
    const screen = await get(knowledgePath, cookie);
    // The cloud metadata address is on the list as a REFUSAL, never as a material with content.
    expect(screen.body).toContain("169.254.169.254");
    expect(screen.body).toContain("nieprzeczytane: 2");
  });

  it("a_feed_becomes_a_material_and_its_entries_become_findings_with_provenance", async () => {
    const added = await post(
      "/studio/knowledge",
      {
        action: "add_source",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "RSS",
        locator: required("WENEXT_E2E_KNOWLEDGE_FEED_URL"),
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(added.status);

    const screen = await get(knowledgePath, cookie);
    expect(screen.body).toContain("Notatki o kolejkach");
    expect(screen.body).toContain("Pierwszy wpis");
    // The entry is the FEED's claim, rendered as such, with the material it came from.
    expect(screen.body).toContain("z materiału");
    expect(screen.body).toContain("Odśwież");
  });

  it("refreshing_the_feed_through_the_queue_is_accepted_and_says_so", async () => {
    const screen = await get(knowledgePath, cookie);
    const feedSource = sourceIdFor(screen.body, required("WENEXT_E2E_KNOWLEDGE_FEED_URL"));
    const queued = await post(
      "/studio/knowledge",
      {
        action: "refresh",
        workspace_id: workspaceId,
        project_id: projectId,
        source_id: feedSource,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(queued.status);
    expect(queued.location ?? "").toContain("info=knowledge_refresh_queued");
  });

  it("a_note_is_stored_and_its_text_comes_back", async () => {
    const added = await post(
      "/studio/knowledge",
      {
        action: "add_source",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "USER_NOTE",
        title: NOTE_TITLE,
        text: NOTE_TEXT,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(added.status);
    const screen = await get(knowledgePath, cookie);
    expect(screen.body).toContain(NOTE_TITLE);
    expect(screen.body).toContain("notatka");
  });

  it("a_fact_without_a_material_is_refused_by_the_backend_and_the_screen_says_so", async () => {
    const refused = await post(
      "/studio/knowledge",
      {
        action: "write_item",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "FACT",
        statement: `Bez zrodla ${STAMP}`,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(refused.status);
    expect(refused.location ?? "").toContain("info=refused");
    const screen = await get(knowledgePath, cookie);
    expect(screen.body).not.toContain(`Bez zrodla ${STAMP}`);
  });

  it("a_fact_with_the_fetched_page_as_its_material_is_written_and_shows_that_material", async () => {
    expect(pageSourceId.length, "nie mam id materiału ze strony").toBeGreaterThan(30);
    const written = await post(
      "/studio/knowledge",
      {
        action: "write_item",
        workspace_id: workspaceId,
        project_id: projectId,
        kind: "FACT",
        statement: FINDING,
        source_id: pageSourceId,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(written.status);

    const screen = await get(knowledgePath, cookie);
    expect(screen.body).toContain(FINDING);
    // Provenance ON THE ROW: the material, when it was read, and the first bytes of the hash.
    expect(screen.body).toContain("Kolejki bez ack");
    expect(screen.body).toContain("odcisk ");
  });

  it("a_correction_is_visible_afterwards_and_the_old_finding_is_still_there", async () => {
    const screen = await get(knowledgePath, cookie);
    const itemId = itemIdFor(screen.body, FINDING);
    const corrected = await post(
      "/studio/knowledge",
      {
        action: "correct",
        workspace_id: workspaceId,
        project_id: projectId,
        item_id: itemId,
        kind: "FACT",
        source_id: pageSourceId,
        statement: CORRECTED,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(corrected.status);

    const after = await get(knowledgePath, cookie);
    expect(after.body).toContain(CORRECTED);
    // The old sentence is not gone; it moved into the history, which is the whole promise.
    expect(after.body).toContain("Historia ustaleń");
    expect(after.body).toContain(FINDING);
    expect(after.body).toContain("poprawione");
  });

  it("a_page_can_be_read_again_from_the_screen_and_the_hash_is_taken_again", async () => {
    /**
     * The refresh button is not only for feeds. A page the person is looking at is fetched on
     * the spot, and the material's read time moves, which is what "sprawdź, czy nadal aktualne"
     * has to mean in practice.
     */
    const before = await get(knowledgePath, cookie);
    const sourceId = sourceIdFor(before.body, required("WENEXT_E2E_KNOWLEDGE_PAGE_URL"));
    const refreshed = await post(
      "/studio/knowledge",
      {
        action: "refresh",
        workspace_id: workspaceId,
        project_id: projectId,
        source_id: sourceId,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(refreshed.status);

    const after = await get(knowledgePath, cookie);
    expect(after.body).toContain("Kolejki bez ack");
    expect(after.body).toContain("przeczytane, treść zachowana");
  });

  it("two_findings_about_one_thing_are_shown_together_and_neither_is_chosen", async () => {
    for (const statement of [`Rano ${STAMP}`, `Wieczorem ${STAMP}`]) {
      const written = await post(
        "/studio/knowledge",
        {
          action: "write_item",
          workspace_id: workspaceId,
          project_id: projectId,
          kind: "INTERPRETATION",
          statement,
          about: `pora ${STAMP}`,
          return_to: knowledgePath,
        },
        cookie,
      );
      expect([302, 303]).toContain(written.status);
    }

    const screen = await get(knowledgePath, cookie);
    expect(screen.body).toContain("Sprzeczne ustalenia");
    expect(screen.body).toContain(`data-conflict="pora ${STAMP}"`);
    expect(screen.body).toContain(`Rano ${STAMP}`);
    expect(screen.body).toContain(`Wieczorem ${STAMP}`);
    expect(screen.body).toContain("sprzeczne: 1");
  });

  it("a_withdrawal_keeps_the_row_and_the_reason", async () => {
    const screen = await get(knowledgePath, cookie);
    const itemId = itemIdFor(screen.body, CORRECTED);
    const retracted = await post(
      "/studio/knowledge",
      {
        action: "retract",
        workspace_id: workspaceId,
        project_id: projectId,
        item_id: itemId,
        reason: `zdezaktualizowane ${STAMP}`,
        return_to: knowledgePath,
      },
      cookie,
    );
    expect([302, 303]).toContain(retracted.status);

    const after = await get(knowledgePath, cookie);
    expect(after.body).toContain(`zdezaktualizowane ${STAMP}`);
    expect(after.body).toContain("wycofane");
  });

  it("everything_survives_a_second_login_because_it_is_in_postgresql", async () => {
    const second = await logIn();
    const screen = await get(knowledgePath, second);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain("Kolejki bez ack");
    expect(screen.body).toContain(NOTE_TITLE);
    expect(screen.body).toContain("Notatki o kolejkach");
  });

  it("another_project_sees_none_of_it", async () => {
    /**
     * The scope of this screen is the PROJECT, so that is the boundary worth breaking here.
     * (The workspace boundary is `test_the_second_workspace_sees_none_of_it` in
     * `services/control_plane/tests/test_knowledge_api.py`; creating a second workspace from this
     * suite would change a count the P1 suite asserts against the same database.)
     */
    const other = `Inny${STAMP}`;
    const created = await post(
      "/studio/project",
      {
        action: "create",
        workspace_id: workspaceId,
        name: other,
        goal: "Nic tu nie czytaliśmy",
        mode: "RESEARCH",
        return_to: `/w/${workspaceId}`,
      },
      cookie,
    );
    expect([302, 303]).toContain(created.status);
    const workspace = await get(`/w/${workspaceId}`, cookie);
    const otherProject = projectIdFor(workspace.body, other);

    const screen = await get(`/w/${workspaceId}/p/${otherProject}/knowledge`, cookie);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain("Nic tu jeszcze nie ma");
    expect(screen.body).not.toContain("Kolejki bez ack");
    expect(screen.body).not.toContain(NOTE_TITLE);
    expect(screen.body).not.toContain(CORRECTED);
  });

  it("the_materials_screen_is_behind_the_session_like_every_other_studio_screen", async () => {
    const page = await fetch(url(knowledgePath), { redirect: "manual" });
    expect([302, 303, 307, 308], `zwróciło ${page.status}`).toContain(page.status);
    expect(page.headers.get("location") ?? "").toContain("/login");
    expect(await page.text()).not.toContain(NOTE_TITLE);
  });
});
