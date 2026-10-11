"""A background agent can inspect and retire its project plans without an operator."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from control_plane.presence.store import PresenceStore
from control_plane.sessions.context import starting_context
from test_presence_scheduled_return import _registry_for
from test_project_approvals_api import _account, _project, _proposal

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


async def _setup(pool, tenant):
    project = UUID(await _project(pool, tenant))
    registry = await _registry_for(pool, tenant, {"project": project})
    return project, registry


async def _plan(pool, tenant, project, note="Check this exact reply later"):
    return await PresenceStore(pool).schedule_return(
        organization_id=tenant["organization"], workspace_id=tenant["workspace"],
        project_id=project, due_at=datetime.now(UTC) + timedelta(hours=1),
        note=note, session_id=None, limit=3,
    )


async def test_agent_sees_another_sessions_plan_and_can_retire_it(pool, tenant):
    project, registry = await _setup(pool, tenant)
    planned = await _plan(pool, tenant, project)
    context = await starting_context(pool, capability=registry.capability, goal="Continue")
    assert context.planned_returns == (
        (str(planned.id), planned.due_at.isoformat(), "Check this exact reply later"),
    )
    assert str(planned.id) in context.render()
    view = await registry.call("session.list_returns", {})
    assert [row["wakeup_id"] for row in view["powroty"]] == [str(planned.id)]
    assert view["powroty"][0]["notatka"] == "Check this exact reply later"

    result = await registry.call("session.cancel_return", {"wakeup_id": str(planned.id)})
    assert result["stan"] == "WYKONANE", result
    assert (await registry.call("session.list_returns", {}))["powroty"] == []
    fresh = await starting_context(pool, capability=registry.capability, goal="Continue")
    assert fresh.planned_returns == ()
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT cancelled_at FROM wakeups WHERE id=$1", planned.id)
    assert await PresenceStore(pool).due_returns(
        organization_id=tenant["organization"], project_id=project,
        now=datetime.now(UTC) + timedelta(hours=2),
    ) == ()


async def test_agent_and_dashboard_cannot_cancel_another_projects_plan(
    pool, tenant, logged_in_client,
):
    project, registry = await _setup(pool, tenant)
    other, _ = await _setup(pool, tenant)
    planned = await _plan(pool, tenant, other, "Other project's confidential plan")
    assert (await registry.call("session.list_returns", {}))["powroty"] == []
    context = await starting_context(pool, capability=registry.capability, goal="Continue")
    assert context.planned_returns == ()
    result = await registry.call("session.cancel_return", {"wakeup_id": str(planned.id)})
    assert result["stan"] == "NIEDOSTEPNE", result
    path = (f"/api/workspaces/{tenant['workspace']}/projects/{project}"
            f"/returns/{planned.id}/cancel")
    response = await logged_in_client.post(path)
    assert response.status_code == 404
    async with pool.acquire() as conn:
        # A 404 after changing the other project's row would still be a broken boundary.
        assert await conn.fetchval(
            "SELECT cancelled_at FROM wakeups WHERE id=$1", planned.id,
        ) is None


async def test_a_started_return_is_not_reported_as_cancelled(pool, tenant):
    project, registry = await _setup(pool, tenant)
    planned = await _plan(pool, tenant, project)
    await PresenceStore(pool).consume_return(
        planned.id, organization_id=tenant["organization"], now=datetime.now(UTC),
    )
    result = await registry.call("session.cancel_return", {"wakeup_id": str(planned.id)})
    assert result["stan"] == "NIEDOSTEPNE", result
    assert (await registry.call("session.list_returns", {}))["powroty"] == []


async def test_reading_exact_draft_does_not_publish_or_leak_shared_account_drafts(pool, tenant):
    project, registry = await _setup(pool, tenant)
    other, other_registry = await _setup(pool, tenant)
    account = await _account(pool, tenant, project)
    text = "Exact draft including its final question. " * 250
    revision = await _proposal(pool, tenant, account, project_id=project, text=text)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO project_account_assignments "
            "(organization_id, workspace_id, project_id, social_account_id) VALUES ($1,$2,$3,$4)",
            tenant["organization"], tenant["workspace"], other, UUID(account),
        )
    result = await registry.call("drafts.read", {"revision_id": revision})
    assert result["stan"] == "WYKONANE", result
    assert result["szkic"]["tresc"] == text
    assert result["szkic"]["stan"] == "READY_FOR_APPROVAL"
    assert result["szkic"]["wniosek"] is None
    assert result["szkic"]["adres"].startswith("https://www.reddit.com/")
    for wanted in (revision, str(uuid4())):
        denied = await other_registry.call("drafts.read", {"revision_id": wanted})
        assert denied["stan"] == "NIEDOSTEPNE", denied
        assert "szkic" not in denied
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id=$1",
            tenant["organization"],
        ) == 0
