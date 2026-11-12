"""Every reply draft carries the post it answers (v11, 13.10).

THE FOUNDER (13.10), after a day of real use: "I cannot tell whose post it is, what it says, or
whether it is trending." A reply made with `draft.create` without an opportunity mark showed only
"Reply on X". Three things fix that, all here:

  * AT `draft.create`, for a REPLY under an X or Reddit post, the post is read once
    (`snapshot.py`) and recorded in the project's OPPORTUNITY for that conversation (0039: the
    opportunity row is one per conversation, and `room.show` already carries it, now with `cel`: the
    post's author, avatar, text, age and counters, and the Trending badge). An opportunity the agent
    marked is refreshed (its reason and angle are kept); one nobody marked is CREATED, with
    `AUTO_REASON`, which the page shows as no reason at all;
  * IN THE BACKFILL (`backfill_waiting_drafts`, run by `tools/dev/backfill_target_context.py`, never
    by the tool server): the same for the drafts that are READY_FOR_APPROVAL now;
  * AT THE PLATFORM, a post the founder's own browser says is GONE is recorded (`gone_at`), and a
    waiting draft under it is expired (`freshness.py`, reason `ZNIKNAL`).

BEST EFFORT, BOUNDED, NEVER A REFUSAL. The draft is the work; its context is a courtesy. Every read
has its own timeout, the whole step is bounded (`STEP_TIMEOUT`), and a failure of any kind is logged
and leaves the draft exactly as it was. Nothing here approves, signs, publishes or widens a scope.

A REDDIT POST WITHOUT ITS TIME IS NOT RECORDED when no opportunity stands for it yet: a later
`draft.create` runs `ensure_reddit_thread` on an opportunity, which needs the time, and an
opportunity this module created without one would turn the next draft into a refusal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import split_media

from .snapshot import (
    TargetSnapshot,
    fetch_fxtwitter,
    from_reddit_thread,
    from_x_page,
    trailing_status,
    x_target,
)
from .store import (
    AUTO_REASON,
    OpportunityRefused,
    OpportunityStore,
    OpportunityView,
    new_opportunity,
)

_LOG = logging.getLogger(__name__)

#: The whole step in `draft.create`, whatever the sources do.
STEP_TIMEOUT = 20.0
#: A snapshot read this recently is not read again by the next draft under the same post.
REFRESH_AFTER = timedelta(minutes=10)

#: An async reader of the founder's own browser for an X post address, or of Reddit for a post
#: address; `None` when this process has none. The tool server builds them from its registry.
PageReader = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class Readers:
    x_page: PageReader | None = None
    reddit_thread: PageReader | None = None
    http: Any = None


async def read_target(url: str, platform: str, readers: Readers) -> TargetSnapshot | None:
    """The post at `url`, from the best source that answers, or `None`. Never raises."""
    try:
        if platform == "X":
            target = x_target(url)
            if target is None:
                return None
            snapshot = await fetch_fxtwitter(url, client=readers.http)
            if snapshot is None and readers.x_page is not None:
                page = await readers.x_page(url)
                snapshot = from_x_page(page, url=url, status_id=target[1]) if page else None
            return snapshot
        if platform == "REDDIT" and readers.reddit_thread is not None:
            thread = await readers.reddit_thread(url)
            return from_reddit_thread(thread, url=url) if thread is not None else None
    except Exception:
        _LOG.info("odczyt posta %s nie powiódł się", url, exc_info=True)
    return None


async def record(
    store: OpportunityStore,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID | None,
    snapshot: TargetSnapshot,
    now: datetime | None = None,
) -> OpportunityView | None:
    """Write what was read into the conversation's opportunity (create it when there is none)."""
    moment = now or datetime.now(UTC)
    existing = await store.for_target(
        organization_id=organization_id, project_id=project_id, external_id=snapshot.external_id
    )
    if snapshot.gone:
        if existing is not None:
            await store.note_gone(
                organization_id=organization_id,
                project_id=project_id,
                external_id=snapshot.external_id,
            )
        return existing
    if snapshot.platform == "REDDIT" and snapshot.created_at is None and existing is None:
        return None  # see the module docstring
    try:
        opportunity = new_opportunity(
            platform=snapshot.platform,
            url=snapshot.url,
            external_id=snapshot.external_id,
            reason=existing.reason if existing is not None else AUTO_REASON,
            angle=existing.angle if existing is not None else "",
            community=snapshot.community or "",
            title=snapshot.title or "",
            excerpt=snapshot.text
            or (existing.excerpt if existing is not None else "")
            or snapshot.url.removeprefix("https://"),
            author=snapshot.handle,
            created_at=snapshot.created_at,
            score=snapshot.likes,
            num_comments=snapshot.replies,
            views=snapshot.views,
            reposts=snapshot.reposts,
            author_name=snapshot.name,
            avatar_url=snapshot.avatar_url,
            metrics_at=moment,
            require_time=False,
            author_verified=snapshot.verified,
            media=snapshot.media,
        )
        marked = await store.mark(
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            session_id=session_id,
            opportunity=opportunity,
        )
    except OpportunityRefused as exc:
        _LOG.info("odczytany post nie ma kształtu okazji: %s", exc)
        return existing
    return marked.view


async def record_reply_target(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID | None,
    kind: str,
    platform: str,
    target_external_id: str,
    target_url: str,
    readers: Readers,
) -> OpportunityView | None:
    """The step of `draft.create`: read the post a reply answers and keep it. Never raises."""
    if kind != "REPLY" or platform not in ("X", "REDDIT"):
        return None
    if platform == "REDDIT" and not target_external_id.startswith("t3_"):
        return None  # a reply to a comment: the post's own snapshot is not this comment's
    store = OpportunityStore(pool)

    async def step() -> OpportunityView | None:
        existing = await store.for_target(
            organization_id=organization_id, project_id=project_id, external_id=target_external_id
        )
        if (
            existing is not None
            and existing.metrics_at is not None
            and datetime.now(UTC) - existing.metrics_at < REFRESH_AFTER
        ):
            return existing
        snapshot = await read_target(target_url, platform, readers)
        if snapshot is None:
            return existing
        return await record(
            store,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            session_id=session_id,
            snapshot=snapshot,
        )

    try:
        return await asyncio.wait_for(step(), timeout=STEP_TIMEOUT)
    except Exception:
        _LOG.info("kontekst posta %s nie został zapisany", target_url, exc_info=True)
        return None


async def record_quote_context(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    kind: str,
    platform: str,
    text: str,
    readers: Readers,
) -> OpportunityView | None:
    """The step of `draft.create` for a QUOTE: an own post on X whose text ends with the address of
    a post. Read that post once and keep it for context (`OpportunityStore.note_context`: no page,
    no "Skipped" row), so the card can draw the quoted post under our words. Never raises, never
    refuses or changes the draft."""
    if kind != "OWN_POST" or platform != "X":
        return None
    quoted = trailing_status(text)
    if quoted is None:
        return None
    url, _handle, status_id = quoted
    store = OpportunityStore(pool)

    async def step() -> OpportunityView | None:
        external_id = f"x:{status_id}"
        existing = await store.for_target(
            organization_id=organization_id, project_id=project_id, external_id=external_id
        )
        if (
            existing is not None
            and existing.metrics_at is not None
            and datetime.now(UTC) - existing.metrics_at < REFRESH_AFTER
        ):
            return existing
        snapshot = await read_target(url, "X", readers)
        if snapshot is None or snapshot.gone:
            return existing
        opportunity = new_opportunity(
            platform="X",
            url=snapshot.url,
            external_id=snapshot.external_id,
            reason=AUTO_REASON,
            excerpt=snapshot.text or snapshot.url.removeprefix("https://"),
            author=snapshot.handle,
            created_at=snapshot.created_at,
            score=snapshot.likes,
            num_comments=snapshot.replies,
            views=snapshot.views,
            reposts=snapshot.reposts,
            author_name=snapshot.name,
            avatar_url=snapshot.avatar_url,
            metrics_at=datetime.now(UTC),
            require_time=False,
            author_verified=snapshot.verified,
            media=snapshot.media,
        )
        await store.note_context(
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            opportunity=opportunity,
        )
        return await store.for_target(
            organization_id=organization_id, project_id=project_id, external_id=external_id
        )

    try:
        return await asyncio.wait_for(step(), timeout=STEP_TIMEOUT)
    except Exception:
        _LOG.info("kontekst cytowanego posta %s nie został zapisany", url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------

_MISSING_SQL = """
SELECT r.id AS revision_id, r.workspace_id,
       r.payload ->> 'plan_version' AS project_id,
       r.payload ->> 'target_external_id' AS target_external_id,
       r.payload ->> 'target_url' AS target_url,
       a.platform
  FROM action_proposal_revisions r
  JOIN action_proposals p ON p.id = r.proposal_id AND p.organization_id = r.organization_id
  JOIN social_accounts a ON a.id = p.social_account_id
  JOIN projects pr
    ON pr.organization_id = r.organization_id AND pr.id::text = r.payload ->> 'plan_version'
  LEFT JOIN project_opportunities o
    ON o.organization_id = r.organization_id AND o.project_id = pr.id
   AND o.external_id = r.payload ->> 'target_external_id'
 WHERE r.organization_id = $1 AND r.state = 'READY_FOR_APPROVAL' AND r.expires_at > now()
   AND r.payload ->> 'action_type' = 'SUBMIT_COMMENT'
   AND (o.id IS NULL OR o.metrics_at IS NULL)
 ORDER BY r.created_at DESC
 LIMIT $2
"""


#: The own posts on X waiting for approval that END with the address of a post (a quote, v11 round
#: 3): the post they quote is read for context the same way a reply's target is.
_QUOTES_SQL = """
SELECT r.id AS revision_id, r.workspace_id,
       r.payload ->> 'plan_version' AS project_id,
       r.payload ->> 'text_and_media' AS text
  FROM action_proposal_revisions r
  JOIN action_proposals p ON p.id = r.proposal_id AND p.organization_id = r.organization_id
  JOIN social_accounts a ON a.id = p.social_account_id
  JOIN projects pr
    ON pr.organization_id = r.organization_id AND pr.id::text = r.payload ->> 'plan_version'
 WHERE r.organization_id = $1 AND r.state = 'READY_FOR_APPROVAL' AND r.expires_at > now()
   AND r.payload ->> 'action_type' IN ('SUBMIT_POST', 'PUBLISH_CONTENT')
   AND a.platform = 'X'
 ORDER BY r.created_at DESC
 LIMIT $2
"""


@dataclass(frozen=True)
class BackfillRow:
    revision_id: UUID
    target_external_id: str
    #: Where the post was read (`fxtwitter`, `x.read`, `reddit.read`), or `None` when nothing was.
    source: str | None
    gone: bool = False


async def backfill_waiting_drafts(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    readers: Readers,
    limit: int = 100,
    pause: float = 0.5,
) -> list[BackfillRow]:
    """Give the drafts that wait for approval NOW the post they answer. Sequential and paced
    (`pause` seconds between reads), because the sources are other people's servers."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_MISSING_SQL, organization_id, limit)
    store = OpportunityStore(pool)
    done: list[BackfillRow] = []
    for row in rows:
        target = str(row["target_external_id"] or "")
        platform = "X" if target.startswith("x:") else str(row["platform"]).upper()
        if platform == "REDDIT" and not target.startswith("t3_"):
            continue
        snapshot = await read_target(str(row["target_url"] or ""), platform, readers)
        if snapshot is not None:
            try:
                await record(
                    store,
                    organization_id=organization_id,
                    workspace_id=row["workspace_id"],
                    project_id=UUID(str(row["project_id"])),
                    session_id=None,
                    snapshot=snapshot,
                )
            except Exception:
                _LOG.exception("nie udało się zapisać kontekstu dla %s", target)
                snapshot = None
        done.append(
            BackfillRow(
                revision_id=row["revision_id"],
                target_external_id=target,
                source=snapshot.source if snapshot is not None else None,
                gone=bool(snapshot is not None and snapshot.gone),
            )
        )
        if pause:
            await asyncio.sleep(pause)
    async with pool.acquire() as conn:
        quotes = await conn.fetch(_QUOTES_SQL, organization_id, limit)
    for row in quotes:
        found = trailing_status(split_media(str(row["text"] or ""))[0])
        if found is None:
            continue
        url, _handle, status_id = found
        project = UUID(str(row["project_id"]))
        existing = await store.for_target(
            organization_id=organization_id, project_id=project, external_id=f"x:{status_id}"
        )
        if existing is not None and existing.metrics_at is not None:
            continue
        snapshot = await read_target(url, "X", readers)
        if snapshot is not None and not snapshot.gone:
            try:
                await store.note_context(
                    organization_id=organization_id,
                    workspace_id=row["workspace_id"],
                    project_id=project,
                    opportunity=new_opportunity(
                        platform="X",
                        url=snapshot.url,
                        external_id=snapshot.external_id,
                        reason=AUTO_REASON,
                        excerpt=snapshot.text or snapshot.url.removeprefix("https://"),
                        author=snapshot.handle,
                        created_at=snapshot.created_at,
                        score=snapshot.likes,
                        num_comments=snapshot.replies,
                        views=snapshot.views,
                        reposts=snapshot.reposts,
                        author_name=snapshot.name,
                        avatar_url=snapshot.avatar_url,
                        metrics_at=datetime.now(UTC),
                        require_time=False,
                        author_verified=snapshot.verified,
                        media=snapshot.media,
                    ),
                )
            except Exception:
                _LOG.exception("nie udało się zapisać cytowanego posta %s", url)
                snapshot = None
        done.append(
            BackfillRow(
                revision_id=row["revision_id"],
                target_external_id=f"x:{status_id}",
                source=snapshot.source if snapshot is not None else None,
            )
        )
        if pause:
            await asyncio.sleep(pause)
    return done
