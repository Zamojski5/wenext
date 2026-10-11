"""One session turn against the REAL Codex, run by hand, with its evidence printed (P2).

    make p2-live-check
    make p2-live-check P2_ARGS="--goal 'sprawdź, dlaczego odzew jest mały' --minutes 10"

WHAT THIS IS FOR. Everything else about P2 is measured against a stand-in app-server: the
session, the queue, the client, the tool server and the database all run for real, and the only
thing scripted is which tools the model calls. That proves the mechanics and proves nothing
about a model. This command is the other half, and it can only be run where a logged-in Codex
lives: the `api` container, on the `codex-home` volume.

IT IS DELIBERATELY NOT A TEST. It writes a session, spends a person's quota and depends on an
account, so it is a command somebody decides to run, never something a suite triggers. It prints
what happened and exits non-zero when the turn did not produce an answer, so the result is
readable rather than argued about.

WHAT IT REFUSES TO DO:

  * it does not publish. The only tool that proposes an action is `draft.create`, which ends in
    the consent path with nothing sent, and this command does not approve anything;
  * it does not pick a connection for you when several are live. Which quota and which identity
    a turn runs on is a decision, and a default would make it silently;
  * it does not create a workspace or a project. It works on ones that already exist, so a live
    check cannot quietly populate the database it is meant to observe.

WHAT IT CANNOT TELL YOU: whether the answer was any good. It reports that a turn ran, which
tools were really called, and what came back. Judgment is a separate measurement with its own
criteria, and calling this one a quality check would be the overclaim this repository keeps
paying for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

DEFAULT_GOAL = (
    "Przedstaw się w jednym zdaniu, wypisz dostępne narzędzia i powiedz, czego w tym "
    "projekcie brakuje, żebyś mógł ruszyć z celem. Nic nie publikuj."
)


def _print(section: str, body: str = "") -> None:
    print(f"== {section} ==" if not body else f"== {section} ==\n{body}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    from control_plane.observe.wiring import build_session_handlers
    from control_plane.sessions.store import SessionStore

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
            organizations = await conn.fetch(
                "SELECT id, name FROM organizations ORDER BY created_at"
            )
            if len(organizations) != 1 and args.organization is None:
                print(
                    f"BŁĄD: organizacji jest {len(organizations)}; wskaż jedną przez "
                    "--organization. Zgadywanie tenanta jest operacją jednokierunkową.",
                    file=sys.stderr,
                )
                return 3
            organization_id = (
                UUID(args.organization) if args.organization else organizations[0]["id"]
            )

            project = await conn.fetchrow(
                "SELECT p.id, p.workspace_id, p.name, p.goal, p.paused_at FROM projects p "
                "WHERE p.organization_id = $1 AND ($2::uuid IS NULL OR p.id = $2) "
                "AND p.archived_at IS NULL ORDER BY p.created_at LIMIT 1",
                organization_id,
                UUID(args.project) if args.project else None,
            )
            if project is None:
                print(
                    "BŁĄD: nie ma projektu do sprawdzenia. Załóż go w studiu; ten cel świadomie "
                    "NIE tworzy projektów, żeby nie zapełniać bazy, którą ma obserwować.",
                    file=sys.stderr,
                )
                return 3
            if project["paused_at"] is not None:
                print(
                    f"BŁĄD: projekt {project['name']} jest wstrzymany. Wznów go albo wskaż inny.",
                    file=sys.stderr,
                )
                return 3

            connections = await conn.fetch(
                "SELECT id, label, status, home_ref FROM codex_connections "
                "WHERE organization_id = $1 AND disconnected_at IS NULL ORDER BY created_at",
                organization_id,
            )
        if not connections:
            print(
                "BŁĄD: nie ma żadnego żywego połączenia Codex. Sesja nie ma na czym ruszyć.",
                file=sys.stderr,
            )
            return 3
        if len(connections) > 1 and args.connection is None:
            listing = ", ".join(f"{row['id']} ({row['label']})" for row in connections)
            print(
                f"BŁĄD: żywych połączeń jest {len(connections)}: {listing}. Wskaż jedno przez "
                "--connection. Na którym koncie i z czyjego limitu biegnie tura, to decyzja.",
                file=sys.stderr,
            )
            return 3
        connection_id = UUID(args.connection) if args.connection else connections[0]["id"]

        _print(
            "wejście",
            json.dumps(
                {
                    "organizacja": str(organization_id),
                    "projekt": {"id": str(project["id"]), "nazwa": project["name"]},
                    "połączenie": str(connection_id),
                    "cel": args.goal,
                    "budżet_minut": args.minutes,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

        store = SessionStore(pool)
        session = await store.create(
            organization_id=organization_id,
            workspace_id=project["workspace_id"],
            project_id=project["id"],
            goal=args.goal,
            budget={"max_turns": 2, "max_minutes": args.minutes, "max_tool_calls": args.tool_calls},
            codex_connection_id=connection_id,
        )
        _print("sesja", str(session.id))

        handlers = build_session_handlers(pool, organization_id=organization_id)
        started = datetime.now(UTC)
        result = await handlers.run_once(session.id)
        elapsed = (datetime.now(UTC) - started).total_seconds()

        events = await store.events(
            organization_id=organization_id, session_id=session.id, limit=500
        )
        fresh = await store.get(organization_id=organization_id, session_id=session.id)

        tools = [
            {"tool": e.payload.get("tool"), "arguments": e.payload.get("arguments")}
            for e in events
            if e.kind == "TOOL_CALL"
        ]
        answers = [e.payload.get("text") for e in events if e.kind == "ASSISTANT_MESSAGE"]
        problems = [e.payload.get("detail") for e in events if e.kind == "ERROR"]

        _print(
            "wynik",
            json.dumps(
                {
                    "wynik_tury": result.outcome,
                    "szczegol": result.detail[:400],
                    "status_sesji": fresh.status if fresh else "?",
                    "watek_silnika": fresh.engine_thread_id if fresh else None,
                    "sekund": round(elapsed, 1),
                    "zdarzen": len(events),
                    "wywolan_narzedzi": len(tools),
                    "narzedzia": tools,
                    "bledy": problems,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        if answers:
            _print("odpowiedź modelu", str(answers[-1])[:2000])

        _print(
            "czego ten pomiar NIE dowodzi",
            "- nie ocenia jakości odpowiedzi ani trafności wyboru narzędzi;\n"
            "- nic nie opublikował i nie mógł: draft.create kończy się propozycją w ścieżce "
            "zgody;\n"
            "- jedna tura na jednym projekcie nie jest pomiarem dostępności usługi.",
        )

        if result.outcome != "ANSWERED":
            print(
                f"NIE ZALICZONE: tura skończyła się jako {result.outcome}. Powód powyżej.",
                file=sys.stderr,
            )
            return 1
        if not tools:
            # An answer with no tool call means the model never reached the product. The turn
            # technically worked and the thing this check exists to prove did not happen.
            print(
                "NIE ZALICZONE: model odpowiedział, ale nie zawołał ANI JEDNEGO narzędzia. "
                "Sesja bez narzędzi to zwykła tura modelu, a nie agent w produkcie.",
                file=sys.stderr,
            )
            return 1
        print("ZALICZONE: tura wykonana, narzędzia zawołane, nic nie opublikowano.", flush=True)
        return 0
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="p2-live-check",
        description="Jedna tura sesji na PRAWDZIWYM Codeksie, z dowodami. Nic nie publikuje.",
    )
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Cel powierzony agentowi.")
    parser.add_argument("--organization", default=None, help="UUID organizacji.")
    parser.add_argument("--project", default=None, help="UUID projektu.")
    parser.add_argument("--connection", default=None, help="UUID połączenia Codex.")
    parser.add_argument("--minutes", type=int, default=10, help="Budżet czasu jednej tury.")
    parser.add_argument("--tool-calls", type=int, default=30, help="Budżet wywołań narzędzi.")
    args = parser.parse_args(argv)
    if args.minutes < 1:
        parser.error("--minutes musi być dodatnie")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
