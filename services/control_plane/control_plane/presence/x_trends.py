"""What is spreading on X: the agent's watch list, an hourly read without a model, the view.

WHY THIS EXISTS (27.09). The founder judged the first X post weak and asked for an agent that
learns how accounts with large engagement write, and does so around the clock. A model running
without pause would spend the delegation's daily turns in hours. So the work splits in two:

  * cheap collection, no model: once an hour, for a project with a live delegation and a ready X
    account, the capsule reads the account's feed and the queries the AGENT chose (`x.watch`), and
    every post is stored with the counters X showed;
  * the model reads the history (`x.trends`) in the turns it runs anyway, so it sees a day of
    what gained traction instead of one snapshot.

WHAT THIS NEVER DOES: write to X, or wake the agent. X's automation rules forbid automated replies
to posts found by search (help.x.com, April 2026), and a collected post is a measurement, not an
occasion. Replies to OUR posts wake the agent through `collector._x_replies`, a different path.

THE SCHEDULE rides on the presence tick, which already runs every ten minutes for every delegated
project: it asks for this project's collection in the current hour slot, and the slot's key makes
five asks one job. A delegation signed at 15:07 starts collecting within ten minutes, and a
restart joins the slot instead of doubling it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.backoff import ATTEMPT_LIMIT
from control_plane.workspace.audience import record_followers

_LOG = logging.getLogger(__name__)

JOB_X_COLLECT = "x.collect_trends"
COLLECT_INTERVAL_SECONDS = 3600
#: After the publisher (5) and before nothing in particular: a missed hour is the next hour.
COLLECT_PRIORITY = 8
#: Queries the agent may keep at once. Each is one page read an hour, so four queries and the feed
#: are five reads an hour, about what a person checking X hourly does.
MAX_WATCH_QUERIES = 4
#: How long a collected post is kept. Two weeks cover "what worked last week" with room to spare.
RETENTION = timedelta(days=14)
MAX_BODY = 4000

OUTCOME_COLLECTED = "COLLECTED"
OUTCOME_SKIPPED = "SKIPPED"

WATCH_ADDED = "dodane"
WATCH_EXISTS = "juz_obserwowane"
WATCH_LIMIT = "limit"


async def schedule_collect(
    port: Any, *, organization_id: UUID, workspace_id: UUID, project_id: UUID, now: datetime
) -> Any:
    """One collection per project per hour slot, idempotent within the slot."""
    slot = int(now.timestamp()) // COLLECT_INTERVAL_SECONDS
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_X_COLLECT,
        routing_key="observe",
        priority=COLLECT_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"x-collect:{project_id}:{slot}",
        payload_ref=f"x-collect/{project_id}",
        delay_seconds=0.0,
    )


def _project_of_payload(payload_ref: str | None) -> UUID | None:
    if not payload_ref or not payload_ref.startswith("x-collect/"):
        return None
    try:
        return UUID(payload_ref.split("/", 1)[1])
    except ValueError:
        return None


def _moment(value: str | None) -> datetime | None:
    """X's timestamp as the reader gave it, or `None` when it is absent or unreadable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _count(metrics: dict[str, int], key: str) -> int | None:
    value = metrics.get(key)
    return value if isinstance(value, int) and value >= 0 else None


def _followers_on(page: Any, handle: str) -> int | None:
    """The follower count of OUR profile on this page, or `None`.

    Only when the page is the profile of the account we asked for: a redirect or a suspended
    account shows somebody else's numbers or none, and neither is a reading of ours.
    """
    profile = getattr(page, "profile", None) or {}
    if str(profile.get("handle") or "").lower() != handle.lower():
        return None
    value = profile.get("followers")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


@dataclass(frozen=True)
class WatchQuery:
    id: UUID
    query: str
    mode: str
    created_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {"zapytanie": self.query, "tryb": self.mode, "od": self.created_at.isoformat()}


@dataclass(frozen=True)
class CollectResult:
    outcome: str
    detail: str
    reads: int = 0
    stored: int = 0
    #: The follower count read off the account's own profile this hour, `None` when not read.
    followers: int | None = None


class XTrendStore:
    """The two tables of 0035: what the agent watches, and what the collection saw."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def watches(self, *, organization_id: UUID, project_id: UUID) -> tuple[WatchQuery, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, query, mode, created_at FROM x_watch_queries "
                "WHERE organization_id = $1 AND project_id = $2 AND removed_at IS NULL "
                "ORDER BY created_at",
                organization_id,
                project_id,
            )
        return tuple(
            WatchQuery(row["id"], row["query"], row["mode"], row["created_at"]) for row in rows
        )

    async def add_watch(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        query: str,
        mode: str,
        session_id: UUID | None,
    ) -> str:
        """`dodane`, `juz_obserwowane` or `limit`. Serialised per project, so two sessions adding
        at once cannot both pass the limit."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"x-watch:{project_id}",
            )
            active = await conn.fetch(
                "SELECT lower(btrim(query)) AS key FROM x_watch_queries "
                "WHERE organization_id = $1 AND project_id = $2 AND removed_at IS NULL",
                organization_id,
                project_id,
            )
            if query.strip().lower() in {row["key"] for row in active}:
                return WATCH_EXISTS
            if len(active) >= MAX_WATCH_QUERIES:
                return WATCH_LIMIT
            await conn.execute(
                "INSERT INTO x_watch_queries (organization_id, workspace_id, project_id, query, "
                "mode, created_by_session) VALUES ($1, $2, $3, $4, $5, $6)",
                organization_id,
                workspace_id,
                project_id,
                query.strip(),
                mode,
                session_id,
            )
        return WATCH_ADDED

    async def remove_watch(self, *, organization_id: UUID, project_id: UUID, query: str) -> bool:
        async with self._pool.acquire() as conn:
            removed = await conn.fetchval(
                "UPDATE x_watch_queries SET removed_at = now() "
                "WHERE organization_id = $1 AND project_id = $2 AND removed_at IS NULL "
                "AND lower(btrim(query)) = lower(btrim($3)) RETURNING id",
                organization_id,
                project_id,
                query,
            )
        return removed is not None

    async def record(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        collected_at: datetime,
        source: str,
        query: str | None,
        posts: Sequence[Any],
        skip_author: str | None = None,
    ) -> int:
        """Store one read. Posts without an X id, and posts of `skip_author` (our own account,
        measured by the publisher), are left out. A counter X did not show stays NULL."""
        rows = []
        seen: set[str] = set()
        for post in posts:
            post_id = getattr(post, "id", None)
            if not post_id or not str(post_id).isdigit() or len(str(post_id)) > 25:
                continue
            if post_id in seen:
                continue
            author = getattr(post, "author", None)
            if skip_author and author and author.lower() == skip_author.lower():
                continue
            seen.add(post_id)
            metrics = dict(getattr(post, "metrics", {}) or {})
            rows.append(
                (
                    organization_id,
                    workspace_id,
                    project_id,
                    collected_at,
                    source,
                    query,
                    str(post_id),
                    author,
                    (getattr(post, "text", None) or "")[:MAX_BODY] or None,
                    _moment(getattr(post, "published_at", None)),
                    getattr(post, "permalink", None),
                    _count(metrics, "likes"),
                    _count(metrics, "replies"),
                    _count(metrics, "reposts"),
                    _count(metrics, "views"),
                )
            )
        if not rows:
            return 0
        async with self._pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO x_trend_posts (organization_id, workspace_id, project_id, "
                "collected_at, source, query, post_id, author, body, posted_at, permalink, likes, "
                "replies, reposts, views) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, "
                "$12, $13, $14, $15) ON CONFLICT DO NOTHING",
                rows,
            )
        return len(rows)

    async def prune(self, *, organization_id: UUID, older_than: datetime) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM x_trend_posts WHERE organization_id = $1 AND collected_at < $2",
                organization_id,
                older_than,
            )
        return int(status.split()[-1]) if status.startswith("DELETE") else 0

    async def coverage(
        self, *, organization_id: UUID, project_id: UUID, since: datetime
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(DISTINCT collected_at) AS reads, count(DISTINCT post_id) AS posts, "
                "max(collected_at) AS last FROM x_trend_posts "
                "WHERE organization_id = $1 AND project_id = $2 AND collected_at >= $3",
                organization_id,
                project_id,
                since,
            )
        return {
            "odczyty": int(row["reads"] or 0),
            "posty": int(row["posts"] or 0),
            "ostatni_odczyt": row["last"].isoformat() if row["last"] else None,
        }

    async def trends(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        since: datetime,
        source: str | None = None,
        limit: int = 15,
    ) -> dict[str, list[dict[str, Any]]]:
        """The window's posts, each once: its latest counters, how much they grew since it was
        first seen in the window, and where it was seen. Two orders of the same set: by
        engagement now, and by growth (only posts seen in more than one read can have any)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH w AS (
                    SELECT * FROM x_trend_posts
                     WHERE organization_id = $1 AND project_id = $2 AND collected_at >= $3
                       AND ($4::text IS NULL OR source = $4)
                ), latest AS (
                    SELECT DISTINCT ON (post_id) post_id, author, body, permalink, posted_at,
                           likes, replies, reposts, views, collected_at
                      FROM w ORDER BY post_id, collected_at DESC
                ), earliest AS (
                    SELECT DISTINCT ON (post_id) post_id, likes AS likes0, views AS views0,
                           collected_at AS first_seen
                      FROM w ORDER BY post_id, collected_at
                ), seen AS (
                    SELECT post_id,
                           array_agg(DISTINCT CASE WHEN source = 'home' THEN 'feed'
                                                   ELSE 'szukaj: ' || query END) AS seen_in
                      FROM w GROUP BY post_id
                )
                SELECT l.*, e.likes0, e.views0, e.first_seen, s.seen_in
                  FROM latest l JOIN earliest e USING (post_id) JOIN seen s USING (post_id)
                """,
                organization_id,
                project_id,
                since,
                source,
            )

        def row_json(row: asyncpg.Record) -> dict[str, Any]:
            grew_likes = (
                row["likes"] - row["likes0"]
                if row["likes"] is not None and row["likes0"] is not None
                else None
            )
            grew_views = (
                row["views"] - row["views0"]
                if row["views"] is not None and row["views0"] is not None
                else None
            )
            return {
                "autor": f"@{row['author']}" if row["author"] else None,
                "tekst": (row["body"] or "")[:600],
                "adres": row["permalink"],
                "opublikowany": row["posted_at"].isoformat() if row["posted_at"] else None,
                "polubienia": row["likes"],
                "odpowiedzi": row["replies"],
                "podania": row["reposts"],
                "wyswietlenia": row["views"],
                "przyrost_polubien": grew_likes,
                "przyrost_wyswietlen": grew_views,
                "pierwszy_raz": row["first_seen"].isoformat(),
                "ostatnio": row["collected_at"].isoformat(),
                "gdzie": list(row["seen_in"] or []),
            }

        def engagement(row: asyncpg.Record) -> tuple[int, int]:
            score = (row["likes"] or 0) + 3 * (row["replies"] or 0) + 2 * (row["reposts"] or 0)
            return score, row["views"] or 0

        def growth(row: asyncpg.Record) -> tuple[int, int]:
            likes = (row["likes"] or 0) - (row["likes0"] or 0) if row["likes0"] is not None else 0
            views = (row["views"] or 0) - (row["views0"] or 0) if row["views0"] is not None else 0
            return likes, views

        top = sorted(rows, key=engagement, reverse=True)[:limit]
        rising = [
            row
            for row in sorted(rows, key=growth, reverse=True)
            if row["first_seen"] != row["collected_at"] and growth(row) > (0, 0)
        ][:limit]
        return {
            "najwieksze_zaangazowanie": [row_json(row) for row in top],
            "najszybciej_rosnace": [row_json(row) for row in rising],
        }


class XTrendHandlers:
    """The `x.collect_trends` handler: one project's hourly read, no model."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        store: XTrendStore,
        presence: Any,
        source_factory: Callable[[], Any | None],
        organization_id: UUID,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._store = store
        self._presence = presence
        self._source_factory = source_factory
        self._organization_id = organization_id
        self._clock = clock

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {JOB_X_COLLECT: self.collect_job}

    async def collect_job(self, claim: ClaimedJob) -> None:
        project_id = _project_of_payload(claim.payload_ref)
        if project_id is None:
            _LOG.warning("x.collect_trends bez projektu w payload_ref=%r", claim.payload_ref)
            return
        result = await self.collect_once(project_id, self._clock())
        _LOG.info(
            "x.collect_trends %s: %s (%s; odczyty %d, posty %d)",
            project_id,
            result.outcome,
            result.detail[:200],
            result.reads,
            result.stored,
        )

    async def _x_account(self, project_id: UUID) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT x.workspace_id, a.id AS account_id, a.external_account_id AS handle,
                       a.state
                  FROM project_account_assignments x
                  JOIN social_accounts a
                    ON a.organization_id = x.organization_id AND a.id = x.social_account_id
                 WHERE x.organization_id = $1 AND x.project_id = $2
                   AND x.unassigned_at IS NULL AND a.platform = 'X'
                 ORDER BY x.assigned_at
                 LIMIT 1
                """,
                self._organization_id,
                project_id,
            )

    async def collect_once(self, project_id: UUID, now: datetime) -> CollectResult:
        from community_observer.x_browser import X_ORIGIN, XPlatformRefused

        delegation = await self._presence.live_delegation(
            organization_id=self._organization_id, project_id=project_id
        )
        if delegation is None:
            return CollectResult(OUTCOME_SKIPPED, "brak żywego powierzenia")
        account = await self._x_account(project_id)
        if account is None or account["state"] != "READY":
            return CollectResult(OUTCOME_SKIPPED, "projekt nie ma gotowego konta X")
        source = self._source_factory()
        if source is None:
            return CollectResult(OUTCOME_SKIPPED, "kapsuła X nie jest skonfigurowana")
        handle = str(account["handle"])
        workspace_id = account["workspace_id"]
        reads = stored = 0
        followers: int | None = None
        detail = "zebrano"
        try:
            identity = await source.verify_identity(handle)
            if not identity.logged_in or (identity.handle or "").lower() != handle.lower():
                return CollectResult(
                    OUTCOME_SKIPPED, f"przeglądarka X nie jest zalogowana jako @{handle}"
                )
            plan: list[tuple[str, str | None, Callable[[], Awaitable[Any]]]] = [
                ("home", None, lambda: source.read(f"{X_ORIGIN}/home"))
            ]
            for watch in await self._store.watches(
                organization_id=self._organization_id, project_id=project_id
            ):
                plan.append(
                    (
                        "search",
                        watch.query,
                        lambda q=watch.query, m=watch.mode: source.search(q, mode=m),
                    )
                )
            # 27.09: the account's OWN profile, last, once an hour. The follower count is the
            # number the founder asked for, and its change hour by hour is what tells a post that
            # brought people from one that only brought views (P6 etap A, `audience_snapshots`).
            plan.append(("profile", None, lambda: source.read(f"{X_ORIGIN}/{handle}")))
            for kind, query, read in plan:
                try:
                    page = await read()
                except XPlatformRefused as exc:
                    # X refusing the client is an answer about the whole session: stop reading
                    # for this hour instead of repeating the refused request query by query.
                    detail = f"X odmówił odczytu ({exc.status}); przerwano do następnej godziny"
                    break
                except Exception as exc:  # one failed read never ends the others
                    _LOG.info("x.collect_trends: odczyt %s nieudany: %s", kind, exc)
                    continue
                reads += 1
                if kind == "profile":
                    followers = _followers_on(page, handle)
                    if followers is not None:
                        async with self._pool.acquire() as conn:
                            await record_followers(
                                conn,
                                organization_id=self._organization_id,
                                social_account_id=account["account_id"],
                                followers=followers,
                                observed_at=now,
                            )
                    continue
                stored += await self._store.record(
                    organization_id=self._organization_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    collected_at=now,
                    source=kind,
                    query=query,
                    posts=getattr(page, "posts", ()),
                    skip_author=handle,
                )
        finally:
            await source.aclose()
        await self._store.prune(organization_id=self._organization_id, older_than=now - RETENTION)
        if followers is not None:
            detail = f"{detail}; obserwujący: {followers}"
        return CollectResult(
            OUTCOME_COLLECTED, detail, reads=reads, stored=stored, followers=followers
        )
