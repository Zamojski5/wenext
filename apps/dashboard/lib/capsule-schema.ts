/**
 * The capsule's browser as the dashboard reads it — CONTRACT AGREED WITH THE BACKEND SESSION on
 * 2026-09-03 (docs/DASHBOARD-DESIGN.md §4a). Three routes, all behind the operator boundary:
 *
 *   GET  /api/capsule                      → status, and everything the screen shows next to it
 *   POST /api/capsule/session              → opens INTERACTIVE, returns `href` (a ticket URL)
 *   POST /api/capsule/session/confirm      → measures both modes, resumes parked jobs
 *
 * WHO DECIDES "LOGGED IN". THE API DOES, and that is the whole point of `status`: the backend
 * computes `AWAITING_HUMAN` when the capsule is interactive and `SIGNED_IN` when the last checked
 * `/api/me.json` carried an account. The dashboard never derives it from the capsule's own mode —
 * "the capsule is up" is not "you are logged in", and turning one into the other would be a claim
 * about a measurement nobody made (L5241 point 5).
 *
 * WHAT THE DASHBOARD DOES NOT DO HERE: it does not embed the capsule's screen (the interactive
 * view stays out of this package — `tests/source_scan.test.ts`, scope E1.F2, decision confirmed by
 * the orchestrator on 2026-09-03), and it never collects a platform password. The human opens the
 * capsule's own browser in a new tab, signs in there, and comes back to press "Gotowe".
 *
 * ONE FIELD GOT RENAMED FOR THIS PACKAGE, and the reason is worth keeping: the readiness flag of
 * the capsule's viewer was first named after the remote-desktop protocol, and
 * `tests/source_scan.test.ts` forbids that string here (the interactive viewer is E1.F2, and the
 * orchestrator confirmed on 2026-09-03 that the ban stays). The scanner therefore made a naming
 * decision — the backend session renamed it to `viewer_ready`, which this file reads. The scope
 * guard worked exactly where it was supposed to.
 *
 * Parser discipline as in `lib/observe-schema.ts`: every field explicit, a missing or wrongly typed
 * field fails the whole read with the field named, nothing defaulted. `error` is a MEASURED fact and
 * travels inside `ok: true` — the screen states it instead of pretending the read failed.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function nullableStr(
  row: Record<string, unknown>,
  field: string,
): { readonly present: true; readonly value: string | null } | { readonly present: false } {
  if (!(field in row)) return { present: false };
  const value = row[field];
  if (value === null) return { present: true, value: null };
  if (typeof value === "string") return { present: true, value };
  return { present: false };
}

/** The three states the API computes. An unknown one is a failed read, never a fourth pill. */
export const CAPSULE_STATUSES: readonly string[] = ["SIGNED_OUT", "AWAITING_HUMAN", "SIGNED_IN"];

/** The two modes of the capsule's browser. `INTERACTIVE` is the one a human can see into. */
export const CAPSULE_MODES: readonly string[] = ["HEADLESS", "INTERACTIVE"];

/** What one mode measured against the platform. `error` excludes the readings and vice versa. */
export interface CapsuleProbe {
  readonly logged_in: boolean | null;
  readonly username: string | null;
  readonly threads: number | null;
  readonly error: string | null;
}

export interface CapsuleStatus {
  readonly status: string;
  /** `false` — nobody configured the capsule; there is nothing to open and nothing to fix here. */
  readonly configured: boolean;
  /** The ticket URL, when one is live. `null` on the read — a ticket comes from the WRITE. */
  readonly href: string | null;
  readonly account: string | null;
  readonly since: string | null;
  /**
   * `HEADLESS`, `INTERACTIVE`, or `null` when the control plane could not ask. Context only —
   * never the pill.
   */
  readonly mode: string | null;
  /** `true` — the capsule shows its screen but the human is not steering it (D-026). */
  readonly display_only: boolean;
  /** `true` — the human currently has the controls. */
  readonly human_steer_open: boolean;
  /**
   * The LAST KNOWN reading of the platform session — the control plane does not touch Reddit on
   * this route. `null` means nobody has checked yet, which is not the same as "not logged in".
   */
  readonly session: CapsuleProbe | null;
  /**
   * Is the capsule's own viewer reachable — "open it now" vs "it is still starting".
   *
   * READ ONLY FOR THE SENTENCE, never for the pill: the API's `status` decides whether anybody is
   * signed in. The field was renamed at this package's request (its first name carried a string
   * `tests/source_scan.test.ts` forbids here, scope E1.F2), which is why it can be read at all.
   */
  readonly viewer_ready: boolean;
  /** A stated problem (capsule unreachable, gateway down). A FACT, not a failed read. */
  readonly error: string | null;
}

function parseProbe(raw: unknown, what: string): Read<CapsuleProbe> {
  if (raw === null || raw === undefined) {
    // A side that did not run at all is not a side that measured "not logged in".
    return { ok: true, value: { logged_in: null, username: null, threads: null, error: "nie uruchomiono" } };
  }
  if (typeof raw !== "object" || Array.isArray(raw)) return failure(`${what}: nie jest obiektem`);
  const row = raw as Record<string, unknown>;
  const error = nullableStr(row, "error");
  const loggedIn = row["logged_in"];
  const username = nullableStr(row, "username");
  const threads = row["threads"];
  const missing: string[] = [];
  if (!error.present) missing.push("error");
  if (loggedIn !== null && loggedIn !== undefined && typeof loggedIn !== "boolean") missing.push("logged_in");
  if (!username.present && username !== undefined) missing.push("username");
  if (threads !== null && threads !== undefined && (typeof threads !== "number" || !Number.isFinite(threads))) {
    missing.push("threads");
  }
  if (missing.length > 0) return failure(`${what}: brakujące albo złego typu pola: ${missing.join(", ")}`);
  return {
    ok: true,
    value: {
      logged_in: typeof loggedIn === "boolean" ? loggedIn : null,
      username: username.present ? username.value : null,
      threads: typeof threads === "number" ? threads : null,
      error: (error as { value: string | null }).value,
    },
  };
}

export function parseCapsuleStatus(row: Record<string, unknown>): Read<CapsuleStatus> {
  const missing: string[] = [];
  const status = str(row, "status");
  const mode = nullableStr(row, "mode");
  const href = nullableStr(row, "href");
  const account = nullableStr(row, "account");
  const since = nullableStr(row, "since");
  const error = nullableStr(row, "error");
  const configured = row["configured"];
  const viewerReady = row["viewer_ready"];
  const displayOnly = row["display_only"];
  const humanSteer = row["human_steer_open"];

  if (status === null) missing.push("status");
  else if (!CAPSULE_STATUSES.includes(status)) missing.push(`status='${status}' nie jest znanym stanem kapsuły`);
  if (!mode.present) missing.push("mode");
  else if (mode.value !== null && !CAPSULE_MODES.includes(mode.value)) {
    missing.push(`mode='${mode.value}' nie jest znanym trybem kapsuły`);
  }
  if (!href.present) missing.push("href");
  if (!account.present) missing.push("account");
  if (!since.present) missing.push("since");
  if (!error.present) missing.push("error");
  if (typeof configured !== "boolean") missing.push("configured");
  if (typeof viewerReady !== "boolean") missing.push("viewer_ready");
  if (typeof displayOnly !== "boolean") missing.push("display_only");
  if (typeof humanSteer !== "boolean") missing.push("human_steer_open");
  if (missing.length > 0) {
    return failure(`kapsuła: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  // `session: null` is "nobody has checked yet", which is NOT "not logged in" — so it stays null
  // rather than becoming a probe full of falses.
  let session: CapsuleProbe | null = null;
  if (row["session"] !== null && row["session"] !== undefined) {
    const parsed = parseProbe(row["session"], "ostatni odczyt sesji");
    if (!parsed.ok) return parsed;
    session = parsed.value;
  }
  return {
    ok: true,
    value: {
      status: status as string,
      href: (href as { value: string | null }).value,
      account: (account as { value: string | null }).value,
      since: (since as { value: string | null }).value,
      mode: (mode as { value: string | null }).value,
      display_only: displayOnly as boolean,
      human_steer_open: humanSteer as boolean,
      session,
      viewer_ready: viewerReady as boolean,
      configured: configured as boolean,
      error: (error as { value: string | null }).value,
    },
  };
}

/** `POST /api/capsule/session` — where the human signs in, and for how long the ticket lives. */
export interface CapsuleTicket {
  readonly href: string;
  readonly ticket_expires_at: string;
  readonly status: string;
}

export function parseCapsuleTicket(row: Record<string, unknown>): Read<CapsuleTicket> {
  const href = str(row, "href");
  const expires = str(row, "ticket_expires_at");
  const status = str(row, "status");
  const missing: string[] = [];
  if (href === null) missing.push("href");
  if (expires === null) missing.push("ticket_expires_at");
  if (status === null) missing.push("status");
  if (missing.length > 0) {
    return failure(`otwarcie kapsuły: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return { ok: true, value: { href: href as string, ticket_expires_at: expires as string, status: status as string } };
}

export interface CapsuleConfirmation {
  readonly status: string;
  readonly account: string | null;
  /** What the capsule saw WHILE the human was steering it. */
  readonly with_human: CapsuleProbe;
  /** What it saw afterwards, showing its screen but not steered (D-026: Reddit refuses headless). */
  readonly display_only: CapsuleProbe;
  /** How many parked jobs went back to the queue because the session now works. */
  readonly resumed_jobs: number;
}

export function parseCapsuleConfirmation(row: Record<string, unknown>): Read<CapsuleConfirmation> {
  const status = str(row, "status");
  if (status === null || !CAPSULE_STATUSES.includes(status)) {
    return failure(`potwierdzenie logowania: 'status' brakuje albo nie jest znanym stanem`);
  }
  const account = nullableStr(row, "account");
  if (!account.present) return failure("potwierdzenie logowania: brak pola 'account'");
  const withHuman = parseProbe(row["with_human"], "sprawdzenie przy Tobie");
  if (!withHuman.ok) return withHuman;
  const displayOnly = parseProbe(row["display_only"], "sprawdzenie bez steru");
  if (!displayOnly.ok) return displayOnly;
  const resumed = row["resumed_jobs"];
  if (typeof resumed !== "number" || !Number.isFinite(resumed)) {
    return failure("potwierdzenie logowania: 'resumed_jobs' nie jest liczbą");
  }
  return {
    ok: true,
    value: {
      status,
      account: (account as { value: string | null }).value,
      with_human: withHuman.value,
      display_only: displayOnly.value,
      resumed_jobs: resumed,
    },
  };
}
