"""Dane widoku „Od Twojej ostatniej wizyty", liczone z tabel, bez jednego wywołania modelu.

CZEMU BEZ MODELU. Podsumowanie napisane przez model jest ładniejsze i jest DRUGĄ wersją tego,
co się wydarzyło. Gdy się rozjedzie z pierwszą, człowiek uwierzy ładniejszej. Tutaj każda liczba
pochodzi z wiersza, każdy wiersz prowadzi do swojego źródła, a zdania są szablonami, nie
generowanym tekstem.

OKNO JEST OKNEM NIEOBECNOŚCI, NIE DOBĄ. „Ostatnie 24 godziny" to inne pytanie niż „odkąd mnie
nie było" i tylko drugie ma sens po dwóch dniach przerwy. Okno bierze się z `project_visits`
(0024): od poprzedniej wizyty do teraz. Pierwsza wizyta pokazuje całą historię projektu i mówi
o tym wprost, zamiast udawać, że przed chwilą coś się zaczęło.

CISZA TEŻ JEST WYNIKIEM I MA DWIE RÓŻNE POSTACIE, których nie wolno pomylić:
  * kolektor pracował N razy i nie było o czym pisać, oraz
  * kolektor nie pracował ani razu.
Brak obudzeń agenta nie dowodzi braku odczytów kolektora ani awarii. Odczyty są mierzone osobno.

CZEGO TEN MODUŁ NIE TWIERDZI. Nie twierdzi, że agent ZASTOSOWAŁ korektę człowieka. Tego z bazy
nie widać: widać, że korekta była zapisana, zanim sesja ruszyła, i że ta sesja pytała o pamięć.
Tyle wraca i tak jest to nazwane. „Zastosowana" znaczyłoby zajrzenie modelowi do głowy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.sessions.summary import one_sentence, title_of

#: Po jakiej przerwie wejście w projekt przesuwa okno. Piętnaście minut, żeby odświeżenie strony
#: albo powrót z innej zakładki nie skasowały tego, po co człowiek przyszedł. Krócej i widok
#: znika w trakcie czytania; dłużej i dwa wejścia tego samego wieczoru pokazują to samo dwa razy.
VISIT_GRACE_MINUTES = 15

#: Narzędzia pamięci. Sesja, która żadnego nie zawołała, nie widziała korekt, choćby były
#: zapisane: to jest granica tego, co ten moduł może powiedzieć o „zastosowaniu".
MEMORY_TOOLS = ("memory.list", "memory.search")


@dataclass(frozen=True)
class Window:
    """Okno nieobecności. `since is None` znaczy pierwszą wizytę, nie brak danych."""

    since: datetime | None
    until: datetime
    first_visit: bool

    def sentence(self) -> str:
        if self.first_visit:
            return "To Twoja pierwsza wizyta w tym projekcie, więc poniżej jest cała jego historia."
        assert self.since is not None
        hours = max(1, round((self.until - self.since).total_seconds() / 3600))
        return f"Od Twojej ostatniej wizyty, czyli przez ostatnie {hours} h."


@dataclass(frozen=True)
class ReturnView:
    window: Window
    wakeups: dict[str, Any]
    sessions: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    publications: tuple[dict[str, Any], ...]
    corrections: tuple[dict[str, Any], ...]
    incidents: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: 27.09: follower counts of the project's accounts at the window's start and end, measured.
    audience: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def quiet(self) -> bool:
        """Czy w oknie NIC się nie wydarzyło. Sam fakt pracy kolektora nie jest wydarzeniem."""
        return not (self.sessions or self.decisions or self.publications or self.corrections)


def _payload(raw: Any) -> Any:
    """asyncpg oddaje jsonb jako tekst albo jako obiekt, zależnie od kodeka. Obie drogi tutaj."""
    if raw is None:
        return None
    if isinstance(raw, str | bytes | bytearray):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


class ReturnStore:
    """Siedem odczytów i jeden zapis: znacznik wizyty."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- znacznik wizyty ---------------------------------------------------------------------

    async def visit(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID | None,
        now: datetime | None = None,
        grace_minutes: int = VISIT_GRACE_MINUTES,
    ) -> Window:
        """Zanotuj wejście i oddaj okno, które należy pokazać.

        JEDNO ZDANIE SQL, NIE ODCZYT I ZAPIS. Dwa wejścia w tej samej sekundzie z dwóch kart
        przeglądarki to normalne zdarzenie, a wersja czytająca najpierw wiersz, a potem go
        nadpisująca, gubiłaby okno jednej z nich. `ON CONFLICT` z warunkiem na karencję robi to
        samo atomowo, a `RETURNING` oddaje stan PO decyzji bazy, nie przed nią.
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO project_visits
                    (organization_id, workspace_id, project_id, user_id,
                     previous_seen_at, last_seen_at)
                VALUES ($1, $2, $3, $4, NULL, coalesce($5, now()))
                ON CONFLICT (organization_id, project_id, user_id) DO UPDATE
                   SET previous_seen_at = CASE
                           WHEN coalesce($5, now()) - project_visits.last_seen_at
                                > make_interval(mins => $6)
                           THEN project_visits.last_seen_at
                           ELSE project_visits.previous_seen_at
                       END,
                       last_seen_at = CASE
                           WHEN coalesce($5, now()) - project_visits.last_seen_at
                                > make_interval(mins => $6)
                           THEN coalesce($5, now())
                           ELSE project_visits.last_seen_at
                       END,
                       visits = project_visits.visits + 1
                RETURNING previous_seen_at, last_seen_at,
                          (xmax = 0) AS inserted
                """,
                organization_id,
                workspace_id,
                project_id,
                user_id,
                now,
                grace_minutes,
            )
        first = bool(row["inserted"]) or row["previous_seen_at"] is None
        # KONIEC OKNA TO TERAZ, NIE ZNACZNIK WIZYTY. Przy odświeżeniu w czasie karencji
        # `last_seen_at` zostaje nietknięty, a okno kończące się na nim ucinałoby wszystko, co
        # wydarzyło się od pierwszego wejścia: człowiek odświeżałby stronę i nie widział nowych
        # zdarzeń. Zmierzone testem `a_refresh_does_not_wipe_the_window`.
        return Window(
            since=None if first else row["previous_seen_at"],
            until=now or datetime.now(UTC),
            first_visit=first,
        )

    async def window_of(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID | None,
        now: datetime,
    ) -> Window:
        """Okno BEZ notowania wizyty: dla raportu, który ma nie zmieniać tego, co mierzy."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT previous_seen_at FROM project_visits
                WHERE organization_id = $1 AND project_id = $2
                  AND user_id IS NOT DISTINCT FROM $3
                """,
                organization_id,
                project_id,
                user_id,
            )
        since = row["previous_seen_at"] if row is not None else None
        return Window(since=since, until=now, first_visit=since is None)

    # -- widok -------------------------------------------------------------------------------

    async def view(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        window: Window,
    ) -> ReturnView:
        since = window.since
        until = window.until
        async with self._pool.acquire() as connection:
            wakeups = await self._wakeups(connection, organization_id, project_id, since, until)
            sessions = await self._sessions(connection, organization_id, project_id, since, until)
            decisions = await self._decisions(connection, organization_id, workspace_id, project_id)
            publications = await self._publications(
                connection, organization_id, project_id, since, until
            )
            corrections = await self._corrections(
                connection, organization_id, workspace_id, project_id, since, until
            )
            incidents = await self._incidents(connection, organization_id, since, until)
            audience = await self._audience(connection, organization_id, project_id, since, until)

        notes: list[str] = []
        if wakeups["razem"] == 0:
            # No wakeups says nothing about independent collector reads or delegation.
            notes.append("W tym czasie nie uruchomiono pracy agenta w tle.")
        elif not sessions and not publications:
            notes.append(
                f"Nic się nie wydarzyło, a kolektor pracował {wakeups['razem']} razy. "
                "Cisza jest zmierzona, nie domyślona."
            )
        # The number the founder asked for on X (27.09), said as a sentence like every other note.
        for change in audience:
            notes.append(change.sentence(first_visit=window.first_visit))
            stale = change.stale_sentence(until=until)
            if stale is not None:
                notes.append(stale)
        return ReturnView(
            window=window,
            wakeups=wakeups,
            sessions=sessions,
            decisions=decisions,
            publications=publications,
            corrections=corrections,
            incidents=incidents,
            notes=tuple(notes),
            audience=tuple(change.to_json() for change in audience),
        )

    # -- pojedyncze odczyty ------------------------------------------------------------------

    async def _audience(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        project_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> list[Any]:
        """Follower counts of the accounts this project works under, where measured (27.09)."""
        from control_plane.workspace.audience import followers_across

        account_ids = [
            row["social_account_id"]
            for row in await connection.fetch(
                "SELECT social_account_id FROM project_account_assignments "
                "WHERE organization_id = $1 AND project_id = $2 AND unassigned_at IS NULL",
                organization_id,
                project_id,
            )
        ]
        return await followers_across(
            connection,
            organization_id=organization_id,
            account_ids=account_ids,
            since=since,
            until=until,
        )

    async def _wakeups(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        project_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> dict[str, Any]:
        rows = await connection.fetch(
            """
            SELECT reason, skipped_reason, count(*) AS ile,
                   count(*) FILTER (WHERE session_id IS NOT NULL) AS with_session
            FROM wakeups
            WHERE organization_id = $1 AND project_id = $2
              AND due_at IS NULL
              AND created_at <= $4 AND ($3::timestamptz IS NULL OR created_at > $3)
            GROUP BY reason, skipped_reason
            """,
            organization_id,
            project_id,
            since,
            until,
        )
        by_reason: dict[str, int] = {}
        skipped: dict[str, int] = {}
        total = 0
        skipped_total = 0
        with_session = 0
        for row in rows:
            count = int(row["ile"])
            total += count
            with_session += int(row["with_session"])
            by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + count
            if row["skipped_reason"] is not None:
                skipped_total += count
                skipped[row["skipped_reason"]] = skipped.get(row["skipped_reason"], 0) + count
        return {
            "razem": total,
            "powody": by_reason,
            "pominiete": skipped_total,
            "powody_pominiecia": skipped,
            "z_sesja": with_session,
        }

    async def _sessions(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        project_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> tuple[dict[str, Any], ...]:
        rows = await connection.fetch(
            """
            SELECT s.id, s.status, s.goal, s.result_summary, s.result_refs,
                   s.created_at, s.finished_at,
                   coalesce(t.tools, ARRAY[]::text[]) AS tools
            FROM agent_sessions s
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT e.payload ->> 'tool') AS tools
                FROM session_events e
                WHERE e.session_id = s.id AND e.kind = 'TOOL_CALL'
                  AND e.payload ->> 'tool' IS NOT NULL
            ) t ON true
            WHERE s.organization_id = $1 AND s.project_id = $2
              AND s.created_at <= $4 AND ($3::timestamptz IS NULL OR s.created_at > $3)
            ORDER BY s.created_at DESC
            LIMIT 20
            """,
            organization_id,
            project_id,
            since,
            until,
        )
        return tuple(
            {
                "session_id": str(row["id"]),
                "status": row["status"],
                "cel": row["goal"],
                # `result_summary` jest NULL, dopóki tura się nie skończyła. NULL wraca jako NULL,
                # a nie jako pusty tekst: „sesja jeszcze nie odpowiedziała" to nie to samo,
                # co „odpowiedziała pustką".
                "wynik": row["result_summary"],
                "wynik_zdanie": one_sentence(row["result_summary"]),
                "tytul": title_of(row["goal"]),
                "odnosniki": _payload(row["result_refs"]) or [],
                "narzedzia": [tool for tool in (row["tools"] or []) if tool],
                "zaczeto": row["created_at"].isoformat(),
                "skonczono": row["finished_at"].isoformat() if row["finished_at"] else None,
            }
            for row in rows
        )

    async def _decisions(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> tuple[dict[str, Any], ...]:
        """Propozycje czekające TERAZ, nie w oknie: czekanie jest stanem, nie zdarzeniem.

        Ten sam join, co w `api/presence.py::read_decisions` i z tego samego powodu: propozycja
        zna swoje konto, a projekt dostaje konta. Wygasłych nie ma, bo ich okno zgody się
        zamknęło i żaden przycisk go nie otworzy.
        """
        rows = await connection.fetch(
            """
            SELECT r.id AS revision_id, r.created_at, r.expires_at,
                   r.payload ->> 'text_and_media' AS text,
                   r.payload ->> 'target_url' AS target_url,
                   a.external_account_id
              FROM action_proposal_revisions r
              JOIN action_proposals p
                ON p.id = r.proposal_id AND p.organization_id = r.organization_id
              JOIN social_accounts a ON a.id = p.social_account_id
              JOIN project_account_assignments x
                ON x.organization_id = p.organization_id
               AND x.social_account_id = p.social_account_id
               AND x.unassigned_at IS NULL
             WHERE r.organization_id = $1 AND r.workspace_id = $2 AND x.project_id = $3
               AND r.payload ->> 'plan_version' = $3::text
               AND r.state = 'READY_FOR_APPROVAL'
               AND r.expires_at > now()
               AND NOT EXISTS (
                   SELECT 1 FROM publication_requests q WHERE q.proposal_revision_id = r.id)
             ORDER BY r.created_at
             LIMIT 20
            """,
            organization_id,
            workspace_id,
            project_id,
        )
        from control_plane.pilot.publication import split_media

        decisions = []
        for row in rows:
            # The text as it would go on the platform; an attached image is named apart (27.09).
            text, media = split_media(row["text"] or "")
            decisions.append(
                {
                    "revision_id": str(row["revision_id"]),
                    "tekst": text if row["text"] is not None else None,
                    "obraz": str(media.artifact_id) if media is not None else None,
                    "adres": row["target_url"],
                    "konto": row["external_account_id"],
                    "czeka_od": row["created_at"].isoformat(),
                    "wygasa": row["expires_at"].isoformat(),
                }
            )
        return tuple(decisions)

    async def _publications(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        project_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> tuple[dict[str, Any], ...]:
        """Publikacje projektu z oknem, każda ze swoją OSTATNIĄ obserwacją.

        Widoczność nie jest polem publikacji, tylko ostatnim pomiarem: `still_present` z chwili,
        w której ktoś naprawdę tam zajrzał. Publikacja bez obserwacji wraca z `widocznosc: null`,
        i to jest uczciwe „nie sprawdzaliśmy", a nie „zniknęła".
        """
        rows = await connection.fetch(
            """
            SELECT q.id, q.status, q.target_url, q.published_at, q.queued_at, q.detail, q.kind,
                   o.observed_at, o.still_present, o.score, o.reply_count, o.views
            FROM publication_requests q
            LEFT JOIN LATERAL (
                SELECT observed_at, still_present, score, reply_count,
                       CASE WHEN raw->'metrics'->>'views' ~ '^[0-9]{1,15}$'
                            THEN (raw->'metrics'->>'views')::bigint END AS views
                FROM publication_observations
                WHERE request_id = q.id
                ORDER BY observed_at DESC
                LIMIT 1
            ) o ON true
            WHERE q.organization_id = $1 AND q.project_id = $2
              AND q.queued_at <= $4 AND ($3::timestamptz IS NULL OR q.queued_at > $3)
            ORDER BY q.queued_at DESC
            LIMIT 20
            """,
            organization_id,
            project_id,
            since,
            until,
        )
        return tuple(
            {
                "request_id": str(row["id"]),
                "status": row["status"],
                # 0023: „nasz wątek" czy „odpowiedź". Człowiek wracający po dwóch dniach ma prawo
                # wiedzieć, czy agent założył coś swojego, czy dopisał się do cudzej rozmowy.
                "rodzaj": row["kind"],
                "adres": row["target_url"],
                "opublikowano": row["published_at"].isoformat() if row["published_at"] else None,
                "powod": row["detail"],
                "widocznosc": (
                    None
                    if row["observed_at"] is None
                    else {
                        "sprawdzono": row["observed_at"].isoformat(),
                        "jest": row["still_present"],
                        "punkty": row["score"],
                        "odpowiedzi": row["reply_count"],
                        # 27.09: views, when the platform showed them (X does, to the author);
                        # a new account's reach is read here before any reply comes. Absent from
                        # the read stays `None`, never 0, as everywhere in the observation.
                        "wyswietlenia": row["views"],
                    }
                ),
            }
            for row in rows
        )

    async def _corrections(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> tuple[dict[str, Any], ...]:
        """Korekty człowieka z okna i to, czy jakaś sesja mogła je w ogóle zobaczyć.

        `w_zasiegu_sesji` znaczy dokładnie tyle: po zapisaniu korekty ruszyła sesja tego projektu,
        która zawołała narzędzie pamięci. Nie znaczy „agent posłuchał". Tego z bazy nie widać i
        nazwanie tego „zastosowaną" byłoby wpisaniem w raport rzeczy, której nikt nie zmierzył.
        """
        rows = await connection.fetch(
            """
            SELECT k.id, k.statement, k.created_at, k.payload,
                   EXISTS (
                       SELECT 1
                       FROM agent_sessions s
                       JOIN session_events e ON e.session_id = s.id
                       WHERE s.organization_id = k.organization_id
                         AND s.project_id = $2
                         AND s.created_at > k.created_at
                         AND e.kind = 'TOOL_CALL'
                         AND e.payload ->> 'tool' = ANY($5::text[])
                   ) AS w_zasiegu
            FROM knowledge_items k
            WHERE k.organization_id = $1 AND k.workspace_id = $6 AND k.project_id = $2
              AND k.kind = 'PREFERENCE' AND k.stated_by = 'USER'
              AND k.retracted_at IS NULL
              AND k.created_at <= $4 AND ($3::timestamptz IS NULL OR k.created_at > $3)
            ORDER BY k.created_at DESC
            LIMIT 20
            """,
            organization_id,
            project_id,
            since,
            until,
            list(MEMORY_TOOLS),
            workspace_id,
        )
        return tuple(
            {
                "item_id": str(row["id"]),
                "tresc": row["statement"],
                "zapisano": row["created_at"].isoformat(),
                "w_zasiegu_sesji": bool(row["w_zasiegu"]),
                "publikacja": (_payload(row["payload"]) or {}).get("request_id"),
            }
            for row in rows
        )

    async def _incidents(
        self,
        connection: asyncpg.Connection,
        organization_id: UUID,
        since: datetime | None,
        until: datetime,
    ) -> tuple[dict[str, Any], ...]:
        """Incydenty komponentów nakładające się na okno.

        ORGANIZACYJNE, NIE PROJEKTOWE, bo taki jest dyspozytor i taki jest kolektor: gdy stanie,
        stoi dla wszystkich projektów. Pokazujemy je tutaj, bo „agent nic nie zrobił" i „agent
        nie mógł nic zrobić" to dwie różne odpowiedzi na to samo pytanie człowieka.
        """
        rows = await connection.fetch(
            """
            SELECT id, component, reason, detected_by, started_at, ended_at
            FROM incidents
            WHERE organization_id = $1
              AND started_at <= $3
              AND ($2::timestamptz IS NULL OR ended_at IS NULL OR ended_at > $2)
            ORDER BY started_at DESC
            LIMIT 20
            """,
            organization_id,
            since,
            until,
        )
        return tuple(
            {
                "incident_id": str(row["id"]),
                "komponent": row["component"],
                "powod": row["reason"],
                "wykryl": row["detected_by"],
                "od": row["started_at"].isoformat(),
                "do": row["ended_at"].isoformat() if row["ended_at"] else None,
                "trwa": row["ended_at"] is None,
            }
            for row in rows
        )


def since_hours(window: Window, hours: int, now: datetime) -> Window:
    """Okno o stałej długości, dla raportu walidacji (dzień, tydzień), nie dla ekranu."""
    return Window(since=now - timedelta(hours=hours), until=now, first_visit=False)
