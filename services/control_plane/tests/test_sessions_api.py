"""HTTP for agent sessions (P2): the gate, the queue, and what the screen reads back.

WHAT THESE TESTS ARE FOR, and it is not the agent. No turn runs here: every route that should
make the agent work enqueues a job and returns, so what is measured is the boundary (an id from
another organization is absent, not forbidden), the contract with the queue (a job appears, with
a key that does not swallow a second message), and the shape the screen reads.

THE ONE THING WORTH STATING TWICE: `after_seq`. The screen holds the number of the last event it
rendered and asks for what came after. That number lives in the trace, not in the browser and
not in the worker, which is exactly why a restart of either recovers without losing anything.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = [pytest.mark.db, pytest.mark.p2]

WORKSPACES = "/api/workspaces"
GOAL = "sprawdź, dlaczego odzew jest mały, i zaproponuj zmianę"


async def _workspace(client, name: str = "Przestrzeń P2") -> str:
    response = await client.post(WORKSPACES, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["workspace"]["workspace_id"]


async def _project(client, workspace_id: str, name: str = "Projekt P2") -> str:
    response = await client.post(f"{WORKSPACES}/{workspace_id}/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _sessions_path(workspace_id: str, project_id: str) -> str:
    return f"{WORKSPACES}/{workspace_id}/projects/{project_id}/sessions"


async def _start(client, workspace_id: str, project_id: str, **body) -> dict:
    response = await client.post(
        _sessions_path(workspace_id, project_id), json={"goal": GOAL, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


async def _jobs(pool, organization_id) -> list[tuple[str, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT job_type, idempotency_key FROM jobs WHERE organization_id = $1 "
            "AND job_type = 'session.turn' ORDER BY created_at",
            organization_id,
        )
    return [(r["job_type"], r["idempotency_key"]) for r in rows]


# ---------------------------------------------------------------------------
# Starting one
# ---------------------------------------------------------------------------


async def test_a_goal_creates_a_session_queued_with_a_job_and_its_goal_in_the_trace(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)

    session = await _start(logged_in_client, workspace_id, project_id)

    assert session["status"] == "QUEUED"
    assert session["goal"] == GOAL
    assert session["engine_thread_id"] is None, (
        "sesja ma wątek silnika, zanim cokolwiek ruszyło; ekran pokaże „pracuje” bez pracy"
    )
    jobs = await _jobs(pool, tenant["organization"])
    assert [key for _type, key in jobs] == [f"session-turn:{session['session_id']}:created"], jobs

    read = await logged_in_client.get(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    )
    assert read.status_code == 200
    body = read.json()
    assert [e["kind"] for e in body["events"]] == ["USER_MESSAGE"]
    assert body["events"][0]["payload"]["text"] == GOAL
    assert body["last_seq"] == 1


async def test_an_empty_goal_is_refused_before_anything_is_created(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)

    response = await logged_in_client.post(
        _sessions_path(workspace_id, project_id), json={"goal": "   "}
    )

    assert response.status_code in (400, 422), response.text
    assert await _jobs(pool, tenant["organization"]) == []


async def test_a_session_of_another_organization_is_a_404_not_a_403(logged_in_client, pool) -> None:
    """Absent, not forbidden. A 403 would tell the caller which identifiers are real."""
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    mine = await _start(logged_in_client, workspace_id, project_id)

    async with pool.acquire() as conn:
        stranger = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"obca-{uuid.uuid4()}"
        )
        foreign_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            stranger,
        )
        foreign_project = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'obcy') "
            "RETURNING id",
            stranger,
            foreign_workspace,
        )
        foreign_session = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cudzy cel') RETURNING id",
            stranger,
            foreign_workspace,
            foreign_project,
        )

    # The foreign session, addressed through its own (foreign) path and through mine.
    assert (
        await logged_in_client.get(
            f"{WORKSPACES}/{foreign_workspace}/projects/{foreign_project}/sessions/"
            f"{foreign_session}"
        )
    ).status_code == 404
    assert (
        await logged_in_client.get(f"{_sessions_path(workspace_id, project_id)}/{foreign_session}")
    ).status_code == 404
    assert (
        await logged_in_client.get(f"{_sessions_path(workspace_id, project_id)}/{uuid.uuid4()}")
    ).status_code == 404
    # And mine still reads, so the test is not passing because everything 404s.
    assert (
        await logged_in_client.get(
            f"{_sessions_path(workspace_id, project_id)}/{mine['session_id']}"
        )
    ).status_code == 200


async def test_a_project_of_another_workspace_cannot_host_a_session(logged_in_client) -> None:
    first = await _workspace(logged_in_client, "Pierwsza")
    second = await _workspace(logged_in_client, "Druga")
    project_in_second = await _project(logged_in_client, second, "Projekt drugiej")

    response = await logged_in_client.post(
        _sessions_path(first, project_in_second), json={"goal": GOAL}
    )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Talking to one
# ---------------------------------------------------------------------------


async def test_a_message_lands_in_the_trace_and_asks_for_a_turn(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)

    response = await logged_in_client.post(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}/message",
        json={"text": "skróć i podaj źródło"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["seq"] == 2
    keys = [key for _t, key in await _jobs(pool, tenant["organization"])]
    assert keys == [
        f"session-turn:{session['session_id']}:created",
        f"session-turn:{session['session_id']}:message-2",
    ], (
        f"klucze jobów: {keys}. Klucz z samej sesji sprawiłby, że druga wiadomość znika w tym "
        "samym jobie, co pierwsza"
    )


async def test_two_messages_produce_two_jobs_and_neither_swallows_the_other(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}/message"

    await logged_in_client.post(path, json={"text": "pierwsza uwaga"})
    await logged_in_client.post(path, json={"text": "druga uwaga"})

    assert len(await _jobs(pool, tenant["organization"])) == 3
    read = await logged_in_client.get(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    )
    texts = [e["payload"].get("text") for e in read.json()["events"]]
    assert texts == [GOAL, "pierwsza uwaga", "druga uwaga"]


async def test_a_double_click_on_send_is_one_message_and_one_turn(
    logged_in_client, pool, tenant
) -> None:
    """Measured 27.09: the founder's direction reached the trace twice, 25 ms apart, from one
    send. The two requests race, so they are fired at the same time here."""
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}/message"

    first, second = await asyncio.gather(
        logged_in_client.post(path, json={"text": "zmiana tempa nauki"}),
        logged_in_client.post(path, json={"text": "zmiana tempa nauki"}),
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["seq"] == second.json()["seq"]
    read = await logged_in_client.get(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    )
    texts = [e["payload"].get("text") for e in read.json()["events"]]
    assert texts == [GOAL, "zmiana tempa nauki"]
    assert len(await _jobs(pool, tenant["organization"])) == 2, "jedna tura na jedną wiadomość"


async def test_the_same_words_said_again_later_are_a_new_message(pool, tenant) -> None:
    """The window is for a double click, not for a person who repeats themselves."""
    from datetime import timedelta

    from control_plane.sessions import SessionStore

    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, 'powtórka', 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
    store = SessionStore(pool)
    session_id = (
        await store.create(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            goal="cel",
            budget={},
        )
    ).id

    _first, added = await store.append_user_message(
        organization_id=tenant["organization"], session_id=session_id, text="jeszcze raz"
    )
    _again, added_again = await store.append_user_message(
        organization_id=tenant["organization"],
        session_id=session_id,
        text="jeszcze raz",
        window=timedelta(0),
    )

    assert added and added_again, "po oknie ta sama treść to nowa wiadomość"


async def test_the_screen_catches_up_from_the_number_it_last_saw(logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"

    first = (await logged_in_client.get(path)).json()
    assert first["last_seq"] == 1

    await logged_in_client.post(f"{path}/message", json={"text": "jeszcze jedno"})

    catch_up = (await logged_in_client.get(f"{path}?after_seq={first['last_seq']}")).json()
    assert [e["seq"] for e in catch_up["events"]] == [2]
    assert catch_up["last_seq"] == 2
    # Asking again with the same number returns nothing new, which is what a poll must do.
    assert (await logged_in_client.get(f"{path}?after_seq=2")).json()["events"] == []


async def test_long_conversation_cursor_never_skips_an_unread_page(
    logged_in_client, pool, tenant
) -> None:
    from control_plane.api.sessions import MAX_EVENTS
    from control_plane.sessions.store import SessionStore

    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    store = SessionStore(pool)
    for index in range(MAX_EVENTS + 4):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=uuid.UUID(session["session_id"]),
            kind="ASSISTANT_MESSAGE",
            payload={"text": f"Message {index}"},
        )
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    first = (await logged_in_client.get(path)).json()
    assert len(first["events"]) == MAX_EVENTS
    assert first["last_seq"] == MAX_EVENTS
    assert first["latest_seq"] == MAX_EVENTS + 5
    second = (await logged_in_client.get(f"{path}?after_seq={first['last_seq']}")).json()
    assert [e["seq"] for e in second["events"]] == list(range(MAX_EVENTS + 1, MAX_EVENTS + 6))
    assert second["last_seq"] == second["latest_seq"] == MAX_EVENTS + 5


# ---------------------------------------------------------------------------
# Stopping and resuming
# ---------------------------------------------------------------------------


async def test_interrupt_records_the_intent_and_does_not_claim_the_engine_stopped(
    logged_in_client, pool
) -> None:
    """The route appends the event and returns. Whether the engine stopped is the turn's answer,
    and a route claiming PAUSED for a RUNNING session would be a state nobody measured."""
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status = 'RUNNING' WHERE id = $1",
            uuid.UUID(session["session_id"]),
        )

    response = await logged_in_client.post(f"{path}/interrupt")

    assert response.status_code == 202 and response.json()["running"] is True
    body = (await logged_in_client.get(path)).json()
    assert body["session"]["status"] == "RUNNING", (
        "trasa ogłosiła zatrzymanie, choć tura biegnie w innym procesie i nikt jej nie pytał"
    )
    assert [e["kind"] for e in body["events"]][-1] == "INTERRUPT"


async def test_interrupting_a_session_that_is_not_running_pauses_it(logged_in_client) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"

    response = await logged_in_client.post(f"{path}/interrupt")

    assert response.status_code == 202 and response.json()["running"] is False
    assert (await logged_in_client.get(path)).json()["session"]["status"] == "PAUSED"


async def test_resume_queues_a_turn_and_refuses_a_session_that_is_already_running(
    logged_in_client, pool, tenant
) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    await logged_in_client.post(f"{path}/interrupt")

    resumed = await logged_in_client.post(f"{path}/resume")
    assert resumed.status_code == 202
    assert (await logged_in_client.get(path)).json()["session"]["status"] == "QUEUED"
    assert len(await _jobs(pool, tenant["organization"])) >= 2

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status = 'RUNNING' WHERE id = $1",
            uuid.UUID(session["session_id"]),
        )
    again = await logged_in_client.post(f"{path}/resume")
    assert again.status_code == 409 and "pracuje" in again.json()["detail"]


async def test_a_finished_session_takes_no_more_messages(logged_in_client, pool) -> None:
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status = 'DONE', finished_at = now() WHERE id = $1",
            uuid.UUID(session["session_id"]),
        )

    for route, body in ((f"{path}/message", {"text": "halo"}), (f"{path}/resume", None)):
        response = (
            await logged_in_client.post(route, json=body)
            if body
            else (await logged_in_client.post(route))
        )
        assert response.status_code == 409, route


async def test_every_action_on_a_session_leaves_an_audit_row_with_identifiers_only(
    logged_in_client, pool, tenant
) -> None:
    """The goal and the person's words are prose and belong in the trace. `audit_events` is
    append-only and its payload allowlist has no free-text key, on purpose."""
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    path = f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}"
    await logged_in_client.post(f"{path}/message", json={"text": "uwaga"})
    await logged_in_client.post(f"{path}/interrupt")
    await logged_in_client.post(f"{path}/resume")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT kind, payload FROM audit_events WHERE subject_ref = $1 ORDER BY occurred_at",
            f"session:{session['session_id']}",
        )
    kinds = [r["kind"] for r in rows]
    assert kinds == [
        "AGENT_SESSION_STARTED",
        "AGENT_SESSION_MESSAGE_ADDED",
        "AGENT_SESSION_INTERRUPTED",
        "AGENT_SESSION_RESUMED",
    ], kinds
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        assert set(payload) == {"session_id", "project_id"}, payload
        assert GOAL not in str(payload), "cel wyciekł do dziennika audytu"


@pytest.mark.parametrize(
    "before,after", [("WAITING_USER", "QUEUED"), ("PAUSED", "QUEUED"), ("RUNNING", "RUNNING")]
)
async def test_followup_keeps_stream_alive_without_overwriting_a_running_turn(
    logged_in_client, pool, tenant, before, after
):
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    from uuid import UUID

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status=$2 WHERE id=$1", UUID(session["session_id"]), before
        )
    response = await logged_in_client.post(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}/message",
        json={"text": "Doprecyzowanie celu"},
    )
    assert response.status_code == 202
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT status FROM agent_sessions WHERE id=$1", UUID(session["session_id"])
            )
            == after
        )


async def test_failed_turn_can_retry_in_its_existing_conversation(logged_in_client, pool):
    workspace_id = await _workspace(logged_in_client)
    project_id = await _project(logged_in_client, workspace_id)
    session = await _start(logged_in_client, workspace_id, project_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status='FAILED',finished_at=now() WHERE id=$1",
            uuid.UUID(session["session_id"]),
        )
    response = await logged_in_client.post(
        f"{_sessions_path(workspace_id, project_id)}/{session['session_id']}/resume"
    )
    assert response.status_code == 202
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status,finished_at FROM agent_sessions WHERE id=$1",
            uuid.UUID(session["session_id"]),
        )
    assert row["status"] == "QUEUED"
    assert row["finished_at"] is None
