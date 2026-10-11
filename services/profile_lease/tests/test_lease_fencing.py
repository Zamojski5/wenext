"""Integration tests for the lease and fencing — against a REAL PostgreSQL.

Run by `tools/dev/run_lease_tests.sh`, which brings up a single-use Postgres container,
applies migration 0001 and sets `WENEXT_TEST_DSN`.

These tests defend two items of the Definition of Done of phase E1.F0:

    "a stale fencing token is rejected" (L5178)
    "spike Postgres lease + fencing" (L5164)

as well as the acceptance tests from section 26: L5581, L5582, L5592, L5596.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from community_os_contracts import FencingToken, StaleFencingToken
from profile_lease.store import LeaseUnavailable, ProfileLeaseStore, ProfileQuarantined

DSN = os.environ.get("WENEXT_TEST_DSN")

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not DSN, reason="brak WENEXT_TEST_DSN — uruchom tools/dev/run_lease_tests.sh"
    ),
]

SHORT_TTL = timedelta(seconds=30)
#: A TTL short enough that waiting for a REAL expiry is feasible.
#: Moving `expires_at` back in the database is impossible, and rightly so — it is
#: blocked by CHECK (expires_at > acquired_at), which guards against a lease dead on arrival.
INSTANT_TTL = timedelta(milliseconds=400)


async def wait_for_expiry() -> None:
    await asyncio.sleep(INSTANT_TTL.total_seconds() + 0.3)


@pytest_asyncio.fixture
async def pool():
    connection_pool = await asyncpg.create_pool(DSN, min_size=1, max_size=6)
    yield connection_pool
    await connection_pool.close()


@pytest_asyncio.fixture
async def world(pool) -> dict[str, UUID]:
    """A fresh organization, account and profile for every test."""
    async with pool.acquire() as conn:
        org = await conn.fetchval("INSERT INTO organizations (name) VALUES ('test') RETURNING id")
        konto = await conn.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'fixture', $2) RETURNING id
            """,
            org,
            f"konto-{uuid4()}",
        )
        profil = await conn.fetchval(
            """
            INSERT INTO browser_profiles
                (organization_id, social_account_id, state, profile_build_marker, profile_platform)
            VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing/153.0.8010.12', 'linux-arm64')
            RETURNING id
            """,
            org,
            konto,
        )
    return {"org": org, "konto": konto, "profil": profil}


@pytest.fixture
def store(pool) -> ProfileLeaseStore:
    return ProfileLeaseStore(pool)


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


async def test_first_lease_has_generation_one(store, world) -> None:
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    assert lease.generation == 1
    assert lease.profile_id == world["profil"]


async def test_second_worker_does_not_get_live_lease(store, world) -> None:
    """ "two workers do not run one profile" (L5581)."""
    await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    with pytest.raises(LeaseUnavailable, match="już wydzierżawiony"):
        await store.acquire(
            organization_id=world["org"],
            profile_id=world["profil"],
            owner_runner_id=uuid4(),
            ttl=SHORT_TTL,
        )


async def test_concurrent_attempts_yield_one_lease(store, world) -> None:
    """A race between two runners is settled by the database, not by ordering in the code."""
    results = await asyncio.gather(
        *[
            store.acquire(
                organization_id=world["org"],
                profile_id=world["profil"],
                owner_runner_id=uuid4(),
                ttl=SHORT_TTL,
            )
            for _ in range(5)
        ],
        return_exceptions=True,
    )
    successful = [outcome for outcome in results if not isinstance(outcome, Exception)]
    assert len(successful) == 1, f"oczekiwano dokładnie jednego lease'u, jest {len(successful)}"


async def test_quarantined_profile_cannot_be_leased(store, world, pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE browser_profiles SET state = 'QUARANTINED' WHERE id = $1", world["profil"]
        )
    with pytest.raises(ProfileQuarantined):
        await store.acquire(
            organization_id=world["org"],
            profile_id=world["profil"],
            owner_runner_id=uuid4(),
            ttl=SHORT_TTL,
        )


# ---------------------------------------------------------------------------
# Fencing — the heart of the DoD
# ---------------------------------------------------------------------------


async def test_heartbeat_with_current_token_passes(store, world) -> None:
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    new_expires_at = await store.heartbeat(lease.token(), ttl=SHORT_TTL)
    assert new_expires_at is not None


async def test_stale_fencing_token_is_rejected(store, world, pool) -> None:
    """F0 DoD (L5178). The most important test in this file.

    Scenario: runner A holds a generation 1 lease. The lease expires, runner B takes over
    the profile (generation 2). Runner A — which is still running and does not know it has
    lost the profile — tries to send a heartbeat with the stale token. It MUST lose.
    """
    lease_a = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=INSTANT_TTL,
    )

    await wait_for_expiry()

    lease_b = await store.takeover(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
        reconciliation_confirmed=True,
    )
    assert lease_b.generation == lease_a.generation + 1

    with pytest.raises(StaleFencingToken) as exc:
        await store.heartbeat(lease_a.token(), ttl=SHORT_TTL)
    assert exc.value.presented == 1
    assert exc.value.expected == 2


async def test_guard_rejects_stale_token(store, world, pool) -> None:
    """ "an old runner does not perform a tool call after expiry" (L5582)."""
    lease_a = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=INSTANT_TTL,
    )
    await wait_for_expiry()
    await store.takeover(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
        reconciliation_confirmed=True,
    )

    with pytest.raises(StaleFencingToken):
        await store.guard(lease_a.token())


async def test_guard_rejects_forged_token(store, world) -> None:
    await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    forged_token = FencingToken(profile_id=world["profil"], generation=99, lease_id=uuid4())
    with pytest.raises(StaleFencingToken):
        await store.guard(forged_token)


async def test_live_lease_cannot_be_taken_over(store, world) -> None:
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    with pytest.raises(LeaseUnavailable, match="nadal żyje"):
        await store.takeover(
            organization_id=world["org"],
            profile_id=world["profil"],
            owner_runner_id=uuid4(),
            ttl=SHORT_TTL,
            reconciliation_confirmed=True,
        )
    assert (await store.current_generation(world["profil"])) == lease.generation


async def test_takeover_without_reconciliation_quarantines_profile(store, world, pool) -> None:
    """ "uncertain reconciliation moves the profile to QUARANTINED" (L2379)."""
    # Wynik nie jest tu do niczego potrzebny, ale WYWOŁANIE tak: bez wydzierżawienia nie byłoby
    # czego przejmować i `takeover` odmówiłby z innego powodu niż ten, o który chodzi w teście.
    await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=INSTANT_TTL,
    )
    await wait_for_expiry()

    with pytest.raises(ProfileQuarantined):
        await store.takeover(
            organization_id=world["org"],
            profile_id=world["profil"],
            owner_runner_id=uuid4(),
            ttl=SHORT_TTL,
            reconciliation_confirmed=False,
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM browser_profiles WHERE id = $1", world["profil"]
        )
    assert state == "QUARANTINED"


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


async def test_release_after_capsule_stop(store, world, pool) -> None:
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    await store.release(lease.token(), capsule_stopped=True)

    async with pool.acquire() as conn:
        profile_state = await conn.fetchval(
            "SELECT state FROM browser_profiles WHERE id = $1", world["profil"]
        )
    assert profile_state == "AVAILABLE"
    # After a release the lease can be taken again, with a higher generation.
    new_lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    assert new_lease.generation == lease.generation + 1


async def test_release_without_confirmation_quarantines_profile(store, world, pool) -> None:
    """ "the lease is released only after ... confirming supervisor/container identity" (L2378)."""
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    with pytest.raises(ProfileQuarantined):
        await store.release(lease.token(), capsule_stopped=False)

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM browser_profiles WHERE id = $1", world["profil"]
        )
    assert state == "QUARANTINED"


async def test_release_with_stale_token_is_rejected(store, world, pool) -> None:
    lease_a = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=INSTANT_TTL,
    )
    await wait_for_expiry()
    await store.takeover(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
        reconciliation_confirmed=True,
    )

    with pytest.raises(StaleFencingToken):
        await store.release(lease_a.token(), capsule_stopped=True)


# ---------------------------------------------------------------------------
# Schema constraints — invariants enforced by the database, not by the code
# ---------------------------------------------------------------------------


async def test_one_non_retired_profile_per_account(pool, world) -> None:
    """ "partial unique constraint" from L2303; test L5596."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO browser_profiles
                    (organization_id, social_account_id, state, profile_build_marker, profile_platform)
                VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing/153.0.8010.12', 'linux-arm64')
                """,
                world["org"],
                world["konto"],
            )


async def test_headless_session_cannot_have_display(pool, world, store) -> None:
    """ "an E1 run starts no display and no stream gateway" (L5532) — a DB CHECK guards it."""
    lease = await store.acquire(
        organization_id=world["org"],
        profile_id=world["profil"],
        owner_runner_id=uuid4(),
        ttl=SHORT_TTL,
    )
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO browser_sessions
                    (organization_id, profile_id, profile_lease_id, capsule_instance_id, os_pid,
                     process_started_at, process_instance_id, capsule_supervisor_identity,
                     browser_build_digest, browser_mode, cdp_endpoint_ref, display_id)
                VALUES ($1, $2, $3, $4, 7, now(), '7:123', 'sup-1',
                        'sha256:x', 'HEADLESS', 'internal://cdp', ':99')
                """,
                world["org"],
                world["profil"],
                lease.id,
                uuid4(),
            )


async def test_manual_only_capability_cannot_be_headless(pool, world) -> None:
    """L5537 enforced by a CHECK, not only by pydantic."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO capability_snapshots
                    (organization_id, social_account_id, surface_ref, capability, state,
                     execution_mode, required_browser_mode, side_effect, retry_class,
                     browser_build_digest, reason_code, confidence, adapter_version,
                     verified_at, expires_at)
                VALUES ($1, $2, 'fixture://c', 'READ_THREAD', 'MANUAL_ONLY',
                        'MANUAL_ONLY', 'HEADLESS', 'NONE', 'NO_RETRY',
                        'sha256:x', 'PROBE', 0.9, 'fixture-1.0.0', now(), now() + interval '1 day')
                """,
                world["org"],
                world["konto"],
            )


async def test_audit_is_append_only(pool, world) -> None:
    async with pool.acquire() as conn:
        identyfikator = await conn.fetchval(
            """
            INSERT INTO audit_events (organization_id, kind, subject_ref, actor_ref)
            VALUES ($1, 'TEST', 'x', 'y') RETURNING id
            """,
            world["org"],
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute(
                "UPDATE audit_events SET kind = 'ZMIENIONE' WHERE id = $1", identyfikator
            )
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("DELETE FROM audit_events WHERE id = $1", identyfikator)


async def test_composite_fk_blocks_foreign_organization(pool, world) -> None:
    """ "composite tenant FK blocks a profile ... pointing at an account/profile of a foreign
    organization" (L5587)."""
    async with pool.acquire() as conn:
        foreign_org_id = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca') RETURNING id"
        )
        # A fresh account in the home organization — without it the collision with the
        # `jeden_aktywny_na_konto` index would fire earlier than the foreign key and the
        # test would check something other than what it declares.
        spare_account_id = await conn.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'fixture', $2) RETURNING id
            """,
            world["org"],
            f"konto-{uuid4()}",
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO browser_profiles
                    (organization_id, social_account_id, state, profile_build_marker, profile_platform)
                VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing/153.0.8010.12', 'linux-arm64')
                """,
                foreign_org_id,
                spare_account_id,
            )
