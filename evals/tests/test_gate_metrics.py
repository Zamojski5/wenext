"""Golden test vector of the edit distance function from L4607 (DoD D17).

This function is the contract between F0 and F4. If these assertions fail, the draft quality
gate in E1 computes something other than the eval that was meant to predict it.
"""

from __future__ import annotations

import math

import pytest

from evals.scoring.gate_metrics import (
    GOLDEN_VECTORS,
    MATERIAL_EDIT_THRESHOLD,
    approved_without_material_edit,
    assess_edit,
    levenshtein,
    normalized_edit_distance,
    tokenize_for_edit_distance,
)


@pytest.mark.parametrize(("description", "original", "edited", "expected"), GOLDEN_VECTORS)
def test_golden_vector(description: str, original: str, edited: str, expected: float) -> None:
    result = normalized_edit_distance(original, edited)
    assert math.isclose(result, expected, abs_tol=1e-9), (
        f"{description}: oczekiwano {expected}, jest {result}"
    )


def test_gate_threshold_is_015_from_L4607() -> None:
    assert MATERIAL_EDIT_THRESHOLD == 0.15


def test_distance_is_symmetric() -> None:
    for _description, a, b, _ in GOLDEN_VECTORS:
        assert math.isclose(
            normalized_edit_distance(a, b), normalized_edit_distance(b, a), abs_tol=1e-12
        )


def test_distance_always_within_0_1() -> None:
    samples = [t for v in GOLDEN_VECTORS for t in (v[1], v[2])]
    for a in samples:
        for b in samples:
            result = normalized_edit_distance(a, b)
            assert 0.0 <= result <= 1.0


def test_levenshtein_on_tuples() -> None:
    assert levenshtein((), ()) == 0
    assert levenshtein(("a",), ()) == 1
    assert levenshtein(("a", "b", "c"), ("a", "x", "c")) == 1
    assert levenshtein(("a", "b"), ("b", "a")) == 2


def test_tokenization_trims_edge_punctuation_but_not_inner() -> None:
    assert tokenize_for_edit_distance("„Kod: sub-50ms.”") == ("kod", "sub-50ms")
    assert tokenize_for_edit_distance("3,4x") == ("3,4x",)


def test_the_three_L4607_conditions_act_independently() -> None:
    """An Approve counts only when ALL three conditions are met."""
    text = "raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć"
    minor_edit = "raz dwa trzy cztery pięć sześć siedem osiem dziewięć jedenaście"  # 0.1

    assert approved_without_material_edit(text, minor_edit)
    # 1) the operator's MATERIAL_EDIT flag disqualifies on its own
    assert not approved_without_material_edit(text, minor_edit, material_edit_flag=True)
    # 2) a change of thesis disqualifies on its own, even if the distance is zero
    assert not approved_without_material_edit(text, text, thesis_changed=True)
    # 3) the distance alone, above the threshold, disqualifies
    major_edit = "raz dwa trzy cztery pięć sześć siedem osiem jedenaście dwanaście"  # 0.2
    assert not approved_without_material_edit(text, major_edit)


def test_assess_edit_records_both_fields_required_by_L4607() -> None:
    assessment = assess_edit("raz dwa trzy", "raz dwa cztery", material_edit_flag=False)
    assert assessment.distance > 0
    assert assessment.material_edit_flag is False
    assert assessment.thesis_changed is False
    assert assessment.distance_within_threshold is False  # 1/3 > 0.15


def test_the_F4_contract_is_a_single_symbol() -> None:
    """`open_decision` no. 6: F4 imports, it does not reimplement.

    The test guards the import path, because that is what the contract is — renaming the
    module would break F4 quietly.
    """
    import importlib

    module = importlib.import_module("evals.scoring.gate_metrics")
    for symbol in (
        "normalized_edit_distance",
        "approved_without_material_edit",
        "MATERIAL_EDIT_THRESHOLD",
        "assess_edit",
    ):
        assert hasattr(module, symbol), f"F4 importuje {symbol} z tego modułu"
