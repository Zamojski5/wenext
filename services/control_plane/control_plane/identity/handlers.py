"""Two queue jobs of P7: building an export, and purging an organization after its grace period.

WHY JOBS. An export reads every table of an organization and writes a file; a purge deletes
them. Neither belongs in an HTTP handler: the first would hold a worker for as long as the
customer is large, and the second must survive a restart in the middle, because a purge that
stopped halfway and left no trace is the worst outcome available.

THE PURGE SWEEP IS NOT SELF-CHAINING, unlike the RSS and experience sweeps. It is enqueued when
a deletion is requested, with a delay reaching past the grace period, and it re-enqueues itself
ONLY while that same deletion is still pending — so a cancelled deletion ends the chain by being
cancelled, and nothing keeps running after the thing it was waiting for is gone.

NOTHING HERE CALLS A MODEL and nothing here touches another organization: both handlers take the
organization from the job's own row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.dispatcher import Handler

JOB_EXPORT = "identity.export"
JOB_PURGE = "identity.purge"
ROUTING_KEY = "identity"

#: Above the knowledge sweeps and below a session: somebody is waiting for this file, and
#: somebody else is waiting to stop existing.
PRIORITY = 40

#: One attempt for the purge, three for the export. A purge that half-ran and is retried would
#: delete again what is already gone, which is harmless, but a retry loop over a deletion is not
#: something to leave running; the export, by contrast, is a read and a write of one file.
EXPORT_ATTEMPTS = 3
PURGE_ATTEMPTS = 1

#: How often the purge job looks again while a deletion is still inside its grace period.
PURGE_CHECK_INTERVAL = timedelta(hours=6)

_EXPORT_PREFIX = "data-export:"
_PURGE_PREFIX = "organization-purge:"


def export_ref(job_id: UUID) -> str:
    return f"{_EXPORT_PREFIX}{job_id}"


def purge_ref(organization_id: UUID) -> str:
    return f"{_PURGE_PREFIX}{organization_id}"


def _from_ref(payload_ref: str | None, prefix: str, what: str) -> UUID:
    if not payload_ref or not payload_ref.startswith(prefix):
        raise ValueError(f"payload_ref={payload_ref!r} nie wskazuje {what}")
    return UUID(payload_ref[len(prefix) :])


async def schedule_export(port, *, organization_id: UUID, job_id: UUID) -> object:
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_EXPORT,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=EXPORT_ATTEMPTS,
        idempotency_key=f"data-export:{job_id}",
        payload_ref=export_ref(job_id),
    )


async def schedule_purge(
    port, *, organization_id: UUID, delay_seconds: float = 0.0, attempt: int | None = None
) -> object:
    """The look that happens after the grace period. Keyed by the hour it is due in.

    Same rule as the other sweeps in this product, for the same measured reason: a job keyed by
    the hour it RUNS in collides with its own row when it schedules its successor, and the chain
    stops with nobody noticing.
    """
    due_hour = (
        int((datetime.now(UTC).timestamp() + delay_seconds) // 3600) if attempt is None else attempt
    )
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=None,
        job_type=JOB_PURGE,
        routing_key=ROUTING_KEY,
        priority=PRIORITY,
        attempt_limit=PURGE_ATTEMPTS,
        idempotency_key=f"organization-purge:{organization_id}:{due_hour}",
        payload_ref=purge_ref(organization_id),
        delay_seconds=delay_seconds,
    )


@dataclass
class IdentityHandlers:
    """The export and the purge, over one pool. `home_root` may be None on a host with no volume."""

    pool: asyncpg.Pool
    organization_id: UUID
    queue: object | None = None
    volume: object | None = None
    home_root: Path | None = None
    check_interval: timedelta = PURGE_CHECK_INTERVAL

    def as_mapping(self) -> dict[str, Handler]:
        return {JOB_EXPORT: self.build_export, JOB_PURGE: self.purge_organization}

    async def build_export(self, claim: ClaimedJob) -> None:
        """Write the file and point the job at it. A failure lands on the row with its reason."""
        from control_plane.identity.data import DataStore

        job_id = _from_ref(claim.payload_ref, _EXPORT_PREFIX, "eksportu")
        store = DataStore(self.pool)
        job = await store.export_job(organization_id=claim.organization_id, job_id=job_id)
        if job is None:
            # The organization was purged between the request and the claim. Nothing to build.
            return
        await store.run_export(
            organization_id=claim.organization_id,
            job_id=job_id,
            volume=self.volume or self._volume(),
        )

    async def purge_organization(self, claim: ClaimedJob) -> None:
        """Delete the organization's own rows if the grace period has passed, else look later."""
        from control_plane.identity.data import DataStore

        organization_id = _from_ref(claim.payload_ref, _PURGE_PREFIX, "organizacji")
        store = DataStore(self.pool)
        pending = await store.pending_deletion(organization_id)
        if pending is None:
            # Cancelled, or already purged. The chain ends here, which is what cancelling means.
            return
        purged = await store.purge(organization_id=organization_id)
        if purged is not None:
            return
        if self.queue is not None:
            await schedule_purge(
                self.queue,
                organization_id=organization_id,
                delay_seconds=self.check_interval.total_seconds(),
            )

    def _volume(self):
        from control_plane.knowledge.ingest import artifact_volume

        return artifact_volume()
