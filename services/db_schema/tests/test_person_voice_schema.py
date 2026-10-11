"""Migracja 0022: głos osoby, wersjonowany i append-only.

O CO TU CHODZI. Nie o to, że tabela istnieje, tylko o to, że historii głosu NIE DA SIĘ zmienić.
Głos jest dowodem, z czym agent pisał komentarz sprzed tygodnia; wersja poprawiona w miejscu
kasuje ten dowód po cichu i nikt się nie dowie, że odpowiedź powstała z innym opisem człowieka.

Dlatego tu są cztery drogi, którymi ta historia mogłaby zniknąć albo się pomieszać, i każda
kończy się błędem bazy: UPDATE, DELETE, TRUNCATE i dwie wersje o tym samym numerze. Piąta droga
to wyciek między osobami i przestrzeniami: profil z innej przestrzeni nie może dostać wersji.
"""

from __future__ import annotations

import uuid

import pytest
from db_schema.expected_schema import PERSON_VOICE_VERSIONS_COLUMNS

pytestmark = pytest.mark.db


async def _workspace(connection) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id = await connection.fetchval(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"Firma {uuid.uuid4()}"
    )
    workspace_id = await connection.fetchval(
        "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
        organization_id,
        "Przestrzeń",
    )
    return organization_id, workspace_id


async def _profile(connection, organization_id, workspace_id, name: str = "Anna") -> uuid.UUID:
    return await connection.fetchval(
        "INSERT INTO person_profiles (organization_id, workspace_id, name, kind)"
        " VALUES ($1, $2, $3, 'PERSON') RETURNING id",
        organization_id,
        workspace_id,
        name,
    )


async def _version(connection, organization_id, workspace_id, profile_id, **overrides):
    fields = {
        "knows_deeply": "utrzymanie starych systemów",
        "positions": "przepisanie od zera rzadko się zwraca",
        "would_never_claim": "że zna się na rynkach finansowych",
        "voice_samples": "Piszę krótko i konkretnie.",
        "version": None,
    }
    fields.update(overrides)
    version = fields["version"]
    if version is None:
        return await connection.fetchrow(
            """
            INSERT INTO person_voice_versions
                (organization_id, workspace_id, person_profile_id, version, knows_deeply,
                 positions, would_never_claim, voice_samples)
            VALUES ($1, $2, $3,
                    (SELECT coalesce(max(version), 0) + 1 FROM person_voice_versions
                      WHERE organization_id = $1 AND workspace_id = $2 AND person_profile_id = $3),
                    $4, $5, $6, $7)
            RETURNING id, version
            """,
            organization_id,
            workspace_id,
            profile_id,
            fields["knows_deeply"],
            fields["positions"],
            fields["would_never_claim"],
            fields["voice_samples"],
        )
    return await connection.fetchrow(
        """
        INSERT INTO person_voice_versions
            (organization_id, workspace_id, person_profile_id, version, knows_deeply,
             positions, would_never_claim, voice_samples)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, version
        """,
        organization_id,
        workspace_id,
        profile_id,
        version,
        fields["knows_deeply"],
        fields["positions"],
        fields["would_never_claim"],
        fields["voice_samples"],
    )


async def test_the_table_has_exactly_the_declared_columns(pool) -> None:
    assert len(PERSON_VOICE_VERSIONS_COLUMNS) == 11, PERSON_VOICE_VERSIONS_COLUMNS
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'person_voice_versions'"
        )
    present = sorted(row["column_name"] for row in rows)
    assert present, "nie ma tabeli person_voice_versions — migracja 0022 nie została zastosowana"
    assert present == sorted(PERSON_VOICE_VERSIONS_COLUMNS), present


async def test_a_version_cannot_be_edited_in_place(pool) -> None:
    """Sedno całej migracji: UPDATE ma być błędem, nie cichą zmianą historii."""
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        profile_id = await _profile(connection, organization_id, workspace_id)
        written = await _version(connection, organization_id, workspace_id, profile_id)
        assert written["version"] == 1

        with pytest.raises(Exception, match="append-only"):
            await connection.execute(
                "UPDATE person_voice_versions SET positions = $1 WHERE id = $2",
                "co innego",
                written["id"],
            )
        still = await connection.fetchval(
            "SELECT positions FROM person_voice_versions WHERE id = $1", written["id"]
        )
        assert still == "przepisanie od zera rzadko się zwraca"


async def test_a_version_cannot_be_deleted_or_truncated_away(pool) -> None:
    """DELETE i TRUNCATE osobno: wyzwalacz wierszowy nie widzi TRUNCATE (lekcja z 0002)."""
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        profile_id = await _profile(connection, organization_id, workspace_id)
        written = await _version(connection, organization_id, workspace_id, profile_id)

        with pytest.raises(Exception, match="append-only"):
            await connection.execute(
                "DELETE FROM person_voice_versions WHERE id = $1", written["id"]
            )
        with pytest.raises(Exception, match="append-only"):
            await connection.execute("TRUNCATE person_voice_versions")
        assert await connection.fetchval(
            "SELECT count(*) FROM person_voice_versions WHERE id = $1", written["id"]
        )


async def test_two_versions_cannot_share_a_number(pool) -> None:
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        profile_id = await _profile(connection, organization_id, workspace_id)
        await _version(connection, organization_id, workspace_id, profile_id, version=1)
        with pytest.raises(Exception) as duplicate:
            await _version(connection, organization_id, workspace_id, profile_id, version=1)
        assert "person_voice_versions" in str(duplicate.value)


async def test_numbering_starts_from_one_for_each_person(pool) -> None:
    """Druga osoba w tej samej przestrzeni zaczyna od wersji 1, a nie od 2."""
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        anna = await _profile(connection, organization_id, workspace_id, "Anna")
        jan = await _profile(connection, organization_id, workspace_id, "Jan")
        await _version(connection, organization_id, workspace_id, anna)
        second = await _version(connection, organization_id, workspace_id, anna)
        first_for_jan = await _version(connection, organization_id, workspace_id, jan)
        assert second["version"] == 2
        assert first_for_jan["version"] == 1


async def test_a_profile_from_another_workspace_cannot_get_a_voice(pool) -> None:
    """Composite FK, nie ufność: trójka (organizacja, przestrzeń, profil) musi istnieć razem."""
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        other_organization, other_workspace = await _workspace(connection)
        stranger = await _profile(connection, other_organization, other_workspace, "Ktoś obcy")
        with pytest.raises(Exception) as refused:
            await _version(connection, organization_id, workspace_id, stranger)
        assert "person_voice_versions_point_at_profile" in str(refused.value)


async def test_three_of_the_four_fields_refuse_to_be_blank(pool) -> None:
    """Próbki mogą być puste, reszta nie: głos bez treści to głos, który nic nie mówi."""
    async with pool.acquire() as connection:
        organization_id, workspace_id = await _workspace(connection)
        profile_id = await _profile(connection, organization_id, workspace_id)
        for field in ("knows_deeply", "positions", "would_never_claim"):
            with pytest.raises(Exception) as refused:
                await _version(
                    connection, organization_id, workspace_id, profile_id, **{field: "   "}
                )
            assert field in str(refused.value), field
        empty_samples = await _version(
            connection, organization_id, workspace_id, profile_id, voice_samples=""
        )
        assert empty_samples["version"] >= 1
