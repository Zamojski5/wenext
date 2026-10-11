"""The hosted MCP endpoint against a real database, through its real ASGI app.

WHAT THESE TESTS HOLD, each one a way the endpoint could go wrong quietly:

  * ONE TOKEN, ONE PROJECT. Two invitations are two organizations; what one saves (an
    opportunity, a draft revision) is invisible to the other, including by a guessed revision id.
  * A REVOKED TOKEN IS REFUSED ON ITS NEXT REQUEST, with the same bare 404 an unknown or malformed
    token gets, and the token is never echoed in any response or written to stderr.
  * PARALLEL FIRST CALLS WORK: eight requests racing on a cold token build one cached server and
    open one host session, not eight.
  * THE PROTOCOL SHAPE a real client relies on: notifications get 202, `GET` gets 405, a batch is
    answered as a batch, garbage is a JSON-RPC parse error.
  * THE HOSTED SURFACE is the hosted allowlist and not the plugin's: no capsule, no account, no
    browser tools, and none of the card's send tools, whichever agent initialized.

These talk to the ASGI app with `httpx.AsyncClient` on THIS event loop (see `conftest.py` on why
not `TestClient`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from control_plane.hosted.http_app import (
    ENTRY_IDLE_S,
    create_app,
)
from control_plane.hosted.invite import Invitation, create_invitation
from control_plane.tools.capability import issue
from control_plane.tools.catalog import APP_ONLY_TOOLS, HOSTED_TOOLS

pytestmark = [pytest.mark.db, pytest.mark.p2]

_ids = iter(range(1, 10_000))


def _frame(method: str, params: dict | None = None, *, notify: bool = False) -> dict:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = params
    if not notify:
        frame["id"] = next(_ids)
    return frame


def _initialize(agent: str = "codex-mcp-client") -> dict:
    return _frame(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": agent, "version": "1.0"},
        },
    )


def _call(name: str, arguments: dict | None = None) -> dict:
    return _frame("tools/call", {"name": name, "arguments": arguments or {}})


def _tool_json(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


async def _invite(pool, name: str = "Acme") -> Invitation:
    return await create_invitation(
        pool, email=f"{name.lower()}-{datetime.now(UTC).timestamp()}@example.com", name=name
    )


@pytest_asyncio.fixture
async def hosted(pool):
    """(app, client) over the real pool; the app's service is reachable for clock control."""
    application = create_app(pool=pool)
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp-edge") as http:
        yield application, http


def _service(application):
    return application.state.hosted["service"]


async def _post(http, token: str, body: Any) -> httpx.Response:
    return await http.post(f"/mcp/{token}", json=body)


def _reddit_args(post: str, community: str = "saas") -> dict:
    return {
        "platform": "REDDIT",
        "url": f"https://www.reddit.com/r/{community}/comments/{post}/how_do_you_do_x/",
        "external_id": f"t3_{post}",
        "community": f"r/{community}",
        "title": f"How do you do X? ({post})",
        "excerpt": "We keep losing track of X.",
        "author": "someone",
        "created_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "dlaczego": "Asks for exactly this.",
        "kat": "Show how you solved it.",
    }


# ---------------------------------------------------------------------------
# The door: tokens, methods, bodies
# ---------------------------------------------------------------------------


async def test_unknown_malformed_and_revoked_tokens_get_the_same_bare_404(
    hosted, pool, capfd
) -> None:
    _, http = hosted
    invitation = await _invite(pool)
    live = await _post(http, invitation.token, _initialize())
    assert live.status_code == 200

    unknown = "wnx_" + "A" * 43
    refused = [
        await _post(http, unknown, _initialize()),
        await _post(http, "not-a-token", _initialize()),
        await _post(http, "wnx_short", _initialize()),
    ]
    await pool.execute(
        "UPDATE hosted_tokens SET revoked_at = now() WHERE organization_id = $1",
        invitation.organization_id,
    )
    refused.append(await _post(http, invitation.token, _initialize()))

    assert [one.status_code for one in refused] == [404, 404, 404, 404]
    assert len({one.text for one in refused}) == 1, "the four refusals must be indistinguishable"
    # Nothing carries the token: not the bodies, not the diagnostics.
    out = capfd.readouterr()
    for secret in (invitation.token, unknown):
        assert secret not in live.text and all(secret not in one.text for one in refused)
        assert secret not in out.out and secret not in out.err


async def test_a_revoked_token_stops_working_on_its_next_request(hosted, pool) -> None:
    _, http = hosted
    invitation = await _invite(pool)
    assert (await _post(http, invitation.token, _initialize())).status_code == 200
    assert (await _post(http, invitation.token, _frame("tools/list"))).status_code == 200

    from control_plane.hosted.tokens import HostedTokenStore

    await HostedTokenStore(pool).revoke_label(invitation.email)

    again = await _post(http, invitation.token, _frame("tools/list"))
    assert again.status_code == 404


async def test_an_archived_project_opens_nothing(hosted, pool) -> None:
    _, http = hosted
    invitation = await _invite(pool)
    assert (await _post(http, invitation.token, _frame("ping"))).status_code == 200
    await pool.execute(
        "UPDATE projects SET archived_at = now() WHERE id = $1", invitation.project_id
    )
    assert (await _post(http, invitation.token, _frame("ping"))).status_code == 404


async def test_the_protocol_shape_a_real_client_relies_on(hosted, pool) -> None:
    _, http = hosted
    token = (await _invite(pool)).token

    # GET and DELETE: no server-initiated stream and no session to end.
    for method in ("GET", "DELETE"):
        refused = await http.request(method, f"/mcp/{token}")
        assert refused.status_code == 405
        assert refused.headers["allow"] == "POST"
        assert token not in refused.text

    init = await _post(http, token, _initialize())
    assert init.status_code == 200
    assert init.headers["content-type"].startswith("application/json")
    result = init.json()["result"]
    assert result["serverInfo"]["name"] == "wenext"
    assert result.get("instructions")
    assert "mcp-session-id" not in init.headers, "the endpoint is stateless on the wire"

    # A notification is acknowledged, not answered.
    assert (
        await _post(http, token, _frame("notifications/initialized", notify=True))
    ).status_code == 202
    # A ping is answered with an empty result.
    ping = await _post(http, token, _frame("ping"))
    assert ping.json()["result"] == {}

    # A batch is answered as a batch, minus the notifications inside it.
    batch = await _post(
        http,
        token,
        [_frame("ping"), _frame("notifications/initialized", notify=True), _frame("ping")],
    )
    assert batch.status_code == 200 and len(batch.json()) == 2
    only_notifications = await _post(
        http, token, [_frame("notifications/initialized", notify=True)]
    )
    assert only_notifications.status_code == 202

    # Garbage is a JSON-RPC parse error, an empty batch an invalid request.
    garbage = await http.post(f"/mcp/{token}", content=b"{not json")
    assert garbage.status_code == 400 and garbage.json()["error"]["code"] == -32700
    assert (await _post(http, token, [])).status_code == 400
    too_big = await http.post(f"/mcp/{token}", content=b"[" + b"0," * 600_000 + b"0]")
    assert too_big.status_code == 413

    # An unknown method is a JSON-RPC error, not a crash.
    unknown = await _post(http, token, _frame("prompts/list"))
    assert unknown.status_code == 200 and unknown.json()["error"]["code"] == -32601


async def test_healthz_reports_the_database(hosted) -> None:
    _, http = hosted
    ok = await http.get("/healthz")
    assert ok.status_code == 200 and ok.json()["ok"] is True


# ---------------------------------------------------------------------------
# What the agent sees
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_name", "expected_host"),
    [
        ("codex-mcp-client", "codex"),
        ("claude-code", "claude-code"),
        ("some-other-agent", "claude-code"),
    ],
)
async def test_the_agent_is_read_from_initialize_and_the_surface_is_the_hosted_one(
    hosted, pool, client_name: str, expected_host: str
) -> None:
    _, http = hosted
    invitation = await _invite(pool)
    assert (await _post(http, invitation.token, _initialize(client_name))).status_code == 200

    # `tools/list` carries no clientInfo: the host remembered for the token answers it.
    listed = await _post(http, invitation.token, _frame("tools/list"))
    tools = listed.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == HOSTED_TOOLS
    assert not names & APP_ONLY_TOOLS, "the card's send tools are never served to a hosted session"
    assert not {"accounts.connect", "browser.inspect", "communities.join", "drafts.publish"} & names

    # The session that carries the trace belongs to this agent, in this project.
    row = await pool.fetchrow(
        "SELECT budget ->> 'host' AS host, kind FROM agent_sessions WHERE project_id = $1",
        invitation.project_id,
    )
    assert row["host"] == expected_host and row["kind"] == "CONVERSATION"


async def test_the_tool_call_and_its_result_are_written_to_the_trace(hosted, pool) -> None:
    _, http = hosted
    invitation = await _invite(pool)
    await _post(http, invitation.token, _initialize())
    answer = _tool_json(await _post(http, invitation.token, _call("project.read")))
    assert answer["stan"] == "WYKONANE"

    kinds = [
        row["kind"]
        for row in await pool.fetch(
            "SELECT e.kind FROM session_events e JOIN agent_sessions s ON s.id = e.session_id"
            " WHERE s.project_id = $1 AND e.kind IN ('TOOL_CALL', 'TOOL_RESULT') ORDER BY e.seq",
            invitation.project_id,
        )
    ]
    assert kinds == ["TOOL_CALL", "TOOL_RESULT"]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_two_tokens_cannot_see_each_others_opportunities_or_revisions(hosted, pool) -> None:
    _, http = hosted
    mine, theirs = await _invite(pool, "Mine"), await _invite(pool, "Theirs")
    assert mine.organization_id != theirs.organization_id
    for one in (mine, theirs):
        await _post(http, one.token, _initialize())

    marked = _tool_json(
        await _post(http, mine.token, _call("opportunities.mark", _reddit_args("iso001")))
    )
    assert marked["stan"] == "WYKONANE", marked
    draft = _tool_json(
        await _post(
            http,
            mine.token,
            _call(
                "draft.create",
                {
                    "text": "Here is how we solved it.",
                    "target_url": "https://www.reddit.com/r/saas/comments/iso001/how_do_you_do_x/",
                },
            ),
        )
    )
    assert draft["stan"] == "OCZEKUJE", draft
    revision_id = draft["propozycja"]["revision_id"]

    # Mine sees both.
    listed = _tool_json(await _post(http, mine.token, _call("opportunities.list")))
    assert listed["liczba"] == 1
    assert (
        _tool_json(
            await _post(http, mine.token, _call("drafts.read", {"revision_id": revision_id}))
        )["stan"]
        == "WYKONANE"
    )

    # Theirs sees neither the opportunity nor the revision, not even by its exact id.
    other = _tool_json(await _post(http, theirs.token, _call("opportunities.list")))
    assert other["liczba"] == 0 and other["lacznie_w_filtrze"] == 0
    guessed = _tool_json(
        await _post(http, theirs.token, _call("drafts.read", {"revision_id": revision_id}))
    )
    assert guessed["stan"] != "WYKONANE", guessed
    assert "tresc" not in json.dumps(guessed) and "solved" not in json.dumps(guessed)

    # And the rows really are in separate tenants.
    counts = {
        row["organization_id"]: row["n"]
        for row in await pool.fetch(
            "SELECT organization_id, count(*) AS n FROM project_opportunities"
            " WHERE organization_id = ANY($1::uuid[]) GROUP BY organization_id",
            [mine.organization_id, theirs.organization_id],
        )
    }
    assert counts == {mine.organization_id: 1}


async def test_a_token_cannot_be_pointed_at_another_project_by_a_tool_argument(
    hosted, pool
) -> None:
    """The scope is the token's. No tool takes an organization or a project id from the model."""
    _, http = hosted
    mine, theirs = await _invite(pool, "Mine"), await _invite(pool, "Theirs")
    await _post(http, mine.token, _initialize())
    answer = _tool_json(
        await _post(
            http,
            mine.token,
            _call(
                "project.read",
                {
                    "project_id": str(theirs.project_id),
                    "organization_id": str(theirs.organization_id),
                },
            ),
        )
    )
    assert str(mine.project_id) in json.dumps(answer)
    assert str(theirs.project_id) not in json.dumps(answer)


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


async def test_parallel_first_calls_build_one_server_and_open_one_session(hosted, pool) -> None:
    application, http = hosted
    invitation = await _invite(pool)

    answers = await asyncio.gather(
        *[_post(http, invitation.token, _frame("tools/list")) for _ in range(8)],
        _post(http, invitation.token, _call("project.read")),
    )

    assert [one.status_code for one in answers] == [200] * 9
    assert all("error" not in one.json() for one in answers)
    assert _service(application).cached() == 1
    sessions = await pool.fetchval(
        "SELECT count(*) FROM agent_sessions WHERE project_id = $1", invitation.project_id
    )
    assert sessions == 1, "eight racing first calls must share one host session"


async def test_an_idle_entry_is_evicted_and_a_busy_one_is_kept(hosted, pool) -> None:
    application, http = hosted
    service = _service(application)
    clock = {"now": 1000.0}
    service.clock = lambda: clock["now"]
    busy, idle = await _invite(pool, "Busy"), await _invite(pool, "Idle")
    await _post(http, busy.token, _frame("ping"))
    await _post(http, idle.token, _frame("ping"))
    assert service.cached() == 2

    clock["now"] += ENTRY_IDLE_S - 5
    await _post(http, busy.token, _frame("ping"))  # touched: stays
    clock["now"] += 10  # the idle one is now older than the bound
    await _post(http, busy.token, _frame("ping"))
    assert service.cached() == 1

    # After eviction a fresh request rebuilds the entry and still works, on the SAME session.
    again = await _post(http, idle.token, _frame("tools/list"))
    assert again.status_code == 200
    assert service.cached() == 2
    sessions = await pool.fetchval(
        "SELECT count(*) FROM agent_sessions WHERE project_id = $1", idle.project_id
    )
    assert sessions == 1


async def test_an_expired_cached_capability_is_replaced_on_the_next_request(hosted, pool) -> None:
    """`registry.call` checks expiry on EVERY call, so a capability issued when the entry was
    built would end under a long-lived entry. The window is renewed per request instead."""
    application, http = hosted
    service = _service(application)
    invitation = await _invite(pool)
    await _post(http, invitation.token, _initialize())
    entry = next(iter(service._entries.values()))
    expired = issue(
        session_id=entry.session_id,
        organization_id=invitation.organization_id,
        workspace_id=invitation.workspace_id,
        project_id=invitation.project_id,
        dsn="postgresql://unused",
        ttl_minutes=1,
        host="codex",
        hosted=True,
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    entry.server.capability = expired
    entry.registry.capability = expired

    answer = _tool_json(await _post(http, invitation.token, _call("project.read")))

    assert answer["stan"] == "WYKONANE"
    assert entry.server.capability is entry.registry.capability
    assert entry.server.capability.expires_at > datetime.now(UTC)
    assert entry.server.capability.hosted is True


# ---------------------------------------------------------------------------
# The limit
# ---------------------------------------------------------------------------


async def test_a_token_over_its_hourly_limit_is_told_so_as_a_tool_result(hosted, pool) -> None:
    application, http = hosted
    _service(application).calls_per_hour = 3
    invitation = await _invite(pool)
    await _post(http, invitation.token, _initialize())

    results = [await _post(http, invitation.token, _call("project.read")) for _ in range(5)]

    flags = [one.json()["result"]["isError"] for one in results]
    assert flags == [False, False, False, True, True]
    assert "Too many" in results[-1].json()["result"]["content"][0]["text"]
    # Only `tools/call` counts: a ping or a list is free.
    assert (await _post(http, invitation.token, _frame("tools/list"))).status_code == 200
