"""`/api/auth` — the doors a person walks through before they have a session (P7 etap A).

    POST /api/auth/register            a new organization with its first owner
    POST /api/auth/login               email and password of an existing user
    POST /api/auth/invitations/accept  an invitation token plus a password chosen here
    POST /api/auth/email               the founder's migrated account naming itself (with session)

THREE OF THESE FOUR ARE REACHABLE WITHOUT A SESSION, which is why they are listed by name in
`auth.CREDENTIAL_ROUTES` rather than hidden behind a prefix rule: widening an authentication
boundary must be a line somebody had to write.

WHAT THEY REFUSE, AND WHY IN THOSE WORDS:

  * a refusal never says whether an account exists. "Ten adres jest już zajęty" on an open
    registration route is a free account oracle, so registering a taken address and mistyping a
    password come back with the same sentence;
  * a session is minted ONLY after the database says yes, and it carries `user_id` — from the
    row, never from the request. The shape being avoided is the same one `api/session.py`
    names: `stream_gateway` reads `user_id` out of a POST body;
  * registration can be closed by the deployment (`WENEXT_REGISTRATION_OPEN=0`) and then says
    so plainly instead of pretending the route does not exist. A product that quietly stops
    accepting customers looks broken rather than closed.

WHAT IS NOT HERE AND IS A KNOWN GAP: rate limiting. Neither this module nor anything in front of
it slows down a caller trying passwords; `api/session.py` reports the same gap for the operator
route. It is written down rather than implied, because the fix belongs in front of the
application (the reverse proxy) and pretending otherwise would leave everybody comfortable.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import AuditWriter
from control_plane.auth import (
    SESSIONS,
    OperatorSession,
    current_operator,
    pool_for,
    set_session_cookie,
    unauthorized,
)
from control_plane.identity.store import (
    MAX_CREDENTIAL_LENGTH,
    MAX_EMAIL_LENGTH,
    IdentityRefused,
    IdentityStore,
)

#: Powód odmowy trybu lokalnego idzie do LOGU, nie do ciała odpowiedzi: człowiek przy tej
#: maszynie ma go zobaczyć, a obcy pytający bez poświadczenia nie ma się dowiedzieć niczego.
_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["tożsamość"])

#: `0` closes registration. Anything else (including the variable being absent) leaves it open,
#: because self-service onboarding is what this milestone is for.
REGISTRATION_VARIABLE = "WENEXT_REGISTRATION_OPEN"

#: One sentence for every refusal at the door. The same words whatever went wrong, so that being
#: refused teaches nothing about who exists.
REFUSED = "nie udało się zalogować. Sprawdź adres i hasło"


#: The shortest password this product accepts at the door. Eight is not a security claim; it is
#: the floor under "haslo" as a word, and the real defence is Argon2id plus a rate limit that
#: belongs in front of the application (see the module docstring).
MIN_PASSWORD_LENGTH = 8

#: The exact field set of each body on a route reachable WITHOUT a session. Compared for
#: EQUALITY, like `api/session.py` does: an extra field is where an identity or an organization
#: would be smuggled in next to the credential.
REGISTER_FIELDS = frozenset({"email", "password", "organization_name"})
LOGIN_FIELDS = frozenset({"email", "password"})
ACCEPT_FIELDS = frozenset({"token", "password"})


async def _presented(request: Request, fields: frozenset[str]) -> dict[str, str] | None:
    """The body of a request at a credential door, or None for anything that is not one.

    PARSED BY HAND, exactly as `api/session.py` parses the operator password, and for the same
    reason: a Pydantic model answers a missing or malformed body with a 422 that DESCRIBES THE
    SCHEMA, and these three routes are reachable without a session. `test_auth_boundary` requires
    every route to answer an unauthenticated caller with an empty 401, and it is right to.
    """
    import json as _json

    body = await request.body()
    if not body:
        return None
    try:
        parsed = _json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or frozenset(parsed) != fields:
        return None
    values: dict[str, str] = {}
    for name in fields:
        value = parsed[name]
        if not isinstance(value, str) or not value or len(value) > MAX_CREDENTIAL_LENGTH:
            return None
        values[name] = value
    return values


class EmailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)


def registration_open(env: dict[str, str] | None = None) -> bool:
    raw = (os.environ if env is None else env).get(REGISTRATION_VARIABLE)
    return raw is None or raw.strip() not in ("0", "false", "FALSE", "no")


async def identity_store(request: Request) -> IdentityStore:
    return IdentityStore(await pool_for(request.app))


def source_address(request: Request) -> str | None:
    """Where the request came from, as far as this process can honestly tell.

    `request.client.host` and NOT `X-Forwarded-For`: a header the caller sets is not an identity,
    and trusting it would hand an attacker a fresh rate-limit key per request. Behind a proxy
    this is the proxy, which the limiter's docstring says out loud.
    """
    client = request.client
    return client.host if client is not None else None


async def _limiter(request: Request):
    from control_plane.identity.attempts import AttemptLimiter

    return AttemptLimiter(await pool_for(request.app))


def _too_many(seconds: int) -> Response:
    """The refusal a rate-limited caller gets: 429, empty body, `Retry-After`.

    Empty for the same reason as the 401 next to it (D-016): a body here would be a place to
    say something about the account. 429 rather than 401 because the two are different facts and
    a person who typed their own password wrong five times deserves to learn which one this is;
    the counter exists for unregistered addresses as well, so the status leaks no account.
    """
    return Response(status_code=429, headers={"retry-after": str(max(1, seconds))})


async def _audit(
    request: Request,
    *,
    kind: str,
    organization_id: Any,
    subject_ref: str,
    actor_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await pool_for(request.app)
    async with pool.acquire() as connection, connection.transaction():
        await AuditWriter(connection).record(
            kind=kind,
            organization_id=organization_id,
            subject_ref=subject_ref,
            actor_ref=actor_ref,
            payload=payload,
        )


def _session_payload(user, membership, memberships) -> dict[str, Any]:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "needs_email": user.needs_email,
        "organization_id": str(membership.organization_id),
        "organization_name": membership.organization_name,
        "role": membership.role,
        "memberships": [
            {
                "organization_id": str(row.organization_id),
                "organization_name": row.organization_name,
                "role": row.role,
            }
            for row in memberships
        ],
    }


@router.post("/register", status_code=201)
async def register(request: Request) -> Any:
    """A new organization, its first owner, and a session. No console, no founder involved.

    THE STORE IS BUILT INSIDE, after the body has been checked, and not taken as a dependency.
    A `Depends` is resolved BEFORE the handler runs, so an unauthenticated caller with an empty
    body would make this process open a database pool — and on a deployment with no DSN that is
    a 500 where the boundary promises an empty 401. Measured by
    `test_operator_boundary.py::test_every_route_except_health_requires_an_operator`.
    """
    body = await _presented(request, REGISTER_FIELDS)
    if body is None:
        return unauthorized()
    store = await identity_store(request)
    if not registration_open():
        raise HTTPException(
            status_code=403,
            detail=(
                "to wdrożenie nie przyjmuje nowych przestrzeni. Poproś o zaproszenie osobę, "
                "która już tu pracuje"
            ),
        )
    if len(body["password"]) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków",
        )
    # Registration is rate limited by SOURCE ADDRESS only. There is no account yet, so an email
    # key here would let somebody lock a stranger out of registering under their own address.
    limiter = await _limiter(request)
    address = source_address(request)
    verdict = await limiter.check(email=None, address=address)
    if not verdict.allowed:
        return _too_many(verdict.retry_after_seconds)
    try:
        user, membership = await store.register_organization(
            email=body["email"],
            password=body["password"],
            organization_name=body["organization_name"],
        )
    except IdentityRefused as exc:
        # A refused registration counts: trying addresses until one is free is the shape this
        # limit exists for, and the refusal sentence is the same for every reason anyway.
        await limiter.record_failure(email=None, address=address)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token, _session = SESSIONS.open(
        operator_id=user.id, organization_id=membership.organization_id, user_id=user.id
    )
    await _audit(
        request,
        kind="ORGANIZATION_REGISTERED",
        organization_id=membership.organization_id,
        subject_ref=f"organization:{membership.organization_id}",
        actor_ref=f"user:{user.id}",
        payload={"user_id": str(user.id), "role": membership.role},
    )
    response = _json_response(201, {"session": _session_payload(user, membership, [membership])})
    set_session_cookie(response, token)
    return response


@router.post("/login")
async def login(request: Request) -> Any:
    """Email and password. The organization comes from the membership, never from the request.

    The store is built after the body check, for the reason `register` spells out.
    """
    body = await _presented(request, LOGIN_FIELDS)
    if body is None:
        return unauthorized()
    store = await identity_store(request)

    # ASKED BEFORE THE PASSWORD IS VERIFIED. A blocked caller must not get the Argon2id
    # verification either, or the limit would still be paying the attacker's compute bill.
    limiter = await _limiter(request)
    address = source_address(request)
    verdict = await limiter.check(email=body["email"], address=address)
    if not verdict.allowed:
        return _too_many(verdict.retry_after_seconds)

    user = await store.verify_login(email=body["email"], password=body["password"])
    if user is None:
        blocked = await limiter.record_failure(email=body["email"], address=address)
        if not blocked.allowed:
            return _too_many(blocked.retry_after_seconds)
        return unauthorized()
    memberships = await store.memberships_of(user.id)
    if not memberships:
        # A user with no live membership has nowhere to be. Same empty 401: whether the account
        # exists is not something a refused caller gets to learn.
        return unauthorized()
    active = await store.active_membership(
        user_id=user.id, organization_id=memberships[0].organization_id
    )
    if active is None:
        # The oldest membership is in an organization whose deletion was requested. Try the rest
        # before refusing: being in a deleted organization must not lock somebody out of another.
        # A plain loop, not a generator expression: `await` inside one makes it an ASYNC
        # generator, and `next()` over that raises — measured as a 500 on exactly this path.
        for row in memberships[1:]:
            found = await store.active_membership(
                user_id=user.id, organization_id=row.organization_id
            )
            if found is not None:
                active = found
                break
    if active is None:
        return unauthorized()

    await limiter.record_success(email=body["email"], address=address)
    await store.record_login(user.id)
    token, _session = SESSIONS.open(
        operator_id=user.id, organization_id=active.organization_id, user_id=user.id
    )
    await _audit(
        request,
        kind="OPERATOR_SESSION_OPENED",
        organization_id=active.organization_id,
        subject_ref=f"user:{user.id}",
        actor_ref=f"user:{user.id}",
    )
    response = _json_response(200, {"session": _session_payload(user, active, memberships)})
    set_session_cookie(response, token)
    return response


@router.post("/local-session")
async def local_session(request: Request) -> Any:
    """Sesja bez hasła na maszynie lokalnej (tryb `WENEXT_LOCAL_AUTOLOGIN`).

    CIAŁO ŻĄDANIA NIE WYBIERA KONTA i nie ma go tu wcale. Użytkownika wskazuje zmienna po stronie
    serwera; gdyby wskazywał go dzwoniący, ta trasa byłaby wyborem dowolnego konta bez hasła,
    a nie wygodą właściciela laptopa.

    ODMOWA JEST DOMYŚLNA i sprawdzana za każdym razem, nie raz przy starcie: `deployment_is_local`
    patrzy na adres, na którym słucha to API, na adres dzwoniącego oraz na to, czy wdrożenie ma
    skonfigurowane drzwi z internetu (domena, bind brzegu).

    KAŻDA ODMOWA TO TO SAMO PUSTE 401, co w pozostałych drzwiach z poświadczeniem, i to jest
    JEDNA reguła tej trasy: albo otwiera sesję (200), albo odpowiada pustym 401. Powód zostaje
    w logu serwera. Powód w ciele odpowiedzi mówiłby obcemu, czy to wdrożenie ma domenę i czy
    ktoś włączył tryb lokalny, a granica operatora ma nie odpowiadać na pytania zadane bez
    poświadczenia (`test_operator_boundary` sprawdza dokładnie to i nie potrzebuje wyjątku).

    LIMIT PRÓB POŚWIADCZEŃ NIE JEST TU RUSZANY: nie ma tu poświadczenia do zgadywania, a wpisanie
    tej trasy do licznika pozwoliłoby zablokować właścicielowi zwykłe logowanie samym pukaniem.
    """
    from control_plane.identity.local_access import (
        LocalAccessRefused,
        deployment_is_local,
        find_owner,
        who_is_armed,
    )

    server = request.scope.get("server") or (None, None)
    allowed, reason = deployment_is_local(
        server_host=server[0], client_host=request.client.host if request.client else None
    )
    if not allowed:
        _LOG.info("tryb lokalny odmówił: %s", reason)
        return unauthorized()

    pool = await pool_for(request.app)
    async with pool.acquire() as connection:
        try:
            user_id, organization_id = await find_owner(connection, who_is_armed())
        except LocalAccessRefused as refused:
            _LOG.info("tryb lokalny bez właściciela: %s", refused)
            return unauthorized()

    store = await identity_store(request)
    user = await store.user(user_id)
    active = await store.active_membership(user_id=user_id, organization_id=organization_id)
    if user is None or active is None:
        _LOG.info("tryb lokalny: konto %s nie ma czynnego dostępu", user_id)
        return unauthorized()
    await store.record_login(user_id)
    token, _session = SESSIONS.open(
        operator_id=user_id, organization_id=organization_id, user_id=user_id
    )
    # KAŻDA sesja auto zostawia ślad. Bez tego „kto to zrobił" przy trybie lokalnym nie ma
    # odpowiedzi, a odpowiedź „właściciel laptopa" jest dokładnie tą, której dziennik ma udzielać.
    await _audit(
        request,
        kind="OPERATOR_SESSION_OPENED",
        organization_id=organization_id,
        subject_ref=f"user:{user_id}",
        actor_ref="local-mode",
    )
    response = _json_response(
        200, {"session": _session_payload(user, active, await store.memberships_of(user_id))}
    )
    set_session_cookie(response, token)
    return response


@router.post("/invitations/accept", status_code=201)
async def accept_invitation(request: Request) -> Any:
    """Turn a link into a membership. The password is chosen HERE, by the person accepting.

    The store is built after the body check, for the reason `register` spells out.
    """
    body = await _presented(request, ACCEPT_FIELDS)
    if body is None:
        return unauthorized()
    store = await identity_store(request)
    if len(body["password"]) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"hasło musi mieć co najmniej {MIN_PASSWORD_LENGTH} znaków",
        )
    # By source address: the token IS the credential here, and guessing tokens is the attack.
    limiter = await _limiter(request)
    address = source_address(request)
    verdict = await limiter.check(email=None, address=address)
    if not verdict.allowed:
        return _too_many(verdict.retry_after_seconds)
    try:
        user, membership = await store.accept_invitation(
            token=body["token"], password=body["password"]
        )
    except IdentityRefused as exc:
        await limiter.record_failure(email=None, address=address)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await limiter.record_success(email=None, address=address)

    memberships = await store.memberships_of(user.id)
    token, _session = SESSIONS.open(
        operator_id=user.id, organization_id=membership.organization_id, user_id=user.id
    )
    await _audit(
        request,
        kind="ORGANIZATION_MEMBER_JOINED",
        organization_id=membership.organization_id,
        subject_ref=f"user:{user.id}",
        actor_ref=f"user:{user.id}",
        payload={"user_id": str(user.id), "role": membership.role},
    )
    response = _json_response(201, {"session": _session_payload(user, membership, memberships)})
    set_session_cookie(response, token)
    return response


@router.post("/email")
async def set_email(
    body: EmailBody,
    request: Request,
    operator: Annotated[OperatorSession, Depends(current_operator)],
    store: Annotated[IdentityStore, Depends(identity_store)],
) -> dict[str, Any]:
    """The migrated founder account naming itself. Behind the session, unlike the three above.

    Migration 0018 could not know the founder's address, so it left the column NULL rather than
    inventing one. This is where the person fills it in, once.
    """
    if operator.user_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "ta sesja pochodzi z hasła operatora, a nie z konta użytkownika. Zaloguj się "
                "adresem, gdy już go ustawisz"
            ),
        )
    try:
        user = await store.set_email(operator.user_id, body.email)
    except IdentityRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        request,
        kind="USER_EMAIL_SET",
        organization_id=operator.organization_id,
        subject_ref=f"user:{user.id}",
        actor_ref=f"user:{user.id}",
        payload={"user_id": str(user.id)},
    )
    return {"user": {"user_id": str(user.id), "email": user.email, "needs_email": False}}


def _json_response(status: int, payload: dict[str, Any]) -> Response:
    """A response object, because these routes attach a cookie as well as a body."""
    import json

    return Response(
        status_code=status,
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
    )
