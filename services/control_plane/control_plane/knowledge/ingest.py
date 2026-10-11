"""Turning an address, a feed or a typed note into a source with its bytes kept.

WHAT INGEST IS FOR: it is the ONE place where "we looked at something" becomes a row. Everything
it writes carries the same four facts, and a source missing any of them is a source nobody can
check later: WHERE it came from (`locator`), WHEN we read it (`fetched_at`), WHAT the bytes were
(`content_hash`, plus the bytes themselves in `artifacts`) and, when the read failed, WHY
(`status = 'UNAVAILABLE'` with a reason). The rule that shapes every function here is that the
fourth case is a RECORDED STATE and never an empty success: a page that returned 404 is not a
page with no content, and nothing in this module will let it be stored as one.

THE BYTES GO TO `artifacts`, KIND `SOURCE`. Not into a column: the artifact table already has a
checksum, a retention, an ACL and a sweeper, and duplicating any of that beside it would mean two
answers to "what did we read and how long do we keep it". The row goes in before the bytes, so a
rejected insert leaves nothing on the volume.

WHAT AN ENTRY OF A FEED BECOMES. A feed is read as a source; each entry becomes a `FACT` stated
by `SOURCE`, pointing at the feed row. It is deliberately not "the article said X" — we have not
read the article, only the feed's claim that it exists and has this title on this date. Writing
it as anything stronger would manufacture a fact out of a headline.

NOTHING HERE DECIDES ANYTHING ABOUT THE CONTENT. Material arrives as data and stays data: no
instruction inside a page, a feed or a search result is acted on, and P3 says it in those words
("materiały z zewnątrz nie stają się instrukcjami systemowymi").
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.knowledge.fetch import Fetched, FetchRefused, fetch_url
from control_plane.knowledge.rss import Feed, FeedUnreadable, parse_feed
from control_plane.knowledge.search import Answer, WebSearch, backends_from_env
from control_plane.knowledge.store import ItemRow, KnowledgeStore, NewItem, Scope, SourceRow
from control_plane.storage.artifacts import ArtifactStore, ArtifactVolume
from control_plane.storage.config import (
    ARTIFACT_RETENTION,
    ArtifactAcl,
    ArtifactKind,
)

KIND_WEB_PAGE = "WEB_PAGE"
KIND_WEB_SEARCH = "WEB_SEARCH"
KIND_RSS = "RSS"
KIND_NEWSLETTER = "NEWSLETTER"
KIND_DOCUMENT = "DOCUMENT"
KIND_USER_NOTE = "USER_NOTE"

STATUS_FETCHED = "FETCHED"
STATUS_UNAVAILABLE = "UNAVAILABLE"

#: A search is stored under this locator, so looking for the same thing twice updates one row
#: instead of filling the list of materials with near-identical searches.
SEARCH_LOCATOR = "search:{query}"

#: How many entries of one feed are written as items on a refresh.
FEED_ITEMS = 20

#: Where the bytes live. The same variable the other services already read, with the same default,
#: because a second name for one directory is how a deployment ends up with two of them.
ARTIFACTS_DIR_VARIABLE = "WENEXT_ARTIFACTS_DIR"
DEFAULT_ARTIFACTS_DIR = "/var/wenext/artifacts"


def artifact_volume(env: Mapping[str, str] | None = None) -> ArtifactVolume:
    """The volume this deployment keeps raw material on.

    Always a volume and never `None`: a source recorded as FETCHED whose bytes were dropped is a
    hash nobody can check against anything, and "we kept it" is the whole promise of provenance.
    A directory that cannot be written to therefore fails at the write, where it belongs.

    CALL THIS LATE. `ArtifactVolume.__init__` creates the directory, so calling it while ASSEMBLING
    something (a handler map, a router) moves a filesystem failure to process start. Measured by
    wenext-84 on 2026-09-19: building it inside `observe/wiring.build_handlers` raised
    `PermissionError: /var/wenext` on a developer host and took the WHOLE dispatcher down with it,
    observation, pilot and publication included. Hence `Ingestor.volume_factory`, which resolves
    this at the first read or write rather than at construction.
    """
    root = ((env or os.environ).get(ARTIFACTS_DIR_VARIABLE) or DEFAULT_ARTIFACTS_DIR).strip()
    return ArtifactVolume(Path(root or DEFAULT_ARTIFACTS_DIR))


@dataclass(frozen=True)
class Ingested:
    """What one ingest produced: the row, and the items it created (a feed makes several)."""

    source: SourceRow
    items: tuple[ItemRow, ...] = ()
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.source.status == STATUS_FETCHED


@dataclass
class Ingestor:
    """Fetching and writing, over one pool, one artifact volume and one fetcher.

    `fetcher` and `searcher` are injected rather than imported at the point of use: the
    end-to-end suite points them at a local server and reaches no external network, which is the
    condition the assignment puts on these tests.
    """

    pool: asyncpg.Pool
    volume: ArtifactVolume | None = None
    #: A volume built on FIRST USE. See `artifact_volume` for why this is not resolved eagerly:
    #: a constructor that makes a directory turns an unwritable path into a dead dispatcher.
    volume_factory: Callable[[], ArtifactVolume] | None = None
    fetcher: Any = None
    searcher: WebSearch | None = None
    artifacts: ArtifactStore | None = None

    def __post_init__(self) -> None:
        self.store = KnowledgeStore(self.pool)
        self.artifacts = self.artifacts or ArtifactStore()

    def resolved_volume(self) -> ArtifactVolume | None:
        """The volume, building it now if that was deferred. `None` only when nothing was given."""
        if self.volume is None and self.volume_factory is not None:
            self.volume = self.volume_factory()
        return self.volume

    async def _fetch(self, url: str) -> Fetched:
        fetcher = self.fetcher or fetch_url
        return await fetcher(url)

    async def _keep(self, scope: Scope, content: bytes) -> UUID | None:
        """The bytes, in `artifacts`, kind `SOURCE`. `None` when there were none to keep."""
        if not content:
            return None
        async with self.pool.acquire() as conn, conn.transaction():
            now = await conn.fetchval("SELECT now()")
            record = await self.artifacts.record(
                conn,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                kind=ArtifactKind.SOURCE,
                acl=ArtifactAcl.OPERATOR_AND_RUNNER,
                content=content,
                encrypted=False,
                retention_until=now + ARTIFACT_RETENTION,
                volume=self.resolved_volume(),
            )
        return record.artifact_id

    async def _unavailable(
        self, scope: Scope, *, kind: str, locator: str, title: str, reason: str
    ) -> Ingested:
        """One shape for every failure, so no caller has to invent what a failed read looks like."""
        source = await self.store.upsert_source(
            scope,
            kind=kind,
            locator=locator,
            title=title,
            status=STATUS_UNAVAILABLE,
            fetched_at=datetime.now(UTC),
            content_hash=None,
            raw_artifact_id=None,
            unavailable_reason=reason[:2000],
        )
        return Ingested(source=source, reason=reason)

    async def add_url(self, scope: Scope, url: str, *, kind: str = KIND_WEB_PAGE) -> Ingested:
        """Read one page and keep what came back. A refused address is a source with a reason.

        A `FetchRefused` is written down rather than raised onward on purpose: the person pasted
        an address and is owed the sentence saying why this product will not read it, in the same
        list as everything else they added.
        """
        try:
            fetched = await self._fetch(url)
        except FetchRefused as exc:
            return await self._unavailable(scope, kind=kind, locator=url, title="", reason=str(exc))
        if not fetched.ok:
            return await self._unavailable(
                scope, kind=kind, locator=url, title="", reason=fetched.reason or "nieznany powód"
            )

        artifact_id = await self._keep(scope, fetched.content)
        title = fetched.title or url
        source = await self.store.upsert_source(
            scope,
            kind=kind,
            locator=url,
            title=title[:500],
            status=STATUS_FETCHED,
            fetched_at=datetime.now(UTC),
            content_hash=fetched.content_hash,
            raw_artifact_id=artifact_id,
            unavailable_reason=None,
        )
        return Ingested(source=source)

    async def add_note(self, scope: Scope, *, title: str, text: str) -> Ingested:
        """A note the person typed. Its own kind, because its provenance is a person, not a URL.

        It gets the same treatment as a page: the bytes are kept and hashed. A note edited later
        therefore shows a different hash, which is the only way a quotation from it stays checkable.
        """
        content = text.encode("utf-8")
        artifact_id = await self._keep(scope, content)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        locator = f"note:{digest[7:23]}"
        source = await self.store.upsert_source(
            scope,
            kind=KIND_USER_NOTE,
            locator=locator,
            title=(title or "Notatka")[:500],
            status=STATUS_FETCHED,
            fetched_at=datetime.now(UTC),
            content_hash=digest,
            raw_artifact_id=artifact_id,
            unavailable_reason=None,
        )
        return Ingested(source=source)

    async def add_document(
        self, scope: Scope, *, title: str, text: str, session_id: UUID | None = None
    ) -> Ingested:
        """Keep extracted document text as source material, never as the person's own words.

        The caller retains page markers and extraction provenance in the text. Content-addressed
        locators make repeat imports refer to the same source within this project.
        """
        content = text.encode("utf-8")
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        artifact_id = await self._keep(scope, content)
        source = await self.store.upsert_source(
            scope,
            kind=KIND_DOCUMENT,
            locator=(
                f"agent-document:{session_id}:{digest}" if session_id else f"document:{digest}"
            ),
            title=title[:500],
            status=STATUS_FETCHED,
            fetched_at=datetime.now(UTC),
            content_hash=digest,
            raw_artifact_id=artifact_id,
            unavailable_reason=None,
        )
        return Ingested(source=source)

    async def add_feed(self, scope: Scope, url: str, *, kind: str = KIND_RSS) -> Ingested:
        """Read a feed, keep its bytes, and write one item per entry.

        `FeedUnreadable` lands as `UNAVAILABLE` with the parser's sentence: bytes that are not a
        feed are a state of that address, and recording them as an empty feed would mean the
        screen shows a source that is "fine" and contains nothing.
        """
        try:
            fetched = await self._fetch(url)
        except FetchRefused as exc:
            return await self._unavailable(scope, kind=kind, locator=url, title="", reason=str(exc))
        if not fetched.ok:
            return await self._unavailable(
                scope, kind=kind, locator=url, title="", reason=fetched.reason or "nieznany powód"
            )
        try:
            feed: Feed = parse_feed(fetched.content)
        except FeedUnreadable as exc:
            return await self._unavailable(scope, kind=kind, locator=url, title="", reason=str(exc))

        artifact_id = await self._keep(scope, fetched.content)
        source = await self.store.upsert_source(
            scope,
            kind=kind,
            locator=url,
            title=(feed.title or url)[:500],
            status=STATUS_FETCHED,
            fetched_at=datetime.now(UTC),
            content_hash=fetched.content_hash,
            raw_artifact_id=artifact_id,
            unavailable_reason=None,
        )

        written: list[ItemRow] = []
        existing = {
            item.payload.get("link")
            for item in await self.store.list(
                scope, source_id=source.id, include_retired=True, limit=500
            )
        }
        for entry in feed.entries[:FEED_ITEMS]:
            if entry.link and entry.link in existing:
                # A refresh re-reads the same entries. Writing them again would turn a daily job
                # into a growing pile of identical facts.
                continue
            written.append(
                await self.store.write(
                    NewItem(
                        kind="FACT",
                        statement=(entry.title or entry.link or "wpis bez tytułu")[:2000],
                        stated_by="SOURCE",
                        project_id=scope.project_id,
                        source_id=source.id,
                        source_span=entry.link or None,
                        payload={
                            "link": entry.link,
                            "published": entry.published,
                            "summary": entry.summary,
                        },
                    ),
                    scope,
                )
            )
        return Ingested(source=source, items=tuple(written))

    async def run_search(self, scope: Scope, query: str) -> tuple[Ingested, Answer]:
        """Search, keep the answer as a source, and say which backend produced each hit.

        A search that no backend answered becomes `UNAVAILABLE` with the reasons listed. It does
        NOT become an empty result set, because an empty result set reads as "there is nothing",
        and that is a claim about the world this product did not make.
        """
        # The deployment's list, not the module default: a deployment with `…SEARCH_BACKENDS`
        # empty has web search off, and it must be off through this door as well as through the
        # session's tool.
        searcher = self.searcher or WebSearch(backends=backends_from_env())
        answer = await searcher.search(query)
        locator = SEARCH_LOCATOR.format(query=query.strip().lower())[:500]
        if not answer.ok:
            ingested = await self._unavailable(
                scope,
                kind=KIND_WEB_SEARCH,
                locator=locator,
                title=f"Szukanie: {query}"[:500],
                reason=answer.reason(),
            )
            return ingested, answer

        content = json.dumps(
            {
                "query": answer.query,
                "checked": list(answer.checked),
                "unavailable": [list(pair) for pair in answer.unavailable],
                "results": [
                    {
                        "title": hit.title,
                        "url": hit.url,
                        "snippet": hit.snippet,
                        "backend": hit.backend,
                    }
                    for hit in answer.results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        artifact_id = await self._keep(scope, content)
        source = await self.store.upsert_source(
            scope,
            kind=KIND_WEB_SEARCH,
            locator=locator,
            title=f"Szukanie: {query}"[:500],
            status=STATUS_FETCHED,
            fetched_at=datetime.now(UTC),
            content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            raw_artifact_id=artifact_id,
            unavailable_reason=None,
        )
        return Ingested(source=source), answer
