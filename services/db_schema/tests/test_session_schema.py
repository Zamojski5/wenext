"""Migration 0015: a session with a goal, and an append-only trace of what happened in it (P2).

WHAT THE TWO TABLES ARE FOR. Until P2 a model turn was a single call with input the application
assembled itself and a fresh `ephemeral` thread each time (`observe/turns.py`). That shape cannot
carry work that lasts, comes back and gets corrected, so P2 makes the unit of work a SESSION:
one goal, one engine thread resumed across turns, and a numbered trace of everything that
happened. This file measures the four properties of that schema which, if lost, would each let
the product lie about the work:

  1. **A terminal status has a finishing time, and a non-terminal one does not.** Otherwise
     "DONE" is a word in a column rather than a fact with a date, and nothing can say how long
     the work took or whether it ever ended.
  2. **The trace is append-only.** A correction is a new event. A trace that can be rewritten
     shows, after a restart, a conversation that did not happen, which is worse than showing
     nothing (the same argument 0004 makes for measurements and judgments).
  3. **`seq` is unique per session.** It is what a client resumes a read from ("everything after
     42"); two events sharing a number silently drop one from every later read.
  4. **The closed lists are enforced by the database.** Eight statuses and seven event kinds,
     counted before they are compared, because `all([])` is `True` and that has produced two
     false passes in this repository already.

The lookup of a named CHECK is `(conname, conrelid)`, never `conname` alone: `conname` is unique
per RELATION, and a constraint of the right name moved onto a decoy table once satisfied a whole
parity suite while the guarded column had no CHECK at all (`test_enum_parity.py`).
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    AGENT_SESSION_STATUSES,
    SESSION_APPEND_ONLY_TABLES,
    SESSION_EVENT_KINDS,
    SESSION_TABLE_COLUMNS,
)

pytestmark = [pytest.mark.db, pytest.mark.p0]

_LITERAL = re.compile(r"'([^']*)'::text")

TERMINAL = frozenset({"DONE", "FAILED", "BUDGET_EXHAUSTED"})


@pytest_asyncio.fixture
async def tenant_session(pool):
    """One organization, workspace, project and session, seeded through the real tables.

    Local to this module rather than in `conftest.py`, which is shared with the suites of 0013
    and 0014: a fixture added there would change what every one of their tests starts from.
    """
    async with pool.acquire() as connection:
        organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"fixture-0015-{uuid4()}"
        )
        workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization,
        )
        project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'proj') "
            "RETURNING id",
            organization,
            workspace,
        )
        session_id = await connection.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'sprawdź, dlaczego odzew jest mały') RETURNING id",
            organization,
            workspace,
            project,
        )
    return organization, workspace, session_id


async def _columns(connection, table: str) -> set[str]:
    rows = await connection.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return {row["column_name"] for row in rows}


async def _definition(connection, constraint: str, table: str) -> str:
    definition = await connection.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = $1 AND contype = 'c' AND conrelid = ('public.' || $2)::regclass",
        constraint,
        table,
    )
    if not definition:
        elsewhere = await connection.fetch(
            "SELECT conrelid::regclass::text AS present_on FROM pg_constraint WHERE conname = $1",
            constraint,
        )
        raise AssertionError(
            f"nie ma nazwanego CHECK-a {constraint} NA TABELI {table}; ta nazwa występuje na: "
            f"{[r['present_on'] for r in elsewhere] or 'nigdzie'}"
        )
    return str(definition)


async def _refused(connection, sql: str, *args) -> str:
    """Run a statement that must be refused, inside a savepoint, and return the error text.

    The savepoint is not decoration: the first violation aborts the transaction, and every later
    statement then fails with `current transaction is aborted`, whose text contains none of the
    words a loop of expected failures asserts on.
    """
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except Exception as exc:
        return str(exc).lower()
    raise AssertionError(f"instrukcja przeszła, choć miała zostać odrzucona: {sql} {args}")


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


async def test_both_tables_have_exactly_the_declared_columns(pool) -> None:
    assert len(SESSION_TABLE_COLUMNS) == 2, sorted(SESSION_TABLE_COLUMNS)
    async with pool.acquire() as connection:
        for table, declared in SESSION_TABLE_COLUMNS.items():
            assert len(declared) >= 8, f"{table}: lista kolumn podejrzanie krótka"
            actual = await _columns(connection, table)
            assert actual, f"tabela {table} nie istnieje albo nie ma kolumn"
            assert actual == set(declared), (
                f"{table}: tylko w bazie {sorted(actual - set(declared))}, "
                f"tylko w deklaracji {sorted(set(declared) - actual)}"
            )


@pytest.mark.parametrize(
    ("constraint", "table", "values"),
    [
        ("agent_sessions_status_is_known", "agent_sessions", AGENT_SESSION_STATUSES),
        ("session_events_kind_is_known", "session_events", SESSION_EVENT_KINDS),
    ],
    ids=["statusy-sesji", "rodzaje-zdarzen"],
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


async def test_a_session_belongs_to_a_project_through_all_three_columns(pool) -> None:
    """A pair would let a session of workspace A hang under a project of workspace B in the same
    organization, and the screen would fill with somebody else's work."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT (SELECT array_agg(a.attname ORDER BY k.ordinality)
                      FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
                     AS columns,
                   c.confrelid::regclass::text AS parent
            FROM pg_constraint c
            WHERE c.contype = 'f' AND c.conrelid = 'public.agent_sessions'::regclass
            """
        )
    to_projects = [
        r
        for r in rows
        if r["parent"] == "projects"
        and list(r["columns"]) == ["organization_id", "workspace_id", "project_id"]
    ]
    assert to_projects, [dict(r) for r in rows]
    # The connection is an ORGANIZATION resource (0014), so that key is a pair, and saying so
    # here stops a later "fix" from making it a triple and breaking org-wide connections.
    to_connections = [
        r
        for r in rows
        if r["parent"] == "codex_connections"
        and list(r["columns"]) == ["organization_id", "codex_connection_id"]
    ]
    assert to_connections, [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# The four properties, executed rather than read
# ---------------------------------------------------------------------------


async def test_a_terminal_status_needs_a_finishing_time_and_a_live_one_must_not_have_it(
    pool,
) -> None:
    """Executed against a scratch table carrying the real constraint definition, so what is
    exercised is the expression the migration installed and not a paraphrase of it."""
    async with pool.acquire() as connection, connection.transaction():
        definition = await _definition(
            connection, "agent_sessions_finished_when_terminal", "agent_sessions"
        )
        await connection.execute(
            f"CREATE TEMP TABLE finish_probe (status text, finished_at timestamptz, {definition})"
        )
        for status in sorted(AGENT_SESSION_STATUSES - TERMINAL):
            await connection.execute("INSERT INTO finish_probe VALUES ($1, NULL)", status)
            message = await _refused(
                connection, "INSERT INTO finish_probe VALUES ($1, now())", status
            )
            assert "check" in message, (
                f"{status} nie jest stanem końcowym, a przyjął moment zakończenia: {message}"
            )
        for status in sorted(TERMINAL):
            await connection.execute("INSERT INTO finish_probe VALUES ($1, now())", status)
            message = await _refused(
                connection, "INSERT INTO finish_probe VALUES ($1, NULL)", status
            )
            assert "check" in message, (
                f"{status} przeszedł bez momentu zakończenia. „Zakończone” bez daty to słowo "
                f"w kolumnie, nie fakt: {message}"
            )


async def test_the_trace_is_append_only_by_trigger(pool, tenant_session) -> None:
    """An UPDATE and a DELETE both raise. Measured with a real row, not by looking for a trigger:
    a trigger that exists and does nothing looks identical in the catalog."""
    assert SESSION_APPEND_ONLY_TABLES == ("session_events",)
    organization, workspace, session_id = tenant_session
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind, "
            "payload) VALUES ($1, $2, $3, 1, 'USER_MESSAGE', '{\"text\": \"cel\"}'::jsonb)",
            organization,
            workspace,
            session_id,
        )
        for sql in (
            "UPDATE session_events SET kind = 'ERROR' WHERE session_id = $1",
            "DELETE FROM session_events WHERE session_id = $1",
        ):
            message = await _refused(connection, sql, session_id)
            assert "append-only" in message, (
                f"ślad sesji dał się zmienić: {sql}. Po restarcie ekran pokazywałby przebieg, "
                f"którego nie było. Błąd: {message}"
            )


async def test_two_events_cannot_share_a_sequence_number(pool, tenant_session) -> None:
    """`seq` is what a client resumes a read from. A duplicate silently drops one event from
    every later read, and the drop looks like the agent never did that step."""
    organization, workspace, session_id = tenant_session
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind) "
            "VALUES ($1, $2, $3, 1, 'STATUS')",
            organization,
            workspace,
            session_id,
        )
        message = await _refused(
            connection,
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind) "
            "VALUES ($1, $2, $3, 1, 'ASSISTANT_MESSAGE')",
            organization,
            workspace,
            session_id,
        )
        assert "unique" in message or "duplicate" in message, message
        # The same number in ANOTHER session is fine: the sequence is per session, not global.
        other = await connection.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "SELECT organization_id, workspace_id, project_id, 'drugi cel' FROM agent_sessions "
            "WHERE id = $1 RETURNING id",
            session_id,
        )
        await connection.execute(
            "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, kind) "
            "VALUES ($1, $2, $3, 1, 'STATUS')",
            organization,
            workspace,
            other,
        )


async def test_a_session_without_a_goal_is_refused(pool, tenant_session) -> None:
    """A session with no goal has nothing to execute and its state could not be judged. Blank
    space is not a goal either."""
    organization, workspace, session_id = tenant_session
    async with pool.acquire() as connection:
        project_id = await connection.fetchval(
            "SELECT project_id FROM agent_sessions WHERE id = $1", session_id
        )
        for goal in ("", "   ", "\n\t "):
            message = await _refused(
                connection,
                "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
                "VALUES ($1, $2, $3, $4)",
                organization,
                workspace,
                project_id,
                goal,
            )
            assert "check" in message, (goal, message)
