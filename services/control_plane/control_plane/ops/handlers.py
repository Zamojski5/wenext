"""Joby gotowości operacyjnej: puls, wykrywanie awarii i dzienna kopia (P7 etap B).

TRZY JOBY, KAŻDY O CZYMŚ INNYM:

  * `ops.heartbeat` — bije w imieniu komponentów, które NIE mają własnego miejsca na puls
    (dispatcher bije sam z siebie, bo to on uruchamia ten job), i zamyka incydenty tych, które
    wróciły. Nie woła modelu i nie dotyka platformy.
  * `ops.sweep` — zamienia CISZĘ w incydent. Bez tego komponent, który przestał odpowiadać,
    nie zostawia po sobie nic prócz dziury w pulsie, a dziura nie ma powodu ani początku.
  * `ops.backup.daily` — ZAMAWIA kopię i zapisuje w audycie, że ją zamówiono. Sam jej NIE
    robi, i to jest decyzja: `pg_dump` musi biec tam, gdzie widać wolumen z bazą i gdzie jest
    miejsce na plik, a proces workera nie jest tym miejscem. Job zostawia w dzienniku ślad, że
    o kopię poproszono, i tym samym odróżnia „kopii nie zrobiono" od „nikt jej nie zamówił".

DLACZEGO KOPIA NIE MA SWOJEJ TABELI W BAZIE. Spis kopii trzymany w bazie, którą się odtwarza,
jest spisem, którego w momencie odtwarzania nie ma. Manifest leży obok pliku kopii, a ślad
zamówienia w `audit_events` odpowiada na inne pytanie: czy system o nią prosił.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.audit import AuditWriter
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

from .health import COMPONENT_DISPATCHER, INTERVAL, HealthStore
from .ledger import refill

_LOG = logging.getLogger(__name__)

JOB_HEARTBEAT = "ops.heartbeat"
JOB_SWEEP = "ops.sweep"
JOB_BACKUP_DAILY = "ops.backup.daily"

OPS_PRIORITY = 30
ATTEMPT_LIMIT = 1

#: How often the heartbeat job runs. The same interval the heartbeat table buckets by, so one run
#: produces exactly one row and a missed run is exactly one hole.
HEARTBEAT_SECONDS = int(INTERVAL.total_seconds())

#: How often silence is turned into incidents. Slower than the heartbeat on purpose: a sweep that
#: ran as often as the beat would open an incident for every restart.
SWEEP_SECONDS = HEARTBEAT_SECONDS * 2

DAY_SECONDS = 24 * 60 * 60


async def seed_ops_chain(pool: asyncpg.Pool, *, organization_id: UUID) -> None:
    """Start the operational chain for one organization. Safe to call on every process start.

    THIS IS THE MISSING FIRST LINK, and it was missing for real: measured on the live database on
    2026-09-19, after migration 0020 was deployed, `jobs` held not a single `ops.*` row and
    `service_heartbeats` was empty, because `schedule_ops` existed and nobody called it. The jobs
    reschedule THEMSELVES after each run, so the chain sustains itself perfectly once it starts;
    it simply never started. A loop that keeps itself alive and a loop that BEGINS are two
    different properties, and only the first one had a test.

    Idempotent by the slot key, so a restart rejoins the existing chain instead of building a
    second one beside it, and a chain that died comes back on the next process start.
    """
    await schedule_ops(
        PostgresJobTransport(JobStore(pool), organization_id=organization_id),
        organization_id=organization_id,
    )


async def schedule_ops(
    port: JobQueuePort, *, organization_id: UUID, workspace_id: UUID | None = None
) -> None:
    """Put the three operational jobs on the queue, each idempotent within its own slot.

    `workspace_id` is OPTIONAL and normally `None`: availability, incidents, cost and the
    organization ceiling are facts about an ORGANIZATION, and their tables (0020) carry no
    workspace column at all. `jobs.workspace_id` is nullable for exactly this case, which 0003
    spells out: "Job organizacyjny z `workspace_id IS NULL` przechodzi wyłącznie przez org-scope
    policy". Passing a workspace would narrow a job that is not narrow.
    """
    now = datetime.now(UTC)
    for job_type, seconds in (
        (JOB_HEARTBEAT, HEARTBEAT_SECONDS),
        (JOB_SWEEP, SWEEP_SECONDS),
        (JOB_BACKUP_DAILY, DAY_SECONDS),
    ):
        slot = int((now + timedelta(seconds=seconds)).timestamp()) // seconds
        await commands.enqueue_job(
            port,
            organization_id=organization_id,
            workspace_id=workspace_id,
            job_type=job_type,
            routing_key="observe",
            priority=OPS_PRIORITY,
            attempt_limit=ATTEMPT_LIMIT,
            idempotency_key=f"{job_type}:{organization_id}:{slot}",
            payload_ref=f"ops/{organization_id}",
            delay_seconds=float(seconds),
        )


class OpsHandlers:
    """The three operational jobs and the logic behind them."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        health: HealthStore,
        queue: JobQueuePort,
        organization_id: UUID,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._health = health
        self._queue = queue
        self._organization_id = organization_id
        self._clock = clock

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {
            JOB_HEARTBEAT: self.heartbeat_job,
            JOB_SWEEP: self.sweep_job,
            JOB_BACKUP_DAILY: self.backup_job,
        }

    async def heartbeat_job(self, claim: ClaimedJob) -> None:
        """The dispatcher is alive, because this job is running inside it.

        THAT IS THE WHOLE ARGUMENT for why the dispatcher's heartbeat is written here and not by
        a separate watchdog: a watchdog can only say "it answered me", while a job that ran is
        proof the queue moved, which is the thing the dispatcher is for.
        """
        now = self._clock()
        await self._health.beat(
            organization_id=self._organization_id,
            component=COMPONENT_DISPATCHER,
            now=now,
            detail={"job": claim.job_type, "job_id": str(claim.job_id)},
        )
        # The ledger is refilled here rather than in its own job: it is a read over tables this
        # organization already wrote, it costs one statement per kind, and a separate job would
        # be one more thing that can silently stop.
        try:
            filled = await refill(self._pool, organization_id=self._organization_id, now=now)
            if filled.total:
                _LOG.info("ops.heartbeat: księga kosztów %s", filled.to_json())
        except Exception as exc:  # a ledger that cannot be filled is not a reason to look dead
            _LOG.warning("ops.heartbeat: nie udało się zasilić księgi kosztów: %s", exc)

        # SESJE, KTÓRYCH TURA UMARŁA. Zmierzone na live 19.09: job `session.turn` padł po
        # wygaśnięciu lease'u, a `agent_sessions` dalej mówiło RUNNING, więc studio uczciwie
        # pokazywało „pracuje" na podstawie danych, które kłamały. Uzgodnienie idzie tutaj, bo
        # ten job i tak bije co interwał i jest jedynym, który biegnie niezależnie od sesji.
        try:
            from control_plane.sessions.store import SessionStore

            orphans = await SessionStore(self._pool).reconcile_orphans(
                organization_id=self._organization_id
            )
            for one in orphans:
                _LOG.warning(
                    "ops.heartbeat: sesja %s stała w RUNNING bez żywej tury; zamknięta jako FAILED",
                    one.id,
                )
        except Exception as exc:  # uzgodnienie, które padło, nie może zabrać pulsu
            _LOG.warning("ops.heartbeat: uzgodnienie sesji nie powiodło się: %s", exc)

        await self._reschedule(claim, JOB_HEARTBEAT, HEARTBEAT_SECONDS)

    async def sweep_job(self, claim: ClaimedJob) -> None:
        """Turn silence into incidents, with a reason and a beginning."""
        opened = await self._health.sweep_missing(
            organization_id=self._organization_id, now=self._clock()
        )
        for incident in opened:
            _LOG.warning("ops.sweep: %s nie bije od %s", incident.component, incident.started_at)
        await self._reschedule(claim, JOB_SWEEP, SWEEP_SECONDS)

    async def backup_job(self, claim: ClaimedJob) -> None:
        """Record that a backup was asked for today. The file is made by `tools/dev/backup.sh`.

        A row in the audit log and nothing else, deliberately. `pg_dump` belongs where the volume
        and the disk space are; a worker process is not that place, and a job that pretended to
        make the backup would leave the operator believing in a file nobody wrote.
        """
        now = self._clock()
        async with self._pool.acquire() as conn, conn.transaction():
            await AuditWriter(conn).record(
                kind="BACKUP_REQUESTED",
                organization_id=self._organization_id,
                workspace_id=claim.workspace_id,
                subject_ref=f"organization:{self._organization_id}",
                actor_ref="job:ops.backup.daily",
                payload={"job_id": str(claim.job_id), "kind": JOB_BACKUP_DAILY},
            )
        _LOG.info("ops.backup.daily: zamówiono kopię na %s", now.date().isoformat())
        await self._reschedule(claim, JOB_BACKUP_DAILY, DAY_SECONDS)

    async def _reschedule(self, claim: ClaimedJob, job_type: str, seconds: int) -> None:
        """Ask for the next run. Failure to reschedule is logged, never raised: a chain that
        breaks because of one enqueue is exactly the silent stop these jobs exist to notice."""
        slot = int((self._clock() + timedelta(seconds=seconds)).timestamp()) // seconds
        try:
            await commands.enqueue_job(
                self._queue,
                organization_id=self._organization_id,
                # PRZESTRZEŃ ALBO NIC, NIGDY ORGANIZACJA W JEJ MIEJSCU. Stało tu
                # `claim.workspace_id or self._organization_id`, a joby operacyjne są z definicji
                # organizacyjne i mają `workspace_id IS NULL` (0003), więc podstawiała się
                # organizacja i baza odrzucała wiersz kluczem obcym
                # `jobs_workspace_belongs_to_organization`. Skutek: ŁAŃCUCH PRZESTAWAŁ SIĘ
                # PRZEDŁUŻAĆ, a jedynym śladem była linia WARNING w logu. Zauważone przez
                # wenext-ce w logu control plane'u 21.09, po identyfikatorze powtórzonym w obu
                # polach.
                workspace_id=claim.workspace_id,
                job_type=job_type,
                routing_key="observe",
                priority=OPS_PRIORITY,
                attempt_limit=ATTEMPT_LIMIT,
                idempotency_key=f"{job_type}:{self._organization_id}:{slot}",
                payload_ref=f"ops/{self._organization_id}",
                delay_seconds=float(seconds),
            )
        except Exception as exc:
            _LOG.warning("%s: nie udało się zamówić następnego biegu: %s", job_type, exc)


def component_detail(**fields: Any) -> dict[str, Any]:
    """Small helper so callers outside this module write the same shape into `detail`."""
    return {key: value for key, value in fields.items() if value is not None}
