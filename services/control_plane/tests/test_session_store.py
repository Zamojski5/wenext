"""`SessionStore` against a real PostgreSQL: the trace is a record, not a scratchpad (P2, 0015).

Four things are measured here, and each one is a way the product could quietly misreport work:

  * a session cannot be created without its goal landing in the trace, because the goal is the
    only thing the user actually typed and a session opening on an empty screen is not a session;
  * `seq` is allocated by the database while the row is written. The obvious implementation
    (`SELECT max(seq) + 1`, then INSERT) loses a race that really happens here: a turn streams
    events while the user posts a message. The lost event is INVISIBLE, which is why the test
    runs the two concurrently rather than reasoning about them;
  * a session becomes DONE together with the moment it finished, and never without it;
  * `claim_for_turn` is a compare-and-set, so two jobs racing on one session start ONE turn.
    Two turns on one thread is the P2 version of publishing the same comment twice.

Nothing here talks to a model or to Codex. The engine is a string column at this layer.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from control_plane.sessions import (
    EVENT_ASSISTANT_MESSAGE,
    EVENT_STATUS,
    EVENT_TOOL_CALL,
    EVENT_USER_MESSAGE,
    STATUS_DONE,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    SessionRefusal,
    SessionStore,
)

pytestmark = [pytest.mark.db, pytest.mark.p2]

GOAL = "sprawdź, dlaczego odzew jest mały, i zaproponuj zmianę"


async def _project(pool, tenant) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, '') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


async def _session(pool, tenant, *, goal: str = GOAL):
    store = SessionStore(pool)
    project_id = await _project(pool, tenant)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal=goal,
        budget={"max_turns": 8, "max_minutes": 30, "max_tool_calls": 40},
    )
    return store, session


# ---------------------------------------------------------------------------
# Creating a session
# ---------------------------------------------------------------------------


async def test_a_new_session_starts_queued_with_its_goal_as_the_first_event(pool, tenant) -> None:
    store, session = await _session(pool, tenant)

    assert session.status == STATUS_QUEUED
    assert session.engine_thread_id is None, "wątek silnika zakłada dopiero job, nie żądanie HTTP"
    assert session.finished_at is None
    assert session.budget["max_turns"] == 8

    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    assert [e.seq for e in events] == [1]
    assert events[0].kind == EVENT_USER_MESSAGE
    assert events[0].payload == {"text": GOAL, "is_goal": True}, (
        "cel nie trafił do śladu. Sesja otwarta na pustym ekranie nie pokazuje jedynej rzeczy, "
        "którą użytkownik naprawdę napisał"
    )


async def test_a_session_without_a_goal_is_refused_before_the_database(pool, tenant) -> None:
    store = SessionStore(pool)
    project_id = await _project(pool, tenant)
    for goal in ("", "   ", "\n\t"):
        with pytest.raises(SessionRefusal, match="cel"):
            await store.create(
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                project_id=project_id,
                goal=goal,
                budget={},
            )


async def test_a_project_from_another_workspace_is_a_refusal_not_a_session(pool, tenant) -> None:
    """The composite foreign key is what stops it; this test is that the store turns the
    database's complaint into a sentence a person can act on."""
    store = SessionStore(pool)
    async with pool.acquire() as conn:
        other_workspace = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
            tenant["organization"],
            f"obcy-{uuid4().hex[:6]}",
        )
        foreign_project = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'obcy') "
            "RETURNING id",
            tenant["organization"],
            other_workspace,
        )
    with pytest.raises(SessionRefusal, match="workspace"):
        await store.create(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=foreign_project,
            goal=GOAL,
            budget={},
        )


async def test_a_session_of_another_organization_reads_as_absent(pool, tenant) -> None:
    """Not "forbidden": absent. An id from another tenant must be indistinguishable from one that
    does not exist, or the 404 tells the caller which ids are real."""
    store, session = await _session(pool, tenant)
    async with pool.acquire() as conn:
        stranger = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"obca-{uuid4().hex[:6]}"
        )
    assert await store.get(organization_id=stranger, session_id=session.id) is None
    assert await store.events(organization_id=stranger, session_id=session.id) == ()


# ---------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------


async def test_sequence_numbers_are_dense_and_ordered(pool, tenant) -> None:
    store, session = await _session(pool, tenant)
    kinds = [EVENT_STATUS, EVENT_TOOL_CALL, EVENT_ASSISTANT_MESSAGE]
    for kind in kinds:
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=kind,
            payload={"k": kind},
        )
    events = await store.events(organization_id=tenant["organization"], session_id=session.id)
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert [e.kind for e in events] == [EVENT_USER_MESSAGE, *kinds]
    assert await store.last_seq(organization_id=tenant["organization"], session_id=session.id) == 4


async def test_reading_from_a_sequence_number_returns_only_what_came_after(pool, tenant) -> None:
    """How the screen resumes after a restart, and how it polls without re-reading everything."""
    store, session = await _session(pool, tenant)
    for n in range(5):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_STATUS,
            payload={"n": n},
        )
    later = await store.events(
        organization_id=tenant["organization"], session_id=session.id, after_seq=3
    )
    assert [e.seq for e in later] == [4, 5, 6]
    assert (
        await store.events(
            organization_id=tenant["organization"], session_id=session.id, after_seq=99
        )
        == ()
    )


async def test_concurrent_appends_do_not_lose_an_event(pool, tenant) -> None:
    """THE race this store is shaped around: a streaming turn and a user message at once.

    With `SELECT max(seq) + 1` followed by an INSERT, two writers read the same maximum and one
    INSERT is refused by the unique index. That refusal is the GOOD case; the bad one is a
    caller that swallows it, and then an event that really happened is simply not in the trace.
    Twenty concurrent appends, twenty distinct numbers, and no gaps.
    """
    store, session = await _session(pool, tenant)
    count = 20

    async def append(n: int) -> None:
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_STATUS,
            payload={"n": n},
        )

    results = await asyncio.gather(*(append(n) for n in range(count)), return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert not failures, f"{len(failures)} współbieżnych zapisów przepadło: {failures[:3]}"

    events = await store.events(
        organization_id=tenant["organization"], session_id=session.id, limit=1000
    )
    numbers = [e.seq for e in events]
    assert numbers == list(range(1, count + 2)), (
        f"numery zdarzeń nie są gęste: {numbers}. Dziura znaczy zgubione zdarzenie, a zgubionego "
        "zdarzenia nie widać na żadnym ekranie"
    )


async def test_an_unknown_event_kind_is_refused_by_the_store(pool, tenant) -> None:
    store, session = await _session(pool, tenant)
    with pytest.raises(SessionRefusal, match="rodzaj"):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind="THINKING",
            payload={},
        )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_a_terminal_status_gets_its_finishing_time_from_the_store(pool, tenant) -> None:
    """`finished_at` is computed from the status rather than passed in, so no caller can produce
    the row the schema forbids."""
    store, session = await _session(pool, tenant)
    running = await store.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )
    assert running.finished_at is None

    done = await store.set_status(
        session.id,
        organization_id=tenant["organization"],
        status=STATUS_DONE,
        result_summary="odzew jest mały, bo piszemy po fakcie",
        result_refs=[{"kind": "thread", "id": "t3_abc"}],
    )
    assert done.status == STATUS_DONE
    assert done.finished_at is not None
    assert done.result_refs == [{"kind": "thread", "id": "t3_abc"}]
    assert done.is_terminal


async def test_leaving_a_terminal_status_clears_the_finishing_time(pool, tenant) -> None:
    """A session that was FAILED and is resumed must not keep a finishing time from a run that
    did not end the work. The CHECK in 0015 would refuse the row anyway; this is the caller-side
    half, and it is here so the refusal never has to happen."""
    store, session = await _session(pool, tenant)
    await store.set_status(session.id, organization_id=tenant["organization"], status="FAILED")
    resumed = await store.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_RUNNING
    )
    assert resumed.finished_at is None and not resumed.is_terminal


async def test_an_unknown_status_is_refused(pool, tenant) -> None:
    store, session = await _session(pool, tenant)
    with pytest.raises(SessionRefusal, match="status"):
        await store.set_status(
            session.id, organization_id=tenant["organization"], status="THINKING"
        )


async def test_only_one_of_two_racing_jobs_starts_a_turn(pool, tenant) -> None:
    """Two turns on one engine thread is the P2 shape of publishing the same comment twice.

    The guard is a compare-and-set in the database, not a read followed by a write: the second
    one gets `None` and is expected to stop. A store that read the status first would let both
    through under concurrency, and both would call the model.
    """
    store, session = await _session(pool, tenant)

    async def claim():
        return await store.claim_for_turn(session.id, organization_id=tenant["organization"])

    first, second = await asyncio.gather(claim(), claim())
    claimed = [c for c in (first, second) if c is not None]
    assert len(claimed) == 1, "obie próby przejęły sesję; dwie tury pojadą na jednym wątku"
    assert claimed[0].status == STATUS_RUNNING


async def test_a_terminal_session_cannot_be_claimed_for_another_turn(pool, tenant) -> None:
    store, session = await _session(pool, tenant)
    await store.set_status(session.id, organization_id=tenant["organization"], status=STATUS_DONE)
    assert await store.claim_for_turn(session.id, organization_id=tenant["organization"]) is None


async def test_a_session_waiting_for_the_user_can_be_claimed_again(pool, tenant) -> None:
    """The ordinary continuation: the agent answered, the person wrote back, a new turn runs."""
    store, session = await _session(pool, tenant)
    await store.set_status(
        session.id, organization_id=tenant["organization"], status=STATUS_WAITING_USER
    )
    claimed = await store.claim_for_turn(session.id, organization_id=tenant["organization"])
    assert claimed is not None and claimed.status == STATUS_RUNNING


async def test_the_budget_is_counted_from_the_trace_not_from_memory(pool, tenant) -> None:
    """A counter in the handler resets when the worker restarts, and a session restarted twice
    would get three times its budget. The trace survives the restart."""
    store, session = await _session(pool, tenant)
    for _ in range(3):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_TOOL_CALL,
            payload={"tool": "catalog.list"},
        )
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "popraw to"},
    )
    counts = await store.counts_for_budget(
        organization_id=tenant["organization"], session_id=session.id
    )
    assert counts == {"turns": 2, "tool_calls": 3}
