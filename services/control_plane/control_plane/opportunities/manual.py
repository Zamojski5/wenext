"""A reply the founder posted BY HAND, and said so (hosted MVP, 11.10).

In the hosted product nothing leaves Wenext: the card copies the reply, opens the thread, and the
founder posts from their own account. What the product can still know is what the founder TELLS it:
"I posted this one" (`draft.mark_posted`), optionally with the link to their post. This module
records that one fact and nothing else.

IT IS NOT A PUBLICATION. No request, decision, intent or attempt exists for it, the publisher never
sees it, and nothing was read back from the platform, so the room shows the result as UNVERIFIED
with no numbers (a number nobody measured is null, never zero). The two things it does change are
the ones the founder expects to change: the draft stops waiting for them, and the conversation it
answered is ANSWERED (`opportunities/store.py`).

THE LINK IS CHECKED because it is shown to the person later, on their own page: only https links
to Reddit and X are kept. Anything else is refused, never silently dropped, so the agent can say
what to fix.

THE TABLE IS `manual_publications` (one row per revision, unique on it). The revision itself is
immutable (migration 0003), so the fact lives next to it. Marking twice is not an error: the second
call returns the same row and fills in the link if the first one had none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import split_media

PLATFORM_REDDIT = "REDDIT"
PLATFORM_X = "X"

#: Where a link to the founder's own post may point.
_POST_HOSTS = frozenset(
    {
        "reddit.com",
        "www.reddit.com",
        "old.reddit.com",
        "np.reddit.com",
        "redd.it",
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }
)
URL_CHARS = 2000


class ManualPostRefused(Exception):
    """The mark cannot be recorded. The sentence is meant for the agent to relay."""


@dataclass(frozen=True)
class ManualPost:
    """What was recorded, and whether this call created it."""

    id: UUID
    revision_id: UUID
    platform: str
    target_external_id: str
    target_url: str
    url: str | None
    posted_at: datetime
    created: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "revision_id": str(self.revision_id),
            "platforma": self.platform,
            "cel": self.target_external_id,
            "adres_watku": self.target_url,
            "adres_posta": self.url,
            "zapisano": self.posted_at.isoformat(),
            "nowy_zapis": self.created,
        }


def checked_post_url(url: str | None) -> str | None:
    """The founder's own post link, or `None` when they gave none. `ManualPostRefused` otherwise."""
    text = (url or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in _POST_HOSTS:
        raise ManualPostRefused(
            "adres posta musi być linkiem https do Reddita albo X (np. https://www.reddit.com/..."
            " albo https://x.com/...); bez adresu też można, zostaw `url` puste"
        )
    if len(text) > URL_CHARS:
        raise ManualPostRefused(f"adres posta ma więcej niż {URL_CHARS} znaków")
    return text


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


async def record_manual_post(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    session_id: UUID | None,
    revision_id: UUID,
    url: str | None,
) -> ManualPost:
    """Record that the founder posted this revision's text themselves.

    The revision must belong to THIS project (its `plan_version` is the project id, the same scope
    the room reads). A revision of another project, and one that does not exist, are the same
    refusal: it does not confirm that the other one exists.
    """
    link = checked_post_url(url)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT r.id, r.payload FROM action_proposal_revisions r "
            "WHERE r.organization_id = $1 AND r.workspace_id = $2 AND r.id = $3 "
            "AND r.payload ->> 'plan_version' = $4::text",
            organization_id,
            workspace_id,
            revision_id,
            str(project_id),
        )
        if row is None:
            raise ManualPostRefused(
                f"nie ma szkicu {revision_id} w tym projekcie; revision_id bierz z room.show "
                "(`szkice`) albo z wyniku draft.create"
            )
        payload = _payload(row["payload"])
        target = str(payload.get("target_external_id") or "")
        text, _media = split_media(str(payload.get("text_and_media") or ""))
        saved = await conn.fetchrow(
            """
            INSERT INTO manual_publications
                (organization_id, workspace_id, project_id, revision_id, platform,
                 target_external_id, target_url, text, url, session_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (organization_id, revision_id) DO UPDATE
                SET url = coalesce(manual_publications.url, EXCLUDED.url)
            RETURNING id, platform, target_external_id, target_url, url, posted_at,
                      (xmax = 0) AS created
            """,
            organization_id,
            workspace_id,
            project_id,
            revision_id,
            PLATFORM_X if target.startswith("x:") else PLATFORM_REDDIT,
            target,
            str(payload.get("target_url") or ""),
            text,
            link,
            session_id,
        )
    return ManualPost(
        id=saved["id"],
        revision_id=revision_id,
        platform=saved["platform"],
        target_external_id=saved["target_external_id"],
        target_url=saved["target_url"],
        url=saved["url"],
        posted_at=saved["posted_at"],
        created=bool(saved["created"]),
    )
