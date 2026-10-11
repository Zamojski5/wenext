"""Reddit payloads → observation models. Shared by BOTH Reddit adapters (D-023, D-025).

The Data API (`oauth.reddit.com/r/<sub>/new`) and the browser-facing `.json` views
(`www.reddit.com/r/<sub>/new.json`) return the same shape: `Listing → data.children[] → data`,
and a thread page is a pair of listings (the post, then the comment tree). Parsing lives here
once, so `RedditSource` (OAuth) and `RedditBrowserSource` (the capsule's Chrome) cannot drift
apart in how a post becomes an `ObservedThread` — the transport differs, the evidence does not.

Everything here is pure: no I/O, no clock. Callers pass `observed_at`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from community_os_contracts import ObservationPlatform, Observed, UnknownReason

from .models import (
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    ThreadSnapshot,
    raw_digest,
)
from .source import SourceUnavailable, ThreadNotFound, UnknownCommunity

PERMALINK_BASE = "https://www.reddit.com"

#: `r/<name>`: Reddit allows 3-21 word characters; `r/` is required so a key is never mistaken
#: for a HN key in a table that holds both platforms.
COMMUNITY_KEY = re.compile(r"^r/([A-Za-z0-9_]{2,21})$")

#: Post identifiers are base36 — the same rule the capsule's reader enforces on the URL path.
POST_ID = re.compile(r"^[a-z0-9]{1,12}$")


def subreddit_name(community_key: str) -> str:
    """`r/devops` → `devops`. Raises `UnknownCommunity` for anything else."""
    match = COMMUNITY_KEY.match(community_key)
    if match is None:
        raise UnknownCommunity(
            f"klucz community Reddita ma postać `r/<nazwa>`, nie {community_key!r}"
        )
    return match.group(1)


def require_post_id(external_id: str) -> str:
    if not POST_ID.match(external_id):
        raise ThreadNotFound(f"identyfikator posta Reddita to base36, nie {external_id!r}")
    return external_id


def observed_int(value: Any, *, hidden: bool, what: str) -> Observed[int]:
    if hidden:
        return Observed[int].unknown(
            UnknownReason.NOT_EXPOSED_BY_PLATFORM, f"Reddit ukrywa {what} (hide_score)"
        )
    if value is None:
        return Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM, f"Reddit: {what} null")
    if isinstance(value, bool) or not isinstance(value, int):
        return Observed[int].unknown(
            UnknownReason.ADAPTER_COULD_NOT_PARSE, f"Reddit: {what}={value!r}"
        )
    return Observed[int].known(value)


def created_at(data: Mapping[str, Any]) -> datetime:
    epoch = data.get("created_utc")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        return datetime.fromtimestamp(float(epoch), tz=UTC)
    raise SourceUnavailable(f"Reddit: element bez created_utc: {dict(data)!r}"[:300])


def author(data: Mapping[str, Any]) -> str | None:
    value = data.get("author")
    if not value or value == "[deleted]":
        return None
    return str(value)


def body(data: Mapping[str, Any], field: str) -> str | None:
    text = data.get(field)
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or stripped in {"[deleted]", "[removed]"}:
        return None
    return stripped


def listing_children(listing: Any, *, where: str) -> list[Mapping[str, Any]]:
    if not isinstance(listing, Mapping) or listing.get("kind") != "Listing":
        raise SourceUnavailable(f"Reddit: {where} nie jest Listingiem")
    data = listing.get("data")
    children = data.get("children") if isinstance(data, Mapping) else None
    if not isinstance(children, list):
        raise SourceUnavailable(f"Reddit: {where} bez data.children")
    return [child for child in children if isinstance(child, Mapping)]


def thread_from_link(
    data: Mapping[str, Any], *, community_key: str, observed_at: datetime
) -> ObservedThread:
    external_id = str(data.get("id") or "")
    if not external_id:
        raise SourceUnavailable(f"Reddit: post bez id: {dict(data)!r}"[:300])
    permalink = data.get("permalink") or f"/{community_key}/comments/{external_id}/"
    hidden = bool(data.get("hide_score"))
    is_self = bool(data.get("is_self"))
    link_url = None if is_self else (data.get("url") or None)
    return ObservedThread(
        platform=ObservationPlatform.REDDIT,
        community_key=community_key,
        external_id=external_id,
        url=f"{PERMALINK_BASE}{permalink}",
        link_url=link_url,
        title=data.get("title") or f"(bez tytułu) {external_id}",
        body=body(data, "selftext"),
        author=author(data),
        created_at=created_at(data),
        observed_at=observed_at,
        metrics=ThreadMetrics(
            score=observed_int(data.get("score"), hidden=hidden, what="score"),
            comment_count=observed_int(data.get("num_comments"), hidden=False, what="num_comments"),
        ),
        raw_sha256=raw_digest(dict(data)),
    )


def flatten_comments(
    children: list[Mapping[str, Any]],
    parent_external_id: str | None,
    depth: int,
    observed_at: datetime,
    out: list[ObservedComment],
) -> bool:
    """Depth-first over `t1` comments. Returns `True` when a `more` stub was met — truncation."""
    truncated = False
    for child in children:
        kind = child.get("kind")
        data = child.get("data")
        if kind == "more":
            truncated = True
            continue
        if kind != "t1" or not isinstance(data, Mapping) or not data.get("id"):
            continue
        external_id = str(data["id"])
        out.append(
            ObservedComment(
                external_id=external_id,
                parent_external_id=parent_external_id,
                author=author(data),
                body=body(data, "body"),
                created_at=created_at(data),
                observed_at=observed_at,
                depth=depth,
                score=observed_int(
                    data.get("score"), hidden=bool(data.get("score_hidden")), what="score"
                ),
            )
        )
        replies = data.get("replies")
        if isinstance(replies, Mapping):
            nested = (
                replies.get("data", {}).get("children")
                if isinstance(replies.get("data"), Mapping)
                else None
            )
            if isinstance(nested, list) and flatten_comments(
                [c for c in nested if isinstance(c, Mapping)],
                external_id,
                depth + 1,
                observed_at,
                out,
            ):
                truncated = True
    return truncated


def parse_listing_threads(
    payload: Any,
    *,
    community_key: str,
    observed_at: datetime,
    since: datetime | None,
    where: str,
) -> tuple[ObservedThread, ...]:
    """A `/new` listing → threads newer than `since`, in the platform's (newest-first) order.

    `/new` has no server-side "since" filter on either transport; the watermark is applied here.
    """
    threads: list[ObservedThread] = []
    for child in listing_children(payload, where=where):
        if child.get("kind") != "t3" or not isinstance(child.get("data"), Mapping):
            continue
        thread = thread_from_link(
            child["data"], community_key=community_key, observed_at=observed_at
        )
        if since is not None and thread.created_at <= since:
            continue
        threads.append(thread)
    return tuple(threads)


def parse_thread_pair(
    payload: Any,
    *,
    community_key: str,
    external_id: str,
    observed_at: datetime,
    where: str,
) -> ThreadSnapshot:
    """A `/comments/<id>` response (post listing + comment listing) → one snapshot."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise SourceUnavailable(f"Reddit: odpowiedź {where} nie jest parą listingów")
    posts = listing_children(payload[0], where=f"{where}[0]")
    if not posts or posts[0].get("kind") != "t3" or not isinstance(posts[0].get("data"), Mapping):
        raise ThreadNotFound(f"Reddit: brak posta {external_id} w odpowiedzi")
    thread = thread_from_link(
        posts[0]["data"], community_key=community_key, observed_at=observed_at
    )
    comments: list[ObservedComment] = []
    truncated = flatten_comments(
        listing_children(payload[1], where=f"{where}[1]"), None, 0, observed_at, comments
    )
    return ThreadSnapshot(thread=thread, comments=tuple(comments), comments_truncated=truncated)
