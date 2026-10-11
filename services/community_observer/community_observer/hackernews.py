"""Hacker News through the public Algolia API — the first real community the product observes.

Why HN first (`docs/OUTCOMES.md` §6 point 1, D-023): the target audience of the first use case
is technical founders, devtools and OSS (`docs/KIERUNEK-PRODUKTU.md` §10); HN is the densest
such community, its API is public, documented and needs no account, and it was reachable from
the development machine on 2026-09-03 while Reddit answered 403 to everything without OAuth.
The roadmap's own registry logic names this the cheaper path to prefer (3.1 point 42,
`AccessMode.PUBLIC_API_NO_ACCOUNT`).

Two endpoints, both read-only, both measured against the live API on 2026-09-03 and recorded in
`tests/fixtures/`:

* `GET /api/v1/search_by_date?tags=<tag>&hitsPerPage=<n>[&numericFilters=created_at_i>N]`
  — stories newest first. A hit carries `objectID`, `title`, `author`, `url` (link posts),
  `story_text` (HTML, text posts), `points`, `num_comments`, `created_at` (ISO) and
  `created_at_i` (epoch).
* `GET /api/v1/items/<id>` — one item with the FULL comment tree under `children`, each child
  with `id`, `author`, `text` (HTML), `created_at`, `parent_id`, `children`. Deleted comments
  arrive with `author: null, text: null` and are kept as rows (see `ObservedComment`).

What the adapter does NOT do: it does not use the Firebase API (per-item requests, one per
comment — a 44-comment thread would be 45 round trips instead of one), it does not search by
query (Outcome A observes a community, it does not hunt keywords), and it does not write.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from community_os_contracts import AccessMode, ObservationPlatform, Observed, UnknownReason

from .models import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    ThreadSnapshot,
    raw_digest,
)
from .source import (
    MAX_LISTING,
    SourceUnavailable,
    ThreadNotFound,
    UnknownCommunity,
)
from .text import html_to_text

ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={id}"
USER_AGENT = "wenext-community-observer/0.1 (read-only observation; no posting)"

#: Community key → Algolia tag. These four are the surfaces of HN a person can "be present" in.
#: `new` is every story; `front` is what reached the front page (a lagging signal, useful for the
#: picture, useless for entering a conversation early).
COMMUNITIES: Mapping[str, tuple[str, str]] = {
    "ask": ("ask_hn", "Hacker News · Ask HN"),
    "show": ("show_hn", "Hacker News · Show HN"),
    "new": ("story", "Hacker News · wszystkie nowe wątki"),
    "front": ("front_page", "Hacker News · strona główna"),
}

#: Generous for a JSON API on a good day, and a hard stop on a bad one: a read that hangs is a
#: read that failed, and the job lease behind it is 60 seconds long (D-017).
TIMEOUT = httpx.Timeout(20.0)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _observed_int(value: Any, *, what: str) -> Observed[int]:
    """`null` and absent → UNKNOWN with the reason; a number → the number. Never 0 for missing."""
    if value is None:
        return Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM, f"Algolia: {what} null")
    if isinstance(value, bool) or not isinstance(value, int):
        return Observed[int].unknown(
            UnknownReason.ADAPTER_COULD_NOT_PARSE, f"Algolia: {what} = {value!r}"
        )
    return Observed[int].known(value)


def _created_at(item: Mapping[str, Any]) -> datetime:
    """Prefer the epoch (`created_at_i`); fall back to the ISO string. Both are UTC on Algolia."""
    epoch = item.get("created_at_i")
    if isinstance(epoch, int) and not isinstance(epoch, bool):
        return datetime.fromtimestamp(epoch, tz=UTC)
    iso = item.get("created_at")
    if isinstance(iso, str):
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise SourceUnavailable(f"Algolia: element bez created_at/created_at_i: {dict(item)!r}"[:300])


class HackerNewsSource:
    """`CommunitySource` for Hacker News. Public API, no account, read-only."""

    platform = ObservationPlatform.HACKER_NEWS
    access_mode = AccessMode.PUBLIC_API_NO_ACCOUNT

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str = ALGOLIA_BASE_URL,
        clock: Clock = _utc_now,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        self._base_url = base_url.rstrip("/")
        self._clock = clock

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- port -----------------------------------------------------------------

    def describe(self, community_key: str) -> CommunityRef:
        if community_key not in COMMUNITIES:
            raise UnknownCommunity(
                f"Hacker News nie ma community {community_key!r}. Znane: {sorted(COMMUNITIES)}"
            )
        return CommunityRef(
            platform=self.platform,
            key=community_key,
            display_name=COMMUNITIES[community_key][1],
            access_mode=self.access_mode,
        )

    async def list_recent_threads(
        self, community_key: str, *, since: datetime | None, limit: int = MAX_LISTING
    ) -> tuple[ObservedThread, ...]:
        tag = COMMUNITIES.get(community_key)
        if tag is None:
            raise UnknownCommunity(
                f"Hacker News nie ma community {community_key!r}. Znane: {sorted(COMMUNITIES)}"
            )
        if not 1 <= limit <= MAX_LISTING:
            raise ValueError(f"limit={limit} poza zakresem 1..{MAX_LISTING}")
        params: dict[str, str] = {"tags": tag[0], "hitsPerPage": str(limit)}
        if since is not None:
            if since.tzinfo is None:
                raise ValueError("since musi mieć strefę czasową")
            params["numericFilters"] = f"created_at_i>{int(since.timestamp())}"
        payload = await self._get_json("/search_by_date", params=params)
        hits = payload.get("hits") if isinstance(payload, Mapping) else None
        if not isinstance(hits, list):
            raise SourceUnavailable("Algolia: odpowiedź search_by_date bez listy `hits`")
        observed_at = self._clock()
        return tuple(self._thread_from_hit(hit, community_key, observed_at) for hit in hits)

    async def fetch_thread(self, community_key: str, external_id: str) -> ThreadSnapshot:
        if community_key not in COMMUNITIES:
            raise UnknownCommunity(
                f"Hacker News nie ma community {community_key!r}. Znane: {sorted(COMMUNITIES)}"
            )
        if not external_id.isdigit():
            raise ThreadNotFound(f"identyfikator HN to liczba, nie {external_id!r}")
        item = await self._get_json(f"/items/{external_id}", params=None, not_found=external_id)
        kind = item.get("type") if isinstance(item, Mapping) else None
        if kind not in {"story", "poll", "job"}:
            raise ThreadNotFound(f"element {external_id} nie jest wątkiem (type={kind!r})")
        observed_at = self._clock()
        comments = tuple(_flatten_comments(item.get("children") or [], None, 0, observed_at))
        thread = ObservedThread(
            platform=self.platform,
            community_key=community_key,
            external_id=str(item["id"]),
            url=ITEM_URL.format(id=item["id"]),
            link_url=item.get("url") or None,
            title=item.get("title") or f"(bez tytułu) {item['id']}",
            body=html_to_text(item.get("text")),
            author=item.get("author") or None,
            created_at=_created_at(item),
            observed_at=observed_at,
            metrics=ThreadMetrics(
                score=_observed_int(item.get("points"), what="points"),
                # The item endpoint carries the whole tree, so the count is what we read — a
                # measurement, not a counter the platform maintains.
                comment_count=Observed[int].known(len(comments)),
            ),
            raw_sha256=raw_digest(_without_children(item)),
        )
        return ThreadSnapshot(thread=thread, comments=comments, comments_truncated=False)

    # -- helpers ----------------------------------------------------------------

    def _thread_from_hit(
        self, hit: Mapping[str, Any], community_key: str, observed_at: datetime
    ) -> ObservedThread:
        external_id = str(hit.get("objectID") or hit.get("story_id") or "")
        if not external_id:
            raise SourceUnavailable(f"Algolia: hit bez objectID: {dict(hit)!r}"[:300])
        return ObservedThread(
            platform=self.platform,
            community_key=community_key,
            external_id=external_id,
            url=ITEM_URL.format(id=external_id),
            link_url=hit.get("url") or None,
            title=hit.get("title") or f"(bez tytułu) {external_id}",
            body=html_to_text(hit.get("story_text")),
            author=hit.get("author") or None,
            created_at=_created_at(hit),
            observed_at=observed_at,
            metrics=ThreadMetrics(
                score=_observed_int(hit.get("points"), what="points"),
                comment_count=_observed_int(hit.get("num_comments"), what="num_comments"),
            ),
            # `_highlightResult` is search decoration that changes with the query, not data.
            raw_sha256=raw_digest({k: v for k, v in hit.items() if k != "_highlightResult"}),
        )

    async def _get_json(
        self, path: str, *, params: Mapping[str, str] | None, not_found: str | None = None
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Algolia: {type(exc).__name__} przy {url}: {exc}") from exc
        if response.status_code == 404 and not_found is not None:
            raise ThreadNotFound(f"Algolia nie zna elementu {not_found}")
        if response.status_code == 429 or response.status_code >= 500:
            raise SourceUnavailable(f"Algolia odpowiedziała {response.status_code} na {url}")
        if response.status_code != 200:
            raise SourceUnavailable(
                f"Algolia odpowiedziała {response.status_code} na {url}: {response.text[:200]}"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceUnavailable(f"Algolia: odpowiedź na {url} nie jest JSON-em") from exc


def _without_children(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "children"}


def _flatten_comments(
    children: list[Any], parent_external_id: str | None, depth: int, observed_at: datetime
) -> list[ObservedComment]:
    """Depth-first, in the platform's order. Deleted comments stay as rows without author/body."""
    flat: list[ObservedComment] = []
    for child in children:
        if not isinstance(child, Mapping) or child.get("id") is None:
            continue
        if child.get("type") not in (None, "comment"):
            continue
        external_id = str(child["id"])
        flat.append(
            ObservedComment(
                external_id=external_id,
                parent_external_id=parent_external_id,
                author=child.get("author") or None,
                body=html_to_text(child.get("text")),
                created_at=_created_at(child),
                observed_at=observed_at,
                depth=depth,
                # Algolia's item tree carries `points` for the story only; comment points are
                # not public on Hacker News at all. A reason, not a zero.
                score=Observed[int].unknown(
                    UnknownReason.NOT_EXPOSED_BY_PLATFORM, "HN nie pokazuje punktów komentarzy"
                ),
            )
        )
        flat.extend(
            _flatten_comments(child.get("children") or [], external_id, depth + 1, observed_at)
        )
    return flat
