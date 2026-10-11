"""Tests of the invariants the document names explicitly.

Every test cites, in its name or in its assertion, the place in the roadmap that it defends.
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
    BrowserSession,
    BrowserSessionState,
    CapabilitySnapshot,
    CapabilityState,
    CapsuleMode,
    ExecutionAttemptState,
    ExecutionIntent,
    ExecutionMode,
    FencingToken,
    InteractiveReason,
    LeaseState,
    NegativeSignal,
    Observed,
    PriorityTier,
    ProfileLease,
    ResultEvidenceKind,
    RetryClass,
    SemanticCapability,
    SideEffect,
    SignalClass,
    StaleFencingToken,
    StreamSessionRole,
    StreamTicket,
    ThreadQualityAssessment,
    ThreadRole,
    ThreadSecurityBinding,
    UnknownReason,
    WindowState,
    authorize_resume,
    authorize_side_effect,
    may_retry_automatically,
    ordering_key,
)
from community_os_contracts.codex import ResumeRefused

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Invariant 7 (L4299): UNKNOWN is not zero
# ---------------------------------------------------------------------------


def test_zero_and_unknown_are_distinguishable() -> None:
    zero = Observed[int].known(0)
    unknown = Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)

    assert zero.is_known is True
    assert zero.require() == 0
    assert unknown.is_known is False
    assert zero != unknown


def test_unknown_without_a_reason_is_impossible() -> None:
    with pytest.raises(ValidationError, match="albo wartość, albo powód"):
        Observed[int]()


def test_value_cannot_be_known_and_unknown_at_once() -> None:
    with pytest.raises(ValidationError, match="jednocześnie znane i nieznane"):
        Observed[int](value=5, unknown_reason=UnknownReason.SESSION_LOST)


def test_require_on_unknown_raises_instead_of_returning_zero() -> None:
    unknown = Observed[int].unknown(UnknownReason.READ_BUDGET_EXHAUSTED)
    with pytest.raises(ValueError, match="nie wolno jej zamienić na liczbę"):
        unknown.require()


# ---------------------------------------------------------------------------
# Section 16.1 (L2778-2795): payload hash and the invalidation of consent
# ---------------------------------------------------------------------------


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


def _revision(wejscie: ApprovalHashInput, *, revision_no: int = 1) -> ActionProposalRevision:
    return ActionProposalRevision.create(
        id=uuid4(),
        proposal_id=uuid4(),
        revision_no=revision_no,
        state=ActionProposalState.APPROVED,
        hash_input=wejscie,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=90),
        source_observed_at=NOW,
    )


def test_payload_hash_is_deterministic() -> None:
    wejscie = _hash_input()
    assert wejscie.payload_hash() == wejscie.payload_hash()
    # The same content built again gives the same hash.
    rebuilt = ApprovalHashInput(**wejscie.model_dump())
    assert rebuilt.payload_hash() == wejscie.payload_hash()


def test_changing_content_changes_the_hash() -> None:
    a = _hash_input()
    b = _hash_input(text_and_media="Inna treść.")
    assert a.payload_hash() != b.payload_hash()


def test_changing_adapter_version_changes_the_hash() -> None:
    """L2792: adapter version is an input of the hash, so a deploy invalidates pending consents."""
    a = _hash_input(adapter_version="reddit-1.0.0")
    b = _hash_input(adapter_version="reddit-1.0.1")
    assert a.payload_hash() != b.payload_hash()


def test_hash_input_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ApprovalHashInput(**_hash_input().model_dump(), extra="cokolwiek")


def test_revision_with_inconsistent_hash_is_rejected() -> None:
    wejscie = _hash_input()
    with pytest.raises(ValidationError, match="nie odpowiada hash_input"):
        ActionProposalRevision(
            id=uuid4(),
            proposal_id=uuid4(),
            revision_no=1,
            state=ActionProposalState.APPROVED,
            hash_input=wejscie,
            payload_hash="0" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=90),
            source_observed_at=NOW,
        )


def test_editing_content_invalidates_the_approval() -> None:
    """L2795, L5218: every edit creates a new revision and invalidates the previous approval."""
    original = _revision(_hash_input())
    approval = ApprovalDecision(
        id=uuid4(),
        revision_id=original.id,
        approved_payload_hash=original.payload_hash,
        approved_by=uuid4(),
        approved_at=NOW,
    )
    assert approval.matches(original)

    after_edit = _revision(_hash_input(text_and_media="Zredagowane."), revision_no=2)
    assert not approval.matches(after_edit)


# ---------------------------------------------------------------------------
# L5217, L5334: nothing bypasses ExecutionIntent
# ---------------------------------------------------------------------------


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


def _approval(revision: ActionProposalRevision) -> ApprovalDecision:
    return ApprovalDecision(
        id=uuid4(),
        revision_id=revision.id,
        approved_payload_hash=revision.payload_hash,
        approved_by=uuid4(),
        approved_at=NOW,
    )


def test_action_without_approval_cannot_run() -> None:
    revision = _revision(_hash_input())
    with pytest.raises(PermissionError, match="Brak ApprovalDecision"):
        authorize_side_effect(revision=revision, approval=None, intent=_intent(revision), now=NOW)


def test_action_without_intent_cannot_run() -> None:
    revision = _revision(_hash_input())
    with pytest.raises(PermissionError, match="omija ExecutionIntent"):
        authorize_side_effect(revision=revision, approval=_approval(revision), intent=None, now=NOW)


def test_the_correct_path_passes() -> None:
    revision = _revision(_hash_input())
    authorize_side_effect(
        revision=revision, approval=_approval(revision), intent=_intent(revision), now=NOW
    )


def test_intent_is_single_use() -> None:
    """L2821: at most one automatic submit attempt per ExecutionIntent."""
    revision = _revision(_hash_input())
    intent = _intent(revision)
    consumed = intent.consume(NOW)

    assert consumed.consumed_at == NOW
    with pytest.raises(ValueError, match="został już zużyty"):
        consumed.consume(NOW + timedelta(seconds=1))

    with pytest.raises(PermissionError, match="już zużyty"):
        authorize_side_effect(
            revision=revision, approval=_approval(revision), intent=consumed, now=NOW
        )


def test_expired_draft_cannot_be_executed() -> None:
    """L5540: a draft after `draft_ttl` goes to EXPIRED."""
    revision = _revision(_hash_input())
    late = revision.expires_at + timedelta(minutes=1)
    with pytest.raises(PermissionError, match="Rewizja wygasła"):
        authorize_side_effect(
            revision=revision, approval=_approval(revision), intent=_intent(revision), now=late
        )


def test_approval_from_another_account_does_not_match_the_intent() -> None:
    """L5689: an approval of another account does not match the ExecutionIntent."""
    revision_a = _revision(_hash_input())
    revision_b = _revision(_hash_input(text_and_media="Inna."))
    with pytest.raises(PermissionError, match="nie jest związany z tą dokładną rewizją"):
        authorize_side_effect(
            revision=revision_a, approval=_approval(revision_a), intent=_intent(revision_b), now=NOW
        )


@pytest.mark.parametrize(
    "state, may_retry",
    [
        (ExecutionAttemptState.PREPARED, True),
        (ExecutionAttemptState.DISPATCHED, True),
        (ExecutionAttemptState.SUBMIT_STARTED, False),
        (ExecutionAttemptState.UNKNOWN, False),
        (ExecutionAttemptState.CONFIRMED, False),
    ],
)
def test_no_automatic_retry_after_submit_started(state, may_retry) -> None:
    """L2813-2814, L5335."""
    assert may_retry_automatically(state) is may_retry


# ---------------------------------------------------------------------------
# Section 14.4 (L2377, L5178): a stale fencing token is rejected
# ---------------------------------------------------------------------------


def _lease(generation: int = 2) -> ProfileLease:
    return ProfileLease(
        id=uuid4(),
        organization_id=uuid4(),
        profile_id=uuid4(),
        generation=generation,
        owner_runner_id=uuid4(),
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        state=LeaseState.ACTIVE,
    )


def test_current_token_passes() -> None:
    lease = _lease()
    lease.verify(lease.token())


def test_stale_token_is_rejected() -> None:
    lease = _lease(generation=2)
    stale_token = FencingToken(profile_id=lease.profile_id, generation=1, lease_id=lease.id)
    with pytest.raises(StaleFencingToken) as exc:
        lease.verify(stale_token)
    assert exc.value.expected == 2
    assert exc.value.presented == 1


def test_token_from_a_foreign_profile_is_rejected() -> None:
    lease = _lease()
    foreign_token = FencingToken(profile_id=uuid4(), generation=lease.generation, lease_id=lease.id)
    with pytest.raises(StaleFencingToken):
        lease.verify(foreign_token)


# ---------------------------------------------------------------------------
# L5532: an E1 run in HEADLESS mode starts neither a display nor a stream gateway
# ---------------------------------------------------------------------------


def _session(**overrides) -> BrowserSession:
    base = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "capsule_instance_id": uuid4(),
        "os_pid": 1234,
        "process_started_at": NOW,
        "process_instance_id": "boot-id:1234:9999",
        "capsule_supervisor_identity": "capsule-supervisor-1",
        "browser_build_digest": "sha256:cft-153.0.8010.12-linux-arm64",
        "browser_mode": CapsuleMode.HEADLESS,
        "cdp_endpoint_ref": "internal://capsule/1/cdp",
        "state": BrowserSessionState.ACTIVE,
        "last_seen_at": NOW,
    }
    base.update(overrides)
    return BrowserSession(**base)


def test_headless_session_without_display_is_valid() -> None:
    _session()


def test_headless_session_with_display_is_rejected() -> None:
    with pytest.raises(ValidationError, match="HEADLESS nie może mieć displayu"):
        _session(display_id=":99", display_server_pid=200)


def test_headless_session_with_stream_gateway_is_rejected() -> None:
    with pytest.raises(ValidationError, match="stream gatewaya"):
        _session(stream_gateway_instance_id=uuid4())


def test_control_ticket_requires_human_interaction_lease() -> None:
    """L5590: a CONTROL ticket cannot be issued without an atomic HUMAN InteractionLease."""
    with pytest.raises(ValidationError, match="musi być związany z InteractionLease"):
        StreamTicket(
            id=uuid4(),
            browser_session_id=uuid4(),
            user_id=uuid4(),
            role=StreamSessionRole.CONTROL,
            ticket_hash="sha256:x",
            profile_lease_generation=1,
            issued_at=NOW,
            ticket_expires_at=NOW + timedelta(seconds=45),
        )


def test_view_only_ticket_does_not_require_interaction_lease() -> None:
    StreamTicket(
        id=uuid4(),
        browser_session_id=uuid4(),
        user_id=uuid4(),
        role=StreamSessionRole.VIEW_ONLY,
        ticket_hash="sha256:x",
        profile_lease_generation=1,
        issued_at=NOW,
        ticket_expires_at=NOW + timedelta(seconds=45),
    )


# ---------------------------------------------------------------------------
# Section 14.7 (L2580, L5537): capability
# ---------------------------------------------------------------------------


def _capability(**overrides) -> CapabilitySnapshot:
    base = {
        "capability": SemanticCapability.READ_THREAD,
        "state": CapabilityState.SUPPORTED,
        "execution_mode": ExecutionMode.AUTOMATED,
        "side_effect": SideEffect.NONE,
        "retry_class": RetryClass.SAFE_RETRY,
        "result_evidence": (ResultEvidenceKind.URL,),
        "required_browser_mode": CapsuleMode.HEADLESS,
        "browser_build_digest": "sha256:cft-153.0.8010.12-linux-arm64",
        "reason_code": "PROBE_OK",
        "confidence": 0.9,
        "verified_at": NOW,
        "expires_at": NOW + timedelta(hours=24),
        "adapter_version": "reddit-1.0.0",
    }
    base.update(overrides)
    return CapabilitySnapshot(**base)


def test_expired_capability_becomes_unknown() -> None:
    """L2580, L5603: after expiry the state becomes UNKNOWN until a re-probe."""
    snapshot = _capability()
    assert snapshot.effective_state(NOW) is CapabilityState.SUPPORTED
    assert snapshot.effective_state(NOW + timedelta(hours=25)) is CapabilityState.UNKNOWN


def test_manual_only_creates_a_human_task() -> None:
    """L2580: MANUAL_ONLY creates a human task, and not an ordinary capability error."""
    snapshot = _capability(
        state=CapabilityState.MANUAL_ONLY,
        execution_mode=ExecutionMode.MANUAL_ONLY,
        required_browser_mode=CapsuleMode.INTERACTIVE,
    )
    assert snapshot.requires_human_task(NOW) is True


def test_manual_only_cannot_be_headless() -> None:
    """L5537: a capability that does not work headless has no silent fallback."""
    with pytest.raises(ValidationError, match="nie działa bez ekranu"):
        _capability(
            execution_mode=ExecutionMode.MANUAL_ONLY, required_browser_mode=CapsuleMode.HEADLESS
        )


def test_irreversible_action_cannot_be_automated() -> None:
    """L4294: in E1 auto-publish does not exist."""
    with pytest.raises(ValidationError, match="nie może mieć execution_mode=AUTOMATED"):
        _capability(
            capability=SemanticCapability.PUBLISH_REPLY,
            side_effect=SideEffect.IRREVERSIBLE,
            execution_mode=ExecutionMode.AUTOMATED,
            retry_class=RetryClass.RECONCILE_ONLY,
        )


def test_irreversible_action_cannot_have_safe_retry() -> None:
    with pytest.raises(ValidationError, match="SAFE_RETRY"):
        _capability(
            capability=SemanticCapability.PUBLISH_REPLY,
            side_effect=SideEffect.IRREVERSIBLE,
            execution_mode=ExecutionMode.AUTOMATED_AFTER_APPROVAL,
            retry_class=RetryClass.SAFE_RETRY,
        )


# ---------------------------------------------------------------------------
# Section 22.7 (L4472, L5550): a negative signal is a stop
# ---------------------------------------------------------------------------


def _assessment(**overrides) -> ThreadQualityAssessment:
    base = {
        "thread_ref": "t3_abc123",
        "surface_id": uuid4(),
        "community_id": uuid4(),
        "observed_at": NOW,
        "collected_at": NOW,
        "adapter_version": "reddit-1.0.0",
        "depth_profile": Observed[int].known(4),
        "unique_participants": Observed[int].known(11),
        "substantive_reply_ratio": Observed[float].known(0.45),
        "author_participation": Observed[float].known(0.6),
        "icp_share": Observed[float].known(0.3),
        "intentional_amplification": Observed[int].known(2),
        "reach_calibration_basis": "mediana surface dla formatu i godziny",
        "window_state": WindowState.RISING,
        "entry_opportunity": "Możemy dodać konkretny benchmark z własnego wdrożenia.",
        "priority_tier": PriorityTier.A,
        "confidence": 0.7,
        "data_quality": "MEDIUM",
        "heuristic_policy_version": "hp-1",
    }
    base.update(overrides)
    return ThreadQualityAssessment(**base)


def test_negative_signal_forces_reject() -> None:
    negative_signal = NegativeSignal(
        signal_class=SignalClass.NEGATIVE_MODERATION, observed_at=NOW, evidence_ref="mod-log-1"
    )
    with pytest.raises(ValidationError, match="musi mieć priority_tier=REJECT"):
        _assessment(negative_signals=(negative_signal,), priority_tier=PriorityTier.A)


def test_negative_signal_is_not_offset_by_positives() -> None:
    """L5550: no number of positive signals offsets a negative one."""
    negative_signal = NegativeSignal(
        signal_class=SignalClass.LOCK, observed_at=NOW, evidence_ref="lock-1"
    )
    assessment = _assessment(
        negative_signals=(negative_signal,),
        priority_tier=PriorityTier.REJECT,
        substantive_reply_ratio=Observed[float].known(0.99),
        intentional_amplification=Observed[int].known(500),
    )
    assert assessment.is_enterable() is False
    assert ordering_key(assessment) == (9, 9, 0.0)


def test_negative_class_must_really_be_negative() -> None:
    with pytest.raises(ValidationError, match="nie jest klasą negatywną"):
        NegativeSignal(signal_class=SignalClass.LIKE, observed_at=NOW, evidence_ref="x")


def test_fake_engagement_flag_does_not_reject_the_thread() -> None:
    """L4300, L5551: fake engagement detection is log-only in E1."""
    assessment = _assessment(
        fake_engagement_flags=("burst_bez_odpowiedzi",), priority_tier=PriorityTier.A
    )
    assert assessment.priority_tier is PriorityTier.A
    assert assessment.is_enterable() is True

    with pytest.raises(ValidationError, match="log-only"):
        _assessment(
            fake_engagement_flags=("burst_bez_odpowiedzi",), priority_tier=PriorityTier.REJECT
        )


def test_thread_with_a_closed_window_is_not_enterable() -> None:
    """L4590, L5541: a thread with window_state=CLOSED creates no new draft."""
    assert _assessment(window_state=WindowState.CLOSED).is_enterable() is False


def test_unknown_reply_ratio_is_not_counted_as_zero() -> None:
    known_zero = _assessment(substantive_reply_ratio=Observed[float].known(0.0))
    unknown = _assessment(
        substantive_reply_ratio=Observed[float].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)
    )
    # Both land with the same calibration 0.0, but they are different objects with a different
    # meaning — the test guards that an unknown value cannot accidentally be read as a number.
    assert known_zero.substantive_reply_ratio.require() == 0.0
    with pytest.raises(ValueError):
        unknown.substantive_reply_ratio.require()


# ---------------------------------------------------------------------------
# Section 13.4 (L2266, L5645): resume fail-closed
# ---------------------------------------------------------------------------


def _binding(**overrides) -> ThreadSecurityBinding:
    base = {
        "id": uuid4(),
        "codex_connection_id": uuid4(),
        "workspace_id": uuid4(),
        "social_account_id": uuid4(),
        "role": ThreadRole.WORKER,
        "principal_generation": 3,
        "tool_manifest_version": "tm-1",
        "security_policy_version": "sp-1",
    }
    base.update(overrides)
    return ThreadSecurityBinding(**base)


def test_resume_with_a_matching_binding_passes() -> None:
    stored = _binding()
    requested = stored.model_copy(update={"id": uuid4()})
    authorize_resume(stored, requested, active_principal_generation=3)


def test_different_role_ends_resume_fail_closed() -> None:
    stored = _binding(role=ThreadRole.WORKER)
    requested = stored.model_copy(update={"role": ThreadRole.VERIFIER})
    with pytest.raises(ResumeRefused, match="role"):
        authorize_resume(stored, requested, active_principal_generation=3)


def test_different_tool_manifest_ends_resume_fail_closed() -> None:
    stored = _binding()
    requested = stored.model_copy(update={"tool_manifest_version": "tm-2"})
    with pytest.raises(ResumeRefused, match="tool_manifest_version"):
        authorize_resume(stored, requested, active_principal_generation=3)


def test_inactive_principal_generation_ends_resume_fail_closed() -> None:
    stored = _binding(principal_generation=3)
    with pytest.raises(ResumeRefused, match="Principal generation nieaktywna"):
        authorize_resume(stored, stored, active_principal_generation=4)


# ---------------------------------------------------------------------------
# L5161 vs L4370: the closed list of headful reasons
# ---------------------------------------------------------------------------


def test_e1_allows_exactly_five_interactive_reasons() -> None:
    from community_os_contracts import E1_ALLOWED_INTERACTIVE_REASONS

    assert InteractiveReason.IDENTITY_VERIFICATION not in E1_ALLOWED_INTERACTIVE_REASONS
    assert InteractiveReason.FIRST_LOGIN in E1_ALLOWED_INTERACTIVE_REASONS
    assert InteractiveReason.HUMAN_TAKEOVER in E1_ALLOWED_INTERACTIVE_REASONS
    assert len(E1_ALLOWED_INTERACTIVE_REASONS) == 5


def test_negative_class_rank_is_not_an_entry_priority() -> None:
    """A regression test for a real trap found while building the evals (D17).

    `SIGNAL_CLASS_ORDER` describes the STRENGTH of a signal and faithfully renders the list
    from L4471-4479, where the negatives stand at the top. But used as a sorting key for
    candidates they would put blocked threads at the BEGINNING. `entry_priority_rank()` is to
    make that impossible.
    """
    from community_os_contracts.quality import NEGATIVE_STOP_CLASSES, entry_priority_rank

    for klasa in NEGATIVE_STOP_CLASSES:
        with pytest.raises(ValueError, match="sygnałem STOP, nie priorytetem"):
            entry_priority_rank(klasa)

    # For positive classes it still answers, and it preserves the order from the document.
    assert entry_priority_rank(SignalClass.SUBSTANTIVE_REPLY) < entry_priority_rank(
        SignalClass.LIKE
    )
    assert entry_priority_rank(SignalClass.INTENTIONAL_AMPLIFICATION) == entry_priority_rank(
        SignalClass.SUBSTANTIVE_REPLY
    )
