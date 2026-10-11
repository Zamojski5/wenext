"""Migracja 0037: tokeny hostowanego MCP i tożsamości z logowania, każde wskazuje jeden projekt.

O CO TU CHODZI. Token w adresie `/mcp/<token>` jest jedynym kluczem do cudzego projektu, więc
tabela ma trzy rzeczy, których nie wolno jej zgubić, i każda ma tu swój przypadek:

  * w bazie leży skrót, nie token: kolumna przyjmuje wyłącznie 64 znaki hex, więc wpisanie
    samego tokenu (43 znaki base64url) jest odrzucone przez bazę, a nie tylko przez kod;
  * jeden skrót to jeden wiersz (klucz główny), więc dwa projekty nigdy nie dzielą tokenu;
  * token nie może wskazać projektu z innej organizacji ani przestrzeni (trójka tenantowa).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

import asyncpg
import pytest
from db_schema.expected_schema import EXTERNAL_IDENTITIES_COLUMNS, HOSTED_TOKENS_COLUMNS

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


def _digest() -> str:
    return hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()


_INSERT = (
    "INSERT INTO hosted_tokens (token_sha256, organization_id, workspace_id, project_id, label)"
    " VALUES ($1, $2, $3, $4, 'ktos@example.com')"
)


async def test_the_table_has_exactly_the_declared_columns(pool) -> None:
    assert len(HOSTED_TOKENS_COLUMNS) == 8
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'hosted_tokens'"
        )
    columns = sorted(row["column_name"] for row in rows)
    assert columns, "nie ma tabeli hosted_tokens, migracja 0037 nie została zastosowana"
    assert columns == sorted(HOSTED_TOKENS_COLUMNS), columns


async def test_only_a_sha256_digest_can_be_stored_never_the_token_itself(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        await connection.execute(_INSERT, _digest(), organization_id, workspace_id, project_id)
        for not_a_digest in (
            secrets.token_urlsafe(32),  # sam token: 43 znaki, nie 64
            "A" * 64,  # wielkie litery to nie hex małymi literami
            "",
            "g" * 64,
        ):
            with pytest.raises(asyncpg.CheckViolationError) as refused:
                await connection.execute(
                    _INSERT, not_a_digest, organization_id, workspace_id, project_id
                )
            assert "hosted_tokens_digest_is_sha256_hex" in str(refused.value), not_a_digest


async def test_one_digest_is_one_row(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        digest = _digest()
        await connection.execute(_INSERT, digest, organization_id, workspace_id, project_id)
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(_INSERT, digest, organization_id, workspace_id, project_id)


async def test_a_token_cannot_point_at_a_project_of_another_workspace(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        other_organization, other_workspace, _ = await _project(connection)
        # Projekt z organizacji A, przestrzeń i organizacja z B: trójka musi to odrzucić.
        for organization, workspace in (
            (other_organization, workspace_id),
            (organization_id, other_workspace),
            (other_organization, other_workspace),
        ):
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await connection.execute(_INSERT, _digest(), organization, workspace, project_id)


async def test_a_revocation_cannot_predate_the_token(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        digest = _digest()
        await connection.execute(_INSERT, digest, organization_id, workspace_id, project_id)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "UPDATE hosted_tokens SET revoked_at = created_at - interval '1 second'"
                " WHERE token_sha256 = $1",
                digest,
            )
        await connection.execute(
            "UPDATE hosted_tokens SET revoked_at = now() WHERE token_sha256 = $1", digest
        )


# ---------------------------------------------------------------------------
# external_identities: kto jest kim po stronie dostawcy logowania
# ---------------------------------------------------------------------------

_IDENTITY = (
    "INSERT INTO external_identities (provider, subject, organization_id, workspace_id, project_id)"
    " VALUES ($1, $2, $3, $4, $5)"
)


async def test_the_identity_table_has_exactly_the_declared_columns(pool) -> None:
    assert len(EXTERNAL_IDENTITIES_COLUMNS) == 10
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'external_identities'"
        )
    columns = sorted(row["column_name"] for row in rows)
    assert columns, "nie ma tabeli external_identities, migracja 0037 nie została zastosowana"
    assert columns == sorted(EXTERNAL_IDENTITIES_COLUMNS), columns


async def test_one_provider_and_subject_is_one_person_and_one_project_has_one_person(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        subject = secrets.token_hex(8)
        await connection.execute(
            _IDENTITY, "github", subject, organization_id, workspace_id, project_id
        )
        # Ta sama para, ten sam albo inny projekt: klucz główny.
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                _IDENTITY, "github", subject, organization_id, workspace_id, project_id
            )
        # Ten sam podmiot u innego dostawcy to inna osoba.
        other_org, other_ws, other_project = await _project(connection)
        await connection.execute(_IDENTITY, "google", subject, other_org, other_ws, other_project)
        # Dwie osoby w jednym projekcie: hostowany projekt jest jednoosobowy.
        with pytest.raises(asyncpg.UniqueViolationError) as twice:
            await connection.execute(
                _IDENTITY, "github", secrets.token_hex(8), organization_id, workspace_id, project_id
            )
        assert "external_identities_one_per_project" in str(twice.value)


async def test_an_identity_cannot_point_at_a_project_of_another_workspace(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        other_organization, other_workspace, _ = await _project(connection)
        for organization, workspace in (
            (other_organization, workspace_id),
            (organization_id, other_workspace),
        ):
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await connection.execute(
                    _IDENTITY, "github", secrets.token_hex(8), organization, workspace, project_id
                )


async def test_provider_and_subject_cannot_be_blank(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id, project_id = await _project(connection)
        for provider, subject in (("", "1"), ("github", ""), ("  ", "1"), ("github", "x" * 256)):
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    _IDENTITY, provider, subject, organization_id, workspace_id, project_id
                )
