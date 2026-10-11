"""Współistnienie pilota i ścieżki projektowej do 24.09 (P4 etap B).

PROBLEM, KTÓRY TO ROZWIĄZUJE. Polityka pilota (D-029) żyje do 24.09 i pilot publikuje kontem
Demo_Maintainer. Po przeniesieniu na ścieżkę projektową TO SAMO KONTO jest w zakresie delegacji.
Gdyby obie ścieżki wydawały zgody jednocześnie, pod jednym postem stanęłyby dwie nasze
odpowiedzi, każda powołująca się na inny podpis, i żaden dziennik nie umiałby powiedzieć, która
była zamierzona.

DWIE WŁASNOŚCI, OBIE ZMIERZONE NA OBU STANACH POLITYKI:

  * przy ŻYWEJ polityce tik AUTONOMOUS nie uruchamia modelu i zapisuje powód w `wakeups`;
  * po wygaśnięciu albo odwołaniu polityki bramka ZNIKA SAMA. Nie ma tu niczego do odklikania,
    bo wszystko, co wymaga pamiętania o odklikaniu, zostaje nieodklikane.

BRAMKA STOI PRZED SESJĄ, NIE ZA NIĄ. Tura modelu, której wynik i tak nie mógłby zostać wydany,
jest budżetem wydanym na pewno na nic. Dotyczy wyłącznie trybu AUTONOMOUS: RESEARCH niczego nie
publikuje, a REVIEW kończy się na człowieku, który widzi obie ścieżki.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.presence.handlers import (
    OUTCOME_PILOT,
    OUTCOME_WORKED,
)
from control_plane.presence.store import SKIPPED_PILOT_OWNS_ACCOUNT

pytestmark = [pytest.mark.db, pytest.mark.p4]

ACCOUNT = "Demo_Maintainer"


def now() -> datetime:
    return datetime.now(UTC)


async def _policy_for(pool, tenant, handle: str, *, status: str = "ACTIVE", hours: int = 120):
    """Podpisana polityka pilota na wskazane konto. `hours` ujemne daje politykę wygasłą."""
    async with pool.acquire() as conn:
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"r/pilot-{uuid4().hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO publication_policies
                (organization_id, workspace_id, operator_id, status, valid_from, valid_until,
                 community_ids, account_handle, allowed_kinds, min_confidence, max_chars,
                 max_per_hour, max_per_day, judge_prompt_name, judge_prompt_sha256, source_text,
                 stopped_at)
            VALUES ($1, $2, $3, $4, now() - interval '1 day',
                    now() + ($5::text || ' hours')::interval,
                    $6, $7, ARRAY['REPLY'], 0.7, 1200, 2, 12, 'judge/v1', $8, 'słowa założyciela',
                    $9)
            """,
            tenant["organization"],
            tenant["workspace"],
            tenant["operator"],
            status,
            str(hours),
            [community_id],
            handle,
            "c" * 64,
            now() if status in ("STOPPED", "REVOKED") else None,
        )


async def _world_on_the_pilots_account(pool, tenant, **kwargs):
    """Projekt w trybie AUTONOMOUS, którego jedyne konto nazywa się jak konto pilota."""
    from test_presence_tick import _world

    world = await _world(pool, tenant, **kwargs)
    async with pool.acquire() as conn:
        account_id = world["delegation"].scope.accounts[0]
        await conn.execute(
            "UPDATE social_accounts SET external_account_id = $2 WHERE id = $1",
            account_id,
            ACCOUNT,
        )
    return world


async def test_a_live_pilot_policy_stops_the_tick_from_issuing(pool, tenant) -> None:
    from test_presence_tick import _thread

    world = await _world_on_the_pilots_account(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4b001")
    await _policy_for(pool, tenant, ACCOUNT)

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_PILOT, result.detail
    assert ACCOUNT in result.detail
    assert world["turns"].ran == [], "tura modelu poszła na pracę, której nie da się wydać"

    wakeups = await world["presence"].wakeups_for(
        organization_id=tenant["organization"], project_id=world["project"], limit=5
    )
    assert wakeups[0].skipped_reason == SKIPPED_PILOT_OWNS_ACCOUNT
    assert "zajęte przez pilota" in (wakeups[0].skipped_detail or "")
    assert wakeups[0].session_id is None


async def test_an_expired_policy_lets_the_tick_work_again(pool, tenant) -> None:
    """Bramka znika SAMA. Nie ma tu niczego do odklikania, bo wszystko, co wymaga pamiętania
    o odklikaniu, zostaje nieodklikane."""
    from test_presence_tick import _thread

    world = await _world_on_the_pilots_account(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4b002")
    await _policy_for(pool, tenant, ACCOUNT, hours=-1)  # wygasła godzinę temu

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED, result.detail
    assert world["turns"].ran == [result.session_id]


@pytest.mark.parametrize("status", ["REVOKED", "STOPPED", "EXPIRED"])
async def test_a_policy_that_is_not_active_does_not_hold_the_account(pool, tenant, status) -> None:
    from test_presence_tick import _thread

    world = await _world_on_the_pilots_account(pool, tenant)
    await _thread(pool, tenant, world, external_id=f"1p4b{status[:3].lower()}")
    await _policy_for(pool, tenant, ACCOUNT, status=status)

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED, result.detail


async def test_a_policy_on_a_different_account_does_not_hold_this_one(pool, tenant) -> None:
    """Bramka dotyczy KONTA, nie organizacji. Pilot na jednym koncie nie może zatrzymać pracy
    projektu, który pisze z zupełnie innego."""
    from test_presence_tick import _thread

    world = await _world_on_the_pilots_account(pool, tenant)
    await _thread(pool, tenant, world, external_id="1p4b003")
    await _policy_for(pool, tenant, "KtosInny")

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED, result.detail


@pytest.mark.parametrize("mode", ["REVIEW", "RESEARCH"])
async def test_the_gate_applies_only_to_the_mode_that_issues_without_a_person(
    pool, tenant, mode
) -> None:
    """RESEARCH niczego nie publikuje, a REVIEW kończy się na człowieku, który widzi obie
    ścieżki i sam rozstrzyga. Zatrzymanie ich byłoby zatrzymaniem pracy, która nie może
    zaszkodzić."""
    from test_presence_tick import _thread

    world = await _world_on_the_pilots_account(pool, tenant, mode=mode)
    await _thread(pool, tenant, world, external_id=f"1p4b{mode[:3].lower()}")
    await _policy_for(pool, tenant, ACCOUNT)

    result = await world["handlers"].run_once(world["project"], now())
    assert result.outcome == OUTCOME_WORKED, result.detail
    assert world["turns"].ran == [result.session_id]
