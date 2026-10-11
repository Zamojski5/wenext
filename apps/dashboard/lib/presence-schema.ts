/**
 * What `api/presence.py` and `api/delegations.py` answer, parsed once and narrowed here.
 *
 * WHY PARSING AND NOT CASTING. A screen that says "agent czuwa" because a field was `undefined`
 * would be lying about the one thing this milestone exists to report. Every field this module
 * returns has been checked; anything it cannot read comes back as a stated reason, and the
 * screen prints the reason instead of a state.
 *
 * THE TWO NUMBERS THAT ARE NOT THE SAME NUMBER. `budget` is what the person SIGNED and `limits`
 * is what actually binds, and they differ exactly when a budget key was left out (the backend
 * then applies a conservative default). Both travel, and the panel shows the second one marked
 * as not chosen, because a founder must never sign a scope whose real pace is invisible to them.
 */
import type { Read } from "./control-plane";
import { warsawMoment } from "./studio-labels";

/**
 * One of six words, decided by the backend so the product has one definition of "working".
 * `wygasły` (26.09) is a delegation past its deadline, checked there after `odwołany` and before
 * `wstrzymany`: a paused signature that also ran out is expired, and resuming would not help.
 */
export type PresenceState =
  | "niepowierzony"
  | "odwołany"
  | "wygasły"
  | "wstrzymany"
  | "pracuje"
  | "czuwa";

export interface DelegationLimits {
  readonly publikacje_godzina: number;
  readonly publikacje_doba: number;
  /** 0023: own posts have a ceiling of their own, and it binds whatever publications may do. */
  readonly wlasne_posty_doba: number;
  readonly tury_modelu_doba: number | null;
  /** Which limits nobody chose. Rendered differently, never hidden. */
  readonly domyslne: readonly string[];
}

export interface Delegation {
  readonly delegation_id: string;
  readonly mode: string;
  readonly state: string;
  readonly live: boolean;
  readonly accounts: readonly string[];
  readonly communities: readonly string[];
  readonly actions: readonly string[];
  readonly limits: DelegationLimits;
  readonly scope_sha256: string;
  readonly signed_at: string;
  readonly paused_at: string | null;
  readonly revoked_at: string | null;
  /**
   * When the signature stops covering work (26.09). `null` for a delegation signed without a
   * deadline, and for an older control plane that does not send the field: absence is "no
   * deadline known", never "no deadline".
   */
  readonly expires_at: string | null;
}

export interface Wakeup {
  readonly wakeup_id: string;
  readonly reason: string;
  readonly session_id: string | null;
  /** A CODE from a closed list, or `null` when the model ran. The screen groups by it. */
  readonly skipped_reason: string | null;
  /** The sentence for a person, as it was at that moment. */
  readonly skipped_detail: string | null;
  readonly cost: Record<string, unknown> | null;
  readonly created_at: string;
  /** 0023: when the agent said it would come back. `null` for a wake-up an event caused. */
  readonly due_at: string | null;
  readonly cancelled_at: string | null;
  /**
   * What the agent wrote down when it promised to come back. `reason` is a CODE and for a
   * scheduled return it is always "DUE", so a screen showing the code shows a list of identical
   * rows; only this sentence says what the agent is coming back to.
   */
  readonly note: string | null;
}

export interface Budget {
  readonly tury_modelu_doba: number;
  readonly publikacje_doba: number;
  readonly publikacje_godzina: number;
  readonly wlasne_posty_doba: number;
  readonly limit_tur_modelu: number | null;
  readonly limit_publikacji_doba: number;
  readonly limit_publikacji_godzina: number;
  /** The first spent limit, named, or `null`. A boolean here would tell nobody when to return. */
  readonly wyczerpany: string | null;
}

export interface Presence {
  readonly state: PresenceState;
  readonly delegation: Delegation | null;
  readonly budget: Budget | null;
  readonly wakeups: readonly Wakeup[];
  /**
   * Returns the agent SCHEDULED and that have not come due yet. A separate list from `wakeups`
   * on purpose: those already happened, these are still a promise, and one list holding both
   * would be a screen where the future looks like the past.
   */
  readonly planned: readonly Wakeup[];
  /**
   * Praca KOLEKTORA przestrzeni, pod własną nazwą (E6, audyt UX §6 zdanie trzecie).
   *
   * `state` mówi o agencie TEGO projektu czterema słowami, a „czuwa" czyta się jak „nic się nie
   * dzieje" — choć w tym samym czasie kolektor pobiera wątki dla całej przestrzeni. To są dwie
   * różne prace i dlatego są dwa różne pola, a nie piąte słowo w `state`.
   *
   * `null` znaczy „starszy control plane tego nie liczy", a nie „kolektor nie pracuje". Zero
   * obserwowanych znaczy „zmierzono, nie ma ani jednej".
   */
  readonly collector: CollectorState | null;
}

export interface CollectorState {
  readonly obserwowanych: number;
  readonly znanych: number;
  /**
   * `null` przy `obserwowanych > 0` to TRZECIA odpowiedź: włączono obserwację i jeszcze nic nie
   * pobrano. Nie zero i nie „nie wiadomo".
   */
  readonly ostatni_odczyt: string | null;
}

export interface Decision {
  readonly revision_id: string;
  readonly payload_hash: string;
  readonly text: string;
  readonly target_url: string;
  readonly target_external_id: string;
  readonly account: string;
  readonly created_at: string;
  readonly expires_at: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function count(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function strings(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((one): one is string => typeof one === "string") : [];
}

const STATES: readonly PresenceState[] = [
  "niepowierzony",
  "odwołany",
  "wygasły",
  "wstrzymany",
  "pracuje",
  "czuwa",
];

function parseLimits(value: unknown): DelegationLimits {
  const raw = isObject(value) ? value : {};
  return {
    publikacje_godzina: count(raw["publikacje_godzina"]),
    publikacje_doba: count(raw["publikacje_doba"]),
    wlasne_posty_doba: count(raw["wlasne_posty_doba"]),
    tury_modelu_doba:
      typeof raw["tury_modelu_doba"] === "number" ? (raw["tury_modelu_doba"] as number) : null,
    domyslne: strings(raw["domyslne"]),
  };
}

export function parseDelegation(value: unknown): Delegation | null {
  if (!isObject(value)) return null;
  const id = text(value["delegation_id"]);
  if (id === "") return null;
  const scope = isObject(value["scope"]) ? value["scope"] : {};
  return {
    delegation_id: id,
    mode: text(value["mode"], "?"),
    state: text(value["state"], "?"),
    live: value["live"] === true,
    accounts: strings(scope["accounts"]),
    communities: strings(scope["communities"]),
    actions: strings(scope["actions"]),
    limits: parseLimits(value["limits"]),
    scope_sha256: text(value["scope_sha256"]),
    signed_at: text(value["signed_at"]),
    paused_at: typeof value["paused_at"] === "string" ? value["paused_at"] : null,
    revoked_at: typeof value["revoked_at"] === "string" ? value["revoked_at"] : null,
    expires_at: typeof value["expires_at"] === "string" ? value["expires_at"] : null,
  };
}

/** The delegation state the backend gives a signature whose deadline has passed (26.09). */
export const DELEGATION_EXPIRED = "wygasła";

/** The same fact as the project's presence word: masculine, like `wstrzymany` and `odwołany`. */
export const PRESENCE_EXPIRED = "wygasły";

/**
 * Has this project's delegation run out, so that the screen offers a new signature?
 *
 * EITHER WORD COUNTS, the project's (`wygasły`) and the delegation's (`wygasła`), because both are
 * the backend's and either may arrive alone: a presence read that no longer carries the old
 * delegation still says `wygasły`. The backend closes the expired signature inside the new one's
 * transaction, so offering the sign form here needs no revocation first.
 */
export function delegationHasExpired(presence: Pick<Presence, "state" | "delegation">): boolean {
  return presence.state === PRESENCE_EXPIRED || presence.delegation?.state === DELEGATION_EXPIRED;
}

/**
 * The sentence next to the delegation's state: until when it holds, or since when it does not.
 *
 * THE STATE DECIDES WHICH SENTENCE, NOT THE CLOCK OF THIS SERVER. "Wygasło" is said when the
 * backend says `wygasła`, because the backend is what stops the agent; comparing `expires_at` with
 * our own clock here would be a second definition of "expired" that could disagree with the one
 * that governs the work. `null` means there is nothing to say: no deadline, and not expired.
 */
export function deadlineSentence(
  delegation: Pick<Delegation, "state" | "expires_at">,
): string | null {
  const when = delegation.expires_at === null ? null : warsawMoment(delegation.expires_at);
  if (delegation.state === DELEGATION_EXPIRED) {
    return when === null ? "Wygasło." : `Wygasło ${when}.`;
  }
  return when === null ? null : `Ważne do ${when}.`;
}

/**
 * The deadlines a signature made on the presence screen may choose, in hours (26.09).
 *
 * ONE LIST FOR THE FORM AND FOR THE HANDLER. The select renders these options and
 * `app/studio/presence/route.ts` refuses any other value; two lists would be two definitions of
 * what may be signed, which is how a hand-made POST gets a year-long delegation.
 */
export const DELEGATION_EXPIRY_CHOICES: readonly { readonly hours: number; readonly label: string }[] = [
  { hours: 24, label: "1 dzień" },
  { hours: 72, label: "3 dni" },
  { hours: 168, label: "7 dni" },
  { hours: 336, label: "14 dni" },
];

/** Seven days: long enough for a week of work, short enough that nobody forgets they signed. */
export const DEFAULT_DELEGATION_EXPIRY_HOURS = 168;

/**
 * `expires_at` for a signature made now: `now` plus the chosen hours, as an ISO instant in UTC.
 * `null` for a value outside `DELEGATION_EXPIRY_CHOICES`, which the handler turns into a refusal.
 */
export function delegationExpiresAt(hours: number, now: Date): string | null {
  if (!DELEGATION_EXPIRY_CHOICES.some((choice) => choice.hours === hours)) return null;
  return new Date(now.getTime() + hours * 3_600_000).toISOString();
}

export function parsePresence(value: unknown): Read<Presence> {
  if (!isObject(value)) {
    return { ok: false, unauthorized: false, reason: "stan obecności nie jest obiektem" };
  }
  const state = text(value["state"]);
  if (!STATES.includes(state as PresenceState)) {
    return {
      ok: false,
      unauthorized: false,
      reason: `control plane podał stan "${state}", którego ten ekran nie zna`,
    };
  }
  const wakeups = parseWakeups(value["wakeups"]);
  const planned = parseWakeups(value["planned"]);
  let budget: Budget | null = null;
  if (isObject(value["budget"])) {
    const raw = value["budget"];
    budget = {
      tury_modelu_doba: count(raw["tury_modelu_doba"]),
      publikacje_doba: count(raw["publikacje_doba"]),
      publikacje_godzina: count(raw["publikacje_godzina"]),
      wlasne_posty_doba: count(raw["wlasne_posty_doba"]),
      limit_tur_modelu:
        typeof raw["limit_tur_modelu"] === "number" ? (raw["limit_tur_modelu"] as number) : null,
      limit_publikacji_doba: count(raw["limit_publikacji_doba"]),
      limit_publikacji_godzina: count(raw["limit_publikacji_godzina"]),
      wyczerpany: typeof raw["wyczerpany"] === "string" ? raw["wyczerpany"] : null,
    };
  }
  return {
    ok: true,
    value: {
      state: state as PresenceState,
      delegation: parseDelegation(value["delegation"]),
      budget,
      wakeups,
      planned,
      collector: parseCollector(value["collector"]),
    },
  };
}

/** Kolektor albo `null`. Brak pola znaczy „ten obraz go nie liczy", a nie „stoi". */
function parseCollector(raw: unknown): CollectorState | null {
  if (!isObject(raw)) return null;
  const observed = raw["obserwowanych"];
  const known = raw["znanych"];
  if (typeof observed !== "number" || typeof known !== "number") return null;
  const last = raw["ostatni_odczyt"];
  return {
    obserwowanych: observed,
    znanych: known,
    ostatni_odczyt: typeof last === "string" ? last : null,
  };
}

/**
 * Co robi kolektor, jednym zdaniem, obok zdania o agencie.
 *
 * Trzy odpowiedzi, bo trzy są prawdziwe: nie obserwujemy niczego, obserwujemy i jeszcze nic nie
 * przeczytaliśmy, obserwujemy i ostatni odczyt był wtedy. Druga jest tą, której brakowało:
 * „włączyłem" to nie „mam stamtąd wątki".
 */
export function collectorSentence(collector: CollectorState): string {
  if (collector.obserwowanych === 0) {
    return collector.znanych === 0
      ? "Kolektor nie ma czego czytać: ta przestrzeń nie obserwuje żadnej społeczności."
      : "Kolektor nic tu nie czyta: żadna ze znanych społeczności nie jest obserwowana.";
  }
  const where = `${collector.obserwowanych} z ${collector.znanych}`;
  return collector.ostatni_odczyt === null
    ? `Kolektor ma czytać ${where} społeczności tej przestrzeni i jeszcze nic nie pobrał.`
    : `Kolektor czyta ${where} społeczności tej przestrzeni; ostatni odczyt: ${collector.ostatni_odczyt.replace("T", " ").slice(0, 16)}.`;
}

/**
 * One shape, read the same way whichever list it came from.
 *
 * A wake-up that already happened and a return still to come are THE SAME ROW in the database
 * (0023), told apart by `due_at` and the two timestamps; parsing them with two readers would be
 * two chances to disagree about what a row means.
 */
function parseWakeups(raw: unknown): Wakeup[] {
  const rows: Wakeup[] = [];
  for (const one of Array.isArray(raw) ? raw : []) {
    if (!isObject(one)) continue;
    rows.push({
      wakeup_id: text(one["wakeup_id"]),
      reason: text(one["reason"], "?"),
      session_id: typeof one["session_id"] === "string" ? one["session_id"] : null,
      skipped_reason: typeof one["skipped_reason"] === "string" ? one["skipped_reason"] : null,
      skipped_detail: typeof one["skipped_detail"] === "string" ? one["skipped_detail"] : null,
      cost: isObject(one["cost"]) ? one["cost"] : null,
      created_at: text(one["created_at"]),
      due_at: typeof one["due_at"] === "string" ? one["due_at"] : null,
      cancelled_at: typeof one["cancelled_at"] === "string" ? one["cancelled_at"] : null,
      note:
        typeof one["notatka"] === "string" && one["notatka"].trim() !== ""
          ? one["notatka"]
          : null,
    });
  }
  return rows;
}

export function parseDecisions(value: unknown): Read<readonly Decision[]> {
  if (!isObject(value) || !Array.isArray(value["decisions"])) {
    return { ok: false, unauthorized: false, reason: "kolejka decyzji nie jest listą" };
  }
  const decisions: Decision[] = [];
  for (const one of value["decisions"]) {
    if (!isObject(one)) continue;
    const id = text(one["revision_id"]);
    if (id === "") continue;
    decisions.push({
      revision_id: id,
      payload_hash: text(one["payload_hash"]),
      text: text(one["text"]),
      target_url: text(one["target_url"]),
      target_external_id: text(one["target_external_id"]),
      account: text(one["account"], "?"),
      created_at: text(one["created_at"]),
      expires_at: text(one["expires_at"]),
    });
  }
  return { ok: true, value: decisions };
}

/**
 * The sentence the status block leads with. One per state, written for somebody who has just
 * opened the laptop and wants to know whether they have to do anything.
 */
export function stateSentence(presence: Presence): string {
  switch (presence.state) {
    case "niepowierzony":
      return "Ten projekt nie jest powierzony agentowi, więc nic się w nim nie dzieje bez Ciebie.";
    case "odwołany":
      return "Powierzenie zostało odwołane. Agent nie podejmuje tu pracy.";
    case "wygasły":
      return "Powierzenie wygasło; praca w tle stoi, dalsza praca wymaga nowego podpisu.";
    case "wstrzymany":
      return "Powierzenie jest wstrzymane. Wróci tym samym podpisem, gdy je wznowisz.";
    case "pracuje":
      return "Agent pracuje w tej chwili.";
    default:
      return "Agent czuwa: obserwuje i obudzi się, gdy będzie po co.";
  }
}

/** Why one wake-up did nothing, in words. The codes come from `presence/store.py`. */
export function skipSentence(wakeup: Wakeup): string {
  if (wakeup.skipped_detail !== null && wakeup.skipped_detail !== "") return wakeup.skipped_detail;
  switch (wakeup.skipped_reason) {
    case "NO_EVENTS":
      return "nic nowego; model nie był uruchamiany";
    case "NO_DELEGATION":
      return "projekt nie ma powierzenia";
    case "PAUSED":
      return "powierzenie wstrzymane";
    case "BUDGET_EXHAUSTED":
      return "budżet wyczerpany";
    case "NO_CONNECTION":
      return "brak połączenia Codex";
    case "ERROR":
      return "tik zakończył się błędem";
    default:
      return "w toku";
  }
}

/** What woke the agent, in words a person recognises. */
export function reasonLabel(reason: string): string {
  switch (reason) {
    case "NEW_EVENT":
      return "nowe zdarzenie";
    case "DUE":
      return "zaplanowany obchód";
    case "USER_MESSAGE":
      return "wiadomość od Ciebie";
    case "RETRY":
      return "ponowienie";
    default:
      return reason;
  }
}

/**
 * What a delegation may usefully name for one account, given its platform (P6).
 *
 * THE PANEL SHOWS THIS NEXT TO EVERY ACCOUNT, and that is the point: a person signing a scope
 * can tick REPLY for an X account in perfect good faith, and no adapter in this product can
 * carry it out. The backend refuses such a proposal afterwards with a sentence naming the
 * platform (`presence/scope.py::action_supported`); saying it BEFORE the signature is what keeps
 * the signature meaningful. The rule is the backend's; this reads its flags and does not invent
 * a second one.
 */
export function actionsPossibleFor(support: {
  readonly comment: boolean;
  readonly own_post: boolean;
} | null): readonly string[] {
  if (support === null) return ["RESEARCH"];
  const possible = ["RESEARCH"];
  if (support.comment) possible.unshift("REPLY");
  if (support.own_post) possible.push("OWN_POST");
  return possible;
}

/** One sentence about what this account can do here. Polish: the founder reads it. */
export function accountCapabilityNote(support: { readonly comment: boolean; readonly own_post: boolean; readonly label: string } | null): string {
  if (support === null) return "platforma nieznana temu produktowi: tylko czytanie i zapamiętywanie";
  const possible = actionsPossibleFor(support);
  if (possible.length === 1) {
    return `${support.label}: ten produkt nie umie tu dziś publikować, zostaje czytanie i zapamiętywanie`;
  }
  return `${support.label}: ${possible.join(", ")}`;
}

/**
 * What this product knows about an account's audience TODAY (P6 etap A).
 *
 * THE ANSWER IS "NIC", AND SAYING IT IS THE FEATURE. Migration 0019 has a table for these
 * numbers, with a source and a moment of reading on every row, and nothing in this repository
 * writes to it because no adapter reads them from any platform. ZAKRES-FUNKCJONALNY §6 rule 2
 * says a missing measurement is shown as missing, never as a zero, and a follower count is the
 * single most tempting place to print a zero: it looks like a number, and a zero followers reads
 * as a fact about the account rather than about us.
 *
 * `audience_metrics` is the flag in the backend's capability matrix. It is false on every
 * platform, so this function currently returns the same sentence for all of them; it is written
 * against the FLAG rather than against the platform names so that the day an adapter lands, the
 * screen stops lying without anybody having to remember this file.
 */
export function audienceNote(
  support: {
    readonly label: string;
    readonly audience_metrics: boolean;
  } | null,
  // 27.09: the latest reading, when there is one (X, from the hourly read of our own profile).
  reading: {
    readonly obserwujacy: number;
    readonly odczyt: string;
    readonly zmiana_doby: number | null;
  } | null = null,
): string {
  if (support === null || !support.audience_metrics) {
    return "zasięg: brak danych, ten produkt jeszcze nie umie ich odczytać";
  }
  if (reading === null) return "zasięg: brak odczytów";
  const change =
    reading.zmiana_doby === null
      ? "zmiany od doby jeszcze nie znamy"
      : `od doby ${reading.zmiana_doby > 0 ? "+" : ""}${reading.zmiana_doby}`;
  return `obserwujący: ${reading.obserwujacy} (odczyt ${warsawMoment(reading.odczyt)}), ${change}`;
}

/**
 * Why the numbers of several accounts are never added up into "people".
 *
 * A person may have three accounts, and two accounts may be followed by the same person. Adding
 * followers across accounts produces a number that looks like an audience and is not one; it is
 * the oldest vanity metric there is. The screen prints this sentence instead of a sum, and a
 * test asserts that no total appears next to it.
 */
export const AUDIENCE_SUM_IS_NOT_PEOPLE =
  "Liczb z różnych kont nie sumujemy: ta sama osoba bywa po obu stronach, a suma wyglądałaby na zasięg, którym nie jest.";

/**
 * Stan całej usługi, nie jednego projektu (P7 etap B).
 *
 * DLACZEGO OSOBNO OD OBECNOŚCI PROJEKTU. „Agent czuwa" i „usługa działa" to dwa różne zdania o
 * dwóch różnych rzeczach, i mylenie ich jest dokładnie tym, przed czym ten pasek ma chronić:
 * projekt może spokojnie czuwać, bo nic się nie dzieje, ALBO dlatego, że kolektor nie żyje od
 * czterech godzin. Bez tego paska oba wyglądają tak samo.
 *
 * Zdanie składa backend (`api/ops.py`), a nie ten moduł. Dwa miejsca liczące „czy usługa działa"
 * rozjechałyby się, a to jest zdanie, przy którym rozjechanie się jest najkosztowniejsze.
 */
export interface ServiceIncident {
  readonly component: string;
  readonly since: string;
  readonly reason: string;
}

export interface ServiceStatus {
  /** Gotowe zdanie z backendu: „usługa działa" albo „część niedostępna: X od Y". */
  readonly sentence: string;
  readonly healthy: boolean;
  readonly incidents: readonly ServiceIncident[];
}

export function parseServiceStatus(value: unknown): ServiceStatus | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  const sentence = typeof raw["stan"] === "string" ? raw["stan"] : "";
  if (sentence === "") return null;
  const listed = Array.isArray(raw["niedostepne"]) ? raw["niedostepne"] : [];
  const incidents: ServiceIncident[] = [];
  for (const one of listed) {
    if (typeof one !== "object" || one === null) continue;
    const row = one as Record<string, unknown>;
    incidents.push({
      component: typeof row["komponent"] === "string" ? row["komponent"] : "?",
      since: typeof row["od"] === "string" ? row["od"] : "",
      reason: typeof row["powod"] === "string" ? row["powod"] : "",
    });
  }
  return { sentence, healthy: raw["dziala"] === true, incidents };
}
