"""A second `JobQueuePort`, built out of dictionaries. Test material, never production.

WHY THIS FILE EXISTS. One line of the Definition of Done of E1.F1:

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

Until this file existed, the repository had ONE implementation of the port and an argument:
`grep -rn` for the driver's name over `control_plane/domain/` comes back empty, so the domain
cannot be reaching the storage. That is a premise, not a measurement. The measurement is a
SECOND implementation that shares no code with the first and answers the same domain commands
the same way — `tests/test_transport_contract.py` runs both and compares.

WHY IT LIVES IN THE TEST PACKAGE. L5501 allows F1 to write ahead of itself for ports and schemas
only, never for implementations; a second transport under `control_plane/` would be an E3
component smuggled into F1. The Definition of Done asks for a test, and this is part of the test.

WHAT THIS FAKE IS AND IS NOT
----------------------------
IT IS: a re-implementation of the rules `control_plane/jobs/store.py` states in SQL. These are
the ones the contract test actually COMPARES between the two implementations, each because the
scenario would answer differently if this file got it wrong:

  * the idempotency key that adds nothing and overwrites nothing (`DO NOTHING`, not `DO UPDATE`),
  * the claim ordered by `(priority, not_before)` — measured with a batch of one, so WHICH job
    was taken is a deterministic fact rather than an unpromised row order,
  * the backlog counted from the snapshot taken BEFORE the claim,
  * the lease released when a job parks, and `holds_live_lease` judged by the deadline rather
    than by the status column,
  * `dispatch_version` growing on a republish and on a requeue, never on a claim,
  * `lock_version` growing on every write,
  * the compare-and-set refusing a token forged in `attempt_id`, in `lease_generation` or in
    `lease_owner`, and writing nothing when it refuses.

IT IS NOT, and no docstring in this file may claim otherwise:
  * NOT a proof of anything about concurrency. This object is single-threaded and takes no locks.
    `SELECT ... FOR UPDATE SKIP LOCKED` is the mechanism L5193 names, and the only thing that
    measures it is `tests/test_job_claim.py` against a real server. A green contract test says
    nothing about two workers.
  * NOT a proof about lease expiry over time. The contract test never waits for a lease to die,
    so `expires_at > now()` — the sixth element of the fencing tuple — is exercised here only
    from its live side and is NOT MEASURED against the PostgreSQL implementation. Neither is the
    `job_id` element (no forgery in the contract test differs in it alone) nor `expected_status`
    in isolation (after a close the job's status and the lease's status both change, so the two
    conditions cannot be told apart there). All three are measured against the real server in
    `test_job_lease_fencing.py`, by mutation, one element at a time.
  * NOT a transport anything may run on. There is no durability, no process boundary, no
    at-least-once anything.

THE CLOCK. `store.py` computes every deadline with PostgreSQL's `now()` and never with the
process clock (L2668). This object has no server, so it keeps a clock of its own, and that clock
is forced to move STRICTLY FORWARD on every read: two rows created inside the same microsecond
would make "newest first" ambiguous, and an ambiguous order compared against PostgreSQL's would
be a test that fails on a fast machine and passes on a slow one. STATED AS UNMEASURED: removing
that nudge does not turn any test red on demand — it turns one flaky, which is why the nudge is
here and why this paragraph does not claim a mutation behind it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import UUID, uuid4

from community_os_contracts import JobFencingToken, JobWaitingState
from control_plane.domain.errors import refuse_stale_write
from control_plane.domain.models import (
    ClaimBatch,
    ClaimedJob,
    EnqueuedJob,
    JobAttemptView,
    JobDetail,
    JobOutcome,
    JobView,
)

#: The job statuses this transport has to agree with `jobs/store.py` about. They are NOT imported
#: from there: that module imports the driver, and a fake that pulls the driver in through the
#: back door proves nothing about a driver-free transport. The copy is therefore deliberate AND
#: pinned — the test named below compares all three of these constants with `store`'s own and
#: fails if they ever drift:
#:     test_transport_contract.py::test_both_transports_satisfy_the_port_protocols
DISPATCHABLE_STATUS = "READY"
RUNNING_STATUS = "RUNNING"

#: Statuses `publish_ready` accepts as a starting point. Taken from the enum, exactly as
#: `store.py` takes them, so a fifth waiting state added to the contracts arrives in both
#: implementations at once instead of in one of them.
REPUBLISHABLE_STATUSES: tuple[str, ...] = tuple(state.value for state in JobWaitingState)

#: The attempt status written when an attempt ends. `park_until_ready` and `requeue` both write
#: FAILED, because `job_attempts` has no "paused" status — the same choice `store.py` explains
#: at length and for the same reason.
ENDED_ATTEMPT_STATUS = "FAILED"

#: What `requeue` records as the reason the attempt ended when the caller names none. The literal
#: is `store.requeue`'s `failure_class or "REQUEUED"`, and the contract test compares the value
#: that reaches the screen, so a change on one side turns it red.
DEFAULT_REQUEUE_CLASS = "REQUEUED"


@dataclass
class _JobRow:
    """One row of `jobs`. Mutable, because the queue mutates it; nothing outside this module
    ever sees it — callers get the frozen values from `domain/models.py`."""

    job_id: UUID
    organization_id: UUID
    workspace_id: UUID | None
    job_type: str
    routing_key: str
    priority: int
    payload_ref: str | None
    dispatch_version: int
    status: str
    not_before: datetime
    attempt_limit: int
    idempotency_key: str
    lock_version: int
    created_at: datetime


@dataclass
class _AttemptRow:
    """One row of `job_attempts`. `not_before` stays `None`: the PostgreSQL implementation never
    writes that column either, and a fake that filled it in would disagree on the screen."""

    attempt_id: UUID
    job_id: UUID
    attempt_no: int
    dispatch_version: int
    lease_generation: int
    lease_owner: UUID
    status: str
    started_at: datetime
    heartbeat_at: datetime | None
    expires_at: datetime | None
    not_before: datetime | None = None
    completed_at: datetime | None = None
    failure_class: str | None = None


@dataclass
class _LeaseRow:
    """One row of `job_leases`."""

    job_id: UUID
    attempt_id: UUID
    lease_generation: int
    lease_owner: UUID
    status: str
    acquired_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    failure_class: str | None = None


class InMemoryJobTransport:
    """`JobQueuePort` over three dictionaries. No driver, no server, no I/O.

    Scoped to one organization at construction, exactly like `PostgresJobTransport`, including
    the refusal when it was built without one. That refusal is part of what the contract test
    compares: a read-only transport that defaulted to "some organization" on one side and refused
    on the other would be a difference between the two transports, which is the whole subject.
    """

    def __init__(self, *, organization_id: UUID | None = None) -> None:
        self._organization_id = organization_id
        self._jobs: dict[UUID, _JobRow] = {}
        self._attempts: list[_AttemptRow] = []
        self._leases: list[_LeaseRow] = []
        self._latest_moment: datetime | None = None

    # -- the clock ------------------------------------------------------------

    def _now(self) -> datetime:
        """The current moment, forced to be strictly later than the previous one.

        See the module docstring: without the nudge two rows can share a `created_at`, and then
        "newest first" has two correct answers while PostgreSQL has one.
        """
        moment = datetime.now(UTC)
        if self._latest_moment is not None and moment <= self._latest_moment:
            moment = self._latest_moment + timedelta(microseconds=1)
        self._latest_moment = moment
        return moment

    def _writing_organization(self) -> UUID:
        if self._organization_id is None:
            raise RuntimeError(
                "transport zbudowany bez organizacji — ta instancja służy wyłącznie do odczytu. "
                "Claim albo zapis wykonany „w jakiejś” organizacji to operacja na obcym tenancie, "
                "której nikt nie zauważy"
            )
        return self._organization_id

    # -- the transport half of the port ---------------------------------------

    async def publish_ready(self, job_id: UUID, *, not_before: datetime | None = None) -> None:
        """Make a waiting job dispatchable again, with a new dispatch version (L2147).

        Not scoped by organization, and that is a copy of the PostgreSQL statement rather than an
        oversight: `store.publish_ready` looks the job up by id alone. A silent no-op is refused
        on both sides — a job the operator believes was released and the dispatcher never sees.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise LookupError(f"nie ma joba {job_id} — nie ma czego ogłaszać gotowym")
        if job.status not in REPUBLISHABLE_STATUSES:
            raise ValueError(
                f"job {job_id} jest w stanie {job.status}, a do puli wracają wyłącznie joby "
                f"oczekujące ({', '.join(REPUBLISHABLE_STATUSES)}). Zero zaktualizowanych wierszy "
                "zgłoszone jako sukces byłoby jobem, którego dispatcher nigdy nie zobaczy"
            )
        moment = self._now()
        job.status = DISPATCHABLE_STATUS
        job.not_before = max(not_before or moment, moment)
        job.dispatch_version += 1
        job.lock_version += 1

    async def reserve(
        self, *, owner: UUID, limit: int, lease_seconds: float
    ) -> tuple[JobFencingToken, ...]:
        """Take at most `limit` jobs and return one lease token each — tokens, not jobs."""
        batch = await self.claim(worker=owner, limit=limit, lease_seconds=lease_seconds)
        return tuple(claim.fencing_token for claim in batch.claims)

    async def acknowledge(self, token: JobFencingToken) -> None:
        """Close the attempt under this token as a success. A stale token is refused."""
        await self.close_attempt(token, outcome=JobOutcome.SUCCEEDED)

    async def requeue(
        self, token: JobFencingToken, *, not_before: datetime, failure_class: str | None = None
    ) -> None:
        """Hand the job back for a later attempt; `dispatch_version` grows, the lease goes.

        The caller's moment is clamped with `max(…, now)`, which is the Python spelling of the
        `greatest($7, now())` in `_REQUEUE_SQL`: a deadline already behind the queue's clock is
        not a delay.
        """
        reason = failure_class or DEFAULT_REQUEUE_CLASS
        job, attempt, lease = self._fenced_rows(token, reason="oddanie joba do kolejki")
        moment = self._now()
        self._release(lease, attempt, moment=moment, failure_class=reason)
        job.status = DISPATCHABLE_STATUS
        job.not_before = max(not_before, moment)
        job.dispatch_version += 1
        job.lock_version += 1

    # -- the queue's slice of the Domain Data Gateway --------------------------

    async def create_job(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID | None,
        job_type: str,
        routing_key: str,
        priority: int,
        attempt_limit: int,
        idempotency_key: str,
        payload_ref: str | None = None,
        delay_seconds: float = 0.0,
    ) -> EnqueuedJob:
        """Write the row. A repeated idempotency key adds nothing and says so (L2820).

        `created=False` on the conflict path, and the existing row is left ALONE — `DO NOTHING`,
        never `DO UPDATE`. The contract test enqueues the same key twice with a different
        `job_type` and reads the job back, so overwriting here would be visible.
        """
        existing = next(
            (
                job
                for job in self._jobs.values()
                if job.organization_id == organization_id and job.idempotency_key == idempotency_key
            ),
            None,
        )
        if existing is not None:
            return EnqueuedJob(job_id=existing.job_id, created=False)

        moment = self._now()
        job_id = uuid4()
        self._jobs[job_id] = _JobRow(
            job_id=job_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            job_type=job_type,
            routing_key=routing_key,
            priority=priority,
            payload_ref=payload_ref,
            dispatch_version=1,
            status=DISPATCHABLE_STATUS,
            not_before=moment + timedelta(seconds=float(delay_seconds)),
            attempt_limit=attempt_limit,
            idempotency_key=idempotency_key,
            lock_version=1,
            created_at=moment,
        )
        return EnqueuedJob(job_id=job_id, created=True)

    async def claim(
        self,
        *,
        worker: UUID,
        limit: int,
        lease_seconds: float,
        job_types: Sequence[str] | None = None,
        exclude_job_types: Sequence[str] | None = None,
    ) -> ClaimBatch:
        """Claim a batch and report the backlog THIS claim's own snapshot saw.

        The three counts are taken before a single row is touched, which is what makes
        `BatchEmptiness.ALL_CLAIMED_BY_OTHERS` distinguishable from `QUEUE_EMPTY` after the fact.
        Counting afterwards would give a different answer on both implementations, and the same
        wrong one on both — which is exactly the failure a contract test cannot see, so the
        counts are also compared against absolute expected values in the contract test.
        """
        if limit < 1:
            raise ValueError(f"limit = {limit}: claim o zerowej wielkości nie jest zapytaniem")
        organization_id = self._writing_organization()
        moment = self._now()

        # The same lane filter as `_CLAIM_SQL` (`jobs/lanes.py`), applied to the backlog counts
        # too: a lane's emptiness is about that lane.
        scope = [
            job
            for job in self._jobs.values()
            if job.organization_id == organization_id
            and (job_types is None or job.job_type in job_types)
            and (exclude_job_types is None or job.job_type not in exclude_job_types)
        ]
        due_now = [
            job for job in scope if job.status == DISPATCHABLE_STATUS and job.not_before <= moment
        ]
        scheduled_later = sum(
            1 for job in scope if job.status == DISPATCHABLE_STATUS and job.not_before > moment
        )
        already_running = sum(1 for job in scope if job.status == RUNNING_STATUS)

        taken = sorted(due_now, key=lambda job: (job.priority, job.not_before))[:limit]
        claims = tuple(
            self._start_attempt(job, moment=moment, lease_seconds=float(lease_seconds))
            for job in taken
        )
        return ClaimBatch(
            claims=claims,
            due_before_claim=len(due_now),
            scheduled_later=scheduled_later,
            already_running=already_running,
        )

    async def close_attempt(
        self,
        token: JobFencingToken,
        *,
        outcome: JobOutcome,
        failure_class: str | None = None,
    ) -> None:
        """Terminalize the job under this token. Both outcomes are terminal, as in the store.

        This method never puts a job back in the queue and knows nothing about `attempt_limit`:
        whether one more attempt is allowed is the reaper's decision (L5214), in one place.
        """
        job, attempt, lease = self._fenced_rows(token, reason=f"zapis wyniku {outcome.value}")
        moment = self._now()
        attempt_status = "SUCCEEDED" if outcome is JobOutcome.SUCCEEDED else ENDED_ATTEMPT_STATUS
        self._release(
            lease, attempt, moment=moment, failure_class=failure_class, status=attempt_status
        )
        job.status = outcome.value
        job.lock_version += 1

    async def park_until_ready(
        self,
        token: JobFencingToken,
        *,
        waiting_state: JobWaitingState,
        delay_seconds: float,
    ) -> None:
        """Move the job into a `WAITING_*` state and RELEASE the lease (L2147, L5288).

        The waiting state is written twice from one value — as the job's status and as the
        attempt's `failure_class` — for the reason `_PARK_SQL` gives: they cannot then disagree.
        """
        job, attempt, lease = self._fenced_rows(
            token, reason=f"wstrzymanie w stanie {waiting_state.value}"
        )
        moment = self._now()
        self._release(lease, attempt, moment=moment, failure_class=waiting_state.value)
        job.status = waiting_state.value
        job.not_before = moment + timedelta(seconds=float(delay_seconds))
        job.lock_version += 1

    async def fetch_job(self, job_id: UUID, *, organization_id: UUID) -> JobDetail | None:
        """One job of this organization with its attempts, in attempt order, or `None`.

        A job of another organization reads as `None`, not as a row: "not yours" and "not there"
        have to look the same from outside, or a 404 becomes an existence oracle.
        """
        job = self._jobs.get(job_id)
        if job is None or job.organization_id != organization_id:
            return None
        attempts = sorted(
            (attempt for attempt in self._attempts if attempt.job_id == job_id),
            key=lambda attempt: attempt.attempt_no,
        )
        return JobDetail(
            job=self._job_view(job),
            attempts=tuple(
                JobAttemptView(
                    attempt_id=attempt.attempt_id,
                    attempt_no=attempt.attempt_no,
                    dispatch_version=attempt.dispatch_version,
                    lease_generation=attempt.lease_generation,
                    lease_owner=attempt.lease_owner,
                    status=attempt.status,
                    not_before=attempt.not_before,
                    heartbeat_at=attempt.heartbeat_at,
                    expires_at=attempt.expires_at,
                    started_at=attempt.started_at,
                    completed_at=attempt.completed_at,
                    failure_class=attempt.failure_class,
                )
                for attempt in attempts
            ),
        )

    async def fetch_jobs(self, *, organization_id: UUID, limit: int = 100) -> tuple[JobView, ...]:
        """The most recent jobs of ONE organization, newest first, ties broken by id.

        The tie-break copies `ORDER BY j.created_at DESC, j.id`. Python compares `UUID` by its
        integer value and PostgreSQL compares `uuid` byte by byte, big-endian — the same order.
        """
        scope = [job for job in self._jobs.values() if job.organization_id == organization_id]
        by_identifier = sorted(scope, key=lambda job: job.job_id)
        newest_first = sorted(by_identifier, key=lambda job: job.created_at, reverse=True)
        return tuple(self._job_view(job) for job in newest_first[:limit])

    # -- the parts the four writing methods share -----------------------------

    def _start_attempt(self, job: _JobRow, *, moment: datetime, lease_seconds: float) -> ClaimedJob:
        """Move one job to `RUNNING` and open its attempt and lease.

        `attempt_no` and `lease_generation` are `max(existing) + 1`, per job — the Python form of
        the two correlated subqueries in the `numbered` CTE. `lease_owner` is minted fresh on
        every takeover and is NOT the worker's identity: L2666 wants a random owner, and a stable
        one would be dead weight in the fencing tuple.
        """
        attempt_no = (
            max(
                (attempt.attempt_no for attempt in self._attempts if attempt.job_id == job.job_id),
                default=0,
            )
            + 1
        )
        lease_generation = (
            max(
                (lease.lease_generation for lease in self._leases if lease.job_id == job.job_id),
                default=0,
            )
            + 1
        )
        expires_at = moment + timedelta(seconds=lease_seconds)
        attempt = _AttemptRow(
            attempt_id=uuid4(),
            job_id=job.job_id,
            attempt_no=attempt_no,
            dispatch_version=job.dispatch_version,
            lease_generation=lease_generation,
            lease_owner=uuid4(),
            status=RUNNING_STATUS,
            started_at=moment,
            heartbeat_at=moment,
            expires_at=expires_at,
        )
        self._attempts.append(attempt)
        self._leases.append(
            _LeaseRow(
                job_id=job.job_id,
                attempt_id=attempt.attempt_id,
                lease_generation=lease_generation,
                lease_owner=attempt.lease_owner,
                status="ACTIVE",
                acquired_at=moment,
                expires_at=expires_at,
            )
        )
        job.status = RUNNING_STATUS
        job.lock_version += 1
        return ClaimedJob(
            job_id=job.job_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            job_type=job.job_type,
            routing_key=job.routing_key,
            priority=job.priority,
            payload_ref=job.payload_ref,
            dispatch_version=job.dispatch_version,
            attempt_limit=job.attempt_limit,
            attempt_id=attempt.attempt_id,
            attempt_no=attempt_no,
            lease_generation=lease_generation,
            lease_owner=attempt.lease_owner,
            expires_at=expires_at,
        )

    def _fenced_rows(
        self, token: JobFencingToken, *, reason: str
    ) -> tuple[_JobRow, _AttemptRow, _LeaseRow]:
        """The compare-and-set. Returns the three rows to write, or raises.

        SIX conditions, the same six `_HEARTBEAT_SQL`'s comment lists and the same six every
        writing statement in `store.py` carries:

            job_id · attempt_id · lease_generation · lease_owner  — the four identifiers
            the job is in the expected status                     — `expected_status`
            the lease has not expired                             — `expires_at > now()`

        A token that matches none of the live rows is a REFUSAL, never a quiet success — the
        failure mode `errors.py` exists for. The freshness verdict itself is not re-implemented
        here: `refuse_stale_write` hands it to `contracts.require_fresh_job_lease`, so this fake
        and the PostgreSQL store refuse through the very same code.
        """
        moment = self._now()
        job = self._jobs.get(token.job_id)
        lease = next(
            (
                candidate
                for candidate in self._leases
                if candidate.job_id == token.job_id
                and candidate.attempt_id == token.attempt_id
                and candidate.lease_generation == token.lease_generation
                and candidate.lease_owner == token.lease_owner
                and candidate.status == "ACTIVE"
                and candidate.expires_at > moment
            ),
            None,
        )
        attempt = next(
            (
                candidate
                for candidate in self._attempts
                if candidate.attempt_id == token.attempt_id and candidate.status == RUNNING_STATUS
            ),
            None,
        )
        if job is None or job.status != RUNNING_STATUS or lease is None or attempt is None:
            self._refuse(token, reason=reason, moment=moment)
        return job, attempt, lease

    def _refuse(self, token: JobFencingToken, *, reason: str, moment: datetime) -> NoReturn:
        """Turn a compare-and-set that matched nothing into a refusal naming the current holder.

        `live` is read with the SAME two conditions as `_LIVE_LEASE_SQL` — `ACTIVE` **and** not
        past `expires_at`. A lease past its deadline is not a holder, and telling the operator to
        go and stop a worker that holds nothing is worse than telling them nobody holds it.
        """
        live_lease = next(
            (
                candidate
                for candidate in self._leases
                if candidate.job_id == token.job_id
                and candidate.status == "ACTIVE"
                and candidate.expires_at > moment
            ),
            None,
        )
        live = (
            JobFencingToken(
                job_id=live_lease.job_id,
                attempt_id=live_lease.attempt_id,
                lease_generation=live_lease.lease_generation,
                lease_owner=live_lease.lease_owner,
            )
            if live_lease is not None
            else None
        )
        refuse_stale_write(presented=token, live=live, reason=reason)

    @staticmethod
    def _release(
        lease: _LeaseRow,
        attempt: _AttemptRow,
        *,
        moment: datetime,
        failure_class: str | None,
        status: str = ENDED_ATTEMPT_STATUS,
    ) -> None:
        """Close the lease and its attempt together. Never one without the other.

        `_fenced_write` in the store rolls the transaction back when the lease was released and
        the attempt was not, "because half of this is worse than none of it". Here the two writes
        are one function for the same reason.
        """
        lease.status = "RELEASED"
        lease.completed_at = moment
        lease.failure_class = failure_class
        attempt.status = status
        attempt.completed_at = moment
        attempt.failure_class = failure_class

    # -- reading ---------------------------------------------------------------

    def _job_view(self, job: _JobRow) -> JobView:
        """One job row as the domain value, with the two counts the screen cannot compute.

        `holds_live_lease` is `ACTIVE` **and** not past `expires_at`, judged against this
        object's clock. Counting `ACTIVE` rows alone would call an abandoned lease live, which is
        the mistake `_JOB_COLUMNS_SQL` documents as having gone unmeasured for a whole package.
        """
        moment = self._now()
        return JobView(
            job_id=job.job_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            job_type=job.job_type,
            routing_key=job.routing_key,
            priority=job.priority,
            payload_ref=job.payload_ref,
            dispatch_version=job.dispatch_version,
            status=job.status,
            not_before=job.not_before,
            attempt_limit=job.attempt_limit,
            idempotency_key=job.idempotency_key,
            lock_version=job.lock_version,
            created_at=job.created_at,
            attempt_count=sum(1 for attempt in self._attempts if attempt.job_id == job.job_id),
            holds_live_lease=any(
                lease.job_id == job.job_id
                and lease.status == "ACTIVE"
                and lease.expires_at > moment
                for lease in self._leases
            ),
        )
