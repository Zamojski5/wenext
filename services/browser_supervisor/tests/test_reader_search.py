"""Reddit search through the capsule's reader (10.10): the allowlist is the boundary, so this file
measures what the two search paths let through and, more to the point, what they refuse.

No Chrome. The shapes below are the ones `control_plane.observe.reddit_search` builds; the
contract test on that side runs every URL it produces through `authorize_url` as well.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from browser_supervisor.reader import (
    ALLOWED_PATHS,
    ALLOWED_QUERY,
    READER_TOKEN_HEADER,
    SEARCH_PATHS,
    SEARCH_QUERY,
    SUBREDDIT_SEARCH_QUERY,
    FetchResult,
    ReaderServer,
    ReadRefused,
    authorize_url,
)
from test_reader import TOKEN, FakeClock, FakeFetcher

WWW = "https://www.reddit.com"

ACCEPTED: list[tuple[str, str]] = [
    # Every parameter, global search, in the order the client writes them.
    (
        f"{WWW}/search.json?q=deploy+previews&sort=new&t=week&limit=25&type=link&raw_json=1",
        f"{WWW}/search.json?q=deploy+previews&sort=new&t=week&limit=25&type=link&raw_json=1",
    ),
    # One community, `restrict_sr` on.
    (
        f"{WWW}/r/devops/search.json?q=cold+start&restrict_sr=on&sort=relevance&t=all&limit=100",
        f"{WWW}/r/devops/search.json?q=cold+start&restrict_sr=on&sort=relevance&t=all&limit=100",
    ),
    # `q` alone is a search.
    (f"{WWW}/search.json?q=x", f"{WWW}/search.json?q=x"),
    # Reddit's own search syntax survives: phrase, field, exclusion, OR, groups, C++, CI/CD.
    (
        f"{WWW}/search.json?q=%22cold+start%22+title%3Alambda+-ad+%28a+OR+b%29+C%2B%2B+CI%2FCD",
        f"{WWW}/search.json?q=%22cold+start%22+title%3Alambda+-ad+%28a+OR+b%29+C%2B%2B+CI%2FCD",
    ),
    # Non-ASCII letters are letters: the query may be Polish.
    (
        f"{WWW}/search.json?q=wdro%C5%BCenie+bez+przestoj%C3%B3w",
        f"{WWW}/search.json?q=wdro%C5%BCenie+bez+przestoj%C3%B3w",
    ),
    # `restrict_sr` spellings Reddit accepts, and the lower bound of `limit`.
    (
        f"{WWW}/r/devops/search.json?q=a&restrict_sr=1&limit=1",
        f"{WWW}/r/devops/search.json?q=a&restrict_sr=1&limit=1",
    ),
    (
        f"{WWW}/r/devops/search.json?q=a&restrict_sr=true",
        f"{WWW}/r/devops/search.json?q=a&restrict_sr=true",
    ),
    # Canonical form: host lower-cased, the query re-encoded from the parsed pairs.
    ("HTTPS://WWW.REDDIT.COM/search.json?q=a%20b", f"{WWW}/search.json?q=a+b"),
    # Every `sort` and `t` value of the closed sets.
    *[
        (f"{WWW}/search.json?q=a&sort={sort}", f"{WWW}/search.json?q=a&sort={sort}")
        for sort in ("new", "relevance", "top", "comments")
    ],
    *[
        (f"{WWW}/search.json?q=a&t={span}", f"{WWW}/search.json?q=a&t={span}")
        for span in ("hour", "day", "week", "month", "year", "all")
    ],
]

REFUSED: list[str] = [
    # `q` is required, non-blank and bounded.
    f"{WWW}/search.json",
    f"{WWW}/search.json?sort=new",
    f"{WWW}/search.json?q=",
    f"{WWW}/search.json?q=%20%20%20",
    f"{WWW}/search.json?q=" + "a" * 257,
    f"{WWW}/search.json?q=a&q=b",
    # Characters outside the class: control characters, brackets, braces, percent, backslash,
    # angle brackets, pipes, equals, quotes of the typographic kind, emoji.
    f"{WWW}/search.json?q=a%0Ab",
    f"{WWW}/search.json?q=a%00b",
    f"{WWW}/search.json?q=a%09b",
    f"{WWW}/search.json?q=a%0A",
    f"{WWW}/search.json?q=%5Bx%5D",
    f"{WWW}/search.json?q=%7Bx%7D",
    f"{WWW}/search.json?q=100%25",
    f"{WWW}/search.json?q=a%5Cb",
    f"{WWW}/search.json?q=%3Cscript%3E",
    f"{WWW}/search.json?q=a%7Cb",
    f"{WWW}/search.json?q=a%3Db",
    f"{WWW}/search.json?q=%E2%80%9Cx%E2%80%9D",
    f"{WWW}/search.json?q=%F0%9F%9A%80",
    # `sort`: only the search set (`old` and `hot` belong to other paths or to nobody).
    f"{WWW}/search.json?q=a&sort=old",
    f"{WWW}/search.json?q=a&sort=hot",
    f"{WWW}/search.json?q=a&sort=confidence",
    # `t`, `limit`, `type`, `raw_json`, `restrict_sr`: closed values.
    f"{WWW}/search.json?q=a&t=decade",
    f"{WWW}/search.json?q=a&t=",
    f"{WWW}/search.json?q=a&limit=0",
    f"{WWW}/search.json?q=a&limit=101",
    f"{WWW}/search.json?q=a&limit=500",
    f"{WWW}/search.json?q=a&limit=-1",
    f"{WWW}/search.json?q=a&limit=10%0A",
    f"{WWW}/search.json?q=a&type=comment",
    f"{WWW}/search.json?q=a&type=sr",
    f"{WWW}/search.json?q=a&type=user",
    f"{WWW}/search.json?q=a&type=link%2Csr",
    f"{WWW}/search.json?q=a&raw_json=0",
    f"{WWW}/r/devops/search.json?q=a&restrict_sr=0",
    f"{WWW}/r/devops/search.json?q=a&restrict_sr=off",
    f"{WWW}/r/devops/search.json?q=a&restrict_sr=on&restrict_sr=on",
    # `restrict_sr` means "this community"; on the global path it means nothing and is refused.
    f"{WWW}/search.json?q=a&restrict_sr=on",
    # Parameters nobody asked for: pagination, NSFW switches, depth, free keys.
    f"{WWW}/search.json?q=a&after=t3_abc123",
    f"{WWW}/search.json?q=a&before=t3_abc123",
    f"{WWW}/search.json?q=a&include_over_18=on",
    f"{WWW}/search.json?q=a&depth=3",
    f"{WWW}/search.json?q=a&count=25",
    f"{WWW}/search.json?q=a&x=1",
    # Paths that look like search and are not allowed.
    f"{WWW}/search",
    f"{WWW}/search/.json?q=a",
    f"{WWW}/search.json/?q=a",
    f"{WWW}/search.json.php?q=a",
    f"{WWW}/r/devops/search?q=a",
    f"{WWW}/r/devops/search.json/?q=a",
    f"{WWW}/r/a/search.json?q=x",
    f"{WWW}/r/a+b/search.json?q=x",
    f"{WWW}/r/devops/top/search.json?q=x",
    f"{WWW}/r/devops/../search.json?q=x",
    f"{WWW}/user/someone/search.json?q=x",
    f"{WWW}/u/someone/search.json?q=x",
    f"{WWW}/subreddits/search.json?q=x",
    f"{WWW}/r/devops/wiki/search.json?q=x",
    f"{WWW}/r/devops/comments/abc123/search.json?q=x",
    f"{WWW}/message/search.json?q=x",
    f"{WWW}/api/search.json?q=x",
    # The origin lock still applies to the new paths.
    "http://www.reddit.com/search.json?q=a",
    "https://old.reddit.com/search.json?q=a",
    "https://reddit.com/search.json?q=a",
    "https://oauth.reddit.com/search.json?q=a",
    "https://www.reddit.com.evil.example/search.json?q=a",
    "https://www.reddit.com@evil.example/search.json?q=a",
    "https://www.reddit.com:8443/search.json?q=a",
    f"{WWW}/search.json?q=a#frag",
]

# Keys that belong to the listing paths and to nothing else: a listing key must not ride on a
# search path, and a search key must not ride on a listing path.
LISTING_ONLY = ("depth", "sort=old", "sort=confidence", "sort=controversial", "sort=qa")
SEARCH_ONLY = ("q=a", "t=week", "restrict_sr=on", "type=link", "sort=relevance", "sort=comments")
LISTING_PATHS = (
    "/r/devops/new.json",
    "/r/devops/comments/abc123.json",
    "/r/devops/about.json",
    "/api/me.json",
)


@pytest.mark.parametrize(("url", "canonical"), ACCEPTED)
def test_the_two_search_shapes_pass_and_come_back_canonical(url: str, canonical: str) -> None:
    assert authorize_url(url) == canonical


@pytest.mark.parametrize("url", REFUSED)
def test_everything_near_search_that_is_not_the_shape_is_refused(url: str) -> None:
    with pytest.raises(ReadRefused):
        authorize_url(url)


@pytest.mark.parametrize("path", LISTING_PATHS)
@pytest.mark.parametrize("pair", SEARCH_ONLY)
def test_a_search_key_on_a_listing_path_is_refused(path: str, pair: str) -> None:
    # `sort=relevance` is also a search-only VALUE: the listing paths keep their own `sort` set.
    with pytest.raises(ReadRefused):
        authorize_url(f"{WWW}{path}?{pair}")


@pytest.mark.parametrize("pair", LISTING_ONLY)
def test_a_listing_key_on_a_search_path_is_refused(pair: str) -> None:
    for path in ("/search.json", "/r/devops/search.json"):
        with pytest.raises(ReadRefused):
            authorize_url(f"{WWW}{path}?q=a&{pair if '=' in pair else pair + '=1'}")


def test_the_listing_paths_keep_exactly_the_query_they_had() -> None:
    assert set(ALLOWED_QUERY) == {"raw_json", "limit", "depth", "sort"}
    assert ALLOWED_QUERY["sort"].fullmatch("relevance") is None
    assert authorize_url(f"{WWW}/r/devops/new.json?raw_json=1&limit=500&sort=new").endswith(
        "limit=500&sort=new"
    )
    assert authorize_url(f"{WWW}/r/devops/comments/abc123.json?limit=500&depth=10&sort=old")


def test_the_search_query_sets_are_closed_and_restrict_sr_belongs_to_the_community_path() -> None:
    assert set(SEARCH_QUERY) == {"q", "sort", "t", "limit", "type", "raw_json"}
    assert set(SUBREDDIT_SEARCH_QUERY) == set(SEARCH_QUERY) | {"restrict_sr"}
    assert len(SEARCH_PATHS) == 2
    assert all(path in ALLOWED_PATHS for path in SEARCH_PATHS)


def test_a_trailing_newline_no_longer_slips_through_a_dollar_anchored_pattern() -> None:
    """`^1$` matches BEFORE a trailing newline, so `limit=5%0A` used to pass as `5` and the
    canonical query carried the newline to Reddit. The query values are now matched whole."""
    for url in (
        f"{WWW}/r/devops/new.json?limit=5%0A",
        f"{WWW}/r/devops/new.json?raw_json=1%0A",
        f"{WWW}/r/devops/comments/abc123.json?depth=3%0A",
    ):
        with pytest.raises(ReadRefused):
            authorize_url(url)


def test_the_inbox_switch_does_not_widen_search() -> None:
    with pytest.raises(ReadRefused):
        authorize_url(f"{WWW}/search.json?q=a&after=x", allow_inbox=True)
    assert authorize_url(f"{WWW}/search.json?q=a", allow_inbox=True) == f"{WWW}/search.json?q=a"


# -- through the wire --------------------------------------------------------------------------


@pytest.fixture
def running() -> Iterator[tuple[ReaderServer, FakeFetcher, FakeClock]]:
    fetcher = FakeFetcher()
    clock = FakeClock()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=fetcher,
        min_interval_s=2.0,
        timeout_s=7.5,
        clock=clock.now,
        sleep=clock.sleep,
        platform_cooldown_s=300.0,
    )
    server.start()
    try:
        yield server, fetcher, clock
    finally:
        server.stop()


def _read(server: ReaderServer, url: str) -> tuple[int, dict]:
    from urllib.parse import quote

    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/read?url={quote(url, safe='')}",
        headers={READER_TOKEN_HEADER: TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


SEARCH = f"{WWW}/search.json?q=deploy+previews&sort=new&t=week&limit=25&type=link&raw_json=1"


def test_an_allowed_search_is_fetched_as_the_canonical_url_through_the_session(running) -> None:
    server, fetcher, _ = running
    fetcher.result = FetchResult(
        status=200,
        final_url=SEARCH,
        content_type="application/json; charset=UTF-8",
        body='{"kind": "Listing", "data": {"children": []}}',
    )
    status, body = _read(server, SEARCH.replace("deploy+previews", "deploy%20previews"))
    assert status == 200 and body["status"] == 200 and body["viewer"] == "session"
    assert fetcher.calls == [(SEARCH, 7.5)], "Chrome dostaje kanoniczną postać, nie to, co przyszło"
    assert fetcher.viewers == [False]


def test_a_refused_search_is_400_and_chrome_is_not_asked(running) -> None:
    server, fetcher, _ = running
    for url in (
        f"{WWW}/search.json?q=a&after=t3_x",
        f"{WWW}/search.json",
        f"{WWW}/user/someone/search.json?q=a",
        f"{WWW}/r/devops/new.json?q=a",
    ):
        status, body = _read(server, url)
        assert status == 400 and body["error"] == "url_refused", url
    assert fetcher.calls == []


def test_searches_are_paced_like_every_other_read(running) -> None:
    server, fetcher, clock = running
    _read(server, SEARCH)
    assert clock.sleeps == []
    _read(server, SEARCH)
    assert clock.sleeps == [pytest.approx(2.0)], "drugie wyszukiwanie zaraz po pierwszym czeka"
    assert len(fetcher.calls) == 2


def test_a_429_on_a_search_brakes_every_reddit_read_of_the_account(running) -> None:
    """The brake is per address, not per path: a search that Reddit throttles must also hold back
    the observer's listing reads, and the other way round."""
    server, fetcher, clock = running
    fetcher.result = FetchResult(
        status=429, final_url="u", content_type=None, body="", retry_after_s=120.0
    )
    assert _read(server, SEARCH)[1]["status"] == 429
    listing = f"{WWW}/r/devops/new.json?raw_json=1&limit=5"
    status, body = _read(server, listing)
    assert status == 200 and body["status"] == 429 and body["paced"] is True
    assert len(fetcher.calls) == 1
    status, body = _read(server, SEARCH)
    assert body["paced"] is True and len(fetcher.calls) == 1
    clock.t += 121
    fetcher.result = FetchResult(status=200, final_url="u", content_type=None, body="{}")
    assert _read(server, SEARCH)[1]["status"] == 200
