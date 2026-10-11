"""Migracja 0028: dokumenty projektu jako widok na rejestr (P8 etap A, D-043).

CZTERY WŁASNOŚCI, KTÓRYCH PILNUJE BAZA, bo kod nie może ich obiecać sam:

  1. **Zdanie bez podstawy nie istnieje.** To jest cały mechanizm trzymający dokument przy
     rejestrze: gdyby dało się zapisać zdanie z pustą tablicą, dokument zacząłby nieść
     twierdzenia, których nikt nie ustalił, a „rejestr jest źródłem prawdy" zostałoby obietnicą
     na słowo.
  2. **Jedna tożsamość na rolę w projekcie**, z NULLS NOT DISTINCT, żeby dokumenty przestrzeni
     (podręcznik platformy) nie omijały indeksu przez NULL w `project_id`.
  3. **Podręcznik platformy jest dokumentem przestrzeni**, a każdy inny dokumentem projektu.
     Trzeci układ nie znaczy nic i nie ma go jak czytać.
  4. **Wersja z modelu mówi, jakim promptem.** Wersja z reguł nie ma czego podawać i nie wolno
     jej udawać, że miała prompt.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    DOCUMENT_GENERATED_BY,
    DOCUMENT_ROLES,
    DOCUMENT_TABLE_COLUMNS,
)

pytestmark = [pytest.mark.db, pytest.mark.p8]

DIGEST = "b" * 64


@pytest_asyncio.fixture
async def project(pool):
    """Organizacja, przestrzeń i projekt, czyli najmniejszy grunt pod dokument."""
    async with pool.acquire() as conn:
        organization_id = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"org-{uuid4().hex[:8]}"
        )
        workspace_id = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            organization_id,
            f"ws-{uuid4().hex[:8]}",
        )
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            organization_id,
            workspace_id,
            f"projekt-{uuid4().hex[:8]}",
        )
    return {"organization": organization_id, "workspace": workspace_id, "project": project_id}


async def _document(conn, project, role="PROJECT_CARD", platform=None, project_id=...):
    return await conn.fetchval(
        "INSERT INTO project_documents (organization_id, workspace_id, project_id, role, platform)"
        " VALUES ($1, $2, $3, $4, $5) RETURNING id",
        project["organization"],
        project["workspace"],
        project["project"] if project_id is ... else project_id,
        role,
        platform,
    )


async def _version(conn, project, document_id, version=1, **overrides):
    fields = {
        "generated_by": "RULES",
        "prompt_name": None,
        "prompt_sha256": None,
        "input_sha256": DIGEST,
        "item_count": 1,
        **overrides,
    }
    return await conn.fetchval(
        """
        INSERT INTO project_document_versions
            (organization_id, workspace_id, document_id, version, title, generated_by,
             prompt_name, prompt_sha256, input_sha256, item_count)
        VALUES ($1, $2, $3, $4, 'Karta projektu', $5, $6, $7, $8, $9)
        RETURNING id
        """,
        project["organization"],
        project["workspace"],
        document_id,
        version,
        fields["generated_by"],
        fields["prompt_name"],
        fields["prompt_sha256"],
        fields["input_sha256"],
        fields["item_count"],
    )


@pytest.mark.parametrize("table", sorted(DOCUMENT_TABLE_COLUMNS))
async def test_the_declared_columns_are_the_ones_in_the_database(pool, table: str) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        )
    actual = {row["column_name"] for row in rows}
    assert actual, f"tabela {table} nie istnieje"
    assert actual == set(DOCUMENT_TABLE_COLUMNS[table]), (
        f"{table}: tylko w bazie {sorted(actual - set(DOCUMENT_TABLE_COLUMNS[table]))}, "
        f"tylko w deklaracji {sorted(set(DOCUMENT_TABLE_COLUMNS[table]) - actual)}"
    )


async def test_a_sentence_without_a_basis_is_refused(pool, project) -> None:
    async with pool.acquire() as conn:
        document_id = await _document(conn, project)
        version_id = await _version(conn, project, document_id)
        with pytest.raises(Exception) as caught:
            await conn.execute(
                "INSERT INTO project_document_sentences "
                "(organization_id, workspace_id, version_id, position, section, text, basis) "
                "VALUES ($1, $2, $3, 0, '', 'zdanie bez podstawy', $4)",
                project["organization"],
                project["workspace"],
                version_id,
                [],
            )
    assert "have_a_basis" in str(caught.value)


async def test_a_sentence_with_a_basis_goes_in(pool, project) -> None:
    """Kontrola pozytywna, żeby poprzedni test nie okazał się dowodem na zepsutą wstawkę."""
    async with pool.acquire() as conn:
        document_id = await _document(conn, project)
        version_id = await _version(conn, project, document_id)
        written = await conn.fetchval(
            "INSERT INTO project_document_sentences "
            "(organization_id, workspace_id, version_id, position, section, text, basis) "
            "VALUES ($1, $2, $3, 0, 'Hipotezy', 'testujemy krótkie odpowiedzi', $4) RETURNING id",
            project["organization"],
            project["workspace"],
            version_id,
            [uuid4()],
        )
    assert written is not None


async def test_one_document_per_role_in_a_project(pool, project) -> None:
    async with pool.acquire() as conn:
        await _document(conn, project)
        with pytest.raises(Exception) as caught:
            await _document(conn, project)
    assert "project_documents_one_per_role" in str(caught.value)


async def test_a_handbook_belongs_to_the_workspace_and_a_card_to_a_project(pool, project) -> None:
    async with pool.acquire() as conn:
        # Podręcznik: bez projektu, z platformą. Tak wolno.
        handbook = await _document(
            conn, project, role="PLATFORM_HANDBOOK", platform="REDDIT", project_id=None
        )
        assert handbook is not None
        # Podręcznik przypięty do projektu: nie wolno.
        with pytest.raises(Exception) as caught:
            await _document(conn, project, role="PLATFORM_HANDBOOK", platform="REDDIT")
        assert "handbook_is_a_workspace_document" in str(caught.value)
        # Karta projektu z platformą: też nie.
        with pytest.raises(Exception) as second:
            await _document(conn, project, role="PROJECT_CARD", platform="REDDIT")
        assert "handbook_is_a_workspace_document" in str(second.value)


async def test_a_model_version_names_its_prompt_and_a_rules_version_does_not(pool, project) -> None:
    async with pool.acquire() as conn:
        document_id = await _document(conn, project)
        with pytest.raises(Exception) as caught:
            await _version(conn, project, document_id, generated_by="MODEL")
        assert "model_names_its_prompt" in str(caught.value)

        written = await _version(
            conn,
            project,
            document_id,
            version=2,
            generated_by="MODEL",
            prompt_name="documents_summarise",
            prompt_sha256="c" * 64,
        )
        assert written is not None

        with pytest.raises(Exception) as second:
            await _version(
                conn, project, document_id, version=3, prompt_name="reguły nie mają promptu"
            )
        assert "model_names_its_prompt" in str(second.value)


async def test_the_closed_lists_are_the_declared_ones(pool) -> None:
    async with pool.acquire() as conn:
        roles = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'project_documents_role_is_known'"
        )
        sources = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'project_document_versions_generated_by_is_known'"
        )
    assert roles and sources
    for role in DOCUMENT_ROLES:
        assert f"'{role}'" in roles, role
    for source in DOCUMENT_GENERATED_BY:
        assert f"'{source}'" in sources, source
