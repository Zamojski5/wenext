"""A shared social account must not transfer one project's drafts to another."""

from uuid import UUID

import pytest
from control_plane.returns.store import ReturnStore
from test_presence_own_post import _approver
from test_project_approvals_api import _account, _path, _project, _proposal, _sign

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


async def test_shared_account_does_not_share_drafts_or_publication_authority(
    pool, tenant, logged_in_client
):
    mine = await _project(pool, tenant)
    other = await _project(pool, tenant)
    account = await _account(pool, tenant, mine)
    revision = await _proposal(pool, tenant, account, project_id=mine)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO project_account_assignments "
            "(organization_id, workspace_id, project_id, social_account_id) "
            "VALUES ($1,$2,$3,$4)",
            tenant["organization"], tenant["workspace"], UUID(other), UUID(account),
        )
    await _sign(logged_in_client, tenant, other, account, mode="AUTONOMOUS")
    root = f"/api/workspaces/{tenant['workspace']}/projects"
    assert (await logged_in_client.get(f"{root}/{mine}/decisions")).json()["decisions"]
    assert (await logged_in_client.get(f"{root}/{other}/decisions")).json()["decisions"] == []
    async with pool.acquire() as conn:
        assert await ReturnStore(pool)._decisions(
            conn, tenant["organization"], tenant["workspace"], UUID(other),
        ) == ()
    approver = _approver(pool, tenant)
    for selection in (None, UUID(revision)):
        assert await approver.consider_pending(
            workspace_id=tenant["workspace"], project_id=UUID(other), revision_id=selection,
        ) == ()
    response = await logged_in_client.post(_path(tenant, other, revision, "/approve"))
    assert response.status_code == 404
    response = await logged_in_client.post(
        _path(tenant, other, revision, "/revise"), json={"text": "Changed by another project"},
    )
    assert response.status_code == 404
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id=$1",
            tenant["organization"],
        ) == 0
