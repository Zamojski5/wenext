"""`GET /api/approvals` — the approval queue, READ-ONLY (L4595, DoD L5218).

WHAT THIS FILE IS NOT. There is no Approve, no Edit & Approve, no Reject, no Snooze and no Ask AI
here. Those five are the approval queue as a PRODUCT and they are the scope of E1.F4
(L5296-5311). Adding one of them because the store already has the write method would be scope
creep, which L5500-5501 forbids by name.

FOUR THINGS THIS ROUTE DECIDES.

1.  **The sort is ASCENDING BY `expires_at`.** L4595: "kolejka approvali jest sortowana rosnąco
    po `expires_at`", so the operator is not handed cards that will expire regardless first. The
    ordering lives in ONE place — `approval.store._QUEUE_SQL` — and this handler does not re-sort
    what it receives; a second sort here would be a second answer, and the one that lost would be
    invisible.

    **Not by `created_at`.** That is the mistake this route is watched for: newest-first looks
    natural, matches every other list in this service, survives an eyeball review and breaks
    L4595 precisely for the queue where the two orders disagree.

    **Not by `priority_tier` either**, and that is a different kind of refusal. L4595 sorts
    "w obrębie `priority_tier`", but `priority_tier` is a field of `ThreadQualityAssessment`
    (L4464) written by the scoring engine of E1.F3. In F1 there is no column for it and no
    producer that could fill one, so sorting by it would mean inventing the value — the same move
    migration 0003 refused for `scope_level`. F1 implements the half it can honestly implement and
    says so here rather than silently.

2.  **The digest is rendered in FULL — all 64 characters.** "approval wskazuje dokładny payload
    hash" (L5218) is the DoD line this screen is the evidence for. A truncated digest is a screen
    on which two different payloads look identical, which is worse than showing none, because the
    reader stops checking.

3.  **The scope is the session's organization, never a query parameter.** Same rule and same
    words as `api/audit.py` and `api/jobs.py`: the query string is not an identity.

4.  **No state filter, by default or otherwise.** Every revision of the organization is listed and
    each carries its own `state`. A default filter is the most comfortable way to build a list
    nobody can trust: the page returns rows, looks alive, and a whole category is invisible with
    no error and no alarm.

WHY THIS ROUTE TALKS TO THE STORE AND NOT TO `domain.commands`. `api/jobs.py` states the rule the
other way round — "the route reaches the queue through `domain.commands`, never through the
store" — and it is the right rule: the domain layer is the checked entrance (L5206) and a second
entrance is the one that stops being checked. This route does NOT follow it, and the reason is
scope and not judgement: `domain/ports.py` and `domain/commands.py` belong to another package of
this wave and are not edited from here, so the approval port would have to be declared in a file
this package does not own. The consequence is written down instead of hidden: the approval path
has one entrance for now, and it is `approval.store.ApprovalStore`. Moving the read behind an
`ApprovalQueuePort` in `domain/` is a follow-up for whoever owns that file.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.approval.models import ApprovalQueueEntry
from control_plane.approval.store import ApprovalStore
from control_plane.auth import OperatorSession, current_operator, pool_for

router = APIRouter(prefix="/api", tags=["approvale"])

#: The full path, exported so tests do not spell it a second time. A test carrying its own copy of
#: a path keeps passing after the route is renamed.
APPROVALS_PATH = "/api/approvals"

#: How many cards one page carries by default, and the ceiling a caller may ask for. Reported back
#: together with `truncated`, so a page that FILLED UP says "there may be more" and never "this is
#: everything" — the shape `api/audit.py` and `api/jobs.py` use for the same reason.
#:
#: WHERE THAT SENTENCE STOPS BEING TRUE, and this correction is the point of the paragraph. In
#: `api/audit.py` and `api/jobs.py` the sentence covers the whole path, because
#: `apps/dashboard/components/views/audit-view.tsx` and `.../jobs-view.tsx` PRINT the flag. There
#: is no such screen here: `apps/dashboard/app/approvals/page.tsx` is a signpost that reads
#: nothing at all, and `apps/dashboard/lib/routes.ts` marks the route `reads: false`. So in F1
#: `truncated` is carried by the RESPONSE and consumed by NO reader — the guarantee ends at the
#: JSON, and an operator looking at the panel is not told anything about a page that ended.
#:
#: The response half IS measured, and only that half:
#: `tests/test_approvals_api.py::test_a_page_that_filled_up_says_there_may_be_more` asks for a
#: short page over a longer queue and for a page longer than the queue, and requires `True` and
#: `False` respectively. The screen half is not measured because it does not exist; turning the
#: signpost into a list view is E1.F4's job (L5296-5311) together with the five actions, and
#: `lib/routes.ts` already carries the trigger for it.
DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000

#: Length of a SHA-256 digest in hex. The response is checked against it before it is sent: a
#: truncation introduced anywhere between the column and the JSON is a refusal, not a shorter
#: string. Migration 0003 puts the same rule on the column as a CHECK, and this is the second of
#: the two independent locks on the same door — the door being L5218.
PAYLOAD_HASH_LENGTH = 64


async def approval_store(request: Request) -> ApprovalStore:
    """The store these routes read through.

    The pool comes from `auth.pool_for`, which is the one place a pool is created in this process.
    A second pool builder would mean the test suite points one of them at the throwaway PostgreSQL
    of the run and the other at whatever `DATABASE_URL` happens to say.
    """
    pool = await pool_for(request.app)
    return ApprovalStore(pool)


def _entry_payload(entry: ApprovalQueueEntry) -> dict[str, Any]:
    """One queue entry as JSON. Field names English (D-010); operator messages stay Polish."""
    if len(entry.payload_hash) != PAYLOAD_HASH_LENGTH:
        raise HTTPException(
            status_code=500,
            detail=(
                f"payload_hash rewizji {entry.revision_id} ma {len(entry.payload_hash)} znaków, "
                f"a nie {PAYLOAD_HASH_LENGTH}. Skrócony hash to ekran, na którym dwie różne "
                "treści wyglądają identycznie — a DoD L5218 mówi „approval wskazuje DOKŁADNY "
                "payload hash”. Lepiej odmówić niż pokazać prefiks"
            ),
        )
    return {
        "revision_id": str(entry.revision_id),
        "proposal_id": str(entry.proposal_id),
        "organization_id": str(entry.organization_id),
        "workspace_id": str(entry.workspace_id),
        "revision_no": entry.revision_no,
        "state": entry.state.value,
        # FULL digest. Never a prefix, never an ellipsis.
        "payload_hash": entry.payload_hash,
        "created_at": entry.created_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "source_observed_at": entry.source_observed_at.isoformat(),
    }


@router.get("/approvals")
async def list_approvals(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[ApprovalStore, Depends(approval_store)],
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Revisions of the session's organization, ascending by `expires_at` (L4595).

    Empty means empty — an empty list and a `count` of zero, never a placeholder row. D-013 is
    explicit: "Ekran pusty przy braku danych ma pokazywać stan pusty, nigdy atrapy." In F1 that is
    the normal case: no producer of proposals exists, so on a clean database this list is empty and
    honest.
    """
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise HTTPException(
            status_code=400, detail=f"limit={limit} poza zakresem 1..{MAX_PAGE_LIMIT}"
        )

    entries = await store.fetch_queue(organization_id=operator.organization_id, limit=limit)
    return {
        # The order is the store's. Not re-sorted here — see decision 1 in the module docstring.
        "approvals": [_entry_payload(entry) for entry in entries],
        "limit": limit,
        "count": len(entries),
        # "The page filled up", never "there is more": a queue holding exactly `limit` rows
        # reports `True` and that is correct — the flag means "may be more", and the honest
        # alternative (asking for `limit + 1` rows to find out) is a second read this route does
        # not need. Nothing in the panel consumes this; see `DEFAULT_PAGE_LIMIT`.
        "truncated": len(entries) == limit,
        # Named in the response so a reader of the JSON does not have to guess which key the list
        # is ordered by, and so a change of key is visible in the payload a test already reads.
        "sorted_by": "expires_at",
    }
