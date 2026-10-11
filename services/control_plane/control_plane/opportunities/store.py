"""Opportunities: live conversations the agent judged worth the founder's attention (D-055).

WHAT AN OPPORTUNITY IS. One conversation on Reddit or X (adres, identifier, community, title,
excerpt, author, time, counters) plus two sentences the AGENT wrote: `dlaczego` (why it matters)
and `kat` (the angle a reply could take). It is a pointer with a reason, not a draft: a draft is
an ordinary proposal in the existing consent path, and this module never touches that path.

THE STATE IS DERIVED, NOT STORED (migration 0036). NOWA, SZKIC and ODPOWIEDZIANO follow from what
the database already knows: a live draft revision aimed at the same target, and a published
request aimed at the same target. A `status` column would be a second source of the same truth
that the publisher (which this module does not own) would never update. The one fact that cannot
be derived is a decision to skip, and that one is stored with its author.

A SECOND KIND OF "ANSWERED" (hosted, 11.10): the founder posted the reply BY HAND, from their own
account, and told the agent (`draft.mark_posted`, table `manual_publications`). Nothing was
published by Wenext and nothing was read back, so it is a fact the founder stated and not one the
product verified; the room shows such a result as unverified.

THE JOIN KEY IS THE TARGET OF A REPLY, spelled the way `publication_requests.target_external_id`
spells it: `t3_<id>` for a Reddit post, `x:<status id>` for an X post. A plain string equality
then connects an opportunity to its draft and to its publication, exactly as `target_for` and
`draft.create`'s own duplicate check connect them, and there is no second vocabulary to drift.

EVERYTHING IS SCOPED BY THE PROJECT. Every method takes the three ids from the caller (the tool
takes them from the session capability, the route from the operator's path after checking it) and
no query selects without `project_id`, so an opportunity of another project is not found, which
is a different answer from "forbidden" and the only one that does not confirm it exists.

LINKS ARE CHECKED BECAUSE THEY ARE SHOWN TO A PERSON. The agent reads text from strangers and
writes the address of the conversation; an address on the founder's page that points somewhere
other than the platform would be a phishing link placed by whoever wrote the post. Reddit hosts
and the exact X status shape are the only addresses accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

STATUS_NEW = "NOWA"
STATUS_DRAFT = "SZKIC"
STATUS_ANSWERED = "ODPOWIEDZIANO"
STATUS_DISMISSED = "POMINIETA"
STATUSES = (STATUS_NEW, STATUS_DRAFT, STATUS_ANSWERED, STATUS_DISMISSED)
#: What the agent and the page want by default: conversations still waiting for somebody.
OPEN_STATUSES = (STATUS_NEW, STATUS_DRAFT)

PLATFORM_REDDIT = "REDDIT"
PLATFORM_X = "X"
PLATFORMS = (PLATFORM_REDDIT, PLATFORM_X)

#: The community of an X post. X has no communities; the column is NOT NULL and the page shows it.
X_COMMUNITY = "X"

TITLE_CHARS = 300
EXCERPT_CHARS = 1500
REASON_CHARS = 600
AUTHOR_CHARS = 100
#: How many of a project's opportunities one read looks at. A project that marked more than this
#: has a list nobody reads; the newest are the ones that can still be answered.
SCAN_LIMIT = 500

_REDDIT_HOSTS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"})
_REDDIT_COMMENTS = re.compile(r"^/(?:r/[A-Za-z0-9_]+/)?comments/([a-z0-9]{1,12})(?:/|$)")
_REDDIT_SHORT = re.compile(r"^/([a-z0-9]{1,12})/?$")
_BARE_ID = re.compile(r"^[a-z0-9]{1,12}$")
_DIGITS = re.compile(r"^[0-9]{1,25}$")
_SUBREDDIT = re.compile(r"^(?:r/)?([A-Za-z0-9_]{2,21})$")


class OpportunityRefused(Exception):
    """The input cannot become an opportunity. The sentence is Polish and meant to be shown."""


def room_path(workspace_id: UUID, project_id: UUID) -> str:
    """Where the dashboard shows this project's room (`app/(studio)/w/.../p/.../room`)."""
    return f"/w/{workspace_id}/p/{project_id}/room"


def presence_path(workspace_id: UUID, project_id: UUID) -> str:
    """Where the dashboard shows the project's signed scope (`app/(studio)/w/.../p/.../presence`):
    to see it, pause it, resume it or revoke it."""
    return f"/w/{workspace_id}/p/{project_id}/presence"


def status_of(*, dismissed: bool, answered: bool, has_live_draft: bool) -> str:
    """The one rule that turns what the database knows into the state of an opportunity.

    The order is the whole rule. A decision to skip wins over everything, because it is the one
    thing a person said on purpose; a published reply wins over a draft, because a draft that
    already went out is history. A draft that is dead (expired, rejected, superseded without a
    successor) does not count, so the opportunity is NOWA again and can be drafted anew.
    """
    if dismissed:
        return STATUS_DISMISSED
    if answered:
        return STATUS_ANSWERED
    if has_live_draft:
        return STATUS_DRAFT
    return STATUS_NEW


def clip(text: str | None, limit: int) -> str:
    """`text` cut to `limit` characters, with a marker when something was dropped."""
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def reddit_post_id(*, external_id: str, url: str) -> str:
    """The bare post id (`abc123`) from an id given as `abc123` or `t3_abc123` and/or an address.

    When both are given they must agree: an id of one post with the address of another would put
    a link on the founder's page that does not lead to the conversation the reason is about.
    """
    from_id = None
    given = (external_id or "").strip().lower()
    if given:
        given = given.removeprefix("t3_")
        if not _BARE_ID.match(given):
            raise OpportunityRefused(
                f"identyfikator {external_id!r} nie jest identyfikatorem posta Reddita "
                "(np. abc123 albo t3_abc123)"
            )
        from_id = given
    from_url = None
    parts = urlsplit((url or "").strip())
    if parts.scheme == "https" and (parts.hostname or "").lower() in _REDDIT_HOSTS:
        match = _REDDIT_COMMENTS.match(parts.path)
        if match:
            from_url = match.group(1)
    elif parts.scheme == "https" and (parts.hostname or "").lower() == "redd.it":
        match = _REDDIT_SHORT.match(parts.path)
        if match:
            from_url = match.group(1)
    if from_id is None and from_url is None:
        raise OpportunityRefused(
            "podaj adres posta Reddita (https://www.reddit.com/r/<społeczność>/comments/<id>/...) "
            "albo jego identyfikator"
        )
    if from_id is not None and from_url is not None and from_id != from_url:
        raise OpportunityRefused(
            f"adres wskazuje post {from_url}, a identyfikator to {from_id}; podaj jeden post"
        )
    return from_id or from_url or ""


def x_status_id(*, external_id: str, url: str) -> str:
    """The status id (digits) of an X post, from `x:<id>`, `<id>` and/or the exact address."""
    from community_observer.x_browser import parse_status_url

    from_id = None
    given = (external_id or "").strip().removeprefix("x:")
    if given:
        if not _DIGITS.match(given):
            raise OpportunityRefused(
                f"identyfikator {external_id!r} nie jest identyfikatorem posta X (same cyfry)"
            )
        from_id = given
    from_url = None
    if (url or "").strip():
        try:
            _, from_url = parse_status_url(url)
        except ValueError as exc:
            raise OpportunityRefused(str(exc)) from exc
    if from_id is None and from_url is None:
        raise OpportunityRefused("podaj adres posta X (https://x.com/<konto>/status/<id>)")
    if from_id is not None and from_url is not None and from_id != from_url:
        raise OpportunityRefused(
            f"adres wskazuje post {from_url}, a identyfikator to {from_id}; podaj jeden post"
        )
    return from_id or from_url or ""


def reddit_community(community: str, url: str) -> str:
    """`r/<name>` from the argument or, failing that, from the address of the post."""
    given = (community or "").strip()
    if given:
        match = _SUBREDDIT.match(given)
        if match is None:
            raise OpportunityRefused(
                f"społeczność {community!r} nie jest nazwą subreddita (np. r/SaaS)"
            )
        return f"r/{match.group(1)}"
    match = re.search(r"/r/([A-Za-z0-9_]{2,21})/", urlsplit(url or "").path + "/")
    if match is None:
        raise OpportunityRefused("podaj społeczność (np. r/SaaS); z adresu nie da się jej odczytać")
    return f"r/{match.group(1)}"


@dataclass(frozen=True)
class NewOpportunity:
    """What the tool hands over, already normalised. Nothing here is trusted yet."""

    platform: str
    #: `t3_<id>` or `x:<id>`: the target of a reply, see the module docstring.
    external_id: str
    url: str
    community: str
    reason: str
    title: str = ""
    excerpt: str = ""
    author: str | None = None
    created_at: datetime | None = None
    score: int | None = None
    num_comments: int | None = None
    angle: str = ""


def new_opportunity(
    *,
    platform: str,
    url: str,
    reason: str,
    external_id: str = "",
    community: str = "",
    title: str = "",
    excerpt: str = "",
    author: str | None = None,
    created_at: datetime | None = None,
    score: int | None = None,
    num_comments: int | None = None,
    angle: str = "",
    require_time: bool = True,
) -> NewOpportunity:
    """Validate and normalise. Raises `OpportunityRefused` with a sentence for the agent.

    `require_time=False` lets a Reddit post in WITHOUT its publication time. The hosted product
    reads Reddit through a search engine (`observe/reddit_serp.py`), which often gives no
    timestamp, and a time nobody read is stored as NULL, never made up: the column allows it and
    the page already puts such a conversation last.
    """
    platform = (platform or "").strip().upper()
    if platform not in PLATFORMS:
        raise OpportunityRefused(f"platforma {platform!r}: dozwolone są REDDIT i X")
    address = (url or "").strip()
    why = (reason or "").strip()
    if not why:
        raise OpportunityRefused(
            "okazja bez uzasadnienia to link, nie wybór: napisz w `dlaczego`, czemu akurat ta "
            "rozmowa jest warta uwagi założyciela"
        )
    if platform == PLATFORM_REDDIT:
        parts = urlsplit(address)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in _REDDIT_HOSTS | {
            "redd.it"
        }:
            raise OpportunityRefused(
                "adres posta Reddita musi być https://www.reddit.com/... albo https://redd.it/..."
            )
        post = reddit_post_id(external_id=external_id, url=address)
        key = f"t3_{post}"
        place = reddit_community(community, address)
        if created_at is None and require_time:
            raise OpportunityRefused(
                "post Reddita wymaga `created_at` z wyniku reddit.search: bez czasu publikacji "
                "nie da się go ani uszeregować, ani przygotować do publikacji"
            )
        if not clip(title, TITLE_CHARS):
            raise OpportunityRefused("post Reddita wymaga `title` z wyniku reddit.search")
    else:
        status = x_status_id(external_id=external_id, url=address)
        key = f"x:{status}"
        place = X_COMMUNITY
        if not address:
            raise OpportunityRefused("podaj adres posta X (https://x.com/<konto>/status/<id>)")
        if not clip(excerpt, EXCERPT_CHARS):
            # X has no titles: the text of the post is all the founder will see of it.
            raise OpportunityRefused("post X wymaga `excerpt`, czyli treści posta z x.search")
    if created_at is not None and created_at.tzinfo is None:
        raise OpportunityRefused("created_at musi mieć strefę czasową (ISO 8601, np. ...Z)")
    for name, value in (("score", score), ("num_comments", num_comments)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise OpportunityRefused(f"{name} to liczba całkowita albo brak wartości")
    if num_comments is not None and num_comments < 0:
        raise OpportunityRefused("num_comments nie może być ujemne")
    return NewOpportunity(
        platform=platform,
        external_id=key,
        url=address,
        community=place,
        reason=clip(why, REASON_CHARS),
        title=clip(title, TITLE_CHARS),
        excerpt=clip(excerpt, EXCERPT_CHARS),
        author=clip(author, AUTHOR_CHARS) or None,
        created_at=created_at.astimezone(UTC) if created_at is not None else None,
        score=score,
        num_comments=num_comments,
        angle=clip(angle, REASON_CHARS),
    )


@dataclass(frozen=True)
class OpportunityView:
    """One opportunity as the tool and the page show it, with its derived state."""

    id: UUID
    platform: str
    external_id: str
    url: str
    community: str
    title: str
    excerpt: str
    author: str | None
    created_at_source: datetime | None
    score: int | None
    num_comments: int | None
    reason: str
    angle: str
    marked_at: datetime
    stan: str
    #: The live draft revision aimed at this conversation, or `None`.
    draft_revision_id: UUID | None
    dismissed_by: str | None = None

    @property
    def display_title(self) -> str:
        """The title, or for X (which has none) the beginning of the post. Never invented."""
        return self.title or clip(self.excerpt, 90)

    def to_json(self) -> dict[str, Any]:
        """The shape of `okazje` in the room payload, and of `opportunities.list`."""
        return {
            "id": str(self.id),
            "platforma": self.platform,
            "spolecznosc": self.community,
            "tytul": self.display_title,
            "fragment": clip(self.excerpt, 500),
            "autor": self.author,
            "url": self.url,
            "utworzono": self.created_at_source.isoformat() if self.created_at_source else None,
            "wynik": self.score,
            "komentarze": self.num_comments,
            "dlaczego": self.reason,
            "kat": self.angle,
            "stan": self.stan,
            "szkic": str(self.draft_revision_id) if self.draft_revision_id else None,
        }


#: The columns of one opportunity plus the two facts its state is derived from. The draft lookup
#: is a LATERAL on the same target string the publisher uses; "live" is decided here, once: a
#: draft waiting for consent that has not expired, or one already approved (it stands in the
#: publisher's queue and its window no longer matters).
_VIEW_SQL = """
SELECT o.id, o.platform, o.external_id, o.url, o.community, o.title, o.excerpt, o.author,
       o.created_at_source, o.score, o.num_comments, o.reason, o.angle, o.created_at,
       o.dismissed_at, o.dismissed_by,
       d.revision_id AS draft_revision_id,
       (EXISTS (
           SELECT 1 FROM publication_requests q
            WHERE q.organization_id = o.organization_id AND q.workspace_id = o.workspace_id
              AND q.project_id = o.project_id AND q.kind = 'REPLY'
              AND q.target_external_id = o.external_id AND q.status = 'PUBLISHED'
       ) OR EXISTS (
           -- A reply the founder posted BY HAND and said so (`draft.mark_posted`, hosted).
           SELECT 1 FROM manual_publications m
            WHERE m.organization_id = o.organization_id AND m.project_id = o.project_id
              AND m.target_external_id = o.external_id
       )) AS answered
  FROM project_opportunities o
  LEFT JOIN LATERAL (
        SELECT r.id AS revision_id
          FROM action_proposal_revisions r
         WHERE r.organization_id = o.organization_id AND r.workspace_id = o.workspace_id
           AND r.payload ->> 'plan_version' = o.project_id::text
           AND r.payload ->> 'target_external_id' = o.external_id
           AND (
                r.state = 'APPROVED'
                OR (r.state IN ('DRAFT', 'READY_FOR_APPROVAL') AND r.expires_at > now())
           )
         ORDER BY r.created_at DESC
         LIMIT 1
  ) d ON true
 WHERE o.organization_id = $1 AND o.project_id = $2
"""


def _view(row: asyncpg.Record) -> OpportunityView:
    return OpportunityView(
        id=row["id"],
        platform=row["platform"],
        external_id=row["external_id"],
        url=row["url"],
        community=row["community"],
        title=row["title"],
        excerpt=row["excerpt"],
        author=row["author"],
        created_at_source=row["created_at_source"],
        score=row["score"],
        num_comments=row["num_comments"],
        reason=row["reason"],
        angle=row["angle"],
        marked_at=row["created_at"],
        stan=status_of(
            dismissed=row["dismissed_at"] is not None,
            answered=bool(row["answered"]),
            has_live_draft=row["draft_revision_id"] is not None,
        ),
        draft_revision_id=row["draft_revision_id"],
        dismissed_by=row["dismissed_by"],
    )


@dataclass(frozen=True)
class Marked:
    view: OpportunityView
    #: True when this call created the opportunity, False when it found one and refreshed it.
    created: bool


class OpportunityStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def mark(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        session_id: UUID | None,
        opportunity: NewOpportunity,
        dismiss_by: str | None = None,
    ) -> Marked:
        """Create the opportunity, or refresh the one that already stands for this conversation.

        IDEMPOTENT ON (project, platform, external id). A second mark of the same conversation
        updates what the agent now knows (counters, excerpt, its two sentences) and NEVER the
        decision to skip: an agent that finds the same post again must not bring back what the
        founder already waved away. `dismiss_by` is the one way to skip, and it keeps the
        earlier author when the opportunity was skipped already.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO project_opportunities
                    (organization_id, workspace_id, project_id, platform, external_id, url,
                     community, title, excerpt, author, created_at_source, score, num_comments,
                     reason, angle, marked_by_session)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                ON CONFLICT ON CONSTRAINT project_opportunities_one_per_conversation DO UPDATE
                    SET url = EXCLUDED.url,
                        community = EXCLUDED.community,
                        title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title
                                     ELSE project_opportunities.title END,
                        excerpt = CASE WHEN EXCLUDED.excerpt <> '' THEN EXCLUDED.excerpt
                                       ELSE project_opportunities.excerpt END,
                        author = coalesce(EXCLUDED.author, project_opportunities.author),
                        created_at_source = coalesce(EXCLUDED.created_at_source,
                                                     project_opportunities.created_at_source),
                        score = coalesce(EXCLUDED.score, project_opportunities.score),
                        num_comments = coalesce(EXCLUDED.num_comments,
                                                project_opportunities.num_comments),
                        reason = EXCLUDED.reason,
                        angle = CASE WHEN EXCLUDED.angle <> '' THEN EXCLUDED.angle
                                     ELSE project_opportunities.angle END,
                        updated_at = now()
                RETURNING id, (xmax = 0) AS created
                """,
                organization_id,
                workspace_id,
                project_id,
                opportunity.platform,
                opportunity.external_id,
                opportunity.url,
                opportunity.community,
                opportunity.title,
                opportunity.excerpt,
                opportunity.author,
                opportunity.created_at,
                opportunity.score,
                opportunity.num_comments,
                opportunity.reason,
                opportunity.angle,
                session_id,
            )
            if dismiss_by is not None:
                await conn.execute(
                    "UPDATE project_opportunities SET dismissed_at = now(), dismissed_by = $3 "
                    "WHERE organization_id = $1 AND id = $2 AND dismissed_at IS NULL",
                    organization_id,
                    row["id"],
                    dismiss_by,
                )
            view = await self._one(conn, organization_id, project_id, row["id"])
        assert view is not None
        return Marked(view=view, created=bool(row["created"]))

    async def _one(
        self,
        conn: asyncpg.Connection,
        organization_id: UUID,
        project_id: UUID,
        opportunity_id: UUID,
    ) -> OpportunityView | None:
        row = await conn.fetchrow(
            _VIEW_SQL + " AND o.id = $3", organization_id, project_id, opportunity_id
        )
        return _view(row) if row is not None else None

    async def get(
        self, *, organization_id: UUID, project_id: UUID, opportunity_id: UUID
    ) -> OpportunityView | None:
        async with self._pool.acquire() as conn:
            return await self._one(conn, organization_id, project_id, opportunity_id)

    async def for_target(
        self, *, organization_id: UUID, project_id: UUID, external_id: str
    ) -> OpportunityView | None:
        """The opportunity whose conversation is this reply target, or `None`."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _VIEW_SQL + " AND o.external_id = $3", organization_id, project_id, external_id
            )
        return _view(row) if row is not None else None

    async def scan(
        self, *, organization_id: UUID, project_id: UUID, platform: str | None = None
    ) -> list[OpportunityView]:
        """The project's newest opportunities (up to `SCAN_LIMIT`), freshest conversation first.

        Conversations of unknown time go last: a post whose time was never read cannot be called
        fresh. The state is derived per row, so filtering by state happens in the caller, on the
        same `stan` the caller shows.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _VIEW_SQL + " AND ($3::text IS NULL OR o.platform = $3)"
                " ORDER BY o.created_at_source DESC NULLS LAST, o.created_at DESC LIMIT $4",
                organization_id,
                project_id,
                platform,
                SCAN_LIMIT,
            )
        return [_view(row) for row in rows]

    async def by_external_ids(
        self, *, organization_id: UUID, project_id: UUID, external_ids: list[str]
    ) -> dict[str, OpportunityView]:
        """Which of these conversations are already opportunities of this project."""
        if not external_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _VIEW_SQL + " AND o.external_id = ANY($3::text[])",
                organization_id,
                project_id,
                external_ids,
            )
        return {row["external_id"]: _view(row) for row in rows}

    async def dismiss(
        self,
        *,
        organization_id: UUID,
        project_id: UUID,
        opportunity_id: UUID,
        by: str,
    ) -> tuple[OpportunityView, bool] | None:
        """Skip an opportunity. `None` when it is not this project's; `(view, changed)` otherwise.

        Repeating it is not an error and does not move the decision: the first author and time
        stay, `changed` is False. A project's opportunity that does not exist and one that
        belongs to another project are the same `None`.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            changed = await conn.fetchval(
                "UPDATE project_opportunities SET dismissed_at = now(), dismissed_by = $4, "
                "updated_at = now() WHERE organization_id = $1 AND project_id = $2 AND id = $3 "
                "AND dismissed_at IS NULL RETURNING true",
                organization_id,
                project_id,
                opportunity_id,
                by,
            )
            view = await self._one(conn, organization_id, project_id, opportunity_id)
        if view is None:
            return None
        return view, bool(changed)
