"""At most eight pages, the best first; a post we only quote; the picture of the account (v11 round 3, 13.10), against PostgreSQL.

THE FOUNDER (13.10), a second time: "3 of 47" is the feeling to kill. The card gets AT MOST EIGHT pages and the best first
(`opportunities/room.py::room_view`: freshness and growth from `ranking.py`, times how well the account fits), and says how many
more are waiting. Nothing is deleted by it. Held here:

  * THE CAP: twelve drafts, eight pages; the freshest first; the opportunities that stand on the hidden drafts are hidden with them;
    `ukryte` counts what is behind; a draft that was behind comes up as soon as there is room;
  * THE FIT: a draft the founder cannot send from ranks behind one he can, so the eight are the eight he can act on;
  * THE FILL: with fewer than eight drafts the best conversations nobody drafted for take the rest, answered ones last, and only
    the new ones that did not fit are counted as behind (an answered one is not "waiting");
  * A QUOTE: an own post on X that ends with the address of a post carries that post (`cytat`), read once at `draft.create` and kept as
    a CONTEXT row (born dismissed by `CONTEXT_DISMISSER`: no page, not in "Skipped", not counted anywhere); marking the same post as
    a conversation brings it back; a quote whose post was never read still names its handle;
  * `room.show` carries `ukryte`, and the picture of each X draft's account (`konto_awatar`), which is best effort and never a reason
    for the page not to come.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from control_plane.opportunities import pictures, target_context
from control_plane.opportunities.room import MAX_PAGES, room_view, skipped_view
from control_plane.opportunities.snapshot import TargetSnapshot
from control_plane.opportunities.store import (
    CONTEXT_DISMISSER,
    OpportunityStore,
    new_opportunity,
)
from tests.test_opportunities_db import _mark, _world, _x_args
from tests.test_target_context_db import _snapshot, _x_draft, _x_id, _x_url

pytestmark = pytest.mark.db

H = timedelta(hours=1)
QUOTED_ID = "2103587050346250999"
QUOTED_URL = f"https://x.com/indie_maker/status/{QUOTED_ID}"


async def _room(world, tenant, **kw):
    kw.setdefault("max_pages", MAX_PAGES)  # the card's: the dashboard's Pokój lists everything
    return await room_view(
        world.registry.pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
        **kw,
    )


async def _twelve(world) -> list[str]:
    """Twelve replies under posts of 20 minutes, 40 minutes, ... 4 hours old. Returns their revisions, freshest post first."""
    revisions = []
    for n in range(1, 13):
        revisions.append(
            await _x_draft(
                world, _x_url(timedelta(minutes=20 * n), f"u{n}"), text=f"Odpowiedź numer {n}."
            )
        )
    return revisions


@pytest.fixture(autouse=True)
def _no_reads(monkeypatch):
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "off")
    pictures.forget()


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


async def test_twelve_drafts_are_eight_pages_the_freshest_first_and_four_are_counted(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, platform="X")
    revisions = await _twelve(world)
    room = await _room(world, tenant)

    assert MAX_PAGES == 8
    assert [d["revision_id"] for d in room["szkice"]] == revisions[:8], (
        "the freshest posts are the best"
    )
    assert room["ukryte"] == 4
    assert room["okazje"] == [], "no opportunity was marked: a draft is a page by itself"
    # nothing was deleted: the four behind are still waiting, and come up when there is room
    smaller = await _room(world, tenant, max_pages=12)
    assert len(smaller["szkice"]) == 12 and smaller["ukryte"] == 0


async def test_the_opportunities_of_the_hidden_drafts_are_hidden_with_them(pool, tenant) -> None:
    world = await _world(pool, tenant, platform="X")
    marked = []
    for n in range(1, 11):
        status = _x_id(timedelta(minutes=20 * n))
        opp = await _mark(world, **_x_args(status=status, created_at=None))
        marked.append(opp["okazja"]["id"])
        await world.registry.call(
            "draft.create",
            {
                "kind": "REPLY",
                "target_url": opp["okazja"]["url"],
                "text": f"Odpowiedź numer {n}.",
                "account": world.login,
            },
        )
    room = await _room(world, tenant)
    assert len(room["szkice"]) == 8 and room["ukryte"] == 2
    assert {o["id"] for o in room["okazje"]} == set(marked[:8]), (
        "the opportunity stands with its draft"
    )
    assert {d["okazja_id"] for d in room["szkice"]} == set(marked[:8])


async def test_a_draft_the_founder_cannot_send_from_ranks_behind_one_he_can(pool, tenant) -> None:
    from tests.test_opportunities_db import _account

    world = await _world(pool, tenant, platform="X")
    _other_id, other_login = await _account(pool, tenant, world.project_id, "X")
    older = await _x_draft(
        world, _x_url(3 * H, "older"), text="Starsza odpowiedź z konta, które działa."
    )
    created = await world.registry.call(
        "draft.create",
        {
            "kind": "REPLY",
            "target_url": _x_url(timedelta(minutes=10), "fresher"),
            "text": "Świeższa odpowiedź z konta, które nie może teraz wysłać.",
            "account": other_login,
        },
    )
    fresher = created["propozycja"]["revision_id"]
    plain = await _room(world, tenant)
    assert [d["revision_id"] for d in plain["szkice"]] == [fresher, older], (
        "fresher first, when both accounts fit"
    )
    weighed = await _room(world, tenant, fit={f"x:{other_login}".lower(): 0.2})
    assert [d["revision_id"] for d in weighed["szkice"]] == [older, fresher], (
        "an account that cannot send now ranks behind one that can"
    )
    assert len(weighed["szkice"]) == 2 and weighed["ukryte"] == 0, (
        "behind is an order, not a removal"
    )


# ---------------------------------------------------------------------------
# The fill
# ---------------------------------------------------------------------------


async def test_with_few_drafts_the_best_new_conversations_fill_the_page_and_the_rest_is_counted(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, platform="X")
    drafted = await _x_draft(world, _x_url(timedelta(minutes=30)), text="Jedyna odpowiedź.")
    new_ids = []
    for n in range(1, 10):
        opp = await _mark(
            world, **_x_args(status=_x_id(timedelta(minutes=30 * n + 40)), created_at=None)
        )
        new_ids.append(opp["okazja"]["id"])
    room = await _room(world, tenant)

    assert [d["revision_id"] for d in room["szkice"]] == [drafted]
    assert [o["id"] for o in room["okazje"]] == new_ids[:7], (
        "one draft and the seven freshest new conversations: eight pages"
    )
    assert room["ukryte"] == 2, "the new ones that did not fit are counted as behind"


# ---------------------------------------------------------------------------
# A quote
# ---------------------------------------------------------------------------


async def _quote_draft(world, text: str = "Moje zdanie o tym. "):
    created = await world.registry.call(
        "draft.create",
        {"kind": "OWN_POST", "text": f"{text}{QUOTED_URL}", "account": world.login},
    )
    assert created["stan"] == "OCZEKUJE", created
    return created["propozycja"]["revision_id"]


async def _context(pool, tenant, world, **over):
    store = OpportunityStore(pool)
    snap = _snapshot(
        QUOTED_URL,
        3 * H,
        external_id=f"x:{QUOTED_ID}",
        handle="indie_maker",
        name="Indie Maker",
        **over,
    )
    opportunity = new_opportunity(
        platform="X",
        url=QUOTED_URL,
        external_id=snap.external_id,
        reason="auto",
        excerpt=snap.text,
        author=snap.handle,
        created_at=snap.created_at,
        score=snap.likes,
        num_comments=snap.replies,
        views=snap.views,
        reposts=snap.reposts,
        author_name=snap.name,
        avatar_url=snap.avatar_url,
        metrics_at=datetime.now(UTC),
        require_time=False,
        author_verified=True,
        media={
            "typ": "photo",
            "url": "https://pbs.twimg.com/media/q1.jpg",
            "szer": 1200,
            "wys": 675,
        },
    )
    await store.note_context(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world.project_id,
        opportunity=opportunity,
    )
    return store


async def test_a_quote_carries_the_post_it_quotes_and_that_post_is_no_page_and_no_skipped_row(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, platform="X")
    revision = await _quote_draft(world)
    bare = (await _room(world, tenant))["szkice"][0]
    assert bare["revision_id"] == revision and bare["rodzaj"] == "OWN_POST"
    assert bare["cytat"]["url"] == QUOTED_URL and bare["cytat"]["autor"] == "indie_maker"
    assert bare["cytat"]["tekst"] is None, (
        "a quote whose post was never read still names its handle"
    )

    await _context(pool, tenant, world)
    room = await _room(world, tenant)
    cytat = room["szkice"][0]["cytat"]
    assert cytat["nazwa"] == "Indie Maker" and cytat["zweryfikowany"] is True
    assert cytat["media"] == {
        "typ": "photo",
        "url": "https://pbs.twimg.com/media/q1.jpg",
        "szer": 1200,
        "wys": 675,
    }
    assert cytat["metryki"]["polubienia"] == 312 and cytat["tekst"].startswith("Anyone using")
    assert room["okazje"] == [], "a post that is only read for context is no conversation to answer"
    skipped = await skipped_view(
        pool, organization_id=tenant["organization"], project_id=world.project_id
    )
    assert skipped == {"pominiete": [], "pominiete_lacznie": 0}, "and it is not 'skipped' either"
    async with pool.acquire() as conn:
        by = await conn.fetchval(
            "SELECT dismissed_by FROM project_opportunities WHERE project_id = $1 AND external_id = $2",
            world.project_id,
            f"x:{QUOTED_ID}",
        )
    assert by == CONTEXT_DISMISSER


async def test_marking_the_quoted_post_as_a_conversation_brings_it_back_and_a_live_one_is_only_refreshed(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, platform="X")
    store = await _context(pool, tenant, world)
    marked = await _mark(world, **_x_args(status=QUOTED_ID, created_at=None, url=QUOTED_URL))
    assert marked["okazja"]["stan"] == "NOWA", "the context skip is lifted"
    assert (await _room(world, tenant))["okazje"][0]["id"] == marked["okazja"]["id"]
    # a post that already stands as a conversation is only refreshed: its state is exactly what it was
    again = await _context(pool, tenant, world)
    view = await again.for_target(
        organization_id=tenant["organization"],
        project_id=world.project_id,
        external_id=f"x:{QUOTED_ID}",
    )
    assert view.stan == "NOWA" and view.dismissed_by is None and view.author_verified is True
    assert store is not None


async def test_draft_create_reads_the_quoted_post_once_and_never_refuses_the_draft(
    pool, tenant, monkeypatch
) -> None:
    monkeypatch.setenv("WENEXT_TARGET_CONTEXT", "on")
    calls: list[str] = []

    async def fake(url, *, client=None, timeout=0):
        calls.append(url)
        return TargetSnapshot(
            platform="X",
            external_id=f"x:{QUOTED_ID}",
            url=QUOTED_URL,
            handle="indie_maker",
            name="Indie Maker",
            avatar_url="https://pbs.twimg.com/profile_images/3/q_normal.jpg",
            text="Unpopular opinion.",
            created_at=datetime.now(UTC) - 2 * H,
            likes=9,
            views=900,
            verified=True,
            media={
                "typ": "photo",
                "url": "https://pbs.twimg.com/media/q.jpg",
                "szer": 100,
                "wys": 50,
            },
            source="fxtwitter",
        )

    monkeypatch.setattr(target_context, "fetch_fxtwitter", fake)
    world = await _world(pool, tenant, platform="X")
    await _quote_draft(world)
    cytat = (await _room(world, tenant))["szkice"][0]["cytat"]
    assert (
        calls == [QUOTED_URL]
        and cytat["tekst"] == "Unpopular opinion."
        and cytat["zweryfikowany"] is True
    )

    async def boom(url, *, client=None, timeout=0):
        raise RuntimeError("the mirror fell over")

    monkeypatch.setattr(target_context, "fetch_fxtwitter", boom)
    second = await world.registry.call(
        "draft.create",
        {
            "kind": "OWN_POST",
            "text": "Inne zdanie. https://x.com/other/status/2103587050346250777",
            "account": world.login,
        },
    )
    assert second["stan"] == "OCZEKUJE", "a failing read never refuses a draft"


async def test_the_backfill_gives_a_waiting_quote_the_post_it_quotes_and_does_it_once(
    pool, tenant, monkeypatch
) -> None:
    world = await _world(pool, tenant, platform="X")
    await _quote_draft(world)  # made while the context step was off: the quote has only its handle
    calls: list[str] = []

    async def fake(url, *, client=None, timeout=0):
        calls.append(url)
        return TargetSnapshot(
            platform="X",
            external_id=f"x:{QUOTED_ID}",
            url=QUOTED_URL,
            handle="indie_maker",
            name="Indie Maker",
            avatar_url=None,
            text="Read by the backfill.",
            created_at=datetime.now(UTC) - 2 * H,
            likes=5,
            source="fxtwitter",
        )

    monkeypatch.setattr(target_context, "fetch_fxtwitter", fake)
    rows = await target_context.backfill_waiting_drafts(
        pool, organization_id=tenant["organization"], readers=target_context.Readers(), pause=0
    )
    assert [r.source for r in rows] == ["fxtwitter"] and calls == [QUOTED_URL]
    assert (await _room(world, tenant))["szkice"][0]["cytat"]["tekst"] == "Read by the backfill."
    again = await target_context.backfill_waiting_drafts(
        pool, organization_id=tenant["organization"], readers=target_context.Readers(), pause=0
    )
    assert again == [] and calls == [QUOTED_URL], "a quote that was read is not asked again"


async def test_a_reply_or_an_own_post_without_a_trailing_address_has_no_quote(pool, tenant) -> None:
    world = await _world(pool, tenant, platform="X")
    own = await world.registry.call(
        "draft.create",
        {
            "kind": "OWN_POST",
            "text": f"Zdanie z linkiem w środku {QUOTED_URL} i dalej.",
            "account": world.login,
        },
    )
    assert own["stan"] == "OCZEKUJE"
    reply = await _x_draft(world, _x_url(H), text=f"Odpowiedź z adresem na końcu {QUOTED_URL}")
    room = await _room(world, tenant)
    assert {d["revision_id"]: d["cytat"] for d in room["szkice"]}[reply] is None, (
        "only an own post can quote"
    )
    assert [d["cytat"] for d in room["szkice"] if d["rodzaj"] == "OWN_POST"] == [None]


# ---------------------------------------------------------------------------
# room.show
# ---------------------------------------------------------------------------


async def test_room_show_carries_what_is_behind_and_the_picture_of_the_account(
    pool, tenant, monkeypatch
) -> None:
    world = await _world(pool, tenant, platform="X")
    await _twelve(world)
    seen: list[tuple[str, str]] = []

    async def avatar(platform, handle, *, client=None):
        seen.append((platform, handle))
        return "https://pbs.twimg.com/profile_images/9/me_normal.jpg"

    from control_plane.tools import opportunity_tools

    monkeypatch.setattr(opportunity_tools, "account_avatar", avatar)
    shown = await world.registry.call("room.show", {})
    assert len(shown["szkice"]) == 8 and shown["ukryte"] == 4
    assert {d["konto_awatar"] for d in shown["szkice"]} == {
        "https://pbs.twimg.com/profile_images/9/me_normal.jpg"
    }
    assert seen == [("X", world.login)], "one read per account, not one per draft"

    async def broken(platform, handle, *, client=None):
        raise RuntimeError("down")

    monkeypatch.setattr(opportunity_tools, "account_avatar", broken)
    again = await world.registry.call("room.show", {})
    assert again["stan"] == "WYKONANE" and len(again["szkice"]) == 8, (
        "a failing picture never costs the page"
    )
    assert {d["konto_awatar"] for d in again["szkice"]} == {None}
