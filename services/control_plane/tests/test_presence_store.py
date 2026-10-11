"""`PresenceStore` against a real PostgreSQL: a delegation, and why the model ever woke up (P4).

WHAT IS MEASURED HERE, and what each one prevents:

  * **signing twice revokes the first.** Two live delegations would be two different scopes one
    publication could claim to be covered by, and the audit trail could not say which;
  * **pausing is not revoking.** A paused delegation comes back on the SAME signature; a revoked
    one never does. One column for both would lose the difference, and with it the ability to
    say "you stopped it on Tuesday" rather than "somebody replaced it";
  * **one event wakes the agent once**, and the mechanism is the database's UNIQUE, not a read
     followed by a write. The test runs two collectors CONCURRENTLY, because the read-then-write
     version passes every sequential test ever written for it;
  * **a wake-up always says what happened**: a session, or a reason it did not open one. The
    quiet ticks are the majority and they are the ones that answer "why did nothing happen".

Nothing here runs a model. `wakeups.session_id` is a foreign key at this layer.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import (
    EVENT_NEW_THREAD,
    EVENT_REPLY,
    EVENT_RETRY_LIMIT,
    REASON_DUE,
    REASON_NEW_EVENT,
    SKIPPED_NO_EVENTS,
    PresenceRefusal,
    PresenceStore,
)

pytestmark = [pytest.mark.db, pytest.mark.p4]

SCOPE = {"accounts": [], "communities": ["r/przyklad"], "actions": ["REPLY"]}


def NOW() -> datetime:
    """A fresh moment per call, never a module constant.

    Measured on 2026-09-19 in this suite: a module-level `datetime(...)` literal passed for
    seven minutes and then started failing against constraints that compare with `now()`. A
    test that works only until the clock catches up is worse than no test.
    """
    return datetime.now(UTC)


async def _project(pool, tenant) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'zbuduj obecność') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )


async def _sign(store, tenant, project_id, *, mode: str = "AUTONOMOUS", budget=None):
    scope = parse_scope(SCOPE)
    parsed = parse_budget(budget if budget is not None else {"publications_per_day": 6})
    return await store.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=mode,
        scope=scope,
        budget=parsed,
        operator_id=tenant["operator"],
    )


async def test_a_second_signature_is_refused_while_one_is_live(pool, tenant) -> None:
    """Revoking first is a deliberate step, not a formality.

    Two live delegations would be two scopes one publication could claim to be covered by, and
    silently replacing the first would mean a person signs a narrower scope and the wider one
    keeps authorising work until they notice. The database refuses; this measures that the store
    turns the refusal into a sentence rather than an asyncpg traceback.
    """
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    first = await _sign(store, tenant, project_id)
    with pytest.raises(PresenceRefusal) as caught:
        await _sign(store, tenant, project_id, mode="REVIEW")
    assert "odwołaj" in str(caught.value).lower()

    await store.revoke(first.id, organization_id=tenant["organization"], at=NOW())
    second = await _sign(store, tenant, project_id, mode="REVIEW")
    assert second.id != first.id

    history = await store.delegation_history(
        organization_id=tenant["organization"], project_id=project_id
    )
    assert {row.id for row in history} == {first.id, second.id}, "stara zostaje w historii"
    replaced = next(row for row in history if row.id == first.id)
    assert replaced.revoked_at is not None, "stara jest odwołana, nie skasowana"


async def test_signing_mirrors_the_mode_onto_the_project(pool, tenant) -> None:
    """`projects.mode` and `project_delegations.mode` are two spellings of one setting, and the
    signature is the one that decides. Written in the SAME transaction, so a project screen can
    never show AUTONOMOUS while the live signature says REVIEW."""
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)

    async def mode_on_project():
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT mode FROM projects WHERE id = $1", project_id)

    delegation = await _sign(store, tenant, project_id, mode="RESEARCH")
    assert await mode_on_project() == "RESEARCH"

    await store.revoke(delegation.id, organization_id=tenant["organization"], at=NOW())
    await _sign(store, tenant, project_id, mode="REVIEW")
    assert await mode_on_project() == "REVIEW"


async def test_a_paused_delegation_is_not_live_and_still_holds_the_slot(pool, tenant) -> None:
    """Pausing is the operator's stop button, and it must not look like "no delegation": a
    project whose delegation vanished on pause would sign a NEW one to come back, and the new
    signature would not be the thing they read the first time."""
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)

    paused = await store.set_paused(
        delegation.id, organization_id=tenant["organization"], paused=True, at=NOW()
    )
    assert not paused.live and paused.state == "wstrzymana"
    assert (
        await store.live_delegation(organization_id=tenant["organization"], project_id=project_id)
        is None
    )
    still_there = await store.delegation_for(
        organization_id=tenant["organization"], project_id=project_id
    )
    assert still_there is not None and still_there.id == delegation.id

    resumed = await store.set_paused(
        delegation.id, organization_id=tenant["organization"], paused=False, at=NOW()
    )
    assert resumed.live, "wraca tym samym podpisem"
    assert resumed.scope_sha256 == delegation.scope_sha256


async def test_a_revoked_delegation_never_comes_back(pool, tenant) -> None:
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)
    await store.revoke(delegation.id, organization_id=tenant["organization"], at=NOW())

    assert (
        await store.delegation_for(organization_id=tenant["organization"], project_id=project_id)
        is None
    )
    with pytest.raises(PresenceRefusal):
        await store.set_paused(
            delegation.id, organization_id=tenant["organization"], paused=False, at=NOW()
        )


async def test_one_event_wakes_the_agent_once_even_under_a_race(pool, tenant) -> None:
    """TWO COLLECTORS AT THE SAME TIME, on purpose.

    The implementation this guards against is "SELECT whether the key is there, then INSERT".
    It passes every sequential test and loses exactly here: both reads return nothing, both
    writes succeed, and the agent wakes twice about one post, which on Reddit is two comments
    under one thread. `record_event` inserts and lets the UNIQUE constraint answer.
    """
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)
    wakeup = await store.open_wakeup(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        reason=REASON_NEW_EVENT,
    )

    async def record():
        return await store.record_event(
            organization_id=tenant["organization"],
            workspace_id=delegation.workspace_id,
            project_id=project_id,
            wakeup_id=wakeup.id,
            kind=EVENT_NEW_THREAD,
            event_key="NEW_THREAD:t3_abc123",
            payload={"tytul": "ten sam post"},
        )

    outcomes = await asyncio.gather(*(record() for _ in range(8)))
    assert sum(1 for accepted in outcomes if accepted) == 1, outcomes
    assert len(await store.events_of(wakeup.id)) == 1


async def test_the_same_event_may_wake_two_different_projects(pool, tenant) -> None:
    """The key is unique per PROJECT, not globally: two projects may both care about one thread,
    and a global key would silently give the second one nothing to work on."""
    store = PresenceStore(pool)
    first, second = await _project(pool, tenant), await _project(pool, tenant)
    for project_id in (first, second):
        delegation = await _sign(store, tenant, project_id)
        wakeup = await store.open_wakeup(
            organization_id=tenant["organization"],
            workspace_id=delegation.workspace_id,
            project_id=project_id,
            reason=REASON_NEW_EVENT,
        )
        assert await store.record_event(
            organization_id=tenant["organization"],
            workspace_id=delegation.workspace_id,
            project_id=project_id,
            wakeup_id=wakeup.id,
            kind=EVENT_NEW_THREAD,
            event_key="NEW_THREAD:t3_wspolny",
            payload={},
        )


async def test_a_quiet_tick_is_a_row_with_a_reason(pool, tenant) -> None:
    """The commonest row in the table, and the one that answers "why was it quiet all day"."""
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)
    wakeup = await store.open_wakeup(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        reason=REASON_DUE,
    )
    closed = await store.close_skipped(
        wakeup.id,
        organization_id=tenant["organization"],
        reason=SKIPPED_NO_EVENTS,
        detail="nic nowego od ostatniego przebudzenia; model nie był uruchamiany",
    )
    assert closed.session_id is None
    assert closed.skipped_reason == SKIPPED_NO_EVENTS, "kod, bo raport dnia grupuje po nim"
    assert "model nie był uruchamiany" in (closed.skipped_detail or ""), "zdanie dla człowieka"

    listed = await store.wakeups_for(
        organization_id=tenant["organization"], project_id=project_id, limit=5
    )
    assert [row.id for row in listed] == [wakeup.id]


async def test_known_event_keys_answers_only_about_this_project(pool, tenant) -> None:
    store = PresenceStore(pool)
    first, second = await _project(pool, tenant), await _project(pool, tenant)
    delegation = await _sign(store, tenant, first)
    await _sign(store, tenant, second)
    wakeup = await store.open_wakeup(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=first,
        reason=REASON_NEW_EVENT,
    )
    await store.record_event(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=first,
        wakeup_id=wakeup.id,
        kind=EVENT_NEW_THREAD,
        event_key="NEW_THREAD:t3_znany",
        payload={},
    )
    keys = {"NEW_THREAD:t3_znany", "NEW_THREAD:t3_nowy"}
    # DOPÓKI obudzenie nie uruchomiło modelu, zdarzenie jest ODŁOŻONE, nie obsłużone, i nie
    # liczy się jako znane. Tak wygląda tik bez połączenia Codex albo z wyczerpanym budżetem;
    # audyt z 19.09 zmierzył, że wcześniej takie zdarzenie przepadało na zawsze.
    assert (
        await store.known_event_keys(
            organization_id=tenant["organization"], project_id=first, keys=keys
        )
        == set()
    )

    # Po podpięciu sesji to samo zdarzenie JEST znane: model je dostał.
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'praca', 'PRESENCE') RETURNING id",
            tenant["organization"],
            delegation.workspace_id,
            first,
        )
    await store.attach_session(
        wakeup.id, organization_id=tenant["organization"], session_id=session_id
    )
    assert await store.known_event_keys(
        organization_id=tenant["organization"], project_id=first, keys=keys
    ) == {"NEW_THREAD:t3_znany"}
    assert (
        await store.known_event_keys(
            organization_id=tenant["organization"], project_id=second, keys=keys
        )
        == set()
    )


async def _wakeup_with_session(store, pool, tenant, delegation, project_id, *, answered: bool):
    wakeup = await store.open_wakeup(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        reason=REASON_NEW_EVENT,
    )
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind, "
            "status, finished_at) VALUES ($1, $2, $3, 'praca', 'PRESENCE', 'FAILED', now()) "
            "RETURNING id",
            tenant["organization"],
            delegation.workspace_id,
            project_id,
        )
        if answered:
            await conn.execute(
                "INSERT INTO session_events (organization_id, workspace_id, session_id, seq, "
                "kind, payload) VALUES ($1, $2, $3, 1, 'ASSISTANT_MESSAGE', '{\"text\": \"x\"}')",
                tenant["organization"],
                delegation.workspace_id,
                session_id,
            )
    await store.attach_session(
        wakeup.id, organization_id=tenant["organization"], session_id=session_id
    )
    return wakeup


async def test_an_event_whose_session_failed_before_the_model_goes_back_a_bounded_time(
    pool, tenant
) -> None:
    """Measured 27.09: a reply under our X post opened a session that the engine refused in its
    first second (a model the server would not serve), and the reply counted as handled."""
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)
    key = "REPLY:x:2104221711409873297"

    async def record(wakeup) -> bool:
        return await store.record_event(
            organization_id=tenant["organization"],
            workspace_id=delegation.workspace_id,
            project_id=project_id,
            wakeup_id=wakeup.id,
            kind=EVENT_REPLY,
            event_key=key,
            payload={"autor": "@demo_partner"},
        )

    async def known() -> set[str]:
        return await store.known_event_keys(
            organization_id=tenant["organization"], project_id=project_id, keys=[key]
        )

    failed = await _wakeup_with_session(store, pool, tenant, delegation, project_id, answered=False)
    # The first attach happens on a wakeup opened BEFORE its session existed.
    first = await store.open_wakeup(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        reason=REASON_NEW_EVENT,
    )
    assert await record(first)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE wakeup_events SET wakeup_id = $1 WHERE event_key = $2", failed.id, key
        )
    assert await known() == set(), "sesja padła przed modelem: zdarzenie nie jest obsłużone"

    for attempt in range(EVENT_RETRY_LIMIT):
        again = await _wakeup_with_session(
            store, pool, tenant, delegation, project_id, answered=False
        )
        assert await record(again), f"ponowienie {attempt + 1} przenosi zdarzenie"
    async with pool.acquire() as conn:
        retries = await conn.fetchval(
            "SELECT payload ->> 'ponowienia' FROM wakeup_events WHERE event_key = $1", key
        )
    assert retries == str(EVENT_RETRY_LIMIT)
    assert await known() == {key}, "po limicie ponowień zdarzenie uznajemy za obsłużone"


async def test_an_event_the_model_saw_does_not_go_back(pool, tenant) -> None:
    store = PresenceStore(pool)
    project_id = await _project(pool, tenant)
    delegation = await _sign(store, tenant, project_id)
    key = "REPLY:x:42"
    seen = await _wakeup_with_session(store, pool, tenant, delegation, project_id, answered=True)
    assert await store.record_event(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        wakeup_id=seen.id,
        kind=EVENT_REPLY,
        event_key=key,
        payload={},
    )
    assert await store.known_event_keys(
        organization_id=tenant["organization"], project_id=project_id, keys=[key]
    ) == {key}
    later = await _wakeup_with_session(store, pool, tenant, delegation, project_id, answered=True)
    assert not await store.record_event(
        organization_id=tenant["organization"],
        workspace_id=delegation.workspace_id,
        project_id=project_id,
        wakeup_id=later.id,
        kind=EVENT_REPLY,
        event_key=key,
        payload={},
    )
