"""The schedule of "Send all": the limits hold, the jitter stays inside its bounds, the order is the
one the founder asked for, and a reply that cannot be placed is left out rather than squeezed in (v11).

PURE: no database and no clock. `plan_schedule` and `replay_schedule` take the time and the dice as
arguments, so every assertion here is about arithmetic, from the outside in:

  * THE LIMITS: in ANY rolling hour at most `per_hour` publications and in any rolling day at most
    `per_day` (own posts also `own_per_day`), counting what already happened; checked by an
    independent brute-force verifier on hand-made cases and on a few hundred random ones;
  * THE JITTER: consecutive replies are `1 hour / per_hour` apart plus 1 to 4 minutes, the first is
    within 45 seconds of the click, and the jitter only ever adds;
  * THE ORDER: a fresh post on X first, then the rest by youngest thread, an own post and an
    unknown age last;
  * THE DEADLINES: past the horizon, past the draft's approval window or past the scope's own
    deadline a reply is LEFT OUT, with the code the card words;
  * THE REPLAY at the click: the moments shown, or later, never earlier; refused when it slips by
    more than ten minutes;
  * ONE definition of a window: whatever `plan_schedule` places passes `fits` in the order it will
    be approved, so the gate under the project's lock never refuses a planned slot.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from control_plane.presence import batch_plan as bp
from control_plane.presence.batch_plan import Candidate

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)
MIN = timedelta(minutes=1)


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _cand(
    n: int,
    *,
    platform: str = "REDDIT",
    own: bool = False,
    age: timedelta | None = timedelta(hours=2),
    expires: timedelta | None = timedelta(hours=48),
) -> Candidate:
    return Candidate(
        revision_id=_id(n),
        platform=platform,
        own_post=own,
        thread_at=NOW - age if age is not None else None,
        expires_at=NOW + expires if expires is not None else None,
    )


class _Floor(random.Random):
    """Dice that always roll the bottom of the range: the jitter at its minimum."""

    def uniform(self, a: float, b: float) -> float:
        return a


class _Ceiling(random.Random):
    """Dice that always roll the top of the range: the jitter at its maximum."""

    def uniform(self, a: float, b: float) -> float:
        return b


def _plan(cands, *, per_hour=4, per_day=20, own=1, events=(), own_events=(), rng=None, **extra):
    return bp.plan_schedule(
        cands,
        now=NOW,
        per_hour=per_hour,
        per_day=per_day,
        own_per_day=own,
        events=list(events),
        own_events=list(own_events),
        rng=rng or random.Random(7),
        **extra,
    )


def _times(schedule: bp.Schedule) -> list[datetime]:
    return [p.at for p in schedule.placed]


def _verify(times, *, per_hour, per_day, own_times=(), own_per_day=None) -> None:
    """The independent verifier: every rolling window (closed) around every moment is within the limit."""
    pool = sorted(times)
    for end in pool:
        in_hour = sum(1 for x in pool if end - HOUR <= x <= end)
        in_day = sum(1 for x in pool if end - DAY <= x <= end)
        assert in_hour <= per_hour, f"{in_hour} w godzinie do {end}"
        assert in_day <= per_day, f"{in_day} w dobie do {end}"
    if own_per_day is not None:
        own = sorted(own_times)
        for end in own:
            assert sum(1 for x in own if end - DAY <= x <= end) <= own_per_day


# ---------------------------------------------------------------------------
# The limits
# ---------------------------------------------------------------------------


def test_five_replies_at_one_an_hour_are_an_hour_apart_and_none_is_refused() -> None:
    """The case the whole module exists for: the budget gate counted the queue and refused the second."""
    schedule = _plan([_cand(n) for n in range(1, 6)], per_hour=1, per_day=6)
    times = _times(schedule)
    assert len(times) == 5 and not schedule.left
    for earlier, later in pairwise(times):
        assert later - earlier >= HOUR + bp.JITTER_MIN
    _verify(times, per_hour=1, per_day=6)


def test_at_four_an_hour_the_replies_are_a_quarter_of_an_hour_apart_not_in_a_burst() -> None:
    schedule = _plan([_cand(n) for n in range(1, 9)], per_hour=4, per_day=20, rng=_Floor())
    times = _times(schedule)
    assert schedule.gap == timedelta(minutes=15)
    gaps = [later - earlier for earlier, later in pairwise(times)]
    assert all(g == timedelta(minutes=15) + bp.JITTER_MIN for g in gaps), gaps
    assert times[0] == NOW, "the first reply goes out at the click (jitter at its floor)"
    # "N now": only the first is within the window of "now", the rest are spread, never a burst.
    assert schedule.to_json(now=NOW)["teraz"] == 1
    _verify(times, per_hour=4, per_day=20)


def test_the_jitter_stays_inside_its_bounds_on_both_ends() -> None:
    cands = [_cand(n) for n in range(1, 7)]
    floor, ceiling = _times(_plan(cands, rng=_Floor())), _times(_plan(cands, rng=_Ceiling()))
    gap = timedelta(minutes=15)
    assert floor[0] == NOW and ceiling[0] == NOW + bp.FIRST_JITTER_MAX
    for times, jitter in ((floor, bp.JITTER_MIN), (ceiling, bp.JITTER_MAX)):
        for earlier, later in pairwise(times):
            assert later - earlier == gap + jitter
    # and any other dice stay between the two
    for seed in range(40):
        times = _times(_plan(cands, rng=random.Random(seed)))
        assert NOW <= times[0] <= NOW + bp.FIRST_JITTER_MAX
        for earlier, later in pairwise(times):
            assert gap + bp.JITTER_MIN <= later - earlier <= gap + bp.JITTER_MAX
    assert bp.JITTER_MIN == MIN and bp.JITTER_MAX == 4 * MIN, "'a few minutes', 1 to 4"


def test_what_already_went_out_counts_against_the_first_slot() -> None:
    """An attempt ten minutes ago at one an hour: nothing until it leaves the hour."""
    schedule = _plan(
        [_cand(1), _cand(2)], per_hour=1, per_day=6, events=[NOW - 10 * MIN], rng=_Floor()
    )
    first, second = _times(schedule)
    assert first >= NOW - 10 * MIN + HOUR, "not inside the hour of the attempt"
    assert first - (NOW - 10 * MIN + HOUR) <= bp.JITTER_MAX + bp.EDGE, "and not later than needed"
    assert second - first >= HOUR
    _verify([NOW - 10 * MIN, *_times(schedule)], per_hour=1, per_day=6)


def test_a_queued_request_with_a_later_moment_counts_in_ITS_hour_not_this_one() -> None:
    """A request scheduled for three hours from now is no reason to hold the first reply back now."""
    later = NOW + timedelta(hours=3)
    schedule = _plan([_cand(1)], per_hour=1, per_day=6, events=[later], rng=_Floor())
    assert _times(schedule) == [NOW]
    # ... but it IS in the way of a moment inside its own hour: the planner steps around it.
    near = NOW + 20 * MIN
    schedule = _plan([_cand(1), _cand(2)], per_hour=1, per_day=6, events=[near], rng=_Floor())
    times = _times(schedule)
    _verify([near, *times], per_hour=1, per_day=6)
    assert all(abs(t - near) > HOUR for t in times), times


def test_the_day_limit_leaves_the_rest_waiting_and_never_exceeds_it() -> None:
    schedule = _plan([_cand(n) for n in range(1, 11)], per_hour=4, per_day=6)
    assert len(schedule.placed) == 6
    assert {item.code for item in schedule.left} == {bp.LEFT_LIMIT} and len(schedule.left) == 4
    _verify(_times(schedule), per_hour=4, per_day=6)


def test_replies_already_used_today_shrink_the_day() -> None:
    used = [NOW - timedelta(hours=h) for h in (3, 5, 7)]
    schedule = _plan([_cand(n) for n in range(1, 8)], per_hour=4, per_day=6, events=used)
    assert len(schedule.placed) == 3, "three of the six are already spent"
    _verify([*used, *_times(schedule)], per_hour=4, per_day=6)


def test_the_window_edge_is_closed_one_event_exactly_an_hour_ago_still_counts() -> None:
    event = NOW - HOUR  # exactly an hour ago
    assert not bp.fits([event], NOW, per_hour=1, per_day=6), "a closed window includes its edge"
    assert bp.fits([event], NOW + bp.EDGE, per_hour=1, per_day=6)
    day = NOW - DAY
    assert not bp.fits([day], NOW, per_hour=6, per_day=1)
    assert bp.fits([day], NOW + bp.EDGE, per_hour=6, per_day=1)


def test_a_moment_inserted_before_a_queued_one_is_checked_against_that_ones_hour_too() -> None:
    """Three queued at 10, 20, 30 minutes at three an hour: a fourth at 5 would crowd the one at 30."""
    queued = [NOW + n * MIN for n in (10, 20, 30)]
    assert not bp.fits(queued, NOW + 5 * MIN, per_hour=3, per_day=20)
    assert bp.fits(queued, NOW + 5 * MIN, per_hour=4, per_day=20)


def test_own_posts_have_their_own_daily_limit_on_top_of_the_general_one() -> None:
    cands = [_cand(1, own=True, age=None), _cand(2, own=True, age=None), _cand(3)]
    schedule = _plan(cands, own=1)
    placed = {p.revision_id for p in schedule.placed}
    assert _id(3) in placed and len([c for c in (_id(1), _id(2)) if c in placed]) == 1
    assert [item.code for item in schedule.left] == [bp.LEFT_OWN]
    # an own post published this morning uses the day's one
    schedule = _plan([_cand(1, own=True, age=None)], own=1, own_events=[NOW - 2 * HOUR])
    assert not schedule.placed and schedule.left[0].code == bp.LEFT_OWN


def test_a_limit_below_one_places_nothing() -> None:
    for kwargs in ({"per_hour": 0}, {"per_day": 0}):
        schedule = _plan([_cand(1)], **kwargs)
        assert not schedule.placed and schedule.left[0].code == bp.LEFT_NO_LIMIT


@pytest.mark.parametrize("seed", range(200))
def test_random_worlds_never_break_a_limit_and_the_gate_would_pass_every_slot(seed: int) -> None:
    """The planner and the gate are one function: whatever is placed passes `fits` in order."""
    rng = random.Random(seed)
    per_hour, per_day = rng.randint(1, 6), rng.randint(1, 20)
    own_limit = rng.randint(0, 3)
    events = [NOW + timedelta(minutes=rng.randint(-1400, 400)) for _ in range(rng.randint(0, 8))]
    own_events = [e for e in events if rng.random() < 0.3]
    cands = [
        _cand(
            n,
            platform=rng.choice(["X", "REDDIT"]),
            own=rng.random() < 0.2,
            age=None if rng.random() < 0.2 else timedelta(minutes=rng.randint(1, 3000)),
            expires=rng.choice([None, timedelta(hours=rng.randint(1, 60))]),
        )
        for n in range(1, rng.randint(2, 14))
    ]
    # the world it starts from may itself be over the limit; the planner has to be right about
    # what it ADDS, so start from a world that is not.
    seed_world = []
    for e in sorted(events):
        if bp.fits(seed_world, e, per_hour=per_hour, per_day=per_day):
            seed_world.append(e)
    own_world = [e for e in own_events if e in seed_world]
    schedule = bp.plan_schedule(
        cands,
        now=NOW,
        per_hour=per_hour,
        per_day=per_day,
        own_per_day=own_limit,
        events=seed_world,
        own_events=own_world,
        rng=random.Random(seed + 1000),
    )
    all_times = [*seed_world, *_times(schedule)]
    _verify(all_times, per_hour=per_hour, per_day=per_day)
    by_id = {c.revision_id: c for c in cands}
    new_own = [p.at for p in schedule.placed if by_id[p.revision_id].own_post]
    world_ok = all(
        sum(1 for x in own_world if end - DAY <= x <= end) <= own_limit for end in own_world
    )
    if world_ok:  # a world that is already over the own limit says nothing about what is added
        _verify(
            [],
            per_hour=per_hour,
            per_day=per_day,
            own_times=[*own_world, *new_own],
            own_per_day=own_limit,
        )
    elif own_limit == 0:
        assert not new_own
    # the gate, replayed in approval order: nothing it would refuse
    committed = list(seed_world)
    own_committed = list(own_world)
    for placed in schedule.placed:
        own = by_id[placed.revision_id].own_post
        assert bp.fits(
            committed,
            max(placed.at, NOW),
            per_hour=per_hour,
            per_day=per_day,
            own_events=own_committed if own else None,
            own_per_day=own_limit if own else None,
        ), placed
        committed.append(placed.at)
        if own:
            own_committed.append(placed.at)
    # nothing is both placed and left, and nothing is lost
    seen = [p.revision_id for p in schedule.placed] + [item.revision_id for item in schedule.left]
    assert sorted(seen) == sorted(by_id)
    # and the horizon holds
    assert all(p.at <= NOW + bp.HORIZON for p in schedule.placed)


# ---------------------------------------------------------------------------
# The order
# ---------------------------------------------------------------------------


def test_a_fresh_post_on_x_goes_before_reddit_however_young_the_reddit_thread() -> None:
    cands = [
        _cand(1, platform="REDDIT", age=timedelta(minutes=10)),
        _cand(2, platform="X", age=timedelta(hours=5)),
        _cand(3, platform="REDDIT", age=timedelta(hours=1)),
    ]
    order = [c.revision_id for c in bp.order_candidates(cands, now=NOW)]
    assert order == [_id(2), _id(1), _id(3)]


def test_a_stale_post_on_x_is_not_preferred_over_a_fresh_reddit_one() -> None:
    cands = [
        _cand(1, platform="X", age=timedelta(hours=30)),
        _cand(2, platform="REDDIT", age=timedelta(hours=2)),
    ]
    assert [c.revision_id for c in bp.order_candidates(cands, now=NOW)] == [_id(2), _id(1)]
    # the edge: six hours is still fresh, a minute more is not
    edge = [
        _cand(1, platform="REDDIT", age=timedelta(minutes=5)),
        _cand(2, platform="X", age=bp.FRESH_X),
        _cand(3, platform="X", age=bp.FRESH_X + MIN),
    ]
    assert [c.revision_id for c in bp.order_candidates(edge, now=NOW)] == [_id(2), _id(1), _id(3)]


def test_within_a_group_the_youngest_thread_goes_first_and_no_thread_goes_last() -> None:
    cands = [
        _cand(1, age=None, own=True),
        _cand(2, age=timedelta(hours=9)),
        _cand(3, age=timedelta(hours=1)),
        _cand(4, age=None),
        _cand(5, platform="X", age=timedelta(hours=2)),
        _cand(6, platform="X", age=timedelta(minutes=20)),
    ]
    assert [c.revision_id for c in bp.order_candidates(cands, now=NOW)] == [
        _id(6),
        _id(5),
        _id(3),
        _id(2),
        _id(1),
        _id(4),
    ]


def test_the_order_is_the_same_for_the_same_input_and_ties_break_by_the_window() -> None:
    same_age = timedelta(hours=3)
    cands = [
        _cand(2, age=same_age, expires=timedelta(hours=10)),
        _cand(1, age=same_age, expires=timedelta(hours=5)),
        _cand(3, age=same_age, expires=timedelta(hours=10)),
    ]
    first = [c.revision_id for c in bp.order_candidates(cands, now=NOW)]
    assert first == [c.revision_id for c in bp.order_candidates(list(reversed(cands)), now=NOW)]
    assert first == [_id(1), _id(2), _id(3)]


# ---------------------------------------------------------------------------
# The deadlines
# ---------------------------------------------------------------------------


def test_a_reply_whose_window_closes_before_its_turn_is_left_out_not_squeezed_in() -> None:
    cands = [
        _cand(1, age=timedelta(hours=1)),
        _cand(2, age=timedelta(hours=2), expires=timedelta(minutes=30)),
    ]
    schedule = _plan(cands, per_hour=1, per_day=6, rng=_Floor())
    assert [p.revision_id for p in schedule.placed] == [_id(1)]
    assert [(i.revision_id, i.code) for i in schedule.left] == [(_id(2), bp.LEFT_WINDOW)]


def test_a_reply_is_placed_a_tick_margin_before_its_window_closes_not_at_the_edge() -> None:
    """The publisher ticks every ten minutes; a moment closer than that to the end is not safe."""
    ok = _plan([_cand(1, expires=bp.TICK_MARGIN + 5 * MIN)], rng=_Floor())
    assert _times(ok) == [NOW] and not ok.left
    late = _plan([_cand(1, expires=bp.TICK_MARGIN - MIN)], rng=_Floor())
    assert not late.placed and [i.code for i in late.left] == [bp.LEFT_WINDOW]


def test_a_reply_past_the_horizon_is_left_for_another_batch() -> None:
    schedule = _plan([_cand(n) for n in range(1, 16)], per_hour=1, per_day=50)
    last = _times(schedule)[-1]
    assert last <= NOW + bp.HORIZON
    assert len(schedule.placed) <= 12 and schedule.left
    assert {i.code for i in schedule.left} == {bp.LEFT_LIMIT}


def test_the_signed_scope_deadline_binds_every_moment() -> None:
    ends = NOW + 90 * MIN + bp.TICK_MARGIN
    schedule = _plan(
        [_cand(n) for n in range(1, 5)], per_hour=1, per_day=6, valid_until=ends, rng=_Floor()
    )
    assert all(t <= ends - bp.TICK_MARGIN for t in _times(schedule))
    assert len(schedule.placed) == 2 and {i.code for i in schedule.left} == {bp.LEFT_SCOPE}


# ---------------------------------------------------------------------------
# The replay at the click
# ---------------------------------------------------------------------------


def _replay(schedule, cands, *, now, per_hour=4, per_day=20, events=(), **extra):
    by_id = {c.revision_id: c for c in cands}
    return bp.replay_schedule(
        [(by_id[p.revision_id], p.at) for p in schedule.placed],
        now=now,
        per_hour=per_hour,
        per_day=per_day,
        own_per_day=1,
        events=list(events),
        own_events=[],
        **extra,
    )


def test_replay_in_an_unchanged_world_returns_the_same_moments() -> None:
    cands = [_cand(n) for n in range(1, 7)]
    schedule = _plan(cands)
    replayed = _replay(schedule, cands, now=NOW + MIN)
    assert not replayed.left
    shown = _times(schedule)
    got = _times(replayed)
    assert got[1:] == shown[1:], "only a moment in the past moves, to now"
    assert got[0] == max(shown[0], NOW + MIN)


def test_replay_never_moves_a_reply_earlier_than_the_moment_shown() -> None:
    cands = [_cand(n) for n in range(1, 6)]
    schedule = _plan(cands)
    for minutes in (0, 1, 3, 5):
        replayed = _replay(schedule, cands, now=NOW + minutes * MIN)
        for shown, got in zip(_times(schedule), _times(replayed), strict=True):
            assert got >= shown


def test_replay_pushes_a_reply_later_when_the_world_moved_and_refuses_a_big_slip() -> None:
    cands = [_cand(1), _cand(2)]
    schedule = _plan(cands, per_hour=1, per_day=6, rng=_Floor())
    # a publication ten minutes ago: at one an hour the first slot is fifty minutes late, so the
    # batch is not what was shown (the second, an hour on, is still where it was shown)
    moved = _replay(schedule, cands, now=NOW, per_hour=1, per_day=6, events=[NOW - 10 * MIN])
    assert [(i.revision_id, i.code) for i in moved.left] == [(_id(1), bp.LEFT_MOVED)]
    # a small slip is accepted: the event is two minutes from leaving the hour
    small = _replay(schedule, cands, now=NOW, per_hour=1, per_day=6, events=[NOW - 58 * MIN])
    assert not small.left
    assert timedelta(0) < _times(small)[0] - _times(schedule)[0] <= bp.SLIP_TOLERANCE
    _verify([NOW - 58 * MIN, *_times(small)], per_hour=1, per_day=6)


def test_replay_leaves_out_a_reply_whose_window_closed_in_the_meantime() -> None:
    cands = [_cand(1)]
    schedule = _plan(cands)
    closing = Candidate(_id(1), "REDDIT", False, None, NOW + MIN)
    replayed = bp.replay_schedule(
        [(closing, schedule.placed[0].at)],
        now=NOW + 5 * MIN,
        per_hour=4,
        per_day=20,
        own_per_day=1,
        events=[],
        own_events=[],
    )
    assert [i.code for i in replayed.left] == [bp.LEFT_WINDOW] and not replayed.placed


# ---------------------------------------------------------------------------
# The sentence the card says
# ---------------------------------------------------------------------------


def test_the_summary_carries_the_numbers_the_card_turns_into_one_sentence() -> None:
    schedule = _plan([_cand(n) for n in range(1, 5)], per_hour=4, per_day=20, rng=_Floor())
    summary = schedule.to_json(now=NOW)
    assert summary["ile"] == 4 and summary["teraz"] == 1 and summary["odstep_min"] == 15
    assert summary["zwloka_min"] == [1, 4]
    assert summary["koniec"] == schedule.placed[-1].at.isoformat()
    empty = bp.Schedule((), (), HOUR)
    assert empty.to_json(now=NOW)["koniec"] is None
