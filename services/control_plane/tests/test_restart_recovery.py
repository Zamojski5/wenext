"""Restarting a component does not lose a task (L5212), and the loop does not die on an error.

    "restart komponentu nie gubi taska" (L5212)
    "dispatcher w tym samym procesie na `SELECT ... FOR UPDATE SKIP LOCKED`" (L5193)

FOUR FAILURES THIS FILE EXISTS TO MAKE VISIBLE. Each one leaves a green test suite behind:

1.  **A worker killed without warning.** The proof has to be a REAL other process killed with
    `SIGKILL`. Closing a loop inside this interpreter runs `finally` blocks, cancels tasks and
    gives the heartbeat a chance to stop politely — none of which happens when the kernel removes
    a process. The whole point is that nothing on the worker's side got to run.
2.  **A job inserted while the dispatcher was down.** A dispatcher woken only by a notification
    never learns about it: the notification was fired into an empty room, and the row stays
    `READY` for ever. Hence the scan at process start, and hence a poll interval long enough here
    (30 s) that polling cannot be what saves the test.
3.  **A job inserted while the dispatcher was up but nobody signalled.** That is the poll floor,
    measured with `wake()` deliberately never called.
4.  **A loop that dies on the first exception.** It stops dispatching for ever while every unit
    test of the pieces stays green — this project's recurring failure shape. Measured twice: with
    a handler that raises, and with a whole tick that raises.

MARKS ARE PER TEST HERE, not `pytestmark` for the module. One test in this file must run WITHOUT
PostgreSQL — the one that asserts what the background work does when there is no DSN — and a
module-wide `db` mark would hide it from the host run that is the only place it is meaningful.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import sys
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import control_plane
import pytest
from control_plane import runtime
from control_plane.auth import DSN_VARIABLE
from control_plane.jobs.dispatcher import JobDispatcher
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore
from control_plane.runtime import BackgroundPhase

#: SCENARIUSZ 1 i 3 zestawu regresji P0 (`make p0-regression`): restart w czasie zadania oraz
#: wolny handler pod żywym i pod martwym heartbeatem. Marker jest modułowy, a `db` zostaje per
#: test, bo JEDEN test w tym pliku musi biec BEZ PostgreSQL (ten o braku DSN) i marker `db` na
#: całym module ukryłby go przed przebiegiem hostowym, który jest jedynym, gdzie coś znaczy.
pytestmark = pytest.mark.p0

#: `services/control_plane` — the directory the child process needs on `PYTHONPATH`, derived from
#: the imported package rather than from this file's location, so moving the tests cannot break it.
PACKAGE_ROOT = pathlib.Path(control_plane.__file__).resolve().parent.parent

#: Long enough for a child process to claim a job under it, short enough to wait out.
WORKER_LEASE_SECONDS = 1.0
WORKER_HEARTBEAT_SECONDS = 0.25

#: The worker process. Claims exactly one job, announces it on stdout, then hangs for ever so the
#: parent can kill it in the middle of the work. It heartbeats while it hangs — that is what makes
#: the kill the cause of the lease expiry, and not a lease that was doomed anyway.
WORKER_SOURCE = """
import asyncio
import os
import uuid

import asyncpg
from control_plane.jobs.dispatcher import JobDispatcher


async def main() -> None:
    pool = await asyncpg.create_pool(os.environ["WORKER_DSN"], min_size=1, max_size=2)

    async def hang_for_ever(claim) -> None:
        print(f"CLAIMED {claim.job_id} {claim.attempt_no}", flush=True)
        await asyncio.sleep(3600)

    dispatcher = JobDispatcher(
        pool,
        organization_id=uuid.UUID(os.environ["WORKER_ORGANIZATION"]),
        handlers={os.environ["WORKER_JOB_TYPE"]: hang_for_ever},
        lease_seconds=float(os.environ["WORKER_LEASE_SECONDS"]),
        heartbeat_seconds=float(os.environ["WORKER_HEARTBEAT_SECONDS"]),
    )
    await dispatcher.tick()


asyncio.run(main())
"""


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], *, timeout: float, message: str
) -> float:
    """Poll `predicate` until it holds. Returns how long it took; FAILS on the deadline.

    A test that waited a fixed time and then asserted would either be slow or flaky. A test that
    gave up silently would be worse than both.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    while loop.time() - started < timeout:
        if await predicate():
            return loop.time() - started
        await asyncio.sleep(0.05)
    pytest.fail(f"{message} (czekano {timeout:.1f} s)")


@contextlib.asynccontextmanager
async def _running(dispatcher: JobDispatcher):
    """Run `dispatcher.run_forever()` as a task and always cancel it on the way out."""
    task = asyncio.create_task(dispatcher.run_forever())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _enqueue(pool, tenant, *, job_type: str, attempt_limit: int = 5) -> UUID:
    enqueued = await JobStore(pool).enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=job_type,
        routing_key="planning.daily",
        priority=0,
        attempt_limit=attempt_limit,
        idempotency_key=f"restart-{uuid4()}",
    )
    assert enqueued.created
    return enqueued.job_id


async def _job(pool, job_id: UUID):
    async with pool.acquire() as connection:
        job = await connection.fetchrow("SELECT status FROM jobs WHERE id = $1", job_id)
        attempts = await connection.fetch(
            "SELECT attempt_no, status, failure_class FROM job_attempts WHERE job_id = $1 "
            "ORDER BY attempt_no",
            job_id,
        )
    return job["status"], [(row["attempt_no"], row["status"]) for row in attempts]


@pytest.mark.db
async def test_a_killed_worker_does_not_lose_the_job(pool, tenant, tmp_path) -> None:
    """SIGKILL a real worker mid-job; the job finishes anyway, on its second attempt.

    The assertions name the job by id and count its attempts, because "some job was processed" is
    not the claim being made. Two attempts and a terminal `SUCCEEDED` is: the first one died with
    the process, the reaper noticed, and the replacement ran.
    """
    job_type = "fixture.killed-worker"
    job_id = await _enqueue(pool, tenant, job_type=job_type, attempt_limit=5)

    script = tmp_path / "worker.py"
    script.write_text(WORKER_SOURCE, encoding="utf-8")
    child_environment = {
        **os.environ,
        "PYTHONPATH": str(PACKAGE_ROOT),
        "WORKER_DSN": os.environ["WENEXT_TEST_DSN"],
        "WORKER_ORGANIZATION": str(tenant["organization"]),
        "WORKER_JOB_TYPE": job_type,
        "WORKER_LEASE_SECONDS": str(WORKER_LEASE_SECONDS),
        "WORKER_HEARTBEAT_SECONDS": str(WORKER_HEARTBEAT_SECONDS),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        cwd=str(PACKAGE_ROOT),
        env=child_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        announced = await asyncio.wait_for(process.stdout.readline(), timeout=60)
    except TimeoutError:  # pragma: no cover - only on a broken environment
        process.kill()
        pytest.fail("proces workera nie zgłosił pobrania joba w 60 s")
    if not announced.startswith(b"CLAIMED"):
        process.kill()
        stderr = (await process.stderr.read()).decode("utf-8", "replace")
        pytest.fail(f"proces workera nie wystartował. stdout={announced!r}\nstderr:\n{stderr}")

    claimed_job, claimed_attempt = announced.decode().split()[1:3]
    assert claimed_job == str(job_id), (
        f"worker pobrał joba {claimed_job}, a test dotyczy {job_id}. Asercja na KONKRETNYM "
        "job_id, bo „jakiś job się wykonał” nie jest tą tezą"
    )
    assert claimed_attempt == "1"

    # The kernel removes the process: no `finally`, no cancelled heartbeat, no graceful release.
    process.kill()
    await process.wait()
    assert process.returncode == -9, (
        f"proces workera zakończył się kodem {process.returncode}, a miał zginąć od SIGKILL-a. "
        "Zamknięcie pętli w tym samym interpreterze wykonuje `finally` i nie dowodzi niczego"
    )

    status, attempts = await _job(pool, job_id)
    assert (status, attempts) == ("RUNNING", [(1, "RUNNING")]), (
        "zaraz po zabiciu workera job wygląda dokładnie jak job w toku — i to jest ten stan, "
        "w którym zgubiony task jest niewidoczny"
    )

    # The lease was renewed until the kill, so it dies one TTL later and not before.
    await asyncio.sleep(WORKER_LEASE_SECONDS + 0.4)
    report = await JobLeaseReaper(pool, organization_id=tenant["organization"]).reap_once()
    reaped = [job for job in report.reaped if job.job_id == job_id]
    assert len(reaped) == 1, report.explain()
    assert reaped[0].outcome is ReapOutcome.RETRY_SCHEDULED
    assert reaped[0].replacement_attempt_no == 2

    executed: list[UUID] = []

    async def record(claim) -> None:
        executed.append(claim.job_id)

    # `not_before` is a real backoff drawn under the 5 s ceiling, so wait for it rather than
    # assuming it has passed. This is the production schedule, not a shortened one.
    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: record},
        lease_seconds=WORKER_LEASE_SECONDS,
        heartbeat_seconds=WORKER_HEARTBEAT_SECONDS,
    )
    await _wait_until(
        lambda: _tick_until_executed(dispatcher, executed, job_id),
        timeout=15,
        message=f"zastępcza próba joba {job_id} nigdy się nie wykonała",
    )

    status, attempts = await _job(pool, job_id)
    assert executed == [job_id]
    assert status == "SUCCEEDED"
    assert attempts == [(1, "LEASE_EXPIRED"), (2, "SUCCEEDED")], (
        f"job dokończony po zabiciu workera ma próby {attempts}. Dwie: pierwsza zginęła "
        "z procesem, druga ją zastąpiła"
    )


async def _tick_until_executed(
    dispatcher: JobDispatcher, executed: list[UUID], job_id: UUID
) -> bool:
    await dispatcher.tick()
    return job_id in executed


@pytest.mark.db
async def test_a_job_inserted_while_the_dispatcher_was_down_is_executed_after_it_starts(
    pool, tenant
) -> None:
    """No dispatcher, no notification, no signal of any kind — and the job still runs.

    The poll interval is 30 s and the deadline is 5 s, so polling cannot be what makes this pass:
    the only thing that can is the scan the loop does BEFORE its first wait. `wake()` is never
    called, which is the whole point — a job inserted while nothing was listening has nobody left
    to announce it.
    """
    job_type = "fixture.dispatcher-was-down"
    job_id = await _enqueue(pool, tenant, job_type=job_type)

    executed: list[UUID] = []

    async def record(claim) -> None:
        executed.append(claim.job_id)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: record},
        poll_interval_seconds=30.0,
        lease_seconds=10.0,
        heartbeat_seconds=2.0,
    )
    async with _running(dispatcher):
        elapsed = await _wait_until(
            lambda: _has_run(executed, job_id),
            timeout=5,
            message=(
                f"job {job_id} wstawiony przy niezyjącym dispatcherze nie wykonał się po jego "
                "starcie. Skan przy starcie procesu jest jedyną rzeczą, która go widzi — "
                "LISTEN/NOTIFY jako jedyne budzenie gubi taki job na zawsze"
            ),
        )

    assert elapsed < 30.0, (
        f"job wykonał się po {elapsed:.2f} s przy interwale pollingu 30 s — czyli to polling, "
        "a nie skan przy starcie. Test mierzyłby wtedy inny mechanizm"
    )
    status, attempts = await _job(pool, job_id)
    assert (status, attempts) == ("SUCCEEDED", [(1, "SUCCEEDED")])


async def _has_run(executed: list[UUID], job_id: UUID) -> bool:
    return job_id in executed


@pytest.mark.db
async def test_a_job_inserted_after_the_loop_started_runs_without_any_notification(
    pool, tenant
) -> None:
    """The poll interval is the floor: no `wake()`, no notification, and the job still runs."""
    job_type = "fixture.no-notification"
    executed: list[UUID] = []

    async def record(claim) -> None:
        executed.append(claim.job_id)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: record},
        poll_interval_seconds=0.25,
        lease_seconds=10.0,
        heartbeat_seconds=2.0,
    )
    async with _running(dispatcher):
        # Let the startup scan happen and find nothing, so that what follows can only be a poll.
        await asyncio.sleep(0.4)
        job_id = await _enqueue(pool, tenant, job_type=job_type)
        await _wait_until(
            lambda: _has_run(executed, job_id),
            timeout=5,
            message=(
                f"job {job_id} wstawiony przy ŻYWYM dispatcherze i bez żadnej notyfikacji nie "
                "wykonał się. Polling ma być podłogą, nie optymalizacją"
            ),
        )

    assert executed == [job_id]


@pytest.mark.db
async def test_the_loop_survives_a_raising_handler(pool, tenant) -> None:
    """A handler that throws closes its own attempt and does NOT take the loop with it."""
    boom_type = "fixture.boom"
    ok_type = "fixture.after-boom"
    executed: list[UUID] = []

    async def explode(_claim) -> None:
        raise RuntimeError("handler pada celowo")

    async def record(claim) -> None:
        executed.append(claim.job_id)

    boom_id = await _enqueue(pool, tenant, job_type=boom_type)
    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={boom_type: explode, ok_type: record},
        poll_interval_seconds=0.25,
        lease_seconds=10.0,
        heartbeat_seconds=2.0,
    )
    async with _running(dispatcher):
        await _wait_until(
            lambda: _is_terminal(pool, boom_id),
            timeout=5,
            message=f"job {boom_id} z padającym handlerem nie został domknięty",
        )
        after_id = await _enqueue(pool, tenant, job_type=ok_type)
        await _wait_until(
            lambda: _has_run(executed, after_id),
            timeout=5,
            message=(
                "pętla nie przeżyła wyjątku handlera: job wstawiony PO awarii nigdy się nie "
                "wykonał. Pętla umierająca po pierwszym wyjątku gubi wszystkie kolejne zadania "
                "PRZY ZIELONYCH TESTACH"
            ),
        )

    status, attempts = await _job(pool, boom_id)
    assert status == "FAILED"
    assert attempts == [(1, "FAILED")]
    async with pool.acquire() as connection:
        failure_class = await connection.fetchval(
            "SELECT failure_class FROM job_attempts WHERE job_id = $1", boom_id
        )
    assert failure_class == "HANDLER_ERROR"


async def _is_terminal(pool, job_id: UUID) -> bool:
    async with pool.acquire() as connection:
        status = await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
    return status in {"SUCCEEDED", "FAILED", "CANCELLED"}


@pytest.mark.db
async def test_the_loop_survives_a_tick_that_raises(pool, tenant) -> None:
    """Not only handlers throw. A tick that blows up must not end the loop either.

    The first tick raises before it reaches any job — the failure a handler-level `try` would not
    catch, because it happens in the claim itself.
    """
    job_type = "fixture.flaky-tick"
    executed: list[UUID] = []
    ticks: list[int] = []

    async def record(claim) -> None:
        executed.append(claim.job_id)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: record},
        poll_interval_seconds=0.25,
        lease_seconds=10.0,
        heartbeat_seconds=2.0,
    )
    real_tick = dispatcher.tick

    async def flaky_tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("pierwszy tick pada celowo")
        return await real_tick()

    dispatcher.tick = flaky_tick  # type: ignore[method-assign]

    job_id = await _enqueue(pool, tenant, job_type=job_type)
    async with _running(dispatcher):
        await _wait_until(
            lambda: _has_run(executed, job_id),
            timeout=5,
            message=(
                "pętla zginęła na pierwszym wyjątku ticku — od tej chwili nic nie jest "
                "dispatchowane, a wszystkie testy jednostkowe dispatchera są zielone"
            ),
        )
    assert len(ticks) >= 2


@pytest.mark.db
async def test_a_handler_slower_than_the_lease_keeps_it_through_heartbeats(pool, tenant) -> None:
    """A handler that outlives the lease TTL keeps its job — because it heartbeats (L5194).

    Without the heartbeat the reaper is entitled to the job while the handler is still working,
    and two workers end up on one record (L5211) through the back door. The measurement is the
    accepted close: `close_attempt` CASes on `expires_at > now()`, so a `SUCCEEDED` job proves the
    lease was still alive at the end.
    """
    job_type = "fixture.slow-handler"
    job_id = await _enqueue(pool, tenant, job_type=job_type)

    async def slow(_claim) -> None:
        await asyncio.sleep(1.8)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: slow},
        lease_seconds=1.0,
        heartbeat_seconds=0.25,
    )
    report = await dispatcher.tick()
    assert (report.claimed, report.succeeded) == (1, 1), report.explain()

    status, attempts = await _job(pool, job_id)
    assert (status, attempts) == ("SUCCEEDED", [(1, "SUCCEEDED")])


@pytest.mark.db
async def test_without_heartbeats_a_slow_handler_loses_the_job_and_says_so(
    pool, tenant, caplog
) -> None:
    """The other direction of the same guarantee, and the reason the refusal has to be loud.

    With the heartbeat disabled the lease dies under the handler, the fenced write is REFUSED
    (zero updated rows is never a success — `domain/errors.py`), and the job stays claimable by
    the reaper. If this test ever passes with the job `SUCCEEDED`, the CAS stopped fencing.
    """
    job_type = "fixture.no-heartbeat"
    job_id = await _enqueue(pool, tenant, job_type=job_type)

    async def slow(_claim) -> None:
        await asyncio.sleep(1.5)

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        handlers={job_type: slow},
        lease_seconds=0.6,
        heartbeat_seconds=0.2,
    )

    async def never_beat(_token) -> None:
        await asyncio.sleep(3600)

    dispatcher._heartbeat_until_cancelled = never_beat  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="control_plane.jobs.dispatcher"):
        await dispatcher.tick()

    status, attempts = await _job(pool, job_id)
    assert (status, attempts) == ("RUNNING", [(1, "RUNNING")]), (
        "handler bez heartbeatu przepchnął wynik na wygasłym lease'ie — CAS przestał ogradzać"
    )
    assert any("ODRZUCONY" in record.message for record in caplog.records), (
        "odrzucenie zapisu na wygasłym lease'ie przeszło po cichu. Cichy zapis odrzucony to "
        "job, o którym dispatcher myśli, że go domknął"
    )


# ---------------------------------------------------------------------------
# What the background work of the process does — `runtime.py`
# ---------------------------------------------------------------------------


async def test_start_background_without_a_dsn_starts_nothing_and_says_so(monkeypatch) -> None:
    """No DSN: nothing starts, nothing raises, and the state says which of the two happened.

    NOT a database test — it must pass on the host run, where there is no PostgreSQL at all. An
    exception out of the lifespan would take the whole API down with the queue, and `/healthz`
    must keep answering when the database is unreachable (`app.py`). Silence would be worse than
    both: a component whose only way of reporting that it does nothing is a log line is a
    component that silently does nothing.
    """
    monkeypatch.delenv(DSN_VARIABLE, raising=False)
    await runtime.stop_background()

    await runtime.start_background()
    try:
        state = runtime.background_state()
        assert state.phase is BackgroundPhase.NO_DSN, state
        assert state.detail is not None and DSN_VARIABLE in state.detail
    finally:
        await runtime.stop_background()
    assert runtime.background_state().phase is BackgroundPhase.STOPPED


@pytest.mark.db
async def test_several_organizations_each_get_their_own_dispatcher(
    pool, tenant, monkeypatch
) -> None:
    """Since P7 several organizations is the NORMAL case, and each one gets its own dispatcher.

    THIS TEST REPLACED ITS OWN OPPOSITE, and the reason is worth keeping. Until P7 the runtime
    refused to start when it found more than one organization, because `JobDispatcher` claims for
    one tenant and guessing would have run somebody else's jobs. With self-service registration
    that refusal became the bigger fault: the first customer to register a second space stopped
    the queue for everybody, silently, and the first symptom was an export job that never ran
    (measured end to end during P7).

    What the old rule protected is still protected, by the shape rather than by refusing: one
    dispatcher per organization, each claiming only its own, and the set re-read every minute.
    """
    monkeypatch.setenv(DSN_VARIABLE, os.environ["WENEXT_TEST_DSN"])
    async with pool.acquire() as connection:
        organizations = await connection.fetchval("SELECT count(*) FROM organizations")
    assert organizations > 1, (
        "ten test wymaga kilku organizacji w bazie, żeby dotknąć warunku, o którym mówi"
    )

    await runtime.start_background()
    try:
        await asyncio.sleep(0.5)
        state = runtime.background_state()
        assert state.phase is BackgroundPhase.RUNNING, state
        # The detail says how many, because one field cannot name them all and a single id there
        # would read as "this is the only tenant".
        assert state.detail is not None and "organizacji" in state.detail, state.detail
    finally:
        await runtime.stop_background()


@pytest.mark.db
async def test_an_organization_awaiting_deletion_gets_no_dispatcher(pool, tenant) -> None:
    """Its access is already cut; running its jobs would be the product acting for somebody who
    asked it to stop."""
    from control_plane.runtime import _organizations

    async with pool.acquire() as connection:
        doomed = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('do skasowania') RETURNING id"
        )
        user = await connection.fetchval(
            "INSERT INTO users (email, credential_hash) VALUES ($1, $2) RETURNING id",
            f"kasowany-{doomed}@firma.test",
            "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$" + "a" * 43,
        )
        await connection.execute(
            "INSERT INTO data_deletion_jobs (organization_id, requested_by, purge_after) "
            "VALUES ($1, $2, now() + interval '7 days')",
            doomed,
            user,
        )

    listed = await _organizations(pool)

    assert tenant["organization"] in listed
    assert doomed not in listed


@pytest.mark.db
async def test_start_background_really_starts_the_dispatcher_and_the_reaper(
    pool, tenant, monkeypatch
) -> None:
    """The lifespan's `start_background()` ticks a REAL dispatcher against a REAL database.

    The organization list is stubbed to this test's tenant, and only that: everything else is the
    production path, including the empty handler map. The job therefore ends in
    `WAITING_CAPABILITY` — F1 has no handler for any type (D-013), and the dispatcher parks such a
    job with its lease released rather than pretending it succeeded.

    Without this test, `runtime.start_background()` could be an empty function and every other
    test in this file would still pass, because they all build their own dispatcher.
    """
    monkeypatch.setenv(DSN_VARIABLE, os.environ["WENEXT_TEST_DSN"])

    async def organizations(_pool):
        return [tenant["organization"]]

    monkeypatch.setattr(runtime, "_organizations", organizations)

    job_id = await _enqueue(pool, tenant, job_type="fixture.runtime-wiring")
    await runtime.start_background()
    try:
        await _wait_until(
            lambda: _is_parked(pool, job_id),
            timeout=10,
            message=(
                f"job {job_id} nie został ruszony przez zadania w tle. runtime.start_background() "
                "jest wołane z lifespanu aplikacji — jeśli nie startuje dispatchera, kolejka nie "
                "działa w kontenerze, a testy jednostkowe dispatchera są zielone"
            ),
        )
        assert runtime.background_state().phase is BackgroundPhase.RUNNING
        assert runtime.background_state().organization_id == tenant["organization"]
    finally:
        await runtime.stop_background()

    status, attempts = await _job(pool, job_id)
    assert status == "WAITING_CAPABILITY", (
        f"job bez handlera skończył jako {status}. W F1 nie ma ANI JEDNEGO handlera (D-013), "
        "a job, którego nikt nie umie wykonać, ma czekać — nie udawać sukcesu"
    )
    assert attempts == [(1, "FAILED")]


async def _is_parked(pool, job_id: UUID) -> bool:
    """Parked means the dispatcher FINISHED with the job, not that it picked it up.

    Until 2026-09-19 this was `status != "READY"`, which is also true of `RUNNING`. The wait then
    returned while the job was still in the dispatcher's hands, `stop_background()` cancelled the
    tick mid-park, and the assertion below saw `RUNNING`. It passed on a quiet machine and failed
    under load, which is the worst way for a test to be wrong: the behaviour it guards was never
    broken, and the red result teaches whoever sees it to read red as background noise
    (docs/STAN-PRAC.md §7, the fourth variant).
    """
    async with pool.acquire() as connection:
        status = await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
    return status not in ("READY", "RUNNING")
