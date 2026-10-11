"""Draft quality gate metrics — the ONLY source of the definition from L4607.

    | Draft quality | >= 50% of drafts approved without a material edit | < 20% |
    | `Approve` without the operator's `MATERIAL_EDIT` **and** normalized edit distance
      <= 0.15 with no change of thesis; both recorded at approval |
                                                            — L4607

A CONTRACT BETWEEN PHASES — **F4 IMPORTS, IT DOES NOT REIMPLEMENT.**

The approval queue from E1.F4 (L5299-5303) computes the same metric as the F0 eval. If
it computes it differently, the eval result stops predicting the behaviour of the gate and
the whole draft eval loses its point — that is why this is one importable symbol, not two
copies:

    from evals.scoring.gate_metrics import (
        MATERIAL_EDIT_THRESHOLD,
        normalized_edit_distance,
        approved_without_material_edit,
    )

This settles `open_decision` no. 6 from the F0 specification (the second option: one symbol
in `evals/scoring/gate_metrics.py`, F4 imports it). Changing the algorithm here changes the
E1 gate — which is why every change must go through `GOLDEN_VECTORS` below.

---

**What the document does NOT define, and what had to be chosen.** L4607 says "normalized
edit distance", it does not say what it is computed over. The choice is recorded here so
that there is only one:

1. **Token level, not character level.** The gate asks "did the operator materially change
   the draft", not "how many keys did they press". A character distance punishes a comma fix
   just as much as swapping an argument; at a 15% threshold a typo in a long word could
   outweigh replacing a short sentence.
2. **Normalization by `max(len(a), len(b))`.** The result is in [0, 1], symmetric
   and does not grow above 1 when a long paragraph is appended.
3. **Text normalization before tokenization:** NFC, casefold, collapsing whitespace,
   trimming punctuation adjacent to a token. A change of formatting is NOT a material
   edit, and the operator in the UI constantly rearranges dashes and quotation marks.
4. **A change of thesis is NOT computed from the text.** It is a separate input
   `thesis_changed`, because L4607 requires recording "both" at approval — the thesis comes
   from the operator, not from a similarity heuristic. Guessing the thesis would be quietly
   inventing a signal we do not have (an analogy to invariant 7 from 22.2).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "GOLDEN_VECTORS",
    "MATERIAL_EDIT_THRESHOLD",
    "EditAssessment",
    "approved_without_material_edit",
    "assess_edit",
    "levenshtein",
    "normalized_edit_distance",
    "tokenize_for_edit_distance",
]

#: Threshold from L4607. A normalized edit distance <= 0.15 means "no material edit".
#: This is the GATE threshold, not the eval pass threshold (the latter does not exist in F0).
MATERIAL_EDIT_THRESHOLD: float = 0.15

#: Punctuation trimmed from the edges of a token. Inside a token it stays ("sub-50ms",
#: "3,4x" and "v1.62.0" are one word, not three).
# Written with escapes rather than literal characters: the hyphen, the en dash and the em
# dash look almost identical in an editor, and here the difference decides the tokenization.
_EDGE_PUNCTUATION = (
    "\"'`.,;:!?()[]{}<>-"
    "\u00ab\u00bb"  # angle quotation marks
    "\u201e\u201d\u201c\u2018\u2019"  # typographic quotation marks
    "\u2026"  # ellipsis
    "\u2013\u2014"  # en dash, em dash
)

_WHITESPACE = re.compile(r"\s+")


def tokenize_for_edit_distance(text: str) -> tuple[str, ...]:
    """Text → a tuple of comparable tokens. Deterministic, with no dependency on the locale.

    Steps: NFC → casefold → collapsing whitespace → splitting on the space →
    trimming punctuation from the edges → dropping empty tokens.
    """
    normalized = unicodedata.normalize("NFC", text).casefold()
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    if not normalized:
        return ()
    tokens = (t.strip(_EDGE_PUNCTUATION) for t in normalized.split(" "))
    return tuple(t for t in tokens if t)


def levenshtein(a: Sequence[object], b: Sequence[object]) -> int:
    """The classic Levenshtein distance over any sequence. Memory O(min(|a|,|b|))."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)

    previous_row = list(range(len(b) + 1))
    for i, elem_a in enumerate(a, start=1):
        current_row = [i]
        for j, elem_b in enumerate(b, start=1):
            insertion = current_row[j - 1] + 1
            deletion = previous_row[j] + 1
            substitution = previous_row[j - 1] + (elem_a != elem_b)
            current_row.append(min(insertion, deletion, substitution))
        previous_row = current_row
    return previous_row[-1]


def normalized_edit_distance(original: str, edited: str) -> float:
    """The normalized edit distance from L4607. The result is in [0.0, 1.0].

    0.0 = the texts are identical after normalization (a purely cosmetic edit).
    1.0 = no shared token, or one of the texts is empty while the other is not.

    Two empty texts give 0.0 — an empty draft approved as empty is not an edit.
    """
    tokens_a = tokenize_for_edit_distance(original)
    tokens_b = tokenize_for_edit_distance(edited)
    length = max(len(tokens_a), len(tokens_b))
    if length == 0:
        return 0.0
    return levenshtein(tokens_a, tokens_b) / length


@dataclass(frozen=True)
class EditAssessment:
    """The full set of what L4607 requires to be recorded at approval.

    `material_edit_flag` and `thesis_changed` are INPUTS from the operator, not results
    of a computation. `distance` and `distance_within_threshold` are computed.
    """

    distance: float
    distance_within_threshold: bool
    material_edit_flag: bool
    thesis_changed: bool
    counts_as_approved_without_material_edit: bool


def assess_edit(
    original: str,
    edited: str,
    *,
    material_edit_flag: bool = False,
    thesis_changed: bool = False,
) -> EditAssessment:
    """The full L4607 verdict for one pair draft → approved content."""
    distance = normalized_edit_distance(original, edited)
    within_threshold = distance <= MATERIAL_EDIT_THRESHOLD
    return EditAssessment(
        distance=distance,
        distance_within_threshold=within_threshold,
        material_edit_flag=material_edit_flag,
        thesis_changed=thesis_changed,
        counts_as_approved_without_material_edit=(
            not material_edit_flag and within_threshold and not thesis_changed
        ),
    )


def approved_without_material_edit(
    original: str,
    edited: str,
    *,
    material_edit_flag: bool = False,
    thesis_changed: bool = False,
) -> bool:
    """Whether this approval counts towards the ">= 50% of drafts without a material edit"
    threshold (L4607).

    Three conditions at once, all from one sentence of the document:
    no operator `MATERIAL_EDIT` flag **and** distance <= 0.15 **and** no change of thesis.
    """
    return assess_edit(
        original,
        edited,
        material_edit_flag=material_edit_flag,
        thesis_changed=thesis_changed,
    ).counts_as_approved_without_material_edit


# ---------------------------------------------------------------------------
# Golden test vector (DoD D17: "the edit distance function ... has a golden test vector")
# ---------------------------------------------------------------------------
#
# The values are frozen and worked out by hand from the definition, not copied from the
# output of the code. Each entry: (description, original, edited, expected_distance).
# Changing the algorithm, the tokenization or the normalization WILL BREAK this vector —
# that is the point: F4 has the right to rely on the gate computing exactly what it
# computed in F0.
GOLDEN_VECTORS: tuple[tuple[str, str, str, float], ...] = (
    (
        "identyczne",
        "Sprawdziliśmy to na 14 węzłach i koszt spadł o jedną trzecią.",
        "Sprawdziliśmy to na 14 węzłach i koszt spadł o jedną trzecią.",
        0.0,
    ),
    (
        "różnica wyłącznie w interpunkcji i wielkości liter — nie jest edycją",
        "Sprawdziliśmy to na 14 węzłach, i koszt spadł o jedną trzecią!",
        "sprawdziliśmy to na 14 węzłach i koszt spadł o jedną trzecią",
        0.0,
    ),
    (
        "różnica wyłącznie w białych znakach",
        "  Jedno   zdanie\n\ndrugie zdanie  ",
        "Jedno zdanie drugie zdanie",
        0.0,
    ),
    (
        "jeden token zamieniony w tekście 10-tokenowym = 0,1 (poniżej progu)",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć jedenaście",
        0.1,
    ),
    (
        "dwa tokeny zamienione w tekście 10-tokenowym = 0,2 (powyżej progu)",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć",
        "raz dwa trzy cztery pięć sześć siedem osiem jedenaście dwanaście",
        0.2,
    ),
    (
        "dopisanie 2 tokenów do 8 = 2/10 = 0,2",
        "raz dwa trzy cztery pięć sześć siedem osiem",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć",
        0.2,
    ),
    (
        "usunięcie 1 tokenu z 10 = 1/10 = 0,1 (normalizacja przez dłuższy tekst)",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć",
        "raz dwa trzy cztery pięć sześć siedem osiem dziewięć",
        0.1,
    ),
    (
        "pełne przepisanie bez wspólnego tokenu = 1,0",
        "raz dwa trzy",
        "alfa beta gamma",
        1.0,
    ),
    (
        "pusty draft wobec niepustej treści = 1,0",
        "",
        "cokolwiek tu jest",
        1.0,
    ),
    (
        "oba puste = 0,0",
        "",
        "   ",
        0.0,
    ),
    (
        "token z myślnikiem i przecinkiem dziesiętnym zostaje jednym tokenem",
        "opóźnienie sub-50ms przy 3,4x ruchu",
        "opóźnienie sub-50ms przy 3,4x ruchu",
        0.0,
    ),
    (
        "wymiana liczby wewnątrz tokenu jest edycją: 1 z 8 tokenów",
        "opóźnienie sub-50ms przy 3,4x ruchu i sześciu węzłach",
        "opóźnienie sub-90ms przy 3,4x ruchu i sześciu węzłach",
        1 / 8,
    ),
)
