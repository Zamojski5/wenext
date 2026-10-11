"""`RedditBrowserSource` — the session rule first, the transport second, the parser third.

Two layers, tested separately:

* `RedditBrowserSource` against `FakeReader`, a `BrowserReader` that answers from a dict keyed by
  URL path — what Reddit's `.json` views send to a real Chrome. The shapes in `_link`/`_comment`
  are the Data API's documented ones (identical on both transports, `reddit_payload.py`);
  `test_reddit_browser_recorded.py` runs the same parser on bodies RECORDED through the capsule.
* `HttpBrowserReader` against `httpx.MockTransport` standing in for the capsule's reader.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from community_observer import (
    READER_TOKEN_HEADER,
    BrowserReader,
    CommunitySource,
    CredentialsMissing,
    HttpBrowserReader,
    PageRead,
    RedditBrowserSource,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
    build_reddit_browser_source,
)
from community_os_contracts import AccessMode, ObservationPlatform
from tests.conftest import FROZEN_NOW, frozen_clock

WWW = "https://www.reddit.com"

#: Mirrors of the reader's allowlist (`browser_supervisor/reader.py`). The two packages do not
#: import each other, so the adapter's URLs are checked here against the same three shapes — a
#: drift on either side fails a test on that side.
ALLOWED_PATHS = (
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/new\.json$"),
    re.compile(r"^/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{1,12}\.json$"),
    re.compile(r"^/api/me\.json$"),
)
ALLOWED_QUERY = {
    "raw_json": re.compile(r"^1$"),
    "limit": re.compile(r"^([1-9][0-9]{0,2})$"),
    "depth": re.compile(r"^([1-9]|10)$"),
    "sort": re.compile(r"^(old|new|top|confidence|controversial|qa)$"),
}


def _link(**overrides) -> dict:
    data = {
        "id": "abc123",
        "name": "t3_abc123",
        "title": "How do you run migrations in CI?",
        "selftext": "We use a shadow database.",
        "author": "someone",
        "permalink": "/r/devops/comments/abc123/how_do_you_run_migrations_in_ci/",
        "url": "https://www.reddit.com/r/devops/comments/abc123/how_do_you_run_migrations_in_ci/",
        "is_self": True,
        "created_utc": 1788433696.0,
        "score": 12,
        "hide_score": False,
        "num_comments": 3,
    }
    data.update(overrides)
    return {"kind": "t3", "data": data}


def _comment(cid: str, body: str, replies: list | None = None, author: str = "alice") -> dict:
    data = {"id": cid, "author": author, "body": body, "created_utc": 1788433800.0}
    data["replies"] = {"kind": "Listing", "data": {"children": replies}} if replies else ""
    return {"kind": "t1", "data": data}


def _listing(children: list) -> dict:
    return {"kind": "Listing", "data": {"children": children, "after": None}}


LOGGED_IN = {"name": "founder", "id": "abc", "created_utc": 1600000000.0}
ANONYMOUS: dict = {}


class FakeReader:
    """Answers by URL path; records every URL the adapter asked for."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object]] = {"/api/me.json": (200, LOGGED_IN)}
        self.requests: list[str] = []
        self.redirect_to_login = False

    async def read(self, url: str) -> PageRead:
        self.requests.append(url)
        path = urlsplit(url).path
        if self.redirect_to_login:
            return PageRead(200, f"{WWW}/login/?dest={url}", "text/html", "<html>login</html>")
        status, body = self.routes.get(path, (404, {"message": "Not Found", "error": 404}))
        text = body if isinstance(body, str) else json.dumps(body)
        return PageRead(status, url, "application/json; charset=UTF-8", text)


class PreparingReader(FakeReader):
    """A reader with the capsule's mode surface (D-026): `prepare()` is what the adapter calls."""

    def __init__(self, mode: str = "HEADLESS") -> None:
        super().__init__()
        self.mode = mode
        self.prepare_calls = 0

    async def prepare(self) -> dict:
        self.prepare_calls += 1
        self.mode = "INTERACTIVE"
        return {"mode": self.mode, "display_only": True, "human_steer_open": False}


def _source(reader: FakeReader, **kwargs) -> RedditBrowserSource:
    kwargs.setdefault("clock", frozen_clock)
    return RedditBrowserSource(reader, **kwargs)


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


# -- the port -------------------------------------------------------------------


def test_the_adapter_satisfies_the_port_and_declares_account_required(reader: FakeReader) -> None:
    source = _source(reader)
    assert isinstance(source, CommunitySource)
    assert isinstance(reader, BrowserReader)
    assert source.platform is ObservationPlatform.REDDIT
    assert source.access_mode is AccessMode.ACCOUNT_REQUIRED
    assert source.require_login is True


def test_describe_requires_the_r_prefix(reader: FakeReader) -> None:
    source = _source(reader)
    assert source.describe("r/devops").ref == "REDDIT:r/devops"
    assert source.describe("r/devops").display_name == "Reddit · r/devops"
    for bad in ("devops", "ask", "r/", "r/with space", "/r/devops"):
        with pytest.raises(UnknownCommunity):
            source.describe(bad)
    assert reader.requests == [], "describe nie dotyka przeglądarki"


def test_the_token_header_matches_the_reader() -> None:
    assert READER_TOKEN_HEADER == "X-Wenext-Reader-Token"


# -- the session rule -------------------------------------------------------------


async def test_an_anonymous_capsule_session_parks_every_read_and_never_returns_empty(
    reader: FakeReader,
) -> None:
    reader.routes["/api/me.json"] = (200, ANONYMOUS)
    reader.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    source = _source(reader)
    with pytest.raises(CredentialsMissing, match="nie jest zalogowany"):
        await source.list_recent_threads("r/devops", since=None, limit=10)
    with pytest.raises(CredentialsMissing, match="WENEXT_REDDIT_REQUIRE_LOGIN"):
        await source.fetch_thread("r/devops", "abc123")
    assert all(urlsplit(u).path == "/api/me.json" for u in reader.requests), (
        "bez zalogowanej sesji adapter nie czyta listingów — sprawdza tylko sesję"
    )


async def test_a_logged_in_session_is_checked_once_per_ttl(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    source = _source(reader)
    await source.list_recent_threads("r/devops", since=None, limit=10)
    await source.list_recent_threads("r/devops", since=None, limit=10)
    me_reads = [u for u in reader.requests if urlsplit(u).path == "/api/me.json"]
    assert len(me_reads) == 1, "pozytywny wynik `/api/me.json` jest buforowany do TTL"


async def test_the_session_check_is_repeated_after_the_ttl(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    now = {"t": FROZEN_NOW}
    source = RedditBrowserSource(
        reader, clock=lambda: now["t"], login_check_ttl=timedelta(minutes=10)
    )
    await source.list_recent_threads("r/devops", since=None, limit=10)
    now["t"] = FROZEN_NOW + timedelta(minutes=11)
    await source.list_recent_threads("r/devops", since=None, limit=10)
    me_reads = [u for u in reader.requests if urlsplit(u).path == "/api/me.json"]
    assert len(me_reads) == 2


async def test_require_login_false_reads_anonymously_and_says_nothing_about_the_session(
    reader: FakeReader,
) -> None:
    reader.routes["/api/me.json"] = (200, ANONYMOUS)
    reader.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    source = _source(reader, require_login=False)
    threads = await source.list_recent_threads("r/devops", since=None, limit=10)
    assert len(threads) == 1
    assert all(urlsplit(u).path != "/api/me.json" for u in reader.requests)


async def test_session_state_reports_the_account_or_its_absence(reader: FakeReader) -> None:
    state = await _source(reader).session_state()
    assert state.logged_in and state.username == "founder" and state.checked_at == FROZEN_NOW
    reader.routes["/api/me.json"] = (200, ANONYMOUS)
    state = await _source(reader).session_state()
    assert not state.logged_in and state.username is None


async def test_a_redirect_to_the_login_page_is_a_missing_session(reader: FakeReader) -> None:
    reader.redirect_to_login = True
    with pytest.raises(CredentialsMissing, match="logowania"):
        await _source(reader, require_login=False).list_recent_threads(
            "r/devops", since=None, limit=10
        )


# -- listing and thread -----------------------------------------------------------


async def test_listing_asks_for_an_allowlisted_url_and_parses_links(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (
        200,
        _listing([_link(), _link(id="def456", hide_score=True, score=0)]),
    )
    threads = await _source(reader).list_recent_threads("r/devops", since=None, limit=25)

    listing_url = next(u for u in reader.requests if "/new.json" in u)
    parts = urlsplit(listing_url)
    assert parts.scheme == "https" and parts.netloc == "www.reddit.com"
    assert parts.path == "/r/devops/new.json"
    assert dict(parse_qsl(parts.query)) == {"raw_json": "1", "limit": "25"}

    assert len(threads) == 2
    first = threads[0]
    assert first.ref == "REDDIT:r/devops:abc123"
    assert first.url.endswith("/comments/abc123/how_do_you_run_migrations_in_ci/")
    assert first.link_url is None and first.body == "We use a shadow database."
    assert first.metrics.score.require() == 12
    assert first.observed_at == FROZEN_NOW
    assert not threads[1].metrics.score.is_known, "hide_score → UNKNOWN, nie 0"


async def test_listing_applies_the_watermark_client_side(reader: FakeReader) -> None:
    older = _link(id="old1", created_utc=1788000000.0)
    newer = _link(id="new1", created_utc=1788500000.0)
    reader.routes["/r/devops/new.json"] = (200, _listing([newer, older]))
    since = datetime.fromtimestamp(1788400000.0, tz=UTC)
    threads = await _source(reader).list_recent_threads("r/devops", since=since, limit=25)
    assert [t.external_id for t in threads] == ["new1"]


async def test_listing_validates_its_arguments_before_touching_the_browser(
    reader: FakeReader,
) -> None:
    source = _source(reader)
    with pytest.raises(ValueError, match="limit"):
        await source.list_recent_threads("r/devops", since=None, limit=0)
    with pytest.raises(ValueError, match="strefę"):
        await source.list_recent_threads("r/devops", since=datetime(2026, 1, 1), limit=5)
    with pytest.raises(UnknownCommunity):
        await source.list_recent_threads("devops", since=None, limit=5)
    assert reader.requests == []


async def test_fetch_thread_asks_for_the_comment_tree_and_flattens_it(reader: FakeReader) -> None:
    comments = _listing(
        [
            _comment(
                "c1", "top", replies=[_comment("c2", "reply", replies=[_comment("c3", "deep")])]
            ),
            _comment("c4", "another top", author="[deleted]"),
            {"kind": "more", "data": {"count": 17, "children": ["c5", "c6"]}},
        ]
    )
    reader.routes["/r/devops/comments/abc123.json"] = (200, [_listing([_link()]), comments])
    snapshot = await _source(reader).fetch_thread("r/devops", "abc123")

    thread_url = next(u for u in reader.requests if "/comments/" in u)
    parts = urlsplit(thread_url)
    assert parts.path == "/r/devops/comments/abc123.json"
    assert dict(parse_qsl(parts.query)) == {
        "raw_json": "1",
        "limit": "500",
        "depth": "10",
        "sort": "old",
    }
    assert [c.external_id for c in snapshot.comments] == ["c1", "c2", "c3", "c4"]
    assert [c.depth for c in snapshot.comments] == [0, 1, 2, 0]
    assert snapshot.comments[3].author is None
    assert snapshot.comments_truncated, "stub `more` = drzewo obcięte, i to jest zapisane"


async def test_every_url_the_adapter_builds_is_one_the_reader_allows(reader: FakeReader) -> None:
    reader.routes["/r/Kubernetes_ops/new.json"] = (200, _listing([_link()]))
    reader.routes["/r/Kubernetes_ops/comments/abc123.json"] = (
        200,
        [_listing([_link()]), _listing([])],
    )
    source = _source(reader)
    await source.list_recent_threads("r/Kubernetes_ops", since=None, limit=100)
    await source.fetch_thread("r/Kubernetes_ops", "abc123")
    await source.session_state()
    assert len(reader.requests) >= 3
    for url in reader.requests:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc == "www.reddit.com" and not parts.fragment
        assert any(p.match(parts.path) for p in ALLOWED_PATHS), url
        for key, value in parse_qsl(parts.query):
            assert ALLOWED_QUERY[key].match(value), url


# -- error mapping ------------------------------------------------------------------


async def test_a_missing_subreddit_is_unknown_and_a_missing_post_is_not_found(
    reader: FakeReader,
) -> None:
    source = _source(reader)
    with pytest.raises(UnknownCommunity, match="404"):
        await source.list_recent_threads("r/nosuchsub", since=None, limit=5)
    with pytest.raises(ThreadNotFound):
        await source.fetch_thread("r/devops", "zzz999")
    with pytest.raises(ThreadNotFound):
        await source.fetch_thread("r/devops", "NOT_BASE36!")


async def test_a_private_or_quarantined_subreddit_is_a_missing_capability(
    reader: FakeReader,
) -> None:
    for reason in ("private", "quarantined", "gold_only"):
        reader.routes["/r/secret/new.json"] = (403, {"reason": reason, "message": "Forbidden"})
        with pytest.raises(CredentialsMissing, match=reason):
            await _source(reader).list_recent_threads("r/secret", since=None, limit=5)


async def test_a_403_without_a_reason_is_transient_and_named_as_a_possible_block(
    reader: FakeReader,
) -> None:
    reader.routes["/r/devops/new.json"] = (403, "<html>blocked</html>")
    with pytest.raises(SourceUnavailable, match="403"):
        await _source(reader).list_recent_threads("r/devops", since=None, limit=5)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_platform_error_is_never_an_empty_listing(reader: FakeReader, status: int) -> None:
    reader.routes["/r/devops/new.json"] = (status, {"message": "Too Many Requests"})
    with pytest.raises(SourceUnavailable, match=str(status)):
        await _source(reader).list_recent_threads("r/devops", since=None, limit=5)


async def test_html_where_json_was_expected_is_named_as_such(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (200, "<!doctype html><html>…</html>")
    with pytest.raises(SourceUnavailable, match="HTML"):
        await _source(reader).list_recent_threads("r/devops", since=None, limit=5)


async def test_a_listing_that_is_not_a_listing_is_never_empty(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (200, {"kind": "t3", "data": {}})
    with pytest.raises(SourceUnavailable, match="Listing"):
        await _source(reader).list_recent_threads("r/devops", since=None, limit=5)


# -- HttpBrowserReader: the wire to the capsule ----------------------------------------


class _Capsule:
    """The reader's HTTP surface, as the adapter sees it."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.payload: object = {
            "status": 200,
            "final_url": f"{WWW}/api/me.json?raw_json=1",
            "content_type": "application/json",
            "body": "{}",
        }
        self.raise_with: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_with is not None:
            raise self.raise_with
        if isinstance(self.payload, str):
            return httpx.Response(self.status, text=self.payload)
        return httpx.Response(self.status, json=self.payload)

    def reader(self, token: str = "tok") -> HttpBrowserReader:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return HttpBrowserReader("http://capsule:8765/", token=token, client=client)


@pytest.fixture
def capsule() -> _Capsule:
    return _Capsule()


async def test_the_http_reader_sends_the_url_and_the_token_and_returns_the_page(
    capsule: _Capsule,
) -> None:
    page = await capsule.reader().read(f"{WWW}/api/me.json?raw_json=1")
    (request,) = capsule.requests
    assert (
        request.url.scheme == "http" and request.url.host == "capsule" and request.url.port == 8765
    )
    assert request.url.path == "/read"
    assert request.url.params["url"] == f"{WWW}/api/me.json?raw_json=1"
    assert request.headers[READER_TOKEN_HEADER] == "tok"
    assert page == PageRead(200, f"{WWW}/api/me.json?raw_json=1", "application/json", "{}")


async def test_a_rejected_token_names_the_variable(capsule: _Capsule) -> None:
    capsule.status, capsule.payload = 401, {"error": "token_rejected"}
    with pytest.raises(SourceUnavailable, match="WENEXT_BROWSER_READER_TOKEN"):
        await capsule.reader().read(f"{WWW}/api/me.json")


async def test_a_url_the_reader_refuses_is_a_visible_failure_not_an_empty_page(
    capsule: _Capsule,
) -> None:
    capsule.status, capsule.payload = (
        400,
        {"error": "url_refused", "detail": "ścieżka poza allowlistą"},
    )
    with pytest.raises(SourceUnavailable, match="allowlist"):
        await capsule.reader().read(f"{WWW}/r/devops/hot.json")


async def test_an_unreachable_capsule_is_transient_and_names_the_address(capsule: _Capsule) -> None:
    capsule.raise_with = httpx.ConnectError("connection refused")
    with pytest.raises(SourceUnavailable, match="capsule:8765"):
        await capsule.reader().read(f"{WWW}/api/me.json")


@pytest.mark.parametrize("status", [502, 504, 500])
async def test_reader_errors_are_transient(capsule: _Capsule, status: int) -> None:
    capsule.status, capsule.payload = status, {"error": "browser_unavailable", "detail": "CDP"}
    with pytest.raises(SourceUnavailable, match=str(status)):
        await capsule.reader().read(f"{WWW}/api/me.json")


@pytest.mark.parametrize(
    "payload",
    [
        "nie json",
        {"final_url": "x", "body": "{}"},
        {"status": "200", "final_url": "x", "body": "{}"},
        {"status": 200},
    ],
)
async def test_a_malformed_reader_answer_is_never_a_page(
    capsule: _Capsule, payload: object
) -> None:
    capsule.payload = payload
    with pytest.raises(SourceUnavailable):
        await capsule.reader().read(f"{WWW}/api/me.json")


# -- configuration ---------------------------------------------------------------------


def test_build_from_env_is_opt_in() -> None:
    assert build_reddit_browser_source({}) is None
    assert build_reddit_browser_source({"WENEXT_BROWSER_READER_URL": "  "}) is None
    source = build_reddit_browser_source(
        {"WENEXT_BROWSER_READER_URL": "http://capsule:8765", "WENEXT_BROWSER_READER_TOKEN": "t"}
    )
    assert isinstance(source, RedditBrowserSource) and source.require_login is True
    source = build_reddit_browser_source(
        {"WENEXT_BROWSER_READER_URL": "http://capsule:8765", "WENEXT_REDDIT_REQUIRE_LOGIN": "false"}
    )
    assert isinstance(source, RedditBrowserSource) and source.require_login is False


# -- D-026: the capsule must be headful before Reddit is read -----------------------------


async def test_the_adapter_prepares_the_capsule_once_before_reading(reader: FakeReader) -> None:
    preparing = PreparingReader(mode="HEADLESS")
    preparing.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    source = _source(preparing)
    await source.list_recent_threads("r/devops", since=None, limit=5)
    await source.list_recent_threads("r/devops", since=None, limit=5)
    assert preparing.prepare_calls == 1, "raz na TTL, nie przed każdym odczytem"
    assert preparing.mode == "INTERACTIVE"
    assert urlsplit(preparing.requests[0]).path == "/api/me.json", "prepare idzie PRZED me.json"


async def test_a_reader_without_prepare_is_used_as_is(reader: FakeReader) -> None:
    reader.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    threads = await _source(reader).list_recent_threads("r/devops", since=None, limit=5)
    assert len(threads) == 1, "FakeReader bez prepare() — adapter niczego nie wymaga"


async def test_a_403_forgets_the_headful_mark_so_the_next_read_prepares_again() -> None:
    preparing = PreparingReader()
    preparing.routes["/r/devops/new.json"] = (403, "<html>blocked</html>")
    source = _source(preparing, require_login=False)
    with pytest.raises(SourceUnavailable, match="403"):
        await source.list_recent_threads("r/devops", since=None, limit=5)
    assert preparing.prepare_calls == 1
    preparing.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    await source.list_recent_threads("r/devops", since=None, limit=5)
    assert preparing.prepare_calls == 2, "po 403 kapsuła jest sprawdzana ponownie"


async def test_prepare_is_repeated_after_its_ttl() -> None:
    preparing = PreparingReader()
    preparing.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    now = {"t": FROZEN_NOW}
    source = RedditBrowserSource(preparing, require_login=False, clock=lambda: now["t"])
    await source.list_recent_threads("r/devops", since=None, limit=5)
    now["t"] = FROZEN_NOW + timedelta(minutes=11)
    await source.list_recent_threads("r/devops", since=None, limit=5)
    assert preparing.prepare_calls == 2


class _ControlCapsule:
    """The capsule's `/state` and `/mode`, as `HttpBrowserReader.prepare()` sees them."""

    def __init__(self, mode: str = "HEADLESS") -> None:
        self.mode = mode
        self.requests: list[httpx.Request] = []
        self.mode_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/state":
            return httpx.Response(200, json={"mode": self.mode, "display_only": False})
        if request.url.path == "/mode":
            if self.mode_status != 200:
                return httpx.Response(
                    self.mode_status, json={"error": "mode_switch_failed", "detail": "Xtigervnc"}
                )
            body = json.loads(request.content)
            self.mode = body["mode"]
            return httpx.Response(
                200,
                json={
                    "mode": self.mode,
                    "display_only": body.get("display_only", False),
                    "changed": True,
                },
            )
        return httpx.Response(404, json={"error": "not_found"})

    def reader(self) -> HttpBrowserReader:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return HttpBrowserReader("http://capsule:8765", token="tok", client=client)


async def test_prepare_asks_for_display_only_interactive_when_the_capsule_is_headless() -> None:
    capsule = _ControlCapsule("HEADLESS")
    state = await capsule.reader().prepare()
    assert [(r.method, r.url.path) for r in capsule.requests] == [
        ("GET", "/state"),
        ("POST", "/mode"),
    ]
    assert json.loads(capsule.requests[1].content) == {"mode": "INTERACTIVE", "display_only": True}
    assert capsule.requests[1].headers[READER_TOKEN_HEADER] == "tok"
    assert state["mode"] == "INTERACTIVE" and state["display_only"] is True


async def test_prepare_leaves_an_interactive_capsule_alone() -> None:
    capsule = _ControlCapsule("INTERACTIVE")
    state = await capsule.reader().prepare()
    assert [(r.method, r.url.path) for r in capsule.requests] == [("GET", "/state")]
    assert state["mode"] == "INTERACTIVE"


async def test_a_failed_switch_is_a_visible_transient_failure() -> None:
    capsule = _ControlCapsule("HEADLESS")
    capsule.mode_status = 502
    with pytest.raises(SourceUnavailable, match="502"):
        await capsule.reader().prepare()


async def test_a_capsule_without_a_mode_controller_names_the_old_image() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "no_mode_controller"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reader = HttpBrowserReader("http://capsule:8765", token="tok", client=client)
    with pytest.raises(SourceUnavailable, match="D-026"):
        await reader.prepare()


class _RefusingReader(FakeReader):
    """A reader whose capsule cannot become headful — `prepare()` raises."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error
        self.prepare_calls = 0

    async def prepare(self) -> dict:
        self.prepare_calls += 1
        raise self.error


async def test_a_failed_prepare_during_a_listing_is_a_visible_failure_never_an_empty_listing() -> (
    None
):
    """The ONE RULE ABOUT ERRORS (`source.py`) applies before the read too: a capsule that could
    not be made headful has not read anything, so the listing does not exist — it does not become
    `()`. Held by the absence of an `except` in `_ensure_prepared`; this test is what keeps it."""
    refusing = _RefusingReader(SourceUnavailable("kapsuła odpowiedziała 502 na POST /mode"))
    refusing.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    source = _source(refusing, require_login=False)
    with pytest.raises(SourceUnavailable, match="502"):
        await source.list_recent_threads("r/devops", since=None, limit=5)
    with pytest.raises(SourceUnavailable, match="502"):
        await source.fetch_thread("r/devops", "abc123")
    assert refusing.requests == [], "bez headful nie było żadnego odczytu Reddita"
    assert refusing.prepare_calls == 2, "każda próba pyta ponownie — porażka nie jest buforowana"


async def test_an_unexpected_prepare_error_is_not_swallowed_either() -> None:
    refusing = _RefusingReader(RuntimeError("coś zupełnie innego"))
    refusing.routes["/r/devops/new.json"] = (200, _listing([_link()]))
    with pytest.raises(RuntimeError):
        await _source(refusing, require_login=False).list_recent_threads(
            "r/devops", since=None, limit=5
        )
