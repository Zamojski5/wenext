"""The baseline reaction generator: one moment of one conversation in, one reaction out (R1).

`docs/ROADMAP-JAKOSC-ROZMOW.md` R1, `docs/FABLE-5.1-IMPLEMENTACJA.md`: the eval and the product
must call THE SAME generating code through THE SAME port. This module is that code. The
conversation evals (`evals/conversations/runner.py`) call `generate_reaction` with a
`FakeTurnRunner` offline and a `CodexTurnRunner` live; a future handler calls it with the
process's `ModelTurnPort` and the same signature. Nothing here reads the database or the
platform: the caller compiles the situation, exactly as `context.py` does for the judge.

What the generator decides, in this order: the KIND of reaction first (`ReactionKind`, a closed
list of eight), then the text, if the kind has one. Two rules of the founder's, written into the
validator rather than left to the prompt:

1. **A new entry into somebody else's thread needs a concrete contribution.** For `NEW_ENTRY`
   with a text kind the model has to name, in `contribution`, what the draft adds that the thread
   does not have, and the sentence has to clear `MIN_CONTRIBUTION_CHARS` from the judgment
   contract. Reused, not redefined: the judge's `what_user_could_add` and this field are the same
   idea at two moments. `SHORT_ACK` and `HUMOR` are not available to a new entry at all: a joke or a
   "nice" as the account's first words in a stranger's thread is the slop §4 names.
2. **A continuation may be short.** In `OWN_CONTINUATION` and `KNOWN_INTERLOCUTOR` a
   `SHORT_ACK` or a `HUMOR` line is a full result with no contribution requirement; it still
   names the message it answers, and the rationale says which relation earns it.

`WAIT` and `SILENCE` carry no draft and are complete results, not failures. A model output that
breaks these rules is `ReactionRejected`: the eval counts it as a rejected generation and the
product produces no draft. Text fields pass `text_rules.clean_payload` (founder rule 2026-09-04,
no long dashes) and the count is on the result.

Neither `JudgmentRecord` nor `MIN_CONTRIBUTION_CHARS` changes here (brief of 2026-09-06). Whether
`JudgmentRecord` should grow a reaction kind for continuations is recorded as an open question in
`docs/STAN-JAKOSCI-ROZMOW.md`, not decided by this file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from community_os_contracts import ReactionKind, ThreadRole
from community_os_contracts.judgment import MIN_CONTRIBUTION_CHARS

from . import prompts
from .text_rules import clean_payload
from .turns import ModelTurnPort

#: The prompt this generator runs. Versioned in `prompts/PROMPTS.lock` like the others.
PROMPT_NAME = "reaction_baseline"

#: Kinds that carry a draft. The other two are results without text.
TEXT_KINDS: frozenset[ReactionKind] = frozenset(
    {
        ReactionKind.HELP,
        ReactionKind.DIAGNOSE,
        ReactionKind.CLARIFY,
        ReactionKind.DISAGREE,
        ReactionKind.SHORT_ACK,
        ReactionKind.HUMOR,
    }
)
#: Kinds a NEW entry into a thread may not use: they presuppose an exchange with the account.
CONTINUATION_ONLY_KINDS: frozenset[ReactionKind] = frozenset(
    {ReactionKind.SHORT_ACK, ReactionKind.HUMOR}
)
#: Kinds that require `contribution` when the relation is a new entry.
CONTRIBUTION_KINDS: frozenset[ReactionKind] = frozenset(
    {ReactionKind.HELP, ReactionKind.DIAGNOSE, ReactionKind.CLARIFY, ReactionKind.DISAGREE}
)
#: The one thing a draft is built around (prompt v3, 2026-09-13): what the community's upvoted
#: comments do instead of summarising. `NONE` for reactions without a draft. Closed list, stored
#: on the reaction so outcomes can be read per move.
MOVE_NONE = "NONE"
MOVES: tuple[str, ...] = (
    "CLAIM_WITH_LIMIT",
    "COUNTERPOINT",
    "FIRST_HAND",
    "IMAGE",
    "NUMBER",
    # D-031 (prompt v5): two of the four highest-scoring patterns measured on 2026-09-13 that v3
    # had no name for: dry irony at the situation, and one sentence with one strong opinion.
    "IRONY",
    "ONE_LINER",
    MOVE_NONE,
)

RELATION_NEW_ENTRY = "NEW_ENTRY"
RELATION_OWN_CONTINUATION = "OWN_CONTINUATION"
RELATION_KNOWN_INTERLOCUTOR = "KNOWN_INTERLOCUTOR"
RELATIONS: frozenset[str] = frozenset(
    {RELATION_NEW_ENTRY, RELATION_OWN_CONTINUATION, RELATION_KNOWN_INTERLOCUTOR}
)

#: How much of a message the model sees. Long posts are clipped, never dropped.
MESSAGE_CHARS = 2500
#: How many transcript messages the model sees: the newest ones, the target always included.
MAX_MESSAGES = 60


@dataclass(frozen=True)
class SituationMessage:
    message_id: str
    parent_id: str | None
    author: str | None
    body: str
    created_at: datetime
    #: Written by the represented account.
    own: bool = False


@dataclass(frozen=True)
class ReactionSituation:
    """One moment of one conversation, as the generator sees it. Built by the caller (the eval
    from an episode input, a handler from the store); no reference material, nothing later than
    `captured_at`."""

    situation_id: str
    platform: str
    community_display_name: str
    #: ISO 639-1: the language of the reaction.
    language: str
    captured_at: datetime
    transcript: tuple[SituationMessage, ...]
    target_message_id: str
    relation: str
    relation_evidence: str
    own_message_ids: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    #: The account's most recent drafts (first sentences), newest first: a short memory so the
    #: same thesis or phrase is not produced again and again (pilot brief, point 2). Never a rule
    #: about length, jokes or questions.
    recent_own_drafts: tuple[str, ...] = ()
    #: The community's most upvoted comments, as written (prompt v3): the register that gets read
    #: here. Never a source of facts; the prompt forbids imitating them word for word.
    community_exemplars: tuple[str, ...] = ()
    #: What this account knows about the community, as data the system keeps up to date, never as
    #: instructions (D-036). `community_voice_card`: what readers here punish and how they write,
    #: measured on their own comments. `community_move_outcomes`: what this account's published
    #: comments earned here, per move, so the model can see what worked rather than be told.
    community_voice_card: Mapping[str, Any] | None = None
    community_move_outcomes: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.relation not in RELATIONS:
            raise ValueError(f"relation {self.relation!r} spoza {sorted(RELATIONS)}")
        ids = {m.message_id for m in self.transcript}
        if self.target_message_id not in ids:
            raise ValueError(
                f"target_message_id {self.target_message_id!r} nie jest w transkrypcie"
            )
        for message in self.transcript:
            if message.created_at > self.captured_at:
                raise ValueError(
                    f"message {message.message_id} jest po captured_at: przyszłość w sytuacji"
                )

    @property
    def target(self) -> SituationMessage:
        return next(m for m in self.transcript if m.message_id == self.target_message_id)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def neutral_authors(situation: ReactionSituation) -> dict[str, str]:
    """Real author → `author_1`, `author_2`, … in order of first appearance. Handles are not
    the model's business (a name can carry a label, and a real username has no place in a
    prompt); who wrote the root and who came back is still visible through the numbering."""
    ordered = sorted(situation.transcript, key=lambda m: (m.created_at, m.message_id))
    alias: dict[str, str] = {}
    for message in ordered:
        if message.own or message.author is None:
            continue
        alias.setdefault(message.author, f"author_{len(alias) + 1}")
    return alias


def neutral_ids(situation: ReactionSituation) -> dict[str, str]:
    """Real message id → neutral id (`m1`, `m2`, … in time order). The model never sees a real
    id: an id can carry a label (`…-post-silence`, `syn-…`) and a label in the input is the answer
    key in disguise (review 2026-09-06). `reaction_from_output` maps the answer back."""
    ordered = sorted(situation.transcript, key=lambda m: (m.created_at, m.message_id))
    return {m.message_id: f"m{i}" for i, m in enumerate(ordered, start=1)}


def reaction_input(
    situation: ReactionSituation, *, profile: Mapping[str, str], now: datetime
) -> dict[str, Any]:
    """The `user_input` of the reaction prompt. The profile is passed whole (four short fields),
    the transcript newest-`MAX_MESSAGES` with the target always present, message ids neutralised,
    the account's own messages authored as `(this account)`, other authors as `author_N`. No
    situation id, no relation evidence, no handles, no tags, no split: nothing that could name the
    expected reaction."""
    alias = neutral_ids(situation)
    authors = neutral_authors(situation)
    ordered = sorted(situation.transcript, key=lambda m: (m.created_at, m.message_id))
    if len(ordered) > MAX_MESSAGES:
        kept = ordered[-MAX_MESSAGES:]
        if all(m.message_id != situation.target_message_id for m in kept):
            kept = [situation.target, *kept[1:]]
        ordered = kept
    return {
        "now": now.isoformat(),
        "user_profile": {
            "knows_deeply": profile["knows_deeply"],
            "positions": profile["positions"],
            "would_never_claim": profile["would_never_claim"],
            "voice_samples": profile.get("voice_samples") or "",
        },
        "community": {
            "name": situation.community_display_name,
            "platform": situation.platform,
            "language": situation.language,
            "exemplars": list(situation.community_exemplars),
            "voice_card": dict(situation.community_voice_card or {}),
            "move_outcomes": [dict(o) for o in situation.community_move_outcomes],
        },
        "situation": {
            "captured_at": situation.captured_at.isoformat(),
            # The relation's evidence is for the reviewer and the validator, not for the model:
            # free text written by whoever built the episode can carry a label (review 2026-09-06).
            "relation": {
                "state": situation.relation,
                "own_message_ids": [alias[i] for i in situation.own_message_ids if i in alias],
            },
            "target_message_id": alias[situation.target_message_id],
            "materials": list(situation.materials),
            "account_recent_drafts": list(situation.recent_own_drafts),
            "transcript": [
                {
                    "message_id": alias[m.message_id],
                    "parent_id": alias.get(m.parent_id) if m.parent_id is not None else None,
                    "author": "(this account)"
                    if m.own
                    else (authors[m.author] if m.author is not None else "[deleted]"),
                    "own": m.own,
                    "created_at": m.created_at.isoformat(),
                    "body": _clip(m.body, MESSAGE_CHARS),
                }
                for m in ordered
            ],
            "transcript_total": len(situation.transcript),
        },
    }


REACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "move",
        "draft",
        "target_message_id",
        "contribution",
        "brief_rationale",
        "sources_used",
        "confidence",
    ],
    "properties": {
        "decision": {"type": "string", "enum": [k.value for k in ReactionKind]},
        "move": {"type": "string", "enum": list(MOVES)},
        "draft": {"type": ["string", "null"]},
        "target_message_id": {"type": "string"},
        "contribution": {"type": ["string", "null"]},
        "brief_rationale": {"type": "string"},
        "sources_used": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


class ReactionRejected(Exception):
    """The model's output breaks the reaction contract. The eval counts it; the product drafts
    nothing. The message names the rule."""


@dataclass(frozen=True)
class Reaction:
    decision: ReactionKind
    draft: str | None
    target_message_id: str
    contribution: str | None
    brief_rationale: str
    sources_used: tuple[str, ...]
    confidence: float
    #: Long dashes the model produced and `text_rules` rewrote (founder rule 2026-09-04).
    long_dash_count: int
    #: One of `MOVES` (prompt v3); `NONE` when there is no draft or the model predates v3.
    move: str = MOVE_NONE

    @property
    def has_text(self) -> bool:
        return self.draft is not None


@dataclass(frozen=True)
class ReactionResult:
    """A reaction with the identity every record derived from it must carry."""

    situation_id: str
    reaction: Reaction
    model: str
    effort: str
    prompt_name: str
    prompt_sha256: str
    generated_at: datetime


def reaction_from_output(output: Mapping[str, Any], *, situation: ReactionSituation) -> Reaction:
    """Validate the model's JSON against the contract in the module docstring."""
    cleaned, long_dashes = clean_payload(dict(output))
    try:
        decision = ReactionKind(str(cleaned.get("decision")))
    except ValueError as exc:
        raise ReactionRejected(
            f"decision={cleaned.get('decision')!r} spoza listy {[k.value for k in ReactionKind]}"
        ) from exc

    draft = cleaned.get("draft")
    if isinstance(draft, str) and not draft.strip():
        draft = None
    if decision in TEXT_KINDS and not isinstance(draft, str):
        raise ReactionRejected(f"{decision.value} wymaga tekstu w `draft`")
    if decision not in TEXT_KINDS and draft is not None:
        raise ReactionRejected(f"{decision.value} nie ma tekstu; `draft` musi być null")

    alias_back = {neutral: real for real, neutral in neutral_ids(situation).items()}
    answered = cleaned.get("target_message_id")
    target = alias_back.get(answered) if isinstance(answered, str) else None
    if target is None:
        raise ReactionRejected(f"target_message_id={answered!r} nie jest w transkrypcie")
    target_message = next(m for m in situation.transcript if m.message_id == target)
    if decision in TEXT_KINDS and target_message.own:
        raise ReactionRejected(
            f"{decision.value} odpowiada na własną wiadomość konta {target!r}: konto nie "
            "odpowiada samo sobie"
        )

    new_entry = situation.relation == RELATION_NEW_ENTRY
    if new_entry and decision in CONTINUATION_ONLY_KINDS:
        raise ReactionRejected(
            f"{decision.value} w nowym wejściu do cudzego wątku: krótkie potwierdzenie i żart "
            "wymagają istniejącej rozmowy z kontem (relacja OWN_CONTINUATION albo "
            "KNOWN_INTERLOCUTOR)"
        )
    contribution = cleaned.get("contribution")
    if isinstance(contribution, str) and not contribution.strip():
        contribution = None
    if (
        new_entry
        and decision in CONTRIBUTION_KINDS
        and (
            not isinstance(contribution, str) or len(contribution.strip()) < MIN_CONTRIBUTION_CHARS
        )
    ):
        raise ReactionRejected(
            f"nowe wejście z {decision.value} bez konkretnego `contribution` (minimum "
            f"{MIN_CONTRIBUTION_CHARS} znaków, jest "
            f"{len((contribution or '').strip())}): wkład, którego nie da się nazwać, to slop"
        )

    rationale = cleaned.get("brief_rationale")
    if not isinstance(rationale, str) or len(rationale.strip()) < 10:
        raise ReactionRejected(
            "brief_rationale musi być krótkim uzasadnieniem (co najmniej 10 znaków)"
        )

    sources = cleaned.get("sources_used")
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise ReactionRejected("sources_used musi być listą identyfikatorów")
    unknown_sources = [s for s in sources if s not in situation.materials]
    if unknown_sources:
        raise ReactionRejected(f"sources_used wskazuje materiały spoza sytuacji: {unknown_sources}")

    try:
        confidence = float(cleaned.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ReactionRejected(f"confidence={cleaned.get('confidence')!r} nie jest liczbą") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ReactionRejected(f"confidence={confidence} poza [0, 1]")

    move = cleaned.get("move")
    if move is None:
        move = MOVE_NONE  # an output from before prompt v3 (recorded evals) carries no move
    if not isinstance(move, str) or move not in MOVES:
        raise ReactionRejected(f"move={move!r} spoza listy {list(MOVES)}")
    if not isinstance(draft, str):
        move = MOVE_NONE  # no text, no move: the field describes the draft, not the decision

    return Reaction(
        decision=decision,
        draft=draft if isinstance(draft, str) else None,
        target_message_id=target,
        contribution=contribution if isinstance(contribution, str) else None,
        brief_rationale=rationale.strip(),
        sources_used=tuple(sources),
        confidence=confidence,
        long_dash_count=long_dashes,
        move=move,
    )


async def generate_reaction(
    model: ModelTurnPort,
    *,
    situation: ReactionSituation,
    profile: Mapping[str, str],
    now: datetime,
) -> ReactionResult:
    """One reaction for one situation through the product's model port.

    Raises `ModelUnavailable` / `TurnFailed` from the port unchanged (the caller parks or
    records), and `ReactionRejected` when the model's answer breaks the contract.
    """
    prompt = prompts.get(PROMPT_NAME)
    outcome = await model.run(
        role=prompt.role,
        instructions=prompt.text,
        user_input=json.dumps(
            reaction_input(situation, profile=profile, now=now), ensure_ascii=False, sort_keys=True
        ),
        output_schema=REACTION_OUTPUT_SCHEMA,
    )
    reaction = reaction_from_output(outcome.structured_output, situation=situation)
    return ReactionResult(
        situation_id=situation.situation_id,
        reaction=reaction,
        model=outcome.model,
        effort=outcome.effort,
        prompt_name=prompt.name,
        prompt_sha256=prompt.sha256,
        generated_at=now,
    )


def reaction_role() -> ThreadRole:
    """Which route the reaction prompt runs on (pinned by the prompt header: WORKER)."""
    return prompts.get(PROMPT_NAME).role


def situation_from_mapping(raw: Mapping[str, Any]) -> ReactionSituation:
    """A `ReactionSituation` from plain data (the eval's episode input, a job payload later).
    Only the fields the generator may see are read; anything else in `raw` is ignored, which is
    the second guard (after the episode schema) against a reference leaking in."""
    community = raw["community"]
    relation = raw["relation"]
    return ReactionSituation(
        situation_id=str(raw["id"]),
        platform=str(raw["source"]["platform"]),
        community_display_name=str(community["display_name"]),
        language=str(community["language"]),
        captured_at=_parse_time(raw["captured_at"]),
        transcript=tuple(
            SituationMessage(
                message_id=str(m["message_id"]),
                parent_id=m.get("parent_id"),
                author=m.get("author"),
                body=str(m["body"]),
                created_at=_parse_time(m["created_at"]),
                own=bool(m.get("own", False)),
            )
            for m in raw["transcript"]
        ),
        target_message_id=str(raw["target_message_id"]),
        relation=str(relation["state"]),
        relation_evidence=str(relation["evidence"]),
        own_message_ids=tuple(str(i) for i in relation.get("own_message_ids") or ()),
        materials=tuple(str(i) for i in raw.get("materials") or ()),
        recent_own_drafts=tuple(str(d) for d in raw.get("recent_own_drafts") or ()),
        community_exemplars=tuple(str(e) for e in raw.get("community_exemplars") or ()),
        community_voice_card=(
            dict(raw["community_voice_card"]) if raw.get("community_voice_card") else None
        ),
        community_move_outcomes=tuple(dict(o) for o in raw.get("community_move_outcomes") or ()),
    )


def _parse_time(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError(f"czas {value!r} bez strefy")
    return moment


__all__: Sequence[str] = (
    "CONTINUATION_ONLY_KINDS",
    "CONTRIBUTION_KINDS",
    "MOVES",
    "MOVE_NONE",
    "PROMPT_NAME",
    "REACTION_OUTPUT_SCHEMA",
    "TEXT_KINDS",
    "Reaction",
    "ReactionRejected",
    "ReactionResult",
    "ReactionSituation",
    "SituationMessage",
    "generate_reaction",
    "neutral_authors",
    "neutral_ids",
    "reaction_from_output",
    "reaction_input",
    "reaction_role",
    "situation_from_mapping",
)
