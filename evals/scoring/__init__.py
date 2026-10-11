"""Eval scoring — deterministic, with no LLM as a judge.

`gate_metrics` is the most important thing here: it is the only source of the definition
of the normalized edit distance from L4607. **F4 imports this symbol, it does not
reimplement it.**
"""

from __future__ import annotations

from .gate_metrics import (
    MATERIAL_EDIT_THRESHOLD,
    approved_without_material_edit,
    assess_edit,
    normalized_edit_distance,
)
from .ranking import candidate_sort_key, signal_class_rank, sort_candidates

__all__ = [
    "MATERIAL_EDIT_THRESHOLD",
    "approved_without_material_edit",
    "assess_edit",
    "candidate_sort_key",
    "normalized_edit_distance",
    "signal_class_rank",
    "sort_candidates",
]
