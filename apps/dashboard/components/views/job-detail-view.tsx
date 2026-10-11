import { EntityScreen, type TableRow } from "../entity-screen";
import {
  ageCell,
  instantCell,
  missingCell,
  statusCell,
  terminalisationMarker,
  tonedCell,
  valueCell,
} from "../../lib/format";
import type { AttemptRecord, JobDetailRecord } from "../../lib/schema";

/**
 * One job, every attempt it has had, and a verdict on automatic retry — WITH NO RETRY BUTTON.
 *
 * THE ABSENCE OF THE BUTTON IS THE FEATURE. L2673 (with L4560 and L5335) says that after
 * `SUBMIT_STARTED` the side effect may already have happened on the platform, so there is no
 * automatic retry from there; `CONFIRMED` and `UNKNOWN` are in the same set
 * (`contracts.enums.STATES_FORBIDDING_AUTOMATIC_RETRY`). A dashboard offering "ponów" would let a
 * human do by hand precisely what the state machine forbids the machine to do — the rule broken
 * through the interface, with the state machine still intact and still green.
 *
 * IT GOES FURTHER: THERE IS NO RETRY ACTION FOR ANY STATE, not even the five where an automatic
 * retry is allowed. Scope reason first — E1.F1's scope (L5184-5219) lists no mutating action on
 * jobs, and reaper plus attempt limit are required to work WITHOUT a human (L5213-5214). Design
 * reason second — a button that is present for five states and absent for three teaches the
 * operator that its absence is a UI condition to be worked around, and the workaround is a
 * `curl`. `tests/retry_affordance.test.tsx` parametrises over all eight
 * `ExecutionAttemptState` values and asserts zero affordances for every one of them.
 *
 * THE VERDICT IS NOT COMPUTED HERE. `may_retry_automatically` arrives from the control plane,
 * which derives it in one place — `contracts.execution.may_retry_automatically` reached through
 * `control_plane.domain.models.JobAttemptView`. This file contains no list of states and no `if`
 * about which state permits what; it renders three values (`true`, `false`, `null`) into three
 * sentences. Two copies of that rule are two chances for one of them to say "retry".
 *
 * `null` IS RENDERED AS UNKNOWN, NEVER AS PERMISSION. An attempt whose outcome nobody recorded
 * says nothing about whether the post went out; rendering that silence as "you may retry" is the
 * same move as a gate announcing GO with no measurement (docs/KONWENCJE.md: absence of evidence is
 * `NIE_URUCHOMIONO`).
 *
 * PRESENTATION: the verdict is a pill — `ZABRONIONE` in the danger tone, `DOZWOLONE` neutral (it
 * is a fact about the state machine, not good news), `NIEZNANE` marked missing so an absent verdict
 * cannot be skimmed as one. The job's own columns are a list of facts in a card above the table.
 */

/** The four verdicts, exported so the tests do not spell them a second time. */
export const RETRY_ALLOWED = "DOZWOLONE";
export const RETRY_FORBIDDEN = "ZABRONIONE";
export const RETRY_UNKNOWN = "NIEZNANE";
export const RETRY_NO_ATTEMPTS = "BRAK PRÓB";

export const ATTEMPT_COLUMNS: readonly string[] = [
  "attempt_no",
  "status",
  "lease_generation",
  "lease_owner",
  "dispatch_version",
  "started_at",
  "completed_at",
  "wiek heartbeatu",
  "expires_at",
  "failure_class",
  "execution_attempt_state",
  "automatyczne ponowienie",
];

function retryCell(attempt: AttemptRecord) {
  const column = "automatyczne ponowienie";
  if (attempt.may_retry_automatically === true) {
    return tonedCell(column, RETRY_ALLOWED, "neutral");
  }
  if (attempt.may_retry_automatically === false) {
    return tonedCell(column, RETRY_FORBIDDEN, "danger");
  }
  return missingCell(
    column,
    `${RETRY_UNKNOWN}: control plane nie rozpoznał klasy '${attempt.failure_class ?? "brak"}'`,
  );
}

function attemptRow(attempt: AttemptRecord, now: Date): TableRow {
  return {
    key: attempt.attempt_id,
    cells: [
      valueCell("attempt_no", String(attempt.attempt_no)),
      statusCell("status", attempt.status),
      valueCell("lease_generation", String(attempt.lease_generation)),
      valueCell("lease_owner", attempt.lease_owner),
      valueCell("dispatch_version", String(attempt.dispatch_version)),
      instantCell("started_at", attempt.started_at, "brak started_at"),
      instantCell("completed_at", attempt.completed_at, "próba nie jest zakończona"),
      ageCell("wiek heartbeatu", attempt.heartbeat_at, "brak heartbeat_at", now),
      instantCell("expires_at", attempt.expires_at, "brak expires_at"),
      attempt.failure_class === null
        ? missingCell("failure_class", "brak: próba nie zapisała klasy wyniku")
        : valueCell("failure_class", attempt.failure_class),
      attempt.execution_attempt_state === null
        ? missingCell(
            "execution_attempt_state",
            "failure_class nie jest stanem ExecutionAttempt (albo go brak)",
          )
        : tonedCell("execution_attempt_state", attempt.execution_attempt_state, "neutral"),
      retryCell(attempt),
    ],
  };
}

/**
 * The verdict for the CURRENT state of the job, i.e. its latest attempt.
 *
 * "Latest" is this component's only judgement, and it is a judgement about ORDER, not about
 * retries: the control plane returns attempts `ORDER BY attempt_no` (`jobs/store.py`), so the last
 * element is the newest. The verdict itself is the control plane's boolean, untouched.
 */
function RetryVerdict({ attempts }: { readonly attempts: readonly AttemptRecord[] }) {
  const latest = attempts.length === 0 ? undefined : attempts[attempts.length - 1];

  if (latest === undefined) {
    return (
      <section className="verdict" data-retry-verdict={RETRY_NO_ATTEMPTS}>
        <p className="verdict__headline">
          <span>automatyczne ponowienie:</span>
          <span className="pill" data-tone="warn">
            {RETRY_NO_ATTEMPTS}
          </span>
        </p>
        <p className="verdict__detail">
          Ten job nie miał ani jednej próby, więc nie ma stanu, o którym dałoby się to orzec. To
          nie znaczy „ponowienie dozwolone”.
        </p>
      </section>
    );
  }

  const verdict =
    latest.may_retry_automatically === true
      ? RETRY_ALLOWED
      : latest.may_retry_automatically === false
        ? RETRY_FORBIDDEN
        : RETRY_UNKNOWN;

  const tone = verdict === RETRY_FORBIDDEN ? "danger" : verdict === RETRY_UNKNOWN ? "warn" : "neutral";

  const detail =
    verdict === RETRY_ALLOWED
      ? "Control plane orzekł, że z tego stanu wolno ponowić automatycznie. Ponawia REAPER i " +
        "limit prób, bez człowieka (L5213-5214). Ten panel nie ma i nie będzie mieć przycisku " +
        "ponowienia."
      : verdict === RETRY_FORBIDDEN
        ? "Control plane orzekł, że z tego stanu NIE WOLNO ponowić automatycznie: po " +
          "SUBMIT_STARTED skutek uboczny mógł się już wykonać po stronie platformy (L2673, " +
          "L4560, L5335). Ręczne wymuszenie redispatchu byłoby złamaniem tej reguły przez " +
          "interfejs, dlatego takiej akcji tu nie ma."
        : "Control plane nie orzekł nic: klasa wyniku ostatniej próby nie jest stanem " +
          "ExecutionAttempt. Brak orzeczenia to NIE zgoda na ponowienie: nikt nie zmierzył, " +
          "czy skutek uboczny się wykonał.";

  return (
    <section className="verdict" data-retry-verdict={verdict}>
      <p className="verdict__headline">
        <span>automatyczne ponowienie dla bieżącego stanu (próba {latest.attempt_no}):</span>
        <span className="pill" data-tone={tone}>
          {verdict}
        </span>
      </p>
      <p className="verdict__detail">{detail}</p>
    </section>
  );
}

/** The job's own columns, as a list of facts above the attempts table. */
function JobHeader({ detail }: { readonly detail: JobDetailRecord }) {
  const { job } = detail;
  const generations = detail.attempts.map((attempt) => attempt.lease_generation);
  const entries: readonly (readonly [string, string])[] = [
    ["job_id", job.job_id],
    ["job_type", job.job_type],
    ["routing_key", job.routing_key],
    ["status", job.status],
    ["terminalny", terminalisationMarker(job.status).value],
    ["próby", `${detail.attempts_used} / ${detail.attempt_limit}`],
    ["lease żywy", job.holds_live_lease ? "TAK" : "nie"],
    [
      "generacje lease'ów",
      generations.length === 0 ? "brak prób, żadnej generacji" : generations.join(", "),
    ],
    ["dispatch_version", String(job.dispatch_version)],
    ["lock_version", String(job.lock_version)],
    ["workspace_id", job.workspace_id ?? "brak: job organizacyjny"],
    ["payload_ref", job.payload_ref ?? "brak: payload nie jest referencją"],
  ];

  return (
    <dl className="facts">
      {entries.map(([term, value]) => (
        <div className="facts__pair" key={term}>
          <dt className="facts__term">{term}</dt>
          <dd className="facts__value">
            {term === "status" ? (
              <span className="pill" data-tone={statusCell("status", value).tone ?? "neutral"}>
                {value}
              </span>
            ) : (
              value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

interface JobDetailViewProps {
  /** The identifier from the URL, echoed so a 404 screen can name what was asked for. */
  readonly jobId: string;
  /**
   * `undefined` — the read failed. `null` — the control plane answered 404: there is no such job
   * in this organization. A record — the job. THREE facts, three screens.
   */
  readonly detail: JobDetailRecord | null | undefined;
  readonly reason?: string;
  readonly now: Date;
}

export function JobDetailView({ jobId, detail, reason, now }: JobDetailViewProps) {
  if (detail === null) {
    // A 404 is a MEASURED fact and gets its own screen. Rendering it as "brak danych" would claim
    // an empty attempt list for a job that does not exist, and rendering it as "brak odpowiedzi"
    // would claim a failed read that in fact succeeded.
    return (
      <section className="screen" data-route="jobs">
        <header className="screen__head">
          <h1 className="screen__title">Job {jobId}</h1>
        </header>
        <section className="state state--absent" data-state="nie ma takiego joba">
          <p className="state__headline">nie ma takiego joba</p>
          <p className="state__detail">
            Control plane odpowiedział 404 dla identyfikatora {jobId} w organizacji tej sesji.
            Odczyt się UDAŁ. To nie jest brak odpowiedzi. „Nie twoje” i „nie istnieje” wyglądają
            tu tak samo świadomie (api/jobs.py), więc ten ekran nie mówi, który z dwóch przypadków
            zachodzi.
          </p>
        </section>
      </section>
    );
  }

  return (
    <EntityScreen
      columns={ATTEMPT_COLUMNS}
      heading={`Job ${jobId}`}
      lede={
        "Historia prób tego joba i orzeczenie control plane o automatycznym ponowieniu. Ten " +
        "ekran nic nie przelicza i nie ma akcji ponowienia dla żadnego stanu."
      }
      reason={reason}
      routeId="jobs"
      rows={detail === undefined ? undefined : detail.attempts.map((attempt) => attemptRow(attempt, now))}
      what={`Próby joba ${jobId}`}
    >
      {detail === undefined ? null : (
        <>
          <JobHeader detail={detail} />
          <RetryVerdict attempts={detail.attempts} />
        </>
      )}
    </EntityScreen>
  );
}
