"""Regression: waiting for the login has to PUMP the channel, not merely peek into the list.

The error this file guards against blocked a real founders' login:
`czekaj_na_login` read `klient.notyfikacje`, but that list fills up only
as a side effect of `request()`. A loop that merely sleeps never reads from the
app-server stream — so the notification had no way of arriving, and the human had entered
the code long ago.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from codex_runtime.account_adapter import (
    CodexAccountAdapter,
    ConnectionState,
    LoginAttempt,
    LoginUnavailable,
)


class CountingClient:
    """Pretends to be the app-server: logs in only after N `account/read` calls."""

    codex_home = "/tmp/codex-home"  # atrapa ścieżki, nic tu nie powstaje
    initialize_response: ClassVar[dict] = {}

    def __init__(self, *, logs_in_after: int) -> None:
        self.logs_in_after = logs_in_after
        self.account_read_calls = 0
        self._notifications: list[dict] = []

    @property
    def notifications(self) -> list[dict]:
        return list(self._notifications)

    def request(self, method: str, params: dict | None = None) -> dict:
        if method != "account/read":
            return {}
        self.account_read_calls += 1
        if self.account_read_calls >= self.logs_in_after:
            # A real app-server delivers the notification as a side effect of reading frames.
            self._notifications.append({"method": "account/login/completed"})
            return {"account": {"type": "chatgpt", "email": "x@example.test"}}
        return {"requiresOpenaiAuth": True}


def _adapter(client) -> CodexAccountAdapter:
    adapter = CodexAccountAdapter(client=client)
    adapter.transition_to(ConnectionState.AUTH_PENDING)
    adapter.last_login_attempt = LoginAttempt(
        login_id="test",
        verification_url="https://example.test",
        user_code="AAAA-BBBB",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        method="chatgptDeviceCode",
    )
    return adapter


def test_waiting_polls_account_read() -> None:
    """The heart of the regression: without polling the loop would never have finished."""
    client = CountingClient(logs_in_after=3)
    adapter = _adapter(client)

    state = adapter.wait_for_login(interval_s=0.01)

    assert state.logged_in is True
    assert client.account_read_calls >= 3, (
        "czekanie nie odpytywało account/read — kanał nie był pompowany"
    )
    assert adapter.state is ConnectionState.AUTHENTICATED


def test_successful_login_closes_the_attempt() -> None:
    adapter = _adapter(CountingClient(logs_in_after=1))
    adapter.wait_for_login(interval_s=0.01)
    assert adapter.last_login_attempt is None


def test_no_open_attempt_is_an_error() -> None:
    adapter = CodexAccountAdapter(client=CountingClient(logs_in_after=1))
    with pytest.raises(LoginUnavailable):
        adapter.wait_for_login(interval_s=0.01)


def test_expired_attempt_ends_in_timeout() -> None:
    """The deadline is local — past it a `TimeoutError`, and not an infinite wait."""
    client = CountingClient(logs_in_after=9999)
    adapter = _adapter(client)
    adapter.last_login_attempt = LoginAttempt(
        login_id="test",
        verification_url="https://example.test",
        user_code="AAAA-BBBB",
        deadline=datetime.now(UTC) - timedelta(seconds=1),
        method="chatgptDeviceCode",
    )
    with pytest.raises(TimeoutError):
        adapter.wait_for_login(interval_s=0.01)


# ---------------------------------------------------------------------------
# Regression: the unattended run has to drive the adapter to a working state
# ---------------------------------------------------------------------------


def test_unattended_connection_reaches_authenticated(monkeypatch) -> None:
    """The error that wasted the founders' first run.

    `unattended` created a fresh adapter (state DISCONNECTED) and immediately called
    `zsynchronizuj_katalog()`. The state machine from L2134-2141 does not know the
    DISCONNECTED → CAPABILITIES_SYNCING transition, so EVERY cycle ended with an error,
    and the verdict came out NO_GO — falsely blaming the runtime for the caller's error.
    """
    from codex_runtime import unattended

    class StubClient(CountingClient):
        def start(self) -> None: ...
        def initialize(self) -> dict:
            return {}

        def close(self) -> None: ...

    stub = StubClient(logs_in_after=1)
    monkeypatch.setattr(unattended, "AppServerClient", lambda **_: stub)
    monkeypatch.setattr(unattended, "find_codex_bin", lambda: "/nieistotne")

    from pathlib import Path

    _client, adapter = unattended._connect(Path("/tmp/codex-home"))

    assert adapter.state is ConnectionState.AUTHENTICATED, (
        "adapter został w stanie, z którego nie wolno synchronizować katalogu"
    )
