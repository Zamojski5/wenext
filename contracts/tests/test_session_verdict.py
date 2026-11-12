"""The one definition of "is the browser logged in" (13.10.2026).

What is held here is the RULE, with no browser and no platform: seeing the account is the only way to
be logged in, a login wall is the only way to be logged out, everything else (above all a timeout) is
unknown, and when a reader and a writer have both looked, the youngest definitive look wins and an
unknown never overrides one.
"""

from __future__ import annotations

import pytest

from community_os_contracts.session_verdict import (
    RETRYABLE_FAILURES,
    WRITER_PROOF_MAX_AGE_S,
    Observation,
    ReadFailure,
    SessionVerdict,
    is_retryable,
    resolve,
    verdict_of,
    verdict_of_failure,
)

IN, OUT, UNKNOWN = SessionVerdict.LOGGED_IN, SessionVerdict.LOGGED_OUT, SessionVerdict.UNKNOWN


# -- the verdict of one look -------------------------------------------------------------------


def test_the_account_seen_is_logged_in_a_wall_is_logged_out_and_nothing_is_unknown() -> None:
    assert verdict_of(account_seen=True, logged_out_proof=False) is IN
    assert verdict_of(account_seen=False, logged_out_proof=True) is OUT
    assert verdict_of(account_seen=False, logged_out_proof=False) is UNKNOWN


def test_seeing_the_account_beats_a_wall_seen_in_the_same_look() -> None:
    """A page cannot honestly be both; if it says so, the account that was SEEN is the evidence."""
    assert verdict_of(account_seen=True, logged_out_proof=True) is IN


def test_only_a_definitive_verdict_is_definitive() -> None:
    assert IN.definitive and OUT.definitive and not UNKNOWN.definitive


# -- the timeout classification ----------------------------------------------------------------


@pytest.mark.parametrize("failure", list(ReadFailure))
def test_a_failed_look_is_never_logged_out(failure: ReadFailure) -> None:
    """THE RULE OF THE INCIDENT: a timeout, an unreachable capsule, an unreadable page, a platform
    refusal and a rejected request all establish nothing about the session."""
    assert verdict_of_failure(failure) is UNKNOWN
    assert verdict_of_failure(failure.value) is UNKNOWN
    assert verdict_of_failure(None) is UNKNOWN


def test_what_is_worth_a_second_look_and_what_is_not() -> None:
    for slow in (ReadFailure.TIMEOUT, ReadFailure.UNREACHABLE, ReadFailure.UNREADABLE):
        assert is_retryable(slow) and is_retryable(slow.value)
    # A platform refusal is a result to wait on, a rejected request is our configuration.
    assert not is_retryable(ReadFailure.PLATFORM_REFUSED)
    assert not is_retryable(ReadFailure.REJECTED)
    assert not is_retryable(None) and not is_retryable("nonsense")
    assert RETRYABLE_FAILURES == {
        ReadFailure.TIMEOUT,
        ReadFailure.UNREACHABLE,
        ReadFailure.UNREADABLE,
    }


# -- the agreement rule ------------------------------------------------------------------------


def test_a_reader_that_timed_out_does_not_contradict_a_writer_that_published() -> None:
    """The incident in one line: the reader said nothing, the actuator published 2 minutes ago."""
    chosen = resolve(
        [
            Observation(UNKNOWN, 0.0, source="reader"),
            Observation(IN, 120.0, "demo_founder", "writer"),
        ],
        max_age_s=WRITER_PROOF_MAX_AGE_S,
    )
    assert chosen is not None and chosen.verdict is IN and chosen.source == "writer"
    assert chosen.handle == "demo_founder"


def test_the_youngest_definitive_look_wins_whoever_made_it() -> None:
    wall_now = Observation(OUT, 1.0, source="reader")
    published_earlier = Observation(IN, 300.0, "a", "writer")
    assert resolve([wall_now, published_earlier], max_age_s=900) is wall_now
    assert resolve([published_earlier, wall_now], max_age_s=900) is wall_now, "order is no vote"
    account_now = Observation(IN, 2.0, "a", "reader")
    refused_earlier = Observation(OUT, 60.0, source="writer")
    assert resolve([refused_earlier, account_now], max_age_s=900) is account_now


def test_an_unknown_never_overrides_anything_and_alone_is_nothing() -> None:
    assert resolve([Observation(UNKNOWN, 0.0)], max_age_s=900) is None
    assert resolve([], max_age_s=900) is None
    older = Observation(IN, 600.0)
    assert resolve([Observation(UNKNOWN, 0.0), older], max_age_s=900) is older


def test_evidence_older_than_its_time_is_not_evidence() -> None:
    """The writer's proof is believed for `WRITER_PROOF_MAX_AGE_S` and no longer: a session lost
    since then must be noticed by the next real read."""
    assert resolve([Observation(IN, WRITER_PROOF_MAX_AGE_S)], max_age_s=WRITER_PROOF_MAX_AGE_S)
    assert (
        resolve([Observation(IN, WRITER_PROOF_MAX_AGE_S + 1)], max_age_s=WRITER_PROOF_MAX_AGE_S)
        is None
    )
    assert resolve([Observation(IN, -1.0)], max_age_s=900) is None, (
        "a look from the future is noise"
    )


def test_equal_ages_go_to_the_first_listed() -> None:
    reader = Observation(IN, 5.0, "a", "reader")
    writer = Observation(OUT, 5.0, source="writer")
    assert resolve([reader, writer], max_age_s=900) is reader


def test_the_writers_proof_is_believed_for_a_quarter_of_an_hour() -> None:
    assert WRITER_PROOF_MAX_AGE_S == 900.0


def test_the_module_has_no_long_dashes() -> None:
    """The repository forbids U+2013 and U+2014 in everything a person or a model can read."""
    from pathlib import Path

    import community_os_contracts.session_verdict as module

    text = Path(module.__file__).read_text(encoding="utf-8")
    assert chr(0x2013) not in text and chr(0x2014) not in text
