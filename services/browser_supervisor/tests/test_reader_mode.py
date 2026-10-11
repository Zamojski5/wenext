"""`GET /state` and `POST /mode` on the reader's server — the control plane's way in (level 2).

Same token as `/read`, same single thread. A fake controller records what it was asked; the
tests measure the wire: token first, a controller-less server answers 404 (F0 behaviour), bad
bodies are 400, a refused reason is 400, a failed switch is 502, and a good switch returns the
controller's state.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from browser_supervisor.reader import (
    READER_TOKEN_HEADER,
    FetchResult,
    ModeSwitchFailed,
    ModeSwitchRefused,
    ReaderServer,
)

TOKEN = "sekret-testowy"


class FakeFetcher:
    def fetch(self, url: str, *, timeout_s: float) -> FetchResult:
        return FetchResult(200, url, "application/json", "{}")


class FakeController:
    def __init__(self) -> None:
        self.mode = "HEADLESS"
        self.reason: str | None = None
        self.display_only = False
        self.calls: list[tuple[str, str | None, bool]] = []
        self.raise_with: Exception | None = None

    def state(self) -> dict:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "display_only": self.display_only,
            "alive": True,
            "vnc_ready": self.mode == "INTERACTIVE",
        }

    def switch(self, mode: str, reason: str | None, display_only: bool = False) -> dict:
        self.calls.append((mode, reason, display_only))
        if self.raise_with is not None:
            raise self.raise_with
        self.mode, self.reason, self.display_only = mode, reason, display_only
        return dict(self.state(), changed=True)


def _server(controller: FakeController | None) -> ReaderServer:
    return ReaderServer(
        bind_host="127.0.0.1",
        port=0,
        token=TOKEN,
        fetcher=FakeFetcher(),
        min_interval_s=0,
        controller=controller,
    )


@pytest.fixture
def running() -> Iterator[tuple[ReaderServer, FakeController]]:
    controller = FakeController()
    server = _server(controller)
    server.start()
    try:
        yield server, controller
    finally:
        server.stop()


def _call(
    server: ReaderServer, method: str, path: str, *, token: str | None = TOKEN, body: object = None
) -> tuple[int, dict]:
    headers = {READER_TOKEN_HEADER: token} if token is not None else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}", method=method, headers=headers, data=data
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_state_and_mode_need_the_token(running) -> None:
    server, controller = running
    assert _call(server, "GET", "/state", token=None)[0] == 401
    assert _call(server, "GET", "/state", token="zly")[0] == 401
    assert _call(server, "POST", "/mode", token=None, body={"mode": "INTERACTIVE"})[0] == 401
    assert controller.calls == []


def test_state_returns_the_controllers_view(running) -> None:
    server, _ = running
    status, body = _call(server, "GET", "/state")
    assert status == 200
    assert body == {
        "mode": "HEADLESS",
        "reason": None,
        "display_only": False,
        "alive": True,
        "vnc_ready": False,
    }


def test_a_server_without_a_controller_has_no_mode_surface() -> None:
    server = _server(None)
    server.start()
    try:
        assert _call(server, "GET", "/state") == (404, {"error": "no_mode_controller"})
        assert _call(server, "POST", "/mode", body={"mode": "HEADLESS"})[0] == 404
    finally:
        server.stop()


@pytest.mark.parametrize(
    "body",
    [b"nie json", {"reason": "FIRST_LOGIN"}, {"mode": 7}, {"mode": "INTERACTIVE", "reason": 3}, []],
)
def test_a_malformed_mode_request_is_400_and_touches_nothing(running, body: object) -> None:
    server, controller = running
    status, payload = _call(server, "POST", "/mode", body=body)
    assert status == 400 and payload["error"] == "bad_request"
    assert controller.calls == []


def test_a_refused_switch_is_400_and_a_failed_one_is_502(running) -> None:
    server, controller = running
    controller.raise_with = ModeSwitchRefused("powód spoza listy")
    status, payload = _call(server, "POST", "/mode", body={"mode": "INTERACTIVE", "reason": "X"})
    assert status == 400 and payload == {"error": "mode_refused", "detail": "powód spoza listy"}

    controller.raise_with = ModeSwitchFailed("Xtigervnc nie wystartował")
    status, payload = _call(
        server, "POST", "/mode", body={"mode": "INTERACTIVE", "reason": "FIRST_LOGIN"}
    )
    assert status == 502 and payload["error"] == "mode_switch_failed"
    assert "Xtigervnc" in payload["detail"]

    controller.raise_with = RuntimeError("cokolwiek")
    status, payload = _call(server, "POST", "/mode", body={"mode": "HEADLESS"})
    assert status == 502 and "RuntimeError" in payload["detail"]


def test_a_good_switch_returns_the_new_state(running) -> None:
    server, controller = running
    status, payload = _call(
        server, "POST", "/mode", body={"mode": "INTERACTIVE", "reason": "FIRST_LOGIN"}
    )
    assert status == 200
    assert payload == {
        "mode": "INTERACTIVE",
        "reason": "FIRST_LOGIN",
        "display_only": False,
        "alive": True,
        "vnc_ready": True,
        "changed": True,
    }
    assert controller.calls == [("INTERACTIVE", "FIRST_LOGIN", False)]
    status, payload = _call(server, "POST", "/mode", body={"mode": "HEADLESS"})
    assert status == 200 and payload["mode"] == "HEADLESS"
    assert _call(server, "GET", "/state")[1]["mode"] == "HEADLESS"


def test_unknown_paths_are_404_for_both_methods(running) -> None:
    server, _ = running
    assert _call(server, "GET", "/nic", token=None)[0] == 404
    assert _call(server, "POST", "/read", body={})[0] == 404


def test_display_only_travels_to_the_controller_and_must_be_a_bool(running) -> None:
    """D-026: `{"mode": "INTERACTIVE", "display_only": true}` — headful rendering, no human."""
    server, controller = running
    status, payload = _call(
        server, "POST", "/mode", body={"mode": "INTERACTIVE", "display_only": True}
    )
    assert status == 200 and payload["display_only"] is True and payload["reason"] is None
    assert controller.calls == [("INTERACTIVE", None, True)]
    status, payload = _call(
        server, "POST", "/mode", body={"mode": "INTERACTIVE", "display_only": "yes"}
    )
    assert status == 400 and payload["error"] == "bad_request"
    assert len(controller.calls) == 1, "zły typ nie dociera do kontrolera"
