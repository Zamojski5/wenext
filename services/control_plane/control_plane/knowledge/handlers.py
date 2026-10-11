"""Refreshing feeds in the queue this product already has. No model, no judgement, no cost.

WHY THE QUEUE AND NOT A CRON OR A THREAD. A feed refresh is a small job that has to survive a
restart, must not run twice at once, and has to be visible when it fails. The job store already
does leases, attempt limits and backoff; a thread inside the API process would do none of that
and would stop the moment somebody deployed.

TWO JOB TYPES, AND THE SPLIT IS DELIBERATE:

  * `knowledge.rss.sweep` reads which feeds are DUE and enqueues one job each. It fetches nothing
    itself, so a hundred feeds cannot turn a single job into a request storm holding one lease;
  * `knowledge.rss.refresh` reads exactly one feed. It is the unit that can fail, be retried and
    be reported on, and its failure is written onto that source rather than onto "RSS".

THE SWEEP CHAINS ITSELF, AND IT HAS AN END. It schedules the next sweep only while the
organization still has at least one RSS source; with none, the chain stops and adding a feed
starts it again (`api/knowledge.py` enqueues one when an RSS source is added). That is the shape
`pilot.cycle` already uses in this repository, including the part that matters: a self-chaining
job with no stop condition is a job that keeps running after everybody stops looking at it, and
the only way to end it is to find a row nobody remembers exists.

THE IDEMPOTENCY KEY IS THE TARGET HOUR, NOT THE CURRENT ONE. A sweep running at 13:30 that keyed
its successor by the hour it was RUNNING IN would collide with its own row and be dropped, which
ends the chain silently. Keyed by the hour it is due in, a duplicate is still a duplicate and the
chain still advances.

COST. `REFRESH_AFTER` is six hours. A feed read more often than it publishes is somebody else's
bandwidth spent to learn nothing, and P3 asks for this to be cheap in those words ("tanio, bez
modelu"). Nothing in this path calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.dispatcher import Handler
from control_plane.knowledge.ingest import Ingestor
from control_plane.knowledge.store import Scope

JOB_REFRESH = "knowledge.rss.refresh"
JOB_SWEEP = "knowledge.rss.sweep"
ROUTING_KEY = "knowledge.rss"

#: P5 etap A: what came of what we published, written down as EXPERIENCE. One query, one
#: comparison, one insert per changed publication, and no model anywhere in it.
JOB_EXPERIENCE = "knowledge.experience.sweep"
EXPERIENCE_ROUTING_KEY = "knowledge.experience"

#: How often the experience sweep looks. Hourly: the thing it is waiting for is a reply, a score
#: or a removal, and none of those need a finer clock than that.
EXPERIENCE_INTERVAL = timedelta(hours=1)

#: Below the pilot and the sessions. Nobody is waiting in front of a screen for a feed.
PRIORITY = 20

#: Reading a feed is idempotent and cheap, so a transient network failure may be retried. Three
#: and not the frozen five: a feed that fails three times in a row is a feed to look at, not one
#: to hammer.
ATTEMPT_LIMIT = 3

#: How stale a feed has to be before the sweep asks for it again.
REFRESH_AFTER = timedelta(hours=6)

#: Ceiling on one sweep, so a workspace with a thousand feeds does not enqueue a thousand jobs in
#: one go. The rest are still due on the next sweep.
SWEEP_LIMIT = 50

#: How often the sweep looks. Shorter than `REFRESH_AFTER` on purpose: the sweep costs one indexed
#: query and decides nothing, while the resolution of "six hours" would otherwise be twelve.
SWEEP_INTERVAL = timedelta(hours=1)

_PREFIX = "knowledge-source:"


def source_ref(source_id: UUID) -> str:
    return f"{_PREFIX}{source_id}"


def source_from_ref(payload_ref: str | None) -> UUID:
    if not payload_ref or not payload_ref.startswith(_PREFIX):
        raise ValueError(
            f"payload_ref={payload_ref!r} nie wskazuje materiału. Job bez wskazania źródła "
            "nie ma czego odświeżyć"
        )
    return UUID(payload_ref[len(_PREFIX) :])


async def schedule_refresh(
    port, *, organization_id: UUID, workspace_id: UUID, source_id: UUID
) -> object:
    """One refresh of one feed. Keyed by the source and the hour.

    The hour, not the minute and not the source alone: a sweep that runs every ten minutes must
    not enqueue six jobs for the same feed, and a human pressing "odśwież" an hour later must not
    be silently ignored.
    """
    hour = int(datetime.now(UTC).timestamp() // 3600)
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_REFRESH,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"knowledge-rss:{source_id}:{hour}",
        payload_ref=source_ref(source_id),
    )


async def schedule_sweep(port, *, organization_id: UUID, delay_seconds: float = 0.0) -> object:
    """The next look for due feeds. Keyed by the hour it is DUE in. See the module docstring."""
    due_hour = int((datetime.now(UTC).timestamp() + delay_seconds) // 3600)
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_SWEEP,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=1,
        idempotency_key=f"knowledge-rss-sweep:{due_hour}",
        delay_seconds=delay_seconds,
    )


async def schedule_experience_sweep(
    port, *, organization_id: UUID, delay_seconds: float = 0.0
) -> object:
    """The next look at what our publications did. Keyed by the hour it is due in.

    Same key rule as the RSS sweep and for the same measured reason: keyed by the hour it RUNS
    in, a sweep scheduling its successor would collide with its own row and the chain would stop
    without anybody noticing.
    """
    due_hour = int((datetime.now(UTC).timestamp() + delay_seconds) // 3600)
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_EXPERIENCE,
        routing_key=EXPERIENCE_ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=1,
        idempotency_key=f"knowledge-experience:{due_hour}",
        delay_seconds=delay_seconds,
    )


async def seed_experience_chain(pool, *, organization_id: UUID) -> bool:
    """Pierwsze ogniwo łańcucha doświadczeń dla jednej organizacji. Wołane przy starcie procesu.

    TO JEST TO SAMO BRAKUJĄCE OGNIWO, CO W `seed_ops_chain`, i z tego samego powodu: job zamawia
    wyłącznie swój NASTĘPNY bieg, więc łańcuch podtrzymuje się sam, ale nie zaczyna się sam.
    Dotąd startował wyłącznie z trasy `request_experience_sweep`, czyli po kliknięciu człowieka;
    zmierzone 21.09 przy czytaniu kodu etapu B. Pętla, która się podtrzymuje, i pętla, która
    RUSZA, to dwie różne własności, a testy miała tylko pierwsza.

    Zasiewamy tylko wtedy, gdy organizacja ma co mierzyć, czyli ma choć jedną publikację. Job bez
    publikacji i tak nic by nie zapisał i nie przedłużyłby łańcucha, ale zostawiłby w kolejce
    wiersz, który udaje pracę.

    Idempotentne po kluczu godziny: restart dołącza do istniejącego łańcucha zamiast budować
    drugi obok. Zwraca `True`, gdy zasiano.
    """
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    async with pool.acquire() as conn:
        published = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM publication_requests"
            " WHERE organization_id = $1 AND published_at IS NOT NULL)",
            organization_id,
        )
    if not published:
        return False
    await schedule_experience_sweep(
        PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
        delay_seconds=EXPERIENCE_INTERVAL.total_seconds(),
    )
    return True


@dataclass
class KnowledgeHandlers:
    """Both jobs over one pool and one ingestor.

    `ingestor` is injected so a test runs the real handler against a local fixture server and
    reaches no external network, which is the condition P3 puts on these tests.
    """

    pool: asyncpg.Pool
    ingestor: Ingestor
    organization_id: UUID
    queue: object | None = None
    refresh_after: timedelta = REFRESH_AFTER
    sweep_interval: timedelta = SWEEP_INTERVAL
    experience_interval: timedelta = EXPERIENCE_INTERVAL

    def as_mapping(self) -> dict[str, Handler]:
        return {
            JOB_REFRESH: self.refresh_feed,
            JOB_SWEEP: self.sweep,
            JOB_EXPERIENCE: self.sweep_experience,
        }

    async def due_sources(
        self, *, limit: int = SWEEP_LIMIT
    ) -> tuple[tuple[UUID, UUID, UUID | None], ...]:
        """`(source_id, workspace_id, project_id)` for every feed older than `refresh_after`.

        A source never fetched (`fetched_at IS NULL`) counts as due: it was added and not yet
        read, which is the most urgent case there is.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, workspace_id, project_id FROM knowledge_sources
                WHERE organization_id = $1 AND kind = 'RSS'
                  AND (fetched_at IS NULL OR fetched_at < now() - $2::interval)
                ORDER BY fetched_at NULLS FIRST, created_at
                LIMIT $3
                """,
                self.organization_id,
                self.refresh_after,
                limit,
            )
        return tuple((row["id"], row["workspace_id"], row["project_id"]) for row in rows)

    async def feed_count(self) -> int:
        """How many RSS sources this organization has. The chain's stop condition."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM knowledge_sources"
                " WHERE organization_id = $1 AND kind = 'RSS'",
                self.organization_id,
            )

    async def sweep(self, claim: ClaimedJob) -> None:
        if self.queue is None:
            raise RuntimeError(
                "sweep bez kolejki nie ma gdzie zlecić odświeżeń. To błąd konfiguracji, "
                "nie stan materiału"
            )
        for source_id, workspace_id, _project in await self.due_sources():
            await schedule_refresh(
                self.queue,
                organization_id=self.organization_id,
                workspace_id=workspace_id,
                source_id=source_id,
            )
        # The chain continues only while there is something to sweep. With the last feed gone it
        # ends here, and adding a feed starts it again.
        if await self.feed_count() > 0:
            await schedule_sweep(
                self.queue,
                organization_id=self.organization_id,
                delay_seconds=self.sweep_interval.total_seconds(),
            )

    async def sweep_experience(self, claim: ClaimedJob) -> None:
        """Write down what came of every published request, and update what changed.

        THE THREE DECISIONS THIS MAKES, all of them cheap and none of them a judgement:

          * nothing to say yet (too early, no reaction) leaves no row. See
            `experience.outcome_of`, which returns `None` for that case on purpose;
          * the same outcome as the recorded one leaves the recorded one alone. A sweep that
            rewrote every experience every hour would bury the two that changed;
          * a different outcome writes a NEW experience and supersedes the old one, so
            "it looked fine, then it was removed" stays readable as a sequence.

        The chain continues while the organization has any published request at all, and ends
        when it has none: a self-scheduling job with no stop condition is one nobody can turn off.
        """
        from control_plane.knowledge.experience import (
            changed,
            measure,
            outcome_of,
            payload_of,
            statement_of,
        )
        from control_plane.knowledge.store import KnowledgeStore, NewItem, Scope

        store = KnowledgeStore(self.pool)
        rows = await measure(self.pool, organization_id=self.organization_id)
        written = 0
        for row in rows:
            outcome = outcome_of(row)
            if outcome is None:
                continue
            scope = Scope(
                organization_id=self.organization_id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
            )
            existing = await store.experience_for(scope, str(row.request_id))
            if existing is not None and not changed(existing.payload, outcome):
                continue
            item = NewItem(
                kind="EXPERIENCE",
                statement=statement_of(row, outcome),
                # AGENT, because the product recorded it; `measured_by` in the payload says it
                # was a query and not a model, and the session tool repeats that in words.
                stated_by="AGENT",
                project_id=row.project_id,
                person_profile_id=row.person_profile_id,
                payload=payload_of(row, outcome),
            )
            if existing is None:
                await store.write(item, scope)
            else:
                # Never in place. The older measurement stays and points at the newer one.
                await store.correct(existing.id, item, scope)
            written += 1

        if self.queue is not None and rows:
            await schedule_experience_sweep(
                self.queue,
                organization_id=self.organization_id,
                delay_seconds=self.experience_interval.total_seconds(),
            )

    async def refresh_feed(self, claim: ClaimedJob) -> None:
        """Re-read one feed. A source that vanished is not an error; the job simply finishes."""
        source_id = source_from_ref(claim.payload_ref)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT workspace_id, project_id, locator, kind FROM knowledge_sources"
                " WHERE organization_id = $1 AND id = $2",
                self.organization_id,
                source_id,
            )
        if row is None or row["kind"] != "RSS":
            return
        scope = Scope(
            organization_id=self.organization_id,
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
        )
        # `add_feed` writes the outcome onto the source either way: fresh entries with a new hash,
        # or UNAVAILABLE with the reason. Nothing is raised for a feed that is simply down, so a
        # broken feed does not fill the queue with failed jobs.
        await self.ingestor.add_feed(scope, row["locator"])
