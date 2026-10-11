/**
 * The JSON of `api/workspaces.py`, checked field by field before any of it reaches the studio.
 *
 * Same rule as `lib/schema.ts` and `lib/observe-schema.ts`: a missing or wrongly typed field
 * FAILS THE WHOLE READ. It never becomes a blank cell and never becomes `false`. On this screen
 * the difference is not cosmetic — `support.comment` rendered as `false` because the field was
 * absent would tell the user that the product cannot reply on a platform where it can, and
 * `active` defaulting to `false` would show an attached account as detached.
 *
 * `support` is allowed to be `null`, and only `null`: that is the control plane saying "this row
 * names a platform the capability matrix no longer knows". The screen prints it as "integracja
 * niedostępna", which is the safe direction — the unsafe one is a missing matrix rendering as a
 * working integration.
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

/** A field that must be present and either a string or `null`. Absence is NOT `null`. */
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

function flag(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
}

function report(what: string, missing: readonly string[]): Read<never> {
  return failure(
    `${what}: brakuje pól albo mają zły typ (${missing.join(", ")}). To zepsuty odczyt, ` +
      "nie puste dane",
  );
}

// ---------------------------------------------------------------------------
// Platform capabilities
// ---------------------------------------------------------------------------

export interface PlatformSupport {
  readonly platform: string;
  readonly label: string;
  readonly integrated: boolean;
  readonly read: boolean;
  readonly comment: boolean;
  readonly own_post: boolean;
  readonly effect_readback: boolean;
  readonly audience_metrics: boolean;
  /** Polish sentence for a platform with no integration; `null` when there is one. */
  readonly note: string | null;
}

const SUPPORT_FLAGS = [
  "integrated",
  "read",
  "comment",
  "own_post",
  "effect_readback",
  "audience_metrics",
] as const;

export function parsePlatformSupport(row: Record<string, unknown>): Read<PlatformSupport> {
  const missing: string[] = [];
  const platform = str(row, "platform");
  const label = str(row, "label");
  const note = nullableStr(row, "note");
  if (platform === null) missing.push("platform");
  if (label === null) missing.push("label");
  if (!note.present) missing.push("note");
  const flags: Record<string, boolean> = {};
  for (const name of SUPPORT_FLAGS) {
    const value = flag(row, name);
    if (value === null) {
      missing.push(name);
      continue;
    }
    flags[name] = value;
  }
  if (missing.length > 0) return report("możliwości platformy", missing);
  return {
    ok: true,
    value: {
      platform: platform as string,
      label: label as string,
      integrated: flags["integrated"] as boolean,
      read: flags["read"] as boolean,
      comment: flags["comment"] as boolean,
      own_post: flags["own_post"] as boolean,
      effect_readback: flags["effect_readback"] as boolean,
      audience_metrics: flags["audience_metrics"] as boolean,
      note: note.present ? note.value : null,
    },
  };
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

export interface WorkspaceSummary {
  readonly workspace_id: string;
  readonly name: string;
  readonly created_at: string;
}

export function parseWorkspace(row: Record<string, unknown>): Read<WorkspaceSummary> {
  const missing: string[] = [];
  const id = str(row, "workspace_id");
  const name = str(row, "name");
  const createdAt = str(row, "created_at");
  if (id === null) missing.push("workspace_id");
  if (name === null) missing.push("name");
  if (createdAt === null) missing.push("created_at");
  if (missing.length > 0) return report("workspace", missing);
  return {
    ok: true,
    value: { workspace_id: id as string, name: name as string, created_at: createdAt as string },
  };
}

export interface ProjectSummary {
  readonly project_id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly goal: string;
  readonly mode: string;
  readonly paused: boolean;
  readonly paused_at: string | null;
  readonly archived: boolean;
  readonly created_at: string;
  /**
   * 0026: limit JEDNEJ rozmowy w tym projekcie. `null` znaczy „nikt nie ustawił", czyli
   * obowiązuje wartość domyślna produktu, i ekran ma to powiedzieć zamiast pokazać puste pole
   * bez wyjaśnienia. Klucze są te same, co w sesji, więc nic się po drodze nie tłumaczy.
   */
  readonly conversation_budget: ConversationBudget | null;
  /**
   * Wartość domyślna produktu, przysłana przez control plane, żeby ekran nie znał jej na
   * pamięć: liczba wpisana na sztywno w dashboardzie rozjechałaby się z backendem przy
   * pierwszej zmianie i mówiłaby o limicie, którego nie ma.
   */
  readonly conversation_budget_default: ConversationBudget | null;
}

export interface ConversationBudget {
  readonly max_turns?: number;
  readonly max_tool_calls?: number;
}

export function parseProject(row: Record<string, unknown>): Read<ProjectSummary> {
  const missing: string[] = [];
  const id = str(row, "project_id");
  const workspaceId = str(row, "workspace_id");
  const name = str(row, "name");
  const goal = str(row, "goal");
  const mode = str(row, "mode");
  const createdAt = str(row, "created_at");
  const paused = flag(row, "paused");
  const archived = flag(row, "archived");
  const pausedAt = nullableStr(row, "paused_at");
  if (id === null) missing.push("project_id");
  if (workspaceId === null) missing.push("workspace_id");
  if (name === null) missing.push("name");
  if (goal === null) missing.push("goal");
  if (mode === null) missing.push("mode");
  if (createdAt === null) missing.push("created_at");
  if (paused === null) missing.push("paused");
  if (archived === null) missing.push("archived");
  if (!pausedAt.present) missing.push("paused_at");
  if (missing.length > 0) return report("projekt", missing);
  return {
    ok: true,
    value: {
      project_id: id as string,
      workspace_id: workspaceId as string,
      name: name as string,
      goal: goal as string,
      mode: mode as string,
      paused: paused as boolean,
      paused_at: pausedAt.present ? pausedAt.value : null,
      archived: archived as boolean,
      created_at: createdAt as string,
      conversation_budget: parseConversationBudget(row["conversation_budget"]),
      conversation_budget_default: parseConversationBudget(row["conversation_budget_default"]),
    },
  };
}

/** Liczby albo `null`. Zero i wartość spoza zakresu są odczytem zepsutym, nie ustawieniem. */
function parseConversationBudget(raw: unknown): ConversationBudget | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  const budget: { max_turns?: number; max_tool_calls?: number } = {};
  for (const key of ["max_turns", "max_tool_calls"] as const) {
    const value = record[key];
    if (typeof value === "number" && Number.isInteger(value) && value > 0) budget[key] = value;
  }
  return Object.keys(budget).length === 0 ? null : budget;
}

// ---------------------------------------------------------------------------
// Głos osoby (0022)
// ---------------------------------------------------------------------------

/** Jedna wersja głosu. `created_by` jest `null` dla sesji operatora sprzed P7. */
export interface PersonVoiceVersion {
  readonly voice_version_id: string;
  readonly person_profile_id: string;
  readonly version: number;
  readonly knows_deeply: string;
  readonly positions: string;
  readonly would_never_claim: string;
  readonly voice_samples: string;
  readonly created_by: string | null;
  readonly created_at: string;
}

/**
 * Treść profilu pilota zaproponowana jako pierwsza wersja głosu.
 *
 * Osobny typ od `PersonVoiceVersion`, choć pola są te same, bo to jest coś innego: propozycja
 * do przeczytania i zatwierdzenia przez człowieka, a nie zapisany głos. Jeden typ dla obu
 * skończyłby się ekranem, który pokazuje propozycję tak samo jak zapis.
 */
export interface PilotVoiceProposal {
  readonly user_profile_version_id: string;
  readonly version: number;
  readonly knows_deeply: string;
  readonly positions: string;
  readonly would_never_claim: string;
  readonly voice_samples: string;
  readonly created_at: string;
}

const VOICE_TEXT_FIELDS = [
  "knows_deeply",
  "positions",
  "would_never_claim",
  "voice_samples",
] as const;

function voiceTexts(
  row: Record<string, unknown>,
  missing: string[],
): Record<(typeof VOICE_TEXT_FIELDS)[number], string> {
  const texts: Record<string, string> = {};
  for (const field of VOICE_TEXT_FIELDS) {
    const value = str(row, field);
    if (value === null) missing.push(field);
    else texts[field] = value;
  }
  return texts as Record<(typeof VOICE_TEXT_FIELDS)[number], string>;
}

export function parsePersonVoice(row: Record<string, unknown>): Read<PersonVoiceVersion> {
  const missing: string[] = [];
  const id = str(row, "voice_version_id");
  const profileId = str(row, "person_profile_id");
  const version = num(row, "version");
  const createdAt = str(row, "created_at");
  // Obecne i albo tekst, albo null. Brak pola to zepsuty odczyt, a nie „nie wiadomo kto”.
  const createdBy = nullableStr(row, "created_by");
  const texts = voiceTexts(row, missing);
  if (id === null) missing.push("voice_version_id");
  if (profileId === null) missing.push("person_profile_id");
  if (version === null) missing.push("version");
  if (createdAt === null) missing.push("created_at");
  if (!createdBy.present) missing.push("created_by");
  if (missing.length > 0) return report("wersja głosu osoby", missing);
  return {
    ok: true,
    value: {
      voice_version_id: id as string,
      person_profile_id: profileId as string,
      version: version as number,
      knows_deeply: texts.knows_deeply,
      positions: texts.positions,
      would_never_claim: texts.would_never_claim,
      voice_samples: texts.voice_samples,
      created_by: createdBy.present ? createdBy.value : null,
      created_at: createdAt as string,
    },
  };
}

export function parseVoiceProposal(row: Record<string, unknown>): Read<PilotVoiceProposal> {
  const missing: string[] = [];
  const id = str(row, "user_profile_version_id");
  const version = num(row, "version");
  const createdAt = str(row, "created_at");
  const texts = voiceTexts(row, missing);
  if (id === null) missing.push("user_profile_version_id");
  if (version === null) missing.push("version");
  if (createdAt === null) missing.push("created_at");
  if (missing.length > 0) return report("propozycja głosu z profilu pilota", missing);
  return {
    ok: true,
    value: {
      user_profile_version_id: id as string,
      version: version as number,
      knows_deeply: texts.knows_deeply,
      positions: texts.positions,
      would_never_claim: texts.would_never_claim,
      voice_samples: texts.voice_samples,
      created_at: createdAt as string,
    },
  };
}

export interface PersonProfileSummary {
  readonly person_profile_id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly kind: string;
  readonly created_at: string;
  readonly archived: boolean;
}

export function parsePersonProfile(row: Record<string, unknown>): Read<PersonProfileSummary> {
  const missing: string[] = [];
  const id = str(row, "person_profile_id");
  const workspaceId = str(row, "workspace_id");
  const name = str(row, "name");
  const kind = str(row, "kind");
  const createdAt = str(row, "created_at");
  const archived = flag(row, "archived");
  if (id === null) missing.push("person_profile_id");
  if (workspaceId === null) missing.push("workspace_id");
  if (name === null) missing.push("name");
  if (kind === null) missing.push("kind");
  if (createdAt === null) missing.push("created_at");
  if (archived === null) missing.push("archived");
  if (missing.length > 0) return report("profil osoby", missing);
  return {
    ok: true,
    value: {
      person_profile_id: id as string,
      workspace_id: workspaceId as string,
      name: name as string,
      kind: kind as string,
      created_at: createdAt as string,
      archived: archived as boolean,
    },
  };
}

/**
 * The `support` field, which may be an object or `null` and nothing else.
 *
 * `null` is the control plane saying "this row names a platform the capability matrix no longer
 * knows"; the screen renders that as "integracja niedostępna", which is the safe direction. A
 * string, a number or an array is a broken read, because the unsafe direction — a missing matrix
 * rendering as a working integration — is the one mistake in this area that reaches the public.
 */
function supportField(
  row: Record<string, unknown>,
  what: string,
): Read<PlatformSupport | null> {
  if (!("support" in row)) {
    return failure(`${what}: brak pola 'support'. To zepsuty odczyt, nie brak integracji`);
  }
  const raw = row["support"];
  if (raw === null) return { ok: true, value: null };
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return failure(`${what}: pole 'support' nie jest obiektem ani null-em`);
  }
  return parsePlatformSupport(raw as Record<string, unknown>);
}

export interface AccountSummary {
  readonly social_account_id: string;
  readonly platform: string;
  readonly external_account_id: string;
  readonly state: string;
  readonly created_at: string;
  readonly person_profile_id: string | null;
  readonly attached_at: string | null;
  readonly support: PlatformSupport | null;
}

export function parseAccount(row: Record<string, unknown>): Read<AccountSummary> {
  const missing: string[] = [];
  const id = str(row, "social_account_id");
  const platform = str(row, "platform");
  const handle = str(row, "external_account_id");
  const state = str(row, "state");
  const createdAt = str(row, "created_at");
  const profileId = nullableStr(row, "person_profile_id");
  const attachedAt = nullableStr(row, "attached_at");
  if (id === null) missing.push("social_account_id");
  if (platform === null) missing.push("platform");
  if (handle === null) missing.push("external_account_id");
  if (state === null) missing.push("state");
  if (createdAt === null) missing.push("created_at");
  if (!profileId.present) missing.push("person_profile_id");
  if (!attachedAt.present) missing.push("attached_at");
  if (missing.length > 0) return report("konto platformy", missing);

  const support = supportField(row, "konto platformy");
  if (!support.ok) return support;

  return {
    ok: true,
    value: {
      social_account_id: id as string,
      platform: platform as string,
      external_account_id: handle as string,
      state: state as string,
      created_at: createdAt as string,
      person_profile_id: profileId.present ? profileId.value : null,
      attached_at: attachedAt.present ? attachedAt.value : null,
      support: support.value,
    },
  };
}

export interface AttachmentSummary {
  readonly attachment_id: string;
  readonly person_profile_id: string;
  readonly social_account_id: string;
  readonly platform: string;
  readonly external_account_id: string;
  readonly attached_at: string;
  readonly detached_at: string | null;
  readonly active: boolean;
}

export function parseAttachment(row: Record<string, unknown>): Read<AttachmentSummary> {
  const missing: string[] = [];
  const id = str(row, "attachment_id");
  const profileId = str(row, "person_profile_id");
  const accountId = str(row, "social_account_id");
  const platform = str(row, "platform");
  const handle = str(row, "external_account_id");
  const attachedAt = str(row, "attached_at");
  const detachedAt = nullableStr(row, "detached_at");
  const active = flag(row, "active");
  if (id === null) missing.push("attachment_id");
  if (profileId === null) missing.push("person_profile_id");
  if (accountId === null) missing.push("social_account_id");
  if (platform === null) missing.push("platform");
  if (handle === null) missing.push("external_account_id");
  if (attachedAt === null) missing.push("attached_at");
  if (!detachedAt.present) missing.push("detached_at");
  if (active === null) missing.push("active");
  if (missing.length > 0) return report("przypięcie konta", missing);
  return {
    ok: true,
    value: {
      attachment_id: id as string,
      person_profile_id: profileId as string,
      social_account_id: accountId as string,
      platform: platform as string,
      external_account_id: handle as string,
      attached_at: attachedAt as string,
      detached_at: detachedAt.present ? detachedAt.value : null,
      active: active as boolean,
    },
  };
}

export interface AssignmentSummary {
  /** Capabilities of this assignment's platform, so the project screen needs no second lookup. */
  readonly support: PlatformSupport | null;
  readonly assignment_id: string;
  readonly project_id: string;
  readonly social_account_id: string;
  readonly platform: string;
  readonly external_account_id: string;
  readonly assigned_at: string;
  readonly unassigned_at: string | null;
  readonly active: boolean;
}

/**
 * The latest follower count of one account (P6 etap A; X since 27.09, read off our own profile
 * by the hourly collection). An account nobody measured has NO row, never a row with zero, and
 * the change over a day is `null` until there is a reading a day older than the latest.
 */
export interface FollowerReading {
  readonly social_account_id: string;
  readonly obserwujacy: number;
  readonly odczyt: string;
  readonly doba_temu: number | null;
  readonly zmiana_doby: number | null;
}

export function parseFollowerReading(row: Record<string, unknown>): Read<FollowerReading> {
  const missing: string[] = [];
  const accountId = str(row, "social_account_id");
  const followers = num(row, "obserwujacy");
  const readAt = str(row, "odczyt");
  const dayAgo = row["doba_temu"] === null ? null : num(row, "doba_temu");
  const change = row["zmiana_doby"] === null ? null : num(row, "zmiana_doby");
  if (accountId === null) missing.push("social_account_id");
  if (followers === null) missing.push("obserwujacy");
  if (readAt === null) missing.push("odczyt");
  if (!("doba_temu" in row) || (row["doba_temu"] !== null && dayAgo === null)) {
    missing.push("doba_temu");
  }
  if (!("zmiana_doby" in row) || (row["zmiana_doby"] !== null && change === null)) {
    missing.push("zmiana_doby");
  }
  if (missing.length > 0) return report("odczyt obserwujących", missing);
  return {
    ok: true,
    value: {
      social_account_id: accountId as string,
      obserwujacy: followers as number,
      odczyt: readAt as string,
      doba_temu: dayAgo,
      zmiana_doby: change,
    },
  };
}

export function parseAssignment(row: Record<string, unknown>): Read<AssignmentSummary> {
  const missing: string[] = [];
  const id = str(row, "assignment_id");
  const projectId = str(row, "project_id");
  const accountId = str(row, "social_account_id");
  const platform = str(row, "platform");
  const handle = str(row, "external_account_id");
  const assignedAt = str(row, "assigned_at");
  const unassignedAt = nullableStr(row, "unassigned_at");
  const active = flag(row, "active");
  if (id === null) missing.push("assignment_id");
  if (projectId === null) missing.push("project_id");
  if (accountId === null) missing.push("social_account_id");
  if (platform === null) missing.push("platform");
  if (handle === null) missing.push("external_account_id");
  if (assignedAt === null) missing.push("assigned_at");
  if (!unassignedAt.present) missing.push("unassigned_at");
  if (active === null) missing.push("active");
  if (missing.length > 0) return report("przypisanie konta", missing);
  const support = supportField(row, "przypisanie konta");
  if (!support.ok) return support;
  return {
    ok: true,
    value: {
      support: support.value,
      assignment_id: id as string,
      project_id: projectId as string,
      social_account_id: accountId as string,
      platform: platform as string,
      external_account_id: handle as string,
      assigned_at: assignedAt as string,
      unassigned_at: unassignedAt.present ? unassignedAt.value : null,
      active: active as boolean,
    },
  };
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export interface ResultCommunity {
  readonly community_id: string;
  readonly platform: string;
  readonly community_key: string;
  readonly display_name: string;
  readonly access_mode: string;
  readonly observe_enabled: boolean;
  readonly last_polled_at: string | null;
  readonly thread_count: number;
}

export function parseResultCommunity(row: Record<string, unknown>): Read<ResultCommunity> {
  const missing: string[] = [];
  const id = str(row, "community_id");
  const platform = str(row, "platform");
  const key = str(row, "community_key");
  const displayName = str(row, "display_name");
  const accessMode = str(row, "access_mode");
  const enabled = flag(row, "observe_enabled");
  const lastPolled = nullableStr(row, "last_polled_at");
  const threads = num(row, "thread_count");
  if (id === null) missing.push("community_id");
  if (platform === null) missing.push("platform");
  if (key === null) missing.push("community_key");
  if (displayName === null) missing.push("display_name");
  if (accessMode === null) missing.push("access_mode");
  if (enabled === null) missing.push("observe_enabled");
  if (!lastPolled.present) missing.push("last_polled_at");
  if (threads === null) missing.push("thread_count");
  if (missing.length > 0) return report("społeczność", missing);
  return {
    ok: true,
    value: {
      community_id: id as string,
      platform: platform as string,
      community_key: key as string,
      display_name: displayName as string,
      access_mode: accessMode as string,
      observe_enabled: enabled as boolean,
      last_polled_at: lastPolled.present ? lastPolled.value : null,
      thread_count: threads as number,
    },
  };
}

/** Rodzaje z 0023. Zamknięta para, bo trzeci rodzaj po jednej stronie ma być błędem odczytu. */
export const PUBLICATION_KINDS = ["REPLY", "OWN_POST"] as const;
export type PublicationKind = (typeof PUBLICATION_KINDS)[number];

/** Etykieta dla człowieka. Słowa z bazy (`REPLY`, `OWN_POST`) na ekran nie trafiają. */
export function publicationKindLabel(kind: PublicationKind): string {
  return kind === "OWN_POST" ? "nasz wątek" : "odpowiedź";
}

/**
 * Does a prepared draft aim at a post of OUR OWN rather than a reply under somebody else's?
 *
 * Read from the target's identifier, because a draft waiting for a decision has no `kind` yet.
 * Two spellings (26.09): Reddit names the community (`r/SaaS`) and X names the account posting
 * (`x:@handle`), while a reply names the thing it answers (`t3_abc` on Reddit, `x:<digits>` on X,
 * the id of the post under which it goes).
 */
export function targetIsOwnPost(targetExternalId: string): boolean {
  return targetExternalId.startsWith("r/") || targetExternalId.startsWith("x:@");
}

/**
 * The words on the link to a published item. On X the item is a post whether it answers
 * somebody or not, so "odpowiedź" would be the wrong word there.
 */
export function permalinkLabel(permalink: string): string {
  let host = "";
  try {
    host = new URL(permalink).hostname.toLowerCase();
  } catch {
    host = "";
  }
  return host === "x.com" || host.endsWith(".x.com")
    ? "Otwórz post na X"
    : "Otwórz opublikowaną odpowiedź";
}

export interface ResultPublication {
  readonly request_id: string;
  readonly status: string;
  /** 0023: czy to nasz własny wątek, czy odpowiedź pod cudzym. */
  readonly kind: PublicationKind;
  readonly target_url: string;
  /** Exact published item; null until execution produced evidence. */
  readonly permalink: string | null;
  readonly published_at: string | null;
  readonly queued_at: string;
  /**
   * `null` means NOBODY LOOKED. Three values, not two: a publication whose visibility was never
   * measured must not render as "usunięta" and must not render as "widoczna".
   */
  readonly still_present: boolean | null;
  readonly observed_at: string | null;
  /**
   * 0032: what is gone when the publication is not visible. `COMMENT` is our reply, `PARENT_POST`
   * the whole thread under it (which says nothing about us), `null` not established. Optional,
   * so an older control plane without the field still parses.
   */
  readonly removal_scope: "COMMENT" | "PARENT_POST" | null;
  /** 27.09: the platform the request aims at. Optional on the wire; `null` from an older API. */
  readonly platform: "X" | "REDDIT" | null;
  /** 27.09: why the request stands where it stands (the queue's own sentence). */
  readonly detail: string | null;
}

/**
 * The request's state in words a person uses. The queue's codes (`SKIPPED`, `BLOCKED`) were shown
 * as they are, and "SKIPPED, bez potwierdzenia wysłania" did not say that nothing went out.
 */
export function publicationStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    QUEUED: "czeka na wysłanie",
    PUBLISHING: "w trakcie wysyłania",
    PUBLISHED: "wysłana",
    SKIPPED: "pominięta, nic nie wysłano",
    BLOCKED: "zatrzymana",
    STOPPED: "zatrzymana",
    UNKNOWN: "wynik niepewny, nie ponawiamy",
  };
  return labels[status] ?? status;
}

/**
 * The reason, short: without the queue's bracketed markers and repeated prefixes, bounded, and
 * only when there is one. It is the queue's sentence, shortened, never a new one.
 */
export function publicationReason(detail: string | null): string | null {
  if (detail === null) return null;
  // The row's state already says what happened ("pominięta, nic nie wysłano", "zatrzymana"); the
  // queue's sentence says it again in two layers (the publisher's and the actuator's), so the
  // repeated layers go and the cause the platform or the capsule gave stays, word for word.
  const plain = detail
    .replace(/^\[[^\]]*\]\s*/, "")
    .replace(/^aktuator odmówił, nic nie wysłano:\s*/, "")
    .replace(/^aktuator odmówił \(\d{3}\):\s*/, "")
    .replace(/^platforma zablokowała:\s*(?=platforma zablokowała)/, "")
    .replace(/;\s*nic nie wysłano\.?$/, "")
    .replace(/\s+/g, " ")
    .trim();
  if (plain === "") return null;
  return plain.length > 160 ? `${plain.slice(0, 157)}...` : plain;
}

/** The visibility sentence of one publication: seen, gone (and what), or nobody looked. */
export function visibilityLabel(publication: {
  readonly still_present: boolean | null;
  readonly removal_scope?: "COMMENT" | "PARENT_POST" | null;
}): string {
  if (publication.still_present === null) return "skutek niepotwierdzony";
  if (publication.still_present) return "potwierdzona jako widoczna";
  if (publication.removal_scope === "PARENT_POST") {
    return "niewidoczna publicznie: usunięto cały wątek, nie naszą odpowiedź";
  }
  if (publication.removal_scope === "COMMENT") {
    return "niewidoczna publicznie: usunięto naszą odpowiedź";
  }
  return "niewidoczna publicznie";
}

export function parseResultPublication(row: Record<string, unknown>): Read<ResultPublication> {
  const missing: string[] = [];
  const id = str(row, "request_id");
  const status = str(row, "status");
  const url = str(row, "target_url");
  const permalink = nullableStr(row, "permalink");
  const publishedAt = nullableStr(row, "published_at");
  const queuedAt = str(row, "queued_at");
  const observedAt = nullableStr(row, "observed_at");
  const kind = str(row, "kind");
  if (id === null) missing.push("request_id");
  if (status === null) missing.push("status");
  // Nieznany rodzaj to ZEPSUTY ODCZYT, nie „pewnie odpowiedź": domyślanie się tutaj pokazałoby
  // założony przez nas wątek jako dopisek do cudzej rozmowy.
  if (kind === null || !PUBLICATION_KINDS.some((known) => known === kind)) missing.push("kind");
  if (url === null) missing.push("target_url");
  if (!permalink.present) missing.push("permalink");
  if (!publishedAt.present) missing.push("published_at");
  if (queuedAt === null) missing.push("queued_at");
  if (!observedAt.present) missing.push("observed_at");
  if (!("still_present" in row)) missing.push("still_present");
  const present = row["still_present"];
  if (present !== null && typeof present !== "boolean") missing.push("still_present");
  if (missing.length > 0) return report("publikacja", missing);
  return {
    ok: true,
    value: {
      request_id: id as string,
      status: status as string,
      kind: kind as PublicationKind,
      target_url: url as string,
      permalink: permalink.present ? permalink.value : null,
      published_at: publishedAt.present ? publishedAt.value : null,
      queued_at: queuedAt as string,
      still_present: present as boolean | null,
      observed_at: observedAt.present ? observedAt.value : null,
      removal_scope:
        row["removal_scope"] === "COMMENT" || row["removal_scope"] === "PARENT_POST"
          ? row["removal_scope"]
          : null,
      platform: row["platform"] === "X" || row["platform"] === "REDDIT" ? row["platform"] : null,
      detail: typeof row["detail"] === "string" ? row["detail"] : null,
    },
  };
}

export interface ResultTotals {
  readonly communities: number;
  readonly threads: number;
  readonly published: number;
  readonly confirmed_visible: number;
}

/**
 * Co wysłał w świat JEDEN projekt (E6, odpowiedź `…/projects/{id}/results`).
 *
 * OSOBNY KSZTAŁT OD `WorkspaceResults`, I TO NIE JEST POWTÓRZENIE. Odczyt przestrzeni niesie
 * społeczności i ich liczbę, bo społeczność należy do PRZESTRZENI: jedna obserwowana społeczność
 * karmi wszystkie projekty i `communities` nie ma kolumny projektu. Gdyby ten kształt miał te
 * pola, musiałyby w nich stać zera, a zero znaczy „zmierzono i nie ma", podczas gdy prawdą jest
 * „to nie jest liczba tego projektu". Pól po prostu nie ma, więc nie da się ich źle odczytać.
 */
export interface ProjectResults {
  /** Zakres z serwera, sprawdzany przy odczycie: lista projektu nie ma prawa przyjść jako lista przestrzeni. */
  readonly scope: { readonly kind: "PROJECT"; readonly project_id: string };
  readonly publications: readonly ResultPublication[];
  readonly totals: ProjectResultTotals;
}

export interface ProjectResultTotals {
  readonly published: number;
  readonly confirmed_visible: number;
  /** Wnioski, które jeszcze nie wyszły. Osobno od `published`, bo „zgłoszone" to nie „wysłane". */
  readonly waiting: number;
}

export function parseProjectResultTotals(
  row: Record<string, unknown>,
): Read<ProjectResultTotals> {
  const missing: string[] = [];
  const published = num(row, "published");
  const confirmed = num(row, "confirmed_visible");
  const waiting = num(row, "waiting");
  if (published === null) missing.push("published");
  if (confirmed === null) missing.push("confirmed_visible");
  if (waiting === null) missing.push("waiting");
  if (missing.length > 0) return report("podsumowanie publikacji projektu", missing);
  return {
    ok: true,
    value: {
      published: published as number,
      confirmed_visible: confirmed as number,
      waiting: waiting as number,
    },
  };
}

export interface WorkspaceResults {
  readonly communities: readonly ResultCommunity[];
  readonly publications: readonly ResultPublication[];
  readonly totals: ResultTotals;
}

export function parseResultTotals(row: Record<string, unknown>): Read<ResultTotals> {
  const missing: string[] = [];
  const communities = num(row, "communities");
  const threads = num(row, "threads");
  const published = num(row, "published");
  const confirmed = num(row, "confirmed_visible");
  if (communities === null) missing.push("communities");
  if (threads === null) missing.push("threads");
  if (published === null) missing.push("published");
  if (confirmed === null) missing.push("confirmed_visible");
  if (missing.length > 0) return report("podsumowanie rezultatów", missing);
  return {
    ok: true,
    value: {
      communities: communities as number,
      threads: threads as number,
      published: published as number,
      confirmed_visible: confirmed as number,
    },
  };
}
