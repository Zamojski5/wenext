"""The length of a post as X counts it: ONE definition for the draft and for the actuator.

WHY IT LIVES HERE. Two copies used to exist and they disagreed (11.10.2026). `draft.create` (control
plane) detected only `https?://...` as a link, so `getwenext.com` weighed its 13 characters; the X
actuator (browser supervisor) detected a bare domain too and weighed it 23. A 275-character post
ended with that domain, was accepted as a draft, approved by the founder, and refused at the
actuator as 285 of 280. Both services already depend on this package (the capsule image and the
control plane image install it), so the rule is spelled once, here, and both import it.

THE RULES (twitter-text v3, the counter of x.com):

  * a link weighs `X_URL_WEIGHT` (23) whatever its length, and a BARE DOMAIN is a link (`x.co`,
    `getwenext.com`);
  * a code point in `LIGHT_RANGES` weighs 1 (Latin, Polish letters, digits, punctuation, the line
    break); every other one weighs 2 (CJK, most emoji);
  * the limit is `X_MAX_WEIGHT` (280), counted in those units, not in characters.

THE ESTIMATE ERRS UPWARDS, ON PURPOSE. A text counted as shorter than X counts it passes the draft
and fails at the composer; a text counted as longer costs the founder one edit. So the link
detector is wider than X's own (`URL_LIKE` accepts any `label.tld` with a two-letter-or-longer
alphabetic tail, so `Next.js` and `etc.So` count as links), an emoji sequence is counted per code
point (a family emoji weighs about 11 where X shows 2) and the text is not NFC-normalised. The
one place where a naive reading undercounts is the end of a link: the punctuation that closes a
sentence after it is not part of the link in X, so it is weighed as text here (`_link_end`).
"""

from __future__ import annotations

import re

#: X's limit for an account without a subscription, in the units below.
X_MAX_WEIGHT = 280
#: A link weighs this much whatever its length (t.co wrapping).
X_URL_WEIGHT = 23
#: Code point ranges (inclusive) X counts as ONE; everything else counts as two. From the platform's
#: published counting rules (twitter-text v3 config).
LIGHT_RANGES: tuple[tuple[int, int], ...] = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))

#: Anything that looks like a link or a bare domain, deliberately wider than X's own detector (see
#: the module docstring). Labels may be Unicode letters or digits (an internationalised domain is a
#: link for X), the tail is alphabetic.
URL_LIKE = re.compile(
    r"(?:https?://)?(?:[^\W_](?:[\w-]{0,61}[^\W_])?\.)+[A-Za-z]{2,63}"
    r"(?::\d{1,5})?(?:/\S*)?"
)

#: What X leaves OUTSIDE a link when it closes the link's last word: sentence punctuation, closing
#: brackets and quotes, the ellipsis.
_TRAILING = frozenset(".,;:!?)]}>'\"" + chr(0x2026) + chr(0x2019) + chr(0x201D) + chr(0x00BB))


def x_weighted_length(text: str) -> int:
    """The length X's counter shows for `text`, or more, never less (see `URL_LIKE`)."""
    total = 0
    position = 0
    for match in URL_LIKE.finditer(text):
        total += _weight(text[position : match.start()]) + X_URL_WEIGHT
        position = _link_end(text, match)
    return total + _weight(text[position:])


def x_length_problem(text: str) -> tuple[str, int] | None:
    """`None` when X takes `text`; otherwise `("plain" | "weighted", the number that is too big)`.

    THE DECISION, not only the number, is shared: the actuator refuses a text over the limit by
    plain length (a bound on whatever the weighted estimate gets wrong, for instance a long
    string it took for one link) and by weighted length. `draft.create` asks the same function, so a
    draft is refused with the number the actuator would compute and never after the approval.
    """
    plain = len(text)
    if plain > X_MAX_WEIGHT:
        return "plain", plain
    weighted = x_weighted_length(text)
    if weighted > X_MAX_WEIGHT:
        return "weighted", weighted
    return None


def _link_end(text: str, match: re.Match[str]) -> int:
    """Where the link ends for X: the match without the punctuation that closes a sentence."""
    end = match.end()
    # Only a path can swallow trailing punctuation (`/\S*`); a bare domain ends on a letter.
    while end > match.start() and text[end - 1] in _TRAILING:
        end -= 1
    return end


def _weight(chars: str) -> int:
    return sum(1 if any(low <= ord(c) <= high for low, high in LIGHT_RANGES) else 2 for c in chars)
