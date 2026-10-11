"""Capability as versioned per-account state — section 14.7 (L2523-2580).

The key sentences this module enforces:

* "A capability snapshot contains `verified_at`, `expires_at`, `browser_build_digest`,
   `required_browser_mode`, `reason_code` and `confidence`. After expiry its state becomes
   `UNKNOWN` until a re-probe. `MANUAL_ONLY` creates a human task, and not an ordinary
   capability error." (L2580)
* "every capability has a recorded `required_browser_mode`; those requiring a human are
   marked `MANUAL_ONLY`" — Definition of Done of phase F0 (L5177)
* "a capability that does not work headless has `required_browser_mode = INTERACTIVE` or
   `MANUAL_ONLY`, never a silent fallback" (L5537)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    AccessMode,
    CapabilityState,
    CapsuleMode,
    ExecutionMode,
    ResultEvidenceKind,
    RetryClass,
    SemanticCapability,
    SideEffect,
)


class CapabilitySnapshot(BaseModel):
    """The versioned state of one capability for one account on one surface.

    The snapshot is immutable. A re-probe creates a new snapshot, it does not mutate the old
    one — thanks to that a `MarketingRegistrySnapshot` pinned to a run gives a reproducible
    result (L5282).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: SemanticCapability
    state: CapabilityState
    execution_mode: ExecutionMode
    side_effect: SideEffect
    retry_class: RetryClass
    result_evidence: tuple[ResultEvidenceKind, ...] = ()

    #: The capsule mode required for this capability to work at all (DoD F0, L5177).
    #: `None` means "not applicable" only for capabilities that do not use the browser.
    required_browser_mode: CapsuleMode | None
    #: The exact browser build on which the probe was performed (L2580).
    #: A build change invalidates the snapshot — we run the capability probe in the same mode
    #: and on the same build on which the automation later works (L4381).
    browser_build_digest: str
    reason_code: str
    confidence: float = Field(ge=0.0, le=1.0)
    verified_at: datetime
    expires_at: datetime
    adapter_version: str

    @model_validator(mode="after")
    def _internal_consistency(self) -> CapabilitySnapshot:
        if self.expires_at <= self.verified_at:
            raise ValueError("expires_at musi być późniejsze niż verified_at")

        # L5537: a capability that does not work headless must not pretend that it does.
        if (
            self.required_browser_mode is CapsuleMode.HEADLESS
            and self.execution_mode is ExecutionMode.MANUAL_ONLY
        ):
            raise ValueError(
                "MANUAL_ONLY nie może mieć required_browser_mode=HEADLESS — "
                "capability wymagająca człowieka nie działa bez ekranu (L5537)."
            )

        # L4294, L5150: in E1 AUTOMATED does not exist for a writing action.
        if (
            self.side_effect is SideEffect.IRREVERSIBLE
            and self.execution_mode is ExecutionMode.AUTOMATED
        ):
            raise ValueError(
                "Akcja nieodwracalna nie może mieć execution_mode=AUTOMATED. "
                "W E1 wszystkie akcje zapisujące to AUTOMATED_AFTER_APPROVAL (L4294)."
            )

        # L2673, L5335: an irreversible side effect is never safe to retry.
        if (
            self.side_effect is SideEffect.IRREVERSIBLE
            and self.retry_class is RetryClass.SAFE_RETRY
        ):
            raise ValueError("Akcja nieodwracalna nie może mieć retry_class=SAFE_RETRY (L2813).")
        return self

    def effective_state(self, now: datetime) -> CapabilityState:
        """The state with expiry taken into account.

        "After expiry its state becomes `UNKNOWN` until a re-probe." (L2580, L5603)
        We do not mutate the snapshot — we compute the effective state at the moment of use.
        """
        if now >= self.expires_at:
            return CapabilityState.UNKNOWN
        return self.state

    def requires_human_task(self, now: datetime) -> bool:
        """`MANUAL_ONLY` creates a human task, and not an ordinary capability error (L2580)."""
        return self.effective_state(now) is CapabilityState.MANUAL_ONLY


#: The capability mapping in force in E1 — table L4347-4359, transcribed verbatim.
#: This is the gate contract: no writing action has `AUTOMATED` here.
E1_CAPABILITY_MAPPING: dict[SemanticCapability, ExecutionMode] = {
    SemanticCapability.DISCOVER_SURFACES: ExecutionMode.AUTOMATED,
    SemanticCapability.SEARCH_CONTENT: ExecutionMode.AUTOMATED,
    SemanticCapability.READ_CONTENT: ExecutionMode.AUTOMATED,
    SemanticCapability.READ_THREAD: ExecutionMode.AUTOMATED,
    SemanticCapability.READ_METRICS: ExecutionMode.AUTOMATED,
    SemanticCapability.PUBLISH_REPLY: ExecutionMode.AUTOMATED_AFTER_APPROVAL,
    SemanticCapability.REACT_TO_CONTENT: ExecutionMode.AUTOMATED_AFTER_APPROVAL,
    # L4356: "AUTOMATED_AFTER_APPROVAL or MANUAL_ONLY, depending on the adapter".
    # We record the stricter variant as the default; an adapter may go down to MANUAL_ONLY,
    # never higher.
    SemanticCapability.JOIN_COMMUNITY: ExecutionMode.AUTOMATED_AFTER_APPROVAL,
    # L4357: an own post enters only after PASS — out of scope in E1.
    SemanticCapability.PUBLISH_CONTENT: ExecutionMode.UNSUPPORTED,
    SemanticCapability.REQUEST_HUMAN_ACTION: ExecutionMode.AUTOMATED,
    # L4359: no paid channels in this stage.
    SemanticCapability.MANAGE_CAMPAIGN: ExecutionMode.UNSUPPORTED,
    SemanticCapability.READ_AD_METRICS: ExecutionMode.UNSUPPORTED,
}

#: Read capabilities that on a surface behind a login wall are a function of the account
#: state (L4401, L5526).
READ_CAPABILITIES_DERIVED_FROM_ACCOUNT: frozenset[SemanticCapability] = frozenset(
    {
        SemanticCapability.SEARCH_CONTENT,
        SemanticCapability.READ_CONTENT,
        SemanticCapability.READ_THREAD,
        SemanticCapability.READ_METRICS,
    }
)

#: Writing actions in E1 — exactly three kinds (L5300: comment, reply in a thread, reaction).
E1_WRITE_CAPABILITIES: frozenset[SemanticCapability] = frozenset(
    {
        SemanticCapability.PUBLISH_REPLY,
        SemanticCapability.REACT_TO_CONTENT,
        SemanticCapability.JOIN_COMMUNITY,
    }
)


def read_capability_requires_connected_account(
    capability: SemanticCapability, access_mode: AccessMode
) -> bool:
    """Whether this read on this surface needs a connected account.

    The rule from L4400-4402 and the acceptance test from L5526-5529:
    a `PUBLIC_API_NO_ACCOUNT` surface is read without a profile and without a ProfileLease;
    `UNKNOWN` is treated like `ACCOUNT_REQUIRED`.
    """
    if capability not in READ_CAPABILITIES_DERIVED_FROM_ACCOUNT:
        return False
    return access_mode in {
        AccessMode.ACCOUNT_REQUIRED,
        AccessMode.MEMBERSHIP_REQUIRED,
        AccessMode.UNKNOWN,
    }


def cheaper_read_path_preferred(a: AccessMode, b: AccessMode) -> AccessMode:
    """At equal audience fit the planner prefers `PUBLIC_API_NO_ACCOUNT` (L4400, L5528)."""
    ranking = {
        AccessMode.PUBLIC_API_NO_ACCOUNT: 0,
        AccessMode.PUBLIC_WEB_NO_ACCOUNT: 1,
        AccessMode.ACCOUNT_REQUIRED: 2,
        AccessMode.MEMBERSHIP_REQUIRED: 3,
        AccessMode.UNKNOWN: 3,
    }
    return a if ranking[a] <= ranking[b] else b


class ResourceRequirement(BaseModel):
    """No account for an `ACCOUNT_REQUIRED` surface creates a `ResourceRequirement`, not an
    empty result.

    "No account for a community creates a `ResourceRequirement`, not »we will scrape it
     anonymously«" (STAN-PROJEKTU.md, settled point 4; L4402, L5526).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    organization_id: UUID
    workspace_id: UUID
    surface_id: UUID
    capability: SemanticCapability
    access_mode: AccessMode
    reason: str
