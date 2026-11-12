"""When each reply of a batch goes out: the schedule behind "Send all" (v11, 13.10).

THE FOUNDER (13.10): "I do not want to click twenty times a day." The product keeps the human in
the loop (X removed 42 thousand accounts for AI replies with no human, and the public promise is
"Nothing posts without your click"), so the answer is ONE click per REVIEWED batch: the person
reads the list, unticks what they do not want, and presses "Yes, send these N" once. This module is
the part of that which is arithmetic: given the replies, the limits of the signed scope and what is
already committed, WHEN does each one go out.

WHY A SCHEDULE AND NOT A REFUSAL. `human_approval.approve_and_queue` counts, under the project's
lock, every request in the queue that has never been attempted as a publication of its MOMENT
(`budget._queued_counts`, by `coalesce(not_before, queued_at)`: "a queue entry is a publication
this system has committed to, at the moment it may go out"). By `queued_at` alone, approving five
replies at "one an hour" refused the second one the instant the first was queued. The refusal is
right about a batch approved all at once and wrong about a batch SPACED OUT, and spacing is what
the publisher can do: `PublicationStore.next_queued` skips a request whose
`not_before` has not come (`q.not_before IS NULL OR q.not_before <= now()`), and the publisher's
own pace check (`attempts_since`, real attempts) lets a spaced request through. A batch therefore
approves each reply WITH the moment it may go out, and this module decides those moments.

THE RULES OF THE SCHEDULE, all of them pure functions of their arguments (the clock and the dice
come in as arguments, so a test fixes both):

  * THE LIMITS ARE THE SCOPE'S, never wider: at most `publications_per_hour` in ANY rolling hour
    and `publications_per_day` in ANY rolling day, own posts also within `own_posts_per_day`,
    counting what already went out (`execution_attempts`), what is queued with a moment
    (`coalesce(not_before, queued_at)`) and what this very batch has placed. The windows are
    closed (an event exactly an hour ago is still inside), and a new moment is one second past the
    edge, so an off by one cannot let an extra reply through.
  * SPREAD, NOT BURST. Consecutive replies are at least `1 hour / per_hour` apart (one every 15
    minutes at four an hour) PLUS a random delay of `JITTER_MIN` to `JITTER_MAX` (1 to 4 minutes),
    and the first one gets up to `FIRST_JITTER_MAX` of its own. The jitter only ever ADDS, so it can
    never take a reply into a window that is full, and a run of N replies never looks like the
    machine it is. (A burst of four at once would also be legal under "four an hour", and is exactly
    what an anti-automation rule looks for.)
  * ORDER: a FRESH post on X first (a conversation on X is over in hours: `FRESH_X`, the publisher's
    own `YOUNG_X_POST`), then everything else, each group by how young the thread is, the youngest
    first (the publisher's own D-030 rule). A draft that has no thread (an own post) and a thread of
    unknown age go last.
  * EVERY MOMENT IS INSIDE THREE DEADLINES, and a reply that cannot meet them is LEFT OUT, never
    squeezed in: the horizon (`HORIZON`, twelve hours: a reply an evening later is a different
    reply), the draft's own approval window (`TICK_MARGIN` before it closes, because an approved
    revision past its `expires_at` is refused at consumption by `authorize_side_effect` and the
    request is SKIPPED), and the signed scope's own deadline (a request whose delegation expired
    is STOPPED by the publisher). A reply that is left out stays WAITING in the card's list; the
    card says so, with a code, and the person decides what to do with it.

TWO FUNCTIONS PLAN, ON PURPOSE. `plan_schedule` is the dice: it decides the moments when the
person asks (`room.batch_token`). `replay_schedule` is the same limits with NO dice: at the click
(`room.send_batch`) every reply keeps the moment the person was shown, or the first later moment
that still fits when the world moved (a publication happened, another batch was queued); it is
never earlier. A reply that would slip by more than `SLIP_TOLERANCE`, or miss a deadline, is
reported as left, and the batch is refused whole (the card asks again and shows the new plan), so
nothing goes out that is not what was on the screen.

The same `fits` decides at the gate (`slot_blocked`, called by `approve_and_queue` for a reply that
has a `not_before`), under the project's advisory lock, against the rows as they are at that
moment: the planner and the gate cannot disagree about what a window allows, because they are one
function.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from .budget import (
    DAY,
    HOUR,
    LEDGER_ALL,
    LEDGER_OWN,
    Cap,
    Limits,
    caps_for,
    committed_ledger,
    member_ledgers,
)

#: A random delay added to the gap between two replies of a batch: at least this ...
JITTER_MIN = timedelta(minutes=1)
#: ... and at most this. "A few minutes", so the spacing is not a metronome.
JITTER_MAX = timedelta(minutes=4)
#: The first reply of a batch goes out within this of the click (it is the one the person is
#: watching for), with a little of its own randomness.
FIRST_JITTER_MAX = timedelta(seconds=45)
#: How far ahead a reply may be scheduled. Beyond this it stays in the list for another batch.
HORIZON = timedelta(hours=12)
#: The publisher's chain ticks every ten minutes (`PUBLISH_INTERVAL_SECONDS`); a moment this close
#: to the end of a window is not safe, so a reply is placed at least this long before it closes.
TICK_MARGIN = timedelta(minutes=10)
#: A new moment is placed this far past the edge of a full window.
EDGE = timedelta(seconds=1)
#: A post on X younger than this is "fresh" and goes first (the publisher's `YOUNG_X_POST`).
FRESH_X = timedelta(hours=6)
#: The most replies one batch may hold. The card's Limits panel signs at most 50 a day
#: (`CARD_LIMIT_BOUNDS`); a batch is a review, and a person does not review fifty at once.
MAX_BATCH = 25
#: At the click, a reply may land later than the moment shown by this much and still be sent;
#: more than that and the person is shown the new plan first.
SLIP_TOLERANCE = timedelta(minutes=10)
#: Moments this close to the click are "now" in the sentence the card says.
NOW_WINDOW = timedelta(minutes=2)

# Why a reply is not in the schedule. The card has a sentence for each (`ref.<code>`).
LEFT_LIMIT = "POZA_LIMITEM"
LEFT_OWN = "LIMIT_WLASNYCH"
LEFT_WINDOW = "OKNO_ZA_KROTKIE"
LEFT_SCOPE = "ZAKRES_WYGASA"
LEFT_MOVED = "PLAN_PRZESUNIETY"
LEFT_NO_LIMIT = "BRAK_LIMITU"


@dataclass(frozen=True)
class Candidate:
    """One reply that may go in a batch, as the SERVER read it (nothing comes from the card)."""

    revision_id: UUID
    platform: str
    own_post: bool
    #: When the thread it answers was created on the platform, or `None` (an own post, or unknown).
    thread_at: datetime | None
    #: The revision's own approval window (`expires_at`), or `None` for none.
    expires_at: datetime | None
    #: v11: whose publication it is, and every rule it has to satisfy (`budget.caps_for`: the
    #: account's own replies and own posts, the overall ceiling). Empty `caps` is the legacy
    #: reading: the numbers the planner was handed (ONE bucket for the project).
    account_id: UUID | None = None
    caps: tuple[Cap, ...] = ()
    #: How a candidate WITHOUT an hourly rule of its own (an own post under a daily cap) is spaced:
    #: `(key, gap)`, the same key and gap as the replies of its account, so a batch of own posts and
    #: replies from one account is not a burst. `None`: spaced from nothing.
    pace: tuple[Any, timedelta] | None = None


@dataclass(frozen=True)
class Placed:
    revision_id: UUID
    at: datetime
    account_id: UUID | None = None


@dataclass(frozen=True)
class Left:
    """A reply the schedule could not place, and why (`LEFT_*`)."""

    revision_id: UUID
    code: str


@dataclass(frozen=True)
class Schedule:
    placed: tuple[Placed, ...]
    left: tuple[Left, ...]
    #: The base gap between two replies: an hour over the hourly limit. With limits per account
    #: the gap of the account that has the most replies in the batch (see `gaps`).
    gap: timedelta
    #: v11: the base gap of each account that has a reply in the batch (empty: one bucket).
    gaps: tuple[tuple[UUID | None, timedelta], ...] = ()

    def to_json(self, *, now: datetime) -> dict[str, Any]:
        """The numbers the card turns into "4 now, then one every 15 min, done by 14:45"."""
        # `max(..., default=)` over the moments, not `placed[-1]`: with several accounts the last
        # in the list is not the last in time.
        last = max((p.at for p in self.placed), default=None)
        out: dict[str, Any] = {
            "ile": len(self.placed),
            "teraz": sum(1 for p in self.placed if p.at - now <= NOW_WINDOW),
            "odstep_min": max(1, math.ceil(self.gap.total_seconds() / 60)),
            "zwloka_min": [
                int(JITTER_MIN.total_seconds() // 60),
                int(JITTER_MAX.total_seconds() // 60),
            ],
            "koniec": last.isoformat() if last is not None else None,
        }
        if len({gap for _account, gap in self.gaps}) > 1:
            out["konta"] = [
                {
                    "konto_id": str(account) if account is not None else None,
                    "ile": sum(1 for p in self.placed if p.account_id == account),
                    "odstep_min": max(1, math.ceil(gap.total_seconds() / 60)),
                }
                for account, gap in self.gaps
            ]
        return out


# ---------------------------------------------------------------------------
# The windows
# ---------------------------------------------------------------------------


def _window_ok(events: Sequence[datetime], at: datetime, window: timedelta, limit: int) -> bool:
    """With `at` added, does EVERY rolling `window` that ends at `at` or later hold at most `limit`?

    The windows that end before `at` do not contain it, so they cannot have changed. The ones that
    end at a LATER event do: a moment inserted before a queued one takes a place in that one's
    hour, and the check has to see it. Closed windows: an event exactly `window` earlier counts.
    """
    pool = sorted([*events, at])
    for end in pool:
        if end < at:
            continue
        if sum(1 for x in pool if end - window <= x <= end) > limit:
            return False
    return True


def fits_caps(
    ledger: dict[tuple[Any, ...], Sequence[datetime]], at: datetime, caps: Sequence[Cap]
) -> bool:
    """May ONE more publication be at `at`, given every ledger and the rules it must satisfy?

    Each rule counts its own ledger (all publications, the account's replies, its own posts), over
    the rolling hour and the rolling day. A rule with a limit below 1 is never satisfied: no moment
    can give a place to what nothing allows.
    """
    for cap in caps:
        events = ledger.get(cap.ledger, ())
        for window, limit in ((HOUR, cap.per_hour), (DAY, cap.per_day)):
            if limit is None:
                continue
            if limit < 1 or not _window_ok(events, at, window, limit):
                return False
    return True


def earliest_fit_caps(
    ledger: dict[tuple[Any, ...], Sequence[datetime]], start: datetime, caps: Sequence[Cap]
) -> datetime | None:
    """The first moment at or after `start` where one more publication fits every rule, or `None`.

    The candidates are `start` and the moments a window of some rule frees up (an event of ITS
    ledger plus an hour or a day, one second past the edge): between two of them nothing changes,
    so the first that fits is the earliest that fits. `None` only when a limit is below 1.
    """
    if any(
        (cap.per_hour is not None and cap.per_hour < 1)
        or (cap.per_day is not None and cap.per_day < 1)
        for cap in caps
    ):
        return None
    candidates = {start}
    for cap in caps:
        for event in ledger.get(cap.ledger, ()):
            for window, limit in ((HOUR, cap.per_hour), (DAY, cap.per_day)):
                moment = event + window + EDGE
                if limit is not None and moment > start:
                    candidates.add(moment)
    for moment in sorted(candidates):
        if fits_caps(ledger, moment, caps):
            return moment
    return None


def legacy_caps(
    per_hour: int, per_day: int, own_per_day: int | None, *, own_post: bool
) -> tuple[Cap, ...]:
    """The ONE-BUCKET rules: the project's hour and day over everything, and for an own post the
    project's own-post cap over the day."""
    caps = [Cap(LEDGER_ALL, per_hour, per_day, "projekt")]
    if own_post and own_per_day is not None:
        caps.append(Cap(LEDGER_OWN, None, own_per_day, "własne posty projektu"))
    return tuple(caps)


def fits(
    events: Sequence[datetime],
    at: datetime,
    *,
    per_hour: int,
    per_day: int,
    own_events: Sequence[datetime] | None = None,
    own_per_day: int | None = None,
) -> bool:
    """`fits_caps` for the one-bucket reading: `own_events` and `own_per_day` are given only for
    an own post, which also has to fit the cap of own posts, counted over its own events."""
    return fits_caps(
        {LEDGER_ALL: events, LEDGER_OWN: own_events or ()},
        at,
        legacy_caps(
            per_hour,
            per_day,
            own_per_day if own_events is not None else None,
            own_post=own_events is not None,
        ),
    )


def earliest_fit(
    events: Sequence[datetime],
    start: datetime,
    *,
    per_hour: int,
    per_day: int,
    own_events: Sequence[datetime] | None = None,
    own_per_day: int | None = None,
) -> datetime | None:
    """`earliest_fit_caps` for the one-bucket reading."""
    return earliest_fit_caps(
        {LEDGER_ALL: events, LEDGER_OWN: own_events or ()},
        start,
        legacy_caps(
            per_hour,
            per_day,
            own_per_day if own_events is not None else None,
            own_post=own_events is not None,
        ),
    )


# ---------------------------------------------------------------------------
# The order and the deadlines
# ---------------------------------------------------------------------------

_FAR = datetime.max.replace(tzinfo=UTC)


def _age(candidate: Candidate, now: datetime) -> timedelta | None:
    if candidate.thread_at is None:
        return None
    return max(timedelta(0), now - candidate.thread_at)


def order_candidates(candidates: Sequence[Candidate], *, now: datetime) -> list[Candidate]:
    """Fresh X first, then the rest, each by youngest thread; no thread or unknown age last.

    The tie-break is the draft's own approval window, soonest first, then the id, so the order is
    the same on every call with the same input.
    """

    def key(candidate: Candidate) -> tuple[Any, ...]:
        age = _age(candidate, now)
        fresh_x = (
            candidate.platform.upper() == "X"
            and not candidate.own_post
            and age is not None
            and age <= FRESH_X
        )
        return (
            0 if fresh_x else 1,
            1 if age is None else 0,
            age if age is not None else timedelta(0),
            candidate.expires_at or _FAR,
            str(candidate.revision_id),
        )

    return sorted(candidates, key=key)


def _deadlines(
    candidate: Candidate, *, now: datetime, valid_until: datetime | None
) -> tuple[datetime, datetime | None, datetime | None]:
    """`(horizon, window, scope)`: the last moment each of the three deadlines allows."""
    window = candidate.expires_at - TICK_MARGIN if candidate.expires_at is not None else None
    scope = valid_until - TICK_MARGIN if valid_until is not None else None
    return now + HORIZON, window, scope


def _left_code(
    slot: datetime,
    candidate: Candidate,
    *,
    now: datetime,
    valid_until: datetime | None,
) -> str | None:
    """Why `slot` is too late for this reply, or `None` when it is not. The root cause first:
    past the horizon is a matter of the limits; inside it, the window or the scope closed."""
    horizon, window, scope = _deadlines(candidate, now=now, valid_until=valid_until)
    if slot > horizon:
        return LEFT_OWN if candidate.own_post else LEFT_LIMIT
    if window is not None and slot > window:
        return LEFT_WINDOW
    if scope is not None and slot > scope:
        return LEFT_SCOPE
    return None


def _unplaceable(
    slot: datetime | None, candidate: Candidate, *, now: datetime, valid_until: datetime | None
) -> str | None:
    """`_left_code` for a slot that may not exist (`None`: no moment satisfies a limit)."""
    if slot is None:
        return None
    return _left_code(slot, candidate, now=now, valid_until=valid_until)


def _seconds(rng: random.Random, low: timedelta, high: timedelta) -> timedelta:
    return timedelta(seconds=rng.uniform(low.total_seconds(), high.total_seconds()))


# ---------------------------------------------------------------------------
# The two planners
# ---------------------------------------------------------------------------


def _rules_of(
    candidate: Candidate, *, per_hour: int, per_day: int, own_per_day: int
) -> tuple[Cap, ...]:
    """The rules of one candidate: its own (`Candidate.caps`, per account) or, when it has none,
    the one bucket of the numbers the planner was handed."""
    if candidate.caps:
        return candidate.caps
    return legacy_caps(per_hour, per_day, own_per_day, own_post=candidate.own_post)


def _pace(candidate: Candidate, caps: Sequence[Cap]) -> tuple[Any, timedelta]:
    """`(key, gap)`: which candidates are spaced from each other, and by how much.

    The key is the LEDGER of the tightest hourly rule, so under one bucket every reply of the
    project is spaced from the one before it (the bucket is shared), and under limits per account
    each account is spaced on its own (X at six an hour is not slowed by Reddit's two). A
    candidate with no hourly rule at all (an own post under a daily cap) takes the pace it was
    given (`Candidate.pace`: its account's, so own posts and replies of one account do not burst)
    and, with none, is spaced from nothing.
    """
    hourly = [cap for cap in caps if cap.per_hour is not None and cap.per_hour >= 1]
    if not hourly:
        if candidate.pace is not None:
            return candidate.pace
        return ("free", candidate.account_id, candidate.revision_id), timedelta(0)
    tight = min(hourly, key=lambda cap: cap.per_hour or 0)
    return tight.ledger, HOUR / (tight.per_hour or 1)


def _gaps(
    pairs: Sequence[tuple[Candidate, Sequence[Cap]]],
) -> tuple[tuple[UUID | None, timedelta], ...]:
    """The base gap of every account in the batch, in the order accounts first appear."""
    seen: dict[UUID | None, timedelta] = {}
    for candidate, caps in pairs:
        seen.setdefault(candidate.account_id, _pace(candidate, caps)[1])
    return tuple(seen.items())


def _headline_gap(
    gaps: Sequence[tuple[UUID | None, timedelta]], placed: Sequence[Placed]
) -> timedelta:
    """The gap the card's one sentence tells: the account with the most replies in the batch
    (the first of them on a tie), else the first account, else an hour."""
    if not gaps:
        return HOUR
    counts = {account: sum(1 for p in placed if p.account_id == account) for account, _ in gaps}
    best = max(gaps, key=lambda pair: counts[pair[0]])
    return best[1] if counts[best[0]] else gaps[0][1]


def plan_with_caps(
    candidates: Sequence[Candidate],
    *,
    now: datetime,
    ledger: dict[tuple[Any, ...], Sequence[datetime]],
    rng: random.Random,
    valid_until: datetime | None = None,
    per_hour: int = 1,
    per_day: int = 6,
    own_per_day: int = 1,
) -> Schedule:
    """The moments for `candidates`, with jitter. See the module docstring for every rule.

    `ledger` is what is committed (`budget.committed_ledger`), by ledger; it is not modified. Each
    candidate carries its own rules (`Candidate.caps`); one without any gets the one-bucket rules
    of `per_hour`, `per_day` and `own_per_day`.
    """
    work: dict[tuple[Any, ...], list[datetime]] = {key: list(v) for key, v in ledger.items()}
    ordered = order_candidates(candidates, now=now)
    rules = {
        c.revision_id: _rules_of(c, per_hour=per_hour, per_day=per_day, own_per_day=own_per_day)
        for c in ordered
    }
    gaps = _gaps([(c, rules[c.revision_id]) for c in ordered])
    placed: list[Placed] = []
    left: list[Left] = []
    previous: dict[Any, datetime] = {}
    for candidate in ordered:
        caps = rules[candidate.revision_id]
        key, gap = _pace(candidate, caps)
        if key not in previous:
            start = now + _seconds(rng, timedelta(0), FIRST_JITTER_MAX)
        else:
            start = max(now, previous[key] + gap + _seconds(rng, JITTER_MIN, JITTER_MAX))
        slot = earliest_fit_caps(work, start, caps)
        if slot is not None and slot > start:
            # Held back by something older than this batch: a little jitter on the freed moment too.
            slot = earliest_fit_caps(work, slot + _seconds(rng, JITTER_MIN, JITTER_MAX), caps)
        code = _unplaceable(slot, candidate, now=now, valid_until=valid_until)
        if slot is None or code is not None:
            code = code or (LEFT_OWN if candidate.own_post else LEFT_NO_LIMIT)
            left.append(Left(candidate.revision_id, code))
            continue
        placed.append(Placed(candidate.revision_id, slot, candidate.account_id))
        for member in member_ledgers(candidate.account_id, own_post=candidate.own_post):
            work.setdefault(member, []).append(slot)
        previous[key] = slot
    return Schedule(tuple(placed), tuple(left), _headline_gap(gaps, placed), gaps)


def replay_with_caps(
    planned: Sequence[tuple[Candidate, datetime]],
    *,
    now: datetime,
    ledger: dict[tuple[Any, ...], Sequence[datetime]],
    valid_until: datetime | None = None,
    tolerance: timedelta = SLIP_TOLERANCE,
    per_hour: int = 1,
    per_day: int = 6,
    own_per_day: int = 1,
) -> Schedule:
    """The schedule the person was shown, checked again at the click, with NO dice.

    Each reply keeps its moment, or the first later one that fits (never earlier), and is `Left`
    when that is past a deadline or later than the moment shown by more than `tolerance`.
    """
    work: dict[tuple[Any, ...], list[datetime]] = {key: list(v) for key, v in ledger.items()}
    rules = {
        c.revision_id: _rules_of(c, per_hour=per_hour, per_day=per_day, own_per_day=own_per_day)
        for c, _ in planned
    }
    gaps = _gaps([(c, rules[c.revision_id]) for c, _ in planned])
    placed: list[Placed] = []
    left: list[Left] = []
    for candidate, shown in planned:
        slot = earliest_fit_caps(work, max(shown, now), rules[candidate.revision_id])
        code = _unplaceable(slot, candidate, now=now, valid_until=valid_until)
        if code is None and slot is not None and slot - shown > tolerance:
            code = LEFT_MOVED
        if slot is None or code is not None:
            code = code or (LEFT_OWN if candidate.own_post else LEFT_NO_LIMIT)
            left.append(Left(candidate.revision_id, code))
            continue
        placed.append(Placed(candidate.revision_id, slot, candidate.account_id))
        for member in member_ledgers(candidate.account_id, own_post=candidate.own_post):
            work.setdefault(member, []).append(slot)
    return Schedule(tuple(placed), tuple(left), _headline_gap(gaps, placed), gaps)


def plan_schedule(
    candidates: Sequence[Candidate],
    *,
    now: datetime,
    per_hour: int,
    per_day: int,
    own_per_day: int,
    events: Sequence[datetime],
    own_events: Sequence[datetime],
    rng: random.Random,
    valid_until: datetime | None = None,
) -> Schedule:
    """`plan_with_caps` for ONE BUCKET: `events` are the moments of everything committed (attempts
    that reached the platform and queue entries), `own_events` those of own posts."""
    if per_hour < 1 or per_day < 1:
        return Schedule(
            (), tuple(Left(c.revision_id, LEFT_NO_LIMIT) for c in candidates), timedelta(hours=1)
        )
    return plan_with_caps(
        candidates,
        now=now,
        ledger={LEDGER_ALL: events, LEDGER_OWN: own_events},
        rng=rng,
        valid_until=valid_until,
        per_hour=per_hour,
        per_day=per_day,
        own_per_day=own_per_day,
    )


def replay_schedule(
    planned: Sequence[tuple[Candidate, datetime]],
    *,
    now: datetime,
    per_hour: int,
    per_day: int,
    own_per_day: int,
    events: Sequence[datetime],
    own_events: Sequence[datetime],
    valid_until: datetime | None = None,
    tolerance: timedelta = SLIP_TOLERANCE,
) -> Schedule:
    """`replay_with_caps` for ONE BUCKET."""
    if per_hour < 1 or per_day < 1:
        return Schedule(
            (), tuple(Left(c.revision_id, LEFT_NO_LIMIT) for c, _ in planned), timedelta(hours=1)
        )
    return replay_with_caps(
        planned,
        now=now,
        ledger={LEDGER_ALL: events, LEDGER_OWN: own_events},
        valid_until=valid_until,
        tolerance=tolerance,
        per_hour=per_hour,
        per_day=per_day,
        own_per_day=own_per_day,
    )


# ---------------------------------------------------------------------------
# What is committed, read from the rows
# ---------------------------------------------------------------------------


async def committed_events(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    accounts: Sequence[UUID],
    now: datetime,
) -> tuple[list[datetime], list[datetime]]:
    """`(events, own_events)`: the moments of everything that counts against the one bucket.

    The two lists of `budget.committed_ledger` (ALL and OWN), for the callers that plan or gate
    the one shared bucket; the ledger itself carries the per-account lists besides.
    """
    ledger = await committed_ledger(
        conn, organization_id=organization_id, project_id=project_id, accounts=accounts, now=now
    )
    return ledger[LEDGER_ALL], ledger[LEDGER_OWN]


async def slot_blocked(
    conn: asyncpg.Connection,
    *,
    organization_id: UUID,
    project_id: UUID,
    accounts: Sequence[UUID],
    limits: Limits,
    own_post: bool,
    at: datetime,
    now: datetime,
    account_id: UUID | None = None,
    platform: str | None = None,
) -> str | None:
    """Why a reply may NOT be queued for `at`, in a sentence, or `None` when it may.

    The gate of a scheduled request, the counterpart of `Spent.publication_blocked` for a moment
    that is not now: it asks whether the windows around `at` have room, against what is committed
    as of this read. Called inside the project's advisory lock, so two clicks cannot both take the
    last place. With limits per account (`account_id` and `platform` name whose reply it is) the
    rules are that account's; otherwise the one bucket's.
    """
    ledger = await committed_ledger(
        conn, organization_id=organization_id, project_id=project_id, accounts=accounts, now=now
    )
    label = ""
    if limits.per_account and account_id is not None:
        row = await conn.fetchrow(
            "SELECT platform, external_account_id FROM social_accounts "
            "WHERE organization_id = $1 AND id = $2",
            organization_id,
            account_id,
        )
        if row is not None:
            from .budget import account_label

            platform = platform or str(row["platform"])
            label = account_label(str(row["platform"]), str(row["external_account_id"]))
    caps = caps_for(
        limits, account_id=account_id, platform=platform, own_post=own_post, label=label
    )
    for cap in caps:
        one = fits_caps(ledger, at, [cap])
        if not one:
            return (
                f"w terminie {at.astimezone(UTC):%H:%M} UTC nie ma już miejsca w limitach "
                f"({_describe(cap)})"
            )
    return None


def _describe(cap: Cap) -> str:
    """One rule in words: "X @name: odpowiedzi, 6 na godzinę, 30 na dobę"."""
    parts = []
    if cap.per_hour is not None:
        parts.append(f"{cap.per_hour} na godzinę")
    if cap.per_day is not None:
        parts.append(f"{cap.per_day} na dobę")
    return f"{cap.what}: " + ", ".join(parts)
