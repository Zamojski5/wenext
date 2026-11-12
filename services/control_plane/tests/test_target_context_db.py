"""Every reply draft shows the post it answers, stale drafts leave the card, the best is first (v11, DB).

THE FOUNDER (13.10), after a day of real use. Three things, each a way the card could mislead:

  * A REPLY MADE WITH `draft.create` WITHOUT AN OPPORTUNITY MARK showed "Reply on X" and nothing else.
    Now the post is read once and kept with its conversation (`target_context.py`); the card gets
    `cel`: author, avatar, text, age, counters, Trending. Checked here: the record of an X post and of a
    Reddit post, an opportunity the agent marked keeps its own reason and angle, a read that fails or
    raises never refuses or changes the draft, the backfill gives the drafts that wait NOW their post
    (and is idempotent), and what is written is what `room.show` carries;
  * A STALE DRAFT DISAPPEARS: a reply whose post is older than the limit is not in `szkice` (nor the
    opportunity that stands on it), is listed under "Skipped" with its reason the same moment, with NO
    WRITE (a read stays a read), and is expired for good by the publisher's tick through the state
    machine, the conversation recorded as skipped BY THE SYSTEM, once; a comment target is never judged;
  * THE BEST IS FIRST: fresh before old, trending before quiet, on the one list the card keeps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from control_plane.opportunities import target_context
from control_plane.opportunities.freshness import (
    DraftFreshness,
    expire_stale_drafts,
    find_stale,
)
from control_plane.opportunities.room import room_view, skipped_view
from control_plane.opportunities.snapshot import TargetSnapshot
from control_plane.opportunities.store import AUTO_REASON, OpportunityStore, new_opportunity
from control_plane.pilot import send_checks
from tests.test_card_send_db import _state
from tests.test_opportunities_db import (
    _draft,
    _mark,
    _reddit_args,
    _world,
    _x_args,
)

from tests import test_x_publication as xpub

pytestmark = pytest.mark.db

H = timedelta(hours=1)


def _x_id(age: timedelta) -> str:
    """The id of an X post written `age` ago (its snowflake, which is where the age comes from)."""
    now = datetime.now(UTC)
    return str((int((now - age).timestamp() * 1000) - send_checks._X_EPOCH_MS) << 22)


def _x_url(age: timedelta, handle: str = "jack") -> str:
    return f"https://x.com/{handle}/status/{_x_id(age)}"


def _snapshot(url: str, age: timedelta, **over) -> TargetSnapshot:
    status = url.rsplit("/", 1)[1]
    base = {
        "platform": "X",
        "external_id": f"x:{status}",
        "url": url,
        "handle": "jack",
        "name": "Jack Dorsey",
        "avatar_url": "https://pbs.twimg.com/profile_images/1/a_normal.jpg",
        "text": "Anyone using agents for distribution? Tried three tools, none stuck.",
        "created_at": datetime.now(UTC) - age,
        "likes": 312,
        "replies": 12,
        "reposts": 40,
        "views": 48_000,
        "source": "fxtwitter",
    }
    base.update(over)
    return TargetSnapshot(**base)


@pytest.fixture
def mirror(monkeypatch):
    """The mirror, scripted: `answers[url]` is a snapshot, None, or an exception to raise."""
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "on")
    answers: dict = {}
    calls: list[str] = []

    async def fake(url, *, client=None, timeout=0):
        calls.append(url)
        answer = answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(target_context, "fetch_fxtwitter", fake)
    return SimpleNamespace(answers=answers, calls=calls)


async def _x_draft(world, url: str, text: str = "Tools alone did not stick for me either."):
    result = await world.registry.call(
        "draft.create", {"kind": "REPLY", "target_url": url, "text": text, "account": world.login}
    )
    assert result["stan"] == "OCZEKUJE", result
    return result["propozycja"]["revision_id"]


async def _room(world, tenant):
    return await room_view(
        world.registry.pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
    )


async def _skipped(world, tenant):
    return await skipped_view(
        world.registry.pool, organization_id=tenant["organization"], project_id=world.project_id
    )


# ---------------------------------------------------------------------------
# The post a reply answers
# ---------------------------------------------------------------------------


async def test_a_reply_made_without_an_opportunity_mark_keeps_the_post_it_answers(
    pool, tenant, mirror
) -> None:
    world = await _world(pool, tenant, platform="X")
    url = _x_url(2 * H)
    mirror.answers[url] = _snapshot(url, 2 * H)

    revision = await _x_draft(world, url)

    room = await _room(world, tenant)
    (draft,) = room["szkice"]
    assert draft["revision_id"] == revision
    cel = draft["cel"]
    assert cel["autor"] == "jack" and cel["nazwa"] == "Jack Dorsey"
    assert cel["awatar"] == "https://pbs.twimg.com/profile_images/1/a_normal.jpg"
    assert cel["tekst"].startswith("Anyone using agents for distribution?")
    assert cel["metryki"] == {
        "wyswietlenia": 48_000,
        "polubienia": 312,
        "odpowiedzi": 12,
        "udostepnienia": 40,
    }
    assert cel["trending"] is True, "24k views an hour on a post of two hours"
    assert cel["pomiar"] is not None and cel["zniknal"] is False
    # the conversation is an opportunity now, with no reason of the agent's to show
    (opportunity,) = room["okazje"]
    assert opportunity["stan"] == "SZKIC" and opportunity["szkic"] == revision
    assert opportunity["dlaczego"] == "" and opportunity["cel"] == cel
    async with pool.acquire() as conn:
        reason = await conn.fetchval(
            "SELECT reason FROM project_opportunities WHERE project_id = $1", world.project_id
        )
    assert reason == AUTO_REASON


async def test_an_opportunity_the_agent_marked_keeps_its_own_words_and_gets_the_numbers(
    pool, tenant, mirror
) -> None:
    world = await _world(pool, tenant, platform="X")
    url = _x_url(H)
    marked = await _mark(
        world, **_x_args(status=url.rsplit("/", 1)[1], score=None, kat="Opowiedz o sobie.")
    )
    mirror.answers[url] = _snapshot(url, H, likes=900)

    await _x_draft(world, url)

    room = await _room(world, tenant)
    (opportunity,) = room["okazje"]
    assert opportunity["id"] == marked["okazja"]["id"], "the same conversation, one opportunity"
    assert opportunity["dlaczego"] == "Pyta o to samo, co rozwiązuje projekt."
    assert opportunity["kat"] == "Opowiedz o sobie."
    assert opportunity["cel"]["metryki"]["polubienia"] == 900
    assert opportunity["cel"]["nazwa"] == "Jack Dorsey"


async def test_a_read_that_fails_or_raises_never_refuses_or_changes_the_draft(
    pool, tenant, mirror
) -> None:
    world = await _world(pool, tenant, platform="X")
    nothing, boom = _x_url(H), _x_url(2 * H, "someone")
    mirror.answers[nothing] = None
    mirror.answers[boom] = RuntimeError("the mirror fell over")

    first = await _x_draft(world, nothing)
    second = await _x_draft(world, boom, text="Inna odpowiedź, inny post.")

    room = await _room(world, tenant)
    assert {d["revision_id"] for d in room["szkice"]} == {first, second}
    assert all(d["cel"] is None for d in room["szkice"]), "nothing was read, nothing is claimed"
    assert room["okazje"] == []
    assert await _state(pool, first) == "READY_FOR_APPROVAL"


async def test_a_fresh_reading_is_not_repeated_for_the_next_draft_under_the_same_post(
    pool, tenant, mirror
) -> None:
    world = await _world(pool, tenant, platform="X")
    url = _x_url(H)
    mirror.answers[url] = _snapshot(url, H)
    await _x_draft(world, url)
    await _x_draft(world, url, text="A second, different reply under the same post.")
    assert mirror.calls == [url], "one read for two drafts"


async def test_the_step_is_off_when_the_environment_says_so(pool, tenant, monkeypatch) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")

    async def never(*_a, **_k):
        raise AssertionError("the mirror was called")

    monkeypatch.setattr(target_context, "fetch_fxtwitter", never)
    world = await _world(pool, tenant, platform="X")
    await _x_draft(world, _x_url(H))
    assert (await _room(world, tenant))["okazje"] == []


async def test_a_reddit_reply_keeps_the_post_with_its_title_points_and_comments(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "on")
    thread = SimpleNamespace(
        post_id="zz9", subreddit="SaaS", title="How do you find your first users?",
        body="I built a tool and nobody knows it exists.", author="someone",
        url="https://www.reddit.com/r/SaaS/comments/zz9/how/", created_at=datetime.now(UTC) - 3 * H,
        score=120, num_comments=40,
    )  # fmt: skip

    async def read(self, url):
        return thread

    from control_plane.tools.opportunity_tools import OpportunityTools

    monkeypatch.setattr(OpportunityTools, "_reddit_thread_reader", read)
    world = await _world(pool, tenant)

    result = await _draft(world, "https://www.reddit.com/r/SaaS/comments/zz9/how/")
    assert result["stan"] == "OCZEKUJE", result

    room = await _room(world, tenant)
    (opportunity,) = room["okazje"]
    assert opportunity["spolecznosc"] == "r/SaaS" and opportunity["tytul"].startswith("How do you")
    assert (
        opportunity["cel"]["metryki"]["polubienia"] == 120
        and opportunity["cel"]["metryki"]["odpowiedzi"] == 40
    )
    assert opportunity["cel"]["trending"] is True, "40 points an hour"
    assert room["szkice"][0]["cel"] == opportunity["cel"]


async def test_a_post_the_browser_says_is_gone_is_recorded_and_the_draft_under_it_expires(
    pool, tenant, mirror
) -> None:
    world = await _world(pool, tenant, platform="X")
    url = _x_url(H)
    mirror.answers[url] = _snapshot(url, H)
    revision = await _x_draft(world, url)
    store = OpportunityStore(pool)
    marked = await store.for_target(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        external_id=f"x:{url.rsplit('/', 1)[1]}",
    )
    assert marked is not None and marked.gone_at is None

    assert await store.note_gone(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        external_id=marked.external_id,
    )
    assert not await store.note_gone(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        external_id=marked.external_id,
    ), "once"

    (stale,) = await find_stale(pool, organization_id=tenant["organization"])
    assert stale.code == "ZNIKNAL" and str(stale.revision_id) == revision
    room = await _room(world, tenant)
    assert room["szkice"] == [] and room["okazje"] == []
    expired = await expire_stale_drafts(pool, organization_id=tenant["organization"])
    assert [str(one.revision_id) for one in expired] == [revision]
    assert await _state(pool, revision) == "EXPIRED"


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------


async def test_the_backfill_gives_the_waiting_drafts_their_post_and_does_it_once(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")  # the drafts are made without the step
    world = await _world(pool, tenant, platform="X")
    a, b = _x_url(H), _x_url(2 * H, "someone")
    first, second = await _x_draft(world, a), await _x_draft(world, b, text="Inna odpowiedź.")
    assert (await _room(world, tenant))["okazje"] == []

    answers = {a: _snapshot(a, H), b: None}

    class Client:  # the mirror, through the real `fetch_fxtwitter`
        async def get(self, url, headers=None):
            handle, status = url.split("/")[-3], url.split("/")[-1]
            source = a if status in a else b
            snap = answers[source]
            if snap is None:
                return SimpleNamespace(status_code=404, content=b"", json=lambda: {})
            payload = {
                "code": 200,
                "tweet": {
                    "id": status, "text": snap.text, "likes": snap.likes, "views": snap.views,
                    "replies": snap.replies, "retweets": snap.reposts,
                    "created_timestamp": int(snap.created_at.timestamp()),
                    "author": {"screen_name": handle, "name": snap.name, "avatar_url": snap.avatar_url},
                },
            }  # fmt: skip
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: payload)

    readers = target_context.Readers(http=Client())
    rows = await target_context.backfill_waiting_drafts(
        pool, organization_id=tenant["organization"], readers=readers, pause=0
    )

    assert {str(r.revision_id): r.source for r in rows} == {first: "fxtwitter", second: None}
    room = await _room(world, tenant)
    (opportunity,) = room["okazje"]
    assert opportunity["cel"]["nazwa"] == "Jack Dorsey" and opportunity["szkic"] == first
    again = await target_context.backfill_waiting_drafts(
        pool, organization_id=tenant["organization"], readers=readers, pause=0
    )
    assert [str(r.revision_id) for r in again] == [second], "only what was not read is asked again"


# ---------------------------------------------------------------------------
# Stale drafts leave the card
# ---------------------------------------------------------------------------


async def test_a_stale_x_draft_leaves_the_card_with_no_write_and_its_reason_is_under_skipped(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    world = await _world(pool, tenant, platform="X")
    old = await _x_draft(world, _x_url(10 * H), text="Odpowiedź pod starym postem.")
    young = await _x_draft(world, _x_url(7 * H, "someone"), text="Odpowiedź pod młodym postem.")

    room = await _room(world, tenant)
    assert [d["revision_id"] for d in room["szkice"]] == [young], "page 1 has only what is fresh"
    skipped = await _skipped(world, tenant)
    assert skipped["pominiete_lacznie"] == 1
    (row,) = skipped["pominiete"]
    assert row["przez"] == "system" and row["powod_kod"] == "ZA_STARY" and row["platforma"] == "X"
    assert "post ma 10 h" in row["powod"] and "najwyżej 8 h (X)" in row["powod"]
    # a READ: nothing was written
    assert await _state(pool, old) == "READY_FOR_APPROVAL"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM project_opportunities WHERE project_id = $1", world.project_id
            )
            == 0
        )


async def test_the_publishers_tick_expires_it_for_good_and_records_the_skip_once(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    world = await _world(pool, tenant, platform="X")
    old = await _x_draft(world, _x_url(10 * H), text="Odpowiedź pod starym postem.")
    young = await _x_draft(world, _x_url(H, "someone"), text="Odpowiedź pod młodym postem.")
    handlers = xpub._publisher(pool, tenant, xpub.DumbXSource(xpub._logged_in()), xpub.DumbPort())
    handlers._freshness = DraftFreshness(pool, tenant["organization"])

    assert await handlers._anything_left() is True, "a waiting draft keeps the chain ticking"
    await handlers.reconcile_only(datetime.now(UTC))

    assert (
        await _state(pool, old) == "EXPIRED" and await _state(pool, young) == "READY_FOR_APPROVAL"
    )
    skipped = await _skipped(world, tenant)
    assert skipped["pominiete_lacznie"] == 1, (
        "the persisted skip stands in the virtual one, not beside it"
    )
    (row,) = skipped["pominiete"]
    assert (
        row["przez"] == "system" and row["powod_kod"] == "ZA_STARY" and row["pominieto"] is not None
    )
    async with pool.acquire() as conn:
        dismissed = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE project_id = $1", world.project_id
        )
        audit = await conn.fetchval(
            "SELECT count(*) FROM audit_events WHERE actor_ref = 'system:ZA_STARY'"
        )
    assert dismissed == "system:ZA_STARY" and audit >= 1, (
        "through the state machine, with an audit row"
    )
    # idempotent: a second tick finds nothing
    assert await expire_stale_drafts(pool, organization_id=tenant["organization"]) == []
    # and the chain stays alive only while something can still go cold
    assert await handlers._anything_left() is True
    await pool.execute(
        "UPDATE action_proposal_revisions SET state = 'REJECTED' WHERE id = $1", uuid.UUID(young)
    )
    assert await DraftFreshness(pool, tenant["organization"]).waiting() is False


async def test_a_stale_reddit_draft_and_the_opportunity_under_it_leave_the_card(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    world = await _world(pool, tenant)
    created = datetime.now(UTC)
    cold = await _mark(
        world, **_reddit_args(post="cold1", created_at=(created - 60 * H).isoformat())
    )
    warm = await _mark(
        world, **_reddit_args(post="warm1", created_at=(created - 10 * H).isoformat())
    )
    cold_draft = await _draft(world, cold["okazja"]["url"], text="Pod zimnym wątkiem.")
    warm_draft = await _draft(world, warm["okazja"]["url"], text="Pod ciepłym wątkiem.")
    assert cold_draft["stan"] == warm_draft["stan"] == "OCZEKUJE"

    room = await _room(world, tenant)
    assert [d["revision_id"] for d in room["szkice"]] == [warm_draft["propozycja"]["revision_id"]]
    assert [o["id"] for o in room["okazje"]] == [warm["okazja"]["id"]], "the cold one is gone too"
    skipped = await _skipped(world, tenant)
    assert [r["powod_kod"] for r in skipped["pominiete"]] == ["ZA_STARY"]
    assert "najwyżej 48 h (Reddit)" in skipped["pominiete"][0]["powod"]

    await expire_stale_drafts(pool, organization_id=tenant["organization"])
    assert await _state(pool, cold_draft["propozycja"]["revision_id"]) == "EXPIRED"
    # the agent's own opportunity is now skipped, with the system's reason in place of its "why"
    again = await _skipped(world, tenant)
    assert again["pominiete_lacznie"] == 1
    assert (
        again["pominiete"][0]["id"] == cold["okazja"]["id"]
        and again["pominiete"][0]["przez"] == "system"
    )


async def test_a_draft_whose_age_nobody_knows_and_one_that_answers_a_comment_stay(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    world = await _world(pool, tenant)
    no_age = await _draft(
        world, "https://www.reddit.com/r/SaaS/comments/mystery/x/", text="Bez znanego wieku."
    )
    comment = await _draft(
        world,
        "https://www.reddit.com/r/SaaS/comments/oldpost/x/c9/",
        text="Pod komentarzem.",
        target_external_id="t1_c9",
    )
    assert no_age["stan"] == comment["stan"] == "OCZEKUJE"
    assert await find_stale(pool, organization_id=tenant["organization"]) == []
    assert len((await _room(world, tenant))["szkice"]) == 2


# ---------------------------------------------------------------------------
# The best is first
# ---------------------------------------------------------------------------


async def test_the_best_opportunity_is_page_one_fresh_before_old_and_trending_before_quiet(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, platform="X")
    store = OpportunityStore(pool)
    now = datetime.now(UTC)

    async def put(name: str, age: timedelta, **counters):
        status = _x_id(age)
        await store.mark(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=world.project_id,
            session_id=None,
            opportunity=new_opportunity(
                platform="X", url=f"https://x.com/{name}/status/{status}", reason="r", excerpt=name,
                author=name, created_at=now - age, **counters,
            ),
        )  # fmt: skip

    await put("quiet_old", 7 * H, score=2)
    await put("quiet_fresh", H, score=2)
    await put("viral_fresh", 2 * H, score=400, views=90_000)
    await put("viral_old", 7 * H, score=400, views=90_000)
    await put("unknown_age", 0 * H)  # its read time is cleared below
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_opportunities SET created_at_source = NULL WHERE author = 'unknown_age'"
        )

    room = await _room(world, tenant)
    order = [o["autor"] for o in room["okazje"]]
    assert order[0] == "viral_fresh", order
    assert order.index("quiet_fresh") < order.index("quiet_old")
    assert order.index("viral_old") < order.index("quiet_old"), "faster first at the same age"
    # a post nobody timed is dated by its id (an X snowflake carries the time): it is the newest here
    assert order == ["viral_fresh", "unknown_age", "quiet_fresh", "viral_old", "quiet_old"], order
    trending = {o["autor"]: o["cel"]["trending"] for o in room["okazje"]}
    assert trending["viral_fresh"] is True and trending["quiet_fresh"] is False
    assert trending["viral_old"] is False, "fresh means under six hours"


async def test_the_waiting_drafts_follow_the_same_order(pool, tenant, monkeypatch) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    world = await _world(pool, tenant, platform="X")
    older = await _x_draft(world, _x_url(6 * H), text="pod starszym")
    newer = await _x_draft(world, _x_url(timedelta(minutes=30), "fresh"), text="pod nowszym")
    room = await _room(world, tenant)
    assert [d["revision_id"] for d in room["szkice"]] == [newer, older]
