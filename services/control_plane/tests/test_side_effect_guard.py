"""The writing gate: the guard, the adapter and the `execution_attempts` row — DoD L5217, L5334.

WHAT IS MEASURED HERE AND WHAT IS NOT, before anything else. This file measures the ADAPTER — a
real object, called directly, with a real PostgreSQL underneath. It has detection power today.
The other half of L5217, "nothing in the repository bypasses this adapter", is
`test_no_bypass_static.py`, and that one is EMPTY in phase E1.F1 because no writing path exists
yet; its docstring says so in as many words.

EVERY TEST BELOW WAS SEEN RED. In this repository a green suite is not evidence: the red team
measured a fencing tuple that could be shortened in four statements with nobody noticing, and an
egress-policy table whose every row was already caught by an earlier condition, so deleting the
real mechanism passed 36 tests. So each guarantee here was measured by breaking the mechanism in
the production file, watching THIS test go red, and restoring the file. The ledger:

| guarantee | test | mutation, and what the test then said |
| --- | --- | --- |
| no proof, no write | `..._without_an_approval_is_refused` | `guard.py`: delete the `authorization is None` branch → `AttributeError: 'NoneType' object has no attribute 'consumed_intent'` |
| a refusal touches no platform | `..._without_an_approval_is_refused` | `adapter.py`: call `port.submit` before the guard → `port został wywołany mimo odmowy — side effect wyciekł` |
| the proof is in the signature | `..._is_a_required_argument_of_...` | `adapter.py`: give `authorization` a default of `None` → `authorization ma wartość domyślną` |
| an unconsumed grant is not a proof | `..._never_consumed_is_refused` | `guard.py`: delete the `consumed_at is None` branch → `DID NOT RAISE SideEffectRefused` |
| the proof binds to THIS content | `..._for_other_content_...` | `guard.py`: delete the digest comparison → `DID NOT RAISE SideEffectRefused` |
| a consumed grant still expires | `..._grant_deadline_has_passed_...` | `guard.py`: delete the `expires_at` branch → `DID NOT RAISE SideEffectRefused` |
| the guard says yes as well as no | `..._authorizes_exactly_this_content` | (positive control — nothing to mutate; it is what stops a guard that refuses everything from passing) |
| the row says `SUBMIT_STARTED` while the platform call is in flight | `..._in_flight_...` | `adapter.py`: move the whole state march after `port.submit`, keeping every arrow legal → `w trakcie wywołania platformy wiersz mówił 'PREPARED'` |
| a port that raises forbids an automatic retry | `..._leaves_the_attempt_unknown_...` | `adapter.py`: record `FAILED_BEFORE_SIDE_EFFECT` instead of `UNKNOWN` → `assert FAILED_BEFORE_SIDE_EFFECT is UNKNOWN`, and `may_retry()` flips to `True` |
| one intent, one attempt | `..._second_side_effect_on_one_intent_...` | `store.py`: invert the constraint-name test so the violation is re-raised → `asyncpg.exceptions.UniqueViolationError … "execution_attempts_one_per_intent"` |
| zero updated rows is a refusal | `..._is_a_refusal_not_a_success` | `store.py`: delete the `if row is None: raise` in `move_attempt` → `TypeError: 'NoneType' object is not subscriptable` |

WHAT WAS **NOT** MUTATED, so that nobody reads the table above as covering more than it does:

  * **the `UNIQUE (intent_id)` index itself.** It lives in `db/migrations/0003_f1_kernel.sql`,
    outside this package, and removing it would mean editing an applied migration. So the
    mutation above measures the TRANSLATION of the violation into a named refusal, not the index.
    What guards the property itself is the assertion in the same test that the port was called
    exactly ONCE: if the index disappeared, the second `perform` would go through and that count
    would be two.
  * **rule 4 of `store.py`'s docstring — "no moment comes from the process clock".** It is true of
    the code as written and NOTHING TESTS IT. `approval/store.py` has a grep test for the same
    rule; this package does not. If you edit `store.py`, keep the property by hand.

THE SECOND RULE OF THIS FILE: THE DATA MUST REACH THE BRANCH IT CLAIMS TO TEST. `guard.py` checks
four things in a fixed order, so a test for the fourth has to pass the first three. The expired
grant below is therefore a grant that is CONSUMED and whose digest MATCHES — anything else would
be caught earlier and the test would measure a branch it does not name. This is the trap that made
the reaper's expiry condition deletable for weeks: every test handed it a lease that was already
dead.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from community_os_contracts import SemanticCapability
from community_os_contracts.enums import ExecutionAttemptState
from community_os_contracts.execution import (
    ActionProposalState,
    ApprovalHashInput,
    AutomationRun,
    ExecutionIntent,
    SideEffectAuthorization,
)
from control_plane.approval.store import ApprovalStore
from control_plane.execution.adapter import (
    SideEffectAdapter,
    UnknownSideEffectOutcome,
)
from control_plane.execution.guard import SideEffectRefused, require_authorization
from control_plane.execution.store import (
    AttemptAlreadyRecorded,
    AttemptTransitionRefused,
    ExecutionStore,
)

#: Fixed, not `uuid4()`: two calls to `_hash_input` with the same arguments have to produce the
#: same digest, because half of these tests are about a digest matching or not matching.
_ORGANIZATION = UUID("11111111-1111-4111-8111-111111111111")
_WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
_ACCOUNT = UUID("44444444-4444-4444-8444-444444444444")
_ASSIGNMENT = UUID("33333333-3333-4333-8333-333333333333")
_PROFILE_VERSION = UUID("55555555-5555-4555-8555-555555555555")
_PLAN_VERSION = UUID("66666666-6666-4666-8666-666666666666")
_MEMORY_SNAPSHOT = UUID("77777777-7777-4777-8777-777777777777")

#: A moment with a timezone. The pure tests compare it against `expires_at`, and a naive datetime
#: raises `TypeError` in that comparison rather than failing the assertion — a test that dies of a
#: type error measures nothing.
_MOMENT = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)

ADAPTER_VERSION = "f1-gate-adapter@0.1.0"


def _hash_input(
    *,
    organization: UUID = _ORGANIZATION,
    workspace: UUID = _WORKSPACE,
    account: UUID = _ACCOUNT,
    **overrides: object,
) -> ApprovalHashInput:
    """The fourteen inputs of the approval digest (L2778-2793), with everything pinned."""
    fields: dict[str, object] = {
        "organization": organization,
        "workspace": workspace,
        "assignment_version": _ASSIGNMENT,
        "account": account,
        "platform": "linkedin",
        "action_type": SemanticCapability.PUBLISH_REPLY,
        "target_external_id": "urn:li:activity:7000000000000000009",
        "target_url": "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000009/",
        "source_fingerprint": "sha256:zrodlo-bramki",
        "text_and_media": "Treść, na którą operator wyraził zgodę.",
        "intelligence_profile_version": _PROFILE_VERSION,
        "plan_version": _PLAN_VERSION,
        "memory_snapshot": _MEMORY_SNAPSHOT,
        "adapter_version": ADAPTER_VERSION,
    }
    fields.update(overrides)
    return ApprovalHashInput(**fields)


def _proof_for(
    content: ApprovalHashInput,
    *,
    issued_at: datetime = _MOMENT - timedelta(minutes=5),
    expires_at: datetime = _MOMENT + timedelta(minutes=30),
) -> SideEffectAuthorization:
    """A hand-built VALID proof, for the tests that must not touch a database.

    Built out of the real contracts types and never with `model_construct`, except in the one test
    whose whole subject is a proof that skipped the validators — and that one builds its object
    inline rather than through a `consumed=False` switch here, so that this factory produces
    nothing but well-formed proofs and a reader cannot mistake one kind for the other.
    """
    digest = content.payload_hash()
    intent = ExecutionIntent(
        id=uuid4(),
        revision_id=uuid4(),
        payload_hash=digest,
        account_id=content.account,
        assignment_version_id=content.assignment_version,
        issued_at=issued_at,
        expires_at=max(expires_at, issued_at + timedelta(seconds=1)),
    )
    intent = intent.consume(issued_at)
    # The deadline is applied AFTER the consumption, so that an expired grant can also be a
    # consumed one: `consume` refuses a dead intent, and the case this file needs is a grant that
    # was consumed while alive and has since died.
    intent = intent.model_copy(update={"expires_at": expires_at})
    return SideEffectAuthorization(
        revision_id=intent.revision_id,
        payload_hash=digest,
        intent_id=intent.id,
        authorized_at=issued_at,
        consumed_intent=intent,
    )


class _RecordingPort:
    """A port that counts its invocations. F1 has no real implementation of the protocol."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[ApprovalHashInput] = []
        self._failure = failure

    async def submit(self, content: ApprovalHashInput) -> None:
        self.calls.append(content)
        if self._failure is not None:
            raise self._failure


class _PortReadingTheRowMidFlight:
    """A port that asks PostgreSQL what the attempt row says WHILE the platform call is running.

    Its own connection, therefore its own transaction: it can only see what the adapter has
    already committed. That is exactly the question — after a crash, the recovered state is
    whatever was committed before the platform was touched.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.status_seen: str | None = None

    async def submit(self, content: ApprovalHashInput) -> None:
        async with self._pool.acquire() as connection:
            self.status_seen = await connection.fetchval(
                "SELECT status FROM execution_attempts WHERE payload_hash = $1",
                content.payload_hash(),
            )


# ---------------------------------------------------------------------------
# Helpers that build a REAL consent in PostgreSQL, through the approval store.
# ---------------------------------------------------------------------------


async def _social_account(pool, organization_id: UUID) -> UUID:
    """One `social_accounts` row — `action_proposals` has a composite FK to it (D-020)."""
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'linkedin', $2)
            RETURNING id
            """,
            organization_id,
            f"external-{uuid4()}",
        )


async def _authorized_content(pool, tenant) -> tuple[ApprovalHashInput, SideEffectAuthorization]:
    """The whole approval path, end to end, through the store nobody in this file re-implements.

    proposal → revision → the operator's decision → the single-use grant → the consumed proof.
    The proof that comes out is the one `ApprovalStore.authorize_and_consume_intent` produced,
    which is the only production source of one.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    content = _hash_input(
        organization=tenant["organization"], workspace=tenant["workspace"], account=account
    )
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    async with pool.acquire() as connection:
        moment = await connection.fetchval("SELECT now()")
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=content,
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
        expires_at=moment + timedelta(minutes=30),
        actor_ref=f"operator:{tenant['operator']}",
    )
    proof = await store.authorize_and_consume_intent(
        intent.id, organization_id=tenant["organization"], actor_ref="test:brama-zapisu"
    )
    return content, proof


async def _attempt_rows(pool, organization_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as connection:
        return list(
            await connection.fetch(
                "SELECT id, status, intent_id, payload_hash, adapter_version, finished_at "
                "FROM execution_attempts WHERE organization_id = $1",
                organization_id,
            )
        )


# ---------------------------------------------------------------------------
# The guard, with no database in sight
# ---------------------------------------------------------------------------


def test_a_well_formed_proof_authorizes_exactly_this_content() -> None:
    """The POSITIVE CONTROL of the whole guard: it says yes, not only no.

    Without this one, every refusal below is also satisfied by a `require_authorization` that
    raises unconditionally — which is a mechanism nobody would notice was broken until the first
    real write in F2 could not happen at all.
    """
    content = _hash_input()
    proof = _proof_for(content)

    returned = require_authorization(content=content, authorization=proof, now=_MOMENT)

    assert returned is proof, "strażnik przepuścił, ale nie oddał dowodu, który przepuścił"


@pytest.mark.db
async def test_a_manual_adapter_call_without_an_approval_is_refused(pool, tenant) -> None:
    """L5334 in one call: no HTTP, no route, no session — the adapter object, called by hand.

    Three assertions, because "it raised" is the weakest of the three: the port must not have been
    touched, and `execution_attempts` must be empty. A guard that refused AFTER the write would
    satisfy the exception and leak the side effect.
    """
    port = _RecordingPort()
    adapter = SideEffectAdapter(ExecutionStore(pool), port)
    content = _hash_input(organization=tenant["organization"], workspace=tenant["workspace"])

    with pytest.raises(SideEffectRefused, match="Brak dowodu autoryzacji"):
        # Deliberately violating the annotation: `perform` types this argument as required and
        # non-optional, and L5334 is about precisely the caller who ignores that.
        await adapter.perform(content, authorization=None)  # type: ignore[arg-type]

    assert port.calls == [], "port został wywołany mimo odmowy — side effect wyciekł"
    assert await _attempt_rows(pool, tenant["organization"]) == [], (
        "odmowa zostawiła wiersz w execution_attempts — próba, której nikt nie autoryzował"
    )


def test_the_authorization_is_a_required_argument_of_the_only_write_path() -> None:
    """The proof is in the SIGNATURE. A call without it does not run at all.

    Two halves, and the second is the one with teeth: `inspect.signature` pins the shape, and the
    call below proves the interpreter refuses before the body starts. The coroutine is never
    created, so there is nothing to await and nothing to leak.

    The absent parameters are asserted too: `organization_id`, `workspace_id` and `payload_hash`
    are NOT arguments of `perform`, because each of them is read off the content or the proof. An
    argument here would let a caller file a run under a tenant, or against a payload, that the
    consent does not name.
    """
    signature = inspect.signature(SideEffectAdapter.perform)
    authorization = signature.parameters["authorization"]

    assert authorization.default is inspect.Parameter.empty, (
        "authorization ma wartość domyślną — dowód autoryzacji przestał być wymagany przez "
        "sygnaturę i wrócił do bycia konwencją (L5217)"
    )
    assert authorization.kind is inspect.Parameter.KEYWORD_ONLY
    forbidden = {"organization_id", "workspace_id", "payload_hash", "intent_id", "revision_id"}
    assert not forbidden.intersection(signature.parameters), (
        f"perform przyjmuje {forbidden.intersection(signature.parameters)} — te wartości mają "
        "pochodzić z treści i z dowodu, nie od wywołującego"
    )

    adapter = SideEffectAdapter(store=None, port=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="authorization"):
        adapter.perform(_hash_input())  # type: ignore[call-arg]


def test_a_proof_whose_intent_was_never_consumed_is_refused() -> None:
    """A `SideEffectAuthorization` built around a live grant is not a proof of anything.

    The object can only be built with `model_construct`, which skips every pydantic validator —
    including the one in `contracts` that says a proof is born after the consumption, not before
    (L2821). That is the point: the model's validator protects the honest caller, and this branch
    protects against the one who went around it.

    The data reaches THIS branch and not the next: the digest matches and the deadline is in the
    future, so the only thing wrong with this proof is the unconsumed grant.
    """
    content = _hash_input()
    live = ExecutionIntent(
        id=uuid4(),
        revision_id=uuid4(),
        payload_hash=content.payload_hash(),
        account_id=content.account,
        assignment_version_id=content.assignment_version,
        issued_at=_MOMENT - timedelta(minutes=5),
        expires_at=_MOMENT + timedelta(minutes=30),
    )
    assert live.consumed_at is None, "przygotowany intent miał być ŻYWY — test mierzyłby nie to"
    forged = SideEffectAuthorization.model_construct(
        revision_id=live.revision_id,
        payload_hash=live.payload_hash,
        intent_id=live.id,
        authorized_at=_MOMENT,
        consumed_intent=live,
    )

    with pytest.raises(SideEffectRefused, match="nikt nie zużył"):
        require_authorization(content=content, authorization=forged, now=_MOMENT)


def test_a_proof_for_other_content_does_not_authorize_this_content() -> None:
    """The consent points at the exact payload hash; the edited text is a different action (L5218).

    The proof here is entirely valid — consumed, alive, well-formed. The only thing that differs
    is the text, which is one of the fourteen digest inputs. So the data passes the first two
    branches and is stopped by the third, which is the branch this test names.

    The pair is asserted in both directions inside one test on purpose: the same proof MUST
    authorize the content it was issued for. Without that half, a guard that refuses everything
    passes.
    """
    approved = _hash_input()
    edited = _hash_input(text_and_media="Treść, której operator nigdy nie widział.")
    assert approved.payload_hash() != edited.payload_hash(), (
        "obie treści mają ten sam digest — przypadek nie powstał"
    )
    proof = _proof_for(approved)

    assert require_authorization(content=approved, authorization=proof, now=_MOMENT) is proof

    with pytest.raises(SideEffectRefused, match="innej treści"):
        require_authorization(content=edited, authorization=proof, now=_MOMENT)


def test_a_proof_whose_grant_deadline_has_passed_is_refused() -> None:
    """A stale execution grant is single-use AND it expires (L5692). Consuming it stops neither.

    The grant below was consumed while it was alive and has since died — which is the only shape
    that reaches this branch. A grant that had never been consumed would be stopped two branches
    earlier, and a digest mismatch one branch earlier; both would leave this condition deletable.
    The positive control is the same proof one second BEFORE the deadline.
    """
    content = _hash_input()
    deadline = _MOMENT - timedelta(seconds=1)
    proof = _proof_for(content, issued_at=_MOMENT - timedelta(minutes=10), expires_at=deadline)
    assert proof.consumed_intent.consumed_at is not None, "grant miał być ZUŻYTY"

    just_alive = deadline - timedelta(seconds=1)
    assert require_authorization(content=content, authorization=proof, now=just_alive) is proof

    with pytest.raises(SideEffectRefused, match="wygasł"):
        require_authorization(content=content, authorization=proof, now=_MOMENT)


# ---------------------------------------------------------------------------
# The adapter against a real PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_a_confirmed_side_effect_records_the_run_bound_to_the_approved_payload(
    pool, tenant
) -> None:
    """The happy path, end to end, and the row it leaves behind.

    Everything the run says about WHAT was published is read off the proof, so this test compares
    the row with the consent rather than with the arguments it passed: same intent, same digest,
    same tenant. A run recorded against a payload nobody approved would be evidence pointing at
    something that did not happen.
    """
    content, proof = await _authorized_content(pool, tenant)
    port = _RecordingPort()
    adapter = SideEffectAdapter(ExecutionStore(pool), port)

    run = await adapter.perform(content, authorization=proof)

    assert run.state is ExecutionAttemptState.CONFIRMED
    assert run.payload_hash == content.payload_hash() == proof.payload_hash
    assert run.intent_id == proof.intent_id
    assert run.revision_id == proof.revision_id
    assert run.finished_at is not None, "przebieg CONFIRMED bez momentu zakończenia"
    assert [item.payload_hash() for item in port.calls] == [content.payload_hash()], (
        "port dostał inną treść niż ta, której digest sprawdził strażnik"
    )

    rows = await _attempt_rows(pool, tenant["organization"])
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "CONFIRMED"
    assert rows[0]["intent_id"] == proof.intent_id
    assert rows[0]["adapter_version"] == content.adapter_version


@pytest.mark.db
async def test_while_the_platform_call_is_in_flight_the_attempt_already_says_submit_started(
    pool, tenant
) -> None:
    """The ordering that decides what a crash leaves behind (L2673, L4560, L5335).

    The port reads the committed row on its own connection while the adapter is awaiting it. If
    the transition happens after the platform call, the row a dying process leaves says `PREPARED`
    — a state out of which the machine permits another attempt, i.e. a second publication of
    something that may already be published.
    """
    content, proof = await _authorized_content(pool, tenant)
    port = _PortReadingTheRowMidFlight(pool)
    adapter = SideEffectAdapter(ExecutionStore(pool), port)

    await adapter.perform(content, authorization=proof)

    assert port.status_seen == ExecutionAttemptState.SUBMIT_STARTED.value, (
        f"w trakcie wywołania platformy wiersz mówił {port.status_seen!r}. Proces, który umrze "
        "w tym momencie, zostawia stan pozwalający na automatyczne ponowienie — czyli drugą "
        "publikację tej samej treści (L2673, L5335)"
    )


@pytest.mark.db
async def test_a_port_that_raises_leaves_the_attempt_unknown_and_forbids_automatic_retry(
    pool, tenant
) -> None:
    """An exception out of the platform call does not mean the platform wrote nothing (L5337).

    `UNKNOWN` is the pessimistic reading and the only honest one: the connection can drop after
    the request was accepted. The assertion that matters is not the state's name but what the
    machine says about it — `may_retry()` is `False`, and it is read off the transition table.
    """
    content, proof = await _authorized_content(pool, tenant)
    port = _RecordingPort(failure=RuntimeError("połączenie zerwane w trakcie submitu"))
    adapter = SideEffectAdapter(ExecutionStore(pool), port)

    with pytest.raises(UnknownSideEffectOutcome) as refusal:
        await adapter.perform(content, authorization=proof)

    run = refusal.value.run
    assert run.state is ExecutionAttemptState.UNKNOWN
    assert run.may_retry() is False, (
        "stan po nieudanym submicie pozwala na automatyczne ponowienie — a side effect mógł się "
        "już wydarzyć po stronie platformy (L2673, L5335)"
    )
    assert isinstance(refusal.value.__cause__, RuntimeError)

    rows = await _attempt_rows(pool, tenant["organization"])
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "UNKNOWN"


@pytest.mark.db
async def test_a_second_side_effect_on_one_intent_is_refused_by_the_database(pool, tenant) -> None:
    """One intent, at most one attempt (L2821) — and the refusal comes from the INDEX.

    The guard has nothing to say here: the proof is the same valid proof, for the same content,
    inside its deadline. Everything a value check could look at is fine. What stops the second
    write is `UNIQUE (intent_id)` on `execution_attempts`, which is also what stops it after a
    restart and in a second process.
    """
    content, proof = await _authorized_content(pool, tenant)
    port = _RecordingPort()
    adapter = SideEffectAdapter(ExecutionStore(pool), port)

    await adapter.perform(content, authorization=proof)

    with pytest.raises(AttemptAlreadyRecorded, match="ma już próbę wykonania"):
        await adapter.perform(content, authorization=proof)

    assert len(port.calls) == 1, (
        f"port został wywołany {len(port.calls)} razy dla jednego intentu — to jest druga "
        "publikacja tej samej treści (L2821)"
    )
    rows = await _attempt_rows(pool, tenant["organization"])
    assert len(rows) == 1, rows


@pytest.mark.db
async def test_finishing_an_attempt_that_is_not_there_is_a_refusal_not_a_success(
    pool, tenant
) -> None:
    """Zero updated rows RAISES. `asyncpg` returns the string `"UPDATE 0"` and does not.

    The positive control for the same statement is every test above: they all move an attempt
    through three states and read the result back. What is unproven without this test is the
    other branch — the one where the row is not there and the driver reports success anyway.
    """
    store = ExecutionStore(pool)
    ghost = AutomationRun(
        execution_attempt_id=uuid4(),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        intent_id=uuid4(),
        revision_id=uuid4(),
        payload_hash="0" * 64,
        attempt_no=1,
        state=ExecutionAttemptState.PREPARED,
        adapter_version=ADAPTER_VERSION,
        started_at=datetime.now(UTC),
    )

    with pytest.raises(AttemptTransitionRefused, match="zero zaktualizowanych wierszy"):
        await store.move_attempt(ghost, target=ExecutionAttemptState.DISPATCHED)
