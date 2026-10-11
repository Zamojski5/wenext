import { EntityScreen, type TableRow } from "../entity-screen";
import {
  ageCell,
  instantCell,
  linkCell,
  missingCell,
  statusCell,
  statusTone,
  terminalisationMarker,
  tonedCell,
  valueCell,
  type Cell,
} from "../../lib/format";
import { labelOf } from "../../lib/routes";
import type { JobSummary } from "../../lib/schema";

/**
 * The queue, as the operator reads it (L5192-5193).
 *
 * Ten columns, and three of them are not in `GET /api/jobs` — MEASURED, not assumed:
 * `api/jobs.py::_job_payload` sends `attempt_count` and `holds_live_lease` but no `lease_owner`,
 * no lease `expires_at` and no `heartbeat_at`. Those live on `job_attempts`, which the DETAIL
 * endpoint returns. So `app/jobs/page.tsx` makes one detail read per listed row and hands the
 * result here as `LeaseRead`.
 *
 * WHY THAT IS A TYPE AND NOT THREE NULLABLE STRINGS. A per-row read can fail on its own, and when
 * it does, the three lease cells must say "nie odczytano" — not "brak". "Brak lease'u" and "nie
 * wiem, czy jest lease" are different facts about a queue whose reaper is supposed to be taking
 * leases away from dead workers (D-017: TTL 60 s, heartbeat 15 s), and the second one dressed as
 * the first is how an operator concludes a worker died when nothing of the sort was measured.
 *
 * `lease_owner` here is the owner of the LATEST ATTEMPT, which is what the column header says. It
 * is not the same claim as `holds_live_lease`, which PostgreSQL evaluates against its own clock
 * ("a lease that is ACTIVE **and** not past expires_at" — `domain/models.py`), and both are shown
 * because a job whose latest attempt names an owner while `lease żywy` says "nie" is exactly the
 * abandoned-lease case the reaper exists for.
 *
 * PRESENTATION: `status` and a live lease are drawn as pills (`statusCell`, `tonedCell`) so the
 * state of the queue reads by colour before it reads by word. The words are unchanged.
 */

/** What one row's detail read produced. `unread` carries the reason, never a blank. */
export type LeaseRead =
  | {
      readonly kind: "read";
      /** The latest attempt, or `null` when the job has had none. */
      readonly latestAttempt: {
        readonly leaseOwner: string;
        readonly expiresAt: string | null;
        readonly heartbeatAt: string | null;
        readonly leaseGeneration: number;
      } | null;
    }
  | { readonly kind: "unread"; readonly reason: string };

export interface JobsRow {
  readonly job: JobSummary;
  readonly lease: LeaseRead;
}

export const JOBS_COLUMNS: readonly string[] = [
  "job_id",
  "job_type",
  "status",
  "próby",
  "terminalny",
  "lease żywy",
  "lease_owner (ostatnia próba)",
  "lease_expires_at",
  "wiek heartbeatu",
  "not_before",
];

const NO_ATTEMPTS = "brak prób: job nigdy nie był pobrany";

function leaseCells(lease: LeaseRead, now: Date): readonly Cell[] {
  if (lease.kind === "unread") {
    // One reason, repeated in all three cells on purpose: a row where two cells say "nie
    // odczytano" and one says "brak" is a row nobody can read consistently.
    return [
      missingCell("lease_owner (ostatnia próba)", `nie odczytano: ${lease.reason}`),
      missingCell("lease_expires_at", `nie odczytano: ${lease.reason}`),
      missingCell("wiek heartbeatu", `nie odczytano: ${lease.reason}`),
    ];
  }
  const attempt = lease.latestAttempt;
  if (attempt === null) {
    return [
      missingCell("lease_owner (ostatnia próba)", NO_ATTEMPTS),
      missingCell("lease_expires_at", NO_ATTEMPTS),
      missingCell("wiek heartbeatu", NO_ATTEMPTS),
    ];
  }
  return [
    valueCell(
      "lease_owner (ostatnia próba)",
      `${attempt.leaseOwner} (gen. ${attempt.leaseGeneration})`,
    ),
    instantCell("lease_expires_at", attempt.expiresAt, "próba nie zapisała expires_at"),
    ageCell("wiek heartbeatu", attempt.heartbeatAt, "próba nie zapisała heartbeat_at", now),
  ];
}

function rowOf(entry: JobsRow, now: Date): TableRow {
  const { job } = entry;
  return {
    key: job.job_id,
    cells: [
      // The only link in this table, and it goes to a READ-ONLY screen. It is here because the
      // attempt history is the evidence for "restart komponentu nie gubi taska" (L5212) and an
      // identifier the operator has to copy by hand is evidence nobody looks at.
      linkCell("job_id", job.job_id, `/jobs/${encodeURIComponent(job.job_id)}`),
      valueCell("job_type", job.job_type),
      statusCell("status", job.status),
      // `attempt_count` against `attempt_limit`, side by side, because the DoD sentence is about
      // the pair: "lease expiry respektuje attempt limit; ostatnia próba terminalizuje job"
      // (L5214). One number without the other says nothing about how close the job is to the end.
      valueCell("próby", `${job.attempt_count} / ${job.attempt_limit}`),
      terminalisationMarker(job.status),
      job.holds_live_lease ? tonedCell("lease żywy", "TAK", "accent") : valueCell("lease żywy", "nie"),
      ...leaseCells(entry.lease, now),
      instantCell("not_before", job.not_before, "brak not_before"),
    ],
  };
}

/**
 * The rows of THIS PAGE, counted by the same tone the status pill uses (`statusTone`), so the
 * tiles and the table cannot disagree about what "waiting" means. An unrecognised status is
 * counted separately and shown only when present — never folded into "finished".
 *
 * THE TILES COUNT THE ROWS ON THIS PAGE, NOT THE QUEUE. The page is capped (`JOBS_PAGE_LIMIT`)
 * and says so when it fills up; a tile claiming "3 failed" over a truncated page would be a claim
 * about rows nobody read. So they are drawn only when there ARE rows, and the "MOŻE BYĆ WIĘCEJ"
 * note stays exactly where it was.
 */
function Summary({ rows }: { readonly rows: readonly JobsRow[] }) {
  const counts = { accent: 0, warn: 0, danger: 0, neutral: 0, other: 0 };
  for (const entry of rows) {
    const tone = statusTone(entry.job.status);
    if (tone === undefined) counts.other += 1;
    else counts[tone] += 1;
  }
  return (
    <ul className="stats stats--totals" data-summary="tak">
      <li className="stat" data-tone="accent">
        <b className="stat__value">{counts.accent}</b> <span className="stat__label">w toku</span>
      </li>
      <li className="stat" data-tone="warn">
        <b className="stat__value">{counts.warn}</b> <span className="stat__label">czeka</span>
      </li>
      <li className="stat" data-tone="danger">
        <b className="stat__value">{counts.danger}</b> <span className="stat__label">nieudane</span>
      </li>
      <li className="stat">
        <b className="stat__value">{counts.neutral}</b> <span className="stat__label">zakończone</span>
      </li>
      {counts.other > 0 ? (
        <li className="stat" data-tone="warn">
          <b className="stat__value">{counts.other}</b> <span className="stat__label">nierozpoznany status</span>
        </li>
      ) : null}
    </ul>
  );
}

interface JobsViewProps {
  /** `undefined` — the list read failed. `[]` — the read succeeded and the queue is empty. */
  readonly rows: readonly JobsRow[] | undefined;
  /** Why the list read failed. */
  readonly reason?: string;
  /** `true` when the page filled up, i.e. there may be more jobs than are shown. */
  readonly truncated?: boolean;
  /** Injected so the heartbeat ages in the tests are not a function of when the suite ran. */
  readonly now: Date;
}

export function JobsView({ rows, reason, truncated, now }: JobsViewProps) {
  return (
    <EntityScreen
      columns={JOBS_COLUMNS}
      heading={labelOf("jobs")}
      lede={
        "Kolejka zadań agenta z PostgreSQL, najnowsze pierwsze. Tylko do odczytu. Ponawia reaper " +
        "i limit prób, bez człowieka."
      }
      pageNote={
        truncated === true
          ? "Strona zapełniła się do limitu. Jobów MOŻE BYĆ WIĘCEJ niż widać. To nie jest cała kolejka."
          : undefined
      }
      reason={reason}
      routeId="jobs"
      rows={rows === undefined ? undefined : rows.map((entry) => rowOf(entry, now))}
      tableCollapsed
      what={labelOf("jobs")}
    >
      {rows !== undefined && rows.length > 0 ? <Summary rows={rows} /> : null}
    </EntityScreen>
  );
}
