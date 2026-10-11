"""Jeden tik obecności na PRAWDZIWYM Codeksie, uruchamiany ręką (P4 etap B).

    make p4-live-check
    make p4-live-check PILOT_ARGS="--project <uuid> --minutes 10"

CO TO SPRAWDZA, CZEGO NIE SPRAWDZIŁO NIC WCZEŚNIEJ. Cała mechanika P4 jest zmierzona na atrapie
tury: kolektor, obudzenia, deduplikacja, budżety i bramki działają na prawdziwej bazie, ale to,
co „odpowiada", jest scenariuszem. Ten tik jest drugą połową: prawdziwy model, prawdziwe
zdarzenia z bazy, prawdziwe narzędzia.

TRYB RESEARCH, I TO NIE JEST OSTROŻNOŚĆ, TYLKO DEFINICJA POMIARU. Pytanie brzmi „czy agent
obudzi się na prawdziwym zdarzeniu, sięgnie po narzędzie odczytu i skończy". Publikowanie nie
jest częścią tego pytania, a tryb RESEARCH sprawia, że nie może się zdarzyć przypadkiem:
`draft.create` odmawia w nim z powodu, a delegowana decyzja i tak nie wydałaby zgody.

WERDYKT JEST SUROWY W JEDNĄ STRONĘ. NIEZALICZONE dostaje także tura, w której model odpowiedział
ŁADNIE, ale nie zawołał ani jednego narzędzia: agent, który opowiada o zdarzeniu, zamiast je
przeczytać, jest agentem, który zgaduje, a P4 obiecuje pracę, nie opowiadanie o niej.

CZEGO TO NIE ROBI: nie zakłada projektu, nie podpisuje delegacji, nie publikuje i nie wybiera
za nikogo połączenia, gdy żywych jest kilka. Każda z tych rzeczy jest decyzją.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import UUID

#: CO ZAŁOŻYCIEL MA KLIKNĄĆ, słowo w słowo tak, jak to stoi na ekranie.
#:
#: DLACZEGO TO JEST W KODZIE, A NIE W DOKUMENTACJI. Odmowa, która mówi „brak delegacji
#: RESEARCH", zostawia człowieka przed pytaniem „to gdzie ja mam ją podpisać". Zmierzone na
#: live 19.09: `make p4-live-check` stanął dokładnie na tym i nie ruszył, bo nikt nie wiedział,
#: co zrobić dalej. Nazwy pochodzą z `apps/dashboard/.../presence/page.tsx`; gdy się zmienią na
#: ekranie, ta lista kłamie, i dlatego pilnuje jej test, który czyta oba pliki.
HOW_TO_SIGN = """Co zrobić, żeby ten pomiar mógł ruszyć (jedna minuta w studiu):

  1. Otwórz projekt w studiu i kliknij odnośnik „Obecność”.
  2. W sekcji „Powierz projekt agentowi” w polu „Tryb” wybierz
     „RESEARCH: tylko czyta i zapamiętuje”.
  3. W „Konta, z których wolno pisać” zaznacz konto Reddita; w RESEARCH nic nim nie napisze,
     ale tura ma wiedzieć, czyimi oczami patrzy.
  4. W „Społeczności, po przecinku” wpisz obserwowaną społeczność, na przykład r/kubernetes.
  5. W „Działania” zaznacz „RESEARCH: czytanie i zapamiętywanie”. Nie zaznaczaj REPLY ani
     OWN_POST: ten pomiar ma nic nie opublikować i tryb pilnuje tego sam.
  6. Limity zostaw puste, a „Tury modelu na dobę” ustaw na przykład na 20.
  7. Kliknij „Podpisuję powierzenie”.

Potem uruchom to polecenie jeszcze raz. Ono samo NICZEGO NIE PODPISZE: zgoda jest tym, co
odróżnia agenta działającego w czyimś imieniu od skryptu, więc podpis zostaje przy człowieku."""


def _print(section: str, body: str = "") -> None:
    print(f"== {section} ==" if not body else f"== {section} ==\n{body}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    from control_plane.observe.wiring import build_presence_handlers
    from control_plane.presence.scope import MODE_RESEARCH
    from control_plane.presence.store import PresenceStore
    from control_plane.sessions.store import SessionStore

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        _print("NIE_URUCHOMIONO", "brak DATABASE_URL; to polecenie biegnie w kontenerze api")
        return 3

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            organization_id = (
                UUID(args.organization)
                if args.organization
                else await conn.fetchval("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
            )
            if organization_id is None:
                _print("NIE_URUCHOMIONO", "baza nie ma ani jednej organizacji")
                return 3

            presence = PresenceStore(pool)
            if args.project:
                project_id = UUID(args.project)
            else:
                delegated = await presence.delegated_projects(organization_id=organization_id)
                research = [one for one in delegated if one.mode == MODE_RESEARCH and one.live]
                if len(research) != 1:
                    detail = (
                        f"żywych projektów w trybie RESEARCH: {len(research)}. Podaj --project: "
                        "wybór, na którym projekcie i czyim koncie biegnie tura, jest decyzją, "
                        "a domyślna wartość podjęłaby ją po cichu"
                    )
                    # ZERO to inny problem niż DWA i dostaje inną odpowiedź: przy zerze nie ma
                    # czego wskazywać `--project`, więc jedyne sensowne zdanie to instrukcja,
                    # jak tę delegację podpisać.
                    if not research:
                        detail = (
                            "w tej organizacji nie ma ani jednego projektu z żywą delegacją "
                            f"RESEARCH.\n\n{HOW_TO_SIGN}"
                        )
                    _print("NIE_URUCHOMIONO", detail)
                    return 3
                project_id = research[0].project_id

            delegation = await presence.delegation_for(
                organization_id=organization_id, project_id=project_id
            )
            if delegation is None or not delegation.live:
                _print(
                    "NIE_URUCHOMIONO",
                    f"projekt {project_id} nie ma żywej delegacji; nie ma czego wykonywać.\n\n"
                    f"{HOW_TO_SIGN}",
                )
                return 3
            if delegation.mode != MODE_RESEARCH:
                # Surowo, bo to jest pomiar, a nie próba: tik w AUTONOMOUS mógłby przygotować
                # tekst do publikacji, a tego to polecenie nie ma prawa wywołać.
                _print(
                    "NIE_URUCHOMIONO",
                    f"delegacja jest w trybie {delegation.mode}, a ten pomiar biegnie wyłącznie "
                    "w RESEARCH. Odwołaj ją albo wskaż inny projekt przez --project.\n\n"
                    "Odwołanie jest na tym samym ekranie „Obecność”, przycisk „Odwołaj "
                    "powierzenie” pod blokiem „Powierzenie”; potem podpisz nowe:\n\n"
                    f"{HOW_TO_SIGN}",
                )
                return 3

            wakeups_before = await presence.wakeups_for(
                organization_id=organization_id, project_id=project_id, limit=1
            )

        _print(
            "tik obecności",
            f"organizacja {organization_id}\nprojekt      {project_id}\n"
            f"tryb         {delegation.mode}\n"
            f"społeczności {', '.join(delegation.scope.communities) or 'żadne'}",
        )

        handlers = build_presence_handlers(pool, organization_id=organization_id)
        started = datetime.now(UTC)
        result = await handlers.run_once(project_id, started)
        took = (datetime.now(UTC) - started).total_seconds()

        _print("wynik tiku", f"{result.outcome}: {result.detail}\nczas: {took:.1f} s")

        if result.wakeup_id is None:
            _print(
                "NIE ZALICZONE",
                "tik nie zapisał nawet obudzenia. To nie jest cisza, to jest brak pomiaru",
            )
            return 1

        wakeups = await presence.wakeups_for(
            organization_id=organization_id, project_id=project_id, limit=3
        )
        newest = wakeups[0]
        _print(
            "obudzenie",
            json.dumps(
                {
                    "powod": newest.reason,
                    "sesja": str(newest.session_id) if newest.session_id else None,
                    "pominiete": newest.skipped_reason,
                    "szczegol": newest.skipped_detail,
                    "koszt": newest.cost,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

        if newest.session_id is None:
            reason = newest.skipped_detail or newest.skipped_reason or "bez powodu"
            if not wakeups_before:
                _print(
                    "NIE_URUCHOMIONO",
                    f"model nie ruszył: {reason}. Jeśli to „brak nowych zdarzeń”, zasil bazę "
                    "obserwacją i powtórz: ten pomiar wymaga prawdziwego zdarzenia",
                )
                return 3
            _print("NIE ZALICZONE", f"model nie ruszył: {reason}")
            return 1

        sessions = SessionStore(pool)
        events = await sessions.events(
            organization_id=organization_id, session_id=newest.session_id, limit=500
        )
        tools = [one.payload.get("tool") for one in events if one.kind == "TOOL_CALL"]
        _print("narzędzia zawołane", json.dumps(tools, ensure_ascii=False) or "[]")

        session = await sessions.get(organization_id=organization_id, session_id=newest.session_id)
        if session is not None and session.result_summary:
            _print("odpowiedź", session.result_summary[:2000])

        async with pool.acquire() as conn:
            proposals = await conn.fetchval(
                """
                SELECT count(*) FROM action_proposal_revisions r
                  JOIN action_proposals p ON p.id = r.proposal_id
                  JOIN project_account_assignments x
                    ON x.social_account_id = p.social_account_id AND x.project_id = $2
                 WHERE r.organization_id = $1 AND r.created_at >= $3
                """,
                organization_id,
                project_id,
                started,
            )
            requests = await conn.fetchval(
                "SELECT count(*) FROM publication_requests WHERE project_id = $1 "
                "AND queued_at >= $2",
                project_id,
                started,
            )

        if not tools:
            _print(
                "NIE ZALICZONE",
                "model odpowiedział, ale nie zawołał ANI JEDNEGO narzędzia. Agent, który "
                "opowiada o zdarzeniu zamiast je przeczytać, zgaduje",
            )
            return 1
        if requests:
            _print("NIE ZALICZONE", f"powstało {requests} wniosków publikacji w trybie RESEARCH")
            return 1
        _print(
            "ZALICZONE",
            f"tik obudził sesję na prawdziwym zdarzeniu, zawołał {len(tools)} narzędzi "
            f"({', '.join(str(one) for one in tools[:5])}) i skończył bez publikacji "
            f"(propozycje: {proposals}, wnioski: {requests}).",
        )
        return 0
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Jeden tik obecności na prawdziwym Codeksie, w trybie RESEARCH"
    )
    parser.add_argument("--organization", default=None, help="UUID organizacji.")
    parser.add_argument("--project", default=None, help="UUID projektu w trybie RESEARCH.")
    parser.add_argument("--minutes", type=int, default=10, help="Budżet czasu jednej tury.")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _print("PRZERWANE", "tik przerwany ręką; sesja zostaje w bazie z tym, co zdążyła")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
