"""The fresh-database path of `control_plane.bootstrap`: the first thing a new VPS runs.

THIS FILE'S NAME IS LOAD-BEARING. pytest collects the files of a directory in name order, and
`test_a_` sorts before every other `test_*.py` here, so this module runs against the throwaway
PostgreSQL BEFORE any `tenant` fixture has written an organization. The first assertion checks
exactly that — and FAILS, rather than skipping, if some day another file gets in first: a skip
would pass through `tools/dev/run_db_tests.sh` as a refusal of the whole run anyway, and a
silent reorder is the kind of thing that turns a test into decoration.
"""

from __future__ import annotations

import pytest
from control_plane.auth import verify_credential
from control_plane.bootstrap import BootstrapRefused, bootstrap

pytestmark = pytest.mark.db


async def test_a_fresh_database_gets_one_organization_one_workspace_one_operator(pool) -> None:
    async with pool.acquire() as connection:
        organizations = await connection.fetchval("SELECT count(*) FROM organizations")
        assert organizations == 0, (
            f"baza ma już {organizations} organizacji — ten plik ma biec jako PIERWSZY w katalogu "
            "(nazwa `test_a_…` sortuje się przed resztą); jeżeli coś go wyprzedziło, to jest "
            "porażka, nie powód do pominięcia"
        )
        result = await bootstrap(
            connection, organization_name="wenext", workspace_name="anna", password="tajne-12"
        )
        assert result.organization_created and result.workspace_created and result.operator_created
        assert not result.operator_rotated
        assert (
            await connection.fetchval(
                "SELECT name FROM organizations WHERE id = $1", result.organization_id
            )
        ) == "wenext"
        assert (
            await connection.fetchval(
                "SELECT name FROM workspaces WHERE id = $1", result.workspace_id
            )
        ) == "anna"
        verified = await verify_credential(connection, "tajne-12")
        assert verified is not None and verified.operator_id == result.operator_id
        assert "utworzono" in result.describe() and "tajne" not in result.describe()

        # Idempotence of the safe part, refusal of the unsafe part: a second run without
        # `--rotate-password` reuses the tenant and refuses to add an operator.
        with pytest.raises(BootstrapRefused, match="rotate-password"):
            await bootstrap(
                connection, organization_name="other", workspace_name="other", password="inne-33"
            )
        assert (await connection.fetchval("SELECT count(*) FROM organizations")) == 1
        assert (await connection.fetchval("SELECT count(*) FROM workspaces")) == 1
