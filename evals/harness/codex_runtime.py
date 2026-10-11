"""`CodexRuntime` — the same eval suite against the real Codex.

**A dependency that does not yet exist at the time this file is written.** D12
(`services/codex_runtime/`) is being built in parallel. Per the briefing rule the import is
written as if the package existed, and is not copied into `evals/`. The import is
LAZY — as long as nobody asks for `CodexRuntime`, the absence of D12 does not break
test collection.

Run: `pytest evals -m live` (or `make evals-live`). Without a ChatGPT account
and without D12 the `live` tests are skipped with a recorded reason — a skip is NOT
a positive result (GATE-3 without `evals_live` yields `NO_DATA`, never a default GO).

The contract this adapter expects from D12:

    codex_runtime.CodexRuntimeClient(codex_home=..., config=HardenedConfig())
    client.start_thread(role: ThreadRole) -> ThreadHandle
    handle.run_turn(TurnSpec(role=..., model=..., effort=..., output_schema=...),
                    instructions: str, user_input: str) -> TurnResult
    TurnResult.structured_output: dict
    TurnResult.effective_model / effective_effort / effort_observable

Thread roles (L1952, L5313): classification and discovery go with the WORKER role (Luna —
high volume, cheap), the draft with the PLANNER role (Terra). Every task in a SEPARATE
thread, with no resume between roles.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from community_os_contracts import (
    NegativeSignal,
    Observed,
    PriorityTier,
    ThreadQualityAssessment,
    ThreadRole,
    UnknownReason,
    WindowState,
)

from .policy import ADAPTER_VERSION, HEURISTIC_POLICY_VERSION
from .prompts import PromptVersion
from .runtime import ModelRuntime, community_id, surface_id
from .schemas import (
    DISCOVERY_OUTPUT_SCHEMA,
    DRAFT_OUTPUT_SCHEMA,
    THREAD_QUALITY_OUTPUT_SCHEMA,
)
from .types import (
    DeclineReason,
    DiscoveryResult,
    DraftBrief,
    DraftProposal,
    RankedCandidate,
    ThreadObservation,
)


class CodexRuntimeUnavailable(RuntimeError):
    """D12 is not installed or the ChatGPT account is not logged in."""


def _client_factory():
    """Lazy import of D12. A separate function so that a test can swap it out."""
    try:
        from codex_runtime import CodexRuntimeClient  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CodexRuntimeUnavailable(
            "Pakiet `codex_runtime` (D12, services/codex_runtime/) nie jest dostępny. "
            "Evale `live` wymagają go razem z zalogowanym kontem ChatGPT. "
            "Pominięcie tych evali NIE jest wynikiem pozytywnym — GATE-3 bez "
            "`evals_live` to NO_DATA."
        ) from exc
    return CodexRuntimeClient


class CodexRuntime(ModelRuntime):
    """The real runtime. Requires `@pytest.mark.live`, a ChatGPT account and the D12 package."""

    kind = "codex"

    def __init__(self, model: str, effort: str, *, client=None) -> None:
        self.model = model
        self.effort = effort
        self.adapter_version = ADAPTER_VERSION
        self._client = client if client is not None else _client_factory()()

    # --- helpers -------------------------------------------------------------

    def _turn(self, prompt: PromptVersion, role: ThreadRole, payload: dict, schema: dict) -> dict:
        """One turn in a FRESH thread. No resume between tasks (L5313)."""
        from community_os_contracts import TurnSpec

        handle = self._client.start_thread(role=role)
        result = handle.run_turn(
            TurnSpec(role=role, model=self.model, effort=self.effort, output_schema=schema),
            instructions=prompt.text,
            user_input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        if getattr(result, "effective_model", None) not in (None, self.model):
            # `model/rerouted` → the effective effort is unobserved → ROUTE_MISMATCH
            # blocks the commit (D12). The eval likewise has no right to pretend the result
            # comes from the model it asked for.
            raise CodexRuntimeUnavailable(
                f"ROUTE_MISMATCH: zażądano {self.model}, runtime użył {result.effective_model}"
            )
        return result.structured_output

    @staticmethod
    def _observed_float(raw) -> Observed[float]:
        """`{'unknown_reason': ...}` → UNKNOWN. A number → the value. `None` → AN ERROR.

        A model that returns `null` instead of an explicit `UNKNOWN` with a reason breaks
        invariant 7 from 22.2 — and this is to show up as a failing eval, not as a zero.
        """
        if isinstance(raw, dict) and raw.get("unknown_reason"):
            return Observed[float].unknown(
                UnknownReason(raw["unknown_reason"]), raw.get("unknown_detail")
            )
        if isinstance(raw, dict) and "value" in raw and raw["value"] is not None:
            return Observed[float].known(float(raw["value"]))
        if isinstance(raw, int | float):
            return Observed[float].known(float(raw))
        raise ValueError(
            f"Metryka nie jest ani liczbą, ani UNKNOWN z powodem: {raw!r}. "
            "Brak danych bez powodu jest zakazany (L4299, L5242)."
        )

    @staticmethod
    def _observed_int(raw) -> Observed[int]:
        if isinstance(raw, dict) and raw.get("unknown_reason"):
            return Observed[int].unknown(
                UnknownReason(raw["unknown_reason"]), raw.get("unknown_detail")
            )
        if isinstance(raw, dict) and "value" in raw and raw["value"] is not None:
            return Observed[int].known(int(raw["value"]))
        if isinstance(raw, int) and not isinstance(raw, bool):
            return Observed[int].known(raw)
        raise ValueError(f"Metryka nie jest ani liczbą, ani UNKNOWN z powodem: {raw!r}")

    # --- port ----------------------------------------------------------------

    def assess_thread(
        self, prompt: PromptVersion, obs: ThreadObservation
    ) -> ThreadQualityAssessment:
        out = self._turn(
            prompt,
            ThreadRole.WORKER,
            observation_payload(obs),
            THREAD_QUALITY_OUTPUT_SCHEMA,
        )
        negative_signals = tuple(
            NegativeSignal(
                signal_class=s["signal_class"],
                observed_at=obs.observed_at,
                evidence_ref=s.get("evidence_ref", f"{obs.thread_ref}#negative"),
            )
            for s in out.get("negative_signals", [])
        )
        return ThreadQualityAssessment(
            thread_ref=obs.thread_ref,
            surface_id=surface_id(obs.archetype),
            community_id=community_id(obs.community),
            observed_at=obs.observed_at,
            collected_at=obs.observed_at,
            adapter_version=self.adapter_version,
            depth_profile=self._observed_int(out["depth_profile"]),
            unique_participants=self._observed_int(out["unique_participants"]),
            substantive_reply_ratio=self._observed_float(out["substantive_reply_ratio"]),
            author_participation=self._observed_float(out["author_participation"]),
            icp_share=self._observed_float(out["icp_share"]),
            intentional_amplification=self._observed_int(out["intentional_amplification"]),
            reach_calibration_basis=out.get("reach_calibration_basis", obs.reach_calibration_basis),
            negative_signals=negative_signals,
            window_state=WindowState(out["window_state"]),
            fake_engagement_flags=tuple(out.get("fake_engagement_flags", ())),
            entry_opportunity=out["entry_opportunity"],
            priority_tier=PriorityTier(out["priority_tier"]),
            confidence=float(out["confidence"]),
            data_quality=out["data_quality"],
            evidence_refs=tuple(out.get("evidence_refs", ())),
            # The policy version is OURS, not the model's — the model may not invent it.
            heuristic_policy_version=HEURISTIC_POLICY_VERSION,
        )

    def rank_candidates(
        self, prompt: PromptVersion, candidates: Sequence[ThreadObservation]
    ) -> DiscoveryResult:
        payload = {
            "heuristic_policy_version": HEURISTIC_POLICY_VERSION,
            "candidates": [observation_payload(o) for o in candidates],
        }
        out = self._turn(prompt, ThreadRole.WORKER, payload, DISCOVERY_OUTPUT_SCHEMA)
        ranked = tuple(
            RankedCandidate(
                thread_ref=item["thread_ref"],
                position=i,
                priority_tier=PriorityTier(item["priority_tier"]),
                shortlisted=bool(item["shortlisted"]),
                reason=item.get("reason", ""),
            )
            for i, item in enumerate(out["ordered"])
        )
        return DiscoveryResult(ordered=ranked, heuristic_policy_version=HEURISTIC_POLICY_VERSION)

    def draft_reply(
        self, prompt: PromptVersion, obs: ThreadObservation, brief: DraftBrief
    ) -> DraftProposal:
        payload = {
            "thread": observation_payload(obs),
            "entry_thesis": brief.entry_thesis,
            "claims": [
                {"id": c.id, "text": c.text, "status": c.status.value, "marker": c.marker}
                for c in brief.claims
            ],
            "prohibited_topics": [
                {"id": t.id, "keywords": list(t.keywords)} for t in brief.prohibited_topics
            ],
            "approved_ctas": list(brief.approved_ctas),
        }
        out = self._turn(prompt, ThreadRole.PLANNER, payload, DRAFT_OUTPUT_SCHEMA)
        reason = out.get("decline_reason")
        return DraftProposal(
            thread_ref=obs.thread_ref,
            text=out.get("text", ""),
            used_claims=tuple(out.get("used_claims", ())),
            referenced_claims=tuple(out.get("referenced_claims", ())),
            evidence_refs=tuple(out.get("evidence_refs", ())),
            declined=bool(out.get("declined", False)),
            decline_reason=DeclineReason(reason) if reason else None,
        )


def observation_payload(obs: ThreadObservation) -> dict:
    """Observation → JSON for the model.

    A metric invisible to the platform goes as an explicit `UNKNOWN` with a reason, not as
    `null` and not as 0 — the model is to receive exactly the signal it is to return.
    """

    def metric(value, nazwa: str):
        if value is None:
            return {
                "unknown_reason": UnknownReason.NOT_EXPOSED_BY_PLATFORM.value,
                "unknown_detail": f"platforma nie pokazuje {nazwa}",
            }
        return {"value": value}

    return {
        "thread_ref": obs.thread_ref,
        "archetype": obs.archetype,
        "community": obs.community,
        "title": obs.title,
        "body": obs.body,
        "author": obs.author,
        "created_at": obs.created_at.isoformat(),
        "observed_at": obs.observed_at.isoformat(),
        "depth_levels": obs.depth_levels,
        "reply_count": obs.reply_count,
        "unique_participants": obs.unique_participants,
        "median_reply_chars": obs.median_reply_chars,
        "replies_first_hour": obs.replies_first_hour,
        "substantive_reply_ratio": obs.substantive_reply_ratio,
        "author_reply_count": obs.author_reply_count,
        "author_first_reply_minutes": obs.author_first_reply_minutes,
        "icp_share": metric(obs.icp_share, "icp_share"),
        "reach_impressions": metric(obs.reach_impressions, "reach_impressions"),
        "reach_calibration_basis": obs.reach_calibration_basis,
        "hidden_metrics": list(obs.hidden_metrics),
        "votes": obs.votes,
        "reaction_total": obs.reaction_total,
        "amplification_count": obs.amplification_count,
        "amplification_kinds": list(obs.amplification_kinds),
        "window_state": obs.window_state.value,
        "locked": obs.locked,
        "removed_by_moderation": obs.removed_by_moderation,
        "negative_signals": list(obs.negative_signal_labels),
        "fake_engagement_flags": list(obs.fake_engagement_flags),
        "dominant_signal_class": obs.dominant_signal_class.value,
        "heuristic_policy_version": HEURISTIC_POLICY_VERSION,
    }


__all__ = ["CodexRuntime", "CodexRuntimeUnavailable", "observation_payload"]
