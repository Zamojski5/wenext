"""Księga kosztów i sufit organizacji (P7 etap B).

DWIE WŁASNOŚCI, KTÓRE DECYDUJĄ, CZY TE LICZBY SĄ COKOLWIEK WARTE:

  1. **Księga jest wyprowadzana z tego, co się wydarzyło, i idempotentna.** Nikt nie woła
     `record_cost`, więc nie da się go zapomnieć; zasilanie czyta te same tabele co raporty.
     Drugie zasilanie tego samego okna NIE podwaja rachunku, bo bez tego „zasilanie z tabel"
     byłoby gorsze od ręcznego zapisu, który przynajmniej dzieje się raz.
  2. **Sufit organizacji jest drugą kreską, nigdy sumą z budżetem projektu.** Dziesięć projektów
     z limitem sześciu publikacji to sześćdziesiąt publikacji, których nikt nie podpisał. Odmowa
     mówi, KTÓRY sufit się wyczerpał, bo to różnica między „podnieś limit projektu" a „poczekaj".
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.ops.ledger import refill
from control_plane.ops.limits import (
    KIND_MODEL_TURN,
    KIND_PUBLICATION,
    OrgLimits,
    OrgSpend,
    org_limits_for,
    org_spend,
)

pytestmark = [pytest.mark.db, pytest.mark.p7]


def now() -> datetime:
    return datetime.now(UTC)


async def _project_with_wakeups(pool, tenant, *, count: int, ran: bool = True):
    """A project and `count` wake-ups, half of what the ledger derives model turns from."""
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        for n in range(count):
            session_id = None
            if ran:
                session_id = await conn.fetchval(
                    "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, "
                    "goal, kind) VALUES ($1, $2, $3, 'praca', 'PRESENCE') RETURNING id",
                    tenant["organization"],
                    tenant["workspace"],
                    project_id,
                )
            await conn.execute(
                "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason, "
                "session_id, skipped_reason, created_at) "
                "VALUES ($1, $2, $3, 'NEW_EVENT', $4, $5, now() - ($6::text || ' minutes')::interval)",
                tenant["organization"],
                tenant["workspace"],
                project_id,
                session_id,
                None if ran else "NO_EVENTS",
                str(n),
            )
    return project_id


# ---------------------------------------------------------------------------
# Księga
# ---------------------------------------------------------------------------


async def test_model_turns_are_derived_from_wakeups_that_ran(pool, tenant) -> None:
    await _project_with_wakeups(pool, tenant, count=4, ran=True)
    filled = await refill(pool, organization_id=tenant["organization"])
    assert filled.model_turns == 4, filled.to_json()

    async with pool.acquire() as conn:
        spend = await org_spend(conn, organization_id=tenant["organization"])
    assert spend.model_turns_today == 4


async def test_a_wakeup_that_did_not_run_the_model_costs_nothing(pool, tenant) -> None:
    """Cichy tik jest wynikiem, ale nie kosztem. Policzenie go zawyżałoby koszt na publikację
    dokładnie w tych tygodniach, w których agent słusznie nic nie robił."""
    await _project_with_wakeups(pool, tenant, count=5, ran=False)
    filled = await refill(pool, organization_id=tenant["organization"])
    assert filled.model_turns == 0, filled.to_json()


async def test_refilling_twice_does_not_double_the_bill(pool, tenant) -> None:
    """Zasilanie po awarii i zasilanie zaplanowane to ten sam kod. Bez tej własności każdy
    restart podnosiłby rachunek."""
    await _project_with_wakeups(pool, tenant, count=3, ran=True)
    first = await refill(pool, organization_id=tenant["organization"])
    second = await refill(pool, organization_id=tenant["organization"])
    assert first.model_turns == 3
    assert second.model_turns == 0, "drugie zasilanie nie dopisało niczego"

    async with pool.acquire() as conn:
        rows = await conn.fetchval(
            "SELECT count(*) FROM cost_ledger WHERE organization_id = $1 AND kind = $2",
            tenant["organization"],
            KIND_MODEL_TURN,
        )
    assert rows == 3


async def test_the_ledger_points_back_at_what_it_charged_for(pool, tenant) -> None:
    """Suma, której nie da się rozłożyć z powrotem na zdarzenia, jest liczbą, nie rachunkiem."""
    await _project_with_wakeups(pool, tenant, count=2, ran=True)
    await refill(pool, organization_id=tenant["organization"])
    async with pool.acquire() as conn:
        refs = await conn.fetch(
            "SELECT source_ref FROM cost_ledger WHERE organization_id = $1 AND kind = $2",
            tenant["organization"],
            KIND_MODEL_TURN,
        )
    assert all(row["source_ref"].startswith("wakeup:") for row in refs), [
        row["source_ref"] for row in refs
    ]


async def test_a_window_bounds_what_a_refill_looks_at(pool, tenant) -> None:
    """Zasilanie tygodnia nie przepisuje całej historii przy każdym uruchomieniu."""
    project_id = await _project_with_wakeups(pool, tenant, count=2, ran=True)
    async with pool.acquire() as conn:
        old_session = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'stare', 'PRESENCE') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
        await conn.execute(
            "INSERT INTO wakeups (organization_id, workspace_id, project_id, reason, session_id, "
            "created_at) VALUES ($1, $2, $3, 'NEW_EVENT', $4, now() - interval '30 days')",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            old_session,
        )
    filled = await refill(pool, organization_id=tenant["organization"])
    assert filled.model_turns == 2, "sprzed miesiąca nie wchodzi do okna doby"


# ---------------------------------------------------------------------------
# Sufit organizacji
# ---------------------------------------------------------------------------


def test_an_unset_ceiling_is_not_zero() -> None:
    """Brak sufitu i sufit zero to dwie różne rzeczy: pierwsze nie ogranicza, drugie zabrania."""
    assert (
        OrgSpend(model_turns_today=99, publications_today=99, limits=OrgLimits()).exhausted()
        is None
    )
    zero = OrgSpend(
        model_turns_today=0, publications_today=0, limits=OrgLimits(model_turns_per_day=0)
    )
    assert zero.exhausted() is not None


def test_the_refusal_names_which_ceiling_and_the_numbers() -> None:
    spend = OrgSpend(
        model_turns_today=20,
        publications_today=1,
        limits=OrgLimits(model_turns_per_day=20, publications_per_day=6),
    )
    reason = spend.exhausted()
    assert "sufit organizacji" in reason
    assert "20 z 20" in reason


async def test_a_missing_row_means_no_ceiling(pool, tenant) -> None:
    async with pool.acquire() as conn:
        limits = await org_limits_for(conn, organization_id=tenant["organization"])
    assert limits.model_turns_per_day is None
    assert limits.publications_per_day is None


async def test_the_ceiling_counts_the_whole_organization_not_one_project(pool, tenant) -> None:
    """DWA PROJEKTY POD JEDNYM SUFITEM. Każdy z nich mieści się we własnym budżecie; razem nie."""
    await _project_with_wakeups(pool, tenant, count=3, ran=True)
    await _project_with_wakeups(pool, tenant, count=3, ran=True)
    await refill(pool, organization_id=tenant["organization"])
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, 5)",
            tenant["organization"],
        )
        spend = await org_spend(conn, organization_id=tenant["organization"])
    assert spend.model_turns_today == 6
    assert spend.exhausted() is not None, "sześć tur pod sufitem pięciu"


async def test_the_window_is_rolling_not_a_calendar_day(pool, tenant) -> None:
    """„Dwadzieścia na dobę" resetowane o północy pozwala na czterdzieści między 23:00 a 01:00."""
    project_id = await _project_with_wakeups(pool, tenant, count=1, ran=True)
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, kind) "
            "VALUES ($1, $2, $3, 'wczoraj', 'PRESENCE') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
        )
        await conn.execute(
            "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
            "VALUES ($1, $2, 1, $3, now() - interval '25 hours')",
            tenant["organization"],
            KIND_MODEL_TURN,
            f"session:{session_id}",
        )
    await refill(pool, organization_id=tenant["organization"])
    async with pool.acquire() as conn:
        spend = await org_spend(conn, organization_id=tenant["organization"])
    assert spend.model_turns_today == 1, "sprzed 25 godzin wypadło z okna doby"


async def test_publications_reach_the_ceiling_too(pool, tenant) -> None:
    async with pool.acquire() as conn:
        for _ in range(4):
            await conn.execute(
                "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
                "VALUES ($1, $2, 1, $3, now())",
                tenant["organization"],
                KIND_PUBLICATION,
                f"attempt:{uuid4()}",
            )
        await conn.execute(
            "INSERT INTO org_limits (organization_id, publications_per_day) VALUES ($1, 4)",
            tenant["organization"],
        )
        spend = await org_spend(conn, organization_id=tenant["organization"])
    reason = spend.exhausted()
    assert reason is not None and "publikacje" in reason


# ---------------------------------------------------------------------------
# Sufit organizacji w tiku obecności
# ---------------------------------------------------------------------------


async def test_the_tick_refuses_with_the_organizations_sentence_not_the_projects(
    pool, tenant
) -> None:
    """DWA SUFITY, JEDNA ODMOWA, I MÓWI KTÓRY. Projekt ma budżet, w którym się mieści; to sufit
    organizacji go zatrzymuje, i wiersz `wakeups` ma to powiedzieć, bo inaczej założyciel podnosi
    limit projektu i nic się nie zmienia."""
    from control_plane.presence.handlers import OUTCOME_BUDGET
    from test_presence_tick import _thread, _world

    world = await _world(pool, tenant, budget={"model_turns_per_day": 100})
    await _thread(pool, tenant, world, external_id="1p7a001")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, 0)",
            tenant["organization"],
        )

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_BUDGET, result.detail
    assert "sufit organizacji" in result.detail, result.detail
    assert world["turns"].ran == [], "model nie ruszył pod wyczerpanym sufitem organizacji"

    wakeups = await world["presence"].wakeups_for(
        organization_id=tenant["organization"], project_id=world["project"], limit=5
    )
    assert "sufit organizacji" in (wakeups[0].skipped_detail or "")


async def test_a_project_under_both_ceilings_still_runs(pool, tenant) -> None:
    """Sufit, który zatrzymuje pracę mieszczącą się w obu budżetach, byłby gorszy niż jego brak."""
    from control_plane.presence.handlers import OUTCOME_WORKED
    from test_presence_tick import _thread, _world

    world = await _world(pool, tenant, budget={"model_turns_per_day": 10})
    await _thread(pool, tenant, world, external_id="1p7a002")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, 50)",
            tenant["organization"],
        )
    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED, result.detail
