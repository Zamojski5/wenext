"""`/api/x/*`: the "Połącz X" card. Is there an X session, whose is it, and make it the project's.

    GET  /api/x/session          is the capsule logged into X, and as which handle
    POST /api/x/session/confirm  after the human logged in: measure, close the screen, record the
                                 account, give it to the project

FOUR STATES, NOT TWO, and none may pass for another (the Reddit card confused them for a week,
D-046):

  1. no session                      -> the human logs in, which is the one manual step;
  2. a session, handle not read      -> OUR defect; sending the human to log in again for it would
                                        ask for work in answer to our own bug;
  3. a session of ANOTHER account    -> never shown as connected: it looks like success and means
                                        publishing from the wrong identity;
  4. a session of the expected account, or of the first X account of this organization.

WHICH ACCOUNT IS "THE RIGHT ONE". The account the project already has, if it has one: then the
handle read from the browser must match it, and a different one is state 3. A project with no X
account yet takes the account the human logged into, and the card says which handle that was, so
the human sees the identity the agent will act as before any delegation exists. The identity is
checked again, by the actuator, before every publication (`browser_supervisor.x_writer`).

WHAT IS NOT HERE. We do not log in for the human and we do not automate CAPTCHA. The login screen
is the existing `/api/capsule/session` path (with `platform: X` it opens x.com's login page): one
browser, one persistent profile, the Reddit and X sessions side by side. Separate profiles for
two customers are work nobody has done yet (`capsule_session.x_probe_from_env`).
"""

from __future__ import annotations

import os
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from control_plane.audit import AuditWriter
from control_plane.auth import OperatorSession, current_operator, pool_for
from control_plane.observe.capsule_client import (
    CapsuleClient,
    CapsuleConfig,
    CapsuleUnavailable,
    GatewayClient,
    capsule_subject,
)
from control_plane.observe.capsule_session import x_probe_from_env
from control_plane.workspace.account_state import note_capsule_login

router = APIRouter(prefix="/api", tags=["x"])

SESSION_PATH = "/x/session"
CONFIRM_PATH = "/x/session/confirm"

PLATFORM = "X"

#: Sentences for the human, Polish because the founder reads them (D-010). Each one says what to
#: do, or says plainly that there is nothing to do.
NOT_CONFIGURED = "Przeglądarka agenta nie jest skonfigurowana, więc nie ma gdzie trzymać sesji X."
LOG_IN = (
    "Nie widzę jeszcze zalogowanej sesji X. Dokończ logowanie w oknie przeglądarki agenta "
    "i kliknij ponownie „Gotowe, sprawdź połączenie”."
)
#: After the screen was closed (27.09): the person's window is gone, so "finish in the window"
#: would send them to a window that no longer exists.
LOG_IN_AGAIN = (
    "Nie widzę zalogowanej sesji X w przeglądarce agenta. Okno logowania jest już zamknięte: "
    "kliknij „Połącz X”, dokończ logowanie i dopiero wtedy „Gotowe, sprawdź połączenie”."
)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID | None = None
    project_id: UUID | None = None
    session_id: UUID | None = None


async def _x_accounts(pool: Any, organization_id: UUID) -> list[dict[str, Any]]:
    """The X accounts of this organization, in order of addition."""
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, external_account_id, state FROM social_accounts "
            "WHERE organization_id = $1 AND platform = $2 ORDER BY created_at",
            organization_id,
            PLATFORM,
        )
    return [
        {"id": str(row["id"]), "handle": row["external_account_id"], "state": row["state"]}
        for row in rows
    ]


async def _project_x_account(
    pool: Any, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> dict[str, Any] | None:
    """The X account assigned to this project, if any (the first, by assignment)."""
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT a.id, a.external_account_id, a.state
              FROM project_account_assignments x
              JOIN social_accounts a
                ON a.organization_id = x.organization_id AND a.id = x.social_account_id
             WHERE x.organization_id = $1 AND x.workspace_id = $2 AND x.project_id = $3
               AND x.unassigned_at IS NULL AND a.platform = $4
             ORDER BY x.assigned_at
             LIMIT 1
            """,
            organization_id,
            workspace_id,
            project_id,
            PLATFORM,
        )
    if row is None:
        return None
    return {"id": row["id"], "handle": row["external_account_id"], "state": row["state"]}


async def _check_project(
    pool: Any, organization_id: UUID, workspace_id: UUID, project_id: UUID
) -> None:
    """THE gate every project route takes: an id from another organization does not exist."""
    async with pool.acquire() as connection:
        exists = await connection.fetchval(
            "SELECT 1 FROM projects WHERE organization_id = $1 AND workspace_id = $2 AND id = $3",
            organization_id,
            workspace_id,
            project_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail=f"nie ma projektu {project_id}")


@router.get(SESSION_PATH)
async def x_session(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, Any]:
    """The X session NOW. It reads the platform, so do not call it on every render."""
    pool = await pool_for(request.app)
    accounts = await _x_accounts(pool, operator.organization_id)
    probe = x_probe_from_env(os.environ)
    if probe is None:
        return {
            "configured": False,
            "accounts": accounts,
            "logged_in": False,
            "handle": None,
            "message": NOT_CONFIGURED,
        }
    try:
        state = await probe.session_state()
    finally:
        await probe.aclose()
    known = {one["handle"].lower() for one in accounts}
    return {
        "configured": True,
        "accounts": accounts,
        "logged_in": state.logged_in,
        "handle": state.handle,
        "known_account": bool(state.handle and state.handle.lower() in known),
        "checked_at": state.checked_at.isoformat(),
        "message": state.error if not state.logged_in else f"Zalogowano jako @{state.handle}.",
    }


@router.post(CONFIRM_PATH)
async def confirm_x_session(
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    body: ConfirmBody | None = None,
) -> dict[str, Any]:
    """After the human logged in: measure, close the screen, record the account, assign it.

    THE ACCOUNT FOLLOWS REALITY (D-046). This is the one moment we know from a READ which account
    the browser is logged into, so this is where an X account row is created or promoted, and
    only on a confirmed identity: an account marked ready on somebody else's session would be an
    invitation to publish from the wrong identity.

    The screen is closed FIRST (capsule back to display-only, streams revoked), and only then is
    the session read (27.09): the person's browser has no DevTools endpoint, so it cannot be read
    while they are at it. The one reading happens after Chrome restarted on the same profile, so a
    login that did not survive the restart is simply not seen. The price is that a person who
    clicks "Gotowe" too early loses the window; the answer then says to open it again.
    """
    body = body or ConfirmBody()
    pool = await pool_for(request.app)
    project = None
    if body.project_id is not None:
        if body.workspace_id is None:
            raise HTTPException(status_code=422, detail="projekt wymaga workspace_id")
        await _check_project(pool, operator.organization_id, body.workspace_id, body.project_id)
        project = await _project_x_account(
            pool, operator.organization_id, body.workspace_id, body.project_id
        )

    config = CapsuleConfig.for_platform(os.environ, PLATFORM)
    probe = x_probe_from_env(os.environ)
    if config is None or probe is None:
        raise HTTPException(status_code=409, detail=NOT_CONFIGURED)
    try:
        # THE SCREEN CLOSES FIRST (27.09). A person signs in in a browser launched without a
        # DevTools endpoint, because Google refuses a sign-in in one that has it (the capsule's
        # `args.HUMAN_ARGS`), so nothing can read the session while the person is at the screen.
        # "Gotowe" is the person saying they finished; the capsule returns to display-only on the
        # same profile and the session is read there, which also proves it survived the restart.
        try:
            capsule = CapsuleClient(config.reader_url, token=config.reader_token)
            gateway = GatewayClient(config.gateway_url, issuer_token=config.gateway_issuer_token)
        except CapsuleUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        closed = False
        try:
            state = await capsule.state()
            if state.human_steer_open:
                await capsule.close_human_session()
                await gateway.revoke_sessions()
                closed = True
        except CapsuleUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            await capsule.aclose()
            await gateway.aclose()
        seen = await probe.session_state()
    finally:
        await probe.aclose()
    if not seen.logged_in or not seen.handle:
        if seen.login_wall:
            return _answer(False, None, LOG_IN_AGAIN if closed else LOG_IN)
        # Not a login screen: a read that failed or a handle nobody could read. Neither is the
        # person's to fix, so the sentence is ours, not "sign in again".
        return _answer(False, None, seen.error or LOG_IN)
    if project is not None and project["handle"].lower() != seen.handle.lower():
        return _answer(
            False,
            seen.handle,
            f"Przeglądarka agenta jest zalogowana jako @{seen.handle}, a ten projekt działa "
            f"kontem @{project['handle']}. Kliknij „Połącz X”, wyloguj się w oknie przeglądarki "
            "agenta i zaloguj właściwym kontem.",
        )

    handle = seen.handle
    account_id = await _ensure_account(pool, operator.organization_id, handle)
    noted = await note_capsule_login(
        pool,
        organization_id=operator.organization_id,
        platform=PLATFORM,
        logged_in=True,
        username=handle,
        actor_ref=f"operator:{operator.operator_id}",
    )
    assigned = False
    if body.project_id is not None and body.workspace_id is not None and project is None:
        assigned = await _assign(
            pool,
            organization_id=operator.organization_id,
            workspace_id=body.workspace_id,
            project_id=body.project_id,
            account_id=account_id,
        )
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind="CAPSULE_SESSION_CONFIRMED",
            organization_id=operator.organization_id,
            subject_ref=capsule_subject(os.environ, PLATFORM),
            actor_ref=f"operator:{operator.operator_id}",
            # Identifiers, not the handle: `audit_events` takes codes and ids (its allowlist),
            # and the account row carries the handle for anybody who needs it.
            payload={
                "platform": PLATFORM,
                "verdict": "SIGNED_IN",
                "social_account_id": str(account_id),
            },
        )
    where = " i przypisałem je do projektu" if assigned else ""
    return {
        **_answer(True, handle, f"Połączono konto X @{handle}{where}."),
        "konto_id": str(account_id),
        "przypisano": assigned,
        "konto_gotowe": noted.changed or noted.reason is None,
    }


def _answer(connected: bool, handle: str | None, detail: str) -> dict[str, Any]:
    """The shape the card reads: `polaczone`, `konto`, `detail` (the dashboard keeps these)."""
    return {"polaczone": connected, "konto": handle, "detail": detail}


async def _ensure_account(pool: Any, organization_id: UUID, handle: str) -> UUID:
    """The X account with this handle, created PROVISIONED when absent.

    Matched case-insensitively (X does not distinguish case in handles) and stored in the case
    the platform displays, which is what a human recognises. `note_capsule_login` then moves it
    through VERIFYING to READY with its audit entry: this function never writes a state itself.
    """
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"x-account:{organization_id}:{handle.lower()}",
        )
        existing = await connection.fetchval(
            "SELECT id FROM social_accounts WHERE organization_id = $1 AND platform = $2 "
            "AND lower(external_account_id) = lower($3)",
            organization_id,
            PLATFORM,
            handle,
        )
        if existing is not None:
            return existing
        return await connection.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, $2, $3) RETURNING id",
            organization_id,
            PLATFORM,
            handle,
        )


async def _assign(
    pool: Any,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    account_id: UUID,
) -> bool:
    """Give the account to the project, and to the person the project already speaks as.

    The person profile is attached only when it is unambiguous: the project's other accounts all
    hang under ONE profile. Guessing between two people would put one person's name on another's
    account; left unattached, the account shows under "Konta bez profilu" for a human to place.
    """
    async with pool.acquire() as connection, connection.transaction():
        already = await connection.fetchval(
            "SELECT 1 FROM project_account_assignments WHERE organization_id = $1 "
            "AND project_id = $2 AND social_account_id = $3 AND unassigned_at IS NULL",
            organization_id,
            project_id,
            account_id,
        )
        if not already:
            await connection.execute(
                "INSERT INTO project_account_assignments "
                "(organization_id, workspace_id, project_id, social_account_id) "
                "VALUES ($1, $2, $3, $4)",
                organization_id,
                workspace_id,
                project_id,
                account_id,
            )
        attached = await connection.fetchval(
            "SELECT 1 FROM person_profile_accounts WHERE organization_id = $1 "
            "AND social_account_id = $2 AND detached_at IS NULL",
            organization_id,
            account_id,
        )
        if not attached:
            profiles = await connection.fetch(
                """
                SELECT DISTINCT pa.person_profile_id
                  FROM project_account_assignments x
                  JOIN person_profile_accounts pa
                    ON pa.organization_id = x.organization_id
                   AND pa.social_account_id = x.social_account_id
                   AND pa.workspace_id = $4
                   AND pa.detached_at IS NULL
                 WHERE x.organization_id = $1 AND x.project_id = $2 AND x.unassigned_at IS NULL
                   AND x.social_account_id <> $3
                """,
                organization_id,
                project_id,
                account_id,
                workspace_id,
            )
            if len(profiles) == 1:
                await connection.execute(
                    "INSERT INTO person_profile_accounts "
                    "(organization_id, workspace_id, person_profile_id, social_account_id) "
                    "VALUES ($1, $2, $3, $4)",
                    organization_id,
                    workspace_id,
                    profiles[0]["person_profile_id"],
                    account_id,
                )
    return not already
