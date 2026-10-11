/**
 * The client, tested on what it SENDS and on what it does with what comes back.
 *
 * Two families of promise live here, and both are the kind that only a test can hold:
 *
 *   1. **PROMISES ABOUT THE REQUEST.** "The audit read applies no filter" is a claim about a URL,
 *      and no amount of looking at the rendered page can prove it: a page full of rows looks
 *      identical whether or not a whole category was filtered out server-side. So the URL is
 *      captured and asserted.
 *   2. **PROMISES ABOUT FAILURE.** Every reader must fail LOUDLY rather than return an empty list.
 *      A 500, a body that is not JSON, a `jobs` field that is not an array, a renamed column — each
 *      of them must produce `{ ok: false }` with a reason, because `[]` handed to the screen is
 *      indistinguishable from "the queue is empty" (L5241 point 5, D-011).
 *
 * `fetch` is replaced, not intercepted at the socket: the unit under test is this module's mapping
 * from HTTP to `Read<T>`, and a real server would add a second thing that could be wrong. The real
 * server IS exercised — in `e2e/dashboard_reads_postgres.e2e.test.ts`, against real PostgreSQL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONTROL_PLANE_SESSION_COOKIE,
  CONTROL_PLANE_URL_VARIABLE,
  closeControlPlaneSession,
  controlPlaneUrl,
  getAuditEvents,
  getJob,
  getJobs,
  getSession,
  openControlPlaneSession,
} from "../lib/control-plane";

const BASE = "http://control-plane.test:8000";
const TOKEN = "token-operatora-abc";

interface Call {
  readonly url: string;
  readonly method: string;
  readonly headers: Record<string, string>;
  readonly body: string | undefined;
}

let calls: Call[] = [];
let originalFetch: typeof globalThis.fetch;
let originalUrl: string | undefined;

/** Replies with one canned response, recording what was asked for. */
function respondWith(
  status: number,
  body: unknown,
  setCookie: readonly string[] = [],
): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((value, key) => {
      headers[key] = value;
    });
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      headers,
      body: typeof init?.body === "string" ? init.body : undefined,
    });

    const responseHeaders = new Headers({ "content-type": "application/json" });
    for (const cookie of setCookie) {
      responseHeaders.append("set-cookie", cookie);
    }
    // 204 and 304 may not carry a body at all — `new Response("", { status: 204 })` throws in
    // undici, which would turn a canned reply into a fake network error and hide what the test
    // meant to measure. Measured while writing this file, not guessed.
    const payload =
      status === 204 || status === 304 || body === ""
        ? null
        : typeof body === "string"
          ? body
          : JSON.stringify(body);
    return new Response(payload, {
      status,
      headers: responseHeaders,
    });
  }) as typeof globalThis.fetch;
}

beforeEach(() => {
  calls = [];
  originalFetch = globalThis.fetch;
  originalUrl = process.env[CONTROL_PLANE_URL_VARIABLE];
  process.env[CONTROL_PLANE_URL_VARIABLE] = BASE;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalUrl === undefined) {
    delete process.env[CONTROL_PLANE_URL_VARIABLE];
  } else {
    process.env[CONTROL_PLANE_URL_VARIABLE] = originalUrl;
  }
});

describe("the control plane client", () => {
  it("a_missing_address_throws_instead_of_rendering_an_empty_screen", () => {
    // A misconfiguration must not look like an empty queue and must not look like a lost session.
    // `control_plane.auth.pool_for` raises for the same reason when DATABASE_URL is absent.
    delete process.env[CONTROL_PLANE_URL_VARIABLE];
    expect(() => controlPlaneUrl()).toThrow(new RegExp(CONTROL_PLANE_URL_VARIABLE));
    expect(() => controlPlaneUrl()).toThrow(/błąd konfiguracji/);
  });

  it("the_audit_read_sends_no_kind_and_no_since_filter", () => {
    // THE assertion of this file. A default filter would make a whole category of events invisible
    // with no error and no alarm (api/audit.py), and the rendered page cannot show that.
    respondWith(200, { events: [], limit: 200, returned: 0, truncated: false });
    return getAuditEvents(TOKEN).then((result) => {
      expect(result.ok).toBe(true);
      expect(calls.length).toBe(1);

      const url = new URL(calls[0]?.url ?? "");
      expect(url.pathname).toBe("/api/audit-events");
      expect(url.searchParams.get("kind")).toBeNull();
      expect(url.searchParams.get("since")).toBeNull();
      // `limit` is a page limit, which is not a filter: the response reports it and `truncated`.
      expect(url.searchParams.get("limit")).toBe("200");
      expect(Array.from(url.searchParams.keys())).toEqual(["limit"]);
    });
  });

  it("the_session_token_travels_as_a_cookie_header_and_never_in_the_url", () => {
    respondWith(200, { jobs: [], limit: 100, count: 0, truncated: false });
    return getJobs(TOKEN).then(() => {
      const call = calls[0];
      expect(call?.headers["cookie"]).toBe(`${CONTROL_PLANE_SESSION_COOKIE}=${TOKEN}`);
      // A token in a query string lands in every access log there is.
      expect(call?.url).not.toContain(TOKEN);
    });
  });

  it("a_401_is_reported_as_unauthorized_and_everything_else_is_not", async () => {
    respondWith(401, "");
    const refused = await getJobs(TOKEN);
    expect(refused.ok).toBe(false);
    expect(refused.ok === false && refused.unauthorized).toBe(true);

    respondWith(503, "");
    const broken = await getJobs(TOKEN);
    expect(broken.ok).toBe(false);
    // The two need different screens: "log in again" versus "the read did not happen". Collapsing
    // them would tell an operator their session ended when the API was simply down.
    expect(broken.ok === false && broken.unauthorized).toBe(false);
  });

  it("a_broken_response_is_a_failure_and_never_an_empty_list", async () => {
    const cases: readonly (readonly [string, unknown, number])[] = [
      ["ciało nie jest JSON-em", "to nie json", 200],
      ["brak pola jobs", { limit: 100 }, 200],
      ["jobs nie jest listą", { jobs: null, limit: 100 }, 200],
      ["element nie jest obiektem", { jobs: [42], limit: 100 }, 200],
      ["wiersz bez job_id", { jobs: [{ job_type: "X" }], limit: 100 }, 200],
      ["status 500", "", 500],
    ];
    expect(cases.length).toBe(6);

    for (const [name, body, status] of cases) {
      respondWith(status, body);
      const result = await getJobs(TOKEN);
      expect(result.ok, name).toBe(false);
      expect(result.ok === false && result.reason.length, name).toBeGreaterThan(0);
    }
  });

  it("a_row_that_loses_may_retry_automatically_fails_the_read_rather_than_defaulting_it", async () => {
    // The direction that matters. A missing flag must not become `null` (a manufactured UNKNOWN)
    // and must certainly not become a boolean (a manufactured measurement).
    const attempt: Record<string, unknown> = {
      attempt_id: "a",
      attempt_no: 1,
      dispatch_version: 1,
      lease_generation: 1,
      lease_owner: "o",
      status: "FAILED",
      not_before: null,
      heartbeat_at: null,
      expires_at: null,
      started_at: null,
      completed_at: null,
      failure_class: "SUBMIT_STARTED",
      execution_attempt_state: "SUBMIT_STARTED",
    };
    const job = {
      job_id: "j",
      job_type: "T",
      routing_key: "r",
      status: "FAILED",
      priority: 1,
      dispatch_version: 1,
      not_before: "2026-08-31T12:00:00+00:00",
      attempt_limit: 5,
      attempt_count: 1,
      lock_version: 1,
      created_at: "2026-08-31T12:00:00+00:00",
      holds_live_lease: false,
      workspace_id: null,
      payload_ref: null,
    };

    respondWith(200, { job, attempts: [attempt], attempt_limit: 5, attempts_used: 1 });
    const without = await getJob(TOKEN, "j");
    expect(without.ok).toBe(false);
    expect(without.ok === false && without.reason).toContain("may_retry_automatically");

    respondWith(200, {
      job,
      attempts: [{ ...attempt, may_retry_automatically: false }],
      attempt_limit: 5,
      attempts_used: 1,
    });
    const withFlag = await getJob(TOKEN, "j");
    expect(withFlag.ok).toBe(true);
    expect(withFlag.ok === true && withFlag.value?.attempts[0]?.may_retry_automatically).toBe(false);
  });

  it("a_404_on_the_detail_is_a_fact_not_a_failure", async () => {
    // "There is no such job" is a successful read whose answer is negative. It travels inside
    // `ok: true` as `null` so the screen can say so instead of claiming a broken read.
    respondWith(404, { detail: "nie ma joba" });
    const result = await getJob(TOKEN, "nie-ma");
    expect(result.ok).toBe(true);
    expect(result.ok === true && result.value).toBeNull();
  });

  it("a_login_without_a_set_cookie_header_is_a_failure", async () => {
    // 204 with no cookie would otherwise mint a dashboard session holding an empty token — a
    // handle that looks like being logged in and can read nothing.
    respondWith(204, "");
    const nothing = await openControlPlaneSession("hasło");
    expect(nothing.ok).toBe(false);
    expect(nothing.ok === false && nothing.reason).toContain("ciasteczka");

    respondWith(204, "", [
      `${CONTROL_PLANE_SESSION_COOKIE}=${TOKEN}; HttpOnly; Path=/; SameSite=strict`,
    ]);
    const opened = await openControlPlaneSession("hasło");
    expect(opened.ok).toBe(true);
    expect(opened.ok === true && opened.value).toBe(TOKEN);
    // The password goes in the body, and only the password: `api/session.py` refuses a body with
    // any additional field, which is where an identity would be smuggled next to it.
    expect(calls[calls.length - 1]?.body).toBe(JSON.stringify({ password: "hasło" }));
  });

  it("the_session_description_is_read_from_the_answer_not_from_the_caller", async () => {
    respondWith(200, {
      operator_id: "op-1",
      organization_id: "org-1",
      expires_at: "2026-08-31T20:00:00+00:00",
    });
    const session = await getSession(TOKEN);
    expect(session.ok).toBe(true);
    expect(session.ok === true && session.value.operator_id).toBe("op-1");

    respondWith(200, { operator_id: "op-1" });
    const partial = await getSession(TOKEN);
    expect(partial.ok).toBe(false);
  });

  it("logout_uses_delete_and_reports_a_refused_upstream", async () => {
    respondWith(204, "");
    const closed = await closeControlPlaneSession(TOKEN);
    expect(closed.ok).toBe(true);
    expect(calls[0]?.method).toBe("DELETE");
    expect(calls[0]?.url).toContain("/api/session");

    respondWith(500, "");
    const failed = await closeControlPlaneSession(TOKEN);
    expect(failed.ok).toBe(false);
  });

  it("a_network_error_is_a_reason_not_an_exception_thrown_at_the_page", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as typeof globalThis.fetch;

    const result = await getJobs(TOKEN);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("nie udało się połączyć");
    expect(result.ok === false && result.unauthorized).toBe(false);
  });

  it("every_read_asks_for_a_fresh_answer", async () => {
    // A cached page of jobs is a page of jobs that USED to be true, and the operator cannot tell.
    respondWith(200, { jobs: [], limit: 100, count: 0, truncated: false });
    await getJobs(TOKEN);
    const init = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    expect((init?.[1] as RequestInit).cache).toBe("no-store");
  });
});
