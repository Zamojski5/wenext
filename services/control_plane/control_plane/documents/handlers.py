"""Job przepisania dokumentów projektu: po ZDARZENIU, nie po turze (P8 etap B, D-043).

DLACZEGO PO ZDARZENIU. Dokument jest widokiem rejestru, więc przepisywać go warto wtedy, gdy
rejestr się zmienił: ktoś zapisał ustalenie, poprawił je, wycofał, albo zmierzony wynik publikacji
dopisał doświadczenie. Przepisywanie po każdej turze kosztowałoby tyle samo pracy przy turze,
która nic nie ustaliła, a dwie rozmowy naraz dałyby dwa joby bijące się o ten sam projekt.

DLACZEGO OKNO DZIESIĘCIU MINUT. Rozmowa zapisuje ustalenia seriami: pięć zapisów w dwie minuty to
normalna tura. Klucz idempotencji niesie NUMER OKNA, więc wszystkie zdarzenia z jednego okna
zamawiają dokładnie jeden job, a nie pięć. Job czeka do końca swojego okna, czyli najwyżej
dziesięć minut; to jest cena za brak pięciu przepisań pod rząd i za to, że nie ma dwóch jobów
jednego projektu naraz.

CZEGO TEN JOB NIE ROBI. Nie woła modelu (generator jest regułowy, P8 etap A) i nie pisze do
rejestru. Nie tworzy też nowej WERSJI dokumentu, gdy zbiór ustaleń się nie zmienił: rozstrzyga
`input_digest` w `documents/store.py`, a „nie powstała nowa wersja" jest normalnym wynikiem, nie
porażką.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob, EnqueuedJob
from control_plane.domain.ports import JobQueuePort

_LOG = logging.getLogger(__name__)

JOB_REBUILD = "documents.rebuild"

#: Jeden klucz routingu na moduł, tak jak w `observe/scheduling.py`: jeden proces, brak brokera.
ROUTING_KEY = "documents"

#: Niżej niż rozmowy i publikacje, wyżej niż nic. Przepisanie dokumentu nie jest pilne: ma być
#: gotowe, zanim ktoś zacznie następną rozmowę, a nie w tej sekundzie.
PRIORITY_REBUILD = 50

#: Okno składania zdarzeń w jedno przepisanie, w sekundach.
REBUILD_WINDOW_SECONDS = 600

#: Jedna próba. Nieudane przepisanie naprawia następne zdarzenie w rejestrze, a job powtarzany
#: w pętli na zepsutym projekcie kosztowałby tyle samo co pierwszy raz i tyle samo by pomógł.
ATTEMPT_LIMIT = 1


def project_ref(project_id: UUID) -> str:
    return f"project/{project_id}"


def project_from_ref(payload_ref: str | None) -> UUID:
    if payload_ref is None or not payload_ref.startswith("project/"):
        raise ValueError(f"payload_ref={payload_ref!r} nie ma postaci `project/<uuid>`")
    return UUID(payload_ref.split("/", 1)[1])


def rebuild_window(now: datetime, *, window_seconds: int = REBUILD_WINDOW_SECONDS) -> int:
    """Numer NASTĘPNEGO okna. Zdarzenie zamawia przepisanie na koniec okna, w którym padło."""
    return int(now.timestamp() // window_seconds) + 1


async def schedule_rebuild(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    now: datetime | None = None,
    window_seconds: int = REBUILD_WINDOW_SECONDS,
) -> EnqueuedJob:
    """Zamów przepisanie dokumentów tego projektu na koniec bieżącego okna.

    Klucz `documents:<projekt>:<okno>` jest tym, co sprawia, że seria zapisów daje jeden job:
    drugie i dziesiąte zamówienie w tym samym oknie wraca z `created == False` i nic nie dodaje.
    """
    moment = now or datetime.now(UTC)
    window = rebuild_window(moment, window_seconds=window_seconds)
    delay = max(0.0, window * window_seconds - moment.timestamp())
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_REBUILD,
        routing_key=ROUTING_KEY,
        priority=PRIORITY_REBUILD,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"documents:{project_id}:{window}",
        payload_ref=project_ref(project_id),
        delay_seconds=delay,
    )


async def request_rebuild(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID | None,
    now: datetime | None = None,
) -> bool:
    """Zamów przepisanie po zmianie w rejestrze. NIGDY nie wywraca zapisu, który je wywołał.

    Wołane z `KnowledgeStore`, czyli z jednego miejsca, przez które przechodzi KAŻDY zapis do
    rejestru: zapis, korekta i wycofanie. Wołanie tego osobno w pięciu miejscach, które dziś
    piszą ustalenia (narzędzia modelu, trasa człowieka, zaciąganie RSS, przemiatanie doświadczeń),
    znaczyłoby, że szósty pisarz zapomni, a dokument po cichu zostanie stary.

    Zwraca `False`, gdy nie ma czego zamawiać albo gdy kolejka odmówiła. Odmowa kolejki nie może
    przewrócić zapisu ustalenia: rejestr jest źródłem prawdy, a nieprzepisany dokument naprawia
    następne zdarzenie. Zostaje po niej ostrzeżenie w logu, bo cisza w tym miejscu znaczyłaby
    „dokumenty są świeże", a nie są.
    """
    if project_id is None:
        # Ustalenie całej przestrzeni nie należy do żadnego projektu, a dokumenty są projektowe.
        # Podręcznik platformy (dokument przestrzeni) dochodzi w etapie C i wtedy to się zmieni.
        return False
    try:
        from control_plane.jobs.postgres_transport import PostgresJobTransport
        from control_plane.jobs.store import JobStore

        port = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
        await schedule_rebuild(
            port,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            now=now,
        )
    except Exception as exc:  # kolejka nie może przewrócić zapisu do rejestru
        _LOG.warning(
            "nie udało się zamówić przepisania dokumentów projektu %s: %s: %s",
            project_id,
            type(exc).__name__,
            exc,
        )
        return False
    return True


@dataclass
class DocumentHandlers:
    """Jeden job nad jedną pulą. Nic z tego nie bierze się ze środowiska i to jest celowe:
    wdrożenie nie może skończyć z połową rutyny domykania."""

    pool: asyncpg.Pool
    organization_id: UUID

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {JOB_REBUILD: self.rebuild}

    async def rebuild(self, claim: ClaimedJob) -> None:
        """Przepisz komplet dokumentów jednego projektu z jego rejestru.

        Projekt, którego już nie ma, nie jest awarią tego jobu: zdarzenie zamówiło przepisanie,
        zanim ktoś projekt skasował, i jedyne, co da się wtedy zrobić, to skończyć cicho.
        """
        from control_plane.documents.store import DocumentRefused, DocumentStore

        project_id = project_from_ref(claim.payload_ref)
        if claim.workspace_id is None:
            raise ValueError(
                f"job {JOB_REBUILD} bez workspace'u: dokumenty są zawsze czyjeś "
                f"(projekt {project_id})"
            )
        try:
            saved = await DocumentStore(self.pool).rebuild_project(
                organization_id=self.organization_id,
                workspace_id=claim.workspace_id,
                project_id=project_id,
            )
        except DocumentRefused as exc:
            _LOG.info("przepisanie dokumentów projektu %s pominięte: %s", project_id, exc)
            return
        fresh = sum(1 for _version, created in saved if created)
        _LOG.info(
            "dokumenty projektu %s przepisane: %d ról, nowych wersji %d",
            project_id,
            len(saved),
            fresh,
        )


def build_document_handlers(pool: Any, *, organization_id: UUID) -> DocumentHandlers:
    return DocumentHandlers(pool=pool, organization_id=organization_id)
