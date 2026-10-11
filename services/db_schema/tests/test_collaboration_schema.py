"""Migracja 0019: przekazanie zadania między sesjami i liczby o zasięgu (P6).

CO TU JEST MIERZONE, i czym każda z tych rzeczy byłaby bez więzu:

  1. **Sesja nie jest własnym rodzicem.** Jedna literówka w `session.handoff` tworzy wiersz, po
     którym czytanie drzewa przekazań zapętla czytającego. CHECK, nie kod: zapętlone drzewo
     zauważa się dopiero wtedy, gdy ktoś je czyta, a wtedy jest już w bazie.
  2. **Rodzic jest z tego samego workspace'u.** Klucz obcy po trójce tenantowej. Sprawdzenia
     „ten sam projekt" NIE MA w bazie i to jest opisany kompromis: CHECK w PostgreSQL nie może
     odpytać innej tabeli, więc projekt pilnuje `sessions/store.py`.
  3. **Ten sam odczyt zapisany dwa razy to ten sam wiersz.** Bez tego ponowiony job podwaja
     historię, a wykres z tej historii pokazuje wzrost, którego nie było.
  4. **FOLLOWERS to stan, nie suma za okres.** „1200 obserwujących w tym tygodniu" jest zdaniem
     bez znaczenia, a wykres złożony z takich wierszy sugeruje przyrost tam, gdzie był stan.
  5. **Każda liczba niesie źródło.** Odczyt ze strony profilu, z API i wpis ręczny to trzy różne
     jakości dowodu; bez tej kolumny wyglądają tak samo, a różnica wychodzi dopiero wtedy, gdy
     ktoś na nich oprze decyzję.

Wartości zamkniętych list są POLICZONE przed porównaniem, jak wszędzie w tym pakiecie: pusta
lista przeszłaby pętlę porównującą bez jednego porównania (D-020).
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    AUDIENCE_METRICS,
    AUDIENCE_PERIODS,
    AUDIENCE_SNAPSHOTS_COLUMNS,
    AUDIENCE_SOURCES,
)

pytestmark = [pytest.mark.db, pytest.mark.p6]

_LITERAL = re.compile(r"'([^']*)'::text")


@pytest_asyncio.fixture
async def tenant(pool):
    async with pool.acquire() as connection:
        organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ($1) RETURNING id", f"fixture-0019-{uuid4()}"
        )
        workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization,
        )
        other_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws2') RETURNING id",
            organization,
        )
        project = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'p') "
            "RETURNING id",
            organization,
            workspace,
        )
        account = await connection.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            organization,
            f"konto-{uuid4().hex[:8]}",
        )
    return {
        "organization": organization,
        "workspace": workspace,
        "other_workspace": other_workspace,
        "project": project,
        "account": account,
    }


async def _refused(connection, sql: str, *args) -> str:
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except Exception as exc:
        return str(exc).lower()
    raise AssertionError(f"instrukcja przeszła, choć miała zostać odrzucona: {sql} {args}")


async def _session(connection, tenant, *, parent=None, workspace=None):
    return await connection.fetchval(
        "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, "
        "parent_session_id) VALUES ($1, $2, $3, 'cel', $4) RETURNING id",
        tenant["organization"],
        workspace or tenant["workspace"],
        tenant["project"],
        parent,
    )


async def _snapshot(connection, tenant, **overrides):
    values = {
        "metric": "FOLLOWERS",
        "value": 1200,
        "period": "POINT_IN_TIME",
        "source": "PLATFORM_PAGE",
        **overrides,
    }
    return await connection.execute(
        "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
        "period, source, observed_at) VALUES ($1, $2, $3, $4, $5, $6, now())",
        tenant["organization"],
        tenant["account"],
        values["metric"],
        values["value"],
        values["period"],
        values["source"],
    )


# ---------------------------------------------------------------------------
# Przekazanie zadania
# ---------------------------------------------------------------------------


async def test_a_session_written_before_this_migration_has_no_parent(pool, tenant) -> None:
    """Każda sesja z P2 i P4 zachowuje znaczenie: NULL znaczy „nikt nie zlecił"."""
    async with pool.acquire() as connection:
        session = await _session(connection, tenant)
        parent = await connection.fetchval(
            "SELECT parent_session_id FROM agent_sessions WHERE id = $1", session
        )
        assert parent is None


async def test_a_handoff_points_at_the_session_that_asked_for_it(pool, tenant) -> None:
    async with pool.acquire() as connection:
        parent = await _session(connection, tenant)
        child = await _session(connection, tenant, parent=parent)
        assert (
            await connection.fetchval(
                "SELECT parent_session_id FROM agent_sessions WHERE id = $1", child
            )
            == parent
        )


async def test_a_session_cannot_be_its_own_parent(pool, tenant) -> None:
    async with pool.acquire() as connection:
        session = await _session(connection, tenant)
        message = await _refused(
            connection,
            "UPDATE agent_sessions SET parent_session_id = id WHERE id = $1",
            session,
        )
        assert "check" in message, message


async def test_a_parent_from_another_workspace_is_refused(pool, tenant) -> None:
    """Klucz obcy po trójce tenantowej. Przekazanie do cudzego workspace'u nie jest
    przekazaniem, tylko wyciekiem dostępu."""
    async with pool.acquire() as connection:
        stranger = await connection.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) VALUES ($1, $2, 'obcy') "
            "RETURNING id",
            tenant["organization"],
            tenant["other_workspace"],
        )
        foreign_parent = await connection.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["other_workspace"],
            stranger,
        )
        message = await _refused(
            connection,
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal, "
            "parent_session_id) VALUES ($1, $2, $3, 'cel', $4)",
            tenant["organization"],
            tenant["workspace"],
            tenant["project"],
            foreign_parent,
        )
        assert "foreign key" in message or "klucz" in message, message


# ---------------------------------------------------------------------------
# Liczby o zasięgu
# ---------------------------------------------------------------------------


async def test_the_table_has_exactly_the_declared_columns(pool) -> None:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'audience_snapshots'"
        )
    assert {row["column_name"] for row in rows} == set(AUDIENCE_SNAPSHOTS_COLUMNS)


async def test_the_number_belongs_to_an_account_and_not_to_a_workspace(pool) -> None:
    """Brak `workspace_id` jest decyzją: konto jest własnością organizacji, więc kolumna
    workspace'u nie miałaby tu rodzica i nikt by jej nie pilnował."""
    assert "workspace_id" not in AUDIENCE_SNAPSHOTS_COLUMNS
    async with pool.acquire() as connection:
        found = await connection.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'audience_snapshots' AND column_name = 'workspace_id'"
        )
    assert found == 0


@pytest.mark.parametrize(
    ("constraint", "declared"),
    [
        ("audience_snapshots_metric_is_known", AUDIENCE_METRICS),
        ("audience_snapshots_period_is_known", AUDIENCE_PERIODS),
        ("audience_snapshots_source_is_known", AUDIENCE_SOURCES),
    ],
)
async def test_the_closed_lists_are_the_databases(pool, constraint, declared) -> None:
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1 "
            "AND contype = 'c' AND conrelid = 'public.audience_snapshots'::regclass",
            constraint,
        )
    assert definition, f"nie ma nazwanego CHECK-a {constraint}"
    values = set(_LITERAL.findall(definition))
    assert values, "CHECK bez ani jednej wartości przeszedłby każde porównanie"
    assert values == set(declared), (definition, sorted(declared))


async def test_the_same_reading_written_twice_is_one_row(pool, tenant) -> None:
    """Ponowiony job nie podwaja historii. Bez tego wykres pokazałby wzrost, którego nie było."""
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
            "period, source, observed_at) "
            "VALUES ($1, $2, 'FOLLOWERS', 1200, 'POINT_IN_TIME', 'PLATFORM_PAGE', "
            "'2026-09-19T10:00:00Z')",
            tenant["organization"],
            tenant["account"],
        )
        message = await _refused(
            connection,
            "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
            "period, source, observed_at) "
            "VALUES ($1, $2, 'FOLLOWERS', 1300, 'POINT_IN_TIME', 'PLATFORM_API', "
            "'2026-09-19T10:00:00Z')",
            tenant["organization"],
            tenant["account"],
        )
        assert "unique" in message or "duplicate" in message, message


async def test_followers_are_a_state_and_not_a_sum_over_a_period(pool, tenant) -> None:
    async with pool.acquire() as connection:
        await _snapshot(connection, tenant, metric="FOLLOWERS", period="POINT_IN_TIME")
        message = await _refused(
            connection,
            "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
            "period, source, observed_at) "
            "VALUES ($1, $2, 'FOLLOWERS', 1200, 'WEEK', 'PLATFORM_PAGE', now())",
            tenant["organization"],
            tenant["account"],
        )
        assert "check" in message, message
        # A count that IS a sum over a period is fine, and that is the whole difference.
        await _snapshot(connection, tenant, metric="IMPRESSIONS", period="WEEK")


async def test_a_negative_reading_is_refused(pool, tenant) -> None:
    """Wartość ujemna znaczyłaby „odczyt się nie udał". Na to jest brak wiersza, nie minus."""
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
            "period, source, observed_at) "
            "VALUES ($1, $2, 'FOLLOWERS', -1, 'POINT_IN_TIME', 'MANUAL', now())",
            tenant["organization"],
            tenant["account"],
        )
        assert "check" in message, message


async def test_a_reading_without_a_source_is_refused(pool, tenant) -> None:
    async with pool.acquire() as connection:
        message = await _refused(
            connection,
            "INSERT INTO audience_snapshots (organization_id, social_account_id, metric, value, "
            "period, observed_at) VALUES ($1, $2, 'FOLLOWERS', 10, 'POINT_IN_TIME', now())",
            tenant["organization"],
            tenant["account"],
        )
        assert "null" in message, message


async def test_only_the_named_adapter_writes_the_table(pool) -> None:
    """DOWÓD NA „BRAK DANYCH, NIE ZERO". Do 27.09 nic nie zapisywało tej tabeli, więc ekran
    pokazywał brak danych, bo naprawdę ich nie było (ZAKRES-FUNKCJONALNY §6 zasada 2).

    Od 27.09 (D-054) jest DOKŁADNIE JEDEN zapis: `workspace/audience.py`, którym cogodzinny zbiór
    X zapisuje liczbę obserwujących z profilu konta; macierz platform ma `audience_metrics` tylko
    dla X, a ekran „Profile i konta” pokazuje liczbę z godziną odczytu. Drugi zapis w innym
    pliku ma paść tutaj: to byłby nowy adapter, który musi zmienić flagę swojej platformy i ekran.
    """
    import pathlib
    import re as regex

    root = pathlib.Path(__file__).resolve().parents[3]
    writers: list[str] = []
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text("utf-8", errors="ignore")
        if regex.search(r"INSERT\s+INTO\s+audience_snapshots", text, regex.I):
            writers.append(str(path.relative_to(root)))
    assert writers == ["services/control_plane/control_plane/workspace/audience.py"], (
        f"zapisy audience_snapshots: {writers}. Nowy adapter: zaktualizuj ten test, flagę "
        "`audience_metrics` jego platformy i ekran, który dla niej mówi „brak danych”"
    )
