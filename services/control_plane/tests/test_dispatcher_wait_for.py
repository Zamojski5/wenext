"""`WaitFor` — a handler that says "not now" parks its job instead of failing it.

Against PostgreSQL, through the real dispatcher: the job ends in the named `WAITING_*` state,
its lease is RELEASED (L2147), the attempt is closed, and `publish_ready` brings it back.
"""

from __future__ import annotations

import pytest
from community_os_contracts import JobWaitingState
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.backoff import ATTEMPT_LIMIT
from control_plane.jobs.dispatcher import JobDispatcher, WaitFor
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

pytestmark = pytest.mark.db

JOB_TYPE = "probe.wait_for"


def test_wait_for_refuses_the_reapers_state_and_an_empty_reason() -> None:
    with pytest.raises(ValueError, match="WAITING_RETRY"):
        WaitFor(JobWaitingState.WAITING_RETRY, "x")
    with pytest.raises(ValueError, match="powodu"):
        WaitFor(JobWaitingState.WAITING_AUTH, "   ")
    wait = WaitFor(JobWaitingState.WAITING_AUTH, "zaloguj Codexa")
    assert wait.waiting_state is JobWaitingState.WAITING_AUTH
    assert "zaloguj" in str(wait)


async def test_a_handler_raising_wait_for_parks_the_job_and_releases_the_lease(
    pool, tenant
) -> None:
    port = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    enqueued = await commands.enqueue_job(
        port,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_TYPE,
        routing_key="probe",
        priority=1,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key="probe:wait-for",
    )

    async def needs_login(claim: ClaimedJob) -> None:
        raise WaitFor(JobWaitingState.WAITING_AUTH, "Codex nie jest zalogowany")

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={JOB_TYPE: needs_login},
        lease_seconds=10.0,
        heartbeat_seconds=2.0,
    )
    report = await dispatcher.tick()
    assert report.claimed == 1
    assert report.unhandled == 1 and report.failed == 0 and report.succeeded == 0

    async with pool.acquire() as connection:
        status = await connection.fetchval("SELECT status FROM jobs WHERE id = $1", enqueued.job_id)
        leases = await connection.fetch(
            "SELECT status FROM job_leases WHERE job_id = $1", enqueued.job_id
        )
        attempts = await connection.fetch(
            "SELECT status, failure_class FROM job_attempts WHERE job_id = $1", enqueued.job_id
        )
    assert status == "WAITING_AUTH", status
    assert leases and all(row["status"] != "ACTIVE" for row in leases), [dict(r) for r in leases]
    assert len(attempts) == 1 and attempts[0]["status"] != "RUNNING", [dict(a) for a in attempts]
    assert attempts[0]["failure_class"] == "WAITING_AUTH"

    # A second tick does not touch a parked job — it is not dispatchable.
    second = await dispatcher.tick()
    assert second.claimed == 0

    # The human logs in; the operator resumes; the job is READY again with a new dispatch version.
    await port.publish_ready(enqueued.job_id)
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, dispatch_version FROM jobs WHERE id = $1", enqueued.job_id
        )
    assert row["status"] == "READY" and row["dispatch_version"] == 2
