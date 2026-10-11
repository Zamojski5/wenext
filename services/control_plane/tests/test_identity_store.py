"""`control_plane.identity` — users, memberships, invitations, against PostgreSQL (P7 etap A).

WHAT IS BEING PROTECTED. Not "the rows are written": the four ways a second customer could end
up sharing something with the first, plus the two ways a person could be locked out of their own
data:

  * an address that differs only in case becomes a second account, and one person then has two
    halves of their own space;
  * a revoked member keeps working until their cookie expires, which is hours after somebody
    decided otherwise;
  * an invitation token is guessable, reusable or outlives its purpose;
  * the last owner of an organization is removed and nobody can invite, export or delete it
    afterwards — a state only a hand-edited database gets out of.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from control_plane.identity.store import (
    IdentityRefused,
    IdentityStore,
    normalised_email,
    token_digest,
)

pytestmark = pytest.mark.db


@pytest.fixture
def store(pool) -> IdentityStore:
    return IdentityStore(pool)


async def _org(store: IdentityStore, *, email: str, name: str = "Firma"):
    return await store.register_organization(
        email=email, password="dobre-haslo-123", organization_name=name
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_registering_creates_the_space_the_account_and_the_ownership(store) -> None:
    user, membership = await _org(store, email="Ala@Firma.TEST", name="Przestrzeń Ali")

    assert user.email == "ala@firma.test", "adres nie został znormalizowany do małych liter"
    assert membership.role == "OWNER"
    assert membership.organization_name == "Przestrzeń Ali"
    assert not user.needs_email


async def test_a_taken_address_is_refused_without_saying_that_it_is_taken(store) -> None:
    """This route is open to the internet; "this email exists" would be a free account oracle."""
    await _org(store, email="zajete@firma.test")
    with pytest.raises(IdentityRefused) as refused:
        await _org(store, email="zajete@firma.test")
    assert "zajęt" not in str(refused.value).lower()
    assert "zaloguj się" in str(refused.value)


async def test_a_space_without_a_name_is_refused(store) -> None:
    with pytest.raises(IdentityRefused):
        await store.register_organization(
            email="bez@nazwy.test", password="dobre-haslo-123", organization_name="   "
        )


@pytest.mark.parametrize("address", ["bez-malpy", "dwie@@malpy.test", "spacja w@adresie.test", ""])
def test_an_address_that_is_not_one_is_refused_before_the_database(address: str) -> None:
    with pytest.raises(IdentityRefused):
        normalised_email(address)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_accepts_the_password_and_refuses_everything_else(store) -> None:
    user, _ = await _org(store, email="login@firma.test")

    assert (await store.verify_login(email="LOGIN@firma.test", password="dobre-haslo-123")).id == (
        user.id
    ), "logowanie musi działać niezależnie od wielkości liter w adresie"
    assert await store.verify_login(email="login@firma.test", password="złe") is None
    assert await store.verify_login(email="kogos@innego.test", password="dobre-haslo-123") is None


async def test_a_disabled_account_cannot_log_in(store, pool) -> None:
    user, _ = await _org(store, email="wylaczony@firma.test")
    async with pool.acquire() as connection:
        await connection.execute("UPDATE users SET disabled_at = now() WHERE id = $1", user.id)

    assert (
        await store.verify_login(email="wylaczony@firma.test", password="dobre-haslo-123") is None
    )


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


async def test_membership_is_the_access_check_and_it_ends_when_the_membership_does(store) -> None:
    owner, membership = await _org(store, email="wlasciciel@firma.test")
    second, _ = await _org(store, email="obcy@firma.test", name="Inna firma")

    assert (
        await store.active_membership(user_id=owner.id, organization_id=membership.organization_id)
        is not None
    )
    # A user of another organization has no membership here. This is the whole tenant boundary.
    assert (
        await store.active_membership(user_id=second.id, organization_id=membership.organization_id)
        is None
    )


async def test_a_pending_deletion_blocks_access_at_once(store, pool) -> None:
    """The milestone asks that deletion block sign-in immediately, not when the cookie expires."""
    owner, membership = await _org(store, email="kasowany@firma.test")
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO data_deletion_jobs (organization_id, requested_by, purge_after) "
            "VALUES ($1, $2, now() + interval '7 days')",
            membership.organization_id,
            owner.id,
        )

    assert (
        await store.active_membership(user_id=owner.id, organization_id=membership.organization_id)
        is None
    )


async def test_the_last_owner_cannot_be_removed(store) -> None:
    """An organization with no owner can be neither invited into, exported nor deleted."""
    owner, membership = await _org(store, email="jedyny@firma.test")
    with pytest.raises(IdentityRefused) as refused:
        await store.revoke_membership(
            organization_id=membership.organization_id,
            user_id=owner.id,
            acting_user_id=owner.id,
        )
    assert "ostatni właściciel" in str(refused.value)


async def test_revoking_keeps_the_row_with_the_moment_it_ended(store) -> None:
    owner, membership = await _org(store, email="szef@firma.test")
    _invitation, token = await store.invite(
        organization_id=membership.organization_id,
        email="pracownik@firma.test",
        created_by=owner.id,
    )
    member, _ = await store.accept_invitation(token=token, password="inne-haslo-123")

    assert await store.revoke_membership(
        organization_id=membership.organization_id, user_id=member.id, acting_user_id=owner.id
    )

    rows = await store.members(membership.organization_id)
    revoked = [row for row in rows if row.user_id == member.id]
    assert len(revoked) == 1
    assert revoked[0].revoked_at is not None and not revoked[0].active
    assert (
        await store.active_membership(user_id=member.id, organization_id=membership.organization_id)
        is None
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def test_an_invitation_stores_a_digest_and_hands_the_token_back_once(store, pool) -> None:
    owner, membership = await _org(store, email="zapraszam@firma.test")
    invitation, token = await store.invite(
        organization_id=membership.organization_id, email="Gosc@Firma.test", created_by=owner.id
    )

    assert invitation.email == "gosc@firma.test"
    async with pool.acquire() as connection:
        stored = await connection.fetchval(
            "SELECT token_hash FROM invitations WHERE id = $1", invitation.id
        )
    assert stored == token_digest(token)
    assert token not in stored


async def test_accepting_creates_the_account_with_the_password_chosen_by_the_person(store) -> None:
    owner, membership = await _org(store, email="szefowa@firma.test")
    _invitation, token = await store.invite(
        organization_id=membership.organization_id, email="nowy@firma.test", created_by=owner.id
    )

    user, joined = await store.accept_invitation(token=token, password="moje-wlasne-haslo")

    assert user.email == "nowy@firma.test"
    assert joined.role == "MEMBER"
    assert joined.organization_id == membership.organization_id
    # The password is the one typed at acceptance, not anything the inviter knows.
    assert await store.verify_login(email="nowy@firma.test", password="moje-wlasne-haslo")


async def test_an_invitation_works_once(store) -> None:
    owner, membership = await _org(store, email="jeden-raz@firma.test")
    _invitation, token = await store.invite(
        organization_id=membership.organization_id, email="raz@firma.test", created_by=owner.id
    )
    await store.accept_invitation(token=token, password="haslo-pierwsze-123")

    with pytest.raises(IdentityRefused) as refused:
        await store.accept_invitation(token=token, password="haslo-drugie-123")
    assert "przyjęte" in str(refused.value)


async def test_an_expired_invitation_is_refused(store) -> None:
    owner, membership = await _org(store, email="termin@firma.test")
    _invitation, token = await store.invite(
        organization_id=membership.organization_id,
        email="spozniony@firma.test",
        created_by=owner.id,
        ttl=timedelta(seconds=1),
    )
    import asyncio

    await asyncio.sleep(1.1)
    with pytest.raises(IdentityRefused) as refused:
        await store.accept_invitation(token=token, password="haslo-123456")
    assert "ważność" in str(refused.value)


async def test_inviting_somebody_who_is_already_here_is_refused(store) -> None:
    owner, membership = await _org(store, email="ktos@firma.test")
    with pytest.raises(IdentityRefused):
        await store.invite(
            organization_id=membership.organization_id,
            email="ktos@firma.test",
            created_by=owner.id,
        )


async def test_an_existing_account_invited_elsewhere_keeps_its_password(store) -> None:
    """An invitation must not be a way to set somebody else's credential."""
    outsider, _ = await _org(store, email="obecny@firma.test", name="Własna firma")
    owner, membership = await _org(store, email="host@firma.test", name="Cudza firma")
    _invitation, token = await store.invite(
        organization_id=membership.organization_id, email="obecny@firma.test", created_by=owner.id
    )

    user, joined = await store.accept_invitation(token=token, password="probuje-podmienic")

    assert user.id == outsider.id
    assert joined.organization_id == membership.organization_id
    assert await store.verify_login(email="obecny@firma.test", password="dobre-haslo-123")
    assert await store.verify_login(email="obecny@firma.test", password="probuje-podmienic") is None


async def test_a_person_can_belong_to_two_spaces_and_sees_both(store) -> None:
    user, _first = await _org(store, email="dwie@firmy.test", name="Pierwsza")
    owner, second = await _org(store, email="wlasciciel-drugiej@firmy.test", name="Druga")
    _invitation, token = await store.invite(
        organization_id=second.organization_id, email="dwie@firmy.test", created_by=owner.id
    )
    await store.accept_invitation(token=token, password="nieistotne-haslo")

    memberships = await store.memberships_of(user.id)

    assert [row.organization_name for row in memberships] == ["Pierwsza", "Druga"]
    assert [row.role for row in memberships] == ["OWNER", "MEMBER"]


# ---------------------------------------------------------------------------
# The founder's migrated account
# ---------------------------------------------------------------------------


async def _migrated_account(pool, tenant):
    """An account in the shape migration 0018 leaves behind: a password, no address, OWNER.

    Built here rather than read from the migration's own run, and the distinction matters: the
    migration moved the ONE row that existed when it ran, while this fixture's operator is
    created per test, afterwards. That the migration itself does the right thing is measured in
    `services/db_schema/tests/test_identity_schema.py`, which executes the statements from the
    file. What is measured HERE is what the store does with an account in that state.
    """
    async with pool.acquire() as connection:
        credential = await connection.fetchval(
            "SELECT credential_hash FROM operators WHERE organization_id = $1 "
            "AND disabled_at IS NULL",
            tenant["organization"],
        )
        user_id = await connection.fetchval(
            "INSERT INTO users (email, credential_hash) VALUES (NULL, $1) RETURNING id",
            credential,
        )
        await connection.execute(
            "INSERT INTO memberships (user_id, organization_id, role) VALUES ($1, $2, 'OWNER')",
            user_id,
            tenant["organization"],
        )
    return user_id


async def test_the_migrated_account_says_it_needs_an_address_and_can_be_given_one(
    store, pool, tenant
) -> None:
    user_id = await _migrated_account(pool, tenant)

    before = await store.user(user_id)
    assert before is not None and before.needs_email

    after = await store.set_email(user_id, "Zalozyciel@Wenext.test")
    assert after.email == "zalozyciel@wenext.test" and not after.needs_email


async def test_an_address_already_used_cannot_be_moved_onto_another_account(
    store, tenant, pool
) -> None:
    await _org(store, email="pierwszy@firma.test")
    user_id = await _migrated_account(pool, tenant)
    with pytest.raises(IdentityRefused):
        await store.set_email(user_id, "pierwszy@firma.test")


def test_the_digest_is_a_digest_and_not_the_token() -> None:
    token = "jakis-token-do-zaproszenia"
    digest = token_digest(token)
    assert len(digest) == 64 and token not in digest
    assert digest == token_digest(token), "skrót musi być stabilny"
