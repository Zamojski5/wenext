"""Negative control: evidence that the evals are NOT empty.

A suite in which everything passes says nothing until it is known that it can
fail. Every test below substitutes a runtime that deliberately breaks one rule from 22.7
and checks that exactly that check lights up red.

This is the equivalent of the negative test from DoD D19 ("no result yields NO_DATA, never
a default GO") applied to the eval harness.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from community_os_contracts import Observed, PriorityTier, ThreadQualityAssessment

from evals.harness import dataset
from evals.harness.cases import BRIEFS, DISCOVERY_CASES, DRAFT_CASES, QUALITY_CASES
from evals.harness.runner import run_case
from evals.harness.runtime import StubRuntime
from evals.harness.types import DiscoveryResult, DraftProposal


def _case(identyfikator: str, directory):
    return next(c for c in directory if c.case_id == identyfikator)


def _failed_check_names(record) -> set[str]:
    return {c.name for c in record.checks if not c.passed}


# ---------------------------------------------------------------------------


class ZeroInsteadOfUnknown(StubRuntime):
    """Records 0 where the platform exposed nothing — exactly what L4299 forbids."""

    def assess_thread(self, prompt, obs) -> ThreadQualityAssessment:
        assessment = super().assess_thread(prompt, obs)
        return assessment.model_copy(update={"icp_share": Observed[float].known(0.0)})


def test_zero_instead_of_unknown_is_detected() -> None:
    record = run_case(_case("q-f-icp-hidden", QUALITY_CASES), ZeroInsteadOfUnknown())
    assert "icp_share = UNKNOWN, nie zero" in _failed_check_names(record)


# ---------------------------------------------------------------------------


class FlagLowersTier(StubRuntime):
    """Punishes a thread for a fake engagement flag — in E1 the detection is log-only
    (L4492, L5551).

    The punishment is expressed as a bonus for the thread WITHOUT the flag, because the
    lowest tier that is not a rejection is `C` and there is no way to lower it. The violated
    invariant is the same: the presence of the flag changes `priority_tier`.
    """

    def assess_thread(self, prompt, obs) -> ThreadQualityAssessment:
        assessment = super().assess_thread(prompt, obs)
        if obs.fake_engagement_flags or assessment.priority_tier is PriorityTier.REJECT:
            return assessment
        better_tier = {PriorityTier.C: PriorityTier.B, PriorityTier.B: PriorityTier.A}
        return assessment.model_copy(
            update={
                "priority_tier": better_tier.get(assessment.priority_tier, assessment.priority_tier)
            }
        )


@pytest.mark.parametrize("case_id", ["q-f-window-fake-burst", "q-g-window-fake-burst"])
def test_flag_changing_the_tier_is_detected(case_id: str) -> None:
    """The counterfactual twin must catch the difference in tier."""
    from evals.harness.runner import prompt_for

    case = _case(case_id, QUALITY_CASES)
    obs = dataset.observation(case.spec_key)
    runtime = FlagLowersTier()
    prompt = prompt_for(case.family)
    with_flags = runtime.assess_thread(prompt, obs)
    without_flags = runtime.assess_thread(prompt, obs.without_fake_flags())
    assert with_flags.priority_tier is not without_flags.priority_tier, (
        "sabotaż nie zadziałał — test kontrolny nie miałby czego wykryć"
    )

    record = run_case(case, runtime)
    assert "identyczny tier jak bez flagi (kontrfaktyczny bliźniak)" in _failed_check_names(record)


# ---------------------------------------------------------------------------


class NegativeAsWeight(StubRuntime):
    """Treats a negative signal like a negative weight: high engagement overrides it."""

    def rank_candidates(self, prompt, candidates) -> DiscoveryResult:
        result = super().rank_candidates(prompt, candidates)
        by_engagement = {o.thread_ref: (o.votes + o.reaction_total) for o in candidates}
        reordered = tuple(
            replace(c, position=i)
            for i, c in enumerate(
                sorted(result.ordered, key=lambda c: -by_engagement[c.thread_ref])
            )
        )
        return DiscoveryResult(
            ordered=reordered, heuristic_policy_version=result.heuristic_policy_version
        )


def test_negative_signal_outvoted_by_engagement_is_detected() -> None:
    record = run_case(_case("d-f-negatyw-vs-engagement", DISCOVERY_CASES), NegativeAsWeight())
    failed = _failed_check_names(record)
    assert "wątki z negatywem zajmują koniec porządku" in failed


# ---------------------------------------------------------------------------


class UnstableOrder(StubRuntime):
    """Returns an order dependent on the input order — breaks the reproducibility of L5553."""

    def rank_candidates(self, prompt, candidates) -> DiscoveryResult:
        result = super().rank_candidates(prompt, candidates)
        input_order = {o.thread_ref: i for i, o in enumerate(candidates)}
        reordered = tuple(
            replace(c, position=i)
            for i, c in enumerate(sorted(result.ordered, key=lambda c: input_order[c.thread_ref]))
        )
        return DiscoveryResult(
            ordered=reordered, heuristic_policy_version=result.heuristic_policy_version
        )


def test_order_depending_on_input_order_is_detected() -> None:
    record = run_case(_case("d-g-unknown-nie-jest-zerem", DISCOVERY_CASES), UnstableOrder())
    assert "porządek niezależny od kolejności wejścia" in _failed_check_names(record)


# ---------------------------------------------------------------------------


class UnverifiedAsFact(StubRuntime):
    """States an unverified claim as a fact — L751 and DoD F4 (L5307)."""

    def draft_reply(self, prompt, obs, brief) -> DraftProposal:
        draft = super().draft_reply(prompt, obs, brief)
        if draft.declined or not brief.unusable_claims:
            return draft
        claim = brief.unusable_claims[0]
        return replace(
            draft,
            text=f"{draft.text} {claim.text}",
            used_claims=(*draft.used_claims, claim.id),
            referenced_claims=(),
        )


def test_using_an_unverified_claim_as_fact_is_detected() -> None:
    record = run_case(_case("r-f-mixed-depth-deep", DRAFT_CASES), UnverifiedAsFact())
    failed = _failed_check_names(record)
    assert "draft nie stwierdza claimu UNVERIFIED/EXPIRED/RETRACTED jako faktu" in failed
    assert "marker claimu niezweryfikowanego nie występuje w treści" in failed


# ---------------------------------------------------------------------------


class IgnoresProhibitedTopics(StubRuntime):
    """Quotes the thread title together with the prohibited topic."""

    def draft_reply(self, prompt, obs, brief) -> DraftProposal:
        draft = super().draft_reply(prompt, obs, brief)
        if draft.declined:
            return draft
        return replace(draft, text=f"{obs.title} {draft.text}")


def test_breaking_prohibited_topics_is_detected() -> None:
    record = run_case(_case("r-f-prohibited-pricing", DRAFT_CASES), IgnoresProhibitedTopics())
    assert "draft respektuje prohibited_topics" in _failed_check_names(record)


# ---------------------------------------------------------------------------


class WritesToClosedWindow(StubRuntime):
    """Creates a draft for a thread with `window_state = CLOSED` (forbidden by L4590, L5541)."""

    def draft_reply(self, prompt, obs, brief) -> DraftProposal:
        draft = super().draft_reply(prompt, obs, brief)
        if not draft.declined:
            return draft
        return DraftProposal(
            thread_ref=obs.thread_ref,
            text="Świetny wątek, dorzucam swoje trzy grosze o czymś zupełnie innym.",
            used_claims=(),
            referenced_claims=(),
            evidence_refs=(),
        )


def test_draft_into_a_closed_window_is_detected() -> None:
    record = run_case(_case("r-g-window-closed-brak-draftu", DRAFT_CASES), WritesToClosedWindow())
    assert "odmowa albo draft — zgodnie z oczekiwaniem" in _failed_check_names(record)


# ---------------------------------------------------------------------------


class RewritesThread(StubRuntime):
    """Returns the thread body as its own draft."""

    def draft_reply(self, prompt, obs, brief) -> DraftProposal:
        draft = super().draft_reply(prompt, obs, brief)
        if draft.declined:
            return draft
        return replace(draft, text=obs.body)


def test_rewriting_the_thread_as_a_draft_is_detected() -> None:
    record = run_case(_case("r-g-mixed-depth-thread", DRAFT_CASES), RewritesThread())
    assert "draft nie jest kopią treści wątku" in _failed_check_names(record)


# ---------------------------------------------------------------------------


def test_contract_forbids_a_negative_signal_without_REJECT() -> None:
    """A sabotage impossible to carry out: the D21 type blocks it earlier than the eval does.

    This is the right place for this rule — better that it cannot be broken at all
    than that it can be and the eval catches it.
    """
    runtime = StubRuntime()
    from evals.harness.runner import prompt_for
    from evals.harness.types import EvalFamily

    assessment = runtime.assess_thread(
        prompt_for(EvalFamily.QUALITY), dataset.observation("f-neg-locked-high-engagement")
    )
    assert assessment.priority_tier is PriorityTier.REJECT
    with pytest.raises(ValueError, match="REJECT"):
        assessment.model_copy(update={"priority_tier": PriorityTier.A}).model_validate(
            assessment.model_dump() | {"priority_tier": "A"}
        )


def test_draft_without_a_usable_claim_really_declines() -> None:
    """The reverse control: a brief with no claim usable as a fact MUST yield a decline."""
    runtime = StubRuntime()
    from evals.harness.runner import prompt_for
    from evals.harness.types import DeclineReason, EvalFamily

    draft = runtime.draft_reply(
        prompt_for(EvalFamily.DRAFT),
        dataset.observation("f-window-rising"),
        BRIEFS["brief-only-unverified"],
    )
    assert draft.declined
    assert draft.decline_reason is DeclineReason.NO_USABLE_CLAIM
    assert draft.text == ""
