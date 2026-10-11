"""ProfileLease + fencing on PostgreSQL — the spike required by L5164.

Rules from section 14.4 (L2369-2379) that this module implements:

* the lease is authoritative in Postgres,
* `generation` grows on every takeover,
* a partial unique constraint exists for one active lease per profile,
* every gateway call carries a fencing token,
* losing the heartbeat starts reconciliation, not an immediate second start,
* **a stale fencing token is rejected** (this is the F0 DoD verbatim, L5178),
* uncertain reconciliation moves the profile to `QUARANTINED`.

Why a custom implementation and not an off-the-shelf library:
    The research checked `procrastinate`, `pgqueuer`, `arq`, `saq` and `chancy`. None of
    them verifies the owner's identity when finalising work — `procrastinate` finalises a
    job by `id` and status alone, and `pgqueuer` documents outright that a worker with a
    blocked loop loses its heartbeat, the work is handed on, and the original run may still
    finish. Fencing is exactly that missing property, so bolting it on from the outside to a
    library would give the illusion of a guarantee. Besides, F0 needs a lease on a PROFILE,
    not a job queue — the queue is explicitly deferred to F1 (L5193).

Time: all comparisons use PostgreSQL `now()`, never the process clock (L2668).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import asyncpg
from community_os_contracts import FencingToken, StaleFencingToken


class LeaseUnavailable(Exception):
    """The profile has a live, unexpired lease belonging to somebody else."""


class ProfileQuarantined(Exception):
    """The profile is in a state in which it must not be leased."""


@dataclass(frozen=True)
class Lease:
    """A leased profile together with the token that must be presented on every call."""

    id: UUID
    organization_id: UUID
    profile_id: UUID
    generation: int
    owner_runner_id: UUID
    expires_at: datetime

    def token(self) -> FencingToken:
        return FencingToken(
            profile_id=self.profile_id, generation=self.generation, lease_id=self.id
        )


class ProfileLeaseStore:
    """Operations on leases. Every write is a CAS on (id, generation, state)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- acquisition ---------------------------------------------------------

    async def acquire(
        self,
        *,
        organization_id: UUID,
        profile_id: UUID,
        owner_runner_id: UUID,
        ttl: timedelta,
    ) -> Lease:
        """Takes a lease on a profile. If somebody else's lease is alive — it refuses.

        Taking over an expired lease is a separate operation (`takeover`), because
        "losing the heartbeat starts reconciliation, not an immediate second start"
        (L2376). Silently taking over inside `acquire` would break that rule.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            profile_state = await conn.fetchval(
                "SELECT state FROM browser_profiles WHERE id = $1 AND organization_id = $2",
                profile_id,
                organization_id,
            )
            if profile_state is None:
                raise LeaseUnavailable(
                    f"Profil {profile_id} nie istnieje w organizacji {organization_id}"
                )
            if profile_state in ("QUARANTINED", "CORRUPT", "RETIRED"):
                raise ProfileQuarantined(
                    f"Profil {profile_id} jest w stanie {profile_state} — "
                    "nie wolno go wydzierżawić."
                )

            active_lease = await conn.fetchrow(
                """
                SELECT id, generation, expires_at, expires_at <= now() AS przeterminowany
                FROM profile_leases
                WHERE profile_id = $1 AND state = 'ACTIVE'
                FOR UPDATE
                """,
                profile_id,
            )
            if active_lease is not None:
                if active_lease["przeterminowany"]:
                    raise LeaseUnavailable(
                        f"Profil {profile_id} ma przeterminowany lease (generacja "
                        f"{active_lease['generation']}). Wymagane jawne takeover() "
                        "z reconciliation, "
                        "nie ciche przejęcie (L2376)."
                    )
                raise LeaseUnavailable(
                    f"Profil {profile_id} jest już wydzierżawiony "
                    f"(generacja {active_lease['generation']}, "
                    f"wygasa {active_lease['expires_at'].isoformat()})."
                )

            return await self._insert_lease(
                conn,
                organization_id=organization_id,
                profile_id=profile_id,
                owner_runner_id=owner_runner_id,
                ttl=ttl,
            )

    async def takeover(
        self,
        *,
        organization_id: UUID,
        profile_id: UUID,
        owner_runner_id: UUID,
        ttl: timedelta,
        reconciliation_confirmed: bool,
    ) -> Lease:
        """Takes over an expired lease, raising the generation.

        `reconciliation_potwierdzone` must be set explicitly by the caller. The supervisor
        does not know whether the previous owner managed to perform a side effect — the
        execution layer does. Without confirmation the profile goes to `QUARANTINED`,
        because "uncertain reconciliation moves the profile to QUARANTINED" (L2379).
        """
        # Step 1: the decision and the state write — in a transaction of its own.
        # The exception MUST NOT be raised inside the transaction, because a rollback
        # would undo the quarantine and the profile would stay LEASED despite unresolved
        # reconciliation.
        # This bug was caught by the test `test_przejecie_bez_reconciliation_daje_quarantine`.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                stale = await conn.fetchrow(
                    """
                    SELECT id, generation, expires_at <= now() AS przeterminowany
                    FROM profile_leases
                    WHERE profile_id = $1 AND state = 'ACTIVE'
                    FOR UPDATE
                    """,
                    profile_id,
                )
                if stale is None:
                    refusal_reason = "brak_aktywnego"
                elif not stale["przeterminowany"]:
                    refusal_reason = "nadal_zyje"
                elif not reconciliation_confirmed:
                    await conn.execute(
                        "UPDATE profile_leases SET state = 'RECONCILING' WHERE id = $1",
                        stale["id"],
                    )
                    await conn.execute(
                        "UPDATE browser_profiles SET state = 'QUARANTINED' WHERE id = $1",
                        profile_id,
                    )
                    refusal_reason = "kwarantanna"
                else:
                    refusal_reason = None

            # Step 2: reacting to the decision, now outside the writing transaction.
            if refusal_reason == "brak_aktywnego":
                raise LeaseUnavailable(
                    f"Profil {profile_id} nie ma aktywnego lease'u — użyj acquire()."
                )
            if refusal_reason == "nadal_zyje":
                raise LeaseUnavailable(
                    f"Lease profilu {profile_id} (generacja {stale['generation']}) nadal żyje. "
                    "Przejęcie żywego lease'u jest zakazane — dwaj workerzy nie uruchamiają "
                    "jednego profilu (L5581)."
                )
            if refusal_reason == "kwarantanna":
                raise ProfileQuarantined(
                    f"Profil {profile_id} przeniesiony do QUARANTINED: "
                    "przejęcie bez potwierdzonego "
                    "reconciliation jest zakazane (L2379). Wymagana decyzja człowieka."
                )

            # Step 3: the takeover proper, in a new transaction.
            async with conn.transaction():
                await conn.execute(
                    "UPDATE profile_leases SET state = 'EXPIRED', released_at = now() "
                    "WHERE id = $1",
                    stale["id"],
                )
                return await self._insert_lease(
                    conn,
                    organization_id=organization_id,
                    profile_id=profile_id,
                    owner_runner_id=owner_runner_id,
                    ttl=ttl,
                )

    @staticmethod
    async def _insert_lease(
        conn: asyncpg.Connection,
        *,
        organization_id: UUID,
        profile_id: UUID,
        owner_runner_id: UUID,
        ttl: timedelta,
    ) -> Lease:
        # The generation is monotonic per profile and is never reused.
        next_generation = await conn.fetchval(
            "SELECT COALESCE(MAX(generation), 0) + 1 FROM profile_leases WHERE profile_id = $1",
            profile_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO profile_leases
                (organization_id, profile_id, generation, owner_runner_id, expires_at)
            VALUES ($1, $2, $3, $4, now() + $5::interval)
            RETURNING id, generation, expires_at
            """,
            organization_id,
            profile_id,
            next_generation,
            owner_runner_id,
            ttl,
        )
        await conn.execute("UPDATE browser_profiles SET state = 'LEASED' WHERE id = $1", profile_id)
        return Lease(
            id=row["id"],
            organization_id=organization_id,
            profile_id=profile_id,
            generation=row["generation"],
            owner_runner_id=owner_runner_id,
            expires_at=row["expires_at"],
        )

    # -- maintenance ---------------------------------------------------------

    async def heartbeat(self, token: FencingToken, *, ttl: timedelta) -> datetime:
        """Renews the lease. A stale token loses the CAS and is rejected.

        This is the place where the DoD "a stale fencing token is rejected" (L5178) and
        the test "an old runner does not perform a tool call after expiry" (L5582) happen.
        """
        async with self._pool.acquire() as conn:
            new_expires_at = await conn.fetchval(
                """
                UPDATE profile_leases
                SET heartbeat_at = now(), expires_at = now() + $3::interval
                WHERE id = $1 AND generation = $2 AND state = 'ACTIVE' AND expires_at > now()
                RETURNING expires_at
                """,
                token.lease_id,
                token.generation,
                ttl,
            )
        if new_expires_at is None:
            live_generation = await self.current_generation(token.profile_id)
            raise StaleFencingToken(
                expected=live_generation or -1,
                presented=token.generation,
                profile_id=token.profile_id,
            )
        return new_expires_at

    async def guard(self, token: FencingToken) -> None:
        """Checks the token before EVERY Gateway call (L2375).

        Raises `StaleFencingToken` if the token does not match the current, live lease.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT generation
                FROM profile_leases
                WHERE profile_id = $1 AND state = 'ACTIVE' AND expires_at > now()
                """,
                token.profile_id,
            )
        live_generation = row["generation"] if row else None
        if live_generation != token.generation:
            raise StaleFencingToken(
                expected=live_generation if live_generation is not None else -1,
                presented=token.generation,
                profile_id=token.profile_id,
            )

    async def current_generation(self, profile_id: UUID) -> int | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT generation FROM profile_leases WHERE profile_id = $1 AND state = 'ACTIVE'",
                profile_id,
            )

    # -- release -------------------------------------------------------------

    async def release(self, token: FencingToken, *, capsule_stopped: bool) -> None:
        """Releases the lease.

        "the lease is released only after revoking stream/input, stopping the whole capsule,
         fsync, safely unmounting the profile and confirming supervisor/container identity"
        (L2378). This code cannot check it for the caller — it demands an explicit declaration.
        Releasing without confirmation moves the profile to QUARANTINED instead of pretending
        it is OK.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            target_lease_state = "RELEASED" if capsule_stopped else "RECONCILING"
            updated_lease_id = await conn.fetchval(
                """
                UPDATE profile_leases
                SET state = $3, released_at = now()
                WHERE id = $1 AND generation = $2 AND state = 'ACTIVE'
                RETURNING id
                """,
                token.lease_id,
                token.generation,
                target_lease_state,
            )
            if updated_lease_id is None:
                live_generation = await conn.fetchval(
                    "SELECT generation FROM profile_leases "
                    "WHERE profile_id = $1 AND state = 'ACTIVE'",
                    token.profile_id,
                )
                raise StaleFencingToken(
                    expected=live_generation if live_generation is not None else -1,
                    presented=token.generation,
                    profile_id=token.profile_id,
                )

            new_profile_state = "AVAILABLE" if capsule_stopped else "QUARANTINED"
            await conn.execute(
                "UPDATE browser_profiles SET state = $2 WHERE id = $1",
                token.profile_id,
                new_profile_state,
            )

        if not capsule_stopped:
            raise ProfileQuarantined(
                f"Profil {token.profile_id} przeniesiony do QUARANTINED: zwolnienie lease'u bez "
                "potwierdzonego zatrzymania kapsuły jest zakazane (L2378, L2431)."
            )
