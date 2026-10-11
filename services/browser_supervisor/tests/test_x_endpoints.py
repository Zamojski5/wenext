"""The X routes on the reader's server: `/act/x/reply`, `/act/x/post`, `/x/read`, and the
`start_url` of `/mode`, over real HTTP on the loopback.

What is measured: the guards in front of each route and their order (token, gate, body), the one
outcome map shared with `/act/comment`, the X pace (`x_throttle`, on a fake clock), and that the
page opened for the human is left open while the CDP connection is released. `/x/read` and
`start_url` run through a REAL `CdpPageFetcher` whose driver is the dumb fake, so the handler's
glue to the fetcher is exercised, not stubbed away.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from browser_supervisor.reader import (
    ACT_X_POST_PATH,
    ACT_X_REPLY_PATH,
    DEFAULT_X_MIN_INTERVAL_S,
    LOGIN_START_URLS,
    READER_TOKEN_HEADER,
    X_READ_PATH,
    CdpPageFetcher,
    FetchResult,
    ReaderConfigError,
    ReaderServer,
    build_reader_from_env,
)
from browser_supervisor.writer import (
    WRITER_TOKEN_HEADER,
    CommentBlocked,
    CommentRefused,
    CommentUnknown,
)
from browser_supervisor.x_writer import XPostResult
from x_fake_browser import Browser, Clock, Driver, Page, XPage, post_article

TOKEN = "reader-secret"
WRITER = "writer-secret"
POST_ID = "1790000000000000100"
REPLY_BODY = {
    "post_url": f"https://x.com/jack/status/{POST_ID}",
    "post_id": POST_ID,
    "text": "A schema fixes argument hallucination, not authority.",
    "account": "Demo_Maint",
}
OWN_POST_BODY = {"text": "Three things we learned.", "account": "Demo_Maint"}


class FakeFetcher:
    def fetch(self, url: str, *, timeout_s: float, anonymous: bool = False) -> FetchResult:
        return FetchResult(200, url, "application/json", "{}")


class FakeController:
    def __init__(self) -> None:
        self.current: dict[str, Any] = {
            "mode": "INTERACTIVE",
            "display_only": True,
            "human_steer_open": False,
            "alive": True,
        }
        self.calls: list[tuple[str, str | None, bool]] = []
        self.start_urls: list[str | None] = []
        self.unchanged = False

    def state(self) -> Mapping[str, Any]:
        return dict(self.current)

    def switch(
        self,
        mode: str,
        reason: str | None,
        display_only: bool = False,
        start_url: str | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append((mode, reason, display_only))
        self.start_urls.append(start_url)
        self.current = {
            "mode": mode,
            "reason": reason,
            "display_only": display_only,
            "human_steer_open": mode == "INTERACTIVE" and not display_only,
            "alive": True,
        }
        return dict(self.current, changed=not self.unchanged)


class FakeRedditActuator:
    """Present because the writer token requires the Reddit actuator (an existing rule)."""

    def comment(self, **kwargs: Any) -> Any:
        raise AssertionError("an X route must never reach the Reddit actuator")

    def own_post(self, **kwargs: Any) -> Any:
        raise AssertionError("an X route must never reach the Reddit actuator")


class FakeXActuator:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.raise_with: Exception | None = None

    def _answer(self, kind: str, kwargs: dict[str, Any]) -> XPostResult:
        self.calls.append((kind, kwargs, self.clock()))
        if self.raise_with is not None:
            raise self.raise_with
        return XPostResult(
            post_id="1790000000000000777",
            permalink="https://x.com/Demo_Maint/status/1790000000000000777",
            account=kwargs["account"],
            observed_at="2026-09-26T12:00:00+00:00",
            raw={"source": "toast"},
        )

    def reply(self, **kwargs: Any) -> XPostResult:
        return self._answer("reply", kwargs)

    def own_post(self, **kwargs: Any) -> XPostResult:
        return self._answer("own_post", kwargs)


@pytest.fixture
def acting() -> Iterator[tuple[ReaderServer, FakeXActuator, FakeController, Clock]]:
    clock = Clock()
    x_actuator = FakeXActuator(clock)
    controller = FakeController()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        min_interval_s=0.0,
        clock=clock,
        sleep=clock.sleep,
        controller=controller,
        writer_token=WRITER,
        actuator=FakeRedditActuator(),
        x_actuator=x_actuator,
        act_timeout_s=3.0,
    )
    server.start()
    try:
        yield server, x_actuator, controller, clock
    finally:
        server.stop()


def _call(
    server: ReaderServer,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    data = None
    all_headers = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}", method=method, data=data, headers=all_headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def _act(server: ReaderServer, path: str, body: Any, *, token: str | None = WRITER, header=None):
    headers = {} if token is None else {header or WRITER_TOKEN_HEADER: token}
    return _call(server, "POST", path, body=body, headers=headers)


# -- /act/x/reply and /act/x/post: the guards, in order ---------------------------------------------

ROUTES = [(ACT_X_REPLY_PATH, REPLY_BODY), (ACT_X_POST_PATH, OWN_POST_BODY)]


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_only_the_writer_token_opens_an_x_write(acting, path: str, body: dict) -> None:
    server, x_actuator, _, _ = acting
    assert _act(server, path, body, token=None)[0] == 401
    assert _act(server, path, body, token=TOKEN)[0] == 401, "the reader's token is not the writer's"
    assert _act(server, path, body, token=TOKEN, header=READER_TOKEN_HEADER)[0] == 401
    assert x_actuator.calls == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    "state",
    [
        {"mode": "HEADLESS", "display_only": False, "human_steer_open": False, "alive": True},
        {"mode": "INTERACTIVE", "display_only": False, "human_steer_open": True, "alive": True},
        {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": True, "alive": True},
        {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": False, "alive": False},
    ],
)
def test_without_display_only_and_a_closed_steer_nothing_is_written_on_x(
    acting, path: str, body: dict, state: dict
) -> None:
    """The SAME gate as `/act/comment`: a human at the screen and an automation writing into the
    same profile is the one thing D-026 forbids, on X as on Reddit."""
    server, x_actuator, controller, _ = acting
    controller.current = state
    status, payload = _act(server, path, body)
    assert status == 409 and payload["error"] == "capsule_not_display_only", payload
    assert x_actuator.calls == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_every_field_is_required(acting, path: str, body: dict) -> None:
    server, x_actuator, _, _ = acting
    for missing in body:
        partial = {key: value for key, value in body.items() if key != missing}
        assert _act(server, path, partial)[0] == 400, missing
        assert _act(server, path, {**body, missing: ""})[0] == 400, missing
    assert _act(server, path, b"not json")[0] == 400
    assert x_actuator.calls == []


def test_a_reply_reaches_the_actuator_once_with_the_consented_fields(acting) -> None:
    server, x_actuator, _, _ = acting
    status, payload = _act(server, ACT_X_REPLY_PATH, REPLY_BODY)
    assert status == 200, payload
    assert payload == {
        "post_id": "1790000000000000777",
        "permalink": "https://x.com/Demo_Maint/status/1790000000000000777",
        "account": "Demo_Maint",
        "observed_at": "2026-09-26T12:00:00+00:00",
        "submit_started": True,
        "raw": {"source": "toast"},
    }
    assert [(kind, kwargs) for kind, kwargs, _ in x_actuator.calls] == [
        ("reply", {**REPLY_BODY, "timeout_s": 3.0})
    ]


def test_an_own_post_reaches_the_actuator_with_text_and_account_only(acting) -> None:
    server, x_actuator, _, _ = acting
    status, payload = _act(server, ACT_X_POST_PATH, OWN_POST_BODY)
    assert status == 200 and payload["post_id"] == "1790000000000000777"
    assert [(kind, kwargs) for kind, kwargs, _ in x_actuator.calls] == [
        ("own_post", {**OWN_POST_BODY, "timeout_s": 3.0, "image": None, "image_type": None})
    ]


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_an_own_post_carries_one_image_as_bytes_with_its_type(acting) -> None:
    """27.09: a news post with a screenshot. The image arrives as base64 and leaves as bytes."""
    import base64

    server, x_actuator, _, _ = acting
    body = {**OWN_POST_BODY, "image_b64": base64.b64encode(PNG).decode(), "image_type": "image/png"}
    status, payload = _act(server, ACT_X_POST_PATH, body)
    assert status == 200, payload
    [(kind, kwargs, _)] = x_actuator.calls
    assert kind == "own_post" and kwargs["image"] == PNG and kwargs["image_type"] == "image/png"


@pytest.mark.parametrize(
    "extra",
    [
        {"image_b64": "not base64 at all!"},
        {"image_type": "image/png"},
        {"image_b64": "iVBORw0KGgo="},
    ],
)
def test_a_half_or_broken_image_is_refused_before_the_actuator(acting, extra: dict) -> None:
    server, x_actuator, _, _ = acting
    body = {**OWN_POST_BODY, **extra}
    if "image_b64" in extra and "image_type" not in extra and extra["image_b64"] != "iVBORw0KGgo=":
        body["image_type"] = "image/png"
    status, _payload = _act(server, ACT_X_POST_PATH, body)
    assert status == 400
    assert x_actuator.calls == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    ("failure", "status", "error", "started"),
    [
        (CommentRefused("text over the limit"), 409, "refused", False),
        (CommentBlocked("another account", submit_started=False), 423, "blocked", False),
        (CommentBlocked("daily limit", submit_started=True), 423, "blocked", True),
        (CommentUnknown("no evidence", submit_started=True), 504, "unknown", True),
        (RuntimeError("cdp gone"), 502, "browser_unavailable", None),
    ],
)
def test_the_outcome_map_is_the_one_of_act_comment(
    acting, path: str, body: dict, failure: Exception, status: int, error: str, started
) -> None:
    server, x_actuator, _, _ = acting
    x_actuator.raise_with = failure
    got, payload = _act(server, path, body)
    assert (got, payload["error"], payload["submit_started"]) == (status, error, started), payload
    assert str(failure) in payload["detail"]


def test_without_an_x_actuator_the_x_routes_are_404() -> None:
    clock = Clock()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        clock=clock,
        sleep=clock.sleep,
        controller=FakeController(),
        writer_token=WRITER,
        actuator=FakeRedditActuator(),
    )
    server.start()
    try:
        for path, body in ROUTES:
            assert _act(server, path, body) == (404, {"error": "no_actuator"})
    finally:
        server.stop()


# -- the X pace -------------------------------------------------------------------------------------


def test_x_page_loads_keep_their_own_minimum_interval() -> None:
    """MEASURED on 26.09: after several consecutive X reads X answered 403 to everything. The
    X pace is separate from the generic one and counts from the START of the previous X load."""
    clock = Clock()
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        min_interval_s=0.0,
        clock=clock,
        sleep=clock.sleep,
        x_min_interval_s=8.0,
    )
    try:
        inner = server._server
        inner.x_throttle()
        assert clock.sleeps == [], "the first X load does not wait"
        clock.now += 3.0
        inner.x_throttle()
        assert clock.sleeps == [5.0], "the second waits out the rest of the 8 s"
        clock.now += 20.0
        inner.x_throttle()
        assert clock.sleeps == [5.0], "after a long gap nobody waits"
        inner.throttle()
        assert clock.sleeps == [5.0], "the generic pace (0 s here) is a separate clock"
    finally:
        server._server.server_close()


def test_two_x_writes_in_a_row_are_spaced_by_the_x_interval(acting) -> None:
    server, x_actuator, _, _ = acting
    assert _act(server, ACT_X_REPLY_PATH, REPLY_BODY)[0] == 200
    assert _act(server, ACT_X_POST_PATH, OWN_POST_BODY)[0] == 200
    first, second = (at for _, _, at in x_actuator.calls)
    assert second - first >= DEFAULT_X_MIN_INTERVAL_S


# -- /x/read ----------------------------------------------------------------------------------------


class TimedXPage(XPage):
    """An X page that notes WHEN it was opened, on the test's clock."""

    def __init__(self, *args: Any, clock: Clock, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock
        self.opened_at: float | None = None

    def goto(self, url: str, wait_until: str | None = None, timeout: float | None = None) -> Any:
        self.opened_at = self._clock()
        return super().goto(url, wait_until=wait_until, timeout=timeout)


def _cdp_fetcher(driver: Driver) -> CdpPageFetcher:
    fetcher = CdpPageFetcher("http://127.0.0.1:9222")
    fetcher.driver = lambda: driver  # type: ignore[method-assign]
    return fetcher


def _reading_server(pages: list[Page], clock: Clock, *, anonymous_pages=None, allow_anonymous=True):
    browser = Browser(pages, fresh_pages=anonymous_pages)
    driver = Driver(browser)
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=_cdp_fetcher(driver),
        min_interval_s=0.0,
        clock=clock,
        sleep=clock.sleep,
        allow_anonymous=allow_anonymous,
    )
    server.start()
    return server, browser, driver


def _read(server: ReaderServer, query: str, *, token: str | None = TOKEN) -> tuple[int, dict]:
    headers = {} if token is None else {READER_TOKEN_HEADER: token}
    return _call(server, "GET", f"{X_READ_PATH}?{query}", headers=headers)


def _q(**params: str) -> str:
    return urllib.parse.urlencode(params)


def test_a_read_answers_with_the_contract_and_a_null_platform_status() -> None:
    clock = Clock()
    page = XPage([post_article("jack", POST_ID, "hello")], viewer="Demo_Maint")
    server, browser, _ = _reading_server([page], clock)
    try:
        status, payload = _read(server, _q(url=f"https://x.com/jack/status/{POST_ID}"))
    finally:
        server.stop()
    assert status == 200, payload
    assert payload["platform_status"] is None
    assert set(payload) == {
        "requested_url",
        "final_url",
        "capability",
        "anonymous",
        "viewer",
        "login_wall",
        "logged_in",
        "unavailable",
        "posts",
        "profile",
        "text",
        "limitations",
        "platform_status",
    }
    assert payload["capability"] == "post" and payload["viewer"] == "Demo_Maint"
    assert payload["posts"][0]["relation"] == "focal"
    assert page.closed and browser.closed


def test_a_refused_navigation_is_502_with_the_numeric_platform_status() -> None:
    clock = Clock()
    page = XPage(captured_status=403, goto_error=RuntimeError("ERR_HTTP_RESPONSE_CODE_FAILURE"))
    server, _, _ = _reading_server([page], clock)
    try:
        status, payload = _read(server, _q(url="https://x.com/OpenAI"))
    finally:
        server.stop()
    assert status == 502
    assert payload["platform_status"] == 403 and payload["error"] == "platform_refused"


@pytest.mark.parametrize(
    "query",
    [
        "",
        _q(viewer="anonymous"),
        "url=https%3A%2F%2Fx.com%2Fhome&url=https%3A%2F%2Fx.com%2Fexplore",
        _q(url="https://x.com/home", extra="1"),
    ],
)
def test_a_malformed_read_request_is_400_and_opens_nothing(query: str) -> None:
    clock = Clock()
    server, _, driver = _reading_server([], clock)
    try:
        status, payload = _read(server, query)
    finally:
        server.stop()
    assert status == 400 and payload["error"] == "bad_request"
    assert driver.connects == []


def test_a_refused_address_is_400_before_any_wait_or_navigation() -> None:
    clock = Clock()
    server, _, driver = _reading_server([], clock)
    try:
        status, payload = _read(server, _q(url="https://x.com/logout"))
    finally:
        server.stop()
    assert status == 400 and payload["error"] == "url_refused"
    assert driver.connects == [] and clock.sleeps == []


def test_the_viewer_parameter_means_what_it_means_on_read() -> None:
    clock = Clock()
    server, _, driver = _reading_server([], clock, allow_anonymous=False)
    try:
        assert _read(server, _q(url="https://x.com/home", viewer="nobody"))[1]["error"] == (
            "viewer_refused"
        )
        status, payload = _read(server, _q(url="https://x.com/OpenAI", viewer="anonymous"))
        assert status == 400 and payload["error"] == "viewer_refused"
        assert "WENEXT_READER_ALLOW_ANONYMOUS" in payload["detail"]
    finally:
        server.stop()
    assert driver.connects == []


def test_an_anonymous_read_goes_through_a_fresh_context() -> None:
    clock = Clock()
    fresh = XPage([post_article("OpenAI", POST_ID, "launch", logged_in=False)])
    server, browser, _ = _reading_server([], clock, anonymous_pages=[fresh])
    try:
        status, payload = _read(server, _q(url="https://x.com/OpenAI", viewer="anonymous"))
    finally:
        server.stop()
    assert status == 200 and payload["anonymous"] is True and payload["viewer"] is None
    assert browser.new_contexts == 1 and browser.contexts[0].opened == []


def test_the_read_needs_the_reader_token() -> None:
    clock = Clock()
    server, _, driver = _reading_server([], clock)
    try:
        assert _read(server, _q(url="https://x.com/home"), token=None)[0] == 401
        assert _read(server, _q(url="https://x.com/home"), token=WRITER)[0] == 401
    finally:
        server.stop()
    assert driver.connects == []


def test_a_server_without_a_browser_answers_503() -> None:
    server = ReaderServer(bind_host="127.0.0.1", port=0, token=TOKEN, fetcher=FakeFetcher())
    server.start()
    try:
        assert _read(server, _q(url="https://x.com/home"))[0] == 503
    finally:
        server.stop()


def test_two_reads_in_a_row_are_spaced_by_the_x_interval() -> None:
    clock = Clock()
    pages = [
        TimedXPage([post_article("jack", POST_ID, "a")], viewer="Demo_Maint", clock=clock),
        TimedXPage([post_article("jack", POST_ID, "b")], viewer="Demo_Maint", clock=clock),
    ]
    server, _, _ = _reading_server(pages, clock)
    try:
        assert _read(server, _q(url="https://x.com/jack"))[0] == 200
        assert _read(server, _q(url="https://x.com/jack"))[0] == 200
    finally:
        server.stop()
    first, second = (page.opened_at for page in pages)
    assert first is not None and second is not None
    assert second - first >= DEFAULT_X_MIN_INTERVAL_S


# -- /mode with start_url ---------------------------------------------------------------------------


def _mode_server(pages: list[Page], *, fetcher=None, refuse: Exception | None = None):
    browser = Browser(pages)
    driver = Driver(browser, refuse=refuse)
    controller = FakeController()
    controller.current = {"mode": "HEADLESS", "display_only": False, "alive": True}
    server = ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=fetcher or _cdp_fetcher(driver),
        min_interval_s=0.0,
        controller=controller,
    )
    server.start()
    return server, controller, browser


def _mode(server: ReaderServer, body: Any) -> tuple[int, dict]:
    return _call(server, "POST", "/mode", body=body, headers={READER_TOKEN_HEADER: TOKEN})


@pytest.mark.parametrize("start_url", sorted(LOGIN_START_URLS))
def test_the_login_page_is_the_first_tab_of_the_launch_and_nothing_attaches(start_url: str) -> None:
    """27.09: Google refused a sign-in in a browser with a DevTools endpoint. The page is handed
    to the person's launch itself; the capsule opens nothing through CDP."""
    login_tab = Page()
    server, controller, browser = _mode_server([login_tab])
    try:
        status, payload = _mode(
            server, {"mode": "INTERACTIVE", "reason": "FIRST_LOGIN", "start_url": start_url}
        )
    finally:
        server.stop()
    assert status == 200, payload
    assert controller.calls == [("INTERACTIVE", "FIRST_LOGIN", False)]
    assert controller.start_urls == [start_url]
    assert payload["mode"] == "INTERACTIVE" and payload["changed"] is True
    assert payload["start_url"] == {"url": start_url, "opened": True, "final_url": start_url}
    assert login_tab.goto_calls == [] and browser.contexts[0].opened == [], "nic przez CDP"


@pytest.mark.parametrize(
    "start_url",
    [
        "https://x.com/i/flow/login?redirect_after_login=%2Fhome",
        "https://x.com/logout",
        "https://evil.example/login",
        "http://x.com/i/flow/login",
        "https://www.reddit.com/login",
        7,
        "",
    ],
)
def test_any_other_start_url_is_400_and_nothing_switches(start_url: Any) -> None:
    server, controller, browser = _mode_server([Page()])
    try:
        status, payload = _mode(
            server, {"mode": "INTERACTIVE", "reason": "FIRST_LOGIN", "start_url": start_url}
        )
    finally:
        server.stop()
    assert status == 400 and payload["error"] == "bad_request"
    assert controller.calls == [] and browser.contexts[0].opened == []


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "HEADLESS", "start_url": "https://x.com/i/flow/login"},
        {"mode": "INTERACTIVE", "display_only": True, "start_url": "https://x.com/i/flow/login"},
        {"mode": "INTERACTIVE", "start_url": "https://x.com/i/flow/login"},
    ],
)
def test_a_start_url_without_a_human_at_the_screen_is_400(body: dict) -> None:
    """Nobody to show the page to: accepting it silently would tell the caller a page opened."""
    server, controller, _ = _mode_server([Page()])
    try:
        status, payload = _mode(server, body)
    finally:
        server.stop()
    assert status == 400 and payload["error"] == "bad_request"
    assert controller.calls == []


def test_a_window_already_open_is_left_as_the_person_has_it() -> None:
    """A second "Połącz X" while the person's window is up: nothing restarts, and the answer does
    not claim a page it did not open."""
    server, controller, _ = _mode_server([Page()])
    controller.unchanged = True
    try:
        status, payload = _mode(
            server,
            {
                "mode": "INTERACTIVE",
                "reason": "FIRST_LOGIN",
                "start_url": "https://x.com/i/flow/login",
            },
        )
    finally:
        server.stop()
    assert status == 200 and payload["mode"] == "INTERACTIVE"
    assert payload["start_url"]["opened"] is False
    assert "już otwarte" in payload["start_url"]["detail"]


def test_the_page_does_not_depend_on_a_cdp_fetcher_any_more() -> None:
    """Before 27.09 the page was opened through CDP, so no CDP fetcher meant no page. The page is
    now the launch's own first tab, so it opens with no CDP at all."""
    server, controller, _ = _mode_server([], fetcher=FakeFetcher())
    try:
        status, payload = _mode(
            server,
            {
                "mode": "INTERACTIVE",
                "reason": "FIRST_LOGIN",
                "start_url": "https://x.com/i/flow/login",
            },
        )
    finally:
        server.stop()
    assert status == 200 and payload["start_url"]["opened"] is True
    assert controller.start_urls == ["https://x.com/i/flow/login"]


def test_a_switch_without_start_url_answers_exactly_as_before() -> None:
    server, _, browser = _mode_server([Page()])
    try:
        status, payload = _mode(server, {"mode": "INTERACTIVE", "reason": "FIRST_LOGIN"})
    finally:
        server.stop()
    assert status == 200 and "start_url" not in payload
    assert browser.contexts[0].opened == []


# -- configuration ----------------------------------------------------------------------------------


def test_the_x_actuator_needs_the_writer_token() -> None:
    clock = Clock()
    with pytest.raises(ReaderConfigError):
        ReaderServer(
            bind_host="127.0.0.1",
            port=0,
            token=TOKEN,
            fetcher=FakeFetcher(),
            x_actuator=FakeXActuator(clock),
        )


def test_the_environment_decides_the_x_actuator_and_the_x_pace() -> None:
    clock = Clock()
    base = {"WENEXT_READER_PORT": "8766", "WENEXT_READER_TOKEN": TOKEN}
    without = build_reader_from_env(
        base, fetcher=FakeFetcher(), actuator=FakeRedditActuator(), x_actuator=FakeXActuator(clock)
    )
    assert without is not None
    try:
        assert without._server.x_actuator is None, "no writer token, no X writes either"
        assert without._server.x_min_interval_s == DEFAULT_X_MIN_INTERVAL_S
    finally:
        without._server.server_close()
    configured = build_reader_from_env(
        {**base, "WENEXT_WRITER_TOKEN": WRITER, "WENEXT_X_MIN_INTERVAL_SECONDS": "12"},
        fetcher=FakeFetcher(),
        actuator=FakeRedditActuator(),
        x_actuator=FakeXActuator(clock),
    )
    assert configured is not None
    try:
        assert configured._server.x_actuator is not None
        assert configured._server.x_min_interval_s == 12.0
    finally:
        configured._server.server_close()
    with pytest.raises(ReaderConfigError):
        build_reader_from_env(
            {**base, "WENEXT_X_MIN_INTERVAL_SECONDS": "-1"}, fetcher=FakeFetcher()
        )
