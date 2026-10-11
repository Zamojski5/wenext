"""Fixtures shared by the control plane suite.

THE `pool` FIXTURE FAILS WHEN THERE IS NO DSN. IT DOES NOT SKIP.

`pytest.mark.skipif(not DSN, ...)` is the shape this repository has to stop using. Measured by
the red team on 2026-08-31: a module carrying that mark reports "2 tests collected" and then
"2 skipped" with exit code 0 — so a runner that trusts the collected count, or just the exit
code, reads a database suite that never touched a database as a green run. That is the same
disease as a gate announcing GO with no measurement (D-007), one layer down.

A missing database is therefore a FAILURE here: either the suite ran against PostgreSQL, or it
did not run at all and says so. `tools/dev/run_db_tests.sh` is the thing that provides the DSN,
and it independently refuses a run with `passed == 0` or `skipped > 0`.

THE HTTP CLIENT IS `httpx.AsyncClient` OVER `ASGITransport`, NOT `fastapi.testclient`.
Measured, not preferred: `TestClient` drives the application from a portal running in ANOTHER
thread with ANOTHER event loop, while `asyncpg` pools and connections are bound to the loop
that created them. A test that hands the application the `pool` fixture and then calls it
through `TestClient` talks to a pool from a foreign loop — which fails in ways that look like
flakiness rather than like a mistake. `test_operator_boundary.py` keeps using `TestClient`
because nothing there touches the database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
import pytest
import pytest_asyncio
from control_plane.app import create_app
from control_plane.auth import SESSIONS, create_operator

#: Set by `tools/dev/run_db_tests.sh`, which brings up a single-use PostgreSQL and applies
#: every migration before the first test runs.
DSN_VARIABLE = "WENEXT_TEST_DSN"

#: The password the `tenant` fixture gives the single operator. A constant, because the tests
#: assert on the OUTCOME of verification, not on secrecy — and a random password per run would
#: make a failing verification indistinguishable from a mistyped fixture.
OPERATOR_PASSWORD = "operator-fixture-hasło-2026"


@pytest_asyncio.fixture
async def pool():
    """A connection pool against the throwaway PostgreSQL of this run.

    Tests requesting this fixture are database tests and carry `pytest.mark.db`, so the host
    run in `tools/dev/run_all_tests.sh` deselects them with `-m "not db"` instead of skipping
    them. Deselection is visible in the summary as "deselected"; a skip is not.
    """
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.fail(
            f"brak {DSN_VARIABLE} — ten test wymaga PostgreSQL. To jest PORAŻKA, nie "
            "pominięcie: test bazodanowy pominięty po cichu przechodzi w podsumowaniu za "
            "zielony. Uruchom: REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh "
            "services/control_plane"
        )

    connection_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


@pytest.fixture(autouse=True)
def empty_session_store():
    """No session survives into the next test.

    The store is process-local (see `control_plane.auth`), so without this a cookie minted by
    one test would still authenticate in the next one — and a test asserting "no session means
    401" would pass or fail depending on the order pytest happened to pick.
    """
    SESSIONS.clear()
    yield
    SESSIONS.clear()


@pytest_asyncio.fixture
async def tenant(pool) -> dict[str, object]:
    """One organization, one workspace, ONE enabled operator with a known password.

    Why the `UPDATE` first: the throwaway PostgreSQL lives for the whole run and every test
    that needs a login inserts its own operator, while `operators_one_enabled_per_organization`
    (0002) constrains one enabled row PER ORGANIZATION — not one in the table. In production
    E1 has exactly one organization (D-016), so "exactly one enabled operator" holds by
    construction; here it has to be re-established, otherwise `verify_credential` correctly
    refuses to guess which of several enabled operators the password belongs to.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE operators SET disabled_at = now() WHERE disabled_at IS NULL"
        )
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-p7') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization_id,
        )
        operator_id = await create_operator(
            connection, organization_id=organization_id, password=OPERATOR_PASSWORD
        )
    return {
        "organization": organization_id,
        "workspace": workspace_id,
        "operator": operator_id,
        "password": OPERATOR_PASSWORD,
    }


@pytest.fixture
def app(pool):
    """A fresh application whose pool is the throwaway PostgreSQL of this run.

    `app.state.pool` is set here rather than letting `control_plane.auth.pool_for` build one
    from `DATABASE_URL`: a pool created inside the request would live on the same loop but
    outlive the test, and closing it is nobody's job in a test process.
    """
    application = create_app()
    application.state.pool = pool
    return application


@pytest_asyncio.fixture
async def client(app):
    """An HTTP client that speaks to `app` in-process, on THIS event loop."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://control-plane") as http:
        yield http


@pytest_asyncio.fixture
async def logged_in_client(client, tenant):
    """A client holding a real session cookie, obtained through POST /api/session.

    Deliberately not a hand-made cookie: a fixture that forged the session would test the
    routes against a mechanism no caller uses.
    """
    response = await client.post("/api/session", json={"password": tenant["password"]})
    assert response.status_code == 204, (
        f"logowanie fixture'u zwróciło {response.status_code}, ciało {response.content[:200]!r}"
    )
    return client


@pytest.fixture
def insert_job(pool, tenant) -> Callable[..., Awaitable[uuid.UUID]]:
    """Inserts one `jobs` row — the only table of migration 0003 that carries `lock_version`.

    `attempt_limit` is 5 because D-017 froze that value before the queue was written; the row
    is a fixture for the locking tests, not a job anybody dispatches (D-013: F1 has no
    producer of work).
    """

    async def _insert(status: str = "READY", *, connection: asyncpg.Connection | None = None):
        sql = """
            INSERT INTO jobs
                (organization_id, workspace_id, job_type, routing_key, priority, payload_ref,
                 dispatch_version, status, not_before, attempt_limit, idempotency_key,
                 lock_version)
            VALUES ($1, $2, 'FIXTURE', 'fixture.key', 100, NULL, 1, $3, now(), 5, $4, 1)
            RETURNING id
        """
        arguments = (tenant["organization"], tenant["workspace"], status, f"idem-{uuid.uuid4()}")
        if connection is not None:
            return await connection.fetchval(sql, *arguments)
        async with pool.acquire() as own:
            return await own.fetchval(sql, *arguments)

    return _insert
