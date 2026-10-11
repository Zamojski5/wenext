"""`RedditSource` — the credentials rule first, the parser second.

The payloads are the DOCUMENTED shapes of the Data API (Listing → children → data), not
recordings: no application was registered as of 2026-09-03, so nothing here has met the live
API. `community_observer/reddit.py` says the same in its module docstring; the first live run
belongs to the founder.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from community_observer import (
    REDDIT_REQUIRED_ENV,
    CommunitySource,
    CredentialsMissing,
    RedditCredentials,
    RedditSource,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)
from community_os_contracts import AccessMode, ObservationPlatform
from tests.conftest import FROZEN_NOW, frozen_clock

CREDENTIALS = RedditCredentials(
    client_id="cid", client_secret="secret", username="founder", password="pw", user_agent="ua/1"
)


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
    if replies is not None:
        data["replies"] = {"kind": "Listing", "data": {"children": replies}}
    else:
        data["replies"] = ""
    return {"kind": "t1", "data": data}


def _listing(children: list) -> dict:
    return {"kind": "Listing", "data": {"children": children, "after": None}}


class _Reddit:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object]] = {
            "/api/v1/access_token": (
                200,
                {"access_token": "tok", "expires_in": 3600, "token_type": "bearer"},
            ),
        }
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes.get(
            request.url.path, (404, {"message": "Not Found", "error": 404})
        )
        return httpx.Response(status, json=body)

    def source(self, credentials: RedditCredentials | None = CREDENTIALS) -> RedditSource:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return RedditSource(
            credentials,
            client,
            token_url="https://reddit.test/api/v1/access_token",
            api_base_url="https://oauth.reddit.test",
            clock=frozen_clock,
        )


@pytest.fixture
def reddit() -> _Reddit:
    return _Reddit()


def test_the_adapter_satisfies_the_port_and_declares_account_required() -> None:
    source = RedditSource(None)
    assert isinstance(source, CommunitySource)
    assert source.platform is ObservationPlatform.REDDIT
    assert source.access_mode is AccessMode.ACCOUNT_REQUIRED
    assert not source.configured


def test_credentials_come_from_four_variables_or_not_at_all() -> None:
    assert len(REDDIT_REQUIRED_ENV) == 4
    complete = {name: f"value-{i}" for i, name in enumerate(REDDIT_REQUIRED_ENV)}
    assert RedditCredentials.from_env(complete) is not None
    for missing in REDDIT_REQUIRED_ENV:
        partial = {**complete, missing: ""}
        assert RedditCredentials.from_env(partial) is None, f"pusty {missing} to brak poświadczeń"
    assert RedditCredentials.from_env({}) is None


async def test_without_credentials_every_read_says_so_and_never_returns_empty(
    reddit: _Reddit,
) -> None:
    source = reddit.source(credentials=None)
    with pytest.raises(CredentialsMissing, match="WENEXT_REDDIT_CLIENT_ID"):
        await source.list_recent_threads("r/devops", since=None, limit=10)
    with pytest.raises(CredentialsMissing):
        await source.fetch_thread("r/devops", "abc123")
    assert reddit.requests == [], "bez poświadczeń adapter nie dotyka sieci"


def test_describe_requires_the_r_prefix(reddit: _Reddit) -> None:
    source = reddit.source()
    assert source.describe("r/devops").ref == "REDDIT:r/devops"
    for bad in ("devops", "ask", "r/", "r/with space", "/r/devops"):
        with pytest.raises(UnknownCommunity):
            source.describe(bad)


async def test_listing_exchanges_the_token_once_and_parses_links(reddit: _Reddit) -> None:
    reddit.routes["/r/devops/new"] = (
        200,
        _listing([_link(), _link(id="def456", hide_score=True, score=0)]),
    )
    source = reddit.source()
    threads = await source.list_recent_threads("r/devops", since=None, limit=25)
    threads_again = await source.list_recent_threads("r/devops", since=None, limit=25)

    token_requests = [r for r in reddit.requests if r.url.path == "/api/v1/access_token"]
    assert len(token_requests) == 1, "token jest buforowany do wygaśnięcia"
    assert token_requests[0].headers["authorization"].startswith("Basic ")
    assert b"grant_type=password" in token_requests[0].content
    listing_requests = [r for r in reddit.requests if r.url.path == "/r/devops/new"]
    assert listing_requests[0].headers["authorization"] == "Bearer tok"
    assert listing_requests[0].headers["user-agent"] == "ua/1"
    assert listing_requests[0].url.params["limit"] == "25"

    assert len(threads) == len(threads_again) == 2
    first = threads[0]
    assert first.ref == "REDDIT:r/devops:abc123"
    assert first.url.endswith("/comments/abc123/how_do_you_run_migrations_in_ci/")
    assert first.link_url is None, "post tekstowy nie ma link_url"
    assert first.body == "We use a shadow database."
    assert first.metrics.score.require() == 12
    assert first.observed_at == FROZEN_NOW
    hidden = threads[1]
    assert not hidden.metrics.score.is_known, "hide_score → UNKNOWN, nie 0"
    assert hidden.metrics.comment_count.require() == 3


async def test_listing_applies_the_watermark_client_side(reddit: _Reddit) -> None:
    older = _link(id="old1", created_utc=1788000000.0)
    newer = _link(id="new1", created_utc=1788500000.0)
    reddit.routes["/r/devops/new"] = (200, _listing([newer, older]))
    since = datetime.fromtimestamp(1788400000.0, tz=UTC)
    threads = await reddit.source().list_recent_threads("r/devops", since=since, limit=25)
    assert [t.external_id for t in threads] == ["new1"]


async def test_deleted_author_and_removed_body_become_none(reddit: _Reddit) -> None:
    reddit.routes["/r/devops/new"] = (
        200,
        _listing([_link(author="[deleted]", selftext="[removed]")]),
    )
    (thread,) = await reddit.source().list_recent_threads("r/devops", since=None, limit=25)
    assert thread.author is None and thread.body is None


async def test_fetch_thread_flattens_replies_and_marks_a_more_stub_as_truncation(
    reddit: _Reddit,
) -> None:
    comments = _listing(
        [
            _comment(
                "c1", "top", replies=[_comment("c2", "reply", replies=[_comment("c3", "deep")])]
            ),
            _comment("c4", "another top", author="[deleted]"),
            {"kind": "more", "data": {"count": 17, "children": ["c5", "c6"]}},
        ]
    )
    reddit.routes["/r/devops/comments/abc123"] = (200, [_listing([_link()]), comments])
    snapshot = await reddit.source().fetch_thread("r/devops", "abc123")

    assert snapshot.thread.external_id == "abc123"
    assert [c.external_id for c in snapshot.comments] == ["c1", "c2", "c3", "c4"]
    assert [c.depth for c in snapshot.comments] == [0, 1, 2, 0]
    assert snapshot.comments[1].parent_external_id == "c1"
    assert snapshot.comments[3].author is None
    assert snapshot.comments_truncated, "stub `more` = drzewo obcięte, i to jest zapisane"
    assert not snapshot.unique_participants.is_known


async def test_fetch_thread_without_truncation_counts_participants(reddit: _Reddit) -> None:
    comments = _listing([_comment("c1", "a", author="alice"), _comment("c2", "b", author="bob")])
    reddit.routes["/r/devops/comments/abc123"] = (200, [_listing([_link()]), comments])
    snapshot = await reddit.source().fetch_thread("r/devops", "abc123")
    assert not snapshot.comments_truncated
    assert snapshot.unique_participants.require() == 2


async def test_fetch_of_a_missing_post_is_thread_not_found(reddit: _Reddit) -> None:
    with pytest.raises(ThreadNotFound):
        await reddit.source().fetch_thread("r/devops", "zzz999")
    with pytest.raises(ThreadNotFound):
        await reddit.source().fetch_thread("r/devops", "NOT_BASE36!")


async def test_rejected_credentials_are_not_transient(reddit: _Reddit) -> None:
    reddit.routes["/api/v1/access_token"] = (401, {"error": "invalid_grant"})
    with pytest.raises(CredentialsMissing, match="401"):
        await reddit.source().list_recent_threads("r/devops", since=None, limit=25)


async def test_a_token_response_without_a_token_is_not_a_token(reddit: _Reddit) -> None:
    reddit.routes["/api/v1/access_token"] = (200, {"error": "unsupported_grant_type"})
    with pytest.raises(CredentialsMissing, match="unsupported_grant_type"):
        await reddit.source().list_recent_threads("r/devops", since=None, limit=25)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_platform_error_is_never_an_empty_listing(reddit: _Reddit, status: int) -> None:
    reddit.routes["/r/devops/new"] = (status, {"message": "Too Many Requests"})
    with pytest.raises(SourceUnavailable, match=str(status)):
        await reddit.source().list_recent_threads("r/devops", since=None, limit=25)


async def test_a_listing_that_is_not_a_listing_is_never_empty(reddit: _Reddit) -> None:
    reddit.routes["/r/devops/new"] = (200, {"kind": "t3", "data": {}})
    with pytest.raises(SourceUnavailable, match="Listing"):
        await reddit.source().list_recent_threads("r/devops", since=None, limit=25)


async def test_a_dead_token_is_forgotten_and_reported_as_transient(reddit: _Reddit) -> None:
    reddit.routes["/r/devops/new"] = (401, {"message": "Unauthorized"})
    source = reddit.source()
    with pytest.raises(SourceUnavailable, match="401"):
        await source.list_recent_threads("r/devops", since=None, limit=25)
    reddit.routes["/r/devops/new"] = (200, _listing([_link()]))
    await source.list_recent_threads("r/devops", since=None, limit=25)
    token_requests = [r for r in reddit.requests if r.url.path == "/api/v1/access_token"]
    assert len(token_requests) == 2, "po 401 token jest wymieniany na nowo"
