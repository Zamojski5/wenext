"""`reddit.search` for a hosted session: Reddit through a search API (Serper), with the HTTP mocked.

What is held here, without a network or a database:

  * THE REQUEST: the endpoint, the key in `X-API-KEY`, `site:reddit.com/r/ <phrase>` (or the one
    community), the recency filter from `time`, one page of results and never more;
  * THE MAPPING: only Reddit POSTS come back, canonical address, title cleaned of the engine's
    decoration, snippet as the excerpt, the subreddit from the address, a duplicate (the same post
    twice, once as a comment permalink) once, and a field a search engine cannot know is `None`;
  * A DATE IS NEVER MADE UP: an absolute date is that day, a relative one is measured from the
    moment of the request and keeps the engine's own words, anything else is `None`;
  * THE ENVIRONMENT IS SAID, NOT HIDDEN: no key, a refused key, a rate limit, a provider error and
    a shape nobody expected are each an "unavailable" with a reason, and an empty answer is an
    empty list, which is a different sentence;
  * THE ALLOWANCE IS TAKEN BEFORE THE CALL and only after the arguments and the key were checked, so
    a call that was never going to run costs nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from control_plane.observe import reddit_serp
from control_plane.observe.reddit_search import RedditSearchUnavailable
from control_plane.observe.reddit_serp import (
    DEFAULT_DAILY_CAP,
    SERPER_URL,
    hit_from_result,
    parse_serp_date,
    search_reddit,
)
from control_plane.tools.opportunity_tools import _hit_json

NOW = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)

ORGANIC = [
    {
        "title": "How do you find your first 10 users? : r/SideProject",
        "link": "https://www.reddit.com/r/SideProject/comments/1abc23d/how_do_you_find_your_first_10_users/",
        "snippet": "I built a small tool for tracking invoices\n and nobody knows it exists.",
        "date": "3 hours ago",
        "position": 1,
    },
    {
        "title": "r/SaaS on Reddit: What is a boring channel that works?",
        "link": "https://old.reddit.com/r/SaaS/comments/1def45e/boring_channel/gx9kq1k/?context=3",
        "snippet": "Answers from the comments.",
        "date": "Oct 8, 2026",
        "position": 2,
    },
    {  # the same post as the first, found again
        "title": "How do you find your first 10 users? : r/SideProject",
        "link": "https://www.reddit.com/r/SideProject/comments/1abc23d/how_do_you_find_your_first_10_users/hz8/",
        "snippet": "A comment.",
        "position": 3,
    },
    {"title": "u/someone", "link": "https://www.reddit.com/user/someone/", "snippet": "profile"},
    {"title": "Wiki", "link": "https://www.reddit.com/r/SaaS/wiki/index/", "snippet": "wiki"},
    {"title": "Other site", "link": "https://example.com/r/SaaS/comments/abc/x/", "snippet": "x"},
    {
        "title": "Not https",
        "link": "http://www.reddit.com/r/SaaS/comments/abc12/x/",
        "snippet": "x",
    },
    {
        "title": "A post with no date",
        "link": "https://www.reddit.com/r/startups/comments/1ghi67f/a_post_with_no_date/",
        "snippet": "z" * 400,
    },
    {
        "title": "Old",
        "link": "https://www.reddit.com/r/startups/comments/1jkl89g/old/",
        "snippet": "old",
        "date": "2 months ago",
    },
]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(payload: dict[str, Any] | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload if payload is not None else {"organic": ORGANIC})

    return handler, seen


async def _search(handler, **kwargs):
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("now", NOW)
    async with _client(handler) as client:
        return await search_reddit(query="invoice tracking pain", client=client, **kwargs)


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


async def test_the_request_is_one_post_to_serper_with_the_key_in_the_header() -> None:
    handler, seen = _ok()
    await _search(handler, time="day")
    (request,) = seen
    assert request.method == "POST" and str(request.url) == SERPER_URL
    assert SERPER_URL == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == "test-key"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "q": "site:reddit.com/r/ invoice tracking pain",
        "num": 10,
        "tbs": "qdr:d",
    }
    assert "test-key" not in str(request.url) and b"test-key" not in request.content


@pytest.mark.parametrize(
    ("window", "tbs"),
    [("hour", "qdr:h"), ("day", "qdr:d"), ("week", "qdr:w"), ("month", "qdr:m"), ("year", "qdr:y")],
)
async def test_the_recency_filter_follows_the_time_window(window: str, tbs: str) -> None:
    handler, seen = _ok()
    await _search(handler, time=window)
    assert json.loads(seen[0].content)["tbs"] == tbs


async def test_all_time_has_no_recency_filter_and_the_default_is_a_week() -> None:
    handler, seen = _ok()
    await _search(handler, time="all")
    assert "tbs" not in json.loads(seen[0].content)
    await _search(handler)
    assert json.loads(seen[1].content)["tbs"] == "qdr:w"


async def test_one_community_narrows_the_site_and_the_name_is_normalised() -> None:
    handler, seen = _ok()
    await _search(handler, subreddit="r/SaaS")
    assert json.loads(seen[0].content)["q"] == "site:reddit.com/r/SaaS/ invoice tracking pain"
    with pytest.raises(ValueError, match="niepoprawna"):
        await _search(handler, subreddit="not a sub!")
    assert len(seen) == 1, "a bad argument costs no request"


async def test_one_page_is_the_most_it_asks_for_and_the_sort_order_is_ignored() -> None:
    handler, seen = _ok()
    await _search(handler, limit=50, sort="relevance")
    assert json.loads(seen[0].content)["num"] == 10
    await _search(handler, limit=3, sort="top")
    assert json.loads(seen[1].content)["num"] == 3
    assert "sort" not in json.loads(seen[1].content)


async def test_bad_arguments_are_a_value_error_and_never_a_request() -> None:
    handler, seen = _ok()
    for kwargs in (
        {"time": "decade"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"sort": "hot"},
    ):
        with pytest.raises(ValueError):
            await _search(handler, **kwargs)
    async with _client(handler) as client:
        with pytest.raises(ValueError, match="puste"):
            await search_reddit(query="   ", client=client, api_key="k")
        with pytest.raises(ValueError, match="znaków"):
            await search_reddit(query="a" * 300, client=client, api_key="k")
    assert seen == []


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


async def test_only_reddit_posts_come_back_once_each_in_the_engines_order() -> None:
    handler, _ = _ok()
    hits = await _search(handler)
    assert [h.post_id for h in hits] == ["1abc23d", "1def45e", "1ghi67f", "1jkl89g"]
    assert [h.subreddit for h in hits] == ["SideProject", "SaaS", "startups", "startups"]


async def test_a_hit_has_the_canonical_address_a_clean_title_and_a_collapsed_snippet() -> None:
    handler, _ = _ok()
    first, second, third, _ = await _search(handler)
    assert first.url == (
        "https://www.reddit.com/r/SideProject/comments/1abc23d/how_do_you_find_your_first_10_users/"
    )
    assert first.title == "How do you find your first 10 users?", (
        "the ': r/Sub' tail is the engine's"
    )
    assert first.excerpt == "I built a small tool for tracking invoices and nobody knows it exists."
    assert second.title == "What is a boring channel that works?", "the 'r/Sub on Reddit:' head too"
    assert second.url == "https://www.reddit.com/r/SaaS/comments/1def45e/boring_channel/", (
        "a comment permalink and an old.reddit host become the post at www.reddit.com"
    )
    assert len(third.excerpt) == 300 and third.excerpt.endswith("...")


async def test_what_a_search_engine_cannot_know_is_none_and_never_a_made_up_value() -> None:
    handler, _ = _ok()
    for hit in await _search(handler):
        assert (hit.author, hit.score, hit.num_comments) == (None, None, None)
        assert (hit.locked, hit.archived) == (None, None)
    shown = _hit_json((await _search(handler))[0], now=NOW)
    assert shown["author"] is None and shown["score"] is None and shown["num_comments"] is None
    assert shown["zablokowany"] is None and shown["zarchiwizowany"] is None
    assert shown["przyjmie_odpowiedz"] is None, "unknown is not 'takes a reply'"


def test_a_relative_date_is_measured_from_the_request_and_keeps_the_engines_words() -> None:
    assert parse_serp_date("3 hours ago", now=NOW) == NOW - timedelta(hours=3)
    assert parse_serp_date("1 hour ago", now=NOW) == NOW - timedelta(hours=1)
    assert parse_serp_date("45 minutes ago", now=NOW) == NOW - timedelta(minutes=45)
    assert parse_serp_date("2 days ago", now=NOW) == NOW - timedelta(days=2)
    assert parse_serp_date("1 week ago", now=NOW) == NOW - timedelta(weeks=1)


def test_an_absolute_date_is_that_day_at_midnight_utc() -> None:
    expected = datetime(2026, 10, 8, tzinfo=UTC)
    assert parse_serp_date("Oct 8, 2026", now=NOW) == expected
    assert parse_serp_date("October 8, 2026", now=NOW) == expected
    assert parse_serp_date("8 Oct 2026", now=NOW) == expected
    assert parse_serp_date("2026-10-08", now=NOW) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "   ", "2 months ago", "last year", "yesterday", "soon", 12]
)
def test_a_date_that_cannot_be_read_is_none_never_a_guess(raw: Any) -> None:
    assert parse_serp_date(raw, now=NOW) is None


async def test_a_hit_carries_the_time_and_the_engines_own_date_text_side_by_side() -> None:
    handler, _ = _ok()
    first, second, third, old = await _search(handler)
    assert first.created_at == NOW - timedelta(hours=3) and first.date_text == "3 hours ago"
    assert (
        second.created_at == datetime(2026, 10, 8, tzinfo=UTC) and second.date_text == "Oct 8, 2026"
    )
    assert third.created_at is None and third.date_text is None, "no date field, no date"
    assert old.created_at is None and old.date_text == "2 months ago", "kept as said, not as a time"
    shown = _hit_json(first, now=NOW)
    assert shown["wiek_godzin"] == 3.0 and shown["data_zrodla"] == "3 hours ago"
    assert _hit_json(third, now=NOW)["created_at"] is None
    assert _hit_json(third, now=NOW)["wiek_godzin"] is None
    assert "data_zrodla" not in _hit_json(third, now=NOW)


def test_hit_from_result_refuses_what_is_not_a_reddit_post() -> None:
    for link in (
        "https://www.reddit.com/user/x/",
        "https://www.reddit.com/r/SaaS/",
        "https://www.reddit.com/r/SaaS/wiki/index/",
        "https://example.com/r/SaaS/comments/abc12/x/",
        "https://redditmirror.com/r/SaaS/comments/abc12/x/",
        "http://www.reddit.com/r/SaaS/comments/abc12/x/",
        "",
        "javascript:alert(1)",
    ):
        assert hit_from_result({"title": "t", "link": link}, now=NOW) is None, link
    assert hit_from_result({"title": "", "link": ORGANIC[0]["link"]}, now=NOW) is None
    assert hit_from_result(
        {"title": "t", "link": "https://www.reddit.com/r/SaaS/comments/abc12/"}, now=NOW
    )


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


async def test_without_a_key_it_is_unavailable_with_the_reason_and_costs_nothing(
    monkeypatch,
) -> None:
    monkeypatch.delenv(reddit_serp.ENV_KEY, raising=False)
    handler, seen = _ok()
    taken: list[int] = []

    class Quota:
        async def take(self) -> None:
            taken.append(1)

    async with _client(handler) as client:
        with pytest.raises(RedditSearchUnavailable) as caught:
            await search_reddit(query="invoices", client=client, quota=Quota())
    assert caught.value.kind == "not_configured"
    assert "SERPER_API_KEY" in str(caught.value) and "no API key" in str(caught.value)
    assert seen == [] and taken == [], "no request, no allowance spent"


async def test_the_key_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "from-env")
    handler, seen = _ok()
    async with _client(handler) as client:
        await search_reddit(query="invoices", client=client)
    assert seen[0].headers["X-API-KEY"] == "from-env"
    monkeypatch.setenv("SERPER_API_KEY", "   ")
    async with _client(handler) as client:
        with pytest.raises(RedditSearchUnavailable):
            await search_reddit(query="invoices", client=client)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "misconfigured"), (403, "blocked"), (429, "rate_limited"), (400, "unavailable"),
     (500, "unavailable"), (503, "unavailable")],
)  # fmt: skip
async def test_a_provider_refusal_is_unavailable_with_its_own_kind(status: int, kind: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "no"})

    with pytest.raises(RedditSearchUnavailable) as caught:
        await _search(handler)
    assert caught.value.kind == kind
    assert caught.value.detail, "the provider's own words are kept for the log"
    assert "test-key" not in str(caught.value) and "test-key" not in caught.value.detail


async def test_a_network_failure_is_unavailable_and_says_to_try_once_more() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(RedditSearchUnavailable) as caught:
        await _search(handler)
    assert caught.value.kind == "unavailable" and "ConnectError" in caught.value.detail


async def test_nothing_matched_is_an_empty_list_and_an_unknown_shape_is_not() -> None:
    handler, _ = _ok({"searchParameters": {"q": "x"}, "credits": 1})
    assert await _search(handler) == []
    handler, _ = _ok({"organic": []})
    assert await _search(handler) == []
    for body in ({"error": "x"}, {"organic": "nope"}, ["a"]):
        handler, _ = _ok(body)
        with pytest.raises(RedditSearchUnavailable) as caught:
            await _search(handler)
        assert caught.value.kind == "unavailable"

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    with pytest.raises(RedditSearchUnavailable):
        await _search(not_json)


# ---------------------------------------------------------------------------
# The allowance
# ---------------------------------------------------------------------------


async def test_the_allowance_is_taken_once_per_query_before_the_call() -> None:
    order: list[str] = []

    class Quota:
        async def take(self) -> None:
            order.append("take")

    def handler(request: httpx.Request) -> httpx.Response:
        order.append("call")
        return httpx.Response(200, json={"organic": []})

    await _search(handler, quota=Quota())
    await _search(handler, quota=Quota())
    assert order == ["take", "call", "take", "call"]


async def test_a_spent_allowance_stops_the_call_and_says_so() -> None:
    class Spent:
        async def take(self) -> None:
            raise RedditSearchUnavailable("used up", kind="rate_limited")

    handler, seen = _ok()
    with pytest.raises(RedditSearchUnavailable) as caught:
        await _search(handler, quota=Spent())
    assert caught.value.kind == "rate_limited" and seen == []


def test_the_default_allowance_is_sixty_queries_a_day_and_the_environment_may_move_it() -> None:
    quota = reddit_serp.SerpQuota(None, __import__("uuid").uuid4(), environ={})  # type: ignore[arg-type]
    assert DEFAULT_DAILY_CAP == 60 and quota.cap == 60
    org = __import__("uuid").uuid4()
    assert reddit_serp.SerpQuota(None, org, environ={"WENEXT_SERP_DAILY_CAP": "25"}).cap == 25  # type: ignore[arg-type]
    assert reddit_serp.SerpQuota(None, org, environ={"WENEXT_SERP_DAILY_CAP": "x"}).cap == 60  # type: ignore[arg-type]
    assert reddit_serp.SerpQuota(None, org, cap=3, environ={}).cap == 3  # type: ignore[arg-type]


def test_the_module_has_no_long_dash() -> None:
    import re
    from pathlib import Path

    long_dashes = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
    for name in ("control_plane/observe/reddit_serp.py", "tests/test_reddit_serp.py"):
        assert not long_dashes.search(
            (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
        ), name
