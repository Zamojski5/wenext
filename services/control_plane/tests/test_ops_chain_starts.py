"""Łańcuch jobów operacyjnych MUSI się zacząć sam (P7 etap B, usterka znaleziona na live).

DLACZEGO TEN PLIK POWSTAŁ PO FAKCIE. Testy P7 B wołały handlery wprost:
`await handlers.heartbeat_job(claim)`. To mierzy DRUGIE ogniwo łańcucha i milcząco zakłada, że
pierwsze istnieje. Na żywej bazie, po wdrożeniu migracji 0020, okazało się, że nie istnieje:
`schedule_ops` było zdefiniowane i nikt go nie wołał, więc w kolejce nie było ani jednego joba
`ops.*`, a `service_heartbeats` miało zero wierszy. Raport tygodnia mówił wtedy prawdę („okno
pomiaru ma 0.0 doby”) i miał zostać przy tej prawdzie na zawsze.

MORAŁ, KTÓRY TEN PLIK UTRWALA: job, który sam zamawia swój następny bieg, jest kompletny dopiero
razem z tym, kto zamawia PIERWSZY. Pętla, która się podtrzymuje, i pętla, która się zaczyna, to
dwie różne własności, a testy sprawdzały tylko pierwszą.

Ten test jest napisany tak, żeby PADAŁ, dopóki nikt nie zasieje łańcucha, i przeszedł, gdy
`start_background` zacznie wołać `schedule_ops` dla każdej organizacji. Nie obchodzę go i nie
osłabiam: gdyby miał zostać wyłączony, znaczyłoby to, że dostępność usługi znów nie jest mierzona.
"""

from __future__ import annotations

import pathlib

import pytest
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.ops.handlers import (
    JOB_BACKUP_DAILY,
    JOB_HEARTBEAT,
    JOB_SWEEP,
    schedule_ops,
)

pytestmark = [pytest.mark.db, pytest.mark.p7]


async def _ops_jobs(pool, organization_id) -> dict[str, int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT job_type, count(*) AS ile FROM jobs "
            "WHERE organization_id = $1 AND job_type LIKE 'ops.%' GROUP BY job_type",
            organization_id,
        )
    return {row["job_type"]: int(row["ile"]) for row in rows}


async def test_seeding_the_chain_enqueues_all_three_jobs(pool, tenant) -> None:
    """`schedule_ops` jest ziarnem łańcucha: po nim w kolejce są wszystkie trzy joby."""
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    await schedule_ops(
        queue,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )
    jobs = await _ops_jobs(pool, tenant["organization"])
    assert set(jobs) == {JOB_HEARTBEAT, JOB_SWEEP, JOB_BACKUP_DAILY}, jobs


async def test_seeding_twice_does_not_double_the_chain(pool, tenant) -> None:
    """Restart procesu nie może zbudować drugiego łańcucha obok pierwszego: klucz idempotencji
    jest per slot, więc powtórzenie w tym samym oknie trafia w ten sam job."""
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    for _ in range(3):
        await schedule_ops(
            queue,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
        )
    jobs = await _ops_jobs(pool, tenant["organization"])
    assert jobs == {JOB_HEARTBEAT: 1, JOB_SWEEP: 1, JOB_BACKUP_DAILY: 1}, jobs


async def test_starting_the_background_tasks_seeds_the_operational_chain(pool, tenant) -> None:
    """USTERKA ZNALEZIONA NA LIVE 19.09: nikt nie wołał `schedule_ops`.

    Handlery były wpięte w mapę dyspozytora, więc joby `ops.*` wykonałyby się, gdyby istniały,
    i same zamawiały swój następny bieg. Brakowało PIERWSZEGO. Ten test sprawdza to, czego nie
    sprawdzał żaden inny: że sam start zadań w tle zostawia łańcuch zasiany.

    Mierzone przez `start_background`, a nie przez wywołanie `schedule_ops` w teście, bo to
    właśnie połączenia między tymi dwoma rzeczami zabrakło.
    """
    import control_plane.runtime as runtime

    seeder = getattr(runtime, "seed_ops_chain", None)
    assert seeder is not None, (
        "runtime.py nie importuje zasiewania łańcucha operacyjnego, więc nie ma kto go zacząć. "
        "Tak wyglądała usterka z 19.09: `ops.heartbeat` nigdy nie trafiał do kolejki, "
        "`service_heartbeats` zostawało puste, a raport tygodnia mówił „okno pomiaru ma 0.0 "
        "doby” i miał tak mówić zawsze."
    )

    # Wywołujemy to, co runtime importuje, na PRAWDZIWEJ kolejce, zamiast uruchamiać pętlę
    # dyspozytora: pytanie brzmi „czy start zasiewa łańcuch", a nie „czy kolejka działa", co
    # mierzą inne pliki. Gdyby `runtime` przestał wołać tę funkcję, poprzednia asercja i tak by
    # tego nie złapała, więc druga połowa testu czyta ŹRÓDŁO startu.
    await seeder(pool, organization_id=tenant["organization"])
    jobs = await _ops_jobs(pool, tenant["organization"])
    assert JOB_HEARTBEAT in jobs, jobs

    source = pathlib.Path(runtime.__file__).read_text("utf-8")
    assert "seed_ops_chain(pool, organization_id=" in source, (
        "runtime.py importuje zasiewanie, ale go nie woła tam, gdzie startuje dyspozytor "
        "organizacji. Import bez wywołania jest dokładnie tą samą usterką, tylko trudniejszą "
        "do zauważenia"
    )


# ---------------------------------------------------------------------------
# To samo dla tiku obecności
# ---------------------------------------------------------------------------


async def test_seeding_the_presence_chain_covers_every_delegated_project(pool, tenant) -> None:
    """Projekt z podpisaną delegacją musi dostać PIERWSZY tik. Bez tego stałby powierzony i
    nigdy nie ruszył, bo `presence.tick` zamawia wyłącznie swój NASTĘPNY bieg."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from control_plane.presence.handlers import JOB_PRESENCE_TICK, seed_presence_chain
    from control_plane.presence.scope import parse_budget, parse_scope
    from control_plane.presence.store import PresenceStore

    presence = PresenceStore(pool)
    projects = []
    for _ in range(2):
        async with pool.acquire() as conn:
            project_id = await conn.fetchval(
                "INSERT INTO projects (organization_id, workspace_id, name, goal) "
                "VALUES ($1, $2, $3, 'cel') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                f"projekt-{uuid4().hex[:8]}",
            )
        await presence.sign(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=project_id,
            mode="RESEARCH",
            scope=parse_scope({"accounts": [], "communities": [], "actions": ["RESEARCH"]}),
            budget=parse_budget({}),
            operator_id=tenant["operator"],
        )
        projects.append(project_id)

    seeded = await seed_presence_chain(pool, organization_id=tenant["organization"])
    assert seeded == 2, seeded

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload_ref FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_PRESENCE_TICK,
        )
    refs = {row["payload_ref"] for row in rows}
    assert refs == {f"presence/{one}" for one in projects}, refs

    # I drugie zasianie nie buduje drugiego łańcucha obok pierwszego.
    again = await seed_presence_chain(pool, organization_id=tenant["organization"])
    assert again == 2
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_PRESENCE_TICK,
        )
    assert count == 2, count
    assert datetime.now(UTC) is not None  # marker, że test biegł w czasie rzeczywistym


async def test_starting_the_background_tasks_seeds_the_presence_chain_too(pool, tenant) -> None:
    """Ta sama asercja co dla ops: import BEZ wywołania jest tą samą usterką, tylko cichszą."""
    import control_plane.runtime as runtime

    assert getattr(runtime, "seed_presence_chain", None) is not None, (
        "runtime.py nie importuje zasiewania tiku obecności; powierzony projekt nigdy nie ruszy"
    )
    source = pathlib.Path(runtime.__file__).read_text("utf-8")
    assert "seed_presence_chain(pool, organization_id=" in source, (
        "runtime.py importuje zasiewanie obecności, ale go nie woła tam, gdzie startuje "
        "dyspozytor organizacji"
    )
