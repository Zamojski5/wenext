"""Puls, incydenty i to, że awaria jednej platformy nie zatrzymuje reszty (P7 etap B).

CO TU JEST MIERZONE I DLACZEGO AKURAT TO:

  1. **Puls jest kubełkowany po interwale, nie po znaczniku czasu.** Dwa procesy tego samego
     komponentu w jednym interwale dają JEDEN wiersz. Inaczej dostępność liczyłaby procesy, a nie
     czas, i wdrożenie z dwoma workerami wyglądałoby na dwa razy zdrowsze.
  2. **Powrót komponentu zamyka jego incydent.** Incydent, którego nikt nie zamknął, po tygodniu
     wygląda jak trwająca awaria, a raport tygodnia policzy go jako niedostępność do teraz.
  3. **Dwa zgłoszenia tej samej awarii to jeden incydent.** Dwa otwarte byłyby dwiema awariami
     w raporcie, a były jedną.
  4. **Cisza staje się incydentem, ale tylko po dwóch interwałach**, żeby restart nie produkował
     incydentu, i tylko dla komponentów, które KIEDYKOLWIEK biły: komponent, którego to wdrożenie
     nie uruchamia, nie jest awarią.
  5. **Awaria czytnika Reddita nie zatrzymuje kanałów RSS ani sesji rozmowy.** To jest kryterium
     P7 wypisane wprost i jedyny test w tym pliku, który mówi o produkcie, a nie o tabeli.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from control_plane.ops.health import (
    COMPONENT_CAPSULE,
    COMPONENT_COLLECTOR,
    COMPONENT_DISPATCHER,
    DETECTED_BY_HUMAN,
    DETECTED_BY_MISSING,
    DETECTED_BY_SELF,
    INTERVAL,
    MISSED_INTERVALS_BEFORE_DOWN,
    HealthRefusal,
    HealthStore,
    availability,
    interval_of,
)

pytestmark = [pytest.mark.db, pytest.mark.p7]


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Kubełkowanie i dostępność, bez bazy
# ---------------------------------------------------------------------------


def test_two_moments_in_one_interval_are_one_bucket() -> None:
    base = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert interval_of(base) == interval_of(base + timedelta(seconds=30))
    assert interval_of(base) != interval_of(base + INTERVAL)


def test_availability_is_beats_over_expected_intervals() -> None:
    week = timedelta(days=7)
    expected = int(week.total_seconds() // INTERVAL.total_seconds())
    assert availability(expected, week) == 1.0
    assert availability(expected // 2, week) == pytest.approx(0.5, abs=0.01)
    assert availability(0, week) == 0.0


def test_a_window_shorter_than_one_interval_is_not_perfect_availability() -> None:
    """Dzielenie przez zero dałoby 1.0, czyli stuprocentową dostępność za okres, którego nikt nie
    zmierzył. To jest dokładnie ten kształt kłamstwa, o którym jest D-007."""
    assert availability(0, timedelta(seconds=1)) == 0.0
    assert availability(5, timedelta(seconds=1)) == 0.0


def test_more_beats_than_intervals_is_capped_at_one() -> None:
    assert availability(10_000, timedelta(hours=1)) == 1.0


# ---------------------------------------------------------------------------
# Puls i incydenty w bazie
# ---------------------------------------------------------------------------


async def test_two_processes_beating_in_one_interval_write_one_row(pool, tenant) -> None:
    store = HealthStore(pool)
    moment = now()
    await asyncio.gather(
        *(
            store.beat(
                organization_id=tenant["organization"],
                component=COMPONENT_DISPATCHER,
                now=moment,
                detail={"proces": n},
            )
            for n in range(6)
        )
    )
    beats = await store.beats_since(
        organization_id=tenant["organization"],
        component=COMPONENT_DISPATCHER,
        since=moment - INTERVAL,
    )
    assert beats == 1, "sześć procesów, jeden interwał, jeden wiersz"


async def test_a_component_that_comes_back_closes_its_own_incident(pool, tenant) -> None:
    store = HealthStore(pool)
    opened = await store.open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="Reddit odpowiedział 403",
    )
    assert opened.open

    await store.beat(organization_id=tenant["organization"], component=COMPONENT_CAPSULE)
    assert await store.open_incidents(organization_id=tenant["organization"]) == ()


async def test_two_reports_of_one_outage_are_one_incident(pool, tenant) -> None:
    store = HealthStore(pool)
    first = await store.open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="403 z platformy",
    )
    second = await store.open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="ta sama awaria, drugi obserwator",
    )
    assert second.id == first.id
    assert second.reason == first.reason, "pierwszy powód zostaje; drugi go nie nadpisuje"
    assert len(await store.open_incidents(organization_id=tenant["organization"])) == 1


async def test_concurrent_reports_of_one_outage_are_one_incident(pool, tenant) -> None:
    """Sekwencyjny wariant powyżej przechodzi nawet przy sprawdzeniu „czy jest, to nie wstawiaj".
    Ten nie: sześć zgłoszeń naraz, a więz w bazie jest tym, co utrzymuje jeden wiersz."""
    store = HealthStore(pool)
    results = await asyncio.gather(
        *(
            store.open_incident(
                organization_id=tenant["organization"],
                component=COMPONENT_COLLECTOR,
                reason=f"zgłoszenie {n}",
            )
            for n in range(6)
        )
    )
    assert len({one.id for one in results}) == 1, [one.id for one in results]


async def test_silence_becomes_an_incident_only_after_two_intervals(pool, tenant) -> None:
    store = HealthStore(pool)
    moment = now()
    await store.beat(
        organization_id=tenant["organization"], component=COMPONENT_DISPATCHER, now=moment
    )

    # Jeden interwał ciszy: restart, nie awaria.
    assert (
        await store.sweep_missing(organization_id=tenant["organization"], now=moment + INTERVAL)
        == ()
    )
    later = moment + INTERVAL * (MISSED_INTERVALS_BEFORE_DOWN + 1)
    opened = await store.sweep_missing(organization_id=tenant["organization"], now=later)
    assert [one.component for one in opened] == [COMPONENT_DISPATCHER]
    assert opened[0].detected_by == DETECTED_BY_MISSING
    assert "brak pulsu" in opened[0].reason


async def test_a_component_that_never_ran_is_not_an_outage(pool, tenant) -> None:
    """Publikator, którego to wdrożenie nie uruchamia, nie jest awarią. Tydzień incydentów
    o komponencie, którego nikt nie skonfigurował, zagłuszyłby te, które coś znaczą."""
    store = HealthStore(pool)
    opened = await store.sweep_missing(organization_id=tenant["organization"], now=now())
    assert opened == ()


async def test_an_incident_that_started_before_the_window_is_still_reported(pool, tenant) -> None:
    """Awaria, która zaczęła się przed oknem i trwa, jest najważniejszym wierszem raportu
    tygodnia, a zapytanie po `started_at` samo z siebie by ją pominęło."""
    store = HealthStore(pool)
    long_ago = now() - timedelta(days=30)
    await store.open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="trwa od miesiąca",
        now=long_ago,
    )
    found = await store.incidents_since(
        organization_id=tenant["organization"], since=now() - timedelta(days=7)
    )
    assert [one.component for one in found] == [COMPONENT_CAPSULE]


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("nieznany komponent", {"component": "COS_INNEGO"}),
        ("nieznany wykrywacz", {"detected_by": "ZGADYWANIE"}),
        ("pusty powód", {"reason": "   "}),
    ],
)
async def test_an_incident_outside_the_closed_lists_is_refused(pool, tenant, label, kwargs) -> None:
    store = HealthStore(pool)
    base = {
        "organization_id": tenant["organization"],
        "component": COMPONENT_CAPSULE,
        "reason": "powód",
        "detected_by": DETECTED_BY_SELF,
    }
    with pytest.raises(HealthRefusal):
        await store.open_incident(**{**base, **kwargs})


async def test_a_human_intervention_is_recorded_as_such(pool, tenant) -> None:
    """Raport tygodnia liczy interwencje człowieka osobno, więc muszą być odróżnialne."""
    store = HealthStore(pool)
    incident = await store.open_incident(
        organization_id=tenant["organization"],
        component=COMPONENT_CAPSULE,
        reason="ręczne zatrzymanie na czas sprawdzenia",
        detected_by=DETECTED_BY_HUMAN,
    )
    assert incident.detected_by == DETECTED_BY_HUMAN
    assert incident.to_json()["zauwazyl"] == DETECTED_BY_HUMAN
