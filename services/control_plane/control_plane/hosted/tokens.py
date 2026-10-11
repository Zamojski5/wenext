"""Personal tokens of the hosted MCP endpoint: minted once, stored as a hash, found by that hash.

THE TOKEN IS THE KEY TO ONE PROJECT. `https://<domain>/mcp/<token>` is all a stranger's agent
holds, so everything here is about the token not leaking out of the places that must know it:

  * it is shown ONCE, by `invite create`, and never stored: the table keeps its SHA-256
    (`hosted_tokens.token_sha256`, migration 0037). A copy of the database or a query log gives
    nobody an entry to anyone's project;
  * it has 256 bits of randomness (`secrets.token_urlsafe(32)`), which is why a plain SHA-256
    without a salt is enough: there is nothing to guess and nothing a dictionary could crack;
  * a token of the wrong SHAPE never reaches the database (`looks_like_a_token`), so a scanner
    walking `/mcp/<anything>` costs us a regex and not a query;
  * nothing in this module logs, prints or formats a token into an exception. Callers hold the
    token only as long as they need to hash it.

REVOCATION IS `revoked_at`, not DELETE: the row stays as the record that the access existed, and
a revoked token can never start working again by accident.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

#: A visible prefix so a leaked token is recognisable to secret scanners and to its owner.
TOKEN_PREFIX = "wnx_"

#: `secrets.token_urlsafe(32)` is 43 characters of base64url.
_TOKEN_SHAPE = re.compile(r"^wnx_[A-Za-z0-9_-]{43}$")

#: `last_used_at` is refreshed at most this often, so a read is not a write on every request.
TOUCH_EVERY_SECONDS = 60

#: The shortest hash prefix `revoke` accepts: enough to pick one row out of a few thousand, short
#: enough to read off the `list` output. An ambiguous prefix is refused, never guessed.
MIN_PREFIX = 8


class TokenRefused(Exception):
    """The operator asked for something the table cannot do, in a sentence that says why."""


def mint_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def digest_of(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_a_token(candidate: str) -> bool:
    return bool(_TOKEN_SHAPE.fullmatch(candidate))


@dataclass(frozen=True)
class HostedAccess:
    """What a live token opens: one project, and nothing else."""

    digest: str
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    label: str


@dataclass(frozen=True)
class TokenRow:
    """One row of the operator's list. The digest prefix, never the token (nobody has it)."""

    digest: str
    organization_id: UUID
    project_id: UUID
    label: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None

    @property
    def live(self) -> bool:
        return self.revoked_at is None

    @property
    def short(self) -> str:
        return self.digest[:MIN_PREFIX]


class HostedTokenStore:
    """The `hosted_tokens` table. One pool, no cache: a revoked token is refused at once."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        label: str,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        """Mint a token for the project and return it. This return value is the ONLY copy.

        `conn` puts the insert on the caller's connection, so a token and the project it opens can
        be committed (or not) together.
        """
        token = mint_token()
        await (conn or self._pool).execute(
            "INSERT INTO hosted_tokens (token_sha256, organization_id, workspace_id, project_id,"
            " label) VALUES ($1, $2, $3, $4, $5)",
            digest_of(token),
            organization_id,
            workspace_id,
            project_id,
            label[:320],
        )
        return token

    async def lookup(self, token: str) -> HostedAccess | None:
        """The project this token opens, or `None` for an unknown, revoked or malformed one.

        An archived project opens nothing either: the founder archived it, and an agent that
        still held the address would be writing into a project nobody looks at.
        """
        if not looks_like_a_token(token):
            return None
        digest = digest_of(token)
        row = await self._pool.fetchrow(
            "SELECT t.organization_id, t.workspace_id, t.project_id, t.label, t.last_used_at,"
            " (t.last_used_at IS NULL OR t.last_used_at < now() - make_interval(secs => $2))"
            "   AS stale"
            "  FROM hosted_tokens t"
            "  JOIN projects p ON p.organization_id = t.organization_id"
            "   AND p.workspace_id = t.workspace_id AND p.id = t.project_id"
            " WHERE t.token_sha256 = $1 AND t.revoked_at IS NULL AND p.archived_at IS NULL",
            digest,
            float(TOUCH_EVERY_SECONDS),
        )
        if row is None:
            return None
        if row["stale"]:
            await self._pool.execute(
                "UPDATE hosted_tokens SET last_used_at = now() WHERE token_sha256 = $1", digest
            )
        return HostedAccess(
            digest=digest,
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
            label=row["label"],
        )

    async def list_tokens(self) -> tuple[TokenRow, ...]:
        rows = await self._pool.fetch(
            "SELECT token_sha256, organization_id, project_id, label, created_at, revoked_at,"
            " last_used_at FROM hosted_tokens ORDER BY created_at, token_sha256"
        )
        return tuple(
            TokenRow(
                digest=row["token_sha256"],
                organization_id=row["organization_id"],
                project_id=row["project_id"],
                label=row["label"],
                created_at=row["created_at"],
                revoked_at=row["revoked_at"],
                last_used_at=row["last_used_at"],
            )
            for row in rows
        )

    async def revoke_prefix(self, prefix: str) -> int:
        """Revoke the one live token whose digest starts with `prefix` (as `list` prints it)."""
        wanted = prefix.strip().lower()
        if len(wanted) < MIN_PREFIX or not re.fullmatch(r"[0-9a-f]+", wanted):
            raise TokenRefused(
                f"identyfikator tokenu to co najmniej {MIN_PREFIX} znaków hex z kolumny `id` w "
                "wyniku `list`"
            )
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT token_sha256 FROM hosted_tokens"
                " WHERE starts_with(token_sha256, $1) AND revoked_at IS NULL FOR UPDATE",
                wanted,
            )
            if not rows:
                raise TokenRefused(f"nie ma żywego tokenu o identyfikatorze {wanted!r}")
            if len(rows) > 1:
                raise TokenRefused(
                    f"identyfikator {wanted!r} pasuje do {len(rows)} tokenów; podaj dłuższy"
                )
            await conn.execute(
                "UPDATE hosted_tokens SET revoked_at = now() WHERE token_sha256 = $1",
                rows[0]["token_sha256"],
            )
        return 1

    async def revoke_label(self, label: str) -> int:
        """Revoke every live token issued under this label (the invitation's e-mail)."""
        revoked = await self._pool.fetch(
            "UPDATE hosted_tokens SET revoked_at = now() WHERE lower(label) = lower($1)"
            " AND revoked_at IS NULL RETURNING token_sha256",
            label.strip(),
        )
        if not revoked:
            raise TokenRefused(f"nie ma żywego tokenu z etykietą {label!r}")
        return len(revoked)
