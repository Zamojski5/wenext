"""Migracja 0020: czy to chodziło, co padło, ile kosztowało i jaki jest sufit (P7 etap B).

PIĘĆ WŁASNOŚCI, KAŻDA ZAMYKA JEDEN SPOSÓB NA ZŁE LICZBY W RAPORCIE TYGODNIA:

  1. **Jeden puls na komponent na interwał.** Dwa procesy tego samego komponentu nie mogą
     wyglądać na podwójną dostępność.
  2. **Jeden otwarty incydent na komponent.** Dwa zgłoszenia tej samej awarii to jedna awaria;
     dwa otwarte wiersze policzyłyby się jako dwie.
  3. **Incydent nie kończy się przed początkiem.** Ujemny czas trwania w raporcie dostępności
     daje liczbę, której nikt nie umie przeczytać.
  4. **Ten sam koszt policzony dwa razy to jeden wiersz.** Zasilanie księgi idzie z tabel, które
     już istnieją, więc powtórzenie jest normalne i nie może podwajać rachunku.
  5. **Sufit organizacji odróżnia „brak" od „zero".** NULL nie ogranicza, zero zabrania.

Plus dwie rzeczy o kształcie: cztery tabele są ORGANIZACYJNE (bez `workspace_id`), a słownik
komponentów jest wspólny dla pulsu i incydentu, żeby raport umiał połączyć jedno z drugim.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    COST_KINDS,
    INCIDENT_DETECTORS,
    OPS_TABLE_COLUMNS,
    SERVICE_COMPONENTS,
)

pytestmark = [pytest.mark.db, pytest.mark.p7]

_LITERAL = re.compile(r"'([^']*)'::text")


@pytest_asyncio.fixture
async def tenant(pool):
    async with pool.acquire() as connection:
        organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"fixture-0020-{uuid4()}"
        )
        operator = await connection.fetchval(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2) RETURNING id",
            organization,
            "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g",
        )
    return {"organization": organization, "operator": operator}


async def _refused(connection, sql: str, *args) -> str:
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except Exception as exc:
        return str(exc).lower()
    raise AssertionError(f"instrukcja przeszła, choć miała zostać odrzucona: {sql} {args}")


# ---------------------------------------------------------------------------
# Kształt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(OPS_TABLE_COLUMNS))
async def test_each_table_has_exactly_the_declared_columns(pool, table) -> None:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        )
    assert {row["column_name"] for row in rows} == set(OPS_TABLE_COLUMNS[table]), table


@pytest.mark.parametrize("table", sorted(OPS_TABLE_COLUMNS))
async def test_the_operational_tables_belong_to_the_organization_not_a_workspace(
    pool, table
) -> None:
    """Dostępność usługi, awaria, koszt i sufit są faktami o ORGANIZACJI. Kolumna workspace'u
    siedziałaby tu niepilnowana przez nic, jak w `audience_snapshots` z 0019."""
    assert "workspace_id" not in OPS_TABLE_COLUMNS[table]
    async with pool.acquire() as connection:
        found = await connection.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = $1 AND column_name = 'workspace_id'",
            table,
        )
    assert found == 0, table


@pytest.mark.parametrize(
    ("constraint", "table", "declared"),
    [
        ("service_heartbeats_component_is_known", "service_heartbeats", SERVICE_COMPONENTS),
        ("incidents_component_is_known", "incidents", SERVICE_COMPONENTS),
        ("incidents_detected_by_is_known", "incidents", INCIDENT_DETECTORS),
        ("cost_ledger_kind_is_known", "cost_ledger", COST_KINDS),
    ],
)
async def test_the_closed_lists_are_the_databases(pool, constraint, table, declared) -> None:
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1 "
            "AND contype = 'c' AND conrelid = ('public.' || $2)::regclass",
            constraint,
            table,
        )
    assert definition, f"nie ma nazwanego CHECK-a {constraint} na {table}"
    values = set(_LITERAL.findall(definition))
    assert values, "CHECK bez ani jednej wartości przeszedłby każde porównanie"
    assert values == set(declared), (definition, sorted(declared))


def test_the_component_vocabulary_is_shared_by_the_heartbeat_and_the_incident() -> None:
    """„Co biło" i „co padło" muszą być jednym słownikiem, inaczej raport ich nie połączy."""
    from db_schema.expected_schema import SERVICE_COMPONENTS as declared

    assert len(declared) == 5, sorted(declared)


# ---------------------------------------------------------------------------
# Zachowanie
# ---------------------------------------------------------------------------


async def test_one_heartbeat_per_component_per_interval(pool, tenant) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO service_heartbeats (organization_id, component, interval_start) "
            "VALUES ($1, 'DISPATCHER', '2026-09-19T12:00:00Z')",
            tenant["organization"],
        )
        message = await _refused(
            connection,
            "INSERT INTO service_heartbeats (organization_id, component, interval_start) "
            "VALUES ($1, 'DISPATCHER', '2026-09-19T12:00:00Z')",
            tenant["organization"],
        )
        assert "unique" in message or "duplicate" in message, message


async def test_only_one_incident_of_a_component_may_be_open(pool, tenant) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO incidents (organization_id, component, started_at, reason, detected_by) "
            "VALUES ($1, 'CAPSULE', now(), '403', 'SELF_REPORT')",
            tenant["organization"],
        )
        message = await _refused(
            connection,
            "INSERT INTO incidents (organization_id, component, started_at, reason, detected_by) "
            "VALUES ($1, 'CAPSULE', now(), 'to samo', 'SELF_REPORT')",
            tenant["organization"],
        )
        assert "unique" in message or "duplicate" in message, message

        # A CLOSED one does not occupy the slot: outages repeat, and the second one is a new row.
        await connection.execute(
            "UPDATE incidents SET ended_at = now() WHERE organization_id = $1",
            tenant["organization"],
        )
        await connection.execute(
            "INSERT INTO incidents (organization_id, component, started_at, reason, detected_by) "
            "VALUES ($1, 'CAPSULE', now(), 'znowu', 'SELF_REPORT')",
            tenant["organization"],
        )


async def test_an_incident_cannot_end_before_it_started(pool, tenant) -> None:
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO incidents (organization_id, component, started_at, ended_at, reason, "
            "detected_by) VALUES ($1, 'CAPSULE', now(), now() - interval '1 hour', 'x', 'HUMAN')",
            tenant["organization"],
        )
        assert "check" in message, message


async def test_an_incident_without_a_reason_is_refused(pool, tenant) -> None:
    """Awaria bez powodu nie mówi nikomu, co naprawić."""
    async with pool.acquire() as connection:
        for reason in ("", "   "):
            message = await _refused(
                connection,
                "INSERT INTO incidents (organization_id, component, started_at, reason, "
                "detected_by) VALUES ($1, 'CAPSULE', now(), $2, 'SELF_REPORT')",
                tenant["organization"],
                reason,
            )
            assert "check" in message, (reason, message)


async def test_the_same_cost_counted_twice_is_one_row(pool, tenant) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
            "VALUES ($1, 'MODEL_TURN', 1, 'wakeup:abc', now())",
            tenant["organization"],
        )
        message = await _refused(
            connection,
            "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
            "VALUES ($1, 'MODEL_TURN', 1, 'wakeup:abc', now())",
            tenant["organization"],
        )
        assert "unique" in message or "duplicate" in message, message


async def test_a_cost_row_must_point_at_something(pool, tenant) -> None:
    """Suma, której nie da się rozłożyć z powrotem na zdarzenia, jest liczbą, nie rachunkiem."""
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
            "VALUES ($1, 'MODEL_TURN', 1, '  ', now())",
            tenant["organization"],
        )
        assert "check" in message, message


async def test_a_negative_cost_is_refused(pool, tenant) -> None:
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO cost_ledger (organization_id, kind, units, source_ref, occurred_at) "
            "VALUES ($1, 'MODEL_TURN', -1, 'wakeup:x', now())",
            tenant["organization"],
        )
        assert "check" in message, message


async def test_an_organization_has_at_most_one_ceiling(pool, tenant) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, 20)",
            tenant["organization"],
        )
        message = await _refused(
            connection,
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, 30)",
            tenant["organization"],
        )
        assert "unique" in message or "duplicate" in message, message


async def test_a_ceiling_of_zero_is_allowed_and_is_not_the_same_as_none(pool, tenant) -> None:
    """Zero zabrania wszystkiego, NULL nie ogranicza niczego. Baza musi umieć zapisać oba."""
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO org_limits (organization_id, model_turns_per_day, publications_per_day) "
            "VALUES ($1, 0, NULL)",
            tenant["organization"],
        )
        row = await connection.fetchrow(
            "SELECT model_turns_per_day, publications_per_day FROM org_limits "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
    assert row["model_turns_per_day"] == 0
    assert row["publications_per_day"] is None


async def test_a_negative_ceiling_is_refused(pool, tenant) -> None:
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO org_limits (organization_id, model_turns_per_day) VALUES ($1, -1)",
            tenant["organization"],
        )
        assert "check" in message, message
