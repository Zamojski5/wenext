"""`JudgmentRecord` — the rules `docs/KIERUNEK-PRODUKTU.md` §3, §4, §8, §9 put on a decision.

Each test names the sentence of the direction it defends. Negative tests come first: a record
type whose validators were never seen refusing anything is a record type with no validators.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from community_os_contracts import (
    MIN_CONTRIBUTION_CHARS,
    MIN_REASONING_CHARS,
    JudgmentDecision,
    JudgmentFeedback,
    JudgmentFeedbackVerdict,
    JudgmentRecord,
    ObservationPlatform,
    agreement,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SHA = "a" * 64
REASONING = "The author asks how others run MCP in production; the user ran one for a year."
CONTRIBUTION = "Concrete numbers from running an MCP gateway in production for twelve months."


def record(**overrides) -> JudgmentRecord:
    base = {
        "thread_ref": "HACKER_NEWS:ask:49548600",
        "decision": JudgmentDecision.IGNORE,
        "reasoning": REASONING,
        "confidence": 0.6,
        "user_profile_version": 1,
        "prompt_name": "judge_thread",
        "prompt_sha256": SHA,
        "model": "gpt-5.6-luna",
        "effort": "low",
        "created_at": NOW,
    }
    base.update(overrides)
    return JudgmentRecord(**base)


def test_the_three_decisions_are_exactly_engage_ignore_wait() -> None:
    """§3: 'Najważniejszą decyzją jest ENGAGE / IGNORE / WAIT' — and nothing else."""
    assert {member.value for member in JudgmentDecision} == {"ENGAGE", "IGNORE", "WAIT"}


def test_an_ignore_is_a_full_record_with_reasoning() -> None:
    """§9: negative data. `IGNORE` is stored with the same completeness as `ENGAGE`."""
    judgment = record(decision=JudgmentDecision.IGNORE)
    assert judgment.is_negative
    assert len(judgment.reasoning) >= MIN_REASONING_CHARS


@pytest.mark.parametrize("decision", list(JudgmentDecision))
def test_a_decision_without_reasoning_is_refused(decision: JudgmentDecision) -> None:
    """§8: a decision that cannot say why teaches nothing. All three decisions, one by one."""
    contribution = CONTRIBUTION if decision is JudgmentDecision.ENGAGE else None
    with pytest.raises(ValidationError, match="uzasadnienia"):
        record(decision=decision, reasoning="   short   ", what_user_could_add=contribution)


def test_engage_without_a_concrete_contribution_is_refused() -> None:
    """§4: 'To, że model potrafi coś napisać, nie jest powodem, żeby cokolwiek publikować.'"""
    with pytest.raises(ValidationError, match="what_user_could_add"):
        record(decision=JudgmentDecision.ENGAGE)
    with pytest.raises(ValidationError, match="what_user_could_add"):
        record(decision=JudgmentDecision.ENGAGE, what_user_could_add="yes")
    assert len("yes") < MIN_CONTRIBUTION_CHARS


def test_engage_with_a_contribution_is_accepted_and_wait_may_carry_one_too() -> None:
    engage = record(decision=JudgmentDecision.ENGAGE, what_user_could_add=CONTRIBUTION)
    assert engage.what_user_could_add == CONTRIBUTION
    wait = record(decision=JudgmentDecision.WAIT, what_user_could_add=CONTRIBUTION)
    assert not wait.is_negative


def test_the_prompt_hash_must_be_a_full_digest() -> None:
    """A prefix makes two different prompts look identical — the same rule as the payload hash."""
    with pytest.raises(ValidationError, match="SHA-256"):
        record(prompt_sha256=SHA[:12])
    with pytest.raises(ValidationError, match="SHA-256"):
        record(prompt_sha256="G" * 64)


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValidationError, match="strefę"):
        record(created_at=datetime(2026, 9, 3, 12, 0))  # noqa: DTZ001 — naive on purpose
    with pytest.raises(ValidationError, match="strefę"):
        JudgmentFeedback(verdict=JudgmentFeedbackVerdict.AGREE, created_at=datetime(2026, 9, 3))  # noqa: DTZ001


def test_the_record_is_frozen_and_closed() -> None:
    judgment = record()
    with pytest.raises(ValidationError):
        judgment.decision = JudgmentDecision.ENGAGE  # type: ignore[misc]
    with pytest.raises(ValidationError):
        record(score=0.9)  # no `score` anywhere — the same rule as ThreadQualityAssessment


@pytest.mark.parametrize(
    ("decision", "verdict", "expected"),
    [
        (JudgmentDecision.ENGAGE, JudgmentFeedbackVerdict.AGREE, True),
        (JudgmentDecision.ENGAGE, JudgmentFeedbackVerdict.DISAGREE, False),
        (JudgmentDecision.ENGAGE, JudgmentFeedbackVerdict.IRRELEVANT, False),
        (JudgmentDecision.IGNORE, JudgmentFeedbackVerdict.AGREE, True),
        (JudgmentDecision.IGNORE, JudgmentFeedbackVerdict.DISAGREE, False),
        (JudgmentDecision.IGNORE, JudgmentFeedbackVerdict.IRRELEVANT, None),
        (JudgmentDecision.WAIT, JudgmentFeedbackVerdict.IRRELEVANT, False),
    ],
)
def test_agreement_table_is_written_out_by_hand(
    decision: JudgmentDecision, verdict: JudgmentFeedbackVerdict, expected: bool | None
) -> None:
    """`IRRELEVANT` on an `IGNORE` measures nothing about Outcome B and comes back as `None`.

    The table is hand-written so that the test cannot agree with the implementation by
    construction — the same reason `RETRY_EXPECTATIONS` in the control plane is a literal.
    """
    assert len(JudgmentDecision) * len(JudgmentFeedbackVerdict) == 9
    assert agreement(decision, verdict) is expected


def test_observation_platforms_are_the_adapters_that_exist() -> None:
    """One value per adapter in `services/community_observer/`, no registry."""
    assert {member.value for member in ObservationPlatform} == {"HACKER_NEWS", "REDDIT"}
