/**
 * The JSON of `GET /api/workspaces/{ws}/projects/{project}/room`, checked before it reaches the
 * "Pokój" screen.
 *
 * WHAT THE ROOM IS. The one screen the founder opens from Codex to see that the promise was kept:
 * real people who write, right now, about the problem the project's code solves (`okazje`), ready
 * replies in the founder's voice (`szkice`) and what already went out (`wyniki`). The endpoint is
 * built by another worker; this file codes against the contract that worker was given and fails
 * loudly the day the two drift apart.
 *
 * THE RULES THIS PARSER HOLDS, the same as `knowledge-schema.ts`:
 *
 *   1. **A missing key is a broken read, never a default.** Every key of the contract must be
 *      PRESENT. A page that rendered a draft with an invented expiry or an opportunity with an
 *      invented author would be a screen that makes claims nobody measured.
 *   2. **`null` is a value and stays `null`.** Every number of the contract may be unmeasured.
 *      `wynik: null` is "nobody read the score", NOT zero, and the screen says "brak pomiaru".
 *      Coercing it here (`?? 0`) would turn a gap into a measurement before any component sees it.
 *   3. **A closed list is closed.** `stan` of an opportunity and `rodzaj` of a draft are checked
 *      against their lists. A value outside them means the two sides disagree, and a screen that
 *      guessed would send a person to publish something it mislabelled.
 *   4. **Nothing parsed here is an instruction.** Titles, excerpts and drafts are text somebody
 *      (a stranger on Reddit, or a model) wrote. The only thing this file does about it is refuse
 *      an address that is not `http(s)`, because that text ends up in an `href`.
 *
 * The keys are Polish because the contract's are (they are JSON keys, one of the four things
 * `docs/KONWENCJE.md` leaves in Polish); the TypeScript names around them are English.
 */
import type { Read } from "./control-plane";

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The platforms this room knows how to draw. Another value is kept and printed as it is. */
export const ROOM_PLATFORMS = ["REDDIT", "X"] as const;

export const OPPORTUNITY_STATES = ["NOWA", "SZKIC", "ODPOWIEDZIANO", "POMINIETA"] as const;
export type OpportunityState = (typeof OPPORTUNITY_STATES)[number];

export const DRAFT_KINDS = ["REPLY", "OWN_POST"] as const;
export type DraftKind = (typeof DRAFT_KINDS)[number];

export interface Opportunity {
  readonly id: string;
  /** `REDDIT` or `X`; anything else is kept so the screen can print it instead of guessing. */
  readonly platform: string;
  readonly community: string | null;
  readonly title: string | null;
  readonly excerpt: string | null;
  readonly author: string | null;
  readonly url: string;
  readonly created_at: string | null;
  /** The score. `null` is "not measured", never zero. */
  readonly score: number | null;
  readonly comments: number | null;
  /** Why this person matters to this project, one or two sentences written by the agent. */
  readonly why: string | null;
  /** The angle the reply should take. */
  readonly angle: string | null;
  readonly state: OpportunityState;
  /** The revision id of the draft written for this opportunity, if there is one. */
  readonly draft_revision: string | null;
}

export interface DraftTarget {
  readonly url: string;
  readonly title: string | null;
  readonly author: string | null;
}

export interface RoomDraft {
  readonly revision_id: string;
  readonly opportunity_id: string | null;
  readonly platform: string;
  readonly community: string | null;
  readonly kind: DraftKind;
  readonly title: string | null;
  readonly text: string;
  readonly in_reply_to: DraftTarget | null;
  /** The proposal's state, as the control plane names it. See `draftStateOf`. */
  readonly state: string;
  readonly expires_at: string | null;
}

export interface RoomPublication {
  readonly url: string;
  readonly platform: string;
  readonly text: string;
  /**
   * What somebody saw on the platform AFTER publishing, kept as three values that never impersonate
   * each other. "Unconfirmed" is not "visible" and is not "removed": nobody looked.
   */
  readonly visibility: Visibility;
  readonly votes: number | null;
  readonly replies: number | null;
  readonly published_at: string | null;
}

export type Visibility =
  | { readonly kind: "visible" }
  | { readonly kind: "removed" }
  | { readonly kind: "unconfirmed"; readonly raw: string | null };

export interface RoomResults {
  readonly window_days: number;
  readonly published: number | null;
  readonly replies: number | null;
  readonly votes: number | null;
  readonly removed: number | null;
  readonly items: readonly RoomPublication[];
}

export interface Room {
  readonly opportunities: readonly Opportunity[];
  readonly drafts: readonly RoomDraft[];
  readonly results: RoomResults;
  /** The path of this screen, as the endpoint states it. Informational; the page builds its own. */
  readonly room_path: string | null;
}

/** An address that is safe to put in an `href`: `http` or `https`, nothing else. */
export function isWebAddress(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

/** Where the walk is, so a failure can say `okazje[3].kat` instead of "something is wrong". */
type Where = string;

function requireString(row: Record<string, unknown>, key: string, where: Where): Read<string> {
  const value = row[key];
  if (typeof value !== "string" || value === "") {
    return failure(`pokój: ${where}.${key} nie jest niepustym tekstem`);
  }
  return { ok: true, value };
}

/** Present, and a string or `null`. Absence is a broken read. */
function nullableString(
  row: Record<string, unknown>,
  key: string,
  where: Where,
): Read<string | null> {
  if (!(key in row)) return failure(`pokój: brak pola ${where}.${key}`);
  const value = row[key];
  if (value === null) return { ok: true, value: null };
  if (typeof value === "string") return { ok: true, value };
  return failure(`pokój: ${where}.${key} nie jest tekstem ani null`);
}

/** Present, and a finite number or `null`. `null` STAYS `null`. */
function nullableNumber(
  row: Record<string, unknown>,
  key: string,
  where: Where,
): Read<number | null> {
  if (!(key in row)) return failure(`pokój: brak pola ${where}.${key}`);
  const value = row[key];
  if (value === null) return { ok: true, value: null };
  if (typeof value === "number" && Number.isFinite(value)) return { ok: true, value };
  return failure(`pokój: ${where}.${key} nie jest liczbą ani null`);
}

function webAddress(row: Record<string, unknown>, key: string, where: Where): Read<string> {
  const value = requireString(row, key, where);
  if (!value.ok) return value;
  if (!isWebAddress(value.value)) {
    return failure(`pokój: ${where}.${key} nie jest adresem http(s)`);
  }
  return value;
}

function parseOpportunity(row: unknown, index: number): Read<Opportunity> {
  const where = `okazje[${index}]`;
  if (!isObject(row)) return failure(`pokój: ${where} nie jest obiektem`);

  const id = requireString(row, "id", where);
  if (!id.ok) return id;
  const platform = requireString(row, "platforma", where);
  if (!platform.ok) return platform;
  const url = webAddress(row, "url", where);
  if (!url.ok) return url;
  const state = row["stan"];
  if (typeof state !== "string" || !(OPPORTUNITY_STATES as readonly string[]).includes(state)) {
    return failure(`pokój: ${where}.stan ma wartość spoza listy (${String(state)})`);
  }

  const community = nullableString(row, "spolecznosc", where);
  if (!community.ok) return community;
  const title = nullableString(row, "tytul", where);
  if (!title.ok) return title;
  const excerpt = nullableString(row, "fragment", where);
  if (!excerpt.ok) return excerpt;
  const author = nullableString(row, "autor", where);
  if (!author.ok) return author;
  const created = nullableString(row, "utworzono", where);
  if (!created.ok) return created;
  const score = nullableNumber(row, "wynik", where);
  if (!score.ok) return score;
  const comments = nullableNumber(row, "komentarze", where);
  if (!comments.ok) return comments;
  const why = nullableString(row, "dlaczego", where);
  if (!why.ok) return why;
  const angle = nullableString(row, "kat", where);
  if (!angle.ok) return angle;
  const draft = nullableString(row, "szkic", where);
  if (!draft.ok) return draft;

  return {
    ok: true,
    value: {
      id: id.value,
      platform: platform.value,
      community: community.value,
      title: title.value,
      excerpt: excerpt.value,
      author: author.value,
      url: url.value,
      created_at: created.value,
      score: score.value,
      comments: comments.value,
      why: why.value,
      angle: angle.value,
      state: state as OpportunityState,
      draft_revision: draft.value,
    },
  };
}

function parseTarget(value: unknown, where: Where): Read<DraftTarget | null> {
  if (value === null) return { ok: true, value: null };
  if (!isObject(value)) return failure(`pokój: ${where} nie jest obiektem ani null`);
  const url = webAddress(value, "url", where);
  if (!url.ok) return url;
  const title = nullableString(value, "tytul", where);
  if (!title.ok) return title;
  const author = nullableString(value, "autor", where);
  if (!author.ok) return author;
  return { ok: true, value: { url: url.value, title: title.value, author: author.value } };
}

function parseDraft(row: unknown, index: number): Read<RoomDraft> {
  const where = `szkice[${index}]`;
  if (!isObject(row)) return failure(`pokój: ${where} nie jest obiektem`);

  const revision = requireString(row, "revision_id", where);
  if (!revision.ok) return revision;
  const platform = requireString(row, "platforma", where);
  if (!platform.ok) return platform;
  const text = requireString(row, "tekst", where);
  if (!text.ok) return text;
  const state = requireString(row, "stan", where);
  if (!state.ok) return state;
  const kind = row["rodzaj"];
  if (typeof kind !== "string" || !(DRAFT_KINDS as readonly string[]).includes(kind)) {
    return failure(`pokój: ${where}.rodzaj ma wartość spoza listy (${String(kind)})`);
  }

  const opportunity = nullableString(row, "okazja_id", where);
  if (!opportunity.ok) return opportunity;
  const community = nullableString(row, "spolecznosc", where);
  if (!community.ok) return community;
  const title = nullableString(row, "tytul", where);
  if (!title.ok) return title;
  const expires = nullableString(row, "wygasa", where);
  if (!expires.ok) return expires;
  if (!("w_odpowiedzi_na" in row)) return failure(`pokój: brak pola ${where}.w_odpowiedzi_na`);
  const target = parseTarget(row["w_odpowiedzi_na"], `${where}.w_odpowiedzi_na`);
  if (!target.ok) return target;

  return {
    ok: true,
    value: {
      revision_id: revision.value,
      opportunity_id: opportunity.value,
      platform: platform.value,
      community: community.value,
      kind: kind as DraftKind,
      title: title.value,
      text: text.value,
      in_reply_to: target.value,
      state: state.value,
      expires_at: expires.value,
    },
  };
}

/**
 * What "visible on the platform" says, from whatever shape the endpoint sends it in.
 *
 * THE CONTRACT NAMES THE FIELD (`widocznosc`) AND NOT ITS ENCODING, so this accepts the three
 * shapes this repository already uses for the same fact: a boolean (`still_present`), a closed
 * word in English or in Polish, or `null`. Anything it does not recognise is `unconfirmed` and
 * KEEPS the raw word, so a new value shows up as itself rather than as a quiet "visible".
 * The direction of the default is the point: a result nobody confirmed must never read as one
 * somebody did.
 */
export function visibilityOf(raw: unknown): Visibility {
  if (raw === true) return { kind: "visible" };
  if (raw === false) return { kind: "removed" };
  if (typeof raw !== "string") return { kind: "unconfirmed", raw: null };
  const word = raw.trim().toUpperCase();
  if (["CONFIRMED", "VISIBLE", "WIDOCZNA", "WIDOCZNY", "PRESENT", "POTWIERDZONA"].includes(word)) {
    return { kind: "visible" };
  }
  if (["REMOVED", "USUNIETA", "USUNIETY", "USUNIĘTA", "NOT_VISIBLE", "GONE", "DELETED"].includes(word)) {
    return { kind: "removed" };
  }
  return { kind: "unconfirmed", raw: word === "" ? null : raw.trim() };
}

function parsePublication(row: unknown, index: number): Read<RoomPublication> {
  const where = `wyniki.pozycje[${index}]`;
  if (!isObject(row)) return failure(`pokój: ${where} nie jest obiektem`);
  const url = webAddress(row, "url", where);
  if (!url.ok) return url;
  const platform = requireString(row, "platforma", where);
  if (!platform.ok) return platform;
  const text = nullableString(row, "tekst", where);
  if (!text.ok) return text;
  if (!("widocznosc" in row)) return failure(`pokój: brak pola ${where}.widocznosc`);
  const votes = nullableNumber(row, "glosy", where);
  if (!votes.ok) return votes;
  const replies = nullableNumber(row, "odpowiedzi", where);
  if (!replies.ok) return replies;
  const published = nullableString(row, "opublikowano", where);
  if (!published.ok) return published;
  return {
    ok: true,
    value: {
      url: url.value,
      platform: platform.value,
      text: text.value ?? "",
      visibility: visibilityOf(row["widocznosc"]),
      votes: votes.value,
      replies: replies.value,
      published_at: published.value,
    },
  };
}

function parseResults(value: unknown): Read<RoomResults> {
  if (!isObject(value)) return failure("pokój: pole 'wyniki' nie jest obiektem");
  const where = "wyniki";
  const days = value["okno_dni"];
  if (typeof days !== "number" || !Number.isInteger(days) || days <= 0) {
    return failure("pokój: wyniki.okno_dni nie jest dodatnią liczbą całkowitą");
  }
  const published = nullableNumber(value, "opublikowane", where);
  if (!published.ok) return published;
  const replies = nullableNumber(value, "odpowiedzi", where);
  if (!replies.ok) return replies;
  const votes = nullableNumber(value, "glosy", where);
  if (!votes.ok) return votes;
  const removed = nullableNumber(value, "usuniete", where);
  if (!removed.ok) return removed;
  const raw = value["pozycje"];
  if (!Array.isArray(raw)) {
    return failure("pokój: wyniki.pozycje nie jest listą, czyli to zepsuty odczyt, nie pusta lista");
  }
  const items: RoomPublication[] = [];
  for (const [index, row] of raw.entries()) {
    const one = parsePublication(row, index);
    if (!one.ok) return one;
    items.push(one.value);
  }
  return {
    ok: true,
    value: {
      window_days: days,
      published: published.value,
      replies: replies.value,
      votes: votes.value,
      removed: removed.value,
      items,
    },
  };
}

/** The whole body. Any row that is wrong fails the read, and the failure names the row. */
export function parseRoom(body: unknown): Read<Room> {
  if (!isObject(body)) return failure("pokój: ciało odpowiedzi nie jest obiektem");

  const rawOpportunities = body["okazje"];
  if (!Array.isArray(rawOpportunities)) {
    return failure("pokój: pole 'okazje' nie jest listą, czyli to zepsuty odczyt, nie pusty pokój");
  }
  const rawDrafts = body["szkice"];
  if (!Array.isArray(rawDrafts)) {
    return failure("pokój: pole 'szkice' nie jest listą, czyli to zepsuty odczyt, nie pusty pokój");
  }

  const opportunities: Opportunity[] = [];
  for (const [index, row] of rawOpportunities.entries()) {
    const one = parseOpportunity(row, index);
    if (!one.ok) return one;
    opportunities.push(one.value);
  }
  const drafts: RoomDraft[] = [];
  for (const [index, row] of rawDrafts.entries()) {
    const one = parseDraft(row, index);
    if (!one.ok) return one;
    drafts.push(one.value);
  }
  const results = parseResults(body["wyniki"]);
  if (!results.ok) return results;

  const path = body["pokoj"];
  return {
    ok: true,
    value: {
      opportunities,
      drafts,
      results: results.value,
      room_path: typeof path === "string" && path.startsWith("/") ? path : null,
    },
  };
}
