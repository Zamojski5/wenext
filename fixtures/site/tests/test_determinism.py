"""Seed determinism — the condition for reproducible evals (L5553, DoD D16).

Two runs with the same seed must give identical content. Not "similar" — identical down
to the byte, because the eval ranking compares ORDER, and the order falls apart at the
first difference in the reply count or in a timestamp.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from wenext_fixture_site.app import create_app
from wenext_fixture_site.generator import build_repository

from .conftest import log_in, make_settings


def _hash(client: TestClient) -> str:
    return client.get("/api/public/content-hash").json()["content_hash"]


def test_same_seed_gives_same_content_hash() -> None:
    a = TestClient(create_app(make_settings()), follow_redirects=False)
    build_repository.cache_clear()
    b = TestClient(create_app(make_settings()), follow_redirects=False)
    assert _hash(a) == _hash(b)


def test_different_seed_gives_different_content_hash() -> None:
    a = TestClient(create_app(make_settings()), follow_redirects=False)
    build_repository.cache_clear()
    b = TestClient(create_app(make_settings(seed=999)), follow_redirects=False)
    assert _hash(a) != _hash(b)


def test_full_api_dump_is_byte_identical() -> None:
    a = TestClient(create_app(make_settings()), follow_redirects=False)
    build_repository.cache_clear()
    b = TestClient(create_app(make_settings()), follow_redirects=False)
    first = a.get("/api/public/threads?full=1").content
    second = b.get("/api/public/threads?full=1").content
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert len(first) > 100_000, "zrzut podejrzanie mały — czy na pewno pełna treść?"


def test_thread_html_is_byte_identical() -> None:
    a = TestClient(create_app(make_settings()), follow_redirects=False)
    build_repository.cache_clear()
    b = TestClient(create_app(make_settings()), follow_redirects=False)
    log_in(a)
    log_in(b)
    url = a.get("/api/public/threads/f-depth-deep").json()["url"]
    assert a.get(url).content == b.get(url).content


def test_write_does_not_change_seeded_content() -> None:
    """The form accepts a POST but does not mutate the content — otherwise determinism dies."""

    client = TestClient(create_app(make_settings()), follow_redirects=False)
    log_in(client)
    before = _hash(client)
    url = client.get("/api/public/threads/f-depth-deep").json()["url"]
    response = client.post(f"{url}/comments", data={"body": "próba zapisu", "parent_id": ""})
    assert response.status_code == 303
    assert client.get("/debug/writes").json()["count"] == 1
    assert _hash(client) == before


def test_timestamps_do_not_depend_on_the_clock() -> None:
    """No `now()` anywhere — the newest thread's date follows from the seed, not the moment."""

    client = TestClient(create_app(make_settings()), follow_redirects=False)
    threads = client.get("/api/public/threads?sort=new").json()["threads"]
    assert threads[0]["created_at"].endswith("Z")
    assert threads[0]["created_at"] < "2026-08-30T09:00:01Z"
