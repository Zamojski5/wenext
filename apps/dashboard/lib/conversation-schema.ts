/**
 * The conversations of Outcome D/E as the dashboard will read them — A PROPOSED CONTRACT, NOT A
 * WIRED ONE (docs/DASHBOARD-DESIGN.md §4, „Minimalny kontrakt danych dla widoku rozmów”).
 *
 * WHY THIS FILE EXISTS BEFORE THE API. The screens are built on fixtures while the browser adapter
 * and the routes are built by another session; the shape has to live in ONE place so that wiring it
 * later is a matter of adding two readers to `lib/control-plane.ts`, not of rewriting views. Until
 * the backend confirms the shape, NOTHING in `app/` imports this file — there is no route to call,
 * and inventing one would produce a screen that reads nothing while looking like it reads.
 *
 * SAME RULES AS `lib/observe-schema.ts`, and for the same reasons: every field is read explicitly,
 * a missing or wrongly typed field FAILS THE WHOLE READ with the field named, and nothing is
 * defaulted. Two of them are worth stating outright:
 *
 *   1. `score` IS AN `Observed[int]` AND STAYS A PAIR — a number, or `null` with a reason. Exactly
 *      one, mirroring the CHECK on `thread_metric_snapshots` in migration 0004. The view renders an
 *      unknown score as a stated absence, never as `0` (L5241 point 5).
 *   2. `unread_count` AND `is_new` COME FROM THE API, computed against `seen_at`. The browser does
 *      not know the database's clock, and "new" recomputed locally changes on every refresh.
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

function bool(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
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

/** A metric the platform may not show. Exactly one half is set — see the module docstring. */
export interface ObservedScore {
  readonly value: number | null;
  readonly unknownReason: string | null;
}

/**
 * `score` / `score_unknown_reason` read as the pair they are. A row with both set, or neither, is
 * a BROKEN READ: the database refuses that combination, so an API sending it is not to be guessed
 * at.
 */
function observedScore(row: Record<string, unknown>, field: string): ObservedScore | null {
  const rawValue = field in row ? row[field] : undefined;
  const rawReason = `${field}_unknown_reason` in row ? row[`${field}_unknown_reason`] : undefined;
  const hasValue = typeof rawValue === "number" && Number.isFinite(rawValue);
  const hasReason = typeof rawReason === "string" && rawReason.length > 0;
  // Exactly one half. Both set and neither set are the two combinations the database refuses, so
  // an API sending either is a broken read rather than something to guess at.
  if (hasValue === hasReason) return null;
  if (hasValue) return { value: rawValue as number, unknownReason: null };
  return { value: null, unknownReason: rawReason as string };
}

/** The three states of a conversation. The enum stays in `data-state`; the view shows Polish. */
export const CONVERSATION_STATES: readonly string[] = ["AWAITING_REPLY", "REPLIED", "CLOSED"];

export interface ConversationMessage {
  readonly message_id: string;
  readonly external_id: string;
  readonly author: string;
  /** `true` — written from the user's own account. The view draws it as the user's card. */
  readonly is_ours: boolean;
  readonly is_thread_author: boolean;
  readonly body: string;
  readonly created_at_source: string;
  readonly observed_at: string;
  readonly depth: number;
  readonly parent_external_id: string | null;
  readonly score: ObservedScore;
  /** `true` — newer than `seen_at`. Computed by the API, never here. */
  readonly is_new: boolean;
}

export interface ConversationSummary {
  readonly conversation_id: string;
  readonly community_ref: string;
  readonly community_display_name: string;
  readonly thread_id: string;
  readonly thread_ref: string;
  readonly thread_title: string;
  readonly thread_url: string;
  readonly state: string;
  readonly our_account: string;
  readonly our_message_at: string;
  readonly last_message_at: string;
  readonly last_message_author: string;
  readonly last_message_excerpt: string;
  readonly unread_count: number;
  readonly reply_count: number;
  readonly seen_at: string | null;
  readonly score: ObservedScore;
}

export interface ConversationDetail extends ConversationSummary {
  readonly messages: readonly ConversationMessage[];
}

const SUMMARY_STRINGS = [
  "conversation_id",
  "community_ref",
  "community_display_name",
  "thread_id",
  "thread_ref",
  "thread_title",
  "thread_url",
  "state",
  "our_account",
  "our_message_at",
  "last_message_at",
  "last_message_author",
  "last_message_excerpt",
] as const;

export function parseConversation(row: Record<string, unknown>): Read<ConversationSummary> {
  const missing: string[] = [];
  const strings: Record<string, string> = {};
  for (const field of SUMMARY_STRINGS) {
    const value = str(row, field);
    if (value === null) missing.push(field);
    else strings[field] = value;
  }
  const unread = num(row, "unread_count");
  const replies = num(row, "reply_count");
  const seen = nullableStr(row, "seen_at");
  const score = observedScore(row, "score");
  if (unread === null) missing.push("unread_count");
  if (replies === null) missing.push("reply_count");
  if (!seen.present) missing.push("seen_at");
  if (score === null) missing.push("score / score_unknown_reason (dokładnie jedno)");
  if (strings["state"] !== undefined && !CONVERSATION_STATES.includes(strings["state"])) {
    // An unknown state is a FAILED read, not a fourth pill: the view would have to guess what the
    // operator is looking at, and "guessed" is the one thing this dashboard may not render.
    missing.push(`state='${strings["state"]}' nie jest znanym stanem rozmowy`);
  }
  if (missing.length > 0) {
    return failure(`rozmowa: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return {
    ok: true,
    value: {
      ...(strings as unknown as Record<(typeof SUMMARY_STRINGS)[number], string>),
      unread_count: unread as number,
      reply_count: replies as number,
      seen_at: (seen as { value: string | null }).value,
      score: score as ObservedScore,
    } as ConversationSummary,
  };
}

export function parseMessage(row: Record<string, unknown>): Read<ConversationMessage> {
  const missing: string[] = [];
  const strings = {
    message_id: str(row, "message_id"),
    external_id: str(row, "external_id"),
    author: str(row, "author"),
    body: str(row, "body"),
    created_at_source: str(row, "created_at_source"),
    observed_at: str(row, "observed_at"),
  };
  const depth = num(row, "depth");
  const ours = bool(row, "is_ours");
  const threadAuthor = bool(row, "is_thread_author");
  const isNew = bool(row, "is_new");
  const parent = nullableStr(row, "parent_external_id");
  const score = observedScore(row, "score");
  for (const [field, value] of Object.entries(strings)) if (value === null) missing.push(field);
  if (depth === null || depth < 0) missing.push("depth");
  if (ours === null) missing.push("is_ours");
  if (threadAuthor === null) missing.push("is_thread_author");
  if (isNew === null) missing.push("is_new");
  if (!parent.present) missing.push("parent_external_id");
  if (score === null) missing.push("score / score_unknown_reason (dokładnie jedno)");
  if (missing.length > 0) {
    return failure(`wiadomość: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return {
    ok: true,
    value: {
      ...(strings as Record<keyof typeof strings, string>),
      depth: depth as number,
      is_ours: ours as boolean,
      is_thread_author: threadAuthor as boolean,
      is_new: isNew as boolean,
      parent_external_id: (parent as { value: string | null }).value,
      score: score as ObservedScore,
    },
  };
}

export function parseConversationDetail(
  envelope: Record<string, unknown>,
): Read<ConversationDetail> {
  const summary = parseConversation(envelope);
  if (!summary.ok) return summary;
  const raw = envelope["messages"];
  if (!Array.isArray(raw)) {
    // Not "then there are no messages": a conversation with none and a response that lost the
    // field are different facts, and only one of them is a record.
    return failure("rozmowa: pole 'messages' nie jest listą, czyli to zepsuty odczyt");
  }
  const messages: ConversationMessage[] = [];
  for (const [index, item] of raw.entries()) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      return failure(`rozmowa: wiadomość ${index} nie jest obiektem`);
    }
    const parsed = parseMessage(item as Record<string, unknown>);
    if (!parsed.ok) return parsed;
    messages.push(parsed.value);
  }
  return { ok: true, value: { ...summary.value, messages } };
}
