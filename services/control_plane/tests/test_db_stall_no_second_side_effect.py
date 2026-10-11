"""A database that stops answering longer than the lease TTL: the job is lost READABLY, and the
side effect does not happen twice (P0, ROADMAP-PIVOT: "wolna baza lub utrata lease").

WHAT WAS ALREADY MEASURED, AND WHY IT IS NOT THIS. `test_restart_recovery.py` has both directions
of the heartbeat: a handler slower than the TTL keeps its lease BECAUSE it beats, and with the
heartbeat coroutine replaced by a sleep the fenced write is refused. That second test disables the
heartbeat. This one does not: the heartbeat runs, calls the store, and the STORE hangs, which is
what a database under a lock, a paused Docker VM or a saturated pool looks like from up here. The
difference matters because the dispatcher's own code is then fully in play, and because what is
being asserted is not the lease but what the OUTSIDE WORLD gets.

THE POINT. The handler reaches the platform and records `SUBMIT_STARTED` before it hangs, exactly
as the publication path does (`authorize_and_consume` records the state BEFORE the submit). So at
the moment the lease dies there is a comment on Reddit, or maybe there is; nobody knows. Three
things must then hold, and this file asserts all three:

  1. the handler with the side effect is NOT run a second time. Not by the dispatcher, not by the
     reaper, not by the next tick. `SUBMIT_STARTED` forbids an automatic retry (L2673), and a
     second run here means a second comment under somebody's post;
  2. the write the stalled worker finally tries is REFUSED, loudly. A silent refusal is a job the
     dispatcher believes it closed;
  3. the state left behind says which of "done" and "maybe" it is. `FAILED` with reason
     `RETRY_FORBIDDEN` and `from_state = SUBMIT_STARTED` is a job a human can look at; a job
     sitting at RUNNING for ever with a live-looking lease is not.

A slow database that stays INSIDE the TTL is the control case at the bottom: it must not lose
anything, or the test above it would also pass against a dispatcher that gives up too early.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest
from community_os_contracts import ExecutionAttemptState
from community_os_contracts.execution import may_retry_automatically
from control_plane.jobs.dispatcher import JobDispatcher
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore

pytestmark = [pytest.mark.db, pytest.mark.p0]

#: Short enough to wait out, long enough for the handler to record its side effect first.
LEASE_SECONDS = 1.0
HEARTBEAT_SECONDS = 0.25
#: Above `LEASE_SECONDS`, so the stall really outlives the lease rather than nearly outliving it.
STALL_SECONDS = 2.5
JOB_TYPE = "fixture.submit-then-stall"


class StallingJobStore:
    """A `JobStore` whose `heartbeat` hangs, standing in for a database that stopped answering.

    Everything else is delegated, so the dispatcher runs its own heartbeat loop, its own fenced
    close and its own error handling: only the answer from the database is missing. That is the
    difference from disabling the heartbeat, which removes the dispatcher's code from the
    measurement along with the database's.
    """

    def __init__(self, inner: JobStore, *, stall_seconds: float) -> None:
        self._inner = inner
        self._stall_seconds = stall_seconds
        self.stalling = False
        self.heartbeats_attempted = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def heartbeat(self, token, *, lease_seconds: float):
        self.heartbeats_attempted += 1
        if self.stalling:
            await asyncio.sleep(self._stall_seconds)
        return await self._inner.heartbeat(token, lease_seconds=lease_seconds)


async def _enqueue(pool, tenant, *, attempt_limit: int = 5):
    store = JobStore(pool)
    job = await store.enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_TYPE,
        routing_key="execution.browser",
        priority=0,
        attempt_limit=attempt_limit,
        idempotency_key=f"stall-{uuid4()}",
    )
    return job.job_id


async def _record_side_effect(pool, claim) -> None:
    """What the publication path records before it submits: how far the worker got.

    Raw SQL for the reason `test_no_retry_after_submit_started.py` gives: F1 has no writer of this
    column (D-013), so the evidence a real worker would leave is simulated here.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE job_attempts SET failure_class = $2 WHERE attempt_id = $1",
            claim.attempt_id,
            ExecutionAttemptState.SUBMIT_STARTED.value,
        )


async def _job(pool, job_id):
    async with pool.acquire() as connection:
        status = await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
        attempts = [
            (row["attempt_no"], row["status"], row["failure_class"])
            for row in await connection.fetch(
                "SELECT attempt_no, status, failure_class FROM job_attempts "
                "WHERE job_id = $1 ORDER BY attempt_no",
                job_id,
            )
        ]
        audit = [
            (row["kind"], row["reason_code"], row["from_state"])
            for row in await connection.fetch(
                "SELECT kind, payload->>'reason_code' AS reason_code, "
                "payload->>'from_state' AS from_state FROM audit_events "
                "WHERE subject_ref = $1 ORDER BY occurred_at",
                f"job:{job_id}",
            )
        ]
    return status, attempts, audit


def test_the_state_the_handler_records_is_one_that_forbids_a_retry() -> None:
    """The premise of the whole file, checked before anything runs.

    If `SUBMIT_STARTED` ever stopped forbidding a retry, every assertion below would still pass
    while measuring a queue that happily re-submits. That is the shape of a test case which never
    reaches the condition it is named after, and this repository has paid for it four times.
    """
    assert not may_retry_automatically(ExecutionAttemptState.SUBMIT_STARTED)


async def test_a_database_stalled_past_the_ttl_loses_the_job_without_a_second_side_effect(
    pool, tenant, caplog
) -> None:
    job_id = await _enqueue(pool, tenant)
    store = StallingJobStore(JobStore(pool), stall_seconds=STALL_SECONDS)
    runs: list[int] = []

    async def submit_then_stall(claim) -> None:
        runs.append(claim.attempt_no)
        await _record_side_effect(pool, claim)
        # The database stops answering from here on, the heartbeat included.
        store.stalling = True
        await asyncio.sleep(STALL_SECONDS)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={JOB_TYPE: submit_then_stall},
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=HEARTBEAT_SECONDS,
    )
    # `_store` is the ONLY thing the heartbeat calls; claiming and closing go through `_port`,
    # which was built from the real store at construction. So this replacement stalls the
    # heartbeat's database call and nothing else, which is the situation being reproduced.
    dispatcher._store = store  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR, logger="control_plane.jobs.dispatcher"):
        report = await dispatcher.tick()

    assert report.claimed == 1, report.explain()
    assert runs == [1], f"handler z efektem ubocznym uruchomił się {len(runs)} razy: {runs}"
    assert store.heartbeats_attempted >= 1, (
        "heartbeat nie spróbował ani razu, więc to nie jest test zawieszonej bazy, tylko test "
        "wyłączonego heartbeatu, a ten drugi już istnieje w test_restart_recovery.py"
    )
    assert any("ODRZUCONY" in record.message for record in caplog.records), (
        "zapis na wygasłym lease'ie został odrzucony PO CICHU. Cicho odrzucony zapis to job, "
        "o którym dispatcher myśli, że go domknął, a reaper myśli, że jest jego"
    )

    status, attempts, _audit = await _job(pool, job_id)
    assert status == "RUNNING", (
        f"job jest w stanie {status}. Zawieszona baza nie zmieniła NICZEGO po stronie workera: "
        "jego wynik był odrzucony przez CAS, więc job czeka na reapera"
    )
    assert attempts == [(1, "RUNNING", ExecutionAttemptState.SUBMIT_STARTED.value)], (
        f"stan zapisany przez workera zniknął: {attempts}. To jedyny ślad tego, jak daleko "
        "doszedł, i jedyne, na czym reaper może oprzeć zakaz ponowienia"
    )

    # The reaper now owns the job. This is the step where a second side effect would be born.
    reaped = await JobLeaseReaper(pool, organization_id=tenant["organization"]).reap_once()

    assert len(reaped.reaped) == 1, reaped.explain()
    decision = reaped.reaped[0]
    assert decision.recorded_state is ExecutionAttemptState.SUBMIT_STARTED
    assert decision.outcome is ReapOutcome.TERMINALIZED_RETRY_FORBIDDEN, (
        f"reaper zdecydował {decision.outcome} dla próby, która zdążyła wysłać. Komentarz mógł "
        "już stać pod postem; druga próba postawiłaby drugi"
    )
    assert decision.replacement_attempt_no is None

    status, attempts, audit = await _job(pool, job_id)
    assert [a[0] for a in attempts] == [1], (
        f"job dostał próby {[a[0] for a in attempts]} mimo zakazu ponowienia. Limit prób to 5 i "
        "jest wolne miejsce, więc to nie limit trzyma tu drugą próbę, tylko zapisany stan"
    )
    assert status == "FAILED"
    assert audit == [("JOB_STATE_CHANGED", "RETRY_FORBIDDEN", "SUBMIT_STARTED")], (
        f"ślad w audycie: {audit}. Człowiek ma z niego wyczytać, że skutek jest NIEPEWNY, "
        "a nie że zadanie po prostu się nie udało"
    )

    # And the next tick finds nothing to run: the job is terminal, not merely unclaimed.
    runs.clear()
    store.stalling = False
    again = await dispatcher.tick()
    assert again.claimed == 0 and runs == [], (
        f"kolejny tik podjął job po terminalizacji ({again.explain()}), co jest drugim "
        "wykonaniem handlera z efektem ubocznym pod inną nazwą"
    )


async def test_a_slow_database_inside_the_ttl_keeps_the_job_and_finishes_it(pool, tenant) -> None:
    """The control case. Without it the test above would also pass against a dispatcher whose
    heartbeat is broken in every case, which would mean losing every slow job rather than
    surviving them.
    """
    job_id = await _enqueue(pool, tenant)
    # Slower than the heartbeat interval, comfortably inside the TTL: the beat is late, not gone.
    store = StallingJobStore(JobStore(pool), stall_seconds=HEARTBEAT_SECONDS * 1.5)
    runs: list[int] = []

    async def submit_then_wait(claim) -> None:
        runs.append(claim.attempt_no)
        await _record_side_effect(pool, claim)
        store.stalling = True
        await asyncio.sleep(LEASE_SECONDS * 1.6)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={JOB_TYPE: submit_then_wait},
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=HEARTBEAT_SECONDS,
    )
    dispatcher._store = store  # type: ignore[assignment]

    report = await dispatcher.tick()

    assert (report.claimed, report.succeeded) == (1, 1), report.explain()
    assert runs == [1]
    status, attempts, _audit = await _job(pool, job_id)
    assert status == "SUCCEEDED", (
        f"job ze spóźnionym, ale żywym heartbeatem skończył jako {status}. Dispatcher gubi "
        "każde wolne zadanie, a nie tylko te, pod którymi baza naprawdę padła"
    )
    assert [a[0] for a in attempts] == [1]
