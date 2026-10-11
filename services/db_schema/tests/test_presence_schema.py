"""Migration 0017: a delegation, and a record of why the model ever woke up (P4).

THE FIVE PROPERTIES THIS FILE MEASURES, each one a way the product could act without being able
to account for it afterwards:

  1. **One live delegation per project.** Two would be two different scopes a publication could
     claim to be covered by, and the audit trail would not say which. Enforced by a partial
     unique index, not by code, for the reason 0013 gives about active rows: of the three
     mechanisms only an index survives two concurrent signatures.
  2. **A wakeup either ran the model or says why it did not, never both.** A row with a session
     AND a reason for skipping claims the model both ran and did not.
  3. **One event wakes the agent once.** `event_key` is unique per project, as a CONSTRAINT.
     Two collectors asking "is this key already here" both get "no" and both insert, and the
     result is a second model session about the same post and a second comment under it.
  4. **The closed lists are the database's.** Three modes, four wakeup reasons, four event
     kinds, counted before they are compared.
  5. **`agent_sessions.kind` defaults to CONVERSATION.** Every session written by P2 keeps its
     meaning; PRESENCE is a new kind, not a rename of the old one.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    AGENT_SESSION_KINDS,
    DELEGATION_MODES,
    PRESENCE_TABLE_COLUMNS,
    WAKEUP_EVENT_KINDS,
    WAKEUP_REASONS,
)

pytestmark = [pytest.mark.db, pytest.mark.p4]

_LITERAL = re.compile(r"'([^']*)'::text")

SCOPE_DIGEST = "a" * 64


@pytest_asyncio.fixture
async def tenant_project(pool):
    """An organization, a workspace, an operator and a project, through the real tables."""
    async with pool.acquire() as connection:
        organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"fixture-0017-{uuid4()}"
        )
        workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization,
        )
        operator = await connection.fetchval(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2) RETURNING id",
            organization,
            "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g",
        )
        project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'proj') "
            "RETURNING id",
            organization,
            workspace,
        )
    return {
        "organization": organization,
        "workspace": workspace,
        "operator": operator,
        "project": project,
    }


async def _definition(connection, constraint: str, table: str) -> str:
    definition = await connection.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = $1 AND contype = 'c' AND conrelid = ('public.' || $2)::regclass",
        constraint,
        table,
    )
    if not definition:
        raise AssertionError(f"nie ma nazwanego CHECK-a {constraint} NA TABELI {table}")
    return str(definition)


async def _refused(connection, sql: str, *args) -> str:
    """Run a statement that must be refused, inside a savepoint, and return the error text."""
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except Exception as exc:
        return str(exc).lower()
    raise AssertionError(f"instrukcja przeszła, choć miała zostać odrzucona: {sql} {args}")


async def _delegate(connection, tenant_project, *, mode: str = "AUTONOMOUS"):
    return await connection.fetchval(
        """
        INSERT INTO project_delegations
            (organization_id, workspace_id, project_id, mode, scope, budget, scope_sha256,
             signed_by)
        VALUES ($1, $2, $3, $4, '{"accounts": [], "communities": [], "actions": ["REPLY"]}'::jsonb,
                '{"publications_per_day": 6}'::jsonb, $5, $6)
        RETURNING id
        """,
        tenant_project["organization"],
        tenant_project["workspace"],
        tenant_project["project"],
        mode,
        SCOPE_DIGEST,
        tenant_project["operator"],
    )


async def _wakeup(connection, tenant_project, *, reason: str = "NEW_EVENT"):
    return await connection.fetchval(
        "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        tenant_project["organization"],
        tenant_project["workspace"],
        tenant_project["project"],
        reason,
    )


# ---------------------------------------------------------------------------
# Shape and closed lists
# ---------------------------------------------------------------------------


async def test_the_three_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(PRESENCE_TABLE_COLUMNS) == 3, sorted(PRESENCE_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, declared in PRESENCE_TABLE_COLUMNS.items():
            assert len(declared) >= 9, f"{table}: lista kolumn podejrzanie krótka"
            rows = await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1",
                table,
            )
            actual = {row["column_name"] for row in rows}
            assert actual, f"tabela {table} nie istnieje"
            assert actual == set(declared), (
                f"{table}: tylko w bazie {sorted(actual - set(declared))}, "
                f"tylko w deklaracji {sorted(set(declared) - actual)}"
            )


@pytest.mark.parametrize(
    ("constraint", "table", "values"),
    [
        ("project_delegations_mode_is_known", "project_delegations", DELEGATION_MODES),
        ("wakeups_reason_is_known", "wakeups", WAKEUP_REASONS),
        ("wakeup_events_kind_is_known", "wakeup_events", WAKEUP_EVENT_KINDS),
        ("agent_sessions_kind_is_known", "agent_sessions", AGENT_SESSION_KINDS),
    ],
    ids=["tryby", "powody-obudzenia", "rodzaje-zdarzen", "rodzaje-sesji"],
)
async def test_the_closed_lists_are_the_declared_ones(
    pool, constraint: str, table: str, values: frozenset[str]
) -> None:
    assert values, f"{constraint}: pusta lista nie zamyka niczego"
    async with pool.acquire() as connection:
        definition = await _definition(connection, constraint, table)
    actual = set(_LITERAL.findall(definition))
    assert actual == set(values), (
        f"CHECK {constraint}: tylko w bazie {sorted(actual - set(values))}, "
        f"tylko w deklaracji {sorted(set(values) - actual)}"
    )


async def test_a_session_written_before_this_migration_is_a_conversation(
    pool, tenant_project
) -> None:
    """The default is what keeps every P2 session meaning what it meant."""
    async with pool.acquire() as connection:
        default = await connection.fetchval(
            "SELECT column_default FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'agent_sessions' AND column_name = 'kind'"
        )
        assert default == "'CONVERSATION'::text"
        session_id = await connection.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
        )
        kind = await connection.fetchval(
            "SELECT kind FROM agent_sessions WHERE id = $1", session_id
        )
    assert kind == "CONVERSATION"


# ---------------------------------------------------------------------------
# The delegation
# ---------------------------------------------------------------------------


async def test_a_project_can_have_only_one_live_delegation(pool, tenant_project) -> None:
    """Two live ones would be two scopes a publication could claim to be covered by, and the
    audit trail would not say which. The index refuses the second one."""
    async with pool.acquire() as connection:
        first = await _delegate(connection, tenant_project)
        message = await _refused(
            connection,
            """
            INSERT INTO project_delegations
                (organization_id, workspace_id, project_id, mode, scope, scope_sha256, signed_by)
            VALUES ($1, $2, $3, 'REVIEW', '{}'::jsonb, $4, $5)
            """,
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            "b" * 64,
            tenant_project["operator"],
        )
        assert "unique" in message or "duplicate" in message, message

        # Revoked, so the slot is free and a NEW signature may take it. The old row stays.
        await connection.execute(
            "UPDATE project_delegations SET revoked_at = now() WHERE id = $1", first
        )
        second = await _delegate(connection, tenant_project, mode="REVIEW")
        live = await connection.fetch(
            "SELECT id, revoked_at FROM project_delegations WHERE project_id = $1 ORDER BY signed_at",
            tenant_project["project"],
        )
    assert [row["id"] for row in live] == [first, second], "odwołana delegacja zniknęła z historii"
    assert live[0]["revoked_at"] is not None and live[1]["revoked_at"] is None


async def test_a_paused_delegation_still_occupies_the_slot(pool, tenant_project) -> None:
    """Pausing is "for now" and revoking is "the end". A pause that freed the slot would let a
    second signature appear while the first is still the one a person means to come back to."""
    async with pool.acquire() as connection:
        await _delegate(connection, tenant_project)
        await connection.execute(
            "UPDATE project_delegations SET paused_at = now() WHERE project_id = $1",
            tenant_project["project"],
        )
        message = await _refused(
            connection,
            """
            INSERT INTO project_delegations
                (organization_id, workspace_id, project_id, mode, scope, scope_sha256, signed_by)
            VALUES ($1, $2, $3, 'AUTONOMOUS', '{}'::jsonb, $4, $5)
            """,
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            "c" * 64,
            tenant_project["operator"],
        )
        assert "unique" in message or "duplicate" in message, message


async def test_a_delegation_without_a_scope_digest_is_refused(pool, tenant_project) -> None:
    """The digest is what makes "delegation X allowed it" say WHAT it allowed, months later."""
    async with pool.acquire() as connection:
        for digest in ("", "nie-hex", "a" * 63):
            message = await _refused(
                connection,
                """
                INSERT INTO project_delegations
                    (organization_id, workspace_id, project_id, mode, scope, scope_sha256,
                     signed_by)
                VALUES ($1, $2, $3, 'AUTONOMOUS', '{}'::jsonb, $4, $5)
                """,
                tenant_project["organization"],
                tenant_project["workspace"],
                tenant_project["project"],
                digest,
                tenant_project["operator"],
            )
            assert "check" in message, (digest, message)


# ---------------------------------------------------------------------------
# Wakeups
# ---------------------------------------------------------------------------


async def test_a_wakeup_either_ran_the_model_or_says_why_it_did_not(pool, tenant_project) -> None:
    async with pool.acquire() as connection:
        session_id = await connection.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'cel', 'PRESENCE') RETURNING id",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
        )
        # Either alone is fine, and so is neither (a tick still running).
        await _wakeup(connection, tenant_project)
        await connection.execute(
            "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason, session_id) "
            "VALUES ($1, $2, $3, 'NEW_EVENT', $4)",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            session_id,
        )
        await connection.execute(
            "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason, "
            "skipped_reason) VALUES ($1, $2, $3, 'DUE', 'NO_EVENTS')",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
        )
        # Both together is the row that claims the model ran and did not.
        message = await _refused(
            connection,
            "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason, session_id, "
            "skipped_reason) VALUES ($1, $2, $3, 'NEW_EVENT', $4, 'NO_EVENTS')",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            session_id,
        )
        assert "check" in message, message


async def test_one_event_wakes_the_agent_once_per_project(pool, tenant_project) -> None:
    """THE constraint of this migration. Without it two collectors both insert, the agent wakes
    twice about one post, and the second wake-up can put a second comment under it."""
    async with pool.acquire() as connection:
        first = await _wakeup(connection, tenant_project)
        await connection.execute(
            "INSERT INTO wakeup_events (organization_id, workspace_id, project_id, wakeup_id, "
            "kind, event_key) VALUES ($1, $2, $3, $4, 'NEW_THREAD', 'NEW_THREAD:t3_abc123')",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            first,
        )
        second = await _wakeup(connection, tenant_project)
        message = await _refused(
            connection,
            "INSERT INTO wakeup_events (organization_id, workspace_id, project_id, wakeup_id, "
            "kind, event_key) VALUES ($1, $2, $3, $4, 'NEW_THREAD', 'NEW_THREAD:t3_abc123')",
            tenant_project["organization"],
            tenant_project["workspace"],
            tenant_project["project"],
            second,
        )
        assert "unique" in message or "duplicate" in message, message

        # The same key in ANOTHER project is fine: two projects may both care about one thread.
        other_project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'drugi') "
            "RETURNING id",
            tenant_project["organization"],
            tenant_project["workspace"],
        )
        other_wakeup = await connection.fetchval(
            "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason) "
            "VALUES ($1, $2, $3, 'NEW_EVENT') RETURNING id",
            tenant_project["organization"],
            tenant_project["workspace"],
            other_project,
        )
        await connection.execute(
            "INSERT INTO wakeup_events (organization_id, workspace_id, project_id, wakeup_id, "
            "kind, event_key) VALUES ($1, $2, $3, $4, 'NEW_THREAD', 'NEW_THREAD:t3_abc123')",
            tenant_project["organization"],
            tenant_project["workspace"],
            other_project,
            other_wakeup,
        )


async def test_an_event_with_a_blank_key_is_refused(pool, tenant_project) -> None:
    """A blank key deduplicates nothing and would let the same event wake the agent for ever."""
    async with pool.acquire() as connection:
        wakeup = await _wakeup(connection, tenant_project)
        for key in ("", "   "):
            message = await _refused(
                connection,
                "INSERT INTO wakeup_events (organization_id, workspace_id, project_id, "
                "wakeup_id, kind, event_key) VALUES ($1, $2, $3, $4, 'REPLY', $5)",
                tenant_project["organization"],
                tenant_project["workspace"],
                tenant_project["project"],
                wakeup,
                key,
            )
            assert "check" in message, (key, message)


# ---------------------------------------------------------------------------
# Jedna kolejka publikacji dla pilota i dla projektów
# ---------------------------------------------------------------------------
# Te trzy testy nie zasiewają wniosku: postawienie go wymaga biegu albo projektu, rewizji
# propozycji i decyzji zgody, a mierzona własność dotyczy wyłącznie tego, KTÓRE z czterech pól
# są NULL. Definicja CHECK-a jest czytana z `pg_constraint` i WYKONYWANA na tabeli roboczej, więc
# więz, który istnieje, ale przepuszcza wszystko, przepada tutaj tak samo jak brakujący. Zachowanie
# publikatora na obu rodzajach wniosków mierzy `services/control_plane/tests/test_presence_*.py`.


async def _scratch_queue(connection) -> str:
    """Tabela robocza z czterema kolumnami pochodzenia i PRAWDZIWYM więzem z 0017."""
    definition = await _definition(
        connection, "publication_requests_come_from_a_run_or_a_project", "publication_requests"
    )
    name = f"scratch_queue_{uuid4().hex}"
    await connection.execute(
        f"CREATE TEMP TABLE {name} ("
        "run_id uuid, reaction_id uuid, project_id uuid, delegation_id uuid, "
        f"CONSTRAINT {name}_origin {definition})"
    )
    return name


@pytest.mark.parametrize(
    ("label", "values"),
    [
        ("wniosek pilota", ("r", "x", None, None)),
        ("wniosek projektu", (None, None, "p", "d")),
    ],
)
async def test_a_request_may_come_from_a_run_or_from_a_project(pool, label, values) -> None:
    ids = {key: uuid4() for key in "rxpd"}
    async with pool.acquire() as connection, connection.transaction():
        table = await _scratch_queue(connection)
        await connection.execute(
            f"INSERT INTO {table} (run_id, reaction_id, project_id, delegation_id) "
            "VALUES ($1, $2, $3, $4)",
            *[ids[key] if key else None for key in values],
        )


@pytest.mark.parametrize(
    ("label", "values"),
    [
        # Powołuje się i na bieg pilota, i na delegację projektu, czyli na dwie różne zgody.
        ("obie zgody naraz", ("r", "x", "p", "d")),
        # Wniosek bez pochodzenia: nie da się wskazać, na jakiej podstawie ma być opublikowany.
        ("żadnej zgody", (None, None, None, None)),
        # Połówki. Bieg bez reakcji nie wie, pod czym publikuje; projekt bez delegacji nie wie,
        # co zostało podpisane, a sam identyfikator projektu niczego nie zezwala.
        ("bieg bez reakcji", ("r", None, None, None)),
        ("reakcja bez biegu", (None, "x", None, None)),
        ("projekt bez delegacji", (None, None, "p", None)),
        ("delegacja bez projektu", (None, None, None, "d")),
    ],
)
async def test_a_request_with_a_muddled_origin_is_refused(pool, label, values) -> None:
    ids = {key: uuid4() for key in "rxpd"}
    async with pool.acquire() as connection, connection.transaction():
        table = await _scratch_queue(connection)
        message = await _refused(
            connection,
            f"INSERT INTO {table} (run_id, reaction_id, project_id, delegation_id) "
            "VALUES ($1, $2, $3, $4)",
            *[ids[key] if key else None for key in values],
        )
        assert "check" in message, (label, message)


async def test_many_project_requests_fit_under_the_pilot_uniqueness_of_reaction(pool) -> None:
    """`UNIQUE (reaction_id)` z 0007 zostaje i nadal chroni pilota, a projektów nie dotyczy.

    W PostgreSQL wartości NULL są w indeksie unikalnym RÓŻNE, więc dowolnie wiele wniosków
    projektowych (reaction_id IS NULL) przechodzi obok tego samego więzu, który dwa wnioski na tę
    samą reakcję pilota nadal odrzuca. To jedyny powód, dla którego 0007 nie musiało być ruszane,
    więc jest tu zmierzony, a nie założony.
    """
    async with pool.acquire() as connection, connection.transaction():
        present = await connection.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conname = $1 "
            "AND conrelid = 'public.publication_requests'::regclass",
            "publication_requests_reaction_id_key",
        )
        assert present == 1, "zniknął UNIQUE (reaction_id) z 0007"

        name = f"scratch_reaction_{uuid4().hex}"
        await connection.execute(f"CREATE TEMP TABLE {name} (reaction_id uuid UNIQUE)")
        await connection.execute(f"INSERT INTO {name} (reaction_id) VALUES (NULL), (NULL), (NULL)")
        reaction = uuid4()
        await connection.execute(f"INSERT INTO {name} (reaction_id) VALUES ($1)", reaction)
        message = await _refused(
            connection, f"INSERT INTO {name} (reaction_id) VALUES ($1)", reaction
        )
        assert "unique" in message or "duplicate" in message, message


@pytest.mark.parametrize(
    ("constraint", "target"),
    [
        ("publication_requests_point_at_project", "projects"),
        ("publication_requests_point_at_delegation", "project_delegations"),
    ],
)
async def test_the_project_origin_is_a_tenant_triple_foreign_key(pool, constraint, target) -> None:
    """Trójka tenantowa, nie samo `id`: bez organizacji i workspace'u w kluczu wniosek mógłby
    wskazać projekt innego najemcy, a to jest dokładnie ta klasa błędu, którą 0013 zamknęło."""
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1 "
            "AND contype = 'f' AND conrelid = 'public.publication_requests'::regclass",
            constraint,
        )
        assert definition, f"nie ma klucza obcego {constraint} na publication_requests"
        assert "organization_id, workspace_id" in definition, definition
        assert f"REFERENCES {target}(organization_id, workspace_id, id)" in definition, definition
