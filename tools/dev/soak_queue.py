"""Multi-hour fault-injection soak of the job queue.

Why this exists next to the unit suite. `make test-db` proves the queue's invariants over a few
seconds and a handful of rows. Two Definition-of-Done items of E1.F1 are not about a moment,
they are about a WINDOW: "a component restart does not lose a task" (L5212) and "two workers
never take the same record" (L5213). A race that shows up once every few thousand claims, a
lease that resurrects after a clock skew, a reaper that stops after its first exception — none
of those are visible in a test that runs for two seconds.

What this measures, stated narrowly so the artifact cannot be read as more:
  * every job handed out is handed out to EXACTLY ONE worker (L5213),
  * a worker killed mid-lease loses nothing: the job comes back and its attempt count is
    consistent with the limit (L5212, L5214),
  * a job never sits runnable-but-forgotten: no row stays claimable past a reaper pass without
    an attempt row to show for it,
  * the reaper loop survives a raising handler (a loop that dies on the first exception loses
    every later job while every unit test stays green).

What it does NOT measure, and must not be quoted as measuring: anything about the dispatcher
running inside the API container (this harness drives its own workers), anything about the
browser, and anything about a real platform.

EVIDENCE DISCIPLINE, following D-009. The artifact is written after every round, and
`zakonczony` is set ONLY when the declared window is served to the end. An interrupted run
therefore leaves `zakonczony: None` and NO verdict — never a stale pass. `planned_hours` is a
DECLARATION made at the start; the proof of the window's length is `zakonczony - rozpoczety`.
The keys stay Polish because the founder reads this artifact, like every other one in the repo.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import signal
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "control_plane"))

from control_plane.domain.commands import claim_batch, enqueue_job, report_result
from control_plane.domain.models import JobOutcome
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SoakRun:
    """Serialised straight into the artifact by `asdict`, so the field names are the keys."""

    rozpoczety: str
    planned_hours: float
    zakonczony: str | None = None
    rundy: int = 0
    jobow_wstawionych: int = 0
    claimow_wydanych: int = 0
    claimow_unikalnych: int = 0
    podwojnych_claimow: int = 0
    workerow_zabitych: int = 0
    prob_odzyskanych_przez_reapera: int = 0
    jobow_zterminalizowanych: int = 0
    przekroczen_limitu_prob: int = 0
    jobow_zgubionych: int = 0
    wyjatkow_w_petli_reapera: int = 0
    #: Ile jobów FAKTYCZNIE doszło do `attempt_limit`. Bez tego `przekroczen_limitu_prob: 0`
    #: jest puste: zero naruszeń na zbiorze, do którego mechanizm nigdy nie dotarł, to
    #: `all([])`, a nie dowód. Pierwszy przebieg tego harnessu (1,5 h, 6053 zabitych workerów)
    #: dał max(attempt_no) = 2 przy limicie 3 — czyli L5214 NIE zostało zmierzone wcale.
    jobow_ktore_dobily_do_limitu: int = 0
    przerwany_przez_czlowieka: bool = False
    naruszenia: list[str] = field(default_factory=list)


class TerminatedBySignal(BaseException):
    """A terminating signal, re-raised so the `finally` chain runs and the artifact is written.

    Derived from BaseException on purpose: an `except Exception` anywhere in the round loop
    would otherwise swallow the signal and the run would report a verdict for a window it
    never served. The same reasoning is written out in `gate2.py`, and it is the eighth false
    pass of that class in this project that put it there.
    """


def _install_signal_handlers(run: SoakRun) -> None:
    def handler(signum: int, _frame: object) -> None:
        run.przerwany_przez_czlowieka = True
        raise TerminatedBySignal(f"sygnał {signal.Signals(signum).name}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


def _write_artifact(path: Path, run: SoakRun) -> None:
    """Called from the innermost `finally`, so it runs on every exit path except SIGKILL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    closed = run.zakonczony is not None
    clean = closed and not run.naruszenia and run.podwojnych_claimow == 0
    artifact = {
        "nazwa": "soak-kolejki-jobow",
        # No verdict for an unclosed window. This is the D-009 rule, not a stylistic choice.
        "werdykt": ("ZDANE" if clean else "NIE_ZDANE") if closed else "NIE_URUCHOMIONO",
        "zdane": clean,
        "wymaga_czlowieka": not closed,
        "co_mierzy": [
            "L5213 — każdy job wydany DOKŁADNIE jednemu workerowi",
            "L5212 — worker zabity w trakcie lease'u nie gubi joba",
            "L5214 — liczba prób nie przekracza attempt_limit",
            "L2669 — pętla reapera przeżywa wyjątek",
        ],
        "czego_NIE_mierzy": [
            "dispatchera w procesie API (ten harness prowadzi własne workery)",
            "przeglądarki i żadnej prawdziwej platformy",
        ]
        + (
            []
            if run.jobow_ktore_dobily_do_limitu
            else [
                "L5214 — limit prób i terminalizacja NIE zostały uruchomione: żaden job nie "
                "doszedł do attempt_limit, więc `przekroczen_limitu_prob: 0` jest PUSTE. "
                "PRZYCZYNA, zmierzona: reaper tworzy zastępczą próbę i zostawia joba "
                "w WAITING_RETRY z not_before w przyszłości, a z powrotem do READY promuje "
                "go DISPATCHER — którego ten harness nie uruchamia, bo pobiera joby wprost "
                "ze store'u. Łańcuch ponowień zatrzymuje się na drugiej próbie "
                "(zmierzone: max(attempt_no) = 2 przy limicie 3, na dwóch niezależnych "
                "przebiegach: 1,5 h i 0,1 h). Zmierzenie L5214 na oknie wymaga soaka "
                "prowadzonego PRZEZ dispatcher, nie obok niego"
            ]
        ),
        "pokrycie": {
            "jobow_ktore_dobily_do_limitu": run.jobow_ktore_dobily_do_limitu,
            "limit_prob_zmierzony": bool(run.jobow_ktore_dobily_do_limitu),
        },
        "przebieg": asdict(run),
    }
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


async def _sprawdz_niezmienniki(conn: asyncpg.Connection, run: SoakRun) -> None:
    """Reads the invariants straight out of PostgreSQL, not out of this process's counters.

    A harness that only checked its own bookkeeping would prove that the harness is consistent
    with itself. Every query below asks the database.
    """
    # Dwa aktywne lease'y na jednym jobie to złamanie L5213 na poziomie schematu.
    double_leases = await conn.fetchval(
        "SELECT count(*) FROM (SELECT job_id FROM job_leases WHERE status = 'ACTIVE' "
        "GROUP BY job_id HAVING count(*) > 1) AS x"
    )
    if double_leases:
        run.naruszenia.append(f"{double_leases} jobów z więcej niż jednym aktywnym lease'em")

    # Próba over_limit limitem: dokładnie ta strona off-by-one, którą DoD L5214 nazywa po imieniu.
    over_limit = await conn.fetchval(
        "SELECT count(*) FROM (SELECT a.job_id FROM job_attempts a JOIN jobs j ON j.id = a.job_id "
        "GROUP BY a.job_id, j.attempt_limit HAVING count(*) > j.attempt_limit) AS x"
    )
    if over_limit:
        run.przekroczen_limitu_prob += int(over_limit)
        run.naruszenia.append(f"{over_limit} jobów z liczbą prób over_limit attempt_limit")

    # Job w stanie oczekiwania trzymający lease — niezmiennik WAITING_HOLDS_NO_LEASE (L2147).
    waiting_holding_a_lease = await conn.fetchval(
        "SELECT count(*) FROM jobs j JOIN job_leases l ON l.job_id = j.id "
        "WHERE j.status LIKE 'WAITING%' AND l.status = 'ACTIVE'"
    )
    if waiting_holding_a_lease:
        run.naruszenia.append(f"{waiting_holding_a_lease} jobów WAITING_* z aktywnym lease'em")

    # Ile jobów DOSZŁO do limitu. Czytane osobno od naruszeń, bo to jest licznik POKRYCIA,
    # nie licznik błędów: dopóki jest zerowy, wynik `przekroczen_limitu_prob: 0` niczego
    # nie dowodzi i artefakt musi to powiedzieć wprost.
    run.jobow_ktore_dobily_do_limitu = int(
        await conn.fetchval(
            "SELECT count(*) FROM (SELECT a.job_id FROM job_attempts a "
            "JOIN jobs j ON j.id = a.job_id WHERE j.job_type = 'soak' "
            "GROUP BY a.job_id, j.attempt_limit HAVING count(*) >= j.attempt_limit) AS x"
        )
        or 0
    )


async def _worker(
    store: JobStore,
    org: UUID_TYPE,
    run: SoakRun,
    seen: set[str],
    *,
    die_holding_the_lease: bool,
    lease_seconds: float,
) -> None:
    """One worker: claims one job and either finishes it or DIES holding the lease.

    Dying means returning without closing the attempt — the shape a SIGKILLed worker leaves
    behind. The reaper has to be the thing that notices, which is the point of L5212.
    """
    port = PostgresJobTransport(store, organization_id=org)
    worker_id = uuid.uuid4()
    try:
        batch = await claim_batch(port, worker=worker_id, limit=1, lease_seconds=lease_seconds)
    except Exception as exc:
        run.naruszenia.append(f"claim rzucił: {type(exc).__name__}: {exc}")
        return

    for claimed in batch.claims:
        key = f"{claimed.job_id}:{claimed.attempt_id}"
        run.claimow_wydanych += 1
        if key in seen:
            # Ta sama próba wydana dwa razy — to jest złamanie L5213 zmierzone, nie wywnioskowane.
            run.podwojnych_claimow += 1
            run.naruszenia.append(f"ta sama próba wydana dwukrotnie: {key}")
        else:
            seen.add(key)
            run.claimow_unikalnych += 1

        if die_holding_the_lease:
            run.workerow_zabitych += 1
            return  # lease zostaje w powietrzu — reaper ma to zobaczyć

        with contextlib.suppress(Exception):
            await report_result(port, token=claimed.fencing_token, outcome=JobOutcome.SUCCEEDED)


UUID_TYPE = uuid.UUID


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, required=True, help="deklarowane okno, w godzinach")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--jobs-per-round", type=int, default=12)
    parser.add_argument("--lease-seconds", type=float, default=2.0)
    parser.add_argument("--kill-ratio", type=float, default=0.35)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    if not args.dsn:
        print("brak DSN: podaj --dsn albo DATABASE_URL", file=sys.stderr)
        return 2

    artifact = args.artifacts / "soak-queue.json"
    run = SoakRun(rozpoczety=_now(), planned_hours=args.hours)
    _install_signal_handlers(run)
    _write_artifact(
        artifact, run
    )  # od razu, żeby przerwanie w pierwszej sekundzie też zostawiło ślad

    pool = await asyncpg.create_pool(args.dsn, min_size=2, max_size=args.workers + 4)
    deadline = asyncio.get_running_loop().time() + args.hours * 3600.0
    seen: set[str] = set()

    try:
        async with pool.acquire() as conn:
            org = await conn.fetchval("SELECT id FROM organizations LIMIT 1")
            ws = await conn.fetchval(
                "SELECT id FROM workspaces WHERE organization_id = $1 LIMIT 1", org
            )
        if org is None:
            run.naruszenia.append("brak organizacji w bazie — nie ma czego kolejkować")
            return 2

        store = JobStore(pool)
        port = PostgresJobTransport(store, organization_id=org)
        reaper = JobLeaseReaper(pool, organization_id=org)

        while asyncio.get_running_loop().time() < deadline:
            run.rundy += 1

            for _ in range(args.jobs_per_round):
                await enqueue_job(
                    port,
                    organization_id=org,
                    workspace_id=ws,
                    job_type="soak",
                    routing_key="soak",
                    priority=random.randint(0, 3),
                    attempt_limit=3,
                    idempotency_key=f"soak-{run.rundy}-{uuid.uuid4()}",
                )
                run.jobow_wstawionych += 1

            # Wszystkie workery ruszają JEDNOCZEŚNIE — o to w tym teście chodzi.
            await asyncio.gather(
                *(
                    _worker(
                        store,
                        org,
                        run,
                        seen,
                        die_holding_the_lease=random.random() < args.kill_ratio,
                        lease_seconds=args.lease_seconds,
                    )
                    for _ in range(args.workers)
                ),
                return_exceptions=True,
            )

            # Lease'y muszą naprawdę wygasnąć — zegar bazy, nie procesu.
            await asyncio.sleep(args.lease_seconds + 0.5)

            try:
                report = await reaper.reap_once()
                run.prob_odzyskanych_przez_reapera += len(report.reaped)
                run.jobow_zterminalizowanych += report.count(
                    ReapOutcome.TERMINALIZED_ATTEMPTS_EXHAUSTED
                )
                if not report.scanned_to_the_end:
                    # Przebieg urwany limitem partii znaczy, że wygasłych lease'ów może być
                    # więcej — to NIE to samo co "nie było czego zbierać" (docstring ReapReport).
                    run.naruszenia_miekkie = getattr(run, "naruszenia_miekkie", 0) + 1
            except TerminatedBySignal:
                raise
            except Exception as exc:
                # Pętla, która umiera po pierwszym wyjątku, gubi zadania na zawsze przy
                # zielonych testach jednostkowych (L2669). Liczymy i jedziemy dalej.
                run.wyjatkow_w_petli_reapera += 1
                run.naruszenia.append(
                    f"reaper rzucił w rundzie {run.rundy}: {type(exc).__name__}: {exc}"
                )

            async with pool.acquire() as conn:
                await _sprawdz_niezmienniki(conn, run)

            _write_artifact(artifact, run)

        run.zakonczony = _now()
        return 0 if not run.naruszenia and run.podwojnych_claimow == 0 else 1

    except TerminatedBySignal:
        return 130
    finally:
        with contextlib.suppress(Exception):
            await pool.close()
        _write_artifact(artifact, run)
        h = "—"
        if run.zakonczony:
            koniec = datetime.fromisoformat(run.zakonczony)
            start = datetime.fromisoformat(run.rozpoczety)
            h = f"{(koniec - start).total_seconds() / 3600:.2f}"
        print(
            f"soak: rundy={run.rundy} zmierzone={h} h z {run.planned_hours} h zadeklarowanych, "
            f"claimy={run.claimow_wydanych} (unikalnych {run.claimow_unikalnych}), "
            f"podwójnych={run.podwojnych_claimow}, zabitych workerów={run.workerow_zabitych}, "
            f"naruszeń={len(run.naruszenia)}, domknięty={run.zakonczony is not None}"
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
