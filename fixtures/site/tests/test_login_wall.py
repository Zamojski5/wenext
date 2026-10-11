"""Login wall — the `access_mode = ACCOUNT_REQUIRED` model (L4391-4402).

The fixture IS MEANT to block. We test that it blocks, not that it can be worked around:
the system under test is to STOP on this screen and ask for a human (L5154).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wenext_fixture_site.config import SESSION_COOKIE

from .conftest import log_in


@pytest.mark.parametrize(
    "path",
    [
        "/c",
        "/c/infra-praktycy",
        "/c/infra-praktycy?sort=rising",
        "/g",
        "/g?sort=rising",
    ],
)
def test_walled_paths_redirect_to_login(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")


def test_thread_without_session_does_not_leak_content(client: TestClient) -> None:
    url = client.get("/api/public/threads/f-depth-deep").json()["url"]
    response = client.get(url)
    assert response.status_code == 302
    assert "monolit" not in response.text.lower()


def test_write_without_session_is_rejected(client: TestClient) -> None:
    url = client.get("/api/public/threads/f-depth-deep").json()["url"]
    response = client.post(f"{url}/comments", data={"body": "x", "parent_id": ""})
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")
    assert client.get("/debug/writes").json()["count"] == 0


@pytest.mark.parametrize("path", ["/", "/healthz", "/login", "/lab", "/api/public/"])
def test_public_paths_are_not_walled(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_session_unlocks_both_archetypes(client: TestClient) -> None:
    log_in(client)
    assert client.get("/c").status_code == 200
    assert client.get("/c/infra-praktycy").status_code == 200
    assert client.get("/g").status_code == 200


def test_session_cookie_is_persistent(client: TestClient) -> None:
    """The profile handover test headful→headless (DoD L5176) rests on the cookie
    surviving a browser close — that is, having `Max-Age` and not being a session cookie."""

    client.post(
        "/login",
        data={"username": "fixture-user", "password": "fixture-pass", "next": "/c"},
    )
    code = client.get("/debug/mfa").json()["pending"][0]["code"]
    response = client.post("/mfa", data={"code": code, "next": "/c"})
    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "Max-Age=" in set_cookie
    assert "HttpOnly" in set_cookie


def test_logout_restores_the_wall(client: TestClient) -> None:
    log_in(client)
    assert client.get("/c").status_code == 200
    client.get("/logout")
    assert client.get("/c").status_code == 302


def test_locked_thread_rejects_write(client: TestClient) -> None:
    log_in(client)
    url = client.get("/api/public/threads/f-neg-locked-high-engagement").json()["url"]
    assert client.get(url).status_code == 200
    response = client.post(f"{url}/comments", data={"body": "x", "parent_id": ""})
    assert response.status_code == 403
    assert client.get("/debug/writes").json()["count"] == 0


def test_removed_thread_rejects_write(client: TestClient) -> None:
    log_in(client)
    url = client.get("/api/public/threads/f-neg-removed").json()["url"]
    assert client.post(f"{url}/comments", data={"body": "x"}).status_code == 403


def test_next_does_not_allow_open_redirect(client: TestClient) -> None:
    response = client.get("/login?next=https://przyklad.invalid/atak")
    assert response.status_code == 200
    assert "przyklad.invalid" not in response.text
