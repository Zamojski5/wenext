"""Przepisanie dokumentów po zdarzeniu: okno, klucz, wyzwalacz (P8 etap B, D-043).

CO TE TESTY TRZYMAJĄ, bez bazy i bez kolejki:

  * SERIA ZAPISÓW Z JEDNEJ ROZMOWY DAJE JEDEN JOB. To jest cała różnica między wariantem
    „po zdarzeniu" a „po każdym zapisie", i stoi na kluczu idempotencji z numerem okna.
  * ZAMÓWIENIE NIE MOŻE PRZEWRÓCIĆ ZAPISU DO REJESTRU. Rejestr jest źródłem prawdy; niegotowy
    dokument naprawia następne zdarzenie, a wyjątek z kolejki skasowałby ustalenie człowieka.
  * USTALENIE CAŁEJ PRZESTRZENI NIE ZAMAWIA NICZEGO, bo dokumenty są dziś projektowe.

Czego NIE mierzą: czy job naprawdę przepisze dokumenty (to test z bazą) i czy sesja zobaczy
nowszą wersję w indeksie (to pomiar o produkcie, też z bazą).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.documents.handlers import (
    JOB_REBUILD,
    REBUILD_WINDOW_SECONDS,
    ROUTING_KEY,
    project_from_ref,
    project_ref,
    rebuild_window,
    request_rebuild,
    schedule_rebuild,
)

pytestmark = [pytest.mark.p2]


class _FakeQueue:
    """Kolejka, która zapamiętuje zamówienia. Nic poza `create_job` nie jest tu potrzebne."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def create_job(self, **kwargs):
        self.jobs.append(kwargs)
        return object()


async def test_a_burst_of_writes_in_one_window_orders_one_rebuild() -> None:
    """Pięć zapisów w dwie minuty to zwykła tura. Pięć przepisań dokumentu to nie."""
    queue = _FakeQueue()
    project = uuid4()
    start = datetime(2026, 9, 21, 10, 0, 30, tzinfo=UTC)

    for offset in (0, 15, 60, 119, 300):
        await schedule_rebuild(
            queue,
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=project,
            now=start + timedelta(seconds=offset),
        )

    keys = {job["idempotency_key"] for job in queue.jobs}
    assert len(queue.jobs) == 5, "wszystkie zamówienia idą do kolejki"
    assert len(keys) == 1, f"i wszystkie mają jeden klucz, więc powstaje jeden job: {keys}"


async def test_the_next_window_is_a_different_job() -> None:
    """Okno ma się zamykać: zapis po dziesięciu minutach nie może wpaść do poprzedniego jobu."""
    queue = _FakeQueue()
    project = uuid4()
    start = datetime(2026, 9, 21, 10, 0, 30, tzinfo=UTC)

    for offset in (0, REBUILD_WINDOW_SECONDS):
        await schedule_rebuild(
            queue,
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=project,
            now=start + timedelta(seconds=offset),
        )

    assert len({job["idempotency_key"] for job in queue.jobs}) == 2


async def test_two_projects_never_share_a_job() -> None:
    """Dokumenty są projektowe: wspólny klucz znaczyłby, że jeden projekt gubi przepisanie."""
    queue = _FakeQueue()
    now = datetime(2026, 9, 21, 10, 0, 30, tzinfo=UTC)

    for _ in range(2):
        await schedule_rebuild(
            queue,
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            now=now,
        )

    assert len({job["idempotency_key"] for job in queue.jobs}) == 2


async def test_the_job_waits_for_the_end_of_its_window_and_carries_the_project() -> None:
    queue = _FakeQueue()
    project = uuid4()
    now = datetime(2026, 9, 21, 10, 0, 30, tzinfo=UTC)

    await schedule_rebuild(
        queue, organization_id=uuid4(), workspace_id=uuid4(), project_id=project, now=now
    )

    job = queue.jobs[0]
    assert job["job_type"] == JOB_REBUILD
    assert job["routing_key"] == ROUTING_KEY
    assert job["payload_ref"] == project_ref(project)
    assert 0 < job["delay_seconds"] <= REBUILD_WINDOW_SECONDS
    window_end = rebuild_window(now) * REBUILD_WINDOW_SECONDS
    assert job["delay_seconds"] == pytest.approx(window_end - now.timestamp())


async def test_a_workspace_wide_finding_orders_nothing() -> None:
    """Dokumenty są dziś projektowe (podręcznik przestrzeni dochodzi w etapie C)."""
    ordered = await request_rebuild(
        pool=None,  # nie ma jak do niej sięgnąć: funkcja ma wyjść przed dotknięciem czegokolwiek
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=None,
    )

    assert ordered is False


async def test_a_refusing_queue_never_breaks_the_write(monkeypatch) -> None:
    """USTALENIE CZŁOWIEKA JEST WAŻNIEJSZE NIŻ ŚWIEŻOŚĆ DOKUMENTU.

    Gdyby zamówienie rzucało, awaria kolejki kasowałaby zapis do rejestru, czyli jedyne źródło
    prawdy pamięci. Nieprzepisany dokument naprawia następne zdarzenie; skasowane ustalenie nie
    naprawia się samo.
    """
    import control_plane.documents.handlers as handlers

    monkeypatch.setattr(
        handlers, "schedule_rebuild", _raising, raising=True
    )

    ordered = await handlers.request_rebuild(
        pool=object(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
    )

    assert ordered is False


async def _raising(*args, **kwargs):
    raise RuntimeError("kolejka odmówiła")


def test_a_reference_survives_the_round_trip_and_a_wrong_one_is_refused() -> None:
    project = uuid4()

    assert project_from_ref(project_ref(project)) == project
    for wrong in (None, "", "thread/" + str(project), str(project)):
        with pytest.raises(ValueError):
            project_from_ref(wrong)


async def test_every_write_to_the_register_orders_a_rebuild(monkeypatch) -> None:
    """KLEJ, o który tu chodzi: to `KnowledgeStore` ma wołać zamówienie, a nie pięciu pisarzy.

    Do rejestru piszą dziś narzędzia modelu, trasa człowieka, zaciąganie RSS i przemiatanie
    doświadczeń. Gdyby zamówienie stało w każdym z tych miejsc osobno, szósty pisarz by o nim
    zapomniał, a dokument po cichu zostałby stary. Test sprawdza samo połączenie, bo reszta
    (czy job naprawdę przepisze) jest pomiarem z bazą.
    """
    import control_plane.documents.handlers as handlers
    from control_plane.knowledge.store import KnowledgeStore, Scope

    orders: list[dict] = []

    async def recording(pool, **kwargs):
        orders.append(kwargs)
        return True

    monkeypatch.setattr(handlers, "request_rebuild", recording, raising=True)
    scope = Scope(organization_id=uuid4(), workspace_id=uuid4(), project_id=uuid4())
    store = KnowledgeStore(pool=None)  # type: ignore[arg-type]

    own_project = uuid4()
    await store._request_document_rebuild(scope, own_project)
    await store._request_document_rebuild(scope, None)

    assert [one["project_id"] for one in orders] == [own_project, scope.project_id], (
        "projekt bierze się z ustalenia, a gdy ustalenie go nie ma, z zakresu zapisu"
    )
    assert {one["organization_id"] for one in orders} == {scope.organization_id}


def test_the_shared_claim_helper_really_builds_a_claimed_job() -> None:
    """USTERKA Z 21.09, ZŁAPANA DOPIERO PRZEZ BAZĘ, i dlatego ten test jest bez bazy.

    Ręcznie przepisana krotka `ClaimedJob` gubiła cztery pola lease'u. Przeszła `ruff`, import
    i zbieranie testów, a padła czterema `TypeError` naraz dopiero w przebiegu z bazą, bo wcześniej
    nikt jej nie ZBUDOWAŁ. Konstrukcja cudzego kontraktu z pamięci jest dokładnie tym miejscem,
    w którym testy przestają cokolwiek trzymać; ten test buduje ją naprawdę i kosztuje milisekundę.
    """
    from control_plane.domain.models import ClaimedJob
    from tests.job_claims import claimed_job

    organization = uuid4()
    claim = claimed_job(
        organization_id=organization,
        job_type=JOB_REBUILD,
        routing_key=ROUTING_KEY,
        workspace_id=uuid4(),
        payload_ref=project_ref(uuid4()),
    )

    assert isinstance(claim, ClaimedJob)
    assert claim.organization_id == organization
    assert claim.job_type == JOB_REBUILD
