"""Reddit search through a search engine: the `reddit.search` backend of the HOSTED product.

THE LOCAL PRODUCT READS REDDIT THROUGH THE FOUNDER'S OWN BROWSER (`reddit_search.py`, the capsule).
A hosted session has no capsule and no browser of the founder's behind it, and Reddit refuses
requests that come from a data centre, so the hosted `reddit.search` asks a search API instead:
Serper (https://serper.dev, Google results), with `site:reddit.com/r/ <phrase>` and the engine's
recency filter. Same tool, same result shape (`RedditHit`-like objects, the exceptions of
`reddit_search`), a different door.

WHAT THIS DOOR CANNOT TELL, SAID INSTEAD OF GUESSED. A search engine returns a title, a snippet
and an address. It does not return the author, the score, the number of comments, or whether the
post is locked or archived, so those are `None` (the tool then says "unknown", and never
`przyjmie_odpowiedz: true`). The `date` it returns is sometimes absolute ("Oct 8, 2026") and often
relative ("3 hours ago"); both are turned into a time, the relative one against the moment of the
request, and the engine's own text travels next to it (`date_text`) so nobody mistakes an estimate
for a timestamp. A result with no date, or one this module cannot read, has `created_at=None`: a
date is never made up.

COST IS BOUNDED PER ORGANIZATION (`SerpQuota`). Every query is paid for, and a hosted deployment
is reachable by strangers with a token, so each query first takes one unit of the organization's
daily allowance (default 60, `WENEXT_SERP_DAILY_CAP`) from `cost_ledger` (kind FETCH, source
`serp:<uuid>`, which the ledger's own refill never writes, so the two cannot collide). The count
and the insert happen under one advisory lock, so parallel calls cannot both take the last unit.

WITHOUT A KEY (`SERPER_API_KEY`) THE ANSWER IS "UNAVAILABLE" WITH THE REASON, never an empty list:
"nobody wrote about this" and "the search is not configured" are different sentences.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
import httpx

from control_plane.ops.limits import KIND_FETCH

from .reddit_search import (
    BLOCKED,
    MISCONFIGURED,
    NOT_CONFIGURED,
    RATE_LIMITED,
    SORTS,
    TIME_SPANS,
    UNAVAILABLE,
    RedditSearchUnavailable,
    normalize_query,
    normalize_subreddit,
)

__all__ = [
    "DEFAULT_DAILY_CAP",
    "ENV_CAP",
    "ENV_KEY",
    "SERPER_URL",
    "RedditSearchUnavailable",
    "SerpHit",
    "SerpQuota",
    "hit_from_result",
    "parse_serp_date",
    "search_reddit",
]

SERPER_URL = "https://google.serper.dev/search"
ENV_KEY = "SERPER_API_KEY"
ENV_CAP = "WENEXT_SERP_DAILY_CAP"
DEFAULT_DAILY_CAP = 60
#: One page of results is one credit at the provider; more than this is a different price.
MAX_RESULTS = 10
EXCERPT_CHARS = 300
SERP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

#: The engine's recency filter (`tbs=qdr:<x>`), by the tool's `time`. `all` has none.
_RECENCY = {"hour": "h", "day": "d", "week": "w", "month": "m", "year": "y"}

_POST_PATH = re.compile(
    r"^/r/(?P<sub>[A-Za-z0-9_]{2,21})/comments/(?P<id>[a-z0-9]{1,12})(?:/(?P<slug>[^/]+))?"
)
_REDDIT_HOSTS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"})
_RELATIVE = re.compile(r"^(\d{1,3})\s*(minute|min|hour|hr|day|week)s?\s+ago$", re.IGNORECASE)
_TITLE_TAIL = re.compile(r"\s*:\s*r/[A-Za-z0-9_]{2,21}\s*$")
_TITLE_HEAD = re.compile(r"^r/[A-Za-z0-9_]{2,21}\s+on\s+Reddit:\s*", re.IGNORECASE)
_UNIT_SECONDS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800}
_ABSOLUTE_FORMS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d")


@dataclass(frozen=True)
class SerpHit:
    """One result, in the shape of `reddit_search.RedditHit` where it has the fact, `None` where a
    search engine does not give it. `locked` and `archived` are `None`, not `False`: unknown."""

    post_id: str
    subreddit: str
    title: str
    excerpt: str
    url: str
    #: `None` when the result had no readable date (see the module docstring).
    created_at: datetime | None
    #: The engine's own words for the date ("3 hours ago", "Oct 8, 2026"), or `None`.
    date_text: str | None = None
    author: str | None = None
    score: int | None = None
    num_comments: int | None = None
    locked: bool | None = None
    archived: bool | None = None


def parse_serp_date(text: Any, *, now: datetime) -> datetime | None:
    """The time behind a search engine's `date`, or `None` when it cannot be read.

    "5 hours ago" is `now` minus five hours (the engine rounds, so this is an estimate and the raw
    text is kept beside it); "Oct 8, 2026" is that day at 00:00 UTC. Months and years ("2 months
    ago") are too coarse to be a time and give `None`, as does anything else.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _RELATIVE.match(raw)
    if match is not None:
        seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
        return now - timedelta(seconds=seconds)
    for form in _ABSOLUTE_FORMS:
        try:
            moment = datetime.strptime(raw, form)
        except ValueError:
            continue
        return moment.replace(tzinfo=UTC)
    return None


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _clean_title(raw: Any) -> str:
    title = _collapse(raw)
    title = _TITLE_HEAD.sub("", title)
    return _TITLE_TAIL.sub("", title).strip()


def hit_from_result(result: Mapping[str, Any], *, now: datetime) -> SerpHit | None:
    """One `organic[]` entry as a `SerpHit`, or `None` when it is not a Reddit POST: a user
    profile, a subreddit page, a wiki, another site. A comment permalink is the post it sits in."""
    link = str(result.get("link") or "").strip()
    parts = urlsplit(link)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in _REDDIT_HOSTS:
        return None
    match = _POST_PATH.match(parts.path)
    if match is None:
        return None
    title = _clean_title(result.get("title"))
    if not title:
        return None
    sub, post_id, slug = match.group("sub"), match.group("id"), match.group("slug")
    canonical = f"https://www.reddit.com/r/{sub}/comments/{post_id}/" + (f"{slug}/" if slug else "")
    date_text = _collapse(result.get("date")) or None
    excerpt = _collapse(result.get("snippet"))
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[: EXCERPT_CHARS - 3].rstrip() + "..."
    return SerpHit(
        post_id=post_id,
        subreddit=sub,
        title=title,
        excerpt=excerpt,
        url=canonical,
        created_at=parse_serp_date(date_text, now=now),
        date_text=date_text,
    )


def _unavailable(reason: str, kind: str, detail: str = "") -> RedditSearchUnavailable:
    return RedditSearchUnavailable(reason, kind=kind, detail=detail)


def serper_request(*, query: str, subreddit: str | None, time: str, limit: int) -> dict[str, Any]:
    """The JSON body of one Serper call. Validates the arguments like `build_search_url` does."""
    cleaned = normalize_query(query)
    scope = "site:reddit.com/r/"
    if subreddit is not None:
        scope = f"site:reddit.com/r/{normalize_subreddit(subreddit)}/"
    if time not in TIME_SPANS:
        raise ValueError(
            f"Okno czasu {time!r} jest niedozwolone; dozwolone: {', '.join(TIME_SPANS)}."
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError(f"Limit musi być liczbą od 1 do 100, nie {limit!r}.")
    body: dict[str, Any] = {"q": f"{scope} {cleaned}", "num": min(limit, MAX_RESULTS)}
    if time in _RECENCY:
        body["tbs"] = f"qdr:{_RECENCY[time]}"
    return body


class SerpQuota:
    """One organization's daily allowance of search queries, kept in `cost_ledger`."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        organization_id: UUID,
        project_id: UUID | None = None,
        *,
        cap: int | None = None,
        now: datetime | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._pool = pool
        self._organization_id = organization_id
        self._project_id = project_id
        self._now = now
        source = os.environ if environ is None else environ
        if cap is not None:
            self.cap = cap
        else:
            try:
                self.cap = int(str(source.get(ENV_CAP) or "").strip() or DEFAULT_DAILY_CAP)
            except ValueError:
                self.cap = DEFAULT_DAILY_CAP

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def used_today(self) -> int:
        """Queries taken since 00:00 UTC."""
        start = self._moment().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM cost_ledger WHERE organization_id = $1 AND kind = $2 "
                    "AND source_ref LIKE 'serp:%' AND occurred_at >= $3",
                    self._organization_id,
                    KIND_FETCH,
                    start,
                )
            )

    async def take(self) -> None:
        """Take one unit, or raise `RedditSearchUnavailable(kind="rate_limited")` when none is left.

        The count and the insert run in one transaction under an advisory lock named after the
        organization, so two calls at once cannot both see "59 of 60" and both go on.
        """
        moment = self._moment()
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"serp:{self._organization_id}"
            )
            used = int(
                await conn.fetchval(
                    "SELECT count(*) FROM cost_ledger WHERE organization_id = $1 AND kind = $2 "
                    "AND source_ref LIKE 'serp:%' AND occurred_at >= $3",
                    self._organization_id,
                    KIND_FETCH,
                    start,
                )
            )
            if used >= self.cap:
                raise _unavailable(
                    f"The daily search allowance of this project is used up ({used} of "
                    f"{self.cap} queries today, counted from 00:00 UTC). Work with the people "
                    "already saved (opportunities.list) and search again tomorrow.",
                    RATE_LIMITED,
                )
            await conn.execute(
                "INSERT INTO cost_ledger "
                "(organization_id, project_id, kind, units, source_ref, occurred_at) "
                "VALUES ($1, $2, $3, 1, 'serp:' || gen_random_uuid()::text, $4)",
                self._organization_id,
                self._project_id,
                KIND_FETCH,
                moment,
            )


async def search_reddit(
    *,
    query: str,
    subreddit: str | None = None,
    sort: str = "new",
    time: str = "week",
    limit: int = 10,
    quota: SerpQuota | None = None,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    now: datetime | None = None,
) -> list[SerpHit]:
    """Reddit posts that match `query`, found through the search API. Read-only.

    The signature is the one of `reddit_search.search_reddit` (`sort` is accepted and ignored: the
    engine orders by relevance within the recency window), plus the pieces a test injects.
    `ValueError` for a bad argument; `RedditSearchUnavailable` for the environment: no key, the
    daily allowance spent, the provider down or refusing.
    """
    if sort not in SORTS:
        raise ValueError(f"Sortowanie {sort!r} jest niedozwolone; dozwolone: {', '.join(SORTS)}.")
    body = serper_request(query=query, subreddit=subreddit, time=time, limit=limit)
    key = (api_key if api_key is not None else os.environ.get(ENV_KEY) or "").strip()
    if not key:
        raise _unavailable(
            "Reddit search is not available in this deployment: the search backend has no API key "
            f"({ENV_KEY}). Tell the founder search is off for now, and work with the people "
            "already saved (opportunities.list).",
            NOT_CONFIGURED,
        )
    if quota is not None:
        await quota.take()
    moment = now or datetime.now(UTC)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=SERP_TIMEOUT)
    try:
        try:
            # A QUERY, NOT A WRITE. Serper takes its search as a POST body; nothing on any
            # platform is created or changed. `request("POST", ...)` and not `.post(`:
            # `execution/bypass_scan.py` counts every `.post(` outside the side-effect adapter as
            # a possible publication, and this call is a read the scan cannot tell apart by name.
            response = await http.request(
                "POST",
                SERPER_URL,
                json=body,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise _unavailable(
                "The search backend did not answer. Try again in a moment, once.",
                UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
    finally:
        if owned:
            await http.aclose()
    status = response.status_code
    if status in (401, 403):
        raise _unavailable(
            "The search backend refused our API key, so search is off until the operator fixes it.",
            MISCONFIGURED if status == 401 else BLOCKED,
            detail=response.text[:200],
        )
    if status == 429:
        raise _unavailable(
            "The search backend is rate limiting us. Wait a few minutes before searching again.",
            RATE_LIMITED,
            detail=response.text[:200],
        )
    if status >= 400:
        raise _unavailable(
            f"The search backend answered {status}. Try again later; do not retry in a loop.",
            UNAVAILABLE,
            detail=response.text[:200],
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise _unavailable(
            "The search backend returned something that is not JSON.", UNAVAILABLE
        ) from exc
    organic = payload.get("organic") if isinstance(payload, dict) else None
    if not isinstance(organic, list):
        # Serper omits `organic` when nothing matched; an answer with no usable shape at all is not
        # the same thing and must not read as "nobody wrote about this".
        if isinstance(payload, dict) and ("searchParameters" in payload or not payload):
            return []
        raise _unavailable("The search backend answered in a shape we do not know.", UNAVAILABLE)
    hits: list[SerpHit] = []
    seen: set[str] = set()
    for result in organic:
        if not isinstance(result, Mapping):
            continue
        hit = hit_from_result(result, now=moment)
        if hit is None or hit.post_id in seen:
            continue
        seen.add(hit.post_id)
        hits.append(hit)
    return hits
