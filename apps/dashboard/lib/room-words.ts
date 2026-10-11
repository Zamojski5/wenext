/**
 * The sentences of the "Pokój" screen, in one place so a test can walk them.
 *
 * Everything here is a pure function of the room and of ONE instant (`now`), which the page reads
 * once per request and passes down. A component calling `new Date()` for itself would make two
 * cards disagree about what "2 h temu" means when the render straddles a minute, and would make
 * the screen untestable.
 *
 * THREE RULES CARRIED FROM THE REST OF THE DASHBOARD, because they are about honesty:
 *
 *   * `null` is never a number. A score nobody read is "brak pomiaru", not 0 (L5241 point 5);
 *   * the headline counts only people the founder can still ANSWER. A thread already answered or
 *     skipped is not "writing right now", so it is not in the number the promise is built on;
 *   * a word like "właśnie" is a claim about time and is used only while it is true.
 */
import { plural } from "./format";
import type { Opportunity, Room, RoomDraft } from "./room-schema";
import { accountHandle } from "./studio-labels";

const WARSAW_CLOCK = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const WARSAW_DAY = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw",
  day: "numeric",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * `13 października, 15:32`. Assembled from the parts, not taken from `format()`: the separator
 * between the date and the time is whatever the ICU build of the moment prefers (a comma in one,
 * nothing in another), and a sentence that changes with the Node image is not a sentence a test
 * can pin. The same reason `warsawMoment` in `studio-labels.ts` builds its own.
 */
function warsawDay(date: Date): string {
  const parts: Partial<Record<string, string>> = {};
  for (const part of WARSAW_DAY.formatToParts(date)) parts[part.type] = part.value;
  return `${parts["day"] ?? ""} ${parts["month"] ?? ""}, ${parts["hour"] ?? ""}:${parts["minute"] ?? ""}`;
}

/** `2026-10-10T13:32:00+00:00` as `15:32` on the wall clock of the person reading it. */
export function warsawClock(date: Date): string {
  return WARSAW_CLOCK.format(date);
}

/**
 * How long ago, the way a person says it: "przed chwilą", "12 min temu", "2 h temu", "wczoraj",
 * "3 dni temu". `null` when there is no time to speak of.
 *
 * A moment a little in the FUTURE (the two clocks disagree by seconds) reads as "przed chwilą";
 * one far in the future is printed as the instant it is, because hiding a clock that is hours off
 * would hide the very thing somebody has to fix.
 */
export function ageSentence(iso: string | null, now: Date): string | null {
  if (iso === null) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "data nieczytelna";
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < -300) return warsawDay(then);
  if (seconds < 120) return "przed chwilą";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min temu`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h temu`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "wczoraj";
  return `${days} dni temu`;
}

/** Whole hours since `iso`, or `null` when it does not parse. For deciding how fresh is "fresh". */
export function hoursSince(iso: string | null, now: Date): number | null {
  if (iso === null) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  return (now.getTime() - then.getTime()) / 3_600_000;
}

export interface Expiry {
  readonly text: string;
  /** `true` once the approval window has passed. Nothing can be published from this draft. */
  readonly expired: boolean;
}

/**
 * The approval window of a draft as a sentence. A deadline is read in order to act on it, so a
 * far one is the wall-clock day in Warsaw (the same reasoning as `warsawMoment`), and a near one
 * is a countdown. `null` when the control plane gave no deadline: then nothing is said, because
 * "bez terminu" would be a promise the approval route does not make.
 */
export function expirySentence(iso: string | null, now: Date): Expiry | null {
  if (iso === null) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return { text: "termin nieczytelny", expired: false };
  const minutes = Math.ceil((at.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return { text: "Okno zgody minęło", expired: true };
  if (minutes < 60) return { text: `Wygasa za ${minutes} min`, expired: false };
  const hours = Math.floor(minutes / 60);
  if (hours < 36) return { text: `Wygasa za ${hours} h`, expired: false };
  return { text: `Wygasa ${warsawDay(at)}`, expired: false };
}

/** A measured number, or the words for "nobody measured it". Never `0` for `null`. */
export function figure(value: number | null): { readonly text: string; readonly measured: boolean } {
  if (value === null) return { text: "brak pomiaru", measured: false };
  return { text: value.toLocaleString("pl-PL"), measured: true };
}

/** The opportunities a person can still act on. */
export function openOpportunities(room: Room): readonly Opportunity[] {
  return room.opportunities.filter((one) => one.state === "NOWA" || one.state === "SZKIC");
}

/** Past "właśnie" the sentence says "ostatnio": a day-old thread is not being written right now. */
const FRESH_HOURS = 48;

export interface Headline {
  /** The strong part, "8 osób". */
  readonly count: string;
  /** The rest of the sentence. */
  readonly rest: string;
  readonly open: number;
}

/**
 * The promise, with the live count in it: "8 osób właśnie pisze o problemie, który rozwiązuje Twój
 * kod." The verb agrees with the number the way Polish needs it (1 osoba pisze, 2 osoby piszą,
 * 5 osób pisze). `null` when there is nobody, because the room then makes its promise another way
 * (`EmptyRoom`) and must never say "0 osób właśnie pisze".
 */
export function headlineOf(room: Room, now: Date): Headline | null {
  const open = openOpportunities(room);
  if (open.length === 0) return null;
  const hours = open
    .map((one) => hoursSince(one.created_at, now))
    .filter((one): one is number => one !== null);
  const freshest = hours.length === 0 ? null : Math.min(...hours);
  const when = freshest !== null && freshest <= FRESH_HOURS ? "właśnie" : "ostatnio";
  const people = plural(open.length, ["osoba", "osoby", "osób"]);
  const verb = plural(open.length, ["pisze", "piszą", "pisze"]);
  return {
    count: `${open.length} ${people}`,
    rest: `${when} ${verb} o problemie, który rozwiązuje Twój kod.`,
    open: open.length,
  };
}

/** "r/devops" for Reddit however the control plane spelled the community; X keeps what it has. */
export function communityLabel(platform: string, community: string | null): string | null {
  if (community === null || community.trim() === "") return null;
  const bare = community.trim();
  if (platform === "REDDIT") return bare.startsWith("r/") ? bare : `r/${bare}`;
  return bare;
}

/** The name of a person on the platform, as the platform writes it. `null` has no author line. */
export function authorLabel(platform: string, author: string | null): string | null {
  if (author === null || author.trim() === "") return null;
  const bare = author.trim();
  if (platform === "REDDIT") return bare.startsWith("u/") ? bare : `u/${bare}`;
  return accountHandle(platform, bare);
}

/** Two capital letters for an avatar. A placeholder handle gives "Ty", never a stranger's letters. */
export function initialsOf(handle: string | null): string {
  if (handle === null) return "Ty";
  const letters = handle.replace(/^(u\/|@)/, "").replace(/[^A-Za-z0-9ĄĆĘŁŃÓŚŹŻąćęłńóśźż]/g, "");
  return letters === "" ? "Ty" : letters.slice(0, 2).toUpperCase();
}

/** The handle shown on a draft, taken from the account assigned to the project, or a placeholder. */
export function draftHandle(
  platform: string,
  assigned: readonly { readonly platform: string; readonly external_account_id: string }[],
): { readonly text: string; readonly real: boolean } {
  const own = assigned.find((one) => one.platform === platform);
  if (own !== undefined && own.external_account_id.trim() !== "") {
    const shown = authorLabel(platform, own.external_account_id);
    if (shown !== null) return { text: shown, real: true };
  }
  return { text: platform === "X" ? "@twoje_konto" : "u/twoje_konto", real: false };
}

export interface DraftState {
  readonly label: string | null;
  /** Whether "Opublikuj" and "Popraw" make sense. The control plane stays the judge. */
  readonly actionable: boolean;
}

/**
 * What the proposal's state means for the two buttons.
 *
 * THE CONTRACT GIVES `stan` AS A WORD AND NOT ITS LIST, so this names the states the approval
 * route is known to have (`ActionProposalState`) and treats the rest as "ask the server". A
 * state in the first group says so in words and takes the buttons away; an unknown one keeps them,
 * because the route answers 409 to a click it does not accept and that answer is explained on the
 * screen, while a missing button would be a draft nobody could ever send.
 */
export function draftStateOf(draft: RoomDraft): DraftState {
  switch (draft.state.toUpperCase()) {
    case "APPROVED":
    case "PUBLISHING":
    case "ISSUED":
      return { label: "W kolejce do publikacji", actionable: false };
    case "PUBLISHED":
      return { label: "Opublikowano", actionable: false };
    case "EXPIRED":
      return { label: "Okno zgody minęło", actionable: false };
    case "REJECTED":
    case "SUPERSEDED":
      return { label: "Zastąpiony nowszą wersją", actionable: false };
    case "READY_FOR_APPROVAL":
    // What the control plane's room endpoint actually sends (opportunities/room.py).
    case "CZEKA_NA_ZGODE":
      return { label: "Czeka na Twoją zgodę", actionable: true };
    default:
      return { label: null, actionable: true };
  }
}

/** One short sentence about the whole room for the line under the headline. */
export function summaryOf(room: Room, now: Date): string[] {
  const parts: string[] = [];
  const waiting = room.drafts.filter((one) => draftStateOf(one).actionable).length;
  if (waiting > 0) {
    parts.push(
      `${waiting} ${plural(waiting, ["odpowiedź czeka", "odpowiedzi czekają", "odpowiedzi czeka"])} na Twoją zgodę`,
    );
  }
  const freshest = openOpportunities(room)
    .map((one) => one.created_at)
    .filter((one): one is string => one !== null)
    .map((one) => new Date(one))
    .filter((one) => !Number.isNaN(one.getTime()))
    .sort((a, b) => b.getTime() - a.getTime())[0];
  if (freshest !== undefined) {
    const age = ageSentence(freshest.toISOString(), now);
    if (age !== null) parts.push(`najnowsza ${age}`);
  }
  return parts;
}
