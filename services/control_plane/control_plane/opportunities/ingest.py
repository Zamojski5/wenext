"""Make a live Reddit conversation publishable: put its thread where the publisher looks (D-055).

WHY THIS EXISTS. `draft.create` aims a REPLY at `t3_<id>` and a URL and nothing else, so a draft
under a conversation found by `reddit.search` is created without complaint. The refusal comes
later, at publication: `PublicationStore.target_for` finds the thread in `observed_threads` and
its community in `communities`, and a request whose thread is not there is SKIPPED ("brak
wątku"). A reply the founder approved would then stand in the queue and never go out. So before
the draft is written, the live conversation is recorded the way an observed one is.

WHAT THIS DOES NOT DO, and each item is a rule it keeps:

  * It does NOT turn on observation. `ObservationStore.upsert_community` sets `observe_enabled`
    to true on conflict, which would start polling a subreddit nobody chose to watch and re-enable
    one the founder left. A community that does not exist yet is created QUIET (observation off,
    thread analysis off: no polling, no judgment per thread, no model call), and one that exists
    is used as it is, flags untouched.
  * It does NOT overwrite a thread that was already read. Observation stores the whole post; an
    opportunity carries only an excerpt, and replacing the first with the second would degrade
    what the collector knows.
  * It does NOT touch consent. No proposal, decision, delegation, budget or intent is created or
    changed here. `own_posts_allowed` stays NULL on a new community, so own posts there remain
    refused until a person decides, exactly as for any community nobody decided about.
  * It does NOT handle X. The X publisher aims at `x:<id>` and never calls `target_for`, and the
    `communities` table does not list X at all.

THE EVIDENCE IS HONEST ABOUT WHAT IT IS. `raw_sha256` is the digest of the fields this module
stored (the live hit), not of a platform payload it never saw, and the snapshot carries the score
the search showed or the explicit reason it is unknown.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from community_observer.models import ObservedThread, ThreadMetrics
from community_os_contracts import AccessMode, ObservationPlatform, Observed, UnknownReason

from control_plane.observe.store import ObservationStore

from .store import OpportunityView

#: How a Reddit community's access is described elsewhere (`reddit.py`, `reddit_browser.py`):
#: the read runs as somebody's account.
_ACCESS_MODE = AccessMode.ACCOUNT_REQUIRED


async def ensure_reddit_thread(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    opportunity: OpportunityView,
) -> UUID:
    """The id of the thread in `observed_threads`, recording it first when it is not there.

    Raises `ValueError` for an opportunity that is not a Reddit post. Database errors propagate:
    the caller refuses the draft, because a draft that cannot be published is a promise broken
    later and louder.
    """
    if opportunity.platform != "REDDIT" or not opportunity.external_id.startswith("t3_"):
        raise ValueError("tylko post Reddita wymaga wpisu w obserwowanych wątkach")
    bare = opportunity.external_id.removeprefix("t3_")
    store = ObservationStore(pool)
    community = await store.community_by_key(
        organization_id=organization_id,
        workspace_id=workspace_id,
        platform=ObservationPlatform.REDDIT.value,
        community_key=opportunity.community,
    )
    if community is None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO communities
                    (organization_id, workspace_id, platform, community_key, display_name,
                     access_mode, observe_enabled, poll_interval_seconds,
                     thread_analysis_enabled)
                VALUES ($1, $2, $3, $4, $5, $6, false, 600, false)
                ON CONFLICT (organization_id, workspace_id, platform, community_key) DO NOTHING
                """,
                organization_id,
                workspace_id,
                ObservationPlatform.REDDIT.value,
                opportunity.community,
                f"Reddit · {opportunity.community}",
                _ACCESS_MODE.value,
            )
        community = await store.community_by_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            platform=ObservationPlatform.REDDIT.value,
            community_key=opportunity.community,
        )
    if community is None:  # the insert and the read disagree: refuse rather than guess
        raise LookupError(f"nie udało się zapisać społeczności {opportunity.community}")

    async with pool.acquire() as conn:
        known = await conn.fetchval(
            "SELECT id FROM observed_threads WHERE organization_id = $1 AND workspace_id = $2 "
            "AND community_id = $3 AND external_id = $4",
            organization_id,
            workspace_id,
            community.id,
            bare,
        )
    if known is not None:
        return known

    if opportunity.created_at_source is None:
        raise ValueError("post bez czasu publikacji nie może wejść do obserwowanych wątków")
    seen = {
        "platform": "REDDIT",
        "community": opportunity.community,
        "external_id": bare,
        "url": opportunity.url,
        "title": opportunity.title,
        "excerpt": opportunity.excerpt,
        "author": opportunity.author,
        "created_at": opportunity.created_at_source.isoformat(),
        "source": "reddit.search",
    }
    digest = hashlib.sha256(json.dumps(seen, sort_keys=True).encode("utf-8")).hexdigest()
    thread = ObservedThread(
        platform=ObservationPlatform.REDDIT,
        community_key=opportunity.community,
        external_id=bare,
        url=opportunity.url,
        title=opportunity.title or opportunity.display_title,
        body=opportunity.excerpt or None,
        author=opportunity.author,
        created_at=opportunity.created_at_source,
        observed_at=datetime.now(UTC),
        metrics=ThreadMetrics(
            score=(
                Observed[int].known(opportunity.score)
                if opportunity.score is not None
                else Observed[int].unknown(UnknownReason.ADAPTER_COULD_NOT_PARSE)
            ),
            comment_count=(
                Observed[int].known(opportunity.num_comments)
                if opportunity.num_comments is not None
                else Observed[int].unknown(UnknownReason.ADAPTER_COULD_NOT_PARSE)
            ),
        ),
        raw_sha256=digest,
    )
    thread_id, _ = await store.record_thread(community=community, thread=thread)
    return thread_id
