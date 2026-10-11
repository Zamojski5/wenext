"""The episode contract of the conversation evals (R1, `docs/ROADMAP-JAKOSC-ROZMOW.md`).

One episode is one moment in one real conversation: what was visible then, who the account is,
what relation it has to the people in the thread, and which kinds of reaction would have been
acceptable. Separately, and hidden from the generator, what a reviewer considers a good reaction
and why.

THE ONE RULE THIS FILE EXISTS TO ENFORCE. The generator sees `EpisodeInput` and nothing else.
The reference part (`EpisodeReference`: the exemplary reaction, the reviewer's explanation, the
messages that came LATER) never reaches it: `EpisodeInput` forbids extra fields, so a full
`Episode` passed by mistake fails validation instead of leaking; `Episode.for_generator()` is
the only sanctioned way to get the input; `tests/test_conversations_schema.py` asserts that no
hidden field name appears in the input's schema and that a serialized input carries no reference
text. A model that has seen the answer key is not being measured.

Other rules, each with its reason:

* **Time is frozen at `captured_at`.** Every message in the transcript is dated at or before it,
  every later message is dated after it. An episode built from a finished thread must not show
  the generator a reply that had not happened yet.
* **The persona is a reference, not a copy.** `PersonaRef` names a profile label and version;
  the runner resolves it (from `user_profile_versions` live, from `personas/` offline). Copying
  the four profile fields into every episode would let 40 episodes drift into 40 profiles.
* **The relation carries evidence.** "This is a continuation of the account's own conversation"
  is only claimable when the transcript contains the account's own messages; "known
  interlocutor" needs a stated source. New entries have no own messages. Two different
  criteria apply downstream (a new entry needs a concrete contribution, a continuation may be a
  short acknowledgement), so the label must be earned, not asserted.
* **`synthetic` is a flag, not a folder.** Demonstration episodes written by an agent are marked
  and counted separately; they never stand in for real cases or human judgment.
* **Splits are per episode, whole threads together.** The loader refuses two episodes of the
  same thread in different splits: a later message of a dev episode would be the future of a
  holdout episode.

The reviewer's answers follow the project's review materials: the author of a reference
is the operator, another person or a model, and its status is a candidate until the operator has looked
at it. A model's opinion is recorded as a model's opinion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from community_os_contracts import ObservationPlatform, ReactionKind
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: Episode ids: lowercase, digits and hyphens, e.g. `hn-ask-46012345-c7`, `synthetic-thanks-001`.
EPISODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
LANGUAGE = re.compile(r"^[a-z]{2}$")

#: Field names that belong to the hidden part. The schema test asserts none of them appears in
#: `EpisodeInput.model_json_schema()`; the loader refuses an input file that names any of them.
HIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "reference",
        "reaction",
        "text",
        "rationale",
        "author_of_reference",
        "later_messages",
        "outcome_note",
        "status",
        "could_also",
    }
)

#: Where the loader looks by default: real episodes and synthetic demonstrations, side by side,
#: each file one `Episode`. The flag inside the file decides, not the directory.
DEFAULT_EPISODES_DIR = Path(__file__).resolve().parent / "episodes"
DEFAULT_PERSONAS_DIR = Path(__file__).resolve().parent / "personas"
#: Holdout references live apart from their episodes (`references/holdout/<id>.json`, shape
#: `{"episode_id", "reference"}`): the visible run and the rating must not share a file
#: (`docs/ROADMAP-JAKOSC-ROZMOW.md`, R1). The loader re-attaches them so the pair validators run.
DEFAULT_REFERENCES_DIR = Path(__file__).resolve().parent / "references"


class Split(StrEnum):
    DEV = "DEV"
    HOLDOUT = "HOLDOUT"


class RelationState(StrEnum):
    """The account's relation to this moment. Different criteria apply to each downstream."""

    #: The account has not spoken in this thread. A reaction must carry a concrete contribution.
    NEW_ENTRY = "NEW_ENTRY"
    #: Somebody replied to the account's own earlier message in this thread.
    OWN_CONTINUATION = "OWN_CONTINUATION"
    #: The account has talked with this person before, elsewhere; `evidence` says where.
    KNOWN_INTERLOCUTOR = "KNOWN_INTERLOCUTOR"


class ReferenceAuthor(StrEnum):
    OPERATOR = "OPERATOR"
    OTHER_PERSON = "OTHER_PERSON"
    MODEL = "MODEL"


class ReferenceStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    REVIEWED_BY_OPERATOR = "REVIEWED_BY_OPERATOR"


def _aware(moment: datetime, what: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{what} musi mieć strefę czasową (aware datetime)")


class EpisodeMessage(BaseModel):
    """One message as it was visible: id, tree position, author, text, time. `own` marks the
    represented account's messages; `author` is `None` for a deleted or hidden author."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(min_length=1)
    parent_id: str | None = None
    author: str | None = None
    body: str
    created_at: datetime
    own: bool = False

    @model_validator(mode="after")
    def _time_is_aware(self) -> EpisodeMessage:
        _aware(self.created_at, f"message {self.message_id}: created_at")
        return self


class EpisodeSource(BaseModel):
    """Where the moment comes from. `external_id` and `url` point at the thread on the platform;
    `thread_ref` is the observer's `PLATFORM:community:external_id` when the thread is in the
    database (it may not be: an episode can be captured by hand)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: ObservationPlatform
    external_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    thread_ref: str | None = None

    @model_validator(mode="after")
    def _url_is_http(self) -> EpisodeSource:
        if not (self.url.startswith("https://") or self.url.startswith("http://")):
            raise ValueError(f"source.url {self.url!r} nie jest adresem http(s)")
        return self


class EpisodeCommunity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: str = Field(min_length=1)
    key: str = Field(min_length=1)
    #: ISO 639-1, lowercase: the language the reaction is written in.
    language: str = Field(pattern=LANGUAGE.pattern)


class PersonaRef(BaseModel):
    """A reference to a profile version, never its text. `label` names the person represented
    (`operator` for the real profile, `synthetic-*` for demonstration personas)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    profile_version: int = Field(ge=1)


class Relation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: RelationState
    #: Why this state: message ids, a link to the earlier conversation, or "no own messages".
    evidence: str = Field(min_length=1)
    #: The account's own messages in THIS transcript that the continuation refers to.
    own_message_ids: list[str] = Field(default_factory=list)


class EpisodeInput(BaseModel):
    """Everything the generator may see. Extra fields are refused: that is how a whole `Episode`
    handed over by mistake fails loudly instead of leaking the reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=EPISODE_ID.pattern)
    source: EpisodeSource
    #: The moment. Nothing dated after it is visible.
    captured_at: datetime
    community: EpisodeCommunity
    #: The conversation as visible at `captured_at`, oldest first.
    transcript: list[EpisodeMessage] = Field(min_length=1)
    #: The message the reaction answers (or decides not to answer).
    target_message_id: str = Field(min_length=1)
    persona: PersonaRef
    relation: Relation
    #: Ids of knowledge/inspiration materials available to the generator. Empty until R2.
    materials: list[str] = Field(default_factory=list)
    #: Which kinds of reaction a reviewer considers acceptable HERE. Several are allowed on
    #: purpose: the eval compares the kind, not the text against one string.
    allowed_reactions: list[ReactionKind] = Field(min_length=1)
    split: Split
    #: Written by an agent to demonstrate the mechanics. Counted separately, never a real case.
    synthetic: bool
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> EpisodeInput:
        _aware(self.captured_at, "captured_at")
        ids = [m.message_id for m in self.transcript]
        if len(set(ids)) != len(ids):
            raise ValueError("transcript ma powtórzone message_id")
        known = set(ids)
        for message in self.transcript:
            if message.parent_id is not None and message.parent_id not in known:
                raise ValueError(
                    f"message {message.message_id}: parent_id {message.parent_id!r} nie jest "
                    "w transkrypcie (rodzic musi być widoczny albo None dla korzenia)"
                )
            if message.created_at > self.captured_at:
                raise ValueError(
                    f"message {message.message_id} jest z {message.created_at.isoformat()}, "
                    f"po captured_at {self.captured_at.isoformat()}: przyszłość w wejściu"
                )
        if self.target_message_id not in known:
            raise ValueError(
                f"target_message_id {self.target_message_id!r} nie jest w transkrypcie"
            )
        if len(set(self.allowed_reactions)) != len(self.allowed_reactions):
            raise ValueError("allowed_reactions ma powtórzenia")
        own = {m.message_id for m in self.transcript if m.own}
        relation = self.relation
        if relation.state is RelationState.NEW_ENTRY and own:
            raise ValueError(
                f"relation NEW_ENTRY, a transkrypt ma własne wiadomości konta {sorted(own)}"
            )
        if relation.state is RelationState.OWN_CONTINUATION:
            if not relation.own_message_ids:
                raise ValueError("relation OWN_CONTINUATION wymaga own_message_ids")
            missing = [i for i in relation.own_message_ids if i not in own]
            if missing:
                raise ValueError(
                    f"own_message_ids {missing} nie są własnymi wiadomościami w transkrypcie "
                    "(brak w transkrypcie albo own=false)"
                )
        if (
            relation.state is RelationState.KNOWN_INTERLOCUTOR
            and not relation.own_message_ids
            and len(relation.evidence.strip()) < 10
        ):
            # Known from elsewhere: the evidence text has to point at the earlier conversation.
            raise ValueError(
                "relation KNOWN_INTERLOCUTOR bez własnych wiadomości wymaga evidence "
                "wskazującego wcześniejszą rozmowę"
            )
        for name in HIDDEN_FIELD_NAMES:
            if name in self.tags:
                raise ValueError(f"tag {name!r} to nazwa pola części referencyjnej")
        return self


class EpisodeReference(BaseModel):
    """The hidden part: what a reviewer considers a good reaction here and why. Never an input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reaction: ReactionKind
    #: The exemplary text; `None` for `WAIT` and `SILENCE`, required for every other kind.
    text: str | None = None
    #: Other kinds the reviewer would also accept here, beyond `reaction`.
    could_also: list[ReactionKind] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    author_of_reference: ReferenceAuthor
    status: ReferenceStatus
    #: What happened in the thread AFTER `captured_at`. For outcome analysis only.
    later_messages: list[EpisodeMessage] = Field(default_factory=list)
    outcome_note: str | None = None

    @model_validator(mode="after")
    def _text_matches_kind(self) -> EpisodeReference:
        silent = self.reaction in (ReactionKind.WAIT, ReactionKind.SILENCE)
        if silent and self.text is not None:
            raise ValueError(f"reakcja {self.reaction.value} nie ma tekstu; text musi być null")
        if not silent and not (self.text or "").strip():
            raise ValueError(f"reakcja {self.reaction.value} wymaga text")
        if self.status is ReferenceStatus.REVIEWED_BY_OPERATOR and (
            self.author_of_reference is ReferenceAuthor.MODEL
        ):
            # A model's candidate stays a model's candidate until the operator writes their own or
            # adopts it; adoption changes the author, not only the status.
            raise ValueError(
                "status REVIEWED_BY_OPERATOR z author_of_reference=MODEL: ocena modelu nie staje "
                "się oceną operatora przez zmianę statusu"
            )
        return self


class Episode(BaseModel):
    """`input` (what the generator sees) and `reference` (what it must not). A missing reference
    means the episode is HUMAN_REVIEW_PENDING in every report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: EpisodeInput
    reference: EpisodeReference | None = None

    @model_validator(mode="after")
    def _reference_is_in_the_future_and_allowed(self) -> Episode:
        if self.reference is None:
            return self
        for message in self.reference.later_messages:
            if message.created_at <= self.input.captured_at:
                raise ValueError(
                    f"later_messages: {message.message_id} jest z "
                    f"{message.created_at.isoformat()}, nie po captured_at"
                )
        if self.reference.reaction not in self.input.allowed_reactions:
            raise ValueError(
                f"reference.reaction {self.reference.reaction.value} nie jest w "
                f"allowed_reactions {[k.value for k in self.input.allowed_reactions]}"
            )
        return self

    def for_generator(self) -> EpisodeInput:
        """The only sanctioned handover to the generator."""
        return self.input

    @property
    def thread_key(self) -> tuple[str, str]:
        return (self.input.source.platform.value, self.input.source.external_id)


class EpisodeFileError(ValueError):
    """A file under `episodes/` that is not one valid `Episode`. Names the file."""


def load_episode(path: Path) -> Episode:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EpisodeFileError(f"{path}: nie da się odczytać JSON-a: {exc}") from exc
    if not isinstance(raw, dict):
        raise EpisodeFileError(f"{path}: oczekiwano obiektu z polami input/reference")
    try:
        return Episode.model_validate(raw)
    except ValidationError as exc:
        raise EpisodeFileError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class LoadResult:
    """Episodes plus what the loader noticed but did not treat as an error."""

    episodes: tuple[Episode, ...]
    warnings: tuple[str, ...]


def load_episode_set(
    root: Path = DEFAULT_EPISODES_DIR,
    references_dir: Path | None = DEFAULT_REFERENCES_DIR,
    *,
    strict: bool = False,
) -> LoadResult:
    """Every `*.json` under `root`, recursively, sorted by id. Refuses duplicate ids, an id that
    differs from the file name, and one thread split across DEV and HOLDOUT. References kept
    apart under `references_dir` are attached to the episodes THAT WERE LOADED and validated as a
    pair; a reference whose episode is not in `root` (a run over a subdirectory) is a warning, or
    an error with `strict=True`."""
    if not root.is_dir():
        raise EpisodeFileError(f"{root}: katalog epizodów nie istnieje")
    episodes: dict[str, Episode] = {}
    for path in sorted(root.rglob("*.json")):
        episode = load_episode(path)
        if path.stem != episode.input.id:
            raise EpisodeFileError(
                f"{path}: nazwa pliku {path.stem!r} różni się od id {episode.input.id!r}"
            )
        if episode.input.id in episodes:
            raise EpisodeFileError(f"{path}: id {episode.input.id!r} występuje dwa razy")
        episodes[episode.input.id] = episode
    warnings: list[str] = []
    if references_dir is not None and references_dir.is_dir():
        for path in sorted(references_dir.rglob("*.json")):
            attached = _attach_reference(path, episodes)
            if attached is None:
                note = f"{path}: referencja do epizodu spoza wczytanego zbioru"
                if strict:
                    raise EpisodeFileError(note)
                warnings.append(note)
                continue
            episode_id, episode = attached
            episodes[episode_id] = episode
    _refuse_split_threads(episodes.values())
    return LoadResult(
        episodes=tuple(episodes[key] for key in sorted(episodes)), warnings=tuple(warnings)
    )


def load_episodes(
    root: Path = DEFAULT_EPISODES_DIR,
    references_dir: Path | None = DEFAULT_REFERENCES_DIR,
    *,
    strict: bool = False,
) -> tuple[Episode, ...]:
    """`load_episode_set(...).episodes`, for callers that do not report warnings."""
    return load_episode_set(root, references_dir, strict=strict).episodes


def _attach_reference(path: Path, episodes: Mapping[str, Episode]) -> tuple[str, Episode] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EpisodeFileError(f"{path}: nie da się odczytać JSON-a: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"episode_id", "reference"}:
        raise EpisodeFileError(f"{path}: oczekiwano obiektu z polami episode_id i reference")
    episode_id = str(raw["episode_id"])
    if path.stem != episode_id:
        raise EpisodeFileError(f"{path}: nazwa pliku różni się od episode_id {episode_id!r}")
    episode = episodes.get(episode_id)
    if episode is None:
        return None
    if episode.reference is not None:
        raise EpisodeFileError(
            f"{path}: epizod {episode_id!r} ma już referencję w swoim pliku; dwa źródła to błąd"
        )
    try:
        return episode_id, Episode(input=episode.input, reference=raw["reference"])
    except ValidationError as exc:
        raise EpisodeFileError(f"{path}: {exc}") from exc


def _refuse_split_threads(episodes: Iterable[Episode]) -> None:
    splits: dict[tuple[str, str], set[Split]] = {}
    for episode in episodes:
        splits.setdefault(episode.thread_key, set()).add(episode.input.split)
    torn = {key: sorted(s.value for s in value) for key, value in splits.items() if len(value) > 1}
    if torn:
        raise EpisodeFileError(
            f"ten sam wątek w dwu podziałach (przyszłość epizodu dev byłaby w holdoucie): {torn}"
        )


def split_counts(episodes: Iterable[Episode]) -> dict[str, int]:
    counts = {"DEV": 0, "HOLDOUT": 0, "synthetic": 0, "with_reference": 0, "reviewed": 0}
    for episode in episodes:
        counts[episode.input.split.value] += 1
        counts["synthetic"] += int(episode.input.synthetic)
        counts["with_reference"] += int(episode.reference is not None)
        if episode.reference is not None:
            counts["reviewed"] += int(
                episode.reference.status is ReferenceStatus.REVIEWED_BY_OPERATOR
            )
    return counts


class Persona(BaseModel):
    """The four profile fields the generator is grounded in: the same shape as
    `user_profile_versions` (`knows_deeply`, `positions`, `would_never_claim`, `voice_samples`).
    Offline they come from `personas/<label>.v<version>.json`; live from the database."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    profile_version: int = Field(ge=1)
    knows_deeply: str
    positions: str
    would_never_claim: str
    voice_samples: str
    synthetic: bool

    def as_mapping(self) -> dict[str, str]:
        return {
            "knows_deeply": self.knows_deeply,
            "positions": self.positions,
            "would_never_claim": self.would_never_claim,
            "voice_samples": self.voice_samples,
        }


def load_persona(ref: PersonaRef, root: Path = DEFAULT_PERSONAS_DIR) -> Persona:
    path = root / f"{ref.label}.v{ref.profile_version}.json"
    if not path.is_file():
        raise EpisodeFileError(
            f"brak persony {ref.label} v{ref.profile_version} pod {path} (offline persona jest "
            "plikiem; live runner czyta user_profile_versions)"
        )
    try:
        persona = Persona.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise EpisodeFileError(f"{path}: {exc}") from exc
    if (persona.label, persona.profile_version) != (ref.label, ref.profile_version):
        raise EpisodeFileError(f"{path}: label/wersja w pliku różni się od nazwy pliku")
    return persona
