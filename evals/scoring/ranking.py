"""Candidate ordering key. It composes TWO symbols from the D21 contracts — not a third one.

L4468: "We do not sum points into one score. We fix an order of signal classes and sort
the candidates first by class, then by calibration against the channel background."

The sentence has two parts and both are already implemented in `community_os_contracts.quality`:

    signal class              → SIGNAL_CLASS_ORDER
    calibration and the rest  → ordering_key(assessment)

This module only COMPOSES them in that order. There is no local table of class ranks here
and no local sorting by tier — were there one, the evals would be checking a local copy of
the definition instead of the one production will use.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from community_os_contracts import (
    NEGATIVE_STOP_CLASSES,
    SIGNAL_CLASS_ORDER,
    SignalClass,
    ThreadQualityAssessment,
    ordering_key,
)

__all__ = [
    "candidate_sort_key",
    "signal_class_rank",
    "sort_candidates",
    "strictly_precedes_by_class",
]


def signal_class_rank(signal_class: SignalClass) -> int:
    """Rank of a signal class. Straight from the contracts — with no local table."""
    return SIGNAL_CLASS_ORDER[signal_class]


def candidate_sort_key(
    signal_class: SignalClass, assessment: ThreadQualityAssessment
) -> tuple[int, int, int, int, float]:
    """Sort key of a single candidate. A smaller tuple = higher in the ranking.

    The order of the components is a literal reading of L4468:

    1. `stop` — a negative signal pushes to the end "by two orders of magnitude", so it is
       a separate, first component, and not a negative weight inside a sum (L4472).
    2. `signal_class_rank` — the class order from L4471-4479.
    3.-5. — the unpacked `ordering_key` from the contracts: tier, life window, calibration.

    A tuple, not a number: summing this with anything is impossible, and that is the point.
    """
    stop = 1 if (signal_class in NEGATIVE_STOP_CLASSES or assessment.has_negative_signal) else 0
    tier_rank, window_rank, calibration = ordering_key(assessment)
    return (stop, signal_class_rank(signal_class), tier_rank, window_rank, calibration)


def sort_candidates(
    items: Iterable[tuple[SignalClass, ThreadQualityAssessment, object]],
) -> list[tuple[SignalClass, ThreadQualityAssessment, object]]:
    """Stable sort by `candidate_sort_key`, with `thread_ref` as the tie-breaker.

    The tie is broken by `thread_ref` so that the result does NOT depend on the input order —
    the DoD requires an identical ORDER across two runs (L5553).
    """
    return sorted(
        items,
        key=lambda t: (candidate_sort_key(t[0], t[1]), t[1].thread_ref),
    )


def strictly_precedes_by_class(a: SignalClass, b: SignalClass) -> bool:
    """Whether class `a` is STRICTLY stronger than `b` in the L4471-4479 order.

    `INTENTIONAL_AMPLIFICATION` and `SUBSTANTIVE_REPLY` have the same rank (L4474), so
    for that pair the function returns `False` in both directions — and that is the correct
    result, not a failure to settle it.
    """
    return signal_class_rank(a) < signal_class_rank(b)


def order_of(refs: Sequence[str], subset: Iterable[str]) -> list[int]:
    """Positions of the given references in the given order. A helper used in assertions."""
    index = {ref: i for i, ref in enumerate(refs)}
    return [index[r] for r in subset]
