"""`GET /api/audit-events` — a read-only window on the append-only log (L5195).

Three decisions, each against a specific way an audit view goes quietly wrong:

1.  **No mutating route exists on this path.** `audit_events` refuses UPDATE, DELETE and
    TRUNCATE in the schema (0001 plus the statement-level trigger of 0002), so a route that
    tried would fail anyway. The reason not to have one is different: an API surface that offers
    PATCH on the audit log tells every reader that the log can be edited.

2.  **No default filter hides a kind of event.** Called with no parameters, the endpoint returns
    every kind. A default `kind` filter is the most comfortable way to build a log nobody can
    trust — the page returns rows, looks alive, and a whole category is invisible with no error
    and no alarm. That is the effect of having no audit at all, with none of the visibility.

3.  **The page limit is a page limit, and the response says which one applied.** Bounding a page
    by count, and reporting `limit`, `returned` and `truncated`, is not a hidden filter.
    Bounding it by time or by kind would be.

The scope is the ORGANIZATION OF THE SESSION, taken from `current_operator` — never from a query
parameter. A caller asking for another organization gets their own events, because the query
string is not an identity.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.audit import AUDIT_KINDS
from control_plane.auth import OperatorSession, current_operator, pool_for

router = APIRouter(prefix="/api", tags=["audyt"])

#: The full path, exported so tests do not spell it a second time (a test with its own copy of a
#: path keeps passing after the route is renamed).
AUDIT_EVENTS_PATH = "/api/audit-events"

#: How many events one page carries by default, and the ceiling a caller may ask for. The
#: default is generous on purpose: this log grows without a limit in F1 and a small default page
#: would make the newest events look like all the events.
DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000

#: One statement. The two filters are optional IN SQL — `$2` and `$3` being NULL means "no
#: filter", so there is no branch assembling different queries and no chance of one branch
#: growing a scope condition the other lacks.
_QUERY = """
    SELECT id, occurred_at, kind, subject_ref, actor_ref, organization_id, workspace_id,
           payload::text AS payload
    FROM audit_events
    WHERE organization_id = $1
      AND ($2::text[] IS NULL OR kind = ANY ($2::text[]))
      AND ($3::timestamptz IS NULL OR occurred_at >= $3)
    ORDER BY occurred_at DESC, id DESC
    LIMIT $4
"""


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    kind: Annotated[list[str] | None, Query()] = None,
    since: datetime | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Events of the session's organization, newest first, optionally narrowed by kind and time.

    `ORDER BY occurred_at DESC, id DESC` — the identifier is the tie-break, and it is not
    decoration: `occurred_at` defaults to `now()`, which in PostgreSQL is the TRANSACTION's start
    time, so a command writing two entries gives them the same timestamp and the order of the
    two would otherwise be whatever the planner picked.
    """
    unknown = sorted(set(kind or []) - AUDIT_KINDS)
    if unknown:
        # A refusal, not an empty page: a reader filtering by a value nothing can carry gets an
        # empty list and concludes that nothing happened.
        raise HTTPException(
            status_code=400,
            detail=(
                f"nieznane rodzaje zdarzeń: {', '.join(unknown)}. Znane: "
                f"{', '.join(sorted(AUDIT_KINDS))}"
            ),
        )
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"limit={limit} poza zakresem 1..{MAX_PAGE_LIMIT}",
        )

    pool = await pool_for(request.app)
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            _QUERY,
            operator.organization_id,
            list(kind) if kind else None,
            since,
            limit,
        )

    events = [
        {
            "id": row["id"],
            "occurred_at": row["occurred_at"].isoformat(),
            "kind": row["kind"],
            "subject_ref": row["subject_ref"],
            "actor_ref": row["actor_ref"],
            "organization_id": str(row["organization_id"]),
            "workspace_id": str(row["workspace_id"]) if row["workspace_id"] else None,
            # asyncpg hands back `jsonb` as text unless a codec is installed; parsing here keeps
            # the dashboard from having to know that.
            "payload": json.loads(row["payload"]) if row["payload"] else {},
        }
        for row in rows
    ]
    return {
        "events": events,
        "limit": limit,
        "returned": len(events),
        # Says "there may be more", never "this is everything" — a page that quietly ends is how
        # a reader concludes an event does not exist.
        "truncated": len(events) == limit,
    }
