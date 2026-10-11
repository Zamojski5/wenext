"""The community's voice card: how people write here, measured first and described second.

Etap 3, kamień M0 (`docs/OUTCOMES.md` §8). The founder's condition for letting the system draft a
single comment is that the comment must not read as technical, corporate or generated. Before a
prompt can say "write like the members", something has to know how the members write. This module
is that something, in two layers that are kept apart on purpose:

1. **Measured** (`voice_metrics`): counted over EVERY comment of the window with a body, no model
   involved. Length percentiles, the share of first-person comments, of questions, of links, how
   replies are distributed, what the comments that drew a conversation looked like. These numbers
   are the ground truth on the card and the yardstick for a draft later: a draft three times the
   median length is wrong before anyone reads it.
2. **Described** (the `community_voice` prompt over `voice_input`): a model reads the metrics and a
   sample of the comments (the ones that drew replies first) and writes, in Polish, what a person
   should do and avoid here. Every observation cites `comment_ids` from the sample; a citation the
   sample does not contain is dropped and counted (`invalid_quote_count` on the row), the same way
   the picture treats an invented `thread_ref`.

`ObservedComment.score` is an `Observed[int]` since 0005, and the card reports it as such: on Hacker
News every comment score is `UNKNOWN (NOT_EXPOSED_BY_PLATFORM)`, so "what gets replies" is measured
by reply counts, not by points. The prompt is told the same.

Founder rule 2026-09-04: no long dashes in model text; `validate_voice` runs
`text_rules.clean_payload` over the model's part of the card before it is stored, and the count is
on the result.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from community_os_contracts import Observed

from .context import metric_value
from .text_rules import clean_payload

#: How far back the card looks. A day of comments is a mood; a week is a voice.
VOICE_WINDOW = timedelta(days=7)

#: Below this many comments in the window the card is not drawn: a description of "how people
#: write" from a dozen comments would be a description of a dozen people.
VOICE_MIN_COMMENTS = 20

#: How many comments the MODEL sees (the metrics see all of them). Replied-to comments first.
VOICE_SAMPLE_LIMIT = 60
VOICE_SAMPLE_REPLIED = 30
VOICE_BODY_CHARS = 700

#: A comment that drew this many direct replies "got a conversation" (the founder's baseline
#: measurement of 2026-09-04 used the same threshold: 28 of 1754 HN comments).
CONVERSATION_REPLIES = 3

#: English first person, both cases for the pronouns that have one. `I` stays uppercase only:
#: a lowercase `i` is the Polish conjunction, and Polish comments will come with Reddit.
_FIRST_PERSON = re.compile(
    r"\b(I|I'm|I've|I'd|I'll|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself|[Ww]e|[Ww]e're|[Ww]e've|[Oo]ur|[Oo]urs)\b"
)
_LINK = re.compile(r"https?://")
_CODE = re.compile(r"`[^`]+`|^\s{4}\S", re.MULTILINE)


@dataclass(frozen=True)
class CommentVoice:
    """One comment as the card sees it: the row plus its place in the conversation."""

    external_id: str
    thread_ref: str
    thread_title: str
    parent_external_id: str | None
    author: str | None
    body: str
    created_at: datetime
    depth: int
    score: Observed[int]
    #: Direct replies observed under this comment (same thread, `parent_external_id` == this id).
    reply_count: int


def _percentile(sorted_values: Sequence[int], fraction: float) -> int:
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def _share(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def voice_metrics(comments: Sequence[CommentVoice]) -> dict[str, Any]:
    """Deterministic facts about EVERY comment given. Empty input → `{"comment_count": 0}`."""
    total = len(comments)
    if total == 0:
        return {"comment_count": 0}
    lengths = sorted(len(c.body) for c in comments)
    replied = [c for c in comments if c.reply_count >= CONVERSATION_REPLIES]
    any_reply = sum(1 for c in comments if c.reply_count >= 1)
    known_scores = sorted(
        c.score.value for c in comments if c.score.is_known and c.score.value is not None
    )
    unknown_reasons = Counter(
        c.score.unknown_reason.value for c in comments if c.score.unknown_reason is not None
    )
    authors = Counter(c.author for c in comments if c.author is not None)
    return {
        "comment_count": total,
        "length_chars": {
            "median": statistics.median(lengths),
            "p10": _percentile(lengths, 0.10),
            "p90": _percentile(lengths, 0.90),
        },
        "share_first_person": _share(
            sum(1 for c in comments if _FIRST_PERSON.search(c.body)), total
        ),
        "share_question": _share(sum(1 for c in comments if "?" in c.body), total),
        "share_link": _share(sum(1 for c in comments if _LINK.search(c.body)), total),
        "share_code": _share(sum(1 for c in comments if _CODE.search(c.body)), total),
        "share_top_level": _share(sum(1 for c in comments if c.depth == 0), total),
        "depth_max": max(c.depth for c in comments),
        "replies": {
            "share_with_any_reply": _share(any_reply, total),
            "with_3_or_more": len(replied),
            "median_length_with_3_or_more": (
                statistics.median(len(c.body) for c in replied) if replied else None
            ),
            "share_first_person_with_3_or_more": (
                _share(sum(1 for c in replied if _FIRST_PERSON.search(c.body)), len(replied))
                if replied
                else None
            ),
        },
        "score": {
            "known": len(known_scores),
            "median_known": statistics.median(known_scores) if known_scores else None,
            "unknown_reason": unknown_reasons.most_common(1)[0][0] if unknown_reasons else None,
        },
        "authors": {
            "distinct": len(authors),
            "top_author_share": _share(authors.most_common(1)[0][1], total) if authors else 0.0,
        },
    }


def voice_sample(comments: Sequence[CommentVoice]) -> list[CommentVoice]:
    """The comments the model reads: the most replied-to first (up to `VOICE_SAMPLE_REPLIED`), then
    an evenly spaced pick across the rest of the window, so the sample spans the week rather than
    the last hour. Deterministic for a given input."""
    replied = sorted(
        (c for c in comments if c.reply_count >= 1),
        key=lambda c: (-c.reply_count, c.created_at, c.external_id),
    )[:VOICE_SAMPLE_REPLIED]
    chosen = {c.external_id for c in replied}
    rest = sorted(
        (c for c in comments if c.external_id not in chosen),
        key=lambda c: (c.created_at, c.external_id),
    )
    slots = VOICE_SAMPLE_LIMIT - len(replied)
    picks: list[CommentVoice] = []
    if rest and slots > 0:
        step = len(rest) / min(slots, len(rest))
        seen: set[int] = set()
        for i in range(min(slots, len(rest))):
            index = min(len(rest) - 1, int(i * step))
            if index not in seen:
                seen.add(index)
                picks.append(rest[index])
    return replied + picks


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def voice_input(
    *,
    community_display_name: str,
    platform: str,
    window_start: datetime,
    window_end: datetime,
    metrics: Mapping[str, Any],
    sample: Sequence[CommentVoice],
    previous_card: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The input of the `community_voice` prompt."""
    return {
        "community": {"name": community_display_name, "platform": platform},
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "metrics": dict(metrics),
        "sample_size": len(sample),
        "previous_card": dict(previous_card) if previous_card else None,
        "sample": [
            {
                "id": c.external_id,
                "thread": c.thread_title,
                "depth": c.depth,
                "replies": c.reply_count,
                "score": metric_value(c.score),
                "author": c.author if c.author is not None else "[deleted]",
                "body": _clip(c.body, VOICE_BODY_CHARS),
            }
            for c in sample
        ],
    }


def _ids_list() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _observations() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["observation", "comment_ids"],
            "properties": {"observation": {"type": "string"}, "comment_ids": _ids_list()},
        },
    }


VOICE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "how_people_write",
        "what_gets_replies",
        "what_gets_ignored",
        "write_like_this",
        "avoid",
        "example_quotes",
    ],
    "properties": {
        "how_people_write": {"type": "string"},
        "what_gets_replies": _observations(),
        "what_gets_ignored": _observations(),
        "write_like_this": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "example_quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["comment_id", "why"],
                "properties": {"comment_id": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
}

_OBSERVATION_SECTIONS: tuple[str, ...] = ("what_gets_replies", "what_gets_ignored")
_RULE_SECTIONS: tuple[str, ...] = ("write_like_this", "avoid")


class VoiceRejected(Exception):
    """The card is not usable at all: a required key is missing or the summary is empty."""


@dataclass(frozen=True)
class ValidatedVoice:
    content: dict[str, Any]
    invalid_quotes: tuple[str, ...]
    dropped_items: int
    long_dash_count: int

    @property
    def invalid_quote_count(self) -> int:
        return len(self.invalid_quotes)


def validate_voice(output: Mapping[str, Any], known_ids: Iterable[str]) -> ValidatedVoice:
    """Keep only observations and quotes whose every `comment_id` is in the sample; count the
    rest; rewrite long dashes. An observation citing a comment the model never saw is not an
    observation with less evidence, it is an invented one, so it is dropped whole."""
    known = set(known_ids)
    for key in VOICE_OUTPUT_SCHEMA["required"]:
        if key not in output:
            raise VoiceRejected(f"karta głosu bez klucza {key!r}")
    summary = output["how_people_write"]
    if not isinstance(summary, str) or not summary.strip():
        raise VoiceRejected("karta głosu bez `how_people_write`")

    content: dict[str, Any] = {"how_people_write": summary}
    invalid: list[str] = []
    dropped = 0
    for section in _OBSERVATION_SECTIONS:
        kept = []
        for item in output.get(section) or []:
            ids = item.get("comment_ids") if isinstance(item, Mapping) else None
            text = item.get("observation") if isinstance(item, Mapping) else None
            if not isinstance(ids, list) or not isinstance(text, str) or not text.strip():
                dropped += 1
                continue
            bad = [str(i) for i in ids if i not in known]
            if bad or not ids:
                invalid.extend(bad)
                dropped += 1
                continue
            kept.append({"observation": text, "comment_ids": [str(i) for i in ids]})
        content[section] = kept
    for section in _RULE_SECTIONS:
        rules = output.get(section) or []
        kept_rules = [r for r in rules if isinstance(r, str) and r.strip()]
        dropped += len(rules) - len(kept_rules)
        content[section] = kept_rules
    quotes = []
    for item in output.get("example_quotes") or []:
        comment_id = item.get("comment_id") if isinstance(item, Mapping) else None
        why = item.get("why") if isinstance(item, Mapping) else None
        if comment_id in known and isinstance(why, str):
            quotes.append({"comment_id": str(comment_id), "why": why})
        else:
            invalid.append(str(comment_id))
            dropped += 1
    content["example_quotes"] = quotes
    content, long_dashes = clean_payload(content)
    return ValidatedVoice(
        content=content,
        invalid_quotes=tuple(invalid),
        dropped_items=dropped,
        long_dash_count=long_dashes,
    )
