"""The hourly X collection (27.09): what it reads, when it reads nothing, and what the agent sees.

Measured against a real database and a DUMB X source that answers what it was told. Each test is
one sentence of `presence/x_trends.py`:

  * a live delegation and a ready X account: the feed and the agent's queries are read, and every
    post is stored with the counters X showed, our own posts left to the publisher;
  * no live delegation, or the browser logged into another account: nothing is read at all;
  * X refusing the client ends the hour instead of repeating the refusal query by query;
  * the view back ranks by engagement and by growth, and says why it is empty when it is;
  * the watch list has a limit and no duplicates; the presence tick asks once per hour slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from community_observer.x_browser import XPage, XPlatformRefused, XPost, XSessionState
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore
from control_plane.presence.x_trends import (
    JOB_X_COLLECT,
    MAX_WATCH_QUERIES,
    OUTCOME_COLLECTED,
    OUTCOME_SKIPPED,
    WATCH_ADDED,
    WATCH_EXISTS,
    WATCH_LIMIT,
    XTrendHandlers,
    XTrendStore,
    schedule_collect,
)

pytestmark = [pytest.mark.db]

ACCOUNT = "demo_founder"
QUERY = "AI agents min_faves:300 lang:en"


def now() -> datetime:
    return datetime.now(UTC)


def _post(post_id: str, author: str, **metrics: int) -> XPost:
    return XPost(
        id=post_id,
        author=author,
        author_name=author,
        text=f"post {post_id} od {author}",
        text_source="post",
        permalink=f"https://x.com/{author}/status/{post_id}",
        published_at="2026-09-27T10:00:00.000Z",
        relation=None,
        metrics=dict(metrics),
    )


def _page(*posts: XPost, capability: str = "home") -> XPage:
    return XPage(
        requested_url="https://x.com/home",
        final_url="https://x.com/home",
        capability=capability,
        anonymous=False,
        viewer=ACCOUNT,
        login_wall=False,
        logged_in=True,
        unavailable=None,
        posts=tuple(posts),
        profile=None,
        text="",
        limitations=(),
    )


@dataclass
class DumbXSource:
    identity: XSessionState
    home: Any = None
    searches: dict[str, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    closed: int = 0
    #: What a read of any page other than the feed answers (our profile); the feed when unset.
    profile_page: Any = None

    async def verify_identity(self, expected: str) -> XSessionState:
        return self.identity

    async def read(self, url: str, *, anonymous: bool = False) -> XPage:
        self.reads.append(url)
        answer = self.home
        if self.profile_page is not None and not url.endswith("/home"):
            answer = self.profile_page
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def search(self, query: str, *, mode: str = "live") -> XPage:
        self.reads.append(f"search:{query}:{mode}")
        answer = self.searches[query]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def aclose(self) -> None:
        self.closed += 1


async def _seed(pool, tenant, *, sign: bool = True) -> dict[str, Any]:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'marka osobista na X') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-x-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'X', $2, 'READY') RETURNING id",
            tenant["organization"],
            f"{ACCOUNT}{uuid4().hex[:4]}",
        )
        handle = await conn.fetchval(
            "SELECT external_account_id FROM social_accounts WHERE id = $1", account_id
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    delegation = None
    if sign:
        delegation = await PresenceStore(pool).sign(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            mode="AUTONOMOUS",
            scope=parse_scope(
                {"accounts": [str(account_id)], "communities": [], "actions": ["RESEARCH"]}
            ),
            budget=parse_budget({}),
            operator_id=tenant["operator"],
            expires_at=now() + timedelta(days=2),
        )
    return {"project": project_id, "handle": handle, "delegation": delegation}


def _handlers(pool, tenant, source: DumbXSource | None) -> tuple[XTrendHandlers, list[int]]:
    built: list[int] = []

    def factory():
        built.append(1)
        return source

    handlers = XTrendHandlers(
        pool=pool,
        store=XTrendStore(pool),
        presence=PresenceStore(pool),
        source_factory=factory,
        organization_id=tenant["organization"],
    )
    return handlers, built


async def _add_watch(pool, tenant, seeded, query: str = QUERY) -> str:
    return await XTrendStore(pool).add_watch(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        query=query,
        mode="top",
        session_id=None,
    )


async def test_the_feed_and_the_agents_queries_are_stored_with_their_counters(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _add_watch(pool, tenant, seeded)
    source = DumbXSource(
        XSessionState(True, seeded["handle"], now()),
        home=_page(
            _post("100", "levelsio", likes=900, replies=40, views=120_000),
            _post("101", seeded["handle"], likes=1),  # ours: measured by the publisher, not here
            _post("102", "dhh", reposts=12),  # X showed no likes: NULL, never 0
        ),
        searches={QUERY: _page(_post("200", "demo_famous", likes=5000), capability="search")},
    )
    handlers, _built = _handlers(pool, tenant, source)

    result = await handlers.collect_once(seeded["project"], now())

    assert result.outcome == OUTCOME_COLLECTED, result
    assert (result.reads, result.stored) == (3, 3)
    assert source.reads == [
        "https://x.com/home",
        f"search:{QUERY}:top",
        f"https://x.com/{seeded['handle']}",
    ], "profil konta czytany na końcu godziny, raz"
    assert result.followers is None, "strona bez profilu to brak odczytu, nie zero"
    assert source.closed == 1
    async with pool.acquire() as conn:
        rows = {
            row["post_id"]: row
            for row in await conn.fetch(
                "SELECT post_id, source, query, author, likes, replies, reposts, views, posted_at "
                "FROM x_trend_posts WHERE project_id = $1",
                seeded["project"],
            )
        }
    assert set(rows) == {"100", "102", "200"}
    assert rows["100"]["likes"] == 900 and rows["100"]["views"] == 120_000
    assert rows["102"]["likes"] is None and rows["102"]["reposts"] == 12
    assert rows["200"]["source"] == "search" and rows["200"]["query"] == QUERY
    assert rows["100"]["source"] == "home" and rows["100"]["query"] is None
    assert rows["100"]["posted_at"] is not None


def _profile(handle: str, followers: int | None) -> XPage:
    page = _page(capability="profile")
    profile: dict[str, Any] = {"handle": handle, "name": "Demo Founder", "bio": None}
    if followers is not None:
        profile["followers"] = followers
    return XPage(**{**page.__dict__, "profile": profile})


async def _followers_rows(pool, seeded) -> list[Any]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT s.value, s.metric, s.period, s.source, s.observed_at "
            "FROM audience_snapshots s JOIN social_accounts a "
            "ON a.organization_id = s.organization_id AND a.id = s.social_account_id "
            "WHERE a.external_account_id = $1 ORDER BY s.observed_at",
            seeded["handle"],
        )


async def test_our_profile_gives_one_follower_reading_an_hour(pool, tenant) -> None:
    """27.09, P6 etap A on X: the count from our own profile page is a row in
    `audience_snapshots` (FOLLOWERS, a point in time, read off the platform's page). Another
    account's profile or a profile without the number is no reading at all, never a zero."""
    seeded = await _seed(pool, tenant)
    moment = now()
    source = DumbXSource(
        XSessionState(True, seeded["handle"], moment),
        home=_page(_post("100", "levelsio", likes=900)),
        profile_page=_profile(seeded["handle"], 2),
    )
    handlers, _built = _handlers(pool, tenant, source)

    result = await handlers.collect_once(seeded["project"], moment)

    assert result.followers == 2 and "obserwujący: 2" in result.detail, result
    rows = await _followers_rows(pool, seeded)
    assert [(r["value"], r["metric"], r["period"], r["source"]) for r in rows] == [
        (2, "FOLLOWERS", "POINT_IN_TIME", "PLATFORM_PAGE")
    ]

    again = await handlers.collect_once(seeded["project"], moment)
    assert again.followers == 2
    assert len(await _followers_rows(pool, seeded)) == 1, "ten sam odczyt dwa razy to jeden wiersz"

    source.profile_page = _profile("somebody_else", 90_000)
    await handlers.collect_once(seeded["project"], moment + timedelta(hours=1))
    source.profile_page = _profile(seeded["handle"], None)
    await handlers.collect_once(seeded["project"], moment + timedelta(hours=2))
    assert len(await _followers_rows(pool, seeded)) == 1, "cudzy profil i brak liczby to nie odczyt"


async def test_the_change_over_a_day_is_known_only_after_a_day(pool, tenant) -> None:
    from control_plane.workspace.audience import latest_followers, record_followers

    seeded = await _seed(pool, tenant)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE external_account_id = $1", seeded["handle"]
        )
        start = now() - timedelta(hours=30)
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=2,
            observed_at=start,
        )
        early = await latest_followers(
            conn, organization_id=tenant["organization"], account_ids=[account_id]
        )
        assert early[account_id].day_ago is None, "po jednym odczycie zmiany doby nie znamy"
        for hours, value in ((25, 3), (1, 7)):
            await record_followers(
                conn,
                organization_id=tenant["organization"],
                social_account_id=account_id,
                followers=value,
                observed_at=now() - timedelta(hours=hours),
            )
        readings = await latest_followers(
            conn, organization_id=tenant["organization"], account_ids=[account_id, uuid4()]
        )
    assert set(readings) == {account_id}, "konto bez odczytu nie ma wpisu, a nie zero"
    view = readings[account_id].to_json()
    assert view["obserwujacy"] == 7 and view["doba_temu"] == 3 and view["zmiana_doby"] == 4
    assert view["pierwszy_odczyt"] == 2


async def test_without_a_live_delegation_nothing_is_read(pool, tenant) -> None:
    seeded = await _seed(pool, tenant, sign=False)
    source = DumbXSource(XSessionState(True, seeded["handle"], now()), home=_page())
    handlers, built = _handlers(pool, tenant, source)

    result = await handlers.collect_once(seeded["project"], now())

    assert result.outcome == OUTCOME_SKIPPED and "powierzenia" in result.detail
    assert built == [] and source.reads == []


async def test_another_account_in_the_browser_reads_nothing(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    source = DumbXSource(XSessionState(True, "somebody_else", now()), home=_page())
    handlers, _built = _handlers(pool, tenant, source)

    result = await handlers.collect_once(seeded["project"], now())

    assert result.outcome == OUTCOME_SKIPPED and seeded["handle"] in result.detail
    assert source.reads == [] and source.closed == 1


async def test_x_refusing_the_client_ends_the_hour(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _add_watch(pool, tenant, seeded)
    source = DumbXSource(
        XSessionState(True, seeded["handle"], now()),
        home=XPlatformRefused(403, "https://x.com/home"),
        searches={QUERY: _page(_post("200", "demo_famous", likes=5), capability="search")},
    )
    handlers, _built = _handlers(pool, tenant, source)

    result = await handlers.collect_once(seeded["project"], now())

    assert "403" in result.detail
    assert source.reads == ["https://x.com/home"], "po odmowie X nie czytamy dalej tej godziny"
    assert result.stored == 0


async def test_the_view_ranks_by_engagement_and_by_growth(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    store = XTrendStore(pool)
    earlier, later = now() - timedelta(hours=2), now() - timedelta(minutes=5)
    common = {
        "organization_id": tenant["organization"],
        "workspace_id": tenant["workspace"],
        "project_id": seeded["project"],
        "source": "home",
        "query": None,
    }
    await store.record(
        **common,
        collected_at=earlier,
        posts=[_post("1", "big", likes=5000), _post("2", "rising", likes=10, views=100)],
    )
    await store.record(
        **common,
        collected_at=later,
        posts=[_post("1", "big", likes=5010), _post("2", "rising", likes=400, views=9000)],
    )

    trends = await store.trends(
        organization_id=tenant["organization"],
        project_id=seeded["project"],
        since=now() - timedelta(hours=24),
    )

    top = trends["najwieksze_zaangazowanie"]
    rising = trends["najszybciej_rosnace"]
    assert [one["autor"] for one in top] == ["@big", "@rising"]
    assert rising[0]["autor"] == "@rising" and rising[0]["przyrost_polubien"] == 390
    assert rising[0]["przyrost_wyswietlen"] == 8900
    assert top[0]["gdzie"] == ["feed"]


async def test_the_watch_list_has_a_limit_and_no_duplicates(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    assert await _add_watch(pool, tenant, seeded, "one") == WATCH_ADDED
    assert await _add_watch(pool, tenant, seeded, " ONE ") == WATCH_EXISTS
    for index in range(MAX_WATCH_QUERIES - 1):
        assert await _add_watch(pool, tenant, seeded, f"q{index}") == WATCH_ADDED
    assert await _add_watch(pool, tenant, seeded, "one too many") == WATCH_LIMIT
    store = XTrendStore(pool)
    assert await store.remove_watch(
        organization_id=tenant["organization"], project_id=seeded["project"], query="One"
    )
    assert await _add_watch(pool, tenant, seeded, "one too many") == WATCH_ADDED


async def test_one_collection_per_project_per_hour_slot(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    moment = now().replace(minute=5)
    for offset in (0, 10, 20):
        await schedule_collect(
            queue,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            now=moment + timedelta(minutes=offset),
        )
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_X_COLLECT,
        )
    assert count == 1


async def test_the_agents_tools_manage_the_list_and_read_the_history(pool, tenant) -> None:
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    seeded = await _seed(pool, tenant)
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'ucz się, co się niesie') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            seeded["project"],
        )
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )

    empty = await registry.call("x.trends", {})
    assert empty["stan"] == "WYKONANE", empty
    assert "x.watch" in empty["powod_pustego_wyniku"]

    added = await registry.call("x.watch", {"add": QUERY})
    assert [one["zapytanie"] for one in added["obserwowane"]] == [QUERY]

    await XTrendStore(pool).record(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        collected_at=now() - timedelta(minutes=10),
        source="search",
        query=QUERY,
        posts=[_post("9", "demo_famous", likes=7000, replies=300)],
    )
    seen = await registry.call("x.trends", {"hours": 6})
    assert seen["powod_pustego_wyniku"] is None
    assert seen["najwieksze_zaangazowanie"][0]["autor"] == "@demo_famous"
    assert seen["najwieksze_zaangazowanie"][0]["gdzie"] == [f"szukaj: {QUERY}"]
    assert seen["pokrycie"]["odczyty"] == 1
    assert seen["konto"] is None, "bez odczytu profilu nie ma liczby obserwujących, nie ma zera"

    from control_plane.workspace.audience import record_followers

    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE external_account_id = $1", seeded["handle"]
        )
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=account_id,
            followers=5,
            observed_at=now() - timedelta(minutes=3),
        )
    counted = await registry.call("x.trends", {"hours": 6})
    assert counted["konto"]["login"] == seeded["handle"]
    assert counted["konto"]["obserwujacy"] == 5 and counted["konto"]["zmiana_doby"] is None


async def test_the_hours_collection_is_asked_for_before_the_agents_turn(pool, tenant) -> None:
    """27.09 on live: the tick awaits the model's whole turn, and the collection asked for after
    it did not run in its hour. It is now asked for first, so it waits in the queue meanwhile."""
    from control_plane.domain.models import ClaimedJob
    from control_plane.presence.handlers import PresenceHandlers

    seeded = await _seed(pool, tenant)
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    handlers = PresenceHandlers(
        pool=pool,
        presence=PresenceStore(pool),
        sessions=None,
        turns=None,
        approver=None,
        collector=None,
        queue=queue,
        organization_id=tenant["organization"],
    )
    seen_during_turn: list[int] = []

    async def turn(project_id, moment):
        async with pool.acquire() as conn:
            seen_during_turn.append(
                await conn.fetchval(
                    "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
                    tenant["organization"],
                    JOB_X_COLLECT,
                )
            )

        class Result:
            outcome, detail = "ANSWERED", "tura"

        return Result()

    handlers.run_once = turn  # type: ignore[method-assign]
    claim = ClaimedJob(
        job_id=uuid4(),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="presence.tick",
        routing_key="observe",
        priority=1,
        payload_ref=f"presence/{seeded['project']}",
        dispatch_version=1,
        attempt_limit=3,
        attempt_id=uuid4(),
        attempt_no=1,
        lease_generation=1,
        lease_owner=uuid4(),
        expires_at=now() + timedelta(minutes=5),
    )

    await handlers.tick_job(claim)

    assert seen_during_turn == [1], "zbiór tej godziny ma czekać w kolejce już w trakcie tury"
