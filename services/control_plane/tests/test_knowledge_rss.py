"""`control_plane.knowledge.rss` - two feed dialects, and refusing to call anything else a feed.

THE ONE THING THAT MATTERS HERE: "no entries" and "this was not a feed" must not be the same
answer. A parser that returns an empty list for a login page would let the product record that
page as a healthy source with nothing in it, and nobody would ever look again.
"""

from __future__ import annotations

import pytest
from control_plane.knowledge.rss import FeedUnreadable, parse_feed

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Kolejki i bazy</title>
  <item>
    <title>Kolejka bez ack gubi wiadomosci</title>
    <link>https://example.test/kolejka</link>
    <pubDate>Fri, 19 Sep 2026 10:00:00 GMT</pubDate>
    <description>Opis pierwszego wpisu</description>
  </item>
  <item>
    <title>Drugi wpis</title>
    <link>https://example.test/drugi</link>
    <pubDate>Thu, 18 Sep 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Notatki</title>
  <entry>
    <title>Wpis atomowy</title>
    <link rel="alternate" href="https://example.test/atom-1"/>
    <updated>2026-09-19T10:00:00Z</updated>
    <summary>Streszczenie</summary>
  </entry>
</feed>"""


def test_an_rss_channel_gives_title_link_date_and_summary():
    feed = parse_feed(RSS)
    assert feed.title == "Kolejki i bazy"
    assert len(feed.entries) == 2
    first = feed.entries[0]
    assert first.title == "Kolejka bez ack gubi wiadomosci"
    assert first.link == "https://example.test/kolejka"
    assert "2026" in first.published
    assert first.summary == "Opis pierwszego wpisu"


def test_an_atom_feed_reads_the_same_way_including_the_href_form_of_link():
    """RSS puts the address in the text, Atom in an attribute. One parser, both shapes."""
    feed = parse_feed(ATOM)
    assert feed.title == "Notatki"
    assert feed.entries[0].link == "https://example.test/atom-1"
    assert feed.entries[0].summary == "Streszczenie"


def test_an_entry_without_a_summary_is_an_entry_with_an_empty_summary_not_a_crash():
    assert parse_feed(RSS).entries[1].summary == ""


@pytest.mark.parametrize(
    "content",
    [
        b"<html><body>Zaloguj sie</body></html>",
        b"nie xml w ogole",
        b"<?xml version='1.0'?><rss version='2.0'></rss>",
    ],
)
def test_bytes_that_are_not_a_feed_are_a_refusal_and_never_an_empty_feed(content):
    with pytest.raises(FeedUnreadable):
        parse_feed(content)


def test_the_number_of_entries_is_bounded():
    items = b"".join(
        f"<item><title>{n}</title><link>https://example.test/{n}</link></item>".encode()
        for n in range(200)
    )
    feed = parse_feed(
        b"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>"
        + items
        + b"</channel></rss>",
        max_entries=10,
    )
    assert len(feed.entries) == 10


def test_an_external_entity_does_not_read_a_local_file():
    """The classic XXE. `xml.etree` does not expand external entities; this pins it.

    The billion-laughs shape is NOT defended here and the module says so: the defence is the byte
    ceiling one module up, in `fetch.MAX_BYTES`.
    """
    attack = (
        b"<?xml version='1.0'?>"
        b"<!DOCTYPE rss [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
        b"<rss version='2.0'><channel><title>&xxe;</title>"
        b"<item><title>&xxe;</title></item></channel></rss>"
    )
    with pytest.raises(FeedUnreadable):
        parse_feed(attack)
