"""`presence.tick`: the agent works while nobody is watching, and every tick says what it did (P4).

THE EXPENSIVE CLAIM OF THIS MILESTONE is "close the laptop and it keeps working". The cheap way
to implement that is a loop that wakes a model every ten minutes, and it is wrong in two
directions at once: it burns a budget on nothing, and it cannot answer "why did it stay quiet".
So what is measured here is mostly about NOT running the model:

  * a cycle with nothing new does not open a session, and STILL writes a row saying so;
  * the same event does not wake the agent twice, even across two ticks;
  * a paused delegation and a paused project both stop the cycle, and neither leaves a trail of
    identical rows every ten minutes;
  * a spent model budget stops the cycle with the numbers as they were at that moment;
  * no Codex connection is an honest outcome, not a session that cannot move;
  * when the model DOES run, the wake-up carries the cost afterwards, and what the session
    proposed goes through the delegation, not around it.

The engine is a stub here. `test_session_turn_e2e.py` measures a real turn; what this file
measures is the decision to run one at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from control_plane.approval.store import ApprovalStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.pilot.publication import PublicationStore
from control_plane.presence.approval import DelegatedApprover
from control_plane.presence.collector import EventCollector
from control_plane.presence.handlers import (
    OUTCOME_BUDGET,
    OUTCOME_NO_CONNECTION,
    OUTCOME_PAUSED,
    OUTCOME_QUIET,
    OUTCOME_WORKED,
    PresenceHandlers,
)
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import (
    SKIPPED_BUDGET,
    SKIPPED_NO_CONNECTION,
    SKIPPED_NO_EVENTS,
    PresenceStore,
)
from control_plane.sessions.store import SESSION_PRESENCE, SessionStore

pytestmark = [pytest.mark.db, pytest.mark.p4]

COMMUNITY = "r/kubernetes"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class FakeTurn:
    outcome: str = "ANSWERED"
    detail: str = "atrapa silnika"
    session_id: UUID | None = None


class FakeTurns:
    """Stands in for `SessionTurnHandlers`. Records which sessions it was asked to run."""

    def __init__(self) -> None:
        self.ran: list[UUID] = []

    async def run_once(self, session_id: UUID) -> FakeTurn:
        self.ran.append(session_id)
        return FakeTurn(session_id=session_id)


async def _world(pool, tenant, *, mode="AUTONOMOUS", budget=None, connected=True):
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'odpowiadaj tam, gdzie umiemy pomóc') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            COMMUNITY,
        )
        if connected:
            # A per-connection directory, not the volume root: 0014 keeps one live connection
            # per `home_ref`, and every test in this run shares one database.
            await conn.execute(
                "INSERT INTO codex_connections (organization_id, workspace_id, label, home_ref, "
                "status) VALUES ($1, $2, 'atrapa', $3, 'CONNECTED')",
                tenant["organization"],
                tenant["workspace"],
                str(uuid4()),
            )

    presence = PresenceStore(pool)
    delegation = await presence.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=mode,
        scope=parse_scope(
            {
                "accounts": [str(account_id)],
                "communities": [COMMUNITY],
                "actions": ["REPLY"],
            }
        ),
        budget=parse_budget(budget if budget is not None else {"publications_per_day": 6}),
        operator_id=tenant["operator"],
    )
    turns = FakeTurns()
    handlers = PresenceHandlers(
        pool=pool,
        presence=presence,
        sessions=SessionStore(pool),
        turns=turns,
        approver=DelegatedApprover(
            pool=pool,
            presence=presence,
            approvals=ApprovalStore(pool),
            publication=PublicationStore(pool),
            organization_id=tenant["organization"],
        ),
        collector=EventCollector(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        organization_id=tenant["organization"],
    )
    return {
        "handlers": handlers,
        "turns": turns,
        "presence": presence,
        "project": project_id,
        "community": community_id,
        "delegation": delegation,
    }


async def _thread(pool, tenant, world, *, external_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title, body,
                 author, created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, $4, $5, 'jak to ogarnąć', 'treść', 'op', now(), now(), now(), $6)
            """,
            tenant["organization"],
            tenant["workspace"],
            world["community"],
            external_id,
            f"https://www.reddit.com/{COMMUNITY}/comments/{external_id}/x/",
            f"{uuid4().hex}{uuid4().hex}"[:64],
        )


async def _wakeups(world):
    return await world["presence"].wakeups_for(
        organization_id=world["delegation"].organization_id,
        project_id=world["project"],
        limit=20,
    )


async def test_a_cycle_with_nothing_new_does_not_run_the_model_and_still_says_so(
    pool, tenant
) -> None:
    """THE COMMONEST TICK OF THE DAY. A row with a reason, and no session."""
    world = await _world(pool, tenant)
    result = await world["handlers"].run_once(world["project"], now())

    assert result.outcome == OUTCOME_QUIET
    assert result.session_id is None
    assert world["turns"].ran == [], "model nie był uruchamiany"

    wakeups = await _wakeups(world)
    assert len(wakeups) == 1
    assert wakeups[0].session_id is None
    assert wakeups[0].skipped_reason == SKIPPED_NO_EVENTS
    assert "nic nowego" in (wakeups[0].skipped_detail or "")


async def test_a_new_thread_wakes_the_agent_once_and_not_on_the_next_tick(pool, tenant) -> None:
    """The same post must not produce a second session, and a second comment under it."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a001")

    first = await world["handlers"].run_once(world["project"], now())
    assert first.outcome == OUTCOME_WORKED, first.detail
    assert first.events == 1
    assert world["turns"].ran == [first.session_id]

    second = await world["handlers"].run_once(world["project"], now())
    assert second.outcome == OUTCOME_QUIET, second.detail
    assert world["turns"].ran == [first.session_id], "drugi tik nie rusza modelu drugi raz"

    wakeups = await _wakeups(world)
    assert len(wakeups) == 2
    assert [bool(w.session_id) for w in wakeups] == [False, True], "najnowsze pierwsze"


async def test_the_session_it_opens_is_a_presence_session_with_the_goal_in_it(pool, tenant) -> None:
    """A real session, the same rows and the same screen as one a person opened, so that after
    the fact they can read exactly what the agent was asked and what it did."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a002")
    result = await world["handlers"].run_once(world["project"], now())

    sessions = SessionStore(pool)
    session = await sessions.get(
        organization_id=world["delegation"].organization_id, session_id=result.session_id
    )
    assert session is not None
    assert session.kind == SESSION_PRESENCE
    assert "odpowiadaj tam, gdzie umiemy pomóc" in session.goal, "cel projektu jest w celu sesji"
    # The event id is not checked here: the goal DESCRIBES the wakeup, it does not quote it.
    assert "NEW_THREAD" in session.goal, "i powód przebudzenia"
    assert session.budget, "budżet sesji pochodzi z delegacji"
    # 27.09: the signed mandate and what is left of it, so the session can plan with them.
    assert "Zakres powierzenia:" in session.goal, session.goal
    assert "W ostatniej dobie: publikacje 0 z" in session.goal, session.goal

    events = await sessions.events(
        organization_id=world["delegation"].organization_id, session_id=session.id
    )
    assert events, "cel trafia do śladu jako pierwsze zdarzenie"


async def test_a_paused_delegation_stops_the_cycle_without_filling_the_table(pool, tenant) -> None:
    """Ten minutes apart for a day is a hundred and forty four rows saying the same thing. A
    paused project writes none of them: the pause is already a fact, recorded where it happened."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a003")
    await world["presence"].set_paused(
        world["delegation"].id,
        organization_id=world["delegation"].organization_id,
        paused=True,
        at=now(),
    )

    for _ in range(3):
        result = await world["handlers"].run_once(world["project"], now())
        assert result.outcome == OUTCOME_PAUSED
    assert await _wakeups(world) == ()
    assert world["turns"].ran == []


async def test_a_paused_project_stops_the_cycle_too(pool, tenant) -> None:
    """Two stop buttons, both honoured. `projects.paused_at` is what P1's screen writes."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a004")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE projects SET paused_at = now() WHERE id = $1", world["project"])

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_PAUSED
    assert world["turns"].ran == []


async def test_a_spent_model_budget_stops_the_cycle_with_the_numbers_of_that_moment(
    pool, tenant
) -> None:
    world = await _world(pool, tenant, budget={"model_turns_per_day": 1})
    await _thread(pool, tenant, world, external_id="1p4a005")
    first = await world["handlers"].run_once(world["project"], now())
    assert first.outcome == OUTCOME_WORKED

    await _thread(pool, tenant, world, external_id="1p4a006")
    second = await world["handlers"].run_once(world["project"], now())
    assert second.outcome == OUTCOME_BUDGET, second.detail
    assert "1 z 1" in second.detail
    assert world["turns"].ran == [first.session_id], "drugi raz model nie ruszył"

    wakeups = await _wakeups(world)
    assert wakeups[0].skipped_reason == SKIPPED_BUDGET
    assert "1 z 1" in (wakeups[0].skipped_detail or ""), "liczby z chwili pominięcia"


async def test_no_codex_connection_is_an_outcome_not_a_broken_session(pool, tenant) -> None:
    world = await _world(pool, tenant, connected=False)
    await _thread(pool, tenant, world, external_id="1p4a007")

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_NO_CONNECTION
    assert result.session_id is None
    wakeups = await _wakeups(world)
    assert wakeups[0].skipped_reason == SKIPPED_NO_CONNECTION
    assert "połączenia Codex" in (wakeups[0].skipped_detail or "")


async def test_a_wakeup_that_ran_the_model_carries_what_it_cost(pool, tenant) -> None:
    """`cost` is NULL while a wake-up is in flight and filled when it ends: zero is not the same
    thing as "not known yet", and a day report that confused them would under-count."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a008")
    result = await world["handlers"].run_once(world["project"], now())

    wakeups = await _wakeups(world)
    assert wakeups[0].session_id == result.session_id
    assert wakeups[0].skipped_reason is None, "uruchomił model, więc nie ma powodu pominięcia"
    assert wakeups[0].cost is not None
    assert wakeups[0].cost["wynik_tury"] == "ANSWERED"
    assert wakeups[0].cost["turns"] >= 0


async def test_finishing_a_turn_does_not_publish_a_draft_the_model_left_pending(
    pool, tenant, monkeypatch
) -> None:
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1draftonly")

    async def unexpected_sweep(*args, **kwargs):
        raise AssertionError("A finished turn must not silently issue every pending draft")

    monkeypatch.setattr(DelegatedApprover, "consider_pending", unexpected_sweep)
    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED
    assert result.issued == 0


async def test_two_ticks_of_one_project_do_not_wake_it_twice_for_one_thread(pool, tenant) -> None:
    """Deduplication ACROSS ticks, not only inside one: the second tick collects the same thread
    (it is still inside the window) and must find its key already recorded."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4a009")
    moment = now()
    first = await world["handlers"].run_once(world["project"], moment)
    second = await world["handlers"].run_once(world["project"], moment + timedelta(seconds=30))

    assert first.events == 1 and second.events == 0
    assert second.outcome == OUTCOME_QUIET
    assert len(world["turns"].ran) == 1
