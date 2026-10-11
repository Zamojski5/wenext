"""Job queue contracts — the transport boundary of phase E1.F1 (L5202, L5208).

Phase E1.F1 keeps jobs in the relational store and dispatches them in the same process on
`SELECT ... FOR UPDATE SKIP LOCKED`. A broker arrives in E3 and carries nothing but a `job_id`.
The deferral of that broker is admissible ONLY because the dispatcher hides behind a port:

    "Odroczenie jest ważne tylko dlatego, że oba elementy są schowane za portami: dispatcher
     za `JobTransportPort` […]. Dołożenie brokera i RLS w E3 nie może zmienić komend domenowych
     ani schematu." (L5206)

and the Definition of Done says the same thing as a test:

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

Hence the two rules this module lives by, and neither is negotiable:

1. **No driver here.** This module must not import, mention or hint at any database driver.
   The moment a domain command can reach a connection through this port, swapping the transport
   stops being a swap. `tests/test_job_contracts.py` scans this file's AST and its text.
2. **A Protocol, not an ABC.** A structural type does not force the E3 transport to inherit from
   the contracts package. The same choice was already made for `BrowserBackend` in the sidecar.

The fencing token is separate from `browser.FencingToken` on purpose. That one is bound to
`profile_id` and closed with `extra="forbid"`, so it cannot carry a job at all — reusing it
would have meant loosening a token whose whole job is to be rigid (L2375, L5178).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StaleJobFencingToken(Exception):
    """Raised when a worker presents a lease generation that is no longer current.

    The queue counterpart of `StaleFencingToken` (L2377): the lease is authoritative in the
    canonical store, the generation grows on every takeover, and a worker whose lease was
    reaped must not finish its work as if nothing had happened — that is exactly the
    "two workers take over the same record" failure the DoD forbids (L5211).
    """

    def __init__(self, *, expected: JobFencingToken, presented: JobFencingToken) -> None:
        super().__init__(
            f"Odrzucony fencing token joba {presented.job_id}: "
            f"aktualny lease to job {expected.job_id}, próba {expected.attempt_id}, "
            f"generacja {expected.lease_generation}, właściciel {expected.lease_owner}; "
            f"przedstawiono próbę {presented.attempt_id}, generację {presented.lease_generation}, "
            f"właściciela {presented.lease_owner}."
        )
        self.expected = expected
        self.presented = presented


class JobFencingToken(BaseModel):
    """The token that MUST accompany every effect performed on behalf of a leased job.

    Four fields and no more. Each one answers a different question, which is why the test names
    them one by one instead of counting them:

      * `job_id`           — which record,
      * `attempt_id`       — which attempt on that record (the attempt limit counts these, L5214),
      * `lease_generation` — which takeover; grows monotonically, never decreases,
      * `lease_owner`      — which runner believes it holds the lease.

    Frozen, because a token that can be edited after being handed out fences nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    attempt_id: UUID
    #: Counts from 1 like the profile lease generation (L2372); 0 would mean "no lease".
    lease_generation: int = Field(ge=1)
    lease_owner: UUID


def require_fresh_job_lease(*, current: JobFencingToken, presented: JobFencingToken) -> None:
    """Rejects a call made under a stale lease. The only allowed way to check a job token.

    Compares the whole token, not just the generation: a current generation belonging to
    ANOTHER job or attempt is not freshness, it is a mix-up. `expected` has to be read from the
    canonical store by the caller — this function deliberately cannot read anything itself, so it
    can never confirm a lease against the worker's own memory.
    """
    if (
        current.job_id != presented.job_id
        or current.attempt_id != presented.attempt_id
        or current.lease_generation != presented.lease_generation
        or current.lease_owner != presented.lease_owner
    ):
        raise StaleJobFencingToken(expected=current, presented=presented)


#: The port's surface, kept as data so that a contract test can enumerate it without importing
#: any implementation. The same pattern as `ADAPTER_CONTRACT_METHODS` (L2551-2561).
JOB_TRANSPORT_PORT_METHODS: tuple[str, ...] = (
    "publish_ready",
    "reserve",
    "acknowledge",
    "requeue",
)


@runtime_checkable
class JobTransportPort(Protocol):
    """The seam between the domain and whatever moves job identifiers around (L5206, L5216).

    The surface is deliberately poor and speaks only in identifiers and tokens. There is no
    method here that returns a payload, a connection or a cursor — a domain command that could
    reach the storage through this port would make the E3 swap untestable.

    `runtime_checkable` buys exactly one thing: `isinstance` sees whether the methods exist. It
    does NOT check signatures, so the contract test also asserts the names from
    `JOB_TRANSPORT_PORT_METHODS`.
    """

    def publish_ready(self, job_id: UUID, *, not_before: datetime | None = None) -> None:
        """Announces that a job is ready to be dispatched, optionally not before a moment.

        `not_before` is how the backoff schedule from D-017 reaches the transport; the schedule
        itself belongs to the control plane, never here.
        """
        ...

    def reserve(
        self, *, owner: UUID, limit: int, lease_seconds: int
    ) -> tuple[JobFencingToken, ...]:
        """Takes at most `limit` jobs for `owner` and returns a lease token for each.

        Returns tokens, not jobs: the record itself is read by the domain from the canonical
        store. Two callers must never receive a token for the same `job_id` — that is the DoD
        line "dwa workery nie przejmują tego samego rekordu" (L5211).
        """
        ...

    def acknowledge(self, token: JobFencingToken) -> None:
        """Closes the attempt held under this token. A stale token must be refused."""
        ...

    def requeue(self, token: JobFencingToken, *, not_before: datetime) -> None:
        """Hands the job back for a later attempt. A stale token must be refused.

        Whether one more attempt is allowed at all is decided by the attempt limit on the domain
        side (L5214) — the transport never invents an extra attempt.
        """
        ...
