"""The pilot's pure parts: clipping that keeps the target and its parent chain, the situation
hash, the pacing ceiling and the slot arithmetic (docs/START-PILOTA-24H.md, points 1, 3, 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from community_observer import ObservedComment, ObservedThread, ThreadMetrics, raw_digest
from community_os_contracts import ObservationPlatform, Observed, UnknownReason
from control_plane.observe.reactions import MAX_MESSAGES
from control_plane.pilot.handlers import calls_allowed_so_far, planned_at, slot_of
from control_plane.pilot.situation import (
    build_situation,
    clip_keeping_target,
    messages_from_thread,
    situation_hash,
)
from control_plane.pilot.store import PilotRun

T0 = datetime(2026, 9, 6, 20, 0, tzinfo=UTC)
NOT_EXPOSED = Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)


def thread(external_id: str = "1") -> ObservedThread:
    return ObservedThread(
        platform=ObservationPlatform.HACKER_NEWS,
        community_key="ask",
        external_id=external_id,
        url=f"https://news.ycombinator.com/item?id={external_id}",
        title="Ask HN: anything",
        body="the question",
        author="op",
        created_at=T0 - timedelta(hours=3),
        observed_at=T0,
        metrics=ThreadMetrics(score=Observed[int].known(1), comment_count=Observed[int].known(0)),
        raw_sha256=raw_digest({"id": external_id}),
    )


def comment(n: int, *, parent: str | None, minutes_before: int, body: str | None = None):
    return ObservedComment(
        external_id=f"c{n}",
        parent_external_id=parent,
        author=f"u{n}",
        body=body if body is not None else f"comment {n}",
        created_at=T0 - timedelta(minutes=minutes_before),
        observed_at=T0,
        depth=0 if parent is None else 1,
        score=NOT_EXPOSED,
    )


def run(*, budget: int = 24, hours: int = 24, used: int = 0) -> PilotRun:
    return PilotRun(
        id=UUID(int=1),
        organization_id=UUID(int=2),
        workspace_id=UUID(int=3),
        community_id=UUID(int=4),
        status="ACTIVE",
        starts_at=T0,
        ends_at=T0 + timedelta(hours=hours),
        stopped_at=None,
        poll_interval_seconds=1800,
        model_calls_budget=budget,
        model_calls_used=used,
        max_generations_per_thread=2,
        profile_version_id=UUID(int=5),
        prompt_name="reaction_baseline",
        prompt_sha256="0" * 64,
        paused_job_types=("judge.thread",),
        restore={"poll_interval_seconds": 300},
        host_events=[],
        created_at=T0,
    )


# -- clipping ------------------------------------------------------------------------------


def test_the_post_is_the_root_and_an_orphan_comment_hangs_under_it() -> None:
    messages = messages_from_thread(
        thread(),
        [
            comment(1, parent=None, minutes_before=100),
            comment(2, parent="ghost", minutes_before=50),
        ],
    )
    assert [m.message_id for m in messages] == ["1", "c1", "c2"]
    assert messages[0].parent_id is None and messages[0].body.startswith("Ask HN: anything")
    assert messages[2].parent_id == "1", "rodzic poza odczytem → pod postem, nie widmo"


def test_clipping_keeps_the_target_and_its_parent_chain_and_marks_the_read_incomplete() -> None:
    # A deep chain c1 → c2 → c3 (old) plus many newer top-level comments.
    comments = [
        comment(1, parent=None, minutes_before=170),
        comment(2, parent="c1", minutes_before=160),
        comment(3, parent="c2", minutes_before=150),
    ]
    comments += [
        comment(10 + i, parent=None, minutes_before=140 - i) for i in range(MAX_MESSAGES + 20)
    ]
    messages = messages_from_thread(thread(), comments)
    kept, complete = clip_keeping_target(messages, target_id="c3")
    ids = {m.message_id for m in kept}
    assert not complete and len(kept) == MAX_MESSAGES
    assert {"c3", "c2", "c1", "1"} <= ids, "cel, jego rodzice i post zostają"
    newest = f"c{10 + MAX_MESSAGES + 19}"
    assert newest in ids, "reszta okna to najnowsze wiadomości"
    by_id = {m.message_id: m for m in kept}
    for m in kept:
        assert m.parent_id is None or m.parent_id in by_id, f"{m.message_id}: rodzic poza oknem"


def test_a_small_thread_is_complete_and_the_hash_changes_only_with_the_conversation() -> None:
    comments = [comment(1, parent=None, minutes_before=30)]
    messages = messages_from_thread(thread(), comments)
    kept, complete = clip_keeping_target(messages, target_id="1")
    assert complete and len(kept) == 2
    h1 = situation_hash(thread_external_id="1", target_id="1", messages=kept)
    h2 = situation_hash(thread_external_id="1", target_id="1", messages=kept)
    assert h1 == h2 and len(h1) == 64
    more = messages_from_thread(thread(), [*comments, comment(2, parent=None, minutes_before=1)])
    kept2, _ = clip_keeping_target(more, target_id="1")
    assert situation_hash(thread_external_id="1", target_id="1", messages=kept2) != h1
    edited = messages_from_thread(
        thread(), [comment(1, parent=None, minutes_before=30, body="edited")]
    )
    kept3, _ = clip_keeping_target(edited, target_id="1")
    assert situation_hash(thread_external_id="1", target_id="1", messages=kept3) != h1


def test_build_situation_is_a_new_entry_with_the_platform_truncation_flag() -> None:
    situation, digest, complete = build_situation(
        situation_id="pilot-x-1",
        platform="HACKER_NEWS",
        community_display_name="HN Ask",
        language="en",
        thread=thread(),
        comments=[comment(1, parent=None, minutes_before=10)],
        target_id="1",
        read_at=T0,
        comments_truncated=True,
        recent_own_drafts=("first sentence of a draft.",),
    )
    assert situation.relation == "NEW_ENTRY" and situation.target_message_id == "1"
    assert situation.recent_own_drafts == ("first sentence of a draft.",)
    assert len(digest) == 64 and complete is False, (
        "drzewo obcięte przez platformę = niepełny odczyt"
    )


# -- budget pacing and slots --------------------------------------------------------------------


def test_pacing_spreads_the_budget_over_the_window_and_never_exceeds_it() -> None:
    r = run(budget=24, hours=24)
    assert calls_allowed_so_far(r, T0) == 2, "smoke + jedno na start"
    assert calls_allowed_so_far(r, T0 + timedelta(minutes=1)) == 2, "minutę po starcie nic nowego"
    assert calls_allowed_so_far(r, T0 + timedelta(hours=6)) == 1 + 5 + 1
    assert calls_allowed_so_far(r, T0 + timedelta(hours=12)) == 1 + 11 + 1
    assert calls_allowed_so_far(r, T0 + timedelta(hours=24)) == 24
    assert calls_allowed_so_far(r, T0 + timedelta(hours=40)) == 24, "po oknie nic ponad budżet"
    assert calls_allowed_so_far(r, T0 - timedelta(hours=1)) == 2, "przed startem tak jak na starcie"


def test_slots_are_thirty_minutes_from_the_fixed_start() -> None:
    r = run()
    assert slot_of(r, T0) == 0 and slot_of(r, T0 + timedelta(minutes=29)) == 0
    assert (
        slot_of(r, T0 + timedelta(minutes=30)) == 1
        and slot_of(r, T0 + timedelta(hours=23, minutes=59)) == 47
    )
    assert planned_at(r, 3) == T0 + timedelta(minutes=90)
    assert not r.active_at(T0 + timedelta(hours=24)) and r.active_at(T0 + timedelta(hours=23))
