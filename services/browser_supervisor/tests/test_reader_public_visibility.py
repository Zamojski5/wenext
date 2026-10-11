"""The two P0 switches of the capsule's reader: the anonymous viewer and the inbox path.

WHY EITHER EXISTS. On 17.09 AutoModerator removed both of our comments in two new communities,
and our read-back an hour later still said the comment was present. The cause is structural: the
reader navigates the capsule's PERSISTENT profile, which carries the account's Reddit session, and
Reddit shows an author their own removed comments. So the reader could answer "is it there for
us" and nothing in the system could answer "is it there for anybody else" (STAN-PRAC §8.22).

Two narrow additions answer it, and BOTH ARE OFF UNLESS SWITCHED ON. That is the property most of
this file measures, because each one widens the boundary this module exists to be: one reads
through a context that is not the account's, the other reaches the account's private messages.
A widened boundary arriving as a default is a boundary nobody decided.

What is NOT measured here: Chrome. `FakeFetcher` stands in for CDP exactly as in `test_reader.py`,
so these tests prove which context the reader ASKS FOR and what it refuses before asking. Whether
`browser.new_context()` really yields a logged-out page in the capsule's Chrome is a measurement
on the capsule, and it has not been made (see the P0 report).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

import pytest
from browser_supervisor.reader import (
    ENV_ALLOW_ANONYMOUS,
    ENV_ALLOW_INBOX,
    INBOX_PATH,
    READER_TOKEN_HEADER,
    VIEWER_ANONYMOUS,
    VIEWER_SESSION,
    FetchResult,
    ReaderServer,
    ReadRefused,
    authorize_url,
    switch_is_on,
)

TOKEN = "sekret-testowy"
INBOX_URL = "https://www.reddit.com/message/inbox.json?raw_json=1&limit=100"
THREAD_URL = "https://www.reddit.com/r/devops/comments/abc123.json?raw_json=1"


class RecordingFetcher:
    """Records the `anonymous` flag of every fetch. A fake that dropped the flag would let an
    anonymous request be served from the account's session with nothing to notice it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.result = FetchResult(
            status=200,
            final_url=THREAD_URL,
            content_type="application/json",
            body="{}",
        )

    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult:
        self.calls.append((url, anonymous))
        return self.result


def _server(fetcher: RecordingFetcher, **switches: bool) -> Iterator[ReaderServer]:
    server = ReaderServer(
        bind_host="127.0.0.1", port=0, token=TOKEN, fetcher=fetcher, min_interval_s=0.0, **switches
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def closed() -> Iterator[tuple[ReaderServer, RecordingFetcher]]:
    """A reader with both switches OFF, which is how one starts without an explicit decision."""
    fetcher = RecordingFetcher()
    yield from ((server, fetcher) for server in _server(fetcher))


@pytest.fixture
def opened() -> Iterator[tuple[ReaderServer, RecordingFetcher]]:
    fetcher = RecordingFetcher()
    yield from (
        (server, fetcher) for server in _server(fetcher, allow_inbox=True, allow_anonymous=True)
    )


def _read(server: ReaderServer, url: str, *, viewer: str | None = None) -> tuple[int, dict]:
    query = {"url": url}
    if viewer is not None:
        query["viewer"] = viewer
    path = "/read?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}", headers={READER_TOKEN_HEADER: TOKEN}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_the_inbox_is_outside_the_allowlist_until_the_switch_says_otherwise() -> None:
    """The allowlist function itself, without a server: the default is three paths, not four."""
    with pytest.raises(ReadRefused, match=ENV_ALLOW_INBOX):
        authorize_url(INBOX_URL)
    assert authorize_url(INBOX_URL, allow_inbox=True) == INBOX_URL
    # And the switch adds THAT path only: everything the three-path allowlist refused, it still
    # refuses. Without this the test would pass just as well against `allow_inbox` disabling the
    # allowlist altogether, which is the failure mode worth catching.
    for refused in (
        "https://www.reddit.com/user/someone/about.json",
        "https://www.reddit.com/message/sent.json",
        "https://old.reddit.com/message/inbox.json",
        "https://www.reddit.com/message/inbox.json?after=t4_abc",
    ):
        with pytest.raises(ReadRefused):
            authorize_url(refused, allow_inbox=True)


def test_a_reader_with_the_switches_off_refuses_both_additions(closed) -> None:
    server, fetcher = closed
    status, body = _read(server, INBOX_URL)
    assert status == 400 and body["error"] == "url_refused"
    assert ENV_ALLOW_INBOX in body["detail"]

    status, body = _read(server, THREAD_URL, viewer=VIEWER_ANONYMOUS)
    assert status == 400 and body["error"] == "viewer_refused"
    assert ENV_ALLOW_ANONYMOUS in body["detail"]

    assert fetcher.calls == [], (
        "czytnik z wyłączonymi przełącznikami dotknął Chrome. Odmowa ma być PRZED nawigacją, "
        "inaczej wyłączony przełącznik nadal kosztuje odczyt u Reddita"
    )


def test_the_switches_come_from_the_environment_and_default_to_off() -> None:
    assert switch_is_on({ENV_ALLOW_INBOX: "1"}, ENV_ALLOW_INBOX)
    assert switch_is_on({ENV_ALLOW_ANONYMOUS: "TRUE"}, ENV_ALLOW_ANONYMOUS)
    for value in ("", "  ", "0", "false", "no", "maybe", "anonymous"):
        assert not switch_is_on({ENV_ALLOW_ANONYMOUS: value}, ENV_ALLOW_ANONYMOUS), value
    assert not switch_is_on({}, ENV_ALLOW_ANONYMOUS)


# ---------------------------------------------------------------------------
# On, and doing exactly the one thing it says
# ---------------------------------------------------------------------------


def test_the_default_viewer_is_the_account_session(opened) -> None:
    """Every caller written before P0 sends no `viewer`, and must keep reading as it did."""
    server, fetcher = opened
    status, body = _read(server, THREAD_URL)
    assert status == 200
    assert body["viewer"] == VIEWER_SESSION
    assert fetcher.calls == [(THREAD_URL, False)]


def test_viewer_anonymous_asks_for_a_context_without_the_session(opened) -> None:
    server, fetcher = opened
    status, body = _read(server, THREAD_URL, viewer=VIEWER_ANONYMOUS)
    assert status == 200
    # The echo is part of the answer. The adapter on the other side refuses a reply that does not
    # carry it, so a capsule image predating this parameter cannot pass a logged-in read off as
    # an anonymous one.
    assert body["viewer"] == VIEWER_ANONYMOUS
    assert fetcher.calls == [(THREAD_URL, True)]


def test_an_unknown_viewer_is_refused_rather_than_read_as_the_default(opened) -> None:
    """`viewer=anon`, `viewer=`, `viewer=Anonymous `: the first two are refusals, the third is
    the same word. Silently treating an unknown value as `session` would answer a question the
    caller did not ask, and the caller would file the answer as a visibility verdict."""
    server, fetcher = opened
    for value in ("anon", "", "author", "logged-out"):
        status, body = _read(server, THREAD_URL, viewer=value)
        assert status == 400, value
        assert body["error"] == "viewer_refused", value
    assert fetcher.calls == []

    status, body = _read(server, THREAD_URL, viewer="  Anonymous  ")
    assert status == 200 and body["viewer"] == VIEWER_ANONYMOUS
    assert fetcher.calls == [(THREAD_URL, True)]


def test_the_inbox_is_read_as_the_account_not_anonymously(opened) -> None:
    """The two switches are independent. The inbox is the ACCOUNT's mail: read without the
    session it is either empty or a login page, so nothing here couples one to the other."""
    server, fetcher = opened
    status, body = _read(server, INBOX_URL)
    assert status == 200 and body["viewer"] == VIEWER_SESSION
    assert fetcher.calls == [(INBOX_URL, False)]
    assert INBOX_PATH.match("/message/inbox.json")
