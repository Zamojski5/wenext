"""Allowed state transitions — section 14.8 (L2582-2606) and section 16.1 (L2773-2775).

Six machines, transcribed one to one. This module is the ONLY place in the repository where
that knowledge lives; `require_transition` refuses everything outside the table, so "the state
drifted apart" is not representable. The same shape as `TRANSITIONS` in
`services/codex_runtime/codex_runtime/account_adapter.py`, which does this for the Codex
connection machine (L2134-2141).

Two rules that look like details and are not:

* **Every state is a key, terminal ones included, with an empty set of targets.** A missing key
  would make `require_transition` raise `KeyError` from a terminal state instead of refusing the
  transition — an accident that reads as a crash rather than as a rejection.
* **Nothing here is "obvious".** `DRAFT → EXPIRED` is not in the table, because L2773-2775 does
  not draw that arrow, even though L5540 speaks of a draft expiring. The discrepancy is written
  down in NOTES.md rather than settled silently by adding a transition the document lacks.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from .enums import (
    BrowserProfileState,
    BrowserSessionState,
    ExecutionAttemptState,
    HumanTakeoverState,
    SocialAccountState,
)
from .execution import ActionProposalState


class IllegalTransition(Exception):
    """A transition outside the table of section 14.8 / 16.1."""

    def __init__(
        self,
        *,
        machine: str,
        source: StrEnum,
        target: StrEnum,
        allowed: frozenset[StrEnum],
    ) -> None:
        allowed_text = ", ".join(sorted(state.value for state in allowed)) or "(stan terminalny)"
        super().__init__(
            f"Niedozwolone przejście maszyny {machine}: {source.value} → {target.value}. "
            f"Dozwolone z {source.value}: {allowed_text}."
        )
        self.machine = machine
        self.source = source
        self.target = target
        self.allowed = allowed


# ---------------------------------------------------------------------------
# Section 14.8 — the five machines drawn at L2584-2606
# ---------------------------------------------------------------------------

#: L2585-2587:
#:     PROVISIONED → VERIFYING → READY
#:     READY → PAUSED | REAUTH_REQUIRED | QUARANTINED
SOCIAL_ACCOUNT_TRANSITIONS: Mapping[SocialAccountState, frozenset[SocialAccountState]] = {
    SocialAccountState.PROVISIONED: frozenset({SocialAccountState.VERIFYING}),
    SocialAccountState.VERIFYING: frozenset({SocialAccountState.READY}),
    SocialAccountState.READY: frozenset(
        {
            SocialAccountState.PAUSED,
            SocialAccountState.REAUTH_REQUIRED,
            SocialAccountState.QUARANTINED,
        }
    ),
    SocialAccountState.PAUSED: frozenset(),
    SocialAccountState.REAUTH_REQUIRED: frozenset(),
    SocialAccountState.QUARANTINED: frozenset(),
}

#: L2589-2591:
#:     CREATING → AVAILABLE → LEASED → AVAILABLE
#:     AVAILABLE/LEASED → QUARANTINED | CORRUPT | RETIRED
BROWSER_PROFILE_TRANSITIONS: Mapping[BrowserProfileState, frozenset[BrowserProfileState]] = {
    BrowserProfileState.CREATING: frozenset({BrowserProfileState.AVAILABLE}),
    BrowserProfileState.AVAILABLE: frozenset(
        {
            BrowserProfileState.LEASED,
            BrowserProfileState.QUARANTINED,
            BrowserProfileState.CORRUPT,
            BrowserProfileState.RETIRED,
        }
    ),
    BrowserProfileState.LEASED: frozenset(
        {
            BrowserProfileState.AVAILABLE,
            BrowserProfileState.QUARANTINED,
            BrowserProfileState.CORRUPT,
            BrowserProfileState.RETIRED,
        }
    ),
    BrowserProfileState.QUARANTINED: frozenset(),
    BrowserProfileState.CORRUPT: frozenset(),
    BrowserProfileState.RETIRED: frozenset(),
}

#: L2593-2595:
#:     STARTING → ACTIVE → STOPPING → CLOSED
#:     STARTING/ACTIVE/STOPPING → CRASHED | ORPHANED
BROWSER_SESSION_TRANSITIONS: Mapping[BrowserSessionState, frozenset[BrowserSessionState]] = {
    BrowserSessionState.STARTING: frozenset(
        {
            BrowserSessionState.ACTIVE,
            BrowserSessionState.CRASHED,
            BrowserSessionState.ORPHANED,
        }
    ),
    BrowserSessionState.ACTIVE: frozenset(
        {
            BrowserSessionState.STOPPING,
            BrowserSessionState.CRASHED,
            BrowserSessionState.ORPHANED,
        }
    ),
    BrowserSessionState.STOPPING: frozenset(
        {
            BrowserSessionState.CLOSED,
            BrowserSessionState.CRASHED,
            BrowserSessionState.ORPHANED,
        }
    ),
    BrowserSessionState.CLOSED: frozenset(),
    BrowserSessionState.CRASHED: frozenset(),
    BrowserSessionState.ORPHANED: frozenset(),
}

#: L2597-2599:
#:     REQUESTED → SAFE_STOPPING → CONTROL_GRANTED → RELEASED
#:     REQUESTED/SAFE_STOPPING/CONTROL_GRANTED → CANCELLED | EXPIRED | RECONCILIATION_REQUIRED
HUMAN_TAKEOVER_TRANSITIONS: Mapping[HumanTakeoverState, frozenset[HumanTakeoverState]] = {
    HumanTakeoverState.REQUESTED: frozenset(
        {
            HumanTakeoverState.SAFE_STOPPING,
            HumanTakeoverState.CANCELLED,
            HumanTakeoverState.EXPIRED,
            HumanTakeoverState.RECONCILIATION_REQUIRED,
        }
    ),
    HumanTakeoverState.SAFE_STOPPING: frozenset(
        {
            HumanTakeoverState.CONTROL_GRANTED,
            HumanTakeoverState.CANCELLED,
            HumanTakeoverState.EXPIRED,
            HumanTakeoverState.RECONCILIATION_REQUIRED,
        }
    ),
    HumanTakeoverState.CONTROL_GRANTED: frozenset(
        {
            HumanTakeoverState.RELEASED,
            HumanTakeoverState.CANCELLED,
            HumanTakeoverState.EXPIRED,
            HumanTakeoverState.RECONCILIATION_REQUIRED,
        }
    ),
    HumanTakeoverState.RELEASED: frozenset(),
    HumanTakeoverState.CANCELLED: frozenset(),
    HumanTakeoverState.EXPIRED: frozenset(),
    HumanTakeoverState.RECONCILIATION_REQUIRED: frozenset(),
}

#: L2601-2605:
#:     PREPARED → DISPATCHED → SUBMIT_STARTED
#:     SUBMIT_STARTED → CONFIRMED | UNKNOWN
#:     PREPARED/DISPATCHED → FAILED_BEFORE_SIDE_EFFECT
#:     PREPARED/DISPATCHED → CAPABILITY_BLOCKED | IDENTITY_BLOCKED
#:
#: Note what is NOT here: nothing leads back to PREPARED or DISPATCHED. That is the same rule
#: `STATES_FORBIDDING_AUTOMATIC_RETRY` states in the enums — once the submit has started, the
#: side effect may already exist on the platform side, so a new attempt is a second publication
#: (L2673, L4560, L5335).
EXECUTION_ATTEMPT_TRANSITIONS: Mapping[ExecutionAttemptState, frozenset[ExecutionAttemptState]] = {
    ExecutionAttemptState.PREPARED: frozenset(
        {
            ExecutionAttemptState.DISPATCHED,
            ExecutionAttemptState.FAILED_BEFORE_SIDE_EFFECT,
            ExecutionAttemptState.CAPABILITY_BLOCKED,
            ExecutionAttemptState.IDENTITY_BLOCKED,
        }
    ),
    ExecutionAttemptState.DISPATCHED: frozenset(
        {
            ExecutionAttemptState.SUBMIT_STARTED,
            ExecutionAttemptState.FAILED_BEFORE_SIDE_EFFECT,
            ExecutionAttemptState.CAPABILITY_BLOCKED,
            ExecutionAttemptState.IDENTITY_BLOCKED,
        }
    ),
    ExecutionAttemptState.SUBMIT_STARTED: frozenset(
        {ExecutionAttemptState.CONFIRMED, ExecutionAttemptState.UNKNOWN}
    ),
    ExecutionAttemptState.CONFIRMED: frozenset(),
    ExecutionAttemptState.UNKNOWN: frozenset(),
    ExecutionAttemptState.FAILED_BEFORE_SIDE_EFFECT: frozenset(),
    ExecutionAttemptState.CAPABILITY_BLOCKED: frozenset(),
    ExecutionAttemptState.IDENTITY_BLOCKED: frozenset(),
}


# ---------------------------------------------------------------------------
# Section 16.1 — the ActionProposal machine (L2773-2775)
# ---------------------------------------------------------------------------

#: L2773-2775:
#:     DRAFT → READY_FOR_APPROVAL → APPROVED | REJECTED | EXPIRED
#:     APPROVED → SUPERSEDED, jeżeli powstanie nowa rewizja
#:
#: `APPROVED → SUPERSEDED` is the only source of `SUPERSEDED` the document draws, and
#: `ActionProposalRevision.supersede` asks this table rather than keeping its own copy of
#: the rule.
ACTION_PROPOSAL_TRANSITIONS: Mapping[ActionProposalState, frozenset[ActionProposalState]] = {
    ActionProposalState.DRAFT: frozenset({ActionProposalState.READY_FOR_APPROVAL}),
    ActionProposalState.READY_FOR_APPROVAL: frozenset(
        {
            ActionProposalState.APPROVED,
            ActionProposalState.REJECTED,
            ActionProposalState.EXPIRED,
        }
    ),
    ActionProposalState.APPROVED: frozenset({ActionProposalState.SUPERSEDED}),
    ActionProposalState.REJECTED: frozenset(),
    ActionProposalState.EXPIRED: frozenset(),
    ActionProposalState.SUPERSEDED: frozenset(),
}


#: All six machines, addressable by name. The names are the ones the document uses for the
#: entities, not the enum class names.
TRANSITION_TABLES: Mapping[str, Mapping[StrEnum, frozenset[StrEnum]]] = {
    "SocialAccount": SOCIAL_ACCOUNT_TRANSITIONS,
    "BrowserProfile": BROWSER_PROFILE_TRANSITIONS,
    "BrowserSession": BROWSER_SESSION_TRANSITIONS,
    "HumanTakeover": HUMAN_TAKEOVER_TRANSITIONS,
    "ExecutionAttempt": EXECUTION_ATTEMPT_TRANSITIONS,
    "ActionProposal": ACTION_PROPOSAL_TRANSITIONS,
}

#: Which machine a state belongs to. Lets the caller pass two states and nothing else — a caller
#: choosing the table by hand could check a transition against the wrong machine and be told it
#: is fine.
_MACHINE_BY_STATE_TYPE: Mapping[type[StrEnum], str] = {
    SocialAccountState: "SocialAccount",
    BrowserProfileState: "BrowserProfile",
    BrowserSessionState: "BrowserSession",
    HumanTakeoverState: "HumanTakeover",
    ExecutionAttemptState: "ExecutionAttempt",
    ActionProposalState: "ActionProposal",
}


def machine_of(state: StrEnum) -> str:
    """The name of the machine this state belongs to, or `ValueError` for a foreign enum."""
    try:
        return _MACHINE_BY_STATE_TYPE[type(state)]
    except KeyError:
        raise ValueError(
            f"{type(state).__name__} nie jest maszyną stanów z sekcji 14.8 ani 16.1"
        ) from None


def is_allowed_transition(source: StrEnum, target: StrEnum) -> bool:
    """Whether the table allows `source → target`. False also for two different machines."""
    if type(source) is not type(target):
        return False
    table = TRANSITION_TABLES[machine_of(source)]
    return target in table[source]


def require_transition(source: StrEnum, target: StrEnum) -> None:
    """Lets a legal transition through, raises `IllegalTransition` for anything else.

    Deliberately returns nothing: the caller applies the new state itself. This function is the
    guard, not the mutator — the models it guards are frozen and each one builds its own
    successor.
    """
    if type(source) is not type(target):
        raise IllegalTransition(
            machine=f"{type(source).__name__}/{type(target).__name__}",
            source=source,
            target=target,
            allowed=frozenset(),
        )
    machine = machine_of(source)
    allowed = TRANSITION_TABLES[machine][source]
    if target not in allowed:
        raise IllegalTransition(machine=machine, source=source, target=target, allowed=allowed)
