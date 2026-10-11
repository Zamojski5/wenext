"""`GET …/sessions/{id}/stream` — the trace of a running session, as it happens (P2 etap B).

WHY A STREAM AT ALL. A turn takes minutes and produces its trace one event at a time. Until now
the only way to see it was to reload, so the screen either looked frozen while the agent worked
or forced the person to poll by hand. What the milestone asks for is that the conversation
arrive without anybody refreshing.

WHY SERVER-SENT EVENTS AND NOT WEBSOCKETS. SSE is one HTTP GET with a text body: no new
dependency, no second protocol to secure, no upgrade path through the proxy, and the browser
reconnects on its own. The product needs one direction — the server tells, the screen listens —
and the other direction already exists as ordinary form POSTs. A WebSocket would buy nothing and
cost a protocol.

HOW IT KNOWS WHAT TO SEND. `session_events.seq` is dense and increasing per session (0015), so
the stream is a loop: read everything after the last sequence number sent, send it, sleep a
moment, repeat. The client says where it got to (`after_seq`, or the standard `Last-Event-ID`
header when the browser reconnects), so a dropped connection resumes exactly where it stopped
rather than replaying the conversation.

WHAT MAKES IT STOP, and this is the part that decides whether the mark on the screen lies:

  * the session reaches a status that is not RUNNING or QUEUED — the work is over, and the
    stream says so with a final `status` event instead of hanging open;
  * `MAX_STREAM_SECONDS` passes. A connection held for ever is a worker nobody gets back and a
     proxy timeout waiting to happen. THE DEADLINE IS NOT AN ENDING: the frame says `wznow` and
     the client opens the stream again from its last sequence number. It used to say `koniec`,
     and an independent audit on 2026-09-19 measured the consequence: a session still RUNNING
     after two minutes went quiet on the screen for ever, because the script read `koniec` as
     "the work is over". `koniec` now means one thing only, and it is the thing above;
  * the client goes away, which `await request.is_disconnected()` notices.

WHAT IT IS NOT. Not a push: this polls the database every `TICK_SECONDS` on behalf of one
reader. With a handful of people watching a handful of sessions that is cheaper than the
machinery to do otherwise (LISTEN/NOTIFY needs a connection per listener and a second failure
mode), and the number is written down here so it can be measured rather than guessed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.sessions import STATUS_QUEUED, STATUS_RUNNING, SessionStore
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["sessions"])

STREAM_PATH = "/workspaces/{workspace_id}/projects/{project_id}/sessions/{session_id}/stream"

#: How often the loop looks for new events. Half a second: fast enough that a person reading the
#: screen sees the agent working, slow enough that ten open screens are twenty queries a second
#: against an indexed key.
TICK_SECONDS = 0.5

#: How long one connection lives. Two minutes, then the client reconnects with its last sequence
#: number. A stream held open for ever outlives proxies, restarts and patience.
#:
#: `WENEXT_SESSION_STREAM_SECONDS` shortens it for a test that has to SEE the reconnect: waiting
#: two real minutes in an end-to-end suite is not a test anybody runs, and a deadline nobody
#: exercises is how this bug survived review in the first place.
MAX_STREAM_SECONDS = 120.0

STREAM_SECONDS_VARIABLE = "WENEXT_SESSION_STREAM_SECONDS"


def _stream_seconds() -> float:
    """The deadline of THIS connection. Read per request, so a test can set it without a restart."""
    import os

    raw = os.environ.get(STREAM_SECONDS_VARIABLE, "").strip()
    if not raw:
        return MAX_STREAM_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return MAX_STREAM_SECONDS
    # A non-positive deadline would close every stream instantly and look like a broken proxy.
    return seconds if seconds > 0 else MAX_STREAM_SECONDS


#: How many events one tick may send. A turn that wrote two hundred events while the reader was
#: away catches up in pages rather than in one frame nobody can parse.
MAX_EVENTS_PER_TICK = 100

#: The statuses that mean "there is more to come". Anything else ends the stream, because a
#: stream still open on a finished session is what makes a screen say "pracuje" for ever.
LIVE_STATUSES = frozenset({STATUS_RUNNING, STATUS_QUEUED})


def _frame(event: str, data: dict[str, Any], *, ident: int | None = None) -> str:
    """One SSE frame. `id:` is what the browser sends back as `Last-Event-ID` on reconnect."""
    lines = []
    if ident is not None:
        lines.append(f"id: {ident}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


async def _resolve(
    request: Request, operator: OperatorSession, workspace_id: UUID, session_id: UUID
):
    """The same two gates as `api/sessions.py`: the workspace, then the row.

    Resolved BEFORE the stream opens. A 404 has to be a 404, and a stream that opened and then
    sent an error frame would look to the browser like a working connection to nothing.
    """
    pool = await pool_for(request.app)
    workspace = await WorkspaceStore(pool).workspace(
        organization_id=operator.organization_id, workspace_id=workspace_id
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    store = SessionStore(pool)
    session = await store.get(organization_id=operator.organization_id, session_id=session_id)
    if session is None or session.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"nie ma sesji {session_id}")
    return store, session


#: Co ile obrotów pętli liczyć odcisk kart, gdy w rozmowie nic się nie dzieje. Przy
#: `TICK_SECONDS = 0.5` sześć obrotów to trzy sekundy opóźnienia dla zmiany, która przyszła
#: spoza rozmowy, i dwadzieścia odczytów na minutę na otwartą rozmowę. Liczba jest DECYZJĄ, nie
#: przypadkiem: stoi tu opisana, żeby nikt nie szukał usterki w opóźnieniu, które jest wyborem.
_STAMP_EVERY = 6


async def _cards_stamp(
    request: Request,
    store: Any,
    operator: OperatorSession,
    session_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
) -> str:
    """Odcisk kart TEJ sesji: `karta_id:stan` po kolei, sklejone `|`.

    Liczony tylko wtedy, gdy w tym obrocie pętli przyszły nowe zdarzenia: bez zdarzeń nie ma jak
    zmienić się stan karty, więc odczyt byłby zapytaniem o nic co sekundę.
    """
    from control_plane.sessions.projection import cards_for
    from control_plane.tools.cards import fingerprint

    events = await store.events(
        organization_id=operator.organization_id, session_id=session_id, limit=500
    )
    cards = await cards_for(
        await pool_for(request.app),
        list(events),
        organization_id=operator.organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return fingerprint(cards)


@router.get(STREAM_PATH)
async def stream_session(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    after_seq: int = 0,
    last_event_id: Annotated[str | None, Header(alias="last-event-id")] = None,
) -> StreamingResponse:
    """The trace from `after_seq` on, as it is written. Ends when the session stops running."""
    store, session = await _resolve(request, operator, workspace_id, session_id)
    if session.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"nie ma sesji {session_id}")

    # `Last-Event-ID` wins when present: it is the browser telling us where its own reconnect
    # left off, which is more trustworthy than a query parameter copied into a link.
    start = after_seq
    if last_event_id is not None and last_event_id.strip().isdigit():
        start = int(last_event_id.strip())

    async def frames() -> AsyncIterator[str]:
        seen = max(0, start)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _stream_seconds()
        # Odcisk kart z poprzedniego obrotu pętli. Strumień NIE niesie kart, tylko ten napis
        # (uzgodnione z ekranem 20.09): karty rysuje serwer z projekcji, a przeglądarka po
        # zmianie odcisku przeładowuje stronę. Druga kopia reguł rysowania w skrypcie ekranu
        # byłaby miejscem, w którym ekran zaczyna pisać „Połączono” wcześniej niż serwer.
        stamp: str | None = None
        ticks = 0
        # The status is sent FIRST, before any event: a screen that connects to a session which
        # finished while it was away must learn that immediately, not after a two-minute silence.
        current = await store.get(organization_id=operator.organization_id, session_id=session_id)
        if current is not None:
            yield _frame("status", {"status": current.status, "session_id": str(session_id)})

        last_status = current.status if current is not None else None
        while True:
            if await request.is_disconnected():
                return
            events = await store.events(
                organization_id=operator.organization_id,
                session_id=session_id,
                after_seq=seen,
                limit=MAX_EVENTS_PER_TICK,
            )
            for event in events:
                seen = event.seq
                yield _frame(
                    "event",
                    {
                        "seq": event.seq,
                        "kind": event.kind,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    },
                    ident=event.seq,
                )

            # ODCISK LICZY SIĘ TAKŻE BEZ NOWYCH ZDARZEŃ, i to jest cały sens tej mechaniki.
            # Stan karty bierze się z PRODUKTU, nie ze śladu: człowiek kończy logowanie w drugiej
            # zakładce i w tej rozmowie nie dzieje się nic, a karta ma przestać pokazywać „Łączę”.
            # Wersja licząca odcisk tylko przy nowych zdarzeniach przegapiała dokładnie ten
            # przypadek, dla którego powstała (złapane przez wenext-ce w przeglądzie, 20.09).
            ticks += 1
            if events or ticks % _STAMP_EVERY == 0:
                fresh_stamp = await _cards_stamp(
                    request, store, operator, session_id, workspace_id, session.project_id
                )
                if fresh_stamp != stamp:
                    stamp = fresh_stamp
                    yield _frame("karty", {"odcisk": stamp})

            fresh = await store.get(organization_id=operator.organization_id, session_id=session_id)
            status = fresh.status if fresh is not None else "NIEZNANY"
            if status != last_status:
                yield _frame("status", {"status": status, "session_id": str(session_id)})
                last_status = status
            if status not in LIVE_STATUSES and not events:
                # Finished, and nothing left to hand over. The last frame says so, so the screen
                # can stop the animation instead of waiting for a silence it cannot interpret.
                yield _frame("status", {"status": status, "session_id": str(session_id)})
                yield _frame("koniec", {"reason": "session-not-running", "last_seq": seen})
                return
            if loop.time() >= deadline:
                # NOT `koniec`. The session is still running; only this CONNECTION is over, and
                # the client is told to open another one from `last_seq`. The name of the frame
                # is the whole fix: a client that cannot tell "connect again" from "the work
                # ended" stops showing a turn that is still going (audyt 2026-09-19).
                yield _frame("wznow", {"reason": "deadline", "last_seq": seen})
                return
            await asyncio.sleep(TICK_SECONDS)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            # No buffering anywhere: a proxy that collects the body defeats the whole point, and
            # `X-Accel-Buffering` is what nginx reads.
            "cache-control": "no-store",
            "x-accel-buffering": "no",
            "connection": "keep-alive",
        },
    )
