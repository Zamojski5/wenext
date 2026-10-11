"""`POST /api/jobs/waiting/resume` — bring back the jobs a handler parked with `WaitFor`.

A job in `WAITING_AUTH` (the model is not logged in) or `WAITING_CAPABILITY` (no adapter, no
credentials, no user profile) waits for a human. When the human has acted — logged Codex in,
saved a profile, added Reddit credentials — this is how the queue learns about it. Nothing
resumes such a job on its own, and that is deliberate: a job that retried "log in" every
minute would exhaust its attempt limit (`park_until_ready` closes an attempt each time).

`WAITING_RETRY` is NOT touched: it belongs to the reaper's replacement attempts and the
dispatcher activates those itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from community_os_contracts import JobWaitingState
from fastapi import APIRouter, Depends, Request

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.domain import commands
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.handlers import JOB_JUDGE, JOB_PICTURE, JOB_POLL, JOB_SNAPSHOT, JOB_VOICE
from control_plane.pilot.handlers import PAUSED_JOB_TYPES
from control_plane.pilot.store import PilotStore

router = APIRouter(prefix="/api", tags=["joby"])

RESUME_WAITING_PATH = "/api/jobs/waiting/resume"

#: The two states a human unblocks. Written out rather than derived as "all but WAITING_RETRY",
#: so a fifth waiting state added to the contracts is a conscious decision here, not a default.
HUMAN_UNBLOCKED_STATES: frozenset[str] = frozenset(
    {JobWaitingState.WAITING_AUTH.value, JobWaitingState.WAITING_CAPABILITY.value}
)

#: Only jobs this process can actually run are worth waking. Measured 2026-09-04 on the founder's
#: dev database: 15 030 F1 `soak`/`bench`/`demo_e2e` jobs parked in WAITING_CAPABILITY because no
#: handler exists for them — a resume that woke them would let the dispatcher claim a thousand,
#: park each one again ("brak handlera") and burn an attempt per job for nothing. The four observe
#: job types are the ones a human's action (login, profile, credentials) can unblock.
HANDLED_JOB_TYPES: frozenset[str] = frozenset(
    {JOB_POLL, JOB_SNAPSHOT, JOB_PICTURE, JOB_JUDGE, JOB_VOICE, "pilot.cycle"}
)

#: How many jobs one call may resume. The queue of one operator never approaches this.
SCAN_LIMIT = 1000


async def resume_human_unblocked(pool, operator: OperatorSession) -> dict[str, Any]:
    """Publish every `WAITING_AUTH` / `WAITING_CAPABILITY` job as READY and audit the count.

    Shared by this route and by the capsule card's "done" (`api/capsule.py`, D-026): a human who
    just logged the capsule in has unblocked exactly the jobs a missing login parked, and the two
    entry points must resume them the same way — one function, one audit kind.
    """
    port = PostgresJobTransport(JobStore(pool), organization_id=operator.organization_id)
    jobs = await commands.list_jobs(
        port, organization_id=operator.organization_id, limit=SCAN_LIMIT
    )
    parked = [job for job in jobs if job.status in HUMAN_UNBLOCKED_STATES]
    waiting = [job for job in parked if job.job_type in HANDLED_JOB_TYPES]
    skipped_without_handler = len(parked) - len(waiting)
    deferred = await deferred_for_pilot(
        pool, waiting, organization_id=operator.organization_id, now=datetime.now(UTC)
    )
    resumed = []
    for job in waiting:
        if job.job_id in deferred:
            continue
        await port.publish_ready(job.job_id)
        resumed.append(str(job.job_id))
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="WAITING_JOBS_RESUMED",
            organization_id=operator.organization_id,
            subject_ref=f"organization:{operator.organization_id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"count": len(resumed)},
        )
    return {
        "resumed": len(resumed),
        "job_ids": resumed,
        "scanned": len(jobs),
        "skipped_without_handler": skipped_without_handler,
        "deferred_for_pilot": len(deferred),
    }


async def deferred_for_pilot(
    pool, jobs: Sequence[Any], *, organization_id: UUID, now: datetime
) -> set[UUID]:
    """Job ids of judge / picture / voice jobs whose community has an ACTIVE 24 h pilot. They stay
    parked: waking them would spend model calls outside the pilot's budget and double the pilot's
    turns (docs/START-PILOTA-24H.md, point 3). Resume them after the run ends."""
    paused = await PilotStore(pool).paused_community_ids(organization_id=organization_id, now=now)
    if not paused:
        return set()
    deferred: set[UUID] = set()
    thread_ids = [
        UUID(job.payload_ref.split("/", 1)[1])
        for job in jobs
        if job.job_type == JOB_JUDGE and job.payload_ref and job.payload_ref.startswith("thread/")
    ]
    thread_community: dict[UUID, UUID] = {}
    if thread_ids:
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT id, community_id FROM observed_threads "
                "WHERE organization_id = $1 AND id = ANY($2::uuid[])",
                organization_id,
                thread_ids,
            )
        thread_community = {r["id"]: r["community_id"] for r in rows}
    for job in jobs:
        if job.job_type not in PAUSED_JOB_TYPES or not job.payload_ref:
            continue
        kind, _, raw = job.payload_ref.partition("/")
        try:
            ref_id = UUID(raw)
        except ValueError:
            continue
        community_id = thread_community.get(ref_id) if kind == "thread" else ref_id
        if community_id in paused:
            deferred.add(job.job_id)
    return deferred


@router.post("/jobs/waiting/resume")
async def resume_waiting_jobs(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    pool = await pool_for(request.app)
    return await resume_human_unblocked(pool, operator)
