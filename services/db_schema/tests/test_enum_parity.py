"""The database and `contracts` must name the same states with the same words.

Two vocabularies for one notion is how this repository has already been hurt: `__all__` listed
twenty-seven names that had never existed, D-005 cited a constant under a name the code did not
have, and three documents pointed at `ZAKAZANE_FLAGI` while the code said `FORBIDDEN_FLAGS`.
Every one of those survived because nothing compared the two sides. This file compares them.

Three kinds of parity are checked, and the count of pairs is asserted before any pair is
compared — a mapping that lost an entry would otherwise reduce this file to an expensive
no-op, which is the same defect as a gate with an empty list of required controls (D-011):

1. native PostgreSQL enum types created by migration 0001 against the `StrEnum`s in
   `community_os_contracts.enums`,
2. the closed value lists that migrations 0001 and 0003 keep as `text` + a NAMED `CHECK`
   against the lists in `db_schema.expected_schema`,
3. the composition of the `jobs.status` list, half of which is derived from
   `contracts.JobWaitingState` rather than written out — so that a waiting state added to the
   contracts fails here instead of quietly becoming a value the database refuses.

Why `text` + `CHECK` at all, rather than more native enums: `ALTER TYPE ... ADD VALUE` cannot
run inside a transaction block and cannot be reverted, so a native enum makes a misnamed state
a one-way migration. Migration 0001 already used the pattern for
`capsule_mode_transitions.reason`; 0003 uses it for every new list.

CORRECTION OF 2026-08-31, and the reason this file was reopened. Every CHECK lookup here used
to filter `pg_constraint` on `conname` alone. `conname` is unique per RELATION, not per
database, so a constraint of the right name standing on a COMPLETELY DIFFERENT table passed the
parity check while the column it guarded had lost its CHECK entirely. Measured on a mutated
copy of the schema — `artifacts_kind_is_known` dropped from `artifacts` and re-created under the
same name on a decoy table — the suite reported `54 passed` and `artifacts.kind` accepted any
string. The lookup is `(conname, conrelid)` now, and `CheckedValueList.table`, which used to be
read only inside an error message, is the second half of the key.
"""

from __future__ import annotations

import re

import community_os_contracts as contracts
import pytest
from community_os_contracts.enums import ExecutionAttemptState, JobWaitingState
from community_os_contracts.execution import ActionProposalState
from db_schema.expected_schema import (
    JOB_LIFECYCLE_STATUSES,
    NATIVE_ENUM_PARITY,
    enumerated_checks,
)

#: NO module-level `pytest.mark.db` here, deliberately. `tools/dev/run_all_tests.sh` runs this
#: package with `-m "not db"`, so a marker on the whole module would deselect
#: `test_contracts_have_not_grown_a_second_source_of_job_statuses` from `make test-all` — and
#: that test needs no database at all. A guard against a second source of truth that only fires
#: when somebody remembers to start PostgreSQL is a guard that stops firing.
#: The four tests that do need a connection carry the marker one by one.

#: `pg_get_constraintdef` renders a value list as `ARRAY['A'::text, 'B'::text]`, so the literals
#: come out of the rendered definition rather than out of the migration file. Reading the
#: catalog and not the file is deliberate: the file is what somebody wrote, the catalog is what
#: the server enforces, and only the second one can refuse a row.
_LITERAL = re.compile(r"'([^']*)'::text")


def job_statuses() -> frozenset[str]:
    """The full `jobs.status` list: five lifecycle values plus every `WAITING_*` from contracts.

    The composition is a function, not a constant, because the second half has to be READ from
    `contracts` on every run. Copying the four waiting states into `expected_schema` would have
    created the second vocabulary this whole file exists to prevent.
    """
    waiting = {state.value for state in JobWaitingState}
    assert len(waiting) == 4, f"JobWaitingState ma {len(waiting)} wartości, L2144 wymienia cztery"
    assert len(JOB_LIFECYCLE_STATUSES) == 5, JOB_LIFECYCLE_STATUSES
    combined = frozenset(JOB_LIFECYCLE_STATUSES | waiting)
    assert len(combined) == 9, (
        f"statusy joba nakładają się: {sorted(JOB_LIFECYCLE_STATUSES & waiting)}"
    )
    return combined


async def _enum_values(connection, type_name: str) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT e.enumlabel FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = $1
        """,
        type_name,
    )
    values = {row["enumlabel"] for row in rows}
    # A type that does not exist returns NO rows, and `set() == set()` would make two missing
    # things look equal. This is the guard that turns "the type is gone" into a failure.
    assert values, f"typ wyliczeniowy {type_name} nie istnieje w bazie albo nie ma wartości"
    return values


async def _check_values(connection, constraint: str, table: str) -> set[str]:
    """The literals of CHECK `constraint` AS IT STANDS ON `table` — both halves of the key.

    `table` is not decoration and not only a nicer error message. Until 2026-08-31 this helper
    filtered on `conname` alone, and `conname` is unique per RELATION in PostgreSQL, not per
    database. A constraint of the right name sitting on a completely different table therefore
    satisfied every parity assertion in this file while the column it was meant to guard had no
    CHECK left at all.

    Measured, because a claim about detection power is worth nothing unmeasured: dropping
    `artifacts_kind_is_known` from `artifacts` and re-creating it under the same name on a decoy
    table left this suite at `54 passed`, with `artifacts.kind` accepting any string whatsoever.
    That is the shape of a false pass — the report says the closed list is enforced, the database
    enforces nothing — and it is why the lookup is `(conname, conrelid)` now.

    `conrelid` is resolved through `regclass`, so a table that does not exist raises here instead
    of quietly matching nothing.
    """
    definition = await connection.fetchval(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conname = $1 AND contype = 'c' AND conrelid = ('public.' || $2)::regclass
        """,
        constraint,
        table,
    )
    if not definition:
        # Where the name DID turn up matters to whoever reads the failure: "constraint missing"
        # and "constraint moved to another table" are different defects with different fixes,
        # and the second one is the one this repository just discovered it could not see.
        elsewhere = await connection.fetch(
            """
            SELECT conrelid::regclass::text AS present_on FROM pg_constraint
            WHERE conname = $1 AND contype = 'c'
            """,
            constraint,
        )
        raise AssertionError(
            f"nie ma nazwanego CHECK-a {constraint} NA TABELI {table}. "
            f"Ta sama nazwa występuje na: {[row['present_on'] for row in elsewhere] or 'nigdzie'}. "
            "Nazwy nadawane są w migracji właśnie po to, żeby ten test mógł go SZUKAĆ, ale "
            "sama nazwa nie jest tożsamością: conname jest unikalne w obrębie RELACJI, więc "
            "CHECK przeniesiony na inną tabelę zaliczał parytet, gdy pilnowana kolumna nie "
            "miała już żadnej kontroli"
        )
    values = set(_LITERAL.findall(definition))
    assert values, f"CHECK {constraint} na {table} nie zawiera ani jednego literału: {definition}"
    return values


@pytest.mark.db
async def test_native_enum_types_match_the_contracts(pool) -> None:
    assert len(NATIVE_ENUM_PARITY) == 9, (
        f"lista par typ↔kontrakt ma {len(NATIVE_ENUM_PARITY)} pozycji, oczekiwano dziewięciu"
    )
    async with pool.acquire() as connection:
        for type_name, contract_name in NATIVE_ENUM_PARITY:
            contract_enum = getattr(contracts, contract_name, None)
            assert contract_enum is not None, (
                f"`community_os_contracts` nie eksportuje {contract_name} — nazwa-widmo w teście "
                "jest tą samą chorobą, co nazwa-widmo w __all__"
            )
            expected = {member.value for member in contract_enum}
            assert expected, f"{contract_name} nie ma ani jednej wartości"
            actual = await _enum_values(connection, type_name)
            assert actual == expected, (
                f"typ {type_name} i {contract_name} rozjechały się: "
                f"tylko w bazie {sorted(actual - expected)}, "
                f"tylko w kontraktach {sorted(expected - actual)}"
            )


@pytest.mark.db
async def test_named_check_lists_match_the_declared_lists(pool) -> None:
    checks = enumerated_checks(job_statuses())
    # Twelve since 0023 (the kind of a publication request); eleven since P7 (0018 added the
    # membership role and the two data-job statuses); eight before that. The number is asserted
    # rather than derived so that a list ADDED to `expected_schema` without a thought about this
    # parity test fails here, which is exactly what it is for.
    assert len(checks) == 12, f"zadeklarowanych list CHECK: {len(checks)}, oczekiwano dwunastu"
    # Every entry has to name a table, and no two entries may be the same (constraint, table)
    # pair. Asserted rather than assumed: `table` was dead data in this file until 2026-08-31,
    # and an entry with an empty or duplicated table would put it straight back to being dead.
    # Eleven distinct tables for twelve constraints — `artifacts` carries two of them (kind, acl).
    assert all(check.table for check in checks), [check for check in checks if not check.table]
    pairs = {(check.constraint, check.table) for check in checks}
    assert len(pairs) == 12, f"pary (CHECK, tabela) nie są rozłączne: {sorted(pairs)}"
    assert len({check.table for check in checks}) == 11, [check.table for check in checks]
    async with pool.acquire() as connection:
        for check in checks:
            assert check.values, f"{check.constraint} deklaruje pustą listę wartości"
            actual = await _check_values(connection, check.constraint, check.table)
            assert actual == set(check.values), (
                f"CHECK {check.constraint} na {check.table} rozjechał się z expected_schema: "
                f"tylko w bazie {sorted(actual - set(check.values))}, "
                f"tylko w deklaracji {sorted(set(check.values) - actual)}"
            )


@pytest.mark.db
async def test_state_lists_taken_straight_from_the_contracts(pool) -> None:
    """Three lists whose single source of truth is a contracts enum, not `expected_schema`.

    Writing these out in `expected_schema` would have meant three copies of the same knowledge:
    the document, the contracts and the schema. Here the schema is compared to the contracts
    directly, so adding a state in one place breaks the other.
    """
    # Constraint -> (table it stands on, values the contracts require). The table is part of the
    # tuple for the same reason it is part of `CheckedValueList`: `conname` alone is not an
    # identity in `pg_constraint`, and a constraint of the right name on the wrong table used to
    # satisfy this loop while the guarded column had no CHECK at all.
    pairs = {
        "action_proposal_revisions_state_is_known": (
            "action_proposal_revisions",
            {s.value for s in ActionProposalState},
        ),
        "execution_attempts_status_is_known": (
            "execution_attempts",
            {s.value for s in ExecutionAttemptState},
        ),
        "capsule_mode_transitions_reason_check": (
            "capsule_mode_transitions",
            {r.value for r in contracts.InteractiveReason},
        ),
        # Migration 0004 (observation and judgment). Every closed list there is a contracts enum.
        "communities_platform_is_known": (
            "communities",
            {p.value for p in contracts.ObservationPlatform},
        ),
        "communities_access_mode_is_known": (
            "communities",
            {a.value for a in contracts.AccessMode},
        ),
        "thread_judgments_decision_is_known": (
            "thread_judgments",
            {d.value for d in contracts.JudgmentDecision},
        ),
        "judgment_feedback_verdict_is_known": (
            "judgment_feedback",
            {v.value for v in contracts.JudgmentFeedbackVerdict},
        ),
        "thread_metric_snapshots_score_unknown_reason_is_known": (
            "thread_metric_snapshots",
            {r.value for r in contracts.UnknownReason},
        ),
        "thread_metric_snapshots_comment_count_unknown_reason_is_known": (
            "thread_metric_snapshots",
            {r.value for r in contracts.UnknownReason},
        ),
        # Migration 0005 — the comment's score pair (voice card, etap 3 M0).
        "observed_comments_score_unknown_reason_is_known": (
            "observed_comments",
            {r.value for r in contracts.UnknownReason},
        ),
        # Migration 0006 — the pilot's reaction kind is the generator's closed list.
        "pilot_reactions_decision_is_known": (
            "pilot_reactions",
            {k.value for k in contracts.ReactionKind},
        ),
    }
    assert len(pairs) == 11, pairs
    assert len(pairs["action_proposal_revisions_state_is_known"][1]) == 6, (
        "L2773-2775: sześć stanów"
    )
    assert len(pairs["execution_attempts_status_is_known"][1]) == 8, "L2601-2605: osiem stanów"
    assert len(pairs["capsule_mode_transitions_reason_check"][1]) == 6, "sekcja 24.1: sześć powodów"
    assert len(pairs["communities_platform_is_known"][1]) == 2, "dwa adaptery istnieją"
    assert len(pairs["communities_access_mode_is_known"][1]) == 5, "22.5: pięć trybów dostępu"
    assert len(pairs["thread_judgments_decision_is_known"][1]) == 3, "§3: ENGAGE / IGNORE / WAIT"
    assert len(pairs["judgment_feedback_verdict_is_known"][1]) == 3, "AGREE / DISAGREE / IRRELEVANT"
    assert len(pairs["thread_metric_snapshots_score_unknown_reason_is_known"][1]) == 9, (
        "UnknownReason"
    )
    assert len({table for table, _ in pairs.values()}) == 9, pairs

    async with pool.acquire() as connection:
        for constraint, (table, expected) in pairs.items():
            actual = await _check_values(connection, constraint, table)
            assert actual == expected, (
                f"CHECK {constraint} na {table} rozjechał się z kontraktami: "
                f"tylko w bazie {sorted(actual - expected)}, "
                f"tylko w kontraktach {sorted(expected - actual)}"
            )


@pytest.mark.db
async def test_job_status_list_in_the_database_is_the_composed_one(pool) -> None:
    """The `jobs.status` list is the one place where the two halves meet.

    The failure this guards against is narrow and real: somebody adds `WAITING_RATE_LIMIT` to
    `JobWaitingState` for the dispatcher, the database keeps refusing it, and the job that hits
    that state fails to be written with a CHECK violation nobody expects — at run time, in the
    queue, rather than here.
    """
    expected = job_statuses()
    async with pool.acquire() as connection:
        actual = await _check_values(connection, "jobs_status_is_known", "jobs")
    assert actual == set(expected), (
        f"jobs_status_is_known rozjechał się ze złożeniem: "
        f"tylko w bazie {sorted(actual - set(expected))}, "
        f"tylko w złożeniu {sorted(set(expected) - actual)}"
    )


def test_contracts_have_not_grown_a_second_source_of_job_statuses() -> None:
    """If `contracts` ever gains a `JobState`/`JobStatus` enum, this composition is obsolete.

    Deliberately a test that fails on somebody else's reasonable change. Right now the roadmap
    enumerates no job statuses at all (`status` appears in the `jobs` column list at L3831 and
    nowhere as a value list) and the contracts hold only the four `WAITING_*` states, so half of
    the list is spelled out in `expected_schema`. The day that stops being true, the schema has
    a second source of truth and nobody would notice — unless something says so out loud.
    """
    exported = set(contracts.__all__)
    assert exported, "`community_os_contracts.__all__` jest puste"
    rival = {name for name in exported if re.fullmatch(r"Job(State|Status)", name)}
    assert not rival, (
        f"contracts eksportuje teraz {sorted(rival)} — od tej chwili to jest źródło prawdy dla "
        "statusów joba. Zastąp JOB_LIFECYCLE_STATUSES tym enumem i uzgodnij CHECK w 0003, "
        "zamiast trzymać dwie listy"
    )
