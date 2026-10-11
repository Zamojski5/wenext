"""`ActionProposalRevision.supersede` — the direction along which old content leaks out.

The docstring of `ActionProposalRevision` promised a `supersede` method from the very first
commit, and the class did not have one. The hole was not cosmetic: without it nothing moved the
previous revision to `SUPERSEDED`, so a revision approved before an edit stayed `APPROVED` and
`authorize_side_effect` let it through. The operator consented to text they no longer see.

Requirements defended here:
  * "Każda edycja tworzy nową rewizję i unieważnia poprzedni approval." (L2795)
  * "edycja draftu tworzy nową rewizję, unieważnia zgodę i nie przedłuża pierwotnego TTL" (L5539)
  * "APPROVED → SUPERSEDED, jeżeli powstanie nowa rewizja" (L2775)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from community_os_contracts import (
    ActionProposalRevision,
    ActionProposalState,
    ApprovalDecision,
    ApprovalHashInput,
    ExecutionIntent,
    IllegalTransition,
    SemanticCapability,
    authorize_and_consume,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(minutes=90)


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


def _revision(
    hash_input: ApprovalHashInput,
    *,
    revision_no: int = 1,
    state: ActionProposalState = ActionProposalState.APPROVED,
) -> ActionProposalRevision:
    return ActionProposalRevision.create(
        id=uuid4(),
        proposal_id=uuid4(),
        revision_no=revision_no,
        state=state,
        hash_input=hash_input,
        created_at=NOW,
        expires_at=NOW + TTL,
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


def test_a_superseded_revision_is_no_longer_executable() -> None:
    """Revision 1 with ITS OWN, CORRECT approval must stop being executable once revision 2 exists."""
    first = _revision(_hash_input())
    approval = _approval(first)

    # Before the edit the correct path really does pass — otherwise the assertion below would
    # be proving nothing but a typo in the fixture.
    proof = authorize_and_consume(revision=first, approval=approval, intent=_intent(first), now=NOW)
    assert proof.revision_id == first.id

    superseded, second = first.supersede(
        new_id=uuid4(),
        new_hash_input=_hash_input(text_and_media="Zredagowana treść, inna niż zatwierdzona."),
        now=NOW + timedelta(minutes=5),
    )

    assert superseded.id == first.id
    assert superseded.state is ActionProposalState.SUPERSEDED
    assert second.revision_no == first.revision_no + 1
    assert second.payload_hash != first.payload_hash
    assert second.state is ActionProposalState.DRAFT

    # `match` on purpose: a bare `pytest.raises(PermissionError)` would also be satisfied by a
    # typo in the fixture producing a mismatched approval, and the test would claim to prove the
    # supersede rule while proving nothing. The refusal has to be ABOUT the state.
    with pytest.raises(PermissionError, match="SUPERSEDED"):
        authorize_and_consume(
            revision=superseded,
            approval=approval,
            intent=_intent(first),
            now=NOW + timedelta(minutes=5),
        )


def test_an_edit_does_not_extend_the_original_ttl() -> None:
    """L5539: an edit does not extend the original TTL. The window belongs to the thread, not
    to the number of edits — otherwise an operator could keep a draft alive forever by editing."""
    first = _revision(_hash_input())
    _, second = first.supersede(
        new_id=uuid4(),
        new_hash_input=_hash_input(text_and_media="Poprawka."),
        now=NOW + timedelta(minutes=60),
    )

    assert second.expires_at <= first.expires_at
    assert second.expires_at == first.expires_at
    assert second.created_at == NOW + timedelta(minutes=60)


def test_an_edit_that_changes_nothing_is_not_a_new_revision() -> None:
    """A revision with the same hash is not an edit; letting it through would invalidate a
    consent for nothing and leave two revisions the approval cannot tell apart."""
    first = _revision(_hash_input())
    with pytest.raises(ValueError, match="hash"):
        first.supersede(
            new_id=uuid4(), new_hash_input=first.hash_input, now=NOW + timedelta(minutes=1)
        )


def test_an_expired_revision_cannot_be_superseded() -> None:
    """L5540: after the TTL the draft goes to EXPIRED and is not regenerated without a decision
    of the operator. Editing must not be a back door around that."""
    first = _revision(_hash_input())
    with pytest.raises(ValueError, match="wygas"):
        first.supersede(
            new_id=uuid4(),
            new_hash_input=_hash_input(text_and_media="Za późno."),
            now=first.expires_at,
        )


def test_supersede_obeys_the_action_proposal_transition_table() -> None:
    """L2775 names exactly one legal source of `SUPERSEDED`: `APPROVED`. The table is the law,
    and `supersede` asks it rather than carrying its own copy of the rule."""
    draft = _revision(_hash_input(), state=ActionProposalState.DRAFT)
    with pytest.raises(IllegalTransition):
        draft.supersede(
            new_id=uuid4(),
            new_hash_input=_hash_input(text_and_media="Edycja draftu."),
            now=NOW + timedelta(minutes=1),
        )
