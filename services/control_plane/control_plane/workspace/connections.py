"""Reads and writes of migration 0014: Codex connections.

A CONNECTION IS ACCESS TO THE ENGINE, NEVER A CREDENTIAL. This module writes a pointer to the
directory where Codex keeps `auth.json` and the facts the app-server reported about the account.
It never writes a token, and `CODEX_CONNECTION_FORBIDDEN_COLUMNS` in `db_schema` is what makes
adding one a failing test.

`workspace_id IS NULL` MEANS "AVAILABLE TO EVERY WORKSPACE OF THIS ORGANIZATION", and a value
narrows it to one. That is the shape WIZJA-WORKSPACE asks for: one connection may serve many
sessions, and the account's limits do not become a per-workspace allowance. The isolation of
CONTEXT is the session's job (P2); this table only says which engine a session may reach.

EVERY WRITE THAT CHANGES STATE ALSO WRITES `last_read_at` OR `last_error`, never neither. A row
that says CONNECTED with no timestamp is a claim nobody can date, and "kiedy to odczytano" is the
first thing ZAKRES-FUNKCJONALNY §6 demands of any number on the screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.workspace.engine import EngineSnapshot, LoginTicket
from control_plane.workspace.store import WorkspaceRefusal

#: The five states of migration 0014, repeated here because the API validates against them.
CONNECTION_STATUSES: tuple[str, ...] = (
    "PENDING_LOGIN",
    "CONNECTED",
    "RATE_LIMITED",
    "DISCONNECTED",
    "ERROR",
)


class HomeAlreadyLive(WorkspaceRefusal):
    """Another live connection already points at this directory (L2159)."""


@dataclass(frozen=True)
class CodexConnectionRow:
    id: UUID
    organization_id: UUID
    workspace_id: UUID | None
    label: str
    home_ref: str
    status: str
    account_email: str | None
    plan_type: str | None
    installation_id: str | None
    models: dict[str, Any]
    rate_limits: dict[str, Any]
    login_ticket: dict[str, Any]
    last_read_at: datetime | None
    last_error: str | None
    created_at: datetime
    disconnected_at: datetime | None


def _json(value: Any) -> dict[str, Any]:
    """asyncpg hands back `jsonb` as text unless a codec is registered. Decode once, here."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row(record: asyncpg.Record) -> CodexConnectionRow:
    return CodexConnectionRow(
        id=record["id"],
        organization_id=record["organization_id"],
        workspace_id=record["workspace_id"],
        label=record["label"],
        home_ref=record["home_ref"],
        status=record["status"],
        account_email=record["account_email"],
        plan_type=record["plan_type"],
        installation_id=record["installation_id"],
        models=_json(record["models"]),
        rate_limits=_json(record["rate_limits"]),
        login_ticket=_json(record["login_ticket"]),
        last_read_at=record["last_read_at"],
        last_error=record["last_error"],
        created_at=record["created_at"],
        disconnected_at=record["disconnected_at"],
    )


_COLUMNS = """
    id, organization_id, workspace_id, label, home_ref, status, account_email, plan_type,
    installation_id, models, rate_limits, login_ticket, last_read_at, last_error, created_at,
    disconnected_at
"""


class CodexConnectionStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_connections(
        self, *, organization_id: UUID, include_disconnected: bool = True
    ) -> tuple[CodexConnectionRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM codex_connections
                WHERE organization_id = $1 AND ($2 OR disconnected_at IS NULL)
                ORDER BY created_at, id
                """,
                organization_id,
                include_disconnected,
            )
        return tuple(_row(row) for row in rows)

    async def get(self, *, organization_id: UUID, connection_id: UUID) -> CodexConnectionRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM codex_connections WHERE organization_id = $1 AND id = $2",
                organization_id,
                connection_id,
            )
        return _row(row) if row is not None else None

    async def live_home_refs(self, *, organization_id: UUID) -> set[str]:
        """Directories already taken by a live connection of this organization.

        Used by the screen to decide whether the volume root is still free to adopt — a question
        that has to be answered from the data, not from whether a button was pressed before.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT home_ref FROM codex_connections"
                " WHERE organization_id = $1 AND disconnected_at IS NULL",
                organization_id,
            )
        return {row["home_ref"] for row in rows}

    async def create(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID | None,
        label: str,
        home_ref: str,
        status: str = "PENDING_LOGIN",
        connection_id: UUID | None = None,
    ) -> CodexConnectionRow:
        """Insert one connection, or raise `HomeAlreadyLive`.

        The refusal comes from `codex_connections_one_live_per_home`, not from a read before the
        write: two requests adopting the root at the same moment would both pass a check and both
        insert, and then two app-servers would share one `CODEX_HOME` — which L2159 forbids and
        which the partial unique index is the only thing that cannot lose.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO codex_connections
                        (id, organization_id, workspace_id, label, home_ref, status)
                    VALUES (COALESCE($1, gen_random_uuid()), $2, $3, $4, $5, $6)
                    RETURNING {_COLUMNS}
                    """,
                    connection_id,
                    organization_id,
                    workspace_id,
                    label,
                    home_ref,
                    status,
                )
        except asyncpg.UniqueViolationError as exc:
            raise HomeAlreadyLive(
                "ten katalog obsługuje już inne żywe połączenie. Dwa app-servery na jednym "
                "CODEX_HOME są zakazane; najpierw odłącz tamto połączenie"
            ) from exc
        return _row(row)

    # -- state written by the login job and by a refresh --------------------------

    async def set_login_ticket(
        self, *, connection_id: UUID, ticket: LoginTicket, deadline: datetime
    ) -> None:
        """Publish the code the human has to type, while the wait is still running.

        `disconnected_at IS NULL` in the WHERE clause, and that is not belt and braces. The job
        publishes its ticket before it first asks whether it was cancelled, so a human who ended
        the connection in the meantime would otherwise get the row moved back to PENDING_LOGIN —
        a disconnected connection showing a live login code. Measured: the cancel test read
        PENDING_LOGIN where it had just written DISCONNECTED.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE codex_connections SET login_ticket = $2::jsonb, status = 'PENDING_LOGIN',"
                " last_error = NULL WHERE id = $1 AND disconnected_at IS NULL",
                connection_id,
                json.dumps(
                    {
                        "login_id": ticket.login_id,
                        "verification_url": ticket.verification_url,
                        "user_code": ticket.user_code,
                        "deadline_at": deadline.isoformat(),
                    }
                ),
            )

    async def record_snapshot(
        self, *, connection_id: UUID, snapshot: EngineSnapshot, now: datetime
    ) -> None:
        """What one successful read produced, with the moment it was read.

        The status follows the snapshot rather than being passed in: RATE_LIMITED when the engine
        said so, CONNECTED when there is an account, PENDING_LOGIN when there is not. A caller
        choosing the status separately is a caller that can disagree with the data it just wrote.
        """
        if snapshot.rate_limited:
            status = "RATE_LIMITED"
        elif snapshot.logged_in:
            status = "CONNECTED"
        else:
            status = "PENDING_LOGIN"
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE codex_connections SET
                    status = $2,
                    account_email = $3,
                    plan_type = $4,
                    installation_id = $5,
                    models = $6::jsonb,
                    rate_limits = $7::jsonb,
                    login_ticket = '{}'::jsonb,
                    last_read_at = $8,
                    last_error = NULL
                WHERE id = $1
                """,
                connection_id,
                status,
                snapshot.account_email,
                snapshot.plan_type,
                snapshot.installation_id,
                json.dumps(snapshot.models),
                json.dumps(snapshot.rate_limits),
                now,
            )

    async def record_not_read(self, *, connection_id: UUID, reason: str, now: datetime) -> None:
        """ "We deliberately did not measure this one", which is neither success nor failure.

        The STATUS IS LEFT ALONE on purpose. A connection whose engine we never open has not
        changed state, and writing ERROR would say something went wrong when nothing did. What is
        written is the absence itself: limits and models marked as not read, with the reason, and
        the moment we decided not to look — because "kiedy" is required of every claim on the
        screen even when the claim is "nie sprawdziliśmy".
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE codex_connections SET
                    models = '{}'::jsonb,
                    rate_limits = $2::jsonb,
                    last_read_at = $3,
                    last_error = NULL
                WHERE id = $1
                """,
                connection_id,
                json.dumps({"read": False, "reason": reason}),
                now,
            )

    async def record_error(self, *, connection_id: UUID, reason: str, now: datetime) -> None:
        """An error is a STATE with a sentence, not a silence.

        `last_read_at` is written too: "we tried, at this moment, and this is what came back" is
        a measurement, and a screen that could not date the failure would look like a screen that
        never tried.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE codex_connections SET status = 'ERROR', last_error = $2,"
                " login_ticket = '{}'::jsonb, last_read_at = $3 WHERE id = $1",
                connection_id,
                reason[:2000],
                now,
            )

    async def disconnect(self, *, organization_id: UUID, connection_id: UUID) -> bool:
        """Mark DISCONNECTED and free the directory. Nothing is deleted, on disk or in the table.

        The row stays with its `disconnected_at`, the directory stays with its `auth.json`, and
        `account/logout` is NOT called: logging the account out is a separate, explicit request,
        because a disconnect in this product means "stop using it here", not "sign me out".
        """
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE codex_connections SET status = 'DISCONNECTED', disconnected_at = now(),"
                " login_ticket = '{}'::jsonb"
                " WHERE organization_id = $1 AND id = $2 AND disconnected_at IS NULL"
                " RETURNING id",
                organization_id,
                connection_id,
            )
        return updated is not None

    async def cancel_requested(self, *, connection_id: UUID) -> bool:
        """True when the human ended this connection while the login job was still waiting.

        The signal is the row itself — DISCONNECTED, or gone. No extra column and no in-memory
        flag: the job and the request that cancels it run in different tasks and, after a
        restart, in different processes.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, disconnected_at FROM codex_connections WHERE id = $1",
                connection_id,
            )
        if row is None:
            return True
        return row["disconnected_at"] is not None or row["status"] == "DISCONNECTED"
