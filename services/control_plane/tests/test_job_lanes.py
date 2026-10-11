"""Lanes of the job queue: an engine turn no longer holds the publisher, and no claim loses its lease
while it waits in its batch (26.09).

Two failures measured on live, one test each:

  * a conversation turn ran for its whole duration while the publication it had just issued sat
    in the queue behind it (`docs/PROBA-RDZENIA-2026-09-23.md`, the "czekam" loop);
  * the jobs claimed behind a slow one were reaped after 60 s and their results refused when they
    finally ran ("wynik SUCCEEDED joba ... ODRZUCONY", 26.09 after the outage).

Plus the literal list in `jobs/lanes.py`, pinned to the constants it mirrors.
"""

from __future__ import annotations

import asyncio

import pytest
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.backoff import ATTEMPT_LIMIT
from control_plane.jobs.dispatcher import JobDispatcher
from control_plane.jobs.lanes import ENGINE_JOB_TYPES
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore


def test_every_engine_job_type_is_the_constant_its_handler_module_uses() -> None:
    """A renamed engine job must fail here, not move silently into the lane next to a turn."""
    from control_plane.observe.scheduling import JOB_JUDGE, JOB_PICTURE, JOB_VOICE
    from control_plane.pilot.handlers import JOB_PILOT_CYCLE
    from control_plane.presence.handlers import JOB_PRESENCE_TICK
    from control_plane.sessions.handlers import JOB_SESSION_TURN
    from control_plane.workspace.handlers import JOB_LOGIN, JOB_READ

    expected = {
        JOB_SESSION_TURN,
        JOB_PRESENCE_TICK,
        JOB_JUDGE,
        JOB_PICTURE,
        JOB_VOICE,
        JOB_PILOT_CYCLE,
        JOB_LOGIN,
        JOB_READ,
    }
    assert expected == ENGINE_JOB_TYPES


def test_the_publisher_and_the_observation_are_not_engine_jobs() -> None:
    """The whole point: these are the jobs that must move while a turn runs."""
    from control_plane.pilot.publisher import JOB_PUBLISH

    assert JOB_PUBLISH not in ENGINE_JOB_TYPES
    assert "observe.snapshot_thread" not in ENGINE_JOB_TYPES
    assert "observe.poll_community" not in ENGINE_JOB_TYPES


async def _enqueue(port, tenant, *, job_type: str, key: str, priority: int = 1):
    return await commands.enqueue_job(
        port,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=job_type,
        routing_key="probe",
        priority=priority,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=key,
    )


async def _status(pool, job_id) -> str:
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)


@pytest.mark.db
async def test_a_claim_queued_behind_a_slow_job_keeps_its_lease(pool, tenant) -> None:
    """The second job of a batch waits longer than the lease lives, and its result still counts.

    Before 26.09 only the RUNNING job was beaten: the second one's lease expired while the first
    worked, and its SUCCEEDED was refused as a write under a dead lease.
    """
    port = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    slow = await _enqueue(port, tenant, job_type="probe.lanes.slow", key="lanes:slow", priority=1)
    second = await _enqueue(
        port, tenant, job_type="probe.lanes.second", key="lanes:second", priority=2
    )
    ran: list[str] = []

    async def slow_handler(claim: ClaimedJob) -> None:
        # Longer than the lease below: without a beat the queued claim would be dead by now.
        await asyncio.sleep(3.5)
        ran.append(claim.job_type)

    async def second_handler(claim: ClaimedJob) -> None:
        ran.append(claim.job_type)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={"probe.lanes.slow": slow_handler, "probe.lanes.second": second_handler},
        lease_seconds=2.0,
        heartbeat_seconds=0.5,
        batch_limit=2,
    )
    report = await dispatcher.tick()

    assert report.claimed == 2
    assert ran == ["probe.lanes.slow", "probe.lanes.second"]
    assert report.succeeded == 2, report.explain()
    assert await _status(pool, slow.job_id) == "SUCCEEDED"
    assert await _status(pool, second.job_id) == "SUCCEEDED"


@pytest.mark.db
async def test_an_io_job_runs_while_an_engine_job_is_still_running(pool, tenant) -> None:
    """Two dispatchers on disjoint lanes: the io lane completes while the engine lane is busy.

    The engine handler blocks on an event nobody sets until the io job has SUCCEEDED, so this
    test can only pass if the two lanes really run side by side. Each lane must also leave the
    other lane's job alone.
    """
    port = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    engine_job = await _enqueue(port, tenant, job_type="probe.lanes.engine", key="lanes:engine")
    io_job = await _enqueue(port, tenant, job_type="probe.lanes.io", key="lanes:io")
    release = asyncio.Event()
    engine_started = asyncio.Event()

    async def engine_handler(claim: ClaimedJob) -> None:
        engine_started.set()
        await asyncio.wait_for(release.wait(), timeout=20)

    async def io_handler(claim: ClaimedJob) -> None:
        return None

    handlers = {"probe.lanes.engine": engine_handler, "probe.lanes.io": io_handler}
    engine = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers=handlers,
        job_types={"probe.lanes.engine"},
        batch_limit=1,
        lease_seconds=10.0,
        heartbeat_seconds=1.0,
        lane="engine",
    )
    io = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers=handlers,
        exclude_job_types={"probe.lanes.engine"},
        lease_seconds=10.0,
        heartbeat_seconds=1.0,
        lane="io",
    )

    engine_tick = asyncio.create_task(engine.tick())
    await asyncio.wait_for(engine_started.wait(), timeout=10)

    io_report = await io.tick()
    assert io_report.claimed == 1, io_report.explain()
    assert await _status(pool, io_job.job_id) == "SUCCEEDED"
    # The engine job is still running: the io lane did not wait for it and did not take it.
    assert not engine_tick.done()
    assert await _status(pool, engine_job.job_id) == "RUNNING"

    release.set()
    engine_report = await asyncio.wait_for(engine_tick, timeout=20)
    assert engine_report.claimed == 1 and engine_report.succeeded == 1
    assert await _status(pool, engine_job.job_id) == "SUCCEEDED"


async def test_an_empty_lane_is_refused_rather_than_read_as_an_empty_queue() -> None:
    """`job_types=()` would claim nothing for ever and look exactly like a drained queue."""

    class _Port:
        async def claim(self, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("claim must be refused before reaching the port")

    from uuid import uuid4

    with pytest.raises(ValueError, match="pusta lista"):
        await commands.claim_batch(
            _Port(), worker=uuid4(), limit=1, lease_seconds=10.0, job_types=()
        )
