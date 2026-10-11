"""Discussion quality assessment — section 22.7 (L4430-4492).

Two things this module is to make impossible at the level of the type:

1. **Summing points into a single score.** "We do not sum points into a single score. We fix
   an ordering of the signal classes and sort the candidates first by class, then by the
   calibration against the channel's background." (L4468) That is why there is no
   `score: float` field here, and the ordering is a function returning a sorting tuple.

2. **Offsetting a negative signal with a positive one.** "a negative signal in
   `ThreadQualityAssessment` is not offset by any number of positive signals"
   (L5550); a negative "acts like a stop, not like a weight" (L4472).

The methodological caveat that has to stay in the code exactly as it stands in the document
(L4481): the published X ranking weights multiply the predicted action probabilities of one
specific viewer, and not raw counters. We use them exclusively as an ORDERING heuristic.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import PriorityTier, SignalClass, WindowState
from .observed import Observed

#: The STRENGTH of a signal per the ordering from L4471-4479. Lower number = STRONGER signal.
#: This is a transcription of the document's list, read from the top: the negatives stand at
#: its peak, because they are "two orders of magnitude" stronger than any positive signal
#: (L4472).
#:
#: THIS IS NOT AN ENTRY PRIORITY AND IT MUST NOT BE USED AS A SORTING KEY FOR CANDIDATES.
#: A negative signal is the strongest, but it acts like a STOP, not like a high priority —
#: sorting by this rank alone would put blocked threads at the BEGINNING of the list, that is
#: exactly the wrong way round. The trap was found by the review while building the evals (D17).
#: `ordering_key()`, which cuts the negatives off earlier, is what orders candidates;
#: using this map on its own as a key is blocked by `entry_priority_rank()`.
SIGNAL_CLASS_ORDER: dict[SignalClass, int] = {
    SignalClass.NEGATIVE_MODERATION: 0,
    SignalClass.BAN: 0,
    SignalClass.LOCK: 0,
    # INTENTIONAL_AMPLIFICATION ≈ SUBSTANTIVE_REPLY (L4474) — the same rank.
    SignalClass.INTENTIONAL_AMPLIFICATION: 1,
    SignalClass.SUBSTANTIVE_REPLY: 1,
    SignalClass.REPOST: 2,
    SignalClass.REPLY: 3,
    SignalClass.FOLLOW: 4,
    SignalClass.SUBSCRIBE: 4,
    SignalClass.LIKE: 5,
    SignalClass.UPVOTE: 5,
}

#: Classes that are a hard stop, and not a negative term in a sum (L4444, L4472).
NEGATIVE_STOP_CLASSES: frozenset[SignalClass] = frozenset(
    {SignalClass.NEGATIVE_MODERATION, SignalClass.BAN, SignalClass.LOCK}
)


class NegativeSignal(BaseModel):
    """A single negative signal. Its presence excludes the thread regardless of the positives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_class: SignalClass
    observed_at: datetime
    evidence_ref: str

    @model_validator(mode="after")
    def _only_negative_classes(self) -> NegativeSignal:
        if self.signal_class not in NEGATIVE_STOP_CLASSES:
            raise ValueError(
                f"{self.signal_class} nie jest klasą negatywną. "
                f"Dozwolone: {sorted(c.value for c in NEGATIVE_STOP_CLASSES)}"
            )
        return self


class ThreadQualityAssessment(BaseModel):
    """The assessment record from L4449-4466. Fields in the order from the document.

    All observable metrics are of the `Observed[...]` type, because a signal not visible from
    the outside is recorded as `UNKNOWN`, never as zero (L4299, L5552).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_ref: str
    surface_id: UUID
    community_id: UUID
    observed_at: datetime
    collected_at: datetime
    adapter_version: str

    #: Thread depth: levels, unique interlocutors, median reply length (L4438).
    depth_profile: Observed[int]
    unique_participants: Observed[int]
    #: Share of replies with substance: number, source, code, experience, counter-argument (L4439).
    substantive_reply_ratio: Observed[float]
    #: Whether the author replies, after what time, how many times (L4440).
    author_participation: Observed[float]
    #: The share of interlocutors matching `audience_models` (L4441).
    icp_share: Observed[float]
    #: A quote, a repost with a comment, a crosspost (L4442).
    intentional_amplification: Observed[int]
    #: What we normalize against — the surface median for format, hour, network size (L4443).
    reach_calibration_basis: str

    negative_signals: tuple[NegativeSignal, ...] = ()
    window_state: WindowState
    #: Log-only in E1: flags do NOT lower priority_tier and do NOT reject the thread (L4492, L5551).
    fake_engagement_flags: tuple[str, ...] = ()
    #: What concrete value we can add (L4463).
    entry_opportunity: str
    priority_tier: PriorityTier
    confidence: float = Field(ge=0.0, le=1.0)
    data_quality: str
    evidence_refs: tuple[str, ...] = ()
    #: So that the result is reproducible (L4481, L5553).
    heuristic_policy_version: str

    @model_validator(mode="after")
    def _negative_signal_is_a_stop(self) -> ThreadQualityAssessment:
        """A negative signal forces REJECT — it cannot be outvoted by positives (L5550)."""
        if self.negative_signals and self.priority_tier is not PriorityTier.REJECT:
            raise ValueError(
                "Wątek z sygnałem negatywnym musi mieć priority_tier=REJECT. "
                "Sygnał negatywny działa jak stop, nie jak ujemna waga (L4444, L4472, L5550)."
            )
        return self

    @model_validator(mode="after")
    def _fake_flags_do_not_change_tier(self) -> ThreadQualityAssessment:
        """In E1 fake engagement detection is log-only (L4300, L4492, L5551).

        This cannot be expressed by the type alone — the flag and the tier are independent
        fields. The validator guards at least this much: a flag on its own does not justify
        REJECT; REJECT requires a real negative signal.
        """
        if (
            self.priority_tier is PriorityTier.REJECT
            and self.fake_engagement_flags
            and not self.negative_signals
        ):
            raise ValueError(
                "REJECT nie może wynikać z samej flagi fake engagementu — w E1 detekcja "
                "jest log-only i nie odrzuca wątku (L4300, L4492, L5551)."
            )
        return self

    @property
    def has_negative_signal(self) -> bool:
        return bool(self.negative_signals)

    def is_enterable(self) -> bool:
        """Whether entering this thread may be considered at all.

        A thread with `window_state = CLOSED` creates no new draft (L4590, L5541).
        """
        if self.has_negative_signal:
            return False
        if self.window_state is WindowState.CLOSED:
            return False
        return self.priority_tier in {PriorityTier.A, PriorityTier.B}


def entry_priority_rank(signal_class: SignalClass) -> int:
    """The rank of a class for ordering ENTRY CANDIDATES.

    It refuses to serve negative classes, because for them the answer is not "low priority",
    but "we do not enter". Whoever asks about the entry priority for `LOCK` is asking the
    wrong question — and it is better that they learn it from an exception than from a silent
    zero at the top of the list.
    """
    if signal_class in NEGATIVE_STOP_CLASSES:
        raise ValueError(
            f"{signal_class} jest sygnałem STOP, nie priorytetem wejścia (L4444, L4472). "
            "Sprawdź `NEGATIVE_STOP_CLASSES` przed pytaniem o porządek."
        )
    return SIGNAL_CLASS_ORDER[signal_class]


def ordering_key(assessment: ThreadQualityAssessment) -> tuple[int, int, float]:
    """The candidate sorting key. First the signal class, then the calibration (L4468).

    Returns a tuple, not a number — deliberately, so that nobody can sum it with anything
    else nor call it a "score". A smaller tuple = a higher priority.
    """
    if assessment.has_negative_signal:
        # A negative pushes to the very end, regardless of everything else.
        return (9, 9, 0.0)

    tier_rank = {PriorityTier.A: 0, PriorityTier.B: 1, PriorityTier.C: 2, PriorityTier.REJECT: 3}
    window_rank = {
        WindowState.RISING: 0,
        WindowState.PEAK: 1,
        WindowState.DECAYING: 2,
        WindowState.CLOSED: 3,
    }
    # Calibration: we use the share of substantive replies when it is known.
    # An unknown value is NOT treated like zero — it lands at the end of its class.
    calibration = (
        -assessment.substantive_reply_ratio.value
        if assessment.substantive_reply_ratio.is_known
        and assessment.substantive_reply_ratio.value is not None
        else 0.0
    )
    return (tier_rank[assessment.priority_tier], window_rank[assessment.window_state], calibration)
