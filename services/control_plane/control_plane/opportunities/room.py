"""The data behind the "Pokój" page: opportunities, drafts waiting for the founder, results (D-055).

ONE READ, THREE LISTS, NO MODEL. The page answers three questions in this order: who is talking
right now about the problem this project solves (`okazje`), what is waiting for my click
(`szkice`), and what did the replies already sent achieve (`wyniki`). Everything is counted from
tables; there is no call to a model and not one number that cannot be pointed at in the database.

A NUMBER THAT WAS NOT MEASURED IS NULL, NEVER ZERO. A published reply nobody has read back has no
score and no reply count: its `glosy` and `odpowiedzi` are null, and the totals are null when
nothing was measured at all. `usuniete` is a count of verdicts, and a project whose publications
are all UNVERIFIED has no verdicts, so it is null too ("UNVERIFIED nie jest doświadczeniem",
KONWENCJE): "0 removed" would claim a check nobody made.

THE DRAFTS ARE EXACTLY WHAT THE APPROVE ROUTE WILL ACCEPT. A revision waiting for consent, not
expired, of an account still assigned to this project (the same conditions as `_revision_row` in
`api/project_approvals.py`), so the page never offers a button that answers 404 or 409 for a
reason it could have known. Whether the project has a live delegation, and whether the budget is
spent, are things only the click can learn; the page must show those refusals as they come.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import split_media, split_own_post
from control_plane.pilot.send_checks import x_created_at
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST

from .freshness import SYSTEM_PREFIX, find_stale
from .ranking import rank_score
from .snapshot import trailing_status
from .store import (
    CONTEXT_DISMISSER,
    OPEN_STATUSES,
    STATUS_ANSWERED,
    STATUS_DISMISSED,
    OpportunityStore,
    OpportunityView,
    room_path,
)

#: The window the page reports results over.
RESULTS_WINDOW_DAYS = 7
#: How many opportunities, drafts and result rows one read returns. The page is a room, not an
#: archive: more than this is older than anybody will act on.
MAX_OPPORTUNITIES = 50
MAX_DRAFTS = 50
MAX_RESULT_ROWS = 100
#: How many skipped conversations the card lists (newest decision first); the total is told apart.
MAX_SKIPPED = 20
#: HOW MANY PAGES THE CARD HAS, AT MOST (v11, 13.10: "1 of 8", not "3 of 47"). The best drafts first
#: (`ranking.py` times how well the account fits), then, when there are fewer than this, the best of
#: the opportunities nobody has drafted for. Everything behind them is NOT shown and is counted in
#: `ukryte`; nothing is deleted, and a draft that was behind comes up as soon as there is room.
MAX_PAGES = 8

#: The only state of a draft on this page. Every row is a revision that waits for a human click;
#: the field exists so the page does not have to know that.
DRAFT_WAITING = "czeka_na_zgode"

KIND_REPLY = "REPLY"
KIND_OWN_POST = "OWN_POST"


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _platform_of_target(target_external_id: str, fallback: str) -> str:
    return "X" if target_external_id.startswith("x:") else fallback


def opportunity_rank(one: OpportunityView, now: datetime) -> float:
    """The order of the card for one opportunity (`opportunities/ranking.py`): younger and
    faster-growing first. X posts without a read time are dated by their id."""
    created = one.created_at_source
    if created is None and one.external_id.startswith("x:"):
        created = x_created_at(None, one.external_id.removeprefix("x:"))
    return rank_score(one.platform, created, one.metrics, now)


def draft_json(
    row: asyncpg.Record | dict[str, Any],
    *,
    opportunity: OpportunityView | None,
    thread: asyncpg.Record | dict[str, Any] | None,
) -> dict[str, Any]:
    """One draft in the shape of `szkice`. Pure, so the shape is tested without a database.

    `opportunity` is the one this draft answers (same target), `thread` the observed thread when
    there is one; either supplies the title, author and community of the conversation. A reply to
    something we know nothing about keeps its address and says nothing else: no title is made up.

    `konto` is the login of the account the draft will go out from (the proposal's own account,
    which the query already joins), so a card outside the dashboard can name it without asking the
    assignments endpoint. `odcisk` is the digest of the revision (`payload_hash`): the card in the
    person's agent hands it back when the person presses "Wyślij", so a confirmation is bound to
    the text that was on the screen (`tools/card_send.py`). The dashboard reads neither.
    """
    payload = _payload(row["payload"])
    target = str(payload.get("target_external_id") or "")
    platform = _platform_of_target(target, str(row["platform"]))
    own = ACTION_OF_CAPABILITY.get(str(payload.get("action_type"))) == ACTION_OWN_POST
    shown, _media = split_media(str(payload.get("text_and_media") or ""))
    title = ""
    body = shown
    if own and platform != "X":
        title, body = split_own_post(shown)
        title = title.strip()
    replying_to: dict[str, Any] | None = None
    if own:
        community = "X" if platform == "X" else target
    else:
        community = "X" if platform == "X" else ""
        about = ""
        author = None
        if thread is not None:
            community = str(thread["community_key"])
            about = str(thread["title"])
            author = thread["author"]
        if opportunity is not None:
            # The opportunity is what the agent recorded about this very conversation, and it
            # carries the fields the page shows; the observed thread is the fallback.
            community = opportunity.community
            about = opportunity.display_title or about
            author = opportunity.author or author
        replying_to = {
            "url": str(payload.get("target_url") or ""),
            "tytul": about,
            "autor": author,
        }
        title = about
    return {
        "revision_id": str(row["revision_id"]),
        "okazja_id": str(opportunity.id) if opportunity is not None else None,
        "platforma": platform,
        "konto": row.get("account_handle"),
        "spolecznosc": community,
        "rodzaj": KIND_OWN_POST if own else KIND_REPLY,
        "tytul": title,
        "tekst": body,
        "w_odpowiedzi_na": replying_to,
        "stan": DRAFT_WAITING,
        "wygasa": _moment(row["expires_at"]),
        "odcisk": row.get("payload_hash"),
        # v11: the post this reply answers (author, avatar, text, age, counters, Trending), from the
        # conversation's opportunity; `None` for an own post and for a target nobody has read yet.
        "cel": opportunity.target_json() if opportunity is not None and not own else None,
        # v11: an own post on X that ENDS with the address of a post is a quote (X draws our words
        # and the post as a box). `cytat` is that post, read for context (`note_context`); the card
        # takes the address off the end of the text it shows and draws the box instead.
        "cytat": None,
        # The founder's own picture for the account, filled by the tool that serves the card.
        "konto_awatar": None,
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The summary numbers of `wyniki` from its rows. Pure.

    `opublikowane` is a count and may be zero. The other three are null unless something was
    measured: `glosy` and `odpowiedzi` sum only the rows that carry a measurement, and
    `usuniete` counts REMOVED verdicts only when at least one row has a verdict at all.
    """
    scores = [row["glosy"] for row in rows if row["glosy"] is not None]
    replies = [row["odpowiedzi"] for row in rows if row["odpowiedzi"] is not None]
    verdicts = [row["widocznosc"] for row in rows if row["widocznosc"] != "UNVERIFIED"]
    return {
        "okno_dni": RESULTS_WINDOW_DAYS,
        "opublikowane": len(rows),
        "odpowiedzi": sum(replies) if replies else None,
        "glosy": sum(scores) if scores else None,
        "usuniete": sum(1 for one in verdicts if one == "REMOVED") if verdicts else None,
        "pozycje": rows,
    }


def quote_json(
    url: str, handle: str, opportunity: OpportunityView | None, now: datetime
) -> dict[str, Any]:
    """The post a draft quotes, in the shape of `cel` plus its address. A quote whose post was never
    read still has its address and handle (from the address itself), and nothing else."""
    if opportunity is not None:
        shown = opportunity.target_json(now)
        return {**shown, "url": url, "autor": shown["autor"] or handle}
    return {
        "url": url,
        "autor": handle,
        "nazwa": None,
        "awatar": None,
        "tekst": None,
        "utworzono": None,
        "metryki": {
            "wyswietlenia": None,
            "polubienia": None,
            "odpowiedzi": None,
            "udostepnienia": None,
        },
        "pomiar": None,
        "trending": False,
        "zniknal": False,
        "zweryfikowany": None,
        "media": None,
    }


async def _drafts(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    exclude: frozenset[UUID] = frozenset(),
    now: datetime | None = None,
    fit: Mapping[str, float] | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """The waiting drafts as `(score, draft)`, best first. `exclude` are the stale ones
    (`freshness.py`): they are not on the page, and not counted against `MAX_DRAFTS`.

    The score is `ranking.py`'s (how fresh the post is and how fast it is growing) TIMES HOW WELL
    THE ACCOUNT FITS (`fit`, keyed `PLATFORM:login` in lower case, 1.0 when absent): a draft the
    founder cannot send right now (the account is outside the signed scope, or its session is lost)
    ranks behind one he can, so the eight on the card are the eight he can act on."""
    moment = now or datetime.now(UTC)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id AS revision_id, r.expires_at, r.payload, r.payload_hash, a.platform,
                   a.external_account_id AS account_handle
              FROM action_proposal_revisions r
              JOIN action_proposals p
                ON p.id = r.proposal_id AND p.organization_id = r.organization_id
              JOIN social_accounts a ON a.id = p.social_account_id
              JOIN project_account_assignments x
                ON x.organization_id = p.organization_id
               AND x.social_account_id = p.social_account_id
               AND x.unassigned_at IS NULL AND x.project_id = $3
             WHERE r.organization_id = $1 AND r.workspace_id = $2
               AND r.payload ->> 'plan_version' = $3::text
               AND r.state = 'READY_FOR_APPROVAL' AND r.expires_at > now()
               AND r.id <> ALL($5::uuid[])
               -- Posted by hand and said so (`draft.mark_posted`): no longer waiting for anyone.
               AND NOT EXISTS (
                   SELECT 1 FROM manual_publications m
                    WHERE m.organization_id = r.organization_id AND m.revision_id = r.id
               )
             ORDER BY r.created_at DESC
             LIMIT $4
            """,
            organization_id,
            workspace_id,
            project_id,
            MAX_DRAFTS,
            list(exclude),
        )
        targets = sorted(
            {str(_payload(r["payload"]).get("target_external_id") or "") for r in rows}
        )
        threads = await conn.fetch(
            """
            SELECT t.external_id, t.title, t.author, c.community_key
              FROM observed_threads t
              JOIN communities c
                ON c.id = t.community_id AND c.organization_id = t.organization_id
             WHERE t.organization_id = $1 AND t.workspace_id = $2
               AND ('t3_' || t.external_id) = ANY($3::text[])
            """,
            organization_id,
            workspace_id,
            targets,
        )
    thread_of = {f"t3_{t['external_id']}": t for t in threads}
    # The posts that own posts QUOTE are read from the same table as the replies' targets.
    quotes: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        shown_text, _media = split_media(str(_payload(row["payload"]).get("text_and_media") or ""))
        found = trailing_status(shown_text) if str(row["platform"]) == "X" else None
        own_post = (
            ACTION_OF_CAPABILITY.get(str(_payload(row["payload"]).get("action_type")))
            == ACTION_OWN_POST
        )
        if found is not None and own_post:
            quotes[str(row["revision_id"])] = found
    opportunities = await OpportunityStore(pool).by_external_ids(
        organization_id=organization_id,
        project_id=project_id,
        external_ids=[
            *(target for target in targets if target),
            *(f"x:{found[2]}" for found in quotes.values()),
        ],
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        target = str(_payload(row["payload"]).get("target_external_id"))
        opportunity = opportunities.get(target)
        thread = thread_of.get(target)
        shown = draft_json(row, opportunity=opportunity, thread=thread)
        quoted = quotes.get(str(row["revision_id"]))
        if quoted is not None:
            shown["cytat"] = quote_json(
                quoted[0], quoted[1], opportunities.get(f"x:{quoted[2]}"), moment
            )
        if opportunity is not None:
            score = opportunity_rank(opportunity, moment)
        elif shown["rodzaj"] == KIND_REPLY:
            created = None
            if target.startswith("x:"):
                created = x_created_at(None, target.removeprefix("x:"))
            score = rank_score(shown["platforma"], created, None, moment)
        else:
            score = 0.0  # an own post has no thread to be fresh: after the replies
        score *= (fit or {}).get(f"{shown['platforma']}:{shown['konto'] or ''}".lower(), 1.0)
        scored.append((score, shown))
    # Stable: among equals the newest draft stays first (the query's own order).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


async def _results(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    since: datetime,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT q.id, q.target_url, q.target_external_id, q.public_visibility, q.published_at,
                   e.permalink, r.payload, a.platform, obs.score, obs.reply_count,
                   obs.observed_at IS NOT NULL AS measured,
                   (SELECT o.id FROM project_opportunities o
                     WHERE o.organization_id = q.organization_id AND o.project_id = q.project_id
                       AND o.external_id = q.target_external_id
                     LIMIT 1) AS opportunity_id
              FROM publication_requests q
              JOIN action_proposal_revisions r
                ON r.id = q.proposal_revision_id AND r.organization_id = q.organization_id
              JOIN action_proposals p
                ON p.id = r.proposal_id AND p.organization_id = r.organization_id
              JOIN social_accounts a ON a.id = p.social_account_id
              LEFT JOIN execution_evidence e
                ON e.attempt_id = q.attempt_id AND e.organization_id = q.organization_id
              LEFT JOIN LATERAL (
                    SELECT o.score, o.reply_count, o.observed_at
                      FROM publication_observations o
                     WHERE o.organization_id = q.organization_id AND o.request_id = q.id
                     ORDER BY o.observed_at DESC
                     LIMIT 1
              ) obs ON true
             WHERE q.organization_id = $1 AND q.workspace_id = $2 AND q.project_id = $3
               AND q.status = 'PUBLISHED' AND q.published_at >= $4
             ORDER BY q.published_at DESC
             LIMIT $5
            """,
            organization_id,
            workspace_id,
            project_id,
            since,
            MAX_RESULT_ROWS,
        )
    async with pool.acquire() as conn:
        manual = await conn.fetch(
            """
            SELECT m.platform, m.target_external_id, m.target_url, m.url, m.text, m.posted_at,
                   (SELECT o.id FROM project_opportunities o
                     WHERE o.organization_id = m.organization_id AND o.project_id = m.project_id
                       AND o.external_id = m.target_external_id
                     LIMIT 1) AS opportunity_id
              FROM manual_publications m
             WHERE m.organization_id = $1 AND m.workspace_id = $2 AND m.project_id = $3
               AND m.posted_at >= $4
             ORDER BY m.posted_at DESC
             LIMIT $5
            """,
            organization_id,
            workspace_id,
            project_id,
            since,
            MAX_RESULT_ROWS,
        )
    items = []
    for row in rows:
        shown, _media = split_media(str(_payload(row["payload"]).get("text_and_media") or ""))
        measured = bool(row["measured"])
        items.append(
            {
                "url": row["permalink"] or row["target_url"],
                # The opportunity this reply answered, when it answered one: the join key a card
                # needs to put the result next to the person.
                "okazja_id": str(row["opportunity_id"]) if row["opportunity_id"] else None,
                "platforma": _platform_of_target(
                    str(row["target_external_id"]), str(row["platform"])
                ),
                "tekst": shown,
                "widocznosc": row["public_visibility"],
                # An observation exists or it does not: its reply count is a measurement only
                # when it does (the column itself defaults to 0, which says nothing).
                "glosy": row["score"] if measured else None,
                "odpowiedzi": row["reply_count"] if measured else None,
                "opublikowano": _moment(row["published_at"]),
            }
        )
    for row in manual:
        # A reply the founder posted by hand: nobody read it back, so the verdict is UNVERIFIED and
        # the numbers are null (never zero). The link is the one they pasted, else the thread.
        items.append(
            {
                "url": row["url"] or row["target_url"],
                "okazja_id": str(row["opportunity_id"]) if row["opportunity_id"] else None,
                "platforma": _platform_of_target(
                    str(row["target_external_id"]), str(row["platform"])
                ),
                "tekst": row["text"],
                "widocznosc": "UNVERIFIED",
                "glosy": None,
                "odpowiedzi": None,
                "opublikowano": _moment(row["posted_at"]),
            }
        )
    items.sort(key=lambda one: one["opublikowano"] or "", reverse=True)
    return totals(items[:MAX_RESULT_ROWS])


async def room_view(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    now: datetime | None = None,
    fit: Mapping[str, float] | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """The whole payload of `GET .../room`. The caller has already checked the project.

    The CARD asks for AT MOST `max_pages` PAGES (`MAX_PAGES`, v11): the best drafts first, ranked
    by freshness, how fast the post is growing and how well the account fits (`fit`); with room
    left, the best opportunities nobody drafted for; answered ones only in what is left after
    those. What is behind is not on the page and is counted: `ukryte` is how many drafts and new
    conversations were left out, and nothing is deleted. Without `max_pages` (the dashboard's
    Pokój, which lists everything) the payload is what it always was, with no `ukryte`."""
    moment = now or datetime.now(UTC)
    scanned = await OpportunityStore(pool).scan(
        organization_id=organization_id, project_id=project_id
    )
    # STALE DRAFTS ARE NOT ON THE PAGE (`freshness.py`): a reply whose post went cold, and the
    # opportunity that stands on that draft, disappear at once, with no write (this is a read);
    # the publisher's tick expires them for good, and "Skipped" lists the reason either way.
    stale = frozenset(
        one.revision_id
        for one in await find_stale(
            pool, organization_id=organization_id, project_id=project_id, now=moment
        )
    )
    # Skipped opportunities leave the page; answered ones stay (after the open ones) so the
    # founder sees what already got a reply, and the results block carries the numbers.
    visible = [
        one
        for one in scanned
        if one.stan in (*OPEN_STATUSES, STATUS_ANSWERED) and one.draft_revision_id not in stale
    ]
    # THE BEST OPPORTUNITY IS PAGE 1 (`ranking.py`): freshness, then how fast it is growing; the
    # card keeps this order within a rank. Answered ones stay last.
    visible.sort(key=lambda one: (one.stan == STATUS_ANSWERED, -opportunity_rank(one, moment)))
    ranked = await _drafts(
        pool,
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project_id,
        exclude=stale,
        now=moment,
        fit=fit,
    )
    extra: dict[str, Any] = {}
    if max_pages is None:
        drafts = [shown for _score, shown in ranked]
        opportunities = visible[:MAX_OPPORTUNITIES]
    else:
        kept, behind = ranked[:max_pages], ranked[max_pages:]
        drafts = [shown for _score, shown in kept]
        with_draft = {shown["okazja_id"] for shown in drafts if shown["okazja_id"]}
        behind_draft = {shown["okazja_id"] for _score, shown in behind if shown["okazja_id"]}
        # The rest of the page: opportunities that have no draft among the best, in the order above.
        others = [
            one
            for one in visible
            if str(one.id) not in with_draft and str(one.id) not in behind_draft
        ]
        room_left = max(0, max_pages - len(drafts))
        shown_ids = {str(one.id) for one in others[:room_left]} | with_draft
        behind_new = sum(1 for one in others[room_left:] if one.stan != STATUS_ANSWERED)
        opportunities = [one for one in visible if str(one.id) in shown_ids][:MAX_OPPORTUNITIES]
        extra["ukryte"] = len(behind) + behind_new
    return {
        "okazje": [one.to_json() for one in opportunities],
        "szkice": drafts,
        **extra,
        "wyniki": await _results(
            pool,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            since=moment - timedelta(days=RESULTS_WINDOW_DAYS),
        ),
        "pokoj": room_path(workspace_id, project_id),
    }


def skipped_json(one: OpportunityView) -> dict[str, Any]:
    """One skipped conversation as the card lists it (`pominiete` of `room.show`). Pure.

    `przez` says who decided: the agent (`session:` author) gives a reason, a person who pressed
    "pomin" on the page gave none, so `powod` is null and the card says so instead of guessing.
    """
    author = str(one.dismissed_by or "")
    by_agent = author.startswith("session:")
    by_system = author.startswith(SYSTEM_PREFIX)
    return {
        "id": str(one.id),
        "platforma": one.platform,
        "spolecznosc": one.community,
        "tytul": one.display_title,
        "url": one.url,
        "pominieto": _moment(one.dismissed_at),
        "przez": "system" if by_system else "agent" if by_agent else "czlowiek",
        "powod": one.reason if by_agent or by_system else None,
        # v11: the stable code of a skip the SYSTEM made (`ZA_STARY`), so the card words it itself.
        "powod_kod": author.removeprefix(SYSTEM_PREFIX) if by_system else None,
    }


async def skipped_view(
    pool: asyncpg.Pool, *, organization_id: UUID, project_id: UUID
) -> dict[str, Any]:
    """The conversations the agent (or the person) decided not to answer, for the card's
    "Skipped (N)". `pominiete_lacznie` counts every one of them in the scan, `pominiete` the
    newest."""
    scanned = await OpportunityStore(pool).scan(
        organization_id=organization_id, project_id=project_id
    )
    skipped = [
        one
        for one in scanned
        if one.stan == STATUS_DISMISSED and one.dismissed_by != CONTEXT_DISMISSER
    ]
    skipped.sort(key=lambda one: one.dismissed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    rows = [skipped_json(one) for one in skipped]
    # A stale draft is gone from the card the moment it is stale, and the publisher's tick has not
    # necessarily run yet: its reason is listed now, from the same rows (`freshness.py`), newest
    # first. Once the tick persists it, the persisted row stands in its place (the draft is no
    # longer waiting, so it is not listed twice).
    stale = await find_stale(pool, organization_id=organization_id, project_id=project_id)
    rows = [one.skipped_json() | {"pominieto": _moment(datetime.now(UTC))} for one in stale] + rows
    return {"pominiete": rows[:MAX_SKIPPED], "pominiete_lacznie": len(rows)}
