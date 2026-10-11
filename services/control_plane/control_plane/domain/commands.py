"""The domain commands of the job queue. The ONLY way in for `api/` and for the dev CLI.

Every function here takes a `JobQueuePort` as its first argument and nothing that resembles a
database. That is the requirement, not the taste:

    "Odroczenie [RabbitMQ i RLS] jest ważne tylko dlatego, że oba elementy są schowane za
     portami […]. Dołożenie brokera i RLS w E3 nie może zmienić komend domenowych ani
     schematu." (L5206)

and its Definition of Done:

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

So: the DoD grep for the driver's name over this package comes back empty — which is why the
name appears in none of these docstrings — and
`tests/test_jobs_api.py::test_domain_commands_run_against_a_transport_that_is_not_a_database`
runs all four of these against an in-memory object with no PostgreSQL anywhere near it. If that
test ever needs a database, this layer has leaked.

WHAT THESE COMMANDS DELIBERATELY DO NOT DECIDE. The attempt limit. `report_result` closes a job
as `SUCCEEDED` or `FAILED`; it never quietly grants one more try, and there is no `RETRY`
outcome. "lease expiry respektuje attempt limit; ostatnia próba terminalizuje job zamiast
tworzyć nieskończony redispatch" (L5214) belongs to the reaper, which compares `attempt_no` with
`attempt_limit` in one place. A second comparison here would be a second answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from community_os_contracts import JobFencingToken, JobWaitingState

from .models import ClaimBatch, EnqueuedJob, JobDetail, JobOutcome, JobView
from .ports import JobQueuePort


async def enqueue_job(
    port: JobQueuePort,
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
    """Put one job in the queue.

    `attempt_limit` is a REQUIRED argument with no default, and that is D-017 being obeyed rather
    than quoted. The value 5 is frozen in that decision and lives in one place; a default here
    would be a second copy of the threshold, and a threshold in two places drifts without
    warning. Migration 0003 makes the same choice — the column has `NOT NULL` and no `DEFAULT`.

    `delay_seconds` is how the backoff schedule reaches the store, as a DURATION rather than a
    moment: the deadline itself is computed by PostgreSQL as `now() + …`, because a `not_before`
    computed from this process's clock is wrong by however much the two clocks disagree (L2668).
    """
    if attempt_limit < 1:
        raise ValueError(
            f"attempt_limit = {attempt_limit}: job, który nie ma prawa do ani jednej próby, "
            "nigdy się nie wykona i nigdy nie zterminalizuje"
        )
    if delay_seconds < 0:
        raise ValueError(
            f"delay_seconds = {delay_seconds}: opóźnienie w przeszłość znaczyłoby, że job jest "
            "gotowy wcześniej, niż powstał"
        )
    return await port.create_job(
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


async def claim_batch(
    port: JobQueuePort,
    *,
    worker: UUID,
    limit: int,
    lease_seconds: float,
    job_types: Sequence[str] | None = None,
    exclude_job_types: Sequence[str] | None = None,
) -> ClaimBatch:
    """Claim up to `limit` jobs for `worker`.

    `limit` must be positive. A claim for zero jobs would come back empty and its emptiness
    would be indistinguishable from a drained queue — the caller would then read
    `BatchEmptiness.QUEUE_EMPTY` off a request that asked for nothing. Refusing here keeps that
    particular lie from being expressible.
    """
    if limit < 1:
        raise ValueError(
            f"limit = {limit}: claim o zerowej wielkości wraca pusty i wygląda dokładnie jak "
            "pusta kolejka — a to są dwie różne rzeczy i cała reszta tego modułu je rozdziela"
        )
    if lease_seconds <= 0:
        raise ValueError(
            f"lease_seconds = {lease_seconds}: lease wygasły w chwili powstania reaper odbiera "
            "natychmiast, czyli redispatch bez końca zamiast limitu prób (L5214). Baza odrzuca "
            "to zresztą CHECK-iem expires_at > acquired_at"
        )
    if job_types is not None and not job_types:
        raise ValueError(
            "pusta lista typów jobów: tor bez żadnego typu nie weźmie niczego, a jego pustka "
            "wyglądałaby jak pusta kolejka"
        )
    if job_types is None and exclude_job_types is None:
        # The whole queue: the call every port has always answered, lanes or not.
        return await port.claim(worker=worker, limit=limit, lease_seconds=lease_seconds)
    return await port.claim(
        worker=worker,
        limit=limit,
        lease_seconds=lease_seconds,
        job_types=job_types,
        exclude_job_types=exclude_job_types,
    )


async def report_result(
    port: JobQueuePort,
    *,
    token: JobFencingToken,
    outcome: JobOutcome,
    failure_class: str | None = None,
) -> None:
    """Close the attempt this token holds, terminalizing the job.

    Refuses with `JobFencingRefused` when the token no longer holds the job — the write of a
    worker whose lease was reaped must not land (L2668). The refusal comes from the store, which
    is the only layer that can count the rows a CAS matched.

    A FAILED outcome without a `failure_class` is rejected. The column is the only place the
    reaper and the operator's screen can read WHY an attempt ended, and an attempt that failed
    for no recorded reason is the row nobody can act on later.
    """
    if outcome is JobOutcome.FAILED and not failure_class:
        raise ValueError(
            "porażka bez failure_class — próba zamknięta bez zapisanego powodu nie mówi ani "
            "reaperowi, ani operatorowi, co się stało"
        )
    await port.close_attempt(token, outcome=outcome, failure_class=failure_class)


async def release_on_waiting(
    port: JobQueuePort,
    *,
    token: JobFencingToken,
    waiting_state: JobWaitingState,
    delay_seconds: float,
) -> None:
    """Park the job in a `WAITING_*` state and GIVE THE LEASE BACK.

    "Joby w stanach `WAITING_*` nie trzymają lease'u runtime'u; przejście do `READY` emituje
     event, który atomowo tworzy nową wersję dispatchu dla oczekujących jobów." (L2147)

    The rule is already in the repository as data — `WAITING_HOLDS_NO_LEASE` in
    `services/codex_runtime/codex_runtime/jobs.py`, `frozenset(JobWaitingState)`, with a comment
    saying F1 would be the phase that checks it. This is that check on the queue side.

    Why the release is not optional: a job waiting for the operator to re-authenticate waits for
    as long as the operator takes. A lease held across that wait either expires — and then the
    reaper burns an attempt against the limit of L5214 for a job that never ran — or it does not,
    and the job occupies the one active-lease slot `job_leases_one_active_per_job` allows while
    doing nothing at all.

    `delay_seconds` must be positive. A job parked with `not_before` in the past is immediately
    dispatchable again, which turns waiting for an external condition into a loop with no pauses.
    """
    if delay_seconds <= 0:
        raise ValueError(
            f"delay_seconds = {delay_seconds}: job wstrzymany bez odroczenia jest natychmiast "
            "redispatchowalny, czyli nie czeka na nic — tylko kręci się w pętli"
        )
    await port.park_until_ready(token, waiting_state=waiting_state, delay_seconds=delay_seconds)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
# These two are queries rather than commands, and they are here anyway: `api/` reaches the store
# THROUGH this module and through no other route. A router importing the transport directly would
# be the second entrance, and the second entrance is the one that stops being checked.


async def read_job(port: JobQueuePort, job_id: UUID, *, organization_id: UUID) -> JobDetail | None:
    """One job with the full history of its attempts. `None` when there is no such job HERE.

    `None` rather than an empty `JobDetail`: a job nobody wrote and a job with no attempts are
    different facts, and the API turns the first into a 404 instead of rendering an empty shell
    that looks like a record. A job of another organization is also `None` — "not yours" and
    "not there" have to read the same from outside, or the 404 becomes an existence oracle.
    """
    return await port.fetch_job(job_id, organization_id=organization_id)


async def list_jobs(
    port: JobQueuePort, *, organization_id: UUID, limit: int = 100
) -> tuple[JobView, ...]:
    """The most recent jobs of one organization, newest first."""
    if limit < 1:
        raise ValueError(f"limit = {limit}: lista o zerowej długości nie jest odpowiedzią")
    return await port.fetch_jobs(organization_id=organization_id, limit=limit)
