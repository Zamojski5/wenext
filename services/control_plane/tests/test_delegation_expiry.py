"""The delegation ends at its deadline because the PRODUCT stops it (0033), and background work
does not start when it must not (26.09).

Measured failures behind each test:

  * the 22/23.09 supervision was meant to end at 09:10 UTC and ended an hour later, when a human
    pressed pause; the table had no deadline to keep (`docs/NADZOR-12H-2026-09-22.md`);
  * a background turn ordered BEFORE a pause (a resume after the Codex limit, a wait for an
    engine) ran after it, because only the tick looked at the delegation;
  * a sentence typed during a turn reached that turn through `turn/steer`, and then its own
    `message-{seq}` job ran one more full model turn with nothing to answer;
  * a tick with a new event opened a session while the Codex account sat at its usage limit.

The engine is never started in this file: every test that could reach it hands the handler a
client factory that fails loudly, so "the model did not run" is measured, not assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.api.delegations import DEFAULT_TERM, MAX_TERM, signed_deadline
from control_plane.presence.scope import Budget, Scope, digest
from control_plane.sessions.handlers import (
    OUTCOME_NOTHING_NEW,
    OUTCOME_STOPPED,
    SessionTurnHandlers,
)
from control_plane.sessions.store import (
    EVENT_INTERRUPT,
    EVENT_STATUS,
    EVENT_USER_MESSAGE,
    SESSION_PRESENCE,
    SessionStore,
)
from fastapi import HTTPException
from tests.test_presence_tick import COMMUNITY, _thread, _wakeups, _world

NOW = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)


# -- pure: what is signed ----------------------------------------------------------------


def test_a_deadline_changes_the_digest_and_its_absence_keeps_the_old_one() -> None:
    """Same scope, a week and a year: two permissions. And a signature older than 0033 keeps the
    digest it was signed with, because the key is added only when there is a deadline."""
    scope = Scope(accounts=(uuid4(),), communities=(COMMUNITY,), actions=("REPLY",))
    budget = Budget(publications_per_day=3)
    old = digest(mode="AUTONOMOUS", scope=scope, budget=budget)
    week = digest(mode="AUTONOMOUS", scope=scope, budget=budget, expires_at=NOW + DEFAULT_TERM)
    year = digest(mode="AUTONOMOUS", scope=scope, budget=budget, expires_at=NOW + MAX_TERM)
    assert old == digest(mode="AUTONOMOUS", scope=scope, budget=budget, expires_at=None)
    assert len({old, week, year}) == 3
    # Microseconds do not make a different signature: the stored deadline is truncated.
    assert week == digest(
        mode="AUTONOMOUS",
        scope=scope,
        budget=budget,
        expires_at=(NOW + DEFAULT_TERM).replace(microsecond=123456),
    )


def test_the_signed_deadline_has_a_default_and_bounds() -> None:
    assert signed_deadline(None, now=NOW) == NOW + DEFAULT_TERM
    assert signed_deadline(NOW + timedelta(hours=24), now=NOW) == NOW + timedelta(hours=24)
    for wrong in (NOW + timedelta(minutes=1), NOW + timedelta(days=91), NOW - timedelta(hours=1)):
        with pytest.raises(HTTPException) as refused:
            signed_deadline(wrong, now=NOW)
        assert refused.value.status_code == 422
    with pytest.raises(HTTPException):
        signed_deadline(datetime(2026, 10, 1, 12, 0), now=NOW)  # no timezone


# -- the database: the deadline is enforced by the product ----------------------------------


async def _expire(pool, delegation_id) -> None:
    """Move a signature into the past: signed two hours ago, due an hour ago (the CHECK wants
    the deadline after the signature, so both move)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_delegations SET signed_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 hour' WHERE id = $1",
            delegation_id,
        )


@pytest.mark.db
async def test_a_signature_carries_its_deadline_and_is_live_until_then(pool, tenant) -> None:
    world = await _world(pool, tenant)
    presence = world["presence"]
    deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    await presence.revoke(
        world["delegation"].id,
        organization_id=tenant["organization"],
        at=datetime.now(UTC),
    )
    signed = await presence.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        mode="AUTONOMOUS",
        scope=world["delegation"].scope,
        budget=world["delegation"].budget,
        operator_id=tenant["operator"],
        expires_at=deadline,
    )
    assert signed.expires_at == deadline
    assert signed.live and not signed.expired and signed.state == "aktywna"
    assert signed.scope_sha256 == digest(
        mode="AUTONOMOUS", scope=signed.scope, budget=signed.budget, expires_at=deadline
    )


@pytest.mark.db
async def test_past_its_deadline_the_tick_stops_records_it_once_and_interrupts(
    pool, tenant
) -> None:
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1exp001")
    sessions = SessionStore(pool)
    running = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        goal="praca w tle",
        budget={},
        kind=SESSION_PRESENCE,
    )
    await _expire(pool, world["delegation"].id)

    current = await world["presence"].delegation_for(
        organization_id=tenant["organization"], project_id=world["project"]
    )
    assert current is not None and current.expired and not current.live
    assert current.state == "wygasła"

    for _ in range(2):
        result = await world["handlers"].run_once(world["project"], datetime.now(UTC))
        assert "wygasła" in result.detail
    assert world["turns"].ran == [], "po terminie model nie rusza"
    assert await _wakeups(world) == ()

    async with pool.acquire() as conn:
        audits = await conn.fetchval(
            "SELECT count(*) FROM audit_events WHERE kind = 'PROJECT_DELEGATION_EXPIRED' "
            "AND subject_ref = $1",
            f"delegation:{world['delegation'].id}",
        )
    assert audits == 1, "termin zapisany raz, choć tik przyszedł dwa razy"
    events = await sessions.events(organization_id=tenant["organization"], session_id=running.id)
    assert [e.kind for e in events].count(EVENT_INTERRUPT) == 1


def _no_engine(**_kwargs):
    raise AssertionError("silnik nie miał prawa ruszyć w tym teście")


def _turns(pool, tenant, tmp_path: Path) -> SessionTurnHandlers:
    async def home(_session):
        path = tmp_path / "home"
        path.mkdir(exist_ok=True)
        return path

    return SessionTurnHandlers(
        pool=pool,
        sessions=SessionStore(pool),
        queue=None,  # type: ignore[arg-type]  # nothing is chained in these tests
        organization_id=tenant["organization"],
        dsn="postgresql://unused",
        codex_home_for=home,
        client_factory=_no_engine,
        capability_dir=tmp_path / "caps",
    )


@pytest.mark.db
async def test_a_background_turn_ordered_before_the_deadline_does_not_run_after_it(
    pool, tenant, tmp_path
) -> None:
    world = await _world(pool, tenant)
    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        goal="powrót po limicie konta",
        budget={},
        kind=SESSION_PRESENCE,
    )
    await _expire(pool, world["delegation"].id)

    result = await _turns(pool, tenant, tmp_path).run_once(session.id, reason="limit-22:00")

    assert result.outcome == OUTCOME_STOPPED, result.detail
    assert "wygasła" in result.detail
    after = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert after is not None and after.status == "DONE"


@pytest.mark.db
async def test_a_paused_delegation_parks_a_background_turn_without_the_engine(
    pool, tenant, tmp_path
) -> None:
    world = await _world(pool, tenant)
    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        goal="czekanie na silnik",
        budget={},
        kind=SESSION_PRESENCE,
    )
    await world["presence"].set_paused(
        world["delegation"].id,
        organization_id=tenant["organization"],
        paused=True,
        at=datetime.now(UTC),
    )

    result = await _turns(pool, tenant, tmp_path).run_once(session.id, reason="silnik-1")

    assert result.outcome == OUTCOME_STOPPED
    after = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert after is not None and after.status == "PAUSED"
    events = await sessions.events(organization_id=tenant["organization"], session_id=session.id)
    assert any(e.kind == EVENT_STATUS and "wstrzymana" in str(e.payload) for e in events)


@pytest.mark.db
async def test_a_message_already_steered_into_a_turn_does_not_order_another_one(
    pool, tenant, tmp_path
) -> None:
    """The `message-{seq}` job arrives after the turn that already received the sentence."""
    world = await _world(pool, tenant)
    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        goal="rozmowa z człowiekiem",
        budget={},
    )
    message = await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": "dopowiedzenie w trakcie tury"},
    )
    # The marker the steering writes after `turn/steer` accepted the sentence.
    await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_STATUS,
        payload={"wenext_wejscie_do_seq": message.seq, "level": "engine"},
    )
    await sessions.set_status(
        session.id, organization_id=tenant["organization"], status="WAITING_USER"
    )

    result = await _turns(pool, tenant, tmp_path).run_once(
        session.id, reason=f"message-{message.seq}"
    )

    assert result.outcome == OUTCOME_NOTHING_NEW, result.detail
    after = await sessions.get(organization_id=tenant["organization"], session_id=session.id)
    assert after is not None and after.status == "WAITING_USER"


@pytest.mark.db
async def test_a_tick_waits_for_the_codex_limit_instead_of_opening_a_session(pool, tenant) -> None:
    """A recorded account limit still in force skips the tick with its own reason, distinct from
    the project's budget, and the event is collected again after the reset."""
    world = await _world(pool, tenant)
    await _thread(pool, tenant, world, external_id="1exp002")
    sessions = SessionStore(pool)
    async with pool.acquire() as conn:
        connection_id = await conn.fetchval(
            "SELECT id FROM codex_connections WHERE organization_id = $1 AND workspace_id = $2 "
            "AND status = 'CONNECTED' ORDER BY created_at DESC LIMIT 1",
            tenant["organization"],
            tenant["workspace"],
        )
    limited = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=world["project"],
        goal="tura, która trafiła w limit",
        budget={},
        codex_connection_id=connection_id,
    )
    back_at = datetime.now(UTC) + timedelta(hours=2)
    await sessions.append_event(
        organization_id=tenant["organization"],
        session_id=limited.id,
        kind=EVENT_STATUS,
        payload={
            "status": "WAITING_EVENT",
            "powod": "LIMIT_KONTA_CODEX",
            "odnowi_sie": back_at.isoformat(),
        },
    )

    result = await world["handlers"].run_once(world["project"], datetime.now(UTC))

    assert result.outcome == "LIMIT_SILNIKA", result.detail
    assert "nie budżet projektu" in result.detail
    assert world["turns"].ran == []
    wakeups = await _wakeups(world)
    assert wakeups[0].skipped_reason == "ENGINE_LIMIT"
    assert wakeups[0].session_id is None, "zdarzenie wróci w następnym tiku po odnowieniu"
