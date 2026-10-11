"""HTTP for delegating a project to the agent, and for taking it back (P4).

WHAT THESE FOUR ROUTES ARE. A delegation is the sentence that lets the agent act while nobody is
watching, so signing one is the single most consequential thing a person does in this product.
The API is shaped around that:

  * SIGNING IS EXPLICIT AND WHOLE. The body carries the mode, the accounts, the communities, the
    actions and the budget together, and the server computes the digest over all of them. There
    is no route that edits a scope: changing what the agent may do means revoking and signing
    again, so no signature ever covers something its signer did not read.
  * PAUSING AND REVOKING ARE DIFFERENT ROUTES, because they are different promises. A pause
    comes back on the SAME signature; a revocation never comes back. One route with a flag would
    make it possible to undo a revocation by accident.
  * THE READ SHOWS THE LIMITS THAT BIND, defaults included. A delegation that named no
    publication limit still has one (`presence/budget.py`), and a panel that showed an empty
    budget would let somebody sign a scope whose real pace is invisible to them.

The gate is P1's: the workspace resolves through `WorkspaceStore` for this operator's
organization, and the project is checked inside it, so an id from another organization is
indistinguishable from one that does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.presence.budget import effective_limits
from control_plane.presence.card_consent import interrupt_presence_sessions
from control_plane.presence.scope import (
    DEFAULT_TERM,
    MAX_TERM,
    MIN_TERM,
    MODES,
    ScopeError,
    parse_budget,
    parse_scope,
)
from control_plane.presence.store import DelegationRow, PresenceRefusal, PresenceStore
from control_plane.workspace import WorkspaceStore

router = APIRouter(prefix="/api", tags=["delegations"])

#: Relative to the router's own `/api` prefix (the mistake `api/sessions.py` records: an absolute
#: path here mounts at `/api/api/...`, which answers 404 and looks like a missing route).
DELEGATION_PATH = "/workspaces/{workspace_id}/projects/{project_id}/delegation"

#: Bounds on what one signature may name. Not a policy: a body from the network allocates, and
#: a scope with ten thousand communities is a mistake in the client rather than a wish.
MAX_ACCOUNTS = 50
MAX_COMMUNITIES = 100


class SignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(AUTONOMOUS|REVIEW|RESEARCH)$")
    accounts: list[UUID] = Field(max_length=MAX_ACCOUNTS)
    communities: list[str] = Field(max_length=MAX_COMMUNITIES)
    actions: list[str] = Field(max_length=10)
    budget: dict[str, int] | None = None
    replaces: UUID | None = None
    allow_own_posts: bool = False
    #: 0033: when the permission ends. Absent means `DEFAULT_TERM`, which the response shows, so
    #: nothing binds that the signer could not see. The card the agent proposes always sends it.
    expires_at: datetime | None = None


def signed_deadline(requested: datetime | None, *, now: datetime) -> datetime:
    """The deadline a new signature carries, or `HTTPException(422)` with the reason.

    Pure, so the bounds are tested without a database. The upper bound is not a policy about
    trust; it is the difference between a delegation and a setting nobody remembers signing.
    """
    if requested is None:
        return (now + DEFAULT_TERM).replace(microsecond=0)
    if requested.tzinfo is None:
        raise HTTPException(status_code=422, detail="termin powierzenia musi mieć strefę czasową")
    if requested < now + MIN_TERM:
        raise HTTPException(
            status_code=422,
            detail="termin powierzenia musi przypadać co najmniej 5 minut po podpisie",
        )
    if requested > now + MAX_TERM:
        raise HTTPException(
            status_code=422,
            detail="termin powierzenia nie może przekraczać 90 dni; dłuższą pracę powierza się "
            "kolejnym podpisem",
        )
    return requested.astimezone(UTC).replace(microsecond=0)


async def _presence(request: Request) -> PresenceStore:
    return PresenceStore(await pool_for(request.app))


async def _resolve_project(
    request: Request, operator: OperatorSession, workspace_id: UUID, project_id: UUID
) -> None:
    """THE gate, the same two steps every P1 route takes."""
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


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    delegation: DelegationRow,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=operator.organization_id,
            workspace_id=delegation.workspace_id,
            subject_ref=f"delegation:{delegation.id}",
            actor_ref=f"operator:{operator.operator_id}",
            payload={
                "delegation_id": str(delegation.id),
                "project_id": str(delegation.project_id),
                "mode": delegation.mode,
                # The digest of what was signed. The scope itself is not here: it is a list of
                # identifiers a person chose, and `audit_events` takes identifiers and codes.
                "payload_hash": delegation.scope_sha256,
            },
        )


def delegation_payload(delegation: DelegationRow | None) -> dict[str, Any] | None:
    """One key at a time, so a column added to 0017 does not silently reach the browser."""
    if delegation is None:
        return None
    limits = effective_limits(delegation)
    return {
        "delegation_id": str(delegation.id),
        "project_id": str(delegation.project_id),
        "mode": delegation.mode,
        "state": delegation.state,
        "live": delegation.live,
        "scope": delegation.scope.to_json(),
        # What the signature SAID, and what actually binds. Both, because they differ whenever a
        # budget key was left out, and the panel has to show the second one.
        "budget": delegation.budget.to_json(),
        "limits": limits.to_json(),
        "scope_sha256": delegation.scope_sha256,
        "signed_at": delegation.signed_at.isoformat(),
        "paused_at": delegation.paused_at.isoformat() if delegation.paused_at else None,
        "revoked_at": delegation.revoked_at.isoformat() if delegation.revoked_at else None,
        "expires_at": delegation.expires_at.isoformat() if delegation.expires_at else None,
        "expired": delegation.expired,
    }


@router.get(DELEGATION_PATH)
async def read_delegation(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """The delegation that governs this project now, or `None`. A revoked one is history."""
    await _resolve_project(request, operator, workspace_id, project_id)
    store = await _presence(request)
    delegation = await store.delegation_for(
        organization_id=operator.organization_id, project_id=project_id
    )
    history = await store.delegation_history(
        organization_id=operator.organization_id, project_id=project_id
    )
    return {
        "delegation": delegation_payload(delegation),
        "history": [delegation_payload(row) for row in history],
    }


@router.post(DELEGATION_PATH, status_code=201)
async def sign_delegation(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    body: SignBody,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Sign a scope. Refuses while a live delegation exists: revoking first is deliberate."""
    await _resolve_project(request, operator, workspace_id, project_id)
    if body.mode not in MODES:  # belt and braces with the pattern above
        raise HTTPException(status_code=422, detail=f"tryb {body.mode} jest nieznany")
    try:
        scope = parse_scope(
            {
                "accounts": [str(account) for account in body.accounts],
                "communities": body.communities,
                "actions": body.actions,
            }
        )
        budget = parse_budget(body.budget)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    expires_at = signed_deadline(body.expires_at, now=datetime.now(UTC))
    store = await _presence(request)
    try:
        delegation = await store.sign(
            organization_id=operator.organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            mode=body.mode,
            scope=scope,
            budget=budget,
            operator_id=operator.operator_id,
            user_id=operator.user_id,
            replaces=body.replaces,
            allow_own_posts=body.allow_own_posts,
            expires_at=expires_at,
        )
    except PresenceRefusal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.replaces is not None:
        await _interrupt_running_sessions(request, operator, project_id)
    await _audit(
        request, kind="PROJECT_DELEGATION_SIGNED", operator=operator, delegation=delegation
    )
    return {"delegation": delegation_payload(delegation)}


@router.post(f"{DELEGATION_PATH}/pause")
async def pause_delegation(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Stop the agent now, keeping the signature. Anything already running is interrupted."""
    return await _set_paused(request, workspace_id, project_id, operator, paused=True)


@router.post(f"{DELEGATION_PATH}/resume")
async def resume_delegation(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    return await _set_paused(request, workspace_id, project_id, operator, paused=False)


async def _set_paused(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: OperatorSession,
    *,
    paused: bool,
) -> dict[str, Any]:
    await _resolve_project(request, operator, workspace_id, project_id)
    store = await _presence(request)
    current = await store.delegation_for(
        organization_id=operator.organization_id, project_id=project_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ten projekt nie ma delegacji")
    if not paused and current.expired:
        # Resuming a signature past its deadline would change nothing (it stays not live) and
        # would look like it did. The way on is a new signature, which the refusal says.
        raise HTTPException(
            status_code=409,
            detail="powierzenie wygasło; wznowić można tylko nowym podpisem z nowym terminem",
        )
    try:
        delegation = await store.set_paused(
            current.id,
            organization_id=operator.organization_id,
            paused=paused,
            at=datetime.now(UTC),
        )
    except PresenceRefusal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if paused:
        # A pause that left a turn running would be a button that does not stop anything. The
        # interrupt is an EVENT for the reason `api/sessions.py` gives: the turn runs in another
        # process and watches its own trace.
        await _interrupt_running_sessions(request, operator, project_id)
    await _audit(
        request,
        kind="PROJECT_DELEGATION_PAUSED" if paused else "PROJECT_DELEGATION_RESUMED",
        operator=operator,
        delegation=delegation,
    )
    return {"delegation": delegation_payload(delegation)}


@router.post(f"{DELEGATION_PATH}/revoke")
async def revoke_delegation(
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """Take the delegation back for good. Proposals still pending stop being issuable at once."""
    await _resolve_project(request, operator, workspace_id, project_id)
    store = await _presence(request)
    current = await store.delegation_for(
        organization_id=operator.organization_id, project_id=project_id
    )
    if current is None:
        raise HTTPException(status_code=404, detail="ten projekt nie ma delegacji")
    delegation = await store.revoke(
        current.id, organization_id=operator.organization_id, at=datetime.now(UTC)
    )
    await _interrupt_running_sessions(request, operator, project_id)
    await _audit(
        request, kind="PROJECT_DELEGATION_REVOKED", operator=operator, delegation=delegation
    )
    return {"delegation": delegation_payload(delegation)}


async def _interrupt_running_sessions(
    request: Request, operator: OperatorSession, project_id: UUID
) -> None:
    """Ask every presence session of this project that is still moving to stop.

    The work is `presence/card_consent.py::interrupt_presence_sessions`, shared with the card in
    the person's agent: a replaced scope is a different permission on either path.
    """
    await interrupt_presence_sessions(
        await pool_for(request.app),
        organization_id=operator.organization_id,
        project_id=project_id,
    )
