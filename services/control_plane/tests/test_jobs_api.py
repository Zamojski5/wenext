"""`GET /api/jobs` and `GET /api/jobs/{job_id}` — read from PostgreSQL, never from memory.

D-013 is explicit about what this screen is and is not: F1 has no producer of real content, so the
dashboard shows rows put there by the development path, and that path "nie wolno nazywać danymi
produkcyjnymi". What the screen must nevertheless be is HONEST — it shows what the canonical store
holds, so that the DoD "restart komponentu nie gubi taska" (L5212) can be demonstrated on it
rather than asserted about it.

The precedent NOT to repeat is `TicketRegistry` in
`services/stream_gateway/stream_gateway/tickets.py`: a registry in a `dict`, whose own docstring
calls it an F0 shortcut. Every one of its tests passes and the data is gone on restart. Hence the
first test here inserts a row with RAW SQL — through no code of this package at all — and demands
to see it in the response.

The second test is about a flag that must not exist twice. `may_retry_automatically` lives in
`contracts/execution.py` and the API is not allowed to keep its own copy of the condition: after
`SUBMIT_STARTED` the side effect may already have happened on the platform (L2673, L4560, L5335),
and a second copy of that rule is a place for the two to drift apart, silently, in the direction
of retrying a post that was already published.

The client is `logged_in_client` from `conftest.py` — a real session cookie obtained through
`POST /api/session`. Not a monkeypatched boundary: these routes sit behind the operator boundary
(D-016), they read the organization OUT OF THE SESSION, and a test that faked its way past the
door would also have to fake the scope, which is the thing being tested.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from community_os_contracts import (
    JOB_TRANSPORT_PORT_METHODS,
    ExecutionAttemptState,
    JobTransportPort,
    JobWaitingState,
)
from community_os_contracts.execution import may_retry_automatically
from control_plane.api.jobs import JOBS_PATH
from control_plane.domain import commands
from control_plane.domain.models import (
    BatchEmptiness,
    ClaimBatch,
    ClaimedJob,
    EnqueuedJob,
    JobOutcome,
)
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore

#: The eight states of the `ExecutionAttempt` machine (L2601-2605), with the answer written out by
#: hand. Deriving the expectation from `may_retry_automatically` alone would make the test agree
#: with the implementation by construction — it would prove the wiring and nothing about the rule.
#: Both are checked: the hand-written table is the detection power, the comparison with contracts
#: is the drift alarm.
RETRY_EXPECTATIONS: tuple[tuple[ExecutionAttemptState, bool], ...] = (
    (ExecutionAttemptState.PREPARED, True),
    (ExecutionAttemptState.DISPATCHED, True),
    (ExecutionAttemptState.SUBMIT_STARTED, False),
    (ExecutionAttemptState.CONFIRMED, False),
    (ExecutionAttemptState.UNKNOWN, False),
    (ExecutionAttemptState.FAILED_BEFORE_SIDE_EFFECT, True),
    (ExecutionAttemptState.CAPABILITY_BLOCKED, True),
    (ExecutionAttemptState.IDENTITY_BLOCKED, True),
)

_INSERT_JOB_SQL = """
INSERT INTO jobs (organization_id, workspace_id, job_type, routing_key, priority,
                  status, attempt_limit, idempotency_key)
VALUES ($1, $2, $3, $4, $5, $6, 5, $7)
RETURNING id
"""

_INSERT_CLOSED_ATTEMPT_SQL = """
INSERT INTO job_attempts
    (organization_id, job_id, attempt_no, dispatch_version, lease_generation, lease_owner,
     status, completed_at, failure_class)
VALUES ($1, $2, 1, 1, 1, gen_random_uuid(), 'FAILED', now(), $3)
"""


@pytest.mark.db
async def test_the_job_list_reads_from_the_database_not_from_process_memory(
    pool, tenant, logged_in_client
) -> None:
    """A row inserted with raw SQL, by nothing in this package, shows up in the response.

    That is the whole test. Any in-memory registry — and this repository already has one, see the
    module docstring — passes a test that enqueues through the API and then reads through the API.
    It cannot pass this one, because nothing ever told the process that the row exists.
    """
    idempotency_key = f"raw-sql-{uuid4()}"
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            _INSERT_JOB_SQL,
            tenant["organization"],
            tenant["workspace"],
            "raw.insert",
            "principal.plan",
            7,
            "READY",
            idempotency_key,
        )

    response = await logged_in_client.get(JOBS_PATH)
    assert response.status_code == 200, response.text
    body = response.json()

    # Cardinality first. An empty list satisfies every "no wrong row is present" check a weaker
    # version of this test might have made.
    assert body["count"] == 1, body
    assert len(body["jobs"]) == 1, body

    row = body["jobs"][0]
    assert row["job_id"] == str(job_id)
    assert row["job_type"] == "raw.insert"
    assert row["routing_key"] == "principal.plan"
    assert row["priority"] == 7
    assert row["status"] == "READY"
    assert row["attempt_limit"] == 5
    assert row["idempotency_key"] == idempotency_key
    assert row["attempt_count"] == 0
    assert row["holds_live_lease"] is False
    assert row["organization_id"] == str(tenant["organization"])

    # The detail route reads the same row from the same place.
    detail = await logged_in_client.get(f"{JOBS_PATH}/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["job"]["job_id"] == str(job_id)
    assert detail.json()["attempts"] == []
    assert detail.json()["attempts_used"] == 0

    # A job nobody wrote is a 404, not an empty envelope that looks like a record.
    missing = await logged_in_client.get(f"{JOBS_PATH}/{uuid4()}")
    assert missing.status_code == 404, missing.text


@pytest.mark.db
async def test_a_job_of_another_organization_is_not_visible(pool, tenant, logged_in_client) -> None:
    """The scope comes from the session. The query string is not an identity.

    Same rule and same wording as `api/audit.py`. Without it the list is a cross-tenant read the
    moment E1's "one organization" assumption stops holding, and the detail route becomes an
    existence oracle for identifiers that are not the caller's.
    """
    async with pool.acquire() as conn:
        stranger = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obcy-tenant') RETURNING id"
        )
        foreign_job = await conn.fetchval(
            _INSERT_JOB_SQL,
            stranger,
            None,
            "foreign.job",
            "principal.plan",
            0,
            "READY",
            f"foreign-{uuid4()}",
        )
        own_job = await conn.fetchval(
            _INSERT_JOB_SQL,
            tenant["organization"],
            tenant["workspace"],
            "own.job",
            "principal.plan",
            0,
            "READY",
            f"own-{uuid4()}",
        )

    listed = await logged_in_client.get(JOBS_PATH)
    assert listed.status_code == 200, listed.text
    identifiers = [row["job_id"] for row in listed.json()["jobs"]]

    # Cardinality before membership: an empty list would satisfy "the foreign job is absent".
    assert len(identifiers) == 1, identifiers
    assert identifiers == [str(own_job)]

    refused = await logged_in_client.get(f"{JOBS_PATH}/{foreign_job}")
    assert refused.status_code == 404, refused.text


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "expected_may_retry"),
    RETRY_EXPECTATIONS,
    ids=[state.value for state, _ in RETRY_EXPECTATIONS],
)
async def test_job_detail_marks_states_forbidding_automatic_retry(
    pool, tenant, logged_in_client, state, expected_may_retry
) -> None:
    """All EIGHT states of the machine, and the flag comes from contracts — not from a copy.

    `job_attempts.failure_class` is the column L3838 gives us for the class of an attempt's
    outcome, and for an attempt that reached the side-effect boundary that class IS the
    `ExecutionAttempt` state it ended in (L2601-2605). Nothing else in the row can answer "may
    this be retried automatically", because the attempt's own status (`RUNNING`, `SUCCEEDED`,
    `FAILED`, `LEASE_EXPIRED`) does not know whether a post was already submitted.
    """
    assert len(RETRY_EXPECTATIONS) == len(list(ExecutionAttemptState)) == 8, (
        f"maszyna ExecutionAttempt ma {len(list(ExecutionAttemptState))} stanów, a tabela w tym "
        f"pliku {len(RETRY_EXPECTATIONS)} — parametryzacja przestała obejmować wszystkie"
    )
    # The drift alarm: if contracts ever change their mind, it fails here instead of quietly
    # making the API and the document disagree.
    assert may_retry_automatically(state) is expected_may_retry, state

    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            _INSERT_JOB_SQL,
            tenant["organization"],
            tenant["workspace"],
            "retry.flag",
            "principal.plan",
            0,
            "RUNNING",
            f"retry-{uuid4()}",
        )
        await conn.execute(_INSERT_CLOSED_ATTEMPT_SQL, tenant["organization"], job_id, state.value)

    response = await logged_in_client.get(f"{JOBS_PATH}/{job_id}")
    assert response.status_code == 200, response.text
    attempts = response.json()["attempts"]

    assert len(attempts) == 1, attempts
    attempt = attempts[0]
    assert attempt["execution_attempt_state"] == state.value, attempt
    assert attempt["may_retry_automatically"] is expected_may_retry, attempt


@pytest.mark.db
async def test_an_unrecognised_failure_class_reads_as_unknown_never_as_permission(
    pool, tenant, logged_in_client
) -> None:
    """A `failure_class` outside the eight leaves the flag NULL, never `true`.

    This is the direction the mistake would run in. A row whose outcome nobody recorded, or
    recorded as something else, says nothing about whether the side effect happened — and
    "nothing" must not be rendered as "go ahead and retry". Absence of evidence is
    `NIE_URUCHOMIONO`, not `GO` (KONWENCJE.md).
    """
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            _INSERT_JOB_SQL,
            tenant["organization"],
            tenant["workspace"],
            "retry.flag",
            "principal.plan",
            0,
            "RUNNING",
            f"unknown-{uuid4()}",
        )
        await conn.execute(
            _INSERT_CLOSED_ATTEMPT_SQL, tenant["organization"], job_id, "connection reset"
        )

    response = await logged_in_client.get(f"{JOBS_PATH}/{job_id}")
    assert response.status_code == 200, response.text
    attempts = response.json()["attempts"]

    assert len(attempts) == 1, attempts
    assert attempts[0]["failure_class"] == "connection reset"
    assert attempts[0]["execution_attempt_state"] is None
    assert attempts[0]["may_retry_automatically"] is None, (
        "nierozpoznana klasa porażki została zamieniona na zgodę na automatyczne ponowienie — to "
        "jest fałszywe zaliczenie w najgroźniejszym miejscu, bo po SUBMIT_STARTED post może już "
        "być opublikowany (L2673)"
    )


class InMemoryQueue:
    """A transport that is NOT a database, and does not pretend to be one.

    It exists for exactly one line of the Definition of Done:

        "zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem
         kontraktowym portu" (L5216)

    The domain commands below run against this object unchanged. Everything the E3 broker will do
    differently lives behind the port; the commands never learn which side they are on. The
    methods this test does not exercise raise instead of returning a plausible value — a stub
    answering "fine" is how a fake starts proving things about itself.
    """

    def __init__(self) -> None:
        self.created: list[EnqueuedJob] = []
        self.closed: list[tuple[UUID, JobOutcome]] = []
        self.parked: list[tuple[UUID, JobWaitingState]] = []

    async def create_job(self, **fields) -> EnqueuedJob:
        enqueued = EnqueuedJob(job_id=uuid4(), created=True)
        self.created.append(enqueued)
        return enqueued

    async def claim(self, *, worker: UUID, limit: int, lease_seconds: float) -> ClaimBatch:
        claims = tuple(
            ClaimedJob(
                job_id=enqueued.job_id,
                organization_id=uuid4(),
                workspace_id=None,
                job_type="fake",
                routing_key="principal.plan",
                priority=0,
                payload_ref=None,
                dispatch_version=1,
                attempt_limit=5,
                attempt_id=uuid4(),
                attempt_no=1,
                lease_generation=1,
                lease_owner=uuid4(),
                expires_at=None,
            )
            for enqueued in self.created[:limit]
        )
        return ClaimBatch(
            claims=claims, due_before_claim=len(claims), scheduled_later=0, already_running=0
        )

    async def close_attempt(self, token, *, outcome, failure_class=None) -> None:
        self.closed.append((token.job_id, outcome))

    async def park_until_ready(self, token, *, waiting_state, delay_seconds) -> None:
        self.parked.append((token.job_id, waiting_state))

    async def publish_ready(self, job_id, *, not_before=None) -> None:
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")

    async def reserve(self, *, owner, limit, lease_seconds):
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")

    async def acknowledge(self, token) -> None:
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")

    async def requeue(self, token, *, not_before) -> None:
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")

    async def fetch_job(self, job_id, *, organization_id):
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")

    async def fetch_jobs(self, *, organization_id, limit=100):
        raise NotImplementedError("nieużywane w tym teście — atrapa nie udaje, że działa")


async def test_domain_commands_run_against_a_transport_that_is_not_a_database() -> None:
    """DoD L5216 — and it needs no PostgreSQL, which is the proof.

    If a domain command could reach a connection, this test could not exist. That is why
    `grep -rn asyncpg control_plane/domain/` has to come back empty, and why the port is a
    structural `Protocol` rather than a base class the transport inherits from.
    """
    queue = InMemoryQueue()
    assert isinstance(queue, JobQueuePort)

    enqueued = await commands.enqueue_job(
        queue,
        organization_id=uuid4(),
        workspace_id=uuid4(),
        job_type="fake",
        routing_key="principal.plan",
        priority=0,
        attempt_limit=5,
        idempotency_key="fake-1",
    )
    assert enqueued.created is True

    batch = await commands.claim_batch(queue, worker=uuid4(), limit=5, lease_seconds=60.0)
    assert len(batch.claims) == 1, batch.claims
    assert batch.emptiness is BatchEmptiness.NOT_EMPTY

    token = batch.claims[0].fencing_token
    await commands.report_result(queue, token=token, outcome=JobOutcome.SUCCEEDED)
    await commands.release_on_waiting(
        queue, token=token, waiting_state=JobWaitingState.WAITING_QUOTA, delay_seconds=5.0
    )

    # Counts first: three empty lists would satisfy every equality below.
    assert (len(queue.created), len(queue.closed), len(queue.parked)) == (1, 1, 1), (
        queue.created,
        queue.closed,
        queue.parked,
    )
    assert queue.closed[0] == (token.job_id, JobOutcome.SUCCEEDED)
    assert queue.parked[0] == (token.job_id, JobWaitingState.WAITING_QUOTA)


@pytest.mark.db
async def test_the_postgres_transport_is_the_port_the_domain_speaks_to(pool) -> None:
    """The implementation satisfies BOTH the contracts port and the queue port over it.

    `JobTransportPort` is the seam L5206 hides the broker behind, so the transport has to satisfy
    it by structure even though nothing in F1 has a broker yet. The method names from
    `JOB_TRANSPORT_PORT_METHODS` are checked as well, because `runtime_checkable` compares
    presence and not signatures — the contracts' own test says so in as many words.
    """
    transport = PostgresJobTransport(JobStore(pool), organization_id=uuid4())

    assert isinstance(transport, JobTransportPort)
    assert isinstance(transport, JobQueuePort)

    assert len(JOB_TRANSPORT_PORT_METHODS) >= 4
    missing = [name for name in JOB_TRANSPORT_PORT_METHODS if not hasattr(transport, name)]
    assert not missing, missing


@pytest.mark.db
async def test_a_read_only_transport_refuses_to_write(pool) -> None:
    """The instance the routes use has no organization, so it cannot claim or close anything.

    `api/jobs.py` builds the port that way on purpose. A read-only transport that silently
    defaulted to "some organization" would let a future handler act on a tenant nobody chose —
    and that is a mistake with no symptom.
    """
    read_only = PostgresJobTransport(JobStore(pool))

    with pytest.raises(RuntimeError, match="wyłącznie do odczytu"):
        await read_only.reserve(owner=uuid4(), limit=1, lease_seconds=60.0)
    with pytest.raises(RuntimeError, match="wyłącznie do odczytu"):
        await read_only.claim(worker=uuid4(), limit=1, lease_seconds=60.0)
