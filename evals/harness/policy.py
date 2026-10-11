"""Heuristic policy 22.7 — a decision table, not a weighted sum.

The policy version is recorded in EVERY assessment (`heuristic_policy_version`, L4481),
so that the result is reproducible. Changing any rule or threshold here requires
bumping the version — otherwise two different orderings would live under one name.

What is NOT here and must not be added:
- a `score` field, a weighted sum, multiplication of signal class ranks (L4468, L4481),
- `fake_engagement_flags` as an input — in E1 the detection is log-only (L4492, L5551),
- a zero in place of a metric that the platform did not expose (L4299, L5552).
"""

from __future__ import annotations

from community_os_contracts import PriorityTier, WindowState

from .types import ThreadObservation

#: Version of the ordering policy. Format: section/freeze date/revision.
HEURISTIC_POLICY_VERSION = "22.7/2026-08-30/1"

#: Version of the read adapter — goes into `adapter_version` in the assessment (L4452).
ADAPTER_VERSION = "fixture-site/0.1.0"

# --- Decision table thresholds -----------------------------------------------
# The thresholds are explicit and named. They are not "weights": none of them is
# multiplied by or summed with another — each one is a condition in a branch.

#: Share of replies with substance above which a thread is a conversation, not exposure.
SUBSTANTIVE_A = 0.50
SUBSTANTIVE_B = 0.30

#: Share of ICP participants above which a thread is worth entering despite a smaller scale.
ICP_A = 0.40

#: Minimum number of unique participants to speak of a discussion, not an exchange of words.
MIN_PARTICIPANTS = 3

#: Windows in which entering makes any sense at all (L4445, L4590).
OPEN_WINDOWS = frozenset({WindowState.RISING, WindowState.PEAK, WindowState.DECAYING})


def assign_priority_tier(obs: ThreadObservation) -> tuple[PriorityTier, str]:
    """Tier + reason. A cascade of conditions read top-down, the first hit wins.

    The order of the branches IS the definition. The negative comes first, because it is
    a stop, not a weight.
    """
    if obs.negative_signal_classes:
        return (
            PriorityTier.REJECT,
            "sygnał negatywny: "
            + ", ".join(obs.negative_signal_labels)
            + " — twardy stop, nie ujemna waga (L4444, L4472)",
        )

    if obs.window_state is WindowState.CLOSED:
        return PriorityTier.C, "okno życia zamknięte — wejście nie ma już sensu (L4445)"

    meets_substantive_a = obs.substantive_reply_ratio >= SUBSTANTIVE_A
    meets_substantive_b = obs.substantive_reply_ratio >= SUBSTANTIVE_B
    author_present = obs.author_reply_count > 0
    is_discussion = obs.unique_participants >= MIN_PARTICIPANTS
    window_open = obs.window_state in OPEN_WINDOWS
    # UNKNOWN is NOT a zero: an unknown ICP neither creates the condition nor breaks it.
    high_icp = obs.icp_share is not None and obs.icp_share >= ICP_A
    has_amplification = obs.amplification_count > 0

    if meets_substantive_a and author_present and is_discussion and window_open:
        if has_amplification or high_icp:
            return (
                PriorityTier.A,
                "konkret w odpowiedziach, autor obecny, "
                + ("amplifikacja z intencją" if has_amplification else "wysoki udział ICP"),
            )
        return PriorityTier.B, "konkret i autor obecny, ale bez amplifikacji i bez ICP"

    if meets_substantive_b and is_discussion and window_open:
        missing_items = (
            "autor nie odpowiada" if not author_present else "udział konkretu poniżej progu A"
        )
        return PriorityTier.B, f"dyskusja jest, ale {missing_items}"

    if not is_discussion:
        return PriorityTier.C, "za mało unikalnych rozmówców, żeby mówić o dyskusji"

    return PriorityTier.C, "ekspozycja bez rozmowy: udział odpowiedzi z konkretem poniżej progu"


def data_quality(obs: ThreadObservation) -> str:
    """Quality of the read data. Hidden metrics lower the DATA QUALITY, not the tier."""
    if not obs.hidden_metrics:
        return "COMPLETE"
    return "PARTIAL:" + ",".join(sorted(obs.hidden_metrics))


def confidence(obs: ThreadObservation) -> float:
    """Confidence of the assessment. Falls with the number of metrics the platform hid.

    This is NOT a quality score of the thread — it is the quality of OUR READ. The value
    never enters the ordering of candidates.
    """
    base_confidence = 0.9 if obs.negative_signal_classes else 0.8
    penalty = 0.1 * len(obs.hidden_metrics)
    return round(max(0.3, base_confidence - penalty), 3)


def entry_opportunity(obs: ThreadObservation, tier: PriorityTier) -> str:
    """What concrete value we can add (L4463). Always non-empty — the field is required."""
    if tier is PriorityTier.REJECT:
        return "brak — wątek zatrzymany sygnałem negatywnym, nie wchodzimy"
    if obs.window_state is WindowState.CLOSED:
        return "brak — okno zamknięte, wkład trafiłby po czasie"
    title_excerpt = obs.title[:60]
    if obs.substantive_reply_ratio >= SUBSTANTIVE_A:
        return f"odnieść się do konkretu w wątku '{title_excerpt}' i dołożyć własny pomiar"
    return f"doprecyzować pytanie postawione w wątku '{title_excerpt}' i podać liczbę"


__all__ = [
    "ADAPTER_VERSION",
    "HEURISTIC_POLICY_VERSION",
    "ICP_A",
    "MIN_PARTICIPANTS",
    "OPEN_WINDOWS",
    "SUBSTANTIVE_A",
    "SUBSTANTIVE_B",
    "assign_priority_tier",
    "confidence",
    "data_quality",
    "entry_opportunity",
]
