"""The voice card's two layers, measured without a database (etap 3 M0).

Deterministic metrics over every comment; a sample that puts replied-to comments first and spans
the window; a validator that drops observations citing comments the model never saw, counts them,
and rewrites long dashes (founder rule 2026-09-04).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from community_os_contracts import Observed, UnknownReason
from control_plane.observe.voice import (
    CONVERSATION_REPLIES,
    VOICE_MIN_COMMENTS,
    VOICE_OUTPUT_SCHEMA,
    VOICE_SAMPLE_LIMIT,
    VOICE_SAMPLE_REPLIED,
    VOICE_WINDOW,
    CommentVoice,
    VoiceRejected,
    validate_voice,
    voice_input,
    voice_metrics,
    voice_sample,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
UNKNOWN = Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)


def comment(
    n: int,
    *,
    body: str = "plain text",
    replies: int = 0,
    depth: int = 0,
    score: Observed[int] = UNKNOWN,
    author: str | None = None,
) -> CommentVoice:
    return CommentVoice(
        external_id=f"c{n}",
        thread_ref="HACKER_NEWS:ask:1",
        thread_title="Ask HN: anything",
        parent_external_id=None if depth == 0 else "c0",
        author=author if author is not None else f"a{n}",
        body=body,
        created_at=T0 + timedelta(minutes=n),
        depth=depth,
        score=score,
        reply_count=replies,
    )


# -- metrics -----------------------------------------------------------------------------


def test_metrics_over_an_empty_window_are_a_count_of_zero_not_a_crash() -> None:
    assert voice_metrics([]) == {"comment_count": 0}


def test_metrics_count_every_comment_and_report_unknown_scores_as_a_reason() -> None:
    comments = [
        comment(1, body="I think this is right.", replies=3),
        comment(2, body="Why would that work?", replies=0),
        comment(3, body="See https://example.org for the numbers", replies=1),
        comment(4, body="x" * 400, depth=1, score=Observed[int].known(12)),
        comment(5, body="We tried `foo()` and it broke", replies=4, depth=2),
    ]
    m = voice_metrics(comments)
    assert m["comment_count"] == 5
    assert m["length_chars"]["median"] == len("We tried `foo()` and it broke")
    assert m["length_chars"]["p10"] <= m["length_chars"]["median"] <= m["length_chars"]["p90"]
    assert m["share_first_person"] == 0.4, "I … / We … = 2 z 5"
    assert m["share_question"] == 0.2 and m["share_link"] == 0.2 and m["share_code"] == 0.2
    assert m["share_top_level"] == 0.6 and m["depth_max"] == 2
    assert m["replies"]["share_with_any_reply"] == 0.6
    assert m["replies"]["with_3_or_more"] == 2 and CONVERSATION_REPLIES == 3
    assert m["replies"]["median_length_with_3_or_more"] == pytest.approx(
        (len("I think this is right.") + len("We tried `foo()` and it broke")) / 2
    )
    assert m["replies"]["share_first_person_with_3_or_more"] == 1.0
    assert m["score"] == {
        "known": 1,
        "median_known": 12,
        "unknown_reason": "NOT_EXPOSED_BY_PLATFORM",
    }, "HN nie pokazuje punktów komentarzy: powód, nie zero"
    assert m["authors"] == {"distinct": 5, "top_author_share": 0.2}


# -- sample --------------------------------------------------------------------------------


def test_the_sample_puts_replied_comments_first_then_spans_the_window() -> None:
    quiet = [comment(n) for n in range(200)]
    loud = [comment(1000 + n, replies=5 - (n % 5)) for n in range(40)]
    sample = voice_sample(quiet + loud)

    assert len(sample) == VOICE_SAMPLE_LIMIT
    head = sample[:VOICE_SAMPLE_REPLIED]
    assert all(c.reply_count >= 1 for c in head)
    assert [c.reply_count for c in head] == sorted((c.reply_count for c in head), reverse=True)
    tail = sample[VOICE_SAMPLE_REPLIED:]
    minutes = [int((c.created_at - T0).total_seconds() // 60) for c in tail]
    assert minutes == sorted(minutes) and minutes[0] < 20 and minutes[-1] > 180, (
        "reszta próbki jest rozłożona po całym oknie, nie z ostatniej godziny"
    )
    assert len({c.external_id for c in sample}) == len(sample), "bez duplikatów"


def test_a_small_window_is_sampled_whole_and_deterministically() -> None:
    comments = [comment(n, replies=n % 2) for n in range(25)]
    first = voice_sample(comments)
    assert len(first) == 25 and first == voice_sample(list(reversed(comments)))


def test_the_window_and_the_threshold_are_what_the_card_promises() -> None:
    assert timedelta(days=7) == VOICE_WINDOW
    assert VOICE_MIN_COMMENTS == 20


# -- input -------------------------------------------------------------------------------


def test_voice_input_carries_metrics_sample_and_previous_card() -> None:
    comments = [comment(1, body="a" * 900, replies=2), comment(2, body="short")]
    payload = voice_input(
        community_display_name="HN Ask",
        platform="HACKER_NEWS",
        window_start=T0,
        window_end=T0 + VOICE_WINDOW,
        metrics=voice_metrics(comments),
        sample=voice_sample(comments),
        previous_card={"how_people_write": "krótko"},
    )
    assert payload["sample_size"] == 2 and payload["metrics"]["comment_count"] == 2
    assert payload["sample"][0]["id"] == "c1" and payload["sample"][0]["replies"] == 2
    assert len(payload["sample"][0]["body"]) == 700, "treść przycięta do VOICE_BODY_CHARS"
    assert payload["sample"][0]["score"] == "UNKNOWN"
    assert payload["previous_card"] == {"how_people_write": "krótko"}


# -- validation ------------------------------------------------------------------------------


def voice_output(**overrides) -> dict:
    base = {
        "how_people_write": "Krótko, w pierwszej osobie — z jednym konkretem.",
        "what_gets_replies": [
            {"observation": "Własne doświadczenie z liczbą", "comment_ids": ["c1", "c5"]},
            {"observation": "Zmyślone", "comment_ids": ["c404"]},
            {"observation": "Bez dowodu", "comment_ids": []},
        ],
        "what_gets_ignored": [{"observation": "Streszczenia posta", "comment_ids": ["c2"]}],
        "write_like_this": [
            "Jedna liczba na komentarz",
            "",
            "Pytaj o szczegół \u2013 nie o zdanie",
        ],
        "avoid": ["„Great point!”"],
        "example_quotes": [
            {"comment_id": "c5", "why": "Cztery odpowiedzi — dwa zdania."},
            {"comment_id": "c999", "why": "nie istnieje"},
        ],
    }
    base.update(overrides)
    return base


def test_validate_voice_drops_invented_citations_counts_them_and_rewrites_dashes() -> None:
    validated = validate_voice(voice_output(), known_ids={"c1", "c2", "c5"})

    assert (
        validated.content["how_people_write"] == "Krótko, w pierwszej osobie, z jednym konkretem."
    )
    assert validated.content["what_gets_replies"] == [
        {"observation": "Własne doświadczenie z liczbą", "comment_ids": ["c1", "c5"]}
    ]
    assert validated.content["what_gets_ignored"][0]["comment_ids"] == ["c2"]
    assert validated.content["write_like_this"] == [
        "Jedna liczba na komentarz",
        "Pytaj o szczegół, nie o zdanie",
    ]
    assert validated.content["example_quotes"] == [
        {"comment_id": "c5", "why": "Cztery odpowiedzi, dwa zdania."}
    ]
    assert validated.invalid_quotes == ("c404", "c999")
    assert validated.invalid_quote_count == 2
    assert validated.dropped_items == 4, "c404, pusta lista id, pusta reguła, c999"
    assert validated.long_dash_count == 3
    assert "\u2014" not in repr(validated.content) and "\u2013" not in repr(validated.content)
    assert "metrics" not in validated.content, "metryki dokłada handler, nie model"


def test_validate_voice_rejects_a_card_without_the_required_keys_or_summary() -> None:
    output = voice_output()
    del output["avoid"]
    with pytest.raises(VoiceRejected, match="avoid"):
        validate_voice(output, known_ids={"c1"})
    with pytest.raises(VoiceRejected, match="how_people_write"):
        validate_voice(voice_output(how_people_write="   "), known_ids={"c1"})


def test_the_schema_requires_exactly_the_keys_the_validator_checks() -> None:
    assert set(VOICE_OUTPUT_SCHEMA["required"]) == set(VOICE_OUTPUT_SCHEMA["properties"])
    assert set(VOICE_OUTPUT_SCHEMA["required"]) == set(voice_output())
    assert VOICE_OUTPUT_SCHEMA["additionalProperties"] is False
