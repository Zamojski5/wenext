"""Migracja 0036: okazje projektu, czyli rozmowy na żywo wybrane przez agenta.

O CO TU CHODZI. Strona "Pokój" pokazuje założycielowi rozmowy, w które warto wejść. Pięć sposobów,
żeby ją zepsuć, i każdy ma tu swój przypadek:

  * ta sama rozmowa dwa razy w jednym projekcie, czyli dwie pozycje na liście o jednej sprawie;
  * identyfikator, który nie jest celem odpowiedzi (post X z identyfikatorem Reddita i odwrotnie),
    bo wtedy okazja nie połączy się ze szkicem ani z publikacją;
  * okazja bez uzasadnienia albo z adresem, który nie jest https;
  * pominięcie bez autora decyzji;
  * okazja zawieszona pod projektem z innej przestrzeni (trójka tenantowa).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from db_schema.expected_schema import PROJECT_OPPORTUNITIES_COLUMNS

pytestmark = pytest.mark.db


async def _project(connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    organization_id = await connection.fetchval(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"Firma {uuid.uuid4()}"
    )
    workspace_id = await connection.fetchval(
        "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
        organization_id,
    )
    project_id = await connection.fetchval(
        "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'projekt')"
        " RETURNING id",
        organization_id,
        workspace_id,
    )
    return organization_id, workspace_id, project_id


_INSERT = (
    "INSERT INTO project_opportunities (organization_id, workspace_id, project_id, platform,"
    " external_id, url, community, reason) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
    " RETURNING id"
)


async def test_the_table_has_exactly_the_declared_columns(pool) -> None:
    assert len(PROJECT_OPPORTUNITIES_COLUMNS) == 21
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'project_opportunities'"
        )
    columns = sorted(row["column_name"] for row in rows)
    assert columns, "nie ma tabeli project_opportunities, migracja 0036 nie została zastosowana"
    assert columns == sorted(PROJECT_OPPORTUNITIES_COLUMNS), columns


async def test_one_conversation_is_one_opportunity_per_project(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        args = (
            organization_id,
            workspace_id,
            project_id,
            "REDDIT",
            "t3_abc123",
            "https://www.reddit.com/r/saas/comments/abc123/x/",
            "r/saas",
            "ktoś prosi o dokładnie to",
        )
        await connection.fetchval(_INSERT, *args)
        with pytest.raises(asyncpg.UniqueViolationError) as twice:
            await connection.fetchval(_INSERT, *args)
        assert "project_opportunities_one_per_conversation" in str(twice.value)
        # Ten sam identyfikator w INNYM projekcie to inna sprawa: każdy projekt ma swoją listę.
        other_project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'drugi')"
            " RETURNING id",
            organization_id,
            workspace_id,
        )
        await connection.fetchval(_INSERT, organization_id, workspace_id, other_project, *args[3:])


async def test_the_id_must_be_the_target_of_a_reply_on_that_platform(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        url = "https://x.com/jack/status/20"
        good = [("REDDIT", "t3_abc123", "r/saas"), ("X", "x:2103587050346250341", "X")]
        for platform, external_id, community in good:
            await connection.fetchval(
                _INSERT,
                organization_id,
                workspace_id,
                project_id,
                platform,
                external_id,
                url,
                community,
                "powód",
            )
        bad = [
            ("REDDIT", "abc123"),
            ("REDDIT", "t1_abc123"),
            ("REDDIT", "x:123"),
            ("X", "t3_abc123"),
            ("X", "20"),
            ("X", "x:@jack"),
        ]
        for platform, external_id in bad:
            with pytest.raises(asyncpg.CheckViolationError) as refused:
                await connection.fetchval(
                    _INSERT,
                    organization_id,
                    workspace_id,
                    project_id,
                    platform,
                    external_id,
                    url,
                    "X",
                    "powód",
                )
            assert "project_opportunities_id_matches_platform" in str(refused.value), (
                platform,
                external_id,
            )
        with pytest.raises(asyncpg.CheckViolationError) as unknown:
            await connection.fetchval(
                _INSERT,
                organization_id,
                workspace_id,
                project_id,
                "HACKER_NEWS",
                # A shape the id check accepts for X, so only the platform list can refuse it.
                "x:123",
                url,
                "x",
                "powód",
            )
        assert "project_opportunities_platform_is_known" in str(unknown.value)


async def test_a_reason_and_an_https_address_are_required(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        base = [organization_id, workspace_id, project_id, "REDDIT", "t3_abc123"]
        for url, reason in (
            ("http://www.reddit.com/r/saas/comments/abc123/", "powód"),
            ("https://www.reddit.com/r/saas/comments/abc123/", "   "),
            ("https://www.reddit.com/r/saas/comments/abc123/", ""),
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.fetchval(_INSERT, *base, url, "r/saas", reason)


async def test_a_dismissal_names_who_decided(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        opportunity = await connection.fetchval(
            _INSERT,
            organization_id,
            workspace_id,
            project_id,
            "REDDIT",
            "t3_abc123",
            "https://www.reddit.com/r/saas/comments/abc123/",
            "r/saas",
            "powód",
        )
        with pytest.raises(asyncpg.CheckViolationError) as anonymous:
            await connection.execute(
                "UPDATE project_opportunities SET dismissed_at = now() WHERE id = $1", opportunity
            )
        assert "project_opportunities_dismissal_names_its_author" in str(anonymous.value)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "UPDATE project_opportunities SET dismissed_by = 'operator:x' WHERE id = $1",
                opportunity,
            )
        await connection.execute(
            "UPDATE project_opportunities SET dismissed_at = now(), dismissed_by = 'operator:x'"
            " WHERE id = $1",
            opportunity,
        )


async def test_an_opportunity_cannot_hang_under_a_project_of_another_workspace(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, _workspace_id, project_id = await _project(connection)
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'drugi') RETURNING id",
            organization_id,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError) as crossed:
            await connection.fetchval(
                _INSERT,
                organization_id,
                other_workspace,
                project_id,
                "REDDIT",
                "t3_abc123",
                "https://www.reddit.com/r/saas/comments/abc123/",
                "r/saas",
                "powód",
            )
        assert "project_opportunities_point_at_project" in str(crossed.value)
