"""Single use of `ExecutionIntent` — decided in PostgreSQL, not in a Python object (L2821, L5692).

THE HOLE THIS FILE CLOSES, quoted from the type it closes it on:

    `ExecutionIntent.consume()` → "Returns the consumed intent."

It returns a NEW object built with `model_copy`, because the model is frozen. The intent the
caller was holding is unchanged: its `consumed_at` is still `None`, `authorize_side_effect` still
lets it through, and a caller that ignores the return value authorizes an unbounded number of
submits. Every in-memory test of single use passes anyway — including one that runs `consume()`
twice on the same object and sees the second call raise, because that call reads the object it was
given, and nobody promised the caller kept it.

So the two tests here are the two things an in-memory test cannot establish:

*   two GENUINELY PARALLEL transactions against a real server leave exactly one winner, and the
    loser loses because PostgreSQL matched no row — not because a Python object said so;
*   a consumed intent is still consumed for a reader that shares NO memory with the writer.

Both are `pytest.mark.db`. There is no in-memory variant of either, and there must not be: an
in-memory version would pass with the mechanism deleted, which is precisely the failure mode the
red team measured in the egress-policy suite (36 green tests, no mechanism).
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from community_os_contracts import SemanticCapability
from community_os_contracts.execution import (
    ActionProposalState,
    ApprovalHashInput,
)
from control_plane.approval.store import (
    _CONSUME_INTENT_SQL,
    ApprovalStore,
    ApprovalStoreError,
    GrantAlreadyIssued,
    IntentAlreadyConsumed,
    IntentExpired,
)

pytestmark = [pytest.mark.db]

#: How many callers race for one grant. Two is the requirement; more only costs connections and
#: makes an accidental pass less likely.
CONTENDERS = 2

#: FIXED, like in `test_approval_persistence.py`: a digest that changes per call would make the
#: identity checks below agree with themselves by construction.
_FIXED = {
    "organization": UUID("11111111-1111-4111-8111-111111111111"),
    "workspace": UUID("22222222-2222-4222-8222-222222222222"),
    "assignment_version": UUID("33333333-3333-4333-8333-333333333333"),
    "account": UUID("44444444-4444-4444-8444-444444444444"),
    "intelligence_profile_version": UUID("55555555-5555-4555-8555-555555555555"),
    "plan_version": UUID("66666666-6666-4666-8666-666666666666"),
    "memory_snapshot": UUID("77777777-7777-4777-8777-777777777777"),
}


def _hash_input(**overrides) -> ApprovalHashInput:
    fields: dict[str, object] = {
        **_FIXED,
        "platform": "linkedin",
        "action_type": SemanticCapability.PUBLISH_REPLY,
        "target_external_id": "urn:li:activity:7000000000000000002",
        "target_url": "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000002/",
        "source_fingerprint": "sha256:zrodlo-2",
        "text_and_media": "Treść, na którą operator się zgodził.",
        "adapter_version": "linkedin-adapter@0.1.0",
    }
    fields.update(overrides)
    return ApprovalHashInput(**fields)


@pytest_asyncio.fixture
async def wide_pool():
    """A pool with room for every contender to hold its OWN connection at the same time.

    Deliberately not the `pool` fixture: with fewer connections than contenders the second
    transaction would queue behind the pool rather than behind PostgreSQL's row lock, and the race
    this file is about would be supplied by the client library. Same reasoning as `job_pool` in
    `test_job_claim.py`.

    The DSN check is duplicated here on purpose: `pytest.fail`, never `skipif`. A skipped database
    test reads as green in the summary (see the note at the top of `conftest.py`).
    """
    dsn = os.environ.get("WENEXT_TEST_DSN")
    if not dsn:
        pytest.fail(
            "brak WENEXT_TEST_DSN — ten test wymaga PostgreSQL. To jest PORAŻKA, nie pominięcie. "
            "Uruchom: REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh services/control_plane"
        )
    connection_pool = await asyncpg.create_pool(dsn, min_size=CONTENDERS, max_size=CONTENDERS + 4)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


async def _grant(pool, tenant, *, ttl: timedelta = timedelta(minutes=30)):
    """Everything up to a live, unconsumed grant: account, proposal, revision, consent, intent."""
    store = ApprovalStore(pool)
    async with pool.acquire() as connection:
        account = await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'linkedin', $2)
            RETURNING id
            """,
            tenant["organization"],
            f"external-{uuid4()}",
        )
        moment = await connection.fetchval("SELECT now()")

    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=_hash_input(),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=moment + timedelta(hours=1),
    )
    await store.record_decision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=ActionProposalState.APPROVED.value,
        operator_id=tenant["operator"],
    )
    intent = await store.issue_intent(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account,
        expires_at=moment + ttl,
        actor_ref=f"operator:{tenant['operator']}",
    )
    return store, revision, intent


async def test_two_concurrent_consumptions_leave_exactly_one_winner(wide_pool, tenant) -> None:
    """`CONTENDERS` callers, one grant. Exactly one success and exactly one failure.

    Both halves of that sentence are asserted. "At least one failed" would be satisfied by a run
    in which everything failed, and "one succeeded" by a run in which everything succeeded —
    together they pin the outcome to the only correct one.

    The strongest assertion here is the LAST one: exactly one `EXECUTION_INTENT_CONSUMED` row in
    the audit log. Two winners would leave two, and the audit log is the artefact a later reader
    would use to answer "was this post submitted twice".
    """
    store, _revision, intent = await _grant(wide_pool, tenant)
    actor = f"operator:{tenant['operator']}"

    async def contend(index: int):
        return await store.consume_intent(
            intent.id, organization_id=tenant["organization"], actor_ref=f"{actor}/{index}"
        )

    outcomes = await asyncio.gather(
        *(contend(index) for index in range(CONTENDERS)), return_exceptions=True
    )

    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]

    assert len(successes) == 1, (
        f"{len(successes)} zwycięzców na jeden ExecutionIntent. L2821: maksymalnie JEDNA "
        f"automatyczna próba submitu na intent. Wyniki: {outcomes}"
    )
    assert len(failures) == CONTENDERS - 1 == 1, outcomes
    assert all(isinstance(failure, IntentAlreadyConsumed) for failure in failures), failures
    assert successes[0].consumed_at is not None

    async with wide_pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, consumed_at FROM execution_intents WHERE id = $1", intent.id
        )
        consumed_entries = await connection.fetchval(
            """
            SELECT count(*) FROM audit_events
            WHERE organization_id = $1 AND kind = 'EXECUTION_INTENT_CONSUMED'
            """,
            tenant["organization"],
        )
    assert row["status"] == "CONSUMED"
    assert row["consumed_at"] is not None
    assert consumed_entries == 1, (
        f"{consumed_entries} wpisów audytowych o zużyciu jednego intentu — dziennik twierdzi, że "
        "grant jednorazowy został zużyty więcej niż raz"
    )


async def test_two_transactions_open_at_the_same_time_leave_exactly_one_winner(
    wide_pool, tenant
) -> None:
    """The same outcome, with the OVERLAP of the two transactions proven rather than hoped for.

    `asyncio.gather` above leaves the interleaving to the event loop: the result is right whatever
    happens, which also means the test would pass if the two calls ran strictly one after the
    other. That is a weaker claim than "two transactions were open at once", and this repository has
    a rule about hoping — `test_job_claim.py` drives its interleaving with explicit barriers, never
    with sleeps.

    So here the winner's transaction is HELD OPEN while the loser fires its own statement, and the
    loser is shown to be BLOCKED (a 0.5 s `wait_for` times out) before the winner commits. That
    timeout is the evidence of the overlap: an unblocked statement would have returned instantly.

    The statement under test is `approval.store._CONSUME_INTENT_SQL` itself, imported rather than
    retyped. A copy of the SQL here would keep passing after the real one lost its guard.
    """
    _store, _revision, intent = await _grant(wide_pool, tenant)

    winner_updated = asyncio.Event()
    winner_may_commit = asyncio.Event()

    async def winner():
        async with wide_pool.acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            row = await connection.fetchrow(_CONSUME_INTENT_SQL, intent.id, tenant["organization"])
            winner_updated.set()
            # The transaction stays OPEN, holding the row lock.
            await winner_may_commit.wait()
            await transaction.commit()
            return row

    async def loser():
        async with wide_pool.acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            await winner_updated.wait()
            # Blocks inside PostgreSQL until the winner commits, then re-evaluates the WHERE.
            row = await connection.fetchrow(_CONSUME_INTENT_SQL, intent.id, tenant["organization"])
            await transaction.commit()
            return row

    winner_task = asyncio.create_task(winner())
    loser_task = asyncio.create_task(loser())

    await asyncio.wait_for(winner_updated.wait(), timeout=10)
    # The proof of overlap: the loser cannot finish while the winner's transaction is open.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(loser_task), timeout=0.5)

    winner_may_commit.set()
    winner_row = await asyncio.wait_for(winner_task, timeout=10)
    loser_row = await asyncio.wait_for(loser_task, timeout=10)

    assert winner_row is not None, "zwycięzca nie dostał wiersza — CAS nie dopasował niczego"
    assert loser_row is None, (
        "przegrany też dostał wiersz: dwie równoległe transakcje zużyły jeden grant. To jest "
        "druga automatyczna próba submitu, której L2821 zabrania"
    )


async def test_a_consumed_intent_stays_consumed_across_processes(wide_pool, tenant) -> None:
    """A reader sharing NO memory with the writer still sees the grant as spent.

    Three things are asserted, and the third one is the whole point:

    1.  a SECOND pool, a SECOND `ApprovalStore` and therefore fresh connections read
        `status = CONSUMED` and a non-null `consumed_at`;
    2.  a consumption attempted through that second store is refused, by the database;
    3.  **the `ExecutionIntent` object the issuing call returned still has
        `consumed_at is None`.** That object is what an in-memory implementation would have
        consulted, and it says the grant is unspent. It is not the record. The row is.
    """
    store, _revision, intent = await _grant(wide_pool, tenant)
    actor = f"operator:{tenant['operator']}"

    consumed = await store.consume_intent(
        intent.id, organization_id=tenant["organization"], actor_ref=actor
    )
    assert consumed.consumed_at is not None

    # A different pool: different connections, nothing shared but PostgreSQL itself.
    other_pool = await asyncpg.create_pool(os.environ["WENEXT_TEST_DSN"], min_size=1, max_size=2)
    try:
        other_store = ApprovalStore(other_pool)
        reread = await other_store.fetch_intent(intent.id, organization_id=tenant["organization"])
        assert reread is not None
        assert reread.consumed_at is not None, (
            "świeże połączenie widzi intent jako niezużyty — zużycie zostało w pamięci procesu, "
            "a nie w bazie. Po restarcie ten grant autoryzuje drugi submit (L2821)"
        )
        assert reread.consumed_at == consumed.consumed_at

        async with other_pool.acquire() as connection:
            status = await connection.fetchval(
                "SELECT status FROM execution_intents WHERE id = $1", intent.id
            )
        assert status == "CONSUMED"

        with pytest.raises(IntentAlreadyConsumed):
            await other_store.consume_intent(
                intent.id, organization_id=tenant["organization"], actor_ref=actor
            )
    finally:
        await other_pool.close()

    # (3) The object that an in-memory single-use check would have trusted.
    assert intent.consumed_at is None, (
        "obiekt zwrócony przez issue_intent zmienił się w miejscu — to niemożliwe dla modelu "
        "frozen, więc ten test przestał opisywać rzeczywistość"
    )


async def test_a_second_grant_for_the_same_revision_and_account_is_refused_by_the_database(
    wide_pool, tenant
) -> None:
    """L2755, L5692: "one-time ExecutionIntent". The index enforces it, not a code path.

    `UNIQUE (organization_id, one_time_grant_hash)` in migration 0003 is described there as "grant
    nie da się wystawić dwa razy" — and it only means that because the value is DERIVED from what
    the grant binds. A random `one_time_grant_hash` would satisfy the index on every issuance and
    the sentence would be enforced by nothing.
    """
    store, revision, first = await _grant(wide_pool, tenant)
    async with wide_pool.acquire() as connection:
        account = await connection.fetchval(
            "SELECT account_id FROM execution_intents WHERE id = $1", first.id
        )
        moment = await connection.fetchval("SELECT now()")

    with pytest.raises(GrantAlreadyIssued):
        await store.issue_intent(
            revision_id=revision.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )

    async with wide_pool.acquire() as connection:
        grants = await connection.fetchval(
            "SELECT count(*) FROM execution_intents WHERE proposal_revision_id = $1", revision.id
        )
        issued_entries = await connection.fetchval(
            """
            SELECT count(*) FROM audit_events
            WHERE organization_id = $1 AND kind = 'EXECUTION_INTENT_ISSUED'
            """,
            tenant["organization"],
        )
    assert grants == 1, f"{grants} grantów na jedną rewizję — one-time znaczy jeden"
    assert issued_entries == 1, (
        f"{issued_entries} wpisów o wystawieniu grantu — odrzucone wystawienie zostawiło ślad "
        "w dzienniku, czyli transakcja nie została wycofana"
    )


async def test_an_intent_row_with_no_assignment_version_is_refused_not_patched_up(
    wide_pool, tenant
) -> None:
    """Two parts of the project disagree about this column, and the disagreement is made LOUD.

    `execution_intents.assignment_version_id` is NULLABLE — migration 0003 says why: there is no
    assignments table in F1 and "wpisywanie tam wymyślonego UUID-a byłoby udawaniem powiązania".
    `contracts.ExecutionIntent.assignment_version_id` is a REQUIRED `UUID`, because L5321 binds the
    grant to "the exact revision, account and assignment" and `ApprovalHashInput.assignment_version`
    is required for the same reason.

    So a row with a NULL there cannot become a valid `ExecutionIntent`. The only two honest answers
    are "refuse" and "change one of the two documents"; the dishonest one is a substituted
    identifier, which would be a grant claiming to be bound to an assignment nobody named.

    `issue_intent` never writes such a row — it reads the value off the revision's approved hash
    input — so the row here is aged into existence with raw SQL, which is also the proof that the
    refusal is about the DATA and not about the argument list.
    """
    store, _revision, intent = await _grant(wide_pool, tenant)

    # Positive control: while the column is set, the row reads back fine.
    assert await store.fetch_intent(intent.id, organization_id=tenant["organization"]) is not None

    async with wide_pool.acquire() as connection:
        blanked = await connection.fetchval(
            """
            UPDATE execution_intents SET assignment_version_id = NULL
            WHERE id = $1
            RETURNING id
            """,
            intent.id,
        )
    assert blanked == intent.id, "przygotowanie testu nie zmieniło ani jednego wiersza"

    with pytest.raises(ApprovalStoreError, match="assignment_version_id"):
        await store.fetch_intent(intent.id, organization_id=tenant["organization"])


async def test_an_expired_grant_is_refused_and_the_refusal_names_the_expiry(
    wide_pool, tenant
) -> None:
    """L5692: a stale execution grant is single-use AND it expires.

    `expires_at > now()` sits inside the consuming statement, so a grant that dies between a check
    and a write cannot be burned. The refusal is named by re-reading the row, which is why an
    expired grant reports expiry and not "already consumed" — two different facts for the operator,
    and only one of them means a submit may have happened.

    The negative control is the test above: the same statement, on a live grant, succeeds. Without
    that pairing, `expires_at > now()` could be a condition that never matches and every test here
    would still be green.
    """
    # A grant whose deadline is already behind us. `issue_intent` will not build one — the column
    # has `CHECK (expires_at > issued_at)` — so the row is aged with raw SQL afterwards, which is
    # also the only way the CHECK stays honest about newly issued grants.
    store, _revision, intent = await _grant(wide_pool, tenant)
    async with wide_pool.acquire() as connection:
        aged = await connection.fetchval(
            """
            UPDATE execution_intents
            SET issued_at = now() - interval '2 hours', expires_at = now() - interval '1 hour'
            WHERE id = $1
            RETURNING id
            """,
            intent.id,
        )
    assert aged == intent.id, "przygotowanie testu nie zmieniło ani jednego wiersza"

    with pytest.raises(IntentExpired, match="wygasł"):
        await store.consume_intent(
            intent.id,
            organization_id=tenant["organization"],
            actor_ref=f"operator:{tenant['operator']}",
        )

    async with wide_pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, consumed_at FROM execution_intents WHERE id = $1", intent.id
        )
    assert row["consumed_at"] is None
    assert row["status"] == "ISSUED", (
        "wygasły grant został oznaczony jako zużyty — odmowa i wykonanie wyglądają wtedy "
        "identycznie w dzienniku"
    )
