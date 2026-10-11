"""Migration 0013 — the schema half of P1: projects, person profiles and the two attachments.

Everything here is a rule the DATABASE has to hold, not a rule the control plane can be trusted
to apply. Three of them decide whether the P1 completion criteria are true of the data or only
of the screen:

  * **one active profile per social account** — otherwise "dwa profile nie mieszają tożsamości"
    is a statement about the user interface, and two concurrent attachments make it false;
  * **one active assignment per (project, account)** and NOT per account — a project may use
    several accounts and one account may serve several projects, so a uniqueness on the account
    alone would silently take a connection away from the previous project;
  * **detaching keeps the row** — P1 says "odłączenie nie usuwa historii" outright.

The uniqueness rules are asserted by trying to break them, not by reading `pg_index`: an index
that exists and does not fire is the same thing as no index at all. Both are checked — the name,
so a rename is visible, and the behaviour, so the name is not the whole evidence.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    CODEX_CONNECTION_FORBIDDEN_COLUMNS,
    CODEX_CONNECTION_STATUSES,
    CODEX_CONNECTIONS_COLUMNS,
    WORKSPACE_ACTIVE_INDEXES,
    WORKSPACE_TABLE_COLUMNS,
)

pytestmark = pytest.mark.db


async def _columns(connection: asyncpg.Connection, table: str) -> dict[str, str]:
    rows = await connection.fetch(
        """
        SELECT column_name, is_nullable FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {row["column_name"]: row["is_nullable"] for row in rows}


@pytest_asyncio.fixture
async def world(pool):
    """One organization, two workspaces of it, and two social accounts.

    Two workspaces because half of what P1 promises is that they do not see each other, and a
    fixture with one workspace cannot fail that way. Two accounts because the interesting
    uniqueness questions all need a second row to collide with.
    """
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-0013') RETURNING id"
        )
        first_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-a') RETURNING id",
            organization_id,
        )
        second_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-b') RETURNING id",
            organization_id,
        )
        accounts = [
            await connection.fetchval(
                """
                INSERT INTO social_accounts (organization_id, platform, external_account_id)
                VALUES ($1, 'REDDIT', $2) RETURNING id
                """,
                organization_id,
                f"konto-{uuid.uuid4()}",
            )
            for _ in range(2)
        ]
    return {
        "organization": organization_id,
        "workspace": first_workspace,
        "other_workspace": second_workspace,
        "accounts": accounts,
    }


async def _project(connection: asyncpg.Connection, world, *, workspace=None, name="Projekt"):
    return await connection.fetchval(
        "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, $3)"
        " RETURNING id",
        world["organization"],
        workspace or world["workspace"],
        name,
    )


async def _profile(connection: asyncpg.Connection, world, *, workspace=None, name="Anna"):
    return await connection.fetchval(
        "INSERT INTO person_profiles (organization_id, workspace_id, name) VALUES ($1, $2, $3)"
        " RETURNING id",
        world["organization"],
        workspace or world["workspace"],
        name,
    )


async def _attach(connection: asyncpg.Connection, world, profile_id, account_id, *, workspace=None):
    return await connection.fetchval(
        """
        INSERT INTO person_profile_accounts
            (organization_id, workspace_id, person_profile_id, social_account_id)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        world["organization"],
        workspace or world["workspace"],
        profile_id,
        account_id,
    )


async def _assign(connection: asyncpg.Connection, world, project_id, account_id, *, workspace=None):
    return await connection.fetchval(
        """
        INSERT INTO project_account_assignments
            (organization_id, workspace_id, project_id, social_account_id)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        world["organization"],
        workspace or world["workspace"],
        project_id,
        account_id,
    )


async def test_the_four_tables_have_exactly_the_declared_columns(pool) -> None:
    """Both directions, so a column added without a declaration fails too.

    The count is asserted before the loop for the reason `expected_schema` opens with: a loop
    over an empty mapping passes and proves nothing.
    """
    assert len(WORKSPACE_TABLE_COLUMNS) == 4, sorted(WORKSPACE_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, expected in WORKSPACE_TABLE_COLUMNS.items():
            present = await _columns(connection, table)
            assert present, f"nie ma tabeli {table} — migracja 0013 nie została zastosowana"
            assert sorted(present) == sorted(expected), (
                f"{table} ma kolumny {sorted(present)}, zadeklarowano {sorted(expected)}"
            )
            assert present["organization_id"] == "NO", f"{table}.organization_id musi być NOT NULL"
            assert present["workspace_id"] == "NO", f"{table}.workspace_id musi być NOT NULL"


async def test_the_two_active_indexes_exist_under_their_declared_names(pool) -> None:
    """By NAME, and partial. A rename here is a rule that stopped being enforced."""
    assert len(WORKSPACE_ACTIVE_INDEXES) == 3, WORKSPACE_ACTIVE_INDEXES
    async with pool.acquire() as connection:
        for index in WORKSPACE_ACTIVE_INDEXES:
            row = await connection.fetchrow(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = $1 AND indexname = $2
                """,
                index.table,
                index.name,
            )
            assert row is not None, (
                f"nie ma indeksu {index.name} na {index.table}. Reguła aktywności trzymana "
                "kodem zamiast indeksem przepuszcza dwa równoległe zapisy"
            )
            definition = row["indexdef"]
            assert "UNIQUE" in definition, definition
            assert f"{index.predicate_column} IS NULL" in definition, definition
            for column in index.columns:
                assert column in definition, definition


async def test_one_account_cannot_have_two_active_profiles(pool, world) -> None:
    """The rule that makes "two profiles do not mix identities" true of the data.

    The second attachment is refused even though it points at a profile in ANOTHER workspace:
    an account is one identity on one platform, and it cannot be Anna here and Jan there.
    """
    async with pool.acquire() as connection:
        first = await _profile(connection, world, name="Anna")
        second = await _profile(
            connection, world, workspace=world["other_workspace"], name="Jan"
        )
        await _attach(connection, world, first, world["accounts"][0])
        with pytest.raises(asyncpg.UniqueViolationError):
            await _attach(
                connection,
                world,
                second,
                world["accounts"][0],
                workspace=world["other_workspace"],
            )


async def test_detaching_keeps_the_row_and_frees_the_account(pool, world) -> None:
    """P1: "odłączenie nie usuwa historii". Both halves are asserted, and the order matters.

    A detach implemented as DELETE would pass the second half (the account is free again) and
    fail the first, which is exactly the shortcut this test exists to refuse.
    """
    async with pool.acquire() as connection:
        anna = await _profile(connection, world, name="Anna")
        jan = await _profile(connection, world, name="Jan")
        attachment = await _attach(connection, world, anna, world["accounts"][0])

        await connection.execute(
            "UPDATE person_profile_accounts SET detached_at = now() WHERE id = $1", attachment
        )
        # The history row is still there, still pointing at the profile it used to belong to.
        history = await connection.fetchrow(
            "SELECT person_profile_id, detached_at FROM person_profile_accounts WHERE id = $1",
            attachment,
        )
        assert history is not None, "odłączenie skasowało wiersz — historia miała zostać"
        assert history["person_profile_id"] == anna
        assert history["detached_at"] is not None

        # And the account can now belong to somebody else.
        await _attach(connection, world, jan, world["accounts"][0])
        active = await connection.fetchval(
            "SELECT count(*) FROM person_profile_accounts WHERE social_account_id = $1"
            " AND detached_at IS NULL",
            world["accounts"][0],
        )
        assert active == 1, f"aktywnych przypięć jest {active}, ma być dokładnie jedno"


async def test_a_project_takes_more_than_one_account_and_an_account_serves_more_than_one_project(
    pool, world
) -> None:
    """The completion criterion says "projekt obsługuje więcej niż jedno przypisanie".

    The reverse direction is here too, because the tempting index — UNIQUE on `social_account_id`
    alone — would satisfy the first sentence and silently make the second impossible.
    """
    async with pool.acquire() as connection:
        first_project = await _project(connection, world, name="Obecność devops")
        second_project = await _project(connection, world, name="Research")
        await _assign(connection, world, first_project, world["accounts"][0])
        await _assign(connection, world, first_project, world["accounts"][1])
        await _assign(connection, world, second_project, world["accounts"][0])

        in_first = await connection.fetchval(
            "SELECT count(*) FROM project_account_assignments WHERE project_id = $1"
            " AND unassigned_at IS NULL",
            first_project,
        )
        assert in_first == 2, in_first

        # The same pair twice is the collision the index is for.
        with pytest.raises(asyncpg.UniqueViolationError):
            await _assign(connection, world, first_project, world["accounts"][0])


async def test_an_attachment_cannot_reach_a_profile_of_another_workspace(pool, world) -> None:
    """The triple doing the job a pair cannot, inside ONE organization.

    `(organization_id, social_account_id)` alone would be satisfied here, which is why the key
    to the profile carries `workspace_id` as well.
    """
    async with pool.acquire() as connection:
        foreign_profile = await _profile(connection, world, workspace=world["other_workspace"])
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            # Claiming the profile of workspace B while writing the row into workspace A.
            await _attach(connection, world, foreign_profile, world["accounts"][0])


async def test_an_assignment_cannot_reach_a_project_of_another_workspace(pool, world) -> None:
    async with pool.acquire() as connection:
        foreign_project = await _project(connection, world, workspace=world["other_workspace"])
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _assign(connection, world, foreign_project, world["accounts"][0])


async def test_a_project_cannot_be_written_into_a_workspace_of_another_organization(
    pool, world
) -> None:
    async with pool.acquire() as connection:
        other_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-0013') RETURNING id"
        )
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _project(connection, world, workspace=other_workspace)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("projects", "mode", "AUTOPILOT"),
        ("person_profiles", "kind", "ROBOT"),
    ],
)
async def test_the_closed_lists_refuse_an_unknown_value(pool, world, table, column, value) -> None:
    """A typo has to be a refusal, not a new category nobody declared.

    Both lists are `text` plus a NAMED CHECK rather than a native enum, because
    `ALTER TYPE ... ADD VALUE` does not run inside a transaction and cannot be undone
    (db/migrations/README.md).
    """
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                f"INSERT INTO {table} (organization_id, workspace_id, name, {column})"
                " VALUES ($1, $2, 'x', $3)",
                world["organization"],
                world["workspace"],
                value,
            )


#: Whitespace a name may consist of and still be no name. The tab is the one that got through.
BLANK_NAMES: tuple[str, ...] = ("   ", "\t", "\n", "\r", " \t\n ")


@pytest.mark.parametrize("blank", BLANK_NAMES)
async def test_a_project_and_a_profile_refuse_a_blank_name(pool, world, blank) -> None:
    """A row named `"\t"` renders as an unnamed line and cannot be told apart from the next one.

    THE TAB IS WHY THIS IS PARAMETRIZED. `btrim(x)` with NO SECOND ARGUMENT trims SPACES ONLY, so
    the first version of this CHECK accepted `E'\t'` and stored it — measured by the P2 session,
    not reasoned about. The character list in the CHECK is now explicit, and so is this table;
    a CHECK narrowed back to `btrim(name)` fails on four of these five cases.
    """
    async with pool.acquire() as connection:
        for table in ("projects", "person_profiles"):
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    f"INSERT INTO {table} (organization_id, workspace_id, name) VALUES ($1, $2, $3)",
                    world["organization"],
                    world["workspace"],
                    blank,
                )


@pytest.mark.parametrize("blank", BLANK_NAMES)
async def test_a_codex_connection_refuses_a_blank_label(pool, world, blank) -> None:
    """The same hole, in 0014. Same reproduction, same explicit character list."""
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await _connection(connection, world, label=blank, home_ref=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Migration 0014 — Codex connections
# ---------------------------------------------------------------------------


async def _connection(connection: asyncpg.Connection, world, **overrides):
    values = {
        "organization_id": world["organization"],
        "workspace_id": None,
        "label": "Konto założyciela",
        "home_ref": ".",
        **overrides,
    }
    return await connection.fetchval(
        """
        INSERT INTO codex_connections (organization_id, workspace_id, label, home_ref, status)
        VALUES ($1, $2, $3, $4, COALESCE($5, 'PENDING_LOGIN'))
        RETURNING id
        """,
        values["organization_id"],
        values["workspace_id"],
        values["label"],
        values["home_ref"],
        values.get("status"),
    )


async def test_codex_connections_have_exactly_the_declared_columns(pool) -> None:
    assert len(CODEX_CONNECTIONS_COLUMNS) == 16, CODEX_CONNECTIONS_COLUMNS
    async with pool.acquire() as connection:
        present = await _columns(connection, "codex_connections")
        assert present, "nie ma tabeli codex_connections — migracja 0014 nie została zastosowana"
        assert sorted(present) == sorted(CODEX_CONNECTIONS_COLUMNS), sorted(present)
        # The one column that is deliberately nullable, and the reason it is.
        assert present["workspace_id"] == "YES", (
            "codex_connections.workspace_id jest NOT NULL — połączenie ma móc służyć każdemu "
            "workspace'owi organizacji (WIZJA-WORKSPACE: limity konta nie są przydziałem "
            "per workspace)"
        )
        assert present["organization_id"] == "NO"


async def test_a_connection_holds_no_credential_column(pool) -> None:
    """The table points at the directory where Codex keeps `auth.json`; it does not hold the token.

    Checked by name against a closed list rather than by reading the migration, because the
    failure this guards against is a column ADDED later by somebody solving a different problem.
    """
    assert len(CODEX_CONNECTION_FORBIDDEN_COLUMNS) == 9, CODEX_CONNECTION_FORBIDDEN_COLUMNS
    async with pool.acquire() as connection:
        present = await _columns(connection, "codex_connections")
    forbidden = sorted(set(present) & set(CODEX_CONNECTION_FORBIDDEN_COLUMNS))
    assert not forbidden, (
        f"codex_connections ma kolumny z poświadczeniem: {forbidden}. Token w kolumnie to token "
        "w każdej kopii zapasowej i w każdym zrzucie diagnostycznym"
    )


async def test_a_connection_is_available_to_the_whole_organization_or_to_one_workspace(
    pool, world
) -> None:
    """Both shapes have to be writable, and the narrowed one may not reach another organization."""
    async with pool.acquire() as conn:
        organization_wide = await _connection(conn, world, home_ref=".")
        narrowed = await _connection(
            conn,
            world,
            workspace_id=world["workspace"],
            home_ref=str(uuid.uuid4()),
            label="Konto robocze",
        )
        assert organization_wide is not None and narrowed is not None

        other_organization = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-0014') RETURNING id"
        )
        foreign_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _connection(
                conn, world, workspace_id=foreign_workspace, home_ref=str(uuid.uuid4())
            )


async def test_two_live_connections_cannot_share_a_directory(pool, world) -> None:
    """L2159: two App Servers on one CODEX_HOME are forbidden. The index refuses the pair.

    And a DISCONNECTED row frees the directory again, which is what makes "odłączenie zostawia
    historię" compatible with re-accepting the same home later.
    """
    async with pool.acquire() as conn:
        home = str(uuid.uuid4())
        first = await _connection(conn, world, home_ref=home)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _connection(conn, world, home_ref=home, label="Duplikat")

        await conn.execute(
            "UPDATE codex_connections SET status = 'DISCONNECTED', disconnected_at = now()"
            " WHERE id = $1",
            first,
        )
        again = await _connection(conn, world, home_ref=home, label="Po odłączeniu")
        assert again is not None
        history = await conn.fetchval(
            "SELECT count(*) FROM codex_connections WHERE home_ref = $1", home
        )
        assert history == 2, f"po ponownym przyjęciu katalogu jest {history} wierszy, ma być 2"


@pytest.mark.parametrize(
    "home_ref",
    ["..", "../inny", "/etc", "a/b", "", "ZZZZZZZZ-0000-4000-8000-000000000000"],
)
async def test_home_ref_accepts_only_the_root_or_a_uuid(pool, world, home_ref) -> None:
    """The CHECK is the boundary against leaving the volume.

    A row carrying `../..` would tell the runtime to open a directory outside the mount, and the
    code resolving it would look perfectly reasonable while doing it.
    """
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await _connection(conn, world, home_ref=home_ref)


async def test_an_unknown_connection_status_is_refused(pool, world) -> None:
    assert len(CODEX_CONNECTION_STATUSES) == 5, sorted(CODEX_CONNECTION_STATUSES)
    async with pool.acquire() as conn:
        for status in sorted(CODEX_CONNECTION_STATUSES):
            assert await _connection(conn, world, home_ref=str(uuid.uuid4()), status=status)
        with pytest.raises(asyncpg.CheckViolationError):
            await _connection(conn, world, home_ref=str(uuid.uuid4()), status="LOGGED_IN")
