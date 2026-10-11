"""Rozmowa Codex-first na PRAWDZIWYM silniku, z dowodami (D-046).

    make codex-first-live-check
    make codex-first-live-check CF_ARGS="--minutes 15 --goal 'inny cel'"

CZEGO TO DOTYKA, CZEGO NIE DOTYKA `p2-live-check`. Tamten sprawdza, że tura w ogóle biegnie na
żywym koncie i woła narzędzia. Ten sprawdza to, co D-046 nazywa produktem: czy z jednej prośby
człowieka powstają KARTY, czy postęp umie nazwać etapy, czy konfiguracja runtime'u rozmowy jest
tą, o którą prosiliśmy, i czy przy całej tej pracy nie wyszła na zewnątrz ani jedna publikacja.

CZTERY RZECZY, KTÓRYCH TEN CEL NIE ROBI, i każda jest decyzją, nie brakiem:

  * NIE PUBLIKUJE i nie zatwierdza. Liczy wnioski publikacyjne przed turą i po turze; różnica
    inna niż zero jest PORAŻKĄ pomiaru, a nie ciekawostką w logu.
  * NIE PODPISUJE POWIERZENIA. Podpis jest aktem człowieka i cel, który podpisuje sam sobie
    zakres, mierzyłby produkt, którego nie ma.
  * NIE ZAKŁADA organizacji ani projektu. Pracuje na tych, które istnieją, bo pomiar, który
    zapełnia bazę, przestaje o niej cokolwiek mówić.
  * NIE OCENIA JAKOŚCI odpowiedzi. Mówi, co się wydarzyło; czy było dobre, rozstrzyga człowiek.

LIMIT KONTA NIE JEST PORAŻKĄ. Gdy tura kończy się czekaniem na odnowienie limitu, cel wypisuje
stan WAITING_EVENT i godzinę powrotu, i kończy się kodem 2: to jest „nie zmierzono”, a nie
„zmierzono źle”. Mylenie tych dwóch rzeczy jest dokładnie tym, przez co ktoś rano czyta czerwony
wynik jako awarię produktu.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

DEFAULT_ORGANIZATION = "Wenext E2E"
DEFAULT_PROJECT = "Obecność na Reddicie"
#: The task handed to the agent. The default names no person; to point the check at a particular
#: profile, pass `--goal` (`make codex-first-live-check CF_GOAL='...'`).
DEFAULT_GOAL = (
    "Sprawdź, gdzie warto promować ten projekt. Wykorzystaj konto operatora i zacznij od "
    "sensownych rozmów."
)


def _print(section: str, body: str = "") -> None:
    print(f"== {section} ==" if not body else f"== {section} ==\n{body}", flush=True)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _publications(conn: Any, organization_id: UUID) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE organization_id = $1",
            organization_id,
        )
    )


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    from control_plane.observe.wiring import build_session_handlers
    from control_plane.sessions.projection import cards_for, progress_for
    from control_plane.sessions.runtime_profile import profile_for
    from control_plane.sessions.store import SESSION_CONVERSATION, SessionStore

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print(
            "BŁĄD: brak DATABASE_URL. Ten cel biegnie W KONTENERZE api, bo tylko tam jest "
            "zalogowany CODEX_HOME i baza naraz.",
            file=sys.stderr,
        )
        return 3

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            organization = await conn.fetchrow(
                "SELECT id, name FROM organizations "
                "WHERE ($1::uuid IS NULL OR id = $1) AND ($2::text IS NULL OR name = $2) "
                "ORDER BY created_at LIMIT 1",
                UUID(args.organization) if _is_uuid(args.organization) else None,
                None if _is_uuid(args.organization) else args.organization,
            )
            if organization is None:
                print(
                    f"BŁĄD: nie ma organizacji {args.organization!r}. Wskaż inną przez "
                    "--organization; ten cel nie zakłada organizacji.",
                    file=sys.stderr,
                )
                return 3
            organization_id = organization["id"]

            project = await conn.fetchrow(
                "SELECT id, workspace_id, name, goal, paused_at FROM projects "
                "WHERE organization_id = $1 AND archived_at IS NULL "
                "AND ($2::uuid IS NULL OR id = $2) AND ($3::text IS NULL OR name = $3) "
                "ORDER BY created_at LIMIT 1",
                organization_id,
                UUID(args.project) if _is_uuid(args.project) else None,
                None if _is_uuid(args.project) else args.project,
            )
            if project is None:
                print(
                    f"BŁĄD: w tej organizacji nie ma projektu {args.project!r}. Załóż go w "
                    "studiu; ten cel świadomie NIE tworzy projektów.",
                    file=sys.stderr,
                )
                return 3
            if project["paused_at"] is not None:
                print(f"BŁĄD: projekt {project['name']} jest wstrzymany.", file=sys.stderr)
                return 3

            connections = await conn.fetch(
                "SELECT id, label FROM codex_connections "
                "WHERE organization_id = $1 AND disconnected_at IS NULL ORDER BY created_at",
                organization_id,
            )
            before = await _publications(conn, organization_id)

        if not connections:
            print(
                "BŁĄD: nie ma żywego połączenia Codex. Rozmowa nie ma na czym ruszyć.",
                file=sys.stderr,
            )
            return 3
        if len(connections) > 1 and args.connection is None:
            listing = ", ".join(f"{row['id']} ({row['label']})" for row in connections)
            print(
                f"BŁĄD: żywych połączeń jest {len(connections)}: {listing}. Wskaż jedno przez "
                "--connection; na czyim limicie biegnie tura, to decyzja.",
                file=sys.stderr,
            )
            return 3
        connection_id = UUID(args.connection) if args.connection else connections[0]["id"]

        profile = profile_for(SESSION_CONVERSATION)
        _print(
            "wejście",
            _json(
                {
                    "organizacja": organization["name"],
                    "projekt": project["name"],
                    "połączenie": str(connection_id),
                    "cel": args.goal,
                    "profil_runtime": {"web_search": profile.web_search},
                    "wnioski_publikacyjne_przed": before,
                }
            ),
        )

        store = SessionStore(pool)
        session = await store.create(
            organization_id=organization_id,
            workspace_id=project["workspace_id"],
            project_id=project["id"],
            goal=args.goal,
            budget={"max_minutes": args.minutes, "max_tool_calls": args.tool_calls},
            codex_connection_id=connection_id,
            kind=SESSION_CONVERSATION,
        )
        _print("rozmowa", str(session.id))

        handlers = build_session_handlers(pool, organization_id=organization_id)
        started = datetime.now(UTC)
        result = await handlers.run_once(session.id)
        elapsed = (datetime.now(UTC) - started).total_seconds()

        events = list(
            await store.events(organization_id=organization_id, session_id=session.id, limit=500)
        )
        fresh = await store.get(organization_id=organization_id, session_id=session.id)
        cards = await cards_for(
            pool,
            events,
            organization_id=organization_id,
            workspace_id=project["workspace_id"],
            project_id=project["id"],
        )
        progress = progress_for(events)
        async with pool.acquire() as conn:
            after = await _publications(conn, organization_id)

        tools = [str(e.payload.get("tool")) for e in events if e.kind == "TOOL_CALL" and e.payload]
        answers = [e.payload.get("text") for e in events if e.kind == "ASSISTANT_MESSAGE"]
        problems = [e.payload.get("detail") for e in events if e.kind == "ERROR"]
        mismatches = [
            e.payload.get("niezgodnosci") or e.payload.get("detail")
            for e in events
            if e.kind == "STATUS" and (e.payload or {}).get("niezgodnosci")
        ]
        waiting = _waiting_for_limit(events, fresh)

        _print(
            "wynik",
            _json(
                {
                    "wynik_tury": result.outcome,
                    "status_rozmowy": fresh.status if fresh else "?",
                    "sekund": round(elapsed, 1),
                    "zdarzen": len(events),
                    "narzedzia": tools,
                    "karty": [
                        {"rodzaj": one.get("rodzaj"), "stan": one.get("stan")} for one in cards
                    ],
                    "postep": None
                    if progress is None
                    else {"naglowek": progress["naglowek"], "licznik": progress["licznik"]},
                    "niezgodnosci_konfiguracji": mismatches,
                    "wnioski_publikacyjne_po": after,
                    "opublikowano": after - before,
                    "bledy": problems,
                }
            ),
        )
        if answers:
            _print("odpowiedź modelu", str(answers[-1])[:2000])

        _print(
            "czego ten pomiar NIE dowodzi",
            "- nie ocenia jakości odpowiedzi ani trafności wyboru społeczności;\n"
            "- nie sprawdza wyglądu kart na ekranie, tylko ich obecność i stan w projekcji;\n"
            "- jedna rozmowa nie jest pomiarem dostępności usługi.",
        )

        if after != before:
            print(
                f"NIE ZALICZONE: liczba wniosków publikacyjnych zmieniła się o {after - before}. "
                "Ten pomiar nie ma prawa niczego wysłać.",
                file=sys.stderr,
            )
            return 1
        if waiting is not None:
            _print("limit konta", _json(waiting))
            print(
                "NIE ZMIERZONO: rozmowa czeka na odnowienie limitu konta Codex "
                f"({waiting.get('wraca') or 'termin nieznany'}). To nie jest porażka produktu.",
                file=sys.stderr,
            )
            return 2
        if result.outcome != "ANSWERED":
            print(f"NIE ZALICZONE: tura skończyła się jako {result.outcome}.", file=sys.stderr)
            return 1
        if not tools:
            print(
                "NIE ZALICZONE: model odpowiedział, ale nie zawołał ANI JEDNEGO narzędzia. "
                "Rozmowa bez narzędzi to zwykła tura modelu, a nie agent w produkcie.",
                file=sys.stderr,
            )
            return 1
        if mismatches:
            print(
                "NIE ZALICZONE: konfiguracja efektywna silnika różni się od tej, o którą "
                "poprosiła rozmowa. Szczegóły w sekcji wyniku.",
                file=sys.stderr,
            )
            return 1
        if not cards:
            print(
                "NIE ZALICZONE: nie powstała ANI JEDNA karta. Rozmowa Codex-first bez kart to "
                "sam czat: człowiek nie ma czego kliknąć.",
                file=sys.stderr,
            )
            return 1
        print(
            "ZALICZONE: rozmowa wykonana, karty powstały, konfiguracja zgodna, "
            "nic nie opublikowano.",
            flush=True,
        )
        return 0
    finally:
        await pool.close()


def _waiting_for_limit(events: list[Any], fresh: Any) -> dict[str, Any] | None:
    """Czy rozmowa stoi na limicie konta, i od kiedy wraca.

    Czytane ze śladu, a nie ze statusu samego: status mówi WAITING_EVENT także wtedy, gdy sesja
    czeka na coś innego, a godzina powrotu jest jedyną rzeczą, którą człowiek może zaplanować.
    """
    if fresh is None or getattr(fresh, "status", None) != "WAITING_EVENT":
        return None
    from control_plane.sessions.handlers import REASON_ACCOUNT_LIMIT

    for event in reversed(events):
        payload = event.payload or {}
        if event.kind == "STATUS" and payload.get("powod") == REASON_ACCOUNT_LIMIT:
            return {
                "wraca": payload.get("odnowi_sie"),
                "skad": payload.get("skad"),
                "zdanie": payload.get("detail"),
                "surowy_komunikat": str(payload.get("surowy_komunikat") or "")[:300],
            }
    return {"wraca": None, "skad": None, "zdanie": None, "surowy_komunikat": ""}


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex-first-live-check",
        description=(
            "Jedna rozmowa Codex-first na PRAWDZIWYM silniku, z kartami i postępem jako "
            "dowodem. Nic nie publikuje i nie podpisuje."
        ),
    )
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Cel powierzony agentowi.")
    parser.add_argument(
        "--organization", default=DEFAULT_ORGANIZATION, help="Nazwa albo UUID organizacji."
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Nazwa albo UUID projektu.")
    parser.add_argument("--connection", default=None, help="UUID połączenia Codex.")
    parser.add_argument("--minutes", type=int, default=15, help="Budżet czasu jednej tury.")
    parser.add_argument("--tool-calls", type=int, default=60, help="Budżet wywołań narzędzi.")
    args = parser.parse_args(argv)
    if args.minutes < 1:
        parser.error("--minutes musi być dodatnie")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
