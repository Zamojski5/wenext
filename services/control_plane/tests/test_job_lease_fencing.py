"""Fencing of the job lease: CAS on the FULL tuple, against a REAL PostgreSQL (L2666-2668).

The rule this file enforces, quoted from the document because the wording is the specification:

    "Każdy heartbeat, transition, wynik i powiązany outbox zapisuje CAS-em po
     `(job_id, attempt_id, lease_generation, lease_owner, expected_status)`; expiry i heartbeat
     używają czasu PostgreSQL, nie zegara workera. Zero zaktualizowanych wierszy oznacza
     utracony fencing token i wynik starego workera jest odrzucany." (L2666-2668)

Three things about the shape of these tests:

1.  **The assertions are on the DATABASE, not on the exception.** `pytest.raises` proves that a
    refusal was announced; it does not prove that nothing was written. A CAS that raised AFTER
    updating the row would pass an exception-only test — and the write it let through is the
    whole failure.

2.  **The expiry is REAL.** `expires_at` cannot be moved backwards: migration 0003 blocks it
    with `CHECK (expires_at > acquired_at)`, deliberately, because a lease dead on arrival is
    an endless redispatch. So the test takes a lease with a 400 ms TTL and waits — the
    `INSTANT_TTL` pattern from `services/profile_lease/tests/test_lease_fencing.py` around
    line 39.

3.  **The takeover is done with raw SQL on purpose.** Bumping the generation belongs to the
    reaper, which is a sibling package. Reproducing it here in SQL keeps this file from
    depending on code it does not own, and keeps the reaper from being able to make these tests
    pass by changing its own mind about what a takeover is.

---------------------------------------------------------------------------------------------
WHY THE SECOND HALF OF THIS FILE EXISTS: THE FIRST HALF MEASURED PART OF THE TUPLE
---------------------------------------------------------------------------------------------

The three tests above prove that a stale worker loses. They do NOT prove that the tuple is five
fields long, and that difference was MEASURED rather than argued. Run while writing the second
half: with `l.attempt_id = $2` stopped from fencing in all four writing statements, those three
tests report `3 passed, 0 failed`; with `l.lease_owner = $4` stopped from fencing, `3 passed,
0 failed` again. Two elements of the tuple could be removed and this file stayed green. The
reason is the one thing to remember about test data:

    EVERY stale token in the three tests above differs from the live lease in `lease_generation`
    TOO. So `lease_generation` alone rejects it, the CAS never reaches the other conditions, and
    a test that believes it is measuring `attempt_id` is measuring its neighbour.

The second half is built the opposite way round. For each of the six conditions there is ONE test
whose forged token differs from the live lease in EXACTLY that one thing, so nothing else in the
WHERE can do the rejecting:

    job_id            a token carrying ANOTHER job's attempt, generation and owner
    attempt_id        a look-alike attempt row: same job, same generation, same owner
    lease_generation  a takeover that deliberately KEEPS the attempt and the owner
    lease_owner       another live lease's owner, on this job's own attempt and generation
    expected_status   the current token, on a job somebody moved out of RUNNING
    expires_at        the current token, after a REAL expiry, lease row still `ACTIVE`

`_assert_the_forgery_isolates_one_element` states that isolation as an assertion instead of a
comment, and it fails if the fixture stops isolating — because a fixture that quietly stops
reaching the condition is exactly how the previous version of this file passed.

Each of the six tests drives ALL FOUR writing statements (heartbeat, result, park, requeue) with
the same forged token and reports EVERY statement that let the write through, rather than
stopping at the first. So one deletion produces one red test that NAMES the statements which lost
the guard, and deleting a condition from a single query is as visible as deleting it from four.

MEASURED, not claimed: each of the six was run against a mutated `store.py`, seen red, and the
mutation reverted. All six name all four statements in their failure message.

The two tests after them cover the two facts nothing asserted at all — `EnqueuedJob.created is
False` on a deduplicated key, and `holds_live_lease` on a lease past its deadline but still marked
`ACTIVE`, which is the value the operator's screen reads. Both gaps were measured the same way:
with those two tests deselected, each mutation passes the WHOLE package (`190 passed, 0 failed`).

ONE NOTE ON HOW THE MUTATIONS WERE DONE, because it changes what they prove. Deleting
`l.job_id = $1` outright leaves $1 bound and unreferenced, and PostgreSQL then refuses to prepare
the statement at all ("could not determine data type of parameter $1"). That is red, but red
because the driver noticed an unused parameter — not because this suite noticed a lost guard. So
the four identifiers were NEUTRALISED instead (`$1::uuid IS NOT NULL`), which leaves valid SQL
that fences nothing. That the plain deletion also fails is a second, independent line of defence,
and it is worth knowing it exists — but it is not the one these tests are here to be.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from community_os_contracts import JobFencingToken, JobWaitingState
from control_plane.domain import commands
from control_plane.domain.errors import JobFencingRefused
from control_plane.domain.models import JobOutcome
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

#: SCENARIUSZ 2: utrata lease'u i zombie. Stary worker nie zapisuje nic po przejęciu joba.
pytestmark = [pytest.mark.db, pytest.mark.p0]

LONG_LEASE_SECONDS = 60.0

#: The status a job must be in for a fenced write to be accepted — the fifth element of the tuple
#: (L2666). Spelled out here rather than imported from `jobs.store`: a test that reads the
#: threshold it is checking out of the code under test agrees with that code by construction.
RUNNING = "RUNNING"

#: Short enough that waiting for a REAL expiry is feasible; the same reasoning as `INSTANT_TTL`
#: in the profile lease suite. Moving `expires_at` back in the database is impossible, and
#: rightly so — `CHECK (expires_at > acquired_at)` in 0003 forbids a lease that is already dead.
INSTANT_LEASE_SECONDS = 0.4


async def _wait_for_real_expiry() -> None:
    await asyncio.sleep(INSTANT_LEASE_SECONDS + 0.4)


#: `pool` and `tenant` come from `conftest.py`; `tenant` is a fresh organization per test, which
#: matters because the whole suite shares one throwaway PostgreSQL.


async def _claim_one(pool, tenant, *, lease_seconds: float = LONG_LEASE_SECONDS):
    """Enqueues one job through the domain command and claims it. Returns the claim."""
    port = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    await commands.enqueue_job(
        port,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="fixture.fencing",
        routing_key="planning.daily",
        priority=0,
        attempt_limit=5,
        idempotency_key=f"fencing-{uuid4()}",
    )
    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        lease_seconds=lease_seconds,
    )
    assert len(batch.claims) == 1, batch.claims
    return batch.claims[0]


async def _reaper_style_takeover(pool, claim) -> JobFencingToken:
    """What the reaper will do, written here in SQL: close the old lease, open generation N+1.

    Not a call into the reaper package — this file must not be able to pass because a sibling
    package redefined "takeover". The steps are the ones L2669 lists: lock the expired job,
    close the previous attempt as `LEASE_EXPIRED`, raise `lease_generation` and create the
    replacement attempt.
    """
    async with pool.acquire() as conn, conn.transaction():
        closed = await conn.execute(
            "UPDATE job_leases SET status = 'EXPIRED' WHERE job_id = $1 AND status = 'ACTIVE'",
            claim.job_id,
        )
        assert closed == "UPDATE 1", closed
        closed_attempt = await conn.execute(
            """
            UPDATE job_attempts SET status = 'LEASE_EXPIRED', completed_at = now()
            WHERE attempt_id = $1 AND status = 'RUNNING'
            """,
            claim.attempt_id,
        )
        assert closed_attempt == "UPDATE 1", closed_attempt
        row = await conn.fetchrow(
            """
            INSERT INTO job_attempts
                (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                 lease_owner, status, heartbeat_at, expires_at)
            VALUES ($1, $2, $3, $4, $5, gen_random_uuid(), 'RUNNING', now(),
                    now() + make_interval(secs => $6::double precision))
            RETURNING attempt_id, lease_generation, lease_owner, expires_at
            """,
            claim.organization_id,
            claim.job_id,
            claim.attempt_no + 1,
            claim.dispatch_version + 1,
            claim.lease_generation + 1,
            LONG_LEASE_SECONDS,
        )
        await conn.execute(
            """
            INSERT INTO job_leases
                (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                 lease_generation, lease_owner, status, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'ACTIVE', $8)
            """,
            claim.organization_id,
            claim.job_id,
            row["attempt_id"],
            claim.attempt_no + 1,
            claim.dispatch_version + 1,
            row["lease_generation"],
            row["lease_owner"],
            row["expires_at"],
        )
    return JobFencingToken(
        job_id=claim.job_id,
        attempt_id=row["attempt_id"],
        lease_generation=row["lease_generation"],
        lease_owner=row["lease_owner"],
    )


async def test_heartbeat_after_expiry_does_not_resurrect_the_lease(pool, tenant) -> None:
    """A heartbeat is a renewal, never a resurrection — hence `expires_at > now()` in the CAS.

    Without that condition the CAS still matches on the four identifiers, so a worker that was
    frozen for two minutes renews a lease the reaper is entitled to have taken. Two workers then
    believe they hold the same job: the failure L5211 forbids, arriving through the back door.

    The wait is real. `expires_at` cannot be pushed into the past — 0003 blocks it with
    `CHECK (expires_at > acquired_at)` — so the only honest way to be past the expiry is to be
    past it.
    """
    claim = await _claim_one(pool, tenant, lease_seconds=INSTANT_LEASE_SECONDS)
    store = JobStore(pool)
    token = claim.fencing_token

    # The same call succeeds while the lease is alive. Without this half, a heartbeat that
    # NEVER works would satisfy the refusal below.
    alive = await store.heartbeat(token, lease_seconds=INSTANT_LEASE_SECONDS)
    assert alive is not None

    await _wait_for_real_expiry()

    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at, status, expires_at > now() AS still_alive
            FROM job_leases WHERE job_id = $1
            """,
            claim.job_id,
        )
    assert before["still_alive"] is False, "lease jeszcze nie wygasł — test nie mierzy tego, co ma"

    with pytest.raises(JobFencingRefused):
        await store.heartbeat(token, lease_seconds=LONG_LEASE_SECONDS)

    async with pool.acquire() as conn:
        after = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at, status, expires_at > now() AS still_alive
            FROM job_leases WHERE job_id = $1
            """,
            claim.job_id,
        )

    # THE ASSERTION IS ON THE ROW. An exception alone would also be raised by a CAS that updated
    # the row and then complained about it.
    assert after["expires_at"] == before["expires_at"], (
        "heartbeat po wygaśnięciu PRZEDŁUŻYŁ lease — CAS bez warunku expires_at > now() "
        "wskrzesza lease, który reaper ma prawo już zabrać"
    )
    assert after["heartbeat_at"] == before["heartbeat_at"]
    assert after["still_alive"] is False


async def test_a_zombie_worker_loses_the_write_after_a_generation_bump(pool, tenant) -> None:
    """Worker A loses SEPARATELY at the heartbeat and SEPARATELY at the result write.

    Two losses, because they are two different writes and each one has to carry its own CAS.
    A store that fenced the heartbeat and left the result unfenced would publish the stale
    worker's verdict onto a job somebody else is already running — "wynik starego workera jest
    odrzucany" (L2668) is about the result, not only about the renewal.

    The assertion each time is that THE WRITE IS NOT THERE, not that an exception was raised.
    """
    claim = await _claim_one(pool, tenant)
    zombie = claim.fencing_token
    store = JobStore(pool)

    fresh = await _reaper_style_takeover(pool, claim)
    assert fresh.lease_generation == zombie.lease_generation + 1
    assert fresh.lease_owner != zombie.lease_owner
    assert fresh.attempt_id != zombie.attempt_id

    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at FROM job_leases
            WHERE job_id = $1 AND status = 'ACTIVE'
            """,
            claim.job_id,
        )

    # 1. The heartbeat.
    with pytest.raises(JobFencingRefused):
        await store.heartbeat(zombie, lease_seconds=LONG_LEASE_SECONDS)

    async with pool.acquire() as conn:
        after_heartbeat = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at FROM job_leases
            WHERE job_id = $1 AND status = 'ACTIVE'
            """,
            claim.job_id,
        )
    assert after_heartbeat["heartbeat_at"] == before["heartbeat_at"], (
        "zombie odświeżył lease bieżącego workera — krotka CAS nie zawiera generacji albo "
        "właściciela"
    )
    assert after_heartbeat["expires_at"] == before["expires_at"]

    # 2. The result write, which is the one that would corrupt the audit trail.
    with pytest.raises(JobFencingRefused):
        await commands.report_result(
            PostgresJobTransport(store, organization_id=claim.organization_id),
            token=zombie,
            outcome=JobOutcome.SUCCEEDED,
        )

    async with pool.acquire() as conn:
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", claim.job_id)
        zombie_attempt = await conn.fetchrow(
            "SELECT status, completed_at FROM job_attempts WHERE attempt_id = $1",
            zombie.attempt_id,
        )
        live_attempt = await conn.fetchval(
            "SELECT status FROM job_attempts WHERE attempt_id = $1", fresh.attempt_id
        )
        succeeded_rows = await conn.fetchval(
            "SELECT count(*) FROM job_attempts WHERE job_id = $1 AND status = 'SUCCEEDED'",
            claim.job_id,
        )

    assert job_status == "RUNNING", (
        f"job jest w stanie {job_status} — zombie zamknął joba, którego nie miał prawa tknąć"
    )
    assert succeeded_rows == 0, "gdzieś zapisano SUCCEEDED z tokenu, który stracił fencing"
    assert zombie_attempt["status"] == "LEASE_EXPIRED", zombie_attempt["status"]
    assert live_attempt == "RUNNING", live_attempt

    # And the current holder still works — otherwise "the write did not land" would be
    # satisfied by a store in which nothing lands at all.
    assert await store.heartbeat(fresh, lease_seconds=LONG_LEASE_SECONDS) is not None


async def test_cas_on_job_id_and_status_alone_lets_a_zombie_through(pool, tenant) -> None:
    """Why the tuple is five fields long (L2668), and why `guard()` from `profile_lease` is not it.

    `ProfileLeaseStore.guard` (`services/profile_lease/profile_lease/store.py`, around line 269)
    does two things this store must not copy:

      * it compares the GENERATION ALONE, without `attempt_id` and without `lease_owner`;
      * it is a SELECT followed by a comparison in Python — a read and then a write, with a gap
        between them. For a profile guarded before every Gateway call that is a deliberate
        trade; for a job whose result is written once and audited forever it is a hole.

    This test walks through that hole on purpose, and then shows the full-tuple CAS closing it:

      1. the weak guard says worker A is current — and it is, at that instant;
      2. the reaper takes over and generation 2 becomes the live lease;
      3. worker A writes under a CAS reduced to `(job_id, status)` — AND THE WRITE LANDS,
         on the new holder's lease row;
      4. worker A writes through the store's real CAS — and nothing moves.
    """
    claim = await _claim_one(pool, tenant)
    zombie = claim.fencing_token
    store = JobStore(pool)

    # 1. The `guard()` shape: read the live generation, compare it in Python.
    async with pool.acquire() as conn:
        live_generation = await conn.fetchval(
            """
            SELECT lease_generation FROM job_leases
            WHERE job_id = $1 AND status = 'ACTIVE' AND expires_at > now()
            """,
            claim.job_id,
        )
    assert live_generation == zombie.lease_generation, (
        "wzorzec guard() przepuszcza workera A w tej chwili — i słusznie, bo A JEST aktualny. "
        "Dziura jest w tym, co dzieje się między tym sprawdzeniem a zapisem"
    )

    # 2. The takeover happens in the gap.
    fresh = await _reaper_style_takeover(pool, claim)

    # 3. The shortened tuple. asyncpg returns the STRING "UPDATE 0" for a CAS that matched
    #    nothing and DOES NOT RAISE — so the row count is read here explicitly, which is the
    #    only way this distinction is ever visible in database code.
    async with pool.acquire() as conn:
        weak_status = await conn.execute(
            """
            UPDATE job_leases SET heartbeat_at = now()
            WHERE job_id = $1 AND status = 'ACTIVE'
            """,
            claim.job_id,
        )
    assert weak_status == "UPDATE 1", (
        f"skrócona krotka (job_id, status) zwróciła {weak_status!r}. Ten test istnieje po to, "
        "żeby pokazać, że TA krotka PRZEPUSZCZA zombie — jeżeli przestała, to znaczy, że "
        "scenariusz przestał odtwarzać przejęcie i pełna krotka nie jest już przez nic broniona"
    )

    # 4. The real CAS, on the same job, with the same stale token.
    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at, lease_generation FROM job_leases
            WHERE job_id = $1 AND status = 'ACTIVE'
            """,
            claim.job_id,
        )
    with pytest.raises(JobFencingRefused) as refusal:
        await store.heartbeat(zombie, lease_seconds=LONG_LEASE_SECONDS)
    async with pool.acquire() as conn:
        after = await conn.fetchrow(
            """
            SELECT heartbeat_at, expires_at, lease_generation FROM job_leases
            WHERE job_id = $1 AND status = 'ACTIVE'
            """,
            claim.job_id,
        )

    assert after["heartbeat_at"] == before["heartbeat_at"]
    assert after["expires_at"] == before["expires_at"]
    assert after["lease_generation"] == fresh.lease_generation

    # The refusal names the live lease, so the operator reads WHO holds the job now instead of
    # "CAS failed". The evidence comes from `contracts.require_fresh_job_lease`, chained.
    assert refusal.value.live is not None
    assert refusal.value.live.lease_generation == fresh.lease_generation
    assert refusal.value.presented == zombie
    assert refusal.value.__cause__ is not None, (
        "odmowa nie niesie przyczyny z contracts.require_fresh_job_lease — bez tego w kodzie "
        "istnieje druga kopia reguły świeżości tokenu"
    )


# =============================================================================================
# One element of the tuple at a time
# =============================================================================================
# Four helpers, and all four exist to make the thing being measured impossible to confuse with
# something else:
#
#   _queue_snapshot                          — what "nothing was written" means, as data
#   _assert_the_forgery_isolates_one_element — the fixture reaches the condition under test
#   Forgery                                  — one freshly built scenario for ONE statement
#   _every_fenced_write_is_refused           — all four statements, every failure reported

#: The four writing statements of `jobs/store.py`, named as the store names them. The count is
#: asserted wherever they are driven: a loop over an empty sequence reports no problems at all,
#: and `all([])` has already cost this project four times (KONWENCJE.md).
FENCED_WRITES: tuple[str, ...] = (
    "heartbeat (_HEARTBEAT_SQL)",
    "wynik (_CLOSE_ATTEMPT_SQL)",
    "wstrzymanie (_PARK_SQL)",
    "oddanie do kolejki (_REQUEUE_SQL)",
)

#: The four identifiers of the fencing tuple, in the order L2666 lists them.
TOKEN_ELEMENTS: tuple[str, ...] = ("job_id", "attempt_id", "lease_generation", "lease_owner")


class Forgery(NamedTuple):
    """One scenario, built fresh for ONE statement: see `_every_fenced_write_is_refused`.

    `working` is the token that DOES hold the job, and the same statement is run with it after
    the refusal — so "the write was refused" cannot be satisfied by a statement that refuses
    everything. `None` means the fixture destroyed its own lease on purpose (the expiry test) and
    ran its positive control before doing so.

    `repair` runs before that positive control, for the one scenario that deliberately left the
    job in a state no write may touch.
    """

    forged: JobFencingToken
    victim_job_id: UUID
    working: JobFencingToken | None
    repair: Callable[[], Awaitable[object]] | None = None


def _a_moment_requeue_can_be_given() -> datetime:
    """An argument for `requeue`, not a deadline this file depends on.

    The store clamps it with `greatest(…, now())` on the server, so the value only has to be a
    timestamp. This is the one place a test computes a moment from the process clock, and it is
    allowed here precisely because the database overrides it.
    """
    return datetime.now(tz=UTC) + timedelta(seconds=30)


async def _queue_snapshot(pool, organization_id: UUID) -> dict[str, list[tuple]]:
    """Every queue row of one organization, as comparable tuples. `after == before` is the proof.

    Whole-organization rather than one row on purpose: under a neutralised `job_id` condition the
    write lands on ANOTHER job's lease, and a snapshot of the job under test would not see it.

    The emptiness check is not decoration. Two empty snapshots compare equal, so a snapshot that
    silently returned nothing would make every "nothing was written" assertion in this file pass
    — the `all([])` failure wearing a different hat.
    """
    async with pool.acquire() as conn:
        jobs = await conn.fetch(
            """
            SELECT id, status, not_before, dispatch_version, lock_version
            FROM jobs WHERE organization_id = $1 ORDER BY id
            """,
            organization_id,
        )
        attempts = await conn.fetch(
            """
            SELECT attempt_id, status, lease_generation, lease_owner, heartbeat_at, expires_at,
                   completed_at, failure_class
            FROM job_attempts WHERE organization_id = $1 ORDER BY attempt_id
            """,
            organization_id,
        )
        leases = await conn.fetch(
            """
            SELECT id, job_id, attempt_id, status, lease_generation, lease_owner, heartbeat_at,
                   expires_at, completed_at, failure_class
            FROM job_leases WHERE organization_id = $1 ORDER BY id
            """,
            organization_id,
        )
    snapshot = {
        "jobs": [tuple(row) for row in jobs],
        "attempts": [tuple(row) for row in attempts],
        "leases": [tuple(row) for row in leases],
    }
    assert snapshot["jobs"], "pusty snapshot jobów — porównanie „nic się nie zmieniło” byłoby puste"
    assert snapshot["attempts"], "pusty snapshot prób — jak wyżej, porównanie nic nie mierzy"
    assert snapshot["leases"], "pusty snapshot lease'ów — jak wyżej, porównanie nic nie mierzy"
    return snapshot


async def _live_lease_row(pool, job_id: UUID):
    """The `ACTIVE` lease of a job together with the job's status, or `None`.

    Deliberately does NOT filter on `expires_at`: the expiry test needs to see a row that is
    still `ACTIVE` and already dead, which is the state the reaper has not reached yet.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT l.attempt_id, l.lease_generation, l.lease_owner, l.status AS lease_status,
                   l.expires_at > now() AS lease_is_live, j.status AS job_status
            FROM job_leases l JOIN jobs j ON j.id = l.job_id
            WHERE l.job_id = $1 AND l.status = 'ACTIVE'
            """,
            job_id,
        )


async def _assert_the_forgery_isolates_one_element(
    pool, *, forged: JobFencingToken, victim_job_id: UUID, isolated: str
) -> None:
    """Assert that only `isolated` can make the CAS refuse this token. The rule of this file.

    `victim_job_id` is the job whose `ACTIVE` lease the write would land on if `isolated` stopped
    fencing — the same job for five of the six cases, ANOTHER job for `job_id`.

    Checked here, so that a fixture which stops reaching the condition fails loudly instead of
    passing for the wrong reason:

      * the lease that could be hit exists and is `ACTIVE` — otherwise `l.status = 'ACTIVE'`
        rejects the write and nothing else in the WHERE is ever evaluated;
      * it is alive, unless `expires_at` is the element under test;
      * its job is `RUNNING`, unless `expected_status` is the element under test;
      * of the four identifiers, EXACTLY the one under test differs (none, when the element under
        test is not an identifier).

    Without this function the second half of this file could repeat the mistake the first half
    made — every token differing in `lease_generation` as well, so five conditions look measured
    and one is doing all the work.
    """
    assert isolated in (*TOKEN_ELEMENTS, "expected_status", "expires_at"), isolated
    row = await _live_lease_row(pool, victim_job_id)
    assert row is not None, (
        f"job {victim_job_id} nie ma lease'u ACTIVE — bez wiersza, na który zapis MÓGŁBY trafić, "
        f"test „{isolated} broni krotki” nie mierzy niczego"
    )
    assert row["lease_status"] == "ACTIVE", row["lease_status"]

    if isolated == "expires_at":
        assert row["lease_is_live"] is False, (
            "lease jeszcze nie wygasł — warunek expires_at > now() nie jest tym, co odrzuca"
        )
    else:
        assert row["lease_is_live"] is True, (
            f"lease jest już martwy, więc odmowę wywoła expires_at > now(), a nie {isolated}"
        )

    if isolated == "expected_status":
        assert row["job_status"] != RUNNING, (
            "job jest RUNNING, więc expected_status nie jest tym, co odrzuca"
        )
    else:
        assert row["job_status"] == RUNNING, (
            f"job jest w stanie {row['job_status']}, więc odmowę wywoła expected_status, "
            f"a nie {isolated}"
        )

    presented = {
        "job_id": forged.job_id,
        "attempt_id": forged.attempt_id,
        "lease_generation": forged.lease_generation,
        "lease_owner": forged.lease_owner,
    }
    live = {
        "job_id": victim_job_id,
        "attempt_id": row["attempt_id"],
        "lease_generation": row["lease_generation"],
        "lease_owner": row["lease_owner"],
    }
    differing = tuple(name for name in TOKEN_ELEMENTS if presented[name] != live[name])
    expected = (isolated,) if isolated in TOKEN_ELEMENTS else ()
    assert differing == expected, (
        f"token różni się od żywego lease'u w {differing}, a test twierdzi, że izoluje "
        f"{expected}. Dokładnie tak przechodziła poprzednia wersja tego pliku: każdy token "
        "różnił się TEŻ generacją, więc generacja odrzucała wszystko, a pozostałe warunki dały "
        "się usunąć bezkarnie"
    )


async def _every_fenced_write_is_refused(
    pool,
    organization_id: UUID,
    *,
    scenario: Callable[[], Awaitable[Forgery]],
    isolated: str,
) -> dict[str, JobFencingRefused]:
    """Drive ALL FOUR writing statements, each on its OWN fresh forgery, and insist each refuses.

    `scenario` is called again for every statement, and that is not tidiness — it was measured.
    The first version of this helper built one forgery and reused it, and the mutation run showed
    what that costs: under a neutralised condition `_CLOSE_ATTEMPT_SQL` SUCCEEDS and terminalises
    the job, so `_PARK_SQL` and `_REQUEUE_SQL` then refuse because the job is no longer `RUNNING`.
    Two of the four statements were being credited with a guard that was never exercised — the
    same disease this file is about, one level up: a later case that cannot reach the mechanism
    because an earlier one consumed the state.

    Failures are collected rather than raised at the first one, so a condition removed from a
    SINGLE statement produces a message naming THAT statement. Three things are checked per
    statement:

      1. the forged token is refused with `JobFencingRefused` — not with something else, and not
         accepted;
      2. a full queue snapshot is identical before and after, because an exception alone would
         also be raised by a CAS that wrote the row and then complained about it;
      3. the SAME statement, run with the token that really holds the job, LANDS. Without this,
         "every write is refused" would also be true of a store that refuses everything.
    """
    writes = (
        (
            FENCED_WRITES[0],
            lambda store, token: store.heartbeat(token, lease_seconds=LONG_LEASE_SECONDS),
        ),
        (
            FENCED_WRITES[1],
            lambda store, token: store.close_attempt(token, outcome=JobOutcome.SUCCEEDED),
        ),
        (
            FENCED_WRITES[2],
            lambda store, token: store.park_until_ready(
                token, waiting_state=JobWaitingState.WAITING_QUOTA, delay_seconds=30.0
            ),
        ),
        (
            FENCED_WRITES[3],
            lambda store, token: store.requeue(token, not_before=_a_moment_requeue_can_be_given()),
        ),
    )
    # Cardinality BEFORE the predicate.
    assert len(writes) == len(FENCED_WRITES) == 4, writes

    store = JobStore(pool)
    problems: list[str] = []
    refusals: dict[str, JobFencingRefused] = {}
    for name, write in writes:
        forgery = await scenario()
        await _assert_the_forgery_isolates_one_element(
            pool, forged=forgery.forged, victim_job_id=forgery.victim_job_id, isolated=isolated
        )
        before = await _queue_snapshot(pool, organization_id)
        try:
            await write(store, forgery.forged)
        except JobFencingRefused as refused:
            refusals[name] = refused
        # Broad on purpose, and nothing is swallowed: anything other than a refusal is REPORTED.
        # A narrower clause would let the partial-write `RuntimeError` of `_fenced_write` escape
        # as an error instead of naming the statement it came from.
        except Exception as other:
            problems.append(
                f"{name}: zamiast odmowy fencingu poleciało {type(other).__name__}: {other}"
            )
        else:
            problems.append(
                f"{name}: ZAPIS PRZESZEDŁ, choć przedstawiony token nie trzyma tego joba — "
                f"z klauzuli WHERE tego zapytania przestał fencować element {isolated}"
            )
        after = await _queue_snapshot(pool, organization_id)
        if after != before:
            problems.append(
                f"{name}: STAN KOLEJKI SIĘ ZMIENIŁ. Odmowa bez zapisu jest jedynym dozwolonym "
                "wynikiem — CAS, który zapisał wiersz i dopiero potem się poskarżył, jest tym "
                "samym błędem co zapis przyjęty po cichu"
            )

        if forgery.working is None:
            # The expiry scenario killed its own lease on purpose; it ran its positive control
            # before doing so, and nothing can succeed afterwards.
            continue
        if forgery.repair is not None:
            await forgery.repair()
        try:
            await write(store, forgery.working)
        except Exception as other:
            problems.append(
                f"{name}: KONTROLA DODATNIA PADŁA — ten sam zapis z tokenem, który NAPRAWDĘ "
                f"trzyma joba, dał {type(other).__name__}: {other}. Bez tego „zapis odrzucony” "
                "byłoby prawdą także o zapytaniu, które nie działa wcale"
            )
    assert not problems, "zapisy, które nie obroniły krotki fencingowej:\n  - " + "\n  - ".join(
        problems
    )
    assert len(refusals) == 4, refusals
    return refusals


# ---------------------------------------------------------------------------------------------
# 1/6 — job_id
# ---------------------------------------------------------------------------------------------


async def test_only_job_id_keeps_a_write_off_another_jobs_lease(pool, tenant) -> None:
    """`job_id` in the WHERE, and nothing else, stops a write aimed at ANOTHER job's lease.

    The token is a mix-up, not garbage: a worker holding two leases sends job A's identifier
    together with job B's attempt, generation and owner. Every other condition of the CAS matches
    job B's live lease exactly, so `l.job_id = $1` is the only thing between that token and a
    write landing on a job somebody else is running.

    MEASURED: with `l.job_id = $1` replaced by `$1::uuid IS NOT NULL` in all four writing
    statements — neutralised rather than deleted, because a deleted parameter makes PostgreSQL
    refuse to prepare the statement and that would be red for the wrong reason — this test fails
    and names all four. The output is in the report for this package.
    """

    async def scenario() -> Forgery:
        first = await _claim_one(pool, tenant)
        second = await _claim_one(pool, tenant)
        assert first.job_id != second.job_id, "oba claimy wzięły ten sam job"
        return Forgery(
            forged=JobFencingToken(
                job_id=first.job_id,
                attempt_id=second.attempt_id,
                lease_generation=second.lease_generation,
                lease_owner=second.lease_owner,
            ),
            # The lease the write would land on is job B's, which is why `_queue_snapshot` covers
            # the whole organization rather than the job under test.
            victim_job_id=second.job_id,
            working=second.fencing_token,
        )

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="job_id"
    )
    for name, refused in refusals.items():
        # The refusal describes the lease of the job the token CLAIMS to hold, because that is
        # the job an operator would go and look at.
        assert refused.live is not None, f"{name}: odmowa nie nazwała żywego lease'u joba A"
        assert refused.live.job_id == refused.presented.job_id, name
        assert refused.live.attempt_id != refused.presented.attempt_id, name


# ---------------------------------------------------------------------------------------------
# 2/6 — attempt_id
# ---------------------------------------------------------------------------------------------


async def _look_alike_attempt(pool, claim) -> UUID:
    """A SECOND attempt row of the same job, identical to the live one except in `attempt_id`.

    The schema permits this, and that is why `attempt_id` in the CAS is load-bearing rather than
    decorative. `job_attempts` enforces `UNIQUE (job_id, attempt_no)` (L3838) and nothing at all
    about `lease_generation` — the uniqueness of a generation is a constraint on `job_leases`
    (`UNIQUE (job_id, lease_generation)`, 0003 line 203). So two attempt rows of one job may
    carry the SAME generation and the SAME owner, and a token built from the wrong one differs
    from the live lease in `attempt_id` ALONE.

    Raw SQL for the reason the whole file uses raw SQL for setup: this is a state the database
    allows, not one any production code path is asked to produce.
    """
    async with pool.acquire() as conn:
        attempt_id = await conn.fetchval(
            """
            INSERT INTO job_attempts
                (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                 lease_owner, status, heartbeat_at, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'RUNNING', now(),
                    now() + make_interval(secs => $7::double precision))
            RETURNING attempt_id
            """,
            claim.organization_id,
            claim.job_id,
            claim.attempt_no + 1,
            claim.dispatch_version,
            claim.lease_generation,
            claim.lease_owner,
            LONG_LEASE_SECONDS,
        )
    assert attempt_id != claim.attempt_id, attempt_id
    return attempt_id


async def test_only_attempt_id_keeps_a_look_alike_attempt_out(pool, tenant) -> None:
    """`attempt_id` in the WHERE, and nothing else, rejects a token built from the wrong attempt.

    This is one of the two elements the previous wave could remove from all four statements
    without a single test noticing, and the reason is worth keeping: in a natural history the
    generation moves whenever the attempt does, so every stale token in the tests above lost on
    `lease_generation` first and the CAS never reached `l.attempt_id = $2`.

    So the fixture builds the state that isolates it — a second attempt row carrying the same
    generation and the same owner (`_look_alike_attempt` explains why the schema allows that).
    The forged token then differs from the live lease in `attempt_id` and in nothing else.

    MEASURED: with `l.attempt_id = $2` neutralised in all four writing statements this test fails
    and names all four. The output is in the report for this package.
    """

    async def scenario() -> Forgery:
        claim = await _claim_one(pool, tenant)
        return Forgery(
            forged=JobFencingToken(
                job_id=claim.job_id,
                attempt_id=await _look_alike_attempt(pool, claim),
                lease_generation=claim.lease_generation,
                lease_owner=claim.lease_owner,
            ),
            victim_job_id=claim.job_id,
            working=claim.fencing_token,
        )

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="attempt_id"
    )
    for name, refused in refusals.items():
        assert refused.live is not None, name
        assert refused.live.attempt_id != refused.presented.attempt_id, name
        assert refused.live.lease_generation == refused.presented.lease_generation, (
            f"{name}: generacje się różnią, więc to nie attempt_id odrzucił zapis"
        )


# ---------------------------------------------------------------------------------------------
# 3/6 — lease_generation
# ---------------------------------------------------------------------------------------------


async def _takeover_keeping_the_attempt_and_the_owner(pool, claim) -> JobFencingToken:
    """Generation N+1 on the SAME attempt and with the SAME `lease_owner`.

    DELIBERATELY NOT WHAT THE REAPER WILL DO. `claim_batch` explains that L2666 wants a random
    owner per takeover, and `_reaper_style_takeover` above mints one. This variant keeps the owner
    and the attempt on purpose, because a takeover that changed all three would leave a token
    differing from the live lease in three fields — and then `lease_owner` or `attempt_id` would
    do the rejecting and this test would not be measuring `lease_generation` at all.

    The schema allows the row: leases are unique per `(job_id, lease_generation)` and the previous
    one is closed first, so generation N+1 on the same attempt is legal.
    """
    async with pool.acquire() as conn, conn.transaction():
        closed = await conn.execute(
            "UPDATE job_leases SET status = 'EXPIRED' WHERE job_id = $1 AND status = 'ACTIVE'",
            claim.job_id,
        )
        assert closed == "UPDATE 1", closed
        inserted = await conn.execute(
            """
            INSERT INTO job_leases
                (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                 lease_generation, lease_owner, status, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'ACTIVE',
                    now() + make_interval(secs => $8::double precision))
            """,
            claim.organization_id,
            claim.job_id,
            claim.attempt_id,
            claim.attempt_no,
            claim.dispatch_version,
            claim.lease_generation + 1,
            claim.lease_owner,
            LONG_LEASE_SECONDS,
        )
        assert inserted == "INSERT 0 1", inserted
        # The attempt row follows the lease it belongs to. Otherwise the fixture would leave the
        # two disagreeing about which generation is current — the mismatch `heartbeat` refuses to
        # create for exactly this reason.
        mirrored = await conn.execute(
            "UPDATE job_attempts SET lease_generation = $2 WHERE attempt_id = $1",
            claim.attempt_id,
            claim.lease_generation + 1,
        )
        assert mirrored == "UPDATE 1", mirrored
    return JobFencingToken(
        job_id=claim.job_id,
        attempt_id=claim.attempt_id,
        lease_generation=claim.lease_generation + 1,
        lease_owner=claim.lease_owner,
    )


async def test_only_lease_generation_keeps_the_previous_generation_out(pool, tenant) -> None:
    """`lease_generation` in the WHERE, and nothing else, rejects the generation before this one.

    The canonical fencing failure: a worker frozen through a takeover comes back and writes under
    the generation it remembers. The takeover here keeps the attempt and the owner so that the OLD
    token differs from the live lease in the generation and in nothing else — see
    `_takeover_keeping_the_attempt_and_the_owner` for why that departure from the real reaper is
    the point rather than a shortcut.

    MEASURED: with `l.lease_generation = $3` neutralised in all four writing statements this test
    fails and names all four. The output is in the report for this package.
    """

    async def scenario() -> Forgery:
        claim = await _claim_one(pool, tenant)
        stale = claim.fencing_token
        current = await _takeover_keeping_the_attempt_and_the_owner(pool, claim)
        assert current.lease_generation == stale.lease_generation + 1
        assert current.attempt_id == stale.attempt_id
        assert current.lease_owner == stale.lease_owner
        return Forgery(forged=stale, victim_job_id=claim.job_id, working=current)

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="lease_generation"
    )
    for name, refused in refusals.items():
        assert refused.live is not None, name
        assert refused.live.lease_generation == refused.presented.lease_generation + 1, name


# ---------------------------------------------------------------------------------------------
# 4/6 — lease_owner
# ---------------------------------------------------------------------------------------------


async def test_only_lease_owner_keeps_another_leases_owner_out(pool, tenant) -> None:
    """`lease_owner` in the WHERE, and nothing else, rejects a token carrying the wrong owner.

    `lease_owner` is minted by the DATABASE, per takeover, with `gen_random_uuid()` — see
    `claim_batch`, which explains why the worker's own identity deliberately does not go in that
    column. So the value is one a worker can only have received, never computed, and the failure
    it guards against is a worker holding two leases and sending this job's identifiers with the
    other job's owner. That is the token built here: job A's `job_id`, attempt and generation,
    job B's owner.

    The second element the previous wave could remove from all four statements with the suite
    staying green.

    MEASURED: with `l.lease_owner = $4` neutralised in all four writing statements this test fails
    and names all four. The output is in the report for this package.
    """

    async def scenario() -> Forgery:
        first = await _claim_one(pool, tenant)
        second = await _claim_one(pool, tenant)
        assert first.lease_owner != second.lease_owner, (
            "oba claimy dostały tego samego lease_ownera — gen_random_uuid() nie mintuje "
            "właściciela per przejęcie i ten element krotki nie ma czego bronić"
        )
        assert first.lease_generation == second.lease_generation, (
            "generacje obu jobów się różnią, więc token różniłby się w dwóch polach i to nie "
            "lease_owner odrzucałby zapis"
        )
        return Forgery(
            forged=JobFencingToken(
                job_id=first.job_id,
                attempt_id=first.attempt_id,
                lease_generation=first.lease_generation,
                lease_owner=second.lease_owner,
            ),
            victim_job_id=first.job_id,
            working=first.fencing_token,
        )

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="lease_owner"
    )
    for name, refused in refusals.items():
        assert refused.live is not None, name
        assert refused.live.lease_owner != refused.presented.lease_owner, name
        assert refused.live.attempt_id == refused.presented.attempt_id, (
            f"{name}: próby się różnią, więc to nie lease_owner odrzucił zapis"
        )


# ---------------------------------------------------------------------------------------------
# 5/6 — expected_status
# ---------------------------------------------------------------------------------------------


async def _park_the_job_without_touching_its_lease(pool, job_id: UUID) -> str:
    """Move the job into `WAITING_QUOTA` and LEAVE the lease `ACTIVE`. Raw SQL, deliberately.

    No production path builds this: `park_until_ready` releases the lease in the same statement
    that parks the job (L2147). The state is reachable only through a bug, a manual edit or a
    half-applied write — which is why the CAS has to refuse it rather than assume it away.
    """
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "UPDATE jobs SET status = $2 WHERE id = $1 RETURNING status",
            job_id,
            JobWaitingState.WAITING_QUOTA.value,
        )
        still_active = await conn.fetchval(
            "SELECT count(*) FROM job_leases WHERE job_id = $1 AND status = 'ACTIVE'", job_id
        )
    assert status == JobWaitingState.WAITING_QUOTA.value, status
    assert still_active == 1, (
        f"{still_active} aktywnych lease'ów po zaparkowaniu joba — fixture ma zostawić lease "
        "ŻYWY, bo inaczej odmowę wywoła status lease'u, a nie expected_status"
    )
    return status


async def test_only_expected_status_keeps_a_write_off_a_parked_job(pool, tenant) -> None:
    """`expected_status` in the WHERE, and nothing else, refuses a write on a job left `WAITING_*`.

    The token here is CURRENT — the live lease, the live attempt, the live owner, not expired. The
    only thing wrong is the job: it sits in `WAITING_QUOTA` while its lease is still `ACTIVE`,
    which is precisely the inconsistency the reconciler of L2669 hunts and which L2147 forbids (a
    `WAITING_*` job holds no runtime lease). Built with raw SQL because no code path in F1
    produces it — `park_until_ready` releases the lease when it parks.

    Refusing here is what tells the worker to stop, so the fifth element of the tuple is not
    bookkeeping: without it a worker keeps renewing and eventually reports a result onto a job the
    operator has already parked.

    `heartbeat` is one of the four. It did NOT carry `expected_status` before this test was
    written, even though its own comment claimed "all five elements" and L2666 names the heartbeat
    first among the writes that must carry the tuple. That is a change to `jobs/store.py` made by
    this package, and it is reported as a change rather than smuggled in.

    MEASURED: with `EXISTS (… j.status = $5)` neutralised in the CAS of all four statements AND
    the same condition dropped from the three dependent job UPDATEs — so the write LANDS instead
    of half-landing — this test fails and names all four. The output is in the report.
    """

    async def scenario() -> Forgery:
        claim = await _claim_one(pool, tenant)
        await _park_the_job_without_touching_its_lease(pool, claim.job_id)

        async def repair() -> None:
            # The same token, the same lease, the job back in RUNNING — and now the write must
            # land. Otherwise "every write is refused" would also be true of a store that refuses
            # everything.
            async with pool.acquire() as conn:
                restored = await conn.execute(
                    "UPDATE jobs SET status = $2 WHERE id = $1", claim.job_id, RUNNING
                )
            assert restored == "UPDATE 1", restored

        return Forgery(
            forged=claim.fencing_token,
            victim_job_id=claim.job_id,
            working=claim.fencing_token,
            repair=repair,
        )

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="expected_status"
    )
    for name, refused in refusals.items():
        # The token IS the live lease, so `require_fresh_job_lease` has nothing to object to and
        # the refusal comes from the fall-through branch of `refuse_stale_write` — the branch that
        # exists for exactly this case: the tuple looks current and the CAS lost on something else.
        # A missing `__cause__` is what proves this is that branch.
        assert refused.live == refused.presented, f"{name}: {refused.live}"
        assert refused.__cause__ is None, (
            f"{name}: odmowa niesie przyczynę z reguły świeżości tokenu, a token JEST aktualny — "
            "to odmowa z innego powodu niż utrata fencingu i musi tak wyglądać"
        )

    # The last scenario's job was repaired and then closed by `requeue`, so the queue is left in a
    # state a reader can explain. Asserted so that a change in the order of the four writes cannot
    # quietly leave a job nobody can account for.
    async with pool.acquire() as conn:
        statuses = await conn.fetch(
            "SELECT status, count(*) AS ile FROM jobs WHERE organization_id = $1 GROUP BY status",
            tenant["organization"],
        )
    assert {row["status"]: row["ile"] for row in statuses} == {
        "SUCCEEDED": 1,
        "WAITING_QUOTA": 1,
        "READY": 1,
        RUNNING: 1,
    }, [dict(row) for row in statuses]


# ---------------------------------------------------------------------------------------------
# 6/6 — expires_at > now()
# ---------------------------------------------------------------------------------------------


async def test_no_fenced_write_lands_after_a_real_expiry(pool, tenant) -> None:
    """`expires_at > now()` in the WHERE, and nothing else, refuses every write on a dead lease.

    `test_heartbeat_after_expiry_does_not_resurrect_the_lease` above covers the heartbeat. This
    one covers all four, because each write carries its own CAS and a condition present in one
    statement proves nothing about the other three: the result write is the one that would put a
    stale worker's verdict on a job the reaper is entitled to have handed to somebody else.

    The token is CURRENT in all four identifiers and the job is still `RUNNING`. The lease row is
    still `status = 'ACTIVE'` — asserted by `_assert_the_forgery_isolates_one_element`, because if
    the reaper had already closed it then `l.status = 'ACTIVE'` would be doing the rejecting and
    this test would not be measuring the deadline at all.

    The wait is real: 0003 blocks moving `expires_at` backwards with
    `CHECK (expires_at > acquired_at)`, so the only honest way to be past the expiry is to be past
    it. The positive control therefore runs INSIDE the scenario, before the lease dies —
    afterwards no token can succeed, which is the whole point.

    MEASURED: with `l.expires_at > now()` deleted from all four writing statements this test fails
    and names all four. No parameter is involved there, so the deletion is a plain one. The output
    is in the report for this package.
    """
    store = JobStore(pool)

    async def scenario() -> Forgery:
        claim = await _claim_one(pool, tenant, lease_seconds=INSTANT_LEASE_SECONDS)
        # The positive control, while the lease is alive: without it "nothing lands" would be
        # satisfied by a store in which nothing ever lands.
        renewed = await store.heartbeat(claim.fencing_token, lease_seconds=INSTANT_LEASE_SECONDS)
        assert renewed is not None
        await _wait_for_real_expiry()
        return Forgery(forged=claim.fencing_token, victim_job_id=claim.job_id, working=None)

    refusals = await _every_fenced_write_is_refused(
        pool, tenant["organization"], scenario=scenario, isolated="expires_at"
    )
    for name, refused in refusals.items():
        # `_LIVE_LEASE_SQL` carries the same deadline, so the refusal says "nobody holds this job"
        # instead of naming a lease that is already dead. An operator acts on the difference.
        assert refused.live is None, (
            f"{name}: odmowa wskazała martwy lease jako aktualnego posiadacza joba — z zapytania "
            "o żywy lease zniknął warunek expires_at > now(), a operator dostaje polecenie "
            "zatrzymania workera, który już nic nie trzyma"
        )


# =============================================================================================
# Two values nothing asserted at all
# =============================================================================================


async def test_a_repeated_idempotency_key_reports_created_false_and_overwrites_nothing(
    pool, tenant
) -> None:
    """`EnqueuedJob.created is False` — the only place "inserted" differs from "was already there".

    "internal deduplication" (L2820) is the whole reason that field exists, and until this test
    nothing checked the `False` value: `created=True` hardcoded on both paths passed the suite,
    while every caller was told that a deduplicated job had just been added to the queue. A
    producer counting enqueued work off that flag would count the same job forever.

    Four things are checked, because the flag alone is not the fact:

      1. the second call says `created is False`;
      2. it returns the id of the EXISTING job, not a new one;
      3. the table holds ONE row for that key;
      4. the second call's DIFFERENT `job_type` and `priority` did not overwrite the first —
         `ON CONFLICT DO NOTHING`, not `DO UPDATE`. A row silently rewritten by a duplicate
         enqueue would be a job whose type nobody chose.

    MEASURED: flipping the conflict path to `created=True` makes this test red; the output is in
    the report for this package.
    """
    store = JobStore(pool)
    key = f"dedup-{uuid4()}"
    common = {
        "organization_id": tenant["organization"],
        "workspace_id": tenant["workspace"],
        "routing_key": "planning.daily",
        "attempt_limit": 5,
        "idempotency_key": key,
    }

    first = await store.enqueue(job_type="fixture.first", priority=0, **common)
    assert first.created is True, "pierwsze wstawienie zgłosiło, że nic nie wstawiło"

    second = await store.enqueue(job_type="fixture.second", priority=7, **common)
    assert second.created is False, (
        "powtórzony klucz idempotencji zgłoszony jako WSTAWIONY. To jedyne pole, które rozróżnia "
        "„dodano pracę” od „już była” (L2820) — wołający liczący pracę po tej fladze liczyłby ten "
        "sam job bez końca"
    )
    assert second.job_id == first.job_id, (
        f"drugi enqueue zwrócił {second.job_id}, a job o tym kluczu ma id {first.job_id}"
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, job_type, priority FROM jobs
            WHERE organization_id = $1 AND idempotency_key = $2
            """,
            tenant["organization"],
            key,
        )
    assert len(rows) == 1, [dict(row) for row in rows]
    assert rows[0]["id"] == first.job_id
    assert (rows[0]["job_type"], rows[0]["priority"]) == ("fixture.first", 0), (
        f"powtórzony enqueue NADPISAŁ wiersz: {dict(rows[0])}. ON CONFLICT DO NOTHING znaczy "
        "„nie ruszaj”, a nie „zaktualizuj” — job z typem, którego nikt nie wybrał, jest gorszy "
        "niż odmowa"
    )


async def test_holds_live_lease_is_false_for_an_expired_lease_still_marked_active(
    pool, tenant
) -> None:
    """`holds_live_lease` is `ACTIVE` **and** not past `expires_at`. This value goes on a screen.

    `JobView.holds_live_lease` promises exactly that and names the failure it prevents: "a count
    of `ACTIVE` rows alone would call an abandoned lease live". Nothing measured it. On the
    operator's screen "live" means "somebody is working on this, do not touch", so a dead lease
    reported as live is the operator being told to leave a stuck job alone.

    THE DATA HAS TO REACH THE CONDITION, so the test asserts that the lease row is STILL
    `status = 'ACTIVE'` at the moment of the read. If the reaper had already closed it, the
    `l.status = 'ACTIVE'` half of the EXISTS would produce the `False` and the deadline half would
    never be evaluated — the test would pass with the mechanism gone.

    Both read paths are checked: `fetch_job` (the detail screen) and `fetch_jobs` (the list). They
    share `_JOB_COLUMNS_SQL`, and a check covering only one of them would be satisfied by a second
    copy of that SQL drifting away from the first.

    MEASURED: deleting `l.expires_at > now()` from that one EXISTS makes this test red; the output
    is in the report for this package.
    """
    claim = await _claim_one(pool, tenant, lease_seconds=INSTANT_LEASE_SECONDS)
    store = JobStore(pool)
    organization_id = tenant["organization"]

    # While the lease is alive the field says so — otherwise `False` would prove nothing, being
    # also what a field hardcoded to `False` reports.
    alive = await store.fetch_job(claim.job_id, organization_id=organization_id)
    assert alive is not None, "job zniknął z kanonicznego magazynu"
    assert alive.job.holds_live_lease is True, "żywy lease raportowany jako nieżywy"

    await _wait_for_real_expiry()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, expires_at > now() AS still_alive FROM job_leases WHERE job_id = $1",
            claim.job_id,
        )
    assert row is not None, "wiersz lease'u zniknął"
    assert row["status"] == "ACTIVE", (
        f"lease ma status {row['status']}, a nie ACTIVE — wtedy odpowiedź „nieżywy” daje warunek "
        "statusu, a nie terminu, i ten test NIE mierzy tego, co twierdzi"
    )
    assert row["still_alive"] is False, "lease jeszcze nie wygasł — test nie mierzy tego, co ma"

    detail = await store.fetch_job(claim.job_id, organization_id=organization_id)
    assert detail is not None
    assert detail.job.holds_live_lease is False, (
        "ekran szczegółów ogłasza porzucony lease żywym. Liczenie samych wierszy ACTIVE jest "
        "dokładnie tym błędem, który JobView.holds_live_lease nazywa z imienia — a operator "
        "czyta z tego pola, że ktoś się tym jobem zajmuje"
    )

    every_job = await store.fetch_jobs(organization_id=organization_id)
    listed = [job for job in every_job if job.job_id == claim.job_id]
    assert len(listed) == 1, listed
    assert listed[0].holds_live_lease is False, (
        "lista jobów ogłasza porzucony lease żywym, choć ekran szczegółów już nie — dwie różne "
        "odpowiedzi na to samo pytanie znaczą dwie kopie tego zapytania"
    )
