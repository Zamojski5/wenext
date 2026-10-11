"""Łańcuch mierzenia skutków publikacji ma RUSZAĆ SAM (P8 etap B, 21.09).

CO BYŁO NIE TAK. `knowledge.experience.sweep` zamawia wyłącznie swój NASTĘPNY bieg, więc łańcuch
podtrzymuje się bez pomocy, ale nie zaczyna się sam: startował tylko po kliknięciu człowieka
w trasie `request_experience_sweep`. To jest ta sama usterka, co przy `ops.*` sprzed dwóch nocy
(pętla, która się podtrzymuje, i pętla, która RUSZA, to dwie różne własności) i ten sam kształt,
co `rebuild_project` bez wołającego: zbudowane, zmierzone, na produkcji nie biegnie.

DWA MIEJSCA STARTU I DLACZEGO OBA. Zasiew przy starcie procesu podnosi łańcuch po wdrożeniu
i po restarcie, ale biegnie raz: organizacja, która opublikowała coś godzinę później, czekałaby
na następny restart. Publikacja jest jedynym zdarzeniem, po którym w ogóle JEST co mierzyć, więc
ona też zaczyna. Oba są idempotentne po kluczu godziny, więc nigdy nie ma dwóch łańcuchów.

Bez bazy: to są pytania o to, co trafia do kolejki, a nie o to, co kolejka z tym robi.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from control_plane.knowledge.handlers import (
    EXPERIENCE_INTERVAL,
    EXPERIENCE_ROUTING_KEY,
    JOB_EXPERIENCE,
    schedule_experience_sweep,
    seed_experience_chain,
)

pytestmark = [pytest.mark.p8]


class _FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def create_job(self, **kwargs):
        self.jobs.append(kwargs)
        return object()


class _AngryQueue:
    async def create_job(self, **kwargs):
        raise RuntimeError("kolejka nie odpowiada")


class _FakeConnection:
    def __init__(self, answer) -> None:
        self._answer = answer
        self.asked: list[str] = []

    async def fetchval(self, sql, *args):
        self.asked.append(sql)
        return self._answer


class _FakePool:
    """Pula, która oddaje jedno połączenie z jedną odpowiedzią. Tyle pyta zasiew."""

    def __init__(self, answer) -> None:
        self.connection = _FakeConnection(answer)

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool.connection

            async def __aexit__(self, *exc):
                return False

        return _Acquire()


def _publisher(queue) -> object:
    """`PublishHandlers` z atrapami zamiast bazy: mierzona metoda czyta tylko kolejkę i org."""
    from control_plane.approval.store import ApprovalStore
    from control_plane.execution.store import ExecutionStore
    from control_plane.observe.store import ObservationStore
    from control_plane.pilot.publication import PublicationStore
    from control_plane.pilot.publisher import PublishHandlers
    from control_plane.pilot.store import PilotStore

    return PublishHandlers(
        store=ObservationStore(None),  # type: ignore[arg-type]
        pilot=PilotStore(None),  # type: ignore[arg-type]
        publication=PublicationStore(None),  # type: ignore[arg-type]
        approvals=ApprovalStore(None),  # type: ignore[arg-type]
        execution=ExecutionStore(None),  # type: ignore[arg-type]
        queue=queue,
        source=None,
        capsule_state=None,
        port_factory=lambda account: None,
        organization_id=uuid4(),
    )


async def test_a_publication_starts_the_chain_that_will_measure_it() -> None:
    queue = _FakeQueue()

    await _publisher(queue)._start_experience_chain()

    assert len(queue.jobs) == 1
    job = queue.jobs[0]
    assert job["job_type"] == JOB_EXPERIENCE
    assert job["routing_key"] == EXPERIENCE_ROUTING_KEY
    assert job["delay_seconds"] == EXPERIENCE_INTERVAL.total_seconds()
    assert job["workspace_id"] is None, "doświadczenia są faktem o organizacji, nie o przestrzeni"


async def test_a_second_publication_in_the_same_hour_adds_no_second_chain() -> None:
    """Klucz jest ten sam, którego używa łańcuch, więc drugi wpis do kolejki nic nie tworzy."""
    queue = _FakeQueue()
    publisher = _publisher(queue)

    await publisher._start_experience_chain()
    await publisher._start_experience_chain()

    assert len({job["idempotency_key"] for job in queue.jobs}) == 1


async def test_the_same_key_as_the_chain_itself_so_they_never_double_up() -> None:
    """Gdyby publikator miał własny klucz, po każdej publikacji biegłby DRUGI łańcuch obok."""
    queue = _FakeQueue()
    organization = uuid4()

    await schedule_experience_sweep(
        queue,
        organization_id=organization,
        delay_seconds=EXPERIENCE_INTERVAL.total_seconds(),
    )
    publisher = _publisher(queue)
    await publisher._start_experience_chain()

    assert len({job["idempotency_key"] for job in queue.jobs}) == 1


async def test_a_refusing_queue_never_undoes_a_publication_that_happened() -> None:
    """Komentarz stoi już na cudzej stronie. Wyjątek tutaj zmieniłby udaną publikację w błąd."""
    await _publisher(_AngryQueue())._start_experience_chain()


async def test_the_seed_skips_an_organization_that_has_published_nothing(monkeypatch) -> None:
    """Job bez publikacji nic by nie zapisał i nie przedłużył łańcucha: zostawiłby w kolejce
    wiersz, który udaje pracę."""
    import control_plane.knowledge.handlers as handlers

    ordered: list[dict] = []

    async def recording(port, **kwargs):
        ordered.append(kwargs)

    monkeypatch.setattr(handlers, "schedule_experience_sweep", recording, raising=True)

    seeded = await seed_experience_chain(_FakePool(False), organization_id=uuid4())

    assert seeded is False
    assert ordered == []


async def test_the_seed_starts_the_chain_where_there_is_something_to_measure(
    monkeypatch,
) -> None:
    import control_plane.knowledge.handlers as handlers

    ordered: list[dict] = []

    async def recording(port, **kwargs):
        ordered.append(kwargs)

    monkeypatch.setattr(handlers, "schedule_experience_sweep", recording, raising=True)
    organization = uuid4()
    pool = _FakePool(True)

    seeded = await seed_experience_chain(pool, organization_id=organization)

    assert seeded is True
    assert ordered == [
        {
            "organization_id": organization,
            "delay_seconds": EXPERIENCE_INTERVAL.total_seconds(),
        }
    ]
    assert "publication_requests" in pool.connection.asked[0]
    assert "published_at IS NOT NULL" in pool.connection.asked[0], (
        "zasiew pyta o PUBLIKACJE, nie o wnioski: wniosek w kolejce nie ma jeszcze skutku"
    )


def test_the_hour_key_moves_with_the_clock() -> None:
    """Klucz ma numerować GODZINĘ BIEGU, inaczej łańcuch kolidowałby sam ze sobą i stanął."""
    from control_plane.knowledge.handlers import EXPERIENCE_INTERVAL as interval

    first = int(datetime(2026, 9, 21, 10, 5, tzinfo=UTC).timestamp() // 3600)
    later = int(
        (datetime(2026, 9, 21, 10, 5, tzinfo=UTC).timestamp() + interval.total_seconds()) // 3600
    )

    assert later > first
