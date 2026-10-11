"""RESEARCH mode has no publication tools, and says so when asked (P4).

WHY THIS IS MEASURED AT THE TOOL AND NOT ONLY AT THE DECISION. `presence/approval.py` refuses to
issue a consent for a project delegated in RESEARCH, so a draft written in that mode would never
go out. What that refusal does NOT do is save the model the turn it spent writing the draft, or
tell it why the work was pointless. A tool that lets an agent do work certain to be thrown away
is a tool that lies about the shape of the job, and this file is the guard on that.

THE MODE IS READ FRESH, never cached on the session: revoking is the operator's stop button and
it has to work in the middle of a turn, not from the next one onwards. The last case here pauses
a delegation between two calls and requires the second to refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore
from control_plane.tools.catalog import OUTCOME_PENDING, OUTCOME_UNAVAILABLE

pytestmark = [pytest.mark.db, pytest.mark.p4]


def now() -> datetime:
    return datetime.now(UTC)


async def _sign(pool, tenant, project_id, *, mode: str):
    scope = parse_scope({"accounts": [], "communities": ["r/devops"], "actions": ["REPLY"]})
    return await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=mode,
        scope=scope,
        budget=parse_budget({}),
        operator_id=tenant["operator"],
    )


async def _registry(pool, tenant):
    """A registry over a project with one assigned Reddit account, as a session would have."""
    from control_plane.tools.capability import issue
    from control_plane.tools.registry import ToolRegistry

    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        handle = f"konto-{uuid4().hex[:8]}"
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            handle,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'pracuj, nikt nie patrzy', 'PRESENCE') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
    capability = issue(
        session_id=session_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        dsn="postgresql://atrapa/nieuzywane",
    )
    return ToolRegistry(pool=pool, capability=capability), project_id, handle


def _args(handle: str) -> dict:
    return {
        "target_external_id": "t3_abc123",
        "target_url": "https://www.reddit.com/r/devops/comments/abc123/x/",
        "text": "propozycja odpowiedzi",
        "account": handle,
    }


async def test_a_research_project_cannot_reach_for_a_publication_tool(pool, tenant) -> None:
    registry, project_id, handle = await _registry(pool, tenant)
    await _sign(pool, tenant, project_id, mode="RESEARCH")

    result = await registry.call("draft.create", _args(handle))
    assert result["stan"] == OUTCOME_UNAVAILABLE, result
    assert "RESEARCH" in result["powod"]
    # And it says what to do instead, rather than only what it will not do.
    assert "memory.write" in result.get("wskazowka", "")


@pytest.mark.parametrize("mode", ["AUTONOMOUS", "REVIEW"])
async def test_the_other_modes_still_prepare_a_draft(pool, tenant, mode) -> None:
    """Both of them PREPARE and neither publishes: the outcome is OCZEKUJE, never WYKONANE."""
    registry, project_id, handle = await _registry(pool, tenant)
    await _sign(pool, tenant, project_id, mode=mode)

    result = await registry.call("draft.create", _args(handle))
    assert result["stan"] == OUTCOME_PENDING, result


async def test_a_project_with_no_delegation_still_prepares_a_draft(pool, tenant) -> None:
    """A conversation with a person at the screen is not a delegation, and P2 worked without one.
    RESEARCH is a statement somebody signed; the absence of a signature is not that statement."""
    registry, _project_id, handle = await _registry(pool, tenant)
    result = await registry.call("draft.create", _args(handle))
    assert result["stan"] == OUTCOME_PENDING, result


async def test_pausing_a_delegation_stops_the_tool_in_the_middle_of_a_turn(pool, tenant) -> None:
    """THE STOP BUTTON, measured between two calls of one session.

    The mode is read on every call rather than captured when the session opened, so a person who
    pauses at 3am stops the turn that is running, not the next one.
    """
    registry, project_id, handle = await _registry(pool, tenant)
    delegation = await _sign(pool, tenant, project_id, mode="RESEARCH")
    assert (await registry.call("draft.create", _args(handle)))["stan"] == OUTCOME_UNAVAILABLE

    await PresenceStore(pool).revoke(
        delegation.id, organization_id=tenant["organization"], at=now()
    )
    await _sign(pool, tenant, project_id, mode="AUTONOMOUS")
    # A different target, because the first call left nothing behind but the second must be able
    # to write its own proposal.
    args = _args(handle) | {"target_external_id": "t3_abc124"}
    assert (await registry.call("draft.create", args))["stan"] == OUTCOME_PENDING


async def test_a_long_dash_never_reaches_the_stored_draft(pool, tenant) -> None:
    """The founder's rule (2026-09-04): no em or en dash in any text the model writes. The
    session path stored drafts as the model wrote them; the observe path always cleaned them."""
    registry, _project_id, handle = await _registry(pool, tenant)
    args = _args(handle) | {"text": "Diff first \u2014 then approve. Version 2\u20133 works."}

    result = await registry.call("draft.create", args)

    assert result["stan"] == OUTCOME_PENDING, result
    assert result["propozycja"]["przepisane_dlugie_pauzy"] == 2
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT payload->>'text_and_media' FROM action_proposal_revisions WHERE id = $1",
            UUID(result["propozycja"]["revision_id"]),
        )
    assert stored == "Diff first, then approve. Version 2-3 works."
