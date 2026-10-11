"""Optimistic locking for state transitions — ONE statement, zero rows means CONFLICT (L5192).

The statement, and the reason it is one statement:

    UPDATE <table>
       SET <state> = $4, lock_version = lock_version + 1
     WHERE <id> = $1 AND lock_version = $2 AND <state> = $3
    RETURNING lock_version

Everything is inside it — the identity, the version guard, the source-state guard and the
version bump. A separate `UPDATE` for the version, or an `AFTER` trigger doing the bump, reopens
the window this is meant to close: two transactions both pass the check and the second
overwrites the first, leaving a row that looks perfectly consistent afterwards.

**Zero updated rows is a CONFLICT, never a success.** This is written down in this repository's
conventions because it has already happened in database code: `asyncpg.Connection.execute` on an
`UPDATE ... WHERE lock_version = $1` that matches nothing returns the STRING `"UPDATE 0"` and
raises NOTHING. A command that treats that as success reports a state change that never
happened. Hence `fetchrow` plus `RETURNING`: no row means no update, and no update raises
`OptimisticLockConflict`.

**The arrows come from `contracts.transitions`, not from a second copy of the condition.** That
module is the only place in the repository where the six machines of sections 14.8 and 16.1
live, and `apply_transition` asks `require_transition` rather than restating any of it.

WHAT IS NOT ENFORCED, AND WHY IT IS WRITTEN DOWN HERE RATHER THAN LEFT SILENT (the D-019
pattern): `jobs` is the only table in migrations 0001-0003 that carries `lock_version`, and the
job lifecycle has NO transition table anywhere in this project. The roadmap enumerates no job
statuses at all — `status` appears in the `jobs` column list at L3831 and nowhere as a value
list — and `services/db_schema/tests/test_enum_parity.py::
test_contracts_have_not_grown_a_second_source_of_job_statuses` deliberately FAILS if `contracts`
grows a `JobState`/`JobStatus`. Inventing the arrows here would be inventing a requirement, the
same move migration 0003 refused for `scope_level`. So for `jobs` this module enforces the
version and the source state, and NOT the legality of the arrow; `LockedEntity.legality_unenforced`
carries that sentence, and a test asserts it is there and trips the day contracts models the
machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from community_os_contracts.transitions import TRANSITION_TABLES, machine_of, require_transition

#: Table and column names are interpolated into the SQL — PostgreSQL cannot take an identifier
#: through a parameter placeholder. So the only defence is to refuse anything that is not a plain
#: lowercase identifier, at construction time rather than at query time.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


class OptimisticLockConflict(Exception):
    """The update matched no row: the version, the state or the identity moved on.

    A conflict, never a failure of the caller's logic — the caller re-reads and decides. What it
    must NOT be is a return value that reads like success.
    """


class RefusedTransition(Exception):
    """Refused before the database was touched, for a reason `contracts` cannot express.

    Three of them: a transition to the same state, a state value that does not belong to the
    entity's machine, and an entity whose table has no version column to lock on.
    """


@dataclass(frozen=True)
class LockedEntity:
    """Which table a transition is applied to, and which machine governs it.

    `machine` names a key of `contracts.transitions.TRANSITION_TABLES`, so the arrows stay in
    contracts. Exactly one of `machine` / `legality_unenforced` must be set: an entity that
    neither delegates to a machine nor says out loud that it cannot is the silence this project
    keeps paying for.
    """

    table: str
    state_column: str
    id_column: str = "id"
    #: `None` means this table has no version column in the applied schema, so it cannot be
    #: locked optimistically at all — `apply_transition` refuses before running any SQL rather
    #: than sending an `UPDATE` that would fail on an undefined column.
    version_column: str | None = "lock_version"
    machine: str | None = None
    #: Written reason plus roadmap anchor, for an entity whose machine no document defines.
    legality_unenforced: str | None = None

    def __post_init__(self) -> None:
        names = {
            "table": self.table,
            "state_column": self.state_column,
            "id_column": self.id_column,
        }
        if self.version_column is not None:
            names["version_column"] = self.version_column
        for field_name, value in names.items():
            if not isinstance(value, str) or not _IDENTIFIER.match(value):
                raise ValueError(
                    f"{field_name}={value!r} nie jest prostym identyfikatorem SQL. Nazwy tabel "
                    "i kolumn są wstawiane do zapytania tekstowo, bo placeholder ich nie "
                    "przenosi — walidacja jest tu jedyną obroną"
                )

        if (self.machine is None) == (self.legality_unenforced is None):
            raise ValueError(
                f"{self.table}: ustaw DOKŁADNIE jedno z machine / legality_unenforced. Encja "
                "bez maszyny i bez zapisanego powodu milczy o tym, że legalność przejścia nie "
                "jest sprawdzana"
            )
        if self.machine is not None and self.machine not in TRANSITION_TABLES:
            raise ValueError(
                f"Nieznana maszyna stanów {self.machine!r}. Tabele przejść mieszkają w "
                f"community_os_contracts.transitions: {', '.join(sorted(TRANSITION_TABLES))}"
            )


#: The queue record. THE ONLY table of migrations 0001-0003 with `lock_version`
#: (`db/migrations/0003_f1_kernel.sql:95`), and the reason this module exists (L5192).
JOBS = LockedEntity(
    table="jobs",
    state_column="status",
    legality_unenforced=(
        "Dokument nie rysuje ANI JEDNEGO przejścia statusu joba: `status` występuje w liście "
        "kolumn `jobs` (L3831) i nigdzie jako lista wartości, a contracts trzymają wyłącznie "
        "cztery stany WAITING_* (L2144). Test "
        "db_schema/tests/test_enum_parity.py::test_contracts_have_not_grown_a_second_source_"
        "of_job_statuses celowo pada, gdyby contracts dostały JobState. Egzekwowane tu są więc "
        "wersja i stan źródłowy; legalność strzałki NIE JEST EGZEKWOWANA — do rozstrzygnięcia "
        "razem z maszyną kolejki."
    ),
)

#: The execution attempt: a machine `contracts` really does model (L2601-2605) — and a table
#: WITHOUT a version column in 0003, so it is registered as unlockable rather than pretended.
#: Both facts are load-bearing: the first makes the legality gate testable, the second makes the
#: refusal explicit instead of an `UndefinedColumnError` at run time.
EXECUTION_ATTEMPTS = LockedEntity(
    table="execution_attempts",
    state_column="status",
    version_column=None,
    machine="ExecutionAttempt",
)

#: Every entity this module knows, by table name.
LOCKED_ENTITIES: dict[str, LockedEntity] = {
    JOBS.table: JOBS,
    EXECUTION_ATTEMPTS.table: EXECUTION_ATTEMPTS,
}


def _state_text(value: Any, role: str) -> str:
    """The stored form of a state value: the enum's `value`, or the string itself."""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str) and value:
        return value
    raise RefusedTransition(
        f"{role}={value!r} nie jest stanem: oczekiwany element StrEnum z contracts albo "
        "niepusty napis"
    )


def _require_legal_arrow(entity: LockedEntity, source: Any, target: Any) -> None:
    """Ask `contracts` whether this arrow exists. Raises before anything touches the database."""
    if not isinstance(source, StrEnum) or not isinstance(target, StrEnum):
        raise RefusedTransition(
            f"{entity.table} podlega maszynie {entity.machine}, więc stany muszą być elementami "
            "odpowiedniego StrEnum z community_os_contracts — po napisach nie da się sprawdzić, "
            "o którą maszynę chodzi"
        )
    if machine_of(source) != entity.machine:
        raise RefusedTransition(
            f"stan {source!r} należy do maszyny {machine_of(source)}, a {entity.table} podlega "
            f"{entity.machine}. Sprawdzenie przejścia w cudzej tabeli powiedziałoby „w porządku” "
            "o czymś, czego nikt nie pytał"
        )
    # The arrows themselves. This call is the whole delegation to contracts.
    require_transition(source, target)


async def apply_transition(
    connection: Any,
    *,
    entity: LockedEntity,
    entity_id: UUID,
    expected_lock_version: int,
    source: Any,
    target: Any,
) -> int:
    """Move one row from `source` to `target` under `expected_lock_version`. Returns the new one.

    Order of the guards matters and is asserted by the tests: legality is checked BEFORE the
    connection is used at all, so an illegal transition never reaches PostgreSQL — an `UPDATE`
    that matched nothing is indistinguishable from one that was never sent, which would make
    "rejected before touching the database" unprovable.
    """
    source_text = _state_text(source, "source")
    target_text = _state_text(target, "target")

    if source_text == target_text:
        raise RefusedTransition(
            f"{entity.table}: przejście {source_text} → {target_text} nie zmienia stanu. "
            "Dopasowałoby wiersz, podbiło lock_version i zwróciło liczbę wyglądającą jak postęp "
            "— żadna maszyna z sekcji 14.8 ani 16.1 nie ma pętli własnej"
        )

    if entity.machine is not None:
        _require_legal_arrow(entity, source, target)

    if entity.version_column is None:
        raise RefusedTransition(
            f"tabela {entity.table} nie ma kolumny lock_version w zastosowanym schemacie "
            "(migracje 0001-0003), więc optimistic locking nie ma na czym się oprzeć. Dołożenie "
            "kolumny jest migracją, nie zmianą w tym module"
        )

    statement = (
        f"UPDATE {entity.table} "
        f"SET {entity.state_column} = $4, {entity.version_column} = {entity.version_column} + 1 "
        f"WHERE {entity.id_column} = $1 "
        f"AND {entity.version_column} = $2 "
        f"AND {entity.state_column} = $3 "
        f"RETURNING {entity.version_column}"
    )
    row = await connection.fetchrow(
        statement, entity_id, expected_lock_version, source_text, target_text
    )
    if row is None:
        raise OptimisticLockConflict(
            f"konflikt wersji na {entity.table}: żaden wiersz nie ma jednocześnie "
            f"{entity.id_column}={entity_id}, {entity.version_column}={expected_lock_version} "
            f"i {entity.state_column}={source_text!r}. Zero zaktualizowanych wierszy to KONFLIKT "
            "— wiersz zmienił się pod ręką albo nie istnieje. Odczytaj go ponownie i zdecyduj"
        )
    return int(row[entity.version_column])
