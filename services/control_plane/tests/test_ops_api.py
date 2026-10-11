"""HTTP stanu usługi i sufitu organizacji (P7 etap B).

Trzy rzeczy mierzone na granicy HTTP, bo tam dopiero widać, czy ekran dostanie prawdę:

  * ODPOWIEDŹ ZACZYNA SIĘ OD ZDANIA. „usługa działa" albo „część niedostępna: KOMPONENT od
    KIEDY". Ekran nie składa tego zdania sam, bo wtedy byłyby dwa miejsca, w których można się
    pomylić co do tego, czy usługa działa.
  * KOMPONENT, KTÓRY NIGDY NIE PRACOWAŁ, NIE MA DOSTĘPNOŚCI ZERO. Zero czyta się jak awaria;
    prawda jest taka, że to wdrożenie go nie uruchamia, i to jest inna odpowiedź.
  * SUFIT ZERO TO NIE BRAK SUFITU. Pierwsze zabrania wszystkiego, drugie nie ogranicza niczego,
    i trasa musi umieć zapisać oba.
"""

from __future__ import annotations

import pytest
from control_plane.ops.health import COMPONENT_CAPSULE, COMPONENT_DISPATCHER, HealthStore

pytestmark = [pytest.mark.db, pytest.mark.p7]


def _status_path(tenant) -> str:
    return f"/api/workspaces/{tenant['workspace']}/status"


def _limits_path(tenant) -> str:
    return f"/api/workspaces/{tenant['workspace']}/limits"


async def test_a_quiet_service_says_it_works(pool, tenant, logged_in_client) -> None:
    response = await logged_in_client.get(_status_path(tenant))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stan"] == "usługa działa"
    assert body["dziala"] is True
    assert body["niedostepne"] == []


async def test_an_open_incident_changes_the_sentence(pool, tenant, logged_in_client) -> None:
    await HealthStore(pool).open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="Reddit odpowiedział 403",
    )
    body = (await logged_in_client.get(_status_path(tenant))).json()
    assert body["dziala"] is False
    assert body["stan"].startswith("część niedostępna: CAPSULE od ")
    assert body["niedostepne"][0]["powod"] == "Reddit odpowiedział 403"


async def test_a_component_that_never_ran_has_no_availability_rather_than_zero(
    pool, tenant, logged_in_client
) -> None:
    await HealthStore(pool).beat(
        organization_id=tenant["organization"], component=COMPONENT_DISPATCHER
    )
    body = (await logged_in_client.get(_status_path(tenant))).json()
    by_name = {one["komponent"]: one for one in body["komponenty"]}

    assert by_name[COMPONENT_DISPATCHER]["uruchomiony"] is True
    assert by_name[COMPONENT_DISPATCHER]["interwalow_z_pulsem"] == 1
    # And the one nobody runs:
    assert by_name[COMPONENT_CAPSULE]["uruchomiony"] is False
    assert by_name[COMPONENT_CAPSULE]["dostepnosc"] is None, "zero czytałoby się jak awaria"


async def test_setting_and_clearing_the_organization_ceiling(
    pool, tenant, logged_in_client
) -> None:
    response = await logged_in_client.put(
        _limits_path(tenant), json={"model_turns_per_day": 20, "publications_per_day": 6}
    )
    assert response.status_code == 200, response.text
    limits = response.json()["budzet_organizacji"]["limity"]
    assert limits == {"tury_modelu_doba": 20, "publikacje_doba": 6}

    cleared = await logged_in_client.put(_limits_path(tenant), json={})
    assert cleared.json()["budzet_organizacji"]["limity"] == {
        "tury_modelu_doba": None,
        "publikacje_doba": None,
    }


async def test_a_ceiling_of_zero_is_saved_as_zero_and_not_as_absent(
    pool, tenant, logged_in_client
) -> None:
    """Zero zabrania wszystkiego i jest legalnym ustawieniem: „zatrzymaj wszystko, ale nie
    odwołuj powierzeń". Zapisanie go jako braku sufitu byłoby odwrotnością tej decyzji."""
    await logged_in_client.put(_limits_path(tenant), json={"model_turns_per_day": 0})
    body = (await logged_in_client.get(_status_path(tenant))).json()
    assert body["budzet_organizacji"]["limity"]["tury_modelu_doba"] == 0
    assert body["budzet_organizacji"]["wyczerpany"] is not None


async def test_changing_the_ceiling_is_written_to_the_audit_log(
    pool, tenant, logged_in_client
) -> None:
    await logged_in_client.put(_limits_path(tenant), json={"model_turns_per_day": 7})
    async with pool.acquire() as conn:
        kinds = await conn.fetch(
            "SELECT kind FROM audit_events WHERE organization_id = $1 ORDER BY occurred_at DESC "
            "LIMIT 5",
            tenant["organization"],
        )
    assert "ORG_LIMITS_CHANGED" in [row["kind"] for row in kinds]


async def test_a_limit_beyond_the_bound_is_refused(pool, tenant, logged_in_client) -> None:
    """Sufit, którego nikt nigdy nie dosięgnie, nie jest sufitem. Ograniczony, żeby literówka
    go nie wyprodukowała."""
    response = await logged_in_client.put(
        _limits_path(tenant), json={"model_turns_per_day": 10_000_000}
    )
    assert response.status_code == 422


async def test_the_status_of_another_organizations_workspace_does_not_exist(
    pool, tenant, logged_in_client
) -> None:
    async with pool.acquire() as conn:
        other_org = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-p7') RETURNING id"
        )
        other_ws = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            other_org,
        )
    response = await logged_in_client.get(f"/api/workspaces/{other_ws}/status")
    assert response.status_code == 404
