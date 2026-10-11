"""`/api/session` — the three routes of the operator session (D-016).

    POST   → presents the password, mints the session cookie
    GET    → says who is logged in
    DELETE → ends the session on the SERVER, then asks the browser to drop the cookie

Two rules this module is built to keep, both of them about the same mistake:

1.  **The request never names the operator.** The body of `POST` carries a password and nothing
    else — a body with any additional field is REFUSED rather than partially read, because a
    field like `operator_id` or `organization_id` sitting next to the password is an identity
    somebody hoped would be trusted. `GET` reports the operator the cookie resolves to, not
    anything the caller says. The shape being avoided is
    `services/stream_gateway/stream_gateway/app.py` around line 118: `POST /browser-sessions`
    takes `user_id` from the payload and has no `Depends` at all.

2.  **A refused request answers 401 with an empty body and no `Set-Cookie`.** Not a 422 with a
    validation report (which is why the body is parsed by hand instead of by a Pydantic model —
    a schema error is a free description of the API), not a message distinguishing "wrong
    password" from "no operator provisioned", and no cookie, because a caller must not be able
    to collect a session by being refused.

There is deliberately no audit entry for a REFUSED attempt. `audit_events.organization_id` is
NOT NULL and a refused request has no organization to attribute a row to; inventing one would
put a fabricated tenant into an append-only log. Rate limiting the credential route is NOT part
of this package and is not solved elsewhere either — it is reported as a gap rather than
implied.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from control_plane.audit import AuditWriter
from control_plane.auth import (
    MAX_CREDENTIAL_LENGTH,
    SESSION_COOKIE_NAME,
    SESSIONS,
    OperatorSession,
    clear_session_cookie,
    current_operator,
    pool_for,
    set_session_cookie,
    unauthorized,
    verify_credential,
)

router = APIRouter(prefix="/api", tags=["sesja operatora"])

#: The single field the credential body may contain. A frozenset compared for EQUALITY, not for
#: containment: `{"password", "operator_id"}` must fail, and a `>=` test would let it through.
CREDENTIAL_BODY_FIELDS = frozenset({"password"})


async def _presented_credential(request: Request) -> str | None:
    """The password this request presents, or None for anything else.

    Every `None` below is a refusal and none of them explains itself to the caller. Parsing by
    hand rather than through a Pydantic model is what keeps that true: a model answers a missing
    or malformed body with a 422 describing the schema.
    """
    body = await request.body()
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    if frozenset(parsed) != CREDENTIAL_BODY_FIELDS:
        # An extra field is a refusal, not something to ignore: it is where an identity or an
        # organization would be smuggled in next to the password.
        return None
    password = parsed["password"]
    if not isinstance(password, str) or not password or len(password) > MAX_CREDENTIAL_LENGTH:
        return None
    return password


@router.post("/session", status_code=204)
async def open_session(request: Request) -> Response:
    """Verify the password against the single enabled `operators` row and mint a session.

    This is the one (method, path) pair `control_plane.auth.CREDENTIAL_ROUTE` lets past the
    boundary without a session; the refusals here are what make that safe.
    """
    password = await _presented_credential(request)
    if password is None:
        return unauthorized()

    # P2 etap B: the fourth credential door gets the same limiter as the other three. Asked
    # BEFORE the Argon2id verification, so a blocked caller does not get the product's compute
    # either. Keyed by source address only: this route names no account.
    from control_plane.api.auth import source_address
    from control_plane.identity.attempts import AttemptLimiter

    pool = await pool_for(request.app)
    limiter = AttemptLimiter(pool)
    address = source_address(request)
    verdict = await limiter.check(email=None, address=address)
    if not verdict.allowed:
        return Response(
            status_code=429, headers={"retry-after": str(max(1, verdict.retry_after_seconds))}
        )

    async with pool.acquire() as connection:
        verified = await verify_credential(connection, password)
        if verified is None:
            blocked = await limiter.record_failure(email=None, address=address)
            if not blocked.allowed:
                return Response(
                    status_code=429,
                    headers={"retry-after": str(max(1, blocked.retry_after_seconds))},
                )
            return unauthorized()
        await limiter.record_success(email=None, address=address)

        token, session = SESSIONS.open(
            operator_id=verified.operator_id, organization_id=verified.organization_id
        )
        try:
            async with connection.transaction():
                await AuditWriter(connection).record(
                    kind="OPERATOR_SESSION_OPENED",
                    organization_id=session.organization_id,
                    subject_ref=f"operator:{session.operator_id}",
                    actor_ref=f"operator:{session.operator_id}",
                    payload={"expires_at": session.expires_at.isoformat()},
                )
        except Exception:
            # The in-memory session is rolled back together with the audit row. A session
            # without its audit entry is exactly the asymmetry `AuditWriter` exists to prevent.
            SESSIONS.close(token)
            raise

    response = Response(status_code=204)
    set_session_cookie(response, token)
    return response


@router.get("/session")
async def describe_session(
    operator: Annotated[OperatorSession, Depends(current_operator)],
) -> dict[str, str]:
    """Who is logged in, according to the COOKIE and the row behind it.

    No parameter of this handler comes from the request body or the query string, and that is
    the point: there is nothing here for a caller to claim.
    """
    return {
        "operator_id": str(operator.operator_id),
        "organization_id": str(operator.organization_id),
        "expires_at": operator.expires_at.isoformat(),
    }


@router.delete("/session", status_code=204)
async def close_session(
    request: Request, operator: Annotated[OperatorSession, Depends(current_operator)]
) -> Response:
    """End the session server-side, then ask the browser to drop the cookie.

    In this order, and both halves matter: a logout that only clears the cookie leaves the
    session valid for anybody who kept a copy of it, and the copy is the reason to log out.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    closed = SESSIONS.close(token) if token else False

    if closed:
        pool = await pool_for(request.app)
        async with pool.acquire() as connection, connection.transaction():
            await AuditWriter(connection).record(
                kind="OPERATOR_SESSION_CLOSED",
                organization_id=operator.organization_id,
                subject_ref=f"operator:{operator.operator_id}",
                actor_ref=f"operator:{operator.operator_id}",
            )

    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
