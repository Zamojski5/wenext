"""Is the agent's browser logged in? ONE definition, for every reader and every writer (13.10.2026).

THE INCIDENT. On 12 and 13.10.2026 `x.read` answered "the browser has no session of @demo_founder" for
hours while the actuator published replies from that very account, and the card of the Reddit account
showed "connected" while the Reddit actuator refused with "the page does not show a logged-in
account". Four places answered the one question ("is the browser logged in?") with four different
rules: the X reader waited 2 s for the account link, the X actuator waited 10 s for the same link,
the control plane called any failed read "no session", and the Reddit actuator called a 5 s timeout
on a custom element "logged out". Each rule was defensible alone; together they made the product
disagree with itself, and a person was told to log in again for what was OUR slow read.

THE RULE, in three sentences:

  1. LOGGED_IN needs the ACCOUNT SEEN (a handle read from the page).
  2. LOGGED_OUT needs POSITIVE PROOF: the platform's own login wall. Not seeing the account is not
     proof of its absence: a slow page, a timeout, a changed navigation and a capsule that does not
     answer all look exactly like that.
  3. Everything else is UNKNOWN, which is never a prompt to sign in again, and a read that ended
     UNKNOWN for a transient reason is tried once more before anyone is told.

THE AGREEMENT. A reader and a writer that look at the same profile can both be right and still
differ in time. `resolve` is how the product picks one answer: the YOUNGEST DEFINITIVE observation
wins, an UNKNOWN carries no information and never overrides a definitive one. A reply the actuator
published two minutes ago is a definitive "logged in" (the actuator refuses without the account), so
a reader that merely timed out must not contradict it.

WHERE IT LIVES. Both the capsule (browser supervisor) and the control plane (through the observer)
already depend on this package, and `x_text` set the precedent: a rule that two services must apply
the same way is written once, here, and imported by both.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

#: How long a successful write is believed to say "the session is there". The actuator refuses to
#: write without the account on the page, so a published reply is proof at the moment of
#: publication; a quarter of an hour covers a card that is polled and a model that is mid-turn,
#: and is short enough that a session lost since then is noticed at the next real read.
WRITER_PROOF_MAX_AGE_S = 900.0


class SessionVerdict(StrEnum):
    """What one look at the browser established about its login."""

    LOGGED_IN = "LOGGED_IN"
    LOGGED_OUT = "LOGGED_OUT"
    UNKNOWN = "UNKNOWN"

    @property
    def definitive(self) -> bool:
        return self is not SessionVerdict.UNKNOWN


class ReadFailure(StrEnum):
    """Why a look at the browser did not establish anything. Every one of them is UNKNOWN."""

    #: The page or the capsule did not answer in time.
    TIMEOUT = "TIMEOUT"
    #: The capsule did not accept the connection, or failed without a word from the platform.
    UNREACHABLE = "UNREACHABLE"
    #: The page rendered, but the account was not on it (slow hydration, a changed navigation).
    UNREADABLE = "UNREADABLE"
    #: The platform refused the client (403, 429): stop now, do not repeat.
    PLATFORM_REFUSED = "PLATFORM_REFUSED"
    #: The capsule refused our request (token, address): our configuration, a retry changes nothing.
    REJECTED = "REJECTED"


#: The failures a second look can fix. A platform refusal is a RESULT to wait on, a rejected request
#: is a configuration error: repeating either one would be noise at best and harm at worst.
RETRYABLE_FAILURES = frozenset(
    {ReadFailure.TIMEOUT, ReadFailure.UNREACHABLE, ReadFailure.UNREADABLE}
)


def verdict_of(*, account_seen: bool, logged_out_proof: bool) -> SessionVerdict:
    """The verdict of one look. Seeing the account beats everything; a wall is the only proof of
    absence; the rest is unknown."""
    if account_seen:
        return SessionVerdict.LOGGED_IN
    if logged_out_proof:
        return SessionVerdict.LOGGED_OUT
    return SessionVerdict.UNKNOWN


def verdict_of_failure(failure: ReadFailure | str | None) -> SessionVerdict:
    """A look that failed. Always UNKNOWN: the function exists so that the rule has a name and a
    test, and so that no caller writes `LOGGED_OUT` for a timeout in a hurry."""
    return SessionVerdict.UNKNOWN


def is_retryable(failure: ReadFailure | str | None) -> bool:
    """Whether a second look is worth making before the product says anything."""
    try:
        return ReadFailure(failure) in RETRYABLE_FAILURES
    except ValueError:
        return False


@dataclass(frozen=True)
class Observation:
    """One look, from one observer, `age_s` seconds ago."""

    verdict: SessionVerdict
    age_s: float
    handle: str | None = None
    #: Who looked: `reader` (a page read), `writer` (an actuator that wrote).
    source: str = ""


def resolve(observations: Iterable[Observation], *, max_age_s: float) -> Observation | None:
    """The one observation the product acts on, or `None` when nothing definitive is fresh enough.

    The youngest DEFINITIVE observation within `max_age_s` wins. An UNKNOWN is skipped, whoever made
    it: it says that someone could not tell, which is no evidence either way. Ties go to the order
    given, so a caller lists the reader first.
    """
    best: Observation | None = None
    for one in observations:
        if not one.verdict.definitive or one.age_s < 0 or one.age_s > max_age_s:
            continue
        if best is None or one.age_s < best.age_s:
            best = one
    return best


__all__ = [
    "RETRYABLE_FAILURES",
    "WRITER_PROOF_MAX_AGE_S",
    "Observation",
    "ReadFailure",
    "SessionVerdict",
    "is_retryable",
    "resolve",
    "verdict_of",
    "verdict_of_failure",
]
