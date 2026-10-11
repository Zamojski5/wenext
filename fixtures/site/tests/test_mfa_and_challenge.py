"""Screens that headless will not pass on its own (L5154, L5169).

The heart of it: the second-factor code DOES NOT EXIST in the HTML of the `/mfa` page. If
it did, the fixture would be proving the opposite of what it should — that logging in can
be automated from the DOM. The automated test takes the code from a debug endpoint, which
a real platform does not have.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from wenext_fixture_site.app import create_app

from .conftest import log_in, make_settings


def _start_login(client: TestClient) -> str:
    client.post(
        "/login",
        data={"username": "fixture-user", "password": "fixture-pass", "next": "/c"},
    )
    return client.get("/debug/mfa").json()["pending"][0]["code"]


def test_mfa_page_does_not_contain_the_code(client: TestClient) -> None:
    code = _start_login(client)
    html = client.get("/mfa?next=%2Fc").text
    assert code not in html
    assert 'data-testid="mfa-form"' in html


def test_no_response_header_carries_the_code(client: TestClient) -> None:
    code = _start_login(client)
    response = client.get("/mfa?next=%2Fc")
    assert all(code not in value for value in response.headers.values())


def test_wrong_password_creates_no_pending_login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "fixture-user", "password": "zle", "next": "/c"},
    )
    assert response.status_code == 401
    assert client.get("/debug/mfa").json()["pending"] == []


def test_wrong_code_grants_no_session(client: TestClient) -> None:
    _start_login(client)
    response = client.post("/mfa", data={"code": "000000", "next": "/c"})
    assert response.status_code == 401
    assert client.get("/c").status_code == 302


def test_correct_code_grants_access_to_both_archetypes(client: TestClient) -> None:
    log_in(client)
    assert client.get("/c/infra-praktycy").status_code == 200
    assert client.get("/g").status_code == 200


def test_mfa_without_prior_password_returns_to_login(client: TestClient) -> None:
    response = client.get("/mfa?next=%2Fc")
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")


def test_challenge_after_burst_of_fast_requests() -> None:
    client = TestClient(create_app(make_settings(challenge_after=10)), follow_redirects=False)
    log_in(client)
    for _ in range(11):
        response = client.get("/c")
    assert response.status_code == 302
    assert response.headers["location"].startswith("/challenge")

    page = client.get("/challenge?next=%2Fc")
    assert page.status_code == 429
    assert "challenge-form" in page.text

    # The challenge sticks to the session until the interaction is confirmed.
    assert client.get("/c").status_code == 302
    assert client.post("/challenge", data={"human": "on", "next": "/c"}).status_code == 303
    assert client.get("/c").status_code == 200


def test_challenge_can_be_disabled_with_zero() -> None:
    client = TestClient(create_app(make_settings(challenge_after=0)), follow_redirects=False)
    log_in(client)
    for _ in range(50):
        response = client.get("/c")
    assert response.status_code == 200
