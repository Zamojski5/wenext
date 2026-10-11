"""HTTP for agent sessions (P2): start one, read it, talk to it, stop it, resume it.

THE GATE IS THE SAME ONE P1 USES. Every route with an id in its path resolves the workspace
through `WorkspaceStore.workspace` and answers 404 when it comes back `None`, so an identifier
from another organization is indistinguishable from one that does not exist. The session itself
is then fetched with `organization_id` in the WHERE clause as well: two gates, because the first
one protects the path and the second one protects the row.

NOTHING HERE RUNS A TURN. Every route that should make the agent work enqueues a `session.turn`
job and returns. The turn is minutes long and reaches a model; running it inside a request would
tie the answer to one HTTP connection, lose it when the browser navigates away, and give up
every guarantee the queue provides (lease, heartbeat, no double execution). The screen learns
what happened by reading the trace, which is also what it does after a restart.

WHY "INTERRUPT" IS AN EVENT AND NOT A CALL. The turn runs in a worker process; this route runs
in the API process. They share a database and nothing else. So the button appends an `INTERRUPT`
event and the running turn, which watches its own trace, asks the engine to stop. A handle kept
in memory would work only while both happened to be the same process.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.sessions import (
    EVENT_INTERRUPT,
    HOST_BUDGET_KEY,
    SESSION_KINDS,
    STATUS_PAUSED,
    STATUS_QUEUED,
    SessionRefusal,
    SessionRow,
    SessionStore,
    host_of,
)
from control_plane.sessions.handlers import schedule_turn
from control_plane.sessions.projection import cards_for, progress_for
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["sessions"])

#: Relative to the router's own `/api` prefix, like every path in `api/workspaces.py`. Spelling
#: it absolute here would mount the routes at `/api/api/...`, which answers 404 and looks exactly
#: like a missing route.
SESSIONS_PATH = "/workspaces/{workspace_id}/projects/{project_id}/sessions"

#: The goal is prose a person writes for an agent, so a paragraph is fine. Bounded anyway: an
#: unbounded text column reached through an HTTP body is a free allocation.
MAX_GOAL_LENGTH = 4000
MAX_MESSAGE_LENGTH = 4000

#: How many events one read returns. The screen pages with `after_seq`, so this is a page size
#: and not a limit on the conversation.
MAX_EVENTS = 200

#: Ile zdarzeń czyta projekcja kart i postępu. Osobna liczba od `MAX_EVENTS`, bo to jest odczyt
#: STANU, a nie strona listy: karta sprzed pięciuset zdarzeń dalej stoi w rozmowie.
MAX_TRACE_FOR_CARDS = 1000


class StartSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=MAX_GOAL_LENGTH)
    #: Which Codex connection to run on. Optional, and its absence is NOT "any": a session with
    #: no connection parks on its first turn and says what is missing.
    codex_connection_id: UUID | None = None
    budget: dict[str, int] | None = None


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


async def _store(request: Request) -> SessionStore:
    return SessionStore(await pool_for(request.app))


async def _workspaces(request: Request) -> WorkspaceStore:
    return WorkspaceStore(await pool_for(request.app))


async def _resolve_project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> Any:
    """THE gate: the workspace belongs to this operator's organization and holds this project.

    Oddaje wiersz projektu, a nie `None`, bo wywołujący potrzebuje z niego jednej rzeczy poza
    samym istnieniem: budżetu rozmowy (0026). Jeden odczyt zamiast dwóch, i bramka nadal jest
    tą samą bramką.
    """
    workspaces = await _workspaces(request)
    found = await workspaces.workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    project = await workspaces.get_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")
    return project


async def _resolve_session(
    request: Request, operator: OperatorSession, workspace_id: UUID, session_id: UUID
) -> SessionRow:
    workspaces = await _workspaces(request)
    if (
        await workspaces.workspace(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    store = await _store(request)
    session = await store.get(organization_id=operator.organization_id, session_id=session_id)
    if session is None or session.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"nie ma sesji {session_id}")
    return session


#: What a person is told when they try to talk to, resume or interrupt a conversation that their
#: own coding agent runs (the plugin). The dashboard is where they approve drafts; the agent is
#: where they talk. Starting a turn here would run a SECOND model on a conversation whose first
#: one is the person's own agent, with the person's words never reaching it.
HOST_SESSION_REFUSAL = (
    "Ta rozmowa jest prowadzona w Twoim agencie (Codex albo Claude Code). Napisz tam; "
    "tutaj zatwierdzasz szkice."
)


def _refuse_host_session(session: SessionRow) -> None:
    """409 for a session that runs in the person's own agent. One place, four routes."""
    if host_of(session) is not None:
        raise HTTPException(status_code=409, detail=HOST_SESSION_REFUSAL)


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    session: SessionRow,
) -> None:
    """One audit row after the write it describes, in its own transaction (same as P1).

    The payload carries identifiers only. The goal and the person's messages are prose and live
    in `session_events`; `audit_events` is append-only and its payload allowlist has no
    free-text key on purpose.
    """
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=operator.organization_id,
            workspace_id=session.workspace_id,
            subject_ref=f"session:{session.id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={"session_id": str(session.id), "project_id": str(session.project_id)},
        )


async def _ask_for_a_turn(request: Request, session: SessionRow, *, reason: str) -> None:
    pool = await pool_for(request.app)
    queue = PostgresJobTransport(JobStore(pool), organization_id=session.organization_id)
    await schedule_turn(
        queue,
        organization_id=session.organization_id,
        workspace_id=session.workspace_id,
        session_id=session.id,
        reason=reason,
    )


def session_payload(session: SessionRow) -> dict[str, Any]:
    """Written out by hand, one key at a time, so a column added to 0015 does not silently start
    travelling to the browser (the rule `api/workspaces.py` states).

    D-045: `tytul`, `wynik_zdanie` i `rodzaj` są tu dla paska rozmów projektu. Pierwsze dwa liczy
    SERWER (`sessions/summary.py`), bo „jak nazywa się ta rozmowa" ma mieć w produkcie jedną
    definicję, a nie osobną w pasku, osobną w nagłówku i jeszcze jedną w prompcie modelu.
    """
    from control_plane.sessions.summary import one_sentence, title_of

    return {
        # `session_id`, not `id`, and the object is wrapped by its routes: the same shape P1's
        # workspace API uses. One convention across the API is worth more than a shorter key.
        "session_id": str(session.id),
        "project_id": str(session.project_id),
        "codex_connection_id": (
            str(session.codex_connection_id) if session.codex_connection_id else None
        ),
        "goal": session.goal,
        # `null`, a nie pusty napis, gdy z celu nie da się wyciąć zdania: ekran pisze wtedy
        # „Rozmowa bez nazwy" i to jest jedyne miejsce, gdzie ta decyzja stoi.
        "tytul": "Praca w tle" if session.kind == "PRESENCE" else title_of(session.goal),
        "rodzaj": session.kind,
        "status": session.status,
        # Deliberately exposed: the screen says "pracuje" only when there IS an engine thread and
        # the status says so, and a session with no thread yet is a session that has not started.
        "engine_thread_id": session.engine_thread_id,
        "budget": session.budget,
        "result_summary": session.result_summary,
        # Jedno zdanie dla paska; pełne `result_summary` jedzie obok i zostaje nietknięte.
        "wynik_zdanie": one_sentence(session.result_summary),
        "result_refs": session.result_refs,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
    }


def event_payload(event: Any) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


@router.post(SESSIONS_PATH, status_code=201)
async def start_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    body: StartSessionBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Entrust a goal. Creates the session and asks for its first turn.

    BUDŻET BIERZE SIĘ Z PROJEKTU (0026), gdy żądanie go nie niesie. Ustawienie jest jedno, w
    projekcie, i stąd trafia do każdej nowej rozmowy; przepisywanie go ręcznie do każdej sesji
    byłoby sposobem na to, żeby połowa rozmów miała inny limit niż druga połowa.
    """
    project = await _resolve_project(request, operator, workspace_id, project_id)
    store = await _store(request)
    try:
        session = await store.create(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            goal=body.goal.strip(),
            budget=body.budget or project.conversation_budget or {},
            codex_connection_id=body.codex_connection_id,
        )
    except SessionRefusal as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(request, kind="AGENT_SESSION_STARTED", operator=operator, session=session)
    await _ask_for_a_turn(request, session, reason="created")
    return {"session": session_payload(session)}


@router.get(SESSIONS_PATH)
async def list_sessions(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    rodzaj: str | None = None,
) -> dict[str, Any]:
    """Rozmowy i tiki obecności tego projektu, najnowsze pierwsze.

    `?rodzaj=CONVERSATION` zawęża do rozmów, i to jest filtr SERWERA, nie przeglądarki: pasek
    rozmów biorący wszystko i odsiewający tiki obecności dostawałby przy aktywnym projekcie samą
    obecność, bo tików jest kilkanaście dziennie, a rozmów kilka.
    """
    await _resolve_project(request, operator, workspace_id, project_id)
    store = await _store(request)
    sessions = await store.for_project(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        kind=_checked_kind(rodzaj),
    )
    return {"sessions": [session_payload(s) for s in sessions]}


@router.get("/workspaces/{workspace_id}/sessions")
async def list_workspace_sessions(
    request: Request,
    workspace_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    rodzaj: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Ostatnie rozmowy CAŁEJ przestrzeni, ze wszystkich jej projektów (D-045).

    POWÓD JEST POLICZALNY, nie estetyczny: sekcja „Ostatnie" w pasku, złożona po stronie
    przeglądarki, znaczy jedno zapytanie na projekt przy każdym wejściu na dowolny ekran, czyli
    dziesięć odczytów przy dziesięciu projektach. Tu jest jeden.

    Granica ta sama, co wszędzie: przestrzeń musi należeć do organizacji tego operatora.
    """
    workspaces = await _workspaces(request)
    if (
        await workspaces.workspace(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    store = await _store(request)
    sessions = await store.for_workspace(
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        kind=_checked_kind(rodzaj),
        limit=max(1, min(50, limit)),
    )
    return {"sessions": [session_payload(s) for s in sessions]}


def _checked_kind(rodzaj: str | None) -> str | None:
    """Rodzaj z zapytania albo odmowa. Zamknięta lista, bo literówka w filtrze oddająca WSZYSTKO
    jest gorsza niż błąd: pasek rozmów pokazałby wtedy tiki obecności i nikt by nie wiedział
    czemu."""
    if rodzaj is None:
        return None
    value = rodzaj.strip().upper()
    if value not in SESSION_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"rodzaj={rodzaj!r} nie jest znany; są dwa: {', '.join(sorted(SESSION_KINDS))}",
        )
    return value


@router.get(SESSIONS_PATH + "/{session_id}")
async def read_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    after_seq: int = 0,
) -> dict[str, Any]:
    """The session and its trace from `after_seq` on.

    `after_seq` is how the screen catches up: it holds the last number it rendered and asks for
    what came after. That is also how it recovers after a restart, of the browser or of the
    worker, because the number lives in the trace and not in either process.
    """
    session = await _resolve_session(request, operator, workspace_id, session_id)
    store = await _store(request)
    events = await store.events(
        organization_id=operator.organization_id,
        session_id=session_id,
        after_seq=max(0, after_seq),
        limit=MAX_EVENTS,
    )
    last = await store.last_seq(organization_id=operator.organization_id, session_id=session_id)
    # KARTY I POSTĘP LICZĄ SIĘ Z CAŁEGO ŚLADU, nie z tej strony zdarzeń. `after_seq` jest
    # przyrostem dla listy wypowiedzi; karta, która powstała sto zdarzeń temu, ma być widoczna
    # także wtedy, gdy ekran prosi o ostatnie dziesięć. Dlatego osobny, ograniczony odczyt.
    whole = await store.events(
        organization_id=operator.organization_id, session_id=session_id, limit=MAX_TRACE_FOR_CARDS
    )
    return {
        "session": session_payload(session),
        "events": [event_payload(e) for e in events],
        # Resume only after events actually delivered, not after an unread page.
        "last_seq": events[-1].seq if events else min(max(0, after_seq), last),
        "latest_seq": last,
        "budzet": await _budget_payload(store, session),
        "karty": await cards_for(
            await pool_for(request.app),
            list(whole),
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=session.project_id,
        ),
        "postep": progress_for(list(whole)),
    }


async def _budget_payload(store: SessionStore, session: SessionRow) -> dict[str, Any]:
    """Ile z budżetu tej rozmowy zostało, policzone tak samo jak przy zatrzymaniu.

    LICZBY IDĄ NA EKRAN PRZED, A NIE PO. Założyciel zobaczył limit dopiero wtedy, gdy rozmowa
    się o niego rozbiła, i odczytał go jako limit swojego konta Codex. Pasek stanu z „12 z 30
    wymian" odpowiada na to pytanie zanim ktokolwiek je zada.

    Liczone przez tę samą funkcję, której używa worker (`sessions/handlers.py::budget_of`), żeby
    ekran i silnik nie miały dwóch zdań o jednej liczbie.
    """
    from control_plane.sessions.handlers import budget_of

    budget = budget_of(session)
    spent = await store.counts_for_budget(
        organization_id=session.organization_id, session_id=session.id
    )
    return {
        "tury": {"zuzyte": spent.get("turns", 0), "limit": budget.get("max_turns", 0)},
        "narzedzia": {
            "zuzyte": spent.get("tool_calls", 0),
            "limit": budget.get("max_tool_calls", 0),
        },
        "minuty_na_ture": budget.get("max_minutes", 0),
        # Czy limit pochodzi z ustawienia człowieka, czy z wartości domyślnej. Ekran ma to
        # powiedzieć wprost: limit, o którym nie wiadomo, kto go wybrał, wygląda na cudzy.
        # The marker of a plugin session sits in the same column and is not a limit anybody chose.
        "wlasny": any(key != HOST_BUDGET_KEY for key in session.budget),
    }


@router.post(SESSIONS_PATH + "/{session_id}/message", status_code=202)
async def add_message(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    body: MessageBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Say something to a running or waiting session.

    Accepted even while a turn is in flight, and that is the point: the message lands in the
    trace and the NEXT turn picks it up, because the handler sends everything the agent has not
    answered yet. Refusing during a turn would make the person wait to be allowed to speak.
    """
    session = await _resolve_session(request, operator, workspace_id, session_id)
    _refuse_host_session(session)
    if session.is_terminal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"sesja jest zakończona ({session.status}); zacznij nową zamiast dopisywać "
                "do zamkniętej"
            ),
        )
    store = await _store(request)
    event, added = await store.append_user_message(
        organization_id=operator.organization_id,
        session_id=session_id,
        text=body.text.strip(),
    )
    if not added:
        # The same message a moment ago (a double click): it is already in the trace and has
        # already asked for its turn, so nothing more happens.
        return {"accepted": True, "seq": event.seq, "repeated": True}
    await store.queue_followup(organization_id=operator.organization_id, session_id=session_id)
    await _audit(request, kind="AGENT_SESSION_MESSAGE_ADDED", operator=operator, session=session)
    await _ask_for_a_turn(request, session, reason=f"message-{event.seq}")
    return {"accepted": True, "seq": event.seq}


@router.post(SESSIONS_PATH + "/{session_id}/interrupt", status_code=202)
async def interrupt_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Ask a running turn to stop.

    Appends the event and returns. It does NOT set the status: whether the engine actually
    stopped is the turn's answer, and this route claiming PAUSED would be a state nobody
    measured. A session that is not running is marked PAUSED here, because there is no turn to
    ask and the person's intent is still recorded.
    """
    session = await _resolve_session(request, operator, workspace_id, session_id)
    _refuse_host_session(session)
    if session.is_terminal:
        raise HTTPException(status_code=409, detail=f"sesja jest zakończona ({session.status})")
    store = await _store(request)
    await store.append_event(
        organization_id=operator.organization_id,
        session_id=session_id,
        kind=EVENT_INTERRUPT,
        payload={"detail": "człowiek nacisnął przerwij"},
    )
    if session.status != "RUNNING":
        await store.set_status(
            session_id, organization_id=operator.organization_id, status=STATUS_PAUSED
        )
    await _audit(request, kind="AGENT_SESSION_INTERRUPTED", operator=operator, session=session)
    return {"accepted": True, "running": session.status == "RUNNING"}


@router.post(SESSIONS_PATH + "/{session_id}/continue", status_code=201)
async def continue_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Nowa rozmowa, która wie, z czego wyrosła.

    PO CO TO ISTNIEJE. Rozmowa, która doszła do limitu, kończyła się ekranem bez wyjścia:
    „Sesja jest zakończona. Zacznij nową, żeby powierzyć kolejny cel", i człowiek zostawał sam z
    pytaniem, gdzie ta nowa jest. Ta trasa robi ją jednym kliknięciem i zapisuje `parent_session_id`
    (0019), więc historia nie rwie się w miejscu, w którym rwał się budżet.

    NIE WZNAWIA STAREJ i nie podnosi jej limitu po cichu. Limit został wyczerpany naprawdę, a
    doliczenie tur do zamkniętej sesji zrobiłoby z budżetu liczbę, która nic nie znaczy. Nowa
    rozmowa dostaje budżet z ustawień projektu, czyli ten, który człowiek widzi na ekranie.
    """
    project = await _resolve_project(request, operator, workspace_id, project_id)
    parent = await _resolve_session(request, operator, workspace_id, session_id)
    if parent.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"nie ma sesji {session_id}")
    _refuse_host_session(parent)
    store = await _store(request)
    try:
        session = await store.create(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            goal=parent.goal,
            budget=project.conversation_budget or {},
            codex_connection_id=parent.codex_connection_id,
            parent_session_id=parent.id,
        )
    except SessionRefusal as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(request, kind="AGENT_SESSION_STARTED", operator=operator, session=session)
    await _ask_for_a_turn(request, session, reason="continued")
    return {"session": session_payload(session), "parent_session_id": str(parent.id)}


@router.post(SESSIONS_PATH + "/{session_id}/resume", status_code=202)
async def resume_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Start a new turn on a paused or waiting session, on the same engine thread."""
    session = await _resolve_session(request, operator, workspace_id, session_id)
    _refuse_host_session(session)
    if session.is_terminal and session.status != "FAILED":
        raise HTTPException(
            status_code=409,
            detail=f"sesja jest zakończona ({session.status}); wznowienie nie ma czego podjąć",
        )
    if session.status == "RUNNING":
        raise HTTPException(status_code=409, detail="sesja już pracuje")
    store = await _store(request)
    await store.set_status(
        session_id, organization_id=operator.organization_id, status=STATUS_QUEUED
    )
    await _audit(request, kind="AGENT_SESSION_RESUMED", operator=operator, session=session)
    await _ask_for_a_turn(request, session, reason=f"resume-{session.updated_at.timestamp():.0f}")
    return {"accepted": True}
