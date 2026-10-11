// @vitest-environment node
/**
 * The two handlers behind connecting an account from its card (26.09), driven through their real
 * code: a real `Request`, the real handler, the real control plane client. Only the two things that
 * live outside this process are doubled, the cookie jar and the control plane's HTTP answers.
 *
 * WHY A TEST OF THE GLUE AND NOT ONLY OF THE PARTS. The bug this work replaces lived exactly between
 * two correct parts: the card posted `polacz`, the handler called the control plane, the client kept
 * `detail`, and the `href` the control plane returned went nowhere. Every part had a test and the
 * person was still never shown a login screen (`docs/KONWENCJE.md`: "Klej między sprawdzonymi
 * częściami"). The double here is deliberately dumb: it answers by method and path and records
 * what it was sent, so a wrong path, a wrong body or a dropped field fails loudly.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jar = vi.hoisted(() => ({ handle: undefined as string | undefined }));

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => (jar.handle === undefined ? undefined : { value: jar.handle }),
  }),
}));

import { POST as openAgentBrowser } from "../app/studio/capsule/open/route";
import { POST as cardAction } from "../app/studio/card/route";
import { CAPSULE_VIEWER_ORIGIN_VARIABLE, openDecision } from "../lib/capsule-flow";
import {
  CAPSULE_CONFIRM_TIMEOUT_MS,
  CONTROL_PLANE_URL_VARIABLE,
  REQUEST_TIMEOUT_MS,
} from "../lib/control-plane";
import { clearDashboardSessions, openDashboardSession } from "../lib/session";
import { STUDIO_INFO } from "../lib/studio-info";

const ORIGIN = "http://127.0.0.1:8760";
const TICKET = `${ORIGIN}/login-capsule#ticket=abc`;
const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";
const PROJECT = "bbbbbbbb-0000-4000-8000-000000000001";
const SESSION = "cccccccc-0000-4000-8000-000000000001";
const CONVERSATION = `/w/${WORKSPACE}/p/${PROJECT}/c/${SESSION}`;
const MESSAGE_PATH = `/api/workspaces/${WORKSPACE}/projects/${PROJECT}/sessions/${SESSION}/message`;
/** U+2013 and U+2014, built from their codes so this file does not contain them itself. */
const LONG_DASHES = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`);

type Reply = { readonly status: number; readonly body: unknown } | "timeout";

interface Seen {
  readonly key: string;
  readonly body: unknown;
}

let seen: Seen[] = [];

/** The control plane, answering by `METHOD /path`. Anything unlisted is a loud 404. */
function controlPlane(routes: Record<string, Reply>): void {
  seen = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${new URL(String(input)).pathname}`;
      seen.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      const reply = routes[key] ?? { status: 404, body: { detail: `atrapa nie zna ${key}` } };
      if (reply === "timeout") {
        const stopped = new Error("The operation was aborted due to timeout");
        stopped.name = "TimeoutError";
        throw stopped;
      }
      return new Response(JSON.stringify(reply.body), {
        status: reply.status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

const SIGNED_IN = {
  "GET /api/session": {
    status: 200,
    body: { operator_id: "op", organization_id: "org", expires_at: "2026-09-27T00:00:00Z" },
  },
} as const;

function form(path: string, fields: Record<string, string>): Request {
  return new Request(`http://dashboard.test${path}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields).toString(),
  });
}

function sent(key: string): readonly unknown[] {
  return seen.filter((one) => one.key === key).map((one) => one.body);
}

beforeEach(() => {
  vi.stubEnv(CONTROL_PLANE_URL_VARIABLE, "http://control-plane.test");
  vi.stubEnv(CAPSULE_VIEWER_ORIGIN_VARIABLE, ORIGIN);
  jar.handle = openDashboardSession("control-plane-token");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  clearDashboardSessions();
  jar.handle = undefined;
});

describe("POST /studio/capsule/open", () => {
  const open = (fields: Record<string, string>) =>
    openAgentBrowser(
      form("/studio/capsule/open", {
        workspace_id: WORKSPACE,
        project_id: PROJECT,
        session_id: SESSION,
        karta_id: "konto:X:anna",
        return_to: CONVERSATION,
        ...fields,
      }),
    );

  it("sends_the_new_tab_to_the_agents_browser_with_the_platform_the_card_named", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/capsule/session": {
        status: 200,
        body: { href: TICKET, ticket_expires_at: "2026-09-26T12:02:00Z", status: "AWAITING_HUMAN" },
      },
    });
    const response = await open({ platform: "X" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(TICKET);
    // The body the control plane reads, spelled as it reads it.
    expect(sent("POST /api/capsule/session")).toEqual([{ platform: "X" }]);
  });

  it("a_card_that_named_no_platform_opens_reddit", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/capsule/session": {
        status: 200,
        body: { href: TICKET, ticket_expires_at: "2026-09-26T12:02:00Z", status: "AWAITING_HUMAN" },
      },
    });
    const response = await open({ platform: "" });
    expect(response.headers.get("location")).toBe(TICKET);
    expect(sent("POST /api/capsule/session")).toEqual([{ platform: "REDDIT" }]);
  });

  it("a_platform_outside_the_set_is_refused_before_the_control_plane_is_asked", async () => {
    controlPlane({ ...SIGNED_IN });
    const response = await open({ platform: "LINKEDIN" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=capsule_open_refused`);
    expect(sent("POST /api/capsule/session")).toEqual([]);
  });

  it("a_refusal_lands_in_the_new_tab_as_a_sentence_on_the_conversation", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/capsule/session": { status: 409, body: { detail: "kapsuła nie jest skonfigurowana" } },
    });
    const response = await open({ platform: "X" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=capsule_open_refused`);
  });

  it("a_ticket_outside_the_configured_origin_is_never_followed", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/capsule/session": {
        status: 200,
        body: { href: "http://evil.example/x", ticket_expires_at: "x", status: "AWAITING_HUMAN" },
      },
    });
    const response = await open({ platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=capsule_open_blocked`);
  });

  it("a_return_address_off_this_origin_is_replaced_by_the_studio_root", async () => {
    controlPlane({ ...SIGNED_IN });
    const response = await open({ platform: "LINKEDIN", return_to: "//evil.example/x" });
    expect(response.headers.get("location")).toBe("/?info=capsule_open_refused");
  });

  it("without_a_session_the_tab_goes_to_the_login_screen", async () => {
    jar.handle = undefined;
    controlPlane({});
    const response = await open({ platform: "X" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/login");
    expect(seen).toEqual([]);
  });

  it("every_sentence_the_open_decision_can_choose_exists_in_the_studio", () => {
    // The handler passes `openDecision`'s key straight through. A key with no sentence here would
    // be a flash that silently renders nothing, in a tab the person did not expect to have.
    const keys = [
      openDecision({ ok: false, unauthorized: false, reason: "503" }, ORIGIN),
      openDecision(
        { ok: true, value: { href: "http://evil.example/", ticket_expires_at: "x", status: "x" } },
        ORIGIN,
      ),
    ].map((decision) => (decision.kind === "info" ? decision.info : "not-info"));
    expect(keys).toEqual(["capsule_open_refused", "capsule_open_blocked"]);
    for (const key of keys) expect(STUDIO_INFO[key], key).toBeDefined();
  });
});

describe("POST /studio/card with the session check (sprawdz)", () => {
  const check = (sciezka: string, payload: Record<string, unknown>) =>
    cardAction(
      form("/studio/card", {
        workspace_id: WORKSPACE,
        project_id: PROJECT,
        session_id: SESSION,
        karta_id: "konto:X:anna",
        akcja: "sprawdz",
        metoda: "POST",
        sciezka,
        payload: JSON.stringify(payload),
        return_to: CONVERSATION,
      }),
    );

  it("a_connected_account_continues_the_conversation_with_one_sentence", async () => {
    const waits = vi.spyOn(AbortSignal, "timeout");
    controlPlane({
      ...SIGNED_IN,
      "POST /api/x/session/confirm": { status: 200, body: { polaczone: true, konto: "anna" } },
      [`POST ${MESSAGE_PATH}`]: { status: 200, body: {} },
    });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=account_connected`);
    // The payload is the card's, unchanged: nothing from the screen but `text`, and no `text` here.
    expect(sent("POST /api/x/session/confirm")).toEqual([{ platform: "X" }]);
    expect(sent(`POST ${MESSAGE_PATH}`)).toEqual([
      { text: "Konto X jest połączone (@anna). Kontynuuj pracę." },
    ]);
    // The check waited as long as the Operations confirm does, not the default five seconds.
    expect(waits).toHaveBeenCalledWith(CAPSULE_CONFIRM_TIMEOUT_MS);
  });

  it("a_reddit_check_names_reddit_and_writes_the_handle_without_an_at", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/capsule/session/confirm": {
        status: 200,
        body: { polaczone: true, konto: "anna" },
      },
      [`POST ${MESSAGE_PATH}`]: { status: 200, body: {} },
    });
    const response = await check("/api/capsule/session/confirm", {});
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=account_connected`);
    expect(sent(`POST ${MESSAGE_PATH}`)).toEqual([
      { text: "Konto Reddit jest połączone (anna). Kontynuuj pracę." },
    ]);
  });

  it("a_connected_answer_without_an_account_still_reads_as_one_sentence", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/x/session/confirm": { status: 200, body: { polaczone: true } },
      [`POST ${MESSAGE_PATH}`]: { status: 200, body: {} },
    });
    await check("/api/x/session/confirm", { platform: "X" });
    expect(sent(`POST ${MESSAGE_PATH}`)).toEqual([
      { text: "Konto X jest połączone. Kontynuuj pracę." },
    ]);
  });

  it("a_check_that_found_nobody_signed_in_says_so_and_tells_the_agent_nothing", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/x/session/confirm": { status: 200, body: { polaczone: false, konto: null } },
    });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=account_not_connected`);
    expect(sent(`POST ${MESSAGE_PATH}`)).toEqual([]);
  });

  it("an_answer_that_does_not_say_is_neither_success_nor_failure", async () => {
    // An older backend without `polaczone`. "Not connected" here would send the person to sign
    // in again after a sign-in that worked; "connected" would be a claim nobody measured.
    controlPlane({ ...SIGNED_IN, "POST /api/x/session/confirm": { status: 200, body: {} } });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=card_done`);
    expect(sent(`POST ${MESSAGE_PATH}`)).toEqual([]);
  });

  it("a_check_that_outlived_the_wait_says_it_may_have_worked", async () => {
    controlPlane({ ...SIGNED_IN, "POST /api/x/session/confirm": "timeout" });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=account_check_pending`);
  });

  it("a_message_that_did_not_arrive_is_said_plainly", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/x/session/confirm": { status: 200, body: { polaczone: true, konto: "anna" } },
      [`POST ${MESSAGE_PATH}`]: { status: 500, body: { detail: "boom" } },
    });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=account_connected_unsent`);
  });

  it("a_refused_check_is_a_refusal", async () => {
    controlPlane({
      ...SIGNED_IN,
      "POST /api/x/session/confirm": { status: 409, body: { detail: "Kapsuła nie jest skonfigurowana" } },
    });
    const response = await check("/api/x/session/confirm", { platform: "X" });
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=card_refused`);
  });

  it("only_a_session_check_gets_the_long_wait", async () => {
    const waits = vi.spyOn(AbortSignal, "timeout");
    controlPlane({ ...SIGNED_IN, "POST /api/communities": { status: 200, body: {} } });
    const response = await cardAction(
      form("/studio/card", {
        workspace_id: WORKSPACE,
        project_id: PROJECT,
        session_id: SESSION,
        karta_id: "spolecznosc:REDDIT:r/SaaS",
        akcja: "obserwuj",
        metoda: "POST",
        sciezka: "/api/communities",
        payload: JSON.stringify({ platform: "REDDIT", community_key: "SaaS" }),
        return_to: CONVERSATION,
      }),
    );
    expect(response.headers.get("location")).toBe(`${CONVERSATION}?info=card_done`);
    expect(waits).toHaveBeenCalledWith(REQUEST_TIMEOUT_MS);
    expect(waits).not.toHaveBeenCalledWith(CAPSULE_CONFIRM_TIMEOUT_MS);
  });

  it("every_key_these_handlers_leave_behind_has_a_sentence", () => {
    for (const key of [
      "account_connected",
      "account_connected_unsent",
      "account_not_connected",
      "account_check_pending",
      "capsule_open_refused",
      "capsule_open_blocked",
    ]) {
      const message = STUDIO_INFO[key];
      expect(message, key).toBeDefined();
      // No long dashes in text a person reads (the founder's rule for every sentence).
      expect(message?.text, key).not.toMatch(LONG_DASHES);
    }
  });
});
