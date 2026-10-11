"""Tests of the safe handling of DevToolsActivePort.

They do not need Chrome — they substitute a local HTTP server pretending to be CDP.
Thanks to that they guard exactly the logic that decides whether the Gateway attaches
to a live browser or to a corpse.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from browser_supervisor import devtools_port as dp
from browser_supervisor.devtools_port import MissingDevToolsPort, StaleDevToolsPort

EXPECTED = "Chrome/153.0.8010.12"
BROWSER_UUID = "0bbae64d-dd8f-4dbf-a98b-0a50097eed25"
WS_PATH = f"/devtools/browser/{BROWSER_UUID}"


class _FakeCDP(BaseHTTPRequestHandler):
    """A server pretending to be `/json/version`. Parameters injected via class attributes."""

    browser = EXPECTED
    ws_path = WS_PATH

    def do_GET(self):  # the required BaseHTTPRequestHandler interface
        if self.path != "/json/version":
            self.send_response(404)
            self.end_headers()
            return
        port = self.server.server_address[1]
        content = json.dumps(
            {
                "Browser": self.browser,
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}{self.ws_path}",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):  # silences the logs in the test output
        return


@pytest.fixture
def fake_cdp():
    """Starts the server and returns a function that configures its answer."""
    servers: list[HTTPServer] = []

    def start_server(*, browser: str = EXPECTED, ws_path: str = WS_PATH) -> int:
        klasa = type("_Handler", (_FakeCDP,), {"browser": browser, "ws_path": ws_path})
        serwer = HTTPServer(("127.0.0.1", 0), klasa)
        servers.append(serwer)
        threading.Thread(target=serwer.serve_forever, daemon=True).start()
        return serwer.server_address[1]

    yield start_server
    for serwer in servers:
        serwer.shutdown()


def _write_port_file(directory: Path, port: int, ws_path: str = WS_PATH) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    dp.port_file_path(directory).write_text(f"{port}\n{ws_path}\n", encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_live_endpoint_passes(tmp_path: Path, fake_cdp) -> None:
    port = fake_cdp()
    _write_port_file(tmp_path, port)

    endpoint = dp.verify(tmp_path, expected_browser=EXPECTED)

    assert endpoint.port == port
    assert endpoint.browser_path == WS_PATH
    assert endpoint.http_base == f"http://127.0.0.1:{port}"


def test_stale_port_after_kill_9_is_rejected(tmp_path: Path) -> None:
    """The most important test in this file.

    Measured live: after `kill -9` the file stays and points at a dead port.
    The verification MUST detect this, otherwise the Gateway attaches to a browser that
    does not exist.
    """
    _write_port_file(tmp_path, _free_port())

    with pytest.raises(StaleDevToolsPort, match="nie odpowiada"):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_port_taken_over_by_another_process_is_rejected(tmp_path: Path, fake_cdp) -> None:
    """The bare fact that the port answers is NOT enough.

    The port number may have been reused by a different browser. What decides is the
    identifier from the file's second line.
    """
    port = fake_cdp(ws_path="/devtools/browser/ffffffff-ffff-ffff-ffff-ffffffffffff")
    _write_port_file(tmp_path, port, ws_path=WS_PATH)

    with pytest.raises(StaleDevToolsPort, match="identyfikator przeglądarki się nie zgadza"):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_mismatched_build_is_rejected(tmp_path: Path, fake_cdp) -> None:
    """Rejecting a mismatched build (L5159, L2308)."""
    port = fake_cdp(browser="Chrome/152.0.7977.64")
    _write_port_file(tmp_path, port)

    with pytest.raises(StaleDevToolsPort, match="Niezgodny build"):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_incomplete_port_file_is_rejected(tmp_path: Path) -> None:
    """A write interrupted by a crash gives a file with a single line."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    dp.port_file_path(tmp_path).write_text("43823\n", encoding="utf-8")

    with pytest.raises(StaleDevToolsPort, match="linii zamiast dwóch"):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_garbage_instead_of_port_number_is_rejected(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dp.port_file_path(tmp_path).write_text("nie-numer\n/devtools/browser/x\n", encoding="utf-8")

    with pytest.raises(StaleDevToolsPort, match="nie jest numerem portu"):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_missing_port_file(tmp_path: Path) -> None:
    with pytest.raises(MissingDevToolsPort):
        dp.verify(tmp_path, expected_browser=EXPECTED)


def test_remove_if_exists_reports_deletion(tmp_path: Path) -> None:
    _write_port_file(tmp_path, 12345)
    assert dp.remove_if_exists(tmp_path) is True
    assert dp.remove_if_exists(tmp_path) is False


def test_waiting_fails_when_nothing_starts(tmp_path: Path) -> None:
    with pytest.raises(MissingDevToolsPort, match="nie pojawił się"):
        dp.wait_for_endpoint(tmp_path, expected_browser=EXPECTED, timeout_s=1.0, interval_s=0.1)


def test_a_launch_closes_the_tabs_a_restart_brought_back_and_keeps_one() -> None:
    """27.09: ten `about:blank` tabs after twenty-seven minutes, one per restart."""
    import contextlib
    import io
    import json as json_

    from browser_supervisor.devtools_port import DevToolsEndpoint, close_restored_pages

    listing = [
        {"id": "A", "type": "page", "url": "about:blank"},
        {"id": "W", "type": "service_worker", "url": "https://x.com/sw.js"},
        {"id": "B", "type": "page", "url": "about:blank"},
        {"id": "C", "type": "page", "url": "https://x.com/home"},
    ]
    asked: list[str] = []

    @contextlib.contextmanager
    def opener(url: str, timeout: float):
        asked.append(url)
        yield io.BytesIO(json_.dumps(listing).encode() if url.endswith("/json/list") else b"")

    endpoint = DevToolsEndpoint(port=45000, browser_path="/devtools/browser/x")
    assert close_restored_pages(endpoint, opener=opener) == 2
    assert asked[1:] == [
        "http://127.0.0.1:45000/json/close/B",
        "http://127.0.0.1:45000/json/close/C",
    ], "one page stays, workers are not pages"


def test_a_failed_listing_closes_nothing_and_does_not_raise() -> None:
    from browser_supervisor.devtools_port import DevToolsEndpoint, close_restored_pages

    def refusing(url: str, timeout: float):
        raise OSError("connection refused")

    endpoint = DevToolsEndpoint(port=45000, browser_path="/devtools/browser/x")
    assert close_restored_pages(endpoint, opener=refusing) == 0
