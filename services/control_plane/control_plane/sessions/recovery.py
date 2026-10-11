"""Naprawa rozmów, które czekają na termin, który nigdy nie nadejdzie (D-045, live 20.09).

CO SIĘ STAŁO. Rozmowa doszła do limitu konta Codex, a odczyt `account/rateLimits/read` dał
liczbę, którą moduł limitów potraktował jako odstęp, choć była epoką. Powrót zaplanowano na
15 czerwca 2083. Sesja stała w `WAITING_EVENT`, czyli wyglądała na czekającą, i nikt nie miał
powodu jej sprawdzać: nie była błędem, nie była porażką, po prostu miała wrócić.

PRZELICZENIE JEST JUŻ NAPRAWIONE, a ten moduł jest o czymś innym: o wierszach, które POWSTAŁY
przed naprawą. Poprawka w kodzie nie cofa jobów zaplanowanych na przyszły wiek, a sesja, która
ich dotyczy, nie odezwie się sama z definicji. Dlatego naprawa startuje razem z procesem, tam
gdzie zasiewane są łańcuchy operacyjne, i jest idempotentna: druga próba nie znajduje już nic.

DRUGI PRZYPADEK, zmierzony następnego przebiegu tego samego dnia: termin z TEKSTU o dobę
później niż wiarygodna liczba z silnika. Tekst niesie samą godzinę bez daty, więc „try again at
8:33 AM" odczytane o 8:33:14 znaczy jutro. Sesja miała wrócić za dobę, mimo że silnik w tym samym
zdarzeniu podał odnowienie na za dwie sekundy. Reguła wyboru jest już poprawiona, ale joby
zaplanowane wcześniej zostają, i ten moduł je zdejmuje.

CZEGO TEN MODUŁ NIE ROBI: nie zgaduje nowego terminu z niczego. Bierze go z tego, co stoi w
śladzie: najpierw z liczby silnika (`z_silnika`), potem z surowego komunikatu („try again at
8:33 AM"), a gdy nie ma żadnego, planuje ślepą próbę za kwadrans. Zapisuje też w rozmowie zdanie
o korekcie, bo człowiek przeczytał wcześniej godzinę, która już nie obowiązuje.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from control_plane.sessions import rate_limit as limits
from control_plane.sessions.store import EVENT_STATUS, STATUS_WAITING_EVENT, SessionStore

JOB_SESSION_TURN = "session.turn"

#: Prefiks `payload_ref` joba tury. Ten sam kształt, który pisze `schedule_turn`.
_PAYLOAD_PREFIX = "session/"


async def replan_impossible_returns(
    pool: Any, *, organization_id: UUID, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Przeplanuj powroty, których termin wypada dalej niż `MAX_AHEAD`.

    Zwraca po jednym wpisie na naprawioną rozmowę, żeby wołający miał co wpisać do logu. Pusta
    lista znaczy „nie było czego naprawiać" i jest normalnym wynikiem, nie ciszą.
    """
    moment = now or datetime.now(UTC)
    horizon = moment + limits.MAX_AHEAD
    #: O ile termin joba musi być później od liczby silnika, żeby uznać go za pomyłkę. Pół
    #: godziny: mniejsza różnica mieści się w odstępie ponowienia i nie jest niczym złym.
    drift = timedelta(minutes=30)
    fixed: list[dict[str, Any]] = []
    # Zdarzenie dopisuje STORE, nie to zapytanie. Numer `seq` bierze się tam pod zamkiem na
    # sesję, bo wersja licząca `max(seq) + 1` w INSERCIE została ZMIERZONA jako gubiąca zdarzenia
    # przy dwóch piszących naraz; powtórzenie jej tutaj byłoby cofnięciem tamtej poprawki.
    sessions = SessionStore(pool)

    async with pool.acquire() as conn:
        # KAŻDY PRZYSZŁY POWRÓT, nie tylko ten za sto lat. Drugi przypadek (doba zamiast dwóch
        # sekund) mieści się w horyzoncie i przy węższym zapytaniu byłby niewidoczny; wierszy
        # jest tyle, ile rozmów czeka, więc szeroki odczyt nic nie kosztuje.
        stuck = await conn.fetch(
            """
            SELECT id, payload_ref, not_before
              FROM jobs
             WHERE organization_id = $1 AND job_type = $2
               AND status = 'READY' AND not_before > $3
             ORDER BY not_before
            """,
            organization_id,
            JOB_SESSION_TURN,
            moment,
        )
        for job in stuck:
            session_id = _session_of(job["payload_ref"])
            if session_id is None:
                continue
            session = await conn.fetchrow(
                "SELECT id, status FROM agent_sessions WHERE organization_id = $1 AND id = $2",
                organization_id,
                session_id,
            )
            # Tylko rozmowy, które NAPRAWDĘ czekają. Job z odległym terminem przy sesji w innym
            # stanie jest czymś innym i nie zgaduję, czym: przeplanowanie go byłoby wejściem w
            # cudzy mechanizm.
            if session is None or str(session["status"]) != STATUS_WAITING_EVENT:
                continue

            payload = await _last_limit_event(conn, organization_id, session_id)
            when, source = _new_deadline(payload, moment)
            too_far = job["not_before"] > horizon
            from_engine = _moment_of(payload.get("z_silnika"))
            drifted = from_engine is not None and job["not_before"] - from_engine > drift
            if not too_far and not drifted:
                continue
            await conn.execute(
                "UPDATE jobs SET not_before = $2 WHERE id = $1 AND status = 'READY'",
                job["id"],
                when,
            )
            await sessions.append_event(
                organization_id=organization_id,
                session_id=session_id,
                kind=EVENT_STATUS,
                payload=_correction(when, source, job["not_before"]),
            )
            fixed.append(
                {
                    "session_id": str(session_id),
                    "bylo": job["not_before"].isoformat(),
                    "jest": when.isoformat(),
                    "skad": source,
                }
            )
    return fixed


async def _last_limit_event(conn: Any, organization_id: UUID, session_id: UUID) -> dict[str, Any]:
    """Payload ostatniego zdarzenia o limicie tej rozmowy, albo pusty słownik."""
    row = await conn.fetchrow(
        """
        SELECT payload FROM session_events
         WHERE organization_id = $1 AND session_id = $2 AND kind = $3
           AND payload ? 'surowy_komunikat'
         ORDER BY seq DESC LIMIT 1
        """,
        organization_id,
        session_id,
        EVENT_STATUS,
    )
    if row is None:
        return {}
    payload = row["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


def _new_deadline(payload: dict[str, Any], moment: datetime) -> tuple[datetime, str]:
    """Nowy termin ze śladu: liczba silnika, potem komunikat, potem ślepa próba.

    Liczba silnika, która JUŻ MINĘŁA, jest najlepszą informacją, jaką mamy: okno odnowiło się w
    międzyczasie, więc nie ma na co czekać poza minimalnym odstępem. Tak wyglądał przypadek z
    live: silnik podał 06:33:16, naprawa biegła o 06:40, a job stał na jutro.
    """
    from_engine = _moment_of(payload.get("z_silnika"))
    if from_engine is not None:
        return max(from_engine, moment + limits.MIN_GAP), "engine"
    from_text = limits.resets_from_text(str(payload.get("surowy_komunikat") or ""), now=moment)
    if from_text is not None:
        return max(from_text, moment + limits.MIN_GAP), "message"
    return moment + limits.BLIND_RETRY, "blind"


def _moment_of(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _correction(when: datetime, source: str, was: datetime) -> dict[str, Any]:
    """Zdanie o korekcie, do śladu. Człowiek przeczytał wcześniej godzinę, która już nie
    obowiązuje, więc nowa godzina ma stanąć w rozmowie, a nie tylko w kolejce."""
    local = when.astimezone().strftime("%H:%M")
    detail = (
        f"Poprawiłem termin powrotu: poprzedni ({was.astimezone():%Y-%m-%d %H:%M}) wynikał z "
        f"błędnie odczytanego limitu konta. Wrócę o {local}."
    )
    return {
        "status": STATUS_WAITING_EVENT,
        "detail": detail,
        "powod": "POPRAWKA_TERMINU_POWROTU",
        "odnowi_sie": when.isoformat(),
        "skad": source,
    }


def _session_of(payload_ref: str | None) -> UUID | None:
    if not payload_ref or not payload_ref.startswith(_PAYLOAD_PREFIX):
        return None
    try:
        return UUID(payload_ref[len(_PAYLOAD_PREFIX) :])
    except ValueError:
        return None
