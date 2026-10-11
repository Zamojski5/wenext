"""The dispatcher loop, in the SAME PROCESS as the API (L5193), with polling as its floor.

    "PostgreSQL jobs jako canonical state; dispatcher w tym samym procesie na
     `SELECT ... FOR UPDATE SKIP LOCKED`" (L5193)
    "restart komponentu nie gubi taska" (L5212)

WHY POLLING IS NOT AN OPTIMISATION TO BE REMOVED LATER. A dispatcher woken ONLY by
`LISTEN/NOTIFY` — or only by an in-process signal from the code that enqueued the job — loses
every job inserted while it was not listening, and loses it FOR EVER: the notification is fired
once, into nobody, and the row stays `READY` with nothing left to announce it. The failure is
invisible, because the queue looks healthy and the dispatcher looks alive.

So this loop has two floors under it, and both are tested:

*   **a scan at process start**, before the first sleep — for everything inserted while the
    process was down
    (`test_a_job_inserted_while_the_dispatcher_was_down_is_executed_after_it_starts`),
*   **a hard poll interval** — for everything inserted while it was up but nobody signalled
    (`test_a_job_inserted_after_the_loop_started_runs_without_any_notification`).

`wake()` exists on top of those as an optimisation, never as the mechanism. Deleting `wake()`
costs latency; deleting either floor costs jobs.

TWO PASSES PER TICK, AND THE ORDER IS THE MEANING:

1.  **Activation** of replacement attempts the reaper scheduled. Those jobs sit in
    `WAITING_RETRY` and are NOT dispatchable, so `JobStore.claim_batch` cannot see them — which
    is exactly why they are parked there. If such a job were left `READY`, the claim statement
    would mint its OWN `job_attempts` row (`max(attempt_no) + 1`) and the reaper's replacement
    row would be skipped for ever: one phantom attempt per retry, and half of every job's attempt
    budget spent on rows nobody ran. See `jobs/reaper.py` for the other half of this reasoning.
2.  **Claim** of fresh jobs through the domain command, which is the one path that exists.

WHAT THIS DISPATCHER DOES WITH A JOB IT CANNOT RUN. Phase E1.F1 has NO handlers at all — D-013
is explicit that the producer of real work arrives in F3/F4 and that the dev CLI is a test bench,
not production data. An unknown `job_type` is therefore a missing capability, not a failed
attempt, and the job is parked in `WAITING_CAPABILITY` with its lease released (L2147) instead of
being terminalized. Nothing republishes it, so it waits — visibly, on the operator's screen, with
the reason recorded on the attempt.

WHAT THIS DISPATCHER DELIBERATELY DOES NOT DO:

*   **It never grants a retry.** A handler that raises closes its attempt as `FAILED`, which is
    terminal. Automatic retry in F1 comes from lease expiry alone, because the comparison of
    `attempt_no` with `attempt_limit` (L5214) lives in ONE place — the reaper — and
    `domain/commands.py` says the same thing about itself. A second comparison here would be a
    second answer to the same question.
*   **It runs handlers one at a time.** Concurrency inside one dispatcher is not in F1's scope
    (L5500-5501), and with no handlers in F1 it would be untested machinery. A slow handler
    therefore delays the next tick; the heartbeat below is what keeps it from losing its lease,
    and since 26.09 every claim of a tick is beaten from the moment it is claimed. Concurrency
    BETWEEN jobs comes from running two dispatchers per organization on disjoint LANES
    (`jobs/lanes.py`), never from parallelism inside one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

import asyncpg
from community_os_contracts import JobFencingToken, JobWaitingState

from control_plane.domain import commands
from control_plane.domain.errors import JobFencingRefused
from control_plane.domain.models import BatchEmptiness, ClaimedJob, JobOutcome
from control_plane.jobs.backoff import (
    DISPATCHER_POLL_INTERVAL_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_TTL_SECONDS,
    backoff_seconds,
)
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.reaper import WAITING_RETRY_STATUS
from control_plane.jobs.store import JobStore

_LOG = logging.getLogger(__name__)

#: One claimed job, handed to whatever knows how to run it. Takes the claim, not just the token:
#: a handler needs `job_type`, `payload_ref` and the tenant columns, and the token alone would
#: force every handler to read the row back.
Handler = Callable[[ClaimedJob], Awaitable[None]]

#: `failure_class` written when a handler raises. Not spelled like any `ExecutionAttemptState`
#: value on purpose — the reaper reads that column as a possible execution state, and a queue-level
#: class that parsed as one would make the retry decision read the dispatcher's bookkeeping as a
#: report about a real side effect.
HANDLER_ERROR_FAILURE_CLASS = "HANDLER_ERROR"

#: Where a job with no handler waits. See the module docstring.
UNHANDLED_WAITING_STATE = JobWaitingState.WAITING_CAPABILITY


class WaitFor(Exception):
    """Raised by a handler that CANNOT run its job now and knows why — no adapter, no
    credentials, no user profile, a model that is not logged in.

    Not a failure. The dispatcher parks the job in the named `WAITING_*` state with the lease
    released (L2147), exactly as it parks a job with no handler at all, and the operator sees the
    state on the queue screen. `POST /api/jobs/waiting/resume` brings it back once the cause is
    fixed. A handler that raised anything else would terminalize the job (`HANDLER_ERROR`), and
    "log in first" is not a defect of the job.

    `WAITING_RETRY` is refused: that state belongs to the reaper's replacement attempts and the
    dispatcher's activation pass expects one to exist for it (`jobs/reaper.py`, point 1).
    """

    def __init__(self, waiting_state: JobWaitingState, reason: str) -> None:
        if waiting_state is JobWaitingState.WAITING_RETRY:
            raise ValueError(
                "WaitFor(WAITING_RETRY) jest zakazane: ten stan należy do reapera i wymaga "
                "zaplanowanej próby zastępczej, której handler nie tworzy"
            )
        if not reason.strip():
            raise ValueError("WaitFor bez powodu — operator nie dowie się, na co job czeka")
        super().__init__(f"{waiting_state.value}: {reason}")
        self.waiting_state = waiting_state
        self.reason = reason


async def _stop(task: asyncio.Task[None]) -> None:
    """Cancel a heartbeat task and wait for it. Idempotent: a finished task is left alone."""
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class _RunOutcome(StrEnum):
    """How running one claim ended. Private: it never reaches the database or an artifact."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNHANDLED = "UNHANDLED"


@dataclass(frozen=True)
class TickReport:
    """What one tick did. Every field is a count, because counting is how a stall is noticed.

    `emptiness` is the claim's own diagnosis of an empty batch (`domain/models.py`): a drained
    queue and a queue whose every job is held by somebody else read the same from the outside and
    are not the same situation.
    """

    activated: int
    claimed: int
    succeeded: int
    failed: int
    unhandled: int
    emptiness: BatchEmptiness

    @property
    def taken(self) -> int:
        return self.activated + self.claimed

    def explain(self) -> str:
        """One sentence for the operator (D-010)."""
        if not self.taken:
            return f"dispatcher: nic nie wziął ({self.emptiness.value})"
        return (
            f"dispatcher: aktywowanych {self.activated}, pobranych {self.claimed}, "
            f"udanych {self.succeeded}, nieudanych {self.failed}, "
            f"bez handlera {self.unhandled}"
        )


# ---------------------------------------------------------------------------
# Activation of a scheduled replacement attempt
# ---------------------------------------------------------------------------
# ONE statement, for the reason `jobs/store.py` states in its first rule: a lock taken by a
# separate SELECT lives exactly as long as ITS transaction, so a second dispatcher's SKIP LOCKED
# would have nothing left to skip. Reading it from the bottom up:
#
#   due      — the attempts the reaper scheduled whose `not_before` has arrived, locked with
#              SKIP LOCKED. MATERIALIZED because three CTEs read it and an inlined CTE may be
#              evaluated more than once — which would take the lock more than once.
#   started  — the attempt row gets its real `started_at` and the lease deadline, computed by
#              PostgreSQL (L2668).
#   lease    — the `job_leases` row, ACTIVE, carrying the generation and owner the REAPER minted.
#              The token a worker will present is therefore the one the reaper wrote down, which
#              is what makes `lease_generation` monotonic across a takeover.
#   running  — the job leaves `WAITING_RETRY` for `RUNNING`.
#
# The job does NOT pass through `READY` on the way. That is not a shortcut: `READY` is exactly
# the window in which `JobStore.claim_batch` would grab it and mint a second attempt row for the
# same retry.
_ACTIVATE_SQL = """
WITH due AS MATERIALIZED (
    SELECT a.attempt_id
    FROM job_attempts a
    JOIN jobs j ON j.id = a.job_id
    WHERE j.organization_id = $1
      AND j.status = $4
      AND a.status = 'RUNNING'
      AND a.completed_at IS NULL
      AND a.not_before IS NOT NULL
      AND a.not_before <= now()
      AND NOT EXISTS (
          SELECT 1 FROM job_leases l WHERE l.job_id = a.job_id AND l.status = 'ACTIVE'
      )
      -- The same lane filter as the claim (`jobs/lanes.py`): a retry of an engine job must not
      -- be activated by the lane that runs next to a conversation turn.
      AND ($5::text[] IS NULL OR j.job_type = ANY($5::text[]))
      AND ($6::text[] IS NULL OR NOT (j.job_type = ANY($6::text[])))
    ORDER BY a.not_before
    LIMIT $2
    FOR UPDATE OF j, a SKIP LOCKED
),
started AS (
    UPDATE job_attempts a
    SET started_at = now(),
        heartbeat_at = now(),
        expires_at = now() + make_interval(secs => $3::double precision)
    FROM due d
    WHERE a.attempt_id = d.attempt_id
    RETURNING a.attempt_id, a.organization_id, a.job_id, a.attempt_no, a.dispatch_version,
              a.lease_generation, a.lease_owner, a.expires_at
),
lease AS (
    INSERT INTO job_leases
        (organization_id, job_id, attempt_id, attempt_no, dispatch_version, lease_generation,
         lease_owner, status, expires_at)
    SELECT s.organization_id, s.job_id, s.attempt_id, s.attempt_no, s.dispatch_version,
           s.lease_generation, s.lease_owner, 'ACTIVE', s.expires_at
    FROM started s
    RETURNING attempt_id
),
running AS (
    UPDATE jobs j
    SET status = 'RUNNING',
        lock_version = j.lock_version + 1
    FROM started s
    WHERE j.id = s.job_id AND j.status = $4
    RETURNING j.id
)
SELECT
    s.job_id, s.organization_id, s.attempt_id, s.attempt_no, s.dispatch_version,
    s.lease_generation, s.lease_owner, s.expires_at,
    j.workspace_id, j.job_type, j.routing_key, j.priority, j.payload_ref, j.attempt_limit,
    (SELECT count(*) FROM lease)   AS leases_created,
    (SELECT count(*) FROM running) AS jobs_started
FROM started s
JOIN jobs j ON j.id = s.job_id
"""


class JobDispatcher:
    """Claims and runs jobs of ONE organization, in the process that serves the API.

    Builds its own store and transport from the pool, so there is exactly one place where the
    queue's plumbing is wired together. `handlers` maps `job_type` to the coroutine that runs it;
    in phase E1.F1 it is empty, and the module docstring says what that means for a job.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        organization_id: UUID,
        handlers: Mapping[str, Handler] | None = None,
        worker: UUID | None = None,
        lease_seconds: float = LEASE_TTL_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        poll_interval_seconds: float = DISPATCHER_POLL_INTERVAL_SECONDS,
        batch_limit: int = 8,
        jitter: random.Random | None = None,
        job_types: Collection[str] | None = None,
        exclude_job_types: Collection[str] | None = None,
        lane: str = "all",
    ) -> None:
        if heartbeat_seconds >= lease_seconds:
            raise ValueError(
                f"heartbeat {heartbeat_seconds} s przy TTL lease'u {lease_seconds} s: bicie serca "
                "rzadsze niż wygaśnięcie nie odnawia niczego. D-017 zamraża TTL/4, żeby trzy "
                "pominięte uderzenia nie zabierały joba"
            )
        self._pool = pool
        self._organization_id = organization_id
        self._store = JobStore(pool)
        self._port = PostgresJobTransport(self._store, organization_id=organization_id)
        self._handlers: dict[str, Handler] = dict(handlers or {})
        self._worker = worker or uuid4()
        self._lease_seconds = float(lease_seconds)
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._batch_limit = batch_limit
        self._jitter = jitter if jitter is not None else random.Random()
        self._wake = asyncio.Event()
        # LANES (`jobs/lanes.py`). Sorted tuples, because they go into SQL as arrays and a stable
        # order keeps the statement's parameters comparable between runs in the log.
        self._job_types = tuple(sorted(job_types)) if job_types is not None else None
        self._exclude_job_types = (
            tuple(sorted(exclude_job_types)) if exclude_job_types is not None else None
        )
        self._lane = lane

    @property
    def worker(self) -> UUID:
        """Which process this is, for the log and for `claim_batch`."""
        return self._worker

    def wake(self) -> None:
        """Ask the loop to tick before its next scheduled poll. An OPTIMISATION, never the path.

        Safe to call from anywhere in this process and safe to call when nothing is listening —
        the event is simply set, and the next `run_forever` iteration consumes it. Everything this
        method makes faster still happens without it, one poll interval later.
        """
        self._wake.set()

    # -- one tick -------------------------------------------------------------

    async def tick(self) -> TickReport:
        """Activate what is due, claim what is ready, run all of it. Returns what happened."""
        activated = await self.activate_due_attempts()
        batch = await commands.claim_batch(
            self._port,
            worker=self._worker,
            limit=self._batch_limit,
            lease_seconds=self._lease_seconds,
            job_types=self._job_types,
            exclude_job_types=self._exclude_job_types,
        )

        claims = (*activated, *batch.claims)
        # EVERY CLAIM IS BEATEN FROM THE MOMENT IT IS CLAIMED, not from the moment it starts.
        # Until 26.09 only the running job renewed its lease, so the jobs queued behind a slow
        # one lost theirs after 60 s and their results were refused when they finally ran
        # (measured: dozens of "ODRZUCONY" per hour after the outage). Each beat is stopped by
        # `_run_one` before the job's result is written.
        beats = {
            claim.attempt_id: asyncio.create_task(
                self._heartbeat_until_cancelled(claim.fencing_token)
            )
            for claim in claims
        }
        succeeded = failed = unhandled = 0
        try:
            for claim in claims:
                outcome = await self._run_one(claim, beat=beats[claim.attempt_id])
                if outcome is _RunOutcome.SUCCEEDED:
                    succeeded += 1
                elif outcome is _RunOutcome.FAILED:
                    failed += 1
                else:
                    unhandled += 1
        finally:
            for beat in beats.values():
                await _stop(beat)

        report = TickReport(
            activated=len(activated),
            claimed=len(batch.claims),
            succeeded=succeeded,
            failed=failed,
            unhandled=unhandled,
            emptiness=batch.emptiness,
        )
        if report.taken:
            _LOG.info("%s", report.explain())
        return report

    async def activate_due_attempts(self) -> tuple[ClaimedJob, ...]:
        """Give the reaper's scheduled replacement attempts their leases. ONE statement.

        A partial activation — the attempt started but no lease written, or the job left in
        `WAITING_RETRY` — raises inside the transaction so it rolls back. Half of this is worse
        than none of it: an attempt marked started with no lease is a job no fencing token can be
        presented for, and the reaper would not see it either, because the reaper looks for
        expired LEASES.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch(
                _ACTIVATE_SQL,
                self._organization_id,
                self._batch_limit,
                self._lease_seconds,
                # The status comes from the reaper's own constant, not from a second literal in
                # this file: the two modules have to name the SAME waiting state or the retry path
                # stops halfway, with jobs parked for ever and nothing on fire.
                WAITING_RETRY_STATUS,
                list(self._job_types) if self._job_types is not None else None,
                list(self._exclude_job_types) if self._exclude_job_types is not None else None,
            )
            if not rows:
                return ()
            leases_created = rows[0]["leases_created"]
            jobs_started = rows[0]["jobs_started"]
            if (leases_created, jobs_started) != (len(rows), len(rows)):
                raise RuntimeError(
                    f"aktywacja {len(rows)} zaplanowanych prób zapisała {leases_created} "
                    f"lease'ów i {jobs_started} jobów. Zapis częściowy jest gorszy niż żaden — "
                    "próba wystartowana bez lease'u nie ma tokenu fencingu, a reaper jej nie "
                    "znajdzie, bo reaper szuka wygasłych LEASE'ÓW"
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
            for row in rows
        )
        _LOG.info(
            "dispatcher aktywował %d zaplanowanych prób: %s",
            len(claims),
            ", ".join(f"{claim.job_id}#{claim.attempt_no}" for claim in claims),
        )
        return claims

    async def _run_one(
        self, claim: ClaimedJob, *, beat: asyncio.Task[None] | None = None
    ) -> _RunOutcome:
        """Run one claimed job under a heartbeat and record its result. Never raises.

        A refusal from the fenced write (`JobFencingRefused`) is logged and swallowed: it means
        the lease was reaped while the handler ran, the reaper already owns the job, and raising
        here would take down the tick over a job that is being handled correctly by somebody else.
        """
        handler = self._handlers.get(claim.job_type)
        if handler is None:
            if beat is not None:
                await _stop(beat)
            await self._park_unhandled(claim)
            return _RunOutcome.UNHANDLED

        try:
            if beat is None:
                await self._with_heartbeat(claim.fencing_token, handler(claim))
            else:
                # The beat started at claim time (`tick`). It stops BEFORE the result is written:
                # a beat racing the final CAS would be refused and logged as a lost lease.
                try:
                    await handler(claim)
                finally:
                    await _stop(beat)
        except WaitFor as wait:
            _LOG.warning(
                "job %s (typ %s, próba %s) nie może teraz ruszyć: %s — idzie w %s, lease zwolniony",
                claim.job_id,
                claim.job_type,
                claim.attempt_no,
                wait.reason,
                wait.waiting_state.value,
            )
            await self._park(claim, wait.waiting_state)
            return _RunOutcome.UNHANDLED
        except Exception:
            _LOG.exception(
                "handler joba %s (typ %s, próba %s) rzucił wyjątek — próba zostaje zamknięta "
                "jako %s, a job jest terminalny. Automatyczne ponowienie w F1 pochodzi wyłącznie "
                "z wygaśnięcia lease'u, bo limit prób porównuje się w jednym miejscu (L5214)",
                claim.job_id,
                claim.job_type,
                claim.attempt_no,
                HANDLER_ERROR_FAILURE_CLASS,
            )
            await self._close(
                claim,
                outcome=JobOutcome.FAILED,
                failure_class=HANDLER_ERROR_FAILURE_CLASS,
            )
            return _RunOutcome.FAILED

        await self._close(claim, outcome=JobOutcome.SUCCEEDED, failure_class=None)
        return _RunOutcome.SUCCEEDED

    async def _close(
        self, claim: ClaimedJob, *, outcome: JobOutcome, failure_class: str | None
    ) -> None:
        try:
            await commands.report_result(
                self._port,
                token=claim.fencing_token,
                outcome=outcome,
                failure_class=failure_class,
            )
        except JobFencingRefused as refused:
            _LOG.error(
                "wynik %s joba %s ODRZUCONY: %s. Lease został odebrany w trakcie pracy — "
                "job należy teraz do reapera i tego zapisu nie wolno przepchnąć",
                outcome.value,
                claim.job_id,
                refused,
            )

    async def _park_unhandled(self, claim: ClaimedJob) -> None:
        """Give the lease back and park the job: nobody in this process handles this type."""
        _LOG.warning(
            "brak handlera dla typu %s (job %s) — job idzie w %s, lease zwolniony. Handlery "
            "rejestruje control_plane.observe.wiring; typ spoza tej mapy nikt tu nie wykona",
            claim.job_type,
            claim.job_id,
            UNHANDLED_WAITING_STATE.value,
        )
        await self._park(claim, UNHANDLED_WAITING_STATE)

    async def _park(self, claim: ClaimedJob, waiting_state: JobWaitingState) -> None:
        delay = backoff_seconds(claim.attempt_no, jitter=self._jitter)
        try:
            await commands.release_on_waiting(
                self._port,
                token=claim.fencing_token,
                waiting_state=waiting_state,
                # A parked job with `not_before` in the past is dispatchable again at once, so the
                # delay must be positive; the frozen backoff schedule is reused rather than a
                # sixth number invented next to it.
                delay_seconds=max(delay, 1.0),
            )
        except JobFencingRefused as refused:
            _LOG.error("wstrzymanie joba %s ODRZUCONE: %s", claim.job_id, refused)

    async def _with_heartbeat(self, token: JobFencingToken, work: Awaitable[None]) -> None:
        """Run `work` while renewing its lease every `heartbeat_seconds` (D-017: TTL/4).

        Without this, any handler slower than the lease TTL has its job taken by the reaper while
        it is still working — two workers on one record, which is the failure L5211 forbids,
        arriving through the back door. `jobs/store.py` says the same thing about the CAS.
        """
        beat = asyncio.create_task(self._heartbeat_until_cancelled(token))
        try:
            await work
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _heartbeat_until_cancelled(self, token: JobFencingToken) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._store.heartbeat(token, lease_seconds=self._lease_seconds)
            except JobFencingRefused as refused:
                # The lease is gone. Stop beating and let the handler finish; its own write will
                # be refused by the same CAS, which is the correct outcome and is logged there.
                _LOG.error(
                    "heartbeat joba %s ODRZUCONY: %s. Lease przepadł w trakcie pracy",
                    token.job_id,
                    refused,
                )
                return

    # -- the loop -------------------------------------------------------------

    async def run_forever(self) -> None:
        """Tick for ever. The FIRST tick happens before the first wait — see the docstring.

        An exception from a tick is logged with its traceback and the loop lives on. A loop that
        dies on the first unexpected error stops dispatching for ever, and every unit test of the
        pieces stays green — this project's recurring failure shape, not a new one.
        """
        _LOG.info(
            "dispatcher startuje: tor %s, worker %s, poll %.1f s, TTL lease'u %.1f s",
            self._lane,
            self._worker,
            self._poll_interval_seconds,
            self._lease_seconds,
        )
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception(
                    "tick dispatchera padł — pętla żyje dalej. Pętla umierająca po pierwszym "
                    "wyjątku gubi wszystkie kolejne zadania PRZY ZIELONYCH TESTACH"
                )
            await self._wait_for_the_next_tick()

    async def _wait_for_the_next_tick(self) -> None:
        """Wait for `wake()` or for the poll interval, whichever comes first.

        The timeout is the floor. `wake()` only makes the loop earlier, never later, and a
        dispatcher whose `wake()` is never called still drains the queue every interval.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval_seconds)
        self._wake.clear()
