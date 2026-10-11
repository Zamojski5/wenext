"""The product's door: `POST /mcp` with the edge's signed identity, against a real database.

WHAT THESE TESTS HOLD:

  * NO HEADER, NO ENTRY. An absent, forged, expired or replayed header is a bare 401 and creates
    nothing: a person who is not vouched for by the edge cannot even cause a row to be written.
  * A NEW PERSON GETS A PROJECT ON THEIR FIRST REQUEST, with the placeholder accounts the draft
    path needs, and a second request (a new `jti`) reaches the SAME project.
  * PARALLEL FIRST REQUESTS PROVISION ONCE. Eight requests with one unknown (provider, subject)
    racing each other must end with one organization, one project and one identity row.
  * TWO PEOPLE ARE TWO TENANTS, and an identity the operator switched off is refused.
  * THE INTERNAL DOOR (`/mcp/{token}`) keeps working beside it.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from control_plane.hosted.edge import EDGE_HEADER, EdgeVerifier, sign_edge_header
from control_plane.hosted.http_app import create_app
from control_plane.hosted.provision import PLACEHOLDER_ACCOUNTS, ExternalIdentityStore
from control_plane.tools.catalog import HOSTED_TOOLS

pytestmark = [pytest.mark.db, pytest.mark.p2]

SECRET = "edge-test-secret-" + "x" * 32
_ids = iter(range(1, 10_000))


def _frame(method: str, params: dict | None = None, *, notify: bool = False) -> dict:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = params
    if not notify:
        frame["id"] = next(_ids)
    return frame


def _initialize(agent: str = "claude-code") -> dict:
    return _frame(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": agent}},
    )


def _call(name: str, arguments: dict | None = None) -> dict:
    return _frame("tools/call", {"name": name, "arguments": arguments or {}})


def _subject() -> str:
    return str(int(time.time() * 1e6))


def _header(subject: str, **changes) -> dict[str, str]:
    args = {"provider": "github", "subject": subject, "email": f"{subject}@example.com"}
    args.update(changes)
    return {EDGE_HEADER: sign_edge_header(SECRET, login=f"user{subject[-6:]}", **args)}


def _tool_json(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


@pytest_asyncio.fixture
async def edge(pool):
    application = create_app(pool=pool, edge=EdgeVerifier(SECRET))
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp-edge") as http:
        yield application, http


async def _count(pool, query: str, *args) -> int:
    return await pool.fetchval(query, *args)


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


async def test_without_a_valid_header_the_answer_is_a_bare_401_and_nothing_is_created(
    edge, pool
) -> None:
    _, http = edge
    subject = _subject()
    before = await _count(pool, "SELECT count(*) FROM organizations")
    forged = _header(subject)
    forged[EDGE_HEADER] = forged[EDGE_HEADER][:-4] + "AAAA"
    expired = {
        EDGE_HEADER: sign_edge_header(
            SECRET, provider="github", subject=subject, now=time.time() - 3600
        )
    }
    other_secret = {EDGE_HEADER: sign_edge_header("y" * 48, provider="github", subject=subject)}

    answers = [
        await http.post("/mcp", json=_initialize()),
        await http.post("/mcp", json=_initialize(), headers=forged),
        await http.post("/mcp", json=_initialize(), headers=expired),
        await http.post("/mcp", json=_initialize(), headers=other_secret),
        await http.post("/mcp", json=_initialize(), headers={EDGE_HEADER: "garbage"}),
    ]

    assert [one.status_code for one in answers] == [401] * 5
    assert len({one.text for one in answers}) == 1
    assert await _count(pool, "SELECT count(*) FROM organizations") == before
    assert not await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM external_identities WHERE subject = $1)", subject
    )


async def test_the_same_header_twice_is_a_replay(edge) -> None:
    _, http = edge
    headers = _header(_subject())
    assert (await http.post("/mcp", json=_initialize(), headers=headers)).status_code == 200
    assert (await http.post("/mcp", json=_initialize(), headers=headers)).status_code == 401


async def test_without_a_secret_configured_the_product_door_does_not_exist(pool) -> None:
    application = create_app(pool=pool)  # the environment has no WENEXT_EDGE_SECRET
    assert application.state.hosted["edge"] is None
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp-edge") as http:
        answer = await http.post("/mcp", json=_initialize(), headers=_header(_subject()))
    assert answer.status_code == 404


async def test_get_and_delete_on_the_product_door_are_405(edge) -> None:
    _, http = edge
    for method in ("GET", "DELETE"):
        answer = await http.request(method, "/mcp")
        assert answer.status_code == 405 and answer.headers["allow"] == "POST"


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def test_a_new_person_gets_a_project_on_the_first_request_and_the_same_one_after(
    edge, pool
) -> None:
    _, http = edge
    subject = _subject()

    first = await http.post("/mcp", json=_initialize(), headers=_header(subject))
    assert first.status_code == 200 and first.json()["result"]["serverInfo"]["name"] == "wenext"

    identity = await pool.fetchrow(
        "SELECT * FROM external_identities WHERE provider = 'github' AND subject = $1", subject
    )
    assert identity is not None
    assert identity["email"] == f"{subject}@example.com"
    assert identity["display_name"] == f"user{subject[-6:]}"
    project = await pool.fetchrow(
        "SELECT name, mode FROM projects WHERE id = $1", identity["project_id"]
    )
    assert project["name"] == "My project" and project["mode"] == "REVIEW"
    accounts = await pool.fetch(
        "SELECT a.platform, a.external_account_id, a.state FROM project_account_assignments x"
        " JOIN social_accounts a ON a.id = x.social_account_id WHERE x.project_id = $1"
        " AND x.unassigned_at IS NULL ORDER BY a.platform",
        identity["project_id"],
    )
    assert [(a["platform"], a["external_account_id"]) for a in accounts] == [
        ("REDDIT", PLACEHOLDER_ACCOUNTS["REDDIT"]),
        ("X", PLACEHOLDER_ACCOUNTS["X"]),
    ]
    # No user, no membership, no password: the person comes in through the edge.
    assert not await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM memberships WHERE organization_id = $1)",
        identity["organization_id"],
    )

    # A second request, with its own `jti`, reaches the SAME project.
    again = await http.post("/mcp", json=_frame("tools/list"), headers=_header(subject))
    assert again.status_code == 200
    assert {tool["name"] for tool in again.json()["result"]["tools"]} == HOSTED_TOOLS
    assert (
        await _count(pool, "SELECT count(*) FROM external_identities WHERE subject = $1", subject)
        == 1
    )
    read = _tool_json(await http.post("/mcp", json=_call("project.read"), headers=_header(subject)))
    assert str(identity["project_id"]) in json.dumps(read)


async def test_parallel_first_requests_provision_exactly_once(edge, pool) -> None:
    _, http = edge
    subject = _subject()

    answers = await asyncio.gather(
        *[http.post("/mcp", json=_frame("tools/list"), headers=_header(subject)) for _ in range(8)]
    )

    assert [one.status_code for one in answers] == [200] * 8
    rows = await pool.fetch("SELECT * FROM external_identities WHERE subject = $1", subject)
    assert len(rows) == 1
    org = rows[0]["organization_id"]
    assert await _count(pool, "SELECT count(*) FROM projects WHERE organization_id = $1", org) == 1
    assert (
        await _count(pool, "SELECT count(*) FROM workspaces WHERE organization_id = $1", org) == 1
    )
    assert (
        await _count(pool, "SELECT count(*) FROM social_accounts WHERE organization_id = $1", org)
        == 2
    )
    # And no orphan organization was left behind by the requests that lost the race: the
    # organizations created for this login all carry its name, and there is exactly one.
    login = f"user{subject[-6:]}"
    assert await _count(pool, "SELECT count(*) FROM organizations WHERE name = $1", login) == 1


async def test_two_people_are_two_tenants(edge, pool) -> None:
    _, http = edge
    anna, bob = _subject(), str(int(_subject()) + 1)
    for subject in (anna, bob):
        await http.post("/mcp", json=_initialize(), headers=_header(subject))

    marked = _tool_json(
        await http.post(
            "/mcp",
            json=_call(
                "opportunities.mark",
                {
                    "platform": "REDDIT",
                    "url": "https://www.reddit.com/r/saas/comments/edg001/how_to/",
                    "external_id": "t3_edg001",
                    "community": "r/saas",
                    "title": "How do I find my first users?",
                    "excerpt": "Nobody knows my product exists.",
                    "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    "dlaczego": "Asks exactly this.",
                    "kat": "Show the loop.",
                },
            ),
            headers=_header(anna),
        )
    )
    assert marked["stan"] == "WYKONANE", marked

    mine = _tool_json(
        await http.post("/mcp", json=_call("opportunities.list"), headers=_header(anna))
    )
    theirs = _tool_json(
        await http.post("/mcp", json=_call("opportunities.list"), headers=_header(bob))
    )
    assert mine["liczba"] == 1 and theirs["liczba"] == 0


async def test_a_draft_can_be_written_for_an_auto_provisioned_person(edge) -> None:
    """The placeholder accounts exist, so the first draft is not refused with 'no account'."""
    _, http = edge
    subject = _subject()
    await http.post("/mcp", json=_initialize(), headers=_header(subject))
    draft = _tool_json(
        await http.post(
            "/mcp",
            json=_call(
                "draft.create",
                {
                    "text": "Here is how we handled it.",
                    "target_url": "https://www.reddit.com/r/saas/comments/edg002/how_to/",
                },
            ),
            headers=_header(subject),
        )
    )
    assert draft["stan"] == "OCZEKUJE", draft


# ---------------------------------------------------------------------------
# Switching a person off
# ---------------------------------------------------------------------------


async def test_a_disabled_identity_is_refused_and_creates_nothing_new(edge, pool) -> None:
    _, http = edge
    subject = _subject()
    assert (
        await http.post("/mcp", json=_initialize(), headers=_header(subject))
    ).status_code == 200

    assert await ExternalIdentityStore(pool).disable(subject=subject) == 1

    refused = await http.post("/mcp", json=_frame("tools/list"), headers=_header(subject))
    assert refused.status_code == 403
    assert (
        await _count(pool, "SELECT count(*) FROM external_identities WHERE subject = $1", subject)
        == 1
    )
    # Switching off twice finds nothing alive.
    assert await ExternalIdentityStore(pool).disable(subject=subject) == 0


async def test_an_archived_project_opens_nothing_for_its_person(edge, pool) -> None:
    _, http = edge
    subject = _subject()
    await http.post("/mcp", json=_initialize(), headers=_header(subject))
    project_id = await pool.fetchval(
        "SELECT project_id FROM external_identities WHERE subject = $1", subject
    )
    await pool.execute("UPDATE projects SET archived_at = now() WHERE id = $1", project_id)
    refused = await http.post("/mcp", json=_frame("tools/list"), headers=_header(subject))
    assert refused.status_code == 403


async def test_the_same_subject_at_another_provider_is_another_person(edge, pool) -> None:
    _, http = edge
    subject = _subject()
    await http.post("/mcp", json=_initialize(), headers=_header(subject, provider="github"))
    await http.post("/mcp", json=_initialize(), headers=_header(subject, provider="google"))
    projects = {
        row["project_id"]
        for row in await pool.fetch(
            "SELECT project_id FROM external_identities WHERE subject = $1", subject
        )
    }
    assert len(projects) == 2


async def test_the_internal_token_door_keeps_working_beside_the_product_door(edge, pool) -> None:
    from control_plane.hosted.invite import create_invitation

    _, http = edge
    invitation = await create_invitation(pool, email=f"i{_subject()}@example.com", name="Beside")
    answer = await http.post(f"/mcp/{invitation.token}", json=_initialize())
    assert answer.status_code == 200
    # The token does not open the product door and the header does not open the token door.
    assert (
        await http.post("/mcp", json=_initialize(), headers={EDGE_HEADER: invitation.token})
    ).status_code == 401
    assert (
        await http.post("/mcp/wnx_" + "A" * 43, json=_initialize(), headers=_header(_subject()))
    ).status_code == 404
