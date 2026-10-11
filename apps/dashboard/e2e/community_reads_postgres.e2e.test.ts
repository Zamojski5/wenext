/**
 * `/community` AGAINST REAL POSTGRESQL — the Outcome A screen, driven end to end.
 *
 * Same chain and same discipline as `dashboard_reads_postgres.e2e.test.ts`, and nothing here is a
 * stand-in:
 *
 *   raw SQL (tools/e2e_seed.py)  →  PostgreSQL  →  control plane (uvicorn)  →  Next.js  →  HTML
 *
 * The seed writes ONE community, ONE thread, ONE profile version and ONE `ENGAGE` judgment — and
 * deliberately no `IGNORE` judgment, no picture and no feedback. Three of the assertions below are
 * therefore about MEASURED EMPTIES: the read happened, the answer was empty, and the screen says
 * "brak" rather than drawing nothing (L5241 point 5). The fourth thing the suite does is the one
 * thing no unit test can: it SUBMITS the feedback form the way a browser without JavaScript would
 * (a `application/x-www-form-urlencoded` POST to the dashboard's own route handler), follows the
 * 303, and looks for the verdict in the HTML the server produces from the row the control plane
 * wrote — and for the audit event written in the same transaction.
 *
 * WHY THE FEEDBACK FORM AND NOT "ENABLE COMMUNITY". Enabling a community puts a poll job in the
 * queue, and the dispatcher inside the control plane process would then read the real Hacker News
 * from inside a test run. Feedback is a pure database write with an audit row: the whole effect is
 * observable through this dashboard, and nothing leaves the loopback interface.
 *
 * A MISSING ENVIRONMENT IS A FAILURE, NEVER A SKIP — see the other e2e file for why, and
 * `tools/verify_run.mjs` for the second lock on it.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_COMMUNITY_ID",
  "WENEXT_E2E_JUDGMENT_ID",
  "WENEXT_E2E_THREAD_TITLE",
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
  if (pathname.startsWith("http")) {
    return pathname;
  }
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

interface Fetched {
  readonly status: number;
  readonly location: string | null;
  /** The served HTML with React's empty text separators (`<!-- -->`) removed — see `served`. */
  readonly body: string;
  readonly setCookie: readonly string[];
}

/**
 * React Server Components separate adjacent text nodes with `<!-- -->`, so "Osądy IGNORE: brak"
 * arrives as `Osądy IGNORE<!-- -->: brak`. MEASURED on the first run of this file: three
 * assertions on sentences failed against markup that a browser renders exactly as the sentence.
 * Stripping the separators makes the assertions about what the operator reads, not about how the
 * framework serialised it. Nothing else is touched — attributes, tags and order stay as served.
 */
function served(html: string): string {
  return html.replace(/<!--\s*-->/g, "");
}

async function get(pathname: string, cookie?: string): Promise<Fetched> {
  const response = await fetch(url(pathname), {
    headers: cookie === undefined ? {} : { cookie },
    redirect: "manual",
  });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: served(await response.text()),
    setCookie: response.headers.getSetCookie(),
  };
}

/** A form POST exactly as a browser without JavaScript sends it. */
async function postForm(pathname: string, fields: Record<string, string>, cookie?: string): Promise<Fetched> {
  const response = await fetch(url(pathname), {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      ...(cookie === undefined ? {} : { cookie }),
    },
    body: new URLSearchParams(fields).toString(),
    redirect: "manual",
  });
  return {
    status: response.status,
    location: response.headers.get("location"),
    body: served(await response.text()),
    setCookie: response.headers.getSetCookie(),
  };
}

function handleCookieFrom(response: Fetched): string {
  const header = response.setCookie.find((value) => value.startsWith("wenext_dashboard_handle="));
  if (header === undefined) {
    throw new Error(
      `logowanie nie odesłało ciasteczka uchwytu; nagłówki: ${JSON.stringify(response.setCookie)}`,
    );
  }
  return header.split(";")[0] ?? "";
}

/** The markup of ONE judgment card, cut out of the page by its `data-judgment` marker. */
function cardOf(body: string, judgmentId: string): string {
  const marker = body.indexOf(`data-judgment="${judgmentId}"`);
  expect(marker, `w HTML-u nie ma karty osądu ${judgmentId}`).toBeGreaterThan(-1);
  // The marker sits INSIDE the opening `<li …>`, after `class` and `data-decision` (React writes
  // attributes in JSX order), so the card begins at the last `<li` before it — measured on the
  // first run, where a slice starting at the marker lost `data-decision`.
  const start = body.lastIndexOf("<li", marker);
  expect(start, "znacznik osądu nie siedzi w <li>").toBeGreaterThan(-1);
  const end = body.indexOf("</li>", marker);
  expect(end, "karta osądu nie jest zamknięta").toBeGreaterThan(marker);
  return body.slice(start, end);
}

/** The `value` of every `<button name="verdict">` in a card, in document order. */
function verdictButtonsOf(card: string): readonly string[] {
  return Array.from(card.matchAll(/<button\b[^>]*>/g))
    .map((match) => match[0])
    .filter((tag) => tag.includes('name="verdict"'))
    .map((tag) => /value="([^"]+)"/.exec(tag)?.[1] ?? "");
}

/** The number a totals tile shows: `<li … data-total="X" …><b class="stat__value">N</b>`. */
function totalOf(body: string, field: string): number {
  const match = new RegExp(`data-total="${field}"[^>]*>\\s*<b class="stat__value">(\\d+)</b>`).exec(body);
  expect(match, `brak kafelka data-total="${field}"`).not.toBeNull();
  return Number(match?.[1]);
}

let cookie = "";

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

  const login = await postForm("/login/submit", { password: required("WENEXT_E2E_OPERATOR_PASSWORD") });
  expect(login.status, `logowanie zwróciło ${login.status}, ciało ${login.body.slice(0, 200)}`).toBe(303);
  cookie = handleCookieFrom(login);
});

describe("/community against real PostgreSQL", () => {
  it("renders_the_community_thread_and_judgment_inserted_straight_into_postgres", async () => {
    const communityId = required("WENEXT_E2E_COMMUNITY_ID");
    const judgmentId = required("WENEXT_E2E_JUDGMENT_ID");
    const page = await get("/community", cookie);

    expect(page.status).toBe(200);
    // Size and markers first: an error page satisfies every negative assertion below.
    expect(page.body.length).toBeGreaterThan(1000);
    expect(page.body).toContain('data-screen="community"');
    // None of the five reads failed — every section is a fact, not a "brak odpowiedzi".
    expect(page.body).not.toContain('data-state="brak odpowiedzi"');

    // The community card, by the identifier PostgreSQL generated for the seeded row.
    expect(page.body).toContain(`data-community="${communityId}"`);
    // Przecinek, nie długa pauza: recenzja renderu 2026-09-19 znalazła tę pauzę w zasiewie,
    // czyli w treści, którą ekran pokazuje człowiekowi. Zasiew poprawiony, asercja za nim.
    expect(page.body).toContain("Hacker News, Ask HN (e2e)");
    expect(page.body).toContain("obserwacja włączona");
    // Counts as tiles, each a measured number: one thread, one judgment, zero of the rest.
    // Declined for the number: one thread is "1 wątek", zero pictures are "0 obrazów"
    // (docs/DASHBOARD-DESIGN.md §1 zasada nr 1, punkt 6 recenzji iteracji 2).
    expect(page.body).toMatch(/<b class="stat__value">1<\/b>\s*<span class="stat__label">wątek/);
    expect(page.body).toMatch(/<b class="stat__value">1<\/b>\s*<span class="stat__label">osąd</);
    expect(page.body).toMatch(/<b class="stat__value">0<\/b>\s*<span class="stat__label">obrazów/);

    // The judgment card: the thread title written by the seed, the decision, the contribution.
    const card = cardOf(page.body, judgmentId);
    expect(card).toContain('data-decision="ENGAGE"');
    expect(card).toContain(required("WENEXT_E2E_THREAD_TITLE"));
    expect(card).toContain("Liczby z roku produkcji (e2e).");
    expect(card).toContain('data-feedback="none"');

    // Totals: one ENGAGE, nothing else, no answers yet.
    expect(totalOf(page.body, "decision_ENGAGE")).toBe(1);
    expect(totalOf(page.body, "decision_IGNORE")).toBe(0);
    expect(totalOf(page.body, "decision_WAIT")).toBe(0);
    expect(totalOf(page.body, "feedback_AGREE")).toBe(0);

    // The profile is the seeded version, prefilled into the form for the next version.
    expect(page.body).toContain('data-profile="present"');
    expect(page.body).toContain("Profil v1");
    expect(page.body).toContain("kolejki na Postgresie (e2e)");
  });

  it("every_judgment_card_carries_the_three_feedback_buttons_posting_to_the_dashboard_handler", async () => {
    const page = await get("/community", cookie);
    const card = cardOf(page.body, required("WENEXT_E2E_JUDGMENT_ID"));

    expect(card).toContain('action="/community/feedback"');
    expect(card).toContain('method="post"');
    expect(card).toContain(`name="judgment_id" value="${required("WENEXT_E2E_JUDGMENT_ID")}"`);
    // Attribute order in the tag is React's, not ours; the assertion is on the set and the order
    // of the buttons themselves.
    expect(verdictButtonsOf(card)).toEqual(["AGREE", "DISAGREE", "IRRELEVANT"]);
    // And the whole screen needs no browser code: no inline handlers, no client bundle markers.
    expect(page.body).not.toMatch(/\son(click|submit|change)=/i);
  });

  it("measured_empties_say_brak_instead_of_drawing_nothing", async () => {
    // The seed wrote no IGNORE judgment and no picture. Both reads SUCCEEDED and came back empty,
    // and the screen has to say so in words — a blank section is indistinguishable from a section
    // that failed to load.
    const page = await get("/community", cookie);

    expect(page.body).toContain("Ostatnie osądy IGNORE: brak");
    expect(page.body).toContain('data-picture="none"');
    expect(page.body).toContain("jeszcze bez obrazu");
    // Empty is not "unread": no section reports a failed read.
    expect(page.body).not.toContain('data-state="brak odpowiedzi"');
    // And no card pretends to be an IGNORE judgment.
    expect(page.body).not.toContain('data-decision="IGNORE"');
  });

  it("the_forms_of_the_screen_post_only_to_allowed_handlers_and_the_capsule_says_it_is_absent", async () => {
    // FIVE forms are always on this screen; the capsule's two appear only when a capsule is
    // configured, and in this harness none is — the control plane runs from the repository with no
    // `WENEXT_BROWSER_READER_URL`. So the assertion is a SUBSET plus a closed set, not a count:
    // a count would fail on the founder's machine (where the capsule exists) or here, never both.
    const page = await get("/community", cookie);
    const actions = Array.from(page.body.matchAll(/<form[^>]*action="([^"]+)"/g)).map((m) => m[1]);
    // `/community/own-posts` to decyzja o własnych wątkach z P4 C (migracja 0025): stoi przy
    // każdej społeczności, tak jak `enable`, `profile` i `feedback`, i nie zależy od kapsuły.
    const always = [
      "/logout",
      "/community/enable",
      "/community/profile",
      "/community/feedback",
      "/community/resume",
      "/community/own-posts",
    ];
    const allowed = new Set([...always, "/community/capsule/open", "/community/capsule/confirm"]);

    expect(actions.length).toBeGreaterThanOrEqual(always.length);
    for (const action of always) {
      expect(actions, action).toContain(action);
    }
    for (const action of actions) {
      expect(allowed, `formularz kieruje do ${action}`).toContain(action);
    }

    // And the capsule card is there, saying what it measured: nothing is configured, so it offers
    // nothing to press. That is a FACT of this environment, not an empty section.
    expect(page.body).toContain('data-capsule="SIGNED_OUT"');
    expect(page.body).toContain("nie skonfigurowana");
  });

  it("the_community_screen_redirects_without_a_session_and_leaks_nothing", async () => {
    const page = await get("/community");

    expect([302, 303, 307, 308], `zwróciło ${page.status}`).toContain(page.status);
    expect(page.location ?? "").toContain("/login");
    for (const secret of [
      required("WENEXT_E2E_COMMUNITY_ID"),
      required("WENEXT_E2E_JUDGMENT_ID"),
      required("WENEXT_E2E_THREAD_TITLE"),
    ]) {
      expect(page.body, `wyciekło ${secret.slice(0, 12)}…`).not.toContain(secret);
    }
    expect(page.body).not.toContain('data-screen="community"');
    expect(page.body).not.toContain("data-judgment=");
    expect(page.body).not.toContain("data-total=");
  });

  it("the_feedback_form_posts_without_javascript_and_the_verdict_comes_back_from_postgres", async () => {
    const judgmentId = required("WENEXT_E2E_JUDGMENT_ID");

    // 1. The POST a browser without JavaScript sends when the operator presses "Tak, …".
    const submitted = await postForm(
      "/community/feedback",
      { judgment_id: judgmentId, verdict: "AGREE", note: "e2e-notatka" },
      cookie,
    );
    expect(submitted.status, `handler odpowiedział ${submitted.status}: ${submitted.body.slice(0, 200)}`).toBe(303);
    expect(submitted.location ?? "").toContain("/community?info=feedback_saved");

    // 2. Following the redirect, as a browser would: the flash sentence the SERVER chose.
    const after = await get(submitted.location ?? "/community", cookie);
    expect(after.status).toBe(200);
    expect(after.body).toContain('data-flash="tak"');
    expect(after.body).toContain("Odpowiedź zapisana obok osądu.");

    // 3. The verdict, read back from the row the control plane wrote — not echoed from the form.
    const card = cardOf(after.body, judgmentId);
    expect(card).toContain('data-feedback="AGREE"');
    expect(card).toContain("Twoja odpowiedź: <strong>AGREE</strong>. Notatka: e2e-notatka");
    expect(card).not.toContain('data-feedback="none"');
    // The totals move with it: the first answer of the flywheel.
    expect(totalOf(after.body, "feedback_AGREE")).toBe(1);
    expect(totalOf(after.body, "feedback_DISAGREE")).toBe(0);

    // 4. The audit row written in the SAME transaction (api/judgments.py), visible through the
    // other read path of this dashboard. A feedback without its audit entry would be exactly the
    // asymmetry the control plane's AuditWriter exists to prevent.
    const audit = await get("/audit", cookie);
    expect(audit.status).toBe(200);
    expect(audit.body).toContain("JUDGMENT_FEEDBACK_RECORDED");
    expect(audit.body).toContain(`judgment:${judgmentId}`);
  });

  it("feedback_for_an_unknown_judgment_is_refused_and_writes_nothing", async () => {
    // The negative control for the test above: a judgment the control plane does not know must
    // come back as a refusal sentence, and the totals must not move.
    const unknown = "00000000-0000-4000-8000-000000000000";
    const submitted = await postForm(
      "/community/feedback",
      { judgment_id: unknown, verdict: "AGREE", note: "" },
      cookie,
    );
    expect(submitted.status).toBe(303);
    expect(submitted.location ?? "").toContain("/community?info=feedback_refused");

    const after = await get(submitted.location ?? "/community", cookie);
    expect(after.status).toBe(200);
    expect(after.body).toContain("Control plane odrzucił odpowiedź");
    expect(totalOf(after.body, "feedback_AGREE")).toBe(1);
  });

  it("the_capsule_handlers_refuse_without_a_session_and_answer_a_sentence_with_one", async () => {
    // The write side of the capsule, end to end. In this harness no capsule is configured (the
    // control plane runs from the repository with no reader URL), so the control plane refuses and
    // the handlers must turn that into a SENTENCE — not a blank page, not a jump anywhere.
    for (const pathname of ["/community/capsule/open", "/community/capsule/confirm"]) {
      const anonymous = await postForm(pathname, {});
      expect([302, 303, 307, 308], `${pathname} bez sesji zwróciło ${anonymous.status}`).toContain(anonymous.status);
      expect(anonymous.location ?? "", pathname).toContain("/login");
      expect(anonymous.body, pathname).not.toContain("login-capsule");
    }

    const opened = await postForm("/community/capsule/open", {}, cookie);
    expect(opened.status).toBe(303);
    // Whatever happens, the browser is NOT sent outside this dashboard when there is no capsule.
    expect(opened.location ?? "").toContain("/community?info=");
    expect(opened.location ?? "").not.toContain("login-capsule");

    const confirmed = await postForm("/community/capsule/confirm", {}, cookie);
    expect(confirmed.status).toBe(303);
    expect(confirmed.location ?? "").toContain("/community?info=");

    // And the sentences come from the closed set of `app/community/page.tsx` — a code the page does
    // not know would render as no message at all, which is how a silent failure looks.
    const known = [
      "capsule_open_refused",
      "capsule_open_blocked",
      "capsule_confirm_failed",
      "capsule_confirm_pending",
      "capsule_signed_in",
      "capsule_signed_out",
      "unreachable",
    ];
    for (const response of [opened, confirmed]) {
      const info = new URL(response.location ?? "", "http://x").searchParams.get("info") ?? "";
      expect(known, `nieznany kod ?info=${info}`).toContain(info);
      const page = await get(`/community?info=${info}`, cookie);
      expect(page.status).toBe(200);
      expect(page.body, info).toContain('data-flash="tak"');
    }
  });

  it("the_feedback_handler_redirects_to_login_without_a_session_and_writes_nothing", async () => {
    // The write side of the boundary. Without the handle, the handler must not reach the control
    // plane at all — and the totals, read with a valid session afterwards, prove it did not.
    const submitted = await postForm("/community/feedback", {
      judgment_id: required("WENEXT_E2E_JUDGMENT_ID"),
      verdict: "DISAGREE",
      note: "",
    });
    expect([302, 303, 307, 308]).toContain(submitted.status);
    expect(submitted.location ?? "").toContain("/login");

    const page = await get("/community", cookie);
    expect(totalOf(page.body, "feedback_DISAGREE")).toBe(0);
    expect(totalOf(page.body, "feedback_AGREE")).toBe(1);
  });
});
