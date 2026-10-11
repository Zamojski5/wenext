"""What the model sees, and how what it says back is checked — pure functions, no I/O.

Two directions, each with a rule that the store cannot enforce:

* **Inbound** (`picture_input`, `judge_input`): the control plane compiles the context. The
  model gets threads, comments, metrics, the user's profile and the previous picture as DATA,
  truncated by the constants below so a single 400-comment thread cannot crowd out the rest of
  the window. `UNKNOWN` metrics are passed as the literal string `"UNKNOWN"`, never as 0 or
  `null` (22.2 point 7 — the same rule as in `community_observer.models`).

* **Outbound** (`validate_picture`, `judgment_from_output`): the model's answer is checked
  against what it was shown. A picture that cites a `thread_ref` not in the window is counted
  in `invalid_ref_count` and the offending observations are dropped — a claim without evidence
  is exactly what `docs/KIERUNEK-PRODUKTU.md` §6 says the product must not store. A judgment is
  built through `JudgmentRecord`, so an `ENGAGE` that cannot name its contribution is refused
  here by the contracts validator and, one layer down, by the database CHECK.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from community_os_contracts import JudgmentDecision, JudgmentRecord, Observed
from pydantic import ValidationError

from .text_rules import clean_payload

_LOG = logging.getLogger(__name__)

#: Truncation limits. Not thresholds of any decision — the size of what the model reads.
PICTURE_MAX_THREADS = 80
PICTURE_BODY_CHARS = 600
PICTURE_COMMENTS_PER_THREAD = 5
PICTURE_COMMENT_CHARS = 300
JUDGE_BODY_CHARS = 2000
JUDGE_COMMENTS = 15
JUDGE_COMMENT_CHARS = 500

UNKNOWN = "UNKNOWN"


def metric_value(observed: Observed[int]) -> int | str:
    """A known metric as a number, an unknown one as the literal `"UNKNOWN"`. Never 0 or null."""
    return observed.value if observed.is_known and observed.value is not None else UNKNOWN


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class CommentContext:
    external_id: str
    parent_external_id: str | None
    author: str | None
    body: str | None
    created_at: datetime
    depth: int
    #: Points where the platform shows them (Reddit), a reason where it does not (HN). 0005.
    score: Observed[int]


@dataclass(frozen=True)
class ThreadContext:
    """One thread as the model will see it. Built by the store from rows, not from the source."""

    ref: str
    external_id: str
    url: str
    link_url: str | None
    title: str
    body: str | None
    author: str | None
    created_at: datetime
    score: Observed[int]
    comment_count: Observed[int]
    comments: tuple[CommentContext, ...]


def _comment_payload(comment: CommentContext, chars: int) -> dict[str, Any]:
    return {
        "id": comment.external_id,
        "parent_id": comment.parent_external_id,
        "author": comment.author if comment.author is not None else "[deleted]",
        "body": _clip(comment.body, chars) if comment.body is not None else "[deleted]",
        "created_at": comment.created_at.isoformat(),
        "depth": comment.depth,
        "score": metric_value(comment.score),
    }


def picture_input(
    *,
    community_display_name: str,
    platform: str,
    window_start: datetime,
    window_end: datetime,
    threads: Sequence[ThreadContext],
    previous_picture: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The input of `community_picture`. Threads newest first, capped, comments sampled."""
    ordered = sorted(threads, key=lambda t: t.created_at, reverse=True)[:PICTURE_MAX_THREADS]
    return {
        "community": {"name": community_display_name, "platform": platform},
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "thread_count_in_window": len(threads),
        "threads_included": len(ordered),
        "previous_picture": dict(previous_picture) if previous_picture else None,
        "threads": [
            {
                "ref": t.ref,
                "title": t.title,
                "url": t.url,
                "link_url": t.link_url,
                "author": t.author,
                "created_at": t.created_at.isoformat(),
                "score": metric_value(t.score),
                "comment_count": metric_value(t.comment_count),
                "body": _clip(t.body, PICTURE_BODY_CHARS),
                "comments_sample": [
                    _comment_payload(c, PICTURE_COMMENT_CHARS)
                    for c in t.comments[:PICTURE_COMMENTS_PER_THREAD]
                ],
                "comments_total_observed": len(t.comments),
            }
            for t in ordered
        ],
    }


def picture_excerpt(picture: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The part of a picture a judge needs: culture and current state. Not the whole thing."""
    if not picture:
        return None
    return {
        "durable_culture": list(picture.get("durable_culture") or []),
        "current_state": list(picture.get("current_state") or []),
        "what_gets_conversation": picture.get("what_gets_conversation"),
        "what_gets_ignored": picture.get("what_gets_ignored"),
    }


def judge_input(
    *,
    profile: Mapping[str, str],
    community_display_name: str,
    platform: str,
    picture: Mapping[str, Any] | None,
    thread: ThreadContext,
    now: datetime,
) -> dict[str, Any]:
    """The input of `judge_thread`. The profile is passed whole — it is four short fields."""
    return {
        "now": now.isoformat(),
        "user_profile": {
            "knows_deeply": profile["knows_deeply"],
            "positions": profile["positions"],
            "would_never_claim": profile["would_never_claim"],
            "voice_samples": profile.get("voice_samples") or "",
        },
        "community": {
            "name": community_display_name,
            "platform": platform,
            "what_we_learned": picture_excerpt(picture),
        },
        "thread": {
            "ref": thread.ref,
            "title": thread.title,
            "url": thread.url,
            "link_url": thread.link_url,
            "author": thread.author,
            "created_at": thread.created_at.isoformat(),
            "score": metric_value(thread.score),
            "comment_count": metric_value(thread.comment_count),
            "body": _clip(thread.body, JUDGE_BODY_CHARS),
            "comments": [
                _comment_payload(c, JUDGE_COMMENT_CHARS) for c in thread.comments[:JUDGE_COMMENTS]
            ],
            "comments_total_observed": len(thread.comments),
        },
    }


# ---------------------------------------------------------------------------
# Output schemas — structured outputs are a requirement (TurnSpec), not an option
# ---------------------------------------------------------------------------


def _refs_list() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


PICTURE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "topics",
        "recurring_problems",
        "what_gets_conversation",
        "what_gets_ignored",
        "durable_culture",
        "current_state",
        "where_user_could_contribute",
    ],
    "properties": {
        "summary": {"type": "string"},
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "why_it_matters", "thread_refs"],
                "properties": {
                    "name": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "thread_refs": _refs_list(),
                },
            },
        },
        "recurring_problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["problem", "thread_refs"],
                "properties": {"problem": {"type": "string"}, "thread_refs": _refs_list()},
            },
        },
        "what_gets_conversation": {"type": "string"},
        "what_gets_ignored": {"type": "string"},
        "durable_culture": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["observation", "confidence", "thread_refs"],
                "properties": {
                    "observation": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "thread_refs": _refs_list(),
                },
            },
        },
        "current_state": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["observation", "thread_refs"],
                "properties": {"observation": {"type": "string"}, "thread_refs": _refs_list()},
            },
        },
        "where_user_could_contribute": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["thread_ref", "why"],
                "properties": {"thread_ref": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
}

JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reasoning", "what_user_could_add", "confidence", "wait_until_hint"],
    "properties": {
        "decision": {"type": "string", "enum": [d.value for d in JudgmentDecision]},
        "reasoning": {"type": "string"},
        "what_user_could_add": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "wait_until_hint": {"type": ["string", "null"]},
    },
}

#: Sections of a picture whose items carry `thread_refs`, checked by `validate_picture`.
_REF_SECTIONS: tuple[str, ...] = (
    "topics",
    "recurring_problems",
    "durable_culture",
    "current_state",
)


class PictureRejected(Exception):
    """The picture is not usable at all — a required key is missing or of the wrong type."""


@dataclass(frozen=True)
class ValidatedPicture:
    content: dict[str, Any]
    invalid_refs: tuple[str, ...]
    dropped_items: int
    #: Long dashes (U+2014/U+2013) the model produced despite the prompt and `text_rules` rewrote
    #: before the content reached the store. Logged by the handler: a prompt that stops working
    #: shows up as a number, not as a surprise on the dashboard (founder rule 2026-09-04).
    long_dash_count: int = 0

    @property
    def invalid_ref_count(self) -> int:
        return len(self.invalid_refs)


def validate_picture(output: Mapping[str, Any], known_refs: Iterable[str]) -> ValidatedPicture:
    """Keep only observations whose every `thread_ref` exists in the window. Count the rest.

    An item citing an unknown ref is DROPPED — not kept with the bad ref removed — because an
    observation whose evidence was invented is not an observation with less evidence. The
    count survives on the picture row so a model that hallucinates refs is visible over time.
    """
    known = set(known_refs)
    for key in PICTURE_OUTPUT_SCHEMA["required"]:
        if key not in output:
            raise PictureRejected(f"obraz community bez klucza {key!r}")
    if not isinstance(output["summary"], str) or not output["summary"].strip():
        raise PictureRejected("obraz community bez `summary`")

    content: dict[str, Any] = dict(output)
    invalid: list[str] = []
    dropped = 0
    for section in _REF_SECTIONS:
        kept = []
        for item in output.get(section) or []:
            refs = item.get("thread_refs") if isinstance(item, Mapping) else None
            if not isinstance(refs, list):
                dropped += 1
                continue
            bad = [r for r in refs if r not in known]
            if bad or not refs:
                invalid.extend(bad)
                dropped += 1
                continue
            kept.append(dict(item))
        content[section] = kept
    kept_pointers = []
    for item in output.get("where_user_could_contribute") or []:
        ref = item.get("thread_ref") if isinstance(item, Mapping) else None
        if ref in known:
            kept_pointers.append(dict(item))
        else:
            invalid.append(str(ref))
            dropped += 1
    content["where_user_could_contribute"] = kept_pointers
    content, long_dashes = clean_payload(content)
    return ValidatedPicture(
        content=content,
        invalid_refs=tuple(invalid),
        dropped_items=dropped,
        long_dash_count=long_dashes,
    )


class JudgmentRejected(Exception):
    """The model's judgment violates the contract — usually an `ENGAGE` without a contribution."""


def judgment_from_output(
    output: Mapping[str, Any],
    *,
    thread_ref: str,
    user_profile_version: int,
    prompt_name: str,
    prompt_sha256: str,
    model: str,
    effort: str,
    created_at: datetime,
) -> JudgmentRecord:
    """Build the contract record from the model's JSON. The contract decides what is acceptable.

    Text fields pass through `text_rules.clean_payload` first (founder rule 2026-09-04: no long
    dashes in anything the model writes); a non-zero count is logged, because the prompt should
    have made it zero.
    """
    output, long_dashes = clean_payload(dict(output))
    if long_dashes:
        _LOG.warning(
            "osąd %s: model użył %d długich pauz mimo reguły w prompcie %s — przepisane",
            thread_ref,
            long_dashes,
            prompt_name,
        )
    try:
        decision = JudgmentDecision(str(output.get("decision")))
    except ValueError as exc:
        raise JudgmentRejected(
            f"decision={output.get('decision')!r} nie jest ENGAGE/IGNORE/WAIT"
        ) from exc
    contribution = output.get("what_user_could_add")
    if isinstance(contribution, str) and not contribution.strip():
        contribution = None
    try:
        return JudgmentRecord(
            thread_ref=thread_ref,
            decision=decision,
            reasoning=str(output.get("reasoning") or ""),
            what_user_could_add=contribution if isinstance(contribution, str) else None,
            confidence=float(output.get("confidence", 0.0) or 0.0),
            user_profile_version=user_profile_version,
            prompt_name=prompt_name,
            prompt_sha256=prompt_sha256,
            model=model,
            effort=effort,
            created_at=created_at,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise JudgmentRejected(f"osąd modelu odrzucony przez kontrakt: {exc}") from exc
