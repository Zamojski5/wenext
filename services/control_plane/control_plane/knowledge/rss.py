"""Reading a feed: RSS 2.0 and Atom, into entries with a date and a link.

WHY A PARSER AND NOT A LIBRARY. `feedparser` would be a new dependency in an image that already
ships three HTTP clients, and what P3 needs from a feed is four fields. The trade is stated
rather than hidden: this parser understands the two shapes below and says so when it understands
nothing, and the RAW BYTES are kept in `artifacts` beside the source, so a better parser later
reads the same material rather than needing a re-fetch.

XML AND UNTRUSTED INPUT. `xml.etree.ElementTree` does not expand external entities and raises on
undefined ones, so the classic XXE read of `/etc/passwd` is not available through it. What it
does NOT defend against is a billion-laughs expansion of INTERNAL entities, which is why the
bytes reaching this module are already capped by `fetch.MAX_BYTES` — the ceiling is the defence,
and it lives one module up on purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

#: How many entries one feed contributes. A feed with a thousand items is a feed somebody will
#: want paginated; until then the newest ones are the ones a project acts on.
MAX_ENTRIES = 50

_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG_SUFFIX = re.compile(r"^\{[^}]*\}")


class FeedUnreadable(Exception):
    """The bytes are not a feed this parser understands. A state of the source, with a reason."""


@dataclass(frozen=True)
class FeedEntry:
    title: str
    link: str
    published: str
    summary: str


@dataclass(frozen=True)
class Feed:
    title: str
    entries: tuple[FeedEntry, ...]


def _local(tag: str) -> str:
    return _TAG_SUFFIX.sub("", tag)


def _text(node, *names: str) -> str:
    """First non-empty child with one of these local names. Namespace-insensitive on purpose:
    the same feed is served as RSS and as Atom by different generators."""
    for child in node:
        if _local(child.tag) in names:
            value = (child.text or "").strip()
            if value:
                return value
    return ""


def _link(node) -> str:
    """RSS puts the URL in `<link>text</link>`, Atom in `<link href="…"/>`. Both, in that order."""
    for child in node:
        if _local(child.tag) != "link":
            continue
        text = (child.text or "").strip()
        if text:
            return text
        href = child.attrib.get("href", "").strip()
        if href and child.attrib.get("rel", "alternate") == "alternate":
            return href
    return ""


def parse_feed(content: bytes, *, max_entries: int = MAX_ENTRIES) -> Feed:
    """Entries out of a feed, newest first as the feed itself ordered them.

    Raises `FeedUnreadable` rather than returning an empty feed. "No entries" and "this was not a
    feed" are different facts about a source, and a caller that cannot tell them apart would
    record an empty RSS source as successfully read.
    """
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise FeedUnreadable(f"to nie jest poprawny XML: {exc}") from exc

    root_name = _local(root.tag)
    if root_name == "rss":
        channel = next((child for child in root if _local(child.tag) == "channel"), None)
        if channel is None:
            raise FeedUnreadable("RSS bez elementu <channel>")
        items = [child for child in channel if _local(child.tag) == "item"]
        title = _text(channel, "title")
    elif root_name == "feed":
        items = [child for child in root if _local(child.tag) == "entry"]
        title = _text(root, "title")
    else:
        raise FeedUnreadable(
            f"element główny {root_name!r} to ani <rss>, ani <feed>. Ten materiał nie jest kanałem"
        )

    entries = tuple(
        FeedEntry(
            title=_text(item, "title"),
            link=_link(item),
            published=_text(item, "pubDate", "published", "updated", "date"),
            summary=_text(item, "description", "summary", "content")[:2000],
        )
        for item in items[:max_entries]
    )
    return Feed(title=title, entries=entries)
