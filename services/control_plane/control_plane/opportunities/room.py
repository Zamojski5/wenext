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
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from control_plane.pilot.publication import split_media, split_own_post
from control_plane.presence.approval import ACTION_OF_CAPABILITY, ACTION_OWN_POST

from .store import (
    OPEN_STATUSES,
    STATUS_ANSWERED,
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


async def _drafts(
    pool: asyncpg.Pool, *, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> list[dict[str, Any]]:
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
    opportunities = await OpportunityStore(pool).by_external_ids(
        organization_id=organization_id,
        project_id=project_id,
        external_ids=[target for target in targets if target],
    )
    return [
        draft_json(
            row,
            opportunity=opportunities.get(str(_payload(row["payload"]).get("target_external_id"))),
            thread=thread_of.get(str(_payload(row["payload"]).get("target_external_id"))),
        )
        for row in rows
    ]


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
) -> dict[str, Any]:
    """The whole payload of `GET .../room`. The caller has already checked the project."""
    moment = now or datetime.now(UTC)
    scanned = await OpportunityStore(pool).scan(
        organization_id=organization_id, project_id=project_id
    )
    # Skipped opportunities leave the page; answered ones stay (after the open ones) so the
    # founder sees what already got a reply, and the results block carries the numbers.
    visible = [one for one in scanned if one.stan in (*OPEN_STATUSES, STATUS_ANSWERED)]
    visible.sort(key=lambda one: one.stan == STATUS_ANSWERED)  # stable: freshness is kept
    return {
        "okazje": [one.to_json() for one in visible[:MAX_OPPORTUNITIES]],
        "szkice": await _drafts(
            pool,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
        "wyniki": await _results(
            pool,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            since=moment - timedelta(days=RESULTS_WINDOW_DAYS),
        ),
        "pokoj": room_path(workspace_id, project_id),
    }
