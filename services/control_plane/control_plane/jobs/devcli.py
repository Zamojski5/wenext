"""The development path that fills the queue — D-013, and NOT a producer of production data.

    "Dashboard w F1 pokazuje prawdziwe wiersze z prawdziwej bazy, wstawiane przez ścieżkę
     deweloperską — ta ścieżka i tak musi istnieć, bo bez niej nie da się udowodnić DoD
     „restart komponentu nie gubi taska" (L5212). NIE JEST ONA JEDNAK CELEM FAZY I NIE WOLNO JEJ
     NAZYWAĆ DANYMI PRODUKCYJNYMI." (D-013)

Read that sentence before using this module for anything. E1.F1 has no producer of real work:
candidates arrive in F3 (Opportunity Inbox, L5277) and `ActionProposal` in F4 (L5299). A queue
screen filled from here is a TEST BENCH — the rows are real rows in the real database, which is
the point, and they mean nothing about the world. The first genuine data on that screen comes
from the headless read of F2.

Two properties this CLI has on purpose:

*   **It goes through `domain.commands`**, like the API and like everything else. A developer
    tool with its own INSERT would be the one writer that never learns about a new invariant.
*   **`--attempt-limit` is REQUIRED and has no default.** D-017 freezes the value 5 and keeps it
    in one configuration module; a default here would be a second copy of that threshold, and a
    threshold in two places drifts silently. Migration 0003 makes the same choice — the column is
    `NOT NULL` with no `DEFAULT`.

Usage:

    DATABASE_URL=postgresql://… python -m control_plane.jobs.devcli enqueue \\
        --job-type discovery.scan --routing-key planning.daily --attempt-limit 5

Operator-facing output is Polish (D-010).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID, uuid4

import asyncpg

from control_plane.domain import commands
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

#: Spelled exactly as `docker-compose.yml` sets it for the `api` service, and exactly as
#: `control_plane.auth` reads it. A second name would mean the container works and the CLI does
#: not, or the other way round.
DSN_VARIABLE = "DATABASE_URL"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_plane.jobs.devcli",
        description=(
            "Ścieżka DEWELOPERSKA kolejki jobów (D-013). Wstawia prawdziwe wiersze do "
            "prawdziwej bazy i nie są to dane produkcyjne."
        ),
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=f"DSN PostgreSQL; domyślnie zmienna {DSN_VARIABLE}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    enqueue = subcommands.add_parser(
        "enqueue", help="wstaw jednego joba do kolejki przez komendę domenową"
    )
    enqueue.add_argument("--job-type", required=True)
    enqueue.add_argument("--routing-key", required=True)
    enqueue.add_argument(
        "--attempt-limit",
        type=int,
        required=True,
        help=(
            "limit prób. WYMAGANY i bez domyślnej wartości: D-017 zamraża 5 i trzyma tę liczbę "
            "w jednym module konfiguracji, a próg zapisany w dwóch miejscach się rozjeżdża"
        ),
    )
    enqueue.add_argument("--priority", type=int, default=100)
    enqueue.add_argument(
        "--organization",
        default=None,
        help="UUID organizacji; pominięty oznacza „jedyna, jaka jest” — patrz _sole_organization",
    )
    enqueue.add_argument("--workspace", default=None, help="UUID workspace'u; opcjonalny")
    enqueue.add_argument("--payload-ref", default=None)
    enqueue.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="odroczenie liczone PRZEZ BAZĘ jako now() + tyle sekund",
    )
    enqueue.add_argument(
        "--idempotency-key",
        default=None,
        help="pominięty oznacza świeży losowy klucz, czyli nowy job za każdym uruchomieniem",
    )
    return parser


async def _sole_organization(pool: asyncpg.Pool) -> UUID:
    """The single organization of this installation, or a refusal.

    E1 is one organization, one workspace, one operator (D-016), so "the only one there is" is a
    well-defined thing to mean. Zero rows and several rows are both errors and both say which
    one happened: guessing would put the job in a tenant nobody chose, and that is a mistake
    nobody would notice.
    """
    rows = await pool.fetch("SELECT id, name FROM organizations ORDER BY created_at, id")
    if len(rows) == 1:
        return rows[0]["id"]
    if not rows:
        raise SystemExit(
            "W bazie nie ma ANI JEDNEJ organizacji. Job nie ma gdzie powstać — załóż "
            "organizację i workspace, albo podaj --organization."
        )
    listed = ", ".join(f"{row['id']} ({row['name']})" for row in rows)
    raise SystemExit(
        f"W bazie jest {len(rows)} organizacji: {listed}. Podaj --organization — zgadywanie "
        "wstawiłoby joba do obcego tenanta i nikt by tego nie zauważył."
    )


async def _enqueue(arguments: argparse.Namespace, dsn: str) -> int:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        organization_id = (
            UUID(arguments.organization)
            if arguments.organization
            else await _sole_organization(pool)
        )
        workspace_id = UUID(arguments.workspace) if arguments.workspace else None
        port = PostgresJobTransport(JobStore(pool), organization_id=organization_id)
        enqueued = await commands.enqueue_job(
            port,
            organization_id=organization_id,
            workspace_id=workspace_id,
            job_type=arguments.job_type,
            routing_key=arguments.routing_key,
            priority=arguments.priority,
            attempt_limit=arguments.attempt_limit,
            idempotency_key=arguments.idempotency_key or f"devcli-{uuid4()}",
            payload_ref=arguments.payload_ref,
            delay_seconds=arguments.delay_seconds,
        )
    finally:
        await pool.close()

    if enqueued.created:
        print(f"Wstawiono joba {enqueued.job_id} (ścieżka deweloperska, D-013).")
        return 0
    # Not a success dressed up as one: nothing was added, and the caller is told so.
    print(
        f"NIC NIE WSTAWIONO — job o tym kluczu idempotencji już istnieje: {enqueued.job_id}. "
        "To deduplikacja wewnętrzna (L2820), nie nowy job.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    dsn = arguments.dsn or os.environ.get(DSN_VARIABLE)
    if not dsn:
        raise SystemExit(
            f"Brak DSN. Ustaw {DSN_VARIABLE} albo podaj --dsn. Bez połączenia z bazą ta komenda "
            "nie ma czego zapełnić — a kolejka jest w bazie, nie w pamięci procesu."
        )
    if arguments.command == "enqueue":
        return asyncio.run(_enqueue(arguments, dsn))
    raise SystemExit(f"nieznana podkomenda: {arguments.command}")


if __name__ == "__main__":  # pragma: no cover — entry point
    raise SystemExit(main())
