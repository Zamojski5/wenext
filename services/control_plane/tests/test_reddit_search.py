"""Live Reddit search and thread reading (`observe/reddit_search.py`), measured without Reddit.

Four things are measured, in the order a failure would cost the most:

1. THE CONTRACT WITH THE CAPSULE. Every URL this module builds goes through the reader's own
   `authorize_url`, and the character class of `q` is compared with the reader's character by
   character. The reader lives in another service and the control plane's image does not carry it,
   so the test puts the source tree on the path; a missing tree is a failure, not a skip.
2. WHAT A HIT IS. Removed, deleted, NSFW, stickied, promoted and profile posts never come back,
   each by itself and all together in one recorded-shape listing.
3. WHAT EACH FAILURE SAYS. The adapter's real ladder (`RedditBrowserSource` and
   `HttpBrowserReader`, over fakes of the page and of the HTTP wire) produces the messages, and the
   `kind` of `RedditSearchUnavailable` is derived from them, so a reworded message in
   `community_observer` turns this file red instead of turning every failure into "unavailable".
4. THE THREAD. A permalink or an id plus a community, post state, the best comments.

The fixtures are the RECORDED `/r/kubernetes/new.json` and `/comments/1w6ln9w.json` of 2026-09-04,
trimmed to the fields the parser reads, plus variants for the skip rules (see `_nagrano` in each).
A recording of a live SEARCH does not exist yet: the capsule did not serve `/search.json` until
this change was deployed.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from community_observer import HttpBrowserReader, PageRead, RedditBrowserSource, SourceUnavailable
from control_plane.observe import reddit_search
from control_plane.observe.reddit_search import (
    BLOCKED,
    CAPSULE_DOWN,
    LOGIN_REQUIRED,
    MAX_QUERY_CHARS,
    MISCONFIGURED,
    NOT_CONFIGURED,
    NOT_FOUND,
    RATE_LIMITED,
    REFUSED,
    SORTS,
    TIME_SPANS,
    UNAVAILABLE,
    UNAVAILABLE_KINDS,
    RedditHit,
    RedditSearchUnavailable,
    RedditThread,
    _CapsuleReader,
    build_search_url,
    hit_from_child,
    normalize_query,
    normalize_subreddit,
    parse_thread_reference,
    read_reddit_thread,
    reader_from_env,
    search_reddit,
)

REPO = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
# Typographic characters by code point: this repository forbids the long dashes in files, and a
# literal curly quote is an ambiguous character to the linter.
EM, EN = chr(0x2014), chr(0x2013)
LEFT_Q, RIGHT_Q, LEFT_SQ, RIGHT_SQ = chr(0x201C), chr(0x201D), chr(0x2018), chr(0x2019)
ELLIPSIS, NBSP = chr(0x2026), chr(0x00A0)
LONG_DASHES = {EM, EN}


def _reader_module() -> Any:
    """The capsule's reader, imported from the source tree (the contract's other side)."""
    tree = REPO / "services" / "browser_supervisor"
    assert (tree / "browser_supervisor" / "reader.py").is_file(), (
        f"brak {tree}: bez kodu czytnika kontrakt z kapsułą nie jest sprawdzany"
    )
    if str(tree) not in sys.path:
        sys.path.insert(0, str(tree))
    import browser_supervisor.reader as reader

    return reader


READER = _reader_module()


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _listing_children() -> list[dict[str, Any]]:
    return json.loads(_fixture("reddit_search_listing.json")["body"])["data"]["children"]


def _good_child() -> dict[str, Any]:
    return copy.deepcopy(next(c for c in _listing_children() if c["data"]["id"] == "1w6bg37"))


# -- 1. the contract with the capsule ------------------------------------------------------------

URL_CASES: list[dict[str, Any]] = [
    {"query": "deploy previews"},
    {"query": "deploy previews", "subreddit": "devops"},
    {"query": "deploy previews", "subreddit": "r/devops"},
    {"query": "deploy previews", "subreddit": "/r/DevOps/"},
    {"query": '"cold start" OR latency -ad title:lambda (a OR b) C++ CI/CD #1 @x R&D'},
    {"query": "wdrożenie bez przestojów"},
    # What a model types by habit: curly quotes, a dash, a newline, an ellipsis, a no-break space.
    {"query": f"{LEFT_Q}deploy previews{RIGHT_Q} {EM} slow{ELLIPSIS}\n and{NBSP}hard"},
    {"query": "x" * MAX_QUERY_CHARS},
    {"query": "a", "sort": "relevance", "time": "all", "limit": 100},
    {"query": "a", "sort": "top", "time": "hour", "limit": 1},
    {"query": "a", "sort": "comments", "time": "year", "subreddit": "ab"},
    {"query": "a", "time": "month", "subreddit": "x" * 21},
]


@pytest.mark.parametrize("case", URL_CASES)
def test_every_url_built_here_is_accepted_by_the_readers_own_allowlist_unchanged(
    case: dict[str, Any],
) -> None:
    kwargs = {"subreddit": None, "sort": "new", "time": "week", "limit": 25} | case
    url = build_search_url(**kwargs)
    assert READER.authorize_url(url) == url, (
        "to, co wysyłamy, jest dokładnie tym, co sprawdza czytnik"
    )


def test_every_combination_of_sort_time_and_limit_passes_the_reader() -> None:
    for sort in SORTS:
        for span in TIME_SPANS:
            for limit in (1, 2, 9, 10, 99, 100):
                for subreddit in (None, "devops"):
                    url = build_search_url(
                        query="a", subreddit=subreddit, sort=sort, time=span, limit=limit
                    )
                    assert READER.authorize_url(url) == url


def test_the_closed_sets_are_the_readers_closed_sets() -> None:
    sort_rule = READER.SEARCH_QUERY["sort"]
    time_rule = READER.SEARCH_QUERY["t"]
    limit_rule = READER.SEARCH_QUERY["limit"]
    assert all(sort_rule.fullmatch(sort) for sort in SORTS)
    assert all(time_rule.fullmatch(span) for span in TIME_SPANS)
    for outside in ("old", "hot", "controversial", "confidence", "qa", ""):
        assert sort_rule.fullmatch(outside) is None
    for outside in ("decade", "", "today"):
        assert time_rule.fullmatch(outside) is None
    assert limit_rule.fullmatch(str(reddit_search.MAX_LIMIT))
    assert limit_rule.fullmatch(str(reddit_search.MAX_LIMIT + 1)) is None
    assert limit_rule.fullmatch(str(reddit_search.MIN_LIMIT))
    assert limit_rule.fullmatch(str(reddit_search.MIN_LIMIT - 1)) is None


def test_the_query_character_class_is_the_readers_class_character_by_character() -> None:
    """If the two classes differ, a query this module accepts is refused by the capsule (a 400 the
    agent cannot act on), or a query the capsule would refuse looks accepted here. The `a` in front
    keeps the reader's lookahead (a blank query) out of the comparison."""
    reader_rule = READER.SEARCH_QUERY["q"]
    for code in range(0x20, 0x3000):
        char = chr(code)
        ours = reddit_search._QUERY_CHARS.fullmatch(char) is not None
        theirs = reader_rule.fullmatch("a" + char) is not None
        assert ours == theirs, f"U+{code:04X} {char!r}: tu {ours}, czytnik {theirs}"
    assert str(MAX_QUERY_CHARS) in reader_rule.pattern, "długość zapytania to też kontrakt"
    assert reader_rule.fullmatch("a" * MAX_QUERY_CHARS)
    assert reader_rule.fullmatch("a" * (MAX_QUERY_CHARS + 1)) is None


def test_the_url_comes_out_in_the_form_the_reader_re_derives_and_in_a_fixed_order() -> None:
    url = build_search_url(
        query="deploy previews", subreddit="r/devops", sort="new", time="week", limit=25
    )
    assert url == (
        "https://www.reddit.com/r/devops/search.json?q=deploy+previews&restrict_sr=on"
        "&sort=new&t=week&limit=25&type=link&raw_json=1"
    )
    assert build_search_url(query="a b", subreddit=None, sort="top", time="day", limit=5) == (
        "https://www.reddit.com/search.json?q=a+b&sort=top&t=day&limit=5&type=link&raw_json=1"
    )


def test_a_query_is_folded_to_plain_text_before_it_is_checked() -> None:
    assert normalize_query("  deploy \n\t previews  ") == "deploy previews"
    folded = normalize_query(f"{LEFT_Q}slow{RIGHT_Q} {EM} {LEFT_SQ}ok{RIGHT_SQ} a{EN}b x{ELLIPSIS}")
    assert folded == "\"slow\" - 'ok' a-b x..."
    assert not LONG_DASHES & set(folded)


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        ("", "puste"),
        ("   \n\t ", "puste"),
        ("x" * (MAX_QUERY_CHARS + 1), "najwyżej"),
        ("find [this]", "[ ]"),
        ("100% sure", "%"),
        ("a|b", "|"),
        ("a=b", "="),
        ("<script>", "< >"),
        ("rocket \U0001f680", "\U0001f680"),
        ("back\\slash", "\\"),
        ("curly {braces}", "{ }"),
        ("tilde~", "~"),
    ],
)
def test_a_query_the_reader_would_refuse_is_a_value_error_that_says_why(
    query: str, fragment: str
) -> None:
    with pytest.raises(ValueError, match="Zapytanie") as excinfo:
        build_search_url(query=query, subreddit=None, sort="new", time="week", limit=25)
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize("query", [None, 5, b"abc", ["a"]])
def test_a_query_that_is_not_text_is_a_value_error(query: Any) -> None:
    with pytest.raises(ValueError, match="tekstem"):
        normalize_query(query)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sort": "hot"},
        {"sort": "old"},
        {"sort": ""},
        {"sort": None},
        {"time": "decade"},
        {"time": ""},
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": True},
        {"limit": "25"},
        {"limit": 2.5},
        {"limit": None},
        {"subreddit": "a"},
        {"subreddit": "x" * 22},
        {"subreddit": "dev ops"},
        {"subreddit": "devops/new"},
        {"subreddit": "r/"},
        {"subreddit": ""},
        {"subreddit": "a+b"},
        {"subreddit": "devops.json"},
        {"subreddit": 7},
    ],
)
def test_an_argument_outside_the_closed_sets_is_a_value_error_not_a_request(
    overrides: dict[str, Any],
) -> None:
    kwargs = {
        "query": "a",
        "subreddit": None,
        "sort": "new",
        "time": "week",
        "limit": 25,
    } | overrides
    with pytest.raises(ValueError):
        build_search_url(**kwargs)


def test_subreddit_spellings_mean_one_community() -> None:
    for spelling in ("devops", "r/devops", "R/devops", "/r/devops/", " r/devops "):
        assert normalize_subreddit(spelling) == "devops"
    assert normalize_subreddit("r/DevOps") == "DevOps", "the case Reddit gave is kept"


# -- 2. what a hit is ----------------------------------------------------------------------------


def _hits_from_fixture() -> list[RedditHit]:
    return [hit for c in _listing_children() if (hit := hit_from_child(c)) is not None]


def test_the_recorded_listing_yields_exactly_the_posts_worth_showing() -> None:
    hits = _hits_from_fixture()
    assert [hit.post_id for hit in hits] == ["1w6ln9w", "1w6bg37", "1w3gqu0", "1w4064j"]
    skipped = {c["data"].get("id") for c in _listing_children()} - {h.post_id for h in hits}
    assert {
        "1w630ci",  # the recorded stickied weekly thread
        "1w47fx7",  # the recorded stickied monthly thread
        "rmv0001",
        "rmv0002",  # removed by a moderator / text removed
        "del0001",
        "del0002",  # author deleted / text deleted
        "nsf0001",  # NSFW
        "ads0001",  # promoted
        "usr0001",  # a post on someone's profile
        "bad0001",  # no creation time
    } <= skipped
    assert "2xyz" in skipped, "the `t5` (a community, not a post) is skipped too"
    assert len(_listing_children()) == len(hits) + len(skipped), "every child is accounted for"


def test_a_hit_carries_what_the_agent_needs_to_decide_and_to_open_it() -> None:
    first, short, link, archived = _hits_from_fixture()
    assert first.subreddit == "kubernetes"
    assert first.title == "Kubernetes documentation assumes you already know Kubernetes"
    assert first.author == "redditor_001"
    assert first.url == (
        "https://www.reddit.com/r/kubernetes/comments/1w6ln9w/"
        "kubernetes_documentation_assumes_you_already_know/"
    )
    assert first.created_at == datetime(2026, 9, 3, 22, 2, 8, tzinfo=UTC)
    assert first.created_at.utcoffset().total_seconds() == 0
    assert (first.score, first.num_comments) == (42, 33)
    assert (first.locked, first.archived) == (False, False)
    # Excerpt: whitespace collapsed, about 300 characters, and honest about being cut.
    assert "\n" not in first.excerpt and "  " not in first.excerpt
    assert first.excerpt.endswith("...") and 300 < len(first.excerpt) <= 303
    assert short.excerpt == " ".join(short.excerpt.split()) and not short.excerpt.endswith("...")
    assert short.post_id == "1w6bg37" and short.num_comments == 6
    # A link post has no text; the no-break space of its title is plain whitespace now.
    assert link.excerpt == "" and "\xa0" not in link.title and "  " not in link.title
    assert (archived.locked, archived.archived) == (True, True), "kept, with its flags"


def test_every_returned_url_is_a_canonical_permalink_the_reader_can_read_back() -> None:
    for hit in _hits_from_fixture():
        assert hit.url.startswith(
            f"https://www.reddit.com/r/{hit.subreddit}/comments/{hit.post_id}/"
        )
        thread = f"https://www.reddit.com/r/{hit.subreddit}/comments/{hit.post_id}.json?raw_json=1"
        assert READER.authorize_url(thread) == thread


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"removed_by_category": "moderator"}, "removed by a moderator"),
        ({"removed_by_category": "deleted"}, "deleted by its author"),
        ({"removed_by_category": "automod_filtered"}, "held by AutoModerator"),
        ({"selftext": "[removed]"}, "text removed"),
        ({"selftext": " [deleted] "}, "text deleted, padded"),
        ({"author": "[deleted]"}, "author deleted"),
        ({"author": None}, "no author"),
        ({"author": ""}, "empty author"),
        ({"over_18": True}, "NSFW"),
        ({"stickied": True}, "stickied"),
        ({"promoted": True}, "an ad"),
        ({"subreddit_type": "user", "subreddit": "u_someone"}, "a profile post"),
        ({"id": ""}, "no id"),
        ({"id": "NOT-BASE36"}, "an id the reader would refuse"),
        ({"subreddit": ""}, "no subreddit"),
        ({"subreddit": "a"}, "a subreddit the reader would refuse"),
        ({"created_utc": None}, "no creation time"),
        ({"created_utc": "yesterday"}, "a creation time that is not a number"),
        ({"title": ""}, "no title"),
        ({"title": " \n "}, "a blank title"),
    ],
)
def test_each_skip_rule_works_on_its_own(change: dict[str, Any], why: str) -> None:
    good = _good_child()
    assert hit_from_child(good) is not None, "the unchanged child is a hit"
    good["data"].update(change)
    assert hit_from_child(good) is None, why


def test_a_child_that_is_not_a_post_or_has_no_data_is_ignored_not_an_error() -> None:
    assert hit_from_child({"kind": "t5", "data": {"display_name": "devops"}}) is None
    assert hit_from_child({"kind": "t1", "data": {"id": "abc"}}) is None
    assert hit_from_child({"kind": "t3"}) is None
    assert hit_from_child({"kind": "t3", "data": "nope"}) is None
    assert hit_from_child({}) is None


def test_a_missing_or_odd_number_is_zero_not_a_crash() -> None:
    child = _good_child()
    child["data"].update(score=None, num_comments="many")
    hit = hit_from_child(child)
    assert hit is not None and (hit.score, hit.num_comments) == (0, 0)
    child["data"].update(score=7.0, num_comments=True)
    hit = hit_from_child(child)
    assert hit is not None and (hit.score, hit.num_comments) == (7, 0)


def test_a_permalink_of_an_unexpected_shape_falls_back_to_the_canonical_one() -> None:
    child = _good_child()
    child["data"]["permalink"] = "https://evil.example/r/x/comments/1w6bg37/"
    hit = hit_from_child(child)
    assert hit is not None
    assert hit.url == "https://www.reddit.com/r/kubernetes/comments/1w6bg37/"
    child["data"].pop("permalink")
    assert hit_from_child(child).url == "https://www.reddit.com/r/kubernetes/comments/1w6bg37/"


# -- the fakes ------------------------------------------------------------------------------------


class FakeReader:
    """A `RedditReader` of the tests: records what it was asked and answers from a table."""

    def __init__(self, listing: Any = None, thread: Any = None) -> None:
        self.listing = listing
        self.thread = thread
        self.listing_calls: list[tuple[str, str]] = []
        self.thread_calls: list[tuple[str, str]] = []
        self.closed = 0

    async def read_listing(self, url: str, *, where: str) -> Any:
        self.listing_calls.append((url, where))
        if isinstance(self.listing, Exception):
            raise self.listing
        return self.listing

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        self.thread_calls.append((community_key, external_id))
        if isinstance(self.thread, Exception):
            raise self.thread
        return self.thread

    async def aclose(self) -> None:
        self.closed += 1


def _listing_payload() -> Any:
    return json.loads(_fixture("reddit_search_listing.json")["body"])


def _thread_payload() -> Any:
    return json.loads(_fixture("reddit_search_thread.json")["body"])


# -- search_reddit ---------------------------------------------------------------------------------


async def test_search_asks_the_reader_for_the_canonical_url_and_returns_the_hits() -> None:
    reader = FakeReader(listing=_listing_payload())
    hits = await search_reddit(
        query="docs assume",
        subreddit="r/kubernetes",
        sort="relevance",
        time="month",
        limit=10,
        reader=reader,
    )
    url = (
        "https://www.reddit.com/r/kubernetes/search.json?q=docs+assume&restrict_sr=on"
        "&sort=relevance&t=month&limit=10&type=link&raw_json=1"
    )
    assert reader.listing_calls == [(url, "/r/kubernetes/search.json")]
    assert READER.authorize_url(url) == url
    assert [hit.post_id for hit in hits] == ["1w6ln9w", "1w6bg37", "1w3gqu0", "1w4064j"]
    assert all(isinstance(hit, RedditHit) for hit in hits)


async def test_search_defaults_are_new_and_this_week_and_25() -> None:
    reader = FakeReader(listing=_listing_payload())
    await search_reddit(query="a", reader=reader)
    assert reader.listing_calls[0][0] == (
        "https://www.reddit.com/search.json?q=a&sort=new&t=week&limit=25&type=link&raw_json=1"
    )


async def test_an_empty_listing_is_an_empty_list_not_an_error() -> None:
    empty = {"kind": "Listing", "data": {"children": []}}
    assert await search_reddit(query="nothing matches", reader=FakeReader(listing=empty)) == []


async def test_a_listing_of_only_unwanted_posts_is_an_empty_list() -> None:
    payload = _listing_payload()
    payload["data"]["children"] = [
        c for c in payload["data"]["children"] if c["data"].get("id") in {"1w630ci", "nsf0001"}
    ]
    assert await search_reddit(query="a", reader=FakeReader(listing=payload)) == []


@pytest.mark.parametrize("payload", [{}, [], "text", None, {"kind": "Listing"}, {"kind": "t3"}])
async def test_an_answer_that_is_not_a_listing_is_unavailable_not_an_empty_result(
    payload: Any,
) -> None:
    """The whole point of the ladder (`source.py`): a read that did not happen raises, it never
    returns `[]`, because `[]` reads as "nobody is talking about this"."""
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(query="a", reader=FakeReader(listing=payload))
    assert excinfo.value.kind == UNAVAILABLE


async def test_bad_arguments_never_reach_the_reader() -> None:
    reader = FakeReader(listing=_listing_payload())
    for kwargs in ({"query": ""}, {"query": "a", "sort": "hot"}, {"query": "a", "limit": 500}):
        with pytest.raises(ValueError):
            await search_reddit(reader=reader, **kwargs)
    assert reader.listing_calls == []


async def test_an_injected_reader_is_not_closed_by_the_search() -> None:
    reader = FakeReader(listing=_listing_payload())
    await search_reddit(query="a", reader=reader)
    assert reader.closed == 0


async def test_a_reader_the_search_built_itself_is_closed_even_when_the_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[FakeReader] = []

    def build(env: Mapping[str, str] | None = None) -> FakeReader:
        built.append(
            FakeReader(listing=SourceUnavailable("Reddit odpowiedział 429 na /search.json"))
        )
        return built[0]

    monkeypatch.setattr(reddit_search, "reader_from_env", build)
    with pytest.raises(RedditSearchUnavailable):
        await search_reddit(query="a")
    assert len(built) == 1 and built[0].closed == 1

    built.clear()
    built_ok = FakeReader(listing=_listing_payload())
    monkeypatch.setattr(reddit_search, "reader_from_env", lambda env=None: built_ok)
    assert await search_reddit(query="a")
    assert built_ok.closed == 1


# -- the reader client comes from the environment ---------------------------------------------------


async def test_without_a_configured_capsule_search_says_so_in_polish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WENEXT_BROWSER_READER_URL", raising=False)
    monkeypatch.delenv("WENEXT_BROWSER_READER_TOKEN", raising=False)
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(query="a")
    assert excinfo.value.kind == NOT_CONFIGURED
    assert "w tym procesie" in str(excinfo.value), "ta sama fraza co NOT_CONFIGURED_HERE"
    assert "WENEXT_BROWSER_READER_URL" in excinfo.value.detail
    with pytest.raises(RedditSearchUnavailable) as thread:
        await read_reddit_thread("https://www.reddit.com/r/devops/comments/abc123/x/")
    assert thread.value.kind == NOT_CONFIGURED


async def test_the_default_reader_is_the_one_every_other_reddit_read_uses() -> None:
    assert reader_from_env({}) is None
    assert reader_from_env({"WENEXT_BROWSER_READER_URL": "  "}) is None
    reader = reader_from_env(
        {"WENEXT_BROWSER_READER_URL": "http://capsule:8765", "WENEXT_BROWSER_READER_TOKEN": "t"}
    )
    assert isinstance(reader, _CapsuleReader)
    source = reader._source
    assert isinstance(source, RedditBrowserSource)
    assert isinstance(source._reader, HttpBrowserReader)
    assert source._reader.base_url == "http://capsule:8765"
    await reader.aclose()


async def test_the_reader_url_must_still_be_one_of_our_containers() -> None:
    with pytest.raises(ValueError, match="D-027"):
        reader_from_env(
            {"WENEXT_BROWSER_READER_URL": "http://reddit.com", "WENEXT_BROWSER_READER_TOKEN": "t"}
        )


# -- 3. what each failure says: the adapter's real ladder -------------------------------------------


class FakePage:
    """A `BrowserReader` of the observer's port: answers each URL with a canned `PageRead`."""

    def __init__(self, answer: PageRead | dict[str, PageRead]) -> None:
        self.answer = answer
        self.urls: list[str] = []

    async def read(self, url: str) -> PageRead:
        self.urls.append(url)
        if isinstance(self.answer, PageRead):
            return self.answer
        return self.answer[url.split("?")[0]]


def _page(status: int, body: str = "", *, final: str | None = None) -> PageRead:
    return PageRead(
        status=status,
        final_url=final or "https://www.reddit.com/search.json",
        content_type="application/json",
        body=body,
    )


def _source(page: FakePage, *, require_login: bool = False) -> _CapsuleReader:
    return _CapsuleReader(RedditBrowserSource(page, require_login=require_login))


async def test_search_through_the_real_adapter_asks_for_nothing_but_the_search_url() -> None:
    """No `/api/me.json`: a public search does not need the account, and a logged-out capsule must
    not turn into a 'login required' for it."""
    page = FakePage(_page(200, _fixture("reddit_search_listing.json")["body"]))
    hits = await search_reddit(query="docs", reader=_source(page, require_login=True))
    assert [hit.post_id for hit in hits][:2] == ["1w6ln9w", "1w6bg37"]
    assert len(page.urls) == 1
    assert READER.authorize_url(page.urls[0]) == page.urls[0]
    assert "/search.json" in page.urls[0]


@pytest.mark.parametrize(
    ("page", "kind", "fragment"),
    [
        (_page(429), RATE_LIMITED, "429"),
        (_page(403, "<html>blocked</html>"), BLOCKED, "403"),
        (
            _page(403, json.dumps({"reason": "private", "error": 403})),
            LOGIN_REQUIRED,
            "zalogowania",
        ),
        (_page(403, json.dumps({"reason": "quarantined"})), LOGIN_REQUIRED, "zalogowania"),
        (
            _page(200, "{}", final="https://www.reddit.com/login/?dest=x"),
            LOGIN_REQUIRED,
            "zalogowania",
        ),
        (_page(404), NOT_FOUND, "społeczności"),
        (_page(500), UNAVAILABLE, "spróbuj ponownie"),
        (_page(503), UNAVAILABLE, "spróbuj ponownie"),
        (_page(200, "<html>not json</html>"), UNAVAILABLE, "spróbuj ponownie"),
        (_page(418, "teapot"), UNAVAILABLE, "spróbuj ponownie"),
    ],
)
async def test_what_reddit_answers_becomes_a_kind_and_a_polish_sentence(
    page: PageRead, kind: str, fragment: str
) -> None:
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(query="a", subreddit="devops", reader=_source(FakePage(page)))
    error = excinfo.value
    assert error.kind == kind
    assert fragment in str(error)
    assert str(error) == error.reason and error.detail, "the adapter's own words stay in `detail`"


async def test_an_unknown_community_is_named_in_the_sentence() -> None:
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(
            query="a", subreddit="r/nosuchcommunity", reader=_source(FakePage(_page(404)))
        )
    assert excinfo.value.kind == NOT_FOUND and "r/nosuchcommunity" in str(excinfo.value)


def _http_reader(handler: Any) -> _CapsuleReader:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reader = HttpBrowserReader("http://capsule:8765", token="t", client=client)
    return _CapsuleReader(RedditBrowserSource(reader, require_login=False))


def _wire(read_status: int, read_body: Any, *, token_ok: bool = True) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json={"mode": "INTERACTIVE"})
        assert request.url.path == "/read"
        if not token_ok:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(read_status, json=read_body)

    return handler


@pytest.mark.parametrize(
    ("handler", "kind"),
    [
        # An older capsule image: the reader refuses the search URL with a 400 `url_refused`.
        (
            _wire(
                400, {"error": "url_refused", "detail": "ścieżka poza allowlistą: '/search.json'"}
            ),
            REFUSED,
        ),
        # The capsule answers, and says the token is wrong: not "down", and not a retry.
        (_wire(200, {}, token_ok=False), MISCONFIGURED),
        (_wire(502, {"error": "browser_unavailable", "detail": "CDP"}), CAPSULE_DOWN),
        (_wire(504, {"error": "browser_timeout", "detail": "za długo"}), CAPSULE_DOWN),
        # The reader's own brake after a 429 answers 200 with `status: 429` in the body.
        (
            _wire(
                200,
                {"status": 429, "final_url": "u", "content_type": None, "body": "", "paced": True},
            ),
            RATE_LIMITED,
        ),
        (_wire(200, {"status": 200, "final_url": "u", "content_type": None}), CAPSULE_DOWN),
    ],
)
async def test_what_the_capsule_answers_becomes_a_kind_through_the_real_http_client(
    handler: Any, kind: str
) -> None:
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(query="a", reader=_http_reader(handler))
    assert excinfo.value.kind == kind, excinfo.value.detail


async def test_a_capsule_that_is_down_is_a_kind_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await search_reddit(query="a", reader=_http_reader(handler))
    assert excinfo.value.kind == CAPSULE_DOWN
    assert "nie odpowiada" in str(excinfo.value)


async def test_a_search_url_the_capsule_accepts_comes_back_as_hits_through_the_wire() -> None:
    body = _fixture("reddit_search_listing.json")["body"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/state":
            return httpx.Response(200, json={"mode": "INTERACTIVE"})
        seen.append(request.url.params["url"])
        assert request.headers["X-Wenext-Reader-Token"] == "t"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "final_url": seen[-1],
                "content_type": "application/json",
                "body": body,
                "viewer": "session",
            },
        )

    hits = await search_reddit(query="docs", subreddit="kubernetes", reader=_http_reader(handler))
    assert len(hits) == 4
    assert READER.authorize_url(seen[0]) == seen[0], "the capsule would accept exactly this URL"


def test_every_kind_has_one_polish_sentence_without_long_dashes() -> None:
    assert set(UNAVAILABLE_KINDS) == {
        NOT_CONFIGURED,
        REFUSED,
        CAPSULE_DOWN,
        MISCONFIGURED,
        RATE_LIMITED,
        BLOCKED,
        LOGIN_REQUIRED,
        NOT_FOUND,
        UNAVAILABLE,
    }
    # `not_found` is the one kind whose sentence names the community, so it is composed, not listed.
    assert set(reddit_search._REASONS) == set(UNAVAILABLE_KINDS) - {NOT_FOUND}
    for kind, sentence in reddit_search._REASONS.items():
        assert kind in UNAVAILABLE_KINDS
        assert sentence.endswith(".") and "\n" not in sentence
        assert sentence.count(". ") == 0, f"{kind}: one sentence"
        assert not LONG_DASHES & set(sentence)
        assert "Reddit" in sentence or "kapsuł" in sentence
    with pytest.raises(ValueError):
        RedditSearchUnavailable("x", kind="not-a-kind")
    error = RedditSearchUnavailable("Zdanie.", kind=RATE_LIMITED, detail="d")
    assert str(error) == "Zdanie." and error.kind == RATE_LIMITED and error.detail == "d"
    assert RedditSearchUnavailable("Zdanie.").kind == UNAVAILABLE


# -- 4. the thread ---------------------------------------------------------------------------------

PERMALINK = "https://www.reddit.com/r/kubernetes/comments/1w6ln9w/kubernetes_documentation/"


async def test_a_thread_is_read_from_its_permalink_with_post_state_and_top_level_comments() -> None:
    reader = FakeReader(thread=_thread_payload())
    thread = await read_reddit_thread(PERMALINK, reader)
    assert reader.thread_calls == [("r/kubernetes", "1w6ln9w")]
    assert isinstance(thread, RedditThread)
    assert thread.post_id == "1w6ln9w" and thread.subreddit == "kubernetes"
    assert thread.title == "Kubernetes documentation assumes you already know Kubernetes"
    assert thread.body.startswith("I") and "Kubernetes" in thread.body
    assert thread.author == "redditor_001"
    assert thread.url == (
        "https://www.reddit.com/r/kubernetes/comments/1w6ln9w/"
        "kubernetes_documentation_assumes_you_already_know/"
    )
    assert thread.created_at == datetime(2026, 9, 3, 22, 2, 8, tzinfo=UTC)
    assert (thread.score, thread.num_comments) == (42, 33)
    assert (thread.locked, thread.archived) == (False, False)
    assert [c.comment_id for c in thread.comments] == [
        "p7nyymw",
        "p7nz4bp",
        "p7nz50c",
        "p7nzyax",
        "p7o18az",
        "hid0001",
    ]
    assert {c.depth for c in thread.comments} == {0}
    assert thread.comments_truncated is True, "the fixture ends with a `more` stub"
    by_id = {c.comment_id: c for c in thread.comments}
    assert by_id["p7nz4bp"].is_op is True and by_id["p7nyymw"].is_op is False
    assert by_id["p7nz50c"].score == 32 and by_id["p7nzyax"].score == -5
    assert by_id["hid0001"].score is None, "a hidden score is None, not a number"
    assert "rm00001" not in by_id, "a removed comment has no text to show"
    assert all(c.created_at.tzinfo is not None for c in thread.comments)
    assert by_id["p7nyymw"].author == "redditor_022"
    assert all(len(c.excerpt) <= 403 and "\n" not in c.excerpt for c in thread.comments)


async def test_replies_come_with_their_depth_when_asked_for() -> None:
    payload = _thread_payload()
    one = await read_reddit_thread(
        PERMALINK, FakeReader(thread=payload), max_depth=1, max_comments=100
    )
    assert {c.depth for c in one.comments} == {0, 1}
    ids = [c.comment_id for c in one.comments]
    assert ids.index("p7o0f48") == ids.index("p7nyymw") + 1, "a reply follows its parent"
    assert "p7o16hp" not in ids, "depth 2 is beyond max_depth=1"
    deep = await read_reddit_thread(
        PERMALINK, FakeReader(thread=payload), max_depth=3, max_comments=100
    )
    assert {c.depth for c in deep.comments} == {0, 1, 2, 3}
    assert "p7o4tin" in {c.comment_id for c in deep.comments}
    assert all(c.depth <= 3 for c in deep.comments)


async def test_more_comments_than_asked_keeps_the_best_in_the_order_they_were_written() -> None:
    thread = await read_reddit_thread(
        PERMALINK, FakeReader(thread=_thread_payload()), max_comments=2
    )
    assert [c.comment_id for c in thread.comments] == ["p7nz4bp", "p7nz50c"], "scores 22 and 32"
    assert thread.comments_truncated is True
    all_of_them = await read_reddit_thread(
        PERMALINK, FakeReader(thread=_thread_payload()), max_comments=100
    )
    assert len(all_of_them.comments) == 6


async def test_a_thread_without_a_more_stub_that_fits_is_not_truncated() -> None:
    payload = _thread_payload()
    payload[1]["data"]["children"] = [
        c for c in payload[1]["data"]["children"] if c["kind"] == "t1"
    ]
    thread = await read_reddit_thread(PERMALINK, FakeReader(thread=payload), max_comments=100)
    assert thread.comments_truncated is False


async def test_a_thread_whose_text_is_gone_has_an_empty_body_not_a_placeholder() -> None:
    payload = _thread_payload()
    payload[0]["data"]["children"][0]["data"]["selftext"] = "[removed]"
    payload[0]["data"]["children"][0]["data"]["author"] = "[deleted]"
    thread = await read_reddit_thread(PERMALINK, FakeReader(thread=payload))
    assert thread.body == "" and thread.author is None


async def test_a_long_post_is_cut_with_an_ellipsis() -> None:
    payload = _thread_payload()
    payload[0]["data"]["children"][0]["data"]["selftext"] = "word " * 5000
    thread = await read_reddit_thread(PERMALINK, FakeReader(thread=payload))
    assert thread.body.endswith("...") and len(thread.body) <= reddit_search.THREAD_BODY_CHARS + 3


async def test_locked_and_archived_are_reported_because_they_decide_whether_to_reply() -> None:
    payload = _thread_payload()
    data = payload[0]["data"]["children"][0]["data"]
    data.update(locked=True, archived=True)
    thread = await read_reddit_thread(PERMALINK, FakeReader(thread=payload))
    assert (thread.locked, thread.archived) == (True, True)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        [{"kind": "Listing", "data": {"children": []}}],
        [
            {"kind": "Listing", "data": {"children": []}},
            {"kind": "Listing", "data": {"children": []}},
        ],
    ],
)
async def test_a_payload_that_holds_no_post_is_unavailable_never_an_empty_thread(
    payload: Any,
) -> None:
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await read_reddit_thread(PERMALINK, FakeReader(thread=payload))
    assert excinfo.value.kind in {UNAVAILABLE, NOT_FOUND}


@pytest.mark.parametrize(
    ("text", "subreddit", "expected"),
    [
        (
            "https://www.reddit.com/r/kubernetes/comments/1w6ln9w/kubernetes_documentation/",
            None,
            ("kubernetes", "1w6ln9w"),
        ),
        ("https://www.reddit.com/r/kubernetes/comments/1w6ln9w/", None, ("kubernetes", "1w6ln9w")),
        ("https://www.reddit.com/r/kubernetes/comments/1w6ln9w", None, ("kubernetes", "1w6ln9w")),
        (
            "https://old.reddit.com/r/Kubernetes/comments/1w6ln9w/x/",
            None,
            ("Kubernetes", "1w6ln9w"),
        ),
        (
            "https://reddit.com/r/kubernetes/comments/1w6ln9w/x/?utm_source=share#c",
            None,
            ("kubernetes", "1w6ln9w"),
        ),
        ("http://www.reddit.com/r/kubernetes/comments/1w6ln9w/x/", None, ("kubernetes", "1w6ln9w")),
        ("www.reddit.com/r/kubernetes/comments/1w6ln9w/x/", None, ("kubernetes", "1w6ln9w")),
        (
            "  https://www.reddit.com/r/kubernetes/comments/1w6ln9w/x/  ",
            None,
            ("kubernetes", "1w6ln9w"),
        ),
        # The permalink wins over a `subreddit=` that disagrees with it: the address is the fact.
        (
            "https://www.reddit.com/r/kubernetes/comments/1w6ln9w/x/",
            "devops",
            ("kubernetes", "1w6ln9w"),
        ),
        ("1w6ln9w", "kubernetes", ("kubernetes", "1w6ln9w")),
        ("t3_1w6ln9w", "r/kubernetes", ("kubernetes", "1w6ln9w")),
        (" 1w6ln9w ", "/r/kubernetes/", ("kubernetes", "1w6ln9w")),
    ],
)
def test_a_thread_reference_is_a_permalink_or_an_id_with_its_community(
    text: str, subreddit: str | None, expected: tuple[str, str]
) -> None:
    assert parse_thread_reference(text, subreddit) == expected


@pytest.mark.parametrize(
    ("text", "subreddit"),
    [
        ("", None),
        ("   ", None),
        (None, None),
        (7, None),
        ("1w6ln9w", None),  # an id alone cannot be read: the capsule needs the community
        ("t3_1w6ln9w", None),
        ("1W6LN9W", "kubernetes"),  # base36 is lower case
        ("1w6ln9w-x", "kubernetes"),
        ("1w6ln9wabcdef", "kubernetes"),  # more than 12 characters
        ("1w6ln9w", "a"),
        ("1w6ln9w", "dev ops"),
        ("https://evil.example/r/kubernetes/comments/1w6ln9w/x/", None),
        ("https://www.reddit.com.evil.example/r/kubernetes/comments/1w6ln9w/x/", None),
        ("https://www.reddit.com@evil.example/r/kubernetes/comments/1w6ln9w/x/", None),
        ("ftp://www.reddit.com/r/kubernetes/comments/1w6ln9w/x/", None),
        ("https://www.reddit.com/r/kubernetes/", None),
        ("https://www.reddit.com/r/kubernetes/new/", None),
        ("https://www.reddit.com/user/someone/comments/1w6ln9w/x/", None),
        ("https://www.reddit.com/r/kubernetes/comments/", None),
        ("https://www.reddit.com/r/kubernetes/comments/ABC/x/", None),
        ("https://www.reddit.com/comments/1w6ln9w/", None),
        ("https://redd.it/1w6ln9w", None),
    ],
)
def test_anything_else_as_a_thread_reference_is_a_value_error_that_names_the_fix(
    text: Any, subreddit: str | None
) -> None:
    with pytest.raises(ValueError):
        parse_thread_reference(text, subreddit)


def test_an_id_with_no_community_is_told_to_give_the_address_or_the_community() -> None:
    with pytest.raises(ValueError, match="pełny adres wątku albo parametr subreddit"):
        parse_thread_reference("1w6ln9w")


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_comments": 0},
        {"max_comments": 101},
        {"max_comments": True},
        {"max_comments": "5"},
        {"max_depth": -1},
        {"max_depth": 4},
        {"max_depth": True},
        {"max_depth": 1.5},
    ],
)
async def test_thread_limits_outside_their_range_never_reach_the_reader(
    overrides: dict[str, Any],
) -> None:
    reader = FakeReader(thread=_thread_payload())
    with pytest.raises(ValueError):
        await read_reddit_thread(PERMALINK, reader, **overrides)
    assert reader.thread_calls == []


async def test_a_bare_id_is_read_with_its_community_and_the_reader_is_not_closed() -> None:
    reader = FakeReader(thread=_thread_payload())
    thread = await read_reddit_thread("t3_1w6ln9w", reader, subreddit="r/kubernetes")
    assert reader.thread_calls == [("r/kubernetes", "1w6ln9w")] and thread.post_id == "1w6ln9w"
    assert reader.closed == 0


async def test_a_thread_through_the_real_adapter_uses_the_url_the_capsule_allows() -> None:
    page = FakePage(_page(200, json.dumps(_thread_payload())))
    thread = await read_reddit_thread(PERMALINK, _source(page))
    assert thread.comments
    assert len(page.urls) == 1
    assert READER.authorize_url(page.urls[0]) == page.urls[0], "the observer's own thread URL"
    assert page.urls[0].startswith("https://www.reddit.com/r/kubernetes/comments/1w6ln9w.json")


@pytest.mark.parametrize(
    ("page", "kind", "fragment"),
    [
        (_page(404), NOT_FOUND, "wątku"),
        (_page(429), RATE_LIMITED, "429"),
        (_page(403, json.dumps({"reason": "private"})), LOGIN_REQUIRED, "zalogowania"),
        (_page(403, "<html></html>"), BLOCKED, "403"),
    ],
)
async def test_a_thread_that_cannot_be_read_says_why(
    page: PageRead, kind: str, fragment: str
) -> None:
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await read_reddit_thread(PERMALINK, _source(FakePage(page)))
    assert excinfo.value.kind == kind and fragment in str(excinfo.value)


async def test_reading_a_thread_still_needs_the_logged_in_account_the_observer_needs() -> None:
    """`read_thread_payload` is the observer's own call and keeps its rule: with login required and
    no session in the capsule it refuses, and says so as a login the human has to do."""
    logged_out = FakePage(
        {
            "https://www.reddit.com/api/me.json": _page(200, "{}"),
            "https://www.reddit.com/r/kubernetes/comments/1w6ln9w.json": _page(
                200, json.dumps(_thread_payload())
            ),
        }
    )
    with pytest.raises(RedditSearchUnavailable) as excinfo:
        await read_reddit_thread(PERMALINK, _source(logged_out, require_login=True))
    assert excinfo.value.kind == LOGIN_REQUIRED
    assert not any("comments" in url for url in logged_out.urls), (
        "the thread was not even asked for"
    )


# -- the module itself ------------------------------------------------------------------------------


def test_the_module_calls_no_writing_method_and_carries_no_long_dash() -> None:
    import ast

    path = pathlib.Path(reddit_search.__file__)
    source = path.read_text(encoding="utf-8")
    assert not LONG_DASHES & set(source), "U+2014 i U+2013 są zakazane w tym repozytorium"
    forbidden = {
        "post",
        "put",
        "patch",
        "delete",
        "click",
        "fill",
        "press",
        "type",
        "select_option",
        "set_input_files",
        "submit",
        "evaluate",
        "evaluate_handle",
        "insert_text",
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden, (
                f"reddit_search.py:{node.lineno} .{node.func.attr}()"
            )


def test_the_public_names_are_all_bound() -> None:
    for name in reddit_search.__all__:
        assert hasattr(reddit_search, name), name
