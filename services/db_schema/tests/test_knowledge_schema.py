"""Migration 0016 — the rules that make knowledge worth reading back.

Three of them are the difference between a memory and a pile of sentences, and none of them can
be left to the code that writes:

  * a `FACT` has a `source_id`. Without the CHECK, "fact" is a label anybody can attach;
  * a `PREFERENCE` is `stated_by='USER'`. An agent writing "the user prefers X" invents a will
    nobody expressed, and the next session applies it;
  * an `EXPERIENCE` carries situation, decision and outcome. Missing the outcome it is a plan.

And two about not losing anything: a correction points the old row at the new one instead of
overwriting it, and a retraction has to say why.
"""

from __future__ import annotations

import ast
import uuid

import asyncpg
import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    ARTIFACT_KINDS,
    KNOWLEDGE_ITEM_KINDS,
    KNOWLEDGE_SOURCE_KINDS,
    KNOWLEDGE_SOURCE_STATUSES,
    KNOWLEDGE_STATED_BY,
    KNOWLEDGE_TABLE_COLUMNS,
)
from db_schema.migrations import MIGRATIONS_DIR

pytestmark = pytest.mark.db

#: `control_plane.storage.config` cannot be imported from this package — the two services have
#: separate environments — so the enum is read from the source with `ast`. A parity test that
#: could not see both sides would not be a parity test.
CONFIG_SOURCE = (
    MIGRATIONS_DIR.parent.parent / "services/control_plane/control_plane/storage/config.py"
)


@pytest_asyncio.fixture
async def world(pool):
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-0016') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization_id,
        )
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-2') RETURNING id",
            organization_id,
        )
        project_id = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'P')"
            " RETURNING id",
            organization_id,
            workspace_id,
        )
    return {
        "organization": organization_id,
        "workspace": workspace_id,
        "other_workspace": other_workspace,
        "project": project_id,
    }


async def _source(connection, world, **overrides):
    values = {
        "kind": "WEB_PAGE",
        "locator": f"https://example.test/{uuid.uuid4()}",
        "status": "FETCHED",
        "fetched_at": "now()",
        "content_hash": "sha256:" + "a" * 64,
        "project_id": None,
        "unavailable_reason": None,
        **overrides,
    }
    return await connection.fetchval(
        """
        INSERT INTO knowledge_sources
            (organization_id, workspace_id, project_id, kind, locator, status, fetched_at,
             content_hash, unavailable_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        world["organization"],
        world.get("workspace_override") or world["workspace"],
        values["project_id"],
        values["kind"],
        values["locator"],
        values["status"],
        None
        if values["fetched_at"] is None
        else __import__("datetime").datetime.now(__import__("datetime").UTC),
        values["content_hash"],
        values["unavailable_reason"],
    )


async def _item(connection, world, **overrides):
    values = {
        "kind": "INTERPRETATION",
        "statement": "coś z tego wynika",
        "stated_by": "AGENT",
        "source_id": None,
        "project_id": None,
        "payload": "{}",
        **overrides,
    }
    return await connection.fetchval(
        """
        INSERT INTO knowledge_items
            (organization_id, workspace_id, project_id, kind, statement, stated_by, source_id,
             payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        RETURNING id
        """,
        world["organization"],
        world["workspace"],
        values["project_id"],
        values["kind"],
        values["statement"],
        values["stated_by"],
        values["source_id"],
        values["payload"],
    )


async def test_both_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(KNOWLEDGE_TABLE_COLUMNS) == 2, sorted(KNOWLEDGE_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, expected in KNOWLEDGE_TABLE_COLUMNS.items():
            rows = await connection.fetch(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = $1",
                table,
            )
            present = {row["column_name"]: row["is_nullable"] for row in rows}
            assert present, f"nie ma tabeli {table} — migracja 0016 nie została zastosowana"
            assert sorted(present) == sorted(expected), sorted(present)
            assert present["organization_id"] == "NO"
            assert present["workspace_id"] == "NO"
            # The project is OPTIONAL on purpose: P3 wants material that belongs to the whole
            # workspace as well as to one project.
            assert present["project_id"] == "YES", f"{table}.project_id musi być nullowalne"


async def test_the_artifact_kind_list_gained_source_and_nothing_else(pool) -> None:
    """0016 widened a CHECK from 0003, 0018 widened it again. Both directions, every time.

    The count moved from six to seven when P7 added `EXPORT`, and the both-directions part is
    not ceremony: rewriting this CHECK is exactly how a value disappears. It happened during P7
    — the first draft of 0018 re-listed the kinds from memory and dropped `LOG`, and what broke
    was a browser-artifact test three packages away.
    """
    assert "SOURCE" in ARTIFACT_KINDS and "EXPORT" in ARTIFACT_KINDS
    assert len(ARTIFACT_KINDS) == 7, sorted(ARTIFACT_KINDS)
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'artifacts_kind_is_known'"
            "   AND conrelid = 'artifacts'::regclass"
        )
    assert definition is not None, "0016 zgubiła więz artifacts_kind_is_known"
    for kind in ARTIFACT_KINDS:
        assert f"'{kind}'" in definition, f"{kind} wypadł z listy: {definition}"
    assert "'DOCUMENT'" not in definition, definition


def test_the_python_artifact_kind_enum_matches_the_declared_set() -> None:
    """The enum in `control_plane.storage.config` and the list here are one truth, read as two.

    Parsed with `ast` because the two packages have separate environments and neither can import
    the other. A parity test that could only see one side would pass while the two drifted, which
    is what `test_enum_parity.py` says about names in `pg_constraint`, one layer up.
    """
    tree = ast.parse(CONFIG_SOURCE.read_text(encoding="utf-8"))
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ArtifactKind":
            for statement in node.body:
                if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
                    members.add(str(statement.value.value))
    assert members, f"nie znaleziono ArtifactKind w {CONFIG_SOURCE}"
    assert members == set(ARTIFACT_KINDS), (
        f"enum w Pythonie ma {sorted(members)}, a zadeklarowana lista {sorted(ARTIFACT_KINDS)}"
    )


@pytest.mark.parametrize(
    ("column", "values", "table"),
    [
        ("kind", KNOWLEDGE_SOURCE_KINDS, "knowledge_sources"),
        ("status", KNOWLEDGE_SOURCE_STATUSES, "knowledge_sources"),
        ("kind", KNOWLEDGE_ITEM_KINDS, "knowledge_items"),
        ("stated_by", KNOWLEDGE_STATED_BY, "knowledge_items"),
    ],
)
async def test_the_closed_lists_are_in_the_database(pool, column, values, table) -> None:
    assert values, f"{table}.{column}: pusta lista nie jest listą zamkniętą"
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = $1 AND conrelid = $2::regclass",
            f"{table}_{column}_is_known",
            table,
        )
    assert definition is not None, f"nie ma więzu {table}_{column}_is_known"
    for value in values:
        assert f"'{value}'" in definition, f"{value} nie jest w {definition}"


async def test_a_fact_without_a_source_is_refused(pool, world) -> None:
    """The whole difference between `FACT` and `INTERPRETATION` in this schema."""
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError, match="fact_has_a_source"):
            await _item(connection, world, kind="FACT", stated_by="SOURCE")
        # With a source it goes in, so the refusal above is about the source and nothing else.
        source_id = await _source(connection, world)
        assert await _item(connection, world, kind="FACT", stated_by="SOURCE", source_id=source_id)


async def test_a_preference_must_come_from_the_user(pool, world) -> None:
    async with pool.acquire() as connection:
        for author in ("AGENT", "SOURCE"):
            with pytest.raises(asyncpg.CheckViolationError, match="preference_comes_from_the_user"):
                await _item(connection, world, kind="PREFERENCE", stated_by=author)
        assert await _item(connection, world, kind="PREFERENCE", stated_by="USER")


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"situation": "a"}',
        '{"situation": "a", "decision": "b"}',
        '{"decision": "b", "outcome": "c"}',
    ],
)
async def test_an_experience_without_all_three_parts_is_refused(pool, world, payload) -> None:
    """Situation, decision and outcome. Missing the outcome it is a plan, missing the situation
    an anecdote, and neither is something the next session can learn from."""
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError, match="experience_is_complete"):
            await _item(connection, world, kind="EXPERIENCE", payload=payload)
        assert await _item(
            connection,
            world,
            kind="EXPERIENCE",
            payload='{"situation": "a", "decision": "b", "outcome": "c"}',
        )


async def test_a_correction_points_the_old_row_at_the_new_one_and_deletes_nothing(
    pool, world
) -> None:
    """P3 asks that a correction be VISIBLE later. Overwriting would make it unobservable."""
    async with pool.acquire() as connection:
        old = await _item(connection, world, statement="stara wersja")
        new = await _item(connection, world, statement="nowa wersja")
        await connection.execute(
            "UPDATE knowledge_items SET superseded_by = $2 WHERE id = $1", old, new
        )
        rows = await connection.fetch(
            "SELECT id, statement, superseded_by FROM knowledge_items"
            " WHERE organization_id = $1 ORDER BY created_at",
            world["organization"],
        )
    assert len(rows) == 2, rows
    assert rows[0]["statement"] == "stara wersja", "poprawka nadpisała treść zamiast dopisać"
    assert rows[0]["superseded_by"] == new


async def test_a_retraction_needs_a_reason(pool, world) -> None:
    async with pool.acquire() as connection:
        item = await _item(connection, world)
        for reason in (None, "", "   ", "\t"):
            with pytest.raises(asyncpg.CheckViolationError, match="retraction_has_a_reason"):
                await connection.execute(
                    "UPDATE knowledge_items SET retracted_at = now(), retracted_reason = $2"
                    " WHERE id = $1",
                    item,
                    reason,
                )
        await connection.execute(
            "UPDATE knowledge_items SET retracted_at = now(), retracted_reason = 'nieaktualne'"
            " WHERE id = $1",
            item,
        )


async def test_a_fetched_source_must_say_when_and_what(pool, world) -> None:
    """ "Pobrane" without a moment and a hash is a word, not a measurement."""
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError, match="fetched_have_evidence"):
            await _source(connection, world, content_hash=None)
        with pytest.raises(asyncpg.CheckViolationError, match="fetched_have_evidence"):
            await _source(connection, world, fetched_at=None)


async def test_an_unavailable_source_has_a_reason_and_no_content(pool, world) -> None:
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError, match="unavailable_have_reason"):
            await _source(
                connection, world, status="UNAVAILABLE", content_hash=None, fetched_at=None
            )
        # Content on an unavailable source is the substitution this guards against: we did not
        # read it, so we may not carry bytes claiming we did.
        with pytest.raises(asyncpg.CheckViolationError, match="unavailable_have_reason"):
            await _source(
                connection,
                world,
                status="UNAVAILABLE",
                fetched_at=None,
                unavailable_reason="404",
            )
        assert await _source(
            connection,
            world,
            status="UNAVAILABLE",
            content_hash=None,
            fetched_at=None,
            unavailable_reason="404 z serwera",
        )


async def test_one_row_per_locator_in_a_scope_even_without_a_project(pool, world) -> None:
    """`NULLS NOT DISTINCT`, and the reason it is not optional.

    Without it two workspace-wide materials at the same address are different rows to the index,
    so every refresh would add one and the list of materials would grow forever.
    """
    async with pool.acquire() as connection:
        locator = "https://example.test/staly"
        await _source(connection, world, locator=locator)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _source(connection, world, locator=locator)
        # The same address inside a PROJECT is a different scope and is allowed.
        assert await _source(connection, world, locator=locator, project_id=world["project"])


async def test_knowledge_cannot_reach_across_workspaces(pool, world) -> None:
    """A finding in one workspace may not stand on a source from another."""
    async with pool.acquire() as connection:
        foreign_source = await _source(
            connection, {**world, "workspace_override": world["other_workspace"]}
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _item(
                connection, world, kind="FACT", stated_by="SOURCE", source_id=foreign_source
            )
