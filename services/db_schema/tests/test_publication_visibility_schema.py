"""Migration 0012: the schema can tell "we published it" apart from "anyone can see it".

WHY THE COLUMNS EXIST (STAN-PRAC §8.22). On 17.09 AutoModerator removed both comments we had just
posted in two new communities. Our control read fourteen minutes later returned `still_present =
true`, because every read goes through the capsule's logged-in profile and Reddit shows an author
their own removed comments. The database had one column for "is it there" and it answered a
question nobody had asked.

WHAT THIS FILE MEASURES, and where it stops. The values a CHECK accepts are read from
`pg_constraint` and then EXECUTED against a scratch table carrying the real constraint definition,
so a constraint that exists but permits everything fails here. It does not seed a publication: the
behaviour of the worker is measured in `services/control_plane/tests/test_publication_visibility.py`
against a real queue.

The lookup is `(conname, conrelid)`, never `conname` alone, for the reason `test_enum_parity.py`
records: `conname` is unique per RELATION, so a constraint of the right name moved to a decoy
table once satisfied a whole parity suite while the guarded column had no CHECK at all.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.db, pytest.mark.p0]

WHEN = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

_LITERAL = re.compile(r"'([^']*)'::text")

#: Every value each closed list must accept, and nothing besides.
VISIBILITIES = frozenset({"CONFIRMED", "REMOVED", "UNVERIFIED"})
VIEWERS = frozenset({"AUTHOR", "ANONYMOUS"})
REMOVAL_SOURCES = frozenset({"INBOX", "ANONYMOUS_READ"})

#: `(constraint, table, accepted values)`. The table is part of the key, not decoration.
CLOSED_LISTS = (
    ("publication_requests_public_visibility_is_known", "publication_requests", VISIBILITIES),
    ("publication_observations_viewer_is_known", "publication_observations", VIEWERS),
    ("publication_requests_removal_source_is_known", "publication_requests", REMOVAL_SOURCES),
)


async def _definition(connection, constraint: str, table: str) -> str:
    definition = await connection.fetchval(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conname = $1 AND contype = 'c' AND conrelid = ('public.' || $2)::regclass
        """,
        constraint,
        table,
    )
    if not definition:
        elsewhere = await connection.fetch(
            "SELECT conrelid::regclass::text AS present_on FROM pg_constraint "
            "WHERE conname = $1 AND contype = 'c'",
            constraint,
        )
        raise AssertionError(
            f"nie ma nazwanego CHECK-a {constraint} NA TABELI {table}. Ta sama nazwa występuje "
            f"na: {[r['present_on'] for r in elsewhere] or 'nigdzie'}. Nazwa nie jest tożsamością "
            "w pg_constraint: parą jest (conname, conrelid)"
        )
    return str(definition)


async def _refused(connection, sql: str, *args) -> str:
    """Run a statement that MUST violate a CHECK, and give back the error text.

    Each attempt runs inside its own nested transaction, i.e. a savepoint. Without one the first
    violation aborts the whole transaction and every later statement comes back
    `current transaction is aborted`, a message with no "check" in it, so a loop of expected
    failures would assert on PostgreSQL's complaint about itself rather than on the constraint.
    """
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except Exception as exc:  # the violation we came for
        return str(exc).lower()
    raise AssertionError(f"instrukcja przeszła, choć CHECK miał ją odrzucić: {sql} {args}")


async def _default(connection, table: str, column: str) -> str | None:
    return await connection.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
        table,
        column,
    )


@pytest.mark.parametrize(
    ("constraint", "table", "values"),
    CLOSED_LISTS,
    ids=[c for c, _t, _v in CLOSED_LISTS],
)
async def test_each_closed_list_accepts_exactly_the_declared_values(
    pool, constraint: str, table: str, values: frozenset[str]
) -> None:
    assert values, f"{constraint}: pusta lista wartości to kontrola, która niczego nie zamyka"
    async with pool.acquire() as connection:
        definition = await _definition(connection, constraint, table)
        actual = set(_LITERAL.findall(definition))
    assert actual, f"CHECK {constraint} nie zawiera ani jednego literału: {definition}"
    assert actual == set(values), (
        f"CHECK {constraint} na {table}: tylko w bazie {sorted(actual - set(values))}, "
        f"tylko w deklaracji {sorted(set(values) - actual)}"
    )


async def test_the_visibility_check_really_refuses_a_value_outside_its_list(pool) -> None:
    """Reading the literals out of a definition proves what the definition SAYS. This executes it.

    The constraint is copied onto a scratch table by its own definition text, so what is exercised
    is the expression the migration installed, not a paraphrase of it. A constraint that listed
    the three values and was somehow never enforced would pass the test above and fail this one.
    """
    async with pool.acquire() as connection, connection.transaction():
        definition = await _definition(
            connection, "publication_requests_public_visibility_is_known", "publication_requests"
        )
        await connection.execute(
            f"CREATE TEMP TABLE visibility_probe (public_visibility text, CHECK {definition[6:]})"
            if definition.startswith("CHECK ")
            else f"CREATE TEMP TABLE visibility_probe (public_visibility text, {definition})"
        )
        for value in sorted(VISIBILITIES):
            await connection.execute(
                "INSERT INTO visibility_probe (public_visibility) VALUES ($1)", value
            )
        for refused in ("PUBLISHED", "OK", "confirmed", "", "TRUE"):
            message = await _refused(
                connection, "INSERT INTO visibility_probe (public_visibility) VALUES ($1)", refused
            )
            assert "check" in message, (
                f"wartość {refused!r} odrzucona z innego powodu niż CHECK widoczności: {message}"
            )


async def test_everything_already_in_the_table_is_unverified_and_read_by_the_author(pool) -> None:
    """The defaults, which are the whole migration for the rows that already exist.

    Thirty-eight requests are PUBLISHED in the live database and not one of them has ever been
    read by anybody but the account. Any default other than these two would be a verdict invented
    by a migration (D-007), and `UNVERIFIED` would be the value the code never had to produce.
    """
    async with pool.acquire() as connection:
        assert await _default(connection, "publication_requests", "public_visibility") == (
            "'UNVERIFIED'::text"
        )
        assert await _default(connection, "publication_observations", "viewer") == "'AUTHOR'::text"
        for column in ("public_visibility", "viewer"):
            table = (
                "publication_requests"
                if column == "public_visibility"
                else ("publication_observations")
            )
            nullable = await connection.fetchval(
                "SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = $1 AND column_name = $2",
                table,
                column,
            )
            assert nullable == "NO", (
                f"{table}.{column} jest NULLowalna, czyli trzecia wartość obok listy zamkniętej, "
                "i to ta, którą każdy raport przemilczy"
            )


async def test_a_verdict_other_than_unverified_must_carry_the_moment_it_was_measured(
    pool,
) -> None:
    """`CONFIRMED` with no timestamp is a gate announcing a result with no measurement behind it,
    which is the failure this repository has paid for six times (D-007). The constraint is
    executed, not read: a scratch table with the real definition, and every combination tried."""
    async with pool.acquire() as connection, connection.transaction():
        definition = await _definition(
            connection, "publication_requests_visibility_has_a_check", "publication_requests"
        )
        await connection.execute(
            "CREATE TEMP TABLE visibility_time_probe ("
            " public_visibility text, public_visibility_checked_at timestamptz, "
            f"{definition})"
        )
        allowed = (
            ("UNVERIFIED", None),
            ("UNVERIFIED", WHEN),
            ("CONFIRMED", WHEN),
            ("REMOVED", WHEN),
        )
        for visibility, checked in allowed:
            await connection.execute(
                "INSERT INTO visibility_time_probe VALUES ($1, $2)", visibility, checked
            )
        for visibility in ("CONFIRMED", "REMOVED"):
            message = await _refused(
                connection, "INSERT INTO visibility_time_probe VALUES ($1, NULL)", visibility
            )
            assert "check" in message, (
                f"{visibility} bez `public_visibility_checked_at` przeszło. Werdykt bez "
                f"momentu pomiaru wygląda w raporcie identycznie jak werdykt z pomiarem: {message}"
            )


async def test_a_reported_removal_always_names_where_it_came_from(pool) -> None:
    """An inbox report and an anonymous read are different measurements; a removal that names
    neither is a claim with no provenance, and the next person cannot check it."""
    async with pool.acquire() as connection, connection.transaction():
        definition = await _definition(
            connection, "publication_requests_removal_names_its_source", "publication_requests"
        )
        await connection.execute(
            "CREATE TEMP TABLE removal_probe ("
            f" removal_reported_at timestamptz, removal_source text, {definition})"
        )
        await connection.execute("INSERT INTO removal_probe VALUES (NULL, NULL)")
        await connection.execute("INSERT INTO removal_probe VALUES ($1, 'INBOX')", WHEN)
        for reported, source in ((WHEN, None), (None, "INBOX")):
            message = await _refused(
                connection, "INSERT INTO removal_probe VALUES ($1, $2)", reported, source
            )
            assert "check" in message, (reported, source, message)


async def test_the_index_that_finds_the_last_anonymous_read_exists(pool) -> None:
    """Visibility is computed from the newest ANONYMOUS observation of one request. Without this
    index that read walks every observation of the request, and there is one an hour per
    publication for a day."""
    async with pool.acquire() as connection:
        definition = await connection.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = $1 "
            "AND indexname = $2",
            "publication_observations",
            "publication_observations_by_request_and_viewer",
        )
    assert definition, "nie ma indeksu po (request_id, viewer, observed_at)"
    assert "request_id" in definition and "viewer" in definition
    assert "observed_at DESC" in definition, definition
