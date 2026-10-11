"""The single-operator auth boundary (L5191, D-016) — closed by default, in ONE place.

Package P1 shipped this module as a FAIL-CLOSED STUB that refused everything but `/healthz`.
This is the mechanism D-016 decided, now that migration 0002 has created the table it needs:

*   the password is verified with **Argon2id** against the single enabled row of `operators`
    (the column has a CHECK on the `$argon2id$` prefix, so a plaintext or bcrypt value cannot
    even be stored),
*   the session is a cookie that is **HttpOnly, SameSite=Strict and NOT Secure** — the last one
    is not an oversight: over plain HTTP on the loopback interface a `Secure` cookie is never
    sent back, so the flag would break the session instead of protecting it,
*   the session has a **time limit** and is ended server-side by `DELETE /api/session`.

THE IDENTITY OF THE OPERATOR COMES FROM THE COOKIE AND THE ROW IT POINTS AT — NEVER FROM A
FIELD IN THE REQUEST. The shape being avoided is in this repository:
`services/stream_gateway/stream_gateway/app.py` around line 118 reads `user_id` out of the POST
body of `/browser-sessions` and carries no `Depends` at all, so anybody who reaches the port
names themselves anyone.

Four things here are load-bearing and easy to undo by accident:

1.  **The decision lives in `deny_reason()` and nowhere else.** Enforcement happens twice — in
    middleware and in a router dependency — and both ask this one function. Two copies of an
    authorization rule drift, and the drift is invisible until it is a hole.

2.  **Enforcement is MIDDLEWARE, not only a dependency.** A FastAPI dependency, however global,
    only reaches routes added through `include_router`. `/openapi.json`, `/docs` and `/redoc`
    are plain Starlette routes added by `FastAPI.setup()`, so no dependency touches them — and
    `/openapi.json` is a complete map of the private API. Middleware also covers paths that do
    not exist, which is why a refused caller cannot tell an existing route from a missing one.

3.  **`deny_reason` takes the whole `Request`, not a path.** It has to see the cookie. The
    earlier signature took `request.url.path`, and a boundary that only knows the path can
    either refuse everything (no login is possible) or allow by path (no credential is
    checked). Callers that still pass a string get a loud `TypeError` rather than a quiet
    decision made on half the evidence.

4.  **`PUBLIC_PATHS` stays one entry long and the login route is NOT in it.** `POST
    /api/session` is reachable without a session because that is where a credential is
    presented; the handler itself refuses when the credential is missing or wrong. It is a
    (method, path) pair — `CREDENTIAL_ROUTE` — so `GET /api/session` remains behind the
    boundary and nothing else on that path is opened.

The 401 carries an EMPTY body. D-016 asks for that literally: "brak sesji = 401 z pustym
ciałem, nie komunikat naprowadzający". A helpful error here is a free oracle, and so is a
`WWW-Authenticate` header — deliberately absent, there is no browser-driven login: the
dashboard talks to this service server-side only.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
from fastapi import Request, Response

#: The ONLY paths outside the boundary.
#:
#: `/healthz` is not a convenience. The container healthcheck in `docker-compose.yml` calls it,
#: `depends_on: condition: service_healthy` waits for it, and `make up --wait` hangs on the
#: whole stack if it never turns green. Anything added to this set widens the authentication
#: boundary and belongs in `docs/DECYZJE.md` before it belongs here —
#: `test_the_public_list_holds_health_and_nothing_else` is what makes that a conscious edit.
PUBLIC_PATHS = frozenset({"/healthz"})

#: The one (method, path) pair a request without a session may reach. NOT a public path: the
#: handler behind it refuses every request that carries no valid credential, with the same empty
#: 401 as the boundary itself.
CREDENTIAL_ROUTE: tuple[str, str] = ("POST", "/api/session")

#: Since P7 the product has more than one customer, so there is more than one door at which a
#: credential is presented. Each entry is a (method, path) PAIR, each handler refuses without a
#: valid credential with the same empty 401, and the set is short on purpose:
#:
#:   * `POST /api/session` — the founder's operator password (unchanged, D-016);
#:   * `POST /api/auth/login` — email and password of a user;
#:   * `POST /api/auth/register` — a new organization with its first owner. Self-service
#:     onboarding is the whole point of the milestone; a deployment that does not want it sets
#:     `WENEXT_REGISTRATION_OPEN=0` and the handler refuses with a sentence;
#:   * `POST /api/auth/invitations/accept` — an invitation token plus a password chosen by the
#:     person accepting. Behind the boundary it would be unreachable by the one person it is for;
#:   * `POST /api/auth/local-session` — THE ONE DOOR WITH NO CREDENTIAL AT ALL, and the only
#:     reason it is on this list is that behind the boundary it would answer 401 for ever, which
#:     is what it did until 20.09. It is not open: the handler refuses unless
#:     `WENEXT_LOCAL_AUTOLOGIN` names a user AND the deployment has no door to the internet
#:     (no `WENEXT_PUBLIC_DOMAIN`, no edge bound outside the loopback) AND both ends of the call
#:     are local addresses. Off by default, refused on a server even when switched on, and every
#:     session it opens leaves an audit row (`identity/local_access.py` says what this does and
#:     does not guarantee).
#:
#: Widening this set is widening the authentication boundary, and
#: `test_the_credential_routes_are_the_four_doors_and_nothing_else` makes it a conscious edit.
CREDENTIAL_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        CREDENTIAL_ROUTE,
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/invitations/accept"),
        ("POST", "/api/auth/local-session"),
    }
)

#: Name of the session cookie. Referenced by the dashboard's server side (D-016: the cookie
#: never reaches browser code) and by the tests.
SESSION_COOKIE_NAME = "wenext_operator_session"

#: How long a session lasts. Absolute, NOT sliding: renewing on every request would make a
#: stolen cookie immortal, which is the opposite of a time limit. D-017 froze the queue's
#: thresholds and says nothing about sessions, so this value is set here, with its reason: one
#: working day of the single operator, after which the password is presented again.
SESSION_TTL = timedelta(hours=8)

#: Upper bound on the credential a caller may present. Argon2 verification cost barely depends
#: on the input length, but accepting a multi-megabyte string still hands an unauthenticated
#: caller a free allocation on every request.
MAX_CREDENTIAL_LENGTH = 1024

#: Env var carrying the DSN, spelled exactly as `docker-compose.yml` sets it for the `api`
#: service. A second name would mean the container works and the tests do not, or vice versa.
DSN_VARIABLE = "DATABASE_URL"

UNAUTHORIZED_STATUS = 401

#: One hasher for the process. Defaults of argon2-cffi 25.1 are Argon2**id**, m=64 MiB, t=3,
#: p=4 — the encoded form starts with `$argon2id$`, which is what the CHECK constraint
#: `credential_hash_is_argon2id` in migration 0002 requires.
_HASHER = PasswordHasher()

#: Guards the lazy pool creation below. Two requests arriving before the pool exists would
#: otherwise build two pools and leak one.
_POOL_LOCK = asyncio.Lock()


class OperatorRequired(Exception):
    """Raised by `require_operator`; turned into an empty 401 by the handler in `app.py`.

    Not `fastapi.HTTPException`: that one renders `{"detail": ...}`, and a body is exactly what
    must not be sent.
    """


@dataclass(frozen=True)
class OperatorSession:
    """Who is logged in, where, and until when. Frozen: a session that can be edited fences nothing.

    `user_id` arrives with P7 and is `None` for one case only: a session opened with the
    founder's operator password through `POST /api/session`, which still works. Every session
    opened by a user carries it, and `require_operator` then re-checks the membership behind it
    ON EVERY REQUEST — a revoked member holding an eight-hour cookie must lose access at once,
    and revoking access is precisely what somebody does in a hurry.

    There is still no role field here, and there must not be one: the role lives on the
    membership row and is read when it is needed. A role copied into the session is a permission
    that keeps working after somebody took it away.
    """

    operator_id: UUID
    organization_id: UUID
    expires_at: datetime
    user_id: UUID | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at


class SessionStore:
    """Process-local sessions, keyed by the SHA-256 of the cookie value.

    Two deliberate properties:

    *   **The raw cookie value is never stored.** A memory dump, a repr in a traceback or a
        debugger session then hands out no usable session.
    *   **The store is in memory, so a restart of the control plane logs the operator out.**
        There is no `operator_sessions` table in migrations 0001-0003, and inventing one is a
        migration, not a module. The consequence is written down rather than hidden: F1's DoD
        asks that a component restart not lose a TASK (L5212) — it says nothing about a session,
        and a login is one password away.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, OperatorSession] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def open(
        self,
        *,
        operator_id: UUID,
        organization_id: UUID,
        now: datetime | None = None,
        user_id: UUID | None = None,
    ) -> tuple[str, OperatorSession]:
        """Mint a new session and return (cookie value, session)."""
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        session = OperatorSession(
            operator_id=operator_id,
            organization_id=organization_id,
            expires_at=moment + SESSION_TTL,
            user_id=user_id,
        )
        self._sessions[self._key(token)] = session
        return token, session

    def rescope(self, token: str, organization_id: UUID) -> OperatorSession | None:
        """Point an open session at another organization the same user belongs to.

        The caller checks the membership first; this only moves the scope. A new cookie is not
        minted: the person is the same person, and rotating the cookie on every switch would
        log them out of the tab they left open.
        """
        key = self._key(token)
        session = self._sessions.get(key)
        if session is None:
            return None
        moved = OperatorSession(
            operator_id=session.operator_id,
            organization_id=organization_id,
            expires_at=session.expires_at,
            user_id=session.user_id,
        )
        self._sessions[key] = moved
        return moved

    def resolve(self, token: str, now: datetime | None = None) -> OperatorSession | None:
        """The session this cookie names, or None. An expired session is dropped, not returned."""
        key = self._key(token)
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.is_expired(now):
            del self._sessions[key]
            return None
        return session

    def close(self, token: str) -> bool:
        """End the session server-side. True when there was one to end.

        A logout that only asks the browser to drop the cookie leaves the session valid for
        anybody holding a copy — and the copy is the entire reason to log out.
        """
        return self._sessions.pop(self._key(token), None) is not None

    def clear(self) -> None:
        """Drop every session. Used by the tests between cases."""
        self._sessions.clear()


#: The process-wide store. A module-level singleton on purpose: the boundary has to answer the
#: same way in the middleware, in the dependency and in the route handler of one process.
SESSIONS = SessionStore()


def hash_credential(password: str) -> str:
    """Argon2id encoded hash of an operator password, in the form `operators` accepts."""
    if not password:
        raise ValueError("puste hasło operatora — poświadczenie musi coś zawierać")
    return _HASHER.hash(password)


async def create_operator(
    connection: asyncpg.Connection, *, organization_id: UUID, password: str
) -> UUID:
    """Insert one enabled operator and return its id.

    The provisioning path, used by the development path and by the tests. It does NOT try to
    enforce "only one operator" itself: `operators_one_enabled_per_organization` (0002) does
    that, and a partial unique index is the only one of CHECK / trigger / index that survives
    two concurrent inserts. A second enabled row therefore raises `UniqueViolationError` from
    the database, not from a code path somebody could forget to call.
    """
    return await connection.fetchval(
        "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2) RETURNING id",
        organization_id,
        hash_credential(password),
    )


async def verify_credential(
    connection: asyncpg.Connection, password: str
) -> OperatorSession | None:
    """Verify a password against the single enabled `operators` row. None means refusal.

    Returns a session-shaped value built ENTIRELY from the database row: the caller passes a
    password and nothing else, so there is no field in which an identity or an organization
    could be smuggled alongside it.

    Three refusals, and none of them is an accident:

    *   **no enabled row** — D-016 literally: "brak wiersza w `operators` oznacza odmowę, nie
        dostęp". A boundary that treats "nobody is provisioned yet" as "no credential needed" is
        wide open exactly when nobody is looking;
    *   **more than one enabled row** — E1 has one organization and one operator; with several,
        this function cannot tell which one the password belongs to, and guessing would be
        authentication by coincidence;
    *   **a hash that does not verify.**
    """
    if not password or len(password) > MAX_CREDENTIAL_LENGTH:
        return None

    rows = await connection.fetch(
        "SELECT id, organization_id, credential_hash FROM operators WHERE disabled_at IS NULL"
    )
    if len(rows) != 1:
        # Both zero and many are refusals, and the caller learns nothing about which.
        return None

    row = rows[0]
    try:
        _HASHER.verify(row["credential_hash"], password)
    except (VerificationError, InvalidHash):
        return None

    now = datetime.now(UTC)
    return OperatorSession(
        operator_id=row["id"],
        organization_id=row["organization_id"],
        expires_at=now + SESSION_TTL,
    )


def deny_reason(request: Request) -> str | None:
    """The whole authorization decision. None means "let it through".

    The returned string is for LOGS and TESTS, never for the response body.
    """
    if not isinstance(request, Request):
        raise TypeError(
            "deny_reason() dostaje cały Request, nie ścieżkę. Granica musi zobaczyć ciasteczko "
            "sesji: decyzja podjęta po samej ścieżce albo odrzuca wszystko (nie da się "
            "zalogować), albo przepuszcza po ścieżce (nie sprawdza poświadczenia)"
        )

    path = request.url.path
    if path in PUBLIC_PATHS:
        return None
    if (request.method, path) in CREDENTIAL_ROUTES:
        # A route where a credential is PRESENTED. Each handler refuses without one.
        return None

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return "brak ciasteczka sesji operatora"
    if SESSIONS.resolve(token) is None:
        return "ciasteczko sesji nieznane albo wygasłe"
    return None


def session_of(request: Request) -> OperatorSession | None:
    """The session this request carries, or None. The ONLY source of operator identity."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return SESSIONS.resolve(token)


async def require_operator(request: Request) -> None:
    """FastAPI dependency attached to every discovered router.

    Redundant with the middleware by design. It is here so the requirement is visible at the
    point where routes are mounted, and so that a route reached by some future path that
    bypasses the middleware stack still carries the check. Both ask `deny_reason`, so there is
    one rule and not two.

    SINCE P7 IT ALSO RE-CHECKS MEMBERSHIP, and that check is deliberately here rather than in
    the middleware: the middleware is synchronous and has no database. One indexed lookup per
    request buys the property the milestone needs — access ends when the membership ends, not
    when the cookie does, which is also what makes "delete my organization" mean something
    immediately.
    """
    if deny_reason(request) is not None:
        raise OperatorRequired()
    session = session_of(request)
    if session is None or session.user_id is None:
        # No session at all is already refused above; a session with no user is the founder's
        # operator login, which predates memberships and keeps working exactly as before.
        return
    from control_plane.identity.store import IdentityStore

    pool = await pool_for(request.app)
    membership = await IdentityStore(pool).active_membership(
        user_id=session.user_id, organization_id=session.organization_id
    )
    if membership is None:
        # Revoked, disabled, or an organization whose deletion was requested. The session is
        # dropped so the next request does not pay for the same lookup.
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            SESSIONS.close(token)
        raise OperatorRequired()


async def current_operator(request: Request) -> OperatorSession:
    """The logged-in operator, for handlers that need to know who is acting.

    Declared by the handler as `Depends(current_operator)`. This — and not a body field, a query
    parameter or a header — is where an identity comes from.
    """
    session = session_of(request)
    if session is None:
        raise OperatorRequired()
    return session


def unauthorized() -> Response:
    """The one and only refusal response: 401, empty body, no hints."""
    return Response(status_code=UNAUTHORIZED_STATUS)


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie, with the flags D-016 names one by one.

    `secure=False` is deliberate and documented in the module docstring: the stack publishes
    only on the loopback interface over plain HTTP (D-014), and a `Secure` cookie would never
    come back — the session would silently stop working. It becomes `True` the day this service
    is reachable over TLS, which is also the day D-016's loopback assumption expires.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Ask the browser to drop the cookie. The server side is ended by `SESSIONS.close`."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, samesite="strict")


async def pool_for(app) -> asyncpg.Pool:
    """The connection pool of this process, created once.

    `app.state.pool` wins when it is set — that is how the test suite points the application at
    the throwaway PostgreSQL of the run, on the same event loop as the test. Otherwise the pool
    is built from `DATABASE_URL`, which `docker-compose.yml` already sets for the `api` service.

    A missing DSN raises. It is a misconfiguration, not a refusal: answering 401 here would make
    "the database is not connected" look exactly like "your password is wrong".
    """
    existing = getattr(app.state, "pool", None)
    if existing is not None:
        return existing
    async with _POOL_LOCK:
        existing = getattr(app.state, "pool", None)
        if existing is not None:
            return existing
        dsn = os.environ.get(DSN_VARIABLE)
        if not dsn:
            raise RuntimeError(
                f"brak {DSN_VARIABLE} — control plane nie ma połączenia z PostgreSQL. To jest "
                "błąd konfiguracji, nie odmowa uwierzytelnienia; 401 w tym miejscu kłamałby, "
                "że hasło jest złe"
            )
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        app.state.pool = pool
        return pool
