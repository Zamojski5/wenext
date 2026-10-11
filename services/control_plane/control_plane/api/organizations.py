"""`/api/organizations` — who belongs here, who is invited, and which space you are working in.

P7 etap A. The routes are deliberately few, and each one exists because without it somebody
would have to ask the founder to run SQL:

    GET    /api/organizations                      the spaces this user belongs to
    POST   /api/organizations/switch               work in another one of them
    GET    /api/organizations/{id}/members         who is here, including who used to be
    POST   /api/organizations/{id}/invitations     invite by email, get the link to pass on
    GET    /api/organizations/{id}/invitations     what is still open
    DELETE /api/organizations/{id}/members/{user}  take access away

TWO PROPERTIES WORTH STATING:

  * **the invitation link is shown once, to the person inviting.** There is no mail in etap A,
    and inventing one (an SMTP client nobody configured, a queue nobody watches) would produce
    invitations that silently never arrive. The screen says outright that the link has to be
    passed on by hand;
  * **owners and members differ in exactly one thing: inviting and revoking.** Everything else
    in the product is shared inside an organization. A finer permission model is not in this
    milestone, and a role column nobody reads would be RBAC growing back by accident (D-016).

THE ORGANIZATION IN THE PATH IS ALWAYS THE ONE IN THE SESSION. A request naming another
organization gets 404, the same answer as an organization that does not exist — a 403 would
confirm the identifier is real.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api.auth import identity_store
from control_plane.audit import AuditWriter
from control_plane.auth import (
    SESSION_COOKIE_NAME,
    SESSIONS,
    OperatorSession,
    current_operator,
    pool_for,
)
from control_plane.identity.store import (
    MAX_EMAIL_LENGTH,
    ROLE_OWNER,
    IdentityRefused,
    IdentityStore,
    MembershipRow,
)

router = APIRouter(prefix="/api/organizations", tags=["organizacje"])


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID


async def _here(
    operator: OperatorSession, organization_id: UUID, store: IdentityStore
) -> MembershipRow:
    """The membership this request acts under, or 404. THE gate of every route with an id."""
    if organization_id != operator.organization_id:
        raise HTTPException(status_code=404, detail=f"nie ma przestrzeni {organization_id}")
    if operator.user_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "ta sesja pochodzi z hasła operatora sprzed kont użytkowników. Zaloguj się "
                "adresem e-mail, żeby zarządzać dostępem"
            ),
        )
    membership = await store.active_membership(
        user_id=operator.user_id, organization_id=organization_id
    )
    if membership is None:
        raise HTTPException(status_code=404, detail=f"nie ma przestrzeni {organization_id}")
    return membership


def _owner_only(membership: MembershipRow) -> None:
    if membership.role != ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="tylko właściciel przestrzeni zaprasza i odbiera dostęp",
        )


async def _audit(
    request: Request,
    *,
    kind: str,
    operator: OperatorSession,
    organization_id: UUID,
    subject_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=organization_id,
            subject_ref=subject_ref,
            actor_ref=f"user:{operator.user_id or operator.operator_id}",
            payload=payload,
        )


@router.get("")
async def list_organizations(
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    """Where this person may work, and which of those they are in right now."""
    if operator.user_id is None:
        return {
            "current": str(operator.organization_id),
            "organizations": [],
            "legacy_operator": True,
        }
    memberships = await store.memberships_of(operator.user_id)
    return {
        "current": str(operator.organization_id),
        "legacy_operator": False,
        "organizations": [
            {
                "organization_id": str(row.organization_id),
                "name": row.organization_name,
                "role": row.role,
                "joined_at": row.created_at.isoformat(),
            }
            for row in memberships
        ],
    }


@router.post("/switch")
async def switch_organization(
    body: SwitchBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    """Work in another space this user belongs to. The membership is checked before the move."""
    if operator.user_id is None:
        raise HTTPException(status_code=400, detail="sesja operatora nie ma innych przestrzeni")
    membership = await store.active_membership(
        user_id=operator.user_id, organization_id=body.organization_id
    )
    if membership is None:
        raise HTTPException(status_code=404, detail=f"nie ma przestrzeni {body.organization_id}")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None or SESSIONS.rescope(token, body.organization_id) is None:
        raise HTTPException(status_code=404, detail="ta sesja już nie istnieje")
    return {
        "current": str(body.organization_id),
        "name": membership.organization_name,
        "role": membership.role,
    }


@router.get("/{organization_id}/members")
async def list_members(
    organization_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    """Everybody who belongs here, and everybody who used to. The second half is the audit."""
    await _here(operator, organization_id, store)
    members = await store.members(organization_id)
    emails = await store.emails_of(organization_id)
    return {
        "organization_id": str(organization_id),
        "members": [
            {
                "user_id": str(row.user_id),
                "email": emails.get(row.user_id),
                "role": row.role,
                "joined_at": row.created_at.isoformat(),
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
                "active": row.active,
            }
            for row in members
        ],
    }


@router.post("/{organization_id}/invitations", status_code=201)
async def invite_member(
    organization_id: UUID,
    body: InviteBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    """Create an invitation and hand back its token ONCE.

    The token is in the response and nowhere else: the database keeps only its SHA-256. Whoever
    invited has to pass the link on themselves, and the payload says so in the field name.
    """
    membership = await _here(operator, organization_id, store)
    _owner_only(membership)
    try:
        invitation, token = await store.invite(
            organization_id=organization_id,
            email=body.email,
            created_by=operator.user_id,  # type: ignore[arg-type]  # `_here` refused None above
        )
    except IdentityRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        request,
        kind="ORGANIZATION_MEMBER_INVITED",
        operator=operator,
        organization_id=organization_id,
        subject_ref=f"invitation:{invitation.id}",
        payload={"invitation_id": str(invitation.id)},
    )
    return {
        "invitation": {
            "invitation_id": str(invitation.id),
            "email": invitation.email,
            "expires_at": invitation.expires_at.isoformat(),
        },
        # Named for what it is: this value is shown once and has to be delivered by a person.
        "token_to_pass_on": token,
        "uwaga": (
            "Ten link pokazujemy raz i nie wysyłamy go mailem. Przekaż go osobie, którą "
            "zapraszasz, kanałem, któremu ufasz."
        ),
    }


@router.get("/{organization_id}/invitations")
async def list_invitations(
    organization_id: UUID,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    membership = await _here(operator, organization_id, store)
    _owner_only(membership)
    rows = await store.invitations(organization_id)
    return {
        "invitations": [
            {
                "invitation_id": str(row.id),
                "email": row.email,
                "expires_at": row.expires_at.isoformat(),
                "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
                "live": row.is_live(),
            }
            for row in rows
        ]
    }


@router.delete("/{organization_id}/members/{user_id}", status_code=204)
async def revoke_member(
    organization_id: UUID,
    user_id: UUID,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> None:
    """Take access away. The row stays with the moment it ended, and the session dies at once.

    "At once" is not a figure of speech: `require_operator` re-checks the membership on every
    request, so the revoked person's next call is refused even though their cookie is still
    valid for hours.
    """
    membership = await _here(operator, organization_id, store)
    _owner_only(membership)
    try:
        removed = await store.revoke_membership(
            organization_id=organization_id,
            user_id=user_id,
            acting_user_id=operator.user_id,  # type: ignore[arg-type]
        )
    except IdentityRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="ta osoba nie ma tu aktywnego dostępu")
    await _audit(
        request,
        kind="ORGANIZATION_MEMBER_REVOKED",
        operator=operator,
        organization_id=organization_id,
        subject_ref=f"user:{user_id}",
        payload={"user_id": str(user_id)},
    )
