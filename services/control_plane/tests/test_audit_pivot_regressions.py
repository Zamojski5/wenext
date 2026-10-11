"""Regresje z AUDYTU NIEZALEŻNEGO AGENTA z 19.09.2026 (output/audyt-pivot-2026-09-19/).

Ten plik jest przeniesiony niemal bez zmian z artefaktu audytu i celowo NIE jest przepisany
naszymi słowami: to jest cudzy pomiar, a przepisanie go zamieniłoby dowód w interpretację.
Zmienione są wyłącznie ścieżki, żeby dało się go uruchomić w tym pakiecie.

WSZYSTKIE CZTERY PADAŁY NA DRZEWIE Z 19.09 i każdy opisuje inną obietnicę, której produkt nie
dotrzymywał:

  1. korekta wpisana W TRAKCIE tury ginęła: następna tura dostawała tylko pierwotny cel;
  2. zdarzenie odłożone przy braku połączenia Codex nie wracało po jego przywróceniu;
  3. zdarzenia ponad limit jednego tiku przepadały zamiast czekać na następny;
  4. po śmierci workera reaper kończył joba, a sesja zostawała RUNNING i nie dawała się wznowić.

Każda z tych rzeczy była zmierzona, nie wydedukowana. Trzymamy je razem, w jednym pliku, żeby
było widać, że pochodzą z jednego dnia i jednej sesji audytu.
"""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load("audit_fixtures", "services/control_plane/tests/conftest.py")
presence = load("audit_presence", "services/control_plane/tests/test_presence_tick.py")
session_helpers = load("audit_sessions", "services/control_plane/tests/test_session_turn_e2e.py")


def _running_turn(store, tenant, session):
    """Warunek dla `_until`: sesja jest RUNNING i ma już wątek silnika."""

    async def check():
        current = await store.get(organization_id=tenant["organization"], session_id=session.id)
        if current is not None and current.status == "RUNNING" and current.engine_thread_id:
            return current
        return None

    return check


pool = fixtures.pool
tenant = fixtures.tenant
pytestmark = [pytest.mark.db, pytest.mark.asyncio]

# Importy stoją PO `load(...)`, bo ten plik najpierw rejestruje sąsiednie moduły testowe w
# `sys.modules` pod własnymi nazwami. Przeniesienie ich na górę zmieniłoby kolejność ładowania,
# a nie tylko układ pliku, więc zostaje wyciszenie z powodem.
from control_plane.jobs.postgres_transport import PostgresJobTransport  # noqa: E402
from control_plane.jobs.reaper import JobLeaseReaper  # noqa: E402
from control_plane.jobs.store import JobStore  # noqa: E402
from control_plane.sessions.handlers import schedule_turn  # noqa: E402
from control_plane.sessions.store import (  # noqa: E402
    EVENT_ASSISTANT_MESSAGE,
    EVENT_USER_MESSAGE,
    SessionStore,
)


async def test_audit_pivot_message_during_turn_is_not_answered_by_older_turn(
    pool, tenant, tmp_path
):
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await session_helpers._project(pool, tenant),
        goal="Pierwotny cel: zaproponuj tematy",
        budget={"max_turns": 5},
    )
    handlers = session_helpers._handlers(
        pool, tenant, tmp_path, os.environ["WENEXT_TEST_DSN"], home=None
    )
    first_input = await handlers._message_for_turn(session)
    correction = "Zmiana podczas pracy: zrób research Kubernetes zamiast propozycji tematów."
    assert correction not in first_input
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_USER_MESSAGE,
        payload={"text": correction},
    )
    # This answer belongs to the input captured BEFORE the correction arrived.
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_ASSISTANT_MESSAGE,
        payload={"text": "Proponuję trzy tematy."},
    )
    next_input = await handlers._message_for_turn(session)
    assert correction in next_input, f"Korekta zniknęła z następnej tury: {next_input!r}"


async def test_audit_pivot_deferred_event_returns_after_connection_restored(pool, tenant):
    world = await presence._world(pool, tenant, connected=False)
    await presence._thread(pool, tenant, world, external_id="audit_deferred")
    first = await world["handlers"].run_once(world["project"], presence.now())
    assert first.outcome == presence.OUTCOME_NO_CONNECTION, first
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO codex_connections (organization_id, workspace_id, label, home_ref, status) "
            "VALUES ($1, $2, 'audit', $3, 'CONNECTED')",
            tenant["organization"],
            tenant["workspace"],
            str(uuid4()),
        )
    second = await world["handlers"].run_once(world["project"], presence.now())
    assert second.outcome == presence.OUTCOME_WORKED, (
        f"Zdarzenie utracone po podłączeniu Codexa: {second}; tury={world['turns'].ran}"
    )


async def test_audit_pivot_overflow_events_remain_for_next_tick(pool, tenant):
    world = await presence._world(pool, tenant)
    for i in range(13):
        await presence._thread(pool, tenant, world, external_id=f"audit_overflow_{i}")
    first = await world["handlers"].run_once(world["project"], presence.now())
    assert first.outcome == presence.OUTCOME_WORKED and first.events == 12, first
    second = await world["handlers"].run_once(world["project"], presence.now())
    assert second.outcome == presence.OUTCOME_WORKED and second.events == 1, (
        f"13. zdarzenie nie dotarło do modelu: {second}"
    )


async def test_audit_pivot_real_reaper_recovers_session_after_worker_death(
    pool, tenant, tmp_path, monkeypatch
):
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=await session_helpers._project(pool, tenant),
        goal="Kontynuuj po restarcie",
        budget={"max_turns": 5, "max_minutes": 2, "max_tool_calls": 20},
    )
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    await schedule_turn(
        queue,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        session_id=session.id,
        reason="audit",
    )
    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"], worker=uuid4(), limit=1, lease_seconds=0.1
    )
    assert len(batch.claims) == 1
    script = session_helpers._script(
        tmp_path,
        {
            "thread_id": "audit_restart",
            "default_turn": {
                "steps": [{"delay_ms": 10000}, {"message": "Gotowe"}],
                "status": "completed",
            },
        },
    )
    monkeypatch.setenv("WENEXT_FAKE_APP_SERVER_SCRIPT", str(script))
    handlers = session_helpers._handlers(
        pool, tenant, tmp_path, os.environ["WENEXT_TEST_DSN"], home=tmp_path / "home"
    )
    task = asyncio.create_task(handlers.run_once(session.id))
    try:
        # CZEKAMY NA ZDARZENIE, NIE NA LICZBĘ OBROTÓW (E8). Pętla `for _ in range(100)` liczyła
        # obroty, a każdy z nich robi też zapytanie do bazy: na obciążonym hoście wychodziła
        # zanim tura zdążyła ruszyć i test przewracał się na asercji o czymś innym.
        await session_helpers._until(
            _running_turn(store, tenant, session), what="ruszająca tura z wątkiem silnika"
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0.2)
    report = await JobLeaseReaper(pool, organization_id=tenant["organization"]).reap_once()
    assert len(report.reaped) == 1, report
    after = await store.get(organization_id=tenant["organization"], session_id=session.id)
    assert after.status != "RUNNING", (
        f"Nie ma workera, reaper zakończył job ({report.reaped[0].outcome}), "
        f"ale sesja nadal {after.status}; zwykłe resume odmawia dla tego stanu."
    )
