"""`observe/scheduling.py` — refs, slots and the once-a-day rule. Pure; the queue half is in the
handler tests against PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.observe.scheduling import (
    OBSERVE_JOB_TYPES,
    SNAPSHOT_DELAYS,
    community_ref,
    day_key,
    parse_ref,
    picture_is_due,
    poll_slot,
    thread_ref,
)


def test_refs_round_trip_and_the_kind_is_checked() -> None:
    community_id, thread_id = uuid4(), uuid4()
    assert parse_ref(community_ref(community_id), "community") == community_id
    assert parse_ref(thread_ref(thread_id), "thread") == thread_id
    with pytest.raises(ValueError, match="oczekiwano"):
        parse_ref(community_ref(community_id), "thread")
    with pytest.raises(ValueError):
        parse_ref(None, "thread")
    with pytest.raises(ValueError):
        parse_ref("thread/not-a-uuid", "thread")


def test_poll_slots_are_stable_within_an_interval_and_advance_across_it() -> None:
    interval = 600
    t0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert poll_slot(t0, interval) == poll_slot(t0 + timedelta(seconds=599), interval)
    assert poll_slot(t0 + timedelta(seconds=600), interval) == poll_slot(t0, interval) + 1


def test_day_key_is_the_utc_date() -> None:
    late_warsaw = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)  # 03:30 in Warsaw
    assert day_key(late_warsaw) == "2026-09-03"


def test_picture_is_due_once_a_day_after_the_hour_gate() -> None:
    early = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    later = datetime(2026, 9, 3, 6, 0, tzinfo=UTC)
    assert not picture_is_due(early, None), "przed 05:00 UTC obraz opisałby kilka minut"
    assert picture_is_due(later, None)
    yesterday = datetime(2026, 9, 2, 23, 0, tzinfo=UTC)
    today = datetime(2026, 9, 3, 5, 30, tzinfo=UTC)
    assert picture_is_due(later, yesterday)
    assert not picture_is_due(later, today), "obraz z dzisiaj już jest — raz dziennie"


def test_the_job_type_vocabulary_and_snapshot_schedule_are_explicit() -> None:
    assert len(OBSERVE_JOB_TYPES) == 6
    assert all("." in job_type for job_type in OBSERVE_JOB_TYPES)
    assert [label for label, _ in SNAPSHOT_DELAYS] == ["1h", "6h", "24h"]
    assert [delay for _, delay in SNAPSHOT_DELAYS] == [3600, 21600, 86400]
