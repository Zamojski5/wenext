"""`control_plane.bootstrap` on a database other suites have already filled — the deterministic half.

The fresh-database path (no organization at all) is in `test_a_fresh_database_bootstrap.py`,
which sorts FIRST in this directory and therefore runs against the empty throwaway PostgreSQL.
Everything here works on the `tenant` fixture's own organization, so the order of the other
files does not matter. No `skip` anywhere: `tools/dev/run_db_tests.sh` refuses a run with one.
"""

from __future__ import annotations

import pytest
from control_plane.auth import verify_credential
from control_plane.bootstrap import BootstrapRefused, bootstrap, discover_organization, provision

pytestmark = pytest.mark.db


async def test_discovery_refuses_several_organizations(pool, tenant) -> None:
    """E1 has one organization (D-016). With two or more, guessing would be a tenant mistake."""
    async with pool.acquire() as connection:
        await connection.execute("INSERT INTO organizations (name) VALUES ('second')")
        count = await connection.fetchval("SELECT count(*) FROM organizations")
        assert count >= 2
        with pytest.raises(BootstrapRefused, match="organizacje"):
            await discover_organization(connection, organization_name="ignored")
        with pytest.raises(BootstrapRefused, match="organizacje"):
            await bootstrap(
                connection, organization_name="x", workspace_name="y", password="p" * 12
            )


async def test_provision_reuses_the_workspace_and_refuses_a_second_enabled_operator(
    pool, tenant
) -> None:
    async with pool.acquire() as connection:
        # The tenant fixture already provisioned one enabled operator for this organization.
        with pytest.raises(BootstrapRefused, match="rotate-password"):
            await provision(
                connection,
                organization_id=tenant["organization"],
                organization_created=False,
                workspace_name="ws",
                password="nowe-haslo-1",
            )
        with pytest.raises(BootstrapRefused, match="WENEXT_OPERATOR_PASSWORD"):
            await provision(
                connection,
                organization_id=tenant["organization"],
                organization_created=False,
                workspace_name="ws",
                password="   ",
                rotate_password=True,
            )


async def test_rotation_disables_the_old_operator_instead_of_editing_it(pool, tenant) -> None:
    """The invariant: an operator row is never rewritten, because approvals will point at it."""
    async with pool.acquire() as connection:
        result = await provision(
            connection,
            organization_id=tenant["organization"],
            organization_created=False,
            workspace_name="ws",
            password="drugie-2",
            rotate_password=True,
        )
        assert result.operator_rotated and not result.operator_created
        assert result.workspace_id == tenant["workspace"], (
            "istniejący workspace jest użyty, nie dublowany"
        )
        assert not result.workspace_created
        assert result.operator_id != tenant["operator"]
        rows = await connection.fetch(
            "SELECT id, disabled_at FROM operators WHERE organization_id = $1",
            tenant["organization"],
        )
        by_id = {row["id"]: row["disabled_at"] for row in rows}
        assert by_id[tenant["operator"]] is not None, "stary operator WYŁĄCZONY, nie usunięty"
        assert by_id[result.operator_id] is None
        assert (await verify_credential(connection, tenant["password"])) is None
        verified = await verify_credential(connection, "drugie-2")
        assert verified is not None and verified.operator_id == result.operator_id
        assert "zrotowano" in result.describe()


async def test_a_second_workspace_is_a_refusal(pool, tenant) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'second')",
            tenant["organization"],
        )
        with pytest.raises(BootstrapRefused, match="workspace"):
            await provision(
                connection,
                organization_id=tenant["organization"],
                organization_created=False,
                workspace_name="ws",
                password="x" * 8,
                rotate_password=True,
            )
