"""One founder rule about every text the model produces: no long dashes, ever (2026-09-04).

The em dash (U+2014) and the en dash (U+2013) are the typographic fingerprint of generated text.
The founder's rule, relayed by the orchestrator, is absolute: a judgment's reasoning, a community
picture, a voice card, a draft reply — none of them carries either character. A period, a comma or
a colon does the work instead.

Two lines of defence, on purpose:

1. **The prompt says so** (every prompt in `prompts/` from v2 on carries the rule; the manifest
   test checks the wording is there). Prompts are advice, and a model follows advice most of the
   time.
2. **This module makes it true** before anything reaches the database. `clean_payload` walks the
   validated output and rewrites every long dash deterministically, and reports how many it found —
   a number the handlers log, so a prompt that stops working is visible as a count, not as a
   surprise on the dashboard.

Cleaning rather than rejecting: a judgment that cost a model call is not thrown away for one
character the rule can rewrite without changing what it says. Rejection would be right for a
rule about *meaning*; this one is about a glyph.

The rewrite rules are small and listed here so they can be argued with:

* between two digits (a range such as 2 to 5, or two years) a long dash becomes a hyphen-minus;
* anywhere else it becomes a comma with one space (`to — jak zwykle — działa` becomes
  `to, jak zwykle, działa`); a comma that would land right before other punctuation is dropped,
  one at the very start of the text is dropped, and one at the very end becomes a period.
"""

from __future__ import annotations

import re
from typing import Any

EM_DASH = "\u2014"
EN_DASH = "\u2013"
LONG_DASHES: frozenset[str] = frozenset({EM_DASH, EN_DASH})

_BETWEEN_DIGITS = re.compile(r"(?<=\d)\s*[\u2013\u2014]+\s*(?=\d)")
_ANYWHERE = re.compile(r"\s*[\u2013\u2014]+\s*")
_COMMA_BEFORE_PUNCTUATION = re.compile(r", (?=[,.;:!?)\]])")
_LEADING_COMMA = re.compile(r"^, ")
_TRAILING_COMMA = re.compile(r", $")


def count_long_dashes(text: str) -> int:
    return text.count(EM_DASH) + text.count(EN_DASH)


def has_long_dash(text: str) -> bool:
    return count_long_dashes(text) > 0


def clean_text(text: str) -> str:
    """`text` with every long dash rewritten by the rules in the module docstring. A text without
    one comes back unchanged, the same object."""
    if not has_long_dash(text):
        return text
    text = _BETWEEN_DIGITS.sub("-", text)
    text = _ANYWHERE.sub(", ", text)
    text = _COMMA_BEFORE_PUNCTUATION.sub("", text)
    text = _LEADING_COMMA.sub("", text)
    text = _TRAILING_COMMA.sub(".", text)
    return text


def clean_payload(value: Any) -> tuple[Any, int]:
    """`value` with every string inside it cleaned (dicts by value, lists and tuples by element,
    keys untouched — they are schema, not prose) and the number of long dashes that were found.

    Returns a new structure; the input is not modified. Types other than str/dict/list/tuple pass
    through as they are.
    """
    if isinstance(value, str):
        return clean_text(value), count_long_dashes(value)
    if isinstance(value, dict):
        total = 0
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            cleaned[key], found = clean_payload(item)
            total += found
        return cleaned, total
    if isinstance(value, list | tuple):
        total = 0
        items: list[Any] = []
        for item in value:
            cleaned_item, found = clean_payload(item)
            items.append(cleaned_item)
            total += found
        return (items if isinstance(value, list) else tuple(items)), total
    return value, 0


def assert_no_long_dashes(value: Any, *, where: str) -> None:
    """Fail loudly if `value` still carries a long dash anywhere — the check a store may run right
    before an INSERT, so the rule holds even for a writer that forgot `clean_payload`."""
    _, found = clean_payload(value)
    if found:
        raise ValueError(
            f"{where}: {found} długich pauz (U+2014/U+2013) w tekście — reguła założyciela z "
            "2026-09-04 zakazuje ich w każdym tekście modelu; przepuść wynik przez "
            "text_rules.clean_payload przed zapisem"
        )
