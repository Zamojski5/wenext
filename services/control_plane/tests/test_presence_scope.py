"""What a delegation covers, decided without a database (P4).

WHY THESE ARE PURE TESTS. The scope is the sentence a person signed, and every question about it
("may the agent reply on this account in this community") has to be answerable from that sentence
alone, with no lookup and no state. A scope that needed a query to be understood could not be
shown to the person who signs it.

THE FOUR PROPERTIES, each the opposite of a way a signature could quietly widen:

  1. an ABSENT list is refused, not defaulted. "No accounts" and "the key is missing" are the
     same permission, and only one of them was written on purpose;
  2. an EMPTY list permits nothing. The other reading publishes from an identity nobody chose;
  3. RESEARCH mode beats the scope. A person who set the coarse switch to "read only" meant it,
     whatever the fine list still says;
  4. the digest covers the mode, the scope AND the budget together, so none of the three can
     move under a signature that still looks valid.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.presence.scope import (
    ACTION_OWN_POST,
    ACTION_REPLY,
    ACTION_RESEARCH,
    MODE_AUTONOMOUS,
    MODE_RESEARCH,
    MODE_REVIEW,
    Budget,
    ScopeError,
    covers,
    digest,
    parse_budget,
    parse_scope,
)

pytestmark = [pytest.mark.p4]

ACCOUNT = uuid4()
OTHER_ACCOUNT = uuid4()


def _scope(**overrides):
    payload = {
        "accounts": [str(ACCOUNT)],
        "communities": ["r/przyklad"],
        "actions": ["REPLY"],
        **overrides,
    }
    return parse_scope(payload)


@pytest.mark.parametrize("missing", ["accounts", "communities", "actions"])
def test_a_scope_missing_a_dimension_is_refused(missing) -> None:
    payload = {"accounts": [], "communities": [], "actions": []}
    del payload[missing]
    with pytest.raises(ScopeError) as caught:
        parse_scope(payload)
    assert missing in str(caught.value)


def test_an_empty_scope_permits_nothing() -> None:
    """The dangerous default, measured rather than assumed."""
    empty = parse_scope({"accounts": [], "communities": [], "actions": []})
    verdict = covers(
        mode=MODE_AUTONOMOUS,
        scope=empty,
        action=ACTION_REPLY,
        account_id=ACCOUNT,
        community_key="r/przyklad",
    )
    assert not verdict.covered
    assert verdict.reason


def test_the_scope_is_checked_on_all_three_dimensions() -> None:
    scope = _scope()
    assert covers(
        mode=MODE_AUTONOMOUS,
        scope=scope,
        action=ACTION_REPLY,
        account_id=ACCOUNT,
        community_key="r/przyklad",
    ).covered
    for label, kwargs in (
        ("działanie", {"action": ACTION_OWN_POST}),
        ("konto", {"account_id": OTHER_ACCOUNT}),
        ("społeczność", {"community_key": "r/inne"}),
    ):
        base = {
            "mode": MODE_AUTONOMOUS,
            "scope": scope,
            "action": ACTION_REPLY,
            "account_id": ACCOUNT,
            "community_key": "r/przyklad",
        }
        base.update(kwargs)
        assert not covers(**base).covered, label


@pytest.mark.parametrize("action", [ACTION_REPLY, ACTION_OWN_POST])
def test_research_mode_publishes_nothing_whatever_the_scope_lists(action) -> None:
    """The scope still lists REPLY and OWN_POST; the mode is what wins."""
    scope = _scope(actions=["REPLY", "OWN_POST", "RESEARCH"])
    verdict = covers(
        mode=MODE_RESEARCH,
        scope=scope,
        action=action,
        account_id=ACCOUNT,
        community_key="r/przyklad",
    )
    assert not verdict.covered
    assert "RESEARCH" in verdict.reason


def test_research_mode_still_covers_reading() -> None:
    scope = _scope(actions=["RESEARCH"])
    assert covers(
        mode=MODE_RESEARCH,
        scope=scope,
        action=ACTION_RESEARCH,
        account_id=ACCOUNT,
        community_key="r/przyklad",
    ).covered


def test_review_mode_does_not_widen_or_narrow_the_scope() -> None:
    """REVIEW is about WHO decides, not about what is permitted: the refusal for a REVIEW
    project comes from `approval.py`, and the scope answers the same as in AUTONOMOUS."""
    scope = _scope()
    assert covers(
        mode=MODE_REVIEW,
        scope=scope,
        action=ACTION_REPLY,
        account_id=ACCOUNT,
        community_key="r/przyklad",
    ).covered


def test_an_unknown_action_in_the_scope_is_refused() -> None:
    with pytest.raises(ScopeError):
        parse_scope({"accounts": [], "communities": [], "actions": ["WSZYSTKO"]})


def test_a_budget_key_nobody_enforces_is_refused() -> None:
    """A limit that is silently ignored reads exactly like no limit."""
    with pytest.raises(ScopeError) as caught:
        parse_budget({"publications_per_week": 3})
    assert "publications_per_week" in str(caught.value)


@pytest.mark.parametrize("value", [-1, "6", 1.5, True])
def test_a_budget_that_is_not_a_whole_count_is_refused(value) -> None:
    with pytest.raises(ScopeError):
        parse_budget({"publications_per_day": value})


def test_the_digest_covers_the_mode_the_scope_and_the_budget() -> None:
    scope = _scope()
    budget = parse_budget({"publications_per_day": 6})
    base = digest(mode=MODE_AUTONOMOUS, scope=scope, budget=budget)
    assert len(base) == 64
    assert base != digest(mode=MODE_REVIEW, scope=scope, budget=budget), "tryb nie wchodzi"
    assert base != digest(
        mode=MODE_AUTONOMOUS, scope=scope, budget=parse_budget({"publications_per_day": 7})
    ), "budżet nie wchodzi"
    assert base != digest(
        mode=MODE_AUTONOMOUS, scope=_scope(communities=["r/inne"]), budget=budget
    ), "zakres nie wchodzi"


def test_the_digest_does_not_depend_on_how_the_scope_was_written() -> None:
    """The same permission written twice, in a different order and with a repetition, is one
    signature. Otherwise re-saving an unchanged delegation would invalidate what it authorised."""
    one = parse_scope(
        {
            "accounts": [str(ACCOUNT), str(OTHER_ACCOUNT)],
            "communities": ["r/b", "r/a"],
            "actions": ["REPLY", "REPLY"],
        }
    )
    two = parse_scope(
        {
            "accounts": [str(OTHER_ACCOUNT), str(ACCOUNT)],
            "communities": ["r/a", "r/b"],
            "actions": ["reply"],
        }
    )
    assert digest(mode=MODE_AUTONOMOUS, scope=one, budget=Budget()) == digest(
        mode=MODE_AUTONOMOUS, scope=two, budget=Budget()
    )
