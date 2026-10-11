"""Execution of a single case. One entry point for the stub and for Codex.

The runner does not know which runtime it was given — thanks to that `pytest evals -m "not
live"` and `pytest evals -m live` check EXACTLY the same invariants. The only difference is
who computed the result, and that is recorded in `EvalRecord.runtime`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..scoring.checks import (
    check_discovery_result,
    check_draft,
    check_quality_assessment,
    check_signal_pair,
)
from . import dataset
from .cases import (
    BRIEFS,
    DiscoveryCase,
    DraftCase,
    QualityCase,
    SignalPairCase,
)
from .policy import HEURISTIC_POLICY_VERSION
from .prompts import PromptVersion, get
from .runtime import ModelRuntime
from .types import EvalFamily, EvalRecord, ThreadObservation

PROMPT_FOR_FAMILY: dict[EvalFamily, str] = {
    EvalFamily.QUALITY: "thread_quality",
    EvalFamily.DISCOVERY: "discovery",
    EvalFamily.DRAFT: "draft_reply",
}


def prompt_for(family: EvalFamily) -> PromptVersion:
    return get(PROMPT_FOR_FAMILY[family])


def _record(case_id, family, archetype, prompt, runtime, checks, refs, metrics) -> EvalRecord:
    return EvalRecord(
        case_id=case_id,
        family=family,
        archetype=archetype,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        runtime=runtime.identity,
        heuristic_policy_version=HEURISTIC_POLICY_VERSION,
        checks=tuple(checks),
        observed_refs=tuple(refs),
        metrics=metrics,
    )


# ---------------------------------------------------------------------------


def run_quality_case(case: QualityCase, runtime: ModelRuntime) -> EvalRecord:
    prompt = prompt_for(case.family)
    obs = dataset.observation(case.spec_key)
    assessment = runtime.assess_thread(prompt, obs)

    # Counterfactual twin: the same thread without the fake engagement flags. If the tier
    # differs, the flag entered the ranking — and in E1 the detection is log-only (L4492,
    # L5551).
    without_flags = (
        runtime.assess_thread(prompt, obs.without_fake_flags())
        if obs.fake_engagement_flags
        else None
    )

    checks = check_quality_assessment(
        obs,
        assessment,
        expected_tier=case.expected_tier,
        expected_unknown_fields=case.expected_unknown,
        assessment_without_flags=without_flags,
        heuristic_policy_version=HEURISTIC_POLICY_VERSION,
    )
    return _record(
        case.case_id,
        case.family,
        case.archetype,
        prompt,
        runtime,
        checks,
        (obs.thread_ref,),
        {
            "spec_key": case.spec_key,
            "priority_tier": assessment.priority_tier.value,
            "window_state": assessment.window_state.value,
            "data_quality": assessment.data_quality,
            "confidence": assessment.confidence,
            "negative_signals": len(assessment.negative_signals),
            "fake_engagement_flags": len(assessment.fake_engagement_flags),
            "illustrates": ",".join(obs.illustrates),
        },
    )


# ---------------------------------------------------------------------------


def _candidates(case: DiscoveryCase) -> tuple[ThreadObservation, ...]:
    if case.spec_keys:
        return tuple(dataset.observation(k) for k in case.spec_keys)
    return dataset.for_archetype(case.archetype)


def _refs(keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(dataset.observation(k).thread_ref for k in keys)


def run_discovery_case(case: DiscoveryCase, runtime: ModelRuntime) -> EvalRecord:
    prompt = prompt_for(case.family)
    candidates = _candidates(case)
    result = runtime.rank_candidates(prompt, candidates)

    # Reproducibility of the order: the same threads in reversed input order (L5553).
    permutation_result = runtime.rank_candidates(prompt, tuple(reversed(candidates)))

    # Counterfactual set without the fake engagement flags — where there are flags at all.
    has_flags = any(o.fake_engagement_flags for o in candidates)
    fakefree_result = (
        runtime.rank_candidates(
            prompt,
            tuple(o.without_fake_flags() if o.fake_engagement_flags else o for o in candidates),
        )
        if has_flags
        else None
    )

    checks = check_discovery_result(
        result,
        candidates,
        must_not_shortlist=_refs(case.must_not_shortlist),
        must_be_last=_refs(case.must_be_last),
        must_precede=tuple((_refs((a,))[0], _refs((b,))[0]) for a, b in case.must_precede),
        permutation_result=permutation_result,
        fakefree_result=fakefree_result,
        heuristic_policy_version=HEURISTIC_POLICY_VERSION,
    )
    return _record(
        case.case_id,
        case.family,
        case.archetype,
        prompt,
        runtime,
        checks,
        result.order,
        {
            "kandydatow": len(candidates),
            "krotka_lista": len(result.shortlist),
            "z_negatywem": sum(1 for o in candidates if o.negative_signal_classes),
            "z_flagami_fake": sum(1 for o in candidates if o.fake_engagement_flags),
        },
    )


def run_signal_pair_case(case: SignalPairCase, runtime: ModelRuntime) -> EvalRecord:
    prompt = prompt_for(case.family)
    baza = dataset.observation(case.base_spec_key)
    stronger_obs = baza.replace_signal_class(case.stronger)
    weaker_obs = baza.replace_signal_class(case.weaker)
    result = runtime.rank_candidates(prompt, (weaker_obs, stronger_obs))

    checks = check_signal_pair(
        result,
        stronger_obs.thread_ref,
        weaker_obs.thread_ref,
        case.stronger,
        case.weaker,
        tie=case.tie,
    )
    return _record(
        case.case_id,
        case.family,
        case.archetype,
        prompt,
        runtime,
        checks,
        result.order,
        {
            "base_spec_key": case.base_spec_key,
            "stronger": case.stronger.value,
            "weaker": case.weaker.value,
            "tie": case.tie,
        },
    )


# ---------------------------------------------------------------------------


def run_draft_case(case: DraftCase, runtime: ModelRuntime) -> EvalRecord:
    prompt = prompt_for(case.family)
    obs = dataset.observation(case.spec_key)
    brief = BRIEFS[case.brief_id]
    draft = runtime.draft_reply(prompt, obs, brief)

    checks = check_draft(
        obs,
        brief,
        draft,
        expect_declined=case.expect_declined,
        expected_decline_reason=case.expected_decline_reason,
    )
    return _record(
        case.case_id,
        case.family,
        case.archetype,
        prompt,
        runtime,
        checks,
        (obs.thread_ref,),
        {
            "spec_key": case.spec_key,
            "brief_id": case.brief_id,
            "declined": draft.declined,
            "decline_reason": draft.decline_reason.value if draft.decline_reason else None,
            "dlugosc_draftu": len(draft.text),
            "used_claims": ",".join(draft.used_claims),
        },
    )


def run_case(case, runtime: ModelRuntime) -> EvalRecord:
    """Dispatcher by case type."""
    if isinstance(case, QualityCase):
        return run_quality_case(case, runtime)
    if isinstance(case, DiscoveryCase):
        return run_discovery_case(case, runtime)
    if isinstance(case, SignalPairCase):
        return run_signal_pair_case(case, runtime)
    if isinstance(case, DraftCase):
        return run_draft_case(case, runtime)
    raise TypeError(f"Nieznany typ przypadku: {type(case).__name__}")


__all__ = [
    "PROMPT_FOR_FAMILY",
    "prompt_for",
    "run_case",
    "run_discovery_case",
    "run_draft_case",
    "run_quality_case",
    "run_signal_pair_case",
]
