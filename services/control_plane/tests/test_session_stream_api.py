"""The live trace of a session, against PostgreSQL (P2 etap B).

WHAT IS BEING PROTECTED. A stream is easy to write and easy to get wrong in ways that are
invisible until somebody watches a real turn:

  * it replays the whole conversation on every reconnect, so a reader sees the same answer three
    times and cannot tell which is new;
  * it stays open after the session finished, so the screen says "pracuje" for ever;
  * it never ends by itself, so a proxy closes it and the screen goes quiet with no explanation;
  * it ENDS BY ITSELF AND SAYS THE WORK IS OVER, which is the same silence wearing a label. An
    independent audit measured this one on 2026-09-19: the deadline sent `koniec`, the script
    believed it, and a session still RUNNING stopped updating after two minutes;
  * it streams somebody else's session, because the gate was checked once and not per row.

Every case below is one of those. The stream is read as raw SSE text, the way a browser would.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from control_plane.api.session_stream import (
    MAX_STREAM_SECONDS,
    STREAM_SECONDS_VARIABLE,
    TICK_SECONDS,
)
from control_plane.sessions import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_STATUS,
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    SessionStore,
)

pytestmark = pytest.mark.db


async def _project(pool, tenant) -> uuid.UUID:
    async with pool.acquire() as connection:
        return await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid.uuid4().hex[:8]}",
        )


async def _session(pool, tenant, project_id, *, status: str = STATUS_RUNNING):
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="sprawdź, co się dzieje",
        budget={"max_turns": 3},
    )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE agent_sessions SET status = $2 WHERE id = $1", session.id, status
        )
    return session


def stream_path(tenant, project_id, session_id) -> str:
    return (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/sessions/{session_id}/stream"
    )


def frames_of(text: str) -> list[dict[str, str]]:
    """The raw SSE text, parsed the way a browser parses it."""
    frames = []
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        frame: dict[str, str] = {}
        for line in chunk.splitlines():
            key, _, value = line.partition(": ")
            frame[key] = value
        frames.append(frame)
    return frames


async def read_stream(client, path: str, *, timeout: float = 10.0, **params) -> str:
    """Read the whole stream until the server closes it."""
    collected = ""
    async with client.stream("GET", path, params=params, timeout=timeout) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for chunk in response.aiter_text():
            collected += chunk
    return collected


async def test_a_finished_session_streams_its_trace_once_and_closes(
    logged_in_client, pool, tenant
) -> None:
    """The base case, and the one that proves the stream ENDS: nothing is running any more."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status=STATUS_WAITING_USER)
    store = SessionStore(pool)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_ASSISTANT_MESSAGE,
        payload={"text": "gotowe"},
    )

    text = await read_stream(logged_in_client, stream_path(tenant, project_id, session.id))

    frames = frames_of(text)
    kinds = [frame["event"] for frame in frames]
    assert kinds[0] == "status", kinds
    assert "event" in kinds, "ślad nie dotarł do strumienia"
    assert kinds[-1] == "koniec", kinds
    assert "session-not-running" in frames[-1]["data"]
    # The trace opens with the goal the person entrusted (`SessionStore.create` writes it), so
    # two events here: that one and the answer. Each exactly once — a stream that replays is a
    # stream whose reader cannot tell new from old.
    identifiers = [int(frame["id"]) for frame in frames if frame.get("id")]
    assert identifiers == [1, 2], identifiers
    assert "gotowe" in text


async def test_events_written_while_the_stream_is_open_arrive_without_a_reload(
    logged_in_client, pool, tenant
) -> None:
    """THE property the milestone asks for: the conversation arrives while the turn runs."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status=STATUS_RUNNING)
    store = SessionStore(pool)

    async def write_later() -> None:
        await asyncio.sleep(TICK_SECONDS * 2)
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_STATUS,
            payload={"detail": "w trakcie"},
        )
        await asyncio.sleep(TICK_SECONDS * 2)
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_ASSISTANT_MESSAGE,
            payload={"text": "odpowiedź po chwili"},
        )
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE agent_sessions SET status = $2 WHERE id = $1",
                session.id,
                STATUS_WAITING_USER,
            )

    writer = asyncio.create_task(write_later())
    try:
        text = await read_stream(
            logged_in_client, stream_path(tenant, project_id, session.id), timeout=20.0
        )
    finally:
        await writer

    assert "w trakcie" in text
    assert "odpowiedź po chwili" in text
    frames = frames_of(text)
    assert frames[-1]["event"] == "koniec"
    # The status frames bracket the work: RUNNING when it opened, not RUNNING at the end.
    statuses = [frame["data"] for frame in frames if frame["event"] == "status"]
    assert STATUS_RUNNING in statuses[0]
    assert STATUS_WAITING_USER in statuses[-1]


async def test_a_reconnect_resumes_and_does_not_replay(logged_in_client, pool, tenant) -> None:
    """`Last-Event-ID` is the browser saying where it got to. Ignoring it means every reconnect
    shows the whole conversation again."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status=STATUS_WAITING_USER)
    store = SessionStore(pool)
    for index in range(3):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_ASSISTANT_MESSAGE,
            payload={"text": f"wiadomość {index}"},
        )

    path = stream_path(tenant, project_id, session.id)
    everything = frames_of(await read_stream(logged_in_client, path))
    identifiers = [int(frame["id"]) for frame in everything if frame.get("id")]
    # One for the goal, three for the messages: the trace starts with what the person asked for.
    assert identifiers == [1, 2, 3, 4], identifiers

    # Reconnecting with the header the browser sends.
    async with logged_in_client.stream(
        "GET", path, headers={"last-event-id": "3"}, timeout=10.0
    ) as response:
        rest = ""
        async for chunk in response.aiter_text():
            rest += chunk
    resumed = [int(frame["id"]) for frame in frames_of(rest) if frame.get("id")]
    assert resumed == [4], resumed

    # And the query parameter does the same thing for a client that is not a browser.
    by_query = await read_stream(logged_in_client, path, after_seq=3)
    assert [int(f["id"]) for f in frames_of(by_query) if f.get("id")] == [4]


async def test_a_session_of_another_workspace_is_a_404_and_not_an_open_stream(
    logged_in_client, pool, tenant
) -> None:
    """The gate is resolved BEFORE the stream opens: an error frame on an open connection looks
    to the browser like a working stream of nothing."""
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    async with pool.acquire() as connection:
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'Obca') RETURNING id",
            tenant["organization"],
        )

    answer = await logged_in_client.get(
        f"/api/workspaces/{other_workspace}/projects/{project_id}/sessions/{session.id}/stream"
    )

    assert answer.status_code == 404
    assert "text/event-stream" not in answer.headers.get("content-type", "")


async def test_a_stream_without_a_session_is_refused_like_every_other_route(
    client, pool, tenant
) -> None:
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id)
    answer = await client.get(stream_path(tenant, project_id, session.id))
    assert answer.status_code == 401
    assert answer.content == b""


def test_the_stream_has_a_deadline_and_it_is_shorter_than_a_proxy_timeout() -> None:
    """A connection held for ever is a worker nobody gets back. Two minutes, said in a constant."""
    assert 30.0 <= MAX_STREAM_SECONDS <= 300.0
    assert TICK_SECONDS <= 1.0, "wolniejszy tick i ekran przestaje wyglądać na żywy"


async def test_the_deadline_asks_for_a_reconnect_and_never_says_the_work_ended(
    logged_in_client, pool, tenant, monkeypatch
) -> None:
    """Sedno poprawki z audytu 19.09: sesja NADAL pracuje, więc kończy się połączenie, nie praca.

    Limit skrócony zmienną środowiskową, bo test czekający dwie minuty to test, którego nikt nie
    uruchamia, a niesprawdzany limit czasu jest dokładnie tym, przez co ten błąd przeszedł
    przegląd.
    """
    monkeypatch.setenv(STREAM_SECONDS_VARIABLE, str(TICK_SECONDS * 3))
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status=STATUS_RUNNING)

    text = await read_stream(
        logged_in_client, stream_path(tenant, project_id, session.id), timeout=20.0
    )

    frames = frames_of(text)
    assert frames[-1]["event"] == "wznow", frames
    assert "deadline" in frames[-1]["data"]
    assert [frame for frame in frames if frame["event"] == "koniec"] == [], (
        "sesja nadal jest RUNNING, a `koniec` znaczy, że praca się skończyła"
    )
    # Status nie kłamie: ostatnie, co ekran usłyszał o stanie, to RUNNING.
    statuses = [frame["data"] for frame in frames if frame["event"] == "status"]
    assert statuses and STATUS_RUNNING in statuses[-1]


async def test_after_the_deadline_a_reconnect_keeps_receiving_the_running_session(
    logged_in_client, pool, tenant, monkeypatch
) -> None:
    """Co ma się zdarzyć PO wznowieniu: nowe zdarzenia pracującej sesji nadal docierają."""
    monkeypatch.setenv(STREAM_SECONDS_VARIABLE, str(TICK_SECONDS * 3))
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status=STATUS_RUNNING)
    store = SessionStore(pool)
    path = stream_path(tenant, project_id, session.id)

    first = frames_of(await read_stream(logged_in_client, path, timeout=20.0))
    assert first[-1]["event"] == "wznow"
    seen = max((int(frame["id"]) for frame in first if frame.get("id")), default=0)
    assert seen >= 1, first

    # Agent pisze dalej, bo tura trwa. Tego właśnie ekran nie zobaczył przed poprawką.
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_ASSISTANT_MESSAGE,
        payload={"text": "zdanie po wznowieniu"},
    )

    async with logged_in_client.stream(
        "GET", path, headers={"last-event-id": str(seen)}, timeout=20.0
    ) as response:
        resumed = ""
        async for chunk in response.aiter_text():
            resumed += chunk

    assert "zdanie po wznowieniu" in resumed
    identifiers = [int(frame["id"]) for frame in frames_of(resumed) if frame.get("id")]
    assert identifiers == [seen + 1], identifiers
    assert frames_of(resumed)[-1]["event"] == "wznow", (
        "wciąż RUNNING, więc znowu prośba o wznowienie"
    )


def test_a_broken_deadline_variable_falls_back_to_the_written_one(monkeypatch) -> None:
    """Zła wartość w środowisku nie może zamknąć strumienia natychmiast ani otworzyć go na zawsze."""
    from control_plane.api.session_stream import _stream_seconds

    for value in ("", "   ", "nie liczba", "0", "-5"):
        monkeypatch.setenv(STREAM_SECONDS_VARIABLE, value)
        assert _stream_seconds() == MAX_STREAM_SECONDS, value
    monkeypatch.setenv(STREAM_SECONDS_VARIABLE, "2.5")
    assert _stream_seconds() == 2.5


async def test_queue_to_running_is_reported_without_waiting_for_reconnect(
    logged_in_client, pool, tenant
):
    project_id = await _project(pool, tenant)
    session = await _session(pool, tenant, project_id, status="QUEUED")

    async def transition():
        for status in (STATUS_RUNNING, STATUS_WAITING_USER):
            await asyncio.sleep(TICK_SECONDS * 2)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agent_sessions SET status=$2 WHERE id=$1", session.id, status
                )

    writer = asyncio.create_task(transition())
    try:
        text = await read_stream(
            logged_in_client, stream_path(tenant, project_id, session.id), timeout=20.0
        )
    finally:
        await writer
    statuses = [frame["data"] for frame in frames_of(text) if frame["event"] == "status"]
    assert "QUEUED" in statuses[0]
    assert any(STATUS_RUNNING in status for status in statuses[1:-1])
    assert STATUS_WAITING_USER in statuses[-1]
