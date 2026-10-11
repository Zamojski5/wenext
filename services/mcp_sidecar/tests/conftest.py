"""Shared fixtures for the sidecar tests."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from mcp_sidecar import TOOL_NAMES, TaskScopedCapability

#: Counter of writes on the external site. The "tools are read-only" test rests on the fact
#: that after a whole session of tools this counter is still zero.
WRITES: list[str] = []


class _FixtureSiteWithForm(http.server.BaseHTTPRequestHandler):
    """A minimal fixture site with a form. `POST` writes — and there is nobody to call it."""

    def do_GET(self):  # the BaseHTTPRequestHandler interface
        content = (
            "<html><body><h1 id='tytul'>Community fixture</h1>"
            "<form method='post' action='/post'>"
            "<input name='tresc'><button type='submit'>Wyślij</button></form>"
            f"<p id='licznik-zapisow'>{len(WRITES)}</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        WRITES.append(self.rfile.read(length).decode("utf-8", errors="replace"))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        return


@pytest.fixture
def fixture_site() -> Iterator[str]:
    """Starts the local fixture site and returns its base URL."""
    WRITES.clear()
    serwer = http.server.HTTPServer(("127.0.0.1", 0), _FixtureSiteWithForm)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{serwer.server_address[1]}"
    finally:
        serwer.shutdown()


@pytest.fixture
def records() -> list[str]:
    return WRITES


def build_capability(**overrides) -> TaskScopedCapability:
    """A capability with sensible defaults, to be overridden in a test."""
    data = {
        "organization_id": "org-1",
        "workspace_id": "ws-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "assignment_id": "assign-1",
        "bound_account_ref": "acct-ref-7f3a",
        "profile_lease_generation": 3,
        "allowed_tools": TOOL_NAMES,
        "allowed_origins": ("http://fixture-site:8000",),
        "expiry": datetime.now(UTC) + timedelta(minutes=30),
        "thread_binding_fingerprint": "sha256:binding-abc",
    }
    data.update(overrides)
    return TaskScopedCapability(**data)


@pytest.fixture
def capability() -> TaskScopedCapability:
    return build_capability()
