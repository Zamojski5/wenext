"""Harness input and output types. Everything frozen and comparable.

The rule that holds the whole harness together: **no runtime output is a quality scalar.**
Discovery returns an ORDERED LIST (L4468), classification returns
a `ThreadQualityAssessment` from the D21 contracts, the draft returns a structure with an
explicit list of used claims. Nowhere is there a `score: float` field — and none may be added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from community_os_contracts import (
    PriorityTier,
    SignalClass,
    ThreadQualityAssessment,
    WindowState,
)


class EvalFamily(StrEnum):
    """The three families from L5168. The names are keys in the `tools/gonogo` report."""

    DISCOVERY = "discovery"
    QUALITY = "quality"
    DRAFT = "draft"


@dataclass(frozen=True)
class ThreadObservation:
    """Observation of a single thread — exactly what the adapter sees from the outside.

    `None` in a metric field means: THE PLATFORM DOES NOT EXPOSE THIS. The metric name is
    then in `hidden_metrics`. The consumer must record `UNKNOWN`, never zero (L4299, L5552).

    `dominant_signal_class` is an observation, not a judgement: it says which signal class
    from L4471-4479 prevails in this thread. Ranking sorts by it first (L4468).

    `reach_calibration_basis` is an INPUT DATUM, not the result of a computation — in F0
    there is no surface baseline (register of the infeasible D04, item 4).
    """

    thread_ref: str
    archetype: str
    community: str
    title: str
    body: str
    author: str
    created_at: datetime
    observed_at: datetime

    depth_levels: int
    reply_count: int
    unique_participants: int
    median_reply_chars: int
    replies_first_hour: int
    substantive_reply_ratio: float
    author_reply_count: int
    author_first_reply_minutes: int | None

    icp_share: float | None
    reach_impressions: int | None
    reach_calibration_basis: str
    hidden_metrics: tuple[str, ...]

    votes: int
    reaction_total: int
    amplification_count: int
    amplification_kinds: tuple[str, ...]

    window_state: WindowState
    locked: bool
    removed_by_moderation: bool
    negative_signal_labels: tuple[str, ...]
    negative_signal_classes: tuple[SignalClass, ...]
    #: Log-only in E1 (L4492, L5551). It is NOT an input to the ranking function.
    fake_engagement_flags: tuple[str, ...]
    dominant_signal_class: SignalClass

    #: D16 seed metadata — what this thread is in the set for.
    spec_key: str
    illustrates: tuple[str, ...]
    seed_note: str

    def replace_signal_class(self, signal_class: SignalClass) -> ThreadObservation:
        """A copy differing ONLY in signal class — for the ordering pairs from L4474-4479."""
        from dataclasses import replace

        return replace(
            self,
            thread_ref=f"{self.thread_ref}#{signal_class.value}",
            dominant_signal_class=signal_class,
        )

    def without_fake_flags(self) -> ThreadObservation:
        """Counterfactual twin without the fake engagement flags (L4492, L5551)."""
        from dataclasses import replace

        return replace(self, thread_ref=f"{self.thread_ref}#noflags", fake_engagement_flags=())


@dataclass(frozen=True)
class RankedCandidate:
    """One ranking position. Without a `score` field — deliberately (L4468)."""

    thread_ref: str
    position: int
    priority_tier: PriorityTier
    shortlisted: bool
    reason: str


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of the `discovery` family: an ordered list, not a scalar value."""

    ordered: tuple[RankedCandidate, ...]
    heuristic_policy_version: str

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(c.thread_ref for c in self.ordered)

    @property
    def shortlist(self) -> tuple[str, ...]:
        return tuple(c.thread_ref for c in self.ordered if c.shortlisted)


class ClaimStatus(StrEnum):
    """L743. A draft must not use an `UNVERIFIED` claim as a fact (L751, L5307)."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    RETRACTED = "RETRACTED"


#: Statuses that must NOT be used as a fact in a draft. `EXPIRED` and `RETRACTED`
#: sit here together with `UNVERIFIED`, because an expired claim is not a verified fact either.
CLAIM_STATUSES_NOT_USABLE_AS_FACT: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.UNVERIFIED, ClaimStatus.EXPIRED, ClaimStatus.RETRACTED}
)


@dataclass(frozen=True)
class Claim:
    """Claim from L742-749. `marker` is a unique string by which the claim's use is visible."""

    id: str
    text: str
    status: ClaimStatus
    marker: str
    evidence_ref: str | None = None

    @property
    def usable_as_fact(self) -> bool:
        return self.status not in CLAIM_STATUSES_NOT_USABLE_AS_FACT


@dataclass(frozen=True)
class ProhibitedTopic:
    """An entry from `prohibited_topics` in `WorkspaceIntelligenceProfileVersion` (L724)."""

    id: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class DraftBrief:
    """Input to the draft: claims, prohibited topics, the entry thesis.

    In E1 there are no `CONVERSATION_FOLLOW_UP` cases here — the conversation follow-up is
    written by a human (L4569-4571), so the draft eval does not contain them.
    """

    brief_id: str
    entry_thesis: str
    claims: tuple[Claim, ...]
    prohibited_topics: tuple[ProhibitedTopic, ...]
    approved_ctas: tuple[str, ...] = ()

    @property
    def usable_claims(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.usable_as_fact)

    @property
    def unusable_claims(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if not c.usable_as_fact)


class DeclineReason(StrEnum):
    """Reasons why a draft is NOT produced. A decline is a result, not an error."""

    WINDOW_CLOSED = "WINDOW_CLOSED"
    NEGATIVE_SIGNAL = "NEGATIVE_SIGNAL"
    NO_USABLE_CLAIM = "NO_USABLE_CLAIM"
    PROHIBITED_TOPIC = "PROHIBITED_TOPIC"


@dataclass(frozen=True)
class DraftProposal:
    """Result of the `draft` family.

    `used_claims` are the claims that the draft ASSERTS AS FACT. Separating them from
    `referenced_claims` matters: asking about an unverified number is allowed,
    stating it as a fact is not (L751).
    """

    thread_ref: str
    text: str
    used_claims: tuple[str, ...]
    referenced_claims: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    declined: bool = False
    decline_reason: DeclineReason | None = None


@dataclass(frozen=True)
class RuntimeIdentity:
    """Who computed the result. Goes into every eval record (L5308)."""

    kind: str
    model: str
    effort: str
    adapter_version: str


@dataclass(frozen=True)
class CheckResult:
    """A single structural assertion. No LLM as a judge (requirement D)."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvalRecord:
    """Record of one case. This is the machine-readable output for `tools/gonogo`.

    L5308: "every draft names the exact knowledge snapshot, the prompt version, the model
    and the effort". Here the same applies to every eval, not only to the draft.
    """

    case_id: str
    family: EvalFamily
    archetype: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    runtime: RuntimeIdentity
    heuristic_policy_version: str
    checks: tuple[CheckResult, ...]
    observed_refs: tuple[str, ...] = ()
    metrics: dict[str, float | int | str | bool | None] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "family": self.family.value,
            "archetype": self.archetype,
            "prompt": {
                "name": self.prompt_name,
                "version": self.prompt_version,
                "sha256": self.prompt_sha256,
            },
            "runtime": {
                "kind": self.runtime.kind,
                "model": self.runtime.model,
                "effort": self.runtime.effort,
                "adapter_version": self.runtime.adapter_version,
            },
            "heuristic_policy_version": self.heuristic_policy_version,
            "observed_refs": list(self.observed_refs),
            "metrics": dict(sorted(self.metrics.items())),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "passed": self.passed,
        }


__all__ = [
    "CLAIM_STATUSES_NOT_USABLE_AS_FACT",
    "CheckResult",
    "Claim",
    "ClaimStatus",
    "DeclineReason",
    "DiscoveryResult",
    "DraftBrief",
    "DraftProposal",
    "EvalFamily",
    "EvalRecord",
    "ProhibitedTopic",
    "RankedCandidate",
    "RuntimeIdentity",
    "ThreadObservation",
    "ThreadQualityAssessment",
]
