"""Fixtures for the schema suite.

THE `pool` FIXTURE FAILS WHEN THERE IS NO DSN. IT DOES NOT SKIP.

`pytest.mark.skipif(not DSN, ...)` is the shape this repository has to stop using. Measured by
the red team on 2026-08-31: a module carrying that mark reports "2 tests collected" and then
"2 skipped" with exit code 0 — so a runner that trusts the collected count, or just the exit
code, reads a database suite that never touched a database as a green run. That is the same
disease as a gate announcing GO with no measurement (D-007), one layer down.

A missing database is therefore a FAILURE here. `tools/dev/run_db_tests.sh` provides the DSN
and independently refuses a run with `passed == 0` or `skipped > 0`.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

#: Set by `tools/dev/run_db_tests.sh`, which brings up a single-use PostgreSQL and applies
#: every migration in `db/migrations/` before the first test runs.
DSN_VARIABLE = "WENEXT_TEST_DSN"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.fail(
            f"brak {DSN_VARIABLE} — ten test wymaga PostgreSQL. To jest PORAŻKA, nie "
            "pominięcie: test bazodanowy pominięty po cichu przechodzi w podsumowaniu za "
            "zielony. Uruchom: REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh services/db_schema"
        )

    connection_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()
