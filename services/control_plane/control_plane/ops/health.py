"""Czy komponenty usługi chodziły, i co było nie tak, gdy nie chodziły (P7 etap B).

DLACZEGO PULS, A NIE „ZAPYTAJ, CZY ŻYJE". Pytanie „czy teraz odpowiada" mierzy chwilę. Raport
tygodnia potrzebuje czegoś innego: ile interwałów w ciągu siedmiu dób miało dowód pracy. Wiersz
pisany przez sam komponent jest takim dowodem, a jego BRAK jest dowodem przeciwnym i nie da się
go pomylić z „nikt nie pytał".

INTERWAŁ, NIE ZNACZNIK CZASU. Puls zapisuje POCZĄTEK interwału, do którego należy, więc:

  * dwa procesy tego samego komponentu w jednym interwale dają jeden wiersz (UNIQUE), a nie dwa,
    które wyglądałyby na podwójną dostępność;
  * dostępność liczy się zwykłym dzieleniem, bez kubełkowania przy każdym raporcie, czyli bez
    drugiego miejsca, w którym można się pomylić o jeden.

NIEDOSTĘPNOŚĆ PLATFORMY JEST INCYDENTEM KOMPONENTU, NIE AWARIĄ USŁUGI. Reddit odpowiadający 403
to incydent CAPSULE albo PUBLISHER. Reszta pracy (kanały RSS, rozmowy, inne projekty) ma biec
dalej, i to jest własność, którą mierzy `test_ops_isolation.py`, a nie obietnica z docstringa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

COMPONENT_DISPATCHER = "DISPATCHER"
COMPONENT_COLLECTOR = "COLLECTOR"
COMPONENT_PUBLISHER = "PUBLISHER"
COMPONENT_CAPSULE = "CAPSULE"
COMPONENT_CODEX = "CODEX_CONNECTION"
#: The same vocabulary in the heartbeat and in the incident, on purpose: "what was beating" and
#: "what broke" have to be one dictionary, or the report cannot join them.
COMPONENTS = (
    COMPONENT_DISPATCHER,
    COMPONENT_COLLECTOR,
    COMPONENT_PUBLISHER,
    COMPONENT_CAPSULE,
    COMPONENT_CODEX,
)

DETECTED_BY_SELF = "SELF_REPORT"
DETECTED_BY_MISSING = "MISSING_HEARTBEAT"
DETECTED_BY_HUMAN = "HUMAN"
DETECTORS = (DETECTED_BY_SELF, DETECTED_BY_MISSING, DETECTED_BY_HUMAN)

#: How long one heartbeat interval is. Five minutes: short enough that a week of them is a
#: meaningful denominator (2016 intervals), long enough that a worker busy with one long job does
#: not look dead. A component that cannot beat within five minutes has something to say anyway.
INTERVAL = timedelta(minutes=5)

#: How many intervals may pass with no heartbeat before the component counts as down. TWO, not
#: one: a single missed beat is a slow tick or a restart, and an incident opened for every
#: restart would make the incident log unreadable, which is the same as having none.
MISSED_INTERVALS_BEFORE_DOWN = 2


class HealthRefusal(Exception):
    """A value outside a closed list. Nothing was written."""


@dataclass(frozen=True)
class IncidentRow:
    id: UUID
    component: str
    started_at: datetime
    ended_at: datetime | None
    reason: str
    detected_by: str

    @property
    def open(self) -> bool:
        return self.ended_at is None

    def to_json(self) -> dict[str, Any]:
        return {
            "incident_id": str(self.id),
            "komponent": self.component,
            "od": self.started_at.isoformat(),
            "do": self.ended_at.isoformat() if self.ended_at else None,
            "powod": self.reason,
            "zauwazyl": self.detected_by,
            "trwa": self.open,
        }


def interval_of(moment: datetime) -> datetime:
    """The start of the interval this moment falls in. One rule, used by writers and readers."""
    seconds = int(INTERVAL.total_seconds())
    stamp = int(moment.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(stamp, tz=UTC)


def availability(beats: int, window: timedelta) -> float:
    """Fraction of the expected intervals that carried a heartbeat, between 0 and 1.

    A window shorter than one interval has NO expected intervals, and the answer is 0.0 rather
    than 1.0: dividing by zero would otherwise report perfect availability for a period nobody
    measured, which is the exact shape of lie `D-007` is about.
    """
    expected = int(window.total_seconds() // INTERVAL.total_seconds())
    if expected <= 0:
        return 0.0
    return min(1.0, beats / expected)


class HealthStore:
    """Heartbeats and incidents. Holds a pool, like every other store here."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def beat(
        self,
        *,
        organization_id: UUID,
        component: str,
        now: datetime | None = None,
        detail: dict[str, Any] | None = None,
    ) -> datetime:
        """Record that this component was alive in the current interval, and close its incident.

        ONE CALL DOES BOTH, and that is the point: a component that comes back and does not close
        its own incident leaves an open one that looks, a week later, like an outage that never
        ended. Returning is the evidence; nobody else has to notice it.
        """
        import json

        if component not in COMPONENTS:
            raise HealthRefusal(f"komponent {component!r} poza listą {COMPONENTS}")
        moment = now or datetime.now(UTC)
        slot = interval_of(moment)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO service_heartbeats (organization_id, component, interval_start, detail)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (organization_id, component, interval_start) DO NOTHING
                """,
                organization_id,
                component,
                slot,
                json.dumps(detail or {}, ensure_ascii=False),
            )
            await conn.execute(
                "UPDATE incidents SET ended_at = $3 "
                "WHERE organization_id = $1 AND component = $2 AND ended_at IS NULL",
                organization_id,
                component,
                moment,
            )
        return slot

    async def open_incident(
        self,
        *,
        organization_id: UUID,
        component: str,
        reason: str,
        detected_by: str = DETECTED_BY_SELF,
        now: datetime | None = None,
    ) -> IncidentRow:
        """Report that a component is not working. Idempotent while one is already open.

        A second report of the same outage returns the FIRST incident rather than opening
        another: two collectors noticing one refusal is normal, and two open incidents would be
        counted as two outages by the week report.
        """
        if component not in COMPONENTS:
            raise HealthRefusal(f"komponent {component!r} poza listą {COMPONENTS}")
        if detected_by not in DETECTORS:
            raise HealthRefusal(f"wykrywacz {detected_by!r} poza listą {DETECTORS}")
        if not reason.strip():
            raise HealthRefusal("incydent bez powodu nie mówi nikomu, co naprawić")
        moment = now or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM incidents WHERE organization_id = $1 "
                "AND component = $2 AND ended_at IS NULL",
                organization_id,
                component,
            )
            if existing is not None:
                return _incident_of(existing)
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO incidents
                        (organization_id, component, started_at, reason, detected_by)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING {_COLUMNS}
                    """,
                    organization_id,
                    component,
                    moment,
                    reason.strip()[:500],
                    detected_by,
                )
            except asyncpg.UniqueViolationError:
                # Another process opened it between the read and the write. Its row is as good
                # as ours would have been; the constraint is what makes that safe to assume.
                row = await conn.fetchrow(
                    f"SELECT {_COLUMNS} FROM incidents WHERE organization_id = $1 "
                    "AND component = $2 AND ended_at IS NULL",
                    organization_id,
                    component,
                )
        return _incident_of(row)

    async def close_incident(
        self, *, organization_id: UUID, component: str, now: datetime | None = None
    ) -> IncidentRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE incidents SET ended_at = $3 WHERE organization_id = $1 "
                f"AND component = $2 AND ended_at IS NULL RETURNING {_COLUMNS}",
                organization_id,
                component,
                now or datetime.now(UTC),
            )
        return _incident_of(row) if row is not None else None

    async def open_incidents(self, *, organization_id: UUID) -> tuple[IncidentRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM incidents WHERE organization_id = $1 "
                "AND ended_at IS NULL ORDER BY started_at",
                organization_id,
            )
        return tuple(_incident_of(row) for row in rows)

    async def incidents_since(
        self, *, organization_id: UUID, since: datetime
    ) -> tuple[IncidentRow, ...]:
        """Every incident that OVERLAPS the window, not only those that started inside it.

        An outage that began before the window and is still running is the most important row in
        a week report, and a query on `started_at` alone would leave it out.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM incidents WHERE organization_id = $1 "
                "AND (ended_at IS NULL OR ended_at >= $2) ORDER BY started_at DESC",
                organization_id,
                since,
            )
        return tuple(_incident_of(row) for row in rows)

    async def beats_since(self, *, organization_id: UUID, component: str, since: datetime) -> int:
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM service_heartbeats WHERE organization_id = $1 "
                    "AND component = $2 AND interval_start >= $3",
                    organization_id,
                    component,
                    since,
                )
                or 0
            )

    async def last_beat(self, *, organization_id: UUID, component: str) -> datetime | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT max(interval_start) FROM service_heartbeats "
                "WHERE organization_id = $1 AND component = $2",
                organization_id,
                component,
            )

    async def sweep_missing(
        self, *, organization_id: UUID, now: datetime | None = None
    ) -> tuple[IncidentRow, ...]:
        """Open an incident for every component that stopped beating, and return them.

        A component that NEVER beat is deliberately not reported: it is not an outage, it is a
        component this deployment does not run, and a week of incidents about a publisher nobody
        configured would drown the ones that mean something.
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - INTERVAL * MISSED_INTERVALS_BEFORE_DOWN
        opened: list[IncidentRow] = []
        for component in COMPONENTS:
            last = await self.last_beat(organization_id=organization_id, component=component)
            if last is None or last >= cutoff:
                continue
            opened.append(
                await self.open_incident(
                    organization_id=organization_id,
                    component=component,
                    reason=(
                        f"brak pulsu od {last.isoformat()}; ostatni interwał starszy niż "
                        f"{MISSED_INTERVALS_BEFORE_DOWN} okresy po "
                        f"{int(INTERVAL.total_seconds())} s"
                    ),
                    detected_by=DETECTED_BY_MISSING,
                    now=moment,
                )
            )
        return tuple(opened)


_COLUMNS = "id, component, started_at, ended_at, reason, detected_by"


def _incident_of(row: asyncpg.Record) -> IncidentRow:
    return IncidentRow(
        id=row["id"],
        component=row["component"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        reason=row["reason"],
        detected_by=row["detected_by"],
    )
