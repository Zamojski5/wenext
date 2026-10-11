"""`execution_attempts` in PostgreSQL — the row that says a writing action was really attempted.

    "immutable schematy ActionProposal / ExecutionIntent / AutomationRun z payload hash" (L5198)
    "maksymalnie jedna automatyczna próba submitu na ExecutionIntent" (L2821)
    "SUBMIT_STARTED / UNKNOWN → brak automatycznego retry" (L2813-2814, L5335)

D-020 settles the naming: `AutomationRun` is a TYPE over the `execution_attempts` table, not a
table of its own — the chain `automation_runs` does not occur in the roadmap even once. Three
column names differ from the field names and the mapping lives in exactly one place, `_run_of`
and the INSERT below: `AutomationRun.execution_attempt_id` is `id`, `AutomationRun.revision_id`
is `proposal_revision_id`, `AutomationRun.state` is `status`.

FOUR THINGS THAT ARE LOAD-BEARING HERE.

1.  **The three fields binding the run to the consent are never arguments.** `intent_id`,
    `revision_id` and `payload_hash` are copied off the proof by
    `AutomationRun.from_authorization`, which is why `record_attempt` takes the proof and not
    those three values. A run recorded against a payload nobody approved would be evidence
    pointing at something that did not happen — the same class of object as a gate announcing
    `GO` before the measurement.

2.  **One attempt per intent is an INDEX, not a code path.** `UNIQUE (intent_id)` on
    `execution_attempts` (migration 0003) is what stops the second submit; this module only
    translates the violation into a refusal with a name. The translation is matched on the
    CONSTRAINT NAME, because a primary-key collision is a different fact and must not be reported
    as "already attempted".

3.  **Every state change is a compare-and-set on the source state, and zero rows RAISES.**
    `asyncpg` reports an `UPDATE` that matched nothing as the string `"UPDATE 0"` and raises
    nothing, so a transition that trusted the driver would report a state change that never
    happened. The arrows themselves come from `contracts.transitions.require_transition`; there is
    no second copy of the machine here.

4.  **No moment comes from the process clock** — and this one is NOT MEASURED. `started_at` is
    read with `SELECT now()` inside the transaction that inserts the row and `finished_at` is
    written by the server inside the statement, which is true of the code as written and is
    checked by nothing. `approval/store.py` has a grep test for the same rule; this package does
    not, and saying so here is cheaper than a docstring that promises a guard nobody built. If you
    edit this file, keep the property by hand.

WHAT THIS MODULE CANNOT DO, WRITTEN DOWN RATHER THAN LEFT SILENT (the D-019 pattern).
**No attempt writes an audit row.** `control_plane.audit.AUDIT_KINDS` is a CLOSED vocabulary owned
by another package and it holds no value for "an execution attempt was recorded", "a submit
started" or "an attempt finished". Inventing one is an edit to `audit.py`, which this package does
not own. So the audit trail of a writing action currently ends at `EXECUTION_INTENT_CONSUMED`,
written by `approval/store.py`, and the attempt itself is visible only in `execution_attempts`.
This is a GAP, not a design, and it closes the day the vocabulary gains something like
`EXECUTION_ATTEMPT_RECORDED`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import asyncpg
from community_os_contracts.enums import ExecutionAttemptState
from community_os_contracts.execution import AutomationRun, SideEffectAuthorization
from community_os_contracts.transitions import (
    EXECUTION_ATTEMPT_TRANSITIONS,
    require_transition,
)

#: The UNIQUE index of migration 0003 that carries L2821. Matched by name so that a collision on
#: any OTHER constraint is re-raised instead of being reported as "this intent already has an
#: attempt" — a refusal that names the wrong cause sends the reader looking in the wrong place.
ONE_ATTEMPT_PER_INTENT_INDEX = "execution_attempts_one_per_intent"


class ExecutionStoreError(Exception):
    """Base for every refusal of this module. Never a return value that reads like success."""


class AttemptAlreadyRecorded(ExecutionStoreError):
    """This intent already has an attempt. L2821: at most one automatic submit per intent."""


class AttemptTransitionRefused(ExecutionStoreError):
    """The state CAS matched no row: the attempt moved on, or it is not there."""


_ATTEMPT_COLUMNS = """
    id, organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
    attempt_no, status, adapter_version, started_at, finished_at
"""

_INSERT_ATTEMPT_SQL = f"""
INSERT INTO execution_attempts
    (id, organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
     attempt_no, status, adapter_version, started_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
RETURNING {_ATTEMPT_COLUMNS}
"""

#: The state CAS. The source state is inside the WHERE, so the check and the write cannot be
#: separated by anything; zero rows is a refusal.
_TRANSITION_ATTEMPT_SQL = f"""
UPDATE execution_attempts
SET status = $4
WHERE id = $1 AND organization_id = $2 AND status = $3
RETURNING {_ATTEMPT_COLUMNS}
"""

#: The same CAS, plus the moment the attempt stopped. `finished_at` is `now()` on the server and
#: never a value carried in from the caller: the schema has
#: `CHECK (finished_at IS NULL OR finished_at >= started_at)` and `started_at` was written by the
#: same clock.
_FINISH_ATTEMPT_SQL = f"""
UPDATE execution_attempts
SET status = $4, finished_at = now()
WHERE id = $1 AND organization_id = $2 AND status = $3
RETURNING {_ATTEMPT_COLUMNS}
"""

_ATTEMPT_BY_ID_SQL = f"""
SELECT {_ATTEMPT_COLUMNS}
FROM execution_attempts
WHERE id = $1 AND organization_id = $2
"""


def _run_of(row: asyncpg.Record) -> AutomationRun:
    """One `execution_attempts` row as the contracts type. The only transcription of it."""
    return AutomationRun(
        execution_attempt_id=row["id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        intent_id=row["intent_id"],
        revision_id=row["proposal_revision_id"],
        payload_hash=row["payload_hash"],
        attempt_no=row["attempt_no"],
        state=ExecutionAttemptState(row["status"]),
        adapter_version=row["adapter_version"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class ExecutionStore:
    """Reads and writes `execution_attempts`. Holds a pool, never a connection.

    Same rule as `jobs.store.JobStore` and `approval.store.ApprovalStore`: every method acquires a
    connection, uses it and gives it back, so nothing here keeps a transaction open across an
    `await` the caller controls.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def database_now(self) -> datetime:
        """The database clock, for callers that have to compare a value against a stored deadline.

        `guard.require_authorization` takes `now` as an argument precisely so that this is the
        only place the answer comes from.
        """
        async with self._pool.acquire() as connection:
            return await connection.fetchval("SELECT now()")

    async def record_attempt(
        self,
        *,
        authorization: SideEffectAuthorization,
        organization_id: UUID,
        workspace_id: UUID,
        attempt_no: int,
        adapter_version: str,
        attempt_id: UUID | None = None,
    ) -> AutomationRun:
        """Write the `PREPARED` row for one authorized action and return it as read back.

        There is no `intent_id`, no `revision_id` and no `payload_hash` parameter, and that is the
        whole of rule 1 in the module docstring: those three come out of the proof through
        `AutomationRun.from_authorization` and a caller cannot name different ones.

        A second attempt for the same intent is refused by `UNIQUE (intent_id)` — in PostgreSQL,
        which is the only place a refusal survives a restart and a second process (L2821).
        """
        async with self._pool.acquire() as connection, connection.transaction():
            moment = await connection.fetchval("SELECT now()")
            run = AutomationRun.from_authorization(
                authorization,
                execution_attempt_id=attempt_id or uuid4(),
                organization_id=organization_id,
                workspace_id=workspace_id,
                attempt_no=attempt_no,
                state=ExecutionAttemptState.PREPARED,
                adapter_version=adapter_version,
                started_at=moment,
            )
            try:
                row = await connection.fetchrow(
                    _INSERT_ATTEMPT_SQL,
                    run.execution_attempt_id,
                    run.organization_id,
                    run.workspace_id,
                    run.intent_id,
                    run.revision_id,
                    run.payload_hash,
                    run.attempt_no,
                    run.state.value,
                    run.adapter_version,
                    run.started_at,
                )
            except asyncpg.UniqueViolationError as violation:
                if violation.constraint_name != ONE_ATTEMPT_PER_INTENT_INDEX:
                    raise
                raise AttemptAlreadyRecorded(
                    f"ExecutionIntent {run.intent_id} ma już próbę wykonania. Jeden intent = "
                    "najwyżej jedna automatyczna próba submitu (L2821). Ta odmowa pochodzi z "
                    "indeksu w bazie, nie z obiektu w pamięci — przeżywa restart i drugi proces"
                ) from violation
        return _run_of(row)

    async def move_attempt(
        self, run: AutomationRun, *, target: ExecutionAttemptState
    ) -> AutomationRun:
        """Move one attempt along the machine of L2601-2605, under a CAS on its source state.

        The arrow is checked by `contracts.transitions.require_transition` BEFORE any statement is
        sent, so an illegal transition never reaches PostgreSQL. Zero updated rows is a REFUSAL:
        the row moved under our hands and `"UPDATE 0"` from the driver would otherwise read as a
        state change that never happened.

        A transition into a state the machine treats as terminal also writes `finished_at`; the
        rest do not. The set of terminal states is read off the transition table itself
        (`require_transition` refuses every arrow out of them), so there is no second list here to
        drift apart from it.
        """
        require_transition(run.state, target)
        statement = _FINISH_ATTEMPT_SQL if _is_terminal(target) else _TRANSITION_ATTEMPT_SQL
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                statement,
                run.execution_attempt_id,
                run.organization_id,
                run.state.value,
                target.value,
            )
        if row is None:
            raise AttemptTransitionRefused(
                f"próba wykonania {run.execution_attempt_id} nie jest już w stanie "
                f"{run.state.value} — zero zaktualizowanych wierszy przy przejściu do "
                f"{target.value}. To ODMOWA, nie sukces: albo wiersza nie ma, albo zmienił się "
                "pod ręką. Odczytaj go ponownie i zdecyduj"
            )
        return _run_of(row)

    async def fetch_run(self, attempt_id: UUID, *, organization_id: UUID) -> AutomationRun | None:
        """One attempt of THIS organization, read from the row. The scope is required."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(_ATTEMPT_BY_ID_SQL, attempt_id, organization_id)
        return _run_of(row) if row is not None else None


def _is_terminal(state: ExecutionAttemptState) -> bool:
    """Whether the machine of L2601-2605 draws no arrow out of this state.

    Read off `EXECUTION_ATTEMPT_TRANSITIONS` rather than written out as a second list: a state
    added to the table with an empty successor set becomes terminal here without anyone
    remembering to edit this file.
    """
    return not EXECUTION_ATTEMPT_TRANSITIONS[state]
