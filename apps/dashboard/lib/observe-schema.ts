/**
 * The observe module's JSON (`api/communities.py`, `api/judgments.py`, `api/user_profile.py`),
 * checked field by field before any of it reaches the `/community` screen.
 *
 * Same rule as `lib/schema.ts`, and for the same reason: a missing field is a FAILURE of the whole
 * read, never a blank cell. A blank on this screen would read as "the agent had nothing to say",
 * which is a claim about a judgment nobody made.
 *
 * `picture.content` is the one field kept as an opaque object: its shape belongs to the prompt
 * (`control_plane/observe/prompts/community_picture.v1.md`) and changes with it, and the control
 * plane already validated it against the output schema and dropped every observation without
 * evidence (`observe/context.py::validate_picture`). The view reads the keys it knows and renders
 * their absence as "the picture does not say", not as nothing.
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

export interface CommunityCounts {
  readonly threads: number;
  readonly comments: number;
  readonly snapshots: number;
  readonly judgments: number;
  readonly pictures: number;
}

export interface CommunitySummary {
  readonly community_id: string;
  readonly platform: string;
  readonly community_key: string;
  readonly ref: string;
  readonly display_name: string;
  readonly access_mode: string;
  readonly observe_enabled: boolean;
  readonly poll_interval_seconds: number;
  readonly last_polled_at: string | null;
  readonly watermark_created_at: string | null;
  readonly created_at: string;
  /**
   * 0025: has a PERSON decided that we may found our own threads here. Three states, and `null`
   * is not missing data: it means nobody decided, which is a refusal with a question attached.
   * Absent from an older control plane, and then read as `null` for the same reason.
   */
  readonly own_posts_allowed: boolean | null;
  readonly own_posts_decided_at: string | null;
  /** Present on the list endpoint, absent on the create response. */
  readonly counts: CommunityCounts | null;
}

export function parseCommunity(row: Record<string, unknown>): Read<CommunitySummary> {
  const missing: string[] = [];
  const communityId = str(row, "community_id");
  const platform = str(row, "platform");
  const communityKey = str(row, "community_key");
  const ref = str(row, "ref");
  const displayName = str(row, "display_name");
  const accessMode = str(row, "access_mode");
  const createdAt = str(row, "created_at");
  const pollInterval = num(row, "poll_interval_seconds");
  const enabled = row["observe_enabled"];
  const lastPolled = nullableStr(row, "last_polled_at");
  const watermark = nullableStr(row, "watermark_created_at");
  // Trzy stany, a nie dwa: `true`, `false` i „nikt nie zdecydował". Brak klucza (starsze api)
  // czyta się tak samo jak brak decyzji, bo tym właśnie jest.
  const ownPosts = row["own_posts_allowed"];
  const ownPostsAllowed = typeof ownPosts === "boolean" ? ownPosts : null;
  const ownPostsDecided = nullableStr(row, "own_posts_decided_at");
  if (communityId === null) missing.push("community_id");
  if (platform === null) missing.push("platform");
  if (communityKey === null) missing.push("community_key");
  if (ref === null) missing.push("ref");
  if (displayName === null) missing.push("display_name");
  if (accessMode === null) missing.push("access_mode");
  if (createdAt === null) missing.push("created_at");
  if (pollInterval === null) missing.push("poll_interval_seconds");
  if (typeof enabled !== "boolean") missing.push("observe_enabled");
  if (!lastPolled.present) missing.push("last_polled_at");
  if (!watermark.present) missing.push("watermark_created_at");

  let counts: CommunityCounts | null = null;
  if ("counts" in row) {
    const raw = row["counts"];
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
      missing.push("counts");
    } else {
      const record = raw as Record<string, unknown>;
      const fields = ["threads", "comments", "snapshots", "judgments", "pictures"] as const;
      const parsed: Partial<Record<(typeof fields)[number], number>> = {};
      for (const field of fields) {
        const value = num(record, field);
        if (value === null) missing.push(`counts.${field}`);
        else parsed[field] = value;
      }
      if (missing.length === 0) counts = parsed as CommunityCounts;
    }
  }
  if (missing.length > 0) {
    return failure(`community: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return {
    ok: true,
    value: {
      community_id: communityId as string,
      platform: platform as string,
      community_key: communityKey as string,
      ref: ref as string,
      display_name: displayName as string,
      access_mode: accessMode as string,
      observe_enabled: enabled as boolean,
      own_posts_allowed: ownPostsAllowed,
      own_posts_decided_at: ownPostsDecided.present ? ownPostsDecided.value : null,
      poll_interval_seconds: pollInterval as number,
      last_polled_at: (lastPolled as { value: string | null }).value,
      watermark_created_at: (watermark as { value: string | null }).value,
      created_at: createdAt as string,
      counts,
    },
  };
}

export interface CommunityPicture {
  readonly picture_id: string;
  readonly community_id: string;
  readonly version: number;
  readonly window_start: string;
  readonly window_end: string;
  readonly thread_count: number;
  readonly invalid_ref_count: number;
  readonly model: string;
  readonly effort: string;
  readonly prompt_name: string;
  readonly prompt_sha256: string;
  readonly created_at: string;
  /** Opaque on purpose — see the module docstring. */
  readonly content: Record<string, unknown>;
}

export function parsePicture(row: Record<string, unknown>): Read<CommunityPicture> {
  const missing: string[] = [];
  const strings = {
    picture_id: str(row, "picture_id"),
    community_id: str(row, "community_id"),
    window_start: str(row, "window_start"),
    window_end: str(row, "window_end"),
    model: str(row, "model"),
    effort: str(row, "effort"),
    prompt_name: str(row, "prompt_name"),
    prompt_sha256: str(row, "prompt_sha256"),
    created_at: str(row, "created_at"),
  };
  const numbers = {
    version: num(row, "version"),
    thread_count: num(row, "thread_count"),
    invalid_ref_count: num(row, "invalid_ref_count"),
  };
  for (const [field, value] of Object.entries(strings)) if (value === null) missing.push(field);
  for (const [field, value] of Object.entries(numbers)) if (value === null) missing.push(field);
  const content = row["content"];
  if (typeof content !== "object" || content === null || Array.isArray(content)) missing.push("content");
  if (missing.length > 0) {
    return failure(`picture: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return {
    ok: true,
    value: {
      ...(strings as Record<keyof typeof strings, string>),
      ...(numbers as Record<keyof typeof numbers, number>),
      content: content as Record<string, unknown>,
    },
  };
}

export interface JudgmentSummary {
  readonly judgment_id: string;
  readonly thread_id: string;
  readonly thread_ref: string;
  readonly thread_title: string;
  readonly thread_url: string;
  readonly community_display_name: string;
  readonly decision: string;
  readonly reasoning: string;
  readonly what_user_could_add: string | null;
  readonly confidence: number;
  readonly user_profile_version: number;
  readonly model: string;
  readonly created_at: string;
  readonly latest_feedback: string | null;
  readonly latest_feedback_note: string | null;
}

export function parseJudgment(row: Record<string, unknown>): Read<JudgmentSummary> {
  const missing: string[] = [];
  const strings = {
    judgment_id: str(row, "judgment_id"),
    thread_id: str(row, "thread_id"),
    thread_ref: str(row, "thread_ref"),
    thread_title: str(row, "thread_title"),
    thread_url: str(row, "thread_url"),
    community_display_name: str(row, "community_display_name"),
    decision: str(row, "decision"),
    reasoning: str(row, "reasoning"),
    model: str(row, "model"),
    created_at: str(row, "created_at"),
  };
  const numbers = { confidence: num(row, "confidence"), user_profile_version: num(row, "user_profile_version") };
  const contribution = nullableStr(row, "what_user_could_add");
  const feedback = nullableStr(row, "latest_feedback");
  const note = nullableStr(row, "latest_feedback_note");
  for (const [field, value] of Object.entries(strings)) if (value === null) missing.push(field);
  for (const [field, value] of Object.entries(numbers)) if (value === null) missing.push(field);
  if (!contribution.present) missing.push("what_user_could_add");
  if (!feedback.present) missing.push("latest_feedback");
  if (!note.present) missing.push("latest_feedback_note");
  if (missing.length > 0) {
    return failure(`judgment: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return {
    ok: true,
    value: {
      ...(strings as Record<keyof typeof strings, string>),
      ...(numbers as Record<keyof typeof numbers, number>),
      what_user_could_add: (contribution as { value: string | null }).value,
      latest_feedback: (feedback as { value: string | null }).value,
      latest_feedback_note: (note as { value: string | null }).value,
    },
  };
}

/** Counts per decision and per (latest) feedback verdict — `api/judgments.py` `summary`. */
export interface JudgmentTotals {
  readonly decision_ENGAGE: number;
  readonly decision_IGNORE: number;
  readonly decision_WAIT: number;
  readonly feedback_AGREE: number;
  readonly feedback_DISAGREE: number;
  readonly feedback_IRRELEVANT: number;
}

export const TOTAL_FIELDS: readonly (keyof JudgmentTotals)[] = [
  "decision_ENGAGE",
  "decision_IGNORE",
  "decision_WAIT",
  "feedback_AGREE",
  "feedback_DISAGREE",
  "feedback_IRRELEVANT",
];

export function parseTotals(row: Record<string, unknown>): Read<JudgmentTotals> {
  const parsed: Record<string, number> = {};
  const missing: string[] = [];
  for (const field of TOTAL_FIELDS) {
    const value = num(row, field);
    if (value === null) missing.push(field);
    else parsed[field] = value;
  }
  if (missing.length > 0) {
    return failure(`summary: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return { ok: true, value: parsed as unknown as JudgmentTotals };
}

export interface UserProfile {
  readonly profile_id: string;
  readonly version: number;
  readonly knows_deeply: string;
  readonly positions: string;
  readonly would_never_claim: string;
  readonly voice_samples: string;
  readonly created_at: string;
}

export function parseProfile(row: Record<string, unknown>): Read<UserProfile> {
  const missing: string[] = [];
  const strings = {
    profile_id: str(row, "profile_id"),
    knows_deeply: str(row, "knows_deeply"),
    positions: str(row, "positions"),
    would_never_claim: str(row, "would_never_claim"),
    voice_samples: str(row, "voice_samples"),
    created_at: str(row, "created_at"),
  };
  const version = num(row, "version");
  for (const [field, value] of Object.entries(strings)) if (value === null) missing.push(field);
  if (version === null) missing.push("version");
  if (missing.length > 0) {
    return failure(`profile: brakujące albo złego typu pola: ${missing.join(", ")}`);
  }
  return { ok: true, value: { ...(strings as Record<keyof typeof strings, string>), version: version as number } };
}
