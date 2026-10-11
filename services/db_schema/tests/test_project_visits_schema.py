"""Migracja 0024: znacznik ostatniej wizyty w projekcie.

O CO TU CHODZI. Widok powrotu odpowiada na pytanie „co się wydarzyło, ODKĄD MNIE NIE BYŁO", więc
cała jego prawdziwość stoi na jednym wierszu na człowieka i projekt. Trzy sposoby, żeby to
zepsuć, i każdy ma tu swój przypadek:

  * drugi wiersz dla tej samej pary, przez który jedna z wizyt przestaje cokolwiek znaczyć;
  * sesje bez użytkownika zakładające nowy wiersz przy każdym wejściu (NULL <> NULL), czyli
    znacznik, który nie działa dokładnie dla tego jednego przypadku, dla którego dopuściliśmy NULL;
  * okno odwrócone w czasie, czyli „poprzednia wizyta" późniejsza niż obecna.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from db_schema.expected_schema import PROJECT_VISITS_COLUMNS

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


async def _visit(connection, organization_id, workspace_id, project_id, user_id=None, **extra):
    columns = ["organization_id", "workspace_id", "project_id", "user_id"]
    values = [organization_id, workspace_id, project_id, user_id]
    for name, value in extra.items():
        columns.append(name)
        values.append(value)
    placeholders = ", ".join(f"${index}" for index in range(1, len(values) + 1))
    names = ", ".join(columns)
    statement = f"INSERT INTO project_visits ({names}) VALUES ({placeholders}) RETURNING id"
    return await connection.fetchval(statement, *values)


async def test_the_table_has_exactly_the_declared_columns(pool) -> None:
    assert len(PROJECT_VISITS_COLUMNS) == 9, PROJECT_VISITS_COLUMNS
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'project_visits'"
        )
    present = sorted(row["column_name"] for row in rows)
    assert present, "nie ma tabeli project_visits, migracja 0024 nie została zastosowana"
    assert present == sorted(PROJECT_VISITS_COLUMNS), present


async def test_one_row_per_person_and_project(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        user_id = await connection.fetchval(
            "INSERT INTO users (email, credential_hash) VALUES ($1, $2) RETURNING id",
            f"ktos-{uuid.uuid4().hex[:8]}@firma.test",
            "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$" + "a" * 43,
        )
        await _visit(connection, organization_id, workspace_id, project_id, user_id)
        with pytest.raises(Exception) as duplicate:
            await _visit(connection, organization_id, workspace_id, project_id, user_id)
        assert "project_visits_one_per_person_and_project" in str(duplicate.value)


async def test_sessions_without_a_user_share_one_row_instead_of_multiplying(pool) -> None:
    """NULLS NOT DISTINCT. Bez tego każde wejście operatora sprzed P7 zakładałoby nowy wiersz."""
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        await _visit(connection, organization_id, workspace_id, project_id, None)
        with pytest.raises(Exception) as duplicate:
            await _visit(connection, organization_id, workspace_id, project_id, None)
        assert "project_visits_one_per_person_and_project" in str(duplicate.value)


async def test_the_window_cannot_run_backwards(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        with pytest.raises(Exception) as refused:
            await _visit(
                connection,
                organization_id,
                workspace_id,
                project_id,
                None,
                previous_seen_at=datetime(2026, 9, 19, 10, tzinfo=UTC),
                last_seen_at=datetime(2026, 9, 19, 9, tzinfo=UTC),
            )
        assert "project_visits_window_is_ordered" in str(refused.value)


async def test_a_project_from_another_workspace_cannot_be_visited(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, _ = await _project(connection)
        _, _, stranger = await _project(connection)
        with pytest.raises(Exception) as refused:
            await _visit(connection, organization_id, workspace_id, stranger, None)
        assert "project_visits_point_at_project" in str(refused.value)
