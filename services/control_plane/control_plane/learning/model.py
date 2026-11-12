"""What works, as arithmetic anybody can redo by hand: buckets, medians, an n-guard (13.10).

THE FOUNDER (13.10): "powinno się uczyć". This module is the whole of the "learning" and it is
deliberately small. There is no model in it and no fit: it cuts what was published into groups
(a bucket per property: how old the post was when we replied, how big its author is, which hook
the text used, own post or reply, with a link or without, what time of day), takes the MEDIAN of
what each group got, and says something only when two groups of the same property are far apart
AND each has enough items behind it. A person with a calculator and the learning tables gets the
same sentence.

THREE RULES, EACH BECAUSE OF A WAY THIS COULD LIE:

  1. **n >= 3 per group, or no claim.** A median of two numbers is the two numbers. Every group
     that is not backed by `MIN_N` items with the metric measured is dropped from the comparison,
     and every sentence carries its `n`, so "140 views" is never read as a fact about the world
     when it is a fact about six replies.
  2. **one horizon per claim, the most mature one that has the data.** Views at 1 h and views at
     72 h are not the same quantity, and a sentence comparing a young bucket at 1 h with an old
     bucket at 24 h would be about the clock. Each snapshot has a horizon (1, 6, 24, 72 h); the
     comparison runs inside ONE horizon, and the horizon is the largest at which the property has
     two comparable groups. It is never picked because the answer there looks better.
  3. **a gap must be big.** The better group needs `MIN_RATIO` times the worse one's median AND
     `MIN_ABS` more in absolute terms. Medians of 0 and 3 views are not a finding; 25 and 140
     are. These thresholds are constants in this file, reported in every lesson's payload, and
     the range behind each median travels with it.

WHAT THIS DOES NOT CLAIM: causes. "Replies under young posts got more views" is a pattern in our
own small history; the post's author, the topic and the day all move together with it. Every
statement says what was measured; the tool that hands it to the agent says it is observational.

A NUMBER NOBODY READ IS NONE, NEVER ZERO (the rule of 0035, 0036, 0039). A snapshot whose X did not
show views has `views = None` and is out of the views median, not a zero in it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

# --------------------------------------------------------------------------------------------
# Horizons: when a counter is read
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonWindow:
    """One moment of an item's life at which its counters are written down.

    `due_minutes` is the age from which the snapshot is taken (about 1 h after publishing means
    "from the first read at or after 60 minutes"). `expires_minutes` is the age after which it is
    no longer taken at all: a "24 h" read made at 40 h would be a different quantity. The
    sweep runs hourly, so a window that is too tight loses snapshots to the schedule itself.
    `accept_minutes` is the lower bound for a reading somebody ELSE made (the founder's diary
    says "after 18 h"): close enough to count, never close enough to be taken by us.
    """

    hours: int
    due_minutes: int
    expires_minutes: int
    accept_minutes: int


WINDOWS: tuple[HorizonWindow, ...] = (
    HorizonWindow(1, 60, 180, 45),
    HorizonWindow(6, 360, 720, 270),
    HorizonWindow(24, 1440, 2880, 1080),
    HorizonWindow(72, 4320, 7200, 3240),
)
HORIZONS: tuple[int, ...] = tuple(window.hours for window in WINDOWS)
WINDOW_OF: dict[int, HorizonWindow] = {window.hours: window for window in WINDOWS}

#: The longest an item stays interesting: after this nothing about it is read any more.
LAST_EXPIRY = timedelta(minutes=WINDOWS[-1].expires_minutes)


def age_minutes(published_at: datetime, moment: datetime) -> int:
    """Whole minutes between publishing and `moment`, never negative."""
    return max(0, int((moment - published_at).total_seconds() // 60))


def horizon_of(age: int) -> int | None:
    """The horizon a reading of this age belongs to, or `None` when it is between two of them."""
    for window in WINDOWS:
        if window.accept_minutes <= age <= window.expires_minutes:
            return window.hours
    return None


def due_horizons(published_at: datetime, now: datetime, have: Iterable[int]) -> list[int]:
    """The horizons whose snapshot should be taken NOW: due, not expired, not already there."""
    age = age_minutes(published_at, now)
    taken = set(have)
    return [
        window.hours
        for window in WINDOWS
        if window.hours not in taken and window.due_minutes <= age <= window.expires_minutes
    ]


# --------------------------------------------------------------------------------------------
# Properties of one published item, derived from its text
# --------------------------------------------------------------------------------------------

HOOKS: tuple[str, ...] = (
    "BOL",
    "PROWOKACJA",
    "DEMO",
    "LICZBA",
    "PYTANIE",
    "NEWS",
    "POMOC",
    "OPINIA",
    "ZART",
    "INNE",
)

HOOK_CLASSIFIER = "CLASSIFIER"
HOOK_DIARY = "DIARY"

_WENEXT = re.compile(r"wenext", re.IGNORECASE)
_URL = re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)
_DOMAIN = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|io|ai|dev|app|co|net|org|xyz|sh|so|me|gg|ly|tech)\b",
    re.IGNORECASE,
)

#: THE RULES OF THE HOOK, in the order they are tried. Each is a regular expression anybody can
#: read, and the first that matches wins. They are rules of thumb, not a model: the founder's own
#: label (the diary) replaces them (`hook_source = DIARY`), and a text no rule recognises is
#: `OPINIA`, the plainest thing a short post can be.
_QUESTION = re.compile(r"\?")
_NUMBER = re.compile(
    r"(?:[$€£]\s?\d)"
    r"|(?:\b\d[\d,.]*\s?(?:k|m|x|%|views?|likes?|users?|days?|hours?|hrs?|h|min|minutes?|weeks?"
    r"|months?|customers?|signups?|stars?|followers?|replies|posts?|tweets?)\b)"
    r"|(?:\b\d{2,}\b)",
    re.IGNORECASE,
)
_PROVOKE = re.compile(
    r"\b(?:is dead|are dead|dead|overrated|nobody (?:needs|wants|cares|reads|uses|reads)|"
    r"stop (?:doing|building|using|writing)|wrong|myth|lie|lies|worst|scam|never)\b",
    re.IGNORECASE,
)
_PAIN = re.compile(
    r"\b(?:struggl\w*|can'?t|cannot|hard (?:to|part)|bad at|not so much|no users|"
    r"nobody uses|stuck|tired of|hate|painful|frustrat\w*|burn\w*|"
    r"zero (?:users|customers|signups))\b",
    re.IGNORECASE,
)
_NEWS = re.compile(
    r"\b(?:just (?:shipped|launched|released|dropped)|launched|released|announced|announcing|"
    r"introducing|new (?:model|release|feature|version)|breaking|billing)\b",
    re.IGNORECASE,
)
_HELP = re.compile(
    r"\b(?:try|you can|you could|you should|consider|tip|one way|here'?s how|how to|"
    r"works? for me|fix(?:ed)?)\b",
    re.IGNORECASE,
)
_JOKE = re.compile(r"\b(?:lol|lmao|haha+|joke|jk)\b|:\)|\U0001f602", re.IGNORECASE)
_DEMO = re.compile(r"\bdemo\b", re.IGNORECASE)


def has_link(text: str) -> bool:
    """A web address in the text, with or without a scheme (`x.com/..`, `getwenext.com`)."""
    return bool(_URL.search(text) or _DOMAIN.search(text))


def mentions_wenext(text: str) -> bool:
    return bool(_WENEXT.search(text))


def classify_hook(text: str, *, has_media: bool = False) -> str:
    """The hook of a text by the first matching rule. Pure, and explainable by reading `_RULES`.

    A picture or a video is a DEMO whatever the words say; then a question; then a concrete
    number; then pain, provocation, news, advice and a joke by their key phrases; and what is
    left is an opinion. The text is judged without its web addresses, so a link to a "demo" page
    is not a demo.
    """
    body = _URL.sub(" ", text or "")
    if has_media or _DEMO.search(body):
        return "DEMO"
    if _QUESTION.search(body):
        return "PYTANIE"
    if _NUMBER.search(body):
        return "LICZBA"
    for hook, pattern in (
        ("BOL", _PAIN),
        ("PROWOKACJA", _PROVOKE),
        ("NEWS", _NEWS),
        ("POMOC", _HELP),
        ("ZART", _JOKE),
    ):
        if pattern.search(body):
            return hook
    return "OPINIA"


# --------------------------------------------------------------------------------------------
# Buckets: how a property of an item is cut into groups
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One published item AT ONE HORIZON: its properties and its counters then.

    Counters are `None` when the platform did not show them. The properties are `None` when
    nobody established them (the age of a post whose time is unknown), and an item with an
    unknown property is simply left out of that property's comparison.
    """

    key: str
    platform: str
    kind: str
    horizon: int
    published_at: datetime
    hook: str | None = None
    target_followers: int | None = None
    target_age_minutes: int | None = None
    position_replies: int | None = None
    mentions_wenext: bool = False
    has_link: bool = False
    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    body: str = ""
    permalink: str = ""
    project_id: Any = None
    workspace_id: Any = None


def bucket_target_age(item: Item) -> str | None:
    """How old the post was when we replied. Replies only: an own post has no target."""
    age = item.target_age_minutes
    if item.kind != "REPLY" or age is None:
        return None
    if age < 30:
        return "<30 min"
    if age <= 120:
        return "30-120 min"
    return ">2 h"


def bucket_followers(item: Item) -> str | None:
    """The size of the account we answered. Replies only, and only when it was read."""
    followers = item.target_followers
    if item.kind != "REPLY" or followers is None:
        return None
    if followers < 1_000:
        return "<1k"
    if followers < 10_000:
        return "1k-10k"
    if followers < 100_000:
        return "10k-100k"
    return "100k+"


def bucket_position(item: Item) -> str | None:
    """How many replies were already under the post when we joined it."""
    count = item.position_replies
    if item.kind != "REPLY" or count is None:
        return None
    if count <= 5:
        return "0-5"
    if count <= 20:
        return "6-20"
    return ">20"


def bucket_hook(item: Item) -> str | None:
    return item.hook if item.hook and item.hook != "INNE" else None


def bucket_kind(item: Item) -> str | None:
    return "reply" if item.kind == "REPLY" else "own post"


def bucket_link(item: Item) -> str | None:
    return "link" if item.has_link else "no link"


def bucket_mention(item: Item) -> str | None:
    return "mentions Wenext" if item.mentions_wenext else "no Wenext"


def bucket_hour(item: Item) -> str | None:
    hour = item.published_at.astimezone(UTC).hour
    start = hour // 6 * 6
    return f"{start:02d}-{start + 6:02d} UTC"


@dataclass(frozen=True)
class Dimension:
    """One property the model compares groups of: its key, its bucketer and how a group is said."""

    key: str
    bucket: Callable[[Item], str | None]
    #: A complete noun phrase for the items of one bucket, e.g. "replies under posts younger than
    #: 30 min". Written out per bucket so the sentence reads like a sentence.
    phrase: Callable[[str], str]


_AGE_PHRASE = {
    "<30 min": "replies under posts younger than 30 min",
    "30-120 min": "replies under posts 30 to 120 min old",
    ">2 h": "replies under posts older than 2 h",
}
_FOLLOWERS_PHRASE = {
    "<1k": "replies to accounts with under 1k followers",
    "1k-10k": "replies to accounts with 1k to 10k followers",
    "10k-100k": "replies to accounts with 10k to 100k followers",
    "100k+": "replies to accounts with 100k or more followers",
}
_POSITION_PHRASE = {
    "0-5": "replies sent when the post had up to 5 replies",
    "6-20": "replies sent when the post had 6 to 20 replies",
    ">20": "replies sent when the post had more than 20 replies",
}
_KIND_PHRASE = {"reply": "replies", "own post": "own posts"}
_LINK_PHRASE = {"link": "items with a link", "no link": "items without a link"}
_MENTION_PHRASE = {
    "mentions Wenext": "items that mention Wenext",
    "no Wenext": "items that do not mention Wenext",
}

DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("target_age", bucket_target_age, lambda b: _AGE_PHRASE.get(b, b)),
    Dimension("target_followers", bucket_followers, lambda b: _FOLLOWERS_PHRASE.get(b, b)),
    Dimension("position", bucket_position, lambda b: _POSITION_PHRASE.get(b, b)),
    Dimension("hook", bucket_hook, lambda b: f"items with the {b} hook"),
    Dimension("kind", bucket_kind, lambda b: _KIND_PHRASE.get(b, b)),
    Dimension("link", bucket_link, lambda b: _LINK_PHRASE.get(b, b)),
    Dimension("mention", bucket_mention, lambda b: _MENTION_PHRASE.get(b, b)),
    Dimension("time_of_day", bucket_hour, lambda b: f"items published {b}"),
)
DIMENSION_OF: dict[str, Dimension] = {dimension.key: dimension for dimension in DIMENSIONS}

# --------------------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------------------

#: A group with fewer items than this (with the metric measured) never takes part in a claim.
MIN_N = 3
#: The better group's median must be at least this many times the worse one's (floor 1, so a
#: median of 0 does not make every positive number "infinitely better")...
MIN_RATIO = 2.0
#: ...and at least this much higher in absolute terms. Views are plentiful, likes are rare. The
#: floor for views is 20, set after the first run on the founder's real history (13.10): at one
#: hour his posts show 4 to 46 views, and "18 against 7" was a "lesson" about three posts that
#: differed in a dozen ways. A gap of 20 views is still small; it is the smallest that is not noise
#: at this size of account, and it moves nothing for an account that grows.
MIN_ABS = {"views": 20.0, "likes": 3.0}

#: Views first (only X shows them, and the founder's question is reach); likes (X) or points
#: (Reddit) where views are not there to compare.
METRICS: tuple[str, ...] = ("views", "likes")


@dataclass(frozen=True)
class BucketStats:
    """What one group got, with the counts behind every number."""

    bucket: str
    n: int
    n_views: int
    median_views: float | None
    n_likes: int
    median_likes: float | None
    #: Keys (external ids) of the items in the group, newest first, capped for the payload.
    evidence: tuple[str, ...] = ()
    #: The smallest and the largest value behind each median: how noisy the group is, said next
    #: to the median instead of hidden by it.
    min_views: int | None = None
    max_views: int | None = None
    min_likes: int | None = None
    max_likes: int | None = None

    def n_of(self, metric: str) -> int:
        return self.n_views if metric == "views" else self.n_likes

    def median_of(self, metric: str) -> float | None:
        return self.median_views if metric == "views" else self.median_likes

    def to_json(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "n": self.n,
            "n_views": self.n_views,
            "median_views": self.median_views,
            "n_likes": self.n_likes,
            "median_likes": self.median_likes,
            "range_views": (
                [self.min_views, self.max_views] if self.min_views is not None else None
            ),
            "range_likes": (
                [self.min_likes, self.max_likes] if self.min_likes is not None else None
            ),
            "evidence": list(self.evidence),
        }


MAX_EVIDENCE = 12


def _median(values: Sequence[int]) -> float | None:
    return float(median(values)) if values else None


def stats_of(bucket: str, items: Sequence[Item]) -> BucketStats:
    views = [item.views for item in items if item.views is not None]
    likes = [item.likes for item in items if item.likes is not None]
    newest_first = sorted(items, key=lambda item: item.published_at, reverse=True)
    return BucketStats(
        bucket=bucket,
        n=len(items),
        n_views=len(views),
        median_views=_median(views),
        n_likes=len(likes),
        median_likes=_median(likes),
        evidence=tuple(item.key for item in newest_first[:MAX_EVIDENCE]),
        min_views=min(views) if views else None,
        max_views=max(views) if views else None,
        min_likes=min(likes) if likes else None,
        max_likes=max(likes) if likes else None,
    )


def aggregate(items: Iterable[Item], dimension: Dimension) -> dict[str, BucketStats]:
    """Group the items of one dimension by bucket and take each group's numbers."""
    groups: dict[str, list[Item]] = {}
    for item in items:
        bucket = dimension.bucket(item)
        if bucket is not None:
            groups.setdefault(bucket, []).append(item)
    return {bucket: stats_of(bucket, rows) for bucket, rows in groups.items()}


def _eligible(buckets: dict[str, BucketStats], metric: str) -> list[BucketStats]:
    return [stats for stats in buckets.values() if stats.n_of(metric) >= MIN_N]


@dataclass(frozen=True)
class Claim:
    """A clear difference between two groups of one property, inside one horizon."""

    platform: str
    dimension: str
    horizon: int
    metric: str
    winner: BucketStats
    loser: BucketStats
    others: tuple[BucketStats, ...] = ()
    ratio: float | None = None

    @property
    def key(self) -> str:
        """The identity of a lesson: one per platform and property. The horizon is NOT in it, so
        a lesson that matured from 1 h to 24 h data is a correction of the same lesson."""
        return f"{self.platform}:{self.dimension}"


def _gap(better: float, worse: float, metric: str) -> tuple[bool, float | None]:
    ratio = better / worse if worse > 0 else None
    big = better >= MIN_RATIO * max(worse, 1.0) and (better - worse) >= MIN_ABS[metric]
    return big, ratio


def claim_for(
    platform: str, dimension: Dimension, horizon: int, buckets: dict[str, BucketStats]
) -> Claim | None:
    """The claim of one property at one horizon, or `None` when it is not backed by the data.

    The first metric (views, then likes) with at least two groups of `MIN_N` decides; a property
    that has two such groups but no clear gap in that metric makes no claim, and the second
    metric is NOT tried then: looking at likes only after the views disappointed is how a
    pattern is found in noise.
    """
    for metric in METRICS:
        eligible = _eligible(buckets, metric)
        if len(eligible) < 2:
            continue
        ranked = sorted(eligible, key=lambda stats: (stats.median_of(metric) or 0.0, stats.bucket))
        worst, best = ranked[0], ranked[-1]
        best_value = best.median_of(metric) or 0.0
        worst_value = worst.median_of(metric) or 0.0
        big, ratio = _gap(best_value, worst_value, metric)
        if not big:
            return None
        return Claim(
            platform=platform,
            dimension=dimension.key,
            horizon=horizon,
            metric=metric,
            winner=best,
            loser=worst,
            others=tuple(
                stats
                for stats in sorted(buckets.values(), key=lambda s: s.bucket)
                if stats.bucket not in (best.bucket, worst.bucket)
            ),
            ratio=ratio,
        )
    return None


def has_comparison(buckets: dict[str, BucketStats]) -> bool:
    """Whether at least two groups have enough items in views or in likes: the horizon is usable."""
    return any(len(_eligible(buckets, metric)) >= 2 for metric in METRICS)


def find_claims(items: Iterable[Item], *, platform: str) -> list[Claim]:
    """Every clear difference this platform's items show, one per property.

    PER PROPERTY, THE MOST MATURE HORIZON THAT CAN BE COMPARED AT ALL: the largest of 72, 24, 6, 1
    at which two groups have `MIN_N` items. A claim is made from that horizon alone. If it shows
    no clear gap, the property has no lesson; a smaller horizon is not consulted for one.
    """
    rows = [item for item in items if item.platform == platform]
    claims: list[Claim] = []
    for dimension in DIMENSIONS:
        for horizon in sorted(HORIZONS, reverse=True):
            buckets = aggregate((item for item in rows if item.horizon == horizon), dimension)
            if not has_comparison(buckets):
                continue
            claim = claim_for(platform, dimension, horizon, buckets)
            if claim is not None:
                claims.append(claim)
            break
    return claims


# --------------------------------------------------------------------------------------------
# Sentences
# --------------------------------------------------------------------------------------------

PLATFORM_NAME = {"X": "X", "REDDIT": "Reddit"}


def number(value: float | None) -> str:
    """A median as people write it: whole when whole, one decimal otherwise."""
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _unit(platform: str, metric: str) -> str:
    if metric == "views":
        return "views"
    return "points" if platform == "REDDIT" else "likes"


def statement_of(claim: Claim) -> str:
    """The lesson as one sentence with every number and its n. Nothing here is not in the claim."""
    dimension = DIMENSION_OF[claim.dimension]
    unit = _unit(claim.platform, claim.metric)
    best = claim.winner
    worst = claim.loser
    return (
        f"On {PLATFORM_NAME.get(claim.platform, claim.platform)}, at about {claim.horizon} h "
        f"after posting: {dimension.phrase(best.bucket)} got a median of "
        f"{number(best.median_of(claim.metric))} {unit} (n={best.n_of(claim.metric)}) versus "
        f"{number(worst.median_of(claim.metric))} {unit} for {dimension.phrase(worst.bucket)} "
        f"(n={worst.n_of(claim.metric)})."
    )


def payload_of(claim: Claim, *, measured_at: datetime) -> dict[str, Any]:
    """The payload of a LESSON row: the claim with the evidence behind it (`0040` requires both
    sides and their `n`)."""
    return {
        "lesson_key": claim.key,
        "platform": claim.platform,
        "dimension": claim.dimension,
        "horizon_hours": claim.horizon,
        "metric": claim.metric,
        "winner": claim.winner.to_json(),
        "loser": claim.loser.to_json(),
        "others": [stats.to_json() for stats in claim.others],
        "ratio": round(claim.ratio, 2) if claim.ratio is not None else None,
        "thresholds": {
            "min_n": MIN_N,
            "min_ratio": MIN_RATIO,
            "min_abs": MIN_ABS[claim.metric],
        },
        "measured_at": measured_at.isoformat(),
        "measured_by": "job learning.sweep",
        "caveat": "observational and small: a pattern in our own history, not a cause",
    }


# --------------------------------------------------------------------------------------------
# Coverage: how much there is to learn from
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """How much data stands behind the lessons, said even when there are none."""

    tracked: int = 0
    with_snapshot: dict[int, int] = field(default_factory=dict)
    last_snapshot_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "sledzone": self.tracked,
            "z_migawka": {f"{hours}h": self.with_snapshot.get(hours, 0) for hours in HORIZONS},
            "ostatnia_migawka": (
                self.last_snapshot_at.isoformat() if self.last_snapshot_at else None
            ),
        }
