"""Włączanie i wyłączanie OBSERWACJI społeczności, jako JEDNA usługa dla ekranu i dla agenta.

CO TO ZNACZY OBSERWACJA, I CZEGO NIE ZNACZY (E3, audyt UX 20.09). Ten moduł włącza czytanie
publicznych wątków społeczności. Nie zapisuje naszego konta do społeczności na platformie, nie
jest widoczny dla jej moderatorów i nie wymaga konta. Do 20.09 wołał go `communities.join`, a
karta mówiła „Dołączono", czyli produkt raportował skutek, którego nie osiągał. Nazwy są dziś
rozdzielone: obserwację włącza `communities.observe`, a członkostwo konta ma własną pozycję w
katalogu i własną odpowiedź.

PO CO OSOBNY MODUŁ (D-046). Kliknięcie „Obserwuj” na karcie w rozmowie i narzędzie
`communities.observe` mają robić DOKŁADNIE to samo, bo inaczej karta pokazuje stan, którego agent
nie umie osiągnąć, albo odwrotnie. Trasa HTTP i rejestr narzędzi wołają więc te same dwie funkcje,
a nie dwie kopie tej samej sekwencji: sprawdź klucz adapterem, wstaw wpis, zapisz do audytu, zamów
pierwszy odczyt.

TRZY RZECZY, KTÓRE TA USŁUGA TRZYMA:

  1. POWTÓRZENIE NIE SZKODZI. Wstawienie jest `ON CONFLICT ... DO UPDATE`, a wynik mówi, czy
     społeczność powstała teraz, czy już była. Drugie kliknięcie daje ten sam stan, nie błąd.
  2. KLUCZ SPRAWDZA ADAPTER, nie my. `r/nie-ma-takiego` ma zostać odrzucone przez tego, kto wie,
     jak wygląda nazwa na tej platformie, a nie przez wyrażenie regularne w trzech miejscach.
  3. OBSERWACJA TO NIE ZGODA NA PISANIE. Czy w tej społeczności wolno publikować, rozstrzyga
     delegacja i decyzja o własnych postach (0025), osobno i świadomie.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from community_observer import UnknownCommunity
from community_os_contracts import ObservationPlatform

from control_plane.audit import AuditWriter
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.scheduling import schedule_poll
from control_plane.observe.store import CommunityRow, ObservationStore
from control_plane.observe.wiring import build_sources

#: Dziesięć minut między odczytami jednej społeczności, ta sama wartość, co na ekranie.
DEFAULT_POLL_INTERVAL_SECONDS = 600


class MembershipRefused(Exception):
    """Operacja nie może się odbyć i powód nadaje się do pokazania człowiekowi."""


@dataclass(frozen=True)
class Membership:
    """Stan społeczności po operacji. To samo, co karta SPOŁECZNOŚĆ pokazuje po kliknięciu."""

    community_id: UUID
    platform: str
    key: str
    display_name: str
    observing: bool
    #: Czy wpis powstał TERAZ. Drugie kliknięcie daje `False` i ten sam stan.
    created: bool
    #: Job pierwszego odczytu, gdy dołączenie go zamówiło. Bez tego „dołączono” znaczyłoby tylko
    #: „jest wiersz w tabeli”, a człowiek czeka na to, że coś się przeczyta.
    first_poll_job_id: UUID | None = None
    #: Czy ten job powstał teraz, czy taki już czekał w kolejce.
    first_poll_created: bool = False
    #: Pełny wiersz, dla wołających, którzy zwracają go dalej: trasa HTTP ma swój
    #: `community_payload` i ma go używać.
    row: CommunityRow | None = None

    def as_card(self) -> dict[str, Any]:
        return {
            "id": str(self.community_id),
            "platforma": self.platform,
            "klucz": self.key,
            "nazwa": self.display_name,
            "obserwowana": self.observing,
            "utworzona_teraz": self.created,
            "pierwszy_odczyt": str(self.first_poll_job_id) if self.first_poll_job_id else None,
        }


async def start_observing(
    pool: Any,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    platform: str,
    community_key: str,
    actor_ref: str,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
) -> Membership:
    """Zacznij obserwować jedną społeczność. Idempotentne."""
    try:
        wanted = ObservationPlatform(platform)
    except ValueError as exc:
        raise MembershipRefused(f"platforma {platform!r} nie jest znana w tym produkcie") from exc
    source = build_sources(os.environ).get(wanted)
    if source is None:
        raise MembershipRefused(
            f"brak adaptera dla {wanted.value} w tym procesie, więc nie ma czym sprawdzić klucza"
        )
    try:
        ref = source.describe(community_key)
    except UnknownCommunity as exc:
        raise MembershipRefused(str(exc)) from exc

    store = ObservationStore(pool)
    before = await store.community_by_key(
        organization_id=organization_id,
        workspace_id=workspace_id,
        platform=wanted.value,
        community_key=ref.key,
    )
    community = await store.upsert_community(
        organization_id=organization_id,
        workspace_id=workspace_id,
        ref=ref,
        poll_interval_seconds=poll_interval_seconds,
    )
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="COMMUNITY_OBSERVATION_ENABLED",
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_ref=f"community:{community.id}",
            actor_ref=actor_ref,
            # Sam identyfikator: `ALLOWED_PAYLOAD_KEYS` wpuszcza do audytu identyfikatory i
            # wartości z zamkniętych list, nie nazwy. Klucz społeczności bierze się z wiersza.
            payload={"community_id": str(community.id)},
        )
    queue = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
    first_poll = await schedule_poll(
        queue,
        organization_id=organization_id,
        workspace_id=workspace_id,
        community_id=community.id,
        interval_seconds=community.poll_interval_seconds,
        now=datetime.now(UTC),
        immediately=True,
    )
    return Membership(
        community_id=community.id,
        platform=community.platform,
        key=community.community_key,
        display_name=community.display_name,
        observing=community.observe_enabled,
        created=before is None,
        first_poll_job_id=first_poll.job_id,
        first_poll_created=first_poll.created,
        row=community,
    )


async def stop_observing(
    pool: Any,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    platform: str,
    community_key: str,
    actor_ref: str,
) -> Membership:
    """Przestań obserwować. Nie kasuje tego, co już przeczytane: obserwacja to nie historia."""
    store = ObservationStore(pool)
    community = await store.community_by_key(
        organization_id=organization_id,
        workspace_id=workspace_id,
        platform=platform,
        community_key=community_key,
    )
    if community is None:
        raise MembershipRefused(
            f"{community_key} nie jest obserwowana w tym workspace, więc nie ma czego wyłączać"
        )
    updated = await store.set_observing(
        organization_id=organization_id,
        workspace_id=workspace_id,
        community_id=community.id,
        enabled=False,
    )
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="COMMUNITY_OBSERVATION_DISABLED",
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_ref=f"community:{community.id}",
            actor_ref=actor_ref,
            payload={"community_id": str(community.id)},
        )
    return Membership(
        community_id=updated.id,
        platform=updated.platform,
        key=updated.community_key,
        display_name=updated.display_name,
        observing=updated.observe_enabled,
        created=False,
        row=updated,
    )
