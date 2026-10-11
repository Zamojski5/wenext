"""Values the domain commands speak in. No driver, no I/O, no clock.

Everything here is a plain frozen dataclass or a `StrEnum`, and that is a boundary condition
rather than a style: `control_plane/domain/` is the layer the E3 broker swap must not touch
(L5206, L5216), so a driver type — a record, a pool, a connection — appearing in one of these
signatures would put the storage back inside the seam. The DoD grep for the driver's name over
this package has to come back empty, WHICH IS WHY THE NAME IS NOT WRITTEN ANYWHERE HERE, not
even inside an explanation: the first version of this docstring spelled it out and failed its
own check. `tests/test_jobs_api.py` proves the rule the only way that counts — it runs every
command against a transport that is not a database at all.

The one import from outside is `community_os_contracts`, which is where the fencing token and the
retry rule live. That is the point of contracts — not a second copy of either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from community_os_contracts import ExecutionAttemptState, JobFencingToken
from community_os_contracts.execution import may_retry_automatically


class JobOutcome(StrEnum):
    """How an attempt ended, as far as the worker reporting it is concerned.

    Exactly two values, and no `RETRY`. Whether one more attempt is allowed is the attempt
    limit's business (L5214), which lives in the reaper — and the transport port says the same
    thing in its own docstring: "the transport never invents an extra attempt". A third value
    here would be a second place where that decision could be made.
    """

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class BatchEmptiness(StrEnum):
    """WHY a claim came back empty. An empty batch is a diagnosis, never a verdict.

    "Pusty zbiór, pusta lista, zero zaktualizowanych wierszy — to są BŁĘDY, nie zaliczenia."
    A dispatcher that cannot tell these apart logs the same line for a drained queue and for a
    queue whose every job is stuck under a lease nobody is renewing. The first is Tuesday
    afternoon; the second is an incident.
    """

    #: Something was claimed.
    NOT_EMPTY = "NOT_EMPTY"
    #: Nothing dispatchable, nothing scheduled, nothing running. The queue really is empty.
    QUEUE_EMPTY = "QUEUE_EMPTY"
    #: There WAS work — another worker holds it. Either it was locked inside somebody else's
    #: claim statement, or it is already `RUNNING` under a live lease.
    ALL_CLAIMED_BY_OTHERS = "ALL_CLAIMED_BY_OTHERS"
    #: Work exists but its `not_before` has not arrived. This is the backoff schedule of D-017
    #: doing its job, and it must not read as an empty queue.
    ALL_SCHEDULED_FOR_LATER = "ALL_SCHEDULED_FOR_LATER"


@dataclass(frozen=True)
class EnqueuedJob:
    """The result of putting a job in the queue.

    `created` is `False` when the idempotency key was already there — "internal deduplication"
    (L2820). The caller gets the id of the EXISTING job and is told that nothing new was written,
    because "we enqueued it" and "it was already enqueued" are different facts and only one of
    them means work was added.
    """

    job_id: UUID
    created: bool


@dataclass(frozen=True)
class ClaimedJob:
    """One job taken by one worker, together with the lease it now holds.

    The four fencing fields are here as columns and are handed out as `fencing_token`, which is
    `contracts.JobFencingToken` — not a local look-alike. A local four-field twin would be
    rejected by `require_fresh_job_lease` where it matters, or worse, accepted by a second local
    copy of that check (L2377, L5178).
    """

    job_id: UUID
    organization_id: UUID
    workspace_id: UUID | None
    job_type: str
    routing_key: str
    priority: int
    payload_ref: str | None
    dispatch_version: int
    attempt_limit: int
    attempt_id: UUID
    attempt_no: int
    lease_generation: int
    lease_owner: UUID
    #: When the lease dies unless renewed. Computed by PostgreSQL, never by this process (L2668).
    #: `None` only for a transport with no clock of its own, i.e. the in-memory fake in the tests.
    expires_at: datetime | None

    @property
    def fencing_token(self) -> JobFencingToken:
        """The token that must accompany every write made on behalf of this job."""
        return JobFencingToken(
            job_id=self.job_id,
            attempt_id=self.attempt_id,
            lease_generation=self.lease_generation,
            lease_owner=self.lease_owner,
        )


@dataclass(frozen=True)
class ClaimBatch:
    """What one claim returned, plus the backlog the claim statement itself saw.

    The three counts come from the SAME statement and therefore the SAME snapshot as the claim,
    taken BEFORE the update. That is what makes them usable: `due_before_claim > 0` together with
    an empty batch means the rows were there and somebody else had them locked — a fact no
    follow-up query could establish afterwards, because by then the world has moved.
    """

    claims: tuple[ClaimedJob, ...]
    #: `READY` rows whose `not_before` had already passed, as of the claim's snapshot.
    due_before_claim: int
    #: `READY` rows still waiting for their `not_before`.
    scheduled_later: int
    #: Rows already `RUNNING` under somebody's lease.
    already_running: int

    @property
    def emptiness(self) -> BatchEmptiness:
        """Which of the four situations this batch is. The order of the checks is the meaning."""
        if self.claims:
            return BatchEmptiness.NOT_EMPTY
        if self.due_before_claim > 0 or self.already_running > 0:
            # Work existed and this worker did not get it. Contention or dead leases — either
            # way, NOT an empty queue.
            return BatchEmptiness.ALL_CLAIMED_BY_OTHERS
        if self.scheduled_later > 0:
            return BatchEmptiness.ALL_SCHEDULED_FOR_LATER
        return BatchEmptiness.QUEUE_EMPTY

    def explain(self) -> str:
        """One sentence for the operator, in Polish (D-010: komunikaty czyta założyciel)."""
        counts = (
            f"gotowych teraz {self.due_before_claim}, "
            f"zaplanowanych później {self.scheduled_later}, "
            f"w toku {self.already_running}"
        )
        if self.claims:
            return f"pobrano {len(self.claims)} jobów ({counts})"
        if self.emptiness is BatchEmptiness.ALL_CLAIMED_BY_OTHERS:
            return (
                f"nic nie pobrano, ale praca JEST — trzyma ją ktoś inny albo martwy lease "
                f"({counts})"
            )
        if self.emptiness is BatchEmptiness.ALL_SCHEDULED_FOR_LATER:
            return f"nic nie pobrano — wszystko czeka na swój not_before ({counts})"
        return f"kolejka jest pusta ({counts})"


@dataclass(frozen=True)
class JobAttemptView:
    """One row of `job_attempts`, as the operator's screen reads it.

    `execution_attempt_state` and `may_retry_automatically` are DERIVED, and derived in exactly
    one place — here — from `contracts.may_retry_automatically`. The API must not carry a second
    copy of the condition: after `SUBMIT_STARTED` the side effect may already have happened on
    the platform (L2673, L4560, L5335), and two copies of that rule are two chances for one of
    them to say "retry".
    """

    attempt_id: UUID
    attempt_no: int
    dispatch_version: int
    lease_generation: int
    lease_owner: UUID
    status: str
    not_before: datetime | None
    heartbeat_at: datetime | None
    expires_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    #: The class of the outcome — the only free column L3838 gives the attempt. For an attempt
    #: that reached the side-effect boundary, that class IS the `ExecutionAttempt` state it ended
    #: in (L2601-2605); for one that failed earlier it is whatever the worker recorded.
    failure_class: str | None

    @property
    def execution_attempt_state(self) -> ExecutionAttemptState | None:
        """`failure_class` read as an `ExecutionAttempt` state, or `None` when it is not one."""
        if self.failure_class is None:
            return None
        try:
            return ExecutionAttemptState(self.failure_class)
        except ValueError:
            return None

    @property
    def may_retry_automatically(self) -> bool | None:
        """Whether an automatic retry is allowed. `None` means UNKNOWN — never "yes".

        This is the direction the mistake would run in. A row whose outcome nobody recorded, or
        recorded as something outside the machine, says nothing about whether the post went out.
        Rendering that silence as permission is the same move as a gate announcing `GO` with no
        measurement; absence of evidence is `NIE_URUCHOMIONO` (KONWENCJE.md).
        """
        state = self.execution_attempt_state
        if state is None:
            return None
        return may_retry_automatically(state)


@dataclass(frozen=True)
class JobView:
    """One row of `jobs`, plus two counts the screen cannot compute for itself."""

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
    attempt_count: int
    #: A lease that is `ACTIVE` **and** not past `expires_at`, judged by PostgreSQL's clock. A
    #: count of `ACTIVE` rows alone would call an abandoned lease live.
    holds_live_lease: bool


@dataclass(frozen=True)
class JobDetail:
    """A job together with the full history of its attempts, newest numbering last."""

    job: JobView
    attempts: tuple[JobAttemptView, ...]
