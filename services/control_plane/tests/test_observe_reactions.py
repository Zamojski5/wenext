"""The baseline reaction generator without a database or a model (R1, `observe/reactions.py`).

Measured: the contract rules the founder asked for (a new entry needs a contribution and may not
open with a short acknowledgement or a joke; WAIT and SILENCE carry no text; the account does not
answer itself), the input the model sees (profile whole, transcript clipped, target kept), the
port call through `FakeTurnRunner` with the prompt's identity on the result, and the leak guard of
`situation_from_mapping` (extra keys are ignored, never forwarded).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from community_os_contracts import ReactionKind, ThreadRole
from community_os_contracts.judgment import MIN_CONTRIBUTION_CHARS
from control_plane.observe import prompts
from control_plane.observe.reactions import (
    MAX_MESSAGES,
    MOVE_NONE,
    MOVES,
    REACTION_OUTPUT_SCHEMA,
    ReactionRejected,
    ReactionSituation,
    SituationMessage,
    generate_reaction,
    neutral_ids,
    reaction_from_output,
    reaction_input,
    situation_from_mapping,
)
from control_plane.observe.turns import FakeTurnRunner

T0 = datetime(2026, 9, 2, 15, 20, tzinfo=UTC)
PROFILE = {
    "knows_deeply": "queues in production",
    "positions": "measure before tuning",
    "would_never_claim": "kubernetes at scale",
    "voice_samples": "short sentences, one number",
}


def msg(mid: str, minutes_before: int, *, parent: str | None = None, own: bool = False, body="x"):
    return SituationMessage(
        message_id=mid,
        parent_id=parent,
        author="acct" if own else f"u_{mid}",
        body=body,
        created_at=T0 - timedelta(minutes=minutes_before),
        own=own,
    )


def new_entry(*messages: SituationMessage, target: str = "m1") -> ReactionSituation:
    return ReactionSituation(
        situation_id="s-new",
        platform="HACKER_NEWS",
        community_display_name="HN Ask",
        language="en",
        captured_at=T0,
        transcript=messages or (msg("m1", 75), msg("m2", 32, parent="m1")),
        target_message_id=target,
        relation="NEW_ENTRY",
        relation_evidence="no own messages",
    )


def continuation() -> ReactionSituation:
    return ReactionSituation(
        situation_id="s-cont",
        platform="HACKER_NEWS",
        community_display_name="HN Ask",
        language="en",
        captured_at=T0,
        transcript=(
            msg("m1", 200),
            msg("m2", 150, parent="m1", own=True),
            msg("m3", 9, parent="m2"),
        ),
        target_message_id="m3",
        relation="OWN_CONTINUATION",
        relation_evidence="m3 answers m2",
        own_message_ids=("m2",),
    )


def output(**overrides) -> dict:
    base = {
        "decision": "DIAGNOSE",
        "draft": "BRPOP removes the job before you process it, so a crash loses it silently.",
        "target_message_id": "m1",
        "contribution": "names the pop-then-crash loss mechanism and how to count it",
        "brief_rationale": "The one reply covers eviction; the account's experience is the pop loss.",
        "sources_used": [],
        "confidence": 0.7,
        "move": "CLAIM_WITH_LIMIT",
    }
    base.update(overrides)
    return base


# -- prompt v3: the community's exemplars go in, the move comes out -----------------------------


def test_the_communitys_exemplars_reach_the_model_under_community_not_situation() -> None:
    """Prompt v3: the most upvoted comments of the community travel as `community.exemplars`,
    text only. A situation without them sends an empty list, never a missing key, so the prompt's
    reference to the field always resolves."""
    from dataclasses import replace

    plain = reaction_input(new_entry(), profile=PROFILE, now=T0)
    assert plain["community"]["exemplars"] == []
    with_examples = replace(
        new_entry(), community_exemplars=("Fire the platform team. Step 2: watch.", "One-liner.")
    )
    payload = reaction_input(with_examples, profile=PROFILE, now=T0)
    assert payload["community"]["exemplars"] == [
        "Fire the platform team. Step 2: watch.",
        "One-liner.",
    ]
    assert "exemplars" not in payload["situation"], "przykłady to cecha społeczności, nie sytuacji"
    assert "move" in REACTION_OUTPUT_SCHEMA["required"]
    assert REACTION_OUTPUT_SCHEMA["properties"]["move"]["enum"] == list(MOVES)


def test_what_the_account_knows_about_the_community_travels_as_data_not_as_instructions() -> None:
    """D-036: the model is given the community's register and what this account's moves earned
    here, and then chooses. Nothing is assigned to it: there is no requested move, no allowed-move
    table and no post kind computed by a regex. A situation without the data sends empty values,
    never missing keys, so the prompt's reference to the fields always resolves."""
    from dataclasses import replace

    assert "IRONY" in MOVES and "ONE_LINER" in MOVES
    plain = reaction_input(new_entry(), profile=PROFILE, now=T0)
    assert plain["community"]["voice_card"] == {} and plain["community"]["move_outcomes"] == []
    assert "requested_move" not in plain["situation"]
    assert "move_alternatives" not in plain["situation"]
    assert "post_kind" not in plain["situation"]
    rich = replace(
        new_entry(),
        community_voice_card={"avoid": ["Marketing lists."], "how_people_write": "Short."},
        community_move_outcomes=(
            {"move": "IMAGE", "published": 1, "points_vs_thread_median": 1.0, "replied_share": 0.0},
        ),
    )
    payload = reaction_input(rich, profile=PROFILE, now=T0)
    assert payload["community"]["voice_card"] == {
        "avoid": ["Marketing lists."],
        "how_people_write": "Short.",
    }
    assert payload["community"]["move_outcomes"][0]["move"] == "IMAGE"
    # Every move stays available to the model, and the schema is the only closed list.
    assert reaction_from_output(output(move="IRONY"), situation=new_entry()).move == "IRONY"
    assert reaction_from_output(output(move="ONE_LINER"), situation=new_entry()).move == "ONE_LINER"
    assert REACTION_OUTPUT_SCHEMA["properties"]["move"]["enum"] == list(MOVES)


def test_a_situation_from_plain_data_reads_the_community_knowledge_when_present() -> None:
    raw = {
        "id": "ep-2",
        "source": {"platform": "REDDIT"},
        "community": {"display_name": "r/x", "language": "en"},
        "captured_at": T0.isoformat(),
        "transcript": [
            {"message_id": "m1", "parent_id": None, "author": "a", "body": "q", "created_at": T0}
        ],
        "target_message_id": "m1",
        "relation": {"state": "NEW_ENTRY", "evidence": "none"},
        "community_voice_card": {"avoid": ["x"]},
        "community_move_outcomes": [{"move": "NUMBER", "published": 2}],
    }
    situation = situation_from_mapping(raw)
    assert situation.community_voice_card == {"avoid": ["x"]}
    assert situation.community_move_outcomes == ({"move": "NUMBER", "published": 2},)
    for key in ("community_voice_card", "community_move_outcomes"):
        raw.pop(key)
    bare = situation_from_mapping(raw)
    assert bare.community_voice_card is None and bare.community_move_outcomes == ()


def test_the_move_is_parsed_from_the_closed_list_and_defaults_to_none() -> None:
    reaction = reaction_from_output(output(move="COUNTERPOINT"), situation=new_entry())
    assert reaction.move == "COUNTERPOINT"
    # An output from before v3 (recorded evals) carries no move: NONE, not a rejection.
    legacy = output()
    del legacy["move"]
    assert reaction_from_output(legacy, situation=new_entry()).move == MOVE_NONE
    with pytest.raises(ReactionRejected, match="move="):
        reaction_from_output(output(move="RANT"), situation=new_entry())
    # No text, no move: SILENCE with a claimed move is stored as NONE.
    silent = reaction_from_output(
        output(decision="SILENCE", draft=None, contribution=None, move="IMAGE"),
        situation=new_entry(),
    )
    assert silent.move == MOVE_NONE and not silent.has_text


def test_a_situation_from_plain_data_reads_exemplars_when_present() -> None:
    raw = {
        "id": "ep-1",
        "source": {"platform": "REDDIT"},
        "community": {"display_name": "r/x", "language": "en"},
        "captured_at": T0.isoformat(),
        "transcript": [
            {"message_id": "m1", "parent_id": None, "author": "a", "body": "q", "created_at": T0}
        ],
        "target_message_id": "m1",
        "relation": {"state": "NEW_ENTRY", "evidence": "none"},
        "community_exemplars": ["top comment"],
    }
    assert situation_from_mapping(raw).community_exemplars == ("top comment",)
    raw.pop("community_exemplars")
    assert situation_from_mapping(raw).community_exemplars == ()


# -- the contract -------------------------------------------------------------------------------


def test_a_valid_new_entry_diagnosis_passes_and_long_dashes_are_rewritten() -> None:
    reaction = reaction_from_output(
        output(draft="BRPOP pops first — a crash in between loses the job."), situation=new_entry()
    )
    assert reaction.decision is ReactionKind.DIAGNOSE
    assert reaction.draft == "BRPOP pops first, a crash in between loses the job."
    assert reaction.long_dash_count == 1 and reaction.has_text


def test_a_new_entry_may_not_open_with_a_short_ack_or_a_joke() -> None:
    for kind in ("SHORT_ACK", "HUMOR"):
        with pytest.raises(ReactionRejected, match="nowym wejściu"):
            reaction_from_output(output(decision=kind, contribution=None), situation=new_entry())


def test_a_new_entry_needs_a_named_contribution_of_contract_length() -> None:
    with pytest.raises(ReactionRejected, match=f"minimum {MIN_CONTRIBUTION_CHARS}"):
        reaction_from_output(output(contribution="yes"), situation=new_entry())
    with pytest.raises(ReactionRejected, match="bez konkretnego"):
        reaction_from_output(output(contribution=None), situation=new_entry())
    # In a continuation the same text needs no contribution: the relation earns it.
    short = output(
        decision="SHORT_ACK", draft="glad it helped :)", contribution=None, target_message_id="m3"
    )
    reaction = reaction_from_output(short, situation=continuation())
    assert reaction.decision is ReactionKind.SHORT_ACK and reaction.contribution is None


def test_wait_and_silence_carry_no_text_and_text_kinds_require_it() -> None:
    for kind in ("WAIT", "SILENCE"):
        ok = reaction_from_output(
            output(decision=kind, draft=None, contribution=None), situation=new_entry()
        )
        assert ok.draft is None and not ok.has_text
        with pytest.raises(ReactionRejected, match="musi być null"):
            reaction_from_output(output(decision=kind, contribution=None), situation=new_entry())
    with pytest.raises(ReactionRejected, match="wymaga tekstu"):
        reaction_from_output(output(draft="   "), situation=new_entry())


def test_the_account_does_not_answer_itself_and_the_target_must_exist() -> None:
    with pytest.raises(ReactionRejected, match="samo sobie"):
        reaction_from_output(
            output(decision="HELP", target_message_id="m2", contribution=None),
            situation=continuation(),
        )
    with pytest.raises(ReactionRejected, match="nie jest w transkrypcie"):
        reaction_from_output(output(target_message_id="m404"), situation=new_entry())


def test_unknown_decision_sources_and_confidence_are_rejected() -> None:
    with pytest.raises(ReactionRejected, match="spoza listy"):
        reaction_from_output(output(decision="PRAISE"), situation=new_entry())
    with pytest.raises(ReactionRejected, match="spoza sytuacji"):
        reaction_from_output(output(sources_used=["doc-7"]), situation=new_entry())
    with pytest.raises(ReactionRejected, match=r"poza \[0, 1\]"):
        reaction_from_output(output(confidence=1.4), situation=new_entry())
    with pytest.raises(ReactionRejected, match="brief_rationale"):
        reaction_from_output(output(brief_rationale="ok"), situation=new_entry())


def test_the_schema_requires_exactly_the_fields_the_validator_reads() -> None:
    assert set(REACTION_OUTPUT_SCHEMA["required"]) == set(REACTION_OUTPUT_SCHEMA["properties"])
    assert set(REACTION_OUTPUT_SCHEMA["required"]) == set(output())
    assert REACTION_OUTPUT_SCHEMA["properties"]["decision"]["enum"] == [
        k.value for k in ReactionKind
    ]


# -- the input ----------------------------------------------------------------------------------


def test_reaction_input_carries_the_profile_whole_and_clips_the_transcript() -> None:
    long = tuple(
        msg(f"m{i}", 500 - i, parent=None if i == 0 else "m0") for i in range(MAX_MESSAGES + 5)
    )
    situation = new_entry(*long, target="m0")
    payload = reaction_input(situation, profile=PROFILE, now=T0)
    assert payload["user_profile"] == PROFILE
    assert payload["community"] == {
        "name": "HN Ask",
        "platform": "HACKER_NEWS",
        "language": "en",
        "exemplars": [],
        "voice_card": {},
        "move_outcomes": [],
    }
    assert all(
        m["message_id"].startswith("m") and m["message_id"][1:].isdigit()
        for m in payload["situation"]["transcript"]
    ), "prawdziwe id wiadomości nie idą do modelu"
    ids = [m["message_id"] for m in payload["situation"]["transcript"]]
    alias = neutral_ids(situation)
    assert len(ids) == MAX_MESSAGES and alias["m0"] in ids, "cel zawsze w wejściu, reszta najnowsza"
    assert payload["situation"]["target_message_id"] == alias["m0"]
    assert payload["situation"]["transcript_total"] == MAX_MESSAGES + 5
    assert payload["situation"]["relation"]["state"] == "NEW_ENTRY"
    assert "id" not in payload["situation"], "id sytuacji nie idzie do modelu"


def test_a_situation_refuses_a_future_message_and_an_unknown_target() -> None:
    with pytest.raises(ValueError, match="przyszłość"):
        new_entry(msg("m1", 10), msg("m2", -3, parent="m1"))
    with pytest.raises(ValueError, match="target_message_id"):
        new_entry(target="m9")


def test_situation_from_mapping_ignores_reference_keys_it_is_handed() -> None:
    raw = {
        "id": "ep-1",
        "source": {"platform": "HACKER_NEWS", "external_id": "1", "url": "https://x/1"},
        "captured_at": T0.isoformat(),
        "community": {"display_name": "HN Ask", "key": "ask", "language": "en"},
        "transcript": [
            {
                "message_id": "m1",
                "parent_id": None,
                "author": "a",
                "body": "q",
                "created_at": (T0 - timedelta(hours=1)).isoformat(),
            }
        ],
        "target_message_id": "m1",
        "relation": {"state": "NEW_ENTRY", "evidence": "none"},
        "reference": {"text": "THE ANSWER"},
        "later_messages": [{"message_id": "m9"}],
    }
    situation = situation_from_mapping(raw)
    payload = reaction_input(situation, profile=PROFILE, now=T0)
    import json

    dumped = json.dumps(payload)
    assert "THE ANSWER" not in dumped and "m9" not in dumped


# -- through the port ---------------------------------------------------------------------------


async def test_generate_reaction_runs_the_worker_prompt_and_stamps_its_identity() -> None:
    fake = FakeTurnRunner(outputs=[output()], model="fake-terra", effort="medium")
    result = await generate_reaction(fake, situation=new_entry(), profile=PROFILE, now=T0)
    (call,) = fake.calls
    assert call.role is ThreadRole.WORKER
    assert call.output_schema == REACTION_OUTPUT_SCHEMA
    assert '"situation"' in call.user_input and '"user_profile"' in call.user_input
    prompt = prompts.get("reaction_baseline")
    assert call.instructions == prompt.text
    assert result.prompt_name == "reaction_baseline" and result.prompt_sha256 == prompt.sha256
    assert result.model == "fake-terra" and result.effort == "medium"
    assert result.reaction.decision is ReactionKind.DIAGNOSE and result.situation_id == "s-new"


async def test_a_contract_breaking_answer_is_rejected_not_saved() -> None:
    fake = FakeTurnRunner(outputs=[output(decision="HUMOR", contribution=None)])
    with pytest.raises(ReactionRejected):
        await generate_reaction(fake, situation=new_entry(), profile=PROFILE, now=T0)


# -- review 2026-09-06: two explicit cases ------------------------------------------------------


def test_the_target_stays_in_the_window_when_the_transcript_is_clipped() -> None:
    """`MAX_MESSAGES` newest messages go to the model; when the target is older than all of them it
    replaces the oldest kept one, so the model always sees what it reacts to."""
    old_target = msg("target", 10_000, body="the question everybody is answering")
    newer = tuple(msg(f"n{i}", 5_000 - i, parent="target") for i in range(MAX_MESSAGES + 10))
    situation = new_entry(old_target, *newer, target="target")
    payload = reaction_input(situation, profile=PROFILE, now=T0)
    ids = [m["message_id"] for m in payload["situation"]["transcript"]]
    alias = neutral_ids(situation)
    assert len(ids) == MAX_MESSAGES
    assert ids[0] == alias["target"], "cel zastępuje najstarszą zachowaną wiadomość"
    assert ids[-1] == alias[f"n{MAX_MESSAGES + 9}"], "reszta to najnowsze wiadomości"
    assert payload["situation"]["transcript_total"] == MAX_MESSAGES + 11


def test_short_ack_and_humor_are_allowed_with_a_known_interlocutor() -> None:
    """A person the account has talked with elsewhere earns the same short reactions as a
    continuation of the account's own thread; the evidence is the earlier conversation."""
    known = ReactionSituation(
        situation_id="s-known",
        platform="REDDIT",
        community_display_name="r/test",
        language="en",
        captured_at=T0,
        transcript=(msg("m1", 30), msg("m2", 4, parent="m1")),
        target_message_id="m2",
        relation="KNOWN_INTERLOCUTOR",
        relation_evidence="talked with u_m2 in REDDIT:test:1w6ln9w on 2026-09-03",
    )
    ack = reaction_from_output(
        output(
            decision="SHORT_ACK",
            draft="good to see you back :)",
            contribution=None,
            target_message_id="m2",
        ),
        situation=known,
    )
    assert ack.decision is ReactionKind.SHORT_ACK and ack.contribution is None
    joke = reaction_from_output(
        output(
            decision="HUMOR",
            draft="still no exactly-once, I see",
            contribution=None,
            target_message_id="m2",
        ),
        situation=known,
    )
    assert joke.decision is ReactionKind.HUMOR
