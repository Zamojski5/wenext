"""Migracja 0035: zapytania X wybrane przez agenta i posty zebrane co godzinę.

O CO TU CHODZI. Zbieracz bez modelu zapisuje, co się niesie na X, a agent czyta z tego historię
doby. Cztery sposoby, żeby to zepsuć, i każdy ma tu swój przypadek:

  * dwa aktywne wpisy tego samego zapytania, różniące się tylko wielkością liter, czyli podwójny
    odczyt i podwójne głosy tego samego źródła;
  * post z wyszukiwania bez zapytania, z którego przyszedł (nie da się ocenić źródła);
  * licznik ujemny albo identyfikator posta, który nie jest identyfikatorem X;
  * dwa zapisy tego samego posta z tego samego odczytu.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from db_schema.expected_schema import X_TREND_POSTS_COLUMNS, X_WATCH_QUERIES_COLUMNS

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


async def _columns(connection, table: str) -> list[str]:
    rows = await connection.fetch(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return sorted(row["column_name"] for row in rows)


async def test_both_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(X_WATCH_QUERIES_COLUMNS) == 9 and len(X_TREND_POSTS_COLUMNS) == 16
    async with pool.acquire() as connection:
        watch = await _columns(connection, "x_watch_queries")
        posts = await _columns(connection, "x_trend_posts")
    assert watch, "nie ma tabeli x_watch_queries, migracja 0035 nie została zastosowana"
    assert watch == sorted(X_WATCH_QUERIES_COLUMNS), watch
    assert posts == sorted(X_TREND_POSTS_COLUMNS), posts


async def test_one_active_query_per_text_whatever_the_case(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        insert = (
            "INSERT INTO x_watch_queries (organization_id, workspace_id, project_id, query)"
            " VALUES ($1, $2, $3, $4) RETURNING id"
        )
        first = await connection.fetchval(
            insert, organization_id, workspace_id, project_id, "AI agents min_faves:300"
        )
        with pytest.raises(asyncpg.UniqueViolationError) as duplicate:
            await connection.fetchval(
                insert, organization_id, workspace_id, project_id, " ai agents MIN_FAVES:300 "
            )
        assert "x_watch_queries_one_active" in str(duplicate.value)
        # A removed query leaves room for the same text again: removal is history, not a block.
        await connection.execute(
            "UPDATE x_watch_queries SET removed_at = now() WHERE id = $1", first
        )
        again = await connection.fetchval(
            insert, organization_id, workspace_id, project_id, "AI agents min_faves:300"
        )
        assert again != first


async def test_a_post_from_a_search_names_its_query_and_a_feed_post_does_not(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        insert = (
            "INSERT INTO x_trend_posts (organization_id, workspace_id, project_id, collected_at,"
            " source, query, post_id, likes) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        )
        moment = datetime.now(UTC)
        await connection.execute(
            insert, organization_id, workspace_id, project_id, moment, "home", None, "1", 3
        )
        await connection.execute(
            insert, organization_id, workspace_id, project_id, moment, "search", "ai", "1", 3
        )
        for source, query in (("search", None), ("home", "ai")):
            with pytest.raises(asyncpg.CheckViolationError) as mismatch:
                await connection.execute(
                    insert,
                    organization_id,
                    workspace_id,
                    project_id,
                    moment,
                    source,
                    query,
                    "2",
                    1,
                )
            assert "x_trend_posts_search_has_its_query" in str(mismatch.value)


async def test_counters_are_measurements_and_ids_are_x_ids(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        insert = (
            "INSERT INTO x_trend_posts (organization_id, workspace_id, project_id, collected_at,"
            " source, post_id, likes) VALUES ($1, $2, $3, $4, 'home', $5, $6)"
        )
        moment = datetime.now(UTC)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                insert, organization_id, workspace_id, project_id, moment, "12", -1
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                insert, organization_id, workspace_id, project_id, moment, "t3_abc", 1
            )
        # NULL is "X did not show it", which is allowed; zero is a measurement, also allowed.
        await connection.execute(
            insert, organization_id, workspace_id, project_id, moment, "12", None
        )
        with pytest.raises(asyncpg.UniqueViolationError) as twice:
            await connection.execute(
                insert, organization_id, workspace_id, project_id, moment, "12", 0
            )
        assert "x_trend_posts_one_per_read" in str(twice.value)
