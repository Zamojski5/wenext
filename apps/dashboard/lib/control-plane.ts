/**
 * The dashboard's ONLY door to the control plane, and it is a server-side door.
 *
 * D-016 decides the shape: the operator session lives in an HttpOnly cookie that browser code
 * must never be able to read, and the control plane is not published to the host at all
 * (`docker-compose.yml`: service `api` has no `ports:`). The Next.js server is therefore its only
 * client, and every function here runs in a server component or a route handler. Two consequences
 * that must not be relaxed:
 *
 *   1. **No `NEXT_PUBLIC_*` variable, ever.** A value with that prefix is inlined into the
 *      browser bundle; the address of the private API would ship to the client and the client
 *      would be able to talk to it. The address arrives as `WENEXT_CONTROL_PLANE_URL`, spelled
 *      exactly as `docker-compose.yml` sets it for the `dashboard` service.
 *      `tests/no_browser_surface.test.ts` scans the package for the prefix and for `"use client"`.
 *   2. **The control plane's session cookie is passed as a header on THIS side.** It never leaves
 *      this process towards the browser — `lib/session.ts` gives the browser an opaque handle
 *      instead. That is asserted end to end by
 *      `e2e/dashboard_reads_postgres.e2e.test.ts::the_control_plane_session_token_never_appears_in_a_dashboard_response`.
 *
 * WHAT A FAILED READ RETURNS, AND WHY IT IS NOT AN EMPTY LIST. Every reader returns
 * `Read<T>` — `{ ok: true, value }` or `{ ok: false, reason }`. It never returns `[]` on failure,
 * never `null`, never a partially built object. An empty array handed to the screen is
 * indistinguishable from "the queue is empty", which is the whole failure this package exists to
 * prevent: `all([]) == True` moved into the visual layer (D-011, and L5241 point 5 — "brak danych
 * nigdy nie jest zamieniany na zero"). A response whose shape is wrong is a FAILURE too, not an
 * empty page: `asRecordArray` refuses anything that is not an array of objects.
 *
 * `unauthorized` is separated from every other failure because the two need different screens: a
 * session the control plane no longer knows means "log in again", while a refused connection
 * means "the read did not happen" and must never be dressed up as a login prompt.
 */
import type { AuditEvent, JobDetailRecord, JobSummary, SessionDescription } from "./schema";
import { parseAuditEvent, parseJobDetail, parseJobSummary, parseSessionDescription } from "./schema";

/** The result of one read. Never widened with a third "empty" case — see the module docstring. */
export type Read<T> =
  | { readonly ok: true; readonly value: T }
  | {
      readonly ok: false;
      readonly reason: string;
      readonly unauthorized: boolean;
      /**
       * `true` — THIS SIDE stopped waiting; the request may still be running upstream. It is not a
       * synonym for failure: a write that timed out may have completed, so a caller must not report
       * it as "nothing happened" (see `lib/capsule-flow.ts::confirmDecision`).
       */
      readonly timedOut?: boolean;
    };

/**
 * Env var carrying the control plane's base URL. NOT prefixed `NEXT_PUBLIC_`, and the name is the
 * one `docker-compose.yml` already sets — a second name would mean the container works and the
 * tests do not, or the reverse.
 */
export const CONTROL_PLANE_URL_VARIABLE = "WENEXT_CONTROL_PLANE_URL";

/** Name of the control plane's session cookie, as `control_plane.auth.SESSION_COOKIE_NAME`. */
export const CONTROL_PLANE_SESSION_COOKIE = "wenext_operator_session";

/**
 * How many jobs the queue screen asks for.
 *
 * Lower than the control plane's own `DEFAULT_PAGE_LIMIT` of 200 on purpose: `app/jobs/page.tsx`
 * makes ONE detail read per listed job to fill the lease columns (the list endpoint does not
 * carry them), so this number is also the number of extra requests one page view costs. The
 * response reports `truncated`, and the screen prints it, so a page that ends says "there may be
 * more" instead of "this is everything".
 */
export const JOBS_PAGE_LIMIT = 100;

/** How many audit events the log screen asks for. No `kind` and no `since` — see `getAuditEvents`. */
export const AUDIT_PAGE_LIMIT = 200;

/** Requests are answered on loopback in milliseconds; a read that hangs is a read that failed. */
export const REQUEST_TIMEOUT_MS = 5000;

/**
 * The configured base URL. THROWS when it is missing.
 *
 * A misconfiguration must not be rendered as an empty screen and must not be rendered as "please
 * log in". `control_plane.auth.pool_for` raises for exactly the same reason when `DATABASE_URL`
 * is absent: answering 401 there would make "the database is not connected" look like "your
 * password is wrong".
 */
export function controlPlaneUrl(): string {
  const configured = process.env[CONTROL_PLANE_URL_VARIABLE];
  if (configured === undefined || configured === "") {
    throw new Error(
      `brak ${CONTROL_PLANE_URL_VARIABLE}. Panel nie wie, gdzie jest control plane. To błąd ` +
        "konfiguracji, nie brak danych: pusty ekran w tym miejscu twierdziłby, że kolejka jest " +
        "pusta, a przekierowanie na logowanie twierdziłoby, że wygasła sesja",
    );
  }
  return configured.replace(/\/+$/, "");
}

interface RawResponse {
  readonly status: number;
  readonly body: string;
  readonly setCookie: readonly string[];
}

/** One HTTP call, with the session cookie if there is one. Network errors become failures. */
/**
 * Jedno wywołanie akcji karty (D-046): metoda, ścieżka i ciało PRZYCHODZĄ Z KARTY.
 *
 * DLACZEGO TO JEST TU, A NIE W HANDLERZE: `call` jest prywatne dla tego modułu, bo to ono zna
 * adres control plane'u i dokleja token (D-016: ani jedno, ani drugie nie ma prawa trafić do
 * kodu przeglądarki). Handler dostaje więc funkcję, a nie adres.
 *
 * SPRAWDZENIE ŚCIEŻKI JEST W HANDLERZE, NIE TUTAJ, i to jest świadome: tutaj nie wiadomo, o
 * którą przestrzeń chodzi, a reguła brzmi „ścieżka musi należeć do przestrzeni z formularza".
 */
export async function callCardAction(
  token: string,
  method: string,
  path: string,
  payload: Record<string, unknown>,
  options: {
    /** For the session checks, which read the platform and legitimately take up to a minute. */
    readonly timeoutMs?: number;
  } = {},
): Promise<Read<CardActionOutcome>> {
  const raw = await call(method, path, {
    token,
    body: JSON.stringify(payload),
    timeoutMs: options.timeoutMs,
  });
  if (!raw.ok) return raw;
  let detail = "";
  let inConversation: boolean | null = null;
  let connected: boolean | null = null;
  let account: string | null = null;
  try {
    const parsed = JSON.parse(raw.value.body) as unknown;
    if (typeof parsed === "object" && parsed !== null) {
      const row = parsed as Record<string, unknown>;
      if ("detail" in row) detail = String(row["detail"]);
      // Tylko wartość logiczna. Brak pola i cokolwiek innego znaczą „ta trasa tego nie mówi",
      // a nie „nie widać": zgadywanie tutaj zamieniłoby ciszę w zarzut.
      if (typeof row["w_rozmowie"] === "boolean") inConversation = row["w_rozmowie"];
      // The same rule for a session check (26.09): only a boolean is an answer. A missing or
      // mistyped `polaczone` means "this route does not say", never "not connected", or an older
      // backend would tell the person to sign in again after a sign-in that worked.
      if (typeof row["polaczone"] === "boolean") connected = row["polaczone"];
      const konto = row["konto"];
      if (typeof konto === "string" && konto.trim() !== "") account = konto.trim();
    }
  } catch {
    detail = raw.value.body.slice(0, 200);
  }
  return {
    ok: true,
    value: { status: raw.value.status, detail, inConversation, connected, account },
  };
}

async function call(
  method: string,
  path: string,
  options: {
    readonly token?: string;
    readonly body?: string;
    /** Overrides `REQUEST_TIMEOUT_MS` for the one call that legitimately takes minutes. */
    readonly timeoutMs?: number;
  } = {},
): Promise<Read<RawResponse>> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (options.token !== undefined) {
    headers["cookie"] = `${CONTROL_PLANE_SESSION_COOKIE}=${options.token}`;
  }
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${controlPlaneUrl()}${path}`, {
      method,
      headers,
      body: options.body,
      // `no-store` on every call: this screen shows the canonical state of a queue that changes
      // under it. A cached page of jobs is a page of jobs that USED to be true, and the operator
      // cannot tell the difference.
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(options.timeoutMs ?? REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    // `AbortSignal.timeout` rejects with a DOMException named `TimeoutError`; an abort from
    // anywhere else arrives as `AbortError`. Both mean the same thing to a caller: we stopped
    // waiting, and what the other side did with the request is unmeasured.
    const name = error instanceof Error ? error.name : "";
    const timedOut = name === "TimeoutError" || name === "AbortError";
    return {
      ok: false,
      unauthorized: false,
      timedOut,
      reason: timedOut
        ? `control plane nie odpowiedział w wyznaczonym czasie (${method} ${path}). Żądanie mogło ` +
          "zostać wykonane po tamtej stronie"
        : `nie udało się połączyć z control plane (${method} ${path}): ${String(error)}`,
    };
  }

  if (response.status === 401) {
    // The boundary answers 401 with an EMPTY body by design (D-016), so there is nothing to read
    // here and nothing to show the operator except "log in again".
    return { ok: false, unauthorized: true, reason: "control plane odmówił: brak ważnej sesji" };
  }

  return {
    ok: true,
    value: {
      status: response.status,
      body: await response.text(),
      setCookie: response.headers.getSetCookie(),
    },
  };
}

/** JSON body of a 200, or a failure. A non-200 and a malformed body are both failures. */
function decode(raw: RawResponse, what: string): Read<Record<string, unknown>> {
  if (raw.status !== 200) {
    return {
      ok: false,
      unauthorized: false,
      reason: `${what}: control plane odpowiedział ${raw.status}`,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.body);
  } catch (error) {
    return { ok: false, unauthorized: false, reason: `${what}: ciało nie jest JSON-em (${String(error)})` };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ok: false, unauthorized: false, reason: `${what}: ciało nie jest obiektem JSON` };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

/**
 * A field that must be an array of objects, or a FAILURE.
 *
 * Not "or an empty array". A response whose `jobs` field is missing, `null`, a string or an array
 * of numbers is a broken read, and rendering it as an empty queue is the exact substitution
 * L5241 point 5 forbids.
 */
function asRecordArray(
  envelope: Record<string, unknown>,
  field: string,
  what: string,
): Read<readonly Record<string, unknown>[]> {
  const raw = envelope[field];
  if (!Array.isArray(raw)) {
    return {
      ok: false,
      unauthorized: false,
      reason: `${what}: pole '${field}' nie jest listą, czyli to zepsuty odczyt, nie pusta lista`,
    };
  }
  for (const [index, item] of raw.entries()) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      return {
        ok: false,
        unauthorized: false,
        reason: `${what}: element ${index} pola '${field}' nie jest obiektem`,
      };
    }
  }
  return { ok: true, value: raw as readonly Record<string, unknown>[] };
}

/** A page of rows plus what the control plane said about the page itself. */
export interface Page<T> {
  readonly rows: readonly T[];
  readonly limit: number;
  /** `true` when the page filled up, i.e. there may be more. Never "this is everything". */
  readonly truncated: boolean;
}

function pageOf<T>(
  envelope: Record<string, unknown>,
  rows: readonly T[],
  fallbackLimit: number,
): Page<T> {
  return {
    rows,
    limit: typeof envelope["limit"] === "number" ? envelope["limit"] : fallbackLimit,
    truncated: envelope["truncated"] === true,
  };
}

/**
 * Present the password and come back with the control plane's session token.
 *
 * The token is a SECRET this process keeps: it goes into the server-side map in `lib/session.ts`
 * and the browser receives an unrelated handle. Returning it from here is what makes that
 * possible — and is also why nothing in `app/` may put the value into a response body.
 */
/**
 * P7: the three doors a person walks through before they have a session.
 *
 * All three answer the same way as the operator login above: a token in `Set-Cookie`, which this
 * server KEEPS and never forwards to the browser (D-016). The browser gets an unrelated handle
 * from `lib/session.ts`.
 */
async function sessionFrom(
  path: string,
  body: Record<string, string>,
  expected: number,
): Promise<Read<string>> {
  const raw = await call("POST", path, { body: JSON.stringify(body) });
  if (!raw.ok) return raw;
  if (raw.value.status !== expected) {
    // A 400 here carries a sentence the person needs to read (a taken address, an expired
    // invitation), so it travels as the reason rather than being flattened into "refused".
    let detail = "";
    try {
      const parsed = JSON.parse(raw.value.body) as { detail?: unknown };
      detail = typeof parsed.detail === "string" ? parsed.detail : "";
    } catch {
      detail = "";
    }
    return {
      ok: false,
      unauthorized: raw.value.status === 401,
      reason: detail || `control plane odpowiedział ${raw.value.status}`,
    };
  }
  const prefix = `${CONTROL_PLANE_SESSION_COOKIE}=`;
  for (const header of raw.value.setCookie) {
    if (!header.startsWith(prefix)) continue;
    const token = header.slice(prefix.length).split(";")[0] ?? "";
    if (token.length > 0) return { ok: true, value: token };
  }
  return {
    ok: false,
    unauthorized: false,
    reason: "control plane przyjął poświadczenie, ale nie odesłał ciasteczka sesji",
  };
}

/** Email and password of a user. */
export function openUserSession(email: string, password: string): Promise<Read<string>> {
  return sessionFrom("/api/auth/login", { email, password }, 200);
}

/**
 * Sesja właściciela bez hasła, gdy wdrożenie jest lokalne (tryb `WENEXT_LOCAL_AUTOLOGIN`).
 *
 * BEZ CIAŁA I BEZ NAZWY KONTA: użytkownika wybiera control plane po swojej zmiennej. Gdyby
 * konto szło stąd, ta trasa byłaby wyborem dowolnej tożsamości bez hasła.
 */
export function openLocalSession(): Promise<Read<string>> {
  return sessionFrom("/api/auth/local-session", {}, 200);
}

/** A new organization with its first owner. Self-service: no console, no founder. */
export function registerOrganization(
  email: string,
  password: string,
  organizationName: string,
): Promise<Read<string>> {
  return sessionFrom(
    "/api/auth/register",
    { email, password, organization_name: organizationName },
    201,
  );
}

/** An invitation token plus the password chosen by the person accepting it. */
export function acceptInvitation(token: string, password: string): Promise<Read<string>> {
  return sessionFrom("/api/auth/invitations/accept", { token, password }, 201);
}

export async function openControlPlaneSession(password: string): Promise<Read<string>> {
  const raw = await call("POST", "/api/session", { body: JSON.stringify({ password }) });
  if (!raw.ok) {
    return raw;
  }
  if (raw.value.status !== 204) {
    return {
      ok: false,
      unauthorized: false,
      reason: `logowanie: control plane odpowiedział ${raw.value.status}`,
    };
  }

  const prefix = `${CONTROL_PLANE_SESSION_COOKIE}=`;
  for (const header of raw.value.setCookie) {
    if (!header.startsWith(prefix)) {
      continue;
    }
    const token = header.slice(prefix.length).split(";")[0] ?? "";
    if (token.length === 0) {
      continue;
    }
    return { ok: true, value: token };
  }
  return {
    ok: false,
    unauthorized: false,
    reason: "logowanie: control plane nie odesłał ciasteczka sesji",
  };
}

/** End the control plane session server-side. Best effort — the local handle is dropped anyway. */
export async function closeControlPlaneSession(token: string): Promise<Read<null>> {
  const raw = await call("DELETE", "/api/session", { token });
  if (!raw.ok) {
    return raw;
  }
  if (raw.value.status !== 204) {
    return {
      ok: false,
      unauthorized: false,
      reason: `wylogowanie: control plane odpowiedział ${raw.value.status}`,
    };
  }
  return { ok: true, value: null };
}

/** Who the control plane says this token belongs to. A 401 here means "log in again". */
export async function getSession(token: string): Promise<Read<SessionDescription>> {
  const raw = await call("GET", "/api/session", { token });
  if (!raw.ok) {
    return raw;
  }
  const envelope = decode(raw.value, "sesja");
  if (!envelope.ok) {
    return envelope;
  }
  return parseSessionDescription(envelope.value);
}

/** One page of jobs, newest first, scoped by the control plane to the session's organization. */
export async function getJobs(token: string): Promise<Read<Page<JobSummary>>> {
  const raw = await call("GET", `/api/jobs?limit=${JOBS_PAGE_LIMIT}`, { token });
  if (!raw.ok) {
    return raw;
  }
  const envelope = decode(raw.value, "lista jobów");
  if (!envelope.ok) {
    return envelope;
  }
  const rows = asRecordArray(envelope.value, "jobs", "lista jobów");
  if (!rows.ok) {
    return rows;
  }

  const jobs: JobSummary[] = [];
  for (const row of rows.value) {
    const parsed = parseJobSummary(row);
    if (!parsed.ok) {
      // ONE unreadable row fails the WHOLE read. Skipping it would silently shorten the queue,
      // and a queue that is quietly one job shorter is worse than a queue that will not render.
      return parsed;
    }
    jobs.push(parsed.value);
  }
  return { ok: true, value: pageOf(envelope.value, jobs, JOBS_PAGE_LIMIT) };
}

/**
 * One job with the full history of its attempts, or `null` when there is no such job.
 *
 * `null` is a FACT (the control plane answered 404: no such job in this organization), which is
 * why it travels inside `ok: true`. It is not the same as a failed read and the screen says so.
 */
export async function getJob(token: string, jobId: string): Promise<Read<JobDetailRecord | null>> {
  const raw = await call("GET", `/api/jobs/${encodeURIComponent(jobId)}`, { token });
  if (!raw.ok) {
    return raw;
  }
  if (raw.value.status === 404) {
    return { ok: true, value: null };
  }
  const envelope = decode(raw.value, "szczegóły joba");
  if (!envelope.ok) {
    return envelope;
  }
  return parseJobDetail(envelope.value);
}

/**
 * The audit log, newest first, WITH NO FILTER AT ALL.
 *
 * The query string carries `limit` and nothing else — no `kind`, no `since`. That is the point
 * `api/audit.py` makes in its own docstring and this is the other half of it: "A default `kind`
 * filter is the most comfortable way to build a log nobody can trust — the page returns rows,
 * looks alive, and a whole category is invisible with no error and no alarm."
 * `tests/control_plane_client.test.ts::the_audit_read_sends_no_kind_and_no_since_filter` asserts
 * the URL, because this is a promise about a request, and only the request can prove it.
 */
export async function getAuditEvents(token: string): Promise<Read<Page<AuditEvent>>> {
  const raw = await call("GET", `/api/audit-events?limit=${AUDIT_PAGE_LIMIT}`, { token });
  if (!raw.ok) {
    return raw;
  }
  const envelope = decode(raw.value, "dziennik audytu");
  if (!envelope.ok) {
    return envelope;
  }
  const rows = asRecordArray(envelope.value, "events", "dziennik audytu");
  if (!rows.ok) {
    return rows;
  }

  const events: AuditEvent[] = [];
  for (const row of rows.value) {
    const parsed = parseAuditEvent(row);
    if (!parsed.ok) {
      return parsed;
    }
    events.push(parsed.value);
  }
  return { ok: true, value: pageOf(envelope.value, events, AUDIT_PAGE_LIMIT) };
}

// ---------------------------------------------------------------------------
// Outcome A — the observe module (`docs/OUTCOMES.md` §3). Reads and the FIRST writes of this
// dashboard beyond the session: enabling a community, saving the user's profile, answering a
// judgment. Every write is a form POST handled by a route handler under `app/community/`, which
// calls one of the functions below server-side — the browser never talks to the control plane.
// ---------------------------------------------------------------------------

import {
  parseCommunity,
  parseJudgment,
  parsePicture,
  parseProfile,
  parseTotals,
  type CommunityPicture,
  type CommunitySummary,
  type JudgmentSummary,
  type JudgmentTotals,
  type UserProfile,
} from "./observe-schema";

/** How many judgments the screen asks for per decision. Small on purpose: the screen is a review
 *  queue for one operator, and `truncated` says when there is more. */
export const JUDGMENTS_PAGE_LIMIT = 50;

function parseAll<T>(
  rows: readonly Record<string, unknown>[],
  parse: (row: Record<string, unknown>) => Read<T>,
): Read<readonly T[]> {
  const parsed: T[] = [];
  for (const row of rows) {
    const one = parse(row);
    if (!one.ok) return one;
    parsed.push(one.value);
  }
  return { ok: true, value: parsed };
}

export async function getCommunities(token: string): Promise<Read<readonly CommunitySummary[]>> {
  const raw = await call("GET", "/api/communities", { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "communities");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "communities", "communities");
  if (!rows.ok) return rows;
  return parseAll(rows.value, parseCommunity);
}

/** The latest picture of one community, `null` when the control plane says there is none yet (404). */
export async function getLatestPicture(
  token: string,
  communityId: string,
): Promise<Read<CommunityPicture | null>> {
  const raw = await call("GET", `/api/communities/${encodeURIComponent(communityId)}/picture`, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "picture");
  if (!envelope.ok) return envelope;
  return parsePicture(envelope.value);
}

export interface JudgmentsPage {
  readonly rows: readonly JudgmentSummary[];
  readonly truncated: boolean;
  readonly totals: JudgmentTotals;
}

export async function getJudgments(
  token: string,
  decision: "ENGAGE" | "IGNORE" | "WAIT" | null,
): Promise<Read<JudgmentsPage>> {
  const query = new URLSearchParams({ limit: String(JUDGMENTS_PAGE_LIMIT) });
  if (decision !== null) query.set("decision", decision);
  const raw = await call("GET", `/api/judgments?${query.toString()}`, { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "judgments");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "judgments", "judgments");
  if (!rows.ok) return rows;
  const parsed = parseAll(rows.value, parseJudgment);
  if (!parsed.ok) return parsed;
  const summary = envelope.value["summary"];
  if (typeof summary !== "object" || summary === null || Array.isArray(summary)) {
    return { ok: false, unauthorized: false, reason: "judgments: pole 'summary' nie jest obiektem" };
  }
  const totals = parseTotals(summary as Record<string, unknown>);
  if (!totals.ok) return totals;
  return {
    ok: true,
    value: { rows: parsed.value, truncated: envelope.value["truncated"] === true, totals: totals.value },
  };
}

/** The current profile, `null` when none was saved yet (404 from the control plane). */
export async function getUserProfile(token: string): Promise<Read<UserProfile | null>> {
  const raw = await call("GET", "/api/user-profile", { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "user-profile");
  if (!envelope.ok) return envelope;
  return parseProfile(envelope.value);
}

/** A write's result: the status the control plane answered with, and its body for the message. */
export interface WriteOutcome {
  readonly createdProjectId?: string;
  readonly status: number;
  readonly detail: string;
  /**
   * Czy skutek tej operacji WIDAĆ tam, gdzie człowiek go szuka. `null` znaczy „odpowiedź tego
   * nie mówi" i tak odpowiada większość tras.
   *
   * Dziś mówi to jedna: poprawka szkicu (`…/approvals/{rewizja}/revise`) zwraca `w_rozmowie`.
   * Powód jest z nocy 21.09: trasa zapisała nową rewizję i wywaliła się PO zapisie, więc
   * poprawka istniała w bazie, a w rozmowie nie było po niej śladu. Ekran pokazywał wtedy
   * „zlecone", czyli zdanie prawdziwe o wysłaniu i mylące o skutku. Z tym polem da się
   * powiedzieć „zapisane, tylko jeszcze tego nie widać", a to jest różnica, którą człowiek
   * musi znać, zanim zacznie poprawiać drugi raz.
   */
  readonly inConversation: boolean | null;
}

/**
 * A card action's outcome: the write's, plus what a session check answered (26.09).
 *
 * A SEPARATE TYPE, not two more fields on `WriteOutcome`: only `callCardAction` reads them, and
 * every other write would have to invent a value for a question its route never answers.
 */
export interface CardActionOutcome extends WriteOutcome {
  /**
   * `polaczone` from `/api/capsule/session/confirm` or `/api/x/session/confirm`: did the check
   * find the account connected. `null` when the answer does not say, which is every other card
   * route and an older backend, and which is NOT "not connected".
   */
  readonly connected: boolean | null;
  /** `konto` from the same answer: the account the check found signed in, or `null`. */
  readonly account: string | null;
}

async function write(token: string, method: string, path: string, body: unknown): Promise<Read<WriteOutcome>> {
  const raw = await call(method, path, { token, body: JSON.stringify(body) });
  if (!raw.ok) return raw;
  let detail = "";
  let createdProjectId: string | undefined;
  try {
    const parsed = JSON.parse(raw.value.body) as unknown;
    if (typeof parsed === "object" && parsed !== null && "project" in parsed) {
      const project = (parsed as { project: { project_id?: unknown } | null }).project;
      if (project && typeof project.project_id === "string") createdProjectId = project.project_id;
    }
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      detail = String((parsed as { detail: unknown }).detail);
    }
  } catch {
    detail = raw.value.body.slice(0, 200);
  }
  // Te trasy nie mówią nic o widoczności skutku, więc `null`, a nie `false`.
  return { ok: true, value: { status: raw.value.status, detail, inConversation: null, ...(createdProjectId ? { createdProjectId } : {}) } };
}

export function enableCommunity(
  token: string,
  platform: string,
  communityKey: string,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/communities", { platform, community_key: communityKey });
}

export function saveUserProfile(
  token: string,
  profile: {
    readonly knows_deeply: string;
    readonly positions: string;
    readonly would_never_claim: string;
    readonly voice_samples: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/user-profile", profile);
}

export function recordFeedback(
  token: string,
  judgmentId: string,
  verdict: string,
  note: string | null,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `/api/judgments/${encodeURIComponent(judgmentId)}/feedback`, {
    verdict,
    note,
  });
}

/**
 * The person's decision about founding our own threads in one community (0025).
 *
 * THREE STATES AND `null` IS ONE OF THEM: it puts the community back to "nobody decided", which
 * is not the same as "not allowed". A boolean here would have made the undo impossible to say.
 */
export function decideOwnPosts(
  token: string,
  communityId: string,
  allowed: boolean | null,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `/api/communities/${encodeURIComponent(communityId)}/own-posts`, {
    allowed,
  });
}

export function resumeWaitingJobs(token: string): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/jobs/waiting/resume", {});
}

// ---------------------------------------------------------------------------
// The capsule's browser (contract in `lib/capsule-schema.ts`, agreed with the backend session on
// 2026-09-03). Three calls, and the third one is the reason `call` takes a timeout: confirming a
// sign-in switches the capsule's mode and reads the platform twice, which the backend measured at
// up to a minute. The default 5 s is right for every other route and would abort this one every
// time — and an aborted write reported as "nie udało się połączyć" would send the operator back to
// press a button whose work had in fact already started.
// ---------------------------------------------------------------------------

import {
  parseCapsuleConfirmation,
  parseCapsuleStatus,
  parseCapsuleTicket,
  type CapsuleConfirmation,
  type CapsuleStatus,
  type CapsuleTicket,
} from "./capsule-schema";

/** How long the confirmation may take. The backend states ~60 s; this leaves room and no more. */
export const CAPSULE_CONFIRM_TIMEOUT_MS = 90_000;

/** A non-200 with the control plane's own sentence, so the screen can say WHY rather than "błąd". */
function refused(raw: RawResponse, what: string): Read<never> {
  let detail = "";
  try {
    const parsed = JSON.parse(raw.body) as unknown;
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      detail = String((parsed as { detail: unknown }).detail);
    }
  } catch {
    detail = raw.body.slice(0, 200);
  }
  return {
    ok: false,
    unauthorized: false,
    reason: detail === "" ? `${what}: control plane odpowiedział ${raw.status}` : `${what}: ${detail}`,
  };
}

/** The capsule as the control plane last saw it. Does NOT touch the platform. */
export async function getCapsule(token: string): Promise<Read<CapsuleStatus>> {
  const raw = await call("GET", "/api/capsule", { token });
  if (!raw.ok) return raw;
  if (raw.value.status !== 200) return refused(raw.value, "stan przeglądarki agenta");
  const envelope = decode(raw.value, "kapsuła");
  if (!envelope.ok) return envelope;
  return parseCapsuleStatus(envelope.value);
}

/**
 * Open the capsule for a human and get the ticket URL. The ticket lives ~2 minutes.
 *
 * `platform` names WHICH sign-in the human is about to do (26.09: one capsule now carries the X
 * session next to Reddit's). Left out, the body stays `{}`, exactly what the Operations screen has
 * always sent, and the control plane chooses as it did before; the studio's account card names
 * the platform, because it knows which account it is connecting.
 */
export async function openCapsuleSession(
  token: string,
  platform?: string,
): Promise<Read<CapsuleTicket>> {
  const body = platform === undefined ? "{}" : JSON.stringify({ platform });
  const raw = await call("POST", "/api/capsule/session", { token, body });
  if (!raw.ok) return raw;
  if (raw.value.status !== 200) return refused(raw.value, "otwarcie przeglądarki agenta");
  const envelope = decode(raw.value, "otwarcie przeglądarki agenta");
  if (!envelope.ok) return envelope;
  return parseCapsuleTicket(envelope.value);
}

/** The human says they are signed in: the control plane measures it and resumes parked jobs. */
export async function confirmCapsuleSession(token: string): Promise<Read<CapsuleConfirmation>> {
  const raw = await call("POST", "/api/capsule/session/confirm", {
    token,
    body: "{}",
    timeoutMs: CAPSULE_CONFIRM_TIMEOUT_MS,
  });
  if (!raw.ok) return raw;
  if (raw.value.status !== 200) return refused(raw.value, "potwierdzenie logowania");
  const envelope = decode(raw.value, "potwierdzenie logowania");
  if (!envelope.ok) return envelope;
  return parseCapsuleConfirmation(envelope.value);
}

// ---------------------------------------------------------------------------
// P1 (ROADMAP-PIVOT, D-040) — the studio: workspaces, projects, person profiles, platform
// accounts and assignments. Contract in `lib/workspace-schema.ts`, backend in
// `control_plane/api/workspaces.py`.
//
// EVERY PATH CARRIES THE WORKSPACE, and the control plane checks it against the session's
// organization. This side must never "remember" which workspace is current and leave it out of a
// path: a request without it would be answered against whatever the backend decided to assume,
// and the isolation P1 is judged on would hold in the database and not on the wire.
// ---------------------------------------------------------------------------

import {
  parseDocumentDetail,
  parseDocumentIndex,
  type DocumentDetail,
  type DocumentEntry,
} from "./documents-schema";
import { parseReturnView, type ReturnView } from "./returns-schema";
import {
  parseAccount,
  parseAssignment,
  parseAttachment,
  parsePersonProfile,
  parsePersonVoice,
  parsePlatformSupport,
  parseProject,
  parseResultCommunity,
  parseProjectResultTotals,
  parseResultPublication,
  parseResultTotals,
  parseVoiceProposal,
  parseWorkspace,
  parseFollowerReading,
  type AccountSummary,
  type FollowerReading,
  type AssignmentSummary,
  type AttachmentSummary,
  type PersonProfileSummary,
  type PersonVoiceVersion,
  type PilotVoiceProposal,
  type PlatformSupport,
  type ProjectSummary,
  type ProjectResults,
  type WorkspaceResults,
  type WorkspaceSummary,
} from "./workspace-schema";

/** Base path of one workspace. The id is encoded: it arrives from a URL segment. */
function workspacePath(workspaceId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}`;
}

/** A DELETE with NO BODY. Separate from `write` on purpose: `JSON.stringify(undefined)` returns
 *  `undefined` while its type says `string`, so routing a bodyless call through `write` would
 *  type-check and send a `content-type` header with nothing behind it. */
async function remove(token: string, path: string): Promise<Read<WriteOutcome>> {
  const raw = await call("DELETE", path, { token });
  if (!raw.ok) return raw;
  let detail = "";
  try {
    const parsed = JSON.parse(raw.value.body) as unknown;
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      detail = String((parsed as { detail: unknown }).detail);
    }
  } catch {
    detail = raw.value.body.slice(0, 200);
  }
  // Te trasy nie mówią nic o widoczności skutku, więc `null`, a nie `false`.
  return { ok: true, value: { status: raw.value.status, detail, inConversation: null } };
}

/** Every field of an envelope that must be an array of rows, parsed or failed as a whole. */
function rowsOf<T>(
  envelope: Record<string, unknown>,
  field: string,
  what: string,
  parse: (row: Record<string, unknown>) => Read<T>,
): Read<readonly T[]> {
  const raw = asRecordArray(envelope, field, what);
  if (!raw.ok) return raw;
  return parseAll(raw.value, parse);
}

export interface WorkspaceList {
  readonly workspaces: readonly WorkspaceSummary[];
  readonly platforms: readonly PlatformSupport[];
}

/** Every workspace of the operator's organization, plus the platform capability matrix. */
export async function getWorkspaces(token: string): Promise<Read<WorkspaceList>> {
  const raw = await call("GET", "/api/workspaces", { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "lista workspace'ów");
  if (!envelope.ok) return envelope;
  const workspaces = rowsOf(envelope.value, "workspaces", "lista workspace'ów", parseWorkspace);
  if (!workspaces.ok) return workspaces;
  const platforms = rowsOf(envelope.value, "platforms", "macierz platform", parsePlatformSupport);
  if (!platforms.ok) return platforms;
  return { ok: true, value: { workspaces: workspaces.value, platforms: platforms.value } };
}

export interface WorkspaceDetail {
  readonly workspace: WorkspaceSummary;
  readonly projects: readonly ProjectSummary[];
}

/**
 * One workspace and its projects, or `null` when the control plane answered 404.
 *
 * `null` is a FACT (no such workspace in this organization) and therefore travels inside
 * `ok: true`; the screen says "nie ma takiego workspace'u" instead of "odczyt się nie udał".
 */
export async function getWorkspace(
  token: string,
  workspaceId: string,
): Promise<Read<WorkspaceDetail | null>> {
  const raw = await call("GET", workspacePath(workspaceId), { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "workspace");
  if (!envelope.ok) return envelope;
  const workspace = envelope.value["workspace"];
  if (typeof workspace !== "object" || workspace === null || Array.isArray(workspace)) {
    return { ok: false, unauthorized: false, reason: "workspace: pole 'workspace' nie jest obiektem" };
  }
  const parsed = parseWorkspace(workspace as Record<string, unknown>);
  if (!parsed.ok) return parsed;
  const projects = rowsOf(envelope.value, "projects", "projekty", parseProject);
  if (!projects.ok) return projects;
  return { ok: true, value: { workspace: parsed.value, projects: projects.value } };
}

export interface ProjectDetail {
  readonly project: ProjectSummary;
  readonly assignments: readonly AssignmentSummary[];
  /** Accounts this workspace owns that are not assigned to this project yet. */
  readonly candidates: readonly AccountSummary[];
}

export async function getProject(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<ProjectDetail | null>> {
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "projekt");
  if (!envelope.ok) return envelope;
  const project = envelope.value["project"];
  if (typeof project !== "object" || project === null || Array.isArray(project)) {
    return { ok: false, unauthorized: false, reason: "projekt: pole 'project' nie jest obiektem" };
  }
  const parsed = parseProject(project as Record<string, unknown>);
  if (!parsed.ok) return parsed;
  const assignments = rowsOf(envelope.value, "assignments", "przypisania", parseAssignment);
  if (!assignments.ok) return assignments;
  const candidates = rowsOf(envelope.value, "candidates", "konta do przypisania", parseAccount);
  if (!candidates.ok) return candidates;
  return {
    ok: true,
    value: { project: parsed.value, assignments: assignments.value, candidates: candidates.value },
  };
}

export interface PersonVoiceView {
  readonly voice: PersonVoiceVersion | null;
  readonly history: readonly PersonVoiceVersion[];
  readonly proposal: PilotVoiceProposal | null;
}

export interface PeopleView {
  readonly profiles: readonly PersonProfileSummary[];
  readonly attachments: readonly AttachmentSummary[];
  readonly assignments: readonly AssignmentSummary[];
  readonly accounts: readonly AccountSummary[];
  readonly projects: readonly ProjectSummary[];
  readonly platforms: readonly PlatformSupport[];
  /** Latest follower readings; an account without one is absent (27.09). */
  readonly audience: readonly FollowerReading[];
}

/** Everything the "Profile i konta" screen shows, in one read. */
export async function getPeople(
  token: string,
  workspaceId: string,
): Promise<Read<PeopleView | null>> {
  const raw = await call("GET", `${workspacePath(workspaceId)}/profiles`, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "profile i konta");
  if (!envelope.ok) return envelope;
  const profiles = rowsOf(envelope.value, "profiles", "profile", parsePersonProfile);
  if (!profiles.ok) return profiles;
  const attachments = rowsOf(envelope.value, "attachments", "przypięcia", parseAttachment);
  if (!attachments.ok) return attachments;
  const assignments = rowsOf(envelope.value, "assignments", "przypisania", parseAssignment);
  if (!assignments.ok) return assignments;
  const accounts = rowsOf(envelope.value, "accounts", "konta", parseAccount);
  if (!accounts.ok) return accounts;
  const projects = rowsOf(envelope.value, "projects", "projekty", parseProject);
  if (!projects.ok) return projects;
  const platforms = rowsOf(envelope.value, "platforms", "macierz platform", parsePlatformSupport);
  if (!platforms.ok) return platforms;
  // Older servers send no `audience`: that is "nothing measured", never an error and never zero.
  const audience =
    "audience" in envelope.value
      ? rowsOf(envelope.value, "audience", "odczyty obserwujących", parseFollowerReading)
      : ({ ok: true, value: [] } as const);
  if (!audience.ok) return audience;
  return {
    ok: true,
    value: {
      profiles: profiles.value,
      attachments: attachments.value,
      assignments: assignments.value,
      accounts: accounts.value,
      projects: projects.value,
      platforms: platforms.value,
      audience: audience.value,
    },
  };
}

/** What really happened in this workspace: communities, publications, totals. */
export async function getWorkspaceResults(
  token: string,
  workspaceId: string,
): Promise<Read<WorkspaceResults | null>> {
  const raw = await call("GET", `${workspacePath(workspaceId)}/results`, { token });
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "rezultaty workspace'u");
  if (!envelope.ok) return envelope;
  const communities = rowsOf(envelope.value, "communities", "społeczności", parseResultCommunity);
  if (!communities.ok) return communities;
  const publications = rowsOf(envelope.value, "publications", "publikacje", parseResultPublication);
  if (!publications.ok) return publications;
  const totals = envelope.value["totals"];
  if (typeof totals !== "object" || totals === null || Array.isArray(totals)) {
    return { ok: false, unauthorized: false, reason: "rezultaty: pole 'totals' nie jest obiektem" };
  }
  const parsedTotals = parseResultTotals(totals as Record<string, unknown>);
  if (!parsedTotals.ok) return parsedTotals;
  return {
    ok: true,
    value: {
      communities: communities.value,
      publications: publications.value,
      totals: parsedTotals.value,
    },
  };
}

/**
 * Publikacje JEDNEGO projektu (E6).
 *
 * Osobna trasa, nie parametr tamtej, i ekran ma się na tym oprzeć: odpowiedź NIE MA pól o
 * społecznościach, więc nie da się jej pokazać jako „wszystko o projekcie". `scope` jest
 * sprawdzany co do wartości — lista przestrzeni podpisana nazwą projektu jest dokładnie tym
 * ustaleniem §6 audytu, którego ta trasa ma nie dopuścić, więc odczyt zrywa się, gdy serwer
 * przyśle zakres inny niż `PROJECT` albo projekt inny niż pytany.
 */
export async function getProjectResults(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<ProjectResults | null>> {
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/results`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "publikacje projektu");
  if (!envelope.ok) return envelope;
  const scope = envelope.value["scope"];
  if (typeof scope !== "object" || scope === null || Array.isArray(scope)) {
    return { ok: false, unauthorized: false, reason: "publikacje projektu: brak pola 'scope'" };
  }
  const kind = (scope as Record<string, unknown>)["kind"];
  const which = (scope as Record<string, unknown>)["project_id"];
  if (kind !== "PROJECT" || which !== projectId) {
    return {
      ok: false,
      unauthorized: false,
      reason:
        `publikacje projektu: serwer odpowiedział zakresem ${String(kind)} dla ${String(which)}, ` +
        `a pytaliśmy o projekt ${projectId}`,
    };
  }
  const publications = rowsOf(envelope.value, "publications", "publikacje", parseResultPublication);
  if (!publications.ok) return publications;
  const totals = envelope.value["totals"];
  if (typeof totals !== "object" || totals === null || Array.isArray(totals)) {
    return {
      ok: false,
      unauthorized: false,
      reason: "publikacje projektu: pole 'totals' nie jest obiektem",
    };
  }
  const parsedTotals = parseProjectResultTotals(totals as Record<string, unknown>);
  if (!parsedTotals.ok) return parsedTotals;
  return {
    ok: true,
    value: {
      scope: { kind: "PROJECT", project_id: projectId },
      publications: publications.value,
      totals: parsedTotals.value,
    },
  };
}

// -- writes -------------------------------------------------------------------

export function createWorkspace(token: string, name: string): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/workspaces", { name });
}

export function createProject(
  token: string,
  workspaceId: string,
  project: { readonly name: string; readonly goal: string; readonly mode: string },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${workspacePath(workspaceId)}/projects`, project);
}

/** A PATCH carrying ONLY the fields the form actually changed — an absent one is left alone. */
export function updateProject(
  token: string,
  workspaceId: string,
  projectId: string,
  patch: {
    readonly name?: string;
    readonly goal?: string;
    readonly mode?: string;
    readonly paused?: boolean;
    readonly archived?: boolean;
    /** 0026: limit jednej rozmowy. Pominięty nie rusza ustawienia. */
    readonly conversation_budget?: Record<string, number>;
    /** 0026: wróć do wartości domyślnej produktu. Potrzebne, bo pominięcie znaczy „nie ruszaj". */
    readonly conversation_budget_default?: boolean;
  },
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "PATCH",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}`,
    patch,
  );
}

export function createPersonProfile(
  token: string,
  workspaceId: string,
  profile: { readonly name: string; readonly kind: string },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${workspacePath(workspaceId)}/profiles`, profile);
}

/**
 * Głos jednej osoby: najnowsza wersja, historia i ewentualna propozycja z profilu pilota.
 *
 * `null` znaczy „nie ma takiej przestrzeni albo takiego profilu”, tak jak w `getPeople`.
 * Pusta historia to NIE to samo: profil jest, głosu nikt jeszcze nie opisał, i ekran mówi
 * o tym wprost zamiast pokazywać puste pola jak zapisane.
 */
export async function getPersonVoice(
  token: string,
  workspaceId: string,
  profileId: string,
): Promise<Read<PersonVoiceView | null>> {
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/profiles/${encodeURIComponent(profileId)}/voice`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "głos osoby");
  if (!envelope.ok) return envelope;
  const history = rowsOf(envelope.value, "history", "historia głosu", parsePersonVoice);
  if (!history.ok) return history;
  const rawProposal = envelope.value["proposal"];
  let proposal: PilotVoiceProposal | null = null;
  if (rawProposal !== null && rawProposal !== undefined) {
    if (typeof rawProposal !== "object" || Array.isArray(rawProposal)) {
      return {
        ok: false,
        unauthorized: false,
        reason: "propozycja głosu z profilu pilota: pole `proposal` nie jest obiektem",
      };
    }
    const parsed = parseVoiceProposal(rawProposal as Record<string, unknown>);
    if (!parsed.ok) return parsed;
    proposal = parsed.value;
  }
  return {
    ok: true,
    value: {
      // Najnowsza jest pierwsza w historii; drugie pole z tą samą treścią to drugie miejsce,
      // w którym można się pomylić o to, która wersja obowiązuje.
      voice: history.value.length > 0 ? history.value[0]! : null,
      history: history.value,
      proposal,
    },
  };
}

export function savePersonVoice(
  token: string,
  workspaceId: string,
  profileId: string,
  voice: {
    readonly knows_deeply: string;
    readonly positions: string;
    readonly would_never_claim: string;
    readonly voice_samples: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${workspacePath(workspaceId)}/profiles/${encodeURIComponent(profileId)}/voice`,
    voice,
  );
}

/**
 * Widok powrotu: co się wydarzyło, odkąd tej osoby nie było w projekcie.
 *
 * Odczyt NOTUJE wizytę po stronie control plane, i tak ma być: wejście na ekran jest wizytą.
 * `null` znaczy „nie ma takiego projektu", tak jak w pozostałych odczytach studia.
 */
export async function getProjectReturn(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<ReturnView | null>> {
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/return`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "widok powrotu");
  if (!envelope.ok) return envelope;
  return parseReturnView(envelope.value);
}

export function sendReturnCorrection(
  token: string,
  workspaceId: string,
  projectId: string,
  text: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/return/correction`,
    { text },
  );
}

/**
 * Dokumenty projektu: indeks (rola, wersja, data, liczba zdań) i jedna wersja ze zdaniami.
 *
 * Projekt bez dokumentów oddaje PUSTĄ LISTĘ, nie `null`: „jeszcze nic nie domknięto" to normalny
 * stan pierwszego dnia, a nie brak projektu. `null` zostaje dla nieistniejącej przestrzeni.
 */
export async function getProjectDocuments(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<Read<readonly DocumentEntry[] | null>> {
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/documents`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "dokumenty projektu");
  if (!envelope.ok) return envelope;
  return parseDocumentIndex(envelope.value);
}

export async function getProjectDocument(
  token: string,
  workspaceId: string,
  projectId: string,
  documentId: string,
  version?: number,
): Promise<Read<DocumentDetail | null>> {
  const query = version === undefined ? "" : `?wersja=${encodeURIComponent(String(version))}`;
  const raw = await call(
    "GET",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/documents/` +
      `${encodeURIComponent(documentId)}${query}`,
    { token },
  );
  if (!raw.ok) return raw;
  if (raw.value.status === 404) return { ok: true, value: null };
  const envelope = decode(raw.value, "dokument projektu");
  if (!envelope.ok) return envelope;
  return parseDocumentDetail(envelope.value);
}

export function registerAccount(
  token: string,
  workspaceId: string,
  account: {
    readonly platform: string;
    readonly external_account_id: string;
    readonly person_profile_id?: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${workspacePath(workspaceId)}/accounts`, account);
}

export function attachAccount(
  token: string,
  workspaceId: string,
  profileId: string,
  accountId: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${workspacePath(workspaceId)}/profiles/${encodeURIComponent(profileId)}/accounts`,
    { social_account_id: accountId },
  );
}

export function detachAccount(
  token: string,
  workspaceId: string,
  profileId: string,
  accountId: string,
): Promise<Read<WriteOutcome>> {
  return remove(
    token,
    `${workspacePath(workspaceId)}/profiles/${encodeURIComponent(profileId)}/accounts/` +
      encodeURIComponent(accountId),
  );
}

export function assignAccount(
  token: string,
  workspaceId: string,
  projectId: string,
  accountId: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/accounts`,
    { social_account_id: accountId },
  );
}

export function unassignAccount(
  token: string,
  workspaceId: string,
  projectId: string,
  accountId: string,
): Promise<Read<WriteOutcome>> {
  return remove(
    token,
    `${workspacePath(workspaceId)}/projects/${encodeURIComponent(projectId)}/accounts/` +
      encodeURIComponent(accountId),
  );
}

// ---------------------------------------------------------------------------
// P1 etap 2 — Codex connections. Access to the ENGINE, which is not a social account: the two
// live behind different routes and different screens, and confusing them is the first mistake
// ZAKRES-FUNKCJONALNY §2 says the interface may not make.
//
// The paths carry NO workspace. A connection belongs to the organization and may be narrowed to
// one workspace by a field in its own row; putting the workspace in the path would imply the
// opposite — that a connection is workspace data — which is exactly the thing WIZJA-WORKSPACE
// rules out ("limity konta nie stają się osobnym przydziałem dla każdego workspace'u").
// ---------------------------------------------------------------------------

import {
  parseCodexRoot,
  parseConnection,
  type CodexConnection,
  type CodexConnectionsView,
} from "./codex-schema";

const CODEX_CONNECTIONS = "/api/codex-connections";

/** Every connection of the organization, plus what the configured volume root really holds. */
export async function getCodexConnections(
  token: string,
): Promise<Read<CodexConnectionsView>> {
  const raw = await call("GET", CODEX_CONNECTIONS, { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "połączenia Codex");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "connections", "połączenia Codex");
  if (!rows.ok) return rows;
  const connections: CodexConnection[] = [];
  for (const row of rows.value) {
    const parsed = parseConnection(row);
    if (!parsed.ok) return parsed;
    connections.push(parsed.value);
  }
  const root = envelope.value["root"];
  if (typeof root !== "object" || root === null || Array.isArray(root)) {
    return { ok: false, unauthorized: false, reason: "połączenia Codex: pole 'root' nie jest obiektem" };
  }
  const parsedRoot = parseCodexRoot(root as Record<string, unknown>);
  if (!parsedRoot.ok) return parsedRoot;
  return { ok: true, value: { connections, root: parsedRoot.value } };
}

/** Register the configured `CODEX_HOME` as a connection. Writes nothing into that directory. */
export function adoptCodexRoot(token: string, label: string): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${CODEX_CONNECTIONS}/adopt-root`, { label });
}

/** A new connection: its own subdirectory, its own hardened config, its own device-code login. */
export function createCodexConnection(
  token: string,
  connection: { readonly label: string; readonly workspace_id?: string },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", CODEX_CONNECTIONS, connection);
}

export function refreshCodexConnection(
  token: string,
  connectionId: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${CODEX_CONNECTIONS}/${encodeURIComponent(connectionId)}/refresh`,
    {},
  );
}

/** Start a device-code login again, after one expired or was refused. Never automatic. */
export function restartCodexLogin(
  token: string,
  connectionId: string,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${CODEX_CONNECTIONS}/${encodeURIComponent(connectionId)}/login`, {});
}

/** Stop using this connection. The row, the directory and `auth.json` all stay. */
export function disconnectCodexConnection(
  token: string,
  connectionId: string,
): Promise<Read<WriteOutcome>> {
  return remove(token, `${CODEX_CONNECTIONS}/${encodeURIComponent(connectionId)}`);
}

// ---------------------------------------------------------------------------
// P3 etap A — materials and findings. Everything here carries provenance: an address, the moment
// it was read and the hash of the bytes kept. The screen never shows a statement without the
// material behind it, so the reads below always ask for both in one request.
//
// The paths carry the workspace, unlike the Codex connections above, because a material belongs
// to a workspace and may be narrowed further to one project.
// ---------------------------------------------------------------------------

import {
  parseConflict,
  parseItem,
  parseSource,
  parseSourceSlice,
  type KnowledgeConflict,
  type KnowledgeItem,
  type KnowledgeSource,
  type KnowledgeView,
  type SourceSlice,
} from "./knowledge-schema";

function knowledgePath(workspaceId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/knowledge`;
}

export async function getKnowledgeSource(
  token: string, workspaceId: string, projectId: string, sourceId: string, offset = 0,
): Promise<Read<SourceSlice>> {
  const query = new URLSearchParams({ project_id: projectId, offset: String(offset) });
  const raw = await call("GET", `${knowledgePath(workspaceId)}/sources/${encodeURIComponent(sourceId)}?${query}`, { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "materiał");
  if (!envelope.ok) return envelope;
  const slice = envelope.value["slice"];
  if (typeof slice !== "object" || slice === null || Array.isArray(slice)) {
    return { ok: false, unauthorized: false, reason: "Nie udało się odczytać treści materiału." };
  }
  return parseSourceSlice(slice as Record<string, unknown>);
}

/** Materials and findings of one project, or of the whole workspace when `projectId` is absent. */
export async function getKnowledge(
  token: string,
  workspaceId: string,
  projectId?: string,
  options: {
    readonly includeRetired?: boolean;
    readonly kind?: string;
    readonly community?: string;
    readonly account?: string;
  } = {},
): Promise<Read<KnowledgeView>> {
  const query = new URLSearchParams();
  if (projectId !== undefined) query.set("project_id", projectId);
  if (options.includeRetired === true) query.set("include_retired", "true");
  if (options.kind !== undefined) query.set("kind", options.kind);
  if (options.community !== undefined) query.set("community", options.community);
  if (options.account !== undefined) query.set("account", options.account);
  const suffix = query.toString() === "" ? "" : `?${query.toString()}`;
  const raw = await call("GET", `${knowledgePath(workspaceId)}${suffix}`, { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "materiały");
  if (!envelope.ok) return envelope;

  const sourceRows = asRecordArray(envelope.value, "sources", "materiały");
  if (!sourceRows.ok) return sourceRows;
  const sources: KnowledgeSource[] = [];
  for (const row of sourceRows.value) {
    const parsed = parseSource(row);
    if (!parsed.ok) return parsed;
    sources.push(parsed.value);
  }

  const itemRows = asRecordArray(envelope.value, "items", "materiały");
  if (!itemRows.ok) return itemRows;
  const items: KnowledgeItem[] = [];
  for (const row of itemRows.value) {
    const parsed = parseItem(row);
    if (!parsed.ok) return parsed;
    items.push(parsed.value);
  }

  const conflictRows = asRecordArray(envelope.value, "conflicts", "materiały");
  if (!conflictRows.ok) return conflictRows;
  const conflicts: KnowledgeConflict[] = [];
  for (const row of conflictRows.value) {
    const parsed = parseConflict(row);
    if (!parsed.ok) return parsed;
    conflicts.push(parsed.value);
  }

  return {
    ok: true,
    value: {
      sources,
      items,
      conflicts,
      unavailable: sources.filter((source) => source.status === "UNAVAILABLE").length,
      stale: items.filter((item) => item.stale && item.in_force).length,
    },
  };
}

/** Add a page, a feed or a note. 201 even when the address could not be read: see the API. */
export function addKnowledgeSource(
  token: string,
  workspaceId: string,
  source: {
    readonly kind: string;
    readonly locator?: string;
    readonly title?: string;
    readonly text?: string;
    readonly project_id?: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${knowledgePath(workspaceId)}/sources`, source);
}

/** Ask the queue to read a feed again. Returns 202: the reading happens in a worker. */
export function refreshKnowledgeSource(
  token: string,
  workspaceId: string,
  sourceId: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${knowledgePath(workspaceId)}/sources/${encodeURIComponent(sourceId)}/refresh`,
    {},
  );
}

/** Search the web through the keyless backends, recording what answered. */
export function searchTheWeb(
  token: string,
  workspaceId: string,
  query: string,
  projectId?: string,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${knowledgePath(workspaceId)}/search`, {
    query,
    ...(projectId === undefined ? {} : { project_id: projectId }),
  });
}

/** Write one finding. The backend refuses a FACT with no material, with a sentence to show. */
export function writeKnowledgeItem(
  token: string,
  workspaceId: string,
  item: {
    readonly kind: string;
    readonly statement: string;
    readonly stated_by: string;
    readonly project_id?: string;
    readonly source_id?: string;
    /** What question this answers. Two findings sharing it are reported as a conflict. */
    readonly about?: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${knowledgePath(workspaceId)}/items`, item);
}

/** The new finding, with the old one pointing at it. Nothing is overwritten. */
export function correctKnowledgeItem(
  token: string,
  workspaceId: string,
  itemId: string,
  item: {
    readonly kind: string;
    readonly statement: string;
    readonly stated_by: string;
    readonly project_id?: string;
    readonly source_id?: string;
    readonly about?: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${knowledgePath(workspaceId)}/items/${encodeURIComponent(itemId)}/correct`,
    item,
  );
}

/**
 * The person's short correction under one publication (P5).
 *
 * It is a PREFERENCE on the backend, stated by the person, tied to the publication and to what
 * came of it. Not a rating and not a counter: the next session reads the sentence.
 *
 * NOT `recordFeedback` above, and the two names are kept apart on purpose: that one is the
 * operator's verdict on a JUDGEMENT of a thread (`judgment_feedback`), this one is a correction
 * of a published text. Merging them would make both unreadable, which is why P5 says outright
 * that the older table stays where it is.
 */
export function recordPublicationFeedback(
  token: string,
  workspaceId: string,
  feedback: {
    readonly request_id: string;
    readonly verdict: string;
    readonly note?: string;
    readonly project_id?: string;
  },
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${knowledgePath(workspaceId)}/feedback`, feedback);
}

/** Ask the queue to measure what came of our publications. 202: a worker reads, no model runs. */
export function requestExperienceSweep(
  token: string,
  workspaceId: string,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", `${knowledgePath(workspaceId)}/experience/sweep`, {});
}

/** Withdraw a finding with a reason. The row stays and stays readable. */
export function retractKnowledgeItem(
  token: string,
  workspaceId: string,
  itemId: string,
  reason: string,
  projectId?: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `${knowledgePath(workspaceId)}/items/${encodeURIComponent(itemId)}/retract`,
    { reason, ...(projectId === undefined ? {} : { project_id: projectId }) },
  );
}

// ---------------------------------------------------------------------------
// P7 — organizations, members, invitations, export and deletion. Read and written by the
// settings screen of the space, which is the place a customer manages themselves from.
// ---------------------------------------------------------------------------

import {
  parseMember,
  parseInvitation,
  type OrganizationInvitation,
  type OrganizationMember,
  type OrganizationView,
} from "./identity-schema";

/** Every space this person belongs to, and which one they are working in. */
export async function getOrganizations(token: string): Promise<Read<OrganizationView>> {
  const raw = await call("GET", "/api/organizations", { token });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "przestrzenie");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "organizations", "przestrzenie");
  if (!rows.ok) return rows;
  const current = envelope.value["current"];
  if (typeof current !== "string") {
    return { ok: false, unauthorized: false, reason: "przestrzenie: brak pola 'current'" };
  }
  return {
    ok: true,
    value: {
      current,
      legacy_operator: envelope.value["legacy_operator"] === true,
      organizations: rows.value.map((row) => ({
        organization_id: String(row["organization_id"] ?? ""),
        name: String(row["name"] ?? ""),
        role: String(row["role"] ?? ""),
      })),
    },
  };
}

export async function getMembers(
  token: string,
  organizationId: string,
): Promise<Read<readonly OrganizationMember[]>> {
  const raw = await call("GET", `/api/organizations/${encodeURIComponent(organizationId)}/members`, {
    token,
  });
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "osoby w przestrzeni");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "members", "osoby w przestrzeni");
  if (!rows.ok) return rows;
  const members: OrganizationMember[] = [];
  for (const row of rows.value) {
    const parsed = parseMember(row);
    if (!parsed.ok) return parsed;
    members.push(parsed.value);
  }
  return { ok: true, value: members };
}

export async function getInvitations(
  token: string,
  organizationId: string,
): Promise<Read<readonly OrganizationInvitation[]>> {
  const raw = await call(
    "GET",
    `/api/organizations/${encodeURIComponent(organizationId)}/invitations`,
    { token },
  );
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "zaproszenia");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "invitations", "zaproszenia");
  if (!rows.ok) return rows;
  const invitations: OrganizationInvitation[] = [];
  for (const row of rows.value) {
    const parsed = parseInvitation(row);
    if (!parsed.ok) return parsed;
    invitations.push(parsed.value);
  }
  return { ok: true, value: invitations };
}

/**
 * Invite somebody. The TOKEN comes back in the response and is shown once, because etap A sends
 * no mail: whoever invites passes the link on themselves.
 */
export async function inviteMember(
  token: string,
  organizationId: string,
  email: string,
): Promise<Read<{ readonly token: string; readonly email: string }>> {
  const raw = await call(
    "POST",
    `/api/organizations/${encodeURIComponent(organizationId)}/invitations`,
    { token, body: JSON.stringify({ email }) },
  );
  if (!raw.ok) return raw;
  if (raw.value.status !== 201) {
    let detail = "";
    try {
      const parsed = JSON.parse(raw.value.body) as { detail?: unknown };
      detail = typeof parsed.detail === "string" ? parsed.detail : "";
    } catch {
      detail = "";
    }
    return { ok: false, unauthorized: raw.value.status === 401, reason: detail || "zaproszenie odrzucone" };
  }
  // Parsed here rather than through `decode`, which requires a 200: this route answers 201,
  // and running it through `decode` turned every successful invitation into "control plane
  // odpowiedział 201". Measured by the end-to-end suite, which got `info=refused` on a call
  // that had in fact created the invitation.
  let body: Record<string, unknown>;
  try {
    const parsed = JSON.parse(raw.value.body) as unknown;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { ok: false, unauthorized: false, reason: "zaproszenie: ciało nie jest obiektem" };
    }
    body = parsed as Record<string, unknown>;
  } catch (error) {
    return {
      ok: false,
      unauthorized: false,
      reason: `zaproszenie: ciało nie jest JSON-em (${String(error)})`,
    };
  }
  const passOn = body["token_to_pass_on"];
  const invitation = body["invitation"];
  if (typeof passOn !== "string" || typeof invitation !== "object" || invitation === null) {
    return { ok: false, unauthorized: false, reason: "zaproszenie: brak tokenu do przekazania" };
  }
  return {
    ok: true,
    value: { token: passOn, email: String((invitation as Record<string, unknown>)["email"] ?? "") },
  };
}

export function revokeMember(
  token: string,
  organizationId: string,
  userId: string,
): Promise<Read<WriteOutcome>> {
  return remove(
    token,
    `/api/organizations/${encodeURIComponent(organizationId)}/members/${encodeURIComponent(userId)}`,
  );
}

export function switchOrganization(
  token: string,
  organizationId: string,
): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/organizations/switch", { organization_id: organizationId });
}

/** The founder's migrated account naming itself, once. */
export function setUserEmail(token: string, email: string): Promise<Read<WriteOutcome>> {
  return write(token, "POST", "/api/auth/email", { email });
}

export function requestExport(token: string, organizationId: string): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `/api/organizations/${encodeURIComponent(organizationId)}/data/exports`,
    {},
  );
}

export async function getExports(
  token: string,
  organizationId: string,
): Promise<Read<readonly Record<string, unknown>[]>> {
  const raw = await call(
    "GET",
    `/api/organizations/${encodeURIComponent(organizationId)}/data/exports`,
    { token },
  );
  if (!raw.ok) return raw;
  const envelope = decode(raw.value, "eksporty");
  if (!envelope.ok) return envelope;
  const rows = asRecordArray(envelope.value, "exports", "eksporty");
  if (!rows.ok) return rows;
  return { ok: true, value: rows.value };
}

/** Delete this organization. The password is required again: this is the destructive one. */
export function requestDeletion(
  token: string,
  organizationId: string,
  password: string,
): Promise<Read<WriteOutcome>> {
  return write(
    token,
    "POST",
    `/api/organizations/${encodeURIComponent(organizationId)}/data/deletion`,
    { password },
  );
}
