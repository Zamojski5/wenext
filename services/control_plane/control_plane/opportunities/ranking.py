"""How fresh a conversation is, and whether it is trending: the sort of the card (v11, 13.10).

THE FOUNDER (13.10), after a day of real use: "the best opportunity must be on page 1", and a reply
into a post that is going viral, or about to, is worth more than the same reply into a post nobody
reads. The card draws ONE person at a time in the order the server sends them (`room.show`), so the
order is decided here, once, by arithmetic over what the database holds, never by a model.

TWO NUMBERS, BOTH PURE FUNCTIONS OF (PLATFORM, WHEN THE POST WAS WRITTEN, ITS COUNTERS, NOW):

  * `is_trending`: the badge. A post is TRENDING when it is FRESH and GROWING FAST:
      X       fresh = younger than 6 hours; fast = at least 2000 views an hour OR 30 likes an hour;
      Reddit  fresh = younger than 24 hours; fast = 25 points an hour OR 8 comments an hour.
    "An hour" is the post's age, but never less than half an hour (`MIN_AGE`): a post of five
    minutes with 40 likes is not "480 an hour", it is a post of five minutes. A counter nobody read
    (None, "not measured") is NOT zero and never makes a post trending: no counter, no badge.
  * `rank_score`: the order. `freshness x heat`, times 1.5 when trending. FRESHNESS halves every
    3 hours on X and every 12 on Reddit (`HALF_LIFE`), so an X post of a day ago is worth a
    quarter of a percent of a new one and a Reddit thread of a day ago a quarter. HEAT is
    `1 + log10(1 + r)` where `r` is the fastest counter measured in units of its own threshold
    (1.0 = exactly trending), so a post that is ten times over the line is worth about 2 times a
    post with no measurement, never 10 (a count is a signal, not a veto of everything younger).
    A post whose age is unknown scores 0 and goes last: it cannot be called fresh.

The thresholds are constants, in this docstring and in the code, because a number that decides what
the founder sees first has to be one he can read and change in one place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

PLATFORM_X = "X"
PLATFORM_REDDIT = "REDDIT"

#: A post younger than this is "fresh" for the badge.
FRESH = {PLATFORM_X: timedelta(hours=6), PLATFORM_REDDIT: timedelta(hours=24)}
#: Freshness halves every this long.
HALF_LIFE = {PLATFORM_X: timedelta(hours=3), PLATFORM_REDDIT: timedelta(hours=12)}
#: The least age a rate is computed over.
MIN_AGE = timedelta(minutes=30)

#: The thresholds of "fast", per hour of the post's age.
X_VIEWS_PER_HOUR = 2000.0
X_LIKES_PER_HOUR = 30.0
X_REPLIES_PER_HOUR = 10.0
X_REPOSTS_PER_HOUR = 10.0
REDDIT_POINTS_PER_HOUR = 25.0
REDDIT_COMMENTS_PER_HOUR = 8.0

#: The multiplier of a trending post in the order.
TRENDING_BOOST = 1.5


@dataclass(frozen=True)
class Metrics:
    """The counters of a post, each `None` when nobody read it. For Reddit `likes` are the points
    (score) and `replies` the number of comments; views and reposts are X's."""

    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None

    def to_json(self) -> dict[str, int | None]:
        return {
            "wyswietlenia": self.views,
            "polubienia": self.likes,
            "odpowiedzi": self.replies,
            "udostepnienia": self.reposts,
        }


def age_of(created_at: datetime | None, now: datetime) -> timedelta | None:
    """How old a post is, never negative, or `None` when nobody knows when it was written."""
    if created_at is None:
        return None
    return max(timedelta(0), now - created_at)


def _per_hour(count: int | None, age: timedelta) -> float | None:
    if count is None or count < 0:
        return None
    return count / (max(age, MIN_AGE).total_seconds() / 3600.0)


def _ratios(platform: str, age: timedelta, metrics: Metrics | None) -> list[float]:
    """Each measured counter in units of its threshold (1.0 = exactly at the line)."""
    if metrics is None:
        return []
    if platform == PLATFORM_X:
        pairs = (
            (metrics.views, X_VIEWS_PER_HOUR),
            (metrics.likes, X_LIKES_PER_HOUR),
            (metrics.replies, X_REPLIES_PER_HOUR),
            (metrics.reposts, X_REPOSTS_PER_HOUR),
        )
    else:
        pairs = (
            (metrics.likes, REDDIT_POINTS_PER_HOUR),
            (metrics.replies, REDDIT_COMMENTS_PER_HOUR),
        )
    out = []
    for count, threshold in pairs:
        rate = _per_hour(count, age)
        if rate is not None:
            out.append(rate / threshold)
    return out


def is_trending(
    platform: str, created_at: datetime | None, metrics: Metrics | None, now: datetime
) -> bool:
    """Fresh and growing fast. The views and likes of X, the points and comments of Reddit.

    Replies and reposts on X are heat for the order but not a reason for the badge on their own:
    the badge says "a lot of people are looking", and that is views and likes.
    """
    age = age_of(created_at, now)
    platform = platform.upper()
    if age is None or age > FRESH.get(platform, FRESH[PLATFORM_REDDIT]) or metrics is None:
        return False
    if platform == PLATFORM_X:
        views = _per_hour(metrics.views, age)
        likes = _per_hour(metrics.likes, age)
        return (views is not None and views >= X_VIEWS_PER_HOUR) or (
            likes is not None and likes >= X_LIKES_PER_HOUR
        )
    points = _per_hour(metrics.likes, age)
    comments = _per_hour(metrics.replies, age)
    return (points is not None and points >= REDDIT_POINTS_PER_HOUR) or (
        comments is not None and comments >= REDDIT_COMMENTS_PER_HOUR
    )


def rank_score(
    platform: str, created_at: datetime | None, metrics: Metrics | None, now: datetime
) -> float:
    """The order of the card: higher first. 0.0 for a post of unknown age."""
    age = age_of(created_at, now)
    if age is None:
        return 0.0
    platform = platform.upper()
    half_life = HALF_LIFE.get(platform, HALF_LIFE[PLATFORM_REDDIT])
    freshness = 0.5 ** (age / half_life)
    ratios = _ratios(platform, age, metrics)
    heat = 1.0 + math.log10(1.0 + max(ratios)) if ratios else 1.0
    boost = TRENDING_BOOST if is_trending(platform, created_at, metrics, now) else 1.0
    return freshness * heat * boost


#: HOW WELL AN ACCOUNT FITS A DRAFT (v11, 13.10), a multiplier on `rank_score`: the card shows the
#: best eight drafts, and a draft the founder cannot send right now must not take a place from one
#: he can. Documented numbers, in this one place:
#:   * the account is in the signed, live scope: 1.0; outside it, or the scope is paused or
#:     expired: `FIT_NO_SCOPE` (a click would be refused or would have to sign something first);
#:   * the account's browser session is lost, never connected, waiting for a login, signed in as
#:     somebody else or held by a person: `FIT_NO_SESSION` / `FIT_HELD` (the publisher could not
#:     post it now); a state nobody could read is not held against the draft.
FIT_NO_SCOPE = 0.5
FIT_NO_SESSION = 0.25
FIT_HELD = 0.1
_SESSION_LOST = frozenset({"utracona", "nigdy", "czeka_na_ciebie", "inne_konto"})


def account_fit(*, in_scope: bool, scope_live: bool, health: str | None) -> float:
    """The multiplier for one account: see `FIT_NO_SCOPE`. `health` is the card's own word
    (`konta_stan`), or `None` when nobody read it. Pure."""
    fit = 1.0 if in_scope and scope_live else FIT_NO_SCOPE
    if health == "wstrzymane":
        return fit * FIT_HELD
    if health in _SESSION_LOST:
        return fit * FIT_NO_SESSION
    return fit
