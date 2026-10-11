"""Rozmowy z niemożliwym terminem powrotu wracają bez ręcznej pracy (live 20.09).

CO TO NAPRAWIA. `account/rateLimits/read` zwrócił epokę tam, gdzie kod spodziewał się odstępu, i
powrót rozmowy zaplanowano na rok 2083. Sesja stała w WAITING_EVENT, czyli wyglądała na
czekającą: nie była błędem, nie była porażką, po prostu miała wrócić. Poprawka przeliczenia nie
cofa jobów, które już stoją w kolejce, więc ta naprawa jest osobna i startuje z procesem.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.sessions.recovery import replan_impossible_returns
from control_plane.sessions.store import (
    EVENT_STATUS,
    STATUS_WAITING_EVENT,
    SessionStore,
)

pytestmark = [pytest.mark.db, pytest.mark.p2]

NOW = datetime(2026, 9, 20, 7, 30, tzinfo=UTC)
REAL = "You've hit your usage limit. Upgrade to Pro (…) or try again at 8:33 AM."


async def _waiting_session(pool, tenant, *, message: str | None):
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
    store = SessionStore(pool)
    session = await store.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="sprawdź, gdzie warto się odezwać",
        budget={},
    )
    if message is not None:
        await store.append_event(
            organization_id=tenant["organization"],
            session_id=session.id,
            kind=EVENT_STATUS,
            payload={
                "status": STATUS_WAITING_EVENT,
                "detail": "Konto Codex doszło do limitu.",
                "powod": "LIMIT_KONTA_CODEX",
                "surowy_komunikat": message,
            },
        )
    await store.set_status(
        session.id,
        organization_id=tenant["organization"],
        status=STATUS_WAITING_EVENT,
        result_summary="czeka na limit",
    )
    return session


async def _impossible_job(pool, tenant, session_id, *, when: datetime) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority,
                     attempt_limit, idempotency_key, payload_ref, status, not_before)
                VALUES ($1, $2, 'session.turn', 'observe', 4, 1, $3, $4, 'READY', $5)
                RETURNING id
                """,
                tenant["organization"],
                tenant["workspace"],
                f"session-turn:{session_id}:limit-{uuid4().hex[:8]}",
                f"session/{session_id}",
                when,
            )
        )


async def _not_before(pool, job_id: str) -> datetime:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT not_before FROM jobs WHERE id = $1", job_id)


async def test_a_return_planned_for_2083_is_moved_to_the_hour_from_the_message(
    pool, tenant
) -> None:
    session = await _waiting_session(pool, tenant, message=REAL)
    job_id = await _impossible_job(
        pool, tenant, session.id, when=datetime(2083, 6, 15, 19, 55, tzinfo=UTC)
    )

    fixed = await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW)

    assert len(fixed) == 1, fixed
    assert fixed[0]["session_id"] == str(session.id)
    assert fixed[0]["skad"] == "message"
    when = await _not_before(pool, job_id)
    assert when < NOW + timedelta(days=1), when
    assert when.astimezone().strftime("%H:%M") == "08:33"

    events = await SessionStore(pool).events(
        organization_id=tenant["organization"], session_id=session.id
    )
    corrections = [
        e for e in events if (e.payload or {}).get("powod") == "POPRAWKA_TERMINU_POWROTU"
    ]
    assert len(corrections) == 1, "korekta stoi W ROZMOWIE, nie tylko w kolejce"
    assert "8:33" in corrections[0].payload["detail"] or "08:33" in corrections[0].payload["detail"]


async def test_without_a_message_the_return_is_a_quarter_of_an_hour_away(pool, tenant) -> None:
    """Brak komunikatu nie upoważnia do zgadywania godziny. Ślepa próba za kwadrans."""
    session = await _waiting_session(pool, tenant, message=None)
    job_id = await _impossible_job(
        pool, tenant, session.id, when=datetime(2083, 6, 15, 19, 55, tzinfo=UTC)
    )

    fixed = await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW)

    assert fixed[0]["skad"] == "blind"
    assert await _not_before(pool, job_id) == NOW + timedelta(minutes=15)


async def test_a_normal_return_is_left_alone_and_a_second_run_finds_nothing(pool, tenant) -> None:
    """Idempotencja i wąskość: powrót za godzinę nie jest niczyim problemem."""
    session = await _waiting_session(pool, tenant, message=REAL)
    soon = NOW + timedelta(hours=1)
    job_id = await _impossible_job(pool, tenant, session.id, when=soon)

    first = await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW)
    assert first == []
    assert await _not_before(pool, job_id) == soon

    far = await _impossible_job(
        pool, tenant, session.id, when=datetime(2083, 6, 15, 19, 55, tzinfo=UTC)
    )
    assert (
        len(await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW))
        == 1
    )
    assert (
        await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW) == []
    ), "druga próba nie ma czego naprawiać"
    assert await _not_before(pool, far) < NOW + timedelta(days=1)


async def test_a_session_that_is_not_waiting_is_not_touched(pool, tenant) -> None:
    """Odległy termin przy sesji w innym stanie znaczy coś innego i nie zgaduję, co."""
    store = SessionStore(pool)
    session = await _waiting_session(pool, tenant, message=REAL)
    await store.set_status(
        session.id,
        organization_id=tenant["organization"],
        status="DONE",
        result_summary="skończona",
    )
    job_id = await _impossible_job(
        pool, tenant, session.id, when=datetime(2083, 6, 15, 19, 55, tzinfo=UTC)
    )

    assert (
        await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW) == []
    )
    assert (await _not_before(pool, job_id)).year == 2083


async def test_a_return_planned_from_the_text_instead_of_the_engine_is_pulled_back(
    pool, tenant
) -> None:
    """DRUGI PRZYPADEK, zmierzony na żywo 20.09 zaraz po pierwszej naprawie.

    Rozmowa wznowiła się o 06:33:10, trafiła w limit o 06:33:14 i zaplanowała powrót na JUTRO,
    bo tekst „try again at 8:33 AM" odczytany o 8:33:14 znaczy następną dobę. Silnik w tym samym
    zdarzeniu podał 06:33:16, czyli dwie sekundy do przodu, i to on miał rację: tekst niesie samą
    godzinę, bez daty. Reguła wyboru jest już poprawiona, ale ten job stoi i sam nie zejdzie.
    """
    store = SessionStore(pool)
    session = await _waiting_session(pool, tenant, message=REAL)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_STATUS,
        payload={
            "status": STATUS_WAITING_EVENT,
            "detail": "Konto Codex doszło do limitu.",
            "powod": "LIMIT_KONTA_CODEX",
            "surowy_komunikat": REAL,
            "z_silnika": "2026-09-20T06:33:16+00:00",
            "z_komunikatu": "2026-09-21T06:33:00+00:00",
        },
    )
    job_id = await _impossible_job(
        pool, tenant, session.id, when=datetime(2026, 9, 21, 6, 34, tzinfo=UTC)
    )

    fixed = await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW)

    assert len(fixed) == 1, fixed
    assert fixed[0]["skad"] == "engine"
    when = await _not_before(pool, job_id)
    # Termin silnika już minął (naprawa biegnie później), więc powrót jest za minimalny odstęp,
    # a nie za dobę. Czekanie na okno, które się odnowiło, nie ma czego dotyczyć.
    assert when == NOW + timedelta(minutes=2), when


async def test_a_return_that_agrees_with_the_engine_is_left_alone(pool, tenant) -> None:
    """Zgodny termin nie jest niczyim problemem, a przeplanowanie go byłoby pracą bez powodu."""
    store = SessionStore(pool)
    session = await _waiting_session(pool, tenant, message=REAL)
    soon = NOW + timedelta(minutes=20)
    await store.append_event(
        organization_id=tenant["organization"],
        session_id=session.id,
        kind=EVENT_STATUS,
        payload={
            "status": STATUS_WAITING_EVENT,
            "powod": "LIMIT_KONTA_CODEX",
            "surowy_komunikat": REAL,
            "z_silnika": soon.isoformat(),
        },
    )
    job_id = await _impossible_job(pool, tenant, session.id, when=soon)

    assert (
        await replan_impossible_returns(pool, organization_id=tenant["organization"], now=NOW) == []
    )
    assert await _not_before(pool, job_id) == soon
