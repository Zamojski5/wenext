"""HTTP for "what did the agent do while I was away" (P4).

TWO QUESTIONS, TWO ROUTES, and they are not the same question:

  * THE STATE: is this project working right now, what governs it, what has it spent, and when
    did it last look around. This is what somebody opens the laptop to see first.
  * THE DECISIONS WAITING: what the agent produced that a person still has to judge. In REVIEW
    mode this is the whole workflow; in AUTONOMOUS it holds what the delegation REFUSED, which
    is the more important half, because a refusal nobody sees is a refusal that teaches nothing.

WHY THE WAKE-UPS COME BACK AS A LIST AND NOT AS A COUNT. "The agent ran eleven times today" is
not an answer a person can act on; "woken by a new thread at 6:12, replied; woken at 6:22,
nothing to do" is. The quiet rows are the majority and they are included on purpose: a day with
no work and a day with a broken collector look identical without them.

NOTHING HERE STARTS WORK. The tick is a job. These routes read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.api.delegations import delegation_payload
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.observe.store import ObservationStore
from control_plane.presence.budget import effective_limits, spent
from control_plane.presence.store import PresenceStore, WakeupRow
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["presence"])

PRESENCE_PATH = "/workspaces/{workspace_id}/projects/{project_id}/presence"
DECISIONS_PATH = "/workspaces/{workspace_id}/projects/{project_id}/decisions"
#: Odwołanie ZAPLANOWANEGO powrotu (0023). Osobna trasa, nie pole w innej: odwołanie dotyczy
#: jednego obudzenia, ma swój identyfikator i ma się dać zrobić bez dotykania powierzenia.
CANCEL_RETURN_PATH = "/workspaces/{workspace_id}/projects/{project_id}/returns/{wakeup_id}/cancel"

#: How many wake-ups one read returns. A day of ticking at ten minutes is about 144 rows, so this
#: covers roughly a day and a half: enough to answer "what happened overnight" in one request.
MAX_WAKEUPS = 200

#: How far back the day report and this screen look by default.
DEFAULT_WINDOW = timedelta(hours=24)


async def _resolve_project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> None:
    workspaces = WorkspaceStore(await pool_for(request.app))
    if (
        await workspaces.workspace(
            organization_id=operator.organization_id, workspace_id=workspace_id
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"nie ma workspace'u {workspace_id}")
    pool = await pool_for(request.app)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM projects WHERE organization_id = $1 AND workspace_id = $2 AND id = $3",
            operator.organization_id,
            workspace_id,
            project_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")


def wakeup_payload(wakeup: WakeupRow, *, note: str | None = None) -> dict[str, Any]:
    """One key at a time. `skipped_reason` is the CODE and `skipped_detail` the sentence: the
    screen groups by the first and shows the second, and neither can stand for the other."""
    return {
        "wakeup_id": str(wakeup.id),
        "reason": wakeup.reason,
        "session_id": str(wakeup.session_id) if wakeup.session_id else None,
        "skipped_reason": wakeup.skipped_reason,
        "skipped_detail": wakeup.skipped_detail,
        "cost": wakeup.cost,
        "created_at": wakeup.created_at.isoformat(),
        "consumed_at": wakeup.consumed_at.isoformat() if wakeup.consumed_at else None,
        # 0023: obudzenie UMÓWIONE na termin. `None` znaczy „to obudzenie wzięło się ze
        # zdarzenia", a nie „termin minął": te dwa zdania mają różne wiersze.
        "due_at": wakeup.due_at.isoformat() if wakeup.due_at else None,
        "cancelled_at": wakeup.cancelled_at.isoformat() if wakeup.cancelled_at else None,
        # ZDANIE AGENTA, nie kod powodu. `reason` zaplanowanego powrotu to zawsze „DUE", więc
        # ekran pokazujący `reason` pokazywałby listę identycznych wierszy. Po co agent wraca,
        # wie tylko notatka, którą zostawił przy planowaniu.
        "notatka": note,
    }


@router.get(PRESENCE_PATH)
async def read_presence(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    limit: Annotated[int, Query(ge=1, le=MAX_WAKEUPS)] = 50,
) -> dict[str, Any]:
    """What governs this project, what it spent, and every wake-up of the last day."""
    await _resolve_project(request, operator, workspace_id, project_id)
    pool = await pool_for(request.app)
    store = PresenceStore(pool)
    now = datetime.now(UTC)

    delegation = await store.delegation_for(
        organization_id=operator.organization_id, project_id=project_id
    )
    wakeups = await store.wakeups_for(
        organization_id=operator.organization_id, project_id=project_id, limit=limit
    )
    # A scheduled row is a plan, not a model run. Its eventual execution has a separate tick.
    wakeups = tuple(row for row in wakeups if row.due_at is None)
    # Osobna lista, nie filtr po `wakeups`: obudzenia to HISTORIA, a te dopiero mają się zdarzyć.
    # Zmieszane w jedną listę dawałyby ekran, na którym przyszłość wygląda jak przeszłość.
    planned = await store.planned_returns(
        organization_id=operator.organization_id, project_id=project_id
    )
    notes = await store.notes_of_returns([row.id for row in planned])

    budget: dict[str, Any] | None = None
    if delegation is not None:
        async with pool.acquire() as conn:
            used = await spent(
                conn,
                organization_id=operator.organization_id,
                project_id=project_id,
                accounts=delegation.scope.accounts,
                limits=effective_limits(delegation),
                now=now,
            )
        budget = used.to_json()
        budget["wyczerpany"] = used.exhausted(include_publications=delegation.mode != "RESEARCH")

    return {
        "delegation": delegation_payload(delegation),
        "budget": budget,
        "wakeups": [wakeup_payload(row) for row in wakeups],
        "planned": [wakeup_payload(row, note=notes.get(row.id)) for row in planned],
        # The single sentence the screen leads with. Computed here rather than in the browser,
        # so the answer to "is it working" has one definition in the product.
        "state": _state_of(delegation, wakeups),
        # KOLEKTOR PRZESTRZENI, POD WŁASNĄ NAZWĄ (E6). `state` mówi o agencie tego projektu i
        # słowo „czuwa" czyta się jak „nic się nie dzieje", choć w tym samym czasie kolektor
        # pobiera wątki. To jest praca PRZESTRZENI, nie tego projektu, więc wraca osobno.
        "collector": await ObservationStore(pool).collector_state(
            organization_id=operator.organization_id, workspace_id=workspace_id
        ),
    }


def _state_of(delegation, wakeups: tuple[WakeupRow, ...]) -> str:
    """One of four words, chosen in the order a person would ask them."""
    if delegation is None:
        return "niepowierzony"
    if delegation.revoked_at is not None:
        return "odwołany"
    if getattr(delegation, "expired", False):
        # 0033: the deadline passed and the product stopped the work itself. Not "wstrzymany",
        # which a button undoes; this one needs a new signature.
        return "wygasły"
    if delegation.paused_at is not None:
        return "wstrzymany"
    if any(row.session_id is not None and row.consumed_at is None for row in wakeups):
        return "pracuje"
    return "czuwa"


@router.post(CANCEL_RETURN_PATH)
async def cancel_return(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    wakeup_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Odwołaj powrót, na który agent się umówił.

    WIERSZ ZOSTAJE, ze znacznikiem odwołania. „Dlaczego agent nie wrócił, skoro zapowiedział"
    to pytanie, które ktoś zadaje tydzień później, a skasowany wiersz nie odpowiada na nie
    wcale. 404 dostaje tylko to, czego nie ma albo co już się wydarzyło: odwołanie czegoś, co
    właśnie obudziło sesję, byłoby obietnicą, której ta trasa nie umie dotrzymać.
    """
    await _resolve_project(request, operator, workspace_id, project_id)
    store = PresenceStore(await pool_for(request.app))
    row = await store.cancel_return(
        wakeup_id, organization_id=operator.organization_id, now=datetime.now(UTC),
        project_id=project_id,
    )
    note = (await store.notes_of_returns([wakeup_id])).get(wakeup_id) if row else None
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"nie ma czekającego powrotu {wakeup_id} (odwołany, zużyty albo nie istnieje)",
        )
    return {"odwolany": wakeup_payload(row, note=note)}


@router.get(DECISIONS_PATH)
async def read_decisions(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Proposals of this project that are still waiting for a person.

    Reached through the project's ACCOUNTS, the same join the delegated approver uses, because a
    proposal knows its account and an account is what a project is given. Expired ones are left
    out: their consent window closed and no button here can reopen it.
    """
    await _resolve_project(request, operator, workspace_id, project_id)
    pool = await pool_for(request.app)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id AS revision_id, r.payload_hash, r.created_at, r.expires_at,
                   r.payload ->> 'text_and_media' AS text,
                   r.payload ->> 'target_url' AS target_url,
                   r.payload ->> 'target_external_id' AS target_external_id,
                   p.social_account_id, a.external_account_id
              FROM action_proposal_revisions r
              JOIN action_proposals p
                ON p.id = r.proposal_id AND p.organization_id = r.organization_id
              JOIN social_accounts a ON a.id = p.social_account_id
              JOIN project_account_assignments x
                ON x.organization_id = p.organization_id
               AND x.social_account_id = p.social_account_id
               AND x.unassigned_at IS NULL
             WHERE r.organization_id = $1 AND r.workspace_id = $2 AND x.project_id = $3
               AND r.payload ->> 'plan_version' = $3::text
               AND r.state = 'READY_FOR_APPROVAL'
               AND r.expires_at > now()
               AND NOT EXISTS (
                   SELECT 1 FROM publication_requests q WHERE q.proposal_revision_id = r.id)
             ORDER BY r.created_at
            """,
            operator.organization_id,
            workspace_id,
            project_id,
        )
    from control_plane.pilot.publication import split_media

    decisions = []
    for row in rows:
        # The text as it would go on the platform; an attached image is named apart (27.09).
        text, media = split_media(row["text"] or "")
        decisions.append(
            {
                "revision_id": str(row["revision_id"]),
                "payload_hash": row["payload_hash"],
                "text": text if row["text"] is not None else None,
                "image": str(media.artifact_id) if media is not None else None,
                "target_url": row["target_url"],
                "target_external_id": row["target_external_id"],
                "account": row["external_account_id"],
                "created_at": row["created_at"].isoformat(),
                "expires_at": row["expires_at"].isoformat(),
            }
        )
    return {"decisions": decisions}
