"""Persistence of the pilot: the manifest, the cycles, the reactions (migration 0006).

The one method that matters most is `reserve_model_call`: a single `UPDATE … WHERE model_calls_used
< model_calls_budget RETURNING`, executed BEFORE the model is asked. A restart between the
reservation and the answer costs one call of budget and nothing else; a restart never resets the
counter, because the counter is a row, not a variable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

RUN_ACTIVE = "ACTIVE"
RUN_FINISHED = "FINISHED"
RUN_STOPPED = "STOPPED"

REVIEW_PENDING = "HUMAN_REVIEW_PENDING"


@dataclass(frozen=True)
class PilotRun:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    community_id: UUID
    status: str
    starts_at: datetime
    ends_at: datetime
    stopped_at: datetime | None
    poll_interval_seconds: int
    model_calls_budget: int
    model_calls_used: int
    max_generations_per_thread: int
    profile_version_id: UUID
    prompt_name: str
    prompt_sha256: str
    paused_job_types: tuple[str, ...]
    restore: dict[str, Any]
    host_events: list[dict[str, Any]]
    created_at: datetime
    # 0007: pace of publication and the stop flag (R4 minimal, docs/ZGODY-PUBLIKACJI-2026-09-07.md).
    max_publications_per_hour: int = 1
    max_publications_per_day: int = 6
    publishing_stopped_at: datetime | None = None

    @property
    def budget_left(self) -> int:
        return self.model_calls_budget - self.model_calls_used

    def active_at(self, now: datetime) -> bool:
        return self.status == RUN_ACTIVE and self.stopped_at is None and now < self.ends_at


@dataclass(frozen=True)
class PilotReaction:
    id: UUID
    run_id: UUID
    thread_id: UUID
    origin: str
    target_message_external_id: str
    decision: str
    draft: str | None
    contribution: str | None
    brief_rationale: str
    confidence: float
    model: str
    effort: str
    profile_version_id: UUID
    prompt_name: str
    prompt_sha256: str
    input_sha256: str
    situation_sha256: str
    read_at: datetime
    context_complete: bool
    comments_seen: int
    wait_reason: str | None
    next_check_at: datetime | None
    review_status: str
    created_at: datetime
    # joined from observed_threads
    thread_external_id: str = ""
    thread_url: str = ""
    thread_title: str = ""
    # 0009: the move the draft is built around (prompt v3); NULL for reactions before it.
    move: str | None = None
    # 0010: the move a cycle asked the generator to try. Set only between 2026-09-16 and
    # 2026-09-17, while the experiment assigned moves (D-031); NULL since D-036 gave the choice
    # back to the model. Kept because the rows of those two days carry it.
    requested_move: str | None = None


@dataclass(frozen=True)
class PilotCycle:
    id: UUID
    run_id: UUID
    slot: int
    planned_at: datetime
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    detail: str | None
    model_calls_used_after: int


def _run(row: asyncpg.Record) -> PilotRun:
    restore = row["restore"]
    events = row["host_events"]
    return PilotRun(
        id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        community_id=row["community_id"],
        status=row["status"],
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        stopped_at=row["stopped_at"],
        poll_interval_seconds=row["poll_interval_seconds"],
        model_calls_budget=row["model_calls_budget"],
        model_calls_used=row["model_calls_used"],
        max_generations_per_thread=row["max_generations_per_thread"],
        profile_version_id=row["profile_version_id"],
        prompt_name=row["prompt_name"],
        prompt_sha256=row["prompt_sha256"],
        paused_job_types=tuple(row["paused_job_types"] or ()),
        restore=json.loads(restore) if isinstance(restore, str) else dict(restore),
        host_events=list(json.loads(events) if isinstance(events, str) else events),
        created_at=row["created_at"],
        max_publications_per_hour=row["max_publications_per_hour"],
        max_publications_per_day=row["max_publications_per_day"],
        publishing_stopped_at=row["publishing_stopped_at"],
    )


_RUN_COLUMNS = (
    "id, organization_id, workspace_id, community_id, status, starts_at, ends_at, stopped_at, "
    "poll_interval_seconds, model_calls_budget, model_calls_used, max_generations_per_thread, "
    "profile_version_id, prompt_name, prompt_sha256, paused_job_types, restore, host_events, "
    "created_at, max_publications_per_hour, max_publications_per_day, publishing_stopped_at"
)

_REACTION_COLUMNS = (
    "r.id, r.run_id, r.thread_id, r.origin, r.target_message_external_id, r.decision, r.draft, "
    "r.contribution, r.brief_rationale, r.confidence, r.model, r.effort, r.profile_version_id, "
    "r.prompt_name, r.prompt_sha256, r.input_sha256, r.situation_sha256, r.read_at, "
    "r.context_complete, r.comments_seen, r.wait_reason, r.next_check_at, r.review_status, "
    "r.created_at, t.external_id AS thread_external_id, t.url AS thread_url, "
    "t.title AS thread_title, r.move, r.requested_move"
)


def _reaction(row: asyncpg.Record) -> PilotReaction:
    return PilotReaction(**dict(row))


class PilotStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- runs -----------------------------------------------------------------------------

    async def create_run(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        community_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        poll_interval_seconds: int,
        model_calls_budget: int,
        max_generations_per_thread: int,
        profile_version_id: UUID,
        prompt_name: str,
        prompt_sha256: str,
        paused_job_types: Sequence[str],
        restore: dict[str, Any],
    ) -> PilotRun:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO pilot_runs
                    (organization_id, workspace_id, community_id, status, starts_at, ends_at,
                     poll_interval_seconds, model_calls_budget, max_generations_per_thread,
                     profile_version_id, prompt_name, prompt_sha256, paused_job_types, restore)
                VALUES ($1, $2, $3, '{RUN_ACTIVE}', $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13::jsonb)
                RETURNING {_RUN_COLUMNS}
                """,
                organization_id,
                workspace_id,
                community_id,
                starts_at,
                ends_at,
                poll_interval_seconds,
                model_calls_budget,
                max_generations_per_thread,
                profile_version_id,
                prompt_name,
                prompt_sha256,
                list(paused_job_types),
                json.dumps(restore),
            )
        return _run(row)

    async def get_run(self, run_id: UUID) -> PilotRun | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_RUN_COLUMNS} FROM pilot_runs WHERE id = $1", run_id
            )
        return _run(row) if row else None

    async def active_run_for_community(self, community_id: UUID) -> PilotRun | None:
        """The ACTIVE manifest of a community, or None. `active_at(now)` is the caller's second
        check: a run past `ends_at` is still ACTIVE in the table until a cycle finishes it."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_RUN_COLUMNS} FROM pilot_runs
                WHERE community_id = $1 AND status = '{RUN_ACTIVE}'
                """,
                community_id,
            )
        return _run(row) if row else None

    async def latest_run(self, *, organization_id: UUID) -> PilotRun | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_RUN_COLUMNS} FROM pilot_runs WHERE organization_id = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                organization_id,
            )
        return _run(row) if row else None

    async def reserve_model_call(self, run_id: UUID) -> int | None:
        """Reserve ONE model call: the new `model_calls_used`, or None when the budget is gone.
        One statement, before the call, durable."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"""
                UPDATE pilot_runs
                   SET model_calls_used = model_calls_used + 1
                 WHERE id = $1 AND status = '{RUN_ACTIVE}'
                   AND model_calls_used < model_calls_budget
                RETURNING model_calls_used
                """,
                run_id,
            )

    async def release_model_call(self, run_id: UUID) -> int | None:
        """Give ONE reservation back: the provider refused the call before doing any work (usage
        limit, outage), so the pilot's budget must not pay for it. Only the model's own refusal
        qualifies; a call that ran and produced a rejected output stays paid."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                UPDATE pilot_runs
                   SET model_calls_used = model_calls_used - 1
                 WHERE id = $1 AND model_calls_used > 0
                RETURNING model_calls_used
                """,
                run_id,
            )

    async def finish_run(self, run_id: UUID, *, status: str, at: datetime) -> PilotRun | None:
        if status not in (RUN_FINISHED, RUN_STOPPED):
            raise ValueError(f"status końcowy runu spoza listy: {status!r}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE pilot_runs SET status = $2, stopped_at = $3
                 WHERE id = $1 AND status = '{RUN_ACTIVE}'
                RETURNING {_RUN_COLUMNS}
                """,
                run_id,
                status,
                at,
            )
        return _run(row) if row else None

    async def set_profile_version(self, run_id: UUID, profile_version_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pilot_runs SET profile_version_id = $2 WHERE id = $1",
                run_id,
                profile_version_id,
            )

    async def append_host_event(
        self, run_id: UUID, *, at: datetime, kind: str, detail: str
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pilot_runs
                   SET host_events = host_events || $2::jsonb
                 WHERE id = $1
                """,
                run_id,
                json.dumps([{"at": at.isoformat(), "kind": kind, "detail": detail}]),
            )

    async def profile_by_id(self, profile_version_id: UUID) -> dict[str, str] | None:
        """The four profile fields of the version the manifest names (not necessarily the latest:
        the pilot pins one version for the whole window)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT knows_deeply, positions, would_never_claim, voice_samples
                FROM user_profile_versions WHERE id = $1
                """,
                profile_version_id,
            )
        return dict(row) if row else None

    async def paused_community_ids(self, *, organization_id: UUID, now: datetime) -> set[UUID]:
        """Communities of one organization with a run ACTIVE at `now`: their judge / picture /
        voice jobs wait."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT community_id FROM pilot_runs
                WHERE organization_id = $1 AND status = '{RUN_ACTIVE}'
                  AND stopped_at IS NULL AND ends_at > $2
                """,
                organization_id,
                now,
            )
        return {r["community_id"] for r in rows}

    async def pending_cycle_jobs(self, run_id: UUID) -> int:
        """Queue rows of `pilot.cycle` for this run that may still run (not finished)."""
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    """
                    SELECT count(*) FROM jobs
                    WHERE job_type = 'pilot.cycle' AND payload_ref = $1
                      AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
                    """,
                    f"pilot/{run_id}",
                )
            )

    async def has_cycle(self, run_id: UUID, slot: int) -> bool:
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM pilot_cycles WHERE run_id = $1 AND slot = $2", run_id, slot
                )
            )

    # -- cycles -----------------------------------------------------------------------------

    async def start_cycle(
        self, run: PilotRun, *, slot: int, planned_at: datetime, started_at: datetime
    ) -> PilotCycle | None:
        """Open a cycle row; None when this slot already ran (a duplicate job)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO pilot_cycles
                    (organization_id, workspace_id, run_id, slot, planned_at, started_at, outcome,
                     model_calls_used_after)
                VALUES ($1, $2, $3, $4, $5, $6, 'STARTED', $7)
                ON CONFLICT (run_id, slot) DO NOTHING
                RETURNING id, run_id, slot, planned_at, started_at, finished_at, outcome, detail,
                          model_calls_used_after
                """,
                run.organization_id,
                run.workspace_id,
                run.id,
                slot,
                planned_at,
                started_at,
                run.model_calls_used,
            )
        return PilotCycle(**dict(row)) if row else None

    async def finish_cycle(
        self,
        cycle_id: UUID,
        *,
        outcome: str,
        detail: str | None,
        finished_at: datetime,
        model_calls_used_after: int,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pilot_cycles
                   SET outcome = $2, detail = $3, finished_at = $4, model_calls_used_after = $5
                 WHERE id = $1
                """,
                cycle_id,
                outcome,
                detail[:1000] if detail else None,
                finished_at,
                model_calls_used_after,
            )

    async def cycles(self, run_id: UUID) -> tuple[PilotCycle, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, run_id, slot, planned_at, started_at, finished_at, outcome, detail,
                       model_calls_used_after
                FROM pilot_cycles WHERE run_id = $1 ORDER BY slot
                """,
                run_id,
            )
        return tuple(PilotCycle(**dict(r)) for r in rows)

    async def last_cycle(self, run_id: UUID) -> PilotCycle | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, run_id, slot, planned_at, started_at, finished_at, outcome, detail,
                       model_calls_used_after
                FROM pilot_cycles WHERE run_id = $1 ORDER BY slot DESC LIMIT 1
                """,
                run_id,
            )
        return PilotCycle(**dict(row)) if row else None

    # -- reactions --------------------------------------------------------------------------

    async def save_reaction(
        self,
        run: PilotRun,
        *,
        thread_id: UUID,
        origin: str,
        target_message_external_id: str,
        decision: str,
        draft: str | None,
        contribution: str | None,
        brief_rationale: str,
        confidence: float,
        model: str,
        effort: str,
        profile_version_id: UUID,
        prompt_name: str,
        prompt_sha256: str,
        input_sha256: str,
        situation_sha256: str,
        read_at: datetime,
        context_complete: bool,
        comments_seen: int,
        wait_reason: str | None,
        next_check_at: datetime | None,
        move: str | None = None,
    ) -> PilotReaction | None:
        """Persist one reaction; None when the same situation version already has one (dedupe by
        `situation_sha256`, enforced by the UNIQUE index, not by a read-then-write)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO pilot_reactions
                    (organization_id, workspace_id, run_id, thread_id, origin,
                     target_message_external_id, decision, draft, contribution, brief_rationale,
                     confidence, model, effort, profile_version_id, prompt_name, prompt_sha256,
                     input_sha256, situation_sha256, read_at, context_complete, comments_seen,
                     wait_reason, next_check_at, move)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18, $19, $20, $21, $22, $23, $24)
                ON CONFLICT (run_id, situation_sha256) DO NOTHING
                RETURNING id
                """,
                run.organization_id,
                run.workspace_id,
                run.id,
                thread_id,
                origin,
                target_message_external_id,
                decision,
                draft,
                contribution,
                brief_rationale,
                confidence,
                model,
                effort,
                profile_version_id,
                prompt_name,
                prompt_sha256,
                input_sha256,
                situation_sha256,
                read_at,
                context_complete,
                comments_seen,
                wait_reason,
                next_check_at,
                move,
            )
            if row is None:
                return None
            full = await conn.fetchrow(
                f"""
                SELECT {_REACTION_COLUMNS}
                FROM pilot_reactions r JOIN observed_threads t ON t.id = r.thread_id
                WHERE r.id = $1
                """,
                row["id"],
            )
        return _reaction(full)

    async def situation_exists(self, run_id: UUID, situation_sha256: str) -> bool:
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM pilot_reactions WHERE run_id = $1 AND situation_sha256 = $2",
                    run_id,
                    situation_sha256,
                )
            )

    async def generations_for_thread(self, run_id: UUID, thread_id: UUID) -> int:
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM pilot_reactions WHERE run_id = $1 AND thread_id = $2",
                    run_id,
                    thread_id,
                )
            )

    async def get_reaction(self, reaction_id: UUID) -> PilotReaction | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_REACTION_COLUMNS}
                FROM pilot_reactions r JOIN observed_threads t ON t.id = r.thread_id
                WHERE r.id = $1
                """,
                reaction_id,
            )
        return _reaction(row) if row is not None else None

    async def reactions(self, run_id: UUID) -> tuple[PilotReaction, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_REACTION_COLUMNS}
                FROM pilot_reactions r JOIN observed_threads t ON t.id = r.thread_id
                WHERE r.run_id = $1 ORDER BY r.created_at
                """,
                run_id,
            )
        return tuple(_reaction(r) for r in rows)

    async def recent_drafts(self, run_id: UUID, *, limit: int = 5) -> tuple[str, ...]:
        """First sentences of the run's newest drafts, newest first: the generator's memory."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT draft FROM pilot_reactions
                WHERE run_id = $1 AND draft IS NOT NULL
                ORDER BY created_at DESC LIMIT $2
                """,
                run_id,
                limit,
            )
        return tuple(_first_sentence(r["draft"]) for r in rows)

    async def due_wait_thread_ids(self, run_id: UUID, *, now: datetime) -> tuple[UUID, ...]:
        """Threads whose LATEST reaction is a WAIT with a deadline that has passed."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (thread_id) thread_id, decision, next_check_at
                FROM pilot_reactions WHERE run_id = $1
                ORDER BY thread_id, created_at DESC
                """,
                run_id,
            )
        return tuple(
            r["thread_id"]
            for r in rows
            if r["decision"] == "WAIT"
            and r["next_check_at"] is not None
            and r["next_check_at"] <= now
        )

    async def candidate_thread_ids(
        self,
        run: PilotRun,
        *,
        since: datetime,
        min_comments: int,
        limit: int,
        fresh_since: datetime | None = None,
    ) -> tuple[UUID, ...]:
        """Threads of the run's community first seen since `since`, with at least `min_comments`
        comments (recorded ones, or the count the platform's listing reported in the latest
        metric snapshot: a freshly enabled community has no recorded comments until its first
        snapshot job an hour later, and the cycle fetches the thread with its comments anyway)
        and no reaction in this run, newest first. A thread the platform dates at or after
        `fresh_since` needs no comment at all (D-030): the newest posts come first in the order
        anyway, so a fresh post is looked at before an older thread with a discussion."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.id
                FROM observed_threads t
                WHERE t.organization_id = $1 AND t.community_id = $2
                  AND t.first_observed_at >= $3
                  AND (
                        ($7::timestamptz IS NOT NULL AND t.created_at_source >= $7::timestamptz)
                        OR GREATEST(
                            (SELECT count(*) FROM observed_comments c WHERE c.thread_id = t.id),
                            COALESCE((SELECT s.comment_count FROM thread_metric_snapshots s
                                       WHERE s.thread_id = t.id AND s.comment_count IS NOT NULL
                                       ORDER BY s.observed_at DESC LIMIT 1), 0)
                        ) >= $4
                  )
                  AND NOT EXISTS (
                        SELECT 1 FROM pilot_reactions r WHERE r.run_id = $5 AND r.thread_id = t.id
                  )
                ORDER BY t.created_at_source DESC
                LIMIT $6
                """,
                run.organization_id,
                run.community_id,
                since,
                min_comments,
                run.id,
                limit,
                fresh_since,
            )
        return tuple(r["id"] for r in rows)

    # -- the learning loop (D-031): what was asked, what was written, what it earned -----------

    async def move_outcomes(
        self, community_id: UUID, *, organization_id: UUID
    ) -> tuple[dict[str, Any], ...]:
        """What this account's PUBLISHED comments earned in the community, per move as written:
        how many, their points against the median top-level comment of their thread (latest
        read), and the share that got at least one reply. Handed to the generator as
        `community.move_outcomes`; the numbers are small and the prompt says so."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH pub AS (
                    SELECT r.move, q.id AS request_id, r.thread_id
                    FROM publication_requests q
                    JOIN pilot_reactions r ON r.id = q.reaction_id
                    JOIN observed_threads t ON t.id = r.thread_id
                    WHERE q.status = 'PUBLISHED' AND q.organization_id = $2
                      AND t.community_id = $1 AND r.move IS NOT NULL AND r.move <> 'NONE'
                ), latest AS (
                    SELECT DISTINCT ON (request_id) request_id, score, reply_count
                    FROM publication_observations
                    ORDER BY request_id, observed_at DESC
                ), thread_median AS (
                    SELECT thread_id,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY score) AS median_score
                    FROM observed_comments
                    WHERE depth = 0 AND score IS NOT NULL
                    GROUP BY thread_id
                )
                SELECT pub.move,
                       count(*) AS published,
                       count(l.score) AS read,
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY (l.score - coalesce(tm.median_score, 0))
                       ) FILTER (WHERE l.score IS NOT NULL) AS points_vs_thread_median,
                       avg(CASE WHEN l.reply_count > 0 THEN 1.0 ELSE 0.0 END)
                           FILTER (WHERE l.reply_count IS NOT NULL) AS replied_share
                FROM pub
                LEFT JOIN latest l ON l.request_id = pub.request_id
                LEFT JOIN thread_median tm ON tm.thread_id = pub.thread_id
                GROUP BY pub.move
                ORDER BY pub.move
                """,
                community_id,
                organization_id,
            )
        return tuple(
            {
                "move": str(r["move"]),
                "published": int(r["published"]),
                "points_vs_thread_median": (
                    None
                    if r["points_vs_thread_median"] is None
                    else round(float(r["points_vs_thread_median"]), 1)
                ),
                "replied_share": (
                    None if r["replied_share"] is None else round(float(r["replied_share"]), 2)
                ),
            }
            for r in rows
        )

    async def outcome_rows(self, *, organization_id: UUID) -> tuple[dict[str, Any], ...]:
        """One row per published comment for the research table (`pilot-outcomes`): when the post
        was written and when we answered, what was asked and what was written, the latest points
        and replies, and the thread's median and count of top-level comments for normalisation."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (request_id) request_id, observed_at, score, reply_count
                    FROM publication_observations
                    ORDER BY request_id, observed_at DESC
                ), thread_stats AS (
                    SELECT thread_id,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY score) AS median_score,
                           max(score) AS best_score,
                           count(*) AS top_level
                    FROM observed_comments
                    WHERE depth = 0 AND score IS NOT NULL
                    GROUP BY thread_id
                ), verdict AS (
                    SELECT DISTINCT ON (reaction_id) reaction_id, verdict
                    FROM draft_judgments ORDER BY reaction_id, created_at DESC
                )
                SELECT c.community_key, t.external_id AS thread_external_id, t.title,
                       t.created_at_source AS post_created_at, q.published_at, r.read_at,
                       r.decision, r.move, r.requested_move, r.confidence, r.comments_seen,
                       q.consent_source, v.verdict,
                       l.observed_at, l.score, l.reply_count,
                       ts.median_score AS thread_median, ts.best_score AS thread_best,
                       ts.top_level AS thread_top_level, r.draft
                FROM publication_requests q
                JOIN pilot_reactions r ON r.id = q.reaction_id
                JOIN observed_threads t ON t.id = r.thread_id
                JOIN communities c ON c.id = t.community_id
                LEFT JOIN latest l ON l.request_id = q.id
                LEFT JOIN thread_stats ts ON ts.thread_id = t.id
                LEFT JOIN verdict v ON v.reaction_id = r.id
                WHERE q.status = 'PUBLISHED' AND q.organization_id = $1
                ORDER BY q.published_at
                """,
                organization_id,
            )
        return tuple(dict(r) for r in rows)


def _first_sentence(text: str, limit: int = 160) -> str:
    stripped = " ".join(text.split())
    for mark in (". ", "? ", "! "):
        cut = stripped.find(mark)
        if 0 < cut < limit:
            return stripped[: cut + 1]
    return stripped[:limit]
