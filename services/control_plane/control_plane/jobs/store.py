"""The job queue over PostgreSQL. Canonical state, one-statement claim, CAS on the full tuple.

    "PostgreSQL jobs jako canonical state; dispatcher w tym samym procesie na
     `SELECT ... FOR UPDATE SKIP LOCKED`" (L5193)
    "job leases, heartbeat, reaper, attempt limit i terminalizacja ostatniej próby" (L5194)

FOUR RULES THIS MODULE IS BUILT ON. Each one is here because of a specific way the obvious
version fails.

1.  **The claim is ONE statement.** The locking SELECT sits inside the same SQL chain as the
    `UPDATE jobs … RETURNING`, and the attempt and the lease are inserted by data-modifying CTEs
    of that same statement. A lock taken by a separate SELECT lives exactly as long as ITS
    transaction: the moment that transaction commits, the row is `READY` and unlocked again, and
    the next worker's SKIP LOCKED has nothing left to skip. `tests/test_job_claim.py` keeps a
    deliberately broken implementation of that shape (`NaiveClaim`) and proves it hands one job
    to two workers.

2.  **Every mutating write is a CAS on the FULL fencing tuple** —
    `(job_id, attempt_id, lease_generation, lease_owner, expected_status)` plus
    `expires_at > now()` — exactly as L2666-2668 spells out. The pattern already in the
    repository, `ProfileLeaseStore.guard` (`services/profile_lease/profile_lease/store.py` around
    line 269), is NOT strong enough here: it compares the generation alone, without `attempt_id`
    and without `lease_owner`, and it does it as a SELECT followed by a comparison in Python —
    a read and then a write, with a gap in between.
    `test_cas_on_job_id_and_status_alone_lets_a_zombie_through` walks through that gap.

    "FULL" is a claim about six conditions in four statements, so it is measured element by
    element rather than asserted: six mutations, six red tests, listed in the comment above
    `_HEARTBEAT_SQL`. That comment is the map — read it before touching any WHERE clause below.
    It also records what the previous wave got wrong, which was not the code: the code carried
    the whole tuple, and the TESTS only pinned part of it.

3.  **Zero updated rows is a REFUSAL, never a success.** In `asyncpg` a CAS that matched nothing
    does not raise: `conn.execute("UPDATE …")` returns the STRING `"UPDATE 0"` and the call
    succeeds. Every fenced write below therefore counts the rows it touched and turns zero into
    `JobFencingRefused`. This is the most common form of this project's recurring disease in
    database code, and it is the one that leaves no trace.

4.  **All time comes from PostgreSQL.** `not_before` and `expires_at` are computed as
    `now() + make_interval(secs => …)` on the server, and every comparison is against `now()`.
    Not one deadline is computed here (L2668), so no spelling of the process clock occurs
    anywhere in this file — durations cross this boundary as seconds, never as moments. The only
    absolute timestamp accepted from a caller is `requeue`'s `not_before`, and even that one is
    clamped with `greatest(…, now())` by the database.
    `test_the_claim_is_one_statement_with_the_skip_locked_select_inside_it` greps this file for
    the three usual spellings and fails on any of them; that grep is why this paragraph names
    none of them, which is a trap this very docstring fell into on the first run.

WHAT IS NOT HERE, on purpose (L5500-5501 forbids scope creep): the reaper loop and the attempt
limit (L5214) — a sibling package; the outbox and RabbitMQ — E3 (L5205); weighted fairness —
out of F1. `priority` is a number this module sorts by and nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import NoReturn
from uuid import UUID

import asyncpg
from community_os_contracts import JobFencingToken, JobWaitingState

from control_plane.domain.errors import refuse_stale_write
from control_plane.domain.models import (
    ClaimBatch,
    ClaimedJob,
    EnqueuedJob,
    JobAttemptView,
    JobDetail,
    JobOutcome,
    JobView,
)

_LOG = logging.getLogger(__name__)

#: The status a job must be in to be dispatchable. One value, matching the partial index
#: `jobs_ready_for_dispatch` in migration 0003 — a second status here would silently stop using
#: that index. `WAITING_*` rows are NOT dispatchable: they hold no lease and they re-enter the
#: pool only through `publish_ready` (L2147).
DISPATCHABLE_STATUS = "READY"

#: The status a job must be in for a worker's write to be accepted. This is the fifth element of
#: the fencing tuple from L2666: `expected_status`.
RUNNING_STATUS = "RUNNING"

#: Jobs that `publish_ready` may move back to `READY`. Exactly the four waiting states of
#: `contracts.JobWaitingState`, taken from the enum rather than typed out, so a fifth one added to
#: the contracts arrives here instead of being quietly rejected by the database.
REPUBLISHABLE_STATUSES: tuple[str, ...] = tuple(state.value for state in JobWaitingState)


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------
# ONE statement. Reading it from the bottom up:
#
#   backlog   — the counts the statement's OWN snapshot saw, taken BEFORE the update. Rows a
#               competing worker has locked but not yet committed still count as READY here, and
#               that is exactly what makes "nothing claimed, but work exists" distinguishable
#               from "the queue is empty". A follow-up query could never establish it: by then
#               the world has moved.
#   claimed   — UPDATE jobs … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED LIMIT …) RETURNING.
#               The lock and the write are in one statement, so the lock cannot expire between
#               them.
#   numbered  — the next attempt number and the next lease generation, per claimed job, plus a
#               lease_owner minted by `gen_random_uuid()`. MATERIALIZED because that function is
#               volatile and an inlined CTE could be evaluated more than once.
#   attempt   — the `job_attempts` row. `expires_at` is computed by the server.
#   lease     — the `job_leases` row, ACTIVE, sharing the attempt's `expires_at`.
#
# The final SELECT starts FROM backlog and LEFT JOINs the rest, so an empty claim still returns
# one row carrying the counts. Data-modifying CTEs run exactly once whether or not the outer
# query reads them, which is what makes that safe.
_CLAIM_SQL = """
WITH backlog AS MATERIALIZED (
    SELECT
        count(*) FILTER (WHERE status = 'READY' AND not_before <= now())  AS due_now,
        count(*) FILTER (WHERE status = 'READY' AND not_before > now())   AS scheduled_later,
        count(*) FILTER (WHERE status = 'RUNNING')                        AS already_running
    FROM jobs
    WHERE organization_id = $3
      AND ($4::text[] IS NULL OR job_type = ANY($4::text[]))
      AND ($5::text[] IS NULL OR NOT (job_type = ANY($5::text[])))
),
claimed AS (
    UPDATE jobs
    SET status = 'RUNNING',
        lock_version = lock_version + 1
    WHERE id IN (
        SELECT id
        FROM jobs
        WHERE organization_id = $3
          AND status = 'READY'
          AND not_before <= now()
          -- LANES (control_plane.jobs.lanes): a dispatcher may serve only part of the queue.
          -- Both NULL is the whole queue. The same two conditions filter `backlog` above, so the
          -- emptiness diagnosis of a lane speaks about that lane and not about its neighbour.
          AND ($4::text[] IS NULL OR job_type = ANY($4::text[]))
          AND ($5::text[] IS NULL OR NOT (job_type = ANY($5::text[])))
        ORDER BY priority, not_before
        FOR UPDATE SKIP LOCKED
        LIMIT $1
    )
    RETURNING id, organization_id, workspace_id, job_type, routing_key, priority,
              payload_ref, dispatch_version, attempt_limit, not_before
),
numbered AS MATERIALIZED (
    SELECT
        c.id, c.organization_id, c.workspace_id, c.job_type, c.routing_key, c.priority,
        c.payload_ref, c.dispatch_version, c.attempt_limit,
        coalesce((SELECT max(a.attempt_no) FROM job_attempts a WHERE a.job_id = c.id), 0) + 1
            AS attempt_no,
        coalesce((SELECT max(l.lease_generation) FROM job_leases l WHERE l.job_id = c.id), 0) + 1
            AS lease_generation,
        gen_random_uuid() AS lease_owner
    FROM claimed c
),
attempt AS (
    INSERT INTO job_attempts
        (organization_id, job_id, attempt_no, dispatch_version, lease_generation, lease_owner,
         status, heartbeat_at, expires_at)
    SELECT n.organization_id, n.id, n.attempt_no, n.dispatch_version, n.lease_generation,
           n.lease_owner, 'RUNNING', now(), now() + make_interval(secs => $2::double precision)
    FROM numbered n
    RETURNING attempt_id, organization_id, job_id, attempt_no, dispatch_version,
              lease_generation, lease_owner, expires_at
),
lease AS (
    INSERT INTO job_leases
        (organization_id, job_id, attempt_id, attempt_no, dispatch_version, lease_generation,
         lease_owner, status, expires_at)
    SELECT a.organization_id, a.job_id, a.attempt_id, a.attempt_no, a.dispatch_version,
           a.lease_generation, a.lease_owner, 'ACTIVE', a.expires_at
    FROM attempt a
    RETURNING job_id, attempt_id, expires_at
)
SELECT
    b.due_now, b.scheduled_later, b.already_running,
    c.id AS job_id, c.organization_id, c.workspace_id, c.job_type, c.routing_key, c.priority,
    c.payload_ref, c.dispatch_version, c.attempt_limit,
    a.attempt_id, a.attempt_no, a.lease_generation, a.lease_owner,
    l.expires_at
FROM backlog b
LEFT JOIN claimed c ON true
LEFT JOIN attempt a ON a.job_id = c.id
LEFT JOIN lease l ON l.attempt_id = a.attempt_id
-- The batch comes back in the order it was chosen in. The ORDER BY inside `claimed` decides WHICH
-- jobs are taken, not the order of the rows this SELECT returns, and the dispatcher runs a batch
-- in the order it receives it: without this line a priority-2 job ran before the priority-1 job
-- claimed with it (measured 27.09, `test_job_lanes`, one run in three).
ORDER BY c.priority, c.not_before, c.id
"""

# ---------------------------------------------------------------------------
# The four writing statements, and the ONE invariant they share
# ---------------------------------------------------------------------------
# Every one of the four carries the SAME six conditions in the WHERE of its leading CAS:
#
#   $1 job_id · $2 attempt_id · $3 lease_generation · $4 lease_owner  — the four identifiers
#   $5 expected_status                                               — the job's expected status
#   l.expires_at > now()                                             — the lease is still alive
#
# The parameter POSITIONS are identical in all four on purpose, so that `_fenced_write` and
# `heartbeat` bind the tuple the same way and a query added later cannot quietly shift one.
#
# EACH of those six is measured by a mutation, one element at a time, in
# `tests/test_job_lease_fencing.py`. `_every_fenced_write_is_refused` drives all four statements,
# each on its own freshly built forgery, and reports EVERY statement that let the write through —
# so a condition dropped from ONE query names that query, and one dropped from all four names all
# four. The six tests, and the condition whose removal turns each of them red:
#
#   job_id            → test_only_job_id_keeps_a_write_off_another_jobs_lease
#   attempt_id        → test_only_attempt_id_keeps_a_look_alike_attempt_out
#   lease_generation  → test_only_lease_generation_keeps_the_previous_generation_out
#   lease_owner       → test_only_lease_owner_keeps_another_leases_owner_out
#   expected_status   → test_only_expected_status_keeps_a_write_off_a_parked_job
#   expires_at        → test_no_fenced_write_lands_after_a_real_expiry
#
# Before those six existed the suite measured only PART of the tuple, and that was measured, not
# assumed: with `attempt_id` stopped from fencing in all four statements the three older fencing
# tests reported `3 passed, 0 failed`, and with `lease_owner` stopped from fencing, `3 passed,
# 0 failed` again. Every stale token in them lost on `lease_generation` first, so the CAS never
# reached the other conditions. Hence the rule those tests now obey, and the reason this comment
# is here: A TEST CASE MUST REACH THE CONDITION IT CLAIMS TO MEASURE, not be caught by a
# neighbouring one.

#: The heartbeat. The CAS carries all five elements of the tuple AND `expires_at > now()`.
#:
#: `expected_status` ($5) is here because L2666 names the HEARTBEAT first in the list of writes
#: that must carry the whole tuple. It was missing until the mutation matrix above was written:
#: the comment claimed "all five" while the WHERE had four identifiers and the LEASE's status,
#: and no test could tell the difference. A renewal on a job that is no longer `RUNNING` renews
#: exactly the shape the reconciler of L2669 hunts — a `WAITING_*` job still holding a runtime
#: lease (L2147) — so refusing it is also the answer that tells the worker to stop.
#:
#: Without `expires_at > now()` the four identifiers still match after the lease has died, so a
#: worker frozen for two minutes renews a lease the reaper was entitled to take — and two
#: workers then believe they hold the same job, which is the failure L5211 forbids arriving
#: through the back door. `expires_at` cannot be moved backwards to test this cheaply: migration
#: 0003 blocks it with `CHECK (expires_at > acquired_at)`, so the test waits for a real expiry.
_HEARTBEAT_SQL = """
WITH fenced AS (
    UPDATE job_leases l
    SET heartbeat_at = now(),
        expires_at = now() + make_interval(secs => $6::double precision)
    WHERE l.job_id = $1
      AND l.attempt_id = $2
      AND l.lease_generation = $3
      AND l.lease_owner = $4
      AND l.status = 'ACTIVE'
      AND l.expires_at > now()
      AND EXISTS (SELECT 1 FROM jobs j WHERE j.id = l.job_id AND j.status = $5)
    RETURNING l.attempt_id, l.expires_at
),
mirrored AS (
    UPDATE job_attempts a
    SET heartbeat_at = now(), expires_at = f.expires_at
    FROM fenced f
    WHERE a.attempt_id = f.attempt_id AND a.status = 'RUNNING'
    RETURNING a.attempt_id
)
SELECT f.expires_at, (SELECT count(*) FROM mirrored) AS mirrored_rows
FROM fenced f
"""

#: Closing an attempt. The lease UPDATE carries the whole tuple, `expires_at > now()` AND the
#: expected job status; the other two updates hang off its `RETURNING`, so nothing is written
#: unless the CAS matched. Putting the job-status check inside that one WHERE (rather than in a
#: later CTE) is what makes the write all-or-nothing: a CTE cannot look at what an earlier CTE
#: of the same statement did, so a check placed later would run after the lease was already
#: released.
_CLOSE_ATTEMPT_SQL = """
WITH fenced AS (
    UPDATE job_leases l
    SET status = 'RELEASED', completed_at = now(), failure_class = $6
    WHERE l.job_id = $1
      AND l.attempt_id = $2
      AND l.lease_generation = $3
      AND l.lease_owner = $4
      AND l.status = 'ACTIVE'
      AND l.expires_at > now()
      AND EXISTS (SELECT 1 FROM jobs j WHERE j.id = l.job_id AND j.status = $5)
    RETURNING l.job_id, l.attempt_id
),
attempt_closed AS (
    UPDATE job_attempts a
    SET status = $7, completed_at = now(), failure_class = $6
    FROM fenced f
    WHERE a.attempt_id = f.attempt_id AND a.status = 'RUNNING'
    RETURNING a.attempt_id
),
job_closed AS (
    UPDATE jobs j
    SET status = $8, lock_version = j.lock_version + 1
    FROM fenced f
    WHERE j.id = f.job_id AND j.status = $5
    RETURNING j.id
)
SELECT
    (SELECT count(*) FROM fenced)         AS fenced_rows,
    (SELECT count(*) FROM attempt_closed) AS attempts_closed,
    (SELECT count(*) FROM job_closed)     AS jobs_closed
"""

#: Parking a job in a `WAITING_*` state. Same fencing, and the lease goes away — L2147: jobs in
#: `WAITING_*` do not hold the runtime lease. `not_before` is pushed into the future by the
#: server, because a parked job whose `not_before` is in the past is dispatchable again at once,
#: which turns waiting for an external condition into a loop with no pauses.
#:
#: `$6` does double duty here — it is BOTH the job's new status and the `failure_class` recorded
#: on the closed lease and attempt — and that is deliberate rather than thrifty: the waiting state
#: IS the reason the attempt ended, so writing it twice from one parameter makes them impossible
#: to disagree. Two parameters would be two chances for the screen to show a job waiting for a
#: quota and an attempt that ended for some other reason.
_PARK_SQL = """
WITH fenced AS (
    UPDATE job_leases l
    SET status = 'RELEASED', completed_at = now(), failure_class = $6
    WHERE l.job_id = $1
      AND l.attempt_id = $2
      AND l.lease_generation = $3
      AND l.lease_owner = $4
      AND l.status = 'ACTIVE'
      AND l.expires_at > now()
      AND EXISTS (SELECT 1 FROM jobs j WHERE j.id = l.job_id AND j.status = $5)
    RETURNING l.job_id, l.attempt_id
),
attempt_closed AS (
    UPDATE job_attempts a
    SET status = 'FAILED', completed_at = now(), failure_class = $6
    FROM fenced f
    WHERE a.attempt_id = f.attempt_id AND a.status = 'RUNNING'
    RETURNING a.attempt_id
),
job_parked AS (
    UPDATE jobs j
    SET status = $6,
        not_before = now() + make_interval(secs => $7::double precision),
        lock_version = j.lock_version + 1
    FROM fenced f
    WHERE j.id = f.job_id AND j.status = $5
    RETURNING j.id
)
SELECT
    (SELECT count(*) FROM fenced)         AS fenced_rows,
    (SELECT count(*) FROM attempt_closed) AS attempts_closed,
    (SELECT count(*) FROM job_parked)     AS jobs_closed
"""

#: Handing the job back for a later attempt. `dispatch_version` grows, because this IS a new
#: issue of the job (L2669). The caller's absolute moment is clamped with `greatest(…, now())`:
#: a `not_before` behind the clock is not a schedule, and the clock that matters is the
#: database's, not the caller's.
_REQUEUE_SQL = """
WITH fenced AS (
    UPDATE job_leases l
    SET status = 'RELEASED', completed_at = now(), failure_class = $6
    WHERE l.job_id = $1
      AND l.attempt_id = $2
      AND l.lease_generation = $3
      AND l.lease_owner = $4
      AND l.status = 'ACTIVE'
      AND l.expires_at > now()
      AND EXISTS (SELECT 1 FROM jobs j WHERE j.id = l.job_id AND j.status = $5)
    RETURNING l.job_id, l.attempt_id
),
attempt_closed AS (
    UPDATE job_attempts a
    SET status = 'FAILED', completed_at = now(), failure_class = $6
    FROM fenced f
    WHERE a.attempt_id = f.attempt_id AND a.status = 'RUNNING'
    RETURNING a.attempt_id
),
job_requeued AS (
    UPDATE jobs j
    SET status = 'READY',
        not_before = greatest($7::timestamptz, now()),
        dispatch_version = j.dispatch_version + 1,
        lock_version = j.lock_version + 1
    FROM fenced f
    WHERE j.id = f.job_id AND j.status = $5
    RETURNING j.id
)
SELECT
    (SELECT count(*) FROM fenced)         AS fenced_rows,
    (SELECT count(*) FROM attempt_closed) AS attempts_closed,
    (SELECT count(*) FROM job_requeued)   AS jobs_closed
"""

#: The live lease of a job, or nothing. Read ONLY to build a refusal that names who holds the
#: job now — an operator can act on "attempt X, generation 3 holds it", and cannot act on
#: "CAS failed". `expires_at > now()` is part of it: a lease past its deadline is not live, no
#: matter what its status column says.
#:
#: MEASURED by `test_no_fenced_write_lands_after_a_real_expiry`, which asserts that the refusal
#: after a real expiry carries `live is None` while the row is still `status = 'ACTIVE'`. Without
#: `expires_at > now()` here the refusal would name the dead lease as the current holder, and the
#: operator would be told to go and stop a worker that no longer holds anything.
_LIVE_LEASE_SQL = """
SELECT job_id, attempt_id, lease_generation, lease_owner
FROM job_leases
WHERE job_id = $1 AND status = 'ACTIVE' AND expires_at > now()
"""

#: The two reads behind the operator's screen. `holds_live_lease` is the field with teeth: it is
#: `ACTIVE` **and** `expires_at > now()`, because a lease nobody has renewed is not live no
#: matter what its status column says — and this value goes on the screen, where "live" means
#: "somebody is working on it, do not touch".
#:
#: MEASURED by `test_holds_live_lease_is_false_for_an_expired_lease_still_marked_active`, which
#: waits for a REAL expiry, asserts the row is still `ACTIVE` (so `status = 'ACTIVE'` cannot be
#: what produces the answer) and then reads the field through BOTH `fetch_job` and `fetch_jobs`.
#: Deleting `l.expires_at > now()` from this one EXISTS turns that test red — and, with that test
#: deselected, the same deletion passes the WHOLE package: `190 passed, 0 failed`, measured. So
#: until it was written, nothing at all stopped the screen from calling an abandoned lease live.
_JOB_COLUMNS_SQL = """
SELECT
    j.id, j.organization_id, j.workspace_id, j.job_type, j.routing_key, j.priority,
    j.payload_ref, j.dispatch_version, j.status, j.not_before, j.attempt_limit,
    j.idempotency_key, j.lock_version, j.created_at,
    (SELECT count(*) FROM job_attempts a WHERE a.job_id = j.id) AS attempt_count,
    EXISTS (
        SELECT 1 FROM job_leases l
        WHERE l.job_id = j.id AND l.status = 'ACTIVE' AND l.expires_at > now()
    ) AS holds_live_lease
FROM jobs j
"""


class JobStore:
    """Reads and writes the three queue tables. The only place in F1 that touches them.

    Holds a pool, not a connection: every method acquires, uses and returns one, so nothing here
    keeps a transaction open across an `await` the caller controls. A store handed a live
    connection would let one caller's forgotten transaction hold the claim lock for everybody.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- writing to the queue -------------------------------------------------

    async def enqueue(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID | None,
        job_type: str,
        routing_key: str,
        priority: int,
        attempt_limit: int,
        idempotency_key: str,
        payload_ref: str | None = None,
        delay_seconds: float = 0.0,
    ) -> EnqueuedJob:
        """Insert one job. A repeated idempotency key adds nothing and says so (L2820).

        `EnqueuedJob.created` is the ONLY place in this module where "inserted" is distinguishable
        from "was already there". MEASURED by
        `test_a_repeated_idempotency_key_reports_created_false_and_overwrites_nothing`, which
        checks the `False` value, the returned id, the row count AND that the second call's
        different `job_type` did not overwrite the first — `DO NOTHING`, not `DO UPDATE`.
        Measured: with that one test deselected, a `created=True` hardcoded on the conflict path
        passes the WHOLE package — `190 passed, 0 failed` — while telling every caller that a
        deduplicated job had just been added to the queue.

        `attempt_limit` has no default here and none in the schema. D-017 freezes the value 5 and
        keeps it in one configuration module; a default in this signature would be a second copy
        of that threshold, and two copies of a threshold drift without warning.
        """
        async with self._pool.acquire() as conn:
            job_id = await conn.fetchval(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority, payload_ref,
                     status, not_before, attempt_limit, idempotency_key)
                VALUES ($1, $2, $3, $4, $5, $6, 'READY',
                        now() + make_interval(secs => $7::double precision), $8, $9)
                ON CONFLICT (organization_id, idempotency_key) DO NOTHING
                RETURNING id
                """,
                organization_id,
                workspace_id,
                job_type,
                routing_key,
                priority,
                payload_ref,
                float(delay_seconds),
                attempt_limit,
                idempotency_key,
            )
            if job_id is not None:
                return EnqueuedJob(job_id=job_id, created=True)

            # The conflict path. The existing row is looked up rather than assumed, and its
            # absence is an error: `DO NOTHING` returning no id means either a duplicate key or
            # something else entirely, and reporting the second case as a successful enqueue
            # would be a job the caller believes exists and nobody ever runs.
            existing = await conn.fetchval(
                "SELECT id FROM jobs WHERE organization_id = $1 AND idempotency_key = $2",
                organization_id,
                idempotency_key,
            )
        if existing is None:
            raise RuntimeError(
                f"INSERT joba nie zwrócił id i nie ma wiersza o kluczu {idempotency_key!r} "
                f"w organizacji {organization_id}. Zero wstawionych wierszy zgłoszone jako "
                "sukces byłoby jobem, o którym wołający myśli, że istnieje, a nikt go nie wykona"
            )
        return EnqueuedJob(job_id=existing, created=False)

    async def claim_batch(
        self,
        *,
        organization_id: UUID,
        worker: UUID,
        limit: int,
        lease_seconds: float,
        job_types: Sequence[str] | None = None,
        exclude_job_types: Sequence[str] | None = None,
    ) -> ClaimBatch:
        """Claim at most `limit` dispatchable jobs. ONE statement; see `_CLAIM_SQL`.

        `worker` names the process asking. It deliberately does NOT become `lease_owner`: L2666
        requires a RANDOM owner per takeover, and the statement mints one with
        `gen_random_uuid()`. A stable worker identity in that column would be dead weight in the
        CAS tuple — a worker that restarted and re-claimed the same job would present the same
        value, so only the generation would be doing any fencing. The worker identity goes to the
        log, where it answers "who drained the queue", and nowhere else.
        """
        if limit < 1:
            raise ValueError(f"limit = {limit}: claim o zerowej wielkości nie jest zapytaniem")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _CLAIM_SQL,
                limit,
                float(lease_seconds),
                organization_id,
                list(job_types) if job_types is not None else None,
                list(exclude_job_types) if exclude_job_types is not None else None,
            )
        if not rows:
            raise RuntimeError(
                "zapytanie claimu nie zwróciło ANI JEDNEGO wiersza. Powinno zwrócić co najmniej "
                "wiersz z licznikami zaległości — brak go znaczy, że łańcuch CTE przestał "
                "startować od `backlog`, a wtedy pusty batch przestaje być rozróżnialny od "
                "pustej kolejki"
            )

        claims = tuple(
            ClaimedJob(
                job_id=row["job_id"],
                organization_id=row["organization_id"],
                workspace_id=row["workspace_id"],
                job_type=row["job_type"],
                routing_key=row["routing_key"],
                priority=row["priority"],
                payload_ref=row["payload_ref"],
                dispatch_version=row["dispatch_version"],
                attempt_limit=row["attempt_limit"],
                attempt_id=row["attempt_id"],
                attempt_no=row["attempt_no"],
                lease_generation=row["lease_generation"],
                lease_owner=row["lease_owner"],
                expires_at=row["expires_at"],
            )
            # The row carrying only the counters has a NULL job id — that is the LEFT JOIN of an
            # empty claim, not a job.
            for row in rows
            if row["job_id"] is not None
        )
        batch = ClaimBatch(
            claims=claims,
            due_before_claim=rows[0]["due_now"],
            scheduled_later=rows[0]["scheduled_later"],
            already_running=rows[0]["already_running"],
        )
        _LOG.debug("worker %s: %s", worker, batch.explain())
        return batch

    # -- writing under a lease (every one of these is a CAS) ------------------

    async def heartbeat(self, token: JobFencingToken, *, lease_seconds: float) -> datetime:
        """Renew the lease. Returns the new deadline; a stale or expired token is refused.

        A heartbeat is a renewal, never a resurrection — hence `expires_at > now()` inside the
        CAS as well as the four identifiers and `expected_status`. The tuple is bound in the same
        five positions as in `_fenced_write`; the duration is last.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                _HEARTBEAT_SQL,
                token.job_id,
                token.attempt_id,
                token.lease_generation,
                token.lease_owner,
                RUNNING_STATUS,
                float(lease_seconds),
            )
            if row is None:
                await self._refuse(conn, token, reason="heartbeat")
            if row["mirrored_rows"] != 1:
                # The lease was renewed but its attempt was not. Raising inside the transaction
                # rolls the renewal back; reporting success would leave the two rows disagreeing
                # about when the lease dies, and the reaper reads the attempt.
                raise RuntimeError(
                    f"heartbeat odświeżył lease, ale {row['mirrored_rows']} wierszy próby — "
                    f"job {token.job_id}, próba {token.attempt_id}. Rozjazd terminów między "
                    "lease'em a próbą znaczy, że reaper i worker patrzą na dwie różne prawdy"
                )
            return row["expires_at"]

    async def close_attempt(
        self,
        token: JobFencingToken,
        *,
        outcome: JobOutcome,
        failure_class: str | None = None,
    ) -> None:
        """Terminalize the job held under this token.

        `SUCCEEDED` and `FAILED` are both terminal here. This method never puts a job back into
        the queue — that is `requeue`, and whether one more attempt is allowed is the attempt
        limit's decision (L5214), which lives in the reaper and in exactly one place.
        """
        attempt_status = "SUCCEEDED" if outcome is JobOutcome.SUCCEEDED else "FAILED"
        await self._fenced_write(
            _CLOSE_ATTEMPT_SQL,
            token,
            reason=f"zapis wyniku {outcome.value}",
            arguments=(failure_class, attempt_status, outcome.value),
        )

    async def park_until_ready(
        self,
        token: JobFencingToken,
        *,
        waiting_state: JobWaitingState,
        delay_seconds: float,
    ) -> None:
        """Move the job into a `WAITING_*` state and RELEASE the lease (L2147, L5288).

        The attempt is closed with the waiting state as its `failure_class`. That is a choice the
        document does not make for us, and the reason it is made this way: `job_attempts` has no
        "paused" status (0003 allows `RUNNING`, `SUCCEEDED`, `FAILED`, `LEASE_EXPIRED`), and an
        attempt left `RUNNING` while its lease is released is precisely the shape the reconciler
        of L2669 hunts — a runnable job with no live lease. Recording WHY it stopped keeps the
        row actionable.

        CONSEQUENCE WORTH KNOWING: the closed attempt counts against `attempt_limit` like any
        other. A job that waits for authentication five times exhausts its limit without ever
        having run. Whether a wait should be free is the reaper's decision to make, in the one
        place that compares `attempt_no` with `attempt_limit` — not here.
        """
        await self._fenced_write(
            _PARK_SQL,
            token,
            reason=f"wstrzymanie w stanie {waiting_state.value}",
            arguments=(waiting_state.value, float(delay_seconds)),
        )

    async def requeue(
        self, token: JobFencingToken, *, not_before: datetime, failure_class: str | None = None
    ) -> None:
        """Hand the job back to the queue, dispatchable no earlier than `not_before`.

        The moment is clamped by the database with `greatest(…, now())`. The caller's clock is
        not the queue's clock, and a deadline already behind the queue's `now()` is not a delay.
        """
        await self._fenced_write(
            _REQUEUE_SQL,
            token,
            reason="oddanie joba do kolejki",
            arguments=(failure_class or "REQUEUED", not_before),
        )

    async def _fenced_write(
        self,
        sql: str,
        token: JobFencingToken,
        *,
        reason: str,
        arguments: tuple[object, ...],
    ) -> None:
        """Run one of the three-CTE writes above and insist that it wrote all three rows.

        The whole point is the counting. `asyncpg` reports a CAS that matched nothing as the
        string `"UPDATE 0"` and does not raise, so a fenced write that trusted the driver would
        announce the stale worker's result as accepted. Here zero fenced rows is a refusal, and
        a PARTIAL write — the lease released but the job not closed — raises inside the
        transaction so it rolls back, because half of this is worse than none of it.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                sql,
                token.job_id,
                token.attempt_id,
                token.lease_generation,
                token.lease_owner,
                RUNNING_STATUS,
                *arguments,
            )
            if row is None or row["fenced_rows"] == 0:
                await self._refuse(conn, token, reason=reason)
            if (row["attempts_closed"], row["jobs_closed"]) != (1, 1):
                raise RuntimeError(
                    f"{reason}: CAS dopasował {row['fenced_rows']} lease'ów, ale zamknął "
                    f"{row['attempts_closed']} prób i {row['jobs_closed']} jobów. Zapis "
                    "częściowy jest gorszy niż żaden — transakcja jest wycofywana"
                )

    async def _refuse(
        self, conn: asyncpg.Connection, token: JobFencingToken, *, reason: str
    ) -> NoReturn:
        """Turn a CAS that matched nothing into a refusal naming who holds the job now.

        Never returns. The freshness comparison itself belongs to
        `contracts.require_fresh_job_lease`, which `refuse_stale_write` calls — there is no
        second copy of it in this package.
        """
        live_row = await conn.fetchrow(_LIVE_LEASE_SQL, token.job_id)
        live = (
            JobFencingToken(
                job_id=live_row["job_id"],
                attempt_id=live_row["attempt_id"],
                lease_generation=live_row["lease_generation"],
                lease_owner=live_row["lease_owner"],
            )
            if live_row is not None
            else None
        )
        refuse_stale_write(presented=token, live=live, reason=reason)

    # -- moving a waiting job back into the pool -----------------------------

    async def publish_ready(self, job_id: UUID, *, not_before: datetime | None = None) -> None:
        """Make a waiting job dispatchable again, with a new dispatch version (L2147).

        Only the four `WAITING_*` states are accepted as a starting point, and anything else
        raises with the status it actually found. A silent no-op here would be a job the operator
        believes was released and the dispatcher never sees.
        """
        async with self._pool.acquire() as conn:
            published = await conn.fetchval(
                """
                UPDATE jobs
                SET status = 'READY',
                    not_before = greatest(coalesce($2::timestamptz, now()), now()),
                    dispatch_version = dispatch_version + 1,
                    lock_version = lock_version + 1
                WHERE id = $1 AND status = ANY($3::text[])
                RETURNING id
                """,
                job_id,
                not_before,
                list(REPUBLISHABLE_STATUSES),
            )
            if published is not None:
                return
            current = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
        if current is None:
            raise LookupError(f"nie ma joba {job_id} — nie ma czego ogłaszać gotowym")
        raise ValueError(
            f"job {job_id} jest w stanie {current}, a do puli wracają wyłącznie joby "
            f"oczekujące ({', '.join(REPUBLISHABLE_STATUSES)}). Zero zaktualizowanych wierszy "
            "zgłoszone jako sukces byłoby jobem, którego dispatcher nigdy nie zobaczy"
        )

    # -- reading -------------------------------------------------------------

    async def fetch_jobs(self, *, organization_id: UUID, limit: int = 100) -> tuple[JobView, ...]:
        """The most recent jobs of ONE organization, newest first, from the canonical store.

        `organization_id` is required and has no default. A read whose scope can be omitted
        eventually is, and the row that then shows up belongs to somebody else — the same rule
        `api/audit.py` states as "the query string is not an identity".
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _JOB_COLUMNS_SQL
                + """
                WHERE j.organization_id = $1
                ORDER BY j.created_at DESC, j.id
                LIMIT $2
                """,
                organization_id,
                limit,
            )
        return tuple(_job_view(row) for row in rows)

    async def fetch_job(self, job_id: UUID, *, organization_id: UUID) -> JobDetail | None:
        """One job plus every attempt it has had, in attempt order.

        Scoped like the list: a job of another organization is `None`, i.e. a 404 rather than a
        row. Identifiers are guessable, and "not yours" and "not there" must read the same from
        the outside.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _JOB_COLUMNS_SQL + " WHERE j.id = $1 AND j.organization_id = $2",
                job_id,
                organization_id,
            )
            if row is None:
                return None
            attempts = await conn.fetch(
                """
                SELECT attempt_id, attempt_no, dispatch_version, lease_generation, lease_owner,
                       status, not_before, heartbeat_at, expires_at, started_at, completed_at,
                       failure_class
                FROM job_attempts
                WHERE job_id = $1
                ORDER BY attempt_no
                """,
                job_id,
            )
        return JobDetail(
            job=_job_view(row),
            attempts=tuple(
                JobAttemptView(
                    attempt_id=attempt["attempt_id"],
                    attempt_no=attempt["attempt_no"],
                    dispatch_version=attempt["dispatch_version"],
                    lease_generation=attempt["lease_generation"],
                    lease_owner=attempt["lease_owner"],
                    status=attempt["status"],
                    not_before=attempt["not_before"],
                    heartbeat_at=attempt["heartbeat_at"],
                    expires_at=attempt["expires_at"],
                    started_at=attempt["started_at"],
                    completed_at=attempt["completed_at"],
                    failure_class=attempt["failure_class"],
                )
                for attempt in attempts
            ),
        )


def _job_view(row: asyncpg.Record) -> JobView:
    """One `jobs` row as the domain value. The only place the column names are transcribed."""
    return JobView(
        job_id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        job_type=row["job_type"],
        routing_key=row["routing_key"],
        priority=row["priority"],
        payload_ref=row["payload_ref"],
        dispatch_version=row["dispatch_version"],
        status=row["status"],
        not_before=row["not_before"],
        attempt_limit=row["attempt_limit"],
        idempotency_key=row["idempotency_key"],
        lock_version=row["lock_version"],
        created_at=row["created_at"],
        attempt_count=row["attempt_count"],
        holds_live_lease=row["holds_live_lease"],
    )
