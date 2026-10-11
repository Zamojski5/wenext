"""The reaper of expired job leases. ONE transaction per job (L2669), attempt limit (L5214).

The paragraph this module implements, quoted because the wording is the specification:

    "Reaper w jednej transakcji blokuje wygasły job, zamyka poprzednią próbę jako `LEASE_EXPIRED`
     i porównuje `attempt_no` z `attempt_limit`. Poniżej limitu zwiększa `lease_generation`
     i `dispatch_version`, tworzy replacement `job_attempt(attempt_no+1, not_before=backoff)`
     oraz `outbox_event`. Po wyczerpaniu limitu terminalizuje job jako `ATTEMPTS_EXHAUSTED`
     i tworzy alert oraz DLQ/audit outbox — bez kolejnej próby." (L2669)

and the two DoD lines it exists for: "restart komponentu nie gubi taska" (L5212) and "lease
expiry respektuje attempt limit; ostatnia próba terminalizuje job zamiast tworzyć nieskończony
redispatch" (L5214). The `outbox_event` half of L2669 is E3 (L5205) and is not written here; in
F1 the dispatcher lives in the same process as the API, so the canonical row IS the dispatch.

WHY ONE TRANSACTION HERE AND TWO IN `takeover()` — read both before changing either.
`services/profile_lease/profile_lease/store.py` (around lines 138-201) splits the profile-lease
takeover into TWO transactions ON PURPOSE, and its comment says why: the refusal must be raised
OUTSIDE the writing transaction, because a rollback would undo the quarantine the same code just
recorded, and the profile would stay `LEASED` with unresolved reconciliation (L2379). There, the
decision to refuse is itself a state change worth keeping.

Here it is the opposite, and copying that shape would be a bug. Nothing this module writes is
worth keeping on its own: an attempt closed as `LEASE_EXPIRED` with no replacement row is a job
that will never run again and is not terminal either — invisible to the operator's screen and to
the dispatcher, which is precisely the "lost task" L5212 forbids. So every write below shares one
transaction and one rollback, and a failure anywhere leaves the expired lease exactly as it was,
for the next pass to pick up. `tests/test_reaper_single_transaction.py` measures that by making
the LAST write of the transaction fail.

THREE MORE THINGS THAT ARE NOT OBVIOUS FROM THE CODE:

1.  **The replacement attempt is created here, unstarted, and the job goes to `WAITING_RETRY` —
    not to `READY`.** `WAITING_*` jobs are not dispatchable (`jobs/store.py`), and that is the
    point: a job left `READY` would be picked up by `JobStore.claim_batch`, whose statement mints
    its own `job_attempts` row as `max(attempt_no) + 1`. The replacement row created here would
    then be skipped for ever — one phantom attempt per retry, half the attempt budget spent on
    rows nobody ran, and `UNIQUE(job_id, attempt_no)` guarding nothing. The scheduled row is
    activated instead by `jobs/dispatcher.py`, which is the only other writer of these tables.
    L2147 is satisfied in the letter that matters: the job holds NO lease while it waits.

2.  **`ATTEMPTS_EXHAUSTED` is not a job status.** `jobs_status_is_known` in migration 0003 allows
    nine values and that is not one of them, and `jobs` has no failure column. So the terminal
    job status is `FAILED` and the REASON lives in exactly one place — `reason_code` in the audit
    payload. Anybody looking for a job in state `ATTEMPTS_EXHAUSTED` will not find one; that is
    the schema's answer, not a shortcut taken here.

3.  **One reaped job leaves exactly ONE audit row**, in both branches. Two rows for one event
    would make "how many jobs did the reaper terminalize" unanswerable by counting, which is the
    only way anybody will ever ask it.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import asyncpg
from community_os_contracts import ExecutionAttemptState, JobWaitingState
from community_os_contracts.execution import may_retry_automatically

from control_plane.audit import AuditWriter
from control_plane.domain.models import JobOutcome
from control_plane.jobs.backoff import REAPER_INTERVAL_SECONDS, backoff_seconds
from control_plane.jobs.store import RUNNING_STATUS as _STORE_RUNNING_STATUS

_LOG = logging.getLogger(__name__)

#: What the reaper writes into `failure_class` when the previous writer left it empty. NOT an
#: `ExecutionAttemptState` — deliberately, and migration 0003 says why: "LEASE_EXPIRED jest osobno
#: od FAILED świadomie: reaper zamykający próbę z wygasłym lease'em to nie to samo zdarzenie, co
#: worker raportujący porażkę". A value that happened to parse as an execution state would make
#: the retry decision below read the reaper's own bookkeeping as the worker's report.
LEASE_EXPIRED_FAILURE_CLASS = "LEASE_EXPIRED"

#: The job status the reaper expects to find and CASes against on every write. A job whose lease
#: is `ACTIVE` is `RUNNING`; anything else means somebody moved it under the reaper's lock, and
#: then zero updated rows has to be a refusal rather than a shrug.
#:
#: IMPORTED from the store rather than spelled again: it is the same `expected_status` the fenced
#: writes of `jobs/store.py` bind (L2666), and two copies of a status string that must match are
#: two chances for one of them to stop matching. If that name ever moves, the import fails loudly
#: here — which is the outcome to want.
RUNNING_STATUS = _STORE_RUNNING_STATUS

#: Where a job goes while it waits out its backoff — the one of the four `WAITING_*` states of
#: `contracts.JobWaitingState` whose name says what the wait is for. Taken FROM the enum: the
#: string also has to match `jobs_status_is_known` in migration 0003 and the filter in
#: `jobs/dispatcher.py`, and three hand-typed copies of one status is two too many.
WAITING_RETRY_STATUS = JobWaitingState.WAITING_RETRY.value

#: The terminal status of a job that will get no further attempt. See point 2 of the docstring.
#: `JobOutcome` is the same vocabulary `JobStore.close_attempt` writes into this column, so the
#: reaper and the worker cannot end a job under two different spellings of failure.
TERMINAL_STATUS = JobOutcome.FAILED.value

#: `audit_events.actor_ref` for everything this module writes. A machine actor, spelled the same
#: way every time, because the operator's audit screen filters by it.
REAPER_ACTOR_REF = "control-plane:reaper"


class ReapOutcome(StrEnum):
    """What happened to one expired job. Four values, and none of them is "nothing"."""

    #: Below the limit and the recorded state allows another attempt: a replacement was created.
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    #: `attempt_no` reached `attempt_limit` (L5214). No further attempt, ever.
    TERMINALIZED_ATTEMPTS_EXHAUSTED = "TERMINALIZED_ATTEMPTS_EXHAUSTED"
    #: The attempt's recorded execution state forbids an automatic retry (L2673) — independently
    #: of the limit, and even on the first attempt.
    TERMINALIZED_RETRY_FORBIDDEN = "TERMINALIZED_RETRY_FORBIDDEN"
    #: Another reaper got there first. A lost race, not a failure — and not a success either.
    LOST_RACE = "LOST_RACE"


@dataclass(frozen=True)
class ReapedJob:
    """One job the reaper acted on, and what it decided.

    Returned rather than logged only, so that the loop, the tests and any future operator screen
    read the same facts. `replacement_attempt_no` is `None` for every terminal outcome — a
    terminalized job that still reported a next attempt would be the off-by-one this whole
    package exists to prevent, and `None` makes it impossible to report by accident.
    """

    job_id: UUID
    attempt_id: UUID
    attempt_no: int
    attempt_limit: int
    outcome: ReapOutcome
    replacement_attempt_no: int | None = None
    #: The delay the replacement attempt waits, in seconds. `None` for terminal outcomes.
    delay_seconds: float | None = None
    #: The execution state the dying attempt had recorded, when it recorded one that the machine
    #: of L2601-2605 knows. `None` is the ordinary case in F1 — nothing here runs a side effect.
    recorded_state: ExecutionAttemptState | None = None


@dataclass(frozen=True)
class ReapReport:
    """The result of one reaper pass. Counting is the whole interface.

    `scanned_to_the_end` distinguishes "there was nothing left to reap" from "the pass stopped
    early, so more expired leases MAY be waiting" — the batch limit was hit, or a race was lost.
    A pass that always reports the same number is the shape in which a queue silently falls
    behind.
    """

    reaped: tuple[ReapedJob, ...]
    scanned_to_the_end: bool

    def count(self, outcome: ReapOutcome) -> int:
        return sum(1 for job in self.reaped if job.outcome is outcome)

    def explain(self) -> str:
        """One sentence for the operator (D-010: komunikaty czyta założyciel)."""
        if not self.reaped:
            return "reaper: brak wygasłych lease'ów"
        parts = [
            f"{outcome.value}={self.count(outcome)}"
            for outcome in ReapOutcome
            if self.count(outcome)
        ]
        tail = "" if self.scanned_to_the_end else ", limit partii osiągnięty — zostały wygasłe"
        return f"reaper: {len(self.reaped)} jobów ({', '.join(parts)}){tail}"


@dataclass(frozen=True)
class _LockedJob:
    """The row the locking SELECT returned, named instead of indexed.

    Exists so that the code below reads the columns once, in one place. Column access spread over
    forty lines is how a rename of `attempt_no` becomes a `KeyError` in production instead of a
    failure here.
    """

    job_id: UUID
    organization_id: UUID
    workspace_id: UUID | None
    attempt_limit: int
    lease_id: UUID
    lease_generation: int
    attempt_id: UUID
    attempt_no: int

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> _LockedJob:
        return cls(
            job_id=row["job_id"],
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            attempt_limit=row["attempt_limit"],
            lease_id=row["lease_id"],
            lease_generation=row["lease_generation"],
            attempt_id=row["attempt_id"],
            attempt_no=row["attempt_no"],
        )


# ---------------------------------------------------------------------------
# The statements
# ---------------------------------------------------------------------------
# Several statements, ONE transaction. That is what L2669 asks for ("w jednej transakcji"), and
# the reason it is not one statement the way `JobStore.claim_batch` is: the old lease has to stop
# being `ACTIVE` before the replacement's lease can ever be created, and inside a single statement
# the order in which data-modifying CTEs run is not defined. `job_leases_one_active_per_job` is a
# partial unique INDEX, so it cannot be deferred to commit time either. Two rows briefly claiming
# to be the active lease of one job would abort the statement — sometimes, depending on the plan,
# which is the worst kind of correct.
#
# The lock comes first and is held by the transaction, so every read below sees a job no other
# reaper can touch. `SKIP LOCKED` rather than plain `FOR UPDATE`: a reaper that blocks on a job
# another reaper is already fixing spends the whole pass waiting instead of draining the rest.

_EXPIRED_JOB_SQL = """
SELECT
    j.id              AS job_id,
    j.organization_id AS organization_id,
    j.workspace_id    AS workspace_id,
    j.attempt_limit   AS attempt_limit,
    l.id              AS lease_id,
    l.lease_generation,
    a.attempt_id,
    a.attempt_no
FROM jobs j
JOIN job_leases l ON l.job_id = j.id
JOIN job_attempts a ON a.attempt_id = l.attempt_id
WHERE j.organization_id = $1
  AND l.status = 'ACTIVE'
  AND l.expires_at <= now()
ORDER BY l.expires_at
LIMIT 1
FOR UPDATE OF j, l, a SKIP LOCKED
"""

#: Closing the dead lease. `coalesce(failure_class, $2)` PRESERVES whatever the worker managed to
#: record before it died — overwriting it would destroy the only evidence of how far the attempt
#: got, which is the evidence the retry decision below is made on.
_CLOSE_LEASE_SQL = """
UPDATE job_leases
SET status = 'EXPIRED',
    completed_at = now(),
    failure_class = coalesce(failure_class, $2)
WHERE id = $1 AND status = 'ACTIVE'
RETURNING id
"""

#: Closing the dead attempt. `LEASE_EXPIRED` is a status of its own in 0003, separate from
#: `FAILED`, and the attempt limit of L5214 counts both.
_CLOSE_ATTEMPT_SQL = """
UPDATE job_attempts
SET status = 'LEASE_EXPIRED',
    completed_at = now(),
    failure_class = coalesce(failure_class, $2)
WHERE attempt_id = $1 AND status = 'RUNNING'
RETURNING attempt_no, failure_class
"""

#: Scheduling the retry. `dispatch_version` grows because this IS a new issue of the job (L2669),
#: and `not_before` is computed by PostgreSQL — `now()` inside a transaction is the transaction's
#: start, so the moment returned here is the same moment the replacement attempt gets, by
#: construction rather than by two calls agreeing.
_SCHEDULE_RETRY_SQL = """
UPDATE jobs
SET status = $4,
    not_before = now() + make_interval(secs => $2::double precision),
    dispatch_version = dispatch_version + 1,
    lock_version = lock_version + 1
WHERE id = $1 AND status = $3
RETURNING dispatch_version, not_before
"""

#: The replacement attempt (L2669). `not_before` is the timestamp the statement above returned:
#: it comes from the database, crosses this process untouched, and lands on the row that has to
#: agree with the job. A second `now() + make_interval(…)` here would be a second chance for the
#: two rows to disagree about when the retry may start.
#:
#: `lease_generation` is the dead lease's generation plus one (L2669), and `lease_owner` is minted
#: by `gen_random_uuid()` — L2666 requires a RANDOM owner per takeover. Neither is a lease yet:
#: a `WAITING_*` job holds no runtime lease (L2147), so the row carrying these two values waits
#: until `jobs/dispatcher.py` activates it.
#:
#: `status` is `RUNNING` because `job_attempts` has no fifth status. 0003 allows exactly
#: `RUNNING`, `SUCCEEDED`, `FAILED`, `LEASE_EXPIRED`, and its
#: `running_attempt_is_not_completed` CHECK ties `RUNNING` to `completed_at IS NULL`. A scheduled
#: attempt is therefore necessarily `RUNNING` with a future `not_before` — which is also why that
#: table HAS a `not_before` column (L3838). `started_at` takes its column default and is corrected
#: by the activation; until then it is the moment the retry was scheduled, not the moment it ran.
_REPLACEMENT_ATTEMPT_SQL = """
INSERT INTO job_attempts
    (organization_id, job_id, attempt_no, dispatch_version, lease_generation, lease_owner,
     status, not_before)
VALUES ($1, $2, $3, $4, $5, gen_random_uuid(), 'RUNNING', $6)
RETURNING attempt_id, attempt_no, not_before
"""

_TERMINALIZE_SQL = """
UPDATE jobs
SET status = $3,
    lock_version = lock_version + 1
WHERE id = $1 AND status = $2
RETURNING id
"""


def _recorded_execution_state(failure_class: str | None) -> ExecutionAttemptState | None:
    """The dying attempt's `failure_class` read as an execution state, or `None`.

    `None` means the attempt recorded nothing the machine of L2601-2605 knows — which in phase
    E1.F1 is the ordinary case, because F1 has no side effect to record. The reaper treats that as
    "a retry is allowed", and that IS reading absence as permission, which this repository
    normally forbids. The reason it is right here, and the boundary of it:

        A worker killed mid-flight leaves nothing behind. If silence forbade a retry, a job whose
        process was `SIGKILL`ed would never run again and DoD L5212 ("restart komponentu nie gubi
        taska") would be unsatisfiable. What makes it SAFE is that the writing path records the
        state that forbids a retry BEFORE the submit and consumes the `ExecutionIntent` in the
        same move (`contracts.authorize_and_consume`), so a crash on the far side of a real side
        effect does NOT leave silence — it leaves a consumed intent and a recorded state.

    The mapping is deliberately the same one `domain/models.py::JobAttemptView` uses, and the
    decision itself is `contracts.may_retry_automatically`. There is no second list of states in
    this file: the states that forbid a retry live in `contracts.enums` and nowhere else.
    """
    if failure_class is None:
        return None
    try:
        return ExecutionAttemptState(failure_class)
    except ValueError:
        # `LEASE_EXPIRED` and every other queue-level class land here. Not an execution state,
        # so it says nothing about a side effect.
        return None


class JobLeaseReaper:
    """Reaps expired job leases for ONE organization. Holds a pool, never a connection.

    Scoped to an organization at construction, like `PostgresJobTransport` and for the same
    reason: a reaper whose scope is an argument can be pointed at somebody else's tenant by a
    caller's mistake, and nobody would notice a queue being drained one tenant over.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        organization_id: UUID,
        jitter: random.Random | None = None,
        actor_ref: str = REAPER_ACTOR_REF,
    ) -> None:
        self._pool = pool
        self._organization_id = organization_id
        # A generator per reaper, not the module-level one, so two reapers in one process cannot
        # fall into step. Injectable so a test can assert an exact delay.
        self._jitter = jitter if jitter is not None else random.Random()
        self._actor_ref = actor_ref

    # -- one pass -------------------------------------------------------------

    async def reap_once(self, *, limit: int = 32) -> ReapReport:
        """Reap up to `limit` expired jobs, each in its own transaction. Never raises on a race.

        The batch limit exists so that one pass cannot run for ever while new leases keep
        expiring, and `scanned_to_the_end` in the report says which of the two happened.

        KNOWN LIMIT, written down rather than discovered later: an UNEXPECTED failure on one job
        — a zero-row CAS under the lock, say — ends the whole pass, because the exception leaves
        this loop. `run_forever` logs it with its traceback and tries again one interval later, so
        a single poisoned row makes the reaper loud and slow rather than silent, but it does hold
        up the rest of the queue until somebody looks. Skipping such a row instead would turn a
        systematic bug into a per-job shrug, which is the trade this phase does not take.
        """
        if limit < 1:
            raise ValueError(
                f"limit = {limit}: przebieg reapera, który nie ma prawa odebrać ani jednego "
                "lease'u, nie jest przebiegiem — a raport z niego wyglądałby jak pusta kolejka"
            )
        reaped: list[ReapedJob] = []
        scanned_to_the_end = False
        for _ in range(limit):
            job = await self._reap_one()
            if job is None:
                # Nothing expired is left that this reaper may take.
                scanned_to_the_end = True
                break
            reaped.append(job)
            if job.outcome is ReapOutcome.LOST_RACE:
                # END THE PASS. A lost race leaves the row exactly as it was, so asking again
                # would return the same job — and the pass would spend its whole batch limit
                # rolling back the same transaction. Somebody else is working the queue right
                # now; the next pass is one interval away.
                break
        report = ReapReport(reaped=tuple(reaped), scanned_to_the_end=scanned_to_the_end)
        if reaped:
            _LOG.info("%s", report.explain())
            await self._close_orphaned_sessions()
        return report

    async def _close_orphaned_sessions(self) -> None:
        """Sesje, które zostały w RUNNING po zabraniu ich tury, kończy jako FAILED.

        WARSTWOWO TO JEST USTĘPSTWO I TAK JEST OPISANE. `jobs/` jest maszynerią pod `sessions/`
        i normalnie nie ma prawa o nich wiedzieć; import jest lokalny właśnie dlatego, że to
        wyjątek, a nie zależność pakietu.

        POWÓD, DLA KTÓREGO USTĘPUJEMY, jest zmierzony dwa razy tego samego dnia. Na live
        19.09 reaper zabrał joba `session.turn` o 18:48, a `agent_sessions` mówiło RUNNING
        z `updated_at` z 18:38: studio uczciwie pokazywało „pracuje", bo dane tak mówiły.
        Niezależny audyt tego samego dnia zapisał to samo jako test: „nie ma workera, reaper
        zakończył job, ale sesja nadal RUNNING".

        Reaper jest jedynym miejscem, w którym ten fakt POWSTAJE, więc jest też jedynym, w
        którym domknięcie nie spóźnia się o cudzy interwał. To samo uzgodnienie woła tik
        `ops.heartbeat`, jako druga droga na wypadek, gdyby reaper nie biegł; funkcja jest
        idempotentna, więc dwa wywołania nie robią nic złego.

        Nie podnosi wyjątku: przebieg reapera nie może paść przez sprzątanie po cudzej warstwie.
        """
        from control_plane.sessions.store import SessionStore

        try:
            closed = await SessionStore(self._pool).reconcile_orphans(
                organization_id=self._organization_id
            )
        except Exception as exc:  # sprzątanie po cudzej warstwie nie może zabrać przebiegu
            _LOG.warning("reaper: nie udało się uzgodnić stanu sesji: %s", exc)
            return
        for one in closed:
            _LOG.warning(
                "reaper: sesja %s stała w RUNNING bez żywej tury; zamknięta jako FAILED", one.id
            )

    async def _reap_one(self) -> ReapedJob | None:
        """One expired job, one transaction. `None` when there is nothing expired to take.

        The `UniqueViolationError` handler is OUTSIDE the transaction block on purpose: swallowing
        the error inside it would leave asyncpg trying to COMMIT an aborted transaction. It is
        also the second line of defence and not the first — the row lock is the first, and it is
        what makes the collision rare enough to be worth handling as a race rather than as a bug.
        """
        locked: list[_LockedJob] = []
        async with self._pool.acquire() as connection:
            try:
                return await self._reap_one_locked(connection, locked)
            except asyncpg.UniqueViolationError as collision:
                if not locked:
                    # The collision can only happen after the SELECT, so this cannot occur — and
                    # if it ever does, guessing would be worse than saying so.
                    raise RuntimeError(
                        "kolizja UNIQUE w reaperze przed zablokowaniem jakiegokolwiek joba — "
                        "nie wiadomo, którego joba dotyczy przegrany wyścig"
                    ) from collision
                identity = locked[0]
                _LOG.info(
                    "reaper przegrał wyścig o joba %s (próba %s): %s",
                    identity.job_id,
                    identity.attempt_no,
                    collision.__class__.__name__,
                )
                return ReapedJob(
                    job_id=identity.job_id,
                    attempt_id=identity.attempt_id,
                    attempt_no=identity.attempt_no,
                    attempt_limit=identity.attempt_limit,
                    outcome=ReapOutcome.LOST_RACE,
                )

    async def _reap_one_locked(
        self, connection: asyncpg.Connection, locked: list[_LockedJob]
    ) -> ReapedJob | None:
        """The whole decision and every write, inside one transaction.

        `locked` is an out-parameter with exactly one job of it: it lets the caller's race handler
        name the job whose replacement lost the collision. The transaction has rolled back by
        then, so nothing else can be read back from the database.
        """
        async with connection.transaction():
            row = await connection.fetchrow(_EXPIRED_JOB_SQL, self._organization_id)
            if row is None:
                return None
            job = _LockedJob.from_row(row)
            locked.append(job)

            closed_lease = await connection.fetchval(
                _CLOSE_LEASE_SQL, job.lease_id, LEASE_EXPIRED_FAILURE_CLASS
            )
            if closed_lease is None:
                # Zero updated rows. In this driver that is not an exception — see
                # `domain/errors.py`. Raising inside the transaction rolls the whole reap back.
                raise RuntimeError(
                    f"reaper nie zamknął lease'u {job.lease_id} joba {job.job_id}: zero "
                    "zaktualizowanych wierszy. Lease przestał być ACTIVE pod blokadą reapera, "
                    "więc dalsze zapisy dotyczyłyby cudzej próby"
                )

            closed_attempt = await connection.fetchrow(
                _CLOSE_ATTEMPT_SQL, job.attempt_id, LEASE_EXPIRED_FAILURE_CLASS
            )
            if closed_attempt is None:
                raise RuntimeError(
                    f"reaper nie zamknął próby {job.attempt_id} joba {job.job_id}: zero "
                    "zaktualizowanych wierszy. Próba nie była już RUNNING, a limit prób z L5214 "
                    "liczy wyłącznie próby, które ktoś domknął"
                )

            recorded_state = _recorded_execution_state(closed_attempt["failure_class"])
            attempt_no = closed_attempt["attempt_no"]

            # The two reasons not to try again, checked in this order. The recorded state comes
            # first because it is the stronger statement about the world: the counter says "we
            # tried enough", the state says "trying again could publish the same post twice"
            # (L2673). Both terminalize; only the reason_code differs, and the audit needs the
            # sharper one.
            if recorded_state is not None and not may_retry_automatically(recorded_state):
                return await self._terminalize(
                    connection,
                    job,
                    attempt_no=attempt_no,
                    outcome=ReapOutcome.TERMINALIZED_RETRY_FORBIDDEN,
                    recorded_state=recorded_state,
                )
            if attempt_no >= job.attempt_limit:
                return await self._terminalize(
                    connection,
                    job,
                    attempt_no=attempt_no,
                    outcome=ReapOutcome.TERMINALIZED_ATTEMPTS_EXHAUSTED,
                    recorded_state=recorded_state,
                )
            return await self._schedule_retry(
                connection, job, attempt_no=attempt_no, recorded_state=recorded_state
            )

    async def _schedule_retry(
        self,
        connection: asyncpg.Connection,
        job: _LockedJob,
        *,
        attempt_no: int,
        recorded_state: ExecutionAttemptState | None,
    ) -> ReapedJob:
        """Park the job for its backoff and create the replacement attempt (L2669)."""
        delay = backoff_seconds(attempt_no, jitter=self._jitter)
        scheduled = await connection.fetchrow(
            _SCHEDULE_RETRY_SQL,
            job.job_id,
            float(delay),
            RUNNING_STATUS,
            WAITING_RETRY_STATUS,
        )
        if scheduled is None:
            raise RuntimeError(
                f"reaper nie odroczył joba {job.job_id}: zero zaktualizowanych wierszy przy "
                f"oczekiwanym statusie {RUNNING_STATUS}. Job zmienił stan pod blokadą reapera"
            )

        replacement = await connection.fetchrow(
            _REPLACEMENT_ATTEMPT_SQL,
            job.organization_id,
            job.job_id,
            attempt_no + 1,
            scheduled["dispatch_version"],
            job.lease_generation + 1,
            scheduled["not_before"],
        )
        if replacement is None:
            raise RuntimeError(
                f"INSERT zastępczej próby joba {job.job_id} nie zwrócił wiersza. Job z zamkniętą "
                "próbą i bez zastępczej nie jest ani wykonywalny, ani terminalny — to dokładnie "
                "zgubiony task z L5212"
            )

        await AuditWriter(connection).record(
            kind="JOB_LEASE_EXPIRED",
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            subject_ref=f"job:{job.job_id}",
            actor_ref=self._actor_ref,
            payload={
                # `str(...)` because `audit.py` accepts scalars only: a UUID object would be
                # refused as a non-scalar value, not silently stringified.
                "job_id": str(job.job_id),
                "attempt_id": str(job.attempt_id),
                "attempt_no": attempt_no,
                "from_state": RUNNING_STATUS,
                "to_state": WAITING_RETRY_STATUS,
                "reason_code": LEASE_EXPIRED_FAILURE_CLASS,
            },
        )
        return ReapedJob(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            attempt_no=attempt_no,
            attempt_limit=job.attempt_limit,
            outcome=ReapOutcome.RETRY_SCHEDULED,
            replacement_attempt_no=replacement["attempt_no"],
            delay_seconds=delay,
            recorded_state=recorded_state,
        )

    async def _terminalize(
        self,
        connection: asyncpg.Connection,
        job: _LockedJob,
        *,
        attempt_no: int,
        outcome: ReapOutcome,
        recorded_state: ExecutionAttemptState | None,
    ) -> ReapedJob:
        """Close the job for good, with ONE audit row saying why. No replacement attempt."""
        terminalized = await connection.fetchval(
            _TERMINALIZE_SQL, job.job_id, RUNNING_STATUS, TERMINAL_STATUS
        )
        if terminalized is None:
            raise RuntimeError(
                f"reaper nie zterminalizował joba {job.job_id}: zero zaktualizowanych wierszy "
                f"przy oczekiwanym statusie {RUNNING_STATUS}. Job z wyczerpanym limitem prób, "
                "który został RUNNING, jest redispatchowany bez końca (L5214)"
            )

        reason_code = (
            "ATTEMPTS_EXHAUSTED"
            if outcome is ReapOutcome.TERMINALIZED_ATTEMPTS_EXHAUSTED
            else "RETRY_FORBIDDEN"
        )
        await AuditWriter(connection).record(
            kind="JOB_STATE_CHANGED",
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            subject_ref=f"job:{job.job_id}",
            actor_ref=self._actor_ref,
            payload={
                "job_id": str(job.job_id),
                "attempt_id": str(job.attempt_id),
                "attempt_no": attempt_no,
                # The state the attempt itself reported, when it reported one the machine knows;
                # otherwise the job status the reaper found.
                "from_state": recorded_state.value if recorded_state else RUNNING_STATUS,
                "to_state": TERMINAL_STATUS,
                "reason_code": reason_code,
            },
        )
        return ReapedJob(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            attempt_no=attempt_no,
            attempt_limit=job.attempt_limit,
            outcome=outcome,
            recorded_state=recorded_state,
        )

    # -- the loop -------------------------------------------------------------

    async def run_forever(self, *, interval: float = REAPER_INTERVAL_SECONDS) -> None:
        """Reap for ever, every `interval` seconds, starting NOW.

        Two properties, both of them the reason this loop is written by hand rather than left to
        a scheduler:

        *   **The first pass happens before the first sleep.** A process that starts by sleeping
            leaves every lease that died while it was down alive for one more interval, and if
            the process is restarted often enough, for ever.
        *   **An exception does not end the loop.** A reaper that dies on the first unexpected
            error stops recovering abandoned jobs while every one of its unit tests stays green
            — this project's recurring failure shape. The error is logged with its traceback and
            the next pass runs.

        `CancelledError` is re-raised, because that one is the shutdown asking the loop to stop.
        """
        _LOG.info("reaper startuje, interwał %.1f s (D-017)", interval)
        while True:
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception(
                    "przebieg reapera padł — pętla żyje dalej. Reaper, który umiera na pierwszym "
                    "wyjątku, przestaje odzyskiwać porzucone joby i nikt tego nie widzi"
                )
            await asyncio.sleep(interval)
