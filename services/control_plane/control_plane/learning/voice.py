"""The founder's rules for the TONE of a reply, as one short text and one seeded rule (13.10).

THE FOUNDER (13.10): dry, human irony, never a caricature. The long reference is his own
`HUMOR-GUIDE`; this is that guide compressed to what an agent can hold while it writes (about a
dozen lines, imperative, English), and it is the ONE text that three places repeat verbatim:
`HOST_INSTRUCTIONS` (the local plugin), `HOSTED_RULES` (the hosted endpoint and the skills rendered
from it) and the `glos` block of `playbook.read`. A test holds them equal, so the tone cannot drift
between the agent that is told it at the start of a conversation and the one that reads it again
right before drafting.

THESE ARE RULES, NOT MEASUREMENTS. Nothing here comes from a number. The one rule that is also kept
in the project's memory (`HUMOR_NEVER_PITCHES`) is stored as a `LESSON` with
`payload.rule = true` and a `source`, which migration 0040 allows ONLY in that form, and
`playbook.read` shows it under `zasady`, never among the measured `lekcje`.

No long dashes (U+2014, U+2013) in any of it: the founder's rule holds for what the agent reads
as much as for what it writes.
"""

from __future__ import annotations

from dataclasses import dataclass

VOICE_RULES = (
    "VOICE. Dry, human, never a caricature. Keep it short: 5 to 20 words by default, lowercase "
    "is fine, never explain the joke. Read the actual post first, then notice the funniest true "
    "angle already in it: reframe it, understate it, name the shared pain, treat the absurd as "
    "normal. Punch up at systems, tools, HR and the situation, never at the person; no humor "
    "about grief, illness or someone asking for help. Technical correctness comes first: a real "
    'question gets a plain, correct answer, no comedy. Banned templates: "Ah yes, the '
    'classic...", "Nothing says X like...", "the real X was Y all along", "POV:", "bro '
    'really", "this is peak". Hard rule: never a joke followed by a pivot to the product or '
    "to distribution; a funny reply stays only funny. Mention the product only in a plain reply, "
    "with no joke, to a post that is literally about the problem it solves (for Wenext itself: "
    "finding users or distribution). Final test: could a person have typed it in 10 seconds, and "
    "could it be pasted under 50 unrelated posts? If it could be pasted, rewrite it."
)


@dataclass(frozen=True)
class SeededRule:
    """A rule kept in memory beside the measured lessons, marked as a rule."""

    key: str
    statement: str
    source: str


HUMOR_NEVER_PITCHES = SeededRule(
    key="rule:humor-never-pitches",
    statement=(
        "Humor replies never pitch: a joke is never followed by a pivot to the product or to "
        "distribution. A funny reply stays only funny; a product mention belongs only in a plain "
        "reply, with no joke, under a post that is literally about the problem it solves."
    ),
    source="founder's voice rule, 13.10.2026 (HUMOR-GUIDE), relayed by the coordinator",
)

SEEDED_RULES: tuple[SeededRule, ...] = (HUMOR_NEVER_PITCHES,)
