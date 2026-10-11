"""Jeden `ClaimedJob` do testów handlerów, zamiast kopii w każdym pliku.

PO CO POWSTAŁ, i to jest pomiar, nie estetyka. `ClaimedJob` ma trzynaście pól, w tym cztery od
lease'u (`attempt_no`, `lease_generation`, `lease_owner`, `expires_at`). W testach istnieje dziś
kilkanaście ręcznych kopii tej krotki i każda powtarza te same trzynaście argumentów. 21.09
napisałem kolejną, bez czterech ostatnich pól, i przeszła wszystko, co dało się uruchomić bez
bazy: `ruff`, import, zbieranie testów. Padła dopiero w przebiegu z bazą, czterema błędami
`TypeError` naraz, bo do tego czasu nikt tej krotki nie zbudował naprawdę.

Wniosek jest ten sam, co przy innych usterkach kleju w tym repozytorium: konstrukcja cudzego
kontraktu przepisywana z pamięci jest miejscem, w którym testy przestają cokolwiek trzymać.
Nowe testy handlerów biorą krotkę stąd; starych kopii nie ruszam w tym samym commicie, żeby
poprawka usterki nie mieszała się z przepisywaniem dwudziestu plików.

DŁUG, ŚWIADOMY I ZAPISANY (decyzja koordynatora, 21.09): w `tests/` zostaje kilkanaście ręcznych
kopii tej krotki. Każda z nich jest dziś kompletna, ale każda następna zmiana pól `ClaimedJob`
wymaga przejścia po wszystkich, a pominięcie jednej wygląda dokładnie tak, jak wyglądała ta
usterka: zielono do pierwszego przebiegu z bazą. Przepisanie ich na ten pomocnik to osobne
zadanie, nie poprawka.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from control_plane.domain.models import ClaimedJob


def claimed_job(
    *,
    organization_id: UUID,
    job_type: str,
    routing_key: str,
    workspace_id: UUID | None = None,
    payload_ref: str | None = None,
    priority: int = 30,
    attempt_limit: int = 1,
    attempt_no: int = 1,
    expires_at: datetime | None = None,
) -> ClaimedJob:
    """Job wzięty z kolejki, z kompletem pól kontraktu. Domyślne są tylko te nieistotne dla
    handlera: identyfikatory próby i lease'u, których handler nie czyta, a które muszą być."""
    return ClaimedJob(
        job_id=uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=job_type,
        routing_key=routing_key,
        priority=priority,
        payload_ref=payload_ref,
        dispatch_version=1,
        attempt_limit=attempt_limit,
        attempt_id=uuid4(),
        attempt_no=attempt_no,
        lease_generation=1,
        lease_owner=uuid4(),
        expires_at=expires_at,
    )
