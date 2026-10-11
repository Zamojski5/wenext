"""`HackerNewsSource` against recorded Algolia payloads, through `httpx.MockTransport`.

Every request the adapter makes goes through `_Algolia`, which records it — so the tests can
assert WHAT was asked (the tag, the `numericFilters` watermark) and not only what came back.
The negative tests at the end are the ones that matter most: a source that turns a 503 or a
broken body into an empty tuple would report a quiet day to the control plane.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from community_observer import (
    COMMUNITY_SOURCE_METHODS,
    HACKER_NEWS_COMMUNITIES,
    CommunitySource,
    HackerNewsSource,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)
from community_os_contracts import AccessMode, ObservationPlatform
from tests.conftest import FROZEN_NOW, frozen_clock


class _Algolia:
    """A scripted Algolia: path → (status, body). Records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object]] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes.get(request.url.path, (404, {"error": "not found"}))
        if isinstance(body, (bytes, str)):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    def source(self) -> HackerNewsSource:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return HackerNewsSource(client, base_url="https://algolia.test/api/v1", clock=frozen_clock)


@pytest.fixture
def algolia() -> _Algolia:
    return _Algolia()


def test_the_adapter_satisfies_the_port() -> None:
    source = HackerNewsSource(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert isinstance(source, CommunitySource)
    assert len(COMMUNITY_SOURCE_METHODS) == 3
    for method in COMMUNITY_SOURCE_METHODS:
        assert callable(getattr(source, method))
    assert source.platform is ObservationPlatform.HACKER_NEWS
    assert source.access_mode is AccessMode.PUBLIC_API_NO_ACCOUNT


def test_describe_names_the_four_surfaces_and_refuses_others(algolia: _Algolia) -> None:
    source = algolia.source()
    assert set(HACKER_NEWS_COMMUNITIES) == {"ask", "show", "new", "front"}
    ref = source.describe("ask")
    assert ref.ref == "HACKER_NEWS:ask"
    assert ref.access_mode is AccessMode.PUBLIC_API_NO_ACCOUNT
    with pytest.raises(UnknownCommunity, match="r/devops"):
        source.describe("r/devops")


async def test_listing_parses_the_recorded_ask_hn_page(
    algolia: _Algolia, hn_search_ask: dict
) -> None:
    algolia.routes["/api/v1/search_by_date"] = (200, hn_search_ask)
    threads = await algolia.source().list_recent_threads("ask", since=None, limit=5)

    assert len(threads) == 5, "pięć hitów w nagraniu → pięć wątków, ani jednego zgubionego"
    first = threads[0]
    assert first.external_id == "49548600"
    assert first.url == "https://news.ycombinator.com/item?id=49548600"
    assert first.title.startswith("Ask HN: Who is using MCP")
    # Read from the recording, not remembered: the first version of this line guessed the
    # author and was wrong — the `KONWENCJE.md` rule about reading the definition, applied to data.
    assert first.author == hn_search_ask["hits"][0]["author"]
    assert first.observed_at == FROZEN_NOW
    assert first.created_at.tzinfo is not None
    assert first.metrics.score.require() == 1
    assert first.metrics.comment_count.require() == 0
    assert first.ref == "HACKER_NEWS:ask:49548600"
    # Text posts carry the body as plain text, link posts do not.
    with_body = [t for t in threads if t.body]
    assert with_body, "Ask HN ma treść — story_text musi zostać przepisany na tekst"
    assert "<p>" not in (with_body[0].body or "")

    request = algolia.requests[0]
    assert request.url.params["tags"] == "ask_hn"
    assert request.url.params["hitsPerPage"] == "5"
    assert "numericFilters" not in request.url.params


async def test_listing_passes_the_watermark_as_a_numeric_filter(
    algolia: _Algolia, hn_search_ask: dict
) -> None:
    algolia.routes["/api/v1/search_by_date"] = (200, hn_search_ask)
    since = datetime(2026, 9, 3, 11, 0, tzinfo=UTC)
    await algolia.source().list_recent_threads("ask", since=since, limit=5)
    assert (
        algolia.requests[0].url.params["numericFilters"] == f"created_at_i>{int(since.timestamp())}"
    )


async def test_listing_refuses_a_naive_watermark(algolia: _Algolia) -> None:
    with pytest.raises(ValueError, match="strefę"):
        await algolia.source().list_recent_threads("ask", since=datetime(2026, 9, 3), limit=5)


async def test_a_null_points_field_is_unknown_not_zero(
    algolia: _Algolia, hn_search_ask: dict
) -> None:
    mutated = copy.deepcopy(hn_search_ask)
    mutated["hits"][0]["points"] = None
    mutated["hits"][1]["num_comments"] = None
    algolia.routes["/api/v1/search_by_date"] = (200, mutated)
    threads = await algolia.source().list_recent_threads("ask", since=None, limit=5)
    assert not threads[0].metrics.score.is_known
    assert threads[0].metrics.score.unknown_reason is not None
    assert not threads[1].metrics.comment_count.is_known
    assert threads[2].metrics.score.is_known


async def test_the_digest_ignores_search_highlighting_and_key_order(
    algolia: _Algolia, hn_search_ask: dict
) -> None:
    algolia.routes["/api/v1/search_by_date"] = (200, hn_search_ask)
    first = (await algolia.source().list_recent_threads("ask", since=None, limit=5))[0]

    reordered = copy.deepcopy(hn_search_ask)
    hit = reordered["hits"][0]
    hit["_highlightResult"] = {"title": {"value": "DIFFERENT"}}
    reordered["hits"][0] = dict(reversed(list(hit.items())))
    algolia.routes["/api/v1/search_by_date"] = (200, reordered)
    again = (await algolia.source().list_recent_threads("ask", since=None, limit=5))[0]
    assert again.raw_sha256 == first.raw_sha256

    changed = copy.deepcopy(hn_search_ask)
    changed["hits"][0]["points"] = 999
    algolia.routes["/api/v1/search_by_date"] = (200, changed)
    other = (await algolia.source().list_recent_threads("ask", since=None, limit=5))[0]
    assert other.raw_sha256 != first.raw_sha256


async def test_fetch_thread_flattens_the_recorded_comment_tree(
    algolia: _Algolia, hn_item_with_comments: dict
) -> None:
    algolia.routes["/api/v1/items/4102013"] = (200, hn_item_with_comments)
    snapshot = await algolia.source().fetch_thread("ask", "4102013")

    assert snapshot.thread.external_id == "4102013"
    assert snapshot.thread.title.startswith("Moderators of HN")
    assert snapshot.thread.metrics.score.require() == 572
    assert not snapshot.comments_truncated

    # Counted independently of the adapter: walk the fixture by hand.
    def count(node: dict) -> int:
        return sum(1 + count(child) for child in node.get("children", []))

    expected_total = count(hn_item_with_comments)
    assert expected_total == 44
    assert len(snapshot.comments) == expected_total
    assert snapshot.thread.metrics.comment_count.require() == expected_total

    top_level = [c for c in snapshot.comments if c.depth == 0]
    assert len(top_level) == len(hn_item_with_comments["children"]) == 28
    assert all(c.parent_external_id is None for c in top_level)
    assert max(c.depth for c in snapshot.comments) == 4

    by_id = {c.external_id: c for c in snapshot.comments}
    for c in snapshot.comments:
        if c.parent_external_id is not None:
            assert by_id[c.parent_external_id].depth == c.depth - 1
    assert all(c.observed_at == FROZEN_NOW for c in snapshot.comments)
    assert all("<p>" not in (c.body or "") for c in snapshot.comments)


async def test_a_deleted_comment_stays_as_a_row_without_author_or_body(
    algolia: _Algolia, hn_item_with_comments: dict
) -> None:
    mutated = copy.deepcopy(hn_item_with_comments)
    mutated["children"][0]["author"] = None
    mutated["children"][0]["text"] = None
    algolia.routes["/api/v1/items/4102013"] = (200, mutated)
    snapshot = await algolia.source().fetch_thread("ask", "4102013")
    deleted = snapshot.comments[0]
    assert deleted.author is None and deleted.body is None
    assert len(snapshot.comments) == 44, "usunięty komentarz to nadal dowód, że ktoś odpowiedział"


async def test_fetch_of_a_missing_item_is_thread_not_found(algolia: _Algolia) -> None:
    with pytest.raises(ThreadNotFound):
        await algolia.source().fetch_thread("ask", "1")
    with pytest.raises(ThreadNotFound):
        await algolia.source().fetch_thread("ask", "not-a-number")


async def test_fetch_of_a_comment_id_is_not_a_thread(
    algolia: _Algolia, hn_item_with_comments: dict
) -> None:
    child = copy.deepcopy(hn_item_with_comments["children"][0])
    algolia.routes[f"/api/v1/items/{child['id']}"] = (200, child)
    with pytest.raises(ThreadNotFound, match="nie jest wątkiem"):
        await algolia.source().fetch_thread("ask", str(child["id"]))


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_platform_error_is_never_an_empty_listing(algolia: _Algolia, status: int) -> None:
    algolia.routes["/api/v1/search_by_date"] = (status, {"message": "nope"})
    with pytest.raises(SourceUnavailable, match=str(status)):
        await algolia.source().list_recent_threads("ask", since=None, limit=5)


async def test_a_body_that_is_not_json_is_never_an_empty_listing(algolia: _Algolia) -> None:
    algolia.routes["/api/v1/search_by_date"] = (200, "<html>maintenance</html>")
    with pytest.raises(SourceUnavailable, match="JSON"):
        await algolia.source().list_recent_threads("ask", since=None, limit=5)


async def test_a_json_body_without_hits_is_never_an_empty_listing(algolia: _Algolia) -> None:
    algolia.routes["/api/v1/search_by_date"] = (200, {"nbHits": 0})
    with pytest.raises(SourceUnavailable, match="hits"):
        await algolia.source().list_recent_threads("ask", since=None, limit=5)


async def test_a_network_failure_is_source_unavailable() -> None:
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    source = HackerNewsSource(
        httpx.AsyncClient(transport=httpx.MockTransport(explode)),
        base_url="https://algolia.test/api/v1",
        clock=frozen_clock,
    )
    with pytest.raises(SourceUnavailable, match="ConnectError"):
        await source.list_recent_threads("ask", since=None, limit=5)


async def test_unknown_community_and_bad_limit_are_refused_before_any_request(
    algolia: _Algolia,
) -> None:
    source = algolia.source()
    with pytest.raises(UnknownCommunity):
        await source.list_recent_threads("r/devops", since=None, limit=5)
    with pytest.raises(ValueError, match="limit"):
        await source.list_recent_threads("ask", since=None, limit=0)
    with pytest.raises(ValueError, match="limit"):
        await source.list_recent_threads("ask", since=None, limit=101)
    assert algolia.requests == [], "walidacja wejścia nie kosztuje zapytania do platformy"


async def test_an_empty_page_is_an_empty_tuple_only_when_the_platform_said_so(
    algolia: _Algolia, hn_search_ask: dict
) -> None:
    """The one legitimate empty result: Algolia answered 200 with `hits: []`."""
    empty = {**hn_search_ask, "hits": [], "nbHits": 0}
    algolia.routes["/api/v1/search_by_date"] = (200, empty)
    assert (
        await algolia.source().list_recent_threads(
            "ask", since=FROZEN_NOW - timedelta(minutes=1), limit=5
        )
        == ()
    )


def test_the_recorded_fixture_is_the_real_shape() -> None:
    """Guards the fixture itself: a hand-edited file would test the parser against a story."""
    from tests.conftest import FIXTURES

    raw = json.loads((FIXTURES / "hn_search_ask.json").read_text(encoding="utf-8"))
    assert raw["params"].startswith("tags=ask_hn")
    assert {"objectID", "title", "author", "points", "num_comments", "created_at_i"} <= set(
        raw["hits"][0]
    )
