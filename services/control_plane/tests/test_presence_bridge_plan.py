"""Most z pilota do projektu: plan i jego wykonanie (P4 etap B).

CO JEST TU MIERZONE I CZEMU KAŻDA Z TYCH RZECZY MA ZNACZENIE:

  1. **Plan jest deterministyczny.** Dwa odczytania tych samych danych dają ten sam plan, co do
     znaku. Bez tego „obejrzyj plan, potem go wykonaj" niczego nie gwarantuje, a to jest jedyny
     powód, dla którego to polecenie ma dwa kroki.
  2. **Zakres bierze TYLKO społeczności piszące.** Pilot obserwuje trzynaście, a publikować wolno
     mu w dwóch (D-029). Wrzucenie wszystkich trzynastu do delegacji byłoby cichym poszerzeniem
     zgody przy okazji przenosin, czyli dokładnie tym, czego ten most nie ma prawa zrobić.
  3. **`--apply` NIE podpisuje delegacji.** Powstaje projekt, profil, przypięcie i przypisanie;
     zgoda zostaje kliknięciem człowieka. Test sprawdza, że w `project_delegations` nie ma nic.
  4. **`--apply` nie tworzy wniosków publikacji i nie rusza polityki ani wierszy pilota.**
     Historia pilota zostaje historią.
  5. **Plan z przeszkodami nie wykonuje się w połowie.** Połowa przenosin zostawia projekt bez
     konta albo profil bez projektu, a osoba, która to ogląda, nie wie, czy patrzy na wynik, czy
     na awarię.

FIXTURE MA REALNE KSZTAŁTY: trzynaście społeczności obserwowanych, dwie w zakresie publikacji,
konto Demo_Maintainer, tempo 2/h i 12/dobę, tak jak polityka z 13.09 (D-029).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from control_plane.presence.bridge import (
    FALLBACK_PER_DAY,
    FALLBACK_PER_HOUR,
    PROJECT_NAME,
    BridgeRefusal,
    apply_plan,
    build_plan,
)

pytestmark = [pytest.mark.db, pytest.mark.p4]

ACCOUNT = "Demo_Maintainer"
WRITABLE = ("r/kubernetes", "r/b2b_sales")
OBSERVED_ONLY = (
    "r/devops",
    "r/sre",
    "r/platform_engineering",
    "r/kubernetes_ops",
    "r/sysadmin",
    "r/selfhosted",
    "r/docker",
    "r/terraform",
    "r/aws",
    "r/startups",
    "r/saas",
)
SOURCE_TEXT = (
    "Odpowiadaj tam, gdzie umiemy realnie pomóc, najwyżej dwa razy na godzinę, "
    "i tylko w r/kubernetes oraz r/b2b_sales."
)


def now() -> datetime:
    return datetime.now(UTC)


async def _pilot_world(pool, tenant, *, with_policy: bool = True, with_account: bool = True):
    """Baza w kształcie pilota: społeczności, biegi, konto i podpisana polityka."""
    async with pool.acquire() as conn:
        # Bieg pilota wskazuje wersję profilu (0004), więc fixture musi ją mieć: bez niej klucz
        # obcy odrzuci wiersz, a test mierzyłby własny brak, a nie most.
        profile_version = await conn.fetchval(
            "INSERT INTO user_profile_versions (organization_id, workspace_id, version, "
            "knows_deeply, positions, would_never_claim, voice_samples) "
            "VALUES ($1, $2, 1, 'k8s', 'koszt przed prędkością', 'nie obiecujemy cudów', '') "
            "RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        community_ids = {}
        for key in (*WRITABLE, *OBSERVED_ONLY):
            community_ids[key] = await conn.fetchval(
                "INSERT INTO communities (organization_id, workspace_id, platform, "
                "community_key, display_name, access_mode, poll_interval_seconds) "
                "VALUES ($1, $2, 'REDDIT', $3, $4, 'ACCOUNT_REQUIRED', 600) RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                key,
                f"Reddit {key}",
            )
            # Każda obserwowana społeczność ma bieg pilota: to z nich plan czyta, co pilot widział.
            await conn.execute(
                """
                INSERT INTO pilot_runs
                    (organization_id, workspace_id, community_id, status, starts_at, ends_at,
                     poll_interval_seconds, model_calls_budget, max_generations_per_thread,
                     profile_version_id, prompt_name, prompt_sha256, paused_job_types, restore)
                VALUES ($1, $2, $3, 'FINISHED', now() - interval '2 days', now(), 600, 50, 2,
                        $4, 'pilot/v3', $5, '{}', '{}'::jsonb)
                """,
                tenant["organization"],
                tenant["workspace"],
                community_ids[key],
                profile_version,
                "d" * 64,
            )

        account_id = None
        if with_account:
            account_id = await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
                "VALUES ($1, 'REDDIT', $2) RETURNING id",
                tenant["organization"],
                ACCOUNT,
            )

        if with_policy:
            await conn.execute(
                """
                INSERT INTO publication_policies
                    (organization_id, workspace_id, operator_id, status, valid_from, valid_until,
                     community_ids, account_handle, allowed_kinds, min_confidence, max_chars,
                     max_per_hour, max_per_day, judge_prompt_name, judge_prompt_sha256,
                     source_text)
                VALUES ($1, $2, $3, 'ACTIVE', now() - interval '1 day', now() + interval '5 days',
                        $4, $5, ARRAY['REPLY'], 0.7, 1200, 2, 12, 'judge/v1', $6, $7)
                """,
                tenant["organization"],
                tenant["workspace"],
                tenant["operator"],
                [community_ids[key] for key in WRITABLE],
                ACCOUNT,
                "c" * 64,
                SOURCE_TEXT,
            )
    return {"communities": community_ids, "account_id": account_id}


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


async def test_the_plan_takes_the_writable_communities_and_nothing_more(pool, tenant) -> None:
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])

    assert plan.blockers == [], plan.blockers
    assert len(plan.communities) == len(WRITABLE) + len(OBSERVED_ONLY)
    assert sorted(one.community_key for one in plan.writable_communities) == sorted(WRITABLE)
    # I NIE WIĘCEJ: jedenaście obserwowanych zostaje poza zakresem delegacji.
    assert sorted(plan.scope.communities) == sorted(WRITABLE)
    assert set(plan.scope.actions) == {"REPLY"}


async def test_the_plan_is_deterministic(pool, tenant) -> None:
    """Dwa odczytania, ten sam plan co do znaku. Inaczej obejrzenie planu niczego nie mówi
    o tym, co zrobi `--apply`."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        first = await build_plan(conn, organization_id=tenant["organization"])
        second = await build_plan(conn, organization_id=tenant["organization"])
    assert first.to_json() == second.to_json()
    assert first.render() == second.render()
    assert first.scope_sha256 == second.scope_sha256


async def test_the_budget_comes_from_the_policys_pace(pool, tenant) -> None:
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
    assert (plan.per_hour, plan.per_day) == (2, 12)
    assert plan.budget.publications_per_hour == 2
    assert plan.budget.publications_per_day == 12


async def test_the_goal_is_the_founders_own_words(pool, tenant) -> None:
    """Cel przepisany z `source_text`, nie ulepszony: to są jego słowa."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
    assert "r/kubernetes" in plan.goal
    assert plan.goal.startswith("Odpowiadaj tam, gdzie umiemy realnie pomóc")


async def test_without_a_policy_the_plan_still_builds_with_the_signed_pace(pool, tenant) -> None:
    """Polityka wygasa 24.09, a przeniesienie nadal ma sens. Plan powstaje z biegów, a tempo
    z wartości, które pilot miał podpisane."""
    await _pilot_world(pool, tenant, with_policy=False)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
    assert plan.policy_id is None
    assert (plan.per_hour, plan.per_day) == (FALLBACK_PER_HOUR, FALLBACK_PER_DAY)
    # Bez polityki nie wiadomo, gdzie wolno pisać, więc plan MÓWI o przeszkodzie, a nie zgaduje.
    assert any("zakresie publikacji" in one for one in plan.blockers), plan.blockers


async def test_a_missing_account_is_a_blocker_and_not_a_guess(pool, tenant) -> None:
    await _pilot_world(pool, tenant, with_account=False)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
    assert any(ACCOUNT in one for one in plan.blockers), plan.blockers
    assert plan.account_id is None


async def test_historical_publications_are_counted_through_the_account(pool, tenant) -> None:
    """Powiązanie odczytowe. Wiersze pilota nie są zmieniane, więc to jest jedyny sposób, żeby
    raport projektu pokazał, co konto już opublikowało."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
    # Zero, bo ten fixture nie zasiewa publikacji. Liczba jest ODCZYTEM przez konto, a nie
    # przepisaniem wierszy: gdyby powstawała z UPDATE-u na `publication_requests`, historia
    # pilota zmieniłaby się, żeby pasować do tego, co planujemy.
    assert plan.historical_publications == 0


# ---------------------------------------------------------------------------
# Wykonanie
# ---------------------------------------------------------------------------


async def test_apply_creates_the_rows_and_does_not_sign_the_delegation(pool, tenant) -> None:
    """NAJWAŻNIEJSZY TEST TEGO PLIKU. Zgoda jest kliknięciem człowieka; skrypt, który by ją
    wystawił, wytworzyłby zgodę, której nikt nie wyraził."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn, conn.transaction():
        plan = await build_plan(conn, organization_id=tenant["organization"])
        applied = await apply_plan(conn, plan)

    async with pool.acquire() as conn:
        project = await conn.fetchrow(
            "SELECT name, mode, goal FROM projects WHERE id = $1", applied.project_id
        )
        delegations = await conn.fetchval(
            "SELECT count(*) FROM project_delegations WHERE project_id = $1", applied.project_id
        )
        assignments = await conn.fetchval(
            "SELECT count(*) FROM project_account_assignments WHERE project_id = $1 "
            "AND unassigned_at IS NULL",
            applied.project_id,
        )
        attachments = await conn.fetchval(
            "SELECT count(*) FROM person_profile_accounts WHERE person_profile_id = $1 "
            "AND detached_at IS NULL",
            applied.person_profile_id,
        )

    assert project["name"] == PROJECT_NAME
    assert assignments == 1 and attachments == 1
    assert delegations == 0, "delegacja NIE może powstać: to jest zgoda człowieka"
    # A dopóki nikt nie podpisał zakresu, projekt stoi w REVIEW: nic nie idzie samo.
    assert project["mode"] == "REVIEW"


async def test_apply_creates_no_publication_requests(pool, tenant) -> None:
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn, conn.transaction():
        plan = await build_plan(conn, organization_id=tenant["organization"])
        applied = await apply_plan(conn, plan)
    async with pool.acquire() as conn:
        requests = await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE project_id = $1", applied.project_id
        )
    assert requests == 0


async def test_apply_does_not_touch_the_policy_or_the_pilot_rows(pool, tenant) -> None:
    """Historia pilota zostaje historią."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT status, valid_until, max_per_hour, community_ids FROM publication_policies "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
        runs_before = await conn.fetchval(
            "SELECT count(*) FROM pilot_runs WHERE organization_id = $1", tenant["organization"]
        )
        async with conn.transaction():
            plan = await build_plan(conn, organization_id=tenant["organization"])
            await apply_plan(conn, plan)
        after = await conn.fetchrow(
            "SELECT status, valid_until, max_per_hour, community_ids FROM publication_policies "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
        runs_after = await conn.fetchval(
            "SELECT count(*) FROM pilot_runs WHERE organization_id = $1", tenant["organization"]
        )
    assert dict(before) == dict(after)
    assert runs_before == runs_after


async def test_apply_is_idempotent(pool, tenant) -> None:
    """Polecenie przenosin jest z tych, które ktoś uruchomi dwa razy, żeby sprawdzić."""
    await _pilot_world(pool, tenant)
    async with pool.acquire() as conn:
        async with conn.transaction():
            plan = await build_plan(conn, organization_id=tenant["organization"])
            first = await apply_plan(conn, plan)
        async with conn.transaction():
            second = await apply_plan(conn, plan)
        projects = await conn.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1 AND name = $2",
            tenant["organization"],
            PROJECT_NAME,
        )
        assignments = await conn.fetchval(
            "SELECT count(*) FROM project_account_assignments WHERE project_id = $1",
            first.project_id,
        )
    assert second.project_id == first.project_id
    assert second.attached_account is False and second.assigned_to_project is False
    assert projects == 1 and assignments == 1


async def test_a_plan_with_blockers_refuses_entirely(pool, tenant) -> None:
    """Połowa przenosin jest gorsza niż żadne."""
    await _pilot_world(pool, tenant, with_account=False)
    async with pool.acquire() as conn:
        plan = await build_plan(conn, organization_id=tenant["organization"])
        with pytest.raises(BridgeRefusal) as caught:
            async with conn.transaction():
                await apply_plan(conn, plan)
        assert ACCOUNT in str(caught.value)
        projects = await conn.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1 AND name = $2",
            tenant["organization"],
            PROJECT_NAME,
        )
    assert projects == 0, "odmowa zostawiła po sobie projekt"
