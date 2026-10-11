/**
 * Turning control-plane JSON into cells an operator can read — without ever inventing a value.
 *
 * ONE RULE GOVERNS THIS WHOLE FILE, and it is L5241 point 5: "Brak danych nigdy nie jest
 * zamieniany na zero; jest UNKNOWN z powodem." A missing timestamp is not "now", a missing
 * heartbeat is not "0 s ago", a missing owner is not an empty string. Every one of those would
 * read on the screen as a measurement, and the screen would be lying with a straight face — the
 * visual form of a gate announcing GO with no measurement (D-007).
 *
 * So each function here returns a `Cell`, and a cell knows whether it holds a value or a stated
 * absence. `components/entity-screen.tsx` renders the two differently and
 * `tests/format.test.ts` watches the difference.
 */

/**
 * One table cell.
 *
 * `missing: true` means THERE IS NO VALUE and `value` is the reason, not the datum. The flag is
 * separate from the text on purpose: a test (and a stylesheet) can then tell an absence from a
 * value that happens to contain the word "brak".
 */
export type Tone = "accent" | "warn" | "danger" | "neutral";

export interface Cell {
  /** Column this cell belongs to. Must be one of the table's declared columns. */
  readonly column: string;
  /** What the operator sees. For a missing value: the stated reason. */
  readonly value: string;
  /** `true` when there is no value at all. */
  readonly missing?: boolean;
  /**
   * Where the cell navigates, when it navigates. NAVIGATION ONLY — a `GET` to another screen of
   * this panel. There is no cell in this dashboard whose link performs an action: E1.F1 has no
   * mutating route on jobs, and a link that changed state would also be a link a browser could
   * follow while prefetching.
   */
  readonly href?: string;
  /**
   * PRESENTATION ONLY: which of four tones the cell is drawn in (`components/entity-screen.tsx`
   * renders a toned cell as a pill — docs/DASHBOARD-DESIGN.md §2 pkt 4). It never changes `value`
   * and never stands in for `missing`: a stated absence has no tone, because an absence must not
   * look like a status. There is no "good" tone, on purpose (D-011): `accent` means "in motion /
   * act here", `warn` means "waiting / absent", `danger` means "failed / forbidden", `neutral`
   * means "terminal / informational".
   */
  readonly tone?: Tone;
}

/** A cell holding a real value. */
export function valueCell(column: string, value: string): Cell {
  return { column, value };
}

/** A cell holding a real value, drawn as a pill in the given tone. The text is unchanged. */
export function tonedCell(column: string, value: string, tone: Tone): Cell {
  return { column, value, tone };
}

/** A cell holding a real value that also navigates to another READ-ONLY screen. */
export function linkCell(column: string, value: string, href: string): Cell {
  if (!href.startsWith("/")) {
    throw new Error(
      `komórka '${column}' linkuje do '${href}'. Dozwolone są wyłącznie ścieżki wewnętrzne, ` +
        "bo panel nie prowadzi nigdzie poza pętlę zwrotną",
    );
  }
  return { column, value, href };
}

/**
 * A cell holding a STATED ABSENCE. `reason` is mandatory — an absence without a reason is the
 * silence this project has already paid for.
 */
export function missingCell(column: string, reason: string): Cell {
  if (!reason) {
    throw new Error(
      `brak wartości w kolumnie '${column}' bez podanego powodu. Pusty powód to milczenie, ` +
        "a milczenie o braku jest w tym repozytorium traktowane jak fałszywe zaliczenie",
    );
  }
  return { column, value: reason, missing: true };
}

/**
 * An ISO-8601 instant as the operator reads it, in UTC.
 *
 * UTC and not the local zone: the control plane, PostgreSQL and the artefacts all speak UTC, and
 * a screen quietly shifting timestamps makes "lease wygasł 14:03" impossible to line up with a
 * log line saying 12:03. The suffix `Z` stays visible for the same reason.
 */
export function formatInstant(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    // A value that came back but cannot be parsed is NOT a missing value — it is a broken one,
    // and hiding it as "brak" would erase evidence of a real defect.
    return `nieparsowalna data: ${iso}`;
  }
  return parsed.toISOString().replace("T", " ").replace(".000Z", "Z");
}

/** `formatInstant` for a nullable field, as a cell. */
export function instantCell(column: string, iso: string | null, absence: string): Cell {
  if (iso === null) {
    return missingCell(column, absence);
  }
  return valueCell(column, formatInstant(iso));
}

/**
 * How long ago `iso` was, in whole seconds, phrased for a person.
 *
 * NEGATIVE AGES ARE REPORTED AS SUCH, not clamped to zero. A heartbeat timestamped in the future
 * means the two clocks disagree — PostgreSQL's and this process's — and that is exactly the kind
 * of thing an operator must be able to see rather than have smoothed away. D-017 sizes the
 * heartbeat interval at 15 s against a 60 s lease TTL, so a clock skew of seconds is not academic.
 */
export function describeAge(iso: string, now: Date): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) {
    return `nieparsowalna data: ${iso}`;
  }
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 0) {
    return `${Math.abs(seconds)} s W PRZYSZŁOŚCI (zegary się rozjeżdżają)`;
  }
  if (seconds < 90) {
    return `${seconds} s temu`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 90) {
    return `${minutes} min temu`;
  }
  return `${Math.floor(minutes / 60)} h temu`;
}

/** The age of a nullable timestamp, as a cell. */
export function ageCell(column: string, iso: string | null, absence: string, now: Date): Cell {
  if (iso === null) {
    return missingCell(column, absence);
  }
  return valueCell(column, describeAge(iso, now));
}

/**
 * Job statuses after which the queue never dispatches the job again.
 *
 * WRITTEN OUT, and cross-checked against `db/migrations/0003_e1f1_kernel.sql` by
 * `tests/job_status_parity.test.ts`: the migration's CHECK lists nine statuses, this list names
 * the three terminal ones, and the test requires the two lists to partition each other exactly.
 * A status added to the schema and forgotten here would otherwise render as "w toku" forever —
 * the queue's own DoD ("ostatnia próba terminalizuje job zamiast tworzyć nieskończony
 * redispatch", L5214) made invisible on the one screen that is supposed to show it.
 */
export const TERMINAL_JOB_STATUSES: readonly string[] = ["SUCCEEDED", "FAILED", "CANCELLED"];

/** Statuses from which the job can still move. The complement of the list above. */
export const NON_TERMINAL_JOB_STATUSES: readonly string[] = [
  "READY",
  "RUNNING",
  "WAITING_AUTH",
  "WAITING_QUOTA",
  "WAITING_CAPABILITY",
  "WAITING_RETRY",
];

/**
 * The terminalisation marker of one job.
 *
 * An UNRECOGNISED status is neither terminal nor non-terminal — it is reported as unrecognised.
 * Defaulting it to "w toku" would be the same wrong direction as rendering an unknown retry flag
 * as `true`: it would tell the operator the queue is still working on something it may have
 * abandoned.
 */
export function terminalisationMarker(status: string): Cell {
  const column = "terminalny";
  if (TERMINAL_JOB_STATUSES.includes(status)) {
    return valueCell(column, "TAK, kolejka go już nie wystawi");
  }
  if (NON_TERMINAL_JOB_STATUSES.includes(status)) {
    return valueCell(column, "nie");
  }
  return missingCell(column, `status '${status}' nie jest znany temu ekranowi`);
}


/**
 * The tone a job or attempt status is drawn in. NOT a fifth source of truth about statuses: the
 * two lists above decide what is terminal, this only decides colour, and a status this function
 * does not recognise is drawn plain — never as "fine".
 *
 *   RUNNING / READY        → accent   (in motion)
 *   WAITING_*              → warn     (parked, needs something)
 *   FAILED                 → danger
 *   SUCCEEDED / CANCELLED  → neutral  (terminal is a fact, not praise — no green anywhere, D-011)
 */
export function statusTone(status: string): Tone | undefined {
  if (status === "RUNNING" || status === "READY") return "accent";
  if (status.startsWith("WAITING_")) return "warn";
  if (status === "FAILED") return "danger";
  if (TERMINAL_JOB_STATUSES.includes(status)) return "neutral";
  return undefined;
}

/** A status as a cell: the raw status text, toned when the status is recognised. */
export function statusCell(column: string, status: string): Cell {
  const tone = statusTone(status);
  return tone === undefined ? valueCell(column, status) : tonedCell(column, status, tone);
}

/**
 * Polish plurals, because "1 wątków" is the kind of detail that makes a screen read like a
 * database dump. THREE forms, in the order the language needs them:
 *
 *   `[jeden, dwa, pięć]` → 1 wątek · 2 wątki · 5 wątków · 22 wątki · 25 wątków
 *
 * The rule (11-14 take the third form even though they end in 2-4) is the one every hand-rolled
 * version gets wrong, which is why this lives in one place and `tests/format.test.ts` walks it.
 */
export function plural(count: number, forms: readonly [string, string, string]): string {
  const absolute = Math.abs(Math.trunc(count));
  const last = absolute % 10;
  const lastTwo = absolute % 100;
  if (absolute === 1) return forms[0];
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return forms[1];
  return forms[2];
}

/** `count` with the right form after it: "1 wątek", "5 wątków". */
export function counted(count: number, forms: readonly [string, string, string]): string {
  return `${count} ${plural(count, forms)}`;
}

/**
 * The DAY something happened, as a person says it: "dziś", "wczoraj", "3 dni temu".
 *
 * Deliberately coarser than `describeAge`: a picture of a community is a daily artefact, and
 * "narysowany 14 h temu" makes the reader do arithmetic to answer "is this today's?". Compared in
 * UTC, like every other instant on this dashboard, so the answer lines up with the artefacts.
 */
export function describeDay(iso: string, now: Date): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) {
    return `nieparsowalna data: ${iso}`;
  }
  const day = (date: Date) => Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const days = Math.round((day(now) - day(then)) / 86_400_000);
  if (days < 0) return `${counted(Math.abs(days), ["dzień", "dni", "dni"])} W PRZYSZŁOŚCI (zegary się rozjeżdżają)`;
  if (days === 0) return "dziś";
  if (days === 1) return "wczoraj";
  return `${counted(days, ["dzień", "dni", "dni"])} temu`;
}

/**
 * A confidence between 0 and 1 as a word. The NUMBER never disappears — the caller keeps it in a
 * `data-` attribute or in the technical details — but a reader deciding whether to trust one
 * sentence does not need two decimal places to do it.
 *
 * A value outside 0..1 is reported as unrecognised rather than clamped: the model is supposed to
 * answer inside that range, and quietly rounding a 7 to "wysoka" hides a broken output.
 */
export function confidenceWord(confidence: number): string {
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) return "nieznana";
  if (confidence < 0.4) return "niska";
  if (confidence < 0.7) return "średnia";
  return "wysoka";
}
