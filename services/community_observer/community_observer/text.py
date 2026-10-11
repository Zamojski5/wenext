"""HTML to plain text for the bodies platforms hand us as markup.

Hacker News returns `story_text` and comment `text` as a small HTML subset: `<p>` between
paragraphs, `<a href>` for links, `<i>` for emphasis, `<pre><code>` for code, and HTML
entities. The model reading a thread needs the text; the database keeping evidence needs the
digest of the RAW payload — so this conversion is applied to what is displayed and judged,
never to what is hashed (`models.raw_digest` hashes the payload as received).

Deliberately small. A general HTML-to-text library would be a dependency for four tags, and
Reddit's `selftext` is markdown that goes through untouched.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+\n")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "a":
            self._href = dict(attrs).get("href")
        elif tag == "pre":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            # Keep the target when the visible text is not the URL itself — a thread that says
            # "see this benchmark" is worthless to the reader without the address.
            self._href = None
        elif tag == "pre":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._href and data.strip() and data.strip() != self._href:
            self.parts.append(f"{data} ({self._href})")
            self._href = None  # written once per anchor
            return
        self.parts.append(data)


def html_to_text(markup: str | None) -> str | None:
    """Plain text with paragraphs preserved, or `None` for `None`/empty input.

    Empty stays `None` on purpose: a body that is an empty string would be stored as "the post
    said nothing", which is a different fact from "the post has no body".
    """
    if markup is None:
        return None
    if not markup.strip():
        return None
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    text = html.unescape("".join(extractor.parts))
    text = _TRAILING_SPACES.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    text = text.strip()
    return text or None
