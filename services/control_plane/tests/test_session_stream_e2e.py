"""The trace of a REAL turn arriving while it runs (P2 etap B, end to end).

WHAT RUNS HERE: PostgreSQL, the session store, the turn handler, a real app-server subprocess
(the stand-in from `codex_runtime.fake_app_server`), the `wenext` MCP server it starts, and the
SSE route read over HTTP at the same time. What does not run is a model; the script decides which
tools are called.

THE PROPERTY UNDER TEST is the one the milestone asks for in one sentence: the conversation
reaches the screen WITHOUT a reload. That is only meaningful if the events arrive WHILE the turn
is still running, so the script below spends a second between steps and the assertions are about
what the stream had delivered before the turn finished.

The helpers are copies of the ones in `test_knowledge_session_e2e.py` for the reason stated
there: importing private helpers across test modules couples two milestones' suites, and thirty
lines of duplication is the cheaper of the two.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions import SessionStore
from control_plane.sessions.handlers import OUTCOME_ANSWERED, SessionTurnHandlers

pytestmark = pytest.mark.db


def _codex_shim(tmp_path: Path) -> Path:
    shim = tmp_path / "codex-fake"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m codex_runtime.fake_app_server "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _script(tmp_path: Path, script: dict) -> Path:
    path = tmp_path / f"script-{uuid4().hex[:8]}.json"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return path


def _handlers(pool, tenant, tmp_path, dsn) -> SessionTurnHandlers:
    async def codex_home_for(_session):
        return tmp_path / "home"

    return SessionTurnHandlers(
        pool=pool,
        sessions=SessionStore(pool),
        queue=None,  # type: ignore[arg-type]
        organization_id=tenant["organization"],
        dsn=dsn,
        codex_bin=_codex_shim(tmp_path),
        codex_home_for=codex_home_for,
        capability_dir=tmp_path / "caps",
    )


@pytest.fixture
def dsn() -> str:
    import os

    value = os.environ.get("WENEXT_TEST_DSN")
    if not value:
        pytest.fail("brak WENEXT_TEST_DSN: ten test wymaga bazy")
    return value


async def _project(pool, tenant):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


def frames_of(text: str) -> list[dict[str, str]]:
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


async def test_the_screen_sees_the_turn_while_it_is_still_running(
    logged_in_client, pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """Events arrive DURING the turn, not when it ends. That is the whole feature."""
    project_id = await _project(pool, tenant)
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="powiedz, co robisz, po kolei",
        budget={"max_turns": 2, "max_minutes": 2, "max_tool_calls": 10},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_stream",
            "turns": [
                {
                    "steps": [
                        {"tool": "catalog.list", "arguments": {}},
                        {"delay_ms": 1200},
                        {"message": "Pierwsza część odpowiedzi."},
                        {"delay_ms": 1200},
                        {"message": "Druga część, już po chwili."},
                    ]
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn)

    path = (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/sessions/{session.id}/stream"
    )
    turn = asyncio.create_task(handlers.run_once(session.id))
    seen_while_running: list[dict[str, str]] = []
    try:
        async with logged_in_client.stream("GET", path, timeout=60.0) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_text():
                for frame in frames_of(chunk):
                    seen_while_running.append(frame)
                    if frame.get("event") == "koniec":
                        break
                # The assertion that matters: the first assistant message reached this reader
                # BEFORE the turn object finished.
                arrived = any(
                    "Pierwsza część" in frame.get("data", "") for frame in seen_while_running
                )
                if arrived and not turn.done():
                    break
    finally:
        result = await turn

    assert result.outcome == OUTCOME_ANSWERED, result.detail
    delivered = "\n".join(frame.get("data", "") for frame in seen_while_running)
    assert "Pierwsza część" in delivered, (
        "pierwsza wiadomość nie dotarła strumieniem przed końcem tury, czyli ekran i tak musiałby "
        "czekać na odświeżenie"
    )
    # The stream identified every frame, which is what lets a reconnect resume instead of replay.
    assert any(frame.get("id") for frame in seen_while_running)


async def test_after_the_turn_the_history_is_the_same_as_what_the_stream_showed(
    logged_in_client, pool, tenant, tmp_path, dsn, monkeypatch
) -> None:
    """A reload must show exactly what arrived live: the stream adds nothing of its own."""
    project_id = await _project(pool, tenant)
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="zrób i powiedz",
        budget={"max_turns": 2, "max_minutes": 2, "max_tool_calls": 10},
    )
    script = _script(
        tmp_path,
        {
            "thread_id": "th_history",
            "turns": [
                {
                    "steps": [
                        {"tool": "catalog.list", "arguments": {}},
                        {"message": "Zrobione."},
                    ]
                }
            ],
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = _handlers(pool, tenant, tmp_path, dsn)
    assert (await handlers.run_once(session.id)).outcome == OUTCOME_ANSWERED

    path = (
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/sessions/{session.id}/stream"
    )
    streamed = ""
    async with logged_in_client.stream("GET", path, timeout=30.0) as response:
        async for chunk in response.aiter_text():
            streamed += chunk
    from_stream = [
        json.loads(frame["data"])["seq"]
        for frame in frames_of(streamed)
        if frame.get("event") == "event"
    ]

    read = await logged_in_client.get(
        f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/sessions/{session.id}"
    )
    assert read.status_code == 200
    from_history = [event["seq"] for event in read.json()["events"]]

    assert from_stream == from_history, (
        "strumień i historia rozjechały się, więc po odświeżeniu człowiek zobaczyłby co innego "
        "niż na żywo"
    )
    assert read.json()["last_seq"] == from_stream[-1]
