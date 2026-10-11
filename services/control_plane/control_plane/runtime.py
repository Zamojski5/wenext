"""Background work owned by the control plane process: the dispatcher and the lease reaper.

Both functions here are called by the application's lifespan (`control_plane.app`), and that call
was written in package P1 while both bodies were still empty — deliberately, because a reaper that
passes its own unit tests, ships in the image and never ticks inside the container is this
project's recurring failure shape, and it costs a Docker rebuild cycle to notice.
`tests/test_lifespan.py` pins both halves of the wiring, `app.py` calls them THROUGH this module
so that a later implementation and a monkeypatch both land, and this file now fills them in.

E1.F1 puts the dispatcher in the same process as the API (L5193) and needs a reaper for expired
job leases (L5194, D-017: a 10 s loop against a 60 s TTL).

FOUR DECISIONS TAKEN HERE, each because the obvious version fails in a specific way:

1.  **A missing DSN starts nothing and says so at ERROR level, and does NOT raise.** `/healthz`
    must keep answering when the database is unreachable — `app.py` says why — and an exception
    out of the lifespan takes the whole process down. The refusal is readable through
    `background_state()`, so "the queue is not running" is a fact a caller can assert on rather
    than a line somebody has to spot in the log.

2.  **A database that is not up YET is retried, for ever.** A container that starts one second
    before PostgreSQL answers would otherwise serve HTTP with a dead queue until somebody
    restarted it, and nothing on the operator's screen would say so. This is the difference
    between "misconfigured" (point 1: retrying cannot help) and "not ready yet".

3.  **The organization is resolved, never guessed.** E1 is one organization, one workspace, one
    operator (D-016), so "the only one there is" is well defined; zero or several is a refusal.
    `jobs/devcli.py::_sole_organization` makes the same check and raises `SystemExit`, which is
    right for a CLI and would kill the API here — the handling differs on purpose, and the query
    is three lines rather than an import of a private name from a dev tool.

4.  **The handler map comes from `control_plane.observe.wiring`.** Until 2026-09-03 it was empty
    on purpose (D-013: F1 had no producer of real work). Outcome A is that producer: four handlers
    that observe a real community, draw its picture and judge its threads. A job type outside the
    map is still parked in `WAITING_CAPABILITY`, never "pretended succeeded" (`jobs/dispatcher.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import asyncpg

from control_plane.auth import DSN_VARIABLE
from control_plane.demo import DEMO_ORGANIZATION_IDS
from control_plane.jobs.backoff import REAPER_INTERVAL_SECONDS
from control_plane.jobs.dispatcher import JobDispatcher
from control_plane.jobs.lanes import ENGINE_JOB_TYPES, ENGINE_LANE, IO_LANE
from control_plane.jobs.reaper import JobLeaseReaper
from control_plane.knowledge.handlers import seed_experience_chain
from control_plane.observe.wiring import build_handlers
from control_plane.ops import seed_ops_chain
from control_plane.presence.handlers import seed_presence_chain
from control_plane.sessions.recovery import replan_impossible_returns

_LOG = logging.getLogger(__name__)

#: How long to wait before trying the wiring again when the database has not answered yet, or
#: when the single organization does not exist yet. Reuses the reaper's loop interval rather than
#: inventing a sixth number; it is NOT a D-017 threshold and changing it amends no decision.
WIRING_RETRY_SECONDS = REAPER_INTERVAL_SECONDS


class BackgroundPhase(StrEnum):
    """What the background work of this process is doing. Readable without reading the log."""

    #: `start_background()` has not run, or `stop_background()` has finished.
    STOPPED = "STOPPED"
    #: No DSN in the environment. A configuration fault; retrying cannot fix it.
    NO_DSN = "NO_DSN"
    #: Wired and ticking: the dispatcher and the reaper are both running.
    RUNNING = "RUNNING"
    #: The supervisor is up but the database or the single organization is not there yet.
    WAITING_FOR_DATABASE = "WAITING_FOR_DATABASE"


@dataclass(frozen=True)
class BackgroundState:
    """A snapshot of what is running. Frozen, so a caller cannot edit the answer it was given."""

    phase: BackgroundPhase
    #: One sentence for the operator when the phase is not `RUNNING` (D-010).
    detail: str | None = None
    organization_id: UUID | None = None


#: Module-level, because `start_background()` takes no arguments — the lifespan calls it that way
#: and `tests/test_lifespan.py` pins the signature. One control plane process, one dispatcher, one
#: reaper, so a single set of module state is the honest shape rather than a hidden singleton.
_SUPERVISOR: asyncio.Task[None] | None = None
_POOL: asyncpg.Pool | None = None
_STATE = BackgroundState(phase=BackgroundPhase.STOPPED)


def background_state() -> BackgroundState:
    """What the background work is doing right now.

    Exists so that "the queue is not running" is assertable. A component whose only way of
    reporting that it does nothing is a log line is a component that silently does nothing.
    """
    return _STATE


def _reconcile_codex_homes() -> None:
    """Zdejmij martwe wpisy serwera narzędzi z domów Codexa. Nigdy nie rzuca.

    Porażka sprzątania nie ma prawa zatrzymać startu procesu: bez niej produkt działa tak jak
    przed tą poprawką, czyli gorzej, ale działa. Dlatego wynik idzie do logu, a wyjątek jest
    łapany i nazwany, zamiast wywracać `start_background`.
    """
    root = os.environ.get("WENEXT_CODEX_HOME_ROOT") or os.environ.get("WENEXT_CODEX_HOME")
    if not root:
        return
    try:
        from pathlib import Path

        from control_plane.workspace.codex_home import reconcile_homes

        for note in reconcile_homes(Path(root)):
            _LOG.warning("codex-home: %s", note)
    except Exception:
        _LOG.exception("codex-home: sprzątanie domów nie powiodło się; start idzie dalej")


async def start_background() -> None:
    """Start the process-local dispatcher and lease reaper. Never raises.

    Called from the application's lifespan. It must not change name or signature: `app.py` looks
    both up on this module at call time.
    """
    global _SUPERVISOR, _STATE

    if _SUPERVISOR is not None and not _SUPERVISOR.done():
        _LOG.warning(
            "start_background() zawołane, gdy zadania w tle już żyją — drugi dispatcher w tym "
            "samym procesie brałby joby równolegle z pierwszym. Nic nie robię"
        )
        return

    # SPRZĄTANIE DOMÓW CODEXA PRZED CZYMKOLWIEK INNYM. Rozmowa zostawia w `config.toml` domu
    # wpis serwera narzędzi z `required = true` i ze ścieżką do swojego pliku zdolności; plik
    # żyje krótko i znika przy restarcie kontenera, a wpis zostaje. Każda następna tura W TYM
    # DOMU, także cudza, dostaje wtedy „required MCP servers failed to initialize" i pada w
    # całości (zmierzone na live 21.09: `judge.thread` pilota padał na wpisie po rozmowie).
    # Robi się to TUTAJ, bo tu jest jedyny moment, w którym wiadomo, że żadna tura jeszcze nie
    # trwa, więc zdjęcie martwego wpisu nikomu nic nie zabiera.
    _reconcile_codex_homes()

    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        detail = (
            f"brak {DSN_VARIABLE} — kolejka jobów NIE DZIAŁA w tym procesie. API odpowiada dalej "
            "(/healthz nie zależy od bazy), ale nic nie dispatchuje i nic nie odbiera wygasłych "
            "lease'ów. To błąd konfiguracji, nie stan przejściowy: ponawianie tego nie naprawi"
        )
        _LOG.error("%s", detail)
        _STATE = BackgroundState(phase=BackgroundPhase.NO_DSN, detail=detail)
        return

    _STATE = BackgroundState(
        phase=BackgroundPhase.WAITING_FOR_DATABASE,
        detail="zadania w tle startują — czekam na bazę i na jedyną organizację",
    )
    _SUPERVISOR = asyncio.create_task(_supervise(dsn), name="control-plane-background")


async def stop_background() -> None:
    """Cancel whatever `start_background()` started, wait for it, and close the pool.

    A task started and never cancelled outlives the request loop and keeps the process from
    shutting down inside its `stop_grace_period`. Idempotent: calling it twice, or without a
    successful start, is a no-op.
    """
    global _SUPERVISOR, _POOL, _STATE

    supervisor, _SUPERVISOR = _SUPERVISOR, None
    if supervisor is not None:
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            # Expected: this is the shutdown path, and both loops re-raise cancellation.
            pass
        except Exception:
            _LOG.exception("zadanie w tle zakończyło się wyjątkiem przy zatrzymywaniu")

    pool, _POOL = _POOL, None
    if pool is not None:
        await pool.close()

    _STATE = BackgroundState(phase=BackgroundPhase.STOPPED)


async def _supervise(dsn: str) -> None:
    """Wire the queue up, retrying until the database answers, then run both loops for ever."""
    global _POOL, _STATE

    while True:
        try:
            # Sixteen, not four: since 26.09 two lanes per organization run at the same time,
            # each with a handler and a heartbeat that both need a connection. With four shared by
            # every organization a heartbeat waited for a free connection longer than the lease
            # lived, and the job was reaped while it worked.
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=16)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception(
                "nie udało się połączyć z PostgreSQL — ponawiam za %.0f s. Do tego czasu kolejka "
                "NIE DZIAŁA",
                WIRING_RETRY_SECONDS,
            )
            await asyncio.sleep(WIRING_RETRY_SECONDS)
            continue

        _POOL = pool
        # Decision 4 of the module docstring is HISTORY since Outcome A: the observe module is
        # the producer of real work D-013 was waiting for, and its four handlers are built here
        # from the environment (`control_plane.observe.wiring`).
        #
        # ONE DISPATCHER PER ORGANIZATION SINCE P7, and this is the change that made a second
        # customer possible at all. `JobDispatcher` claims jobs FOR ONE organization; before P7
        # there was exactly one and `_sole_organization` refused to guess between several, which
        # meant the queue stopped dead the moment anybody registered a second space. That was
        # measured, not imagined: the export job of a freshly registered customer never ran.
        #
        # The set is re-read every `ORGANIZATION_RESCAN_SECONDS`, and the group is restarted when
        # it changes. Restarting rather than adding a task to a live group: the group owns the
        # cancellation of every loop inside it, and a set of loops nobody can cancel together is
        # how a process ends up with two dispatchers on one organization after a reload.
        organization_ids = await _organizations(pool)
        if not organization_ids:
            await pool.close()
            await asyncio.sleep(WIRING_RETRY_SECONDS)
            continue

        _STATE = BackgroundState(
            phase=BackgroundPhase.RUNNING,
            detail=None if len(organization_ids) == 1 else f"organizacji: {len(organization_ids)}",
            organization_id=organization_ids[0],
        )
        _LOG.info(
            "zadania w tle wystartowały dla %d organizacji: dispatcher i reaper na każdą",
            len(organization_ids),
        )
        rebuild = False
        try:
            # A TaskGroup so that cancelling THIS task cancels every loop, and so that none can
            # be left running by a mistake in the shutdown path.
            async with asyncio.TaskGroup() as group:
                for tenant in organization_ids:
                    handlers = build_handlers(pool, organization_id=tenant)
                    # P7 etap B: pierwsze ogniwo łańcucha operacyjnego. Joby `ops.*` zamawiają
                    # swój następny bieg same, więc łańcuch podtrzymuje się bez pomocy, ale nie
                    # zaczyna się sam. Zmierzone na live 19.09: po wdrożeniu 0020 w kolejce nie
                    # było ani jednego `ops.*`, a `service_heartbeats` było puste. Idempotentne
                    # po kluczu slotu, więc restart dołącza do istniejącego łańcucha zamiast
                    # budować drugi obok.
                    await seed_ops_chain(pool, organization_id=tenant)
                    # To samo dla tiku obecności: projekt z podpisaną delegacją stałby
                    # powierzony i nigdy nie ruszył, bo `presence.tick` też zamawia wyłącznie
                    # swój NASTĘPNY bieg. Tik sam odkrywa projekty, więc zasiewanie nie musi
                    # wiedzieć o nich nic poza tym, które są powierzone.
                    await seed_presence_chain(pool, organization_id=tenant)
                    # P8 etap B: łańcuch doświadczeń z publikacji. Ten sam brak pierwszego
                    # ogniwa, co przy `ops.*`: job zamawia wyłącznie swój NASTĘPNY bieg, więc
                    # łańcuch podtrzymuje się sam, a startował dotąd tylko po kliknięciu
                    # człowieka w trasie `request_experience_sweep`. Zasiewamy wyłącznie tam,
                    # gdzie jest co mierzyć, czyli w organizacji z choć jedną publikacją.
                    if await seed_experience_chain(pool, organization_id=tenant):
                        _LOG.info("zasiano łańcuch doświadczeń publikacji dla %s", tenant)
                    # NAPRAWA PO STARCIE, jednorazowa i idempotentna: rozmowy czekające na limit
                    # konta z terminem powrotu poza horyzontem (zmierzone na live 20.09: rok
                    # 2083) nie odezwą się same z definicji, a poprawka w przeliczeniu nie cofa
                    # jobów, które już stoją w kolejce. Druga próba nie znajduje nic.
                    repaired = await replan_impossible_returns(pool, organization_id=tenant)
                    if repaired:
                        _LOG.warning(
                            "przeplanowano powroty rozmów z niemożliwym terminem: %s", repaired
                        )
                    # TWO LANES PER ORGANIZATION (`jobs/lanes.py`): jobs that start a Codex
                    # engine run one at a time, exactly as before (L2159); everything else runs
                    # next to them. Until 26.09 a conversation turn held the publisher, the
                    # visibility checks and the observation for its whole duration.
                    engine = JobDispatcher(
                        pool,
                        organization_id=tenant,
                        handlers=handlers,
                        job_types=ENGINE_JOB_TYPES,
                        # One at a time: a second claimed engine job would only wait, beaten,
                        # behind a turn that can last hours.
                        batch_limit=1,
                        lane=ENGINE_LANE,
                    )
                    io = JobDispatcher(
                        pool,
                        organization_id=tenant,
                        handlers=handlers,
                        exclude_job_types=ENGINE_JOB_TYPES,
                        lane=IO_LANE,
                    )
                    reaper = JobLeaseReaper(pool, organization_id=tenant)
                    group.create_task(engine.run_forever(), name=f"job-dispatcher-engine-{tenant}")
                    group.create_task(io.run_forever(), name=f"job-dispatcher-io-{tenant}")
                    group.create_task(reaper.run_forever(), name=f"job-lease-reaper-{tenant}")
                group.create_task(
                    _watch_organizations(pool, organization_ids), name="organization-watch"
                )
        except* _OrganizationsChanged:
            # The set moved: somebody registered, or an organization was purged. Rebuild from the
            # new set. `continue` is not allowed inside `except*`, hence the flag.
            _LOG.info("zmienił się zbiór organizacji — przebudowuję zadania w tle")
            rebuild = True
        if rebuild:
            continue
        return


#: How often the background task re-reads the list of organizations. A minute by default:
#: registering a space and waiting a minute for the queue to notice is acceptable, and a tighter
#: loop would be a query per second for a set that changes a few times in a product's life.
#:
#: `WENEXT_ORGANIZATION_RESCAN_SECONDS` lowers it. The end-to-end suite sets it to three, because
#: it registers a customer and then waits for that customer's job to run; without the knob the
#: test would either sleep a minute or pass only because somebody else's dispatcher was already
#: running, which is the kind of green that teaches nothing.
ORGANIZATION_RESCAN_VARIABLE = "WENEXT_ORGANIZATION_RESCAN_SECONDS"
ORGANIZATION_RESCAN_SECONDS = 60.0


def _rescan_seconds() -> float:
    raw = os.environ.get(ORGANIZATION_RESCAN_VARIABLE, "").strip()
    if not raw:
        return ORGANIZATION_RESCAN_SECONDS
    try:
        value = float(raw)
    except ValueError:
        _LOG.error(
            "%s=%r nie jest liczbą sekund. Używam domyślnych %.0f s",
            ORGANIZATION_RESCAN_VARIABLE,
            raw,
            ORGANIZATION_RESCAN_SECONDS,
        )
        return ORGANIZATION_RESCAN_SECONDS
    return max(1.0, value)


class _OrganizationsChanged(Exception):
    """Raised inside the task group to bring it down so it can be rebuilt. Not an error."""


async def _organizations(pool: asyncpg.Pool) -> list[UUID]:
    """Every organization with a dispatcher of its own, oldest first.

    An organization whose deletion has been requested is EXCLUDED: its access is already cut, and
    running its jobs during the grace period would mean the product acting for a customer who
    asked it to stop.

    The recording sandbox (`demo.py`) is EXCLUDED too, for the opposite reason: it is the one
    organization that must never act, because the dispatcher is wired to the capsules of the real
    accounts that share this installation.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT o.id FROM organizations o
            WHERE NOT EXISTS (
                SELECT 1 FROM data_deletion_jobs d
                 WHERE d.organization_id = o.id AND d.status = 'REQUESTED'
            )
              AND o.id <> ALL($1::uuid[])
            ORDER BY o.created_at, o.id
            """,
            list(DEMO_ORGANIZATION_IDS),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOG.exception(
            "nie udało się odczytać tabeli organizacji — migracje mogły jeszcze nie przejść. "
            "Ponawiam; do tego czasu kolejka NIE DZIAŁA"
        )
        return []
    if not rows:
        _LOG.error(
            "nie ma ANI JEDNEJ organizacji do obsłużenia (albo wszystkie czekają na usunięcie). "
            "Kolejka NIE DZIAŁA, dopóki jakaś nie powstanie"
        )
    return [row["id"] for row in rows]


async def _watch_organizations(pool: asyncpg.Pool, known: list[UUID]) -> None:
    """Bring the group down when the set of organizations changes. Nothing else."""
    while True:
        await asyncio.sleep(_rescan_seconds())
        current = await _organizations(pool)
        if current and current != known:
            raise _OrganizationsChanged()


async def _sole_organization(pool: asyncpg.Pool) -> UUID | None:
    """The single organization of this installation, or `None` with a reason in the log.

    `None` rather than an exception, because the caller retries: a container started before the
    schema was seeded has to be able to pick the organization up later without being restarted.
    """
    try:
        rows = await pool.fetch("SELECT id, name FROM organizations ORDER BY created_at, id")
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOG.exception(
            "nie udało się odczytać tabeli organizacji — migracje mogły jeszcze nie przejść. "
            "Ponawiam; do tego czasu kolejka NIE DZIAŁA"
        )
        return None

    if len(rows) == 1:
        return rows[0]["id"]
    if not rows:
        _LOG.error(
            "w bazie nie ma ANI JEDNEJ organizacji — nie ma czyjej kolejki obsługiwać. Kolejka "
            "NIE DZIAŁA, dopóki organizacja nie powstanie"
        )
        return None
    listed = ", ".join(f"{row['id']} ({row['name']})" for row in rows)
    _LOG.error(
        "w bazie jest %d organizacji: %s. E1 ma dokładnie jedną (D-016), a zgadywanie "
        "dispatchowałoby joby obcego tenanta i nikt by tego nie zauważył. Kolejka NIE DZIAŁA",
        len(rows),
        listed,
    )
    return None
