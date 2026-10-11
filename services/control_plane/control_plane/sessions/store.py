"""Reads and writes of migration 0015: agent sessions and their append-only trace (P2).

EVERY METHOD TAKES `organization_id` AND PUTS IT IN THE WHERE CLAUSE, for the reason
`workspace/store.py` states: composite foreign keys stop a row being WRITTEN across a tenant
boundary and do nothing at all about a READ. A query filtered on the session id alone answers
with another organization's conversation as soon as there is a second tenant, and the failure is
silent because the screen fills with plausible text.

TWO RULES THIS MODULE EXISTS TO KEEP, both of which are easy to lose by accident:

1. **`seq` is allocated by the database, under a per-session lock, not by the caller.** The race
   is real and was measured, not imagined: twenty concurrent appends, ten refused. Computing the
   number inside the INSERT is NOT enough, because concurrent transactions read `max(seq)` under
   their own snapshots and all get the same answer. `_append` therefore takes a transaction
   scoped advisory lock on the session id first. See its docstring for why the alternatives (a
   table lock, a retry loop) were rejected.
2. **The trace is append-only in the database (trigger) and there is no UPDATE here.** A
   correction is a new event. This module offers no way to rewrite one, so nothing built on it
   can offer one either.

WHAT `record_turn_result` DOES AND DOES NOT DO. It closes a session: status, summary, refs and
`finished_at` in one statement, so a session cannot be DONE without the moment it finished
(migration 0015 has a CHECK saying the same thing, and this is the caller-side half of it).
It does NOT write the assistant's last message: that is an event, it was already appended while
the turn streamed, and writing it twice would make the trace disagree with itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

#: The eight states of a session. `QUEUED` is where every session starts: the row exists and the
#: job that will run it has not been claimed yet.
STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
#: The agent answered and is waiting for the human. This is a RESULT, not a failure: "brak okazji
#: do wartościowego działania jest poprawnym wynikiem" (ROADMAP-PIVOT).
STATUS_WAITING_USER = "WAITING_USER"
STATUS_WAITING_EVENT = "WAITING_EVENT"
STATUS_PAUSED = "PAUSED"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

SESSION_STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    STATUS_WAITING_EVENT,
    STATUS_PAUSED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_BUDGET_EXHAUSTED,
)

#: The three states after which nothing more runs on this session. Kept as one tuple because the
#: database has the same list in a CHECK, and two lists of the same thing drift.
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED, STATUS_BUDGET_EXHAUSTED)

#: The two kinds of session from 0017. Spelled here as well as in the database because the code
#: that creates a session has to name one, and a typo would otherwise be caught only by a CHECK
#: at insert time, in a job, at three in the morning.
SESSION_CONVERSATION = "CONVERSATION"
SESSION_PRESENCE = "PRESENCE"
#: Obie naraz, do sprawdzania tego, co przychodzi z zewnątrz (filtr `?rodzaj=` w API).
SESSION_KINDS: frozenset[str] = frozenset({SESSION_CONVERSATION, SESSION_PRESENCE})

EVENT_USER_MESSAGE = "USER_MESSAGE"
EVENT_ASSISTANT_MESSAGE = "ASSISTANT_MESSAGE"
EVENT_TOOL_CALL = "TOOL_CALL"
EVENT_TOOL_RESULT = "TOOL_RESULT"
EVENT_STATUS = "STATUS"
EVENT_ERROR = "ERROR"
EVENT_INTERRUPT = "INTERRUPT"

EVENT_KINDS = (
    EVENT_USER_MESSAGE,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_STATUS,
    EVENT_ERROR,
    EVENT_INTERRUPT,
)

#: How long a plugin session is reused, and how long its trace may grow before it is rotated
#: (see `open_host_session`). A day and six hundred events: the numbers the plugin was specified
#: with, kept here so the two meet in one place.
HOST_SESSION_MAX_AGE = timedelta(hours=24)
HOST_SESSION_MAX_EVENTS = 600

#: The same text arriving again within this window is one message sent twice (a double click on
#: "wyślij"), not a person repeating themselves on purpose.
USER_MESSAGE_REPEAT_WINDOW = timedelta(seconds=10)


class SessionRefusal(Exception):
    """A write the domain refuses, with a sentence the operator reads (D-010).

    A distinct type rather than `HTTPException`, for the reason `workspace/store.py` gives: the
    store must not know about HTTP, and the router must not guess which exception means "you
    asked for something impossible" and which means "the database is broken".
    """


@dataclass(frozen=True)
class SessionRow:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    codex_connection_id: UUID | None
    goal: str
    # 0017: CONVERSATION (a person is reading) or PRESENCE (nobody is). The difference is not
    # cosmetic: a presence session's budget comes from a delegation and its reason for existing
    # is in `wakeups`, so the screens and the day report have to be able to tell them apart.
    kind: str
    #: 0019: the session that asked for this work, through `session.handoff`. `None` means
    #: nobody asked: a person opened it, or a presence tick did.
    parent_session_id: UUID | None
    status: str
    engine_thread_id: str | None
    budget: dict[str, Any]
    result_summary: str | None
    result_refs: list[Any]
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


#: The key in `agent_sessions.budget` that marks a session as driven by the person's own coding
#: agent through the plugin. It lives in `budget` because that column is free-form jsonb with no
#: CHECK on its keys (0015; the CHECKs of 0026 are on `projects`), and a new session `kind` would
#: break the CHECK of 0017 and the dashboard's schema. `budget_of` ignores keys it does not know,
#: so the marker costs the Wenext-run path nothing.
HOST_BUDGET_KEY = "host"


def host_of(session: SessionRow) -> str | None:
    """The agent driving this session from outside (`codex`, `claude-code`), or `None`.

    ONE DEFINITION OF "A HOST SESSION", used by the turn handler, the HTTP routes, the starting
    context and `memory.write`. Six places each reading `budget["host"]` is six places that
    disagree the day the marker moves.
    """
    value = (session.budget or {}).get(HOST_BUDGET_KEY)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class EventRow:
    id: UUID
    session_id: UUID
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


_SESSION_COLUMNS = (
    "id, organization_id, workspace_id, project_id, codex_connection_id, goal, kind, "
    "parent_session_id, status, engine_thread_id, budget, result_summary, result_refs, "
    "created_at, updated_at, finished_at"
)


def _session_of(row: asyncpg.Record) -> SessionRow:
    return SessionRow(
        id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
        codex_connection_id=row["codex_connection_id"],
        goal=row["goal"],
        kind=row["kind"],
        parent_session_id=row["parent_session_id"],
        status=row["status"],
        engine_thread_id=row["engine_thread_id"],
        # asyncpg hands jsonb back as text unless a codec is set; both shapes are accepted here
        # so this module does not depend on how the pool was built.
        budget=_as_json(row["budget"], {}),
        result_summary=row["result_summary"],
        result_refs=_as_json(row["result_refs"], []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


def _as_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _event_of(row: asyncpg.Record) -> EventRow:
    return EventRow(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        kind=row["kind"],
        payload=_as_json(row["payload"], {}),
        created_at=row["created_at"],
    )


class SessionStore:
    """Holds a pool, never a connection: the same rule as every other store in this package."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- sessions ---------------------------------------------------------------------------

    async def create(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        goal: str,
        budget: dict[str, Any],
        codex_connection_id: UUID | None = None,
        kind: str = SESSION_CONVERSATION,
        parent_session_id: UUID | None = None,
        announce_goal: bool = True,
        status: str = STATUS_QUEUED,
    ) -> SessionRow:
        """A new session, by default in `QUEUED` and with the goal as its first event.

        The session row and the `USER_MESSAGE` carrying the goal are written in ONE transaction.
        A session whose first event failed to land would open on a screen with no goal in it,
        and the goal is the only thing the user actually typed.

        `announce_goal=False` writes NO event at all. A plugin session (`plugin_server`) has a goal
        that WE wrote ("Wtyczka: praca w Twoim agencie"), not the person, and a `USER_MESSAGE` at
        seq 1 is what `memory.write` accepts as "the founder said so": the host agent could cite
        that seq as the person's words and record a PREFERENCE nobody stated. The goal then lives
        only in the row.

        `status` exists for the same caller: a session nobody will run a turn for must not pass
        through `QUEUED`, which is the state the dispatcher's reconciliation reads as "waiting for
        a worker".
        """
        if status not in SESSION_STATUSES:
            raise SessionRefusal(f"status {status!r} poza listą {SESSION_STATUSES}")
        if not goal.strip():
            raise SessionRefusal("cel sesji nie może być pusty")
        if parent_session_id is not None:
            # THE PROJECT IS CHECKED HERE AND NOT IN THE DATABASE, and that is the compromise
            # 0019 writes down rather than hides: a CHECK in PostgreSQL cannot query another
            # table, and the tenant triple in the foreign key gets the organization and the
            # workspace but not the project. A handoff into another project of the same
            # workspace would otherwise be a way to reach work the asking session cannot see.
            await self._require_same_project(
                organization_id=organization_id,
                project_id=project_id,
                parent_session_id=parent_session_id,
            )
        async with self._pool.acquire() as conn, conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO agent_sessions
                        (organization_id, workspace_id, project_id, codex_connection_id, goal,
                         budget, kind, parent_session_id, status)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9)
                    RETURNING {_SESSION_COLUMNS}
                    """,
                    organization_id,
                    workspace_id,
                    project_id,
                    codex_connection_id,
                    goal,
                    json.dumps(budget, ensure_ascii=False),
                    kind,
                    parent_session_id,
                    status,
                )
            except asyncpg.ForeignKeyViolationError as exc:
                raise SessionRefusal(
                    f"projekt {project_id} nie należy do tego workspace'u albo połączenie "
                    f"{codex_connection_id} nie należy do tej organizacji"
                ) from exc
            session = _session_of(row)
            if announce_goal:
                await self._append(
                    conn,
                    session=session,
                    kind=EVENT_USER_MESSAGE,
                    payload={"text": goal, "is_goal": True},
                )
        return session

    async def open_host_session(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        host: str,
        goal: str,
        max_age: timedelta = HOST_SESSION_MAX_AGE,
        max_events: int = HOST_SESSION_MAX_EVENTS,
    ) -> tuple[SessionRow, bool]:
        """The plugin host's conversation in a project, reusing the live one: `(session, created)`.

        A plugin server starts every time the person opens their agent, so creating a session per
        start would fill the project's conversation list with empty rows. REUSED: the latest
        non-terminal `CONVERSATION` of this host in this project that has no Codex connection (a
        plugin session runs on none), younger than `max_age` and with fewer than `max_events`
        events. Anything else is rotated: a day-old window is a new piece of work, and a trace of
        hundreds of events is slow to read on the screen and expensive to hand to a model.

        UNDER A LOCK, because two agent windows opened together would both find nothing and both
        create. The advisory lock is keyed on project and host, held by this method's own
        connection until the session is committed (`create` commits on another one), so the second
        caller waits and then finds the first one's row.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"host-session:{project_id}:{host}",
            )
            row = await conn.fetchrow(
                f"""
                SELECT {_SESSION_COLUMNS} FROM agent_sessions s
                 WHERE s.organization_id = $1 AND s.workspace_id = $2 AND s.project_id = $3
                   AND s.kind = $4 AND s.codex_connection_id IS NULL
                   AND s.budget ->> $5 = $6
                   AND s.status <> ALL($7::text[])
                   AND s.created_at > now() - make_interval(secs => $8::double precision)
                   AND (SELECT coalesce(max(e.seq), 0) FROM session_events e
                         WHERE e.session_id = s.id) < $9
                 ORDER BY s.created_at DESC
                 LIMIT 1
                """,
                organization_id,
                workspace_id,
                project_id,
                SESSION_CONVERSATION,
                HOST_BUDGET_KEY,
                host,
                list(TERMINAL_STATUSES),
                max_age.total_seconds(),
                max_events,
            )
            if row is not None:
                return _session_of(row), False
            session = await self.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                goal=goal,
                budget={HOST_BUDGET_KEY: host},
                announce_goal=False,
                status=STATUS_WAITING_USER,
            )
            return session, True

    async def _require_same_project(
        self, *, organization_id: UUID, project_id: UUID, parent_session_id: UUID
    ) -> None:
        async with self._pool.acquire() as conn:
            parent_project = await conn.fetchval(
                "SELECT project_id FROM agent_sessions WHERE organization_id = $1 AND id = $2",
                organization_id,
                parent_session_id,
            )
        if parent_project is None:
            raise SessionRefusal(
                f"nie ma sesji {parent_session_id} w tej organizacji, więc nie ma kto zlecić"
            )
        if parent_project != project_id:
            raise SessionRefusal(
                f"sesja {parent_session_id} pracuje nad innym projektem. Przekazanie zadania do "
                "cudzego projektu nie jest przekazaniem, tylko sięgnięciem po dostęp, którego "
                "sesja zlecająca nie ma"
            )

    async def get(self, *, organization_id: UUID, session_id: UUID) -> SessionRow | None:
        """THE gate of every route with a session id in the path: `None` means 404, and an id
        from another organization is indistinguishable from one that does not exist."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SESSION_COLUMNS} FROM agent_sessions "
                "WHERE organization_id = $1 AND id = $2",
                organization_id,
                session_id,
            )
        return _session_of(row) if row is not None else None

    async def for_project(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        kind: str | None = None,
        limit: int = 50,
    ) -> tuple[SessionRow, ...]:
        """Sesje projektu, najnowsze pierwsze. `kind=None` znaczy „obu rodzajów”.

        Filtr jest w ZAPYTANIU, a nie po stronie wywołującego, bo limit tnie przed filtrem: pasek
        rozmów biorący pięćdziesiąt sesji i odsiewający tiki obecności dostałby przy aktywnym
        projekcie samą obecność, bo tików bywa kilkanaście dziennie, a rozmów kilka.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_SESSION_COLUMNS} FROM agent_sessions "
                "WHERE organization_id = $1 AND workspace_id = $2 AND project_id = $3 "
                "AND ($5::text IS NULL OR kind = $5) "
                "ORDER BY created_at DESC LIMIT $4",
                organization_id,
                workspace_id,
                project_id,
                limit,
                kind,
            )
        return tuple(_session_of(r) for r in rows)

    async def for_workspace(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        kind: str | None = None,
        limit: int = 10,
    ) -> tuple[SessionRow, ...]:
        """Ostatnie sesje CAŁEJ przestrzeni, ze wszystkich jej projektów (D-045).

        Jeden odczyt zamiast jednego na projekt. Sekcja „Ostatnie” w pasku składana po stronie
        przeglądarki znaczyłaby tyle zapytań, ile projektów, przy każdym wejściu na dowolny ekran.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_SESSION_COLUMNS} FROM agent_sessions "
                "WHERE organization_id = $1 AND workspace_id = $2 "
                "AND ($4::text IS NULL OR kind = $4) "
                "ORDER BY created_at DESC LIMIT $3",
                organization_id,
                workspace_id,
                limit,
                kind,
            )
        return tuple(_session_of(r) for r in rows)

    async def set_status(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        status: str,
        engine_thread_id: str | None = None,
        result_summary: str | None = None,
        result_refs: list[Any] | None = None,
    ) -> SessionRow:
        """Move a session to `status`, and set `finished_at` exactly when that status is terminal.

        `finished_at` is computed HERE from the status rather than passed in, so no caller can
        produce the one row the schema forbids: DONE with no finishing time, or RUNNING with one.
        """
        if status not in SESSION_STATUSES:
            raise SessionRefusal(f"status {status!r} poza listą {SESSION_STATUSES}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE agent_sessions
                   SET status = $3,
                       engine_thread_id = COALESCE($4, engine_thread_id),
                       result_summary = COALESCE($5, result_summary),
                       result_refs = COALESCE($6::jsonb, result_refs),
                       finished_at = CASE WHEN $3 = ANY($7::text[]) THEN now() ELSE NULL END,
                       updated_at = now()
                 WHERE organization_id = $1 AND id = $2
                RETURNING {_SESSION_COLUMNS}
                """,
                organization_id,
                session_id,
                status,
                engine_thread_id,
                result_summary,
                json.dumps(result_refs, ensure_ascii=False) if result_refs is not None else None,
                list(TERMINAL_STATUSES),
            )
        if row is None:
            raise SessionRefusal(f"nie ma sesji {session_id}")
        return _session_of(row)

    async def children_of(
        self, *, organization_id: UUID, session_id: UUID
    ) -> tuple[SessionRow, ...]:
        """Sessions this one handed work to, oldest first. The order is the order it asked."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_SESSION_COLUMNS} FROM agent_sessions "
                "WHERE organization_id = $1 AND parent_session_id = $2 ORDER BY created_at",
                organization_id,
                session_id,
            )
        return tuple(_session_of(row) for row in rows)

    async def reconcile_orphans(self, *, organization_id: UUID) -> tuple[SessionRow, ...]:
        """Sesje w RUNNING, których tura już nie żyje, kończy jako FAILED z powodem.

        POWÓD ISTNIENIA, ZMIERZONY NA LIVE 19.09. Job `session.turn` padł (reaper zabrał go po
        wygaśnięciu lease'u o 18:48), a `agent_sessions` dalej mówiło RUNNING z `updated_at`
        z 18:38. Studio czytało stan z danych i uczciwie pokazywało „pracuje", bo dane tak
        mówiły. Sesja nie może być RUNNING bez żywego joba tury: to nie jest stan, tylko ślad po
        procesie, którego już nie ma.

        CO ZNACZY „NIE ŻYJE". Nie ma dla tej sesji joba `session.turn` w stanie, który jeszcze
        może się wykonać. Job SUCCEEDED też się liczy jako martwy: skoro się skończył, a sesja
        dalej stoi w RUNNING, to znaczy, że handler nie zdążył jej zamknąć.

        Po FAILED sesja przyjmuje NOWĄ turę: `claim_for_turn` startuje z każdego stanu, który nie
        jest terminalny dla pracy, a FAILED nim nie jest z punktu widzenia człowieka, który chce
        wznowić. Awaria jednej tury nie ma kasować rozmowy.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_SESSION_COLUMNS} FROM agent_sessions s
                 WHERE s.organization_id = $1 AND s.status = $2
                   AND NOT EXISTS (
                       SELECT 1 FROM jobs j
                        WHERE j.organization_id = s.organization_id
                          AND j.payload_ref = 'session/' || s.id
                          AND j.job_type = 'session.turn'
                          AND j.status IN ('READY', 'RUNNING', 'WAITING_RETRY', 'WAITING_AUTH',
                                           'WAITING_QUOTA'))
                   -- A BACKGROUND session's turn runs INSIDE the project's `presence.tick` job,
                   -- not in a `session.turn` job (27.09: a tick publishing the first X post was
                   -- marked "tura przerwana" mid-turn and finished ten seconds later).
                   AND NOT (s.kind = 'PRESENCE' AND EXISTS (
                       SELECT 1 FROM jobs p
                        WHERE p.organization_id = s.organization_id
                          AND p.job_type = 'presence.tick'
                          AND p.payload_ref = 'presence/' || s.project_id
                          AND p.status = 'RUNNING'))
                """,
                organization_id,
                STATUS_RUNNING,
            )
        closed: list[SessionRow] = []
        for row in rows:
            session = _session_of(row)
            async with self._pool.acquire() as conn, conn.transaction():
                await self._append(
                    conn,
                    session=session,
                    kind=EVENT_STATUS,
                    payload={
                        "detail": (
                            "tura przerwana: lease wygasł i nie ma joba, który mógłby ją "
                            "dokończyć. Sesję da się wznowić nową turą"
                        )
                    },
                )
            closed.append(
                # `finished_at` liczy `set_status` z samego statusu i tak ma zostać: żaden
                # wywołujący nie może zamknąć sesji bez momentu ani odwrotnie.
                await self.set_status(
                    session.id,
                    organization_id=organization_id,
                    status=STATUS_FAILED,
                    result_summary="tura przerwana: lease wygasł",
                )
            )
        return tuple(closed)

    async def claim_for_turn(self, session_id: UUID, *, organization_id: UUID) -> SessionRow | None:
        """Move a session into RUNNING, but ONLY from a state a turn may start from.

        Returns `None` when the session is already RUNNING or truly finished, and that `None` is
        the point: it is a compare-and-set in the database, so two jobs racing on one session
        cannot both start a turn. The alternative, reading the status and then updating it, is
        the same race the job queue solves with a lease, written again one layer up and losing.

        FAILED IS IN THE LIST, and that is a decision, not an oversight. A failed turn is a
        failed TURN, not a finished conversation: the lease expired, the engine went quiet, the
        model errored. The person looking at that session wants to say "spróbuj jeszcze raz", and
        before 2026-09-19 they could not, because the only route back was a new session with an
        empty trace. Measured on live that day: `session.turn` was reaped, the session stayed
        RUNNING, reconciliation moved it to FAILED, and resuming was refused for that state.

        DONE and BUDGET_EXHAUSTED are NOT in the list, and the difference is the reason. Those
        two are statements about the WORK: one says the goal is met, the other that the money is
        spent. Re-running a turn from either would restart work somebody decided was over.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE agent_sessions
                   -- `finished_at` WRACA DO NULL, i tego wymaga CHECK `agent_sessions_finished
                   -- _when_terminal` z 0015: stan terminalny i moment zakończenia to jedna
                   -- rzecz, nie dwie. Sesja, która znów pracuje, nie jest skończona, a wiersz
                   -- mówiący RUNNING z datą zakończenia byłby zdaniem o dwóch sprzecznych
                   -- faktach. Więz złapał to natychmiast przy pierwszym wznowieniu po FAILED.
                   SET status = $3, updated_at = now(), finished_at = NULL
                 WHERE organization_id = $1 AND id = $2
                   AND status = ANY($4::text[])
                RETURNING {_SESSION_COLUMNS}
                """,
                organization_id,
                session_id,
                STATUS_RUNNING,
                [
                    STATUS_QUEUED,
                    STATUS_WAITING_USER,
                    STATUS_WAITING_EVENT,
                    STATUS_PAUSED,
                    # Patrz docstring: nieudana TURA nie jest skończoną rozmową.
                    STATUS_FAILED,
                ],
            )
        return _session_of(row) if row is not None else None

    async def queue_followup(self, *, organization_id: UUID, session_id: UUID) -> None:
        """Keep the stream open while a newly accepted follow-up waits for its worker.

        Conditional at write time: a concurrent worker may already be running. Never overwrite
        that state with QUEUED based on the older session read by the request handler.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_sessions SET status = $3, updated_at = now() "
                "WHERE organization_id = $1 AND id = $2 AND status = ANY($4::text[])",
                organization_id,
                session_id,
                STATUS_QUEUED,
                [STATUS_WAITING_USER, STATUS_WAITING_EVENT, STATUS_PAUSED],
            )

    # -- events -----------------------------------------------------------------------------

    async def append_event(
        self,
        *,
        organization_id: UUID,
        session_id: UUID,
        kind: str,
        payload: dict[str, Any],
    ) -> EventRow:
        session = await self.get(organization_id=organization_id, session_id=session_id)
        if session is None:
            raise SessionRefusal(f"nie ma sesji {session_id}")
        async with self._pool.acquire() as conn:
            return await self._append(conn, session=session, kind=kind, payload=payload)

    async def append_user_message(
        self,
        *,
        organization_id: UUID,
        session_id: UUID,
        text: str,
        window: timedelta = USER_MESSAGE_REPEAT_WINDOW,
    ) -> tuple[EventRow, bool]:
        """The person's message, unless the SAME text reached this session within `window`.

        Measured 27.09: one message, two identical events 25 ms apart. The composer is a plain
        form, so a second click on "wyślij" before the page reloads sends it again, and the agent
        read the founder's direction twice. The earlier event comes back with `False` ("nothing
        added"). The check runs under the session's own append lock, because the two requests of
        a double click race each other; outside the lock both would find nothing and both insert.
        """
        session = await self.get(organization_id=organization_id, session_id=session_id)
        if session is None:
            raise SessionRefusal(f"nie ma sesji {session_id}")
        payload = {"text": text}
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", str(session.id)
            )
            repeated = await conn.fetchrow(
                """
                SELECT id, session_id, seq, kind, payload, created_at
                  FROM session_events
                 WHERE session_id = $1 AND kind = $2 AND payload = $3::jsonb
                   AND created_at > now() - make_interval(secs => $4::double precision)
                 ORDER BY seq DESC
                 LIMIT 1
                """,
                session.id,
                EVENT_USER_MESSAGE,
                json.dumps(payload, ensure_ascii=False),
                window.total_seconds(),
            )
            if repeated is not None:
                return _event_of(repeated), False
            # `_append` nests as a savepoint and takes the same advisory lock again, which a
            # transaction already holding it is granted at once.
            event = await self._append(
                conn, session=session, kind=EVENT_USER_MESSAGE, payload=payload
            )
            return event, True

    async def _append(
        self,
        conn: asyncpg.Connection,
        *,
        session: SessionRow,
        kind: str,
        payload: dict[str, Any],
    ) -> EventRow:
        """The insert, with `seq` taken under a lock held for THIS SESSION ONLY.

        MEASURED, NOT REASONED ABOUT. The first version computed the number inside the INSERT
        (`coalesce(max(seq), 0) + 1` in a subquery) on the assumption that reading and writing in
        one statement cannot race. Twenty concurrent appends: TEN were refused by the unique
        index. Concurrent transactions read `max(seq)` under their own snapshots, so the
        subquery gives them all the same answer, and the statement being single does not help.

        The lock is a transaction-scoped advisory lock keyed on the session id. It serialises
        appends WITHIN one session, which is exactly the scope of the invariant, and leaves
        appends to other sessions running in parallel. A table lock would have been correct and
        far too wide; a retry loop would have been correct and would have left the caller
        deciding how many losses are acceptable.

        `pg_advisory_xact_lock` releases at COMMIT, so the insert must be inside a transaction.
        `conn.transaction()` nests as a savepoint when the caller already opened one (which
        `create` does), and the lock then lives until that outer transaction ends.
        """
        if kind not in EVENT_KINDS:
            raise SessionRefusal(f"rodzaj zdarzenia {kind!r} poza listą {EVENT_KINDS}")
        try:
            async with conn.transaction():
                # `str(...)`, not the UUID: asyncpg infers the parameter type from the `::text`
                # cast and refuses a UUID object for it ("expected str, got UUID").
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                    str(session.id),
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO session_events
                        (organization_id, workspace_id, session_id, seq, kind, payload)
                    SELECT $1, $2, $3,
                           coalesce(max(e.seq), 0) + 1, $4, $5::jsonb
                      FROM session_events e
                     WHERE e.session_id = $3
                    RETURNING id, session_id, seq, kind, payload, created_at
                    """,
                    session.organization_id,
                    session.workspace_id,
                    session.id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                )
        except asyncpg.UniqueViolationError as exc:
            # Unreachable while the lock above is held, and kept anyway: if somebody removes the
            # lock, this turns a silently dropped event into a loud refusal rather than nothing.
            raise SessionRefusal(
                f"zdarzenie sesji {session.id} kolidowało o numer kolejny; ponów zapis"
            ) from exc
        return _event_of(row)

    async def events(
        self, *, organization_id: UUID, session_id: UUID, after_seq: int = 0, limit: int = 500
    ) -> tuple[EventRow, ...]:
        """The trace from `after_seq` on, in order. This is what the screen resumes a read with,
        and what rebuilds it after a restart."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id, e.session_id, e.seq, e.kind, e.payload, e.created_at
                  FROM session_events e
                  JOIN agent_sessions s
                    ON s.id = e.session_id AND s.organization_id = e.organization_id
                 WHERE e.organization_id = $1 AND e.session_id = $2 AND e.seq > $3
                 ORDER BY e.seq
                 LIMIT $4
                """,
                organization_id,
                session_id,
                after_seq,
                limit,
            )
        return tuple(_event_of(r) for r in rows)

    async def turn_input_events(
        self, *, organization_id: UUID, session_id: UUID
    ) -> tuple[EventRow, ...]:
        """Messages and consumption markers across the whole conversation.

        Tool traffic must not displace later user corrections from a 500-event window.
        Assistant presence determines the first turn; its body is not needed here.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, session_id, seq, kind, created_at, "
                "CASE WHEN kind = 'ASSISTANT_MESSAGE' THEN '{}'::jsonb ELSE payload END AS payload "
                "FROM session_events WHERE organization_id = $1 AND session_id = $2 "
                "AND (kind IN ('USER_MESSAGE', 'ASSISTANT_MESSAGE') "
                "OR (kind = 'STATUS' AND payload ? 'wenext_wejscie_do_seq')) ORDER BY seq",
                organization_id,
                session_id,
            )
        return tuple(_event_of(row) for row in rows)

    async def last_seq(self, *, organization_id: UUID, session_id: UUID) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT coalesce(max(seq), 0) FROM session_events "
                "WHERE organization_id = $1 AND session_id = $2",
                organization_id,
                session_id,
            )
        return int(value or 0)

    async def counts_for_budget(self, *, organization_id: UUID, session_id: UUID) -> dict[str, int]:
        """What the budget is measured against, read from the trace rather than from memory.

        A counter kept in the handler resets when the worker restarts, and a session that
        restarted twice would get three times its budget. The trace survives the restart, so the
        trace is what counts.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (WHERE kind = $3) AS turns,
                    count(*) FILTER (WHERE kind = $4) AS tool_calls
                  FROM session_events
                 WHERE organization_id = $1 AND session_id = $2
                """,
                organization_id,
                session_id,
                EVENT_USER_MESSAGE,
                EVENT_TOOL_CALL,
            )
        return {"turns": int(row["turns"]), "tool_calls": int(row["tool_calls"])}
