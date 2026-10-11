"""`POST /act/comment` (R4 minimal, D-028): the guards in front of the actuator, and the pure
helpers of `writer.py`. No Chrome here: `FakeActuator` stands in for CDP, so these tests measure
what the endpoint refuses, in which order, and how it maps the actuator's three outcomes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from browser_supervisor.reader import (
    ACT_COMMENT_PATH,
    READER_TOKEN_HEADER,
    FetchResult,
    ReaderConfigError,
    ReaderServer,
    build_reader_from_env,
)
from browser_supervisor.writer import (
    WRITER_TOKEN_HEADER,
    CommentBlocked,
    CommentRefused,
    CommentResult,
    CommentUnknown,
    authorize_thread_url,
    public_permalink,
    require_fullname,
)

TOKEN = "sekret-czytnika"
WRITER = "sekret-aktuatora"

GOOD_BODY = {
    "thread_url": "https://www.reddit.com/r/kubernetes/comments/1w934kh/why_your_ai_agent/",
    "parent_fullname": "t3_1w934kh",
    "text": "A JSON schema fixes argument hallucination, not authority.",
    "account": "Demo_Maintainer",
}


class FakeFetcher:
    def fetch(self, url: str, *, timeout_s: float) -> FetchResult:
        return FetchResult(status=200, final_url=url, content_type="application/json", body="{}")


class FakeController:
    def __init__(self) -> None:
        self.current: dict[str, Any] = {
            "mode": "INTERACTIVE",
            "display_only": True,
            "human_steer_open": False,
            "alive": True,
        }

    def state(self) -> Mapping[str, Any]:
        return dict(self.current)

    def switch(
        self, mode: str, reason: str | None, display_only: bool = False
    ) -> Mapping[str, Any]:
        return dict(self.current)


class FakeActuator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_with: Exception | None = None
        self.result = CommentResult(
            fullname="t1_abc123",
            comment_id="abc123",
            permalink="https://www.reddit.com/r/kubernetes/comments/1w934kh/_/abc123/",
            account="Demo_Maintainer",
            observed_at="2026-09-07T08:00:00+00:00",
        )

    def comment(self, **kwargs: Any) -> CommentResult:
        self.calls.append(kwargs)
        if self.raise_with is not None:
            raise self.raise_with
        return self.result


@pytest.fixture
def running() -> Iterator[tuple[ReaderServer, FakeActuator, FakeController]]:
    actuator = FakeActuator()
    controller = FakeController()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        min_interval_s=0.0,
        timeout_s=5.0,
        controller=controller,
        writer_token=WRITER,
        actuator=actuator,
        act_timeout_s=3.0,
    )
    server.start()
    try:
        yield server, actuator, controller
    finally:
        server.stop()


def _post(
    server: ReaderServer,
    body: Any,
    *,
    token: str | None = WRITER,
    header: str = WRITER_TOKEN_HEADER,
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[header] = token
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{ACT_COMMENT_PATH}", data=data, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


# -- the pure helpers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.reddit.com/r/kubernetes/comments/1w934kh/why_your_ai_agent/",
        "https://old.reddit.com/r/kubernetes/comments/1w934kh/",
        "https://www.reddit.com/r/kubernetes/comments/1w934kh",
    ],
)
def test_a_thread_url_is_canonicalised_onto_old_reddit(url: str) -> None:
    assert authorize_thread_url(url) == "https://old.reddit.com/r/kubernetes/comments/1w934kh/"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://www.reddit.com/r/kubernetes/comments/1w934kh/",
        "https://reddit.com/r/kubernetes/comments/1w934kh/",
        "https://www.reddit.com/user/someone/",
        "https://www.reddit.com/r/kubernetes/new.json",
        "https://www.reddit.com@evil.example/r/kubernetes/comments/1w934kh/",
        "https://evil.example/r/kubernetes/comments/1w934kh/",
    ],
)
def test_anything_that_is_not_a_reddit_thread_is_refused_before_chrome(url: str) -> None:
    with pytest.raises(CommentRefused):
        authorize_thread_url(url)


def test_fullnames_and_permalinks() -> None:
    assert require_fullname("t3_1w934kh") == "t3_1w934kh"
    assert require_fullname("t1_abc") == "t1_abc"
    for bad in ("1w934kh", "t2_abc", "t3_", "t3_ABC", ""):
        with pytest.raises(CommentRefused):
            require_fullname(bad)
    thread = "https://old.reddit.com/r/kubernetes/comments/1w934kh/"
    assert (
        public_permalink(
            "/r/kubernetes/comments/1w934kh/why/abc123/", thread_url=thread, comment_id="abc123"
        )
        == "https://www.reddit.com/r/kubernetes/comments/1w934kh/why/abc123/"
    )
    assert (
        public_permalink(None, thread_url=thread, comment_id="abc123")
        == "https://www.reddit.com/r/kubernetes/comments/1w934kh/_/abc123/"
    )


# -- the guards, in order ------------------------------------------------------------------------


def test_the_reader_token_does_not_open_the_actuator(running) -> None:
    server, actuator, _ = running
    assert _post(server, GOOD_BODY, token=None)[0] == 401
    assert _post(server, GOOD_BODY, token=TOKEN)[0] == 401, "token czytnika to nie token aktuatora"
    assert _post(server, GOOD_BODY, token=TOKEN, header=READER_TOKEN_HEADER)[0] == 401
    assert actuator.calls == []


@pytest.mark.parametrize(
    "state",
    [
        {"mode": "HEADLESS", "display_only": False, "human_steer_open": False, "alive": True},
        {"mode": "INTERACTIVE", "display_only": False, "human_steer_open": True, "alive": True},
        {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": True, "alive": True},
        {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": False, "alive": False},
    ],
)
def test_without_display_only_and_a_closed_steer_nothing_is_written(running, state) -> None:
    server, actuator, controller = running
    controller.current = state
    status, body = _post(server, GOOD_BODY)
    assert status == 409 and body["error"] == "capsule_not_display_only", body
    assert actuator.calls == []


def test_a_malformed_body_is_400_and_the_actuator_is_not_touched(running) -> None:
    server, actuator, _ = running
    assert _post(server, {"thread_url": "x"})[0] == 400
    assert _post(server, {**GOOD_BODY, "text": ""})[0] == 400
    assert _post(server, [1, 2])[0] == 400
    assert actuator.calls == []


def test_a_good_request_reaches_the_actuator_once_and_returns_its_result(running) -> None:
    server, actuator, _ = running
    status, body = _post(server, GOOD_BODY)
    assert status == 200, body
    assert body["fullname"] == "t1_abc123" and body["account"] == "Demo_Maintainer"
    assert body["permalink"].startswith("https://www.reddit.com/")
    assert len(actuator.calls) == 1
    call = actuator.calls[0]
    assert call["thread_url"] == GOOD_BODY["thread_url"]
    assert call["parent_fullname"] == "t3_1w934kh"
    assert call["text"] == GOOD_BODY["text"] and call["account"] == "Demo_Maintainer"
    assert call["timeout_s"] == 3.0


@pytest.mark.parametrize(
    ("failure", "status", "error"),
    [
        (CommentRefused("archived"), 409, "refused"),
        (CommentBlocked("you are doing that too much"), 423, "blocked"),
        (CommentUnknown("no confirmation"), 504, "unknown"),
        (RuntimeError("cdp gone"), 502, "browser_unavailable"),
    ],
)
def test_the_three_outcomes_have_three_status_codes(running, failure, status, error) -> None:
    server, actuator, _ = running
    actuator.raise_with = failure
    got_status, body = _post(server, GOOD_BODY)
    assert (got_status, body["error"]) == (status, error), body
    assert str(failure) in body["detail"]


# -- configuration -------------------------------------------------------------------------------


def test_the_actuator_is_served_only_with_its_own_token() -> None:
    env = {"WENEXT_READER_PORT": "0", "WENEXT_READER_TOKEN": TOKEN}
    with pytest.raises(ReaderConfigError):
        # Port 0 is refused by the range check; use a real port number for the config path.
        build_reader_from_env(env, fetcher=FakeFetcher(), actuator=FakeActuator())
    env["WENEXT_READER_PORT"] = "8765"
    without = build_reader_from_env(env, fetcher=FakeFetcher(), actuator=FakeActuator())
    assert without is not None and without._server.actuator is None
    without._server.server_close()

    with pytest.raises(ReaderConfigError):
        ReaderServer(
            bind_host="127.0.0.1",
            port=0,
            token=TOKEN,
            fetcher=FakeFetcher(),
            writer_token=TOKEN,
            actuator=FakeActuator(),
        )
    with pytest.raises(ReaderConfigError):
        ReaderServer(
            bind_host="127.0.0.1", port=0, token=TOKEN, fetcher=FakeFetcher(), writer_token=WRITER
        )


def test_the_actuator_borrows_the_shared_driver_and_never_starts_its_own() -> None:
    """The first live smoke failed because the actuator started a SECOND Playwright driver on the
    reader's thread. With a shared driver injected, the actuator uses it and only it."""
    from browser_supervisor.writer import CdpCommentActuator

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, timeout: int) -> None:
            raise RuntimeError("fake CDP refused")

    class FakeDriver:
        chromium = FakeChromium()

    driver = FakeDriver()
    calls: list[int] = []

    def shared() -> FakeDriver:
        calls.append(1)
        return driver

    actuator = CdpCommentActuator("http://127.0.0.1:1", driver=shared)
    with pytest.raises(CommentRefused, match="brak połączenia CDP"):
        actuator.comment(
            thread_url=GOOD_BODY["thread_url"],
            parent_fullname="t3_1w934kh",
            text="x",
            account="Demo_Maintainer",
            timeout_s=1.0,
        )
    assert calls == [1], "driver współdzielony użyty dokładnie raz"
    assert actuator._playwright is None, "własny driver nie został uruchomiony"


def test_the_url_policy_is_the_actuators_first_gate() -> None:
    from browser_supervisor.writer import CdpCommentActuator

    seen: list[str] = []

    def policy(url: str) -> str:
        seen.append(url)
        raise CommentRefused("polityka testowa odmawia")

    actuator = CdpCommentActuator("http://127.0.0.1:1", url_policy=policy)
    with pytest.raises(CommentRefused, match="polityka testowa"):
        actuator.comment(
            thread_url="https://example.invalid/t",
            parent_fullname="t3_abc",
            text="x",
            account="a",
            timeout_s=1.0,
        )
    assert seen == ["https://example.invalid/t"]


def test_without_an_actuator_the_endpoint_is_404() -> None:
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        controller=FakeController(),
    )
    server.start()
    try:
        assert _post(server, GOOD_BODY)[0] == 404
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Odmowa CAŁEMU klientowi kontra odmowa jednego zapisu
# ---------------------------------------------------------------------------


def test_a_block_page_is_told_apart_from_a_single_refusal() -> None:
    """ZMIERZONE 21.09 sondą kapsuły: `old.reddit.com` oddał 403 i stronę „You've been blocked /
    network security" dla dwóch społeczności ORAZ dla wątku, który pilot komentował 17.09, a
    `www.reddit.com` w tej samej chwili odpowiadał 200 z zalogowanym kontem.

    Rozróżnienie jest całym sensem: odmowa jednego zapisu dotyczy tej próby i następna ma sens,
    a odmowa hosta znaczy, że NIC z tej przeglądarki tam nie wejdzie. Bez tego publikator mieli
    kolejne wnioski i pali próby, każdą „tylko tę jedną".
    """
    from browser_supervisor.writer import host_blocked

    stopped = host_blocked(403, "Whoa there, pardner! You've been blocked by network security.")
    assert stopped is not None
    assert "nie ma dziś drogi na ten host" in stopped
    assert "403" in stopped


def test_neither_the_status_nor_the_words_alone_are_enough() -> None:
    """403 bywa odpowiedzią na pojedynczy zakazany zasób, a słowo „blocked" pada w cudzych
    wątkach o moderacji. Dopiero razem są tym, czym są."""
    from browser_supervisor.writer import host_blocked

    assert host_blocked(403, "you are not allowed to post in this subreddit") is None
    assert host_blocked(200, "my account was blocked by network security last week") is None
    assert host_blocked(None, "") is None
