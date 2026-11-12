"""A quote on X is an own post whose text ends with a status address: how X counts it, and what the draft path says (v11 round 3).

THE FOUNDER (13.10): the trend pass makes own posts that end with `https://x.com/<handle>/status/<id>`, which X turns into a
quote card. The publisher posts the text including the address, so nothing in the actuator should need to change. What is
pinned here is the SHARED counter (`community_os_contracts.x_text`, imported by the draft path and by the actuator):

  * the address weighs 23 whatever its length, like any link, so a long address does not eat the budget of the words;
  * BUT the plain-length bound of the counter (`len(text) > 280` is refused as `plain` whatever it weighs) still counts the
    address at its full length, so the words of a quote have room for about 230 characters (227 with the address below), not the
    256 that X itself would take.
    That is the safe direction (a draft is never accepted and then refused at the composer) and is stated here so a change of it
    is a decision (the contract package is rebuilt into the capsule image);
  * the card's editor counts it the same way (`xCount` is a port of the same function).

`browser_supervisor.x_writer.check_text` calls `x_length_problem` unchanged and types the text as it is (it strips nothing); that
module is not importable from this package's environment, so it was checked by hand against the same function on 13.10.
"""

from __future__ import annotations

import pytest
from community_os_contracts.x_text import (
    X_MAX_WEIGHT,
    X_URL_WEIGHT,
    x_length_problem,
    x_weighted_length,
)
from control_plane.opportunities.snapshot import trailing_status

HANDLE = "indie_maker"
STATUS = "2103587050346250341"
URL = f"https://x.com/{HANDLE}/status/{STATUS}"


@pytest.mark.parametrize(
    "address",
    [
        URL,
        f"https://twitter.com/{HANDLE}/status/{STATUS}",
        f"https://www.x.com/{HANDLE}/status/{STATUS}?s=20&t=abcdefghijklmnop",
        "https://x.com/a/status/1",
    ],
)
def test_a_status_address_weighs_twenty_three_however_long_it_is(address: str) -> None:
    assert x_weighted_length(address) == X_URL_WEIGHT == 23
    words = "This is the take most people miss: distribution beats features."
    assert x_weighted_length(f"{words} {address}") == x_weighted_length(words) + 1 + 23


def test_the_words_of_a_quote_have_233_characters_because_the_plain_bound_counts_the_address_in_full() -> (
    None
):
    room = X_MAX_WEIGHT - len(URL) - 1
    assert room == 227 and len(URL) == 52, "the address is counted in full by the plain bound"
    assert x_length_problem("a" * room + " " + URL) is None
    assert x_length_problem("a" * (room + 1) + " " + URL) == ("plain", 281)
    # what X itself would take (weighted 280) is longer than what the draft path accepts: the safe direction
    assert x_weighted_length("a" * 256 + " " + URL) == X_MAX_WEIGHT


def test_the_quote_detector_and_the_counter_agree_on_what_the_address_is() -> None:
    text = f"My take. {URL}"
    found = trailing_status(text)
    assert found is not None and found[0] == URL
    assert x_weighted_length(text) == x_weighted_length("My take. ") + 23
