"""A spent publication limit says WHEN the next slot frees (27.09).

The delegation refused a post with "publikacje: 1 z 1 w ostatniej godzinie" and nothing else. The
agent planned its return six minutes before the hour freed, was refused again, and spent one of
its three planned returns on nothing. The refusal now names the moment the oldest publication
leaves the window; without one (nothing in the window, a count from elsewhere) it says nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from control_plane.presence.budget import Limits, Spent

LIMITS = Limits(
    publications_per_hour=1, publications_per_day=6, own_posts_per_day=2, model_turns_per_day=12
)


def test_a_spent_hour_says_when_it_frees() -> None:
    spent = Spent(
        model_turns_today=3,
        publications_today=1,
        publications_this_hour=1,
        own_posts_today=0,
        limits=LIMITS,
        hour_frees_at=datetime(2026, 9, 27, 1, 59, 18, tzinfo=UTC),
    )
    assert spent.publication_blocked() == (
        "publikacje: 1 z 1 w ostatniej godzinie, następne miejsce o 01:59 UTC"
    )
    assert spent.own_post_blocked() == spent.publication_blocked()
    assert spent.exhausted() == spent.publication_blocked()


def test_a_spent_day_says_when_it_frees_and_room_says_nothing() -> None:
    day = Spent(
        model_turns_today=3,
        publications_today=6,
        publications_this_hour=0,
        own_posts_today=0,
        limits=LIMITS,
        day_frees_at=datetime(2026, 9, 28, 0, 47, tzinfo=UTC),
    )
    assert day.publication_blocked() == (
        "publikacje: 6 z 6 w ostatniej dobie, następne miejsce o 00:47 UTC"
    )
    room = Spent(
        model_turns_today=0,
        publications_today=0,
        publications_this_hour=0,
        own_posts_today=0,
        limits=LIMITS,
    )
    assert room.publication_blocked() is None
