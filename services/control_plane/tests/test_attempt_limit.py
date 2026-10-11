"""The attempt limit: exactly N attempts, then terminalization — never an endless redispatch.

    "lease expiry respektuje attempt limit; ostatnia próba terminalizuje job zamiast tworzyć
     nieskończony redispatch" (L5214)
    "Poniżej limitu zwiększa `lease_generation` i `dispatch_version`, tworzy replacement
     `job_attempt(attempt_no+1, not_before=backoff)`. Po wyczerpaniu limitu terminalizuje job […]
     — bez kolejnej próby." (L2669)

WHY THE ASSERTION COUNTS ROWS IN `job_attempts` AND NOT THE JOB'S STATUS. Both off-by-one bugs
leave a TERMINAL job behind:

*   `attempt_no > limit` instead of `>=` creates one replacement too many and THEN terminalizes,
    so the job ends `FAILED` with N+1 attempt rows. A test reading only the status would pass.
*   a limit compared against the wrong number terminalizes one attempt early, so the job ends
    `FAILED` with N-1 rows and one paid-for attempt nobody ever ran.

Counting rows catches both directions; reading the status catches neither. This is the same class
of false pass as `all([])` being `True` (KONWENCJE.md) — the assertion holds for a reason that has
nothing to do with the mechanism.

The expiry is REAL. `expires_at` cannot be moved backwards — migration 0003 forbids it with
`CHECK (expires_at > acquired_at)`, deliberately — so these tests take leases with a 400 ms TTL
and wait, the `INSTANT_TTL` pattern already used by `tests/test_job_lease_fencing.py`.

The backoff is REAL too, and drawn from a jitter source that always returns the bottom of the
interval. That is not a mock of the mechanism: full jitter draws from `[0, ceiling]`, so zero is a
value it really produces, and D-017 froze full jitter precisely so that the retries of one outage
do not arrive in the same second.
"""

from __future__ import annotations

import asyncio
import random
from uuid import UUID, uuid4

import pytest
from control_plane.jobs import backoff
from control_plane.jobs.dispatcher import JobDispatcher
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore

pytestmark = [pytest.mark.db]

#: Short enough to wait for a real expiry, long enough to claim under. Same reasoning as
#: `INSTANT_LEASE_SECONDS` in `tests/test_job_lease_fencing.py`.
INSTANT_LEASE_SECONDS = 0.4

#: The heartbeat must be shorter than the TTL or the dispatcher refuses to be constructed.
INSTANT_HEARTBEAT_SECONDS = 0.15


class _BottomOfTheInterval(random.Random):
    """A full-jitter source that always draws the bottom of `[0, ceiling]`.

    Full jitter really can return zero, so this is one of the mechanism's own outcomes rather
    than a stub of it — which matters, because a test that replaced the backoff with "no delay"
    would no longer be exercising the code path that computes one.
    """

    def uniform(self, a: float, b: float) -> float:  # opis w docstringu klasy
        return a


async def _wait_for_real_expiry() -> None:
    await asyncio.sleep(INSTANT_LEASE_SECONDS + 0.4)


async def _enqueue(pool, tenant, *, attempt_limit: int, job_type: str = "fixture.limit") -> UUID:
    """One job with the attempt limit this test wants. Through the store, not raw SQL.

    The `insert_job` fixture in `conftest.py` hardcodes `attempt_limit = 5`, which is the frozen
    D-017 value and exactly what these tests must vary.
    """
    enqueued = await JobStore(pool).enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=job_type,
        routing_key="planning.daily",
        priority=0,
        attempt_limit=attempt_limit,
        idempotency_key=f"limit-{uuid4()}",
    )
    assert enqueued.created, "job o świeżym kluczu idempotencji nie powstał"
    return enqueued.job_id


async def _claim_one(pool, tenant):
    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        lease_seconds=INSTANT_LEASE_SECONDS,
    )
    assert len(batch.claims) == 1, batch.explain()
    return batch.claims[0]


async def _attempt_rows(pool, job_id: UUID):
    async with pool.acquire() as connection:
        return await connection.fetch(
            "SELECT attempt_no, status, failure_class, not_before FROM job_attempts "
            "WHERE job_id = $1 ORDER BY attempt_no",
            job_id,
        )


async def _job_status(pool, job_id: UUID) -> str:
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)


# ---------------------------------------------------------------------------
# The thresholds themselves
# ---------------------------------------------------------------------------
# These two do not touch the database and are marked `db` only because the module is. They cost
# milliseconds and they are the reason a changed threshold is a failing test rather than a
# surprise in a soak run six weeks later (D-017: "testy DoD parametryzują się po nich, żeby zmiana
# progu psuła test, a nie przechodziła niezauważona").


def test_the_frozen_thresholds_are_the_ones_d_017_wrote_down() -> None:
    """The five numbers D-017 froze BEFORE the queue was written, compared as a table.

    Written out here as literals on purpose. Importing the constants and asserting they equal
    themselves is the shape of a test that measures nothing; the decision entry is the other
    source, and this is where the two are held against each other.
    """
    assert backoff.QUEUE_THRESHOLDS == {
        "attempt_limit": 5,
        "lease_ttl_seconds": 60.0,
        "heartbeat_interval_seconds": 15.0,
        "reaper_interval_seconds": 10.0,
        "backoff_ceiling_seconds": (5.0, 15.0, 60.0, 300.0, 900.0),
    }, (
        "tabela progów kolejki rozjechała się z D-017. Zmiana progu jest legalna, ale wymaga "
        "wpisu w dzienniku decyzji i UNIEWAŻNIA wcześniejsze przebiegi soaka — nie wolno jej "
        "przepuścić po cichu"
    )
    # The heartbeat is a quarter of the TTL, so three missed beats fit before the lease dies. A
    # heartbeat at or above the TTL renews nothing, which is why the dispatcher refuses one.
    assert backoff.HEARTBEAT_INTERVAL_SECONDS * 4 == backoff.LEASE_TTL_SECONDS
    assert backoff.REAPER_INTERVAL_SECONDS < backoff.LEASE_TTL_SECONDS, (
        "pętla reapera dłuższa niż TTL lease'u znaczy, że wygasły lease czeka pełny cykl"
    )


@pytest.mark.parametrize(
    ("failed_attempt_no", "ceiling"),
    [(1, 5.0), (2, 15.0), (3, 60.0), (4, 300.0), (5, 900.0), (6, 900.0), (99, 900.0)],
)
def test_the_backoff_ceiling_follows_the_frozen_schedule(
    failed_attempt_no: int, ceiling: float
) -> None:
    """The mapping from attempt number to ceiling, pinned — including the repeat past the end."""
    assert backoff.backoff_ceiling_seconds(failed_attempt_no) == ceiling


def test_the_backoff_is_full_jitter_and_stays_under_the_ceiling() -> None:
    """Full jitter means the WHOLE interval `[0, ceiling]`, and D-017 says full, not half.

    Two things are measured, and the second is the one that matters. A "jitter" that always
    returned the ceiling, or always half of it, would satisfy the bounds check alone — so the
    test also requires the draws to actually spread below half the ceiling, which is what stops
    every job of one outage from coming back in the same second.
    """
    source = random.Random(20260831)
    draws = [backoff.backoff_seconds(2, jitter=source) for _ in range(200)]

    assert len(draws) == 200, "pusta próbka: all() po pustym zbiorze jest prawdziwe (KONWENCJE.md)"
    assert all(0.0 <= draw <= 15.0 for draw in draws), (
        f"losowanie wyszło poza sufit 15 s: min {min(draws)}, max {max(draws)}"
    )
    assert min(draws) < 7.5 < max(draws), (
        f"losowania nie rozłożyły się po całym przedziale (min {min(draws):.2f}, "
        f"max {max(draws):.2f}) — to nie jest PEŁNY jitter, a D-017 wymaga pełnego"
    )


def test_the_backoff_refuses_an_attempt_number_below_one() -> None:
    """Attempts number from 1 (0003: `CHECK (attempt_no >= 1)`), so zero is not a question."""
    with pytest.raises(ValueError, match="failed_attempt_no"):
        backoff.backoff_seconds(0, jitter=random.Random(0))


# ---------------------------------------------------------------------------
# The limit itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attempt_limit", [1, 2, 3])
async def test_exactly_n_attempts_and_no_more(pool, tenant, attempt_limit: int) -> None:
    """A job whose lease keeps expiring gets EXACTLY `attempt_limit` attempts, then dies.

    The cycle is the production one end to end: the claim creates attempt 1, the lease really
    expires, the reaper closes the attempt and schedules the replacement, the dispatcher activates
    it. Nothing here writes a `job_attempts` row by hand — a test that inserted attempts itself
    would count its own rows.
    """
    job_id = await _enqueue(pool, tenant, attempt_limit=attempt_limit)
    reaper = JobLeaseReaper(
        pool, organization_id=tenant["organization"], jitter=_BottomOfTheInterval()
    )
    dispatcher = JobDispatcher(
        pool,
        organization_id=tenant["organization"],
        lease_seconds=INSTANT_LEASE_SECONDS,
        heartbeat_seconds=INSTANT_HEARTBEAT_SECONDS,
    )

    claim = await _claim_one(pool, tenant)
    assert claim.job_id == job_id
    assert claim.attempt_no == 1

    # Every attempt below the limit must get a replacement, and every replacement must be
    # activatable. `range` here is empty for `attempt_limit == 1`, which is the point of
    # parameterizing over 1: a reaper that always creates one replacement fails at N == 1.
    for expected_attempt_no in range(2, attempt_limit + 1):
        await _wait_for_real_expiry()
        report = await reaper.reap_once()
        assert len(report.reaped) == 1, report.explain()
        reaped = report.reaped[0]
        assert reaped.outcome is ReapOutcome.RETRY_SCHEDULED, (
            f"próba {reaped.attempt_no} z limitu {attempt_limit} nie dostała zastępczej: "
            f"{reaped.outcome}"
        )
        assert reaped.replacement_attempt_no == expected_attempt_no
        assert reaped.delay_seconds == 0.0

        activated = await dispatcher.activate_due_attempts()
        assert len(activated) == 1, (
            f"zaplanowana próba {expected_attempt_no} nie dała się aktywować — job stoi "
            "w WAITING_RETRY z próbą, po którą nikt nie przychodzi"
        )
        assert activated[0].job_id == job_id
        assert activated[0].attempt_no == expected_attempt_no
        assert await _job_status(pool, job_id) == "RUNNING"

    # The last attempt expires and terminalizes the job.
    await _wait_for_real_expiry()
    final = await reaper.reap_once()
    assert len(final.reaped) == 1, final.explain()
    assert final.reaped[0].outcome is ReapOutcome.TERMINALIZED_ATTEMPTS_EXHAUSTED
    assert final.reaped[0].attempt_no == attempt_limit
    assert final.reaped[0].replacement_attempt_no is None

    rows = await _attempt_rows(pool, job_id)
    assert [row["attempt_no"] for row in rows] == list(range(1, attempt_limit + 1)), (
        f"job z limitem {attempt_limit} ma próby {[row['attempt_no'] for row in rows]}. "
        "Liczba WIERSZY w job_attempts jest tu dowodem, a nie status joba: obie pomyłki "
        "o jeden zostawiają job terminalny"
    )
    assert len(rows) == attempt_limit
    assert [row["status"] for row in rows] == ["LEASE_EXPIRED"] * attempt_limit
    assert await _job_status(pool, job_id) == "FAILED"

    # AND NO MORE. Another reaper pass and another activation pass must find nothing: a job that
    # keeps producing attempts after terminalization is the endless redispatch of L5214 with an
    # extra step.
    await _wait_for_real_expiry()
    assert (await reaper.reap_once()).reaped == ()
    assert await dispatcher.activate_due_attempts() == ()
    assert len(await _attempt_rows(pool, job_id)) == attempt_limit


async def test_the_replacement_attempt_carries_a_higher_generation_and_dispatch_version(
    pool, tenant
) -> None:
    """The replacement is a NEW issue of the job: both counters grow (L2669).

    `dispatch_version` on the job and `lease_generation` on the attempt. The generation is what
    makes a token from the previous attempt refuse to write (L2666-2668), so a replacement that
    reused it would let a worker whose lease was reaped finish as if nothing had happened.
    """
    job_id = await _enqueue(pool, tenant, attempt_limit=3)
    claim = await _claim_one(pool, tenant)
    async with pool.acquire() as connection:
        dispatch_version_before = await connection.fetchval(
            "SELECT dispatch_version FROM jobs WHERE id = $1", job_id
        )

    await _wait_for_real_expiry()
    reaper = JobLeaseReaper(
        pool, organization_id=tenant["organization"], jitter=_BottomOfTheInterval()
    )
    assert (await reaper.reap_once()).reaped[0].outcome is ReapOutcome.RETRY_SCHEDULED

    async with pool.acquire() as connection:
        job = await connection.fetchrow(
            "SELECT status, dispatch_version, not_before FROM jobs WHERE id = $1", job_id
        )
        replacement = await connection.fetchrow(
            "SELECT attempt_no, dispatch_version, lease_generation, not_before, status "
            "FROM job_attempts WHERE job_id = $1 AND attempt_no = 2",
            job_id,
        )
        old_lease = await connection.fetchrow(
            "SELECT status, failure_class FROM job_leases WHERE attempt_id = $1", claim.attempt_id
        )

    assert job["dispatch_version"] == dispatch_version_before + 1
    assert job["status"] == "WAITING_RETRY", (
        "job po odroczeniu MUSI być niedispatchowalny. Zostawiony w READY zostałby pobrany "
        "przez claim_batch, który wstawiłby WŁASNY wiersz próby — a zastępcza próba reapera "
        "zostałaby pominięta na zawsze"
    )
    assert replacement["dispatch_version"] == job["dispatch_version"]
    assert replacement["lease_generation"] == claim.lease_generation + 1
    assert replacement["status"] == "RUNNING"
    # The job and its scheduled attempt must agree on when the retry may start, to the
    # microsecond: both come from the same `now()` inside one transaction.
    assert replacement["not_before"] == job["not_before"]
    assert old_lease["status"] == "EXPIRED"
    assert old_lease["failure_class"] == "LEASE_EXPIRED"
