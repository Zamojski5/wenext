"""The single-operator boundary, now that it verifies something (L5191, D-016).

What changed since package P1 and why these tests exist:

*   P1 shipped a fail-closed STUB: `deny_reason` refused everything but `/healthz`, so "the
    boundary works" was trivially true and proved nothing about a credential. From here on the
    boundary can also SAY YES, which is the moment it becomes possible for it to say yes to the
    wrong thing.
*   The identity of the operator comes from the session cookie and from the `operators` row it
    points at — NEVER from a field in the request. The shape being avoided is in the tree:
    `services/stream_gateway/stream_gateway/app.py` around line 118 takes `user_id` straight
    out of the POST body and carries no `Depends` at all, so anybody who can reach the port can
    name themselves anyone.

The two counting rules of this repository apply throughout: the number of routes is asserted
BEFORE any predicate over routes (`all([])` is `True`, and that has been four of this project's
false passes), and an empty result set is a failure rather than a quiet pass.
"""

from __future__ import annotations

import asyncpg
import pytest
from control_plane.app import create_app, mounted_routes
from control_plane.auth import (
    CREDENTIAL_ROUTE,
    CREDENTIAL_ROUTES,
    PUBLIC_PATHS,
    SESSION_COOKIE_NAME,
    create_operator,
)

#: NO module-level `pytest.mark.db` here, and that is deliberate — the same reasoning as in
#: `services/db_schema/tests/test_enum_parity.py`. `tools/dev/run_all_tests.sh` runs this package
#: with `-m "not db"`, so a marker on the whole module would deselect the tests below that need
#: no database at all, and a guard that only fires when somebody remembers to start PostgreSQL is
#: a guard that has quietly stopped firing. The tests that do need a connection carry the marker
#: one by one.

#: Substituted into `{placeholders}` so a route with path parameters can be called at all.
ANY_UUID = "00000000-0000-0000-0000-000000000000"


def _route_inventory() -> list[tuple[str, str]]:
    """Every (method, url) pair the application serves, DERIVED from the application.

    Through `control_plane.app.mounted_routes`, which knows what a naive walk of `app.routes`
    gets wrong on FastAPI 0.141.1: `include_router()` appends ONE `_IncludedRouter` object with
    no `path`, so a test iterating `app.routes` sees `/openapi.json`, `/docs`, `/redoc` and
    `/healthz` and NOT ONE route of any mounted router. That naive test passes while checking
    nothing — the perfect false pass, with the routes hidden by the very call that adds them.
    D-016 asks for exactly this generation: "test negatywny na KAŻDEJ trasie zapisującej,
    generowany z listy tras — nie pisany ręcznie, bo trasa dopisana później nie dostałaby
    testu."
    """
    application = create_app()
    pairs: list[tuple[str, str]] = []
    for path, methods in mounted_routes(application):
        if path in PUBLIC_PATHS:
            continue
        url = path
        while "{" in url and "}" in url:
            start = url.index("{")
            end = url.index("}", start)
            url = url[:start] + ANY_UUID + url[end + 1 :]
        for method in sorted(set(methods) - {"HEAD", "OPTIONS"}):
            pairs.append((method, url))
    return pairs


#: Built at import time so `parametrize` can generate one case per route. A module-level
#: constant AND a separate test over its length: if this list were empty, `parametrize` would
#: generate zero cases and the file would report a green run having asserted nothing.
ROUTES = _route_inventory()


def test_the_route_inventory_is_not_empty() -> None:
    """The guard that `parametrize` cannot give itself.

    Zero generated cases is not a passing test, it is an absent one — and pytest reports the
    difference nowhere in its summary.
    """
    assert len(ROUTES) >= 4, (
        f"granica operatora jest sprawdzana na {len(ROUTES)} trasach. Przy zerze parametryzacja "
        "nie tworzy ANI JEDNEGO przypadku, a plik nadal kończy się na zielono"
    )


@pytest.mark.parametrize(("method", "url"), ROUTES)
@pytest.mark.db
async def test_no_route_serves_data_without_a_session(client, method: str, url: str) -> None:
    """Without a session every route answers 401 with an EMPTY body. Every route, one by one.

    POST /api/session is IN this list and belongs here: a caller presenting no credential is
    refused by the handler itself, so the login route leaks neither a schema (which a 422
    would) nor a hint about whether an operator row exists.
    """
    # The count, SEPARATELY and BEFORE the predicate (D-011) — repeated in every case,
    # because parametrizing over an empty list runs nothing at all.
    assert len(ROUTES) >= 4, f"tylko {len(ROUTES)} tras pod testem"

    response = await client.request(method, url)

    assert response.status_code == 401, (
        f"{method} {url} odpowiada {response.status_code}, oczekiwano 401. Ciało: "
        f"{response.content[:200]!r}"
    )
    assert response.content == b"", (
        f"{method} {url}: 401, ale z ciałem {response.content[:200]!r} — D-016 wymaga pustego "
        "ciała, bez komunikatu naprowadzającego"
    )


@pytest.mark.db
async def test_the_credential_route_is_the_only_way_in(client, tenant) -> None:
    """The (method, path) pairs a request without a session may reach.

    Named as constants so widening the set costs a conscious edit and shows up in a diff — the
    same reason `PUBLIC_PATHS` has its own test. `PUBLIC_PATHS` stays one entry long: a login
    route is not public, it is a route at which a credential is checked.

    SINCE P7 THERE ARE FOUR DOORS, not one, because the product has more than one customer:
    the founder's operator password, a user's email and password, registering a new
    organization, and accepting an invitation. Every one of them refuses a request with no
    credential the same way this one does — an empty 401, which is what
    `test_no_route_serves_data_without_a_session` checks for all of them.
    """
    method, path = CREDENTIAL_ROUTE
    assert (method, path) == ("POST", "/api/session")
    assert CREDENTIAL_ROUTE in CREDENTIAL_ROUTES
    assert (
        frozenset(
            {
                ("POST", "/api/session"),
                ("POST", "/api/auth/login"),
                ("POST", "/api/auth/register"),
                ("POST", "/api/auth/invitations/accept"),
                # 20.09: jedyne drzwi bez poświadczenia. Odmawiają domyślnie i odmawiają na serwerze
                # nawet przy włączonym przełączniku; warunki są w `identity/local_access.py`.
                ("POST", "/api/auth/local-session"),
            }
        )
        == CREDENTIAL_ROUTES
    ), sorted(CREDENTIAL_ROUTES)
    assert frozenset({"/healthz"}) == PUBLIC_PATHS, sorted(PUBLIC_PATHS)

    accepted = await client.post("/api/session", json={"password": tenant["password"]})
    assert accepted.status_code == 204, accepted.content[:200]
    assert SESSION_COOKIE_NAME in accepted.cookies, dict(accepted.cookies)

    # The same route with the wrong password: still 401 with an empty body.
    refused = await client.post("/api/session", json={"password": "nie to hasło"})
    assert refused.status_code == 401, refused.content[:200]
    assert refused.content == b""


@pytest.mark.db
async def test_the_session_cookie_is_httponly_and_samesite_strict(client, tenant) -> None:
    """D-016 spells the cookie out: HttpOnly, SameSite=Strict, Secure=false for loopback only.

    Asserted on the raw `set-cookie` header, not on the cookie jar: httpx exposes the value but
    not the flags, and the flags are the entire protection. A cookie readable from JavaScript
    would hand the session to any script the dashboard ever loads.
    """
    response = await client.post("/api/session", json={"password": tenant["password"]})
    assert response.status_code == 204, response.content[:200]

    header = response.headers.get("set-cookie", "")
    lowered = header.lower()
    assert SESSION_COOKIE_NAME in header, header
    assert "httponly" in lowered, header
    assert "samesite=strict" in lowered, header
    assert "path=/" in lowered, header
    assert "secure" not in lowered, (
        "Secure na pętli zwrotnej po HTTP oznacza, że przeglądarka NIE odeśle ciasteczka — "
        f"sesja przestaje działać zamiast być bezpieczniejsza. Nagłówek: {header}"
    )


@pytest.mark.db
async def test_operator_identity_comes_from_the_session_not_from_the_body(
    logged_in_client, tenant, pool
) -> None:
    """Three probes, all of them about the same sentence, and none of them satisfiable by luck.

    1.  GET /api/session reports the operator from the `operators` row the cookie points at,
        even when the request body names a different one.
    2.  POST /api/session carrying any field besides the password is REFUSED — an identity or
        an organization smuggled next to the password never gets a chance to be read.
    3.  GET /api/audit-events is scoped to the session's organization even when the query
        string asks for another one, and the other organization really does have events, so
        honouring the query string would show up here as extra rows rather than as silence.
    """
    other_organization = None
    async with pool.acquire() as connection:
        other_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-organizacja') RETURNING id"
        )
        # An event that MUST NOT be visible to this session. Written through the writer, so the
        # row is shaped exactly like a real one.
        from control_plane.audit import AuditWriter

        async with connection.transaction():
            await AuditWriter(connection).record(
                kind="JOB_STATE_CHANGED",
                organization_id=other_organization,
                subject_ref="job:obcy",
                actor_ref="operator:obcy",
                payload={"from_state": "READY", "to_state": "RUNNING"},
            )
        foreign_events = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1", other_organization
        )
    assert foreign_events == 1, (
        f"obca organizacja ma {foreign_events} zdarzeń — bez nich trzecia próba niżej nie ma "
        "czego wykryć i przechodzi na zielono nawet przy dziurze"
    )

    whoami = await logged_in_client.request(
        "GET", "/api/session", json={"operator_id": str(other_organization)}
    )
    assert whoami.status_code == 200, whoami.content[:200]
    assert whoami.json()["operator_id"] == str(tenant["operator"]), whoami.json()
    assert whoami.json()["organization_id"] == str(tenant["organization"]), whoami.json()

    smuggled = await logged_in_client.post(
        "/api/session",
        json={"password": tenant["password"], "organization_id": str(other_organization)},
    )
    assert smuggled.status_code == 401, smuggled.content[:200]
    assert smuggled.content == b""

    listed = await logged_in_client.get(
        "/api/audit-events", params={"organization_id": str(other_organization)}
    )
    assert listed.status_code == 200, listed.content[:200]
    organizations = {event["organization_id"] for event in listed.json()["events"]}
    assert organizations <= {str(tenant["organization"])}, (
        f"endpoint audytu zwrócił zdarzenia organizacji {sorted(organizations)} — zakres "
        "pochodzi z sesji, nigdy z query stringa"
    )


@pytest.mark.db
async def test_a_rejected_request_leaks_no_identifier(client, pool, tenant, insert_job) -> None:
    """A refusal carries no body, no cookie, no hash and no audit row.

    Every element of this list has a reason:

    *   a body would be an oracle (D-016 asks for an empty one literally),
    *   `set-cookie` on a refusal would let a caller collect a session by being refused,
    *   `WWW-Authenticate` would announce the scheme to probe,
    *   an audit row would be a second oracle, and one that cannot be deleted: `audit_events`
        is append-only. `audit_events.organization_id` is also NOT NULL, and a refused request
        has no organization to attribute the row to — so the missing audit entry here is not an
        omission, it is the only honest option.
    """
    job_id = await insert_job()
    async with pool.acquire() as connection:
        before = await connection.fetchval("SELECT count(*) FROM audit_events")
        credential_hash = await connection.fetchval(
            "SELECT credential_hash FROM operators WHERE id = $1", tenant["operator"]
        )
    assert credential_hash.startswith("$argon2id$"), credential_hash[:20]

    attempts = [
        await client.post("/api/session", json={"password": "złe hasło"}),
        await client.post("/api/session", json={}),
        await client.get("/api/audit-events"),
    ]
    # Cookie set on the client rather than on the request: httpx deprecates per-request cookies
    # because the persistence semantics are ambiguous, and an ambiguous test is not evidence.
    client.cookies.set(SESSION_COOKIE_NAME, "podrobione-ciasteczko")
    attempts.append(await client.get("/api/session"))
    client.cookies.delete(SESSION_COOKIE_NAME)
    assert len(attempts) == 4, len(attempts)

    forbidden_fragments = [str(job_id), credential_hash, credential_hash.split("$")[-1]]
    for index, response in enumerate(attempts):
        assert response.status_code == 401, f"próba {index}: {response.status_code}"
        assert response.content == b"", f"próba {index}: {response.content[:200]!r}"
        assert "set-cookie" not in response.headers, f"próba {index}: {response.headers}"
        assert "www-authenticate" not in response.headers, f"próba {index}: {response.headers}"
        rendered = repr(dict(response.headers))
        for fragment in forbidden_fragments:
            assert fragment not in rendered, f"próba {index} nosi {fragment[:16]}…"

    async with pool.acquire() as connection:
        after = await connection.fetchval("SELECT count(*) FROM audit_events")
    assert after == before, (
        f"odrzucone żądania dopisały {after - before} wpisów audytu. audit_events jest "
        "append-only — wpis powstały przy odmowie zostaje tam na zawsze"
    )


@pytest.mark.db
async def test_a_second_active_operator_is_refused(pool, tenant) -> None:
    """The second enabled operator loses to the partial unique index of 0002 (L4253, D-016).

    In the SCHEMA, not in the application: a CHECK or a trigger counting rows does not survive
    two concurrent inserts, and `operators_one_enabled_per_organization` does. RBAC is forbidden
    in E1 (L4253, L4361), so "one operator" cannot be a role — it has to be a constraint.

    The second half of this test is what gives the first half its detection power: after the
    first row is disabled, the same insert SUCCEEDS. Without it, an index accidentally made
    total instead of partial would keep this test green while making the product unable to ever
    replace an operator.
    """
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.UniqueViolationError) as refused:
            await create_operator(
                connection,
                organization_id=tenant["organization"],
                password="drugie-hasło-operatora",
            )
    assert "operators_one_enabled_per_organization" in str(refused.value), str(refused.value)

    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE operators SET disabled_at = now() WHERE id = $1", tenant["operator"]
        )
        replacement = await create_operator(
            connection,
            organization_id=tenant["organization"],
            password="drugie-hasło-operatora",
        )
    assert replacement is not None
    assert replacement != tenant["operator"]


@pytest.mark.db
async def test_no_enabled_operator_row_means_refusal_not_access(client, pool, tenant) -> None:
    """D-016, literally: "brak wiersza w `operators` oznacza odmowę, nie dostęp".

    The failure mode this guards against is the one that reads as convenience: a boundary that
    treats "nobody is provisioned yet" as "no credential required" is wide open on a fresh
    install, which is exactly when nobody is looking.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE operators SET disabled_at = now() WHERE disabled_at IS NULL"
        )
        enabled = await connection.fetchval(
            "SELECT count(*) FROM operators WHERE disabled_at IS NULL"
        )
    assert enabled == 0, enabled

    response = await client.post("/api/session", json={"password": tenant["password"]})
    assert response.status_code == 401, response.content[:200]
    assert response.content == b""
    assert "set-cookie" not in response.headers


@pytest.mark.db
async def test_logging_out_invalidates_the_cookie_it_was_given(logged_in_client) -> None:
    """DELETE ends the session on the SERVER, not only in the browser.

    A logout that merely asks the browser to drop the cookie leaves the session valid for
    anybody who kept a copy of it — and the copy is the whole reason to log out.
    """
    cookie = logged_in_client.cookies.get(SESSION_COOKIE_NAME)
    assert cookie, dict(logged_in_client.cookies)

    before = await logged_in_client.get("/api/session")
    assert before.status_code == 200, before.content[:200]

    logout = await logged_in_client.delete("/api/session")
    assert logout.status_code == 204, logout.content[:200]

    # The copy somebody kept, replayed. Set on the client, because per-request cookies are
    # deprecated in httpx and their persistence semantics are explicitly called ambiguous.
    logged_in_client.cookies.set(SESSION_COOKIE_NAME, cookie)
    replayed = await logged_in_client.get("/api/session")
    assert replayed.status_code == 401, replayed.content[:200]
    assert replayed.content == b""
