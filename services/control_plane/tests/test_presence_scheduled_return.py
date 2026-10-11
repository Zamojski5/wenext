"""Agent planuje własny powrót (P4 etap C, brak wskazany przez audyt z 19.09).

CZEGO BRAKOWAŁO. Agent umiał REAGOWAĆ: nowy wątek, odpowiedź, moderacja. Nie umiał nic ODŁOŻYĆ.
Sesja, która słusznie stwierdza „teraz nie ma o czym pisać, wrócę za trzy dni", nie miała gdzie
tego zapisać, więc zostawały dwie możliwości: napisać cokolwiek teraz albo zapomnieć. Obie złe,
i pierwsza gorsza.

CO JEST MIERZONE:

  * powrót zapisany przez sesję budzi NOWĄ sesję po terminie, z notatką autora w celu;
  * powrót ODWOŁANY nie budzi nikogo, a wiersz zostaje, żeby dało się odpowiedzieć na pytanie
    „dlaczego agent nie wrócił, skoro obiecał";
  * granice są granicami: minuta to pętla, dwa tygodnie to obietnica bez pokrycia;
  * planować może tylko projekt z ŻYWYM powierzeniem, bo powrót zapisany pod wstrzymanym
    obudziłby agenta po tym, jak człowiek kazał mu przestać;
  * limit powrotów na projekt jest sprawdzany w tej samej transakcji, co zapis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.presence.limits_for_tools import (
    MAX_AFTER_MINUTES,
    MIN_AFTER_MINUTES,
    planned_moment,
)
from control_plane.presence.store import PresenceRefusal, PresenceStore
from control_plane.tools.capability import issue
from control_plane.tools.registry import ToolRegistry

pytestmark = [pytest.mark.db, pytest.mark.p4]

FIXED = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Granice, bez bazy
# ---------------------------------------------------------------------------


def test_a_return_in_a_minute_is_a_loop_not_a_plan() -> None:
    with pytest.raises(ValueError) as caught:
        planned_moment({"after_minutes": 1}, now=FIXED)
    assert "najwcześniejszy" in str(caught.value)


def test_a_return_in_two_weeks_is_a_promise_nobody_keeps() -> None:
    with pytest.raises(ValueError) as caught:
        planned_moment({"after_minutes": 14 * 24 * 60}, now=FIXED)
    assert "siedem dni" in str(caught.value)


def test_both_forms_at_once_are_refused_rather_than_silently_picked() -> None:
    """Model, który podał dwie różne daty, nie wie, kiedy chce wrócić."""
    with pytest.raises(ValueError) as caught:
        planned_moment({"after_minutes": 60, "at": "2026-09-20T12:00:00Z"}, now=FIXED)
    assert "ALBO" in str(caught.value)


def test_neither_form_is_refused() -> None:
    with pytest.raises(ValueError):
        planned_moment({}, now=FIXED)


@pytest.mark.parametrize("minutes", [MIN_AFTER_MINUTES, 60, MAX_AFTER_MINUTES])
def test_the_edges_of_the_window_are_allowed(minutes) -> None:
    assert planned_moment({"after_minutes": minutes}, now=FIXED) == FIXED + timedelta(
        minutes=minutes
    )


def test_an_iso_moment_without_a_zone_is_read_as_utc() -> None:
    """Brak strefy nie może znaczyć „czas maszyny": ta sama notatka na dwóch maszynach
    obudziłaby agenta o dwóch różnych porach."""
    due = planned_moment({"at": "2026-09-19T18:00:00"}, now=FIXED)
    assert due.tzinfo is not None and due.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Pełny obieg
# ---------------------------------------------------------------------------


async def _registry_for(pool, tenant, world):
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'popracuj', 'PRESENCE') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            world["project"],
        )
    return ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=world["project"],
            dsn="postgresql://atrapa/nieuzywane",
        ),
    )


async def test_a_scheduled_return_wakes_a_new_session_with_the_note(pool, tenant) -> None:
    """PEŁNY OBIEG: sesja planuje, termin mija, tik budzi nową sesję i podaje jej notatkę."""
    from control_plane.presence.handlers import OUTCOME_QUIET, OUTCOME_WORKED
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    registry = await _registry_for(pool, tenant, world)

    result = await registry.call(
        "session.schedule_return",
        {
            "after_minutes": 60,
            "reason": "sprawdzę, czy autor odpowiedział",
            "note": "wróć do wątku 1p4c001 i zobacz, czy ktoś zareagował",
        },
    )
    assert result["stan"] == "WYKONANE", result
    assert result["powrot"]["za_minut"] in (59, 60)

    # PRZED terminem nic się nie dzieje: tik widzi ciszę, a nie powrót.
    before = await world["handlers"].run_once(world["project"], now())
    assert before.outcome == OUTCOME_QUIET, before.detail
    assert world["turns"].ran == []

    # PO terminie powrót jest zdarzeniem i budzi sesję.
    after = await world["handlers"].run_once(world["project"], now() + timedelta(minutes=61))
    assert after.outcome == OUTCOME_WORKED, after.detail
    assert after.events == 1
    assert world["turns"].ran == [after.session_id]

    from control_plane.sessions.store import SessionStore

    session = await SessionStore(pool).get(
        organization_id=tenant["organization"], session_id=after.session_id
    )
    assert session is not None
    assert "wróć do wątku 1p4c001" in session.goal, session.goal


async def test_a_cancelled_return_wakes_nobody_and_stays_in_the_table(pool, tenant) -> None:
    from control_plane.presence.handlers import OUTCOME_QUIET
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    registry = await _registry_for(pool, tenant, world)
    result = await registry.call(
        "session.schedule_return", {"after_minutes": 30, "reason": "wrócę"}
    )
    wakeup_id = result["powrot"]["wakeup_id"]

    presence = PresenceStore(pool)
    cancelled = await presence.cancel_return(
        __import__("uuid").UUID(wakeup_id),
        organization_id=tenant["organization"],
        now=now(),
    )
    assert cancelled is not None and cancelled.cancelled_at is not None

    after = await world["handlers"].run_once(world["project"], now() + timedelta(minutes=31))
    assert after.outcome == OUTCOME_QUIET, after.detail
    assert world["turns"].ran == []

    # Wiersz ZOSTAJE: „dlaczego agent nie wrócił, skoro obiecał" to pytanie, które ktoś zada.
    wakeups = await presence.wakeups_for(
        organization_id=tenant["organization"], project_id=world["project"], limit=10
    )
    assert any(str(one.id) == wakeup_id for one in wakeups)


async def test_a_return_fires_once_and_not_on_every_later_tick(pool, tenant) -> None:
    from control_plane.presence.handlers import OUTCOME_QUIET, OUTCOME_WORKED
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    registry = await _registry_for(pool, tenant, world)
    await registry.call("session.schedule_return", {"after_minutes": 10, "reason": "wrócę"})

    first = await world["handlers"].run_once(world["project"], now() + timedelta(minutes=11))
    assert first.outcome == OUTCOME_WORKED
    second = await world["handlers"].run_once(world["project"], now() + timedelta(minutes=12))
    assert second.outcome == OUTCOME_QUIET, second.detail
    assert len(world["turns"].ran) == 1


async def test_a_paused_delegation_refuses_to_take_a_promise(pool, tenant) -> None:
    """Powrót zapisany pod wstrzymanym powierzeniem obudziłby agenta po tym, jak człowiek
    kazał mu przestać."""
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    registry = await _registry_for(pool, tenant, world)
    await PresenceStore(pool).set_paused(
        world["delegation"].id,
        organization_id=tenant["organization"],
        paused=True,
        at=now(),
    )

    result = await registry.call(
        "session.schedule_return", {"after_minutes": 60, "reason": "wrócę"}
    )
    assert result["stan"] == "NIEDOSTEPNE", result
    assert "wstrzymana" in result["powod"]


async def test_the_limit_of_planned_returns_is_enforced(pool, tenant) -> None:
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    presence = PresenceStore(pool)
    for n in range(3):
        await presence.schedule_return(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=world["project"],
            due_at=now() + timedelta(hours=n + 1),
            note=f"powrót {n}",
            session_id=uuid4(),
            limit=3,
        )
    with pytest.raises(PresenceRefusal) as caught:
        await presence.schedule_return(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=world["project"],
            due_at=now() + timedelta(hours=9),
            note="jeden za dużo",
            session_id=uuid4(),
            limit=3,
        )
    assert "limicie 3" in str(caught.value)


async def test_a_return_without_a_reason_is_refused(pool, tenant) -> None:
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    registry = await _registry_for(pool, tenant, world)
    result = await registry.call("session.schedule_return", {"after_minutes": 60, "reason": " "})
    assert result["stan"] == "BLAD", result


async def test_a_return_that_did_not_fit_in_this_tick_comes_back_in_the_next_one(
    pool, tenant
) -> None:
    """LIMIT TIKU NIE MOŻE ZJADAĆ TERMINÓW.

    Zdarzeń na jeden tik jest najwyżej `MAX_EVENTS_PER_TICK`. Powroty jadą na początku listy, ale
    gdy jest ich więcej niż limit, reszta się nie mieści. Wersja, która zużywała WSZYSTKIE terminy
    z góry, kasowała wtedy te nienadane: agent nigdy nie wracał do sprawy, którą sam sobie
    obiecał, a w tabeli zostawał ślad mówiący, że wrócił.
    """
    from control_plane.presence.handlers import MAX_EVENTS_PER_TICK, OUTCOME_WORKED
    from test_presence_tick import _world

    world = await _world(pool, tenant)
    store = PresenceStore(pool)
    moment = now()
    over = MAX_EVENTS_PER_TICK + 1
    for i in range(over):
        await store.schedule_return(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=world["project"],
            due_at=moment + timedelta(minutes=10 + i),
            note=f"wróć do sprawy numer {i}",
            session_id=uuid4(),
            limit=over + 5,
        )

    first = await world["handlers"].run_once(world["project"], moment + timedelta(hours=2))
    assert first.outcome == OUTCOME_WORKED, first.detail
    assert first.events == MAX_EVENTS_PER_TICK, first

    left = await store.planned_returns(
        organization_id=tenant["organization"], project_id=world["project"]
    )
    assert len(left) == over - MAX_EVENTS_PER_TICK, [str(one.due_at) for one in left]

    second = await world["handlers"].run_once(world["project"], moment + timedelta(hours=2))
    assert second.outcome == OUTCOME_WORKED, second.detail
    assert second.events == over - MAX_EVENTS_PER_TICK, second
    assert (
        await store.planned_returns(
            organization_id=tenant["organization"], project_id=world["project"]
        )
        == ()
    )


async def test_due_return_survives_missing_connection_and_uses_org_connection_after_recovery(
    pool, tenant
):
    from control_plane.presence.handlers import OUTCOME_NO_CONNECTION, OUTCOME_QUIET, OUTCOME_WORKED
    from test_presence_tick import _world

    world = await _world(pool, tenant, connected=False)
    registry = await _registry_for(pool, tenant, world)
    scheduled = await registry.call(
        "session.schedule_return",
        {
            "after_minutes": 60,
            "reason": "Evaluate publication feedback",
        },
    )
    assert scheduled["stan"] == "WYKONANE"
    due_at = now() + timedelta(minutes=61)
    failed = await world["handlers"].run_once(world["project"], due_at)
    assert failed.outcome == OUTCOME_NO_CONNECTION
    remaining = await PresenceStore(pool).planned_returns(
        organization_id=tenant["organization"],
        project_id=world["project"],
    )
    assert len(remaining) == 1, "A missing connection must defer rather than consume the return."
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO codex_connections(organization_id, label, home_ref, status) "
            "VALUES ($1, 'organization connection', $2, 'CONNECTED')",
            tenant["organization"],
            str(uuid4()),
        )
    resumed = await world["handlers"].run_once(world["project"], due_at + timedelta(minutes=10))
    assert resumed.outcome == OUTCOME_WORKED, resumed.detail
    assert world["turns"].ran == [resumed.session_id]
    from control_plane.sessions.store import SessionStore

    resumed_session = await SessionStore(pool).get(
        organization_id=tenant["organization"],
        session_id=resumed.session_id,
    )
    assert "Evaluate publication feedback" in resumed_session.goal
    after = await world["handlers"].run_once(world["project"], due_at + timedelta(minutes=20))
    assert after.outcome == OUTCOME_QUIET
    assert world["turns"].ran == [resumed.session_id]
