"""The canary of the database runner.

This test does almost nothing on purpose: it opens a connection and asks the server who it is.
Its job is not to check the schema — later packages of E1.F1 do that — but to prove that the
whole apparatus around it really ran: a container came up, `apply_migrations.sh` applied every
file in `db/migrations/`, `WENEXT_TEST_DSN` reached pytest, and the suite executed rather than
being skipped.

There is deliberately NO `skipif` anywhere in this file. Without a DSN the `pool` fixture in
conftest.py fails, and `tools/dev/run_db_tests.sh` additionally refuses any run that ends with
`passed == 0` or `skipped > 0`. Two independent locks on the same door, because this is the
door through which five of this project's false passes walked.
"""

from __future__ import annotations

import pytest
from db_schema.migrations import migration_files

pytestmark = pytest.mark.db


async def test_database_is_reachable(pool) -> None:
    async with pool.acquire() as connection:
        one = await connection.fetchval("SELECT 1")
        server = await connection.fetchval("SHOW server_version")

    assert one == 1
    assert server, "serwer nie podał swojej wersji — połączenie jest, ale to nie PostgreSQL"


async def test_every_migration_left_something_behind(pool) -> None:
    """Applying the migrations must produce tables — an empty schema is not a pass.

    `apply_migrations.sh` exits zero once psql exits zero for every file, and psql exits zero
    for a file full of comments just as happily as for one that creates eleven tables. The
    count below is asserted separately from anything else for the usual reason: a check over
    an empty set is not a check (D-011).
    """
    files = migration_files()
    async with pool.acquire() as connection:
        tables = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )

    assert files, "nie ma ani jednego pliku migracji — nie ma czego stosować"
    assert tables > 0, (
        f"po zastosowaniu {len(files)} migracji schemat `public` jest pusty. psql kończy się "
        "zerem także dla pliku bez jednego CREATE — zerowa liczba tabel oznacza, że migracje "
        "poszły gdzie indziej albo nie poszły wcale"
    )
