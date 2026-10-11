"""`JobQueuePort` over PostgreSQL. The transport E3 is allowed to replace — and nothing else.

    "Odroczenie [RabbitMQ] jest ważne tylko dlatego, że […] dispatcher [jest schowany] za
     `JobTransportPort`. Dołożenie brokera […] w E3 nie może zmienić komend domenowych ani
     schematu." (L5206)

This class is written NOW, behind the port, rather than fitted with a port later. A port bolted on
after the fact satisfies the DoD "zmiana transportu jobów nie dotyka żadnej komendy domenowej"
(L5216) only over a directory arranged to make that test pass: the commands would already be
written against whatever the store happened to offer, and the seam would be a rename.

The class is thin on purpose. Every SQL statement lives in `store.py`; what happens here is
translation between the port's vocabulary and the store's. Anything else — a retry, a default, a
threshold — would be logic in two places.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from community_os_contracts import JobFencingToken, JobWaitingState

from control_plane.domain.models import (
    ClaimBatch,
    EnqueuedJob,
    JobDetail,
    JobOutcome,
    JobView,
)
from control_plane.jobs.store import JobStore


class PostgresJobTransport:
    """The `JobQueuePort` implementation of phase E1.F1.

    Scoped to one organization at construction rather than per call, which is the shape of the
    "workspace-scoped Domain Data Gateway" L5206 names: a caller cannot claim across tenants by
    passing a different argument, because there is no argument to pass. E1 has exactly one
    organization (D-016), so this costs nothing today and is the right shape for the day RLS
    lands in E3.

    `organization_id` may be `None` for a read-only instance — the API builds one to serve
    `GET /api/jobs` — and then every writing method refuses. Refusing beats defaulting to "some
    organization": a claim run against the wrong tenant is not an error anybody would notice.

    THIS CLASS IS `async` WHILE `contracts.JobTransportPort` DECLARES ITS METHODS `def`. The
    contracts port was written with synchronous signatures and belongs to another package; in
    this stack there is no synchronous implementation to be had, because the control plane is
    FastAPI over `asyncpg` and a blocking `reserve()` would block the loop that serves the API.
    `runtime_checkable` compares the presence of names, so `isinstance` still holds — the
    contracts' own test says as much and checks the names separately. The divergence is reported
    to whoever owns `contracts/jobs.py`, not smoothed over here.
    """

    def __init__(self, store: JobStore, *, organization_id: UUID | None = None) -> None:
        self._store = store
        self._organization_id = organization_id

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
        """Announce that a waiting job is dispatchable again (L2147)."""
        await self._store.publish_ready(job_id, not_before=not_before)

    async def reserve(
        self, *, owner: UUID, limit: int, lease_seconds: float
    ) -> tuple[JobFencingToken, ...]:
        """Take at most `limit` jobs and return one lease token each.

        Tokens only, exactly as the contracts describe the method: "Returns tokens, not jobs".
        Callers that need the record itself use `claim`, which is the Domain Data Gateway half of
        this port and returns the row the claim actually wrote.
        """
        batch = await self._store.claim_batch(
            organization_id=self._writing_organization(),
            worker=owner,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        return tuple(claim.fencing_token for claim in batch.claims)

    async def acknowledge(self, token: JobFencingToken) -> None:
        """Close the attempt under this token as a success. A stale token is refused."""
        await self._store.close_attempt(token, outcome=JobOutcome.SUCCEEDED)

    async def requeue(self, token: JobFencingToken, *, not_before: datetime) -> None:
        """Hand the job back for a later attempt. A stale token is refused.

        The moment is passed to the database as an absolute timestamp and clamped there with
        `greatest(…, now())`. It is NOT converted into a delay here: subtracting this process's
        clock from the caller's moment would put a second clock into a path whose whole point is
        that only PostgreSQL's clock counts (L2668).
        """
        await self._store.requeue(token, not_before=not_before)

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
        """Write the canonical row. A repeated idempotency key adds nothing (L2820)."""
        return await self._store.enqueue(
            organization_id=organization_id,
            workspace_id=workspace_id,
            job_type=job_type,
            routing_key=routing_key,
            priority=priority,
            attempt_limit=attempt_limit,
            idempotency_key=idempotency_key,
            payload_ref=payload_ref,
            delay_seconds=delay_seconds,
        )

    async def claim(
        self,
        *,
        worker: UUID,
        limit: int,
        lease_seconds: float,
        job_types: Sequence[str] | None = None,
        exclude_job_types: Sequence[str] | None = None,
    ) -> ClaimBatch:
        """Claim a batch, together with the backlog the claim's own snapshot saw."""
        return await self._store.claim_batch(
            organization_id=self._writing_organization(),
            worker=worker,
            limit=limit,
            lease_seconds=lease_seconds,
            job_types=job_types,
            exclude_job_types=exclude_job_types,
        )

    async def close_attempt(
        self,
        token: JobFencingToken,
        *,
        outcome: JobOutcome,
        failure_class: str | None = None,
    ) -> None:
        """Terminalize the job under this token. CAS on the full tuple (L2666)."""
        await self._store.close_attempt(token, outcome=outcome, failure_class=failure_class)

    async def park_until_ready(
        self,
        token: JobFencingToken,
        *,
        waiting_state: JobWaitingState,
        delay_seconds: float,
    ) -> None:
        """Park the job in a `WAITING_*` state and give the lease back (L2147, L5288)."""
        await self._store.park_until_ready(
            token, waiting_state=waiting_state, delay_seconds=delay_seconds
        )

    async def fetch_job(self, job_id: UUID, *, organization_id: UUID) -> JobDetail | None:
        """One job of this organization with its attempt history, or `None`."""
        return await self._store.fetch_job(job_id, organization_id=organization_id)

    async def fetch_jobs(self, *, organization_id: UUID, limit: int = 100) -> tuple[JobView, ...]:
        """The most recent jobs of this organization, newest first.

        The scope comes from the caller and is required, NOT from `self._organization_id`. The
        routes take it from the operator's session; falling back to the instance's organization
        would let a read-only transport built with one silently answer a differently scoped
        question.
        """
        return await self._store.fetch_jobs(organization_id=organization_id, limit=limit)
