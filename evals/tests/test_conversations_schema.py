"""The episode contract: the generator sees the input and never the reference (R1, schema.py).

Measured, in this order: the hidden part cannot reach the generator by type; time is frozen at
`captured_at` in both directions; relations carry evidence; the shipped synthetic episodes load,
are three, and are all marked synthetic; the loader refuses a torn thread and a renamed file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from community_os_contracts import ReactionKind
from pydantic import ValidationError

from evals.conversations.schema import (
    DEFAULT_EPISODES_DIR,
    HIDDEN_FIELD_NAMES,
    Episode,
    EpisodeFileError,
    EpisodeInput,
    PersonaRef,
    ReferenceStatus,
    RelationState,
    load_episode,
    load_episode_set,
    load_episodes,
    load_persona,
    split_counts,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def message(mid: str, minutes_before: int, *, parent: str | None = None, own: bool = False) -> dict:
    return {
        "message_id": mid,
        "parent_id": parent,
        "author": "acct" if own else f"user_{mid}",
        "body": f"body of {mid}",
        "created_at": (T0 - timedelta(minutes=minutes_before)).isoformat(),
        "own": own,
    }


def episode(**overrides) -> dict:
    base = {
        "input": {
            "id": "test-episode-001",
            "source": {
                "platform": "HACKER_NEWS",
                "external_id": "1",
                "url": "https://news.ycombinator.com/item?id=1",
            },
            "captured_at": T0.isoformat(),
            "community": {"display_name": "HN Ask", "key": "ask", "language": "en"},
            "transcript": [message("m1", 60), message("m2", 30, parent="m1")],
            "target_message_id": "m1",
            "persona": {"label": "synthetic-maintainer", "profile_version": 1},
            "relation": {"state": "NEW_ENTRY", "evidence": "no own messages"},
            "allowed_reactions": ["HELP", "CLARIFY"],
            "split": "DEV",
            "synthetic": True,
        },
        "reference": {
            "reaction": "HELP",
            "text": "THE ANSWER KEY: check the ack mode first.",
            "rationale": "SECRET RATIONALE the generator must never read",
            "author_of_reference": "MODEL",
            "status": "CANDIDATE",
            "later_messages": [message("m9", -120, parent="m1")],
        },
    }
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        base[section][field] = value
    return base


# -- the rule ------------------------------------------------------------------------------------


def test_the_input_schema_has_no_hidden_field_and_refuses_a_whole_episode() -> None:
    names = set(EpisodeInput.model_json_schema()["properties"])
    assert names, "schemat wejścia bez pól nic nie mierzy"
    assert not (names & HIDDEN_FIELD_NAMES), sorted(names & HIDDEN_FIELD_NAMES)
    with pytest.raises(ValidationError, match="reference"):
        EpisodeInput.model_validate(episode())  # the whole episode, reference included
    with pytest.raises(ValidationError):
        EpisodeInput.model_validate({**episode()["input"], "text": "leak"})


def test_for_generator_carries_no_reference_text_and_no_later_message() -> None:
    full = Episode.model_validate(episode())
    handed = full.for_generator()
    serialized = json.dumps(handed.model_dump(mode="json"), ensure_ascii=False)
    assert "ANSWER KEY" not in serialized and "SECRET RATIONALE" not in serialized
    assert "m9" not in serialized, "wiadomość PO captured_at nie jest w wejściu"
    assert handed.id == "test-episode-001" and handed.target_message_id == "m1"


# -- time -------------------------------------------------------------------------------------------


def test_a_future_message_in_the_transcript_is_refused() -> None:
    future = message("m3", -5, parent="m1")  # five minutes after captured_at
    with pytest.raises(ValidationError, match="przyszłość"):
        Episode.model_validate(episode(input__transcript=[message("m1", 60), future]))


def test_later_messages_must_be_after_captured_at() -> None:
    past = message("m9", 10, parent="m1")
    with pytest.raises(ValidationError, match="later_messages"):
        Episode.model_validate(episode(reference__later_messages=[past]))


def test_naive_datetimes_are_refused() -> None:
    naive = {**message("m1", 60), "created_at": "2026-09-01T11:00:00"}
    with pytest.raises(ValidationError, match="aware"):
        Episode.model_validate(episode(input__transcript=[naive]))


# -- structure --------------------------------------------------------------------------------------


def test_target_and_parents_must_exist_in_the_transcript() -> None:
    with pytest.raises(ValidationError, match="target_message_id"):
        Episode.model_validate(episode(input__target_message_id="nope"))
    orphan = message("m2", 30, parent="ghost")
    with pytest.raises(ValidationError, match="parent_id"):
        Episode.model_validate(episode(input__transcript=[message("m1", 60), orphan]))


def test_relations_carry_evidence() -> None:
    own = message("m2", 30, parent="m1", own=True)
    with pytest.raises(ValidationError, match="NEW_ENTRY"):
        Episode.model_validate(episode(input__transcript=[message("m1", 60), own]))
    with pytest.raises(ValidationError, match="own_message_ids"):
        Episode.model_validate(
            episode(
                input__transcript=[message("m1", 60), own],
                input__relation={"state": "OWN_CONTINUATION", "evidence": "m3 answers m2"},
            )
        )
    ok = Episode.model_validate(
        episode(
            input__transcript=[message("m1", 60), own, message("m3", 5, parent="m2")],
            input__target_message_id="m3",
            input__relation={
                "state": "OWN_CONTINUATION",
                "evidence": "m3 answers m2",
                "own_message_ids": ["m2"],
            },
            input__allowed_reactions=["SHORT_ACK", "SILENCE", "HELP"],
        )
    )
    assert ok.input.relation.state is RelationState.OWN_CONTINUATION


def test_the_reference_matches_its_kind_and_the_allowed_list() -> None:
    with pytest.raises(ValidationError, match="text musi być null"):
        Episode.model_validate(
            episode(reference__reaction="WAIT", input__allowed_reactions=["WAIT"])
        )
    with pytest.raises(ValidationError, match="wymaga text"):
        Episode.model_validate(episode(reference__text=None))
    with pytest.raises(ValidationError, match="allowed_reactions"):
        Episode.model_validate(episode(reference__reaction="HUMOR"))
    with pytest.raises(ValidationError, match="nie staje się oceną operatora"):
        Episode.model_validate(episode(reference__status="REVIEWED_BY_OPERATOR"))


def test_an_episode_without_a_reference_is_valid_and_counted_as_pending() -> None:
    raw = episode()
    raw["reference"] = None
    loaded = Episode.model_validate(raw)
    assert loaded.reference is None
    counts = split_counts([loaded])
    assert counts == {"DEV": 1, "HOLDOUT": 0, "synthetic": 1, "with_reference": 0, "reviewed": 0}


# -- the shipped synthetic set and the loader ---------------------------------------------------


def test_the_three_demonstration_episodes_load_and_are_marked_synthetic() -> None:
    """`episodes/synthetic/` holds exactly the three mechanics demonstrations of this session."""
    demos = load_episodes(DEFAULT_EPISODES_DIR / "synthetic", references_dir=None)
    assert len(demos) == 3, [e.input.id for e in demos]
    assert all(e.input.synthetic for e in demos)
    kinds = {e.reference.reaction for e in demos if e.reference is not None}
    assert kinds == {ReactionKind.SHORT_ACK, ReactionKind.DIAGNOSE, ReactionKind.WAIT}
    states = {e.input.relation.state for e in demos}
    assert states == {RelationState.OWN_CONTINUATION, RelationState.NEW_ENTRY}
    for e in demos:
        assert e.reference is not None
        assert e.reference.status is ReferenceStatus.CANDIDATE, "syntetyk nie jest przejrzany"
        assert e.reference.author_of_reference.value == "MODEL"
    persona = load_persona(PersonaRef(label="synthetic-maintainer", profile_version=1))
    assert persona.synthetic is True and "queue" in persona.knows_deeply.lower()


def test_every_episode_on_disk_loads_and_synthetic_additions_are_continuations_with_a_note() -> (
    None
):
    """The whole set, real episodes included. A synthetic episode outside `synthetic/` is a real
    thread with an ADDED own message (there is no real continuation of the account's own
    conversation yet), so it must be a continuation and say what was added in a
    `syntetyczny:` tag. Every reference is a candidate until the operator reviews it."""
    episodes = load_episodes(DEFAULT_EPISODES_DIR)
    assert len(episodes) >= 3
    counts = split_counts(episodes)
    assert counts["DEV"] + counts["HOLDOUT"] == len(episodes)
    assert counts["reviewed"] == 0, "żadna referencja nie udaje oceny operatora"
    for e in episodes:
        if not e.input.synthetic or e.input.id.startswith("demo-"):
            continue
        assert e.input.relation.state in {
            RelationState.OWN_CONTINUATION,
            RelationState.KNOWN_INTERLOCUTOR,
        }, e.input.id
        note = [tag for tag in e.input.tags if tag.startswith("syntetyczny:")]
        assert note and len(note[0]) > len("syntetyczny:") + 5, (
            f"{e.input.id}: syntetyk bez opisu, co dopisano"
        )
    # Threads never straddle the splits, and holdout references (kept apart) attach and validate.
    holdout = [e for e in episodes if e.input.split.value == "HOLDOUT"]
    if holdout:
        assert all(e.reference is not None for e in holdout), (
            "referencje holdoutu z references/holdout/ nie doczepiły się"
        )


def test_the_loader_refuses_a_renamed_file_a_duplicate_id_and_a_torn_thread(
    tmp_path: Path,
) -> None:
    good = episode()
    (tmp_path / "test-episode-001.json").write_text(json.dumps(good), encoding="utf-8")
    assert len(load_episodes(tmp_path, references_dir=None)) == 1

    (tmp_path / "renamed.json").write_text(json.dumps(good), encoding="utf-8")
    with pytest.raises(EpisodeFileError, match="różni się od id"):
        load_episodes(tmp_path, references_dir=None)
    (tmp_path / "renamed.json").unlink()

    torn = episode(input__id="test-episode-002", input__split="HOLDOUT")
    (tmp_path / "test-episode-002.json").write_text(json.dumps(torn), encoding="utf-8")
    with pytest.raises(EpisodeFileError, match="dwu podziałach"):
        load_episodes(tmp_path, references_dir=None)

    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(EpisodeFileError, match="JSON"):
        load_episode(tmp_path / "broken.json")


def test_a_reference_kept_apart_is_attached_and_validated(tmp_path: Path) -> None:
    raw = episode()
    reference = raw.pop("reference")
    raw["reference"] = None
    episodes_dir = tmp_path / "episodes"
    refs_dir = tmp_path / "references" / "holdout"
    episodes_dir.mkdir()
    refs_dir.mkdir(parents=True)
    (episodes_dir / "test-episode-001.json").write_text(json.dumps(raw), encoding="utf-8")
    assert load_episodes(episodes_dir, references_dir=None)[0].reference is None

    (refs_dir / "test-episode-001.json").write_text(
        json.dumps({"episode_id": "test-episode-001", "reference": reference}), encoding="utf-8"
    )
    (loaded,) = load_episodes(episodes_dir, references_dir=tmp_path / "references")
    assert loaded.reference is not None and loaded.reference.text == reference["text"]
    assert "ANSWER KEY" not in json.dumps(loaded.for_generator().model_dump(mode="json"))

    bad = dict(reference, reaction="HUMOR")  # not in allowed_reactions → the pair validator fires
    (refs_dir / "test-episode-001.json").write_text(
        json.dumps({"episode_id": "test-episode-001", "reference": bad}), encoding="utf-8"
    )
    with pytest.raises(EpisodeFileError, match="allowed_reactions"):
        load_episodes(episodes_dir, references_dir=tmp_path / "references")

    (refs_dir / "test-episode-001.json").unlink()
    (refs_dir / "ghost-episode-009.json").write_text(
        json.dumps({"episode_id": "ghost-episode-009", "reference": reference}), encoding="utf-8"
    )
    loaded = load_episode_set(episodes_dir, references_dir=tmp_path / "references")
    assert len(loaded.episodes) == 1 and len(loaded.warnings) == 1
    assert "spoza wczytanego zbioru" in loaded.warnings[0]
    with pytest.raises(EpisodeFileError, match="spoza wczytanego zbioru"):
        load_episode_set(episodes_dir, references_dir=tmp_path / "references", strict=True)


def test_a_missing_persona_is_an_error_not_an_empty_profile(tmp_path: Path) -> None:
    with pytest.raises(EpisodeFileError, match="brak persony"):
        load_persona(PersonaRef(label="nobody", profile_version=3), tmp_path)
