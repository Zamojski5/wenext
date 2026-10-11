"""Users, organizations they belong to, and invitations (P7 etap A).

WHAT CHANGES WITH THIS MODULE AND WHAT DOES NOT. Until now the product had one operator per
organization and, in practice, one organization; the boundary was "do you know the password".
It is now "which user are you, and do you still belong to this organization". The boundary
itself does not change shape: a workspace of another organization still answers 404, never 403,
because a 403 confirms that the identifier is real.

FOUR RULES THIS MODULE KEEPS, each one a way the multi-tenant version could leak:

  1. **membership is checked per request, not per login.** `active_membership` is called on every
     request that carries a user session. A revoked member with an eight-hour cookie would
     otherwise keep working for eight hours, and revoking access is exactly the thing somebody
     does in a hurry;
  2. **the email is the identity, so it is stored lowercase and compared lowercase.** The schema
     has the CHECK; this module lowercases before writing. "Anna@firma.pl" and "anna@firma.pl"
     as two accounts is one person who cannot see their own data;
  3. **an invitation token is never stored.** Only its SHA-256, like the session cookie. A
     database leak then hands out no way into anybody's organization;
  4. **nothing here takes an organization from the caller.** Every function receives the
     organization from a session that was built from a database row, and the queries scope by it.

THE FOUNDER'S ACCOUNT HAS NO EMAIL and that is a state, not a defect: migration 0018 moved the
single `operators` row to `users` with `email IS NULL`, because nobody ever recorded which
person it belongs to. `needs_email` says so out loud and the interface asks; inventing an address
would put made-up data into the table that decides who can log in.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError

#: How long an invitation link works. A week: long enough for somebody to read their mail after
#: a holiday, short enough that a link forwarded into a group chat stops being a key.
INVITATION_TTL = timedelta(days=7)

#: Upper bound on a presented password, same reason as in `auth.py`: an unauthenticated caller
#: must not get a free multi-megabyte allocation.
MAX_CREDENTIAL_LENGTH = 1024

MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 200

ROLE_OWNER = "OWNER"
ROLE_MEMBER = "MEMBER"
ROLES = (ROLE_OWNER, ROLE_MEMBER)

_HASHER = PasswordHasher()


class IdentityRefused(Exception):
    """A refusal with a sentence meant to be shown to the person (D-010)."""


@dataclass(frozen=True)
class UserRow:
    id: UUID
    email: str | None
    created_at: datetime
    disabled_at: datetime | None
    last_login_at: datetime | None

    @property
    def needs_email(self) -> bool:
        """The founder's migrated account, before anybody said whose it is."""
        return self.email is None


@dataclass(frozen=True)
class MembershipRow:
    id: UUID
    user_id: UUID
    organization_id: UUID
    organization_name: str
    role: str
    created_at: datetime
    revoked_at: datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class InvitationRow:
    id: UUID
    organization_id: UUID
    email: str
    expires_at: datetime
    accepted_at: datetime | None
    created_by: UUID
    created_at: datetime

    def is_live(self, now: datetime | None = None) -> bool:
        return self.accepted_at is None and (now or datetime.now(UTC)) < self.expires_at


def hash_password(password: str) -> str:
    """Argon2id, in the form both `users` and `operators` accept."""
    if not password or len(password) > MAX_CREDENTIAL_LENGTH:
        raise IdentityRefused(
            "hasło musi mieć od jednego znaku do tysiąca; puste hasło nie jest poświadczeniem"
        )
    return _HASHER.hash(password)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalised_email(email: str) -> str:
    """Lowercase, trimmed, and REFUSED when it is not an address.

    The check is deliberately shallow (one `@`, a dot after it, no spaces): an address is proven
    by a message arriving, not by a regular expression, and a strict pattern here would reject
    real addresses while proving nothing about the fake ones.
    """
    value = (email or "").strip().lower()
    if len(value) > MAX_EMAIL_LENGTH:
        raise IdentityRefused("adres jest dłuższy niż 320 znaków, czyli niż dopuszcza standard")
    # EXACTLY one `@`, because `partition` splits on the first one and would happily accept
    # "dwie@@malpy.test" as local "dwie" and domain "@malpy.test". The schema's CHECK refuses
    # that value, and this layer has to agree with the schema rather than lean on it.
    if value.count("@") != 1:
        raise IdentityRefused(f"{email!r} nie wygląda na adres e-mail")
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain or any(ch.isspace() for ch in value):
        raise IdentityRefused(f"{email!r} nie wygląda na adres e-mail")
    return value


def _user(row: asyncpg.Record) -> UserRow:
    return UserRow(
        id=row["id"],
        email=row["email"],
        created_at=row["created_at"],
        disabled_at=row["disabled_at"],
        last_login_at=row["last_login_at"],
    )


def _membership(row: asyncpg.Record) -> MembershipRow:
    return MembershipRow(
        id=row["id"],
        user_id=row["user_id"],
        organization_id=row["organization_id"],
        organization_name=row["organization_name"],
        role=row["role"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


_MEMBERSHIP_COLUMNS = """
    m.id, m.user_id, m.organization_id, m.role, m.created_at, m.revoked_at,
    o.name AS organization_name
"""


class IdentityStore:
    """Everything about who may act where. One pool, no cache, no cross-organization read."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- registration and login ---------------------------------------------------

    async def register_organization(
        self, *, email: str, password: str, organization_name: str
    ) -> tuple[UserRow, MembershipRow]:
        """A new organization with its first user as OWNER. The self-service front door.

        ONE TRANSACTION for all three rows. A user without an organization cannot log in
        anywhere, and an organization without a member is a tenant nobody can reach; either half
        alone is a row somebody would have to clean up by hand, which is the thing this milestone
        exists to stop needing.
        """
        address = normalised_email(email)
        name = (organization_name or "").strip()
        if not name:
            raise IdentityRefused("przestrzeń musi mieć nazwę")
        if len(name) > MAX_NAME_LENGTH:
            raise IdentityRefused(f"nazwa przestrzeni jest dłuższa niż {MAX_NAME_LENGTH} znaków")
        digest = hash_password(password)

        async with self._pool.acquire() as conn, conn.transaction():
            taken = await conn.fetchval("SELECT 1 FROM users WHERE email = $1", address)
            if taken:
                # Deliberately the same sentence a person gets when they mistype an existing
                # address: this route is open to the internet, and "this email is not registered"
                # is a free account oracle.
                raise IdentityRefused(
                    "nie udało się założyć przestrzeni dla tego adresu. Jeśli masz już konto, "
                    "zaloguj się"
                )
            organization_id = await conn.fetchval(
                "INSERT INTO organizations (name) VALUES ($1) RETURNING id", name
            )
            user = _user(
                await conn.fetchrow(
                    "INSERT INTO users (email, credential_hash) VALUES ($1, $2) "
                    "RETURNING id, email, created_at, disabled_at, last_login_at",
                    address,
                    digest,
                )
            )
            membership = _membership(
                await conn.fetchrow(
                    f"""
                    WITH inserted AS (
                        INSERT INTO memberships (user_id, organization_id, role)
                        VALUES ($1, $2, 'OWNER')
                        RETURNING id, user_id, organization_id, role, created_at, revoked_at
                    )
                    SELECT {_MEMBERSHIP_COLUMNS} FROM inserted m
                    JOIN organizations o ON o.id = m.organization_id
                    """,
                    user.id,
                    organization_id,
                )
            )
        return user, membership

    async def verify_login(self, *, email: str, password: str) -> UserRow | None:
        """The user this address and password name, or None. Never says which half was wrong.

        A disabled user and a wrong password are the same answer here, on purpose.
        """
        if not password or len(password) > MAX_CREDENTIAL_LENGTH:
            return None
        try:
            address = normalised_email(email)
        except IdentityRefused:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, credential_hash, created_at, disabled_at, last_login_at "
                "FROM users WHERE email = $1 AND disabled_at IS NULL",
                address,
            )
        if row is None:
            return None
        try:
            _HASHER.verify(row["credential_hash"], password)
        except (VerificationError, InvalidHash):
            return None
        return _user(row)

    async def verify_password_of(self, user_id: UUID, password: str) -> bool:
        """Used where an action needs the password again (deleting an organization)."""
        async with self._pool.acquire() as conn:
            digest = await conn.fetchval(
                "SELECT credential_hash FROM users WHERE id = $1 AND disabled_at IS NULL", user_id
            )
        if digest is None:
            return False
        try:
            _HASHER.verify(digest, password)
        except (VerificationError, InvalidHash):
            return False
        return True

    async def record_login(self, user_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)

    async def user(self, user_id: UUID) -> UserRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, created_at, disabled_at, last_login_at FROM users WHERE id = $1",
                user_id,
            )
        return _user(row) if row is not None else None

    async def set_email(self, user_id: UUID, email: str) -> UserRow:
        """The founder's migrated account getting an address for the first time."""
        address = normalised_email(email)
        async with self._pool.acquire() as conn:
            taken = await conn.fetchval(
                "SELECT 1 FROM users WHERE email = $1 AND id <> $2", address, user_id
            )
            if taken:
                raise IdentityRefused("ten adres należy już do innego konta")
            row = await conn.fetchrow(
                "UPDATE users SET email = $2 WHERE id = $1 "
                "RETURNING id, email, created_at, disabled_at, last_login_at",
                user_id,
                address,
            )
        if row is None:
            raise IdentityRefused("nie ma takiego konta")
        return _user(row)

    # -- membership ---------------------------------------------------------------

    async def memberships_of(self, user_id: UUID) -> tuple[MembershipRow, ...]:
        """Every ACTIVE membership of this user, oldest first. The oldest is the default scope."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_MEMBERSHIP_COLUMNS}
                FROM memberships m
                JOIN organizations o ON o.id = m.organization_id
                WHERE m.user_id = $1 AND m.revoked_at IS NULL
                ORDER BY m.created_at, m.id
                """,
                user_id,
            )
        return tuple(_membership(row) for row in rows)

    async def active_membership(
        self, *, user_id: UUID, organization_id: UUID
    ) -> MembershipRow | None:
        """THE access check, asked on every request that carries a user session.

        Also refuses while a deletion of this organization is pending: the milestone asks that
        requesting deletion block sign-in immediately, and "immediately" cannot mean "when the
        cookie expires".
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_MEMBERSHIP_COLUMNS}
                FROM memberships m
                JOIN organizations o ON o.id = m.organization_id
                JOIN users u ON u.id = m.user_id
                WHERE m.user_id = $1 AND m.organization_id = $2
                  AND m.revoked_at IS NULL AND u.disabled_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM data_deletion_jobs d
                       WHERE d.organization_id = m.organization_id AND d.status = 'REQUESTED'
                  )
                """,
                user_id,
                organization_id,
            )
        return _membership(row) if row is not None else None

    async def members(self, organization_id: UUID) -> tuple[MembershipRow, ...]:
        """Everybody who belongs here, revoked ones included: history is the point of the column."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_MEMBERSHIP_COLUMNS}, u.email AS user_email
                FROM memberships m
                JOIN organizations o ON o.id = m.organization_id
                JOIN users u ON u.id = m.user_id
                WHERE m.organization_id = $1
                ORDER BY m.created_at, m.id
                """,
                organization_id,
            )
        return tuple(_membership(row) for row in rows)

    async def emails_of(self, organization_id: UUID) -> dict[UUID, str | None]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT u.id, u.email FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE m.organization_id = $1",
                organization_id,
            )
        return {row["id"]: row["email"] for row in rows}

    async def revoke_membership(
        self, *, organization_id: UUID, user_id: UUID, acting_user_id: UUID
    ) -> bool:
        """Take somebody's access away. The row stays, with the moment it ended.

        REFUSES to remove the last owner. An organization with no owner is one nobody can invite
        into, export from or delete, and the only way back would be a hand-edited database —
        which is the thing this milestone exists to stop needing.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT role FROM memberships WHERE organization_id = $1 AND user_id = $2 "
                "AND revoked_at IS NULL FOR UPDATE",
                organization_id,
                user_id,
            )
            if row is None:
                return False
            if row["role"] == ROLE_OWNER:
                owners = await conn.fetchval(
                    "SELECT count(*) FROM memberships WHERE organization_id = $1 "
                    "AND role = 'OWNER' AND revoked_at IS NULL",
                    organization_id,
                )
                if owners <= 1:
                    raise IdentityRefused(
                        "to jest ostatni właściciel tej przestrzeni. Najpierw uczyń kogoś innego "
                        "właścicielem, inaczej zostanie przestrzeń, do której nikt nie ma dostępu"
                    )
            await conn.execute(
                "UPDATE memberships SET revoked_at = now() WHERE organization_id = $1 "
                "AND user_id = $2 AND revoked_at IS NULL",
                organization_id,
                user_id,
            )
        return True

    # -- invitations --------------------------------------------------------------

    async def invite(
        self,
        *,
        organization_id: UUID,
        email: str,
        created_by: UUID,
        ttl: timedelta = INVITATION_TTL,
    ) -> tuple[InvitationRow, str]:
        """An invitation and its ONE-TIME token. The token is returned, never stored.

        Etap A shows the link to the person who invited, for them to pass on. Sending mail is a
        separate decision with its own failure modes (deliverability, spoofing, a queue nobody
        watches), and pretending to have solved it by writing `TODO: send email` would be worse
        than saying it plainly on the screen.
        """
        address = normalised_email(email)
        token = secrets.token_urlsafe(32)
        async with self._pool.acquire() as conn, conn.transaction():
            member = await conn.fetchval(
                "SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.organization_id = $1 AND u.email = $2 AND m.revoked_at IS NULL",
                organization_id,
                address,
            )
            if member:
                raise IdentityRefused("ta osoba już należy do tej przestrzeni")
            await conn.execute(
                "DELETE FROM invitations WHERE organization_id = $1 AND email = $2 "
                "AND accepted_at IS NULL",
                organization_id,
                address,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO invitations
                    (organization_id, email, token_hash, expires_at, created_by)
                VALUES ($1, $2, $3, now() + $4::interval, $5)
                RETURNING id, organization_id, email, expires_at, accepted_at, created_by,
                          created_at
                """,
                organization_id,
                address,
                token_digest(token),
                ttl,
                created_by,
            )
        return (
            InvitationRow(
                id=row["id"],
                organization_id=row["organization_id"],
                email=row["email"],
                expires_at=row["expires_at"],
                accepted_at=row["accepted_at"],
                created_by=row["created_by"],
                created_at=row["created_at"],
            ),
            token,
        )

    async def invitations(self, organization_id: UUID) -> tuple[InvitationRow, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, organization_id, email, expires_at, accepted_at, created_by, "
                "created_at FROM invitations WHERE organization_id = $1 ORDER BY created_at DESC",
                organization_id,
            )
        return tuple(
            InvitationRow(
                id=row["id"],
                organization_id=row["organization_id"],
                email=row["email"],
                expires_at=row["expires_at"],
                accepted_at=row["accepted_at"],
                created_by=row["created_by"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    async def accept_invitation(
        self, *, token: str, password: str
    ) -> tuple[UserRow, MembershipRow]:
        """Turn a live invitation into a membership, creating the account when it is new.

        The password is set HERE, by the person accepting, and never chosen by whoever invited
        them: an invitation that carried a password would be a credential travelling in a link.
        """
        digest = token_digest(token or "")
        async with self._pool.acquire() as conn, conn.transaction():
            invitation = await conn.fetchrow(
                "SELECT id, organization_id, email, expires_at FROM invitations "
                "WHERE token_hash = $1 AND accepted_at IS NULL FOR UPDATE",
                digest,
            )
            if invitation is None:
                raise IdentityRefused(
                    "to zaproszenie nie istnieje albo zostało już przyjęte. Poproś o nowe"
                )
            if invitation["expires_at"] <= datetime.now(UTC):
                raise IdentityRefused("to zaproszenie straciło ważność. Poproś o nowe")

            existing = await conn.fetchrow(
                "SELECT id, email, created_at, disabled_at, last_login_at FROM users "
                "WHERE email = $1",
                invitation["email"],
            )
            if existing is None:
                user = _user(
                    await conn.fetchrow(
                        "INSERT INTO users (email, credential_hash) VALUES ($1, $2) "
                        "RETURNING id, email, created_at, disabled_at, last_login_at",
                        invitation["email"],
                        hash_password(password),
                    )
                )
            else:
                # An account that already exists keeps its password: an invitation must not be a
                # way to set somebody else's credential.
                user = _user(existing)

            membership = _membership(
                await conn.fetchrow(
                    f"""
                    WITH inserted AS (
                        INSERT INTO memberships (user_id, organization_id, role, invited_by)
                        VALUES ($1, $2, 'MEMBER', $3)
                        ON CONFLICT (user_id, organization_id) WHERE revoked_at IS NULL
                        DO UPDATE SET role = memberships.role
                        RETURNING id, user_id, organization_id, role, created_at, revoked_at
                    )
                    SELECT {_MEMBERSHIP_COLUMNS} FROM inserted m
                    JOIN organizations o ON o.id = m.organization_id
                    """,
                    user.id,
                    invitation["organization_id"],
                    invitation["id"]
                    and (
                        await conn.fetchval(
                            "SELECT created_by FROM invitations WHERE id = $1", invitation["id"]
                        )
                    ),
                )
            )
            await conn.execute(
                "UPDATE invitations SET accepted_at = now() WHERE id = $1", invitation["id"]
            )
        return user, membership

    async def organization(self, organization_id: UUID) -> tuple[UUID, str] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM organizations WHERE id = $1", organization_id
            )
        return (row["id"], row["name"]) if row is not None else None
