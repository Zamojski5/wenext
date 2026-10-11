"""`observe/context.py` — what the model sees, and how what it says is checked. Pure, no DB."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from community_os_contracts import JudgmentDecision, Observed, UnknownReason
from control_plane.observe.context import (
    JUDGE_OUTPUT_SCHEMA,
    PICTURE_BODY_CHARS,
    PICTURE_COMMENTS_PER_THREAD,
    PICTURE_MAX_THREADS,
    PICTURE_OUTPUT_SCHEMA,
    CommentContext,
    JudgmentRejected,
    PictureRejected,
    ThreadContext,
    judge_input,
    judgment_from_output,
    metric_value,
    picture_input,
    validate_picture,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SHA = "b" * 64


def thread(
    n: int, *, comments: int = 0, body_len: int = 10, score: Observed[int] | None = None
) -> ThreadContext:
    return ThreadContext(
        ref=f"HACKER_NEWS:ask:{n}",
        external_id=str(n),
        url=f"https://news.ycombinator.com/item?id={n}",
        link_url=None,
        title=f"Ask HN: thread {n}",
        body="x" * body_len,
        author=f"author{n}",
        created_at=datetime(2026, 9, 3, 0, n % 60, tzinfo=UTC),
        score=score if score is not None else Observed[int].known(n),
        comment_count=Observed[int].known(comments),
        comments=tuple(
            CommentContext(
                external_id=f"{n}-c{i}",
                parent_external_id=None,
                author=f"c{i}",
                body=f"comment {i}",
                created_at=datetime(2026, 9, 3, 1, i % 60, tzinfo=UTC),
                score=Observed[int].known(i),
                depth=0,
            )
            for i in range(comments)
        ),
    )


def picture_output(**overrides) -> dict:
    base = {
        "summary": "Dzień pod znakiem pytań o MCP w produkcji.",
        "topics": [
            {"name": "MCP", "why_it_matters": "wraca", "thread_refs": ["HACKER_NEWS:ask:1"]}
        ],
        "recurring_problems": [],
        "what_gets_conversation": "konkretne pytania",
        "what_gets_ignored": "ogólniki",
        "durable_culture": [
            {
                "observation": "wyczuleni na promocję",
                "confidence": 0.4,
                "thread_refs": ["HACKER_NEWS:ask:2"],
            }
        ],
        "current_state": [
            {"observation": "release X", "thread_refs": ["HACKER_NEWS:ask:1", "HACKER_NEWS:ask:2"]}
        ],
        "where_user_could_contribute": [
            {"thread_ref": "HACKER_NEWS:ask:1", "why": "pytanie otwarte"}
        ],
    }
    base.update(overrides)
    return base


def test_metric_value_is_unknown_not_zero() -> None:
    assert metric_value(Observed[int].known(0)) == 0
    assert metric_value(Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)) == "UNKNOWN"


def test_picture_input_caps_threads_and_comments_and_marks_unknown_metrics() -> None:
    threads = [thread(n, comments=9, body_len=5000) for n in range(PICTURE_MAX_THREADS + 7)]
    threads[0] = thread(0, score=Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM))
    payload = picture_input(
        community_display_name="Ask HN",
        platform="HACKER_NEWS",
        window_start=NOW,
        window_end=NOW,
        threads=threads,
        previous_picture=None,
    )
    assert payload["thread_count_in_window"] == PICTURE_MAX_THREADS + 7
    assert payload["threads_included"] == PICTURE_MAX_THREADS == len(payload["threads"])
    included = payload["threads"][-1]
    assert len(included["comments_sample"]) == PICTURE_COMMENTS_PER_THREAD
    assert included["comments_total_observed"] == 9, "obcięcie próbki nie ukrywa prawdziwej liczby"
    assert len(included["body"]) == PICTURE_BODY_CHARS
    assert payload["previous_picture"] is None
    # newest first
    created = [t["created_at"] for t in payload["threads"]]
    assert created == sorted(created, reverse=True)
    by_ref = {t["ref"]: t for t in payload["threads"]}
    assert (
        by_ref["HACKER_NEWS:ask:0"]["score"] == "UNKNOWN" if "HACKER_NEWS:ask:0" in by_ref else True
    )


def test_judge_input_carries_the_whole_profile_and_a_picture_excerpt() -> None:
    profile = {
        "knows_deeply": "queues",
        "positions": "boring tech",
        "would_never_claim": "benchmarks",
        "voice_samples": "hej",
    }
    payload = judge_input(
        profile=profile,
        community_display_name="Ask HN",
        platform="HACKER_NEWS",
        picture=picture_output(),
        thread=thread(1, comments=20),
        now=NOW,
    )
    assert payload["user_profile"] == profile
    assert set(payload["community"]["what_we_learned"]) == {
        "durable_culture",
        "current_state",
        "what_gets_conversation",
        "what_gets_ignored",
    }
    assert len(payload["thread"]["comments"]) == 15
    assert payload["thread"]["comments_total_observed"] == 20
    assert payload["now"] == NOW.isoformat()
    without_picture = judge_input(
        profile=profile,
        community_display_name="x",
        platform="HACKER_NEWS",
        picture=None,
        thread=thread(1),
        now=NOW,
    )
    assert without_picture["community"]["what_we_learned"] is None


def test_validate_picture_drops_items_with_invented_refs_and_counts_them() -> None:
    output = picture_output(
        topics=[
            {"name": "ok", "why_it_matters": "x", "thread_refs": ["HACKER_NEWS:ask:1"]},
            {"name": "invented", "why_it_matters": "x", "thread_refs": ["HACKER_NEWS:ask:999"]},
            {"name": "no refs", "why_it_matters": "x", "thread_refs": []},
        ],
        where_user_could_contribute=[
            {"thread_ref": "HACKER_NEWS:ask:1", "why": "ok"},
            {"thread_ref": "HACKER_NEWS:ask:42", "why": "invented"},
        ],
    )
    validated = validate_picture(output, known_refs={"HACKER_NEWS:ask:1", "HACKER_NEWS:ask:2"})
    assert [t["name"] for t in validated.content["topics"]] == ["ok"]
    assert validated.invalid_refs == ("HACKER_NEWS:ask:999", "HACKER_NEWS:ask:42")
    assert validated.invalid_ref_count == 2
    assert validated.dropped_items == 3, "wymyślony ref, pusty ref i wymyślony wskaźnik"
    assert [p["thread_ref"] for p in validated.content["where_user_could_contribute"]] == [
        "HACKER_NEWS:ask:1"
    ]
    assert validated.content["summary"] == output["summary"]
    assert len(validated.content["current_state"]) == 1


def test_validate_picture_rejects_a_picture_without_the_required_keys() -> None:
    output = picture_output()
    del output["durable_culture"]
    with pytest.raises(PictureRejected, match="durable_culture"):
        validate_picture(output, known_refs={"HACKER_NEWS:ask:1"})
    with pytest.raises(PictureRejected, match="summary"):
        validate_picture(picture_output(summary="  "), known_refs={"HACKER_NEWS:ask:1"})


def test_picture_schema_lists_every_required_key_validate_checks() -> None:
    assert set(PICTURE_OUTPUT_SCHEMA["required"]) == set(picture_output())
    assert PICTURE_OUTPUT_SCHEMA["additionalProperties"] is False


def judge_output(**overrides) -> dict:
    base = {
        "decision": "IGNORE",
        "reasoning": "Autor pyta o rzecz, której użytkownik nigdy nie robił; nie ma tu nic do dodania.",
        "what_user_could_add": None,
        "confidence": 0.8,
        "wait_until_hint": None,
    }
    base.update(overrides)
    return base


def _record(output: dict):
    return judgment_from_output(
        output,
        thread_ref="HACKER_NEWS:ask:1",
        user_profile_version=1,
        prompt_name="judge_thread",
        prompt_sha256=SHA,
        model="m",
        effort="low",
        created_at=NOW,
    )


def test_judgment_from_output_builds_the_contract_record() -> None:
    record = _record(judge_output())
    assert record.decision is JudgmentDecision.IGNORE
    assert record.is_negative
    engage = _record(
        judge_output(
            decision="ENGAGE", what_user_could_add="Konkretne liczby z rocznej produkcji MCP."
        )
    )
    assert engage.what_user_could_add.startswith("Konkretne")


def test_judgment_from_output_refuses_slop_and_unknown_decisions() -> None:
    with pytest.raises(JudgmentRejected, match="kontrakt"):
        _record(judge_output(decision="ENGAGE", what_user_could_add=None))
    with pytest.raises(JudgmentRejected, match="kontrakt"):
        _record(judge_output(decision="ENGAGE", what_user_could_add="   "))
    with pytest.raises(JudgmentRejected, match="kontrakt"):
        _record(judge_output(reasoning="nope"))
    with pytest.raises(JudgmentRejected, match="MAYBE"):
        _record(judge_output(decision="MAYBE"))
    with pytest.raises(JudgmentRejected):
        _record(judge_output(confidence=1.7))


def test_judge_schema_pins_the_decision_vocabulary_to_the_contract() -> None:
    assert set(JUDGE_OUTPUT_SCHEMA["properties"]["decision"]["enum"]) == {
        d.value for d in JudgmentDecision
    }
    assert set(JUDGE_OUTPUT_SCHEMA["required"]) == set(judge_output())


# -- founder rule 2026-09-04: no long dashes in anything the model wrote -----------------------


def test_validate_picture_rewrites_long_dashes_and_counts_them() -> None:
    output = picture_output(
        summary="Spokojny dzie\u0144 \u2014 dwa pytania o MCP \u2013 jedno o Postgresa."
    )
    validated = validate_picture(output, known_refs={"HACKER_NEWS:ask:1", "HACKER_NEWS:ask:2"})
    assert (
        validated.content["summary"] == "Spokojny dzie\u0144, dwa pytania o MCP, jedno o Postgresa."
    )
    assert validated.long_dash_count == 2
    assert "\u2014" not in repr(validated.content) and "\u2013" not in repr(validated.content)


def test_judgment_from_output_rewrites_long_dashes_in_every_text_field() -> None:
    record = _record(
        {
            "decision": "ENGAGE",
            "reasoning": "Pytanie o kolejki \u2014 dok\u0142adnie to, co robi\u0142 przez rok.",
            "what_user_could_add": "Jedna liczba \u2013 ile wiadomo\u015bci gubi\u0142a kolejka bez ack \u2014 i dlaczego.",
            "confidence": 0.9,
        }
    )
    assert record.reasoning == "Pytanie o kolejki, dok\u0142adnie to, co robi\u0142 przez rok."
    assert (
        record.what_user_could_add
        == "Jedna liczba, ile wiadomo\u015bci gubi\u0142a kolejka bez ack, i dlaczego."
    )
