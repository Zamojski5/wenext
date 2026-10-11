"""A hosted session against PostgreSQL: the first-users loop with nothing of the founder's behind it.

A hosted project is created by an admin command with ONE placeholder account per platform and no
capsule, no delegation and no dashboard. What is held here is the loop from the founder's side:

  * THE GATE: a hosted registry answers only `HOSTED_TOOLS`, in English, and the card's send tools
    and everything that touches an account are "not here";
  * SEARCH: `reddit.search` goes through the search API (mocked), says what it cannot know, spends one
    unit of the organization's daily allowance per query, and stops at the cap, per organization;
  * SAVE AND DRAFT: `opportunities.mark` takes a post whose time nobody read, `draft.create` needs no
    login, reads the target from the address and refuses what a hosted project cannot do, and no
    result carries a dashboard path or card;
  * THE CARD'S DATA: `room.show` says `wysylka: "reczna"` and gives each draft where to post it;
  * "I POSTED IT": `draft.mark_posted` records the founder's statement, once, only for this
    project, and the room then shows the draft gone, the person answered and the result unverified
    with no numbers;
  * THE LOCAL PRODUCT IS UNCHANGED: `room.show` says `karta` and its drafts are the page's own, and
    `draft.mark_posted` is not there.

`manual_publications` (the table behind `draft.mark_posted`) comes from a migration: until it is in
`db/migrations`, the tests of that table fail loudly instead of passing without it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from control_plane.observe import reddit_serp
from control_plane.observe.reddit_search import RedditSearchUnavailable
from control_plane.tools import ToolRegistry, issue
from control_plane.tools.catalog import HOSTED_TOOLS
from control_plane.tools.registry import NoSuchTool
from tests.test_opportunities_db import (
    World,
    _account,
    _draft,
    _listed,
    _mark,
    _reddit_args,
    _room,
    _world,
    _x_args,
)

pytestmark = pytest.mark.db

SERPER = {
    "organic": [
        {
            "title": "How do you handle invoices? : r/SaaS",
            "link": "https://www.reddit.com/r/SaaS/comments/1abc23d/how_do_you_handle_invoices/",
            "snippet": "We keep losing track of invoices and it is getting out of hand.",
            "date": "3 hours ago",
        },
        {
            "title": "No date here",
            "link": "https://www.reddit.com/r/startups/comments/1ghi67f/no_date_here/",
            "snippet": "A post the engine gave no date for.",
        },
    ]
}


async def _hosted(pool, tenant, *, x: bool = True, host: str = "codex") -> World:
    """A hosted project: a Reddit placeholder account, and an X one unless `x` is False."""
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, '') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"hosted-{uuid.uuid4().hex[:8]}",
        )
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    account_id, login = await _account(pool, tenant, project_id, "REDDIT")
    if x:
        await _account(pool, tenant, project_id, "X")
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            dsn="postgresql://atrapa/nieuzywane",
            host=host,
            hosted=True,
        ),
    )
    return World(project_id, account_id, login, session_id, registry, "REDDIT")


def _serper(monkeypatch, payload: dict[str, Any] | None = None, status: int = 200):
    """Serper behind a mock transport, and a key in the environment."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else SERPER)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        reddit_serp.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.delenv("WENEXT_SERP_DAILY_CAP", raising=False)
    return seen


async def _used(pool, organization_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM cost_ledger WHERE organization_id = $1 AND kind = 'FETCH' "
            "AND source_ref LIKE 'serp:%'",
            organization_id,
        )


async def _hosted_draft(world: World, url: str, text: str = "Moja odpowiedź.", **extra) -> dict:
    return await world.registry.call("draft.create", {"target_url": url, "text": text, **extra})


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["codex", "claude-code"])
async def test_a_hosted_session_lists_only_its_tools_and_refuses_the_rest_like_a_missing_name(
    pool, tenant, host
) -> None:
    world = await _hosted(pool, tenant, host=host)
    listed = await world.registry.call("catalog.list", {})
    assert {t["name"] for t in listed["narzedzia"]} == HOSTED_TOOLS
    assert listed["liczba"] == len(HOSTED_TOOLS)
    for name in (
        "accounts.list", "accounts.connect", "browser.inspect", "communities.observe",
        "community.read", "x.search", "x.read", "delegation.propose", "drafts.publish",
        "room.send_token", "room.send_draft", "room.send_status", "session.handoff",
        "sources.search", "person.voice", "materials.save",
    ):  # fmt: skip
        with pytest.raises(NoSuchTool) as caught:
            await world.registry.call(name, {})
        assert "nie jest dostępne" in str(caught.value), name
    with pytest.raises(NoSuchTool):
        await world.registry.call("not.a.tool", {})


async def test_catalog_describe_gives_the_hosted_wording_and_hides_what_is_not_there(
    pool, tenant
) -> None:
    world = await _hosted(pool, tenant)
    described = await world.registry.call("catalog.describe", {"tool": "draft.create"})
    assert described["stan"] == "WYKONANE"
    assert described["narzedzie"]["input_schema"]["required"] == ["text", "target_url"]
    assert described["narzedzie"]["summary"].startswith("Write a reply draft")
    hidden = await world.registry.call("catalog.describe", {"tool": "accounts.list"})
    missing = await world.registry.call("catalog.describe", {"tool": "no.such"})
    assert (
        hidden["stan"] == "BLAD" and hidden["powod"].split("'")[2] == missing["powod"].split("'")[2]
    )


async def test_a_local_plugin_session_does_not_get_the_hosted_only_tool(pool, tenant) -> None:
    world = await _world(pool, tenant)
    with pytest.raises(NoSuchTool):
        await world.registry.call("draft.mark_posted", {"revision_id": str(uuid.uuid4())})
    listed = await world.registry.call("catalog.list", {})
    assert "draft.mark_posted" not in {t["name"] for t in listed["narzedzia"]}


# ---------------------------------------------------------------------------
# reddit.search through the search API
# ---------------------------------------------------------------------------


async def test_hosted_search_says_what_the_engine_cannot_know_and_spends_one_unit(
    pool, tenant, monkeypatch
) -> None:
    seen = _serper(monkeypatch)
    world = await _hosted(pool, tenant)
    found = await world.registry.call(
        "reddit.search", {"query": "losing track of invoices", "time": "day"}
    )
    assert found["stan"] == "WYKONANE" and found["liczba"] == 2
    body = json.loads(seen[0].content)
    assert body["q"] == "site:reddit.com/r/ losing track of invoices" and body["tbs"] == "qdr:d"
    assert seen[0].headers["X-API-KEY"] == "test-key"
    assert found["zapytanie"] == {
        "query": "losing track of invoices", "subreddit": None, "sort": None, "time": "day",
        "limit": 10,
    }  # fmt: skip
    first, second = found["trafienia"]
    assert first["external_id"] == "t3_1abc23d" and first["community"] == "r/SaaS"
    assert first["title"] == "How do you handle invoices?"
    assert first["author"] is None and first["score"] is None and first["num_comments"] is None
    assert first["przyjmie_odpowiedz"] is None and first["zablokowany"] is None
    assert first["data_zrodla"] == "3 hours ago" and 2.9 < first["wiek_godzin"] < 3.2
    assert second["created_at"] is None and second["wiek_godzin"] is None
    assert first["okazja"] is None
    assert "search engine" in found["uwaga"] and "unknown, not zero" in found["uwaga"]
    assert await _used(pool, tenant["organization"]) == 1
    assert "pokoj" not in found


async def test_hosted_search_without_a_key_is_unavailable_and_costs_nothing(
    pool, tenant, monkeypatch
) -> None:
    seen = _serper(monkeypatch)
    monkeypatch.delenv("SERPER_API_KEY")
    world = await _hosted(pool, tenant)
    result = await world.registry.call("reddit.search", {"query": "invoices"})
    assert result["stan"] == "NIEDOSTEPNE" and result["rodzaj"] == "not_configured"
    assert "no API key" in result["powod"] and seen == []
    assert await _used(pool, tenant["organization"]) == 0


async def test_a_provider_failure_is_unavailable_with_its_kind(pool, tenant, monkeypatch) -> None:
    _serper(monkeypatch, {"message": "no"}, status=429)
    world = await _hosted(pool, tenant)
    result = await world.registry.call("reddit.search", {"query": "invoices"})
    assert result["stan"] == "NIEDOSTEPNE" and result["rodzaj"] == "rate_limited"


async def test_a_known_conversation_is_said_to_be_known(pool, tenant, monkeypatch) -> None:
    _serper(monkeypatch)
    world = await _hosted(pool, tenant)
    first = (await world.registry.call("reddit.search", {"query": "invoices"}))["trafienia"][0]
    await _mark(
        world,
        platform="REDDIT",
        url=first["url"],
        community=first["community"],
        title=first["title"],
        excerpt=first["excerpt"],
        dlaczego="Pyta o to, co robi projekt.",
    )
    again = (await world.registry.call("reddit.search", {"query": "invoices"}))["trafienia"][0]
    assert again["okazja"]["stan"] == "NOWA"


async def test_the_daily_allowance_is_per_organization_and_stops_the_query(
    pool, tenant, monkeypatch
) -> None:
    seen = _serper(monkeypatch)
    monkeypatch.setenv("WENEXT_SERP_DAILY_CAP", "2")
    mine = await _hosted(pool, tenant)
    async with pool.acquire() as conn:
        other_org = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('serp-b') RETURNING id"
        )
        other_ws = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            other_org,
        )
    theirs = await _hosted(pool, {**tenant, "organization": other_org, "workspace": other_ws})
    for _ in range(2):
        assert (await mine.registry.call("reddit.search", {"query": "invoices"}))[
            "stan"
        ] == "WYKONANE"
    spent = await mine.registry.call("reddit.search", {"query": "invoices"})
    assert spent["stan"] == "NIEDOSTEPNE" and spent["rodzaj"] == "rate_limited"
    assert "allowance" in spent["powod"] and "2 of 2" in spent["powod"]
    assert len(seen) == 2, "the third query never reached the provider"
    assert (await theirs.registry.call("reddit.search", {"query": "invoices"}))[
        "stan"
    ] == "WYKONANE"
    assert await _used(pool, tenant["organization"]) == 2
    assert await _used(pool, other_org) == 1


async def test_the_allowance_resets_with_the_utc_day_and_survives_parallel_calls(
    pool, tenant
) -> None:
    world = await _hosted(pool, tenant)
    org, project = tenant["organization"], world.project_id
    evening = datetime(2026, 10, 11, 23, 59, tzinfo=UTC)
    quota = reddit_serp.SerpQuota(pool, org, project, cap=1, now=evening)
    await quota.take()
    with pytest.raises(RedditSearchUnavailable) as caught:
        await reddit_serp.SerpQuota(pool, org, project, cap=1, now=evening).take()
    assert caught.value.kind == "rate_limited"
    morning = evening + timedelta(minutes=2)
    await reddit_serp.SerpQuota(pool, org, project, cap=1, now=morning).take()
    assert await reddit_serp.SerpQuota(pool, org, project, cap=1, now=morning).used_today() == 1

    # Five queries at once against three units left: exactly three get through.
    noon = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)

    async def one() -> bool:
        try:
            await reddit_serp.SerpQuota(pool, org, project, cap=3, now=noon).take()
        except RedditSearchUnavailable:
            return False
        return True

    assert sorted(await asyncio.gather(*(one() for _ in range(5)))) == [
        False,
        False,
        True,
        True,
        True,
    ]


# ---------------------------------------------------------------------------
# Save and draft
# ---------------------------------------------------------------------------


async def test_a_post_whose_time_nobody_read_is_saved_hosted_and_refused_locally(
    pool, tenant
) -> None:
    world = await _hosted(pool, tenant)
    args = _reddit_args()
    del args["created_at"]
    marked = await world.registry.call("opportunities.mark", args)
    assert marked["stan"] == "WYKONANE" and marked["okazja"]["utworzono"] is None
    assert "pokoj" not in marked, "no dashboard path for a founder who has no dashboard"

    local = await _world(pool, tenant)
    refused = await local.registry.call("opportunities.mark", args)
    assert refused["stan"] == "BLAD" and "created_at" in refused["powod"]


async def test_a_hosted_draft_needs_no_login_and_reads_the_target_from_the_address(
    pool, tenant
) -> None:
    world = await _hosted(pool, tenant)
    marked = await _mark(world, **_reddit_args(post="abc123"))
    url = marked["okazja"]["url"]
    draft = await _hosted_draft(world, url, "Odpowiedź bez konta.")
    assert draft["stan"] == "OCZEKUJE", draft
    assert draft["powod"].startswith("Draft saved. Nothing was posted")
    assert draft["propozycja"]["cel"] == "t3_abc123"
    assert draft["propozycja"]["konto"] != "" and draft["okazja"]["stan"] == "SZKIC"
    for hidden in ("karty", "pokoj", "pokoj_adres", "obecnosc", "obecnosc_adres"):
        assert hidden not in draft, hidden
    async with pool.acquire() as conn:
        threads = await conn.fetchval(
            "SELECT count(*) FROM observed_threads WHERE organization_id = $1",
            tenant["organization"],
        )
        communities = await conn.fetchval(
            "SELECT count(*) FROM communities WHERE organization_id = $1", tenant["organization"]
        )
    assert (threads, communities) == (0, 0), "nobody publishes here, so no thread is recorded"


async def test_a_hosted_draft_for_an_x_post_uses_the_x_placeholder(pool, tenant) -> None:
    world = await _hosted(pool, tenant)
    marked = await _mark(world, **_x_args())
    draft = await _hosted_draft(world, marked["okazja"]["url"], "Not for me either.")
    assert draft["stan"] == "OCZEKUJE", draft
    assert draft["propozycja"]["cel"] == f"x:{X_STATUS}"


async def test_what_a_hosted_project_cannot_do_is_refused_with_a_sentence(pool, tenant) -> None:
    world = await _hosted(pool, tenant, x=False)
    marked = await _mark(world, **_x_args())
    no_x = await _hosted_draft(world, marked["okazja"]["url"], "Reply.")
    assert no_x["stan"] == "NIEDOSTEPNE" and "no X placeholder account" in no_x["powod"]
    own = await world.registry.call(
        "draft.create", {"kind": "OWN_POST", "text": "x", "title": "t", "community": "r/SaaS"}
    )
    assert own["stan"] == "NIEDOSTEPNE" and "replies" in own["powod"]
    no_url = await world.registry.call("draft.create", {"text": "x"})
    assert no_url["stan"] == "BLAD" and "target_url" in no_url["powod"]
    clash = await world.registry.call(
        "draft.create",
        {
            "text": "x",
            "target_url": "https://www.reddit.com/r/SaaS/comments/abc123/x/",
            "target_external_id": "t3_zzz999",
        },
    )
    assert clash["stan"] == "BLAD"


async def test_a_hosted_draft_for_the_same_text_twice_is_not_two_drafts(pool, tenant) -> None:
    world = await _hosted(pool, tenant)
    marked = await _mark(world, **_reddit_args())
    first = await _hosted_draft(world, marked["okazja"]["url"], "Ta sama treść.")
    second = await _hosted_draft(world, marked["okazja"]["url"], "Ta sama treść.")
    assert first["stan"] == "OCZEKUJE" and second["stan"] == "OCZEKUJE"
    assert second["propozycja"]["revision_id"] == first["propozycja"]["revision_id"]
    assert first["powod"].startswith("Draft saved. Nothing was posted")
    assert "nie powstała druga propozycja" in second["powod"], (
        "a duplicate saved nothing and says so"
    )


async def test_a_listing_carries_no_dashboard_path_when_hosted_and_does_when_local(
    pool, tenant
) -> None:
    hosted = await _hosted(pool, tenant)
    await _mark(hosted, **_reddit_args(post="aaa111"))
    shown = await _listed(hosted)
    assert "pokoj" not in shown and shown["liczba"] == 1
    local = await _world(pool, tenant)
    await _mark(local, **_reddit_args(post="bbb222"))
    assert "pokoj" in await _listed(local)


async def test_project_update_sets_the_name_for_hosted_and_local_alike(pool, tenant) -> None:
    for world in (await _hosted(pool, tenant), await _world(pool, tenant)):
        done = await world.registry.call("project.update", {"name": "  my\n repo  "})
        assert done["stan"] == "WYKONANE" and done["nazwa"] == "my repo"
        assert (await world.registry.call("project.read", {}))["projekt"]["nazwa"] == "my repo"
        kept = await world.registry.call("project.update", {"goal": "Znaleźć użytkowników"})
        assert kept["nazwa"] == "my repo" and kept["cel"] == "Znaleźć użytkowników"
        blank = await world.registry.call("project.update", {"name": " \t "})
        assert blank["stan"] == "BLAD" and "nazwa" in blank["powod"]
        none = await world.registry.call("project.update", {})
        assert none["stan"] == "BLAD" and "`name`" in none["powod"]


# ---------------------------------------------------------------------------
# The card's data
# ---------------------------------------------------------------------------


async def test_room_show_says_manual_and_gives_each_draft_where_to_post_it(pool, tenant) -> None:
    world = await _hosted(pool, tenant)
    reddit = (await _mark(world, **_reddit_args(post="abc123")))["okazja"]["url"]
    await _hosted_draft(world, reddit, "Odpowiedź na Reddicie & więcej.")
    x_url = (await _mark(world, **_x_args()))["okazja"]["url"]
    await _hosted_draft(world, x_url, "Not for me & you.")
    shown = await world.registry.call("room.show", {})
    assert shown["stan"] == "WYKONANE" and shown["wysylka"] == "reczna"
    for hidden in ("pokoj", "pokoj_adres", "obecnosc", "obecnosc_adres"):
        assert hidden not in shown, hidden
    by_platform = {d["platforma"]: d for d in shown["szkice"]}
    assert set(by_platform) == {"REDDIT", "X"}
    assert all(d["konto"] is None for d in shown["szkice"]), "a placeholder login is not shown"
    assert (
        by_platform["REDDIT"]["watek_url"] == reddit and by_platform["REDDIT"]["intent_url"] is None
    )
    assert by_platform["X"]["watek_url"] == x_url
    assert by_platform["X"]["intent_url"] == (
        f"https://x.com/intent/post?text=Not%20for%20me%20%26%20you.&in_reply_to={X_STATUS}"
    )
    assert shown["okazje"] and shown["wyniki"]["opublikowane"] == 0


async def test_local_room_show_says_card_and_its_drafts_are_the_pages_own(
    pool, tenant, logged_in_client
) -> None:
    world = await _world(pool, tenant)
    opportunity = (await _mark(world, **_reddit_args(post="loc111")))["okazja"]
    await _draft(world, opportunity["url"], "Odpowiedź lokalna.")
    shown = await world.registry.call("room.show", {})
    page = (await logged_in_client.get(_room(tenant, world.project_id))).json()
    assert shown["wysylka"] == "karta"
    assert shown["szkice"] == page["szkice"], "no manual key leaks into a local draft"
    assert all("watek_url" not in d and "intent_url" not in d for d in shown["szkice"])


# ---------------------------------------------------------------------------
# "I posted it"
# ---------------------------------------------------------------------------

POSTED = "https://www.reddit.com/r/SaaS/comments/abc123/x/yy2zz/"
X_STATUS = _x_args()["url"].rsplit("/", 1)[1]  # the id the shared helper uses, whatever it is


async def _drafted_reddit(world: World, post: str = "abc123", text: str = "Odpowiedź do wysłania."):
    marked = await _mark(world, **_reddit_args(post=post))
    draft = await _hosted_draft(world, marked["okazja"]["url"], text)
    assert draft["stan"] == "OCZEKUJE", draft
    return marked["okazja"], draft["propozycja"]["revision_id"]


async def test_marking_a_draft_posted_moves_the_room_and_leaves_the_numbers_unmeasured(
    pool, tenant
) -> None:
    world = await _hosted(pool, tenant)
    opportunity, revision = await _drafted_reddit(world)
    before = await world.registry.call("room.show", {})
    assert [d["revision_id"] for d in before["szkice"]] == [revision]
    assert before["okazje"][0]["stan"] == "SZKIC"

    marked = await world.registry.call(
        "draft.mark_posted", {"revision_id": revision, "url": POSTED}
    )
    assert marked["stan"] == "WYKONANE", marked
    assert (
        marked["zapisano"]["revision_id"] == revision and marked["zapisano"]["nowy_zapis"] is True
    )
    assert (
        marked["zapisano"]["adres_posta"] == POSTED and marked["zapisano"]["platforma"] == "REDDIT"
    )
    assert (
        "niezweryfikowana" in marked["uwaga"]
        and "Nie pisz, że post jest widoczny" in marked["uwaga"]
    )
    assert "pokoj" not in marked

    after = await world.registry.call("room.show", {})
    assert after["szkice"] == [], "the draft no longer waits for anybody"
    assert (
        after["okazje"][0]["stan"] == "ODPOWIEDZIANO"
        and after["okazje"][0]["id"] == opportunity["id"]
    )
    results = after["wyniki"]
    assert results["opublikowane"] == 1
    assert (results["glosy"], results["odpowiedzi"], results["usuniete"]) == (None, None, None)
    (row,) = results["pozycje"]
    assert (
        row["url"] == POSTED
        and row["platforma"] == "REDDIT"
        and row["okazja_id"] == opportunity["id"]
    )
    assert row["widocznosc"] == "UNVERIFIED" and row["glosy"] is None and row["odpowiedzi"] is None
    assert row["tekst"] == "Odpowiedź do wysłania."
    listed = await _listed(world, stan="WSZYSTKIE")
    assert listed["okazje"][0]["stan"] == "ODPOWIEDZIANO"


async def test_marking_twice_is_one_record_and_a_later_link_fills_the_gap(pool, tenant) -> None:
    world = await _hosted(pool, tenant)
    _, revision = await _drafted_reddit(world)
    first = await world.registry.call("draft.mark_posted", {"revision_id": revision})
    assert first["zapisano"]["adres_posta"] is None and first["zapisano"]["nowy_zapis"] is True
    again = await world.registry.call("draft.mark_posted", {"revision_id": revision, "url": POSTED})
    assert again["zapisano"]["nowy_zapis"] is False and again["zapisano"]["adres_posta"] == POSTED
    wrong = await world.registry.call(
        "draft.mark_posted", {"revision_id": revision, "url": "https://x.com/me/status/1"}
    )
    assert wrong["zapisano"]["adres_posta"] == POSTED, "the first link stays"
    async with pool.acquire() as conn:
        rows = await conn.fetchval(
            "SELECT count(*) FROM manual_publications WHERE project_id = $1", world.project_id
        )
    assert rows == 1
    room = await world.registry.call("room.show", {})
    assert room["wyniki"]["opublikowane"] == 1 and room["wyniki"]["pozycje"][0]["url"] == POSTED


async def test_a_reply_on_x_marked_posted_is_an_x_result(pool, tenant) -> None:
    world = await _hosted(pool, tenant)
    marked = await _mark(world, **_x_args())
    draft = await _hosted_draft(world, marked["okazja"]["url"], "Not for me either.")
    mine = "https://x.com/me/status/2103587050347995999"
    done = await world.registry.call(
        "draft.mark_posted", {"revision_id": draft["propozycja"]["revision_id"], "url": mine}
    )
    assert done["zapisano"]["platforma"] == "X" and done["zapisano"]["cel"] == f"x:{X_STATUS}"
    room = await world.registry.call("room.show", {})
    assert room["wyniki"]["pozycje"][0]["platforma"] == "X"
    assert room["okazje"][0]["stan"] == "ODPOWIEDZIANO"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/https://www.reddit.com/x",
        "http://www.reddit.com/r/a/comments/b1/c/",
        "javascript:alert(1)",
        "https://reddit.com.evil.example/x",
        "https://www.reddit.com/" + "a" * 2000,
    ],
)
async def test_a_link_that_is_not_a_post_on_reddit_or_x_is_refused_and_nothing_is_written(
    pool, tenant, url
) -> None:
    world = await _hosted(pool, tenant)
    _, revision = await _drafted_reddit(world)
    refused = await world.registry.call("draft.mark_posted", {"revision_id": revision, "url": url})
    assert refused["stan"] == "BLAD" and ("https" in refused["powod"] or "2000" in refused["powod"])
    async with pool.acquire() as conn:
        rows = await conn.fetchval(
            "SELECT count(*) FROM manual_publications WHERE project_id = $1", world.project_id
        )
    assert rows == 0
    room = await world.registry.call("room.show", {})
    assert [d["revision_id"] for d in room["szkice"]] == [revision], "the draft still waits"


async def test_another_projects_draft_and_a_made_up_one_are_the_same_refusal(pool, tenant) -> None:
    mine = await _hosted(pool, tenant)
    theirs = await _hosted(pool, tenant)
    _, theirs_revision = await _drafted_reddit(theirs, post="oth999")
    foreign = await mine.registry.call("draft.mark_posted", {"revision_id": theirs_revision})
    made_up = await mine.registry.call("draft.mark_posted", {"revision_id": str(uuid.uuid4())})
    assert foreign["stan"] == made_up["stan"] == "BLAD"
    assert foreign["powod"].split(" ", 3)[:2] == made_up["powod"].split(" ", 3)[:2]
    for bad in ("", "not-a-uuid", "123"):
        refused = await mine.registry.call("draft.mark_posted", {"revision_id": bad})
        assert refused["stan"] == "BLAD" and "revision_id" in refused["powod"]
    room = await theirs.registry.call("room.show", {})
    assert len(room["szkice"]) == 1, "the other project's draft is untouched"
    assert room["wyniki"]["opublikowane"] == 0


async def test_a_marked_conversation_can_still_be_found_again_without_losing_its_state(
    pool, tenant, monkeypatch
) -> None:
    _serper(monkeypatch)
    world = await _hosted(pool, tenant)
    marked = await _mark(
        world,
        **_reddit_args(post="1abc23d", sub="SaaS", title="How do you handle invoices?"),
    )
    draft = await _hosted_draft(world, marked["okazja"]["url"], "Odpowiedź.")
    done = await world.registry.call(
        "draft.mark_posted", {"revision_id": draft["propozycja"]["revision_id"]}
    )
    assert done["stan"] == "WYKONANE", done
    found = await world.registry.call("reddit.search", {"query": "invoices"})
    assert found["trafienia"][0]["okazja"]["stan"] == "ODPOWIEDZIANO"
