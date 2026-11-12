"""The X client of the capsule: every answer maps to one honest outcome.

No capsule and no network: `httpx.MockTransport` plays the capsule, and it plays it DUMB, returning
exactly the JSON the contract names. What is measured is the mapping, because that mapping is
where "not logged in", "X refused" and "nothing there" either stay three answers or collapse
into one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from community_observer.source import CredentialsMissing, SourceUnavailable
from community_observer.x_browser import (
    X_READ_PATH,
    XBrowserSource,
    XPlatformRefused,
    XReadTransient,
    canonical_handle,
    page_from_json,
    parse_status_url,
)
from community_os_contracts.session_verdict import ReadFailure, SessionVerdict

NOW = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)
BASE = "http://capsule:8765"


async def _instantly(_seconds: float) -> None:
    """The pause between a look at the session and its retry, without waiting for it."""


def _source(handler) -> XBrowserSource:
    def capsule(request: httpx.Request) -> httpx.Response:
        # The capsule's own control surface: already headful, so nothing is switched.
        if request.url.path == "/state":
            return httpx.Response(200, json={"mode": "INTERACTIVE", "display_only": True})
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(capsule))
    return XBrowserSource(BASE, token="t", client=client, clock=lambda: NOW, sleep=_instantly)


def _page(**fields) -> dict:
    page = {
        "requested_url": "https://x.com/home",
        "final_url": "https://x.com/home",
        "capability": "home",
        "anonymous": False,
        "viewer": None,
        "login_wall": False,
        "logged_in": False,
        "unavailable": None,
        "posts": [],
        "profile": None,
        "text": "",
        "limitations": [],
    }
    page.update(fields)
    return page


def test_handles_and_post_addresses_are_checked_before_any_request() -> None:
    assert canonical_handle("@demo_agent") == "demo_agent"
    for wrong in ("", "sixteen_chars_xx", "bad-handle", "@"):
        with pytest.raises(ValueError):
            canonical_handle(wrong)
    assert parse_status_url("https://x.com/jack/status/20?s=46") == ("jack", "20")
    assert parse_status_url("https://X.com/jack/status/20/") == ("jack", "20")
    for wrong in (
        "https://twitter.com/jack/status/20",
        "https://x.com/jack",
        "https://x.com/jack/status/abc",
        "http://x.com/jack/status/20",
    ):
        with pytest.raises(ValueError):
            parse_status_url(wrong)


async def test_a_logged_in_home_read_gives_the_handle_from_the_navigation() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_page(viewer="demo_agent", logged_in=True))

    state = await _source(handler).session_state()

    assert state.logged_in and state.handle == "demo_agent" and state.error is None
    assert seen[0].url.path == X_READ_PATH
    assert seen[0].url.params["url"] == "https://x.com/home"
    assert seen[0].headers["X-Wenext-Reader-Token"] == "t"


async def test_the_login_wall_is_a_missing_session_and_says_what_to_do() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_page(
                login_wall=True,
                final_url="https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fhome",
            ),
        )

    source = _source(handler)
    with pytest.raises(CredentialsMissing, match="Połącz X"):
        await source.home()
    state = await source.session_state()
    assert not state.logged_in and state.handle is None
    assert "ekran logowania" in (state.error or "")


async def test_an_unread_handle_is_not_called_a_lost_login() -> None:
    """No wall, no handle: our defect, not the human's job."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_page(limitations=["Nie odczytano uchwytu konta z nawigacji."])
        )

    state = await _source(handler).session_state()
    assert not state.logged_in
    assert "Sesja jest" in (state.error or "")


async def test_somebody_else_logged_in_is_not_the_expected_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page(viewer="SomeoneElse", logged_in=True))

    state = await _source(handler).verify_identity("@demo_agent")
    assert state.logged_in and state.handle == "SomeoneElse"
    assert "oczekuje @demo_agent" in (state.error or "")


async def test_a_platform_refusal_keeps_its_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"detail": "X odpowiedział 429", "platform_status": 429, "url": "u"}
        )

    with pytest.raises(XPlatformRefused) as refused:
        await _source(handler).search("agent commerce")
    assert refused.value.status == 429
    assert isinstance(refused.value, SourceUnavailable)


async def test_a_capsule_failure_is_unavailable_without_a_platform_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "Nie udało się odczytać X (TimeoutError)"})

    with pytest.raises(SourceUnavailable) as failed:
        await _source(handler).profile("jack")
    assert not isinstance(failed.value, XPlatformRefused)


async def test_an_anonymous_post_read_asks_for_a_fresh_context_and_tolerates_the_wall() -> None:
    """The visibility check reads as a stranger; a wall there is a fact about X, not a login."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_page(anonymous=True, login_wall=True))

    page = await _source(handler).read_post("https://x.com/jack/status/20", anonymous=True)
    assert page.login_wall and page.anonymous
    assert seen[0].url.params["viewer"] == "anonymous"
    assert seen[0].url.params["url"] == "https://x.com/jack/status/20"


def test_a_post_page_maps_focal_posts_metrics_and_unread_counters() -> None:
    page = page_from_json(
        _page(
            capability="post",
            viewer="demo_agent",
            logged_in=True,
            posts=[
                {"id": "10", "author": "op", "text": "question", "relation": "ancestor"},
                {
                    "id": "20",
                    "author": "jack",
                    "text": "the post",
                    "text_source": "post",
                    "relation": "focal",
                    "metrics": {"replies": 3, "likes": 12, "views": True},
                },
                {"id": "30", "author": "demo_agent", "text": "our reply", "relation": "reply"},
            ],
        ),
        requested="https://x.com/jack/status/20",
        anonymous=False,
    )
    assert page.focal is not None and page.focal.id == "20"
    # A boolean is not a count: an unreadable counter stays absent, never 1.
    assert page.focal.metrics == {"replies": 3, "likes": 12}
    assert [one.id for one in page.by_author("@demo_agent")] == ["30"]
    assert json.loads(json.dumps(page.focal.to_json()))["relation"] == "focal"


async def test_a_headless_capsule_is_switched_to_headful_before_the_first_read() -> None:
    """After a restart the capsule boots HEADLESS; X is read headful, like Reddit (D-026)."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/state":
            return httpx.Response(200, json={"mode": "HEADLESS"})
        if request.url.path == "/mode":
            return httpx.Response(200, json={"mode": "INTERACTIVE", "display_only": True})
        return httpx.Response(200, json=_page(viewer="demo_agent", logged_in=True))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = XBrowserSource(BASE, token="t", client=client, clock=lambda: NOW)
    await source.home()
    await source.home()
    assert calls[:3] == [("GET", "/state"), ("POST", "/mode"), ("GET", X_READ_PATH)]
    assert calls[3:] == [("GET", X_READ_PATH)], "przełączenie raz, nie przed każdym odczytem"


# ---------------------------------------------------------------------------------------------
# THREE ANSWERS, NOT TWO (13.10.2026): a look that failed is UNKNOWN, never "no session"
# ---------------------------------------------------------------------------------------------


class _Capsule:
    """A scripted capsule that counts the page reads it served (`/state` is not a read)."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.reads += 1
        answer = self.answers[min(self.reads, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _logged_in() -> httpx.Response:
    return httpx.Response(200, json=_page(viewer="demo_agent", logged_in=True))


def _unread() -> httpx.Response:
    return httpx.Response(200, json=_page(limitations=["Nie odczytano uchwytu konta z nawigacji."]))


def _wall() -> httpx.Response:
    return httpx.Response(200, json=_page(login_wall=True))


def _capsule_timeout() -> httpx.Response:
    return httpx.Response(
        504, json={"error": "browser_timeout", "detail": "nawigacja", "platform_status": None}
    )


async def test_a_timeout_is_unknown_and_is_retried_once_never_a_lost_session() -> None:
    """The incident: the capsule did not answer, and `x.read` told the agent the session of the
    account was gone. A timeout says nothing about the session; it is looked at once more, and when
    it persists it is reported as what it is."""
    capsule = _Capsule(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    state = await _source(capsule).session_state()
    assert capsule.reads == 2, "one retry, not one look and not an endless loop"
    assert state.verdict is SessionVerdict.UNKNOWN and state.unknown
    assert not state.session_lost and not state.login_wall and not state.logged_in
    assert state.failure == ReadFailure.TIMEOUT.value
    assert "nie odpowiedział w czasie" in (state.error or "")


async def test_a_hung_capsule_and_a_refusing_one_are_unknown_too() -> None:
    """The listen queue of the hung capsule-x was full: `ConnectTimeout`. A capsule that is down
    answers `ConnectError`. Neither is a statement about the session."""
    hung = _Capsule(httpx.ConnectTimeout("hung"), httpx.ConnectTimeout("hung"))
    state = await _source(hung).session_state()
    assert hung.reads == 2
    assert state.verdict is SessionVerdict.UNKNOWN
    assert state.failure == ReadFailure.TIMEOUT.value
    down = _Capsule(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    state = await _source(down).session_state()
    assert down.reads == 2
    assert state.verdict is SessionVerdict.UNKNOWN
    assert state.failure == ReadFailure.UNREACHABLE.value
    assert "czytnik kapsuły nieosiągalny" in (state.error or "")


async def test_the_second_look_settles_a_slow_first_one() -> None:
    """The capsule's own page deadline (504) on the first look, the account on the second."""
    capsule = _Capsule(_capsule_timeout(), _logged_in())
    state = await _source(capsule).session_state()
    assert capsule.reads == 2
    assert state.verdict is SessionVerdict.LOGGED_IN and state.handle == "demo_agent"
    assert state.failure is None and state.error is None


async def test_an_unread_handle_is_unknown_retried_once_and_then_reported_as_unreadable() -> None:
    capsule = _Capsule(_unread(), _unread())
    state = await _source(capsule).session_state()
    assert capsule.reads == 2
    assert state.verdict is SessionVerdict.UNKNOWN and not state.login_wall
    assert state.failure == ReadFailure.UNREADABLE.value
    # ... and a handle that shows up on the second look is a session.
    assert (await _source(_Capsule(_unread(), _logged_in())).session_state()).logged_in


async def test_a_login_wall_is_the_only_logged_out_and_is_not_retried() -> None:
    capsule = _Capsule(_wall(), _logged_in())
    state = await _source(capsule).session_state()
    assert capsule.reads == 1, "a wall is an ANSWER: looking again changes nothing"
    assert state.verdict is SessionVerdict.LOGGED_OUT and state.session_lost
    assert state.login_wall and state.failure is None


@pytest.mark.parametrize(
    ("answer", "failure"),
    [
        (
            httpx.Response(502, json={"detail": "X odpowiedział 429", "platform_status": 429}),
            ReadFailure.PLATFORM_REFUSED,
        ),
        (httpx.Response(401, json={"error": "token_rejected"}), ReadFailure.REJECTED),
        (httpx.Response(400, json={"detail": "url_refused"}), ReadFailure.REJECTED),
    ],
)
async def test_a_refusal_is_unknown_and_not_repeated(answer, failure) -> None:
    """The platform asking us to stop, or the capsule rejecting the request: more of the same is
    noise at best (and, for a 429, the exact thing the capsule's pacing exists to prevent)."""
    capsule = _Capsule(answer, _logged_in())
    state = await _source(capsule).session_state()
    assert capsule.reads == 1
    assert state.verdict is SessionVerdict.UNKNOWN and state.failure == failure.value


async def test_a_caller_with_a_short_budget_of_its_own_can_ask_for_no_retry() -> None:
    capsule = _Capsule(httpx.ReadTimeout("slow"), _logged_in())
    state = await _source(capsule).session_state(retries=0)
    assert capsule.reads == 1 and state.verdict is SessionVerdict.UNKNOWN
    # `verify_identity` passes it on.
    capsule = _Capsule(httpx.ReadTimeout("slow"), _logged_in())
    await _source(capsule).verify_identity("@demo_agent", retries=0)
    assert capsule.reads == 1


async def test_a_read_that_timed_out_raises_the_transient_class_with_its_kind() -> None:
    """`SourceUnavailable` for every old caller, and a kind for the ones that decide by it."""
    with pytest.raises(XReadTransient) as slow:
        await _source(_Capsule(httpx.ReadTimeout("slow"))).read("https://x.com/home")
    assert isinstance(slow.value, SourceUnavailable)
    assert slow.value.kind is ReadFailure.TIMEOUT
    with pytest.raises(XReadTransient) as gone:
        await _source(_Capsule(httpx.ConnectError("refused"))).read("https://x.com/home")
    assert gone.value.kind is ReadFailure.UNREACHABLE
    with pytest.raises(XReadTransient) as unavailable:
        await _source(_Capsule(httpx.Response(503, json={}))).read("https://x.com/home")
    assert unavailable.value.kind is ReadFailure.UNREACHABLE


async def test_the_state_says_its_verdict_in_json() -> None:
    state = await _source(_Capsule(httpx.ReadTimeout("slow"))).session_state(retries=0)
    payload = state.to_json()
    assert payload["verdict"] == "UNKNOWN" and payload["failure"] == "TIMEOUT"
    assert payload["login_wall"] is False
