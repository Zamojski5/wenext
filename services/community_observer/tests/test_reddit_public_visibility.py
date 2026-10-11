"""The two reads that answer "is our comment visible to anybody but us" (P0, STAN-PRAC §8.22).

On 17.09 AutoModerator removed both comments we had posted in new communities. Our read-back an
hour later reported them present, because every read goes through the capsule's logged-in profile
and Reddit shows an author their own removed comments. `PUBLISHED` therefore meant "sent", and
nothing in the system meant "seen".

Two reads close that, and this file measures the property that makes them worth anything: NEITHER
OF THEM EVER ANSWERS "the comment is gone" WHEN WHAT REALLY HAPPENED IS "we could not look".
A 403, a 429, a login redirect, a capsule whose reader has no anonymous path, a capsule that
ignores the parameter: every one of those raises, and the caller records nothing, so the request
stays UNVERIFIED. A single one of them returning an empty thread instead would hand the publisher
a verdict of REMOVED built out of Reddit's rate limiter.

The inbox half is parsing, and it is deliberately narrow: AutoModerator's mail is not all removal
notices, and a removal report inferred from a welcome message is worse than no report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from community_observer import CredentialsMissing, SourceUnavailable, ThreadNotFound
from community_observer.reddit_browser import (
    VIEWER_ANONYMOUS,
    PageRead,
    RedditBrowserSource,
    RemovalReport,
    parse_removal_reports,
)

WWW = "https://www.reddit.com"
OUR_COMMENT = "t1_ourcmt"


def frozen_clock() -> datetime:
    return datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _comment(cid: str, body: str, author: str = "Demo_Maintainer") -> dict:
    return {
        "kind": "t1",
        "data": {
            "id": cid,
            "name": f"t1_{cid}",
            "author": author,
            "body": body,
            "created_utc": 1788433800.0,
            "replies": "",
        },
    }


def _thread(children: list) -> list:
    post = {
        "kind": "Listing",
        "data": {"children": [{"kind": "t3", "data": {"id": "abc123", "name": "t3_abc123"}}]},
    }
    return [post, {"kind": "Listing", "data": {"children": children}}]


class SplitReader:
    """A reader whose ANONYMOUS answers are separate from its logged-in ones.

    That separation is the whole fixture: a fake serving one body to both would make every test
    below pass against an adapter that quietly read as the account.
    """

    def __init__(self) -> None:
        self.session_routes: dict[str, tuple[int, object]] = {
            "/api/me.json": (200, {"name": "Demo_Maintainer"})
        }
        self.anonymous_routes: dict[str, tuple[int, object]] = {}
        self.session_requests: list[str] = []
        self.anonymous_requests: list[str] = []
        self.anonymous_redirects_to_login = False

    async def read(self, url: str) -> PageRead:
        self.session_requests.append(url)
        return self._answer(url, self.session_routes)

    async def read_anonymously(self, url: str) -> PageRead:
        self.anonymous_requests.append(url)
        if self.anonymous_redirects_to_login:
            return PageRead(200, f"{WWW}/login/?dest={url}", "text/html", "<html>login</html>")
        return self._answer(url, self.anonymous_routes)

    @staticmethod
    def _answer(url: str, routes: dict[str, tuple[int, object]]) -> PageRead:
        status, body = routes.get(urlsplit(url).path, (404, {"message": "Not Found"}))
        text = body if isinstance(body, str) else json.dumps(body)
        return PageRead(status, url, "application/json; charset=UTF-8", text)


class SessionOnlyReader:
    """The reader a capsule built before P0 offers: `read` and nothing else."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def read(self, url: str) -> PageRead:
        self.requests.append(url)
        return PageRead(200, url, "application/json", json.dumps({"name": "Demo_Maintainer"}))


def _source(reader) -> RedditBrowserSource:
    return RedditBrowserSource(reader, clock=frozen_clock)


# ---------------------------------------------------------------------------
# The anonymous read
# ---------------------------------------------------------------------------


async def test_the_anonymous_read_does_not_go_through_the_account_session() -> None:
    reader = SplitReader()
    reader.anonymous_routes["/r/devops/comments/abc123.json"] = (
        200,
        _thread([_comment("ourcmt", "nasza treść")]),
    )
    payload = await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")

    assert payload[1]["data"]["children"][0]["data"]["name"] == OUR_COMMENT
    assert len(reader.anonymous_requests) == 1
    assert reader.session_requests == [], (
        "odczyt anonimowy dotknął sesji konta. Nawet `/api/me.json` jest tu błędem, bo "
        "`_ensure_logged_in` na tej ścieżce oznacza, że wynik zależy od zalogowania"
    )


async def test_a_reader_without_an_anonymous_path_refuses_instead_of_reading_as_the_account() -> (
    None
):
    """The failure mode this guards: a fallback to `read()`. It would return the thread, the
    publisher would find the comment, and a removed comment would be filed as CONFIRMED with a
    fresh timestamp. Better to have no answer than the author's answer wearing the public label.
    """
    reader = SessionOnlyReader()
    with pytest.raises(SourceUnavailable, match="anonimowego"):
        await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")
    assert reader.requests == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (403, {"reason": "private", "message": "Forbidden"}, CredentialsMissing),
        (403, "<html>blocked</html>", SourceUnavailable),
        (429, {"message": "Too Many Requests"}, SourceUnavailable),
        (500, {"message": "oops"}, SourceUnavailable),
        (404, {"message": "Not Found"}, ThreadNotFound),
        (200, "<!doctype html><html>…</html>", SourceUnavailable),
    ],
    ids=["403-private", "403-antybot", "429", "500", "404", "html"],
)
async def test_a_read_that_did_not_happen_raises_and_never_looks_like_an_empty_thread(
    status: int, body: object, expected: type[Exception]
) -> None:
    """Every one of these, returned as an empty comment list, would read as REMOVED downstream."""
    reader = SplitReader()
    reader.anonymous_routes["/r/devops/comments/abc123.json"] = (status, body)
    with pytest.raises(expected):
        await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")


async def test_a_login_redirect_on_the_anonymous_path_is_not_an_answer_either() -> None:
    """Reddit answering the logged-out reader with its login wall says nothing about our comment."""
    reader = SplitReader()
    reader.anonymous_redirects_to_login = True
    with pytest.raises(CredentialsMissing):
        await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")


async def test_an_anonymously_read_thread_without_our_comment_is_a_real_empty_answer() -> None:
    """The other side of the same rule: a 200 with somebody else's comments and not ours IS an
    answer, and the caller is entitled to act on it. If this test ever fails the same way as the
    ones above, the adapter has started raising on a successful read."""
    reader = SplitReader()
    reader.anonymous_routes["/r/devops/comments/abc123.json"] = (
        200,
        _thread([_comment("other", "czyjś komentarz", author="somebody")]),
    )
    payload = await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")
    names = [c["data"]["name"] for c in payload[1]["data"]["children"]]
    assert names == ["t1_other"] and OUR_COMMENT not in names


async def test_the_url_of_the_anonymous_read_is_the_allowlisted_thread_shape() -> None:
    """No new URL shape: the anonymous read reuses the path the reader already allows, so only
    the CONTEXT differs. The inbox is the one path this change adds, and it is read as the
    account, not here."""
    reader = SplitReader()
    reader.anonymous_routes["/r/devops/comments/abc123.json"] = (200, _thread([]))
    await _source(reader).read_thread_payload_anonymously("r/devops", "abc123")
    parts = urlsplit(reader.anonymous_requests[0])
    assert parts.netloc == "www.reddit.com"
    assert parts.path == "/r/devops/comments/abc123.json"
    assert "viewer" not in parts.query, (
        "`viewer` jest parametrem NASZEGO czytnika, nie adresu Reddita, więc w URL-u Reddita "
        "wypadłby poza allowlistę parametrów"
    )


# ---------------------------------------------------------------------------
# The inbox
# ---------------------------------------------------------------------------


def _message(**overrides) -> dict:
    data = {
        "author": "AutoModerator",
        "body": "Your comment was automatically removed because your account is too new.",
        "parent_id": OUR_COMMENT,
        "created_utc": 1789000000.0,
        "was_comment": True,
    }
    data.update(overrides)
    return {"kind": "t1", "data": data}


def _inbox(children: list) -> dict:
    return {"kind": "Listing", "data": {"children": children}}


def test_an_automoderator_removal_notice_becomes_a_report() -> None:
    reports = parse_removal_reports(_inbox([_message()]))
    assert len(reports) == 1
    report = reports[0]
    assert isinstance(report, RemovalReport)
    assert report.comment_external_id == OUR_COMMENT, (
        "raport wskazuje na komentarz, do którego AutoModerator ODPOWIEDZIAŁ, czyli na nasz. "
        "to jest ten sam identyfikator, który zapisało `execution_evidence`"
    )
    assert report.reported_at == datetime.fromtimestamp(1789000000.0, tz=UTC)
    assert "automatically removed" in report.note


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"author": "somebody"}, "odpowiedź człowieka pod naszym komentarzem"),
        ({"author": "reddit"}, "wiadomość systemowa, nie AutoModerator"),
        ({"body": "Welcome to r/devops! Please read the rules."}, "powitanie, nie usunięcie"),
        ({"parent_id": "t3_abc123"}, "odpowiedź na POST, nie na nasz komentarz"),
        ({"parent_id": ""}, "nie ma czego przypisać"),
        ({"created_utc": None}, "bez momentu zgłoszenia nie ma dowodu"),
        ({"created_utc": "1789000000"}, "czas jako string to nie czas"),
    ],
)
def test_what_is_not_a_removal_report_is_not_reported(overrides: dict, why: str) -> None:
    """Seven ways to get a false removal, each one of which would mark a live comment REMOVED."""
    assert parse_removal_reports(_inbox([_message(**overrides)])) == (), why


def test_reports_come_back_newest_first_and_other_mail_is_ignored() -> None:
    old = _message(created_utc=1789000000.0, parent_id="t1_older")
    new = _message(created_utc=1789009999.0, parent_id="t1_newer")
    noise = _message(author="alice", body="dzięki, pomogło")
    reports = parse_removal_reports(_inbox([old, noise, new]))
    assert [r.comment_external_id for r in reports] == ["t1_newer", "t1_older"]


def test_an_inbox_that_is_not_a_listing_raises_rather_than_reading_as_empty() -> None:
    """An empty inbox and an unreadable one are different facts. Read as the same one, a broken
    reader looks exactly like an account with no removals."""
    for payload in ({}, {"data": {}}, {"data": {"children": "nope"}}, [], None):
        with pytest.raises(SourceUnavailable):
            parse_removal_reports(payload)


async def test_the_inbox_is_read_as_the_account_and_at_its_allowlisted_path() -> None:
    reader = SplitReader()
    reader.session_routes["/message/inbox.json"] = (200, _inbox([_message()]))
    reports = await _source(reader).read_removal_reports()

    assert [r.comment_external_id for r in reports] == [OUR_COMMENT]
    assert reader.anonymous_requests == [], "skrzynka konta czytana anonimowo jest pusta lub cudza"
    paths = [urlsplit(u).path for u in reader.session_requests]
    assert "/message/inbox.json" in paths
    assert "/api/me.json" in paths, (
        "adapter nie sprawdził, że kapsuła jest zalogowana, a anonimowa skrzynka nie jest niczyja"
    )


async def test_an_inbox_the_capsule_refuses_is_unavailable_not_empty() -> None:
    """Which is what a capsule running WITHOUT `WENEXT_READER_ALLOW_INBOX` answers: 400, turned
    into `SourceUnavailable` by the reader client. Silence would say "no removals"."""
    reader = SplitReader()
    reader.session_routes["/message/inbox.json"] = (403, "<html>blocked</html>")
    with pytest.raises(SourceUnavailable):
        await _source(reader).read_removal_reports()


def test_the_viewer_name_is_the_one_the_capsule_reader_spells() -> None:
    """The two packages do not import each other, so the literal is the contract (same rule as
    `READER_TOKEN_HEADER`)."""
    assert VIEWER_ANONYMOUS == "anonymous"
