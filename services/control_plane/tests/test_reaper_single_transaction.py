"""The reaper is ONE transaction: all of it lands, or none of it does (L2669).

    "Reaper w jednej transakcji blokuje wygasły job, zamyka poprzednią próbę jako `LEASE_EXPIRED`
     i porównuje `attempt_no` z `attempt_limit`." (L2669)

WHAT A HALF-DONE REAP LOOKS LIKE, and why it is worse than a crash. An attempt closed as
`LEASE_EXPIRED` with no replacement row and a job left `RUNNING` is a job that

  * the dispatcher will never claim — it is not `READY`,
  * the reaper will never see again — there is no ACTIVE lease left to expire,
  * the operator's screen shows as running,

which is the "lost task" DoD L5212 forbids, wearing the costume of a job in progress. So the
whole reap shares one transaction, and this file proves it the only way that counts: by making a
write near the END of that transaction fail and then looking at the database.

THE ASSERTIONS ARE ON THE DATABASE, NOT ON THE EXCEPTION. `pytest.raises` proves that a failure
was announced; it does not prove that nothing was written, and the write that got through is the
entire bug (the same rule `tests/test_job_lease_fencing.py` states in its own docstring).

ONE HONEST NOTE ABOUT THE RACE TESTS. `test_two_reapers_racing_on_one_expired_job_create_one_attempt`
measures the ROW LOCK (`FOR UPDATE … SKIP LOCKED`), because with that lock in place the loser
never reaches its INSERT. `UNIQUE(job_id, attempt_no)` is the second line of defence, and it is
measured separately, by handing the reaper a job whose replacement number a competitor has already
taken — the only way to reach that constraint without deleting the lock first.
"""

from __future__ import annotations

import asyncio
import random
from uuid import UUID, uuid4

import pytest
from control_plane.audit import AuditWriter
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore

#: SCENARIUSZ 2 i 4: przejęcie wygasłego lease'u jest jedną transakcją, więc przerwany reaper
#: nie zostawia joba bez próby ani dwóch prób zastępczych.
pytestmark = [pytest.mark.db, pytest.mark.p0]

INSTANT_LEASE_SECONDS = 0.4


class _BottomOfTheInterval(random.Random):
    """Full jitter that always draws the bottom of `[0, ceiling]` — see `test_attempt_limit.py`."""

    def uniform(self, a: float, b: float) -> float:  # opis w docstringu klasy
        return a


async def _wait_for_real_expiry() -> None:
    await asyncio.sleep(INSTANT_LEASE_SECONDS + 0.4)


async def _enqueue_and_claim(pool, tenant, *, attempt_limit: int):
    """One fresh job, claimed under a lease that will really expire. Returns the claim."""
    store = JobStore(pool)
    enqueued = await store.enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="fixture.reaper",
        routing_key="planning.daily",
        priority=0,
        attempt_limit=attempt_limit,
        idempotency_key=f"reaper-{uuid4()}",
    )
    batch = await store.claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        lease_seconds=INSTANT_LEASE_SECONDS,
    )
    assert len(batch.claims) == 1, batch.explain()
    claim = batch.claims[0]
    assert claim.job_id == enqueued.job_id
    return claim


def _reaper(tenant, pool) -> JobLeaseReaper:
    return JobLeaseReaper(
        pool, organization_id=tenant["organization"], jitter=_BottomOfTheInterval()
    )


async def _snapshot(pool, job_id: UUID) -> dict[str, object]:
    """Everything the invariant is about, in one read."""
    async with pool.acquire() as connection:
        job_status = await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
        attempts = await connection.fetch(
            "SELECT attempt_no, status, failure_class FROM job_attempts WHERE job_id = $1 "
            "ORDER BY attempt_no",
            job_id,
        )
        active_leases = await connection.fetchval(
            "SELECT count(*) FROM job_leases WHERE job_id = $1 AND status = 'ACTIVE'", job_id
        )
    return {
        "job_status": job_status,
        "attempts": [(row["attempt_no"], row["status"]) for row in attempts],
        "active_leases": active_leases,
    }


async def test_an_interrupted_reaper_never_leaves_a_job_without_an_attempt(
    pool, tenant, monkeypatch
) -> None:
    """A failure at the LAST write of the reap rolls the whole reap back — attempt included.

    The injected failure is the audit write, which is deliberately the last thing the reap does.
    Anything earlier would prove less: this way the closed attempt, the closed lease, the bumped
    `dispatch_version` and the replacement row have all already been written when the transaction
    is asked to abort.
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=3)
    before = await _snapshot(pool, claim.job_id)
    assert before == {
        "job_status": "RUNNING",
        "attempts": [(1, "RUNNING")],
        "active_leases": 1,
    }

    async def refuse_to_write_audit(self, **_kwargs):  # atrapa testowa
        raise RuntimeError("wstrzyknięta awaria ostatniego zapisu reapera")

    monkeypatch.setattr(AuditWriter, "record", refuse_to_write_audit)

    await _wait_for_real_expiry()
    with pytest.raises(RuntimeError, match="wstrzyknięta awaria"):
        await _reaper(tenant, pool).reap_once()

    after = await _snapshot(pool, claim.job_id)
    assert after == before, (
        f"przerwany reaper zostawił bazę w stanie {after}, a before przebiegiem było {before}. "
        "Job z zamkniętą próbą i bez zastępczej nie jest ani wykonywalny, ani terminalny — "
        "dispatcher go nie weźmie (nie jest READY), reaper go nie zobaczy (nie ma aktywnego "
        "lease'u), a ekran operatora pokaże, że trwa"
    )

    # And the next pass, with the audit working again, finishes the job the interrupted one
    # started. An expired lease that nobody can ever reap again would be the other half of the
    # same failure.
    monkeypatch.undo()
    report = await _reaper(tenant, pool).reap_once()
    assert len(report.reaped) == 1, report.explain()
    assert report.reaped[0].outcome is ReapOutcome.RETRY_SCHEDULED
    assert (await _snapshot(pool, claim.job_id))["attempts"] == [
        (1, "LEASE_EXPIRED"),
        (2, "RUNNING"),
    ]


async def test_two_reapers_racing_on_one_expired_job_create_one_attempt(pool, tenant) -> None:
    """Two reapers, one expired job, exactly one replacement — and the loser is not an error.

    What is being measured here is the row lock. The loser's `SELECT … FOR UPDATE SKIP LOCKED`
    either skips the locked job or, if the winner has already committed, no longer matches it —
    either way it reports an empty pass rather than raising. A reaper that treated "somebody else
    got there first" as a failure would fill the log with incidents during every ordinary
    restart, and one that treated it as success would report attempts nobody created.
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=5)
    await _wait_for_real_expiry()

    first, second = await asyncio.gather(
        _reaper(tenant, pool).reap_once(),
        _reaper(tenant, pool).reap_once(),
        return_exceptions=True,
    )
    for report in (first, second):
        assert not isinstance(report, BaseException), (
            f"przegrany wyścig o wygasły lease wyleciał wyjątkiem: {report!r}. Przegrany "
            "wyścig jest normalnym przypadkiem, nie awarią"
        )

    scheduled = [
        job
        for report in (first, second)
        for job in report.reaped
        if job.outcome is ReapOutcome.RETRY_SCHEDULED
    ]
    assert len(scheduled) == 1, (
        f"dwa reapery zaplanowały {len(scheduled)} zastępczych prób jednego joba. Blokada "
        "wiersza jest tu mechanizmem — bez niej oba przebiegi zamykają tę samą próbę"
    )
    assert scheduled[0].replacement_attempt_no == 2

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT attempt_no FROM job_attempts WHERE job_id = $1 ORDER BY attempt_no",
            claim.job_id,
        )
    assert [row["attempt_no"] for row in rows] == [1, 2]


async def test_a_replacement_number_a_competitor_already_took_is_a_lost_race(pool, tenant) -> None:
    """`UNIQUE(job_id, attempt_no)` is the backstop, and the loser rolls back cleanly.

    Reaching that constraint requires a competitor that has ALREADY created attempt 2 — with the
    row lock in place, two reapers of this package can never both get that far. The row is
    inserted here by raw SQL for exactly that reason: it stands in for whatever future writer
    creates an attempt (F4's execution path is the one the document plans), and it is the only
    way to measure the constraint without first deleting the lock that makes it unreachable.

    L3838 is what this proves: "Obowiązuje UNIQUE(job_id, attempt_no)". Without it two rows could
    both claim to be attempt 2, and counting attempts against `attempt_limit` would count rows
    that mean nothing (L5214).
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=5)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO job_attempts
                (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                 lease_owner, status, not_before)
            VALUES ($1, $2, 2, 1, 9, gen_random_uuid(), 'RUNNING', now())
            """,
            tenant["organization"],
            claim.job_id,
        )
    before = await _snapshot(pool, claim.job_id)
    assert before["attempts"] == [(1, "RUNNING"), (2, "RUNNING")]

    await _wait_for_real_expiry()
    report = await _reaper(tenant, pool).reap_once()

    assert len(report.reaped) == 1, report.explain()
    assert report.reaped[0].outcome is ReapOutcome.LOST_RACE, (
        "kolizja UNIQUE na numerze próby ma być obsłużona jako przegrany wyścig, nie jako awaria"
    )
    assert report.scanned_to_the_end is False, (
        "przebieg, który przegrał wyścig, nie przeskanował kolejki do końca — raport, który "
        "twierdzi inaczej, ukrywa zaległość"
    )
    after = await _snapshot(pool, claim.job_id)
    assert after == before, (
        f"after przegranym wyścigu baza jest w stanie {after}, a była w {before}. Transakcja "
        "reapera ma się wycofać CAŁA — zamknięta próba bez zastępczej to zgubiony job"
    )


async def test_terminalization_leaves_exactly_one_audit_row(pool, tenant) -> None:
    """One reaped job, one audit row — in both branches, and the terminal one says why.

    `jobs` has no failure column and `jobs_status_is_known` (0003) does not know
    `ATTEMPTS_EXHAUSTED`, so the terminal status is `FAILED` and the REASON exists in exactly one
    place: `reason_code` in this row. Two rows for one terminalization would make "how many jobs
    did the reaper give up on" unanswerable by counting, which is the only way anybody will ask.
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=2)
    reaper = _reaper(tenant, pool)

    await _wait_for_real_expiry()
    first = await reaper.reap_once()
    assert first.reaped[0].outcome is ReapOutcome.RETRY_SCHEDULED

    # Activate attempt 2 the way production does, so that its lease can expire in turn.
    from control_plane.jobs.dispatcher import JobDispatcher

    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        lease_seconds=INSTANT_LEASE_SECONDS,
        heartbeat_seconds=0.15,
    )
    assert len(await dispatcher.activate_due_attempts()) == 1

    await _wait_for_real_expiry()
    final = await reaper.reap_once()
    assert final.reaped[0].outcome is ReapOutcome.TERMINALIZED_ATTEMPTS_EXHAUSTED

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT kind, actor_ref, organization_id, workspace_id,
                   payload->>'reason_code' AS reason_code,
                   payload->>'to_state'    AS to_state,
                   payload->>'attempt_no'  AS attempt_no
            FROM audit_events
            WHERE subject_ref = $1
            ORDER BY id
            """,
            f"job:{claim.job_id}",
        )

    assert len(rows) == 2, (
        f"reaper zostawił {len(rows)} wpisów audytu na dwie odebrane próby: "
        f"{[dict(row) for row in rows]}. Reguła jest jedna — jeden odebrany job, jeden wpis"
    )
    exhausted = [row for row in rows if row["reason_code"] == "ATTEMPTS_EXHAUSTED"]
    assert len(exhausted) == 1, (
        f"wpisów o wyczerpaniu limitu prób jest {len(exhausted)}, a ma być dokładnie jeden. "
        "Powód terminalizacji mieszka WYŁĄCZNIE w tym polu: `jobs` nie ma kolumny na powód, "
        "a status ATTEMPTS_EXHAUSTED nie istnieje w schemacie"
    )
    assert exhausted[0]["kind"] == "JOB_STATE_CHANGED"
    assert exhausted[0]["to_state"] == "FAILED"
    assert exhausted[0]["attempt_no"] == "2"
    assert exhausted[0]["actor_ref"] == "control-plane:reaper"
    assert exhausted[0]["organization_id"] == tenant["organization"]
    assert exhausted[0]["workspace_id"] == tenant["workspace"]

    expired = [row for row in rows if row["reason_code"] == "LEASE_EXPIRED"]
    assert len(expired) == 1
    assert expired[0]["kind"] == "JOB_LEASE_EXPIRED"
    assert expired[0]["to_state"] == "WAITING_RETRY"


async def test_a_job_waiting_for_its_retry_is_invisible_to_the_claim(pool, tenant) -> None:
    """The claim must NOT see a job whose replacement attempt is already scheduled.

    This is the reason the reaper parks the job in `WAITING_RETRY` instead of `READY`. The claim
    statement in `jobs/store.py` mints its own attempt row as `max(attempt_no) + 1`; if it could
    reach this job it would create attempt 3 and the reaper's attempt 2 would sit unclaimed for
    ever — one phantom attempt per retry, and half of every job's attempt budget spent on rows
    nobody ran, with `UNIQUE(job_id, attempt_no)` guarding nothing.
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=5)
    await _wait_for_real_expiry()
    assert (await _reaper(tenant, pool).reap_once()).reaped[0].outcome is (
        ReapOutcome.RETRY_SCHEDULED
    )

    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=8,
        lease_seconds=INSTANT_LEASE_SECONDS,
    )
    assert batch.claims == (), (
        f"claim pobrał joba czekającego na ponowienie: {batch.explain()}. Wiersz WAITING_* nie "
        "jest dispatchowalny (L2147) i właśnie dlatego zastępcza próba tam czeka"
    )
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT attempt_no FROM job_attempts WHERE job_id = $1 ORDER BY attempt_no",
            claim.job_id,
        )
    assert [row["attempt_no"] for row in rows] == [1, 2], (
        "claim dołożył trzeci wiersz próby do joba, który już ma zaplanowaną drugą — to jest "
        "ta pominięta zastępcza próba"
    )


async def test_a_lease_that_has_not_expired_yet_is_invisible_to_the_reaper(pool, tenant) -> None:
    """The reaper must reap EXPIRED leases — and this is the test that measures the adjective.

    Every other test in this file hands the reaper a lease that has already died, so all of them
    keep passing when `AND l.expires_at <= now()` is deleted from `_EXPIRED_JOB_SQL`: without the
    condition the reaper still collects the dead lease, just alongside every live one. Measured
    2026-08-31 by mutation — the whole suite stayed green, which made the reaper's titular
    control the only one in the package with no detection power.

    So this test comes at it from the other side: a lease with a long TTL, a reaper called
    WITHOUT waiting. Reaping it would mean tearing the job away from a worker that is still
    holding a valid lease and still working — the exact accident the fencing tuple exists to
    prevent, arriving from the reaper's own end.
    """
    store = JobStore(pool)
    await store.enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="fixture.reaper",
        routing_key="planning.daily",
        priority=0,
        attempt_limit=3,
        idempotency_key=f"reaper-live-{uuid4()}",
    )
    batch = await store.claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        # Long on purpose. The point of the test is that the lease is ALIVE when the reaper runs.
        lease_seconds=300.0,
    )
    assert len(batch.claims) == 1, batch.explain()
    claim = batch.claims[0]

    before = await _snapshot(pool, claim.job_id)
    # Liczność sprawdzana OSOBNO od predykatu: gdyby claim nie założył lease'u, „reaper niczego
    # nie ruszył" byłoby prawdą bez znaczenia. `all([])` trafiło w tym repo cztery razy.
    assert before["active_leases"] == 1, (
        f"fixture nie doprowadził do stanu, którego ten test dotyczy: aktywnych lease'ów "
        f"{before['active_leases']}, a ma być 1 — bez żywego lease'u nie ma czego nie ruszać"
    )

    report = await _reaper(tenant, pool).reap_once()

    assert report.reaped == (), (
        "reaper zabrał joba workerowi, którego lease JESZCZE ŻYJE — usunięty warunek "
        f"expires_at <= now(): {[(job.job_id, job.outcome) for job in report.reaped]}"
    )
    after = await _snapshot(pool, claim.job_id)
    assert after == before, f"reaper zmienił stan żywego joba: {before} -> {after}"


async def test_the_reaper_still_collects_a_lease_that_did_expire(pool, tenant) -> None:
    """Positive control for the test above, and it is not optional.

    A reaper wired to collect NOTHING would satisfy the previous test perfectly. The pair only
    means something together: one proves the condition is present, the other that it is not
    inverted.
    """
    claim = await _enqueue_and_claim(pool, tenant, attempt_limit=3)
    await _wait_for_real_expiry()

    report = await _reaper(tenant, pool).reap_once()

    assert len(report.reaped) == 1, (
        f"reaper nie zebrał lease'u, który naprawdę wygasł — zebrał {len(report.reaped)}"
    )
    assert report.reaped[0].job_id == claim.job_id
