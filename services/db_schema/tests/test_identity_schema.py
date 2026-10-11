"""Migration 0018: users, memberships, invitations, export and deletion jobs (P7 etap A).

WHAT THESE TESTS ARE ABOUT. Not "the tables exist": whether the rules that keep two customers
apart are in the DATABASE rather than in a code path somebody could forget to call. Every case
below is a way two organizations could end up sharing something:

  * one email, two accounts, and a person who cannot see their own data;
  * two active memberships for one pair, so revoking one leaves the other;
  * an invitation token stored as itself, so a database leak is a key to somebody's space;
  * a deletion with two grace periods, so nobody can say when the data really goes;
  * the founder's migrated account losing its access at the moment membership starts mattering.
"""

from __future__ import annotations

import uuid

import pytest
from db_schema.expected_schema import (
    DATA_DELETION_STATUSES,
    DATA_EXPORT_STATUSES,
    IDENTITY_TABLE_COLUMNS,
    MEMBERSHIP_ROLES,
)

pytestmark = pytest.mark.db

ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$" + "a" * 43


async def _organization(connection, name: str = "Firma") -> uuid.UUID:
    return await connection.fetchval(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id", name
    )


async def _user(connection, email: str | None) -> uuid.UUID:
    return await connection.fetchval(
        "INSERT INTO users (email, credential_hash) VALUES ($1, $2) RETURNING id", email, ARGON2
    )


async def test_the_five_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(IDENTITY_TABLE_COLUMNS) == 5, sorted(IDENTITY_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, expected in IDENTITY_TABLE_COLUMNS.items():
            rows = await connection.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = $1",
                table,
            )
            present = sorted(row["column_name"] for row in rows)
            assert present, f"nie ma tabeli {table} — migracja 0018 nie została zastosowana"
            assert present == sorted(expected), present


async def test_none_of_them_carries_a_workspace(pool) -> None:
    """A person belongs to an organization. A `workspace_id` here would be a scope nobody owns."""
    async with pool.acquire() as connection:
        for table in IDENTITY_TABLE_COLUMNS:
            has = await connection.fetchval(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'"
                " AND table_name = $1 AND column_name = 'workspace_id'",
                table,
            )
            assert has == 0, f"{table} ma workspace_id, choć tożsamość jest organizacyjna"


async def test_one_email_is_one_account(pool) -> None:
    async with pool.acquire() as connection:
        await _user(connection, "ktos@firma.test")
        with pytest.raises(Exception) as duplicate:
            await _user(connection, "ktos@firma.test")
        assert "users_one_account_per_email" in str(duplicate.value)


async def test_an_address_is_stored_lowercase_or_not_at_all(pool) -> None:
    """Two spellings of one address are two accounts and one person who cannot see their data."""
    async with pool.acquire() as connection:
        with pytest.raises(Exception) as refused:
            await _user(connection, "Ktos@Firma.test")
        assert "users_email_is_lowercase" in str(refused.value)


async def test_an_account_without_an_address_is_allowed_because_the_founder_has_none(pool) -> None:
    """0018 moved the single `operators` row over and nobody ever recorded whose it is."""
    async with pool.acquire() as connection:
        first = await _user(connection, None)
        second = await _user(connection, None)
        assert first != second, "dwa konta bez adresu muszą móc istnieć obok siebie"


async def test_only_one_active_membership_per_pair(pool) -> None:
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        user = await _user(connection, "czlonek@firma.test")
        await connection.execute(
            "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'MEMBER')",
            user,
            organization,
        )
        with pytest.raises(Exception) as duplicate:
            await connection.execute(
                "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'OWNER')",
                user,
                organization,
            )
        assert "memberships_one_active_per_pair" in str(duplicate.value)

        # Revoking leaves the row and frees the pair: history stays, access can be granted again.
        await connection.execute(
            "UPDATE memberships SET revoked_at = now() WHERE user_id = $1", user
        )
        await connection.execute(
            "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'MEMBER')",
            user,
            organization,
        )
        rows = await connection.fetchval(
            "SELECT count(*) FROM memberships WHERE user_id = $1", user
        )
        assert rows == 2


async def test_the_role_vocabulary_is_closed_at_two(pool) -> None:
    assert {"OWNER", "MEMBER"} == MEMBERSHIP_ROLES
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        user = await _user(connection, "rola@firma.test")
        with pytest.raises(Exception) as refused:
            await connection.execute(
                "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'ADMIN')",
                user,
                organization,
            )
        assert "memberships_role_is_known" in str(refused.value)


async def test_an_invitation_keeps_a_digest_and_never_the_token(pool) -> None:
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        inviter = await _user(connection, "wlasciciel@firma.test")
        with pytest.raises(Exception) as refused:
            await connection.execute(
                "INSERT INTO invitations (organization_id, email, token_hash, expires_at, "
                "created_by) VALUES ($1, 'kto@firma.test', 'nie-jest-skrotem', "
                "now() + interval '1 day', $2)",
                organization,
                inviter,
            )
        assert "token_hash" in str(refused.value)


async def test_an_invitation_that_expires_before_it_is_made_is_refused(pool) -> None:
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        inviter = await _user(connection, "termin@firma.test")
        with pytest.raises(Exception) as refused:
            await connection.execute(
                "INSERT INTO invitations (organization_id, email, token_hash, expires_at, "
                "created_by) VALUES ($1, 'kto@firma.test', $3, now() - interval '1 day', $2)",
                organization,
                inviter,
                "b" * 64,
            )
        assert "invitations_expire_after_they_are_made" in str(refused.value)


async def test_one_live_deletion_per_organization(pool) -> None:
    """Two grace periods for the same data mean nobody can say when it really goes."""
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        user = await _user(connection, "wlasciciel2@firma.test")
        await connection.execute(
            "INSERT INTO data_deletion_jobs (organization_id, requested_by, purge_after) "
            "VALUES ($1, $2, now() + interval '7 days')",
            organization,
            user,
        )
        with pytest.raises(Exception) as duplicate:
            await connection.execute(
                "INSERT INTO data_deletion_jobs (organization_id, requested_by, purge_after) "
                "VALUES ($1, $2, now() + interval '1 day')",
                organization,
                user,
            )
        assert "data_deletion_jobs_one_live_per_organization" in str(duplicate.value)


async def test_a_ready_export_must_have_a_file_and_a_date(pool) -> None:
    """READY with no artifact is a download link pointing at nothing."""
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        user = await _user(connection, "eksport@firma.test")
        job = await connection.fetchval(
            "INSERT INTO data_export_jobs (organization_id, requested_by) VALUES ($1, $2) "
            "RETURNING id",
            organization,
            user,
        )
        with pytest.raises(Exception) as refused:
            await connection.execute(
                "UPDATE data_export_jobs SET status = 'READY' WHERE id = $1", job
            )
        assert "data_export_jobs_ready_have_a_file" in str(refused.value)


async def test_a_failed_export_must_say_why(pool) -> None:
    async with pool.acquire() as connection:
        organization = await _organization(connection)
        user = await _user(connection, "porazka@firma.test")
        job = await connection.fetchval(
            "INSERT INTO data_export_jobs (organization_id, requested_by) VALUES ($1, $2) "
            "RETURNING id",
            organization,
            user,
        )
        with pytest.raises(Exception) as refused:
            await connection.execute(
                "UPDATE data_export_jobs SET status = 'FAILED' WHERE id = $1", job
            )
        assert "data_export_jobs_failed_say_why" in str(refused.value)


async def test_the_status_vocabularies_are_the_declared_ones(pool) -> None:
    assert {"REQUESTED", "RUNNING", "READY", "FAILED", "PURGED"} == DATA_EXPORT_STATUSES
    assert {"REQUESTED", "PURGED", "CANCELLED", "FAILED"} == DATA_DELETION_STATUSES


async def test_the_existing_operator_becomes_a_user_who_owns_their_organization(pool) -> None:
    """THE data migration of 0018. Without it the founder loses access to their own product.

    THE STATEMENTS ARE READ FROM THE MIGRATION FILE and executed here against an operator this
    test creates. Not asserted against the rows the migration produced when it ran: on a fresh
    test database there was no operator at that moment, so such a test would iterate over an
    empty list and pass while saying nothing. Reading the file means the thing under test is the
    code that will run on the founder's database, and an edit to it either keeps this green or
    does not.
    """
    statements = _migration_data_moves()
    assert len(statements) == 2, "0018 ma dwa kroki przeniesienia: konto i członkostwo"

    async with pool.acquire() as connection:
        organization = await _organization(connection, "Organizacja założyciela")
        credential = ARGON2.replace("a" * 43, "z" * 43)
        await connection.execute(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2)",
            organization,
            credential,
        )
        for statement in statements:
            await connection.execute(statement)

        user = await connection.fetchrow(
            "SELECT id, email FROM users WHERE credential_hash = $1", credential
        )
        assert user is not None, "operator nie dostał konta użytkownika"
        # No invented address: the column stays NULL until the person fills it in.
        assert user["email"] is None
        role = await connection.fetchval(
            "SELECT role FROM memberships WHERE user_id = $1 AND organization_id = $2 "
            "AND revoked_at IS NULL",
            user["id"],
            organization,
        )
        assert role == "OWNER", "przeniesiony operator nie jest właścicielem swojej organizacji"


def _migration_data_moves() -> list[str]:
    """The two INSERT ... SELECT statements at the end of 0018, taken from the file itself."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    sql = (root / "db" / "migrations" / "0018_identity.sql").read_text(encoding="utf-8")
    found = []
    for chunk in sql.split(";"):
        # Comment lines are stripped first: the statements in the file are preceded by the
        # paragraph explaining why they exist, and a naive `startswith` would miss the first one.
        statement = "\n".join(
            line for line in chunk.strip().splitlines() if not line.strip().startswith("--")
        ).strip()
        if statement.startswith(("INSERT INTO users", "INSERT INTO memberships")):
            found.append(statement)
    return found
