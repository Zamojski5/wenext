"""Tests of the Stream Gateway HTTP layer — issuing and exchanging the ticket."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from stream_gateway import app as module
from stream_gateway.app import ALLOWED_ORIGIN, app

ISSUER_TOKEN = "issuer-test-token"


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch: pytest.MonkeyPatch):
    """Every test gets an empty registry — tickets are one-time and must not leak.

    Since level 2 of plan B (D-026) issuing needs the issuer token; the tests here are about the
    ticket flow, so the token is configured and sent by default — `test_viewer_and_issuer.py`
    measures what happens without it.
    """
    module.registry = type(module.registry)()
    module._sessions.clear()
    monkeypatch.setattr(module, "ISSUER_TOKEN", ISSUER_TOKEN)
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={module.ISSUER_TOKEN_HEADER: ISSUER_TOKEN})


def _payload(role: str = "VIEW_ONLY", **overrides) -> dict:
    base = {
        "browser_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "role": role,
        "profile_lease_generation": 3,
    }
    base.update(overrides)
    return base


def test_healthz_responds(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_view_only_gets_a_ticket(client: TestClient) -> None:
    response = client.post("/browser-sessions", json=_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "VIEW_ONLY"
    assert len(data["ticket"]) > 20


def test_control_without_interaction_lease_is_rejected(client: TestClient) -> None:
    """L5590 — the rule lives in the registry, the endpoint only has to pass it on as 409."""
    response = client.post("/browser-sessions", json=_payload(role="CONTROL"))
    assert response.status_code == 409
    assert "InteractionLease" in response.json()["detail"]


def test_ticket_never_reaches_the_url(client: TestClient) -> None:
    """L2343: the ticket does not reach the query string or logs — only the response body."""
    response = client.post("/browser-sessions", json=_payload())
    assert response.json()["ticket"] not in str(response.url)


def test_exchange_requires_the_exact_origin(client: TestClient) -> None:
    """L2345: exchange requires an exact Origin."""
    ticket = client.post("/browser-sessions", json=_payload()).json()["ticket"]

    response = client.post(
        "/stream/exchange",
        json={"ticket": ticket, "current_profile_lease_generation": 3},
        headers={"Origin": "https://zly.example"},
    )
    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]


def test_valid_exchange_sets_the_cookie(client: TestClient) -> None:
    ticket = client.post("/browser-sessions", json=_payload()).json()["ticket"]

    response = client.post(
        "/stream/exchange",
        json={"ticket": ticket, "current_profile_lease_generation": 3},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    assert "csrf" in response.json()

    # We check the HEADER, not the client's cookie jar.
    # The cookie has the `Secure` flag, so a client over plain HTTP will not store it — and
    # that is how it should be. An assertion on the jar would require either dropping
    # `Secure` or TLS in a unit test; the first would weaken production, the second would
    # prove nothing.
    header = response.headers.get("set-cookie", "")
    assert "wenext_stream=" in header
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "samesite=strict" in header.lower()
    assert "Path=/stream" in header


def test_ticket_replay_is_rejected(client: TestClient) -> None:
    """L5589."""
    ticket = client.post("/browser-sessions", json=_payload()).json()["ticket"]
    naglowki = {"Origin": ALLOWED_ORIGIN}
    content = {"ticket": ticket, "current_profile_lease_generation": 3}

    assert client.post("/stream/exchange", json=content, headers=naglowki).status_code == 200
    second = client.post("/stream/exchange", json=content, headers=naglowki)
    assert second.status_code == 403
    assert "Replay" in second.json()["detail"]


def test_profile_generation_change_invalidates_the_ticket(client: TestClient) -> None:
    ticket = client.post("/browser-sessions", json=_payload()).json()["ticket"]

    response = client.post(
        "/stream/exchange",
        json={"ticket": ticket, "current_profile_lease_generation": 4},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 403
    assert "ProfileLease" in response.json()["detail"]


def test_websocket_without_cookie_is_rejected(client: TestClient) -> None:
    """Without a valid session the stream does not start — even if someone knew the path."""
    with (
        pytest.raises(Exception),  # noqa: B017 — starlette reports a disconnect
        client.websocket_connect("/stream/ws"),
    ):
        pass
