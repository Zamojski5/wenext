"""The port the domain commands speak to — and the only thing they are allowed to speak to.

    "Odroczenie jest ważne tylko dlatego, że oba elementy są schowane za portami: dispatcher za
     `JobTransportPort`, dostęp do danych za workspace-scoped Domain Data Gateway. Dołożenie
     brokera i RLS w E3 nie może zmienić komend domenowych ani schematu." (L5206)

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

`JobQueuePort` is the union of the two seams that sentence names: the four transport methods from
`contracts.JobTransportPort`, plus the queue's slice of the Domain Data Gateway. E3 splits them —
the broker takes the first half, RLS lands under the second — and the split changes nothing here,
because a command can only ever call a method on this object. It has no other way in.

**Why the methods are `async` here while the contracts declare them `def`.** The contracts port
was written with synchronous signatures. In this stack there is no synchronous implementation to
be had: the control plane is FastAPI over an async driver, so a blocking `reserve()` would block
the event loop that serves the API. The four inherited methods are therefore re-declared as
coroutines. `contracts/jobs.py` belongs to another package and is not edited from here; the
divergence is reported rather than papered over, and it costs nothing at runtime —
`runtime_checkable` compares the PRESENCE of names, which is why the contracts' own test asserts
the names from `JOB_TRANSPORT_PORT_METHODS` separately.

The surface stays poor on purpose. Nothing here returns a connection, a cursor, a session or a
row object; the richest thing that crosses this boundary is a frozen dataclass from
`domain/models.py`. `contracts/tests/test_job_contracts.py` scans the contracts port's own
signatures for exactly those words, and the same rule applies to this extension of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from community_os_contracts import JobFencingToken, JobTransportPort, JobWaitingState

from .models import ClaimBatch, EnqueuedJob, JobDetail, JobOutcome, JobView

#: The methods this port adds on top of `JOB_TRANSPORT_PORT_METHODS`, kept as data so a contract
#: test can enumerate them without importing an implementation — the same pattern the contracts
#: use for the transport half.
JOB_QUEUE_PORT_METHODS: tuple[str, ...] = (
    "create_job",
    "claim",
    "close_attempt",
    "park_until_ready",
    "fetch_job",
    "fetch_jobs",
)


@runtime_checkable
class JobQueuePort(JobTransportPort, Protocol):
    """Everything the job commands of E1.F1 need, and nothing that reveals the storage."""

    # -- the transport half, re-declared as coroutines (see the module docstring) -------------

    async def publish_ready(  # type: ignore[override]
        self, job_id: UUID, *, not_before: datetime | None = None
    ) -> None:
        """Announce that an existing job is dispatchable again (L2147)."""
        ...

    async def reserve(  # type: ignore[override]
        self, *, owner: UUID, limit: int, lease_seconds: float
    ) -> tuple[JobFencingToken, ...]:
        """Take at most `limit` jobs and return one lease token for each."""
        ...

    async def acknowledge(self, token: JobFencingToken) -> None:  # type: ignore[override]
        """Close the attempt held under this token as a success. A stale token is refused."""
        ...

    async def requeue(  # type: ignore[override]
        self, token: JobFencingToken, *, not_before: datetime
    ) -> None:
        """Hand the job back for a later attempt. A stale token is refused."""
        ...

    # -- the queue's slice of the Domain Data Gateway ------------------------------------------

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
        """Write the canonical row. Re-using an idempotency key adds nothing (L2820)."""
        ...

    async def claim(
        self,
        *,
        worker: UUID,
        limit: int,
        lease_seconds: float,
        job_types: Sequence[str] | None = None,
        exclude_job_types: Sequence[str] | None = None,
    ) -> ClaimBatch:
        """Claim a batch and report the backlog the claim's own snapshot saw.

        `job_types` / `exclude_job_types` narrow the claim to one LANE of the queue (see
        `control_plane.jobs.lanes`). Both `None` is the whole queue, as before lanes existed.
        """
        ...

    async def close_attempt(
        self,
        token: JobFencingToken,
        *,
        outcome: JobOutcome,
        failure_class: str | None = None,
    ) -> None:
        """Terminalize the job under this token. Fenced on the full tuple (L2666)."""
        ...

    async def park_until_ready(
        self,
        token: JobFencingToken,
        *,
        waiting_state: JobWaitingState,
        delay_seconds: float,
    ) -> None:
        """Put the job in a `WAITING_*` state AND give the lease back (L2147, L5288)."""
        ...

    async def fetch_job(self, job_id: UUID, *, organization_id: UUID) -> JobDetail | None:
        """One job of this organization with its attempt history, or `None`."""
        ...

    async def fetch_jobs(self, *, organization_id: UUID, limit: int = 100) -> tuple[JobView, ...]:
        """The most recent jobs of this organization, newest first.

        The scope is a REQUIRED argument on both readers. A scope that can be left out
        eventually is — and `api/audit.py` names the rule the routes follow: the identity comes
        from the session, never from the query string.
        """
        ...
