/**
 * The JSON of `api/knowledge.py`, checked before it reaches the "Materiały" screen.
 *
 * THE FIELDS THAT MUST NEVER DEFAULT ARE `status`, `fetched_at` AND `content_hash`. Together they
 * are the provenance of a material, and a parser that filled any of them in would turn "nie udało
 * się przeczytać" into a material that looks read. On this screen that is the difference between
 * a person seeing that an address is dead and a person believing the agent has its content.
 *
 * `source` ON A FINDING IS `null` OR AN OBJECT, and absence is a broken read like everywhere else
 * in this package: the control plane always sends the field, so a missing one means a payload
 * nobody should render, not "this finding stands on nothing".
 *
 * NOTHING PARSED HERE IS AN INSTRUCTION. Titles, statements and snippets are text somebody else
 * wrote; they are rendered as text and nothing in the dashboard acts on what they say.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function report(what: string, missing: readonly string[]): Read<never> {
  return failure(
    `${what}: brakuje pól albo mają zły typ (${missing.join(", ")}). To zepsuty odczyt, ` +
      "nie puste dane",
  );
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function flag(row: Record<string, unknown>, field: string): boolean | null {
  const value = row[field];
  return typeof value === "boolean" ? value : null;
}

/** Present-and-nullable. Absence is a broken read, never a `null`. */
function nullableString(
  row: Record<string, unknown>,
  field: string,
): { readonly present: true; readonly value: string | null } | { readonly present: false } {
  if (!(field in row)) return { present: false };
  const value = row[field];
  if (value === null) return { present: true, value: null };
  if (typeof value === "string") return { present: true, value };
  return { present: false };
}

export interface KnowledgeSource {
  readonly source_id: string;
  readonly project_id: string | null;
  readonly kind: string;
  readonly locator: string;
  readonly title: string;
  /** `null` means nobody has read this address yet. Never rendered as "read just now". */
  readonly fetched_at: string | null;
  readonly content_hash: string | null;
  /** `FETCHED`, `UNAVAILABLE` or `STALE`. Printed, not inferred from the other fields. */
  readonly status: string;
  readonly unavailable_reason: string | null;
  readonly has_raw: boolean;
  readonly created_at: string;
}

export interface KnowledgeItem {
  readonly item_id: string;
  readonly project_id: string | null;
  /** Whose finding it is. A person's findings are visible only where that person works. */
  readonly person_profile_id: string | null;
  readonly kind: string;
  readonly statement: string;
  readonly stated_by: string;
  readonly source_span: string | null;
  /** What question this answers. Two findings sharing it are a declared conflict. */
  readonly about: string | null;
  /**
   * The finding's own structure. Its shape depends on the kind: an EXPERIENCE carries
   * `situation`, `decision` and `outcome` (migration 0016 requires all three), a correction from
   * the person carries the verdict and the publication it is about. Read through `experienceOf`
   * and `verdictOf` rather than field by field, so a payload of the wrong shape is a `null` the
   * screen can handle and not a crash on a nested key.
   */
  readonly payload: Record<string, unknown>;
  readonly valid_from: string;
  readonly superseded_by: string | null;
  readonly retracted_at: string | null;
  readonly retracted_reason: string | null;
  readonly in_force: boolean;
  /**
   * `true` when this stands on material that is old, unreadable or already corrected. Computed
   * by the backend so the screen and the agent's tools cannot disagree about which findings are
   * shaky; `stale_reason` says which of those it is.
   */
  readonly stale: boolean;
  readonly stale_reason: string | null;
  /** Days since the material was read. `null` when there is no material or it was never read. */
  readonly source_age_days: number | null;
  /** The material this finding stands on, or `null` when it stands on a conversation. */
  readonly source: KnowledgeSource | null;
}

/** Findings that hold at once and disagree. NOTHING in the dashboard picks one. */
export interface KnowledgeConflict {
  readonly about: string;
  readonly kind: string;
  readonly certain: boolean;
  readonly advice: string;
  readonly items: readonly {
    readonly item_id: string;
    readonly statement: string;
    readonly stated_by: string;
    readonly created_at: string;
  }[];
}

export interface KnowledgeView {
  readonly sources: readonly KnowledgeSource[];
  readonly items: readonly KnowledgeItem[];
  readonly conflicts: readonly KnowledgeConflict[];
  readonly unavailable: number;
  readonly stale: number;
}

export interface SourceSlice {
  readonly source_id: string;
  readonly text: string;
  readonly offset: number;
  readonly length: number;
  readonly total_bytes: number;
  readonly truncated: boolean;
}

export function parseSourceSlice(row: Record<string, unknown>): Read<SourceSlice> {
  const missing = ["source_id", "text"].filter((key) => typeof row[key] !== "string");
  for (const key of ["offset", "length", "total_bytes"]) {
    const value = row[key];
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) missing.push(key);
  }
  if (typeof row["truncated"] !== "boolean") missing.push("truncated");
  if (missing.length > 0) return report("treść materiału", missing);
  return { ok: true, value: row as unknown as SourceSlice };
}

export function parseSource(row: Record<string, unknown>): Read<KnowledgeSource> {
  const missing: string[] = [];
  const sourceId = str(row, "source_id");
  const kind = str(row, "kind");
  const locator = str(row, "locator");
  const title = str(row, "title");
  const status = str(row, "status");
  const createdAt = str(row, "created_at");
  const projectId = nullableString(row, "project_id");
  const fetchedAt = nullableString(row, "fetched_at");
  const contentHash = nullableString(row, "content_hash");
  const reason = nullableString(row, "unavailable_reason");
  const hasRaw = flag(row, "has_raw");
  if (sourceId === null) missing.push("source_id");
  if (kind === null) missing.push("kind");
  if (locator === null) missing.push("locator");
  if (title === null) missing.push("title");
  if (status === null) missing.push("status");
  if (createdAt === null) missing.push("created_at");
  if (!projectId.present) missing.push("project_id");
  if (!fetchedAt.present) missing.push("fetched_at");
  if (!contentHash.present) missing.push("content_hash");
  if (!reason.present) missing.push("unavailable_reason");
  if (hasRaw === null) missing.push("has_raw");
  if (missing.length > 0) return report("materiał", missing);
  return {
    ok: true,
    value: {
      source_id: sourceId as string,
      project_id: projectId.present ? projectId.value : null,
      kind: kind as string,
      locator: locator as string,
      title: title as string,
      fetched_at: fetchedAt.present ? fetchedAt.value : null,
      content_hash: contentHash.present ? contentHash.value : null,
      status: status as string,
      unavailable_reason: reason.present ? reason.value : null,
      has_raw: hasRaw as boolean,
      created_at: createdAt as string,
    },
  };
}

export function parseItem(row: Record<string, unknown>): Read<KnowledgeItem> {
  const missing: string[] = [];
  const itemId = str(row, "item_id");
  const kind = str(row, "kind");
  const statement = str(row, "statement");
  const statedBy = str(row, "stated_by");
  const validFrom = str(row, "valid_from");
  const projectId = nullableString(row, "project_id");
  const personId = nullableString(row, "person_profile_id");
  const span = nullableString(row, "source_span");
  const about = nullableString(row, "about");
  const superseded = nullableString(row, "superseded_by");
  const retractedAt = nullableString(row, "retracted_at");
  const retractedReason = nullableString(row, "retracted_reason");
  const staleReason = nullableString(row, "stale_reason");
  const payload = row["payload"];
  const inForce = flag(row, "in_force");
  const stale = flag(row, "stale");
  const age = row["source_age_days"];
  if (itemId === null) missing.push("item_id");
  if (kind === null) missing.push("kind");
  if (statement === null) missing.push("statement");
  if (statedBy === null) missing.push("stated_by");
  if (validFrom === null) missing.push("valid_from");
  if (!projectId.present) missing.push("project_id");
  if (!personId.present) missing.push("person_profile_id");
  if (!span.present) missing.push("source_span");
  if (!about.present) missing.push("about");
  if (!superseded.present) missing.push("superseded_by");
  if (!retractedAt.present) missing.push("retracted_at");
  if (!retractedReason.present) missing.push("retracted_reason");
  if (!staleReason.present) missing.push("stale_reason");
  if (inForce === null) missing.push("in_force");
  // NOT defaulted to false. "Nothing looks out of date" and "nobody said" are different, and
  // the second one must not render as the first on a screen about what we can rely on.
  if (stale === null) missing.push("stale");
  if (!(age === null || typeof age === "number")) missing.push("source_age_days");
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    missing.push("payload");
  }
  if (missing.length > 0) return report("ustalenie", missing);

  const rawSource = row["source"];
  let source: KnowledgeSource | null = null;
  if (rawSource !== null) {
    if (typeof rawSource !== "object" || Array.isArray(rawSource)) {
      return failure("ustalenie: pole 'source' nie jest obiektem ani null-em");
    }
    const parsed = parseSource(rawSource as Record<string, unknown>);
    if (!parsed.ok) return parsed;
    source = parsed.value;
  }

  return {
    ok: true,
    value: {
      item_id: itemId as string,
      project_id: projectId.present ? projectId.value : null,
      person_profile_id: personId.present ? personId.value : null,
      kind: kind as string,
      statement: statement as string,
      stated_by: statedBy as string,
      source_span: span.present ? span.value : null,
      about: about.present ? about.value : null,
      valid_from: validFrom as string,
      superseded_by: superseded.present ? superseded.value : null,
      retracted_at: retractedAt.present ? retractedAt.value : null,
      retracted_reason: retractedReason.present ? retractedReason.value : null,
      in_force: inForce as boolean,
      stale: stale as boolean,
      stale_reason: staleReason.present ? staleReason.value : null,
      source_age_days: typeof age === "number" ? age : null,
      payload: payload as Record<string, unknown>,
      source,
    },
  };
}

export function parseConflict(row: Record<string, unknown>): Read<KnowledgeConflict> {
  const missing: string[] = [];
  const about = str(row, "about");
  const kind = str(row, "kind");
  const advice = str(row, "advice");
  const certain = flag(row, "certain");
  if (about === null) missing.push("about");
  if (kind === null) missing.push("kind");
  if (advice === null) missing.push("advice");
  if (certain === null) missing.push("certain");
  if (!Array.isArray(row["items"])) missing.push("items");
  if (missing.length > 0) return report("sprzeczność", missing);

  const items: KnowledgeConflict["items"][number][] = [];
  for (const entry of row["items"] as unknown[]) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      return failure("sprzeczność: element 'items' nie jest obiektem");
    }
    const one = entry as Record<string, unknown>;
    const itemId = str(one, "item_id");
    const statement = str(one, "statement");
    const statedBy = str(one, "stated_by");
    const createdAt = str(one, "created_at");
    if (itemId === null || statement === null || statedBy === null || createdAt === null) {
      return report("ustalenie w sprzeczności", ["item_id", "statement", "stated_by", "created_at"]);
    }
    items.push({
      item_id: itemId,
      statement,
      stated_by: statedBy,
      created_at: createdAt,
    });
  }
  // A "conflict" of one is not a conflict; it is a parser that lost a row.
  if (items.length < 2) {
    return failure("sprzeczność z mniej niż dwoma ustaleniami to zepsuty odczyt, nie zgoda");
  }
  return {
    ok: true,
    value: {
      about: about as string,
      kind: kind as string,
      certain: certain as boolean,
      advice: advice as string,
      items,
    },
  };
}

/** What the sweep measured about one publication (P5). `null` when this is not an experience. */
export interface Experience {
  readonly community: string | null;
  readonly move: string | null;
  readonly decisionKind: string | null;
  readonly account: string | null;
  readonly lengthChars: number | null;
  readonly outcome: string;
  readonly measuredAt: string | null;
  readonly hoursAfter: number | null;
  readonly requestId: string | null;
}

function text(source: Record<string, unknown>, field: string): string | null {
  const value = source[field];
  return typeof value === "string" ? value : null;
}

function number(source: Record<string, unknown>, field: string): number | null {
  const value = source[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function objectAt(payload: Record<string, unknown>, field: string): Record<string, unknown> {
  const value = payload[field];
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * The three parts of an experience, or `null` when this finding is not one.
 *
 * `null` rather than a half-filled object: a row missing `outcome` is a row whose result nobody
 * knows, and rendering it with an empty outcome would put a blank where the whole point is.
 */
export function experienceOf(item: KnowledgeItem): Experience | null {
  if (item.kind !== "EXPERIENCE") return null;
  const situation = objectAt(item.payload, "situation");
  const decision = objectAt(item.payload, "decision");
  const outcome = objectAt(item.payload, "outcome");
  const kind = text(outcome, "kind");
  if (kind === null) return null;
  return {
    community: text(situation, "community"),
    move: text(situation, "move"),
    decisionKind: text(situation, "decision_kind"),
    account: text(decision, "account"),
    lengthChars: number(decision, "length_chars"),
    outcome: kind,
    measuredAt: text(outcome, "measured_at"),
    hoursAfter: number(outcome, "hours_after_publication"),
    requestId: text(item.payload, "request_id"),
  };
}

/** Which button the person pressed, when this finding came from one. */
export function verdictOf(item: KnowledgeItem): string | null {
  return item.kind === "PREFERENCE" ? text(item.payload, "verdict") : null;
}

/** The outcome in words a person reads. The code stays in the data. */
export function outcomeLabel(outcome: string): string {
  const names: Record<string, string> = {
    REMOVED: "niewidoczna publicznie",
    NO_REACTION: "brak reakcji",
    REPLIED: "były odpowiedzi",
    SCORED: "były punkty, bez odpowiedzi",
  };
  return names[outcome] ?? outcome;
}

/** How old a material is, in words a person reads. `null` means nobody has read it. */
export function ageNote(days: number | null): string {
  if (days === null) return "nie przeczytano";
  if (days < 1) return "dziś";
  if (days < 2) return "wczoraj";
  return `${Math.round(days)} dni temu`;
}

/** What a material's state means in one Polish phrase. The screen prints this, not the code. */
export function statusNote(source: KnowledgeSource): string {
  if (source.status === "UNAVAILABLE") {
    return source.unavailable_reason ?? "nie udało się przeczytać, bez podanego powodu";
  }
  if (source.status === "STALE") return "przeczytane dawno, warto odświeżyć";
  return source.content_hash === null
    ? "przeczytane, ale bez odcisku treści"
    : "przeczytane, treść zachowana";
}

/** The Polish name of a material kind. An unknown kind is printed as it came. */
export function sourceKindLabel(kind: string): string {
  const names: Record<string, string> = {
    WEB_PAGE: "strona",
    WEB_SEARCH: "wyszukiwanie",
    RSS: "kanał RSS",
    NEWSLETTER: "newsletter",
    DOCUMENT: "dokument",
    USER_NOTE: "notatka",
  };
  return names[kind] ?? kind;
}

/** The Polish name of a finding kind. */
export function itemKindLabel(kind: string): string {
  const names: Record<string, string> = {
    FACT: "fakt",
    INTERPRETATION: "interpretacja",
    PREFERENCE: "preferencja",
    EXPERIENCE: "doświadczenie",
  };
  return names[kind] ?? kind;
}

/** Who said it. `SOURCE` is deliberately not "agent": a feed entry is not the agent's claim. */
export function statedByLabel(statedBy: string): string {
  const names: Record<string, string> = {
    USER: "od człowieka",
    AGENT: "od agenta",
    SOURCE: "z materiału",
  };
  return names[statedBy] ?? statedBy;
}
