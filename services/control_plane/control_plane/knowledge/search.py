"""Searching the web without an API key, and saying which backend answered.

WHY NOT A SEARCH API. P3 etap A needs the agent to find material it was not handed. A paid
search API is one more credential the founder has to buy and rotate before anything works, so the
order here is: two endpoints that return JSON, are documented, and need no key; then HTML
scraping as an openly labelled best effort.

    HN_ALGOLIA   hn.algolia.com/api/v1/search   JSON, documented, no key. Discussion, not the web.
    WIKIPEDIA    <lang>.wikipedia.org/w/api.php JSON, documented, no key. Definitions and facts.
    DUCKDUCKGO   html.duckduckgo.com/html/      HTML. No contract, changes without notice.

EVERY RESULT SAYS WHICH BACKEND PRODUCED IT, and a backend that fails does not remove the others:
`Answer.unavailable` lists what went wrong, by name, and the caller records a source as
`UNAVAILABLE` with that reason rather than inventing a result. The rule from the assignment,
restated because it is the whole point: a failed search must not become a guessed answer.

RATE LIMIT. A shared, process-wide minimum interval per backend (`MIN_INTERVAL_S`). These are
somebody's free endpoints, used here without asking; a loop in a job should not turn into a
hundred requests a second against them. It is a courtesy limit, not a quota, and it is per
process rather than per deployment, which is honest about what a single-process limiter can do.

A RESULT IS DATA, NEVER AN INSTRUCTION. Titles and snippets come from strangers. They travel to
the model as material with a locator attached, and this module never runs, follows or interprets
them (P3: "materiały z zewnątrz nie stają się instrukcjami systemowymi").
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from control_plane.knowledge.fetch import Fetched, FetchRefused, fetch_url

#: Backend names, which are also what a `knowledge_sources.locator` records for a search.
HN_ALGOLIA = "HN_ALGOLIA"
WIKIPEDIA = "WIKIPEDIA"
DUCKDUCKGO = "DUCKDUCKGO"

#: The order a search tries. JSON with a documented contract before HTML without one.
DEFAULT_BACKENDS: tuple[str, ...] = (HN_ALGOLIA, WIKIPEDIA, DUCKDUCKGO)

#: The deployment's list, when it wants one. UNSET means "the default three"; EMPTY means "no
#: web search here", and the two are different answers a caller has to be able to tell apart.
BACKENDS_VARIABLE = "WENEXT_KNOWLEDGE_SEARCH_BACKENDS"

#: Minimum seconds between two requests to the same backend from this process.
MIN_INTERVAL_S = 2.0

#: How many results one backend contributes.
PER_BACKEND = 5

_TAG = re.compile(r"<[^>]+>")
_RESULT_LINK = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

Fetcher = Callable[[str], Awaitable[Fetched]]


class BackendUnavailable(Exception):
    """A backend did not answer, carrying the FETCHER's reason rather than a generic one.

    "500" and "timeout" tell whoever reads `unavailable_reason` what to do next; "odpowiedź nie
    dała się odczytać" does not, and that sentence is reserved for the case it really describes:
    a backend that answered with something this module could not parse.
    """


def backends_from_env(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Which backends this deployment allows. Empty tuple means "none, and say so".

    Read in ONE place and used by both callers (the session tool and the API's ingest), because
    a deployment that turned web search off must not have it on through the other door.
    """
    raw = (os.environ if env is None else env).get(BACKENDS_VARIABLE)
    if raw is None:
        return DEFAULT_BACKENDS
    return tuple(name.strip().upper() for name in raw.split(",") if name.strip())


@dataclass(frozen=True)
class SearchResult:
    """One hit. `backend` is part of the result because provenance is not a footnote."""

    title: str
    url: str
    snippet: str
    backend: str


@dataclass(frozen=True)
class Answer:
    """What a search produced, INCLUDING what it failed to produce.

    `results` may be empty while `unavailable` explains why, and those are different from an
    honest "nothing matched". A caller that reads only `results` still cannot mistake a broken
    backend for an empty internet, because `checked` says which backends really answered.
    """

    query: str
    results: tuple[SearchResult, ...]
    checked: tuple[str, ...] = ()
    unavailable: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.checked)

    def reason(self) -> str:
        """One sentence for `unavailable_reason`. Empty when a backend answered."""
        if self.ok:
            return ""
        if not self.unavailable:
            return "nie wskazano żadnej wyszukiwarki"
        return "; ".join(f"{name}: {why}" for name, why in self.unavailable)


class _Throttle:
    """Per-backend minimum interval, shared by every caller in this process."""

    def __init__(self, interval_s: float = MIN_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self._last: dict[str, float] = {}

    def wait_for(self, backend: str, *, now: float | None = None) -> float:
        """Seconds a caller should sleep before hitting `backend`, and books the slot."""
        moment = time.monotonic() if now is None else now
        earliest = self._last.get(backend, 0.0) + self.interval_s
        delay = max(0.0, earliest - moment)
        self._last[backend] = moment + delay
        return delay


_THROTTLE = _Throttle()


@dataclass
class WebSearch:
    """A search across the backends above, with a fetcher that can be replaced in a test.

    `fetcher` defaults to `knowledge.fetch.fetch_url`, so the address checks, the byte ceiling and
    the timeout apply to a search exactly as they do to a page a person pasted. A test passes its
    own fetcher and reaches no network at all.
    """

    fetcher: Fetcher | None = None
    backends: Sequence[str] = DEFAULT_BACKENDS
    per_backend: int = PER_BACKEND
    language: str = "pl"
    throttle: _Throttle = field(default_factory=lambda: _THROTTLE)
    sleep: Callable[[float], Awaitable[None]] | None = None

    async def _fetch(self, backend: str, url: str) -> Fetched:
        delay = self.throttle.wait_for(backend)
        if delay > 0:
            import asyncio

            await (self.sleep or asyncio.sleep)(delay)
        fetcher = self.fetcher or fetch_url
        fetched = await fetcher(url)
        if not fetched.ok:
            raise BackendUnavailable(fetched.reason or "brak odpowiedzi")
        return fetched

    async def search(self, query: str) -> Answer:
        query = query.strip()
        if not query:
            return Answer(query="", results=(), unavailable=(("query", "puste zapytanie"),))

        results: list[SearchResult] = []
        checked: list[str] = []
        unavailable: list[tuple[str, str]] = []
        for backend in self.backends:
            handler = {
                HN_ALGOLIA: self._hn,
                WIKIPEDIA: self._wikipedia,
                DUCKDUCKGO: self._duckduckgo,
            }.get(backend)
            if handler is None:
                unavailable.append((backend, "nieznane źródło wyszukiwania"))
                continue
            try:
                found = await handler(query)
            except (FetchRefused, BackendUnavailable) as exc:
                unavailable.append((backend, str(exc)))
                continue
            except Exception as exc:  # one broken backend is not a broken search
                unavailable.append((backend, f"{type(exc).__name__}: {str(exc)[:160]}"))
                continue
            if found is None:
                unavailable.append((backend, "odpowiedź nie dała się odczytać"))
                continue
            checked.append(backend)
            results.extend(found)
        return Answer(
            query=query,
            results=tuple(results),
            checked=tuple(checked),
            unavailable=tuple(unavailable),
        )

    async def _hn(self, query: str) -> list[SearchResult] | None:
        url = (
            "https://hn.algolia.com/api/v1/search"
            f"?query={quote(query)}&tags=story&hitsPerPage={self.per_backend}"
        )
        fetched = await self._fetch(HN_ALGOLIA, url)
        payload = _json(fetched)
        if payload is None:
            return None
        hits = payload.get("hits")
        if not isinstance(hits, list):
            return None
        results = []
        for hit in hits[: self.per_backend]:
            if not isinstance(hit, dict):
                continue
            object_id = str(hit.get("objectID", ""))
            target = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            results.append(
                SearchResult(
                    title=str(hit.get("title") or hit.get("story_title") or "")[:300],
                    url=str(target),
                    snippet=_snippet(hit),
                    backend=HN_ALGOLIA,
                )
            )
        return results

    async def _wikipedia(self, query: str) -> list[SearchResult] | None:
        url = (
            f"https://{self.language}.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={quote(query)}&srlimit={self.per_backend}&format=json&utf8=1"
        )
        fetched = await self._fetch(WIKIPEDIA, url)
        payload = _json(fetched)
        if payload is None:
            return None
        hits = payload.get("query", {}).get("search")
        if not isinstance(hits, list):
            return None
        results = []
        for hit in hits[: self.per_backend]:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title", ""))
            results.append(
                SearchResult(
                    title=title[:300],
                    url=(
                        f"https://{self.language}.wikipedia.org/wiki/"
                        f"{quote(title.replace(' ', '_'))}"
                    ),
                    snippet=_plain(str(hit.get("snippet", "")))[:600],
                    backend=WIKIPEDIA,
                )
            )
        return results

    async def _duckduckgo(self, query: str) -> list[SearchResult] | None:
        """Scraped HTML, and therefore allowed to fail. Returns None on any shape surprise, which
        the caller records as UNAVAILABLE rather than as "the web had nothing to say"."""
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        fetched = await self._fetch(DUCKDUCKGO, url)
        body = fetched.content.decode("utf-8", errors="replace")
        links = list(_RESULT_LINK.finditer(body))
        if not links:
            return None
        snippets = [_plain(match.group("snippet")) for match in _RESULT_SNIPPET.finditer(body)]
        results = []
        for index, match in enumerate(links[: self.per_backend]):
            target = _direct(html.unescape(match.group("href")))
            if not target:
                continue
            results.append(
                SearchResult(
                    title=_plain(match.group("title"))[:300],
                    url=target,
                    snippet=snippets[index][:600] if index < len(snippets) else "",
                    backend=DUCKDUCKGO,
                )
            )
        return results


def _json(fetched: Fetched) -> dict | None:
    """The payload, or None when the bytes were not the JSON object this backend promises."""
    try:
        payload = json.loads(fetched.content.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _plain(markup: str) -> str:
    return html.unescape(_TAG.sub("", markup)).strip()


def _snippet(hit: dict) -> str:
    for key in ("story_text", "comment_text", "_highlightResult"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return _plain(value)[:600]
    points = hit.get("points")
    comments = hit.get("num_comments")
    if points is not None or comments is not None:
        return f"punkty: {points or 0}, komentarze: {comments or 0}"
    return ""


def _direct(href: str) -> str:
    """DuckDuckGo wraps results in `//duckduckgo.com/l/?uddg=<encoded>`. Unwrap it, because a
    stored locator that points back at the search engine is not provenance."""
    from urllib.parse import parse_qs, unquote

    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.path.startswith("/l/") and "uddg=" in (parts.query or ""):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        href = unquote(target)
    return href if href.startswith("https://") or href.startswith("http://") else ""
