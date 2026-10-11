"""The claim: one statement, `FOR UPDATE SKIP LOCKED`, against a REAL PostgreSQL (L5193, L5211).

This file defends one line of the Definition of Done:

    "dwa workery nie przejmują tego samego rekordu" (L5211)

and it does so in a way that cannot pass by accident. Two things here are structural, not
stylistic:

1.  **Cardinality is asserted BEFORE uniqueness.** `len(set(x)) == 50` is satisfied by any set
    of fifty distinct ids — but the empty set has no duplicates either, and `all([])`-shaped
    holes have produced four false passes in this repository (D-011, KONWENCJE.md). So every
    uniqueness check below is preceded by a count.

2.  **The twin test proves the first one can fail.** `test_the_naive_claim_really_does_produce
    _duplicates` runs a DELIBERATELY naive claim — the locking SELECT in one transaction, the
    UPDATE in another — through the SAME barrier script as the real store, and asserts that it
    hands one job to two workers. Without it, "no duplicates" could mean "the mechanism works"
    or "this test could never have seen a duplicate", and yesterday's audit found exactly the
    second case in the egress policy suite: removing the real mechanism stayed green through
    36 tests.

    The interleaving is driven by explicit `asyncio.Event` barriers, never by sleeping and
    hoping. A timing race would be flaky, and a flaky test gets "repaired" into one that always
    passes.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import pathlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from community_os_contracts import JobFencingToken
from control_plane.domain import commands
from control_plane.domain.models import BatchEmptiness, ClaimedJob
from control_plane.jobs import store as store_module
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

pytestmark = [pytest.mark.db]

#: DoD L5211/L5213 in numbers: K workers, M jobs, exactly M claims and not one repeat.
WORKER_COUNT = 8
JOB_COUNT = 50

#: A lease long enough that nothing in this file expires mid-test. Expiry has its own file.
LONG_LEASE_SECONDS = 60.0

#: The drain loop is capped so a bug cannot hang the suite — and the cap being REACHED is
#: asserted as a failure. A cap that silently ended the loop would turn "the queue was drained"
#: into "we gave up", and the total would come out under M with nothing saying why.
DRAIN_ITERATION_CAP = 200


@pytest_asyncio.fixture
async def job_pool():
    """A pool wide enough for `WORKER_COUNT` genuinely concurrent claims.

    Deliberately NOT the `pool` fixture from `conftest.py`: that one is capped at six
    connections, so eight workers would queue behind it and the concurrency this file is about
    would be supplied by the pool rather than by PostgreSQL. The DSN check is duplicated on
    purpose — `pytest.fail`, never `skipif` (see the note in `conftest.py`).
    """
    dsn = os.environ.get("WENEXT_TEST_DSN")
    if not dsn:
        pytest.fail(
            "brak WENEXT_TEST_DSN — ten test wymaga PostgreSQL. To jest PORAŻKA, nie "
            "pominięcie. Uruchom: REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh "
            "services/control_plane"
        )
    connection_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=WORKER_COUNT + 6)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


#: `tenant` comes from `conftest.py` and gives a FRESH organization and workspace per test. That
#: freshness is load-bearing here, not convenience: the whole package suite shares ONE throwaway
#: PostgreSQL, so rows left behind by an earlier test are still in the table. A claim that ignored
#: the organization would eat them, and every count in this file would depend on test order.


def _transport(job_pool, tenant) -> PostgresJobTransport:
    return PostgresJobTransport(JobStore(job_pool), organization_id=tenant["organization"])


async def _enqueue(port, tenant, *, count: int, delay_seconds: float = 0.0) -> list[UUID]:
    """Puts `count` jobs in the queue through the DOMAIN COMMAND, not through raw SQL.

    Going through `commands.enqueue_job` is the point: if the domain command and the store ever
    disagree about what a queued job looks like, this helper is where it shows.
    """
    created: list[UUID] = []
    for index in range(count):
        enqueued = await commands.enqueue_job(
            port,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            job_type="fixture.claim",
            routing_key="planning.daily",
            priority=index % 3,
            attempt_limit=5,
            idempotency_key=f"claim-{uuid4()}",
            delay_seconds=delay_seconds,
        )
        created.append(enqueued.job_id)
    return created


async def _drain(port) -> tuple[list[UUID], bool]:
    """One worker: claim until nothing dispatchable is left. Returns (claimed ids, finished).

    The stop condition is NOT "an empty batch". An empty batch means only that this worker got
    nothing THIS TIME — the other seven may be holding rows locked inside their own claim
    statements. Stopping there ends the drain early and the total comes out under M for a reason
    that has nothing to do with correctness.

    `due_before_claim` is the number of dispatchable rows as of the snapshot the claim statement
    itself took, so it still counts a row another worker has locked but not yet committed. Zero
    there, together with an empty batch, is the only honest "there is nothing left".
    """
    worker = uuid4()
    taken: list[UUID] = []
    for _ in range(DRAIN_ITERATION_CAP):
        batch = await commands.claim_batch(
            port, worker=worker, limit=3, lease_seconds=LONG_LEASE_SECONDS
        )
        taken.extend(claim.job_id for claim in batch.claims)
        if not batch.claims and batch.due_before_claim == 0:
            return taken, True
    return taken, False


async def test_eight_workers_split_fifty_jobs_without_overlap(job_pool, tenant) -> None:
    """K=8 workers, M=50 jobs: exactly M claims in total, and not one job claimed twice.

    THE ORDER OF THE TWO ASSERTIONS IS THE TEST. `len(set(claims)) == JOB_COUNT` passes on fifty
    distinct ids drawn from a hundred claims only by luck — but far worse, on an EMPTY list the
    uniqueness predicate is trivially satisfied, because a set with nothing in it has no
    duplicates. Four of this project's false passes were that exact shape. So the count is
    asserted first and separately, and uniqueness is only checked once we know something was
    claimed at all.
    """
    organization_id = tenant["organization"]
    created = await _enqueue(_transport(job_pool, tenant), tenant, count=JOB_COUNT)
    assert len(created) == JOB_COUNT

    results = await asyncio.gather(
        *(_drain(_transport(job_pool, tenant)) for _ in range(WORKER_COUNT))
    )

    unfinished = [index for index, (_, finished) in enumerate(results) if not finished]
    assert not unfinished, (
        f"workery {unfinished} wyczerpały limit {DRAIN_ITERATION_CAP} iteracji zamiast opróżnić "
        "kolejkę — dopóki tak jest, suma pobrań mówi o cierpliwości pętli, nie o poprawności"
    )

    claims = [job_id for taken, _ in results for job_id in taken]

    # 1. LICZNOŚĆ. Bez tego następna asercja przechodzi na pustej liście.
    assert len(claims) == JOB_COUNT, (
        f"{WORKER_COUNT} workerów pobrało łącznie {len(claims)} jobów z {JOB_COUNT}"
    )
    # 2. Dopiero teraz unikalność.
    assert len(set(claims)) == JOB_COUNT, (
        f"{len(claims) - len(set(claims))} jobów pobrano więcej niż raz — to jest dokładnie "
        "„dwa workery przejmują ten sam rekord” z L5211"
    )
    assert set(claims) == set(created)

    # A claim is three rows, not one: the job flips to RUNNING, an attempt appears, a lease is
    # held. A claim handing out a token without writing the attempt satisfies everything above
    # and loses the job on the first restart (L5212).
    async with job_pool.acquire() as conn:
        running = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND status = 'RUNNING'",
            organization_id,
        )
        attempts = await conn.fetchval(
            "SELECT count(*) FROM job_attempts WHERE organization_id = $1", organization_id
        )
        live_leases = await conn.fetchval(
            """
            SELECT count(*) FROM job_leases
            WHERE organization_id = $1 AND status = 'ACTIVE' AND expires_at > now()
            """,
            organization_id,
        )
        doubly_leased = await conn.fetchval(
            """
            SELECT count(*) FROM (
                SELECT job_id FROM job_leases
                WHERE organization_id = $1 AND status = 'ACTIVE'
                GROUP BY job_id HAVING count(*) > 1
            ) AS wielokrotne
            """,
            organization_id,
        )
    assert running == JOB_COUNT, running
    assert attempts == JOB_COUNT, attempts
    assert live_leases == JOB_COUNT, live_leases
    assert doubly_leased == 0, doubly_leased


class NaiveClaim:
    """A DELIBERATELY BROKEN claim, kept here so the test above has something to be right about.

    The bug is the one the package brief names: the locking SELECT in one transaction and the
    UPDATE in another. The row lock taken by the SELECT lives exactly as long as its
    transaction — the moment it commits, the row is READY and unlocked again, and the next
    worker's SKIP LOCKED has nothing left to skip.

    This class must never be "fixed". It exists in order to fail.
    """

    def __init__(self, pool) -> None:
        self._pool = pool

    async def select_candidates(self, organization_id: UUID, limit: int) -> list[UUID]:
        """Step one. Takes the lock — and then throws it away by committing."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id FROM jobs
                    WHERE organization_id = $1 AND status = 'READY' AND not_before <= now()
                    ORDER BY priority, not_before
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                    """,
                    organization_id,
                    limit,
                )
            # Transaction closed here. THE LOCK IS GONE. Everything below is a lie about it.
            return [row["id"] for row in rows]

    async def mark_running(self, job_ids: list[UUID]) -> None:
        """Step two, in its own transaction — with nothing left guarding the row."""
        if not job_ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET status = 'RUNNING' WHERE id = ANY($1::uuid[])", job_ids
            )


async def test_the_naive_claim_really_does_produce_duplicates(job_pool, tenant) -> None:
    """The detection power of the test above, measured instead of assumed.

    Both halves run the SAME script, with the SAME explicit barriers:

        worker A selects and closes its transaction  →  barrier  →  worker B selects
        →  barrier  →  both write

    Nothing here sleeps and nothing races on wall-clock time: a flaky duplicate would be
    "repaired" one afternoon into a test that passes unconditionally, and the twin test above
    would then be guarding nothing.

    The naive store MUST hand the same job to both workers. The real store MUST NOT.
    """
    organization_id = tenant["organization"]
    await _enqueue(_transport(job_pool, tenant), tenant, count=1)

    naive = NaiveClaim(job_pool)
    a_released_its_lock = asyncio.Event()
    b_took_its_turn = asyncio.Event()
    naive_claims: list[UUID] = []

    async def naive_worker_a() -> None:
        picked = await naive.select_candidates(organization_id, 1)
        a_released_its_lock.set()
        await asyncio.wait_for(b_took_its_turn.wait(), timeout=10)
        await naive.mark_running(picked)
        naive_claims.extend(picked)

    async def naive_worker_b() -> None:
        await asyncio.wait_for(a_released_its_lock.wait(), timeout=10)
        picked = await naive.select_candidates(organization_id, 1)
        b_took_its_turn.set()
        await naive.mark_running(picked)
        naive_claims.extend(picked)

    await asyncio.gather(naive_worker_a(), naive_worker_b())

    # Cardinality first, again: with zero naive claims the duplicate assertion below would be
    # satisfied by an empty list and this test would prove nothing whatsoever.
    assert len(naive_claims) == 2, (
        f"naiwny claim zwrócił {len(naive_claims)} pobrań, oczekiwano 2 — jeżeli któryś worker "
        "nic nie dostał, to nie jest dowód mocy wykrywania, tylko cisza"
    )
    assert len(set(naive_claims)) == 1, (
        "naiwny claim PRZESTAŁ produkować duplikaty. Albo ktoś go naprawił — a on ma być "
        "zepsuty — albo scenariusz z barierami przestał odtwarzać wyścig, i wtedy "
        "test_eight_workers_split_fifty_jobs_without_overlap nie pilnuje już niczego"
    )

    # The same script, the real store. One statement, so the lock never leaves the transaction.
    async with job_pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'READY' WHERE organization_id = $1", organization_id
        )
    store = JobStore(job_pool)
    a_finished = asyncio.Event()
    real_claims: list[UUID] = []

    async def real_worker_first() -> None:
        batch = await store.claim_batch(
            organization_id=organization_id,
            worker=uuid4(),
            limit=1,
            lease_seconds=LONG_LEASE_SECONDS,
        )
        real_claims.extend(claim.job_id for claim in batch.claims)
        a_finished.set()

    async def real_worker_second() -> None:
        await asyncio.wait_for(a_finished.wait(), timeout=10)
        batch = await store.claim_batch(
            organization_id=organization_id,
            worker=uuid4(),
            limit=1,
            lease_seconds=LONG_LEASE_SECONDS,
        )
        real_claims.extend(claim.job_id for claim in batch.claims)

    await asyncio.gather(real_worker_first(), real_worker_second())

    assert len(real_claims) == 1, (
        f"prawdziwy store rozdał {len(real_claims)} pobrań tego samego joba w scenariuszu, "
        "w którym naiwny rozdał dwa"
    )


async def test_a_job_with_not_before_in_the_future_is_not_claimed(job_pool, tenant) -> None:
    """`not_before` is a schedule, not a comment — and it is compared with PostgreSQL's `now()`.

    D-017 freezes the backoff schedule (5 s, 15 s, 60 s, 300 s, 900 s, full jitter). A claim
    ignoring `not_before` turns every one of those delays into an immediate redispatch, which is
    the "nieskończony redispatch" L5214 forbids — arriving as a busy loop rather than an error.
    """
    organization_id = tenant["organization"]
    port = _transport(job_pool, tenant)
    store = JobStore(job_pool)

    later = await _enqueue(port, tenant, count=1, delay_seconds=3600.0)

    batch = await store.claim_batch(
        organization_id=organization_id,
        worker=uuid4(),
        limit=10,
        lease_seconds=LONG_LEASE_SECONDS,
    )
    assert batch.claims == (), batch.claims
    assert batch.scheduled_later == 1, batch.scheduled_later
    assert batch.due_before_claim == 0, batch.due_before_claim
    assert batch.emptiness is BatchEmptiness.ALL_SCHEDULED_FOR_LATER, batch.emptiness

    # The other direction, without which the assertion above is satisfied by a claim that never
    # claims anything at all.
    due = await _enqueue(port, tenant, count=1)
    batch = await store.claim_batch(
        organization_id=organization_id,
        worker=uuid4(),
        limit=10,
        lease_seconds=LONG_LEASE_SECONDS,
    )
    assert len(batch.claims) == 1, batch.claims
    assert batch.claims[0].job_id == due[0]
    assert batch.claims[0].job_id != later[0]


async def test_a_batch_comes_back_in_the_priority_order_it_was_chosen_in(job_pool, tenant) -> None:
    """The dispatcher runs a batch in the order it receives it, so the batch must arrive in the
    order of priority. Measured 27.09: the ORDER BY inside the claim chose the right jobs and the
    outer SELECT returned them in any order, so a priority-2 job ran before the priority-1 job
    claimed with it (`test_job_lanes`, one full run in three)."""
    port = _transport(job_pool, tenant)
    for priority in (2, 0, 1, 2, 0, 1):
        await commands.enqueue_job(
            port,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            job_type="fixture.claim",
            routing_key="planning.daily",
            priority=priority,
            attempt_limit=5,
            idempotency_key=f"order-{uuid4()}",
        )

    batch = await JobStore(job_pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=6,
        lease_seconds=LONG_LEASE_SECONDS,
    )

    assert [claim.priority for claim in batch.claims] == [0, 0, 1, 1, 2, 2]


async def test_an_empty_batch_means_everything_is_taken_not_that_the_queue_is_empty(
    job_pool, tenant
) -> None:
    """An empty batch is a DIAGNOSIS, not a verdict — and the two diagnoses differ.

    "Pusty zbiór, pusta lista, zero zaktualizowanych wierszy — to są BŁĘDY, nie zaliczenia."
    A dispatcher unable to tell "there is no work" from "the work is all taken" logs the same
    line for a drained queue and for fifty jobs stuck under dead leases, and the second case is
    an incident. So the claim statement reports the backlog it saw in its own snapshot.
    """
    organization_id = tenant["organization"]
    store = JobStore(job_pool)
    port = _transport(job_pool, tenant)

    empty = await store.claim_batch(
        organization_id=organization_id, worker=uuid4(), limit=5, lease_seconds=LONG_LEASE_SECONDS
    )
    assert empty.claims == ()
    assert empty.emptiness is BatchEmptiness.QUEUE_EMPTY, empty.emptiness
    assert (empty.due_before_claim, empty.scheduled_later, empty.already_running) == (0, 0, 0)

    await _enqueue(port, tenant, count=2)
    first = await store.claim_batch(
        organization_id=organization_id, worker=uuid4(), limit=5, lease_seconds=LONG_LEASE_SECONDS
    )
    assert len(first.claims) == 2, first.claims
    assert first.emptiness is BatchEmptiness.NOT_EMPTY, first.emptiness

    second = await store.claim_batch(
        organization_id=organization_id, worker=uuid4(), limit=5, lease_seconds=LONG_LEASE_SECONDS
    )
    assert second.claims == ()
    assert second.already_running == 2, second.already_running
    assert second.emptiness is BatchEmptiness.ALL_CLAIMED_BY_OTHERS, second.emptiness
    assert second.emptiness is not BatchEmptiness.QUEUE_EMPTY

    # The operator-facing sentence differs too, because that is where the difference gets read.
    assert empty.explain() != second.explain()


async def test_the_job_row_survives_the_death_of_the_claiming_process(job_pool, tenant) -> None:
    """DoD L5212: "restart komponentu nie gubi taska".

    The claiming process is killed the hardest way this test can kill it — the PostgreSQL
    backend serving its connection is terminated with `pg_terminate_backend`, so there is no
    `finally` block, no lease release and no goodbye. What must survive is the canonical state:
    the job row, the attempt and the lease, carrying the same fencing tuple the dead worker was
    handed.

    A queue holding the claim in process memory — the shape `TicketRegistry` in
    `services/stream_gateway/stream_gateway/tickets.py` still has, and whose own docstring calls
    it an F0 shortcut — passes every unit test and loses the task right here.
    """
    organization_id = tenant["organization"]
    await _enqueue(_transport(job_pool, tenant), tenant, count=1)

    dsn = os.environ["WENEXT_TEST_DSN"]
    # A pool of exactly one connection, so the pid read below is the pid that claims.
    doomed = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        async with doomed.acquire() as conn:
            backend_pid = await conn.fetchval("SELECT pg_backend_pid()")
        batch = await JobStore(doomed).claim_batch(
            organization_id=organization_id,
            worker=uuid4(),
            limit=1,
            lease_seconds=LONG_LEASE_SECONDS,
        )
        assert len(batch.claims) == 1, batch.claims
        token = batch.claims[0].fencing_token

        async with job_pool.acquire() as conn:
            killed = await conn.fetchval("SELECT pg_terminate_backend($1)", backend_pid)
        assert killed is True, (
            f"nie udało się zabić backendu {backend_pid} — bez tego test nie odtwarza śmierci "
            "procesu i nie dowodzi niczego o L5212"
        )
    finally:
        # The pool now holds a dead connection; closing it must not mask the assertions above.
        await doomed.close()

    async with job_pool.acquire() as conn:
        job = await conn.fetchrow("SELECT status FROM jobs WHERE id = $1", token.job_id)
        attempt = await conn.fetchrow(
            """
            SELECT attempt_no, status, lease_generation, lease_owner
            FROM job_attempts WHERE attempt_id = $1
            """,
            token.attempt_id,
        )
        leases = await conn.fetch(
            """
            SELECT lease_generation, lease_owner, status
            FROM job_leases WHERE job_id = $1 ORDER BY lease_generation
            """,
            token.job_id,
        )

    assert job is not None, "wiersz joba zniknął razem z procesem"
    assert job["status"] == "RUNNING", job["status"]
    assert attempt is not None, "próba zniknęła razem z procesem"
    assert attempt["status"] == "RUNNING", attempt["status"]
    assert attempt["lease_generation"] == token.lease_generation
    assert attempt["lease_owner"] == token.lease_owner
    assert len(leases) == 1, [dict(row) for row in leases]
    assert leases[0]["status"] == "ACTIVE"
    assert leases[0]["lease_generation"] == token.lease_generation

    # And the task is not merely present — it is still workable from a NEW process holding the
    # same fencing tuple. "The row exists" without this is an epitaph, not recovery.
    renewed = await JobStore(job_pool).heartbeat(token, lease_seconds=LONG_LEASE_SECONDS)
    assert renewed is not None


async def test_requeue_hands_the_job_back_and_releases_the_lease(job_pool, tenant) -> None:
    """`requeue` is the way back into the queue — and it takes the lease away first.

    The port's own docstring draws the line this test pins down: "Whether one more attempt is
    allowed at all is decided by the attempt limit on the domain side (L5214) — the transport
    never invents an extra attempt." So `requeue` moves the job to READY with a `not_before` and
    closes the attempt. It counts nothing, and it must not leave a live lease on a job that is
    dispatchable again — that combination is precisely what the reaper hunts.
    """
    organization_id = tenant["organization"]
    port = _transport(job_pool, tenant)
    store = JobStore(job_pool)
    await _enqueue(port, tenant, count=1)

    first = await store.claim_batch(
        organization_id=organization_id, worker=uuid4(), limit=1, lease_seconds=LONG_LEASE_SECONDS
    )
    assert len(first.claims) == 1
    token = first.claims[0].fencing_token

    # A moment in the past on purpose: the store clamps it with `greatest(..., now())`, so the
    # job becomes dispatchable at once instead of carrying a `not_before` behind the clock.
    await port.requeue(token, not_before=datetime(2020, 1, 1, tzinfo=UTC))

    async with job_pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, not_before <= now() AS due FROM jobs WHERE id = $1", token.job_id
        )
        live = await conn.fetchval(
            "SELECT count(*) FROM job_leases WHERE job_id = $1 AND status = 'ACTIVE'",
            token.job_id,
        )
        attempt_status = await conn.fetchval(
            "SELECT status FROM job_attempts WHERE attempt_id = $1", token.attempt_id
        )
    assert job["status"] == "READY", job["status"]
    assert job["due"] is True
    assert live == 0, "job wrócił do kolejki, a lease nadal jest ACTIVE"
    assert attempt_status == "FAILED", attempt_status

    second = await store.claim_batch(
        organization_id=organization_id, worker=uuid4(), limit=1, lease_seconds=LONG_LEASE_SECONDS
    )
    assert len(second.claims) == 1, second.claims
    assert second.claims[0].attempt_no == 2, second.claims[0].attempt_no
    assert second.claims[0].lease_generation == 2, second.claims[0].lease_generation
    assert second.claims[0].lease_owner != token.lease_owner, (
        "przejęcie lease'u dostało tego samego właściciela co poprzednie — L2666 wymaga "
        "LOSOWEGO lease_owner, a stały właściciel jest w krotce CAS martwym balastem"
    )


def _string_constants_without_docstrings(source: str) -> list[str]:
    """Every string literal in `source` except the docstrings.

    The docstrings have to go: this module's prose talks ABOUT the locking clause, and a scanner
    counting those hits would report two claim statements where there is one — a false alarm
    that gets silenced by loosening the assertion, and then the check is gone.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_claim_is_one_statement_with_the_skip_locked_select_inside_it() -> None:
    """The two greps the Definition of Done asks for, as a test instead of a habit.

    A grep living in a brief is run once, by the person who wrote the code — the same way
    `rename_identifiers.py` was "required by KONWENCJE.md" and broken on every invocation for
    weeks (STAN-PRAC.md section 7). These are the same two checks, run by every suite:

      * the locking clause appears INSIDE the same SQL string as `UPDATE jobs ... RETURNING`.
        A lock taken in a separate statement dies with its transaction — `NaiveClaim` above is
        what that looks like, and it hands one job to two workers.
      * `store.py` computes no expiry and no `not_before` from the process clock. Every time
        comparison goes through PostgreSQL's `now()` (L2668), so `datetime.now`, `utcnow` and
        `timedelta` have no business being in the file at all.
    """
    source_path = pathlib.Path(inspect.getsourcefile(store_module) or "")
    assert source_path.name == "store.py", source_path
    source = source_path.read_text(encoding="utf-8")

    locking = [
        text
        for text in _string_constants_without_docstrings(source)
        if "FOR UPDATE SKIP LOCKED" in text
    ]

    # Cardinality before the predicate. An empty list satisfies everything after it.
    assert len(locking) == 1, (
        f"w store.py jest {len(locking)} zapytań z blokującym SELECT-em, oczekiwano dokładnie "
        "jednego: claim jest JEDNYM zapytaniem, a druga kopia tego fragmentu znaczy, że gdzieś "
        "powstał SELECT bez UPDATE-u w tej samej transakcji"
    )
    claim_sql = locking[0]
    assert "UPDATE jobs" in claim_sql, claim_sql
    assert "RETURNING" in claim_sql, claim_sql
    assert claim_sql.index("UPDATE jobs") < claim_sql.index("FOR UPDATE SKIP LOCKED"), (
        "blokujący SELECT stoi POZA łańcuchem UPDATE ... RETURNING"
    )

    process_clock = [
        marker for marker in ("datetime.now", "utcnow", "timedelta") if marker in source
    ]
    assert not process_clock, (
        f"store.py sięga po zegar procesu: {process_clock}. Wszystkie porównania czasu idą przez "
        "now() Postgresa, a not_before i expires_at liczy baza (L2668)"
    )


def test_a_claim_hands_out_the_contracts_fencing_token_not_a_look_alike() -> None:
    """The token handed out is `contracts.JobFencingToken`, not a local four-field twin.

    A look-alike would pass every test in this file and then be rejected by
    `require_fresh_job_lease` in the one place it matters — or worse, be accepted by a second,
    local copy of that check (L5178, L2377).
    """
    claim = ClaimedJob(
        job_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=None,
        job_type="t",
        routing_key="r",
        priority=0,
        payload_ref=None,
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=uuid4(),
        attempt_no=1,
        lease_generation=3,
        lease_owner=uuid4(),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    token = claim.fencing_token
    assert isinstance(token, JobFencingToken), type(token)
    assert token.job_id == claim.job_id
    assert token.attempt_id == claim.attempt_id
    assert token.lease_generation == 3
    assert token.lease_owner == claim.lease_owner
