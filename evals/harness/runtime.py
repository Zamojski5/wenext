"""The model runtime port — one interface, two implementations.

    StubRuntime   deterministic, no network, no ChatGPT account. The default.
    CodexRuntime  the real one, via D12 (`services/codex_runtime/`), only `-m live`.

The same set of cases runs against both. The stub proves that THE HARNESS WORKS
and that the quality definition from 22.7 is encoded in executable code; only a run
on Codex says anything about the model — and that is the subject of GATE-3, not of this file.

**Determinism of the stub is a requirement, not a property.** The same observation gives the
same result regardless of the order of calls, the number of runs and the contents of a cache.
That is why `StubRuntime` has no `random`, no `datetime.now()`, no `uuid4()` and no state
between calls. `uuid5` over a fixed namespace replaces `uuid4`.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence

from community_os_contracts import (
    NegativeSignal,
    Observed,
    ThreadQualityAssessment,
    UnknownReason,
    WindowState,
)

from ..scoring.ranking import sort_candidates
from .policy import (
    ADAPTER_VERSION,
    HEURISTIC_POLICY_VERSION,
    assign_priority_tier,
    confidence,
    data_quality,
    entry_opportunity,
)
from .prompts import PromptVersion
from .types import (
    DeclineReason,
    DiscoveryResult,
    DraftBrief,
    DraftProposal,
    RankedCandidate,
    RuntimeIdentity,
    ThreadObservation,
)

#: Fixed namespace — the identifiers must be the same in every run.
NAMESPACE = uuid.UUID("6f1f4c2e-0d9a-5b3e-9f6c-2f1b0a7c4d18")


def surface_id(archetype: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"surface:{archetype}")


def community_id(community: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"community:{community}")


def _contains_prohibited(text: str, brief: DraftBrief) -> bool:
    """Whether the text touches any of the `prohibited_topics` (L724). Casefold comparison."""
    folded = text.casefold()
    return any(k.casefold() in folded for t in brief.prohibited_topics for k in t.keywords)


class ModelRuntime(ABC):
    """The port. The three tasks from L5168 and nothing beyond them."""

    kind: str
    model: str
    effort: str
    adapter_version: str = ADAPTER_VERSION

    @property
    def identity(self) -> RuntimeIdentity:
        return RuntimeIdentity(
            kind=self.kind,
            model=self.model,
            effort=self.effort,
            adapter_version=self.adapter_version,
        )

    @abstractmethod
    def assess_thread(
        self, prompt: PromptVersion, obs: ThreadObservation
    ) -> ThreadQualityAssessment:
        """Discussion quality classification (22.7)."""

    @abstractmethod
    def rank_candidates(
        self, prompt: PromptVersion, candidates: Sequence[ThreadObservation]
    ) -> DiscoveryResult:
        """Discovery — an ordered list, never a scalar (L4468)."""

    @abstractmethod
    def draft_reply(
        self, prompt: PromptVersion, obs: ThreadObservation, brief: DraftBrief
    ) -> DraftProposal:
        """A reply draft or an explicit decline."""


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------


class StubRuntime(ModelRuntime):
    """A deterministic implementation of the 22.7 rules. No network, no account, no randomness.

    The stub does NOT pretend to be a language model. It is an executable record of the
    definition from the document: what the eval requires of the model is done here outright —
    so if an eval fails on the stub, the bug is in the harness or in the definition, and not
    "in the model".
    """

    kind = "stub"

    def __init__(self, model: str = "stub-22.7", effort: str = "none") -> None:
        self.model = model
        self.effort = effort

    # --- quality classification ----------------------------------------------

    def assess_thread(
        self, prompt: PromptVersion, obs: ThreadObservation
    ) -> ThreadQualityAssessment:
        tier, _reason = assign_priority_tier(obs)
        negative_signals = tuple(
            NegativeSignal(
                signal_class=klasa,
                observed_at=obs.observed_at,
                # The original fixture label stays in the evidence — compressing
                # the five L4444 phenomena into three SignalClass classes loses no information.
                evidence_ref=f"{obs.thread_ref}#negative:{label}",
            )
            for klasa, label in zip(
                obs.negative_signal_classes,
                obs.negative_signal_labels[: len(obs.negative_signal_classes)],
                strict=False,
            )
        )
        return ThreadQualityAssessment(
            thread_ref=obs.thread_ref,
            surface_id=surface_id(obs.archetype),
            community_id=community_id(obs.community),
            observed_at=obs.observed_at,
            collected_at=obs.observed_at,
            adapter_version=self.adapter_version,
            depth_profile=Observed[int].known(obs.depth_levels),
            unique_participants=Observed[int].known(obs.unique_participants),
            substantive_reply_ratio=Observed[float].known(obs.substantive_reply_ratio),
            author_participation=Observed[float].known(float(obs.author_reply_count)),
            # Metric hidden by the platform → UNKNOWN with a reason, NEVER zero (L4299, L5552).
            icp_share=(
                Observed[float].known(obs.icp_share)
                if obs.icp_share is not None
                else Observed[float].unknown(
                    UnknownReason.NOT_EXPOSED_BY_PLATFORM,
                    f"fixture nie pokazuje icp_share dla {obs.thread_ref}",
                )
            ),
            intentional_amplification=Observed[int].known(obs.amplification_count),
            reach_calibration_basis=obs.reach_calibration_basis,
            negative_signals=negative_signals,
            window_state=obs.window_state,
            # Log-only: copied into the result, with no effect on the tier (L4492, L5551).
            fake_engagement_flags=obs.fake_engagement_flags,
            entry_opportunity=entry_opportunity(obs, tier),
            priority_tier=tier,
            confidence=confidence(obs),
            data_quality=data_quality(obs),
            evidence_refs=(
                f"{obs.thread_ref}#thread",
                f"{obs.thread_ref}#metrics",
                # Every original negative label from the fixture, including the one that
                # merged with another after the mapping onto SignalClass.
                *(f"{obs.thread_ref}#negative:{e}" for e in obs.negative_signal_labels),
            ),
            heuristic_policy_version=HEURISTIC_POLICY_VERSION,
        )

    # --- discovery -----------------------------------------------------------

    def rank_candidates(
        self, prompt: PromptVersion, candidates: Sequence[ThreadObservation]
    ) -> DiscoveryResult:
        assessments = [
            (o.dominant_signal_class, self.assess_thread(prompt, o), o) for o in candidates
        ]
        ordered_items = sort_candidates(assessments)
        ranked = []
        for position, (_signal_class, assessment, obs) in enumerate(ordered_items):
            _tier, reason = assign_priority_tier(obs)
            ranked.append(
                RankedCandidate(
                    thread_ref=obs.thread_ref,
                    position=position,
                    priority_tier=assessment.priority_tier,
                    shortlisted=assessment.is_enterable(),
                    reason=reason,
                )
            )
        return DiscoveryResult(
            ordered=tuple(ranked), heuristic_policy_version=HEURISTIC_POLICY_VERSION
        )

    # --- draft ---------------------------------------------------------------

    def draft_reply(
        self, prompt: PromptVersion, obs: ThreadObservation, brief: DraftBrief
    ) -> DraftProposal:
        if obs.negative_signal_classes:
            return self._decline(obs, DeclineReason.NEGATIVE_SIGNAL)
        if obs.window_state is WindowState.CLOSED:
            return self._decline(obs, DeclineReason.WINDOW_CLOSED)
        if not brief.usable_claims:
            return self._decline(obs, DeclineReason.NO_USABLE_CLAIM)

        # `prohibited_topics` bind the claims too — a prohibited topic does not stop
        # being prohibited just because we have evidence for it (L724).
        usable = tuple(c for c in brief.usable_claims if not _contains_prohibited(c.text, brief))
        if not usable:
            return self._decline(obs, DeclineReason.PROHIBITED_TOPIC)

        # The draft refers to the substance of the thread and to the claims that MAY be
        # stated as fact. Unverified claims go solely into `referenced_claims`, as a question
        # and without a marker in the body — L751, L5307.
        fragment = " ".join(obs.title.split()[:8])
        if _contains_prohibited(fragment, brief):
            # Quoting the title would smuggle in a prohibited topic. We enter without a quote.
            opening = "Odniosę się do wątku po stronie inżynierskiej."
        else:
            opening = f"U nas ten problem wyglądał podobnie jak w '{fragment}'."
        sentences = [opening, *[c.text for c in usable]]
        unusable = brief.unusable_claims
        if unusable:
            sentences.append(
                "Nie mam natomiast potwierdzonej liczby na drugą część — "
                "czy ktoś mierzył to u siebie po ostatniej zmianie?"
            )
        text = " ".join(sentences)
        return DraftProposal(
            thread_ref=obs.thread_ref,
            text=text,
            used_claims=tuple(c.id for c in usable),
            referenced_claims=tuple(c.id for c in unusable),
            evidence_refs=tuple(c.evidence_ref for c in usable if c.evidence_ref),
        )

    @staticmethod
    def _decline(obs: ThreadObservation, reason: DeclineReason) -> DraftProposal:
        return DraftProposal(
            thread_ref=obs.thread_ref,
            text="",
            used_claims=(),
            referenced_claims=(),
            evidence_refs=(),
            declined=True,
            decline_reason=reason,
        )


__all__ = [
    "NAMESPACE",
    "ModelRuntime",
    "StubRuntime",
    "community_id",
    "surface_id",
]
