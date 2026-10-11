"""Immutable approval and a single-use ExecutionIntent — section 16 (L2736-2828).

This is the sharpest boundary in the whole system. The requirements this module is to enforce
by type, and not by the programmer's discipline:

* "immutable `ActionProposal` / `ExecutionIntent` / `AutomationRun` schemas with a payload
   hash" (L5198)
* "no writing side effect bypasses `ExecutionIntent`" (L5217)
* "the approval points at the exact payload hash; editing the content invalidates the
   consent" (L5218)
* "Every edit creates a new revision and invalidates the previous approval." (L2795)
* "at most one automatic submit attempt per ExecutionIntent" (L2821)
* "`SUBMIT_STARTED` / `UNKNOWN` → no automatic retry" (L2813-2814)
* "an action without a valid approval is unexecutable also on a manual adapter call" (L5334)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import STATES_FORBIDDING_AUTOMATIC_RETRY, ExecutionAttemptState, SemanticCapability


class ActionProposalState(StrEnum):
    """L2773-2775."""

    DRAFT = "DRAFT"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class PreflightFailure(StrEnum):
    """Reasons for which preflight stops the execution even after the approval."""

    SOURCE_CHANGED = "SOURCE_CHANGED"  # L2828
    AUTHORIZATION_CONTEXT_CHANGED = "AUTHORIZATION_CONTEXT_CHANGED"  # L2797
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"  # L5601
    CAPABILITY_BLOCKED = "CAPABILITY_BLOCKED"  # L5602
    INTENT_ALREADY_CONSUMED = "INTENT_ALREADY_CONSUMED"  # L2821
    INTENT_EXPIRED = "INTENT_EXPIRED"  # L5692
    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"  # L5218


class ApprovalHashInput(BaseModel):
    """Exactly twelve fields that go into the approval hash (L2778-2793).

    The order of the fields in this class is the order from the document. Adding or omitting
    any one of them changes the meaning of the operator's consent, which is why the model is
    closed (`extra="forbid"`) and immutable.

    Mind `adapter_version` (L2792): it is an input of the hash, so **deploying a new adapter
    version invalidates pending approvals**. This is intended — the operator consented to
    an action performed by one specific piece of code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization: UUID
    workspace: UUID
    assignment_version: UUID
    account: UUID
    platform: str
    action_type: SemanticCapability
    target_external_id: str
    target_url: str
    source_fingerprint: str
    text_and_media: str
    intelligence_profile_version: UUID
    plan_version: UUID
    memory_snapshot: UUID
    adapter_version: str

    def canonical_bytes(self) -> bytes:
        """The canonical, stable byte representation.

        `sort_keys=True` and `separators` without spaces give determinism independent of the
        insertion order and of the Python version. `ensure_ascii=False` + UTF-8, so that Polish
        characters and emoji in the draft content do not change the hash by changing escaping.
        """
        payload: dict[str, Any] = json.loads(self.model_dump_json())
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def payload_hash(self) -> str:
        """SHA-256 of the canonical representation, in hex form."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ActionProposalRevision(BaseModel):
    """An immutable revision of an action proposal.

    An edit does NOT mutate the revision — it creates a new one (L2795, L5310). That is why
    the model is frozen, and `supersede` returns a new object with a new revision number and
    a new hash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    proposal_id: UUID
    revision_no: int = Field(ge=1)
    state: ActionProposalState
    hash_input: ApprovalHashInput
    payload_hash: str
    created_at: datetime
    #: A draft has a TTL and expires with the thread window (invariant 9, L4301; L5539-5540).
    expires_at: datetime
    #: Fingerprint and moment of the source read — input to the source-change check (L2828).
    source_observed_at: datetime

    @model_validator(mode="after")
    def _hash_matches_input(self) -> ActionProposalRevision:
        expected = self.hash_input.payload_hash()
        if self.payload_hash != expected:
            raise ValueError(
                "payload_hash nie odpowiada hash_input — rewizja jest niespójna. "
                f"Oczekiwano {expected}, jest {self.payload_hash}."
            )
        return self

    @model_validator(mode="after")
    def _ttl_is_valid(self) -> ActionProposalRevision:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at musi być późniejsze niż created_at")
        return self

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        proposal_id: UUID,
        revision_no: int,
        state: ActionProposalState,
        hash_input: ApprovalHashInput,
        created_at: datetime,
        expires_at: datetime,
        source_observed_at: datetime,
    ) -> ActionProposalRevision:
        """Builds a revision, computing the hash from the input — the only recommended way."""
        return cls(
            id=id,
            proposal_id=proposal_id,
            revision_no=revision_no,
            state=state,
            hash_input=hash_input,
            payload_hash=hash_input.payload_hash(),
            created_at=created_at,
            expires_at=expires_at,
            source_observed_at=source_observed_at,
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def supersede(
        self,
        *,
        new_id: UUID,
        new_hash_input: ApprovalHashInput,
        now: datetime,
        source_observed_at: datetime | None = None,
    ) -> tuple[ActionProposalRevision, ActionProposalRevision]:
        """Creates the next revision and puts THIS one into `SUPERSEDED`.

        Returns both rows — `(superseded_previous, new_revision)` — because the model is frozen
        and both have to be persisted. Returning only the new one is what the hole in this class
        looked like: the docstring promised `supersede` from the first commit, the method did not
        exist, so nothing ever moved the old revision out of `APPROVED`. A revision approved
        before an edit stayed executable, and `authorize_side_effect` let it through — the
        operator's consent covering text they no longer see. That is the direction along which
        stale content leaks (L2795, L5218, L5310).

        Three rules the signature makes non-negotiable:

        * **The TTL is not a parameter.** The new revision inherits `expires_at` unchanged,
          because "edycja draftu tworzy nową rewizję, unieważnia zgodę i nie przedłuża
          pierwotnego TTL" (L5539). Were it an argument, an editing loop could keep a draft
          alive past the thread window forever.
        * **A new hash is required.** An edit that changes nothing is not an edit; it would
          invalidate a consent for no reason and leave two revisions the approval cannot tell
          apart.
        * **The state comes from the transition table**, not from a copy of the rule kept here.
          L2775 draws exactly one arrow into `SUPERSEDED`, and it starts at `APPROVED`.

        The successor starts as `DRAFT`: it needs its own approval, which is precisely what
        "unieważnia poprzedni approval" means (L2795).
        """
        # Imported here, not at module level, because `transitions` imports `ActionProposalState`
        # from this module. The cycle is real; the deferred import is the cheaper of the two
        # fixes, the other being a move of the enum into `enums.py`.
        from .transitions import require_transition

        require_transition(self.state, ActionProposalState.SUPERSEDED)

        if self.is_expired(now):
            raise ValueError(
                f"Rewizja wygasła o {self.expires_at.isoformat()} — draft po TTL przechodzi "
                "w EXPIRED i nie jest regenerowany bez decyzji operatora (L5540). "
                "Edycja nie jest drogą naokoło tego wygaśnięcia."
            )

        new_hash = new_hash_input.payload_hash()
        if new_hash == self.payload_hash:
            raise ValueError(
                "Nowa rewizja ma ten sam payload hash co poprzednia — to nie jest edycja. "
                "Każda edycja tworzy nową rewizję z nową treścią (L2795)."
            )

        superseded = self.model_copy(update={"state": ActionProposalState.SUPERSEDED})
        successor = ActionProposalRevision.create(
            id=new_id,
            proposal_id=self.proposal_id,
            revision_no=self.revision_no + 1,
            state=ActionProposalState.DRAFT,
            hash_input=new_hash_input,
            created_at=now,
            # NOT extended. L5539.
            expires_at=self.expires_at,
            source_observed_at=source_observed_at or self.source_observed_at,
        )
        return superseded, successor


class ApprovalDecision(BaseModel):
    """A human's consent to EXACTLY one revision.

    "the approval points at the exact payload hash; editing the content invalidates the
    consent" (L5218). That is why the consent carries the hash, and not only the revision
    identifier — the identifier alone would not protect against swapping the content
    under the same ID.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    revision_id: UUID
    approved_payload_hash: str
    approved_by: UUID | None = None
    approved_membership_id: UUID | None = None
    approved_at: datetime
    #: A rejection requires a reason, which goes into preference memory (L5311).
    reason: str | None = None

    @model_validator(mode="after")
    def one_signer(self) -> ApprovalDecision:
        if (self.approved_by is None) == (self.approved_membership_id is None):
            raise ValueError("An approval must have exactly one signer.")
        return self

    def matches(self, revision: ActionProposalRevision) -> bool:
        """Whether this consent concerns exactly this revision and this content."""
        return (
            self.revision_id == revision.id and self.approved_payload_hash == revision.payload_hash
        )


class ExecutionIntent(BaseModel):
    """A single-use authorization to perform a writing action.

    "a single-use `ExecutionIntent` bound to the exact revision, account and assignment" (L5321).
    "at most one automatic submit attempt per ExecutionIntent" (L2821).
    "a stale execution grant is single-use and expires" (L5692).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    revision_id: UUID
    payload_hash: str
    account_id: UUID
    assignment_version_id: UUID
    issued_at: datetime
    expires_at: datetime
    #: Set once, at the moment of consumption. A second consumption is impossible — see `consume`.
    consumed_at: datetime | None = None

    def consume(self, now: datetime) -> ExecutionIntent:
        """Returns the consumed intent. A second call is an error, not a no-op."""
        if self.consumed_at is not None:
            raise ValueError(
                f"ExecutionIntent {self.id} został już zużyty o {self.consumed_at.isoformat()}. "
                "Jeden intent = najwyżej jedna próba submitu (L2821)."
            )
        if now >= self.expires_at:
            raise ValueError(f"ExecutionIntent {self.id} wygasł o {self.expires_at.isoformat()}.")
        return self.model_copy(update={"consumed_at": now})


def authorize_side_effect(
    *,
    revision: ActionProposalRevision,
    approval: ApprovalDecision | None,
    intent: ExecutionIntent | None,
    now: datetime | None = None,
) -> None:
    """The full set of checks guarding a writing action. Raises `PermissionError` on any gap.

    The acceptance test checks that a manual adapter call without an approval also fails
    (L5334, L5538).

    **This is the check, not the path.** It returns nothing, so the strongest sentence a caller
    can write about it is "it did not raise" — and it leaves the intent unconsumed, so nothing
    stops a second attempt. Production code calls `authorize_and_consume`, which runs these very
    checks, burns the intent and hands back a `SideEffectAuthorization`.
    """
    now = now or datetime.now(UTC)

    if approval is None:
        raise PermissionError(
            "Brak ApprovalDecision. Akcja zapisująca bez zgody człowieka jest niewykonalna "
            "także przy ręcznym wywołaniu adaptera (L5334)."
        )
    if intent is None:
        raise PermissionError(
            "Brak ExecutionIntent. Żaden zapisujący side effect nie omija ExecutionIntent (L5217)."
        )
    if revision.state is not ActionProposalState.APPROVED:
        raise PermissionError(f"Rewizja jest w stanie {revision.state}, a nie APPROVED.")
    if revision.is_expired(now):
        raise PermissionError(
            f"Rewizja wygasła o {revision.expires_at.isoformat()} — draft po TTL przechodzi "
            "w EXPIRED i nie jest regenerowany bez decyzji operatora (L5540)."
        )
    if not approval.matches(revision):
        raise PermissionError(
            "Zgoda nie pasuje do rewizji albo do jej treści. Edycja tworzy nową rewizję "
            "i unieważnia poprzednią zgodę (L2795, L5218)."
        )
    if intent.revision_id != revision.id or intent.payload_hash != revision.payload_hash:
        raise PermissionError("ExecutionIntent nie jest związany z tą dokładną rewizją (L5321).")
    if intent.consumed_at is not None:
        raise PermissionError(
            f"ExecutionIntent {intent.id} już zużyty — brak drugiej automatycznej próby (L2821)."
        )
    if now >= intent.expires_at:
        raise PermissionError(f"ExecutionIntent {intent.id} wygasł.")


class SideEffectAuthorization(BaseModel):
    """Proof that ONE writing action was authorized against ONE payload.

    Why this type exists: `authorize_side_effect` returned `None`, so a caller finished the check
    holding nothing. The only test one could write about it was "it did not raise", which is the
    same class of evidence as a gate announcing `GO` before the measurement — and the audit trail
    had nothing to record beyond the fact that some code once called some function.

    The proof carries the consumed intent, not just its identifier, so that the caller holds the
    row that has to be persisted. An authorization whose intent stays unconsumed in the store is
    the second automatic submit attempt L2821 forbids, arriving one restart later.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: UUID
    payload_hash: str
    intent_id: UUID
    authorized_at: datetime
    consumed_intent: ExecutionIntent

    @model_validator(mode="after")
    def _the_proof_is_bound_to_a_consumed_intent(self) -> SideEffectAuthorization:
        if self.consumed_intent.consumed_at is None:
            raise ValueError(
                "SideEffectAuthorization dla niezużytego ExecutionIntent. Dowód autoryzacji "
                "powstaje po zużyciu intentu, nie przed (L2821)."
            )
        if self.consumed_intent.id != self.intent_id:
            raise ValueError("consumed_intent to inny intent niż intent_id")
        if self.consumed_intent.revision_id != self.revision_id:
            raise ValueError("consumed_intent dotyczy innej rewizji niż revision_id")
        if self.consumed_intent.payload_hash != self.payload_hash:
            raise ValueError("consumed_intent niesie inny payload_hash niż dowód")
        return self


def authorize_and_consume(
    *,
    revision: ActionProposalRevision,
    approval: ApprovalDecision | None,
    intent: ExecutionIntent | None,
    now: datetime | None = None,
) -> SideEffectAuthorization:
    """The only production path to a writing side effect (L5217, L5334).

    Runs every check of `authorize_side_effect`, then consumes the intent in the same call and
    returns the proof. Checking and consuming cannot be separated by a caller's forgetfulness:
    a second call with the same intent ends with `PermissionError`, because the intent it is
    handed is already spent.
    """
    now = now or datetime.now(UTC)

    authorize_side_effect(revision=revision, approval=approval, intent=intent, now=now)
    if intent is None:  # pragma: no cover — `authorize_side_effect` has already raised
        raise PermissionError(
            "Brak ExecutionIntent. Żaden zapisujący side effect nie omija ExecutionIntent (L5217)."
        )

    consumed = intent.consume(now)
    return SideEffectAuthorization(
        revision_id=revision.id,
        payload_hash=revision.payload_hash,
        intent_id=intent.id,
        authorized_at=now,
        consumed_intent=consumed,
    )


class AutomationRun(BaseModel):
    """One automated run of an approved action — L5198, decision D-020.

    D-020 settles what the document leaves open: the chain `automation_runs` does not occur in
    the roadmap even once, the data model knows only `execution_attempts` (L3129) and the state
    machine is called `ExecutionAttempt` (L2600-2605). So `AutomationRun` is a TYPE over that
    table, NOT a table of its own — a migration in the other direction would have been
    one-way work on a database that is already in use.

    Immutable, with a payload hash, exactly as L5198 requires. The hash is what ties the run to
    the operator's consent: the audit answers "which content did this run publish" without
    trusting that the proposal row was never edited afterwards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The `execution_attempts` row this run IS. Not a foreign key to a separate table (D-020).
    execution_attempt_id: UUID
    organization_id: UUID
    workspace_id: UUID
    intent_id: UUID
    revision_id: UUID
    payload_hash: str
    #: Counted against the attempt limit; the last attempt terminalizes the job (L5214).
    attempt_no: int = Field(ge=1)
    state: ExecutionAttemptState
    #: An input of the approval hash (L2792) — kept on the run so the evidence names the exact
    #: code that acted.
    adapter_version: str
    started_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _finished_after_started(self) -> AutomationRun:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at jest wcześniejsze niż started_at")
        return self

    @classmethod
    def from_authorization(
        cls,
        authorization: SideEffectAuthorization,
        *,
        execution_attempt_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        attempt_no: int,
        state: ExecutionAttemptState,
        adapter_version: str,
        started_at: datetime,
        finished_at: datetime | None = None,
    ) -> AutomationRun:
        """Builds a run from the authorization proof — the only recommended way.

        The three fields that bind the run to the consent (`revision_id`, `payload_hash`,
        `intent_id`) are copied from the proof and are not arguments here, so a run cannot be
        recorded against a payload nobody approved.
        """
        return cls(
            execution_attempt_id=execution_attempt_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            intent_id=authorization.intent_id,
            revision_id=authorization.revision_id,
            payload_hash=authorization.payload_hash,
            attempt_no=attempt_no,
            state=state,
            adapter_version=adapter_version,
            started_at=started_at,
            finished_at=finished_at,
        )

    def may_retry(self) -> bool:
        """Whether this run's state still allows an automatic retry (L2673, L5335)."""
        return may_retry_automatically(self.state)


def may_retry_automatically(state: ExecutionAttemptState) -> bool:
    """Whether an attempt may be automatically retried from this state.

    `PREPARED` and `DISPATCHED` may receive a new attempt (L2809-2811).
    `SUBMIT_STARTED` and `UNKNOWN` — never (L2813-2814, L5335).
    """
    return state not in STATES_FORBIDDING_AUTOMATIC_RETRY
