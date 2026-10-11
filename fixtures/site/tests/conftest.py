"""Shared pytest fixtures. Every test gets its own instance of the application.

The session state lives in process memory, so sharing the application between tests would
carry challenge counters and sessions across. A separate instance is cheaper than debugging
such a leak.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wenext_fixture_site.app import create_app
from wenext_fixture_site.config import DEFAULT_SEED, Settings


def make_settings(**overrides: object) -> Settings:
    base = {
        "seed": DEFAULT_SEED,
        "bind_host": "127.0.0.1",
        "bind_port": 8000,
        "username": "fixture-user",
        "password": "fixture-pass",
        "challenge_after": 120,
        "challenge_window_seconds": 10,
        "delayed_render_ms": 2000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), follow_redirects=False)


def log_in(client: TestClient) -> None:
    """The full path: password → code from `/debug/mfa` → persistent session cookie.

    The automated test takes the code from the debug endpoint, because it CANNOT take it
    from the `/mfa` page — and that is exactly the point of this fixture.
    """

    client.post(
        "/login",
        data={"username": "fixture-user", "password": "fixture-pass", "next": "/c"},
    )
    code = client.get("/debug/mfa").json()["pending"][0]["code"]
    response = client.post("/mfa", data={"code": code, "next": "/c"})
    assert response.status_code == 303, "logowanie drugim składnikiem nie przeszło"
