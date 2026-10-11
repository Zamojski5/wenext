"""Level 2 of plan B (D-026) in the gateway: who may mint a ticket, and the page a human opens.

Three locks measured here: (1) `/browser-sessions` is closed without an issuer token in the
environment (503) and closed to a wrong one (401) — it used to be anonymous on the one published
port; (2) the ticket TTL comes from `WENEXT_STREAM_TICKET_TTL_SECONDS` and is clamped, so a typo
cannot mint an hour-long ticket; (3) `/login-capsule` carries a strict CSP, no ticket in its body,
and refuses to render when the image has no noVNC.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from stream_gateway import app as module
from stream_gateway.app import ISSUER_TOKEN_HEADER, VIEWER_PATH, app


@pytest.fixture(autouse=True)
def clean_registry():
    module.registry = type(module.registry)()
    module._sessions.clear()
    yield


def _payload() -> dict:
    return {
        "browser_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "role": "VIEW_ONLY",
        "profile_lease_generation": 1,
    }


def test_without_an_issuer_token_in_the_environment_issuing_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "ISSUER_TOKEN", "")
    client = TestClient(app)
    response = client.post(
        "/browser-sessions", json=_payload(), headers={ISSUER_TOKEN_HEADER: "cokolwiek"}
    )
    assert response.status_code == 503
    assert "WENEXT_GATEWAY_ISSUER_TOKEN" in response.json()["detail"]
    assert client.get("/healthz").json()["wystawianie_ticketow"] is False


def test_a_missing_or_wrong_issuer_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "ISSUER_TOKEN", "sekret-wystawcy")
    client = TestClient(app)
    assert client.post("/browser-sessions", json=_payload()).status_code == 401
    assert (
        client.post(
            "/browser-sessions", json=_payload(), headers={ISSUER_TOKEN_HEADER: "zly"}
        ).status_code
        == 401
    )
    good = client.post(
        "/browser-sessions", json=_payload(), headers={ISSUER_TOKEN_HEADER: "sekret-wystawcy"}
    )
    assert good.status_code == 200 and len(good.json()["ticket"]) > 20
    assert client.get("/healthz").json()["wystawianie_ticketow"] is True


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("", 45), ("120", 120), ("abc", 45), ("5", 15), ("99999", 600), ("  90 ", 90)],
)
def test_the_ticket_ttl_comes_from_the_environment_and_is_clamped(
    monkeypatch: pytest.MonkeyPatch, raw: str, seconds: int
) -> None:
    monkeypatch.setenv("WENEXT_STREAM_TICKET_TTL_SECONDS", raw)
    assert module._ticket_ttl() == timedelta(seconds=seconds)


def test_the_issued_ticket_expires_after_the_configured_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "ISSUER_TOKEN", "t")
    monkeypatch.setenv("WENEXT_STREAM_TICKET_TTL_SECONDS", "120")
    client = TestClient(app, headers={ISSUER_TOKEN_HEADER: "t"})
    body = client.post("/browser-sessions", json=_payload()).json()
    record = next(iter(module.registry._records.values()))
    assert record.ticket_expires_at - record.issued_at == timedelta(seconds=120)
    assert body["ticket_expires_at"] == record.ticket_expires_at.isoformat()


def test_the_viewer_refuses_to_render_without_novnc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(module, "NOVNC_DIR", tmp_path / "nie-ma")
    response = TestClient(app).get(VIEWER_PATH)
    assert response.status_code == 503
    assert "noVNC" in response.json()["detail"]


def test_the_viewer_page_is_static_strict_and_ticket_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "rfb.js").write_text("export default class RFB {}", encoding="utf-8")
    monkeypatch.setattr(module, "NOVNC_DIR", tmp_path)
    response = TestClient(app).get(f"{VIEWER_PATH}#ticket=nigdy-nie-dotrze-do-serwera")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "connect-src 'self' ws://testserver wss://testserver" in csp
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "nigdy-nie-dotrze" not in html, "ticket żyje we fragmencie, serwer go nie widzi"
    assert 'from "/novnc/core/rfb.js"' in html
    assert "/stream/exchange" in html and "/stream/ws" in html
    assert "history.replaceState" in html, "ticket znika z paska adresu od razu"
    assert "https://" not in html.replace('https:" ? "wss"', ""), "zero obcych originów"
