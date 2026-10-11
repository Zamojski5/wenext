"""Resolve real signing identities and lock their access through the consent transaction."""

from uuid import UUID

import asyncpg


class SignerUnavailable(ValueError):
    """The identity no longer has active access to this organization."""


async def resolve_signer(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    operator_id: UUID | None = None,
    membership_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[UUID | None, UUID | None]:
    if user_id is not None or membership_id is not None:
        member = await conn.fetchval(
            "SELECT m.id FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.organization_id=$1 AND ($2::uuid IS NULL OR m.user_id=$2) "
            "AND ($3::uuid IS NULL OR m.id=$3) "
            "AND m.revoked_at IS NULL AND u.disabled_at IS NULL FOR SHARE OF m, u",
            organization_id,
            user_id,
            membership_id,
        )
        if member is None:
            raise SignerUnavailable("Brak aktywnego dostępu do tej organizacji.")
        return None, member
    if operator_id is None or not await conn.fetchval(
        "SELECT id FROM operators WHERE organization_id=$1 AND id=$2 "
        "AND disabled_at IS NULL FOR SHARE",
        organization_id,
        operator_id,
    ):
        raise SignerUnavailable("Brak aktywnego podpisu operatora.")
    return operator_id, None
