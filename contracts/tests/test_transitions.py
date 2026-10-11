"""Transition tables checked against the document, in both directions.

The tables in `transitions.py` are a transcription of section 14.8 (L2582-2606) and of the
`ActionProposal` machine from section 16.1 (L2773-2775). The literal pairs below are typed out
from the document a second time, ON PURPOSE and BY HAND, as plain strings — so that this file
does not import the same knowledge it is supposed to verify. A table copied from the code it
tests proves nothing.

Every comparison runs in BOTH directions: a surplus transition is as much of a defect as a
missing one. A surplus one is worse, because it is a path the document never allowed.
"""

from __future__ import annotations

import itertools

import pytest

from community_os_contracts import (
    STATES_FORBIDDING_AUTOMATIC_RETRY,
    TRANSITION_TABLES,
    ActionProposalState,
    BrowserProfileState,
    BrowserSessionState,
    ExecutionAttemptState,
    HumanTakeoverState,
    IllegalTransition,
    SocialAccountState,
    is_allowed_transition,
    require_transition,
)
from community_os_contracts.transitions import EXECUTION_ATTEMPT_TRANSITIONS

#: The state type of each machine. Needed for the cartesian product: the set of PAIRS is taken
#: from the code under test, but the set of STATES has to come from the enums, otherwise a table
#: missing a whole state would also shrink the product and hide itself.
STATE_TYPES = {
    "SocialAccount": SocialAccountState,
    "BrowserProfile": BrowserProfileState,
    "BrowserSession": BrowserSessionState,
    "HumanTakeover": HumanTakeoverState,
    "ExecutionAttempt": ExecutionAttemptState,
    "ActionProposal": ActionProposalState,
}

#: Transcribed by hand from the document. Strings, not enum members, so that a renamed enum
#: value cannot make this file agree with the code while both drift from the roadmap.
#:
#: SocialAccount   L2585-2587
#: BrowserProfile  L2589-2591
#: BrowserSession  L2593-2595
#: HumanTakeover   L2597-2599
#: ExecutionAttempt L2601-2605
#: ActionProposal  L2773-2775
DOCUMENT_PAIRS: dict[str, frozenset[tuple[str, str]]] = {
    # PROVISIONED → VERIFYING → READY
    # READY → PAUSED | REAUTH_REQUIRED | QUARANTINED
    "SocialAccount": frozenset(
        {
            ("PROVISIONED", "VERIFYING"),
            ("VERIFYING", "READY"),
            ("READY", "PAUSED"),
            ("READY", "REAUTH_REQUIRED"),
            ("READY", "QUARANTINED"),
        }
    ),
    # CREATING → AVAILABLE → LEASED → AVAILABLE
    # AVAILABLE/LEASED → QUARANTINED | CORRUPT | RETIRED
    "BrowserProfile": frozenset(
        {
            ("CREATING", "AVAILABLE"),
            ("AVAILABLE", "LEASED"),
            ("LEASED", "AVAILABLE"),
            ("AVAILABLE", "QUARANTINED"),
            ("AVAILABLE", "CORRUPT"),
            ("AVAILABLE", "RETIRED"),
            ("LEASED", "QUARANTINED"),
            ("LEASED", "CORRUPT"),
            ("LEASED", "RETIRED"),
        }
    ),
    # STARTING → ACTIVE → STOPPING → CLOSED
    # STARTING/ACTIVE/STOPPING → CRASHED | ORPHANED
    "BrowserSession": frozenset(
        {
            ("STARTING", "ACTIVE"),
            ("ACTIVE", "STOPPING"),
            ("STOPPING", "CLOSED"),
            ("STARTING", "CRASHED"),
            ("STARTING", "ORPHANED"),
            ("ACTIVE", "CRASHED"),
            ("ACTIVE", "ORPHANED"),
            ("STOPPING", "CRASHED"),
            ("STOPPING", "ORPHANED"),
        }
    ),
    # REQUESTED → SAFE_STOPPING → CONTROL_GRANTED → RELEASED
    # REQUESTED/SAFE_STOPPING/CONTROL_GRANTED → CANCELLED | EXPIRED | RECONCILIATION_REQUIRED
    "HumanTakeover": frozenset(
        {
            ("REQUESTED", "SAFE_STOPPING"),
            ("SAFE_STOPPING", "CONTROL_GRANTED"),
            ("CONTROL_GRANTED", "RELEASED"),
            ("REQUESTED", "CANCELLED"),
            ("REQUESTED", "EXPIRED"),
            ("REQUESTED", "RECONCILIATION_REQUIRED"),
            ("SAFE_STOPPING", "CANCELLED"),
            ("SAFE_STOPPING", "EXPIRED"),
            ("SAFE_STOPPING", "RECONCILIATION_REQUIRED"),
            ("CONTROL_GRANTED", "CANCELLED"),
            ("CONTROL_GRANTED", "EXPIRED"),
            ("CONTROL_GRANTED", "RECONCILIATION_REQUIRED"),
        }
    ),
    # PREPARED → DISPATCHED → SUBMIT_STARTED
    # SUBMIT_STARTED → CONFIRMED | UNKNOWN
    # PREPARED/DISPATCHED → FAILED_BEFORE_SIDE_EFFECT
    # PREPARED/DISPATCHED → CAPABILITY_BLOCKED | IDENTITY_BLOCKED
    "ExecutionAttempt": frozenset(
        {
            ("PREPARED", "DISPATCHED"),
            ("DISPATCHED", "SUBMIT_STARTED"),
            ("SUBMIT_STARTED", "CONFIRMED"),
            ("SUBMIT_STARTED", "UNKNOWN"),
            ("PREPARED", "FAILED_BEFORE_SIDE_EFFECT"),
            ("DISPATCHED", "FAILED_BEFORE_SIDE_EFFECT"),
            ("PREPARED", "CAPABILITY_BLOCKED"),
            ("PREPARED", "IDENTITY_BLOCKED"),
            ("DISPATCHED", "CAPABILITY_BLOCKED"),
            ("DISPATCHED", "IDENTITY_BLOCKED"),
        }
    ),
    # DRAFT → READY_FOR_APPROVAL → APPROVED | REJECTED | EXPIRED
    # APPROVED → SUPERSEDED, jeżeli powstanie nowa rewizja
    "ActionProposal": frozenset(
        {
            ("DRAFT", "READY_FOR_APPROVAL"),
            ("READY_FOR_APPROVAL", "APPROVED"),
            ("READY_FOR_APPROVAL", "REJECTED"),
            ("READY_FOR_APPROVAL", "EXPIRED"),
            ("APPROVED", "SUPERSEDED"),
        }
    ),
}

#: Counted by hand from the document, machine by machine. Written out so that the cardinality
#: assertion has something to compare against that is NOT `len(DOCUMENT_PAIRS[...])` — a typo
#: dropping one line of the literal set above would otherwise agree with itself.
EXPECTED_PAIR_COUNT = {
    "SocialAccount": 5,
    "BrowserProfile": 9,
    "BrowserSession": 9,
    "HumanTakeover": 12,
    "ExecutionAttempt": 10,
    "ActionProposal": 5,
}

#: How many states each machine has, written out by hand from the enums. Needed because the
#: cartesian product is built from `STATE_TYPES` and a total derived from that same dict agrees
#: with itself: dropping a machine shrinks the product AND the expected number in lockstep.
#: That is not a hypothetical — the mutation "remove `ActionProposal` from STATE_TYPES" left the
#: suite green at 230 passed while 36 cases silently stopped existing.
EXPECTED_STATE_COUNT = {
    "SocialAccount": 6,
    "BrowserProfile": 6,
    "BrowserSession": 6,
    "HumanTakeover": 7,
    "ExecutionAttempt": 8,
    "ActionProposal": 6,
}

#: 6² + 6² + 6² + 7² + 8² + 6² = 257. A literal, on purpose. See EXPECTED_STATE_COUNT above.
EXPECTED_CASE_COUNT = 257

#: The six machines of sections 14.8 and 16.1, named once. Every mapping in this file and in
#: `transitions.py` has to have exactly these keys.
EXPECTED_MACHINES = frozenset(
    {
        "SocialAccount",
        "BrowserProfile",
        "BrowserSession",
        "HumanTakeover",
        "ExecutionAttempt",
        "ActionProposal",
    }
)


def _cartesian_cases() -> list[tuple[str, object, object, bool]]:
    """Every ordered pair of states of every machine, with the verdict read from the table."""
    cases: list[tuple[str, object, object, bool]] = []
    for machine, state_type in STATE_TYPES.items():
        table = TRANSITION_TABLES[machine]
        states = list(state_type)
        for source, target in itertools.product(states, states):
            # A KeyError here is a defect, not a test bug: EVERY state must be a key, also a
            # terminal one, otherwise `require_transition` from a terminal state explodes with
            # KeyError instead of refusing the transition.
            cases.append((machine, source, target, target in table[source]))
    return cases


CARTESIAN_CASES = _cartesian_cases()


def test_all_six_machines_are_covered_by_every_mapping() -> None:
    """The three mappings of this file and the table under test name the SAME six machines.

    Without this, a machine missing from `DOCUMENT_PAIRS` simply produces no parametrized case
    for `test_the_allowed_set_equals_the_document` — a test that never runs looks exactly like
    a test that passed. The same shape as `all([])`, which has already been sprung three times
    in this project.
    """
    assert len(EXPECTED_MACHINES) == 6
    assert set(TRANSITION_TABLES) == EXPECTED_MACHINES, (
        f"transitions.py opisuje maszyny {sorted(TRANSITION_TABLES)}"
    )
    assert set(DOCUMENT_PAIRS) == EXPECTED_MACHINES, (
        f"ręczna transkrypcja dokumentu obejmuje maszyny {sorted(DOCUMENT_PAIRS)}"
    )
    assert set(STATE_TYPES) == EXPECTED_MACHINES, (
        f"iloczyn kartezjański obejmuje maszyny {sorted(STATE_TYPES)}"
    )
    assert set(EXPECTED_PAIR_COUNT) == EXPECTED_MACHINES
    assert set(EXPECTED_STATE_COUNT) == EXPECTED_MACHINES

    # The number of states per machine is written out by hand as well: it is the base of the
    # cartesian count, and a base read from the same dict as the product proves nothing.
    for machine, state_type in STATE_TYPES.items():
        assert len(list(state_type)) == EXPECTED_STATE_COUNT[machine], (
            f"{machine}: enum ma {len(list(state_type))} stanów, spisano "
            f"{EXPECTED_STATE_COUNT[machine]}"
        )
    assert sum(EXPECTED_STATE_COUNT[m] ** 2 for m in EXPECTED_MACHINES) == EXPECTED_CASE_COUNT


@pytest.mark.parametrize("machine", sorted(DOCUMENT_PAIRS))
def test_the_allowed_set_equals_the_document(machine: str) -> None:
    """The table has exactly the pairs the document draws — no more, no fewer."""
    expected = DOCUMENT_PAIRS[machine]
    table = TRANSITION_TABLES[machine]
    actual = {
        (source.value, target.value) for source, targets in table.items() for target in targets
    }

    # Cardinality FIRST, separately from the predicate. An empty table compares equal to an
    # empty expectation and `all([])` is True — that trap has already been sprung three times
    # in this project.
    assert len(expected) == EXPECTED_PAIR_COUNT[machine], (
        f"{machine}: literalny zbiór z dokumentu ma {len(expected)} par, "
        f"a spisano {EXPECTED_PAIR_COUNT[machine]}"
    )
    assert len(actual) == EXPECTED_PAIR_COUNT[machine], (
        f"{machine}: tabela przejść ma {len(actual)} par, dokument {EXPECTED_PAIR_COUNT[machine]}"
    )

    surplus = actual - expected
    missing = expected - actual
    assert not surplus, f"{machine}: przejścia spoza dokumentu: {sorted(surplus)}"
    assert not missing, f"{machine}: przejścia z dokumentu nieobecne w tabeli: {sorted(missing)}"


@pytest.mark.parametrize(
    ("machine", "source", "target", "allowed"),
    CARTESIAN_CASES,
    ids=[f"{m}-{s.value}-{t.value}" for m, s, t, _ in CARTESIAN_CASES],
)
def test_every_pair_outside_the_table_is_rejected(machine, source, target, allowed) -> None:
    """The full cartesian product, not a sample: what is not in the table must be refused."""
    # A literal, never `sum(... for ... in STATE_TYPES)`: a total derived from the same dict the
    # product is built from shrinks together with it and stays true while cases disappear.
    assert len(CARTESIAN_CASES) == EXPECTED_CASE_COUNT, (
        f"wygenerowano {len(CARTESIAN_CASES)} przypadków, a suma n² po maszynach to "
        f"{EXPECTED_CASE_COUNT} — któraś maszyna wypadła z iloczynu"
    )

    assert is_allowed_transition(source, target) is allowed
    if allowed:
        require_transition(source, target)
    else:
        with pytest.raises(IllegalTransition):
            require_transition(source, target)


def test_the_table_agrees_with_states_forbidding_automatic_retry() -> None:
    """L2673, L5335: after SUBMIT_STARTED / CONFIRMED / UNKNOWN there is no automatic retry.

    An "automatic retry" is a transition BACK into a state from which the attempt runs again:
    PREPARED, DISPATCHED or SUBMIT_STARTED. If the table allowed one of those from a forbidding
    state, `may_retry_automatically` would be saying one thing and the machine another.
    """
    assert len(STATES_FORBIDDING_AUTOMATIC_RETRY) == 3, (
        "zbiór stanów zakazujących retry ma być trzyelementowy (L2673, L5335)"
    )
    retry_entry_states = {
        ExecutionAttemptState.PREPARED,
        ExecutionAttemptState.DISPATCHED,
        ExecutionAttemptState.SUBMIT_STARTED,
    }

    offenders: list[str] = []
    for state in STATES_FORBIDDING_AUTOMATIC_RETRY:
        assert state in EXECUTION_ATTEMPT_TRANSITIONS, (
            f"{state.value} nie jest kluczem tabeli ExecutionAttempt"
        )
        offenders += [
            f"{state.value} → {target.value}"
            for target in EXECUTION_ATTEMPT_TRANSITIONS[state]
            if target in retry_entry_states
        ]

    assert not offenders, (
        "tabela pozwala na automatyczne ponowienie ze stanu, który tego zabrania: "
        + ", ".join(sorted(offenders))
    )


def test_a_transition_between_two_different_machines_is_refused() -> None:
    """A state of one machine and a state of another are not a transition at all."""
    with pytest.raises(IllegalTransition):
        require_transition(SocialAccountState.READY, BrowserProfileState.LEASED)


def test_every_machine_has_a_non_empty_table() -> None:
    """Six machines, each with at least one pair. A table that is there but empty passes every
    `all()` check while forbidding everything — that is the shape of a false pass."""
    assert len(TRANSITION_TABLES) == 6
    for machine, table in TRANSITION_TABLES.items():
        pairs = sum(len(targets) for targets in table.values())
        assert pairs > 0, f"{machine}: tabela przejść jest pusta"
