"""The capsule's page reader (D-025): the allowlist first, the token second, the wire third.

No Chrome here. `FakeFetcher` stands in for CDP, so these tests measure the three locks the
module docstring names — what the reader refuses, what it lets through, and what it reports when
the browser fails — not whether Playwright works.
"""

from __future__ import annotations

import json
import pathlib
import socket
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
from browser_supervisor.reader import (
    ALLOWED_ORIGIN,
    READER_TOKEN_HEADER,
    CdpPageFetcher,
    FetchFailed,
    FetchResult,
    FetchTimedOut,
    ReaderConfigError,
    ReaderServer,
    ReadRefused,
    authorize_url,
    build_reader_from_env,
    playwright_errors,
    retry_after_seconds,
)

TOKEN = "sekret-testowy"

ACCEPTED: list[tuple[str, str]] = [
    (
        "https://www.reddit.com/r/kubernetes/new.json?raw_json=1&limit=25",
        "https://www.reddit.com/r/kubernetes/new.json?raw_json=1&limit=25",
    ),
    (
        "https://www.reddit.com/r/devops/comments/abc123.json?raw_json=1&limit=500&depth=10&sort=old",
        "https://www.reddit.com/r/devops/comments/abc123.json?raw_json=1&limit=500&depth=10&sort=old",
    ),
    (
        "https://www.reddit.com/api/me.json?raw_json=1",
        "https://www.reddit.com/api/me.json?raw_json=1",
    ),
    ("https://www.reddit.com/api/me.json", "https://www.reddit.com/api/me.json"),
    ("HTTPS://WWW.REDDIT.COM/api/me.json", "https://www.reddit.com/api/me.json"),
    # 21.09, decyzja koordynatora: `about.json` niesie `user_is_subscriber` dla zalogowanego
    # konta, czyli odczyt zwrotny członkostwa (E3b). Jedna ścieżka, tylko GET, o JEDNEJ
    # społeczności.
    ("https://www.reddit.com/r/devops/about.json", "https://www.reddit.com/r/devops/about.json"),
    (
        "https://www.reddit.com/r/devops/about.json?raw_json=1",
        "https://www.reddit.com/r/devops/about.json?raw_json=1",
    ),
]

REFUSED: list[str] = [
    "",
    "not a url",
    "http://www.reddit.com/r/devops/new.json",
    "https://old.reddit.com/r/devops/new.json",
    "https://reddit.com/r/devops/new.json",
    "https://www.reddit.com.evil.example/r/devops/new.json",
    "https://www.reddit.com@evil.example/r/devops/new.json",
    "https://user:pw@www.reddit.com/r/devops/new.json",
    "https://www.reddit.com:8443/r/devops/new.json",
    "https://www.reddit.com/r/devops/new.json#fragment",
    "https://www.reddit.com/r/devops/new",
    "https://www.reddit.com/r/devops/hot.json",
    "https://www.reddit.com/r/devops/new.json/",
    "https://www.reddit.com/r/devops/../../login.json",
    "https://www.reddit.com/user/someone/about.json",
    # Dopuszczona jest `about.json` SPOŁECZNOŚCI, nie cudzego konta i nie wszystkich naraz.
    "https://www.reddit.com/r/devops/about.json/",
    "https://www.reddit.com/subreddits/about.json",
    "https://www.reddit.com/r/devops/about",
    "https://www.reddit.com/r/devops/comments/abc123/some_title.json",
    "https://www.reddit.com/r/devops/comments/ABC123.json",
    "https://www.reddit.com/r/devops/comments/abcdefghijklm.json",
    "https://www.reddit.com/r/a/new.json",
    "https://www.reddit.com/r/devops/new.json?limit=1000",
    "https://www.reddit.com/r/devops/new.json?limit=0",
    "https://www.reddit.com/r/devops/new.json?after=t3_abc",
    "https://www.reddit.com/r/devops/new.json?limit=1&limit=2",
    "https://www.reddit.com/r/devops/new.json?sort=hot",
    "https://www.reddit.com/r/devops/new.json?raw_json=0",
    "https://www.reddit.com/r/devops/comments/abc123.json?depth=11",
    "https://www.reddit.com/api/v1/me",
    "https://www.reddit.com/api/me.json?x=1",
    "file:///etc/passwd",
    "javascript:alert(1)",
]


@pytest.mark.parametrize(("url", "canonical"), ACCEPTED)
def test_the_three_allowed_shapes_pass_and_come_back_canonical(url: str, canonical: str) -> None:
    assert authorize_url(url) == canonical
    assert authorize_url(url).startswith(ALLOWED_ORIGIN)


@pytest.mark.parametrize("url", REFUSED)
def test_everything_else_is_refused_before_chrome_is_asked(url: str) -> None:
    with pytest.raises(ReadRefused):
        authorize_url(url)


def test_the_token_header_is_spelled_the_way_the_adapter_spells_it() -> None:
    # `community_observer.reddit_browser.READER_TOKEN_HEADER` pins the same literal on its side;
    # the two packages do not import each other, so the literal is the contract.
    assert READER_TOKEN_HEADER == "X-Wenext-Reader-Token"


def test_the_reader_source_has_no_writing_vocabulary() -> None:
    """The same vocabulary as `control_plane/execution/bypass_scan.py`, applied to this one file.

    The repository-wide scan covers `reader.py` too; failing here points at the file and at the
    line. Prose in docstrings may NAME the forbidden methods — the scan looks at call sites and at
    whole string constants, exactly as the bypass scanner does.
    """
    import ast
    import re

    path = pathlib.Path(__file__).resolve().parents[1] / "browser_supervisor" / "reader.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    writing_calls = {
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
    }
    writing_cdp = re.compile(r"^(Input\.[A-Za-z]+|Runtime\.(evaluate|callFunctionOn))$")
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append(node.func.attr)
            assert node.func.attr not in writing_calls, (
                f"reader.py:{node.lineno} woła .{node.func.attr}() — to jest zapis"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not writing_cdp.match(node.value), (
                f"reader.py:{node.lineno} zawiera metodę CDP {node.value!r} — to jest zapis"
            )
    assert "goto" in calls, "czytnik czyta przez nawigację, i tylko przez nią"


# -- the wire -----------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self.result = FetchResult(
            status=200,
            final_url="https://www.reddit.com/api/me.json?raw_json=1",
            content_type="application/json; charset=UTF-8",
            body="{}",
        )
        self.raise_with: Exception | None = None
        self.viewers: list[bool] = []

    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult:
        # `anonymous` is recorded, not ignored: the tests below assert WHICH context the reader
        # asked for, and a fake that swallowed the argument would let the anonymous path read
        # through the account's session with nothing to notice it.
        self.calls.append((url, timeout_s))
        self.viewers.append(anonymous)
        if self.raise_with is not None:
            raise self.raise_with
        return self.result


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
    )
    server.start()
    try:
        yield server, fetcher, clock
    finally:
        server.stop()


def _get(server: ReaderServer, path: str, *, token: str | None = TOKEN) -> tuple[int, dict]:
    headers = {READER_TOKEN_HEADER: token} if token is not None else {}
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


ME = "https%3A%2F%2Fwww.reddit.com%2Fapi%2Fme.json%3Fraw_json%3D1"


def test_without_the_token_nothing_is_fetched(running) -> None:
    server, fetcher, _ = running
    assert _get(server, f"/read?url={ME}", token=None)[0] == 401
    assert _get(server, f"/read?url={ME}", token="zly")[0] == 401
    assert fetcher.calls == []


def test_a_refused_url_is_400_and_chrome_is_not_asked(running) -> None:
    server, fetcher, _ = running
    status, body = _get(server, "/read?url=https%3A%2F%2Fevil.example%2Fr%2Fdevops%2Fnew.json")
    assert status == 400 and body["error"] == "url_refused"
    assert "origin" in body["detail"]
    assert fetcher.calls == []
    status, body = _get(server, "/read")
    assert status == 400 and body["error"] == "url_refused"


def test_an_allowed_url_comes_back_with_what_the_browser_received(running) -> None:
    server, fetcher, _ = running
    fetcher.result = FetchResult(
        status=200,
        final_url="https://www.reddit.com/r/devops/new.json?raw_json=1&limit=5",
        content_type="application/json; charset=UTF-8",
        body='{"kind": "Listing", "data": {"children": []}}',
    )
    status, body = _get(
        server,
        "/read?url=https%3A%2F%2Fwww.reddit.com%2Fr%2Fdevops%2Fnew.json%3Fraw_json%3D1%26limit%3D5",
    )
    assert status == 200
    assert body == {
        "status": 200,
        "final_url": "https://www.reddit.com/r/devops/new.json?raw_json=1&limit=5",
        "content_type": "application/json; charset=UTF-8",
        "body": '{"kind": "Listing", "data": {"children": []}}',
        # The reader ECHOES which context it read through. The client checks that echo, so a
        # capsule image from before `viewer` existed (it would ignore the parameter and read
        # through the account's session) cannot have its answer recorded as an anonymous verdict.
        "viewer": "session",
    }
    assert fetcher.calls == [("https://www.reddit.com/r/devops/new.json?raw_json=1&limit=5", 7.5)]


def test_a_non_200_from_reddit_is_passed_through_not_hidden(running) -> None:
    server, fetcher, _ = running
    fetcher.result = FetchResult(
        status=403,
        final_url="https://www.reddit.com/api/me.json",
        content_type="application/json",
        body='{"reason": "private"}',
    )
    status, body = _get(server, f"/read?url={ME}")
    assert status == 200, (
        "odpowiedź czytnika to 200 — status Reddita jest W CIELE, adapter go mapuje"
    )
    assert body["status"] == 403 and body["body"] == '{"reason": "private"}'


def test_browser_failures_are_reported_as_reader_errors_never_as_bodies(running) -> None:
    server, fetcher, _ = running
    fetcher.raise_with = FetchFailed("brak połączenia CDP")
    status, body = _get(server, f"/read?url={ME}")
    assert status == 502 and body["error"] == "browser_unavailable"
    assert "FetchFailed" in body["detail"] and "CDP" in body["detail"]

    fetcher.raise_with = FetchTimedOut("za długo")
    status, body = _get(server, f"/read?url={ME}")
    assert status == 504 and body["error"] == "browser_timeout"

    fetcher.raise_with = RuntimeError("cokolwiek")
    status, body = _get(server, f"/read?url={ME}")
    assert status == 502 and "RuntimeError" in body["detail"]


def test_reads_are_spaced_by_the_minimum_interval(running) -> None:
    server, fetcher, clock = running
    _get(server, f"/read?url={ME}")
    assert clock.sleeps == [], "pierwszy odczyt nie czeka"
    _get(server, f"/read?url={ME}")
    assert clock.sleeps == [pytest.approx(2.0)], (
        "drugi odczyt zaraz po pierwszym czeka pełny odstęp"
    )
    clock.t += 10
    _get(server, f"/read?url={ME}")
    assert len(clock.sleeps) == 1, "po 10 s przerwy nie ma na co czekać"
    assert len(fetcher.calls) == 3


def test_healthz_counts_what_happened(running) -> None:
    server, fetcher, _ = running
    _get(server, f"/read?url={ME}")
    _get(server, "/read?url=https%3A%2F%2Fevil.example%2F")
    _get(server, f"/read?url={ME}", token=None)
    fetcher.raise_with = FetchFailed("x")
    _get(server, f"/read?url={ME}")
    status, body = _get(server, "/healthz", token=None)
    assert status == 200
    assert body == {
        "ok": True,
        "reads_ok": 1,
        "reads_failed": 1,
        "reads_refused": 2,
        "reads_paced": 0,
        "reddit_paced_for_s": None,
    }
    assert _get(server, "/anything", token=None)[0] == 404


def test_a_server_without_a_token_does_not_exist() -> None:
    with pytest.raises(ReaderConfigError):
        ReaderServer(bind_host="127.0.0.1", port=0, token="", fetcher=FakeFetcher())


# -- configuration ------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_no_port_means_no_reader_at_all() -> None:
    assert build_reader_from_env({}, fetcher=FakeFetcher()) is None
    assert build_reader_from_env({"WENEXT_READER_PORT": "  "}, fetcher=FakeFetcher()) is None


@pytest.mark.parametrize(
    "env",
    [
        {"WENEXT_READER_PORT": "8765"},
        {"WENEXT_READER_PORT": "8765", "WENEXT_READER_TOKEN": "   "},
        {"WENEXT_READER_PORT": "abc", "WENEXT_READER_TOKEN": "t"},
        {"WENEXT_READER_PORT": "70000", "WENEXT_READER_TOKEN": "t"},
        {
            "WENEXT_READER_PORT": "8765",
            "WENEXT_READER_TOKEN": "t",
            "WENEXT_READER_MIN_INTERVAL_SECONDS": "-1",
        },
        {
            "WENEXT_READER_PORT": "8765",
            "WENEXT_READER_TOKEN": "t",
            "WENEXT_READER_TIMEOUT_SECONDS": "dużo",
        },
    ],
)
def test_a_port_without_a_usable_token_or_numbers_refuses_to_start(env: dict[str, str]) -> None:
    with pytest.raises(ReaderConfigError):
        build_reader_from_env(env, fetcher=FakeFetcher())


def test_a_complete_environment_builds_a_bound_reader() -> None:
    port = _free_port()
    reader = build_reader_from_env(
        {
            "WENEXT_READER_PORT": str(port),
            "WENEXT_READER_BIND": "127.0.0.1",
            "WENEXT_READER_TOKEN": TOKEN,
            "WENEXT_READER_MIN_INTERVAL_SECONDS": "0",
        },
        fetcher=FakeFetcher(),
    )
    assert reader is not None
    try:
        assert reader.port == port and reader.bind_host == "127.0.0.1"
        reader.start()
        assert _get(reader, "/healthz", token=None)[0] == 200
    finally:
        reader.stop()


# -- the platform's status survives a refused navigation ----------------------------------------
#
# WHAT THIS CLOSES, measured 26.09 on the live `/api/me.json`: Chrome refuses to commit a
# navigation on a 4xx/5xx with an empty body (ERR_HTTP_RESPONSE_CODE_FAILURE), `page.goto` exits
# with an exception and `response` never exists. The reader then returned `FetchFailed` without a
# number, a capsule 502 for EVERY cause at once. The client has the full ladder (403 with a reason,
# 403 without, 429, 5xx, a redirect to /login), but without the status it was dead: the product
# could not tell a rate limit from a suspended account, and result 1 of the 26.09 task needs that.
#
# The doubles raise `playwright_errors()[0]`, the class the fetch itself catches, so the tests run
# with and without Playwright installed.


def _navigation_error() -> type[BaseException]:
    return playwright_errors()[0]


class _FakeFrame:
    pass


class _FakeResponse:
    def __init__(self, url: str, status: int, frame: object, body: str = "") -> None:
        self.url = url
        self.status = status
        self.frame = frame
        self.headers = {"content-type": "application/json"}
        self._body = body

    def text(self) -> str:
        return self._body


class _FakePage:
    """Hands the response to the listener, then refuses the navigation, as Chrome does."""

    def __init__(self, status: int, body: str, *, subresource: bool = False) -> None:
        self.main_frame = _FakeFrame()
        self.url = ""
        self._status = status
        self._body = body
        self._subresource = subresource
        self._listeners: list[Any] = []
        self.closed = False

    def on(self, event: str, callback: Any) -> None:
        if event == "response":
            self._listeners.append(callback)

    def goto(self, url: str, **_: Any) -> Any:
        if self._subresource:
            # A subresource of the page: another frame, the same address. It must not be taken
            # for the main response.
            for callback in self._listeners:
                callback(_FakeResponse(url, 200, _FakeFrame(), "{}"))
        for callback in self._listeners:
            callback(_FakeResponse(url, self._status, self.main_frame, self._body))
        raise _navigation_error()(f"Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at {url}")

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        pass


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.contexts = [_FakeContext(page)]

    def close(self) -> None:
        pass


class _FakeDriver:
    def __init__(self, page: _FakePage) -> None:
        self.chromium = self
        self._page = page

    def connect_over_cdp(self, _endpoint: str, timeout: int | None = None) -> _FakeBrowser:
        return _FakeBrowser(self._page)


def _fetcher_with(page: _FakePage) -> CdpPageFetcher:
    fetcher = CdpPageFetcher("http://127.0.0.1:9222")
    fetcher.driver = lambda: _FakeDriver(page)  # type: ignore[method-assign]
    return fetcher


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, ""),
        (429, ""),
        (503, ""),
        (403, '{"reason": "private"}'),
    ],
)
def test_a_refused_navigation_keeps_the_platform_status(status: int, body: str) -> None:
    """Without this the client gets a 502 and cannot tell a pace from a refusal or an outage."""
    page = _FakePage(status, body)
    result = _fetcher_with(page).fetch("https://www.reddit.com/api/me.json", timeout_s=5)
    assert result.status == status, "the platform status was lost in the refused navigation"
    assert result.body == body
    assert page.closed, "the page is closed on the refusal path too"


def test_a_subresource_does_not_pose_as_the_main_response() -> None:
    """An image or a script has its own code; taking it for the read would mislead the ladder."""
    page = _FakePage(429, "", subresource=True)
    result = _fetcher_with(page).fetch("https://www.reddit.com/api/me.json", timeout_s=5)
    assert result.status == 429, "the result came from a subresource, not from the main frame"


def test_no_response_at_all_still_ends_as_a_fetch_failure() -> None:
    """The boundary of the fix: when there WAS no response, no status may be invented."""
    page = _FakePage(0, "")
    page._listeners = []  # the navigation fails before anything comes back

    def goto_without_response(url: str, **_: Any) -> Any:
        raise _navigation_error()("net::ERR_NAME_NOT_RESOLVED")

    page.goto = goto_without_response  # type: ignore[method-assign]
    with pytest.raises(FetchFailed):
        _fetcher_with(page).fetch("https://www.reddit.com/api/me.json", timeout_s=5)


# -- the brake after Reddit's 429 (27.09) -------------------------------------------------------

LISTING = "https%3A%2F%2Fwww.reddit.com%2Fr%2Fdevops%2Fnew.json%3Fraw_json%3D1%26limit%3D5"


def test_after_a_429_no_reddit_read_leaves_the_capsule_until_the_platforms_wait_is_over(
    running,
) -> None:
    """Measured 27.09: after an api restart the observer kept reading at the 2 s pace while
    Reddit answered 429, and every next job asked again. The answer during the brake is the one
    a 429 gets, so callers keep their handling, and Chrome is not asked at all."""
    server, fetcher, clock = running
    fetcher.result = FetchResult(
        status=429, final_url="u", content_type="text/html", body="", retry_after_s=120.0
    )
    assert _get(server, f"/read?url={LISTING}")[1]["status"] == 429
    assert len(fetcher.calls) == 1

    fetcher.result = FetchResult(status=200, final_url="u", content_type="x", body="{}")
    status, body = _get(server, f"/read?url={LISTING}")
    assert status == 200 and body["status"] == 429 and body["paced"] is True
    assert "429" in body["detail"]
    assert len(fetcher.calls) == 1, "w czasie hamulca nic nie wyszło do Reddita"
    health = _get(server, "/healthz")[1]
    assert health["reads_paced"] == 1 and 0 < health["reddit_paced_for_s"] <= 120

    clock.t += 121
    assert _get(server, f"/read?url={LISTING}")[1]["status"] == 200
    assert len(fetcher.calls) == 2, "po oknie Reddita odczyty wracają"


def test_without_a_wait_from_reddit_the_default_brake_applies_within_bounds() -> None:
    assert retry_after_seconds({"retry-after": "30"}) == 30.0
    assert retry_after_seconds({"x-ratelimit-reset": "412"}) == 412.0
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert retry_after_seconds({}) is None and retry_after_seconds(None) is None


def test_the_brake_covers_the_anonymous_viewer_too() -> None:
    """Both contexts leave from the same address; a stranger's read during the brake would ask the
    same limit again."""
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
        allow_anonymous=True,
        platform_cooldown_s=300.0,
    )
    server.start()
    try:
        fetcher.result = FetchResult(status=429, final_url="u", content_type=None, body="")
        _get(server, f"/read?url={LISTING}")
        _status, body = _get(server, f"/read?url={LISTING}&viewer=anonymous")
        assert body["status"] == 429 and body["paced"] is True
        assert len(fetcher.calls) == 1
        clock.t += 299
        assert _get(server, f"/read?url={LISTING}")[1].get("paced") is True
        clock.t += 2
        fetcher.result = FetchResult(status=200, final_url="u", content_type=None, body="{}")
        assert _get(server, f"/read?url={LISTING}")[1]["status"] == 200
    finally:
        server.stop()
