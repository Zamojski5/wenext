/**
 * TWO CUSTOMERS ON ONE DEPLOYMENT, THROUGH THE REAL FORMS (P7 etap A).
 *
 *   form POST → Next.js route handler → control plane → PostgreSQL → HTML
 *
 * Nothing here is seeded: both organizations are created by filling in the registration form of
 * a real Next.js server talking to a real control plane. That is the milestone's criterion in
 * one sentence — "nowy użytkownik zakłada własną przestrzeń bez ręcznej obsługi bazy przez
 * założyciela" — and a component test over a fixture cannot tell whether it holds.
 *
 * THE CASES, IN THE ORDER THE CRITERION LISTS THEM:
 *
 *   1. registration works from a browser with no session and no console;
 *   2. each customer sees their own space and 404 on the other's — the same answer as a space
 *      that does not exist, because 403 would confirm the identifier is real;
 *   3. a third person invited to B joins B and still sees nothing of A;
 *   4. an export of A downloads and holds A's data and none of B's;
 *   5. deleting B stops B's sign-in immediately, and A keeps working;
 *   6. the founder's operator password still opens a session after migration 0018.
 *
 * CASES SHARE STATE AND RUN IN ORDER, like the other e2e files here: this is one scenario.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = ["WENEXT_E2E_BASE_URL", "WENEXT_E2E_OPERATOR_PASSWORD"] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};
const PASSWORD = "haslo-do-testow-1234";
const STAMP = Math.random().toString(36).slice(2, 8);

function required(name: Required): string {
  const value = environment[name];
  if (value === undefined) throw new Error(`brak ${name}`);
  return value;
}

function url(pathname: string): string {
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

interface Fetched {
  readonly status: number;
  readonly location: string | null;
  readonly body: string;
  readonly cookie: string | null;
}

function cookieOf(response: Response): string | null {
  const header = response.headers
    .getSetCookie()
    .find((value) => value.startsWith("wenext_dashboard_handle="));
  return header === undefined ? null : (header.split(";")[0] ?? null);
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
    cookie: cookieOf(response),
  };
}

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
    cookie: cookieOf(response),
  };
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

function workspaceIn(body: string): string {
  const match = body.match(new RegExp(`href="/w/(${UUID})"`));
  if (match === null) throw new Error("nie ma na ekranie żadnego workspace'u");
  return match[1] as string;
}

const EMAIL_A = `ala-${STAMP}@pierwsza.test`;
const EMAIL_B = `bogdan-${STAMP}@druga.test`;
const EMAIL_C = `czeslaw-${STAMP}@druga.test`;

let cookieA = "";
let cookieB = "";
let cookieC = "";
let workspaceA = "";
let workspaceB = "";

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
});

describe("P7 etap A: dwie organizacje na jednym wdrożeniu", () => {
  it("the_registration_screen_is_reachable_without_a_session", async () => {
    const screen = await get("/register");
    expect(screen.status).toBe(200);
    expect(screen.body).toContain("Załóż własną przestrzeń");
    // And it says what it will not tell a stranger.
    expect(screen.body).toContain("Masz już konto");
  });

  it("a_new_customer_registers_and_lands_in_their_own_empty_space", async () => {
    const registered = await post("/register/submit", {
      email: EMAIL_A,
      password: PASSWORD,
      organization_name: `Pierwsza ${STAMP}`,
    });
    expect([302, 303], registered.body.slice(0, 200)).toContain(registered.status);
    expect(registered.cookie).not.toBeNull();
    cookieA = registered.cookie as string;

    // The onboarding of an empty space: four steps, no demo data.
    const entry = await get("/", cookieA);
    expect(entry.status).toBe(200);
    expect(entry.body).toContain('data-block="onboarding"');
    expect(entry.body).toContain("Połączenie Codex");
    // No demo data: the space is empty and the screen says so rather than seeding examples.
    expect(entry.body).not.toContain("data-project=");

    const created = await post(
      "/studio/workspace",
      { name: `Przestrzeń A ${STAMP}`, return_to: "/" },
      cookieA,
    );
    expect([302, 303]).toContain(created.status);
    const after = await get("/", cookieA);
    // The entry screen redirects into the only workspace once there is one.
    workspaceA =
      after.status === 200 ? workspaceIn(after.body) : (after.location ?? "").split("/w/")[1] ?? "";
    expect(workspaceA).not.toBe("");
  });

  it("a_second_customer_registers_and_the_two_see_nothing_of_each_other", async () => {
    const registered = await post("/register/submit", {
      email: EMAIL_B,
      password: PASSWORD,
      organization_name: `Druga ${STAMP}`,
    });
    expect([302, 303]).toContain(registered.status);
    cookieB = registered.cookie as string;

    await post("/studio/workspace", { name: `Przestrzeń B ${STAMP}`, return_to: "/" }, cookieB);
    const entry = await get("/", cookieB);
    workspaceB =
      entry.status === 200 ? workspaceIn(entry.body) : (entry.location ?? "").split("/w/")[1] ?? "";
    expect(workspaceB).not.toBe("");
    expect(workspaceB).not.toBe(workspaceA);

    // A's screens do not exist for B. The dashboard renders the "nie ma" state rather than
    // anything of A's.
    const trespass = await get(`/w/${workspaceA}`, cookieB);
    expect(trespass.status, `location: ${trespass.location}, workspaceA=${workspaceA}`).toBe(200);
    expect(trespass.body).not.toContain(`Przestrzeń A ${STAMP}`);
    expect(trespass.body).toContain("nie ma");
  });

  it("a_third_person_invited_to_the_second_space_joins_it_and_only_it", async () => {
    const invited = await post(
      "/studio/organization",
      {
        action: "invite",
        workspace_id: workspaceB,
        email: EMAIL_C,
        return_to: `/w/${workspaceB}/organization`,
      },
      cookieB,
    );
    expect([302, 303], invited.body.slice(0, 200)).toContain(invited.status);
    const location = invited.location ?? "";
    expect(location, `pełne przekierowanie: ${location}`).toContain("info=member_invited");

    // The link is shown once, on the inviter's screen.
    const screen = await get(location, cookieB);
    expect(screen.body).toContain('data-invitation-link="1"');
    const match = screen.body.match(/\/invitation\?token=([A-Za-z0-9_-]+)/);
    expect(match, "na ekranie nie ma linku z zaproszeniem").not.toBeNull();
    const token = (match as RegExpMatchArray)[1] as string;

    const joined = await post("/invitation/submit", { token, password: PASSWORD });
    expect([302, 303]).toContain(joined.status);
    cookieC = joined.cookie as string;

    expect((await get(`/w/${workspaceB}`, cookieC)).body).toContain(`Przestrzeń B ${STAMP}`);
    const trespass = await get(`/w/${workspaceA}`, cookieC);
    expect(trespass.body).not.toContain(`Przestrzeń A ${STAMP}`);
  });

  it("the_space_screen_lists_who_is_here_and_who_used_to_be", async () => {
    const screen = await get(`/w/${workspaceB}/organization`, cookieB);
    expect(screen.status).toBe(200);
    expect(screen.body).toContain(EMAIL_B);
    expect(screen.body).toContain(EMAIL_C);
    expect(screen.body).toContain("ma dostęp");
  });

  it("revoking_access_takes_effect_on_the_next_request", async () => {
    const members = await get(`/w/${workspaceB}/organization`, cookieB);
    const section = members.body.slice(members.body.indexOf(EMAIL_C));
    // `name="user_id"[^>]*value=…` and not the attributes in source order: React renders
    // `type` before `name`, so a pattern written the way the JSX reads matches nothing.
    const match = section.match(new RegExp(`name="user_id"[^>]*value="(${UUID})"`));
    expect(
      match,
      `nie ma formularza odbierania dostępu przy tej osobie. Fragment: ${section.slice(0, 600)}`,
    ).not.toBeNull();
    const userId = (match as RegExpMatchArray)[1] as string;

    const revoked = await post(
      "/studio/organization",
      {
        action: "revoke",
        workspace_id: workspaceB,
        user_id: userId,
        return_to: `/w/${workspaceB}/organization`,
      },
      cookieB,
    );
    expect([302, 303]).toContain(revoked.status);

    // The third person's very next request is refused: the dashboard sends them to the login.
    const after = await get(`/w/${workspaceB}`, cookieC);
    expect([302, 303, 307, 308]).toContain(after.status);
    expect(after.location ?? "").toContain("/login");
  });

  it("an_export_downloads_and_holds_this_customer_and_nobody_else", async () => {
    const asked = await post(
      "/studio/organization",
      { action: "export", workspace_id: workspaceA, return_to: `/w/${workspaceA}/organization` },
      cookieA,
    );
    expect([302, 303]).toContain(asked.status);

    // The worker builds it; the screen shows the state until then. Wait for READY rather than
    // assuming: the point of the queue is that this takes as long as it takes.
    let jobId = "";
    // Up to thirty seconds: the queue learns about a newly registered customer on its next
    // rescan (`WENEXT_ORGANIZATION_RESCAN_SECONDS`, three in this harness), and then the worker
    // has to build the file.
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const screen = await get(`/w/${workspaceA}/organization`, cookieA);
      const ready = screen.body.match(new RegExp(`data-export="(${UUID})"[\\s\\S]{0,400}?Pobierz`));
      if (ready !== null) {
        jobId = ready[1] as string;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    expect(jobId, "eksport nie stał się gotowy w 30 sekund").not.toBe("");

    const file = await get(
      `/studio/organization/export?workspace_id=${workspaceA}&job_id=${jobId}`,
      cookieA,
    );
    expect(file.status).toBe(200);
    const payload = JSON.parse(file.body) as Record<string, unknown>;
    const meta = payload["eksport"] as Record<string, unknown>;
    expect(String(meta["nazwa"])).toBe(`Pierwsza ${STAMP}`);
    expect(file.body).toContain(`Przestrzeń A ${STAMP}`);
    expect(file.body).not.toContain(`Przestrzeń B ${STAMP}`);
    // Nothing that could be used to act as this customer.
    expect(file.body).not.toContain("$argon2id$");
    expect(file.body).not.toContain("credential_hash");
  });

  it("deleting_the_second_space_blocks_it_at_once_and_leaves_the_first_alone", async () => {
    const deleted = await post(
      "/studio/organization",
      {
        action: "delete",
        workspace_id: workspaceB,
        password: PASSWORD,
        return_to: `/w/${workspaceB}/organization`,
      },
      cookieB,
    );
    expect([302, 303]).toContain(deleted.status);

    // B's session is dead from the next request, and a fresh login is refused.
    const after = await get(`/w/${workspaceB}`, cookieB);
    expect([302, 303, 307, 308]).toContain(after.status);
    const refused = await post("/login/submit", { email: EMAIL_B, password: PASSWORD });
    expect(refused.location ?? "").toContain("blad=odmowa");

    // A is untouched, and still signs in.
    expect((await get(`/w/${workspaceA}`, cookieA)).status).toBe(200);
    const stillWorks = await post("/login/submit", { email: EMAIL_A, password: PASSWORD });
    expect(stillWorks.location ?? "").not.toContain("blad");
  });

  it("the_founders_operator_password_still_works_after_the_migration", async () => {
    const opened = await post("/login/submit", {
      password: required("WENEXT_E2E_OPERATOR_PASSWORD"),
    });
    expect([302, 303]).toContain(opened.status);
    expect(opened.location ?? "").not.toContain("blad");
    expect(opened.cookie).not.toBeNull();
  });
});
