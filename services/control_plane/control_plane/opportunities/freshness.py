"""A reply whose post went cold leaves the card, and the card says why (v11, 13.10).

THE FOUNDER (13.10), after a day of real use: drafts for posts that are a day old (X) or three days
old (Reddit) sat on page 1 and asked for his click, and a reply into a conversation that is over is
worse than no reply. So a waiting draft whose TARGET is older than the freshness limit (X 8 hours,
Reddit 48, `pilot/send_checks.MAX_AGE_*`, configurable, ONE definition shared with the publisher's
check right before a scheduled send) is STALE, and a stale draft:

  * DISAPPEARS FROM THE CARD AT ONCE, with no write: `room_view` leaves it out of `szkice` (and
    leaves out the opportunity that stands on it), because `room.show` is a read and says so to the
    host (`readOnlyHint`). Its reason is listed under "Skipped" the same moment, computed from the
    same rows (`virtual_skipped`);
  * IS EXPIRED FOR GOOD by the publisher's tick (`expire_stale_drafts`, called from
    `PublishHandlers.reconcile_only`): the revision moves READY_FOR_APPROVAL to EXPIRED through the
    one state machine (`ApprovalStore.move_revision`: the arrow is checked before any statement and
    one audit row is written), and the conversation is recorded as SKIPPED BY THE SYSTEM
    (`dismissed_by = "system:<code>"`, the reason in `reason`), which is what "Skipped (N)" lists.
    Until the tick runs, the read-time answer and the persisted one are the same.

WHAT "TARGET AGE" IS, from what the database already holds and never from a model:
  * X: the opportunity's `created_at_source` when the post was read, else the post's own timestamp,
    which is in its id (a snowflake, `send_checks.x_created_at`), so an X reply needs no read at
    all to be judged;
  * Reddit, a post (`t3_`): the opportunity's `created_at_source`, else the observed thread's;
  * Reddit, a COMMENT (`t1_`): not judged by the thread's age. A fresh comment under an old post is
    a live conversation, and the comment's own time is not in the tables; no age, no expiry.
A draft whose age cannot be established is NEVER called stale on a guess.

A POST THAT IS GONE is a different reason (`ZNIKNAL`) and needs a live read, which the publisher
makes anyway right before it sends (`pilot/publisher.py`: gone, locked, answered, too old).
Nothing here reads the platform. Own posts have no target thread and are never judged here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
from community_os_contracts.execution import ActionProposalState

from control_plane.approval.store import ApprovalStore, ApprovalStoreError
from control_plane.pilot.send_checks import REASON_GONE, REASON_STALE, max_age, x_created_at

from .store import (
    OpportunityRefused,
    OpportunityStore,
    new_opportunity,
    reddit_community,
)

_LOG = logging.getLogger(__name__)

#: Who dismisses a conversation when the system does: `system:<code>`. The card reads the code.
SYSTEM_PREFIX = "system:"


@dataclass(frozen=True)
class StaleDraft:
    """A waiting reply whose target went cold (older than the limit allows) or is gone."""

    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    revision_id: UUID
    platform: str
    target_external_id: str
    target_url: str
    age: timedelta
    limit: timedelta
    created_at: datetime | None
    #: `ZA_STARY` or `ZNIKNAL`. The card words it in its own language (`rs.<code>`).
    code: str
    community: str
    title: str
    excerpt: str
    author: str | None

    @property
    def reason(self) -> str:
        """The sentence, in Polish like every reason the server writes; the card has its own."""
        if self.code == REASON_GONE:
            return (
                "Posta, na który odpowiada ten szkic, już nie ma (odczyt przeglądarki agenta), "
                "więc odpowiedź wygasła. Tekst się nie zmienił; wygasły szkic nie wróci na kartę."
            )
        hours = int(self.age.total_seconds() // 3600)
        allowed = int(self.limit.total_seconds() // 3600)
        return (
            f"Odpowiedź wygasła, zanim ją wysłano: post ma {hours} h, a przy odpowiedzi "
            f"dopuszczamy najwyżej {allowed} h ({'X' if self.platform == 'X' else 'Reddit'}). "
            "Tekst się nie zmienił; wygasły szkic nie wróci na kartę."
        )

    def skipped_json(self) -> dict[str, Any]:
        """The row of "Skipped (N)" for this draft, in the shape of `room.skipped_json`."""
        return {
            "id": str(self.revision_id),
            "platforma": self.platform,
            "spolecznosc": self.community,
            "tytul": self.title or self.excerpt,
            "url": self.target_url,
            "pominieto": None,
            "przez": "system",
            "powod": self.reason,
            "powod_kod": self.code,
        }


def _stale(
    row: dict[str, Any] | asyncpg.Record,
    *,
    platform: str,
    target: str,
    code: str,
    created: datetime | None,
    age: timedelta = timedelta(0),
    limit: timedelta = timedelta(0),
) -> StaleDraft:
    community = str(row["opp_community"] or row["thread_community"] or "")
    return StaleDraft(
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        project_id=UUID(str(row["project_id"])),
        revision_id=row["revision_id"],
        platform=platform,
        target_external_id=target,
        target_url=str(row["target_url"] or ""),
        age=age,
        limit=limit,
        created_at=created,
        code=code,
        community=community or ("X" if platform == "X" else ""),
        title=str(row["opp_title"] or row["thread_title"] or ""),
        excerpt=str(row["opp_excerpt"] or ""),
        author=row["opp_author"] or row["thread_author"],
    )


def judge(
    row: dict[str, Any] | asyncpg.Record, *, now: datetime, environ: Any = None
) -> StaleDraft | None:
    """Is this waiting reply stale? Pure over one row of `_STALE_SQL`.

    `None` when it is not, or when nobody can tell. A post the account's own browser said is GONE
    (`gone_at` of its opportunity, `snapshot.from_x_page`) is stale whatever its age."""
    target = str(row["target_external_id"] or "")
    platform = "X" if target.startswith("x:") else str(row["platform"]).upper()
    if row["opp_gone"] is not None:
        return _stale(row, platform=platform, target=target, code=REASON_GONE, created=None)
    created: datetime | None
    if platform == "X":
        created = row["opp_created"] or x_created_at(None, target.removeprefix("x:"))
    elif target.startswith("t3_"):
        created = row["opp_created"] or row["thread_created"]
    else:
        return None  # a comment, or something else: no age of its own
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = max(timedelta(0), now - created)
    limit = max_age(platform == "X", environ)
    if age <= limit:
        return None
    return _stale(
        row,
        platform=platform,
        target=target,
        code=REASON_STALE,
        created=created,
        age=age,
        limit=limit,
    )


#: The waiting replies of the PROJECTS (a draft of a project names it in `plan_version`), with the
#: columns that say how old their target is. LEFT JOINs: every source of age is optional, and a
#: draft with none is simply not judged.
_STALE_SQL = """
SELECT r.id AS revision_id, r.organization_id, r.workspace_id,
       r.payload ->> 'plan_version' AS project_id,
       r.payload ->> 'target_external_id' AS target_external_id,
       r.payload ->> 'target_url' AS target_url,
       a.platform,
       o.created_at_source AS opp_created, o.community AS opp_community, o.title AS opp_title,
       o.excerpt AS opp_excerpt, o.author AS opp_author, o.gone_at AS opp_gone,
       t.created_at_source AS thread_created, t.title AS thread_title,
       t.author AS thread_author, t.community_key AS thread_community
  FROM action_proposal_revisions r
  JOIN action_proposals p
    ON p.id = r.proposal_id AND p.organization_id = r.organization_id
  JOIN social_accounts a ON a.id = p.social_account_id
  JOIN projects pr
    ON pr.organization_id = r.organization_id AND pr.id::text = r.payload ->> 'plan_version'
  LEFT JOIN project_opportunities o
    ON o.organization_id = r.organization_id AND o.project_id = pr.id
   AND o.external_id = r.payload ->> 'target_external_id'
  LEFT JOIN LATERAL (
        SELECT th.created_at_source, th.title, th.author, c.community_key
          FROM observed_threads th
          JOIN communities c
            ON c.id = th.community_id AND c.organization_id = th.organization_id
         WHERE th.organization_id = r.organization_id
           AND ('t3_' || th.external_id) = r.payload ->> 'target_external_id'
         LIMIT 1
  ) t ON true
 WHERE r.organization_id = $1 AND r.state = 'READY_FOR_APPROVAL' AND r.expires_at > $2
   AND r.payload ->> 'action_type' = 'SUBMIT_COMMENT'
   AND ($3::uuid IS NULL OR pr.id = $3)
"""


async def find_stale(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    project_id: UUID | None = None,
    now: datetime | None = None,
    environ: Any = None,
) -> list[StaleDraft]:
    """The waiting replies of the organization (or one project) whose target went cold. A read."""
    moment = now or datetime.now(UTC)
    async with pool.acquire() as conn:
        rows = await conn.fetch(_STALE_SQL, organization_id, moment, project_id)
    found = (judge(row, now=moment, environ=environ) for row in rows)
    return [one for one in found if one is not None]


async def expire_stale_drafts(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    now: datetime | None = None,
    project_id: UUID | None = None,
    environ: Any = None,
) -> list[StaleDraft]:
    """Expire the stale drafts for good and record each one under "Skipped". Returns what it did.

    One by one and independent: a draft that moved in the meantime (approved by a person a moment
    ago) is left alone (the machine refuses the arrow and the CAS), and a conversation that cannot
    be recorded is logged, because the revision is already expired and that is the part that
    matters. Idempotent: an expired draft is not READY any more.
    """
    stale = await find_stale(
        pool, organization_id=organization_id, project_id=project_id, now=now, environ=environ
    )
    approvals, opportunities = ApprovalStore(pool), OpportunityStore(pool)
    done: list[StaleDraft] = []
    for one in stale:
        try:
            await approvals.move_revision(
                one.revision_id,
                organization_id=one.organization_id,
                workspace_id=one.workspace_id,
                target=ActionProposalState.EXPIRED,
                actor_ref=f"{SYSTEM_PREFIX}{one.code}",
            )
        except (ApprovalStoreError, ValueError) as exc:
            _LOG.info("szkic %s nie wygasł: %s", one.revision_id, exc)
            continue
        done.append(one)
        try:
            await _record_skipped(opportunities, one)
        except Exception:
            _LOG.exception("nie udało się zapisać pominięcia dla szkicu %s", one.revision_id)
    return done


async def _record_skipped(store: OpportunityStore, one: StaleDraft) -> None:
    """The conversation, skipped by the system, with the reason: what "Skipped (N)" lists."""
    try:
        opportunity = new_opportunity(
            platform=one.platform,
            url=one.target_url,
            external_id=one.target_external_id,
            reason=one.reason,
            community=(one.community if one.platform != "X" else "")
            or (reddit_community("", one.target_url) if one.platform != "X" else ""),
            title=one.title or (f"Post {one.target_external_id}" if one.platform != "X" else ""),
            excerpt=one.excerpt or one.target_url.removeprefix("https://"),
            author=one.author,
            created_at=one.created_at,
            require_time=False,
        )
    except OpportunityRefused as exc:
        _LOG.info("pominięcie szkicu %s nie ma kształtu okazji: %s", one.revision_id, exc)
        return
    await store.mark(
        organization_id=one.organization_id,
        workspace_id=one.workspace_id,
        project_id=one.project_id,
        session_id=None,
        opportunity=opportunity,
        dismiss_by=f"{SYSTEM_PREFIX}{one.code}",
    )


class DraftFreshness:
    """What the publisher's tick holds to keep drafts fresh: expire the stale ones, and say whether
    anything waits at all (so the tick chain stays alive while a draft could still go cold)."""

    def __init__(self, pool: asyncpg.Pool, organization_id: UUID) -> None:
        self._pool = pool
        self._organization_id = organization_id

    async def expire(self, now: datetime) -> list[StaleDraft]:
        return await expire_stale_drafts(self._pool, organization_id=self._organization_id, now=now)

    async def waiting(self) -> bool:
        """A project's reply waits for a person's click and its window is open."""
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM action_proposal_revisions r "
                    "WHERE r.organization_id = $1 AND r.state = 'READY_FOR_APPROVAL' "
                    "AND r.expires_at > now() AND r.payload ->> 'action_type' = 'SUBMIT_COMMENT' "
                    "AND EXISTS (SELECT 1 FROM projects p WHERE p.organization_id = $1 "
                    "AND p.id::text = r.payload ->> 'plan_version'))",
                    self._organization_id,
                )
            )
