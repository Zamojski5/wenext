"""Taking your data out, and having it deleted (P7 etap A).

TWO OPERATIONS A CUSTOMER MUST BE ABLE TO PERFORM WITHOUT ASKING ANYBODY, and the milestone puts
them next to each other for a reason: a product that can be joined self-service and left only by
writing to its owner is a product people are right not to trust.

WHAT AN EXPORT IS. One JSON file per organization, holding what that organization put in:
projects, person profiles, platform accounts (WITHOUT credentials), materials, findings, agent
sessions with their traces, publications with their visibility. It is written to `artifacts`
like everything else, so it has a checksum, an ACL, a retention and a sweeper; a copy of a whole
organization lying around with no expiry is a leak waiting for somebody to find it.

WHAT AN EXPORT IS NOT: a backup, and not a migration format. It is what the person can read and
take elsewhere. Nothing here promises the product can import it back.

WHAT DELETION DOES, IN THIS ORDER AND FOR THESE REASONS:

  1. **immediately**: sign-in stops working (the membership check refuses while a deletion is
     pending) and every secret this product holds for that organization is destroyed — platform
     credentials and the Codex home directories of its connections. A grace period on a
     credential is a grace period for whoever stole it;
  2. **after `purge_after`**: the organization's own rows are deleted. The grace period exists
     so a person who clicked by mistake can be helped, and it is the only part that waits;
  3. **never**: the volume root of the live pilot. `home_ref = '.'` is the founder's own
     directory, shared by the deployment, and no tenant's deletion may take it.

WHAT IS NOT DELETED AND IS SAID OUT LOUD: `audit_events` rows of that organization stay. The
table is append-only by trigger (0001, 0002), and a log that can be erased by the subject of the
log is not a log. The report says this; the code does not quietly try and fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

#: How long the exported file lives before the sweeper removes it.
EXPORT_RETENTION = timedelta(days=7)

#: How long an organization has between asking for deletion and the data going. Access is cut at
#: once; this is only the window in which a mistake can still be undone.
DELETION_GRACE = timedelta(days=7)

STATUS_REQUESTED = "REQUESTED"
STATUS_RUNNING = "RUNNING"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"
STATUS_PURGED = "PURGED"
STATUS_CANCELLED = "CANCELLED"

#: Tables emptied by a purge, in dependency order (children first). Written out by hand rather
#: than discovered at run time: a loop over `information_schema` would delete a table added
#: later by somebody who never thought about deletion, and the first time anybody noticed would
#: be after it happened.
PURGE_ORDER: tuple[str, ...] = (
    "knowledge_items",
    "knowledge_sources",
    "session_events",
    "agent_sessions",
    "publication_observations",
    "publication_requests",
    "project_account_assignments",
    "person_profile_accounts",
    "person_profiles",
    # 0035: what the hourly X collection saw and what the agent watched; both hang off projects.
    "x_trend_posts",
    "x_watch_queries",
    # 0037: hosted access (a token and a signed-in identity each point at a project).
    "external_identities",
    "hosted_tokens",
    "projects",
    "codex_connections",
    "profile_deks",
    "browser_profiles",
    "artifacts",
)


@dataclass(frozen=True)
class JobRow:
    id: UUID
    organization_id: UUID
    requested_by: UUID
    status: str
    requested_at: datetime
    finished_at: datetime | None
    purge_after: datetime | None
    detail: str | None
    artifact_id: UUID | None = None


def _export_job(row: asyncpg.Record) -> JobRow:
    return JobRow(
        id=row["id"],
        organization_id=row["organization_id"],
        requested_by=row["requested_by"],
        status=row["status"],
        requested_at=row["requested_at"],
        finished_at=row["finished_at"],
        purge_after=row["purge_after"],
        detail=row["detail"],
        artifact_id=row["artifact_id"],
    )


def _deletion_job(row: asyncpg.Record) -> JobRow:
    return JobRow(
        id=row["id"],
        organization_id=row["organization_id"],
        requested_by=row["requested_by"],
        status=row["status"],
        requested_at=row["requested_at"],
        finished_at=row["finished_at"],
        purge_after=row["purge_after"],
        detail=row["detail"],
    )


class DataStore:
    """The export and deletion jobs of one deployment, and the work behind them."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- export -------------------------------------------------------------------

    async def request_export(self, *, organization_id: UUID, requested_by: UUID) -> JobRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO data_export_jobs (organization_id, requested_by) VALUES ($1, $2) "
                "RETURNING id, organization_id, requested_by, status, artifact_id, requested_at, "
                "finished_at, purge_after, detail",
                organization_id,
                requested_by,
            )
        return _export_job(row)

    async def exports(self, organization_id: UUID, *, limit: int = 20) -> tuple[JobRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, organization_id, requested_by, status, artifact_id, requested_at, "
                "finished_at, purge_after, detail FROM data_export_jobs "
                "WHERE organization_id = $1 ORDER BY requested_at DESC LIMIT $2",
                organization_id,
                limit,
            )
        return tuple(_export_job(row) for row in rows)

    async def export_job(self, *, organization_id: UUID, job_id: UUID) -> JobRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, organization_id, requested_by, status, artifact_id, requested_at, "
                "finished_at, purge_after, detail FROM data_export_jobs "
                "WHERE organization_id = $1 AND id = $2",
                organization_id,
                job_id,
            )
        return _export_job(row) if row is not None else None

    async def collect(self, organization_id: UUID) -> dict[str, Any]:
        """Everything this organization put into the product, as plain data.

        NO CREDENTIALS. `social_accounts` travels with its platform and handle and without
        anything that could be used to act as that account; `profile_deks` and the encrypted
        profile storage are not read here at all. An export is something a person may forward to
        a lawyer or a competitor by accident, and it must stay harmless when they do.
        """
        async with self._pool.acquire() as conn:
            organization = await conn.fetchrow(
                "SELECT id, name, created_at FROM organizations WHERE id = $1", organization_id
            )
            workspaces = await conn.fetch(
                "SELECT id, name, created_at FROM workspaces WHERE organization_id = $1 "
                "ORDER BY created_at",
                organization_id,
            )
            projects = await conn.fetch(
                "SELECT id, workspace_id, name, goal, mode, paused_at, created_at FROM projects "
                "WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            profiles = await conn.fetch(
                "SELECT id, workspace_id, name, kind, created_at FROM person_profiles "
                "WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            accounts = await conn.fetch(
                "SELECT id, platform, external_account_id, state, created_at FROM social_accounts "
                "WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            sources = await conn.fetch(
                "SELECT id, workspace_id, project_id, kind, locator, title, fetched_at, "
                "content_hash, status, unavailable_reason, created_at FROM knowledge_sources "
                "WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            items = await conn.fetch(
                "SELECT id, workspace_id, project_id, person_profile_id, kind, statement, "
                "source_id, stated_by, payload, valid_from, superseded_by, retracted_at, "
                "retracted_reason, created_at FROM knowledge_items "
                "WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            sessions = await conn.fetch(
                "SELECT id, workspace_id, project_id, goal, status, result_summary, created_at "
                "FROM agent_sessions WHERE organization_id = $1 ORDER BY created_at",
                organization_id,
            )
            events = await conn.fetch(
                "SELECT session_id, seq, kind, payload, created_at FROM session_events "
                "WHERE organization_id = $1 ORDER BY session_id, seq",
                organization_id,
            )
            publications = await conn.fetch(
                "SELECT id, workspace_id, status, target_url, published_at, queued_at, "
                "public_visibility, public_visibility_checked_at FROM publication_requests "
                "WHERE organization_id = $1 ORDER BY queued_at",
                organization_id,
            )

        def rows(records) -> list[dict[str, Any]]:
            return [{key: _plain(value) for key, value in record.items()} for record in records]

        return {
            "eksport": {
                "wersja": 1,
                "organizacja": _plain(organization["id"]) if organization else None,
                "nazwa": organization["name"] if organization else None,
                "utworzono": _plain(organization["created_at"]) if organization else None,
                "wygenerowano": datetime.now(UTC).isoformat(),
                "uwaga": (
                    "Ten plik zawiera dane tej organizacji bez żadnych poświadczeń do platform "
                    "i bez zawartości katalogów Codeksa. Dziennik audytu nie jest eksportowany, "
                    "bo jest tylko do dopisywania."
                ),
            },
            "workspaces": rows(workspaces),
            "projekty": rows(projects),
            "profile_osob": rows(profiles),
            "konta_platform": rows(accounts),
            "materialy": rows(sources),
            "ustalenia": rows(items),
            "sesje": rows(sessions),
            "slad_sesji": rows(events),
            "publikacje": rows(publications),
        }

    async def run_export(
        self, *, organization_id: UUID, job_id: UUID, artifacts=None, volume=None
    ) -> JobRow:
        """Build the file, store it as an artifact, and point the job at it.

        A failure is recorded on the row with its reason rather than raised into the queue: the
        person who asked for their data has to see what happened, and a failed job in a queue is
        not something they can read.
        """
        from control_plane.storage.artifacts import ArtifactStore
        from control_plane.storage.config import ArtifactAcl, ArtifactKind

        artifacts = artifacts or ArtifactStore()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE data_export_jobs SET status = 'RUNNING' WHERE id = $1 AND status = $2",
                job_id,
                STATUS_REQUESTED,
            )
        try:
            payload = await self.collect(organization_id)
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            workspace_id = await self._any_workspace(organization_id)
            async with self._pool.acquire() as conn, conn.transaction():
                now = await conn.fetchval("SELECT now()")
                record = await artifacts.record(
                    conn,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    kind=ArtifactKind.EXPORT,
                    acl=ArtifactAcl.OPERATOR_ONLY,
                    content=content,
                    encrypted=False,
                    retention_until=now + EXPORT_RETENTION,
                    volume=volume,
                )
                row = await conn.fetchrow(
                    "UPDATE data_export_jobs SET status = 'READY', artifact_id = $2, "
                    "finished_at = now(), purge_after = now() + $3::interval, detail = NULL "
                    "WHERE id = $1 RETURNING id, organization_id, requested_by, status, "
                    "artifact_id, requested_at, finished_at, purge_after, detail",
                    job_id,
                    record.artifact_id,
                    EXPORT_RETENTION,
                )
            return _export_job(row)
        except Exception as exc:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "UPDATE data_export_jobs SET status = 'FAILED', finished_at = now(), "
                    "detail = $2 WHERE id = $1 RETURNING id, organization_id, requested_by, "
                    "status, artifact_id, requested_at, finished_at, purge_after, detail",
                    job_id,
                    f"{type(exc).__name__}: {str(exc)[:400]}",
                )
            return _export_job(row)

    async def _any_workspace(self, organization_id: UUID) -> UUID:
        """`artifacts.workspace_id` is NOT NULL, so an export needs one. The oldest is the choice.

        An export is organization-wide and the column is workspace-wide; rather than widen the
        schema of a table five other things depend on, the file is filed under the first
        workspace and the payload says what it really covers.
        """
        async with self._pool.acquire() as conn:
            workspace_id = await conn.fetchval(
                "SELECT id FROM workspaces WHERE organization_id = $1 ORDER BY created_at LIMIT 1",
                organization_id,
            )
            if workspace_id is None:
                workspace_id = await conn.fetchval(
                    "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'Przestrzeń') "
                    "RETURNING id",
                    organization_id,
                )
        return workspace_id

    # -- deletion -----------------------------------------------------------------

    async def request_deletion(
        self, *, organization_id: UUID, requested_by: UUID, grace: timedelta = DELETION_GRACE
    ) -> JobRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO data_deletion_jobs (organization_id, requested_by, purge_after) "
                "VALUES ($1, $2, now() + $3::interval) "
                "RETURNING id, organization_id, requested_by, status, requested_at, finished_at, "
                "purge_after, detail",
                organization_id,
                requested_by,
                grace,
            )
        return _deletion_job(row)

    async def pending_deletion(self, organization_id: UUID) -> JobRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, organization_id, requested_by, status, requested_at, finished_at, "
                "purge_after, detail FROM data_deletion_jobs "
                "WHERE organization_id = $1 AND status = 'REQUESTED'",
                organization_id,
            )
        return _deletion_job(row) if row is not None else None

    async def cancel_deletion(self, *, organization_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE data_deletion_jobs SET status = 'CANCELLED', finished_at = now() "
                "WHERE organization_id = $1 AND status = 'REQUESTED' RETURNING id",
                organization_id,
            )
        return updated is not None

    async def destroy_secrets(self, *, organization_id: UUID, home_root: Path | None = None) -> int:
        """Everything this product holds that could be used to ACT as the customer. Immediately.

        Returns how many Codex directories were removed. The volume root (`home_ref = '.'`) is
        never touched: it is the deployment's own directory, shared with the live pilot, and a
        tenant's deletion has no business inside it.
        """
        import shutil

        removed = 0
        async with self._pool.acquire() as conn:
            homes = await conn.fetch(
                "SELECT home_ref FROM codex_connections WHERE organization_id = $1 "
                "AND home_ref <> '.'",
                organization_id,
            )
            await conn.execute(
                "UPDATE codex_connections SET disconnected_at = now() "
                "WHERE organization_id = $1 AND disconnected_at IS NULL",
                organization_id,
            )
            # CRYPTO-ERASURE, not a DELETE, and that is the mechanism the schema was built for
            # (0003, L3903): the wrapped key is destroyed and the profile bytes on the volume
            # become unreadable. Deleting the rows would leave those bytes with no record that
            # their key is gone, which reads as "somebody forgot" rather than as "erased".
            await conn.execute(
                "UPDATE profile_deks SET erased_at = now() "
                "WHERE organization_id = $1 AND erased_at IS NULL",
                organization_id,
            )

        if home_root is not None:
            for row in homes:
                directory = home_root / str(row["home_ref"])
                if directory.is_dir() and directory.resolve() != home_root.resolve():
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
        return removed

    async def purge(self, *, organization_id: UUID, now: datetime | None = None) -> JobRow | None:
        """Delete the organization's own rows, once the grace period has passed.

        Returns `None` when there is nothing due — a job still inside its grace period is not an
        error and not a no-op to log; it is simply not time yet.
        """
        moment = now or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            job = await conn.fetchrow(
                "SELECT id, organization_id, requested_by, status, requested_at, finished_at, "
                "purge_after, detail FROM data_deletion_jobs "
                "WHERE organization_id = $1 AND status = 'REQUESTED' AND purge_after <= $2",
                organization_id,
                moment,
            )
            if job is None:
                return None
            async with conn.transaction():
                for table in PURGE_ORDER:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE organization_id = $1", organization_id
                    )
                await conn.execute(
                    "UPDATE memberships SET revoked_at = now() WHERE organization_id = $1 "
                    "AND revoked_at IS NULL",
                    organization_id,
                )
                row = await conn.fetchrow(
                    "UPDATE data_deletion_jobs SET status = 'PURGED', finished_at = now() "
                    "WHERE id = $1 RETURNING id, organization_id, requested_by, status, "
                    "requested_at, finished_at, purge_after, detail",
                    job["id"],
                )
        return _deletion_job(row)

    async def due_deletions(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT organization_id FROM data_deletion_jobs "
                "WHERE status = 'REQUESTED' AND purge_after <= $1",
                now or datetime.now(UTC),
            )
        return tuple(row["organization_id"] for row in rows)


def _plain(value: Any) -> Any:
    """JSON-safe, and lossless for what this export carries."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return json.loads(value) if value.startswith(("{", "[")) else value
        except ValueError:
            return value
    return value
