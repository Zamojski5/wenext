"""The judgment record — the unit of the data flywheel (`docs/KIERUNEK-PRODUKTU.md` §3, §8, §9).

What this type makes impossible at the level of the type, and why each rule is here:

1. **An `ENGAGE` without saying what the user would add.** §4 of the direction names the slop
   this product must not produce — generic praise, summaries of the OP, comments written so the
   account looks active. A decision to enter that cannot name the concrete contribution is that
   slop one step earlier. So `what_user_could_add` is REQUIRED for `ENGAGE`, and it must be a
   sentence, not a token.
2. **A decision without reasoning.** §8: after 500–1000 decisions the system has to be visibly
   better, and that is only measurable if every decision — `IGNORE` included — explains itself.
   `reasoning` is required for all three decisions.
3. **A decision that cannot be reproduced.** Every record names the prompt (name + full SHA-256),
   the model and the effort that produced it, and the version of the user profile it was
   grounded in. The same discipline `evals/harness/prompts.py` applies to eval records
   (L5308 in the roadmap) applies to production judgments here — without it the flywheel
   has data but no way to tell which change made it better.

Nothing here scores threads. `ThreadQualityAssessment` (`quality.py`) stays a SIGNAL about the
conversation; this record is the DECISION about whether THIS user has something to add to it.
The two answer different questions and `docs/OUTCOMES.md` §4 says so in the row "model decyzji".
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import JudgmentDecision, JudgmentFeedbackVerdict

#: A full SHA-256 digest in hex. The response and the database both hold the FULL digest; a
#: prefix is how two different prompts start to look identical (the same reasoning as
#: `PAYLOAD_HASH_LENGTH` in `control_plane/api/approvals.py`).
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: Below this, "what the user could add" is a label, not a contribution. Measured against the
#: shortest honest example anyone wrote while designing this ("a benchmark of X vs Y at 10k rps",
#: 36 characters); a threshold of one character would let `"yes"` through.
MIN_CONTRIBUTION_CHARS = 20

#: The same floor for the reasoning of any decision. `"not relevant"` is 12 characters and is
#: exactly the kind of record that teaches nothing.
MIN_REASONING_CHARS = 20


class JudgmentRecord(BaseModel):
    """One decision about one thread, with everything needed to reproduce and to learn from it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The thread this is about, in the observer's `platform:community:external_id` spelling.
    thread_ref: str = Field(min_length=1)
    decision: JudgmentDecision
    #: Why. Required for every decision — `IGNORE` most of all (§9: negative data).
    reasoning: str
    #: The concrete thing the user would bring. Required for `ENGAGE`, optional otherwise: a
    #: `WAIT` may already know what to say and only lack the moment; an `IGNORE` may still note
    #: what could have been said and why it would not be welcome.
    what_user_could_add: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    #: Which version of the user's profile the judge read. A judgment is only comparable with
    #: another judgment grounded in the same profile.
    user_profile_version: int = Field(ge=1)
    prompt_name: str = Field(min_length=1)
    prompt_sha256: str
    model: str = Field(min_length=1)
    effort: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _reasoning_is_a_sentence(self) -> JudgmentRecord:
        if len(self.reasoning.strip()) < MIN_REASONING_CHARS:
            raise ValueError(
                f"reasoning ma {len(self.reasoning.strip())} znaków, minimum {MIN_REASONING_CHARS}. "
                "Decyzja bez uzasadnienia nie uczy niczego — także IGNORE musi powiedzieć dlaczego "
                "(docs/KIERUNEK-PRODUKTU.md §8, §9)."
            )
        return self

    @model_validator(mode="after")
    def _engage_names_the_contribution(self) -> JudgmentRecord:
        if self.decision is not JudgmentDecision.ENGAGE:
            return self
        contribution = (self.what_user_could_add or "").strip()
        if len(contribution) < MIN_CONTRIBUTION_CHARS:
            raise ValueError(
                "ENGAGE bez konkretnego what_user_could_add (minimum "
                f"{MIN_CONTRIBUTION_CHARS} znaków, jest {len(contribution)}). Wejście, które nie "
                "potrafi nazwać wkładu, to slop o krok wcześniej (docs/KIERUNEK-PRODUKTU.md §4)."
            )
        return self

    @model_validator(mode="after")
    def _prompt_hash_is_a_full_digest(self) -> JudgmentRecord:
        if not _SHA256_HEX.match(self.prompt_sha256):
            raise ValueError(
                f"prompt_sha256={self.prompt_sha256!r} nie jest pełnym SHA-256 w hex (64 znaki). "
                "Prefiks hasha to dwa różne prompty, które wyglądają identycznie."
            )
        return self

    @model_validator(mode="after")
    def _created_at_is_aware(self) -> JudgmentRecord:
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at musi mieć strefę czasową — naiwny czas jest niejednoznaczny"
            )
        return self

    @property
    def is_negative(self) -> bool:
        """`IGNORE` — the record the public internet never shows (§9)."""
        return self.decision is JudgmentDecision.IGNORE


class JudgmentFeedback(BaseModel):
    """The operator's answer to one judgment. Kept next to the decision, never instead of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: JudgmentFeedbackVerdict
    note: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _created_at_is_aware(self) -> JudgmentFeedback:
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at musi mieć strefę czasową — naiwny czas jest niejednoznaczny"
            )
        return self


def agreement(decision: JudgmentDecision, verdict: JudgmentFeedbackVerdict) -> bool | None:
    """Whether the operator agreed with the agent, or `None` when the pair says nothing about it.

    `IRRELEVANT` on an `IGNORE` is agreement of a kind (both say "not here"), but it is not the
    signal Outcome B measures — B measures whether the operator would have SPOKEN where the agent
    said `ENGAGE` — so it is reported as `None`, not as `True`, and a caller averaging over the
    result cannot count it either way. Returning `False` there would punish the judge for a
    decision the operator did not dispute; returning `True` would inflate agreement with rows
    that measured nothing. This is the `all([])` lesson applied to a metric.
    """
    if verdict is JudgmentFeedbackVerdict.IRRELEVANT:
        return None if decision is JudgmentDecision.IGNORE else False
    return verdict is JudgmentFeedbackVerdict.AGREE
