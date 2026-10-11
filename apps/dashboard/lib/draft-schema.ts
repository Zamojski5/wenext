/**
 * A DRAFT COMMENT and the consent that turns it into a published one. Proposed contract, not wired
 * (docs/DASHBOARD-DESIGN.md §4b); the screens are built on fixtures until the backend confirms it.
 *
 * THIS IS THE ONE PLACE WHERE THIS PRODUCT TOUCHES THE OUTSIDE WORLD, so three rules from the
 * roadmap are load bearing here, and every one of them lives on the backend with the dashboard only
 * reporting what it is told:
 *
 *   1. **No automatic publishing, ever** (roadmap 22.2 point 2). Nothing leaves for a platform
 *      without a human pressing a button in this session. The card therefore has no state in which
 *      a form submits itself, and no timer.
 *   2. **Consent binds to an exact payload** (L5218-5219, roadmap 16.1). The approval names a
 *      revision AND its `payload_sha256`; editing the text produces a NEW revision with a NEW hash,
 *      so the old consent cannot carry over. The dashboard sends back the hash it displayed and the
 *      backend refuses a mismatch. The dashboard computes no hash and holds no copy of that rule.
 *   3. **After the submit starts, no automatic retry** (L2673, L4560, L5335). `SUBMITTING` and a
 *      failure whose outcome is unknown are states in which the side effect may already have
 *      happened, so `may_retry` is the BACKEND's verdict; the card renders a retry control only when
 *      that verdict is `true`, exactly as the job detail screen does with its own flag.
 *
 * Parser discipline as everywhere else: every field explicit, a missing or wrongly typed field
 * fails the whole read with the field named, nothing defaulted, no state invented.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function num(row: Record<string, unknown>, field: string): number | null {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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

/**
 * The seven states of a draft. An unknown one is a FAILED read rather than an eighth pill: the card
 * decides what the operator may press from this value, and guessing would offer a button that
 * publishes something.
 */
export const DRAFT_STATES: readonly string[] = [
  "DRAFT",
  "AWAITING_APPROVAL",
  "APPROVED",
  "SUBMITTING",
  "PUBLISHED",
  "REJECTED",
  "FAILED",
];

/** Who wrote this version of the text. The operator's edits are revisions too, and they are kept. */
export const REVISION_AUTHORS: readonly string[] = ["AGENT", "OPERATOR"];

export interface DraftRevision {
  readonly revision_id: string;
  readonly body: string;
  /** Hash of exactly this text. The consent names it; an edit produces a new one. */
  readonly payload_sha256: string;
  readonly author: string;
  readonly created_at: string;
}

/** Where the comment ended up, when it did. Absent until the platform confirms it. */
export interface DraftPublication {
  readonly external_id: string;
  readonly url: string;
  readonly observed_at: string;
}

/** Why it did not go out, and whether the backend permits another attempt. */
export interface DraftFailure {
  readonly reason: string;
  readonly at: string;
  /** The BACKEND's verdict (L2673). `false` after a submit whose outcome nobody measured. */
  readonly may_retry: boolean;
}

/** How much the account may still say today. Absent means the backend did not report a limit. */
export interface DraftRateLimit {
  readonly used_today: number;
  readonly limit_per_day: number;
  readonly resets_at: string;
}

export interface Draft {
  readonly draft_id: string;
  readonly thread_id: string;
  readonly thread_ref: string;
  readonly thread_title: string;
  readonly thread_url: string;
  readonly community_display_name: string;
  readonly account: string;
  readonly state: string;
  readonly current_revision: DraftRevision;
  /** Every version, oldest first. Empty on the list read, full on the detail read. */
  readonly revisions: readonly DraftRevision[];
  readonly reasoning: string;
  readonly voice_version: number | null;
  readonly user_profile_version: number;
  readonly model: string;
  readonly prompt_name: string;
  readonly prompt_sha256: string;
  readonly created_at: string;
  readonly approved_at: string | null;
  readonly submitted_at: string | null;
  readonly published: DraftPublication | null;
  readonly failure: DraftFailure | null;
  readonly rate_limit: DraftRateLimit | null;
}

const HEX64 = /^[0-9a-f]{64}$/;

export function parseRevision(raw: unknown, what: string): Read<DraftRevision> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return failure(`${what}: rewizja nie jest obiektem`);
  }
  const row = raw as Record<string, unknown>;
  const missing: string[] = [];
  const fields = {
    revision_id: str(row, "revision_id"),
    body: str(row, "body"),
    payload_sha256: str(row, "payload_sha256"),
    author: str(row, "author"),
    created_at: str(row, "created_at"),
  };
  for (const [field, value] of Object.entries(fields)) if (value === null) missing.push(field);
  if (fields.payload_sha256 !== null && !HEX64.test(fields.payload_sha256)) {
    // The consent is built on this value. A hash of the wrong shape is a broken read, not a hash.
    missing.push("payload_sha256 (nie jest 64 znakami szesnastkowymi)");
  }
  if (fields.author !== null && !REVISION_AUTHORS.includes(fields.author)) {
    missing.push(`author='${fields.author}' nie jest znanym autorem rewizji`);
  }
  if (missing.length > 0) {
    return failure(`${what}: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return { ok: true, value: fields as unknown as DraftRevision };
}

function parsePublication(raw: unknown): Read<DraftPublication | null> {
  if (raw === null || raw === undefined) return { ok: true, value: null };
  if (typeof raw !== "object" || Array.isArray(raw)) return failure("szkic: 'published' nie jest obiektem");
  const row = raw as Record<string, unknown>;
  const external = str(row, "external_id");
  const url = str(row, "url");
  const observed = str(row, "observed_at");
  if (external === null || url === null || observed === null) {
    return failure("szkic: 'published' nie ma external_id, url albo observed_at");
  }
  return { ok: true, value: { external_id: external, url, observed_at: observed } };
}

function parseFailure(raw: unknown): Read<DraftFailure | null> {
  if (raw === null || raw === undefined) return { ok: true, value: null };
  if (typeof raw !== "object" || Array.isArray(raw)) return failure("szkic: 'failure' nie jest obiektem");
  const row = raw as Record<string, unknown>;
  const reason = str(row, "reason");
  const at = str(row, "at");
  const mayRetry = row["may_retry"];
  if (reason === null || at === null) return failure("szkic: 'failure' nie ma reason albo at");
  if (typeof mayRetry !== "boolean") {
    // Never defaulted. `true` would be permission to repeat a side effect nobody measured, and
    // `false` would be a verdict this dashboard is not entitled to make.
    return failure("szkic: 'failure.may_retry' nie jest boolem, a domyślnej wartości tu nie ma");
  }
  return { ok: true, value: { reason, at, may_retry: mayRetry } };
}

function parseRateLimit(raw: unknown): Read<DraftRateLimit | null> {
  if (raw === null || raw === undefined) return { ok: true, value: null };
  if (typeof raw !== "object" || Array.isArray(raw)) return failure("szkic: 'rate_limit' nie jest obiektem");
  const row = raw as Record<string, unknown>;
  const used = num(row, "used_today");
  const limit = num(row, "limit_per_day");
  const resets = str(row, "resets_at");
  if (used === null || limit === null || resets === null) {
    return failure("szkic: 'rate_limit' nie ma used_today, limit_per_day albo resets_at");
  }
  return { ok: true, value: { used_today: used, limit_per_day: limit, resets_at: resets } };
}

export function parseDraft(row: Record<string, unknown>): Read<Draft> {
  const missing: string[] = [];
  const strings = {
    draft_id: str(row, "draft_id"),
    thread_id: str(row, "thread_id"),
    thread_ref: str(row, "thread_ref"),
    thread_title: str(row, "thread_title"),
    thread_url: str(row, "thread_url"),
    community_display_name: str(row, "community_display_name"),
    account: str(row, "account"),
    state: str(row, "state"),
    reasoning: str(row, "reasoning"),
    model: str(row, "model"),
    prompt_name: str(row, "prompt_name"),
    prompt_sha256: str(row, "prompt_sha256"),
    created_at: str(row, "created_at"),
  };
  for (const [field, value] of Object.entries(strings)) if (value === null) missing.push(field);
  if (strings.state !== null && !DRAFT_STATES.includes(strings.state)) {
    missing.push(`state='${strings.state}' nie jest znanym stanem szkicu`);
  }
  const profileVersion = num(row, "user_profile_version");
  if (profileVersion === null) missing.push("user_profile_version");
  const voiceVersion = row["voice_version"];
  if (voiceVersion !== null && (typeof voiceVersion !== "number" || !Number.isFinite(voiceVersion))) {
    missing.push("voice_version");
  }
  const approved = nullableStr(row, "approved_at");
  const submitted = nullableStr(row, "submitted_at");
  if (!approved.present) missing.push("approved_at");
  if (!submitted.present) missing.push("submitted_at");
  if (missing.length > 0) {
    return failure(`szkic: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }

  const current = parseRevision(row["current_revision"], "szkic");
  if (!current.ok) return current;

  const rawRevisions = row["revisions"];
  const revisions: DraftRevision[] = [];
  if (rawRevisions !== undefined && rawRevisions !== null) {
    if (!Array.isArray(rawRevisions)) {
      return failure("szkic: pole 'revisions' nie jest listą, czyli to zepsuty odczyt");
    }
    for (const [index, item] of rawRevisions.entries()) {
      const parsed = parseRevision(item, `szkic: rewizja ${index}`);
      if (!parsed.ok) return parsed;
      revisions.push(parsed.value);
    }
  }

  const published = parsePublication(row["published"]);
  if (!published.ok) return published;
  const draftFailure = parseFailure(row["failure"]);
  if (!draftFailure.ok) return draftFailure;
  const rateLimit = parseRateLimit(row["rate_limit"]);
  if (!rateLimit.ok) return rateLimit;

  // A state that claims publication with nothing to point at is a claim without evidence: the
  // whole point of PUBLISHED is that somebody can open the comment.
  if (strings.state === "PUBLISHED" && published.value === null) {
    return failure("szkic: stan PUBLISHED bez dowodu publikacji (pole 'published' jest puste)");
  }
  if (strings.state === "FAILED" && draftFailure.value === null) {
    return failure("szkic: stan FAILED bez powodu (pole 'failure' jest puste)");
  }

  return {
    ok: true,
    value: {
      ...(strings as unknown as Record<keyof typeof strings, string>),
      user_profile_version: profileVersion as number,
      voice_version: typeof voiceVersion === "number" ? voiceVersion : null,
      current_revision: current.value,
      revisions,
      approved_at: (approved as { value: string | null }).value,
      submitted_at: (submitted as { value: string | null }).value,
      published: published.value,
      failure: draftFailure.value,
      rate_limit: rateLimit.value,
    } as Draft,
  };
}
