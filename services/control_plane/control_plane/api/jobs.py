"""`GET /api/jobs` and `GET /api/jobs/{job_id}` — the queue as the operator sees it (L5192-5194).

D-013 settles what this screen is: F1 has no producer of real content, so the rows come from the
development path, and that path "nie wolno nazywać danymi produkcyjnymi". What the screen must
nevertheless be is HONEST. It reads the canonical store, so that "restart komponentu nie gubi
taska" (L5212) can be demonstrated on it rather than asserted about it, and it shows an empty
state when there is nothing rather than anything that looks like a job.

The precedent NOT to repeat is in this repository: `TicketRegistry` in
`services/stream_gateway/stream_gateway/tickets.py` keeps its records in a `dict` and its own
docstring calls that an F0 shortcut. Every one of its tests passes and the data is gone after a
restart. `tests/test_jobs_api.py` therefore inserts a row with raw SQL — through no code of this
package — and demands to see it here.

FOUR THINGS THAT ARE DECIDED HERE.

1.  **The scope is the session's organization, never a query parameter.** Same rule, same words
    as `api/audit.py`: the query string is not an identity. A caller who asks for another
    organization's jobs gets their own, and a job id from another organization is a 404 rather
    than a row — "not yours" and "not there" must read the same, or the 404 becomes an oracle.

2.  **The route reaches the queue through `domain.commands`, never through the store.** The
    domain layer is the checked entrance (L5206); a router importing `JobStore` would be the
    second one, and the second entrance is the one that stops being checked.

3.  **`may_retry_automatically` is computed in ONE place**, and that place is
    `contracts.execution.may_retry_automatically`, reached through `JobAttemptView`. No copy of
    the condition lives here. After `SUBMIT_STARTED` the side effect may already have happened
    on the platform (L2673, L4560, L5335), and two copies of that rule are two chances for one
    of them to say "retry".

4.  **An unrecognised `failure_class` renders the flag as `null`, never as `true`.** An attempt
    whose outcome nobody recorded says nothing about whether a post went out, and silence must
    not be rendered as permission — absence of evidence is `NIE_URUCHOMIONO`, not `GO`
    (KONWENCJE.md).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.domain import commands
from control_plane.domain.models import JobAttemptView, JobDetail, JobView
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

router = APIRouter(prefix="/api", tags=["joby"])

#: The full paths, exported so tests do not spell them a second time. A test carrying its own copy
#: of a path keeps passing after the route is renamed.
JOBS_PATH = "/api/jobs"
JOB_DETAIL_PATH = "/api/jobs/{job_id}"

#: How many jobs one page carries by default, and the ceiling a caller may ask for. Reported in
#: the response together with `truncated`, so a page that ends says "there may be more" instead
#: of "this is everything" — the shape `api/audit.py` uses for the same reason.
DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000


async def job_queue(request: Request) -> JobQueuePort:
    """The port these routes talk to. Overridable in tests, wired to PostgreSQL in production.

    Built with no organization: this instance is READ-ONLY by construction, so a bug in a future
    handler cannot claim or close anything through it — `PostgresJobTransport` refuses every
    writing method when it has no organization. The scope of each read is passed in from the
    session at the call site.

    The pool comes from `auth.pool_for`, which is the one place a pool is created in this
    process. A second pool builder would mean the test suite points one of them at the throwaway
    PostgreSQL and the other at whatever `DATABASE_URL` happens to say.
    """
    pool = await pool_for(request.app)
    return PostgresJobTransport(JobStore(pool))


def _job_payload(job: JobView) -> dict[str, Any]:
    """One job row as JSON. Field names in English (D-010); operator messages stay Polish."""
    return {
        "job_id": str(job.job_id),
        "organization_id": str(job.organization_id),
        "workspace_id": str(job.workspace_id) if job.workspace_id else None,
        "job_type": job.job_type,
        "routing_key": job.routing_key,
        "priority": job.priority,
        "payload_ref": job.payload_ref,
        "dispatch_version": job.dispatch_version,
        "status": job.status,
        "not_before": job.not_before.isoformat(),
        "attempt_limit": job.attempt_limit,
        "idempotency_key": job.idempotency_key,
        "lock_version": job.lock_version,
        "created_at": job.created_at.isoformat(),
        "attempt_count": job.attempt_count,
        "holds_live_lease": job.holds_live_lease,
    }


def _attempt_payload(attempt: JobAttemptView) -> dict[str, Any]:
    """One attempt as JSON, including the two DERIVED fields.

    `execution_attempt_state` and `may_retry_automatically` are read off `JobAttemptView`, which
    computes them from the contracts. This function must never grow an `if` about retries.
    """
    state = attempt.execution_attempt_state
    return {
        "attempt_id": str(attempt.attempt_id),
        "attempt_no": attempt.attempt_no,
        "dispatch_version": attempt.dispatch_version,
        "lease_generation": attempt.lease_generation,
        "lease_owner": str(attempt.lease_owner),
        "status": attempt.status,
        "not_before": attempt.not_before.isoformat() if attempt.not_before else None,
        "heartbeat_at": attempt.heartbeat_at.isoformat() if attempt.heartbeat_at else None,
        "expires_at": attempt.expires_at.isoformat() if attempt.expires_at else None,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "failure_class": attempt.failure_class,
        "execution_attempt_state": state.value if state is not None else None,
        # `None` means UNKNOWN. It is not the same as `false`, and it is very much not `true`.
        "may_retry_automatically": attempt.may_retry_automatically,
    }


# `Annotated[..., Depends(...)]` rather than `= Depends(...)`: a call in a default argument is
# flagged by the linter (B008), and the sibling routers of this wave carry that finding. The
# behaviour is identical and this spelling is the one FastAPI documents.
@router.get("/jobs")
async def list_jobs(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    port: Annotated[JobQueuePort, Depends(job_queue)],
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Jobs of the session's organization, newest first.

    Empty means empty — an empty list and a `count` of zero, never a placeholder row. D-013 is
    explicit: "Ekran pusty przy braku danych ma pokazywać stan pusty, nigdy atrapy."
    """
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise HTTPException(
            status_code=400, detail=f"limit={limit} poza zakresem 1..{MAX_PAGE_LIMIT}"
        )

    jobs = await commands.list_jobs(port, organization_id=operator.organization_id, limit=limit)
    return {
        "jobs": [_job_payload(job) for job in jobs],
        "limit": limit,
        "count": len(jobs),
        "truncated": len(jobs) == limit,
    }


@router.get("/jobs/{job_id}")
async def read_job(
    job_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    port: Annotated[JobQueuePort, Depends(job_queue)],
) -> dict[str, Any]:
    """One job with the full history of its attempts.

    404 when there is no such job in the session's organization. Not an empty envelope: a job
    nobody wrote and a job with no attempts are different facts, and only one of them is a
    record.
    """
    detail: JobDetail | None = await commands.read_job(
        port, job_id, organization_id=operator.organization_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=f"nie ma joba {job_id}")

    return {
        "job": _job_payload(detail.job),
        "attempts": [_attempt_payload(attempt) for attempt in detail.attempts],
        "attempt_limit": detail.job.attempt_limit,
        "attempts_used": len(detail.attempts),
    }
