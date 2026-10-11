"""Migration 0002 — the repairs to an ALREADY APPLIED migration 0001.

Four things are proved here, and each one is a defect that migration 0001 shipped:

1. **Twelve Polish identifiers are gone, and the twelve English ones exist.** Both halves
   matter. A test that only checked the Polish names absent would pass just as happily if
   0002 had DROPped the objects and forgotten to recreate them — the constraint would be gone,
   the invariant unenforced, and the test green. And the check reads FOUR system catalogs,
   because `ALTER TABLE ... RENAME CONSTRAINT` touches neither indexes nor functions nor
   triggers: a version of this test that read only `pg_constraint` would have left eight of
   the twelve in place and said nothing.

2. **`capability_snapshots.required_browser_mode` is NOT NULL.** In 0001 the column was
   nullable two lines below a comment asserting it was not, and the CHECK
   `manual_only_is_not_headless` evaluates to NULL — that is, PASSES — for a NULL mode. DoD F0
   L5177 ("każda capability ma zapisany required_browser_mode") was therefore being scored
   against rows that could have no mode at all.

3. **`TRUNCATE audit_events` is refused.** 0001's append-only trigger is `FOR EACH ROW`, and
   TRUNCATE fires no row triggers. An "append-only" journal that one statement empties is not
   append-only; it is a journal with a documented hole.

4. **`audit_events.workspace_id` has a composite foreign key.** It was a bare uuid. D-020
   forbids retrofitting the tenant TRIPLE onto tables from 0001, so what 0002 adds is the pair
   `(organization_id, workspace_id) -> workspaces(organization_id, id)` with MATCH SIMPLE, which
   leaves an organization-scoped event (workspace_id IS NULL) legal and rejects a workspace that
   belongs to a different organization.

Plus the two additions 0002 carries: `browser_profiles.encrypted_storage_ref` (L3730) and the
`operators` table whose single-row rule is enforced by a partial unique index rather than by
application code (D-016).
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio
from db_schema.expected_schema import RENAME_CATALOGS, RENAMED_IN_0002

pytestmark = pytest.mark.db


#: How each catalog is queried. `pg_index` is read through `pg_class`, because an index is a
#: relation: `pg_index` itself has no name column, and a test that looked for `indexname` in
#: `pg_index` would error rather than report drift.
_CATALOG_QUERIES: dict[str, str] = {
    "pg_constraint": "SELECT conname FROM pg_constraint",
    "pg_index": "SELECT relname FROM pg_class WHERE relkind = 'i'",
    "pg_proc": "SELECT proname FROM pg_proc",
    "pg_trigger": "SELECT tgname FROM pg_trigger",
}


@pytest_asyncio.fixture
async def tenant(pool):
    """One organization and one workspace, created fresh for the test that asks for them.

    Defined in this module rather than in `conftest.py` on purpose: `conftest.py` belongs to
    another package of this phase and is not this author's file to edit.
    """
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-0002') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization_id,
        )
    return {"organization": organization_id, "workspace": workspace_id}


async def _catalog_names(connection, catalog: str) -> set[str]:
    rows = await connection.fetch(_CATALOG_QUERIES[catalog])
    names = {row[0] for row in rows}
    # A catalog query returning nothing is a broken query, not an empty database: every one of
    # these four catalogs is populated in any PostgreSQL cluster. Without this guard a typo in
    # the SQL above would make the "no Polish names" assertion below pass over an empty set.
    assert names, f"zapytanie do {catalog} nie zwróciło ANI JEDNEGO wiersza — to zły filtr"
    return names


async def test_all_four_catalogs_are_read() -> None:
    """The list of catalogs and the list of queries have to be the same four.

    This is the guard on the guard. The real risk in this file is not a wrong assertion, it is
    a catalog nobody queries — that is how eight of the twelve names would have survived.
    """
    assert len(RENAME_CATALOGS) == 4, RENAME_CATALOGS
    assert set(RENAME_CATALOGS) == set(_CATALOG_QUERIES), (
        "lista katalogów rozjechała się z listą zapytań: "
        f"{set(RENAME_CATALOGS) ^ set(_CATALOG_QUERIES)}"
    )
    assert len(RENAMED_IN_0002) == 12, (
        f"0001 zostawiło dwanaście polskich nazw, a lista ma {len(RENAMED_IN_0002)}"
    )
    # Every catalog must actually be exercised by at least one renamed object, or the coverage
    # of this file is narrower than it looks.
    used = {entry.catalog for entry in RENAMED_IN_0002}
    assert used == set(RENAME_CATALOGS), f"katalogi bez ani jednej nazwy do sprawdzenia: {used}"


async def test_no_polish_name_survives_in_any_of_the_four_catalogs(pool) -> None:
    async with pool.acquire() as connection:
        present: dict[str, set[str]] = {
            catalog: await _catalog_names(connection, catalog) for catalog in RENAME_CATALOGS
        }

    assert len(RENAMED_IN_0002) == 12, RENAMED_IN_0002
    survivors = [
        f"{entry.old_name} ({entry.catalog})"
        for entry in RENAMED_IN_0002
        if entry.old_name in present[entry.catalog]
    ]
    assert not survivors, (
        f"polskie nazwy nadal w bazie po migracji 0002: {survivors}. "
        "RENAME CONSTRAINT nie dotyka indeksów, funkcji ani triggerów — każdy katalog osobno"
    )


async def test_every_renamed_object_exists_under_its_english_name(pool) -> None:
    """The other half of the rename, and the half that catches a DROP without a CREATE.

    Absence of the Polish name proves nothing on its own: an object dropped and never recreated
    satisfies it perfectly, while the invariant it enforced is simply gone.
    """
    async with pool.acquire() as connection:
        present: dict[str, set[str]] = {
            catalog: await _catalog_names(connection, catalog) for catalog in RENAME_CATALOGS
        }

    assert len(RENAMED_IN_0002) == 12, RENAMED_IN_0002
    missing = [
        f"{entry.new_name} ({entry.catalog})"
        for entry in RENAMED_IN_0002
        if entry.new_name not in present[entry.catalog]
    ]
    assert not missing, (
        f"po migracji 0002 brakuje obiektów pod nowymi nazwami: {missing}. "
        "Sam brak polskiej nazwy nie jest dowodem przemianowania — DROP bez CREATE też go daje"
    )


async def test_capability_snapshot_without_browser_mode_is_rejected(pool, tenant) -> None:
    """L5177: every capability has a recorded `required_browser_mode`.

    In 0001 the column was nullable, so this INSERT succeeded and the CHECK
    `manual_only_is_not_headless` returned NULL for it — SQL three-valued logic treats that as
    "not violated". The row went in, and the DoD control counted it.
    """
    async with pool.acquire() as connection:
        account_id = await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'fixture', 'konto-0002') RETURNING id
            """,
            tenant["organization"],
        )
        with pytest.raises(asyncpg.NotNullViolationError):
            await connection.execute(
                """
                INSERT INTO capability_snapshots
                    (organization_id, social_account_id, surface_ref, capability, state,
                     execution_mode, side_effect, retry_class, browser_build_digest,
                     reason_code, confidence, adapter_version, verified_at, expires_at)
                VALUES ($1, $2, 'fixture://c', 'READ_THREAD', 'MANUAL_ONLY', 'MANUAL_ONLY',
                        'NONE', 'NO_RETRY', 'sha256:x', 'PROBE', 0.9, 'fixture-1.0.0',
                        now(), now() + interval '1 day')
                """,
                tenant["organization"],
                account_id,
            )


async def test_truncate_on_audit_events_is_rejected(pool, tenant) -> None:
    """A statement-level BEFORE TRUNCATE trigger, because TRUNCATE fires no row triggers.

    The 0001 trigger is `FOR EACH ROW`, so `TRUNCATE audit_events` emptied the append-only
    journal without touching it and without an error.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO audit_events (organization_id, kind, subject_ref, actor_ref)
            VALUES ($1, 'TEST', 'x', 'y')
            """,
            tenant["organization"],
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute("TRUNCATE audit_events")

        # And the row is still there. Without this the test would also pass if TRUNCATE had
        # emptied the table and then raised.
        remaining = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1", tenant["organization"]
        )
        assert remaining == 1, f"TRUNCATE rzucił, ale dziennik ma {remaining} wierszy zamiast 1"


async def test_row_update_and_delete_on_audit_events_are_still_rejected(pool, tenant) -> None:
    """0002 drops and recreates the trigger; the row-level guard must survive that.

    `services/profile_lease/tests/test_lease_fencing.py::test_audit_is_append_only` matches the
    exception text on the word "append-only", so the message is part of the contract of this
    function and not free to change.
    """
    async with pool.acquire() as connection:
        event_id = await connection.fetchval(
            """
            INSERT INTO audit_events (organization_id, kind, subject_ref, actor_ref)
            VALUES ($1, 'TEST', 'x', 'y') RETURNING id
            """,
            tenant["organization"],
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute(
                "UPDATE audit_events SET kind = 'CHANGED' WHERE id = $1", event_id
            )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await connection.execute("DELETE FROM audit_events WHERE id = $1", event_id)


async def test_audit_event_cannot_point_at_a_workspace_of_another_organization(
    pool, tenant
) -> None:
    """The composite FK 0002 adds. Rejection comes from the database, not from the code (D-020)."""
    async with pool.acquire() as connection:
        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-0002') RETURNING id"
        )
        other_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization_id,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO audit_events (organization_id, workspace_id, kind, subject_ref, actor_ref)
                VALUES ($1, $2, 'TEST', 'x', 'y')
                """,
                tenant["organization"],
                other_workspace_id,
            )


async def test_organization_scoped_audit_event_is_still_legal(pool, tenant) -> None:
    """MATCH SIMPLE, not MATCH FULL: a NULL `workspace_id` satisfies the key.

    Without this test the FK above could be tightened to MATCH FULL by accident, which would
    make every organization-scoped audit event unwritable — and 0001 writes them (L2448).
    """
    async with pool.acquire() as connection:
        written = await connection.fetchval(
            """
            INSERT INTO audit_events (organization_id, workspace_id, kind, subject_ref, actor_ref)
            VALUES ($1, NULL, 'TEST', 'x', 'y') RETURNING id
            """,
            tenant["organization"],
        )
        assert written is not None


async def test_workspace_scoped_audit_event_is_accepted(pool, tenant) -> None:
    """The positive case of the same key — a matching pair goes in."""
    async with pool.acquire() as connection:
        written = await connection.fetchval(
            """
            INSERT INTO audit_events (organization_id, workspace_id, kind, subject_ref, actor_ref)
            VALUES ($1, $2, 'TEST', 'x', 'y') RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
        )
        assert written is not None


async def test_browser_profiles_has_the_encrypted_storage_reference(pool) -> None:
    """L3730 lists `encrypted_storage_ref` and 0001 has no such column.

    It is NULLABLE, and that is a stated limitation rather than an oversight: a NOT NULL would
    need either a fabricated default value for a column whose content is an encrypted storage
    handle (D-018), or a rewrite of the F0 lease suite whose INSERTs predate the column.
    """
    async with pool.acquire() as connection:
        column = await connection.fetchrow(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'browser_profiles'
              AND column_name = 'encrypted_storage_ref'
            """
        )
    assert column is not None, "brak kolumny browser_profiles.encrypted_storage_ref (L3730)"
    assert column["data_type"] == "text", column["data_type"]


async def test_empty_encrypted_storage_reference_is_rejected(pool, tenant) -> None:
    """An empty string is not a storage handle. NULL means "not yet"; '' means nothing at all."""
    async with pool.acquire() as connection:
        account_id = await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'fixture', 'konto-storage') RETURNING id
            """,
            tenant["organization"],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO browser_profiles
                    (organization_id, social_account_id, state, profile_build_marker,
                     profile_platform, encrypted_storage_ref)
                VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing/153.0.8010.12',
                        'linux-arm64', '')
                """,
                tenant["organization"],
                account_id,
            )


async def test_only_one_enabled_operator_per_organization(pool, tenant) -> None:
    """D-016: one operator enforced by the SCHEMA, not by application code.

    The partial unique index is the whole mechanism. A second enabled row is refused by
    PostgreSQL; a disabled one is not, because disabling and replacing an operator has to stay
    possible without dropping an index.
    """
    argon2_hash = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$0000000000000000000000000000"
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2)",
            tenant["organization"],
            argon2_hash,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2)",
                tenant["organization"],
                argon2_hash,
            )

        # Disabling the first one frees the slot — otherwise rotating the operator's password
        # would require a migration.
        await connection.execute(
            "UPDATE operators SET disabled_at = now() WHERE organization_id = $1",
            tenant["organization"],
        )
        replacement = await connection.fetchval(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2) RETURNING id",
            tenant["organization"],
            argon2_hash,
        )
        assert replacement is not None


async def test_operators_table_has_no_role_column(pool) -> None:
    """D-016 in its sharpest form: "żadnej kolumny roli — role wróciłyby jako RBAC zakazany w E1".

    L4253 and L4361 forbid RBAC in E1. A column named `role` would be the place it grows back,
    so its absence is asserted rather than remembered.
    """
    async with pool.acquire() as connection:
        columns = {
            row["column_name"]
            for row in await connection.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'operators'
                """
            )
        }
    assert columns, "tabeli operators nie ma w bazie (D-016)"
    assert "credential_hash" in columns, columns
    assert "disabled_at" in columns, columns
    forbidden = {"role", "roles", "permissions", "scopes"}
    assert not (columns & forbidden), (
        f"operators ma kolumnę roli: {columns & forbidden}. RBAC jest zakazany w E1 (L4253, L4361)"
    )


async def test_operator_credential_must_be_an_argon2id_hash(pool, tenant) -> None:
    """D-016 names Argon2id. A CHECK on the encoded prefix makes a weaker hash unstorable.

    Without it, a stub writing a plaintext password or a bcrypt digest would work, and the
    decision would live only in a document.
    """
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2)",
                tenant["organization"],
                "haslo-jawnym-tekstem",
            )
