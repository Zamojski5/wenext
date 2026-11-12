"""The two tables of migration 0040 and the reads over them (13.10).

ONE WRITER OF ROWS, ONE SPELLING OF AN ITEM. `register` is the only insert into `learning_posts`,
and the key it conflicts on is `(organization, platform, our external id)`: the same item reported
by a publication request, by a hand-posted reply and by the founder's diary is one row, and each
later source only ADDS what the first one did not know (`apply_context`). Nothing here overwrites a
measured value with an unmeasured one.

A SNAPSHOT IS TAKEN ONCE PER HORIZON, DECIDED BY THE INDEX. `record_snapshot` inserts and does
nothing on a conflict: two sweeps in the same minute both see the horizon empty and both write,
and only the unique index (`learning_snapshots_one_per_horizon`) says which one counts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from . import model

PLATFORM_X = "X"
PLATFORM_REDDIT = "REDDIT"
KIND_REPLY = "REPLY"
KIND_OWN_POST = "OWN_POST"
ORIGIN_PUBLICATION = "PUBLICATION"
ORIGIN_MANUAL = "MANUAL"
ORIGIN_DIARY = "DIARY"

#: The only hosts an item's permalink may be on. A permalink on any other host (the test fixture
#: site, a local mirror) is not a publication on a platform and is not tracked.
_PLATFORM_HOSTS = r"^https://(www\.|old\.|mobile\.)?(x\.com|twitter\.com|reddit\.com)/"

#: An item the platform took down has no audience, and its zeros are not a result of the way it
#: was written: it stays registered (the removal itself is a fact) and is left out of every
#: comparison and every "best" list.
_NOT_REMOVED = """NOT EXISTS (
    SELECT 1 FROM publication_requests rq
     WHERE rq.id = p.request_id AND rq.public_visibility = 'REMOVED')"""

_X_STATUS = re.compile(
    r"^https://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})/status(?:es)?/(\d{1,25})"
)
_REDDIT_COMMENT = re.compile(
    r"^https://(?:www\.|old\.)?reddit\.com/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{1,12})"
    r"(?:/[^/?#]*/([a-z0-9]{1,12}))?"
)


def item_from_url(url: str) -> tuple[str, str] | None:
    """`(platform, external id)` of OUR item from its permalink, or `None` when it is not one.

    An X status address names the post itself (`x:<id>`). A Reddit address with a comment id
    names a comment (`t1_<id>`); one without names a post (`t3_<id>`).
    """
    found = _X_STATUS.match(url or "")
    if found:
        return PLATFORM_X, f"x:{found.group(2)}"
    found = _REDDIT_COMMENT.match(url or "")
    if found:
        return PLATFORM_REDDIT, f"t1_{found.group(3)}" if found.group(3) else f"t3_{found.group(2)}"
    return None


def handle_from_url(url: str | None) -> str | None:
    found = _X_STATUS.match(url or "")
    return found.group(1) if found else None


@dataclass(frozen=True)
class NewPost:
    organization_id: UUID
    workspace_id: UUID
    platform: str
    kind: str
    origin: str
    external_id: str
    permalink: str
    published_at: datetime
    project_id: UUID | None = None
    request_id: UUID | None = None
    manual_publication_id: UUID | None = None
    account: str | None = None
    body: str = ""
    target_external_id: str | None = None
    target_url: str | None = None
    target_author: str | None = None
    target_followers: int | None = None
    target_created_at: datetime | None = None
    target_age_minutes: int | None = None
    position_replies: int | None = None
    hook: str | None = None
    hook_source: str | None = None
    mentions_wenext: bool = False
    has_link: bool = False
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostRow:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID | None
    platform: str
    kind: str
    origin: str
    request_id: UUID | None
    manual_publication_id: UUID | None
    external_id: str
    permalink: str
    account: str | None
    body: str
    published_at: datetime
    target_external_id: str | None
    target_url: str | None
    target_author: str | None
    target_followers: int | None
    target_created_at: datetime | None
    target_age_minutes: int | None
    position_replies: int | None
    hook: str | None
    hook_source: str | None
    mentions_wenext: bool
    has_link: bool
    facts: dict[str, Any]


_POST_COLUMNS = """
    id, organization_id, workspace_id, project_id, platform, kind, origin, request_id,
    manual_publication_id, external_id, permalink, account, body, published_at,
    target_external_id, target_url, target_author, target_followers, target_created_at,
    target_age_minutes, position_replies, hook, hook_source, mentions_wenext, has_link, facts
"""


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _post(row: asyncpg.Record) -> PostRow:
    values = dict(row)
    values["facts"] = _json(values.get("facts"))
    return PostRow(**values)


@dataclass(frozen=True)
class ObservationRow:
    """One author-side read of a published request, as the publisher recorded it."""

    request_id: UUID
    observed_at: datetime
    score: int | None
    reply_count: int
    raw: dict[str, Any]


class LearningStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- discovery: what was published and is not tracked yet ----------------------------------

    async def untracked_requests(
        self, organization_id: UUID, *, limit: int = 100
    ) -> list[asyncpg.Record]:
        """Published requests with a platform permalink and no learning row, oldest first.

        The join is the one place that gathers what was known WHEN WE REPLIED: the opportunity the
        draft stood on (its author, its time, its reply count and WHEN that count was read), the
        newest collected X post of the target before our reply, and for Reddit the observed
        thread. A request without evidence (no permalink) is not there: nothing to read back.
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(
                f"""
                SELECT q.id AS request_id, q.organization_id, q.workspace_id, q.project_id,
                       q.kind, q.target_external_id, q.target_url, q.published_at,
                       e.external_id AS our_id, e.permalink, e.account,
                       r.payload AS payload,
                       opp.author AS opp_author, opp.created_at_source AS opp_created,
                       opp.num_comments AS opp_replies, opp.metrics_at AS opp_metrics_at,
                       xt.replies AS xt_replies, xt.collected_at AS xt_at,
                       xt.author AS xt_author,
                       th.author AS th_author, th.created_at_source AS th_created
                  FROM publication_requests q
                  JOIN execution_evidence e
                    ON e.organization_id = q.organization_id AND e.attempt_id = q.attempt_id
                  LEFT JOIN action_proposal_revisions r
                    ON r.organization_id = q.organization_id AND r.id = q.proposal_revision_id
                  LEFT JOIN project_opportunities opp
                    ON opp.organization_id = q.organization_id
                   AND opp.project_id = q.project_id
                   AND opp.external_id = q.target_external_id
                  LEFT JOIN LATERAL (
                       SELECT t.replies, t.collected_at, t.author
                         FROM x_trend_posts t
                        WHERE t.organization_id = q.organization_id
                          AND q.target_external_id LIKE 'x:%'
                          AND t.post_id = substr(q.target_external_id, 3)
                          AND t.collected_at <= q.published_at
                          AND t.replies IS NOT NULL
                        ORDER BY t.collected_at DESC
                        LIMIT 1
                  ) xt ON true
                  LEFT JOIN LATERAL (
                       SELECT h.author, h.created_at_source
                         FROM observed_threads h
                        WHERE h.organization_id = q.organization_id
                          AND q.target_external_id LIKE 't3\\_%'
                          AND h.external_id = substr(q.target_external_id, 4)
                        LIMIT 1
                  ) th ON true
                 WHERE q.organization_id = $1
                   AND q.status = 'PUBLISHED' AND q.published_at IS NOT NULL
                   AND e.permalink ~ '{_PLATFORM_HOSTS}'
                   AND NOT EXISTS (SELECT 1 FROM learning_posts lp WHERE lp.request_id = q.id)
                 ORDER BY q.published_at
                 LIMIT $2
                """,
                organization_id,
                limit,
            )

    async def untracked_manual(
        self, organization_id: UUID, *, limit: int = 100
    ) -> list[asyncpg.Record]:
        """Replies the founder posted by hand and said so WITH a link to their post.

        Without the link nothing can be read back, so the row is not tracked; with it, the item's
        id is in the link. The opportunity of the target supplies what was known at the time.
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(
                f"""
                SELECT m.id AS manual_id, m.organization_id, m.workspace_id, m.project_id,
                       m.platform, m.target_external_id, m.target_url, m.url, m.text, m.posted_at,
                       opp.author AS opp_author, opp.created_at_source AS opp_created,
                       opp.num_comments AS opp_replies, opp.metrics_at AS opp_metrics_at
                  FROM manual_publications m
                  LEFT JOIN project_opportunities opp
                    ON opp.organization_id = m.organization_id
                   AND opp.project_id = m.project_id
                   AND opp.external_id = m.target_external_id
                 WHERE m.organization_id = $1 AND m.url IS NOT NULL
                   AND m.url ~ '{_PLATFORM_HOSTS}'
                   AND NOT EXISTS (
                       SELECT 1 FROM learning_posts lp WHERE lp.manual_publication_id = m.id)
                 ORDER BY m.posted_at
                 LIMIT $2
                """,
                organization_id,
                limit,
            )

    # -- rows ----------------------------------------------------------------------------------

    async def register(self, new: NewPost) -> tuple[UUID, bool]:
        """Insert the item, or find the row that already stands for it. `(id, created)`."""
        async with self._pool.acquire() as connection:
            row_id = await connection.fetchval(
                """
                INSERT INTO learning_posts
                    (organization_id, workspace_id, project_id, platform, kind, origin,
                     request_id, manual_publication_id, external_id, permalink, account, body,
                     published_at, target_external_id, target_url, target_author,
                     target_followers, target_created_at, target_age_minutes, position_replies,
                     hook, hook_source, mentions_wenext, has_link, facts)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18, $19, $20, $21, $22, $23, $24, $25::jsonb)
                ON CONFLICT (organization_id, platform, external_id) DO NOTHING
                RETURNING id
                """,
                new.organization_id,
                new.workspace_id,
                new.project_id,
                new.platform,
                new.kind,
                new.origin,
                new.request_id,
                new.manual_publication_id,
                new.external_id,
                new.permalink,
                new.account,
                new.body[:4000],
                new.published_at,
                new.target_external_id,
                new.target_url,
                new.target_author,
                new.target_followers,
                new.target_created_at,
                new.target_age_minutes,
                new.position_replies,
                new.hook,
                new.hook_source,
                new.mentions_wenext,
                new.has_link,
                json.dumps(new.facts, ensure_ascii=False, default=str),
            )
            if row_id is not None:
                return row_id, True
            existing = await connection.fetchval(
                "SELECT id FROM learning_posts"
                " WHERE organization_id = $1 AND platform = $2 AND external_id = $3",
                new.organization_id,
                new.platform,
                new.external_id,
            )
        if existing is None:  # the row vanished between the two statements: say so
            raise RuntimeError(f"learning_posts: {new.external_id} zniknął w trakcie zapisu")
        return existing, False

    async def apply_context(
        self,
        post_id: UUID,
        *,
        hook: str | None = None,
        hook_source: str | None = None,
        target_followers: int | None = None,
        target_author: str | None = None,
        position_replies: int | None = None,
        project_id: UUID | None = None,
        facts: Mapping[str, Any] | None = None,
        overwrite_hook: bool = False,
    ) -> None:
        """Add what a later source knows. A value is only set where the row has none, EXCEPT the
        hook, which the founder's own label (`overwrite_hook`) may replace: a person's word about
        their own post outranks a rule of thumb."""
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE learning_posts SET
                    hook = CASE WHEN $2::text IS NOT NULL AND ($3 OR hook IS NULL)
                                THEN $2 ELSE hook END,
                    hook_source = CASE WHEN $2::text IS NOT NULL AND ($3 OR hook IS NULL)
                                       THEN $4 ELSE hook_source END,
                    target_followers = coalesce(target_followers, $5),
                    target_author = coalesce(target_author, $6),
                    position_replies = coalesce(position_replies, $7),
                    project_id = coalesce(project_id, $8),
                    facts = facts || $9::jsonb,
                    updated_at = now()
                WHERE id = $1
                """,
                post_id,
                hook,
                overwrite_hook,
                hook_source,
                target_followers,
                target_author,
                position_replies,
                project_id,
                json.dumps(dict(facts or {}), ensure_ascii=False, default=str),
            )

    async def note_attempt(self, post_id: UUID, key: str, value: Any) -> None:
        """Remember in `facts` that something was tried (a follower read that failed)."""
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE learning_posts SET facts = facts || $2::jsonb, updated_at = now()"
                " WHERE id = $1",
                post_id,
                json.dumps({key: value}, ensure_ascii=False, default=str),
            )

    async def posts(self, organization_id: UUID, *, since: datetime | None = None) -> list[PostRow]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"SELECT {_POST_COLUMNS} FROM learning_posts"
                " WHERE organization_id = $1 AND ($2::timestamptz IS NULL OR published_at >= $2)"
                " ORDER BY published_at",
                organization_id,
                since,
            )
        return [_post(row) for row in rows]

    async def post_by_external(
        self, organization_id: UUID, platform: str, external_id: str
    ) -> PostRow | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT {_POST_COLUMNS} FROM learning_posts"
                " WHERE organization_id = $1 AND platform = $2 AND external_id = $3",
                organization_id,
                platform,
                external_id,
            )
        return _post(row) if row is not None else None

    async def posts_by_target(
        self, organization_id: UUID, platform: str, target_external_id: str
    ) -> list[PostRow]:
        """Every tracked reply to one target (the founder may have answered a post twice)."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"SELECT {_POST_COLUMNS} FROM learning_posts"
                " WHERE organization_id = $1 AND platform = $2 AND kind = 'REPLY'"
                "   AND target_external_id = $3 ORDER BY published_at",
                organization_id,
                platform,
                target_external_id,
            )
        return [_post(row) for row in rows]

    async def posts_by_body_prefix(
        self, organization_id: UUID, platform: str, kind: str, prefix: str
    ) -> list[PostRow]:
        """Tracked items of a kind whose text starts with `prefix` (case and spaces ignored)."""
        wanted = " ".join(prefix.lower().split())
        if len(wanted) < 12:
            return []  # too short to say it is the same text
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"SELECT {_POST_COLUMNS} FROM learning_posts"
                " WHERE organization_id = $1 AND platform = $2 AND kind = $3"
                "   AND starts_with(lower(regexp_replace(btrim(body), '\\s+', ' ', 'g')), $4)"
                " ORDER BY published_at",
                organization_id,
                platform,
                kind,
                wanted,
            )
        return [_post(row) for row in rows]

    async def posts_needing_followers(
        self, organization_id: UUID, *, max_attempts: int, limit: int
    ) -> list[PostRow]:
        """X replies whose target author's follower count is still unknown, oldest first."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT {_POST_COLUMNS} FROM learning_posts
                 WHERE organization_id = $1 AND platform = 'X' AND kind = 'REPLY'
                   AND target_followers IS NULL AND target_external_id LIKE 'x:%'
                   AND coalesce((facts ->> 'followers_attempts')::int, 0) < $2
                 ORDER BY published_at
                 LIMIT $3
                """,
                organization_id,
                max_attempts,
                limit,
            )
        return [_post(row) for row in rows]

    # -- snapshots -----------------------------------------------------------------------------

    async def horizons_taken(self, post_ids: Sequence[UUID]) -> dict[UUID, set[int]]:
        if not post_ids:
            return {}
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT post_id, horizon_hours FROM learning_snapshots"
                " WHERE post_id = ANY($1::uuid[]) AND horizon_hours IS NOT NULL",
                list(post_ids),
            )
        taken: dict[UUID, set[int]] = {}
        for row in rows:
            taken.setdefault(row["post_id"], set()).add(row["horizon_hours"])
        return taken

    async def record_snapshot(
        self,
        post: PostRow,
        *,
        horizon_hours: int | None,
        measured_at: datetime,
        views: int | None,
        likes: int | None,
        replies: int | None,
        reposts: int | None,
        source: str,
        raw: Mapping[str, Any] | None = None,
    ) -> bool:
        """Write one snapshot. `False` when the horizon (or this very reading) already has one."""
        if all(value is None for value in (views, likes, replies, reposts)):
            return False
        async with self._pool.acquire() as connection:
            inserted = await connection.fetchval(
                """
                INSERT INTO learning_snapshots
                    (organization_id, post_id, horizon_hours, measured_at, age_minutes, views,
                     likes, replies, reposts, source, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                post.organization_id,
                post.id,
                horizon_hours,
                measured_at,
                model.age_minutes(post.published_at, measured_at),
                views,
                likes,
                replies,
                reposts,
                source,
                json.dumps(dict(raw or {}), ensure_ascii=False, default=str),
            )
        return inserted is not None

    async def observations_of(
        self, request_ids: Sequence[UUID]
    ) -> dict[UUID, list[ObservationRow]]:
        """The author-side reads the publisher took of these requests, oldest first."""
        if not request_ids:
            return {}
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT request_id, observed_at, score, reply_count, raw"
                "  FROM publication_observations"
                " WHERE request_id = ANY($1::uuid[]) AND viewer = 'AUTHOR'"
                " ORDER BY request_id, observed_at",
                list(request_ids),
            )
        grouped: dict[UUID, list[ObservationRow]] = {}
        for row in rows:
            grouped.setdefault(row["request_id"], []).append(
                ObservationRow(
                    request_id=row["request_id"],
                    observed_at=row["observed_at"],
                    score=row["score"],
                    reply_count=row["reply_count"],
                    raw=_json(row["raw"]),
                )
            )
        return grouped

    async def last_snapshot_at(self, organization_id: UUID) -> datetime | None:
        async with self._pool.acquire() as connection:
            return await connection.fetchval(
                "SELECT max(measured_at) FROM learning_snapshots WHERE organization_id = $1",
                organization_id,
            )

    # -- the data the model reads --------------------------------------------------------------

    async def items(self, organization_id: UUID) -> list[model.Item]:
        """Every tracked item at every horizon it has a snapshot for, as the model's `Item`."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT p.external_id, p.platform, p.kind, p.published_at, p.hook,
                       p.target_followers, p.target_age_minutes, p.position_replies,
                       p.mentions_wenext, p.has_link, p.body, p.permalink, p.project_id,
                       p.workspace_id,
                       s.horizon_hours, s.views, s.likes, s.replies, s.reposts
                  FROM learning_posts p
                  JOIN learning_snapshots s
                    ON s.organization_id = p.organization_id AND s.post_id = p.id
                 WHERE p.organization_id = $1 AND s.horizon_hours IS NOT NULL
                   AND {_NOT_REMOVED}
                 ORDER BY p.published_at DESC
                """,
                organization_id,
            )
        return [
            model.Item(
                key=row["external_id"],
                platform=row["platform"],
                kind=row["kind"],
                horizon=row["horizon_hours"],
                published_at=row["published_at"],
                hook=row["hook"],
                target_followers=row["target_followers"],
                target_age_minutes=row["target_age_minutes"],
                position_replies=row["position_replies"],
                mentions_wenext=row["mentions_wenext"],
                has_link=row["has_link"],
                views=row["views"],
                likes=row["likes"],
                replies=row["replies"],
                reposts=row["reposts"],
                body=row["body"],
                permalink=row["permalink"],
                project_id=row["project_id"],
                workspace_id=row["workspace_id"],
            )
            for row in rows
        ]

    async def coverage(
        self, organization_id: UUID, *, workspace_id: UUID | None = None
    ) -> model.Coverage:
        async with self._pool.acquire() as connection:
            tracked = await connection.fetchval(
                "SELECT count(*) FROM learning_posts WHERE organization_id = $1"
                " AND ($2::uuid IS NULL OR workspace_id = $2)",
                organization_id,
                workspace_id,
            )
            rows = await connection.fetch(
                "SELECT s.horizon_hours, count(DISTINCT s.post_id) AS posts"
                "  FROM learning_snapshots s JOIN learning_posts p ON p.id = s.post_id"
                " WHERE s.organization_id = $1 AND s.horizon_hours IS NOT NULL"
                "   AND ($2::uuid IS NULL OR p.workspace_id = $2)"
                " GROUP BY s.horizon_hours",
                organization_id,
                workspace_id,
            )
            last = await connection.fetchval(
                "SELECT max(s.measured_at) FROM learning_snapshots s"
                "  JOIN learning_posts p ON p.id = s.post_id"
                " WHERE s.organization_id = $1 AND ($2::uuid IS NULL OR p.workspace_id = $2)",
                organization_id,
                workspace_id,
            )
        return model.Coverage(
            tracked=int(tracked or 0),
            with_snapshot={row["horizon_hours"]: int(row["posts"]) for row in rows},
            last_snapshot_at=last,
        )

    async def top_items(
        self,
        organization_id: UUID,
        *,
        workspace_id: UUID,
        project_id: UUID | None,
        kind: str,
        platform: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """The best items of one kind with their text and numbers, newest horizon of each.

        Ranked by views, then likes, at each item's MOST MATURE snapshot, and the horizon of that
        snapshot is listed with it: a 6 h number and a 24 h number are not the same quantity, so
        the reader compares like with like. Items nobody got a number for are not listed: "best"
        with no number is a story. Removed items are not listed either.
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT p.external_id, p.platform, p.kind, p.body, p.permalink, p.published_at,
                       p.target_author, p.target_followers, p.target_age_minutes,
                       p.position_replies, p.hook, p.mentions_wenext, p.has_link,
                       s.horizon_hours, s.age_minutes, s.views, s.likes, s.replies, s.reposts
                  FROM learning_posts p
                  JOIN LATERAL (
                       SELECT x.* FROM learning_snapshots x
                        WHERE x.post_id = p.id AND x.horizon_hours IS NOT NULL
                        ORDER BY x.horizon_hours DESC LIMIT 1
                  ) s ON true
                 WHERE p.organization_id = $1 AND p.workspace_id = $2
                   AND ($3::uuid IS NULL OR p.project_id IS NULL OR p.project_id = $3)
                   AND p.kind = $4 AND ($5::text IS NULL OR p.platform = $5)
                   AND (s.views IS NOT NULL OR s.likes IS NOT NULL)
                   AND length(p.body) > 0 AND {_NOT_REMOVED}
                 ORDER BY coalesce(s.views, 0) DESC, coalesce(s.likes, 0) DESC,
                          p.published_at DESC
                 LIMIT $6
                """,
                organization_id,
                workspace_id,
                project_id,
                kind,
                platform,
                limit,
            )
        return [dict(row) for row in rows]

    # -- lessons (rows of `knowledge_items` of kind LESSON) -----------------------------------

    async def lessons_in_force(
        self,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID | None,
        *,
        rules: bool = False,
    ) -> dict[str, Any]:
        """The lessons that hold for ONE project (or the workspace, for `None`), by lesson key.

        MEASURED lessons by default: the sweep that rewrites and retracts lessons must never see
        a seeded rule (`payload.rule = true`), or the first sweep without a matching measurement
        would retract the founder's rule. `rules=True` returns only the rules.
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, statement, payload, project_id, created_at
                  FROM knowledge_items
                 WHERE organization_id = $1 AND workspace_id = $2 AND kind = 'LESSON'
                   AND project_id IS NOT DISTINCT FROM $3
                   AND (coalesce(payload -> 'rule', 'false'::jsonb) = 'true'::jsonb) = $4
                   AND superseded_by IS NULL AND retracted_at IS NULL
                 ORDER BY created_at DESC
                """,
                organization_id,
                workspace_id,
                project_id,
                rules,
            )
        found: dict[str, Any] = {}
        for row in rows:
            payload = _json(row["payload"])
            key = str(payload.get("lesson_key") or "")
            if key and key not in found:
                found[key] = _lesson(row, payload)
        return found

    async def supersede_lesson(self, old_id: UUID, *, by: UUID) -> bool:
        """Point an in-force lesson at the one that replaces it. The old row stays, readable."""
        async with self._pool.acquire() as connection:
            updated = await connection.fetchval(
                "UPDATE knowledge_items SET superseded_by = $2"
                " WHERE id = $1 AND kind = 'LESSON' AND superseded_by IS NULL"
                " AND retracted_at IS NULL RETURNING id",
                old_id,
                by,
            )
        return updated is not None

    async def reinstate_lesson(self, lesson_id: UUID, payload: Mapping[str, Any]) -> None:
        """Make an EARLIER lesson hold again, with the numbers that were just measured.

        The same sentence cannot be written twice while it exists (the unique index of 0027), and
        numbers that went back to a sentence which held before are exactly that case. Writing it
        again returns the old row, which is no longer in force; leaving it so would leave the
        project with no lesson at all. Its text is untouched, its payload takes the new
        measurement (ranges and evidence belong to the moment they were measured).
        """
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE knowledge_items SET superseded_by = NULL, payload = $2::jsonb"
                " WHERE id = $1 AND kind = 'LESSON' AND retracted_at IS NULL",
                lesson_id,
                json.dumps(dict(payload), ensure_ascii=False, default=str),
            )

    async def lesson_groups(self, organization_id: UUID) -> set[tuple[UUID, UUID | None]]:
        """The `(workspace, project)` pairs that hold at least one MEASURED lesson now."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT DISTINCT workspace_id, project_id FROM knowledge_items"
                " WHERE organization_id = $1 AND kind = 'LESSON'"
                "   AND coalesce(payload -> 'rule', 'false'::jsonb) <> 'true'::jsonb"
                "   AND superseded_by IS NULL AND retracted_at IS NULL",
                organization_id,
            )
        return {(row["workspace_id"], row["project_id"]) for row in rows}


@dataclass(frozen=True)
class LessonRow:
    id: UUID
    statement: str
    payload: dict[str, Any]
    project_id: UUID | None
    created_at: datetime


def _lesson(row: asyncpg.Record, payload: dict[str, Any]) -> LessonRow:
    return LessonRow(
        id=row["id"],
        statement=row["statement"],
        payload=payload,
        project_id=row["project_id"],
        created_at=row["created_at"],
    )


__all__ = [
    "KIND_OWN_POST",
    "KIND_REPLY",
    "ORIGIN_DIARY",
    "ORIGIN_MANUAL",
    "ORIGIN_PUBLICATION",
    "PLATFORM_REDDIT",
    "PLATFORM_X",
    "LearningStore",
    "LessonRow",
    "NewPost",
    "ObservationRow",
    "PostRow",
    "handle_from_url",
    "item_from_url",
]
