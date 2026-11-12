"""Reading the counters of a published item, and the context of the post it answered (13.10).

THE ORDER, FIXED BY THE FOUNDER'S BRIEF: an X item is read through the CAPSULE first (the account's
own browser, which sees the author-side counters: views among them), and the public mirror
`api.fxtwitter.com/<handle>/status/<id>` fills what the capsule did not show or answers when the
capsule cannot. The two describe the same moment (one call after the other), so a counter the
capsule left out is taken from the mirror and the reading says so (`raw.filled_by`). A reading with
no counter at all is no reading: `None`, never a row of zeros.

BEST EFFORT AND PACED. Every failure of a source (a timeout, a refused read, a changed answer) is a
`None` for the caller, who tries again on the next sweep; nothing here raises into the sweep. The
mirror is asked with the same settings as `opportunities/snapshot.py::fetch_fxtwitter` (no
redirects, no environment proxy, six seconds, the answer capped), and the sweep leaves a pause
between two reads (`service.READ_GAP_SECONDS`), so a backlog never becomes a burst at either door.

THE MIRROR ALSO KNOWS THE AUTHOR. The follower count of the account we answered is not on the
post's page for anybody, but `tweet.author.followers` in the mirror's answer is, and it is the
only number of this kind the product can get without a login. It is the count AT READING TIME,
not at the time of our reply; the lesson that uses it says "followers", not "followers then", and
the row's `facts` keeps the moment it was read.

WHAT COMES FROM A STRANGER IS DATA: handles and counters are shape-checked before they leave this
module; a post's text is not carried at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from control_plane.opportunities.snapshot import FXTWITTER_ORIGIN, MAX_ANSWER_BYTES, x_target

_LOG = logging.getLogger(__name__)

FXTWITTER_TIMEOUT = 6.0

SOURCE_CAPSULE = "CAPSULE"
SOURCE_FXTWITTER = "FXTWITTER"

_COUNTERS = ("views", "likes", "replies", "reposts")


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True)
class Reading:
    """The four counters of one item at one moment. A counter nobody read is `None`."""

    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    source: str = SOURCE_CAPSULE
    #: The platform said the item is not there. A gone item has no counters and no snapshot.
    gone: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def has_any(self) -> bool:
        return any(getattr(self, name) is not None for name in _COUNTERS)

    def missing(self) -> list[str]:
        return [name for name in _COUNTERS if getattr(self, name) is None]

    def filled_from(self, other: Reading) -> Reading:
        """This reading with its missing counters taken from `other`; the source stays ours when
        we had any counter of our own, and is `other`'s when we had none."""
        taken = {name: getattr(other, name) for name in self.missing()}
        taken = {name: value for name, value in taken.items() if value is not None}
        if not taken:
            return self
        merged = {name: getattr(self, name) for name in _COUNTERS} | taken
        return Reading(
            source=self.source if self.has_any() else other.source,
            raw={**self.raw, "filled_by": other.source, "filled": sorted(taken)},
            **merged,
        )


@dataclass(frozen=True)
class TargetFacts:
    """What the mirror says about the post we answered: its author, how big, when, how busy."""

    handle: str | None = None
    followers: int | None = None
    created_at: datetime | None = None
    replies: int | None = None
    read_at: datetime | None = None


def _tweet(payload: Any, status_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return None
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        return None
    seen = str(tweet.get("id") or "")
    if seen and seen != status_id:
        return None  # the mirror answered about another post
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    return tweet, author


def parse_fx_reading(payload: Any, status_id: str) -> Reading | None:
    """The four counters of one post from the mirror's answer, or `None` when it is not one."""
    found = _tweet(payload, status_id)
    if found is None:
        return None
    tweet, _author = found
    reading = Reading(
        views=_count(tweet.get("views")),
        likes=_count(tweet.get("likes")),
        replies=_count(tweet.get("replies")),
        reposts=_count(tweet.get("retweets")),
        source=SOURCE_FXTWITTER,
    )
    return reading if reading.has_any() else None


def parse_fx_target(payload: Any, status_id: str, *, now: datetime) -> TargetFacts | None:
    """The author and the busyness of the post we answered, from the mirror's answer."""
    found = _tweet(payload, status_id)
    if found is None:
        return None
    tweet, author = found
    handle = author.get("screen_name")
    created: datetime | None = None
    stamp = tweet.get("created_timestamp")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool) and stamp > 0:
        try:
            created = datetime.fromtimestamp(float(stamp), tz=UTC)
        except (OverflowError, OSError, ValueError):
            created = None
    return TargetFacts(
        handle=handle if isinstance(handle, str) and handle.isascii() and handle else None,
        followers=_count(author.get("followers")),
        created_at=created,
        replies=_count(tweet.get("replies")),
        read_at=now,
    )


async def fetch_fx_payload(
    url: str, *, client: Any = None, timeout: float = FXTWITTER_TIMEOUT
) -> Any | None:
    """One post's JSON from the public mirror, or `None` on any failure. Never raises."""
    target = x_target(url)
    if target is None:
        return None
    handle, status_id = target
    try:
        import httpx

        owned = client is None
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=3.0), follow_redirects=False, trust_env=False
        )
        try:
            response = await http.get(
                f"{FXTWITTER_ORIGIN}/{handle}/status/{status_id}",
                headers={"User-Agent": "wenext/1.0 (+https://getwenext.com)"},
            )
            if response.status_code != 200 or len(response.content) > MAX_ANSWER_BYTES:
                return None
            return response.json()
        finally:
            if owned:
                await http.aclose()
    except Exception:  # network, TLS, JSON, a changed API: a reading is best effort
        return None


class LearningSources:
    """The readers of one sweep: the X capsule (lazily built, closed at the end), the public
    mirror, and Reddit's reader when this process has one."""

    def __init__(
        self,
        *,
        x_source_factory: Callable[[], Any | None] | None = None,
        reddit_source: Any | None = None,
        http: Any = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._x_factory = x_source_factory
        self._x_source: Any | None = None
        self._x_built = False
        self._reddit = reddit_source
        self._http = http
        self._clock = clock

    async def aclose(self) -> None:
        if self._x_source is not None:
            try:
                await self._x_source.aclose()
            except Exception:  # closing a reader must not end the sweep
                _LOG.info("learning: zamknięcie czytnika X nie powiodło się", exc_info=True)
            self._x_source = None
        self._x_built = False

    def _x(self) -> Any | None:
        if not self._x_built:
            self._x_built = True
            self._x_source = self._x_factory() if self._x_factory is not None else None
        return self._x_source

    @property
    def can_read_reddit(self) -> bool:
        return self._reddit is not None

    # -- X ---------------------------------------------------------------------------------------

    async def read_x_item(self, permalink: str, *, post_id: str) -> Reading | None:
        """Our post's counters: the capsule as the author, then the mirror for what it left out."""
        capsule = await self._read_capsule(permalink, post_id)
        if capsule is not None and capsule.gone:
            return capsule
        reading = capsule
        if reading is None or reading.missing():
            payload = await fetch_fx_payload(permalink, client=self._http)
            mirror = parse_fx_reading(payload, post_id) if payload is not None else None
            if mirror is not None:
                reading = mirror if reading is None else reading.filled_from(mirror)
        return reading if reading is not None and reading.has_any() else None

    async def _read_capsule(self, permalink: str, post_id: str) -> Reading | None:
        source = self._x()
        if source is None:
            return None
        from community_observer import CredentialsMissing, SourceUnavailable

        try:
            page = await source.read_post(permalink)
        except (CredentialsMissing, SourceUnavailable, ValueError) as exc:
            _LOG.info("learning: odczyt naszego posta X %s nieudany: %s", post_id, exc)
            return None
        except Exception:  # a reader that broke is an answer about the reader, not the post
            _LOG.info("learning: odczyt posta X %s przerwany", post_id, exc_info=True)
            return None
        if getattr(page, "unavailable", None) in ("deleted", "not_found"):
            return Reading(gone=True, raw={"unavailable": page.unavailable})
        ours = page.by_id(post_id) if hasattr(page, "by_id") else None
        if ours is None:
            return None
        metrics = dict(getattr(ours, "metrics", None) or {})
        reading = Reading(
            views=_count(metrics.get("views")),
            likes=_count(metrics.get("likes")),
            replies=_count(metrics.get("replies")),
            reposts=_count(metrics.get("reposts")),
            source=SOURCE_CAPSULE,
        )
        return reading if reading.has_any() else None

    async def read_x_target(self, target_url: str | None, *, target_id: str) -> TargetFacts | None:
        """The post we answered, from the mirror: its author's followers, its time, its replies."""
        url = (
            target_url
            if target_url and x_target(target_url)
            else f"https://x.com/i/status/{target_id}"
        )
        payload = await fetch_fx_payload(url, client=self._http)
        if payload is None:
            return None
        return parse_fx_target(payload, target_id, now=self._clock())

    # -- Reddit ----------------------------------------------------------------------------------

    async def read_reddit_item(
        self, *, community_key: str, thread_id: str, our_fullname: str, own_post: bool
    ) -> Reading | None:
        """A Reddit comment's points and replies, or a post's, from the thread's JSON."""
        if self._reddit is None:
            return None
        from community_observer import CredentialsMissing, SourceUnavailable, ThreadNotFound

        from control_plane.pilot.publisher import thread_facts

        try:
            facts = thread_facts(await self._reddit.read_thread_payload(community_key, thread_id))
        except ThreadNotFound:
            return Reading(gone=True, source=SOURCE_CAPSULE, raw={"unavailable": "not_found"})
        except (SourceUnavailable, CredentialsMissing, ValueError) as exc:
            _LOG.info("learning: odczyt wątku Reddita %s nieudany: %s", thread_id, exc)
            return None
        except Exception:
            _LOG.info("learning: odczyt wątku Reddita %s przerwany", thread_id, exc_info=True)
            return None
        if own_post:
            if facts.post_fullname != our_fullname:
                return Reading(gone=True, source=SOURCE_CAPSULE, raw={"unavailable": "mismatch"})
            score = facts.post.get("score")
            replies = len(facts.comments)
        else:
            ours = facts.by_fullname(our_fullname)
            if ours is None:
                return Reading(gone=True, source=SOURCE_CAPSULE, raw={"unavailable": "not_found"})
            score = ours.get("score")
            replies = len(facts.replies_to(our_fullname))
        likes = score if isinstance(score, int) and not isinstance(score, bool) else None
        reading = Reading(likes=likes, replies=replies, source=SOURCE_CAPSULE)
        return reading if reading.has_any() else None
