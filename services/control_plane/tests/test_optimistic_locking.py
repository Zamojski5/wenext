"""`apply_transition` — one statement, and zero updated rows is a CONFLICT (L5192).

The failure this file exists for is named in this repository's conventions, because it has
already happened in database code: asyncpg's `execute("UPDATE … WHERE lock_version = $1")`
returns the STRING `"UPDATE 0"` when nothing matched, and raises NOTHING. A command that reads
that as success reports a state change that did not happen, keeps the old row, and looks green
from every angle — the same disease as a gate announcing GO without a measurement (D-007), one
layer down.

Hence three properties, each with its own test:

*   the update, the version bump and the state guard are ONE statement with `RETURNING`, so
    there is no window between checking and writing (a separate `UPDATE` or an `AFTER` trigger
    reopens exactly that window),
*   no matching row raises `OptimisticLockConflict` instead of returning quietly,
*   a transition outside the table of `contracts.transitions` is refused BEFORE the connection
    is touched — proved with a connection that explodes on any use, not by inspecting the row
    afterwards.

`jobs` is the table under test because it is the ONLY table in migrations 0001-0003 carrying
`lock_version` (grep: `db/migrations/0003_f1_kernel.sql:95`).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from community_os_contracts.enums import ExecutionAttemptState
from community_os_contracts.transitions import TRANSITION_TABLES, IllegalTransition
from control_plane.locking import (
    EXECUTION_ATTEMPTS,
    JOBS,
    LOCKED_ENTITIES,
    LockedEntity,
    OptimisticLockConflict,
    RefusedTransition,
    apply_transition,
)

#: NO module-level `pytest.mark.db` here, and that is deliberate — the same reasoning as in
#: `services/db_schema/tests/test_enum_parity.py`. `tools/dev/run_all_tests.sh` runs this package
#: with `-m "not db"`, so a marker on the whole module would deselect the tests below that need
#: no database at all, and a guard that only fires when somebody remembers to start PostgreSQL is
#: a guard that has quietly stopped firing. The tests that do need a connection carry the marker
#: one by one.


class _ExplodingConnection:
    """Any use of this object is a failed test.

    "Rejected before touching the database" cannot be shown by looking at the row afterwards:
    an `UPDATE` that matched nothing leaves the row identical to one that was never attempted.
    The only way to tell the two apart is to make the connection unusable.
    """

    def __getattr__(self, name: str):
        raise AssertionError(
            f"apply_transition sięgnęło do połączenia (.{name}) przy przejściu, które tabela "
            "przejść z contracts odrzuca — kontrola legalności ma być PRZED bazą"
        )


@pytest.mark.db
async def test_two_concurrent_transitions_leave_one_winner(pool, tenant, insert_job) -> None:
    """Two transactions, the same `lock_version`, a real PostgreSQL: one winner, one conflict.

    The second `UPDATE` does not fail fast — it BLOCKS on the row lock held by the first, and
    only re-evaluates its `WHERE` after the first commits. That is the whole reason the version
    bump has to live in the same statement: with a separate `UPDATE` (or an `AFTER` trigger)
    both transactions would pass their check and the second would overwrite the first.

    The load-bearing assertion is that `lock_version` grew by ONE. Growing by two would mean
    both writes landed, and the row would look perfectly consistent afterwards.
    """
    job_id = await insert_job()
    first_holds_the_lock = asyncio.Event()

    async def winner() -> int:
        try:
            async with pool.acquire() as connection, connection.transaction():
                version = await apply_transition(
                    connection,
                    entity=JOBS,
                    entity_id=job_id,
                    expected_lock_version=1,
                    source="READY",
                    target="RUNNING",
                )
                first_holds_the_lock.set()
                # Holds the row lock so the second UPDATE really has to queue behind it
                # instead of arriving first. Without this the test measures a race that
                # never happened.
                await asyncio.sleep(0.3)
                return version
        finally:
            # Set even when the winner fails before reaching the line above. MEASURED while
            # breaking the mechanism on purpose: without this `finally`, a broken
            # `apply_transition` left the latecomer waiting forever and the test HUNG instead
            # of failing — and a test that hangs eats the run and reports nothing.
            first_holds_the_lock.set()

    async def latecomer() -> int:
        await asyncio.wait_for(first_holds_the_lock.wait(), timeout=30)
        async with pool.acquire() as connection, connection.transaction():
            return await apply_transition(
                connection,
                entity=JOBS,
                entity_id=job_id,
                expected_lock_version=1,
                source="READY",
                target="CANCELLED",
            )

    results = await asyncio.gather(winner(), latecomer(), return_exceptions=True)

    successes = [result for result in results if isinstance(result, int)]
    conflicts = [result for result in results if isinstance(result, OptimisticLockConflict)]
    unexpected = [
        result
        for result in results
        if not isinstance(result, int) and not isinstance(result, OptimisticLockConflict)
    ]
    assert unexpected == [], unexpected
    assert len(successes) == 1, f"udanych przejść: {successes}"
    assert len(conflicts) == 1, f"konfliktów: {conflicts}"
    assert successes[0] == 2, successes

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, lock_version FROM jobs WHERE id = $1", job_id
        )
    assert row["lock_version"] == 2, (
        f"lock_version = {row['lock_version']}. Wzrost o DWA znaczy, że oba zapisy weszły — "
        "przegrany nadpisał zwycięzcę, a wiersz wygląda potem spójnie"
    )
    assert row["status"] == "RUNNING", dict(row)


@pytest.mark.db
async def test_zero_updated_rows_raises_instead_of_returning_silently(
    pool, tenant, insert_job
) -> None:
    """Three ways to match no row, each of them an exception and none of them a quiet return.

    The first block is the control: the same call with the right arguments SUCCEEDS. Without it
    the three refusals below would also be produced by an `apply_transition` that can never
    update anything at all.
    """
    job_id = await insert_job()

    async with pool.acquire() as connection, connection.transaction():
        assert (
            await apply_transition(
                connection,
                entity=JOBS,
                entity_id=job_id,
                expected_lock_version=1,
                source="READY",
                target="RUNNING",
            )
            == 2
        )

    cases = {
        "nieaktualna wersja": dict(entity_id=job_id, expected_lock_version=1, source="RUNNING"),
        "nieistniejący wiersz": dict(
            entity_id=uuid.uuid4(), expected_lock_version=2, source="RUNNING"
        ),
        "inny stan źródłowy": dict(entity_id=job_id, expected_lock_version=2, source="READY"),
    }
    assert len(cases) == 3, sorted(cases)

    for name, arguments in cases.items():
        async with pool.acquire() as connection:
            with pytest.raises(OptimisticLockConflict) as conflict:
                async with connection.transaction():
                    await apply_transition(connection, entity=JOBS, target="SUCCEEDED", **arguments)
            assert "jobs" in str(conflict.value), f"{name}: {conflict.value}"

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, lock_version FROM jobs WHERE id = $1", job_id
        )
    assert (row["status"], row["lock_version"]) == ("RUNNING", 2), dict(row)


async def test_a_transition_outside_the_table_is_rejected_before_touching_the_database() -> None:
    """The arrows come from `contracts.transitions`, and the refusal happens before any SQL.

    `PREPARED → CONFIRMED` is refused because the document does not draw it (L2601-2605): once
    the submit has started the side effect may already exist on the platform, so the machine
    only reaches `CONFIRMED` from `SUBMIT_STARTED`. That rule is transcribed in exactly one
    place in this repository and this module asks it rather than keeping a copy.
    """
    allowed = TRANSITION_TABLES["ExecutionAttempt"][ExecutionAttemptState.PREPARED]
    assert len(allowed) >= 1, allowed
    assert ExecutionAttemptState.CONFIRMED not in allowed, sorted(state.value for state in allowed)

    with pytest.raises(IllegalTransition, match="PREPARED"):
        await apply_transition(
            _ExplodingConnection(),
            entity=EXECUTION_ATTEMPTS,
            entity_id=uuid.uuid4(),
            expected_lock_version=1,
            source=ExecutionAttemptState.PREPARED,
            target=ExecutionAttemptState.CONFIRMED,
        )

    # And the legal one from the same machine gets past the legality gate — otherwise the
    # rejection above would prove only that this entity refuses everything.
    with pytest.raises(RefusedTransition, match="lock_version"):
        await apply_transition(
            _ExplodingConnection(),
            entity=EXECUTION_ATTEMPTS,
            entity_id=uuid.uuid4(),
            expected_lock_version=1,
            source=ExecutionAttemptState.PREPARED,
            target=ExecutionAttemptState.DISPATCHED,
        )


async def test_a_transition_to_the_same_state_is_refused() -> None:
    """A "transition" that changes nothing is refused, for every entity.

    It would match the row, bump `lock_version` and return a number that reads like progress —
    a successful no-op is the cheapest way to make a state machine look alive while it stands
    still. No machine in section 14.8 or 16.1 draws a self-loop either.
    """
    with pytest.raises(RefusedTransition, match="READY"):
        await apply_transition(
            _ExplodingConnection(),
            entity=JOBS,
            entity_id=uuid.uuid4(),
            expected_lock_version=1,
            source="READY",
            target="READY",
        )


def test_the_registry_says_out_loud_what_it_does_not_enforce() -> None:
    """Silence about an unenforced rule is the thing this project keeps paying for (D-019).

    `jobs` is the only table with `lock_version`, and the job lifecycle has NO transition table
    anywhere: the roadmap enumerates no job statuses (`status` appears at L3831 as a column and
    nowhere as a value list), and `db_schema/tests/test_enum_parity.py::
    test_contracts_have_not_grown_a_second_source_of_job_statuses` deliberately FAILS if
    `contracts` grows a `JobState`/`JobStatus`. So for `jobs` this module enforces the version
    and the source state, and NOT the legality of the arrow — recorded here in writing, with a
    tripwire, instead of being left to be discovered.
    """
    assert len(LOCKED_ENTITIES) >= 2, sorted(LOCKED_ENTITIES)
    assert JOBS.machine is None
    assert JOBS.legality_unenforced, "brak zapisanego powodu = milczenie o nieegzekwowanej regule"
    assert "L3831" in JOBS.legality_unenforced, JOBS.legality_unenforced

    # Tripwire: the day contracts models the job machine, this entity must start using it.
    assert "Job" not in TRANSITION_TABLES, (
        "contracts ma teraz maszynę Job — od tej chwili jest źródłem prawdy dla przejść joba. "
        "Ustaw LockedEntity.machine i usuń legality_unenforced, zamiast trzymać dwa źródła"
    )

    # And the other direction: an entity WITH a machine must name one that exists.
    with pytest.raises(ValueError, match="Nieznana maszyna"):
        LockedEntity(table="jobs", state_column="status", machine="NieMaTakiejMaszyny")


def test_an_identifier_that_is_not_an_identifier_is_refused() -> None:
    """Table and column names are interpolated into the SQL — values never are.

    A parameter placeholder cannot carry an identifier in PostgreSQL, so the only defence is to
    refuse anything that is not a plain lowercase identifier, at construction time rather than
    at query time.
    """
    for bad in ["jobs; DROP TABLE jobs", "JOBS", "jobs WHERE 1=1", ""]:
        with pytest.raises(ValueError, match="identyfikator"):
            LockedEntity(table=bad, state_column="status", legality_unenforced="test")
