"""A job in any `WAITING_*` state holds NO lease — L2144-2147, L5288.

The rule is already in the repository as data, not as a sentence: `WAITING_HOLDS_NO_LEASE` in
`services/codex_runtime/codex_runtime/jobs.py` (around line 20) is `frozenset(JobWaitingState)`,
and its comment says outright that F1 will be the phase that checks it. This file is that check
for the queue side of it.

    "Joby w stanach `WAITING_*` nie trzymają lease'u runtime'u; przejście do `READY` emituje
     event, który atomowo tworzy nową wersję dispatchu dla oczekujących jobów." (L2147)

Why it matters concretely: a job waiting for the operator to re-authenticate can wait for hours.
A lease held across that wait either expires — and then the reaper burns an attempt against the
limit of L5214 for a job that never ran — or it does not expire, and the job occupies the single
active lease slot enforced by `job_leases_one_active_per_job` while doing nothing at all.

The constant cannot be imported here: `codex_runtime` is not a dependency of the control plane,
and adding one to reach a frozenset would tie the API process to the Codex runtime. The set is
therefore rebuilt from the same source that constant is built from — `contracts.JobWaitingState`
— and the count is asserted, so a fifth waiting state cannot slip past this file in silence.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from community_os_contracts import JobWaitingState
from control_plane.domain import commands
from control_plane.domain.errors import JobFencingRefused
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

pytestmark = [pytest.mark.db]

LONG_LEASE_SECONDS = 60.0

#: The same set `WAITING_HOLDS_NO_LEASE` is built from, and the reason it is a set and not four
#: literals: a state added to the contracts has to break this file, not sneak through it.
WAITING_STATES = tuple(JobWaitingState)


#: `pool` and `tenant` come from `conftest.py`; `tenant` is a fresh organization per test.


async def _claim_one(pool, tenant):
    port = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    await commands.enqueue_job(
        port,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="fixture.waiting",
        routing_key="planning.daily",
        priority=0,
        attempt_limit=5,
        idempotency_key=f"waiting-{uuid4()}",
    )
    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        lease_seconds=LONG_LEASE_SECONDS,
    )
    assert len(batch.claims) == 1, batch.claims
    return port, batch.claims[0]


@pytest.mark.parametrize("waiting_state", WAITING_STATES, ids=[s.value for s in WAITING_STATES])
async def test_a_waiting_job_holds_no_lease(pool, tenant, waiting_state) -> None:
    """Every one of the four waiting states gives the lease back. Not three of them.

    The count is asserted first and separately: parametrizing over an EMPTY sequence collects
    zero tests and pytest reports a green run, which is the same silence as `all([])` being
    `True`. Four is the number `contracts.JobWaitingState` has and the number migration 0003
    lists in `jobs_status_is_known`.
    """
    assert len(WAITING_STATES) == 4, (
        f"contracts.JobWaitingState ma {len(WAITING_STATES)} wartości. L2144 wymienia cztery; "
        "piąta oznacza, że ten test przestał obejmować wszystkie stany oczekiwania"
    )

    port, claim = await _claim_one(pool, tenant)
    token = claim.fencing_token

    async with pool.acquire() as conn:
        active_before = await conn.fetchval(
            "SELECT count(*) FROM job_leases WHERE job_id = $1 AND status = 'ACTIVE'",
            claim.job_id,
        )
    # Cardinality before the property: with zero leases here, "no active lease" below would be
    # true from the start and the release would not have been measured at all.
    assert active_before == 1, (
        f"przed wstrzymaniem job ma {active_before} aktywnych lease'ów, oczekiwano 1 — bez "
        "lease'u nie ma czego oddawać i ten test niczego nie sprawdza"
    )

    await commands.release_on_waiting(
        port, token=token, waiting_state=waiting_state, delay_seconds=5.0
    )

    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, not_before > now() AS deferred FROM jobs WHERE id = $1", claim.job_id
        )
        leases = await conn.fetch(
            "SELECT status FROM job_leases WHERE job_id = $1 ORDER BY lease_generation",
            claim.job_id,
        )
        attempt = await conn.fetchrow(
            "SELECT status, completed_at, failure_class FROM job_attempts WHERE attempt_id = $1",
            token.attempt_id,
        )

    assert job["status"] == waiting_state.value, job["status"]
    assert job["deferred"] is True, (
        "job wstrzymany bez `not_before` w przyszłości jest natychmiast redispatchowalny — "
        "czekanie na warunek zewnętrzny zamienia się w pętlę bez przerw"
    )
    assert len(leases) == 1, [dict(row) for row in leases]
    assert leases[0]["status"] == "RELEASED", (
        f"lease joba w stanie {waiting_state.value} jest w stanie {leases[0]['status']}. "
        "L2147: joby WAITING_* nie trzymają lease'u — ani ACTIVE, ani „chwilowo ACTIVE”"
    )
    assert attempt["status"] != "RUNNING", attempt["status"]
    assert attempt["completed_at"] is not None
    assert attempt["failure_class"] == waiting_state.value, (
        "próba zamknięta bez zapisanego powodu oczekiwania — reaper i ekran operatora nie mają "
        "wtedy skąd wiedzieć, na co job czekał"
    )

    # And the waiting job is not dispatchable: it is out of the pool until something moves it
    # back to READY (L2147), which is not this command's business.
    batch = await JobStore(pool).claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=5,
        lease_seconds=LONG_LEASE_SECONDS,
    )
    assert batch.claims == (), batch.claims


async def test_parking_a_job_in_waiting_under_a_stale_token_is_refused(pool, tenant) -> None:
    """The waiting path is CAS-fenced like every other write (L2666).

    Without the CAS a worker whose lease was reaped could park somebody else's job in
    `WAITING_AUTH` — a denial of service on the queue performed by a process that no longer
    holds anything, and one that looks in the audit trail like a legitimate wait.
    """
    port, claim = await _claim_one(pool, tenant)
    stale = claim.fencing_token.model_copy(update={"lease_generation": claim.lease_generation + 7})

    with pytest.raises(JobFencingRefused):
        await commands.release_on_waiting(
            port, token=stale, waiting_state=JobWaitingState.WAITING_AUTH, delay_seconds=5.0
        )

    async with pool.acquire() as conn:
        job_status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", claim.job_id)
        lease_status = await conn.fetchval(
            "SELECT status FROM job_leases WHERE job_id = $1", claim.job_id
        )
    assert job_status == "RUNNING", job_status
    assert lease_status == "ACTIVE", lease_status
