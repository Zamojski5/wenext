"""The public path — `PUBLIC_API_NO_ACCOUNT` (L4391, L4400, L5528).

The point of this path: at equal audience fit the planner is to prefer reading without an
account. For that to be comparable, the public API must return THE SAME content as the view
behind the wall.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import log_in


def test_public_api_requires_no_session(client: TestClient) -> None:
    client.cookies.clear()
    for path in (
        "/api/public/",
        "/api/public/surfaces",
        "/api/public/communities",
        "/api/public/threads",
        "/api/public/quality-dimensions",
        "/api/public/content-hash",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/json"), path


def test_public_api_sets_no_session_cookie(client: TestClient) -> None:
    response = client.get("/api/public/threads")
    assert "wenext_fixture_session" not in response.headers.get("set-cookie", "")


def test_both_archetypes_have_24_threads(client: TestClient) -> None:
    counts = client.get("/api/public/").json()["counts"]
    assert counts["THREADED_COMMUNITY"] == 24
    assert counts["PROFESSIONAL_GRAPH"] == 24


def test_declared_access_mode_matches_actual_behavior(client: TestClient) -> None:
    surfaces = {s["surface"]: s for s in client.get("/api/public/surfaces").json()["surfaces"]}
    assert surfaces["forum"]["access_mode"] == "ACCOUNT_REQUIRED"
    assert surfaces["professional-feed"]["access_mode"] == "ACCOUNT_REQUIRED"
    assert surfaces["public-api"]["access_mode"] == "PUBLIC_API_NO_ACCOUNT"
    # A declaration with no cover in behaviour would be worse than none at all.
    assert client.get(surfaces["forum"]["path"]).status_code == 302
    assert client.get(surfaces["public-api"]["path"]).status_code == 200


def test_public_api_and_walled_view_show_the_same_thread(client: TestClient) -> None:
    payload = client.get("/api/public/threads/f-substantive-high").json()
    log_in(client)
    html = client.get(payload["url"]).text
    assert payload["title"] in html
    assert str(payload["metrics"]["reply_count"]) in html
    assert payload["author"] in html


def test_json_snapshot_in_html_is_the_same_document(client: TestClient) -> None:
    """The document requires parsing the internal API in preference to the DOM (L5034)."""

    import json
    import re

    payload = client.get("/api/public/threads/f-depth-deep").json()
    log_in(client)
    html = client.get(payload["url"]).text
    raw = re.search(r'id="wenext-thread-snapshot">(.*?)</script>', html, re.DOTALL).group(1)
    embedded = json.loads(raw.replace("\\u003c", "<"))
    assert embedded == payload


def test_hidden_metrics_are_null_and_named(client: TestClient) -> None:
    """Missing data is to be UNKNOWN with a reason, never zero (L4299, L5552)."""

    payload = client.get("/api/public/threads/f-icp-hidden").json()
    assert payload["metrics"]["reach_impressions"] is None
    assert payload["metrics"]["icp_share"] is None
    assert set(payload["metrics"]["hidden_metrics"]) == {"reach_impressions", "icp_share"}


def test_filtering_by_archetype_and_community(client: TestClient) -> None:
    threads = client.get("/api/public/threads?archetype=PROFESSIONAL_GRAPH").json()["threads"]
    assert len(threads) == 24
    assert {t["archetype"] for t in threads} == {"PROFESSIONAL_GRAPH"}

    threads = client.get("/api/public/threads?community=infra-praktycy").json()["threads"]
    assert threads
    assert {t["community"] for t in threads} == {"infra-praktycy"}


def test_unknown_thread_returns_404(client: TestClient) -> None:
    assert client.get("/api/public/threads/nie-ma-takiego").status_code == 404


def test_healthz_returns_200(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
