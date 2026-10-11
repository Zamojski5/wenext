"""One Definition-of-Done line, measured on two transports instead of argued from a grep.

    "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
     kontraktowym portu" (L5216)

WHY A SECOND IMPLEMENTATION AND NOT A GREP. Before this module the repository had exactly one
`JobQueuePort` — `PostgresJobTransport` — and an argument: no module of `control_plane/domain/`
names a driver, therefore the commands cannot be reaching the storage. That is a premise. The
deferral of RabbitMQ to E3 (L5205) is admissible only because the swap "nie może zmienić komend
domenowych" (L5206), and a premise is not what that sentence promises. So: the SAME commands from
`control_plane/domain/commands.py` run against `tests/memory_transport.py`, which is three
dictionaries and no driver, and the answers are compared.

HOW THIS MODULE TRIES NOT TO BE ANOTHER TEST OF ZERO DETECTING POWER
--------------------------------------------------------------------
1.  Two implementations agreeing proves only that they agree. They are therefore ALSO compared
    against an absolute, written-down table of expected answers
    (`test_the_domain_scenario_gives_the_expected_answers_on_one_transport`), so two identically
    wrong transports fail.
2.  Two empty traces are equal. The comparison test — its name, unwrapped so it stays greppable,
    is `test_the_same_domain_commands_give_the_same_answer_on_both_transports` — asserts the size
    of the trace and the identity of its keys BEFORE comparing values; `all([])` and `{} == {}`
    have cost this project four false passes.
3.  A parametrisation that quietly stops generating one variant leaves a green suite proving
    half of what it claims. `test_both_implementations_were_actually_exercised` counts the
    builds of each transport, and the set it compares against is WRITTEN OUT rather than derived
    from `TRANSPORT_NAMES` — deriving it would mean deleting a variant also deletes the
    expectation, and the check would agree with its own deletion.
4.  A forged token has to lose on the condition it is forged against, not on a neighbouring one.
    `test_a_stale_fencing_token_is_refused_by_both_transports` forges three tokens that each
    differ from the live one in exactly ONE field, and finishes by closing the job with the REAL
    token — so "everything is refused" is not a way to pass.

WHAT THIS MODULE DOES NOT MEASURE, and no docstring here may pretend otherwise:
  * nothing about concurrency. The in-memory transport takes no locks and the scenario is
    sequential. `SELECT ... FOR UPDATE SKIP LOCKED` is measured by `test_job_claim.py` against a
    real server, and a green run here says nothing about two workers.
  * three of the six elements of the fencing tuple. `attempt_id`, `lease_generation` and
    `lease_owner` are each forged here and each measured. `job_id` is NOT (no forgery here
    differs in it alone); `expires_at > now()` is NOT (nothing here waits for a lease to die, so
    the condition is only ever exercised from its live side); and `expected_status` is NOT
    isolated (in the one case where a job is no longer `RUNNING`, its lease is no longer `ACTIVE`
    either, so the two conditions cannot be told apart). All three are measured against a real
    server, by mutation and one element at a time, in `test_job_lease_fencing.py`.
  * nothing about RabbitMQ. It proves that a second transport with no driver in it answers the
    commands identically — which is what makes the E3 swap a swap, not that the swap is done.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import memory_transport
import pytest
from community_os_contracts import (
    JOB_TRANSPORT_PORT_METHODS,
    JobTransportPort,
    JobWaitingState,
)
from control_plane.domain import commands
from control_plane.domain.errors import JobFencingRefused
from control_plane.domain.models import JobOutcome
from control_plane.domain.ports import JOB_QUEUE_PORT_METHODS, JobQueuePort
from control_plane.jobs import store as postgres_store
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from memory_transport import InMemoryJobTransport
from test_domain_does_not_import_a_driver import driver_findings

# Both variants need a real organization row, because the PostgreSQL one writes foreign keys and
# the scenario has to be IDENTICAL on both sides — a different organization per transport would
# be a difference in the test rather than in the transport.
pytestmark = pytest.mark.db

#: The variants the whole module runs on. Editing this tuple is the mutation that
#: `test_both_implementations_were_actually_exercised` exists to catch.
TRANSPORT_NAMES: tuple[str, ...] = ("postgres", "memory")

#: WRITTEN OUT, not derived from `TRANSPORT_NAMES`. If this set were built from that tuple,
#: removing a variant would remove the expectation with it and the check would pass while
#: measuring one implementation — which is precisely the failure it is here to prevent.
EXPECTED_TRANSPORT_NAMES = frozenset({"postgres", "memory"})

#: How many times each transport is built in a full run of this module: twice through the
#: `transport` fixture (two parametrised tests) and once each in the three tests that drive both
#: implementations inside one function. A number below this means a test stopped running.
MINIMUM_BUILDS_PER_TRANSPORT = 5

#: Every transport built by `build_transport`, as `(variant name, concrete class name)`. Read
#: only by the last test in this file.
BUILDS: list[tuple[str, str]] = []

#: The routing key every job in the scenario carries. One value, because the scenario compares
#: ordering by priority and a second routing key would add nothing to measure.
ROUTING_KEY = "principal.plan"

#: The lease every claim in the scenario takes. Long enough that no lease can die mid-scenario:
#: an expiry in the middle would make the comparison depend on how fast the machine is.
LEASE_SECONDS = 300.0

#: The delay that keeps one job out of every claim. Its only job is to make `scheduled_later`
#: non-zero, so `BatchEmptiness.ALL_SCHEDULED_FOR_LATER` is reachable.
FAR_FUTURE_SECONDS = 3600.0


def build_transport(name: str, *, pool: Any, organization_id: UUID | None) -> JobQueuePort:
    """The ONLY way this module makes a transport, so that every build is counted.

    A test that built one directly would be invisible to
    `test_both_implementations_were_actually_exercised`, and the count is the only thing standing
    between this file and a suite that silently measures one implementation.
    """
    if name == "postgres":
        instance: JobQueuePort = PostgresJobTransport(
            JobStore(pool), organization_id=organization_id
        )
    elif name == "memory":
        instance = InMemoryJobTransport(organization_id=organization_id)
    else:
        raise AssertionError(
            f"nieznany wariant transportu {name!r} — dopisanie wariantu wymaga gałęzi tutaj, "
            "bo inaczej parametryzacja generuje przypadek, którego nikt nie zbudował"
        )
    BUILDS.append((name, type(instance).__name__))
    return instance


@pytest.fixture(params=TRANSPORT_NAMES)
def transport(request, pool, tenant) -> JobQueuePort:
    """One transport per variant, scoped to the organization the `tenant` fixture created."""
    return build_transport(request.param, pool=pool, organization_id=tenant["organization"])


# ---------------------------------------------------------------------------
# The scenario. One function, run through whichever port it is handed.
# ---------------------------------------------------------------------------
# It records FACTS, never identifiers or moments: two transports mint different UUIDs and read
# different clocks, so a trace holding either would differ for reasons that are not the subject.
# Relations between identifiers ARE recorded — "the duplicate returned the same job", "the two
# lease owners differ" — because those are the same on both sides when the contract holds.


async def run_domain_scenario(
    port: JobQueuePort, *, organization_id: UUID, workspace_id: UUID
) -> dict[str, object]:
    """Drive the six domain commands and the four transport methods; return what was observed.

    The six commands come from `control_plane/domain/commands.py` and take the port as their
    first argument. `publish_ready`, `reserve`, `acknowledge` and `requeue` are the transport half
    of the port and are called on it directly — they are what E3 replaces, so leaving them out
    would leave the replaced half unmeasured.
    """
    seen: dict[str, object] = {}

    async def enqueue(key: str, *, job_type: str, priority: int, delay: float = 0.0):
        return await commands.enqueue_job(
            port,
            organization_id=organization_id,
            workspace_id=workspace_id,
            job_type=job_type,
            routing_key=ROUTING_KEY,
            priority=priority,
            attempt_limit=5,
            idempotency_key=key,
            delay_seconds=delay,
        )

    async def claim(label: str, *, limit: int) -> Any:
        batch = await commands.claim_batch(
            port, worker=uuid4(), limit=limit, lease_seconds=LEASE_SECONDS
        )
        seen[f"{label}.count"] = len(batch.claims)
        seen[f"{label}.emptiness"] = batch.emptiness.value
        seen[f"{label}.due_before_claim"] = batch.due_before_claim
        seen[f"{label}.scheduled_later"] = batch.scheduled_later
        seen[f"{label}.already_running"] = batch.already_running
        seen[f"{label}.job_types"] = tuple(claimed.job_type for claimed in batch.claims)
        seen[f"{label}.attempt_numbers"] = tuple(claimed.attempt_no for claimed in batch.claims)
        seen[f"{label}.lease_generations"] = tuple(
            claimed.lease_generation for claimed in batch.claims
        )
        seen[f"{label}.dispatch_versions"] = tuple(
            claimed.dispatch_version for claimed in batch.claims
        )
        seen[f"{label}.deadlines_are_set"] = (
            all(claimed.expires_at is not None for claimed in batch.claims)
            and len(batch.claims) > 0
        )
        return batch

    async def look(label: str, job_id: UUID) -> Any:
        detail = await commands.read_job(port, job_id, organization_id=organization_id)
        assert detail is not None, f"{label}: job zniknął z kanonicznego magazynu"
        seen[f"{label}.status"] = detail.job.status
        seen[f"{label}.dispatch_version"] = detail.job.dispatch_version
        seen[f"{label}.lock_version"] = detail.job.lock_version
        seen[f"{label}.attempt_count"] = detail.job.attempt_count
        seen[f"{label}.holds_live_lease"] = detail.job.holds_live_lease
        seen[f"{label}.attempt_statuses"] = tuple(attempt.status for attempt in detail.attempts)
        seen[f"{label}.attempt_failure_classes"] = tuple(
            attempt.failure_class for attempt in detail.attempts
        )
        seen[f"{label}.attempt_numbers"] = tuple(attempt.attempt_no for attempt in detail.attempts)
        return detail

    # -- 1. the idempotency key adds nothing, and overwrites nothing ------------------------
    alpha = await enqueue("contract-alpha", job_type="alpha", priority=10)
    seen["alpha.enqueued"] = alpha.created
    duplicate = await enqueue("contract-alpha", job_type="alpha-rewritten", priority=99)
    seen["duplicate.enqueued"] = duplicate.created
    seen["duplicate.same_job"] = duplicate.job_id == alpha.job_id

    fresh = await look("alpha.fresh", alpha.job_id)
    seen["alpha.fresh.job_type"] = fresh.job.job_type
    seen["alpha.fresh.priority"] = fresh.job.priority
    seen["alpha.fresh.attempt_limit"] = fresh.job.attempt_limit
    seen["alpha.fresh.routing_key"] = fresh.job.routing_key
    seen["alpha.fresh.payload_ref"] = fresh.job.payload_ref
    seen["alpha.fresh.workspace_is_the_one_given"] = fresh.job.workspace_id == workspace_id

    beta = await enqueue("contract-beta", job_type="beta", priority=5)
    await enqueue("contract-gamma", job_type="gamma", priority=20, delay=FAR_FUTURE_SECONDS)

    # -- 2. the claim takes the lowest priority first, and sees its own backlog --------------
    # `limit=1` on purpose. With two dispatchable jobs and a batch of two, the ORDER of the rows
    # coming back is not promised by either implementation — `UPDATE … RETURNING` has no ORDER BY
    # — so a two-row batch would pin something neither transport guarantees. A batch of one turns
    # the same question into a deterministic one: WHICH job was taken.
    first = await claim("claim1", limit=1)
    second = await claim("claim2", limit=10)
    await claim("claim3", limit=10)

    beta_token = first.claims[0].fencing_token
    alpha_token = second.claims[0].fencing_token
    seen["claim1.took_beta"] = first.claims[0].job_id == beta.job_id
    seen["claim2.took_alpha"] = second.claims[0].job_id == alpha.job_id
    seen["claims.owners_differ"] = beta_token.lease_owner != alpha_token.lease_owner

    # -- 3. a result closes the job; the same token a second time is refused -----------------
    await commands.report_result(port, token=beta_token, outcome=JobOutcome.SUCCEEDED)
    await look("beta.closed", beta.job_id)

    with pytest.raises(JobFencingRefused) as refused:
        await commands.report_result(port, token=beta_token, outcome=JobOutcome.SUCCEEDED)
    seen["beta.second_close.live_is_none"] = refused.value.live is None
    seen["beta.second_close.reason"] = refused.value.reason

    # -- 4. parking releases the lease and takes the job out of the pool ---------------------
    await commands.release_on_waiting(
        port, token=alpha_token, waiting_state=JobWaitingState.WAITING_QUOTA, delay_seconds=60.0
    )
    await look("alpha.parked", alpha.job_id)
    await claim("claim4", limit=10)

    # -- 5. publish_ready puts it back with a new dispatch version ---------------------------
    await port.publish_ready(alpha.job_id)
    await look("alpha.published", alpha.job_id)
    third = await claim("claim5", limit=10)

    # -- 6. requeue hands it back; the caller's moment in the past is clamped to now ---------
    await port.requeue(third.claims[0].fencing_token, not_before=datetime(2000, 1, 1, tzinfo=UTC))
    await look("alpha.requeued", alpha.job_id)
    fourth = await claim("claim6", limit=10)

    # -- 7. acknowledge is the transport's own spelling of a successful close ----------------
    await port.acknowledge(fourth.claims[0].fencing_token)
    await look("alpha.acknowledged", alpha.job_id)

    # -- 8. the list the operator's screen reads ---------------------------------------------
    listed = await commands.list_jobs(port, organization_id=organization_id, limit=10)
    seen["list.count"] = len(listed)
    seen["list.job_types"] = tuple(job.job_type for job in listed)
    seen["list.statuses"] = tuple(job.status for job in listed)
    seen["list.attempt_counts"] = tuple(job.attempt_count for job in listed)
    seen["list.live_leases"] = tuple(job.holds_live_lease for job in listed)
    seen["list.dispatch_versions"] = tuple(job.dispatch_version for job in listed)

    # -- 9. scope and refusals ----------------------------------------------------------------
    stranger = await commands.read_job(port, alpha.job_id, organization_id=uuid4())
    seen["foreign_organization.reads_nothing"] = stranger is None

    with pytest.raises(ValueError):
        await port.publish_ready(beta.job_id)
    seen["publish_ready.on_terminal"] = "ValueError"
    with pytest.raises(LookupError):
        await port.publish_ready(uuid4())
    seen["publish_ready.on_missing"] = "LookupError"

    return seen


#: What the scenario has to answer, transport-independently. Written down rather than compared
#: between the two implementations only: two transports that agree on a wrong answer would pass
#: the comparison test and fail this one.
#:
#: Reading the interesting rows: `alpha` is claimed second, parked, republished, claimed again,
#: requeued, claimed a third time and finally acknowledged — so its attempt numbers run 1, 2, 3,
#: its `dispatch_version` grows on the republish and on the requeue (never on a claim), and its
#: `lock_version` grows on EVERY write, which is why it ends at 8.
EXPECTED_SCENARIO: dict[str, object] = {
    "alpha.enqueued": True,
    "duplicate.enqueued": False,
    "duplicate.same_job": True,
    "alpha.fresh.status": "READY",
    "alpha.fresh.dispatch_version": 1,
    "alpha.fresh.lock_version": 1,
    "alpha.fresh.attempt_count": 0,
    "alpha.fresh.holds_live_lease": False,
    "alpha.fresh.attempt_statuses": (),
    "alpha.fresh.attempt_failure_classes": (),
    "alpha.fresh.attempt_numbers": (),
    # `DO NOTHING`, never `DO UPDATE`: the duplicate carried a different type and priority.
    "alpha.fresh.job_type": "alpha",
    "alpha.fresh.priority": 10,
    "alpha.fresh.attempt_limit": 5,
    "alpha.fresh.routing_key": ROUTING_KEY,
    "alpha.fresh.payload_ref": None,
    "alpha.fresh.workspace_is_the_one_given": True,
    # Two jobs are dispatchable, one is scheduled for later; the batch of one takes `beta`,
    # whose priority number is the lower.
    "claim1.count": 1,
    "claim1.emptiness": "NOT_EMPTY",
    "claim1.due_before_claim": 2,
    "claim1.scheduled_later": 1,
    "claim1.already_running": 0,
    "claim1.job_types": ("beta",),
    "claim1.attempt_numbers": (1,),
    "claim1.lease_generations": (1,),
    "claim1.dispatch_versions": (1,),
    "claim1.deadlines_are_set": True,
    "claim1.took_beta": True,
    "claim2.count": 1,
    "claim2.emptiness": "NOT_EMPTY",
    "claim2.due_before_claim": 1,
    "claim2.scheduled_later": 1,
    "claim2.already_running": 1,
    "claim2.job_types": ("alpha",),
    "claim2.attempt_numbers": (1,),
    "claim2.lease_generations": (1,),
    "claim2.dispatch_versions": (1,),
    "claim2.deadlines_are_set": True,
    "claim2.took_alpha": True,
    "claims.owners_differ": True,
    # Nothing left to take, but work exists — the distinction `BatchEmptiness` is for.
    "claim3.count": 0,
    "claim3.emptiness": "ALL_CLAIMED_BY_OTHERS",
    "claim3.due_before_claim": 0,
    "claim3.scheduled_later": 1,
    "claim3.already_running": 2,
    "claim3.job_types": (),
    "claim3.attempt_numbers": (),
    "claim3.lease_generations": (),
    "claim3.dispatch_versions": (),
    "claim3.deadlines_are_set": False,
    "beta.closed.status": "SUCCEEDED",
    "beta.closed.dispatch_version": 1,
    "beta.closed.lock_version": 3,
    "beta.closed.attempt_count": 1,
    "beta.closed.holds_live_lease": False,
    "beta.closed.attempt_statuses": ("SUCCEEDED",),
    "beta.closed.attempt_failure_classes": (None,),
    "beta.closed.attempt_numbers": (1,),
    # The lease was released by the close, so nobody holds the job — a different fact from
    # "somebody else holds it", and the screen renders them differently.
    "beta.second_close.live_is_none": True,
    "beta.second_close.reason": "zapis wyniku SUCCEEDED",
    "alpha.parked.status": "WAITING_QUOTA",
    "alpha.parked.dispatch_version": 1,
    "alpha.parked.lock_version": 3,
    "alpha.parked.attempt_count": 1,
    "alpha.parked.holds_live_lease": False,
    "alpha.parked.attempt_statuses": ("FAILED",),
    "alpha.parked.attempt_failure_classes": ("WAITING_QUOTA",),
    "alpha.parked.attempt_numbers": (1,),
    # A parked job is not dispatchable and holds no lease, so the only work left is the one
    # waiting for its `not_before`.
    "claim4.count": 0,
    "claim4.emptiness": "ALL_SCHEDULED_FOR_LATER",
    "claim4.due_before_claim": 0,
    "claim4.scheduled_later": 1,
    "claim4.already_running": 0,
    "claim4.job_types": (),
    "claim4.attempt_numbers": (),
    "claim4.lease_generations": (),
    "claim4.dispatch_versions": (),
    "claim4.deadlines_are_set": False,
    "alpha.published.status": "READY",
    "alpha.published.dispatch_version": 2,
    "alpha.published.lock_version": 4,
    "alpha.published.attempt_count": 1,
    "alpha.published.holds_live_lease": False,
    "alpha.published.attempt_statuses": ("FAILED",),
    "alpha.published.attempt_failure_classes": ("WAITING_QUOTA",),
    "alpha.published.attempt_numbers": (1,),
    "claim5.count": 1,
    "claim5.emptiness": "NOT_EMPTY",
    "claim5.due_before_claim": 1,
    "claim5.scheduled_later": 1,
    "claim5.already_running": 0,
    "claim5.job_types": ("alpha",),
    "claim5.attempt_numbers": (2,),
    "claim5.lease_generations": (2,),
    "claim5.dispatch_versions": (2,),
    "claim5.deadlines_are_set": True,
    "alpha.requeued.status": "READY",
    "alpha.requeued.dispatch_version": 3,
    "alpha.requeued.lock_version": 6,
    "alpha.requeued.attempt_count": 2,
    "alpha.requeued.holds_live_lease": False,
    "alpha.requeued.attempt_statuses": ("FAILED", "FAILED"),
    "alpha.requeued.attempt_failure_classes": ("WAITING_QUOTA", "REQUEUED"),
    "alpha.requeued.attempt_numbers": (1, 2),
    # The moment handed to `requeue` was in the year 2000 and was clamped to the queue's own
    # clock, so the job is dispatchable at once instead of never.
    "claim6.count": 1,
    "claim6.emptiness": "NOT_EMPTY",
    "claim6.due_before_claim": 1,
    "claim6.scheduled_later": 1,
    "claim6.already_running": 0,
    "claim6.job_types": ("alpha",),
    "claim6.attempt_numbers": (3,),
    "claim6.lease_generations": (3,),
    "claim6.dispatch_versions": (3,),
    "claim6.deadlines_are_set": True,
    "alpha.acknowledged.status": "SUCCEEDED",
    "alpha.acknowledged.dispatch_version": 3,
    "alpha.acknowledged.lock_version": 8,
    "alpha.acknowledged.attempt_count": 3,
    "alpha.acknowledged.holds_live_lease": False,
    "alpha.acknowledged.attempt_statuses": ("FAILED", "FAILED", "SUCCEEDED"),
    "alpha.acknowledged.attempt_failure_classes": ("WAITING_QUOTA", "REQUEUED", None),
    "alpha.acknowledged.attempt_numbers": (1, 2, 3),
    "list.count": 3,
    "list.job_types": ("gamma", "beta", "alpha"),
    "list.statuses": ("READY", "SUCCEEDED", "SUCCEEDED"),
    "list.attempt_counts": (0, 1, 3),
    "list.live_leases": (False, False, False),
    "list.dispatch_versions": (1, 1, 3),
    "foreign_organization.reads_nothing": True,
    "publish_ready.on_terminal": "ValueError",
    "publish_ready.on_missing": "LookupError",
}


def test_both_transports_satisfy_the_port_protocols(transport) -> None:
    """Structural conformance, plus the two constants the fake had to copy.

    `runtime_checkable` compares the PRESENCE of names and not signatures — the contracts say so
    themselves — so the names from both method lists are checked one by one, with the length of
    each list asserted first so that an emptied list cannot pass by vacuity.

    The last two assertions pin the copy: `memory_transport` cannot import `jobs/store.py`,
    because that module imports the driver and a fake that pulls the driver in proves nothing.
    Its two status constants are therefore a deliberate duplicate, and this is the place where
    the duplicate is stopped from drifting.
    """
    assert isinstance(transport, JobTransportPort)
    assert isinstance(transport, JobQueuePort)

    assert len(JOB_TRANSPORT_PORT_METHODS) >= 4, JOB_TRANSPORT_PORT_METHODS
    assert len(JOB_QUEUE_PORT_METHODS) >= 6, JOB_QUEUE_PORT_METHODS
    missing = [
        name
        for name in (*JOB_TRANSPORT_PORT_METHODS, *JOB_QUEUE_PORT_METHODS)
        if not hasattr(transport, name)
    ]
    assert not missing, missing

    assert memory_transport.DISPATCHABLE_STATUS == postgres_store.DISPATCHABLE_STATUS
    assert memory_transport.RUNNING_STATUS == postgres_store.RUNNING_STATUS
    assert memory_transport.REPUBLISHABLE_STATUSES == postgres_store.REPUBLISHABLE_STATUSES


async def test_the_domain_scenario_gives_the_expected_answers_on_one_transport(
    transport, tenant
) -> None:
    """Absolute truth, one transport at a time. The comparison test alone would not give it.

    Two implementations that agree on a wrong answer agree perfectly. Everything the scenario
    observes is therefore also compared with `EXPECTED_SCENARIO`, which was written from the SQL
    in `jobs/store.py` and from migration 0003, not from a run.
    """
    seen = await run_domain_scenario(
        transport,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
    )

    assert len(seen) == len(EXPECTED_SCENARIO), (
        f"scenariusz zapisał {len(seen)} faktów, a tabela oczekiwań ma {len(EXPECTED_SCENARIO)}. "
        f"Nadmiar: {sorted(set(seen) - set(EXPECTED_SCENARIO))}; "
        f"brak: {sorted(set(EXPECTED_SCENARIO) - set(seen))}"
    )
    assert seen == EXPECTED_SCENARIO


async def test_the_same_domain_commands_give_the_same_answer_on_both_transports(
    pool, tenant
) -> None:
    """The Definition-of-Done line itself: swap the transport, the commands do not notice.

    The size and the key set are asserted BEFORE the values. `{} == {}` is true, and a scenario
    that silently stopped recording would otherwise prove that two transports agree about
    nothing.
    """
    traces = {
        name: await run_domain_scenario(
            build_transport(name, pool=pool, organization_id=tenant["organization"]),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
        )
        for name in TRANSPORT_NAMES
    }

    assert len(traces) == 2, sorted(traces)
    for name, trace in traces.items():
        assert len(trace) >= 100, f"{name}: ślad ma {len(trace)} pozycji — to za mało na dowód"

    left, right = (traces[name] for name in sorted(traces))
    assert set(left) == set(right), sorted(set(left).symmetric_difference(right))
    differences = {key: (left[key], right[key]) for key in left if left[key] != right[key]}
    assert not differences, (
        "te same komendy domenowe dały różne wyniki na dwóch implementacjach portu — "
        f"czyli transport JEST widoczny dla domeny (L5216): {differences}"
    )


async def test_a_stale_fencing_token_is_refused_by_both_transports(pool, tenant) -> None:
    """Three forgeries, each differing in exactly ONE field of the live token.

    The rule this test obeys is the one written above `_HEARTBEAT_SQL` in `jobs/store.py`: a case
    has to REACH the condition it claims to measure. A forgery differing in three fields is
    rejected by whichever condition is checked first and proves nothing about the other two, so
    each token below differs in one field and one only.

    The positive control is the last step. After three refusals the REAL token closes the job —
    without it, a transport that refused everything would pass this test perfectly.
    """
    observed: dict[str, dict[str, object]] = {}
    for name in TRANSPORT_NAMES:
        port = build_transport(name, pool=pool, organization_id=tenant["organization"])
        enqueued = await commands.enqueue_job(
            port,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            job_type="fencing",
            routing_key=ROUTING_KEY,
            priority=0,
            attempt_limit=5,
            idempotency_key=f"contract-fencing-{name}",
        )
        batch = await commands.claim_batch(
            port, worker=uuid4(), limit=1, lease_seconds=LEASE_SECONDS
        )
        assert len(batch.claims) == 1, batch
        live = batch.claims[0].fencing_token

        forgeries = {
            "lease_generation": live.model_copy(
                update={"lease_generation": live.lease_generation + 1}
            ),
            "lease_owner": live.model_copy(update={"lease_owner": uuid4()}),
            "attempt_id": live.model_copy(update={"attempt_id": uuid4()}),
        }
        seen: dict[str, object] = {}
        for field_name, forged in forgeries.items():
            assert forged != live, field_name
            with pytest.raises(JobFencingRefused) as refused:
                await commands.report_result(port, token=forged, outcome=JobOutcome.SUCCEEDED)
            seen[f"{field_name}.live_is_the_real_token"] = refused.value.live == live
            seen[f"{field_name}.presented_is_the_forgery"] = refused.value.presented == forged

            # Nothing was written. A refusal that had already closed the job would be a refusal
            # in the message only.
            after = await commands.read_job(
                port, enqueued.job_id, organization_id=tenant["organization"]
            )
            assert after is not None
            seen[f"{field_name}.job_untouched"] = (
                after.job.status,
                after.job.holds_live_lease,
                tuple(attempt.status for attempt in after.attempts),
            )

        await commands.report_result(port, token=live, outcome=JobOutcome.SUCCEEDED)
        closed = await commands.read_job(
            port, enqueued.job_id, organization_id=tenant["organization"]
        )
        assert closed is not None
        seen["real_token.closes_the_job"] = (
            closed.job.status,
            closed.job.holds_live_lease,
            tuple(attempt.status for attempt in closed.attempts),
        )
        observed[name] = seen

    expected = {
        "lease_generation.live_is_the_real_token": True,
        "lease_generation.presented_is_the_forgery": True,
        "lease_generation.job_untouched": ("RUNNING", True, ("RUNNING",)),
        "lease_owner.live_is_the_real_token": True,
        "lease_owner.presented_is_the_forgery": True,
        "lease_owner.job_untouched": ("RUNNING", True, ("RUNNING",)),
        "attempt_id.live_is_the_real_token": True,
        "attempt_id.presented_is_the_forgery": True,
        "attempt_id.job_untouched": ("RUNNING", True, ("RUNNING",)),
        "real_token.closes_the_job": ("SUCCEEDED", False, ("SUCCEEDED",)),
    }
    assert set(observed) == EXPECTED_TRANSPORT_NAMES, sorted(observed)
    for name, seen in observed.items():
        assert seen == expected, name


async def test_a_transport_built_without_an_organization_refuses_to_write_on_both(pool) -> None:
    """The read-only instance `api/jobs.py` builds must refuse identically on both sides.

    A transport that defaulted to "some organization" on one implementation and refused on the
    other would be a difference in the seam, which is this module's subject — and a claim run
    against the wrong tenant is a mistake nobody notices.
    """
    messages: dict[str, tuple[str, str]] = {}
    for name in TRANSPORT_NAMES:
        read_only = build_transport(name, pool=pool, organization_id=None)
        with pytest.raises(RuntimeError) as from_reserve:
            await read_only.reserve(owner=uuid4(), limit=1, lease_seconds=LEASE_SECONDS)
        with pytest.raises(RuntimeError) as from_claim:
            await read_only.claim(worker=uuid4(), limit=1, lease_seconds=LEASE_SECONDS)
        messages[name] = (str(from_reserve.value), str(from_claim.value))

    assert set(messages) == EXPECTED_TRANSPORT_NAMES, sorted(messages)
    for name, (reserve_message, claim_message) in messages.items():
        assert "wyłącznie do odczytu" in reserve_message, name
        assert reserve_message == claim_message, name
    assert len(set(messages.values())) == 1, (
        f"dwie implementacje odmawiają zapisu innymi słowami: {messages}. Komunikat operatora "
        "jest częścią kontraktu — na nim opiera się rozpoznanie, co się właśnie stało"
    )


def test_the_in_memory_transport_holds_no_database_driver() -> None:
    """The fake has to stay a fake. Scanned with the scanner from the module next door.

    The moment this file could reach a connection, "two implementations agree" would mean "two
    ways of asking the same database agree", and the port would be measured against itself.
    """
    source = pathlib.Path(memory_transport.__file__).read_text(encoding="utf-8")
    findings = driver_findings(source, where="memory_transport.py")
    assert not findings, findings

    # And it is really the transport, not an empty module that trivially holds no driver.
    defined = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert set(JOB_TRANSPORT_PORT_METHODS) <= defined, sorted(
        set(JOB_TRANSPORT_PORT_METHODS) - defined
    )
    assert set(JOB_QUEUE_PORT_METHODS) <= defined, sorted(set(JOB_QUEUE_PORT_METHODS) - defined)


def test_both_implementations_were_actually_exercised() -> None:
    """THE LAST TEST IN THIS FILE, AND IT HAS TO STAY LAST. It counts what the others did.

    Everything above is parametrised or loops over `TRANSPORT_NAMES`. A parametrisation that
    quietly stops generating a variant — one entry deleted, one branch of `build_transport`
    removed, one test skipped — leaves a green suite that proves half of what the module's
    docstring claims, and the half it proves is the one that was already true.

    `EXPECTED_TRANSPORT_NAMES` is written out rather than derived from `TRANSPORT_NAMES` on
    purpose: deriving it would make the deletion of a variant delete its own expectation.

    Run this test alone and it fails, correctly — there is nothing to count. It is a report on
    the module's run, not a unit of it.
    """
    assert BUILDS, (
        "żadna implementacja portu nie została zbudowana. Ten test jest raportem z przebiegu "
        "całego modułu — uruchom go razem z nim"
    )

    used = Counter(name for name, _ in BUILDS)
    assert set(used) == EXPECTED_TRANSPORT_NAMES, (
        f"uruchomiono warianty {sorted(used)}, a kontrakt ma być mierzony na "
        f"{sorted(EXPECTED_TRANSPORT_NAMES)}. Parametryzacja przestała generować wariant, "
        "a suita nadal jest zielona — dowodzi wtedy połowy tego, co deklaruje"
    )
    assert min(used.values()) >= MINIMUM_BUILDS_PER_TRANSPORT, (
        f"liczba przebiegów na wariant: {dict(used)}, minimum {MINIMUM_BUILDS_PER_TRANSPORT}. "
        "Któryś test przestał się wykonywać"
    )
    assert len(set(used.values())) == 1, (
        f"warianty przebiegły niesymetrycznie: {dict(used)}. Kontrakt porównuje dwie "
        "implementacje na tych samych przypadkach — nierówna liczba znaczy, że nie na tych samych"
    )

    classes = {name: {built for used_name, built in BUILDS if used_name == name} for name in used}
    for name, built in classes.items():
        assert len(built) == 1, f"{name}: zbudowano więcej niż jedną klasę {sorted(built)}"
    distinct = {next(iter(built)) for built in classes.values()}
    assert distinct == {"PostgresJobTransport", "InMemoryJobTransport"}, (
        f"oba warianty zbudowały {sorted(distinct)} — dwa warianty tej samej klasy nie są "
        "dwiema implementacjami portu"
    )
