"""The shape of an observation, and the two honesty rules it enforces."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from community_observer import (
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    ThreadSnapshot,
    html_to_text,
    raw_digest,
)
from community_os_contracts import ObservationPlatform, Observed, UnknownReason
from pydantic import ValidationError

AWARE = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
NAIVE = datetime(2026, 9, 3, 10, 0)
SHA = "0" * 64


def thread(**overrides) -> ObservedThread:
    base = dict(
        platform=ObservationPlatform.HACKER_NEWS,
        community_key="ask",
        external_id="1",
        url="https://news.ycombinator.com/item?id=1",
        title="Ask HN: anything",
        created_at=AWARE,
        observed_at=AWARE,
        metrics=ThreadMetrics(score=Observed[int].known(3), comment_count=Observed[int].known(0)),
        raw_sha256=SHA,
    )
    base.update(overrides)
    return ObservedThread(**base)


def comment(**overrides) -> ObservedComment:
    base = dict(
        external_id="c1",
        created_at=AWARE,
        observed_at=AWARE,
        depth=0,
        score=Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM),
    )
    base.update(overrides)
    return ObservedComment(**base)


def test_raw_digest_is_a_property_of_the_data_not_of_key_order() -> None:
    a = raw_digest({"title": "x", "points": 3, "nested": {"b": 1, "a": 2}})
    b = raw_digest({"nested": {"a": 2, "b": 1}, "points": 3, "title": "x"})
    assert a == b
    assert len(a) == 64
    assert raw_digest({"title": "x", "points": 4}) != a


@pytest.mark.parametrize("field", ["created_at", "observed_at"])
def test_a_naive_timestamp_on_a_thread_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match="strefę"):
        thread(**{field: NAIVE})


@pytest.mark.parametrize("field", ["created_at", "observed_at"])
def test_a_naive_timestamp_on_a_comment_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match="strefę"):
        comment(**{field: NAIVE})


def test_a_truncated_digest_is_refused() -> None:
    with pytest.raises(ValidationError, match="SHA-256"):
        thread(raw_sha256=SHA[:40])


def test_unknown_metrics_are_unknown_not_zero() -> None:
    hidden = ThreadMetrics(
        score=Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM),
        comment_count=Observed[int].known(2),
    )
    assert not hidden.score.is_known
    with pytest.raises(ValueError, match="UNKNOWN"):
        hidden.score.require()
    assert hidden.comment_count.require() == 2


def test_thread_ref_spelling_matches_the_judgment_contract() -> None:
    assert thread().ref == "HACKER_NEWS:ask:1"


def test_comment_depth_and_parent_agree() -> None:
    with pytest.raises(ValidationError, match="depth"):
        comment(parent_external_id="c0", depth=0)
    with pytest.raises(ValidationError, match="depth"):
        comment(parent_external_id=None, depth=1)
    assert comment(parent_external_id="c0", depth=1).depth == 1


def test_snapshot_refuses_duplicate_comment_ids() -> None:
    with pytest.raises(ValidationError, match="zduplikowane"):
        ThreadSnapshot(thread=thread(), comments=(comment(), comment()))


def test_unique_participants_excludes_the_author_and_deleted_and_goes_unknown_when_truncated() -> (
    None
):
    t = thread(author="op")
    comments = (
        comment(external_id="a", author="op"),
        comment(external_id="b", author="alice"),
        comment(external_id="c", author="alice"),
        comment(external_id="d", author=None),  # deleted — still a row, not a participant
        comment(external_id="e", author="bob"),
    )
    complete = ThreadSnapshot(thread=t, comments=comments)
    assert complete.unique_participants.require() == 2
    truncated = ThreadSnapshot(thread=t, comments=comments, comments_truncated=True)
    assert not truncated.unique_participants.is_known


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("plain", "plain"),
        ("first<p>second", "first\n\nsecond"),
        ("a &amp; b &quot;c&quot;", 'a & b "c"'),
        (
            'see <a href="https://x.test/b">the benchmark</a>.',
            "see the benchmark (https://x.test/b).",
        ),
        ('<a href="https://x.test">https://x.test</a>', "https://x.test"),
        ("<i>emph</i> and <pre><code>x = 1</code></pre>", "emph and\nx = 1"),
        ("one<p><p><p>two", "one\n\ntwo"),
    ],
)
def test_html_to_text(markup: str | None, expected: str | None) -> None:
    assert html_to_text(markup) == expected
