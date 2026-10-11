/**
 * Closed-list values turned into the words the founder reads, in ONE place.
 *
 * An unknown value PRINTS ITSELF rather than falling back to a friendly default. The lists are
 * closed in the database (`projects_mode_is_known`, `person_profiles_kind_is_known`), so a value
 * outside them means the two sides have drifted — and "Autonomiczny" shown for a mode the screen
 * does not recognise would hide exactly that.
 */
const MODES: Readonly<Record<string, string>> = {
  AUTONOMOUS: "Autonomiczny",
  REVIEW: "Z przeglądem",
  RESEARCH: "Research",
};

const MODE_NOTES: Readonly<Record<string, string>> = {
  AUTONOMOUS: "Agent prowadzi cel sam i publikuje w powierzonym zakresie.",
  REVIEW: "Agent przygotowuje treści i czeka na Twoją decyzję przed publikacją.",
  RESEARCH: "Agent zbiera wiedzę i wnioski. Nic nie publikuje.",
};

const KINDS: Readonly<Record<string, string>> = {
  PERSON: "osoba",
  BRAND: "marka",
};

export function modeLabel(mode: string): string {
  return MODES[mode] ?? mode;
}

/** One sentence about what the mode means for the work. Empty for a mode nobody declared. */
export function modeNote(mode: string): string {
  return MODE_NOTES[mode] ?? "";
}

export function kindLabel(kind: string): string {
  return KINDS[kind] ?? kind;
}

/** `2026-09-19T11:04:33+00:00` → `2026-09-19 11:04`. `null` says so in words, never as a dash. */
export function moment(value: string | null): string {
  return value === null ? "brak odczytu" : value.replace("T", " ").slice(0, 16);
}

/**
 * Nazwa platformy dla człowieka. Nieznana PISZE SIĘ SAMA, tak jak tryb wyżej.
 *
 * Lista jest zamknięta w bazie (`communities_platform_is_known`, `social_accounts`), więc wartość
 * spoza niej znaczy, że dwie strony się rozjechały. „Reddit" pokazany dla czegoś, czego ekran nie
 * rozpoznaje, ukryłby właśnie to.
 */
const PLATFORMS: Readonly<Record<string, string>> = {
  REDDIT: "Reddit",
  LINKEDIN: "LinkedIn",
  HACKER_NEWS: "Hacker News",
  X: "X",
};

export function platformLabel(platform: string): string {
  return PLATFORMS[platform] ?? platform;
}

/**
 * An account handle as its platform writes it: `@name` on X, unchanged elsewhere.
 *
 * ONE DEFINITION FOR BOTH ENDS OF THE CONNECT FLOW. The account card shows the handle, and the
 * sentence `/studio/card` sends into the conversation after a successful check repeats it. Spelled
 * twice, the two would disagree about the `@` the first time the backend sends it already there.
 */
export function accountHandle(platform: string | null, login: string): string {
  const bare = login.trim();
  return platform === "X" ? `@${bare.replace(/^@+/, "")}` : bare;
}

const WARSAW_MOMENT = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw",
  day: "numeric",
  month: "long",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * A deadline on the wall clock of the person reading it: Europe/Warsaw, daylight saving included.
 * `2026-10-03T12:00:00+00:00` becomes `3 października 2026, 14:00`.
 *
 * NOT `moment` above, and the difference is the point. `moment` slices the stored string, so it
 * shows UTC, which is right for a row somebody lines up with a log. A deadline is read in order to
 * act on it ("ważne do 14:00"), and two hours off in summer is a delegation that ends while the
 * screen still promises it. The zone is named here instead of being taken from the server process,
 * whose TZ is whatever the container was started with (UTC in the pinned image, measured 26.09).
 *
 * A value that does not parse comes back as it arrived: a broken date on screen is a defect
 * somebody can report, while a broken date hidden is a promise with no end.
 */
export function warsawMoment(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const parts: Partial<Record<string, string>> = {};
  for (const part of WARSAW_MOMENT.formatToParts(date)) parts[part.type] = part.value;
  return `${parts["day"] ?? ""} ${parts["month"] ?? ""} ${parts["year"] ?? ""}, ${parts["hour"] ?? ""}:${parts["minute"] ?? ""}`;
}
