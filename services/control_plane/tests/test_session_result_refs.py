"""`result_refs` carries what the answer RESTS ON, not only what was asked (P5, dodatek).

WHY THE ARGUMENTS WERE NOT ENOUGH. `memory.list` takes a filter and returns findings;
`sources.read` takes an address and returns a material. Built from arguments alone, `result_refs`
could say "it searched" but never "it used THIS finding", and "co zmieniłeś i dlaczego" is a
question about the second one. The identifiers live in the RESULT, so the result is read.

WHAT IS DELIBERATELY NOT DONE HERE: a per-tool reader. A table saying "memory.list keeps its ids
under `ustalenia`" is a second description of a shape another module owns, and it stops being
true the first time a field moves. The walk collects values under keys called `id` or ending in
`_id`, which every tool here already follows, and it survives the shape changing around them.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from control_plane.sessions.handlers import SessionTurnHandlers
from control_plane.sessions.store import (
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    SessionStore,
)
from control_plane.sessions.trace_payloads import MAX_RETURNED_IDS, returned_ids

pytestmark = [pytest.mark.p5]


def test_identifiers_are_taken_from_anywhere_in_the_result() -> None:
    item, source = str(uuid4()), str(uuid4())
    result = {
        "stan": "WYKONANE",
        "ustalenia": [
            {"id": item, "tresc": "klient patrzy na koszt", "zrodlo": {"id": source}},
        ],
    }
    assert returned_ids(result) == [item, source]


def test_a_uuid_inside_a_sentence_is_not_a_reference() -> None:
    """Something the model READ is not something the tool returned. Treating the two alike would
    let a reference be created by writing one down."""
    mentioned = str(uuid4())
    result = {"ustalenia": [{"id": None, "tresc": f"porównaj z {mentioned}"}]}
    assert returned_ids(result) == []


def test_the_same_identifier_twice_is_one_reference() -> None:
    source = str(uuid4())
    first, second = str(uuid4()), str(uuid4())
    result = {
        "ustalenia": [
            {"id": first, "zrodlo": {"id": source}},
            {"id": second, "zrodlo": {"id": source}},
        ]
    }
    assert returned_ids(result) == [first, source, second]


def test_a_long_listing_is_one_call_not_a_hundred_references() -> None:
    result = {"ustalenia": [{"id": str(uuid4())} for _ in range(200)]}
    assert len(returned_ids(result)) == MAX_RETURNED_IDS


@pytest.mark.parametrize("result", [None, {}, [], "tekst", {"licznik": 3}])
def test_a_result_with_no_identifiers_contributes_nothing(result) -> None:
    assert returned_ids(result) == []


def test_keys_that_merely_look_like_identifiers_are_left_alone() -> None:
    """`id` and `*_id` only. A `payload_hash` is not an identifier of a row, and a value that is
    not a UUID is not one either, whatever its key is called."""
    result = {
        "payload_hash": "a" * 64,
        "identyfikator": str(uuid4()),
        "session_id": "nie-uuid",
        "source_id": str(uuid4()),
    }
    assert returned_ids(result) == [result["source_id"]]


# ---------------------------------------------------------------------------
# The pairing, against a real trace
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session(pool, tenant):
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    return await SessionStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="sprawdź, co wiemy",
        budget={},
    )


def _handlers(pool, tenant) -> SessionTurnHandlers:
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    return SessionTurnHandlers(
        pool=pool,
        sessions=SessionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        organization_id=tenant["organization"],
        dsn="postgresql://atrapa/nieuzywane",
    )


async def _call_and_result(store, tenant, session, *, tool: str, arguments: dict, result) -> None:
    for kind, payload in (
        (EVENT_TOOL_CALL, {"tool": tool, "arguments": arguments}),
        (EVENT_TOOL_RESULT, {"tool": tool, "status": "ok", "result": result}),
    ):
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=kind,
            payload=payload,
        )


@pytest.mark.db
async def test_a_turn_with_memory_list_keeps_the_identifiers_it_returned(
    pool, tenant, session
) -> None:
    """The measurement the assignment asks for: after a turn that called a memory tool,
    `result_refs` names what came back, not only what was asked for."""
    store = SessionStore(pool)
    item, source = str(uuid4()), str(uuid4())
    await _call_and_result(
        store,
        tenant,
        session,
        tool="memory.list",
        arguments={"kind": "PREFERENCE"},
        result={"ustalenia": [{"id": item, "zrodlo": {"id": source}}]},
    )

    refs = await _handlers(pool, tenant)._references(session)
    assert len(refs) == 1, refs
    assert refs[0]["tool"] == "memory.list"
    assert refs[0]["arguments"] == {"kind": "PREFERENCE"}
    assert refs[0]["returned_ids"] == [item, source]


@pytest.mark.db
async def test_two_calls_of_one_tool_do_not_share_their_identifiers(pool, tenant, session) -> None:
    """PAIRED BY POSITION, NOT BY NAME. Matching on the tool name would hand the first call the
    second one's identifiers, which is a reference to something that call never saw."""
    store = SessionStore(pool)
    first, second = str(uuid4()), str(uuid4())
    await _call_and_result(
        store,
        tenant,
        session,
        tool="memory.search",
        arguments={"query": "koszt"},
        result={"ustalenia": [{"id": first}]},
    )
    await _call_and_result(
        store,
        tenant,
        session,
        tool="memory.search",
        arguments={"query": "predkosc"},
        result={"ustalenia": [{"id": second}]},
    )

    refs = await _handlers(pool, tenant)._references(session)
    assert [ref["arguments"]["query"] for ref in refs] == ["koszt", "predkosc"]
    assert [ref["returned_ids"] for ref in refs] == [[first], [second]]


@pytest.mark.db
async def test_a_call_whose_result_never_arrived_is_still_a_reference(
    pool, tenant, session
) -> None:
    """An interrupted turn leaves a call with no result. It stays in `result_refs` WITHOUT
    `returned_ids`: the agent reached for the tool, and pretending it did not would hide the
    most interesting half of a turn that was stopped."""
    store = SessionStore(pool)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_TOOL_CALL,
        payload={"tool": "sources.read", "arguments": {"locator": "https://example.test/a"}},
    )

    refs = await _handlers(pool, tenant)._references(session)
    assert len(refs) == 1
    assert refs[0]["tool"] == "sources.read"
    assert "returned_ids" not in refs[0]
