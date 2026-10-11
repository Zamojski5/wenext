"""Providing a hosted person with a project: by invitation, or on their first signed request.

Two doors lead to the same room. `invite create` (administrator, token in the URL) and the edge
(`POST /mcp` with a signed identity, no token) both end in ONE organization with ONE workspace and
ONE project, plus the placeholder accounts the draft path needs. This module is that shared part.

THE PLACEHOLDER ACCOUNTS. A draft is stored against an account of the project
(`draft.create`, the approval chain's `social_account_id`), and a hosted project has no login of
its own: the person posts every reply by hand. So each project gets one account per platform with
a fixed handle and state PROVISIONED (nobody is signed in to it). Without them the first draft
would be refused with "no account", which is a setup problem on our side. The handles differ per
platform on purpose: `draft.create` finds the account by its handle alone, and one handle on two
platforms would be an ambiguous account.

AUTO-PROVISIONING IS IDEMPOTENT UNDER PARALLEL FIRST CALLS. An agent opens several requests at
once on its first connection, and every one of them arrives with the same unknown (provider,
subject). The creation runs in ONE transaction under an advisory lock keyed on that pair, and
re-reads the pair inside the lock: the first request creates everything, the others wait, find the
row and return it. Without the lock they would each create an organization and the primary key of
`external_identities` would refuse all but one, leaving the others' organizations orphaned.

WHAT AN EXTERNAL IDENTITY IS NOT: a user. No `users` row, no membership, no password: the person
comes in through their agent and the edge's sign-in, not through the dashboard.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from .edge import EdgeIdentity
from .tokens import HostedAccess

#: One handle per platform. See the module docstring.
PLACEHOLDER_ACCOUNTS = {"REDDIT": "hosted-reddit", "X": "hosted-x"}

#: What a person's project is called until the agent renames it (`project.update`).
DEFAULT_PROJECT_NAME = "My project"

#: Hosted projects review and do not run on their own: nothing in this deployment publishes, and
#: the person sends every reply by hand.
PROJECT_MODE = "REVIEW"

#: `last_seen_at` is refreshed at most this often.
TOUCH_EVERY_SECONDS = 60


class IdentityDisabled(Exception):
    """The identity exists and the operator switched it off (or its project is archived)."""


async def add_workspace_and_project(
    conn: asyncpg.Connection, *, organization_id: UUID, name: str
) -> tuple[UUID, UUID]:
    """A workspace, a project in review mode and the placeholder accounts, on the caller's
    connection (so the caller decides what else belongs in the same transaction)."""
    workspace_id = await conn.fetchval(
        "INSERT INTO workspaces (organization_id, name) VALUES ($1, $2) RETURNING id",
        organization_id,
        name,
    )
    project_id = await conn.fetchval(
        "INSERT INTO projects (organization_id, workspace_id, name, mode)"
        " VALUES ($1, $2, $3, $4) RETURNING id",
        organization_id,
        workspace_id,
        name,
        PROJECT_MODE,
    )
    for platform, handle in PLACEHOLDER_ACCOUNTS.items():
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id)"
            " VALUES ($1, $2, $3) ON CONFLICT (organization_id, platform, external_account_id)"
            " DO UPDATE SET platform = EXCLUDED.platform RETURNING id",
            organization_id,
            platform,
            handle,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id,"
            " social_account_id) VALUES ($1, $2, $3, $4)",
            organization_id,
            workspace_id,
            project_id,
            account_id,
        )
    return workspace_id, project_id


def digest_of_identity(provider: str, subject: str) -> str:
    """The cache and limit key of an identity: a hash, so no id ever reaches a log line."""
    return hashlib.sha256(f"edge:{provider}\x1f{subject}".encode()).hexdigest()


@dataclass(frozen=True)
class IdentityRow:
    provider: str
    subject: str
    organization_id: UUID
    project_id: UUID
    email: str | None
    display_name: str | None
    disabled: bool


class ExternalIdentityStore:
    """`external_identities`: find a person's project, creating it on their first appearance."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def resolve_or_provision(self, identity: EdgeIdentity) -> HostedAccess:
        """The project of this identity, created when the identity is new.

        Raises `IdentityDisabled` for a switched-off identity or an archived project.
        """
        found = await self._lookup(identity)
        if found is not None:
            return found
        return await self._provision(identity)

    async def _lookup(self, identity: EdgeIdentity) -> HostedAccess | None:
        row = await self._pool.fetchrow(
            "SELECT i.organization_id, i.workspace_id, i.project_id, i.disabled_at,"
            " p.archived_at,"
            " (i.last_seen_at IS NULL OR i.last_seen_at < now() - make_interval(secs => $3))"
            "   AS stale"
            "  FROM external_identities i"
            "  JOIN projects p ON p.organization_id = i.organization_id"
            "   AND p.workspace_id = i.workspace_id AND p.id = i.project_id"
            " WHERE i.provider = $1 AND i.subject = $2",
            identity.provider,
            identity.subject,
            float(TOUCH_EVERY_SECONDS),
        )
        if row is None:
            return None
        if row["disabled_at"] is not None or row["archived_at"] is not None:
            raise IdentityDisabled("identity disabled")
        if row["stale"]:
            await self._pool.execute(
                "UPDATE external_identities SET last_seen_at = now(), email = coalesce($3, email),"
                " display_name = coalesce($4, display_name)"
                " WHERE provider = $1 AND subject = $2",
                identity.provider,
                identity.subject,
                identity.email,
                identity.login,
            )
        return self._access(identity, row)

    @staticmethod
    def _access(identity: EdgeIdentity, row: asyncpg.Record) -> HostedAccess:
        return HostedAccess(
            digest=digest_of_identity(identity.provider, identity.subject),
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
            label=identity.email or identity.login or identity.subject,
        )

    async def _provision(self, identity: EdgeIdentity) -> HostedAccess:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"external-identity:{identity.provider}\x1f{identity.subject}",
            )
            # Again, INSIDE the lock: another request may have created it while this one waited.
            row = await conn.fetchrow(
                "SELECT organization_id, workspace_id, project_id, disabled_at FROM"
                " external_identities WHERE provider = $1 AND subject = $2",
                identity.provider,
                identity.subject,
            )
            if row is not None:
                if row["disabled_at"] is not None:
                    raise IdentityDisabled("identity disabled")
                return self._access(identity, row)
            title = (identity.login or identity.email or "Wenext user")[:100]
            organization_id = await conn.fetchval(
                "INSERT INTO organizations (name) VALUES ($1) RETURNING id", title
            )
            workspace_id, project_id = await add_workspace_and_project(
                conn, organization_id=organization_id, name=DEFAULT_PROJECT_NAME
            )
            await conn.execute(
                "INSERT INTO external_identities (provider, subject, organization_id,"
                " workspace_id, project_id, email, display_name, last_seen_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, now())",
                identity.provider,
                identity.subject,
                organization_id,
                workspace_id,
                project_id,
                identity.email,
                identity.login,
            )
        return HostedAccess(
            digest=digest_of_identity(identity.provider, identity.subject),
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            label=identity.email or identity.login or identity.subject,
        )

    # -- the operator's side ----------------------------------------------------------

    async def list_identities(self) -> tuple[IdentityRow, ...]:
        rows = await self._pool.fetch(
            "SELECT provider, subject, organization_id, project_id, email, display_name,"
            " disabled_at FROM external_identities ORDER BY created_at, provider, subject"
        )
        return tuple(
            IdentityRow(
                provider=row["provider"],
                subject=row["subject"],
                organization_id=row["organization_id"],
                project_id=row["project_id"],
                email=row["email"],
                display_name=row["display_name"],
                disabled=row["disabled_at"] is not None,
            )
            for row in rows
        )

    async def disable(self, *, subject: str, provider: str | None = None) -> int:
        """Switch an identity off by its subject (and provider, when two providers share one)."""
        rows = await self._pool.fetch(
            "UPDATE external_identities SET disabled_at = now()"
            " WHERE subject = $1 AND ($2::text IS NULL OR provider = $2) AND disabled_at IS NULL"
            " RETURNING subject",
            subject.strip(),
            provider,
        )
        return len(rows)
