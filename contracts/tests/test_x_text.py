"""X's length rules, spelled once (11.10.2026).

The control plane (`draft.create`) and the X actuator (`browser_supervisor.x_writer`) used to count
a post with two different functions. A 275-character post ending in `getwenext.com` was a draft of
275 (a bare domain was not a link there) and 285 at the actuator (a bare domain weighs 23), which
refused it AFTER the founder had approved it. Both sides now import `community_os_contracts.x_text`;
this file pins the rule and the vectors the services' own tests repeat.
"""

from __future__ import annotations

import pytest

from community_os_contracts.x_text import (
    X_MAX_WEIGHT,
    X_URL_WEIGHT,
    x_length_problem,
    x_weighted_length,
)

#: The text of the founder's post that failed on 11.10.2026, character for character.
FOUNDER_POST = (
    "I'm good at building things. Getting people to use them, not so much.\n\n"
    'So I\'m building Wenext: type "find my first users" in the Codex chat where you built your '
    "app. It finds people on Reddit who need it and drafts your reply.\n\n"
    "Nothing posts without your click. getwenext.com"
)


def test_the_founders_post_is_275_by_length_and_285_as_x_counts_it() -> None:
    assert len(FOUNDER_POST) == 275
    # The whole difference is the bare domain: 13 characters that X counts as a 23-unit link.
    assert x_weighted_length(FOUNDER_POST) == 275 - len("getwenext.com") + X_URL_WEIGHT == 285
    assert x_length_problem(FOUNDER_POST) == ("weighted", 285)


def test_the_same_post_without_the_domain_fits() -> None:
    shorter = FOUNDER_POST.removesuffix(" getwenext.com")
    assert x_weighted_length(shorter) == len(shorter) == 261
    assert x_length_problem(shorter) is None


@pytest.mark.parametrize(
    ("text", "weighted"),
    [
        ("", 0),
        ("hello", 5),
        # a link weighs 23 whatever its length, with the protocol or without it
        ("getwenext.com", 23),
        ("x.co", 23),
        ("www.getwenext.com", 23),
        ("see getwenext.com now", 4 + 23 + 4),
        ("https://example.com/" + "p" * 200, 23),
        ("http://example.com:8080/a?b=c#d", 23),
        # what closes a sentence after a link is not the link
        ("https://getwenext.com/page.", 23 + 1),
        ("(see getwenext.com/x).", 5 + 23 + 2),
        ("see getwenext.com.", 4 + 23 + 1),
        ('"getwenext.com/x"', 1 + 23 + 1),
        ("getwenext.com/x" + chr(0x2026), 23 + 2),
        # an internationalised domain is a link for X too
        ("żółć.pl", 23),
        # Latin, Polish letters, digits, punctuation and the line break weigh 1
        ("żółć", 4),
        ("a\nb", 3),
        ("\n\n", 2),
        ('"find my first users"', 21),
        ("e + ́", 5),
        # CJK and emoji weigh 2
        ("日本", 4),
        ("\U0001f600", 2),
        ("a\U0001f600b", 4),
        # numbers, abbreviations and versions are not links
        ("e.g. 3.14 is fine", 17),
        ("v1.2.3", 6),
        ("10.10.2026", 10),
        ("U.S.A", 5),
        # a deliberate over-estimate: this looks like a domain, and an over-count costs one edit
        ("Next.js", 23),
    ],
)
def test_the_weight_follows_x(text: str, weighted: int) -> None:
    assert x_weighted_length(text) == weighted


def test_the_decision_is_shared_with_its_number() -> None:
    assert x_length_problem("a" * X_MAX_WEIGHT) is None
    assert x_length_problem("a" * (X_MAX_WEIGHT + 1)) == ("plain", 281)
    # 141 wide characters are 141 by length and 282 as X counts them.
    assert x_length_problem("日" * 141) == ("weighted", 282)
    assert x_length_problem("日" * 140) is None
    # The plain bound stays (the actuator's own safety against a long string taken for one link),
    # so a long real address is refused by length although X would weigh it 23. Named here so a
    # change of that rule is a decision and not a surprise.
    assert x_length_problem("https://example.com/" + "p" * 270) == ("plain", 290)


def test_a_text_that_x_would_refuse_is_never_counted_shorter_than_its_weight() -> None:
    """The invariant behind `draft.create`: whatever sits in `LIGHT_RANGES` weighs 1 and
    everything else 2, so the weight of a text with no link equals the sum of its code points'."""
    text = "Zażółć gęślą jaźń, 日本語 \U0001f680!"
    per_character = sum(1 if ord(c) <= 4351 else 2 for c in text)
    assert x_weighted_length(text) == per_character
