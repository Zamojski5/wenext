/**
 * The JSON of `api/sessions.py`, checked field by field before any of it reaches the screen.
 *
 * Same rule as `lib/workspace-schema.ts`: a missing or wrongly typed field FAILS THE WHOLE READ.
 * It never becomes a blank line and never becomes a default. On this screen the difference is
 * not cosmetic and the two worst cases are easy to name:
 *
 *   * `status` absent, rendered as anything at all, would tell the person the agent is working
 *     when nobody knows what it is doing. ZAKRES-FUNKCJONALNY §5 forbids exactly this: a screen
 *     must not show activity while no model is running;
 *   * `seq` absent, defaulted to 0, would make the conversation poll re-read the same events for
 *     ever, or skip everything after the first gap. That number is the only thing keeping the
 *     screen and the trace in step.
 *
 * `engine_thread_id` and `result_summary` are allowed to be `null`, and only `null`. `null` there
 * is a fact the screen says out loud ("nie ruszyła jeszcze"); ABSENT is a broken read.
 *
 * TRZY POLA D-045 (`tytul`, `wynik_zdanie`, `rodzaj`) MAJĄ TRZECI STAN i to jedyne takie pola w
 * tym pliku. `null` znaczy „serwer policzył i nie ma czego pokazać"; BRAK znaczy „ten obraz api
 * jeszcze ich nie liczy" (kontrakt uzgodniony z wenext-84, wchodzi po stronie control plane'u).
 * Rozróżnienie jest tu po to, żeby ekran nie musiał zgadywać między „rozmowa nie ma nazwy" a
 * „nazwy nikt jeszcze nie liczy", bo pierwsze jest faktem o rozmowie, a drugie o wdrożeniu.
 * `lib/conversations.ts` jest JEDYNYM miejscem, które te trzy stany zamienia na napis, i tam
 * stoi tymczasowe wyliczenie tytułu z celu, do skasowania, gdy pola wejdą do API.
 */
import type { ConversationCard, ConversationProgress } from "./cards-schema";
import { parseCards, parseProgress } from "./cards-schema";
import type { Read } from "./control-plane";

/** What the agent is doing, as the control plane reports it. A closed set, checked on arrival. */
export const SESSION_STATUSES = [
  "QUEUED",
  "RUNNING",
  "WAITING_USER",
  "WAITING_EVENT",
  "PAUSED",
  "DONE",
  "FAILED",
  "BUDGET_EXHAUSTED",
] as const;

export type SessionStatus = (typeof SESSION_STATUSES)[number];

/**
 * Po co sesja w ogóle powstała (D-045). Zamknięty zbiór, sprawdzany na wejściu jak `status`.
 *
 * `CONVERSATION` to rozmowa, którą ktoś zaczął; `PRESENCE` to tik obecności, który agent odpalił
 * sam (P4). Oba są sesjami projektu i oba są w liście, ale pasek rozmów pokazuje wyłącznie
 * pierwsze: lista, w której rozmowy człowieka toną wśród tików co dziesięć minut, przestaje być
 * listą rozmów.
 */
export const SESSION_KINDS = ["CONVERSATION", "PRESENCE"] as const;

export type SessionKind = (typeof SESSION_KINDS)[number];

/** The kinds of line a conversation can hold. Also closed, for the same reason. */
export const EVENT_KINDS = [
  "USER_MESSAGE",
  "ASSISTANT_MESSAGE",
  "TOOL_CALL",
  "TOOL_RESULT",
  "STATUS",
  "ERROR",
  "INTERRUPT",
] as const;

export type EventKind = (typeof EVENT_KINDS)[number];

export interface SessionRow {
  readonly session_id: string;
  readonly project_id: string;
  readonly goal: string;
  readonly status: SessionStatus;
  readonly engine_thread_id: string | null;
  readonly result_summary: string | null;
  readonly result_refs: readonly unknown[];
  readonly created_at: string;
  readonly updated_at: string;
  readonly finished_at: string | null;
  /** D-045. `undefined` = tego pola nie ma w tym obrazie api, `null` = serwer nie ma czego dać. */
  readonly tytul?: string | null;
  readonly wynik_zdanie?: string | null;
  readonly rodzaj?: SessionKind | null;
}

export interface SessionEvent {
  readonly seq: number;
  readonly kind: EventKind;
  readonly payload: Record<string, unknown>;
  readonly created_at: string;
}

/**
 * Ile z limitu tej rozmowy zostało (0026).
 *
 * `null` znaczy, że control plane tego nie przysłał (starszy obraz api), i wtedy pasek stanu
 * MILCZY zamiast pokazywać zera. Zero zużytych tur i brak odpowiedzi wyglądają identycznie, a
 * znaczą coś przeciwnego.
 */
export interface SessionBudget {
  readonly tury: { readonly zuzyte: number; readonly limit: number };
  readonly narzedzia: { readonly zuzyte: number; readonly limit: number };
  readonly minuty_na_ture: number;
  /** Czy limit pochodzi z ustawienia człowieka, czy z wartości domyślnej produktu. */
  readonly wlasny: boolean;
}

export interface SessionDetail {
  readonly session: SessionRow;
  readonly events: readonly SessionEvent[];
  readonly last_seq: number;
  /** End of the trace at this read; last_seq is only the delivered page cursor. */
  readonly latest_seq?: number;
  readonly budzet: SessionBudget | null;
  /**
   * Karty rozmowy i postęp bieżącej tury (D-046, `docs/produkt/KARTY-ROZMOWY.md`).
   *
   * `undefined` znaczy „ten obraz api ich nie liczy" i wtedy ekran rysuje samą rozmowę; pusta
   * tablica znaczy „policzone, nie ma żadnej". `after_seq` ich NIE dotyczy: to jest stan, a nie
   * przyrost, więc przychodzą w całości przy każdym odczycie i przy każdej klatce strumienia.
   */
  readonly karty?: readonly ConversationCard[];
  readonly postep?: ConversationProgress | null;
}

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function report(what: string, missing: readonly string[]): Read<never> {
  return failure(
    `${what}: brakuje pól albo mają zły typ (${missing.join(", ")}). To zepsuty odczyt, ` +
      "a nie pusta rozmowa: ekran nie zgaduje, co agent robi.",
  );
}

function str(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

/** Present and either a string or `null`. Absence is NOT `null` (see the module docstring). */
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

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function parseSession(raw: unknown): Read<SessionRow> {
  const row = object(raw);
  if (row === null) return failure("sesja: odpowiedź nie jest obiektem");

  const missing: string[] = [];
  const sessionId = str(row, "session_id");
  const projectId = str(row, "project_id");
  const goal = str(row, "goal");
  const status = str(row, "status");
  const createdAt = str(row, "created_at");
  const updatedAt = str(row, "updated_at");
  const engineThread = nullableStr(row, "engine_thread_id");
  const summary = nullableStr(row, "result_summary");
  const finishedAt = nullableStr(row, "finished_at");
  const refs = row["result_refs"];

  if (sessionId === null) missing.push("session_id");
  if (projectId === null) missing.push("project_id");
  if (goal === null) missing.push("goal");
  if (status === null || !SESSION_STATUSES.some((known) => known === status)) {
    // An unknown status is a broken read, not a new state to render blankly: the screen would
    // have to guess whether to show "pracuje", and guessing is what this check exists to stop.
    missing.push("status");
  }
  if (createdAt === null) missing.push("created_at");
  if (updatedAt === null) missing.push("updated_at");
  if (!engineThread.present) missing.push("engine_thread_id");
  if (!summary.present) missing.push("result_summary");
  if (!finishedAt.present) missing.push("finished_at");
  if (!Array.isArray(refs)) missing.push("result_refs");

  // TRZY POLA D-045. Brak NIE jest tu zepsutym odczytem, w odróżnieniu od wszystkiego wyżej:
  // kontrakt wchodzi po stronie control plane'u osobno, a ekran ma działać na obu obrazach.
  // Zły TYP już jest zepsutym odczytem, bo znaczy, że rozmawiamy z czymś innym niż to API.
  const title = nullableStr(row, "tytul");
  const resultSentence = nullableStr(row, "wynik_zdanie");
  if ("tytul" in row && !title.present) missing.push("tytul");
  if ("wynik_zdanie" in row && !resultSentence.present) missing.push("wynik_zdanie");
  const kind = row["rodzaj"];
  const kindKnown =
    !("rodzaj" in row) || kind === null || SESSION_KINDS.some((known) => known === kind);
  if (!kindKnown) missing.push("rodzaj");

  if (missing.length > 0) return report("sesja", missing);

  const extra: {
    tytul?: string | null;
    wynik_zdanie?: string | null;
    rodzaj?: SessionKind | null;
  } = {};
  if (title.present) extra.tytul = title.value;
  if (resultSentence.present) extra.wynik_zdanie = resultSentence.value;
  if ("rodzaj" in row) extra.rodzaj = kind as SessionKind | null;

  return {
    ok: true,
    value: {
      ...extra,
      session_id: sessionId as string,
      project_id: projectId as string,
      goal: goal as string,
      status: status as SessionStatus,
      engine_thread_id: (engineThread as { readonly value: string | null }).value,
      result_summary: (summary as { readonly value: string | null }).value,
      result_refs: refs as readonly unknown[],
      created_at: createdAt as string,
      updated_at: updatedAt as string,
      finished_at: (finishedAt as { readonly value: string | null }).value,
    },
  };
}

export function parseEvent(raw: unknown): Read<SessionEvent> {
  const row = object(raw);
  if (row === null) return failure("zdarzenie: nie jest obiektem");

  const missing: string[] = [];
  const seq = row["seq"];
  const kind = str(row, "kind");
  const payload = object(row["payload"]);
  const createdAt = str(row, "created_at");

  if (typeof seq !== "number" || !Number.isInteger(seq) || seq < 1) missing.push("seq");
  if (kind === null || !EVENT_KINDS.some((known) => known === kind)) missing.push("kind");
  if (payload === null) missing.push("payload");
  if (createdAt === null) missing.push("created_at");
  if (missing.length > 0) return report("zdarzenie sesji", missing);

  return {
    ok: true,
    value: {
      seq: seq as number,
      kind: kind as EventKind,
      payload: payload as Record<string, unknown>,
      created_at: createdAt as string,
    },
  };
}

export function parseSessionDetail(raw: unknown): Read<SessionDetail> {
  const body = object(raw);
  if (body === null) return failure("szczegóły sesji: odpowiedź nie jest obiektem");

  const session = parseSession(body["session"]);
  if (!session.ok) return session;

  const rawEvents = body["events"];
  if (!Array.isArray(rawEvents)) return report("szczegóły sesji", ["events"]);
  const events: SessionEvent[] = [];
  for (const entry of rawEvents) {
    const parsed = parseEvent(entry);
    // ONE bad event fails the read. Skipping it would leave a conversation with a hole in it
    // that looks exactly like a conversation where nothing happened.
    if (!parsed.ok) return parsed;
    events.push(parsed.value);
  }

  const lastSeq = body["last_seq"];
  if (typeof lastSeq !== "number" || !Number.isInteger(lastSeq) || lastSeq < 0) {
    return report("szczegóły sesji", ["last_seq"]);
  }
  const latestSeq = body["latest_seq"];
  if ("latest_seq" in body &&
      (typeof latestSeq !== "number" || !Number.isInteger(latestSeq) || latestSeq < lastSeq)) {
    return report("szczegóły sesji", ["latest_seq"]);
  }

  // Karty i postęp (D-046). Nieobecne pola NIE są błędem: starszy obraz api ich nie liczy, a
  // rozmowa bez kart jest normalną rozmową. Obecne, ale zepsute, zrywają odczyt jak wszystko
  // inne w tym pliku: karta w nieznanym stanie musiałaby zostać narysowana jakkolwiek, a
  // „jakkolwiek" przy karcie konta znaczy „Połączono" nad kontem, które połączone nie jest.
  const extra: { karty?: readonly ConversationCard[]; postep?: ConversationProgress | null } = {};
  if ("karty" in body) {
    const cards = parseCards(body["karty"]);
    if (!cards.ok) return cards;
    extra.karty = cards.value;
  }
  if ("postep" in body) {
    const progress = parseProgress(body["postep"]);
    if (!progress.ok) return progress;
    extra.postep = progress.value;
  }

  return {
    ok: true,
    value: {
      ...extra,
      session: session.value,
      events,
      last_seq: lastSeq,
      ...(typeof latestSeq === "number" ? { latest_seq: latestSeq } : {}),
      budzet: parseBudget(body["budzet"]),
    },
  };
}

function pair(raw: unknown): { readonly zuzyte: number; readonly limit: number } | null {
  const value = object(raw);
  if (value === null) return null;
  const used = value["zuzyte"];
  const limit = value["limit"];
  if (typeof used !== "number" || typeof limit !== "number") return null;
  return { zuzyte: used, limit };
}

/** Budżet albo `null`. Nigdy połowa budżetu: ekran pokazujący jedną liczbę z dwóch kłamie. */
export function parseBudget(raw: unknown): SessionBudget | null {
  const value = object(raw);
  if (value === null) return null;
  const tury = pair(value["tury"]);
  const narzedzia = pair(value["narzedzia"]);
  const minuty = value["minuty_na_ture"];
  if (tury === null || narzedzia === null || typeof minuty !== "number") return null;
  return { tury, narzedzia, minuty_na_ture: minuty, wlasny: value["wlasny"] === true };
}

export function parseSessionList(raw: unknown): Read<readonly SessionRow[]> {
  const body = object(raw);
  if (body === null) return failure("lista sesji: odpowiedź nie jest obiektem");
  const rows = body["sessions"];
  if (!Array.isArray(rows)) return report("lista sesji", ["sessions"]);
  const sessions: SessionRow[] = [];
  for (const entry of rows) {
    const parsed = parseSession(entry);
    if (!parsed.ok) return parsed;
    sessions.push(parsed.value);
  }
  return { ok: true, value: sessions };
}

/**
 * Whether this status means a turn is actually in flight.
 *
 * The one place that decides it, because the animated mark and the word on screen must not be
 * able to disagree. RUNNING is the only status that means the model is being waited on; QUEUED
 * means a job exists and has not been claimed, which is NOT the same thing and must not wave.
 */
export function isWaitingOnTheModel(status: SessionStatus): boolean {
  return status === "RUNNING";
}
