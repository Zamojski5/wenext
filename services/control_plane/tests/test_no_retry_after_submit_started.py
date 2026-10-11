"""No automatic retry from a state that forbids one — independently of the attempt limit (L2673).

    "Dla side effectu zewnętrznego nadal obowiązuje `SUBMIT_STARTED → UNKNOWN → reconciliation`,
     bez automatycznego ponowienia." (L2673)
    "`SUBMIT_STARTED` zużywa budżet" (L1562), "`UNKNOWN` zużywa budżet i tworzy alert, nie kolejną
     próbę" (L5337)

WHY THIS IS PARAMETERIZED OVER ALL EIGHT STATES AND NOT OVER THE THREE THAT MATTER. A test that
only fed the forbidding states in would pass just as well against a reaper that never retries
anything — the mechanism it claims to measure would be indistinguishable from a much worse one.
Both halves have to be pinned: the three that must NOT get another attempt, and the five that
MUST, from the same code path, in the same conditions.

AND WHY THE LIMIT IS FIVE HERE. This is the trap the red team found four times in this repository:
a test case that never reaches the condition it is named after, because something earlier catches
it. With `attempt_limit == 1` every one of these eight cases would terminalize on the ATTEMPT
LIMIT, the retry rule would never be consulted, and all eight assertions would hold for the wrong
reason. So the limit is 5, the dying attempt is number 1, and the test asserts that gap
explicitly before it asserts anything else.

The state is written into `job_attempts.failure_class` with raw SQL, standing in for the writer
that records it in production: the execution path of F4 (`contracts.authorize_and_consume` records
the state and consumes the intent BEFORE the submit). F1 has no side effect to record and D-013
says so, which is exactly why this evidence has to be simulated here rather than produced.
"""

from __future__ import annotations

import asyncio
import pathlib
import random
from uuid import uuid4

import pytest
from community_os_contracts import ExecutionAttemptState
from community_os_contracts.enums import STATES_FORBIDDING_AUTOMATIC_RETRY
from community_os_contracts.execution import may_retry_automatically
from control_plane.jobs import reaper as reaper_module
from control_plane.jobs.reaper import JobLeaseReaper, ReapOutcome
from control_plane.jobs.store import JobStore

#: SCENARIUSZ 4: niejednoznaczny submit. Stan zapisany przez workera, nie limit prób, decyduje
#: o tym, czy wolno spróbować drugi raz.
pytestmark = [pytest.mark.db, pytest.mark.p0]

INSTANT_LEASE_SECONDS = 0.4

#: Every value of the machine from L2601-2605, taken FROM the enum rather than typed out. A
#: hand-written list here would stop covering the machine the moment somebody adds a ninth state,
#: and the test would keep passing.
ALL_EXECUTION_STATES = tuple(ExecutionAttemptState)

#: High enough that no case below can be caught by the attempt limit instead of the retry rule.
LIMIT_WELL_ABOVE_THE_FIRST_ATTEMPT = 5


class _BottomOfTheInterval(random.Random):
    """Full jitter that always draws the bottom of `[0, ceiling]` — see `test_attempt_limit.py`."""

    def uniform(self, a: float, b: float) -> float:  # opis w docstringu klasy
        return a


async def _claim_one(pool, tenant, *, attempt_limit: int):
    store = JobStore(pool)
    await store.enqueue(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="fixture.retry-rule",
        routing_key="execution.browser",
        priority=0,
        attempt_limit=attempt_limit,
        idempotency_key=f"retry-rule-{uuid4()}",
    )
    batch = await store.claim_batch(
        organization_id=tenant["organization"],
        worker=uuid4(),
        limit=1,
        lease_seconds=INSTANT_LEASE_SECONDS,
    )
    assert len(batch.claims) == 1, batch.explain()
    return batch.claims[0]


def test_exactly_three_states_forbid_an_automatic_retry() -> None:
    """Eight states, three of which forbid a retry — counted, not assumed.

    The count is asserted for the reason KONWENCJE.md gives: `all([])` is `True`, so a predicate
    over a set is worthless until the size of the set is checked separately. If
    `STATES_FORBIDDING_AUTOMATIC_RETRY` ever became empty, every "no retry" assertion in this file
    would still pass and the queue would happily re-submit a post that may already be published.
    """
    assert len(ALL_EXECUTION_STATES) == 8, (
        f"maszyna ExecutionAttempt ma {len(ALL_EXECUTION_STATES)} stanów, a L2601-2605 opisuje "
        "osiem. Parametryzacja poniżej przestała pokrywać maszynę"
    )
    forbidden = [state for state in ALL_EXECUTION_STATES if not may_retry_automatically(state)]
    assert len(forbidden) == 3, (
        f"stanów zakazujących automatycznego ponowienia jest {len(forbidden)}: {forbidden}. "
        "Ma być dokładnie trzy (L2673, L5337)"
    )
    assert frozenset(forbidden) == STATES_FORBIDDING_AUTOMATIC_RETRY, (
        "predykat may_retry_automatically i zbiór STATES_FORBIDDING_AUTOMATIC_RETRY się "
        "rozjechały — a to ma być jedno źródło prawdy"
    )
    allowed = [state for state in ALL_EXECUTION_STATES if may_retry_automatically(state)]
    assert len(allowed) == 5, allowed


def test_the_reaper_keeps_no_second_list_of_states_forbidding_retry() -> None:
    """The reaper asks `contracts`, and holds no copy of the answer.

    This is the Definition of Done's grep, written as a test so that it runs. A second list of
    those state names inside the reaper would be a second answer to "may this be retried", and
    the two would drift — which is how a post gets published twice.
    """
    source = pathlib.Path(reaper_module.__file__).read_text(encoding="utf-8")
    assert "may_retry_automatically" in source, (
        "reaper nie woła may_retry_automatically z contracts — więc decyzję o ponowieniu "
        "podejmuje gdzieś sam"
    )
    for state in STATES_FORBIDDING_AUTOMATIC_RETRY:
        assert state.value not in source, (
            f"reaper.py wymienia stan {state.value} z nazwy. Lista stanów zakazujących "
            "ponowienia mieszka w contracts.enums i nigdzie więcej"
        )


@pytest.mark.parametrize(
    "state", ALL_EXECUTION_STATES, ids=[state.value for state in ALL_EXECUTION_STATES]
)
async def test_no_replacement_attempt_for_states_forbidding_retry(
    pool, tenant, state: ExecutionAttemptState
) -> None:
    """The dying attempt's recorded state decides, and the limit is nowhere near being reached."""
    claim = await _claim_one(pool, tenant, attempt_limit=LIMIT_WELL_ABOVE_THE_FIRST_ATTEMPT)

    # THE GUARD AGAINST MEASURING NOTHING: this case must reach the retry rule, which it can only
    # do while the attempt limit still has room.
    assert claim.attempt_no == 1
    assert claim.attempt_no < LIMIT_WELL_ABOVE_THE_FIRST_ATTEMPT, (
        "ten przypadek testowy dałby się złapać limitowi prób, a nie regule ponowienia — "
        "mierzyłby wtedy zupełnie inny warunek"
    )

    async with pool.acquire() as connection:
        # The worker recorded how far it got, then died. Raw SQL because F1 has no writer of this
        # column (D-013); F4's execution path is the one that will have it.
        await connection.execute(
            "UPDATE job_attempts SET failure_class = $2 WHERE attempt_id = $1",
            claim.attempt_id,
            state.value,
        )

    await asyncio.sleep(INSTANT_LEASE_SECONDS + 0.4)
    report = await JobLeaseReaper(
        pool, organization_id=tenant["organization"], jitter=_BottomOfTheInterval()
    ).reap_once()

    assert len(report.reaped) == 1, report.explain()
    reaped = report.reaped[0]
    assert reaped.recorded_state is state, (
        f"reaper przeczytał stan {reaped.recorded_state} zamiast {state}. Zamykając próbę "
        "NADPISAŁ to, co worker zdążył zapisać — a to jedyny dowód, jak daleko doszedł"
    )

    async with pool.acquire() as connection:
        attempts = [
            row["attempt_no"]
            for row in await connection.fetch(
                "SELECT attempt_no FROM job_attempts WHERE job_id = $1 ORDER BY attempt_no",
                claim.job_id,
            )
        ]
        job_status = await connection.fetchval(
            "SELECT status FROM jobs WHERE id = $1", claim.job_id
        )
        audit = await connection.fetch(
            "SELECT kind, payload->>'reason_code' AS reason_code, "
            "payload->>'from_state' AS from_state FROM audit_events WHERE subject_ref = $1",
            f"job:{claim.job_id}",
        )

    if may_retry_automatically(state):
        assert reaped.outcome is ReapOutcome.RETRY_SCHEDULED, (
            f"stan {state.value} POZWALA na automatyczne ponowienie (L2809-2811), a reaper go "
            f"nie dał: {reaped.outcome}. Test parametryzuje się po wszystkich ośmiu stanach "
            "właśnie dlatego, że reaper, który nie ponawia nigdy, przeszedłby połowę tych "
            "asercji"
        )
        assert attempts == [1, 2]
        assert job_status == "WAITING_RETRY"
        assert [row["reason_code"] for row in audit] == ["LEASE_EXPIRED"]
    else:
        assert reaped.outcome is ReapOutcome.TERMINALIZED_RETRY_FORBIDDEN, (
            f"stan {state.value} ZAKAZUJE automatycznego ponowienia (L2673), a reaper "
            f"zdecydował {reaped.outcome}. Side effect mógł się już wykonać po drugiej stronie"
        )
        assert reaped.replacement_attempt_no is None
        assert attempts == [1], (
            f"job z zakazanym ponowieniem dostał próby {attempts}. Zakaz obowiązuje NIEZALEŻNIE "
            f"od limitu prób — tu limit to {LIMIT_WELL_ABOVE_THE_FIRST_ATTEMPT} i jest wolne "
            "miejsce"
        )
        assert job_status == "FAILED"
        assert [row["kind"] for row in audit] == ["JOB_STATE_CHANGED"]
        assert [row["reason_code"] for row in audit] == ["RETRY_FORBIDDEN"]
        assert [row["from_state"] for row in audit] == [state.value]


async def test_a_queue_level_failure_class_is_not_read_as_an_execution_state(pool, tenant) -> None:
    """`LEASE_EXPIRED` says nothing about a side effect, so it must not block the retry.

    The reaper writes that class itself when the dying attempt left none. If it parsed as an
    execution state, the reaper's own bookkeeping would be read as the worker's report about the
    outside world — and the SECOND expiry of every job would terminalize it, which looks exactly
    like a working attempt limit until somebody counts the attempts.
    """
    claim = await _claim_one(pool, tenant, attempt_limit=LIMIT_WELL_ABOVE_THE_FIRST_ATTEMPT)
    await asyncio.sleep(INSTANT_LEASE_SECONDS + 0.4)
    reaper = JobLeaseReaper(
        pool, organization_id=tenant["organization"], jitter=_BottomOfTheInterval()
    )
    first = await reaper.reap_once()
    assert first.reaped[0].outcome is ReapOutcome.RETRY_SCHEDULED
    assert first.reaped[0].recorded_state is None

    async with pool.acquire() as connection:
        closed = await connection.fetchval(
            "SELECT failure_class FROM job_attempts WHERE attempt_id = $1", claim.attempt_id
        )
    assert closed == reaper_module.LEASE_EXPIRED_FAILURE_CLASS
    assert reaper_module._recorded_execution_state(closed) is None, (
        f"{closed!r} daje się przeczytać jako stan wykonania — a to zapis kolejki o wygasłym "
        "lease'ie, nie raport o side effekcie"
    )
