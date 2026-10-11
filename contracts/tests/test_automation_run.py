"""`SideEffectAuthorization` and `AutomationRun` — the proof, and the row it is written into.

Why the proof exists at all: `authorize_side_effect` returned `None`, so the only test one could
write about it was "it did not raise". "It did not raise" is the same class of evidence as a gate
reporting `GO` before the measurement — the caller has nothing in hand that says WHICH revision
and WHICH payload the operator consented to. `authorize_and_consume` returns that object.

`AutomationRun` is a TYPE over the `execution_attempts` table and NOT a table of its own —
decision D-020. The chain `automation_runs` does not occur in the roadmap even once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from community_os_contracts import (
    ActionProposalRevision,
    ActionProposalState,
    ApprovalDecision,
    ApprovalHashInput,
    AutomationRun,
    ExecutionAttemptState,
    ExecutionIntent,
    SemanticCapability,
    SideEffectAuthorization,
    authorize_and_consume,
    authorize_side_effect,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _hash_input(**overrides) -> ApprovalHashInput:
    base = {
        "organization": uuid4(),
        "workspace": uuid4(),
        "assignment_version": uuid4(),
        "account": uuid4(),
        "platform": "reddit",
        "action_type": SemanticCapability.PUBLISH_REPLY,
        "target_external_id": "t3_abc123",
        "target_url": "https://example.test/r/x/comments/abc123",
        "source_fingerprint": "sha256:zrodlo",
        "text_and_media": "Odpowiedź merytoryczna z konkretem.",
        "intelligence_profile_version": uuid4(),
        "plan_version": uuid4(),
        "memory_snapshot": uuid4(),
        "adapter_version": "reddit-1.0.0",
    }
    base.update(overrides)
    return ApprovalHashInput(**base)


def _revision() -> ActionProposalRevision:
    return ActionProposalRevision.create(
        id=uuid4(),
        proposal_id=uuid4(),
        revision_no=1,
        state=ActionProposalState.APPROVED,
        hash_input=_hash_input(),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=90),
        source_observed_at=NOW,
    )


def _approval(revision: ActionProposalRevision) -> ApprovalDecision:
    return ApprovalDecision(
        id=uuid4(),
        revision_id=revision.id,
        approved_payload_hash=revision.payload_hash,
        approved_by=uuid4(),
        approved_at=NOW,
    )


def _intent(revision: ActionProposalRevision) -> ExecutionIntent:
    return ExecutionIntent(
        id=uuid4(),
        revision_id=revision.id,
        payload_hash=revision.payload_hash,
        account_id=uuid4(),
        assignment_version_id=uuid4(),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


def test_authorization_returns_a_proof_not_none() -> None:
    """The authorized path hands back an object naming the revision, the payload and the intent."""
    revision = _revision()
    intent = _intent(revision)

    proof = authorize_and_consume(
        revision=revision, approval=_approval(revision), intent=intent, now=NOW
    )

    assert proof is not None
    assert isinstance(proof, SideEffectAuthorization)
    assert proof.revision_id == revision.id
    assert proof.payload_hash == revision.payload_hash
    assert proof.intent_id == intent.id
    assert proof.authorized_at == NOW
    # The intent is burnt inside the same call — an authorization that leaves the intent unused
    # is exactly the second automatic submit attempt L2821 forbids.
    assert proof.consumed_intent.consumed_at == NOW

    with pytest.raises(ValidationError):
        proof.payload_hash = "0" * 64


def test_the_bare_check_still_returns_nothing_and_burns_nothing() -> None:
    """Why the second function exists at all — pinned so nobody "tidies it up" into one.

    `authorize_side_effect` is the CHECK: it returns `None` and leaves the intent unspent, so the
    strongest sentence a caller can write about it is "it did not raise", and a second attempt is
    still possible one restart later. That is the same class of evidence as a gate announcing `GO`
    before the measurement. `authorize_and_consume` is the PATH: it runs the same checks, burns
    the intent and hands back the proof (L5217, L2821).
    """
    revision = _revision()
    intent = _intent(revision)

    assert (
        authorize_side_effect(
            revision=revision, approval=_approval(revision), intent=intent, now=NOW
        )
        is None
    )
    assert intent.consumed_at is None, (
        "sama kontrola zużyła intent — wtedy dwie funkcje byłyby tą samą funkcją"
    )

    proof = authorize_and_consume(
        revision=revision, approval=_approval(revision), intent=intent, now=NOW
    )
    assert proof.consumed_intent.consumed_at == NOW


def test_a_proof_cannot_be_built_for_an_unconsumed_intent() -> None:
    """The proof says the intent was spent. It must not be constructible when it was not."""
    revision = _revision()
    intent = _intent(revision)
    with pytest.raises(ValidationError, match="zużyt"):
        SideEffectAuthorization(
            revision_id=revision.id,
            payload_hash=revision.payload_hash,
            intent_id=intent.id,
            authorized_at=NOW,
            consumed_intent=intent,
        )


def test_the_same_intent_cannot_authorize_twice() -> None:
    """L2821: at most one automatic submit attempt per ExecutionIntent."""
    revision = _revision()
    intent = _intent(revision)
    proof = authorize_and_consume(
        revision=revision, approval=_approval(revision), intent=intent, now=NOW
    )
    with pytest.raises(PermissionError, match="zużyt"):
        authorize_and_consume(
            revision=revision,
            approval=_approval(revision),
            intent=proof.consumed_intent,
            now=NOW + timedelta(seconds=1),
        )


def test_automation_run_is_frozen_and_carries_the_payload_hash() -> None:
    """L5198: immutable `AutomationRun` schema with a payload hash. D-020: over `execution_attempts`."""
    revision = _revision()
    intent = _intent(revision)
    proof = authorize_and_consume(
        revision=revision, approval=_approval(revision), intent=intent, now=NOW
    )

    run = AutomationRun.from_authorization(
        proof,
        execution_attempt_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        attempt_no=1,
        state=ExecutionAttemptState.PREPARED,
        adapter_version="reddit-1.0.0",
        started_at=NOW,
    )

    assert run.payload_hash == revision.payload_hash
    assert run.intent_id == intent.id
    assert run.revision_id == revision.id

    with pytest.raises(ValidationError):
        run.payload_hash = "0" * 64
    with pytest.raises(ValidationError):
        run.state = ExecutionAttemptState.CONFIRMED

    # Closed model: a field nobody declared must not travel along with the run.
    with pytest.raises(ValidationError):
        AutomationRun(**run.model_dump(), automation_run_table_id=uuid4())

    # The run knows the retry rule instead of every caller re-deriving it (L2673, L5335).
    assert run.may_retry() is True
    assert (
        run.model_copy(update={"state": ExecutionAttemptState.SUBMIT_STARTED}).may_retry() is False
    )
