"""Głos osoby: wersje z migracji 0022, plus propozycja przejęcia treści z profilu pilota.

DLACZEGO OSOBNY MODUŁ, A NIE METODY W `store.py`. Tamten plik jest o tym, KTO i CO jest
w przestrzeni: profile, konta, przypięcia. Ten jest o jednej rzeczy, która ma własną regułę
nienaruszalności: wersje głosu są dopisywane i nigdy nie zmieniane, a jedyny sposób, żeby ta
reguła przetrwała rok, to trzymać ją w jednym miejscu, gdzie widać ją całą.

CZEGO TU NIE MA: UPDATE i DELETE. Nie z ostrożności, tylko dlatego, że baza ich nie przyjmie
(wyzwalacz `person_voice_versions_append_only`). Gdyby ten moduł kiedykolwiek chciał poprawić
wersję w miejscu, dostanie wyjątek z bazy, a nie cichą zmianę historii.

PROPOZYCJA, NIE KOPIA. `pilot_voice()` czyta najnowszą `user_profile_versions` tej przestrzeni,
czyli profil, z którym pisał pilot, i zwraca ją jako materiał do PODJĘCIA DECYZJI. Nic tu nie
zapisuje. Przeniesienie treści do głosu osoby dzieje się wyłącznie przez `write()`, wywołane
po kliknięciu człowieka, bo profil pilota należy do konkretnego człowieka i to on ma powiedzieć,
czy to jest głos tej osoby w nowym modelu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

#: Cztery pola głosu, w kolejności, w jakiej czyta je człowiek na ekranie i model w narzędziu.
#: Nazwane raz, bo ta sama czwórka pojawia się w migracji, w API, w narzędziu i na ekranie,
#: a piąte pole dopisane w jednym z tych miejsc byłoby polem, którego reszta nie widzi.
VOICE_FIELDS: tuple[str, ...] = (
    "knows_deeply",
    "positions",
    "would_never_claim",
    "voice_samples",
)

#: Ile wersji wraca w historii bez pytania. Historia głosu rośnie wolno (to nie jest dziennik),
#: więc pięćdziesiąt to dużo więcej niż rok pracy jednej osoby.
HISTORY_LIMIT = 50


class VoiceRefusal(Exception):
    """Zapis, którego dziedzina odmawia, ze zdaniem do przeczytania przez człowieka (D-010)."""


@dataclass(frozen=True)
class VoiceVersionRow:
    id: UUID
    person_profile_id: UUID
    version: int
    knows_deeply: str
    positions: str
    would_never_claim: str
    voice_samples: str
    created_by: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class PilotVoiceRow:
    """Najnowsza wersja profilu pilota tej przestrzeni (`user_profile_versions`, 0004)."""

    id: UUID
    version: int
    knows_deeply: str
    positions: str
    would_never_claim: str
    voice_samples: str
    created_at: datetime


def _version(row: asyncpg.Record) -> VoiceVersionRow:
    return VoiceVersionRow(
        id=row["id"],
        person_profile_id=row["person_profile_id"],
        version=row["version"],
        knows_deeply=row["knows_deeply"],
        positions=row["positions"],
        would_never_claim=row["would_never_claim"],
        voice_samples=row["voice_samples"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class PersonInProject:
    """Jedna osoba w zasięgu projektu: identyfikator, nazwa profilu i jej konta."""

    person_profile_id: UUID
    name: str
    accounts: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "profil_osoby_id": str(self.person_profile_id),
            "profil_osoby": self.name,
            "konta": list(self.accounts),
        }


class PersonVoiceStore:
    """Wersje głosu jednej osoby. Zakres to zawsze trójka: organizacja, przestrzeń, profil."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def versions(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        person_profile_id: UUID,
        limit: int = HISTORY_LIMIT,
    ) -> tuple[VoiceVersionRow, ...]:
        """Historia, od najnowszej. Pusta krotka znaczy „ta osoba nie ma jeszcze głosu”."""
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id, person_profile_id, version, knows_deeply, positions,
                       would_never_claim, voice_samples, created_by, created_at
                FROM person_voice_versions
                WHERE organization_id = $1 AND workspace_id = $2 AND person_profile_id = $3
                ORDER BY version DESC
                LIMIT $4
                """,
                organization_id,
                workspace_id,
                person_profile_id,
                limit,
            )
        return tuple(_version(row) for row in rows)

    async def latest(
        self, *, organization_id: UUID, workspace_id: UUID, person_profile_id: UUID
    ) -> VoiceVersionRow | None:
        found = await self.versions(
            organization_id=organization_id,
            workspace_id=workspace_id,
            person_profile_id=person_profile_id,
            limit=1,
        )
        return found[0] if found else None

    async def write(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        person_profile_id: UUID,
        knows_deeply: str,
        positions: str,
        would_never_claim: str,
        voice_samples: str,
        created_by: UUID | None,
    ) -> VoiceVersionRow:
        """Nowa wersja. Numer liczy baza, więc dwa równoczesne zapisy nie dostaną tego samego.

        Ten sam kształt, co `observe/store.py::save_profile` dla profilu pilota: podzapytanie
        w `VALUES`, a nie odczyt i zapis w dwóch krokach. Przy równoczesnym zapisie przegrany
        dostanie `UniqueViolationError` z indeksu na (organizacja, przestrzeń, osoba, wersja)
        i tu zamienia się to w odmowę ze zdaniem, a nie w cichą nadpisaną wersję.
        """
        async with self._pool.acquire() as connection:
            try:
                row = await connection.fetchrow(
                    """
                    INSERT INTO person_voice_versions
                        (organization_id, workspace_id, person_profile_id, version,
                         knows_deeply, positions, would_never_claim, voice_samples, created_by)
                    VALUES ($1, $2, $3,
                            (SELECT coalesce(max(version), 0) + 1 FROM person_voice_versions
                              WHERE organization_id = $1 AND workspace_id = $2
                                AND person_profile_id = $3),
                            $4, $5, $6, $7, $8)
                    RETURNING id, person_profile_id, version, knows_deeply, positions,
                              would_never_claim, voice_samples, created_by, created_at
                    """,
                    organization_id,
                    workspace_id,
                    person_profile_id,
                    knows_deeply,
                    positions,
                    would_never_claim,
                    voice_samples,
                    created_by,
                )
            except asyncpg.UniqueViolationError as error:
                raise VoiceRefusal(
                    "ktoś zapisał nową wersję głosu tej osoby w tej samej chwili. "
                    "Odśwież ekran i zapisz jeszcze raz, żeby nie nadpisać cudzej zmiany"
                ) from error
            except asyncpg.ForeignKeyViolationError as error:
                raise VoiceRefusal(
                    f"nie ma profilu osoby {person_profile_id} w tej przestrzeni"
                ) from error
        return _version(row)

    async def pilot_voice(
        self, *, organization_id: UUID, workspace_id: UUID
    ) -> PilotVoiceRow | None:
        """Najnowszy profil pilota tej przestrzeni albo None. NIC nie zapisuje."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, version, knows_deeply, positions, would_never_claim,
                       voice_samples, created_at
                FROM user_profile_versions
                WHERE organization_id = $1 AND workspace_id = $2
                ORDER BY version DESC
                LIMIT 1
                """,
                organization_id,
                workspace_id,
            )
        if row is None:
            return None
        return PilotVoiceRow(
            id=row["id"],
            version=row["version"],
            knows_deeply=row["knows_deeply"],
            positions=row["positions"],
            would_never_claim=row["would_never_claim"],
            voice_samples=row["voice_samples"],
            created_at=row["created_at"],
        )

    async def has_active_account(
        self, *, organization_id: UUID, workspace_id: UUID, person_profile_id: UUID
    ) -> bool:
        """Czy ta osoba ma dziś przypięte konto, czyli czy w ogóle się gdzieś odzywa.

        Używane przez ekran do decyzji, czy proponować przejęcie głosu pilota. Profil bez konta
        jest szkicem, a głos pilota należy do człowieka, który naprawdę publikował.
        """
        async with self._pool.acquire() as connection:
            found = await connection.fetchval(
                """
                SELECT 1 FROM person_profile_accounts
                WHERE organization_id = $1 AND workspace_id = $2
                  AND person_profile_id = $3 AND detached_at IS NULL
                LIMIT 1
                """,
                organization_id,
                workspace_id,
                person_profile_id,
            )
        return found is not None

    async def people_of_project(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> list[PersonInProject]:
        """Osoby, których głos wolno przeczytać w TYM projekcie, z nazwami i kontami.

        TA SAMA REGUŁA ZAKRESU, CO `person_in_project`, tylko zapytana o listę zamiast o jeden
        identyfikator. Powstała, bo model nie miał SKĄD wziąć identyfikatora: `accounts.list`
        i `project.read` podawały samą nazwę profilu, a narzędzie przyjmowało wyłącznie UUID i
        odmawiało zdaniem „to nie jest identyfikator" (zmierzone na live 20.09, sesja 01ef065e).
        Lista pozwala odmówić UŻYTECZNIE: powiedzieć, kogo tu można przeczytać.

        Jedna osoba może mieć kilka kont w projekcie, więc konta są zebrane w tablicę, a nie
        rozbite na kilka wierszy: pytanie brzmi „kogo", nie „ile razy".
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT pp.id,
                       pp.name,
                       array_agg(a.external_account_id ORDER BY a.external_account_id) AS konta
                  FROM person_profile_accounts ppa
                  JOIN project_account_assignments paa
                    ON paa.organization_id = ppa.organization_id
                   AND paa.workspace_id = ppa.workspace_id
                   AND paa.social_account_id = ppa.social_account_id
                  JOIN person_profiles pp
                    ON pp.organization_id = ppa.organization_id
                   AND pp.workspace_id = ppa.workspace_id
                   AND pp.id = ppa.person_profile_id
                  JOIN social_accounts a
                    ON a.organization_id = ppa.organization_id
                   AND a.id = ppa.social_account_id
                 WHERE ppa.organization_id = $1 AND ppa.workspace_id = $2
                   AND ppa.detached_at IS NULL
                   AND paa.project_id = $3 AND paa.unassigned_at IS NULL
                 GROUP BY pp.id, pp.name
                 ORDER BY pp.name
                """,
                organization_id,
                workspace_id,
                project_id,
            )
        return [
            PersonInProject(
                person_profile_id=row["id"],
                name=str(row["name"]),
                accounts=tuple(str(one) for one in (row["konta"] or []) if one is not None),
            )
            for row in rows
        ]

    async def person_in_project(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        person_profile_id: UUID,
    ) -> bool:
        """Reguła zakresu osoby z P3, zapytana jednym razem dla narzędzia `person.voice`.

        Prawda tylko wtedy, gdy ta osoba ma AKTYWNIE przypięte konto, które jest AKTYWNIE
        przypisane do tego projektu. Dwa razy „aktywnie”, bo historia zostaje w obu tabelach:
        konto odpięte od profilu pół roku temu nie jest dziś tą osobą, a konto zdjęte z projektu
        nie jest dziś tym projektem. Bez tych dwóch warunków sesja jednego człowieka czytałaby
        głos drugiego, co jest dokładnie tą granicą, której P3 zabroniło przekraczać.
        """
        async with self._pool.acquire() as connection:
            found = await connection.fetchval(
                """
                SELECT 1
                FROM person_profile_accounts ppa
                JOIN project_account_assignments paa
                  ON paa.organization_id = ppa.organization_id
                 AND paa.workspace_id = ppa.workspace_id
                 AND paa.social_account_id = ppa.social_account_id
                WHERE ppa.organization_id = $1 AND ppa.workspace_id = $2
                  AND ppa.person_profile_id = $3 AND ppa.detached_at IS NULL
                  AND paa.project_id = $4 AND paa.unassigned_at IS NULL
                LIMIT 1
                """,
                organization_id,
                workspace_id,
                person_profile_id,
                project_id,
            )
        return found is not None
