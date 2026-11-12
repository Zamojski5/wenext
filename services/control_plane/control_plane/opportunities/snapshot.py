"""What a post says, read once, so the card can show it above the reply (v11, 13.10).

THE FOUNDER (13.10), after a day of real use: a reply made with `draft.create` without an
opportunity mark showed only "Reply on X": whose post is it, what does it say, is it trending?
Every reply draft now carries the post it answers: author (handle, name, avatar), text, when it was
written, and its counters. This module is the READ and nothing else; where the result is kept (the
opportunity row of the conversation, 0039) and when it is read (`draft.create`, the backfill) is
`target_context.py`.

WHAT COMES FROM A STRANGER IS DATA, NOT INSTRUCTIONS. A post's text, a display name and an avatar
address are written by somebody else, so each is clipped, stripped of control characters and held to
its shape before it goes anywhere (`new_opportunity` clips again; `avatars.safe_avatar` refuses any
host that is not the platform's image CDN). Nothing here is ever shown to a model as an order.

THE SOURCES, in this order for X:
  1. `api.fxtwitter.com/<handle>/status/<id>`, the public mirror: ONE short HTTP call that returns
     the text, the author with his avatar, the time and every counter, with no browser and no
     session of the founder's (`fetch_fxtwitter`, six seconds, no redirects, no environment proxy,
     the answer capped at 200 kB). Best effort: any failure is `None`, never an exception, and
     never a refusal of the draft.
  2. the capsule's reader of the founder's own browser (`x.read`'s path,
     `XBrowserSource.read_post`), when the first gave nothing: the post as the signed-in account
     sees it (`from_x_page`), which has no avatar. It is also the ONLY source that may say a post
     is GONE (`page.unavailable`): a mirror that does not know a post is not proof it is not there.
For Reddit the source is the one `reddit.read` already uses (`from_reddit_thread`).

A COUNTER NOBODY READ IS `None`, NEVER ZERO (the rule of 0035, 0036 and 0039).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from control_plane.pilot.send_checks import x_created_at

from .avatars import safe_avatar, safe_picture

FXTWITTER_ORIGIN = "https://api.fxtwitter.com"
FXTWITTER_TIMEOUT = 6.0
MAX_ANSWER_BYTES = 200_000

#: What a post's text, a name and a handle may be when they leave this module.
TEXT_CHARS = 4000
NAME_CHARS = 100
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
_STATUS_PATH = re.compile(r"^/([A-Za-z0-9_]{1,30})/status(?:es)?/(\d{1,25})/?$")


@dataclass(frozen=True)
class TargetSnapshot:
    """One post, as read. Every field that was not read is `None` (text: empty)."""

    platform: str
    #: `x:<status id>` or `t3_<post id>`: the spelling of a reply target everywhere in the product.
    external_id: str
    url: str
    handle: str | None
    name: str | None
    avatar_url: str | None
    text: str
    created_at: datetime | None
    #: X likes, Reddit points.
    likes: int | None = None
    #: X replies, Reddit comments.
    replies: int | None = None
    reposts: int | None = None
    views: int | None = None
    #: Reddit only.
    community: str | None = None
    title: str | None = None
    #: The reader said the post is not there (deleted, not found). Only the account's own browser
    #: may say it; a mirror's silence is not that.
    gone: bool = False
    #: Where it was read: `fxtwitter`, `x.read`, `reddit.read`.
    source: str = ""
    #: The author's verification mark when the read knows it (`None`: not known, never "not
    #: verified"), and the post's FIRST picture or video thumbnail as `{typ, url, szer, wys}`.
    verified: bool | None = None
    media: dict[str, Any] | None = None


def clean(text: Any, limit: int) -> str:
    """A stranger's text: a string, without control characters, clipped."""
    if not isinstance(text, str):
        return ""
    return _CONTROL.sub("", text).strip()[:limit]


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _size(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 20000
        else None
    )


def first_media(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """The first picture of a post as the mirror lists them: a photo, else a video's or a GIF's
    thumbnail. `url` is always an address on a platform picture host (`avatars.safe_picture`);
    anything else is a post without media, never a request aimed by a stranger."""
    media = tweet.get("media")
    if not isinstance(media, dict):
        return None
    pool: list[Any] = []
    for key in ("all", "photos", "videos"):
        found = media.get(key)
        if isinstance(found, list):
            pool.extend(found)
    for item in pool:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        kind = {"photo": "photo", "video": "video", "gif": "gif", "animated_gif": "gif"}.get(
            kind, ""
        )
        if not kind:
            continue
        source = item.get("url") if kind == "photo" else item.get("thumbnail_url")
        address = safe_picture(source if isinstance(source, str) else None)
        if address is None:
            continue
        return {
            "typ": kind,
            "url": address,
            "szer": _size(item.get("width")),
            "wys": _size(item.get("height")),
        }
    return None


def _verified(author: dict[str, Any]) -> bool | None:
    """The mark, from either shape the mirror has used: `verification.verified` or `verified`."""
    verification = author.get("verification")
    if isinstance(verification, dict) and isinstance(verification.get("verified"), bool):
        return bool(verification["verified"])
    flag = author.get("verified")
    return flag if isinstance(flag, bool) else None


def x_target(url: str) -> tuple[str, str] | None:
    """`(handle, status id)` of an X post address, or `None` when it is not one."""
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https" or (parts.hostname or "").lower() not in (
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    ):
        return None
    match = _STATUS_PATH.match(parts.path)
    return (match.group(1), match.group(2)) if match else None


#: A status address at the END of a text (X turns it into a quote card): host x.com or twitter.com,
#: a handle, `/status/<digits>`, nothing after it but an optional `?` query and whitespace.
_TRAILING_STATUS = re.compile(
    r"(https://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})/status(?:es)?/(\d{1,25})"
    r"(?:\?\S*)?)\s*$"
)


def trailing_status(text: str) -> tuple[str, str, str] | None:
    """`(address, handle, status id)` of the X status address a text ENDS with, or `None`.

    A text that ends with the address of a post is a QUOTE on X: the platform shows our words and
    the post below them as a box, and the address itself is not part of what readers see. Only the
    end counts (a link in the middle of the words is a link), and only a real status address (a
    profile or a search address is just a link). The address is returned exactly as written, so
    the card can take it off the end of the text it shows."""
    match = _TRAILING_STATUS.search(text or "")
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


def parse_fxtwitter(payload: Any, *, url: str, status_id: str) -> TargetSnapshot | None:
    """The mirror's answer as a snapshot, or `None` when it is not one (a 404, a changed shape).

    Pure. Defensive on every field: the answer comes from outside and a missing or oddly typed
    field is a field that was not read, never a crash.
    """
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return None
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        return None
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    handle = clean(author.get("screen_name"), 30)
    seen_id = str(tweet.get("id") or "")
    if seen_id and seen_id != status_id:
        return None  # the mirror answered about another post: not this one
    created: datetime | None = None
    stamp = tweet.get("created_timestamp")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool) and stamp > 0:
        try:
            created = datetime.fromtimestamp(float(stamp), tz=UTC)
        except (OverflowError, OSError, ValueError):
            created = None
    if created is None:
        created = x_created_at(None, status_id)
    return TargetSnapshot(
        platform="X",
        external_id=f"x:{status_id}",
        url=url,
        handle=handle if _HANDLE.match(handle) else None,
        name=clean(author.get("name"), NAME_CHARS) or None,
        avatar_url=safe_avatar(author.get("avatar_url")),
        text=clean(tweet.get("text"), TEXT_CHARS),
        created_at=created,
        likes=_count(tweet.get("likes")),
        replies=_count(tweet.get("replies")),
        reposts=_count(tweet.get("retweets")),
        views=_count(tweet.get("views")),
        source="fxtwitter",
        verified=_verified(author),
        media=first_media(tweet),
    )


async def fetch_fxtwitter(
    url: str, *, client: Any = None, timeout: float = FXTWITTER_TIMEOUT
) -> TargetSnapshot | None:
    """One post from the public mirror, or `None` on any failure. Never raises."""
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
            return parse_fxtwitter(response.json(), url=url, status_id=status_id)
        finally:
            if owned:
                await http.aclose()
    except Exception:  # network, TLS, JSON, a changed API: a snapshot is best effort
        return None


_X_METRICS = ("views", "likes", "replies", "reposts")
_UNAVAILABLE_GONE = ("deleted", "not_found")


def from_x_page(page: Any, *, url: str, status_id: str) -> TargetSnapshot | None:
    """A post as the founder's own browser read it (`XPage`), or `None` when the page has nothing.

    A page that says the post is unavailable (`deleted`, `not_found`) is a `gone` snapshot: the one
    place the product learns a post is gone without trying to publish under it.
    """
    unavailable = getattr(page, "unavailable", None)
    if unavailable in _UNAVAILABLE_GONE:
        return TargetSnapshot(
            platform="X",
            external_id=f"x:{status_id}",
            url=url,
            handle=None,
            name=None,
            avatar_url=None,
            text="",
            created_at=x_created_at(None, status_id),
            gone=True,
            source="x.read",
        )
    focal = page.by_id(status_id) if hasattr(page, "by_id") else None
    if focal is None:
        return None
    metrics = dict(getattr(focal, "metrics", None) or {})
    published = getattr(focal, "published_at", None)
    handle = clean(getattr(focal, "author", None), 30)
    return TargetSnapshot(
        platform="X",
        external_id=f"x:{status_id}",
        url=url,
        handle=handle if _HANDLE.match(handle) else None,
        name=clean(getattr(focal, "author_name", None), NAME_CHARS) or None,
        avatar_url=None,
        text=clean(getattr(focal, "text", None), TEXT_CHARS),
        created_at=x_created_at(published, status_id),
        likes=_count(metrics.get("likes")),
        replies=_count(metrics.get("replies")),
        reposts=_count(metrics.get("reposts")),
        views=_count(metrics.get("views")),
        source="x.read",
    )


def from_reddit_thread(thread: Any, *, url: str) -> TargetSnapshot | None:
    """A Reddit post from the reader `reddit.read` uses (`RedditThread`), or `None`."""
    post_id = str(getattr(thread, "post_id", "") or "").removeprefix("t3_")
    if not post_id:
        return None
    created = getattr(thread, "created_at", None)
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    subreddit = clean(getattr(thread, "subreddit", None), 40)
    points = getattr(thread, "score", None)
    return TargetSnapshot(
        platform="REDDIT",
        external_id=f"t3_{post_id}",
        url=clean(getattr(thread, "url", None), 2000) or url,
        handle=clean(getattr(thread, "author", None), 40) or None,
        name=None,
        avatar_url=None,
        text=clean(getattr(thread, "body", None), TEXT_CHARS),
        created_at=created if isinstance(created, datetime) else None,
        likes=points if isinstance(points, int) and not isinstance(points, bool) else None,
        replies=_count(getattr(thread, "num_comments", None)),
        community=f"r/{subreddit}" if subreddit else None,
        title=clean(getattr(thread, "title", None), 500) or None,
        source="reddit.read",
    )
