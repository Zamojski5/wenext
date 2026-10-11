"""`AuditWriter` — one entry point into `audit_events`, inside the caller's transaction (L5195).

Three failures this file is written against, each of which has a green-looking shape:

1.  **A writer that opens its own connection passes every "the entry exists" test and records
    audit for commands that FAILED.** The row is committed independently, so a rollback of the
    domain command leaves an audit trail describing something that never happened — evidence
    pointing the wrong way, which is worse than no evidence. `AuditWriter` therefore takes an
    open `asyncpg.Connection` and refuses one that carries no transaction.
2.  **`audit_events` is append-only** (0001, TRUNCATE closed in 0002), so a secret written there
    once cannot be removed by any ordinary path. Hence the closed list of `kind` values, the
    allowlist of payload keys, and the scan of serialized values modelled on
    `mcp_sidecar.capability.forbidden_field_violations`: the scan looks at the SERIALIZATION,
    not at a list of known key names, because the problem is the value, whatever it is called.
3.  **"No exception was raised" is not "the row is gone".** Every assertion below counts rows.

The counting rule of this repository applies here too: the committed half of the transaction
test runs FIRST, so "zero rows after the rollback" cannot pass because the writer never worked.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from control_plane.audit import (
    ALLOWED_PAYLOAD_KEYS,
    AUDIT_KINDS,
    AuditWriter,
    ForbiddenAuditEntry,
    forbidden_payload_violations,
)
from control_plane.locking import JOBS, apply_transition

#: NO module-level `pytest.mark.db` here, and that is deliberate — the same reasoning as in
#: `services/db_schema/tests/test_enum_parity.py`. `tools/dev/run_all_tests.sh` runs this package
#: with `-m "not db"`, so a marker on the whole module would deselect the tests below that need
#: no database at all, and a guard that only fires when somebody remembers to start PostgreSQL is
#: a guard that has quietly stopped firing. The tests that do need a connection carry the marker
#: one by one.


def test_the_closed_lists_are_not_empty() -> None:
    """Counting before predicates, at the level of the vocabulary itself.

    An empty `AUDIT_KINDS` would make "every kind is allowed" false for nothing and "no kind is
    allowed" true for everything, and every loop below would iterate zero times and pass.
    """
    assert len(AUDIT_KINDS) >= 4, sorted(AUDIT_KINDS)
    assert len(ALLOWED_PAYLOAD_KEYS) >= 4, sorted(ALLOWED_PAYLOAD_KEYS)
    assert all(kind == kind.upper() for kind in AUDIT_KINDS), sorted(AUDIT_KINDS)


@pytest.mark.db
async def test_audit_entry_and_command_share_one_transaction(pool, tenant, insert_job) -> None:
    """The entry commits with the domain command and disappears with its rollback.

    Both halves are here on purpose and in this order. The rollback half alone is satisfied by
    a writer that never writes anything at all, which is why the commit half runs first and
    asserts the row is really there.
    """
    committed_marker = f"job:{uuid.uuid4()}"
    rolled_back_marker = f"job:{uuid.uuid4()}"
    committed_job = await insert_job()
    rolled_back_job = await insert_job()

    async with pool.acquire() as connection:
        async with connection.transaction():
            await AuditWriter(connection).record(
                kind="JOB_STATE_CHANGED",
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                subject_ref=committed_marker,
                actor_ref=f"operator:{tenant['operator']}",
                payload={"from_state": "READY", "to_state": "RUNNING"},
            )
            assert (
                await apply_transition(
                    connection,
                    entity=JOBS,
                    entity_id=committed_job,
                    expected_lock_version=1,
                    source="READY",
                    target="RUNNING",
                )
                == 2
            )

        committed_rows = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE subject_ref = $1", committed_marker
        )
        assert committed_rows == 1, (
            f"udana komenda zostawiła {committed_rows} wpisów audytu — bez tej połowy test "
            "niżej przechodzi także dla writera, który nigdy nic nie zapisuje"
        )

        with pytest.raises(RuntimeError, match="celowo"):
            async with connection.transaction():
                await AuditWriter(connection).record(
                    kind="JOB_STATE_CHANGED",
                    organization_id=tenant["organization"],
                    workspace_id=tenant["workspace"],
                    subject_ref=rolled_back_marker,
                    actor_ref=f"operator:{tenant['operator']}",
                    payload={"from_state": "READY", "to_state": "RUNNING"},
                )
                await apply_transition(
                    connection,
                    entity=JOBS,
                    entity_id=rolled_back_job,
                    expected_lock_version=1,
                    source="READY",
                    target="RUNNING",
                )
                raise RuntimeError("celowo przerwana komenda domenowa")

        rolled_back_rows = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE subject_ref = $1", rolled_back_marker
        )
        assert rolled_back_rows == 0, (
            f"nieudana komenda zostawiła {rolled_back_rows} wpisów audytu. Wpis audytu ma "
            "znikać razem z komendą — inaczej dziennik opisuje zdarzenie, które się nie stało"
        )

        state = await connection.fetchrow(
            "SELECT status, lock_version FROM jobs WHERE id = $1", rolled_back_job
        )
        assert (state["status"], state["lock_version"]) == ("READY", 1), dict(state)


@pytest.mark.db
async def test_a_writer_without_an_open_transaction_refuses(pool, tenant) -> None:
    """The mechanism that makes the test above a guarantee rather than a coincidence.

    Outside a transaction asyncpg autocommits every statement, so the audit row would survive
    the failure of whatever came next. `record()` therefore asks the connection whether a
    transaction is open and refuses when it is not, instead of quietly writing a row that will
    outlive its command.
    """
    async with pool.acquire() as connection:
        assert not connection.is_in_transaction()
        with pytest.raises(ForbiddenAuditEntry, match="transakcj"):
            await AuditWriter(connection).record(
                kind="JOB_STATE_CHANGED",
                organization_id=tenant["organization"],
                subject_ref="job:bez-transakcji",
                actor_ref="operator:test",
            )
        remaining = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE subject_ref = $1", "job:bez-transakcji"
        )
    assert remaining == 0, remaining


@pytest.mark.db
async def test_update_delete_and_truncate_are_all_blocked(pool, tenant) -> None:
    """Three operations, three separate transactions, three separate assertions.

    Separate connections because a failed statement aborts the whole transaction: chaining them
    would mean the second and third rejections were the first one's fallout rather than
    findings of their own. TRUNCATE is the one that was actually missing — 0001's trigger is
    `FOR EACH ROW`, and TRUNCATE has no rows to look at, so the whole log could be emptied by a
    single statement with nothing protesting (fixed in 0002 with a statement-level trigger).
    """
    marker = f"job:{uuid.uuid4()}"
    async with pool.acquire() as connection, connection.transaction():
        event_id = await AuditWriter(connection).record(
            kind="JOB_STATE_CHANGED",
            organization_id=tenant["organization"],
            subject_ref=marker,
            actor_ref="operator:test",
        )
    assert isinstance(event_id, int), type(event_id)

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await connection.execute(
                "UPDATE audit_events SET kind = 'JOB_LEASE_EXPIRED' WHERE id = $1", event_id
            )

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await connection.execute("DELETE FROM audit_events WHERE id = $1", event_id)

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await connection.execute("TRUNCATE audit_events")

    async with pool.acquire() as connection:
        survived = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE id = $1", event_id
        )
    assert survived == 1, (
        f"wpis {event_id} nie przeżył trzech prób modyfikacji — asercja o wyjątkach jest bez "
        "wartości, jeżeli wiersz i tak zniknął"
    )


@pytest.mark.db
async def test_a_forbidden_payload_key_is_rejected_before_the_insert(pool, tenant) -> None:
    """A key outside the allowlist stops the entry, and the row count proves nothing was added.

    `audit_events` cannot be edited or deleted, so this validation is the last place where a
    field nobody reviewed can be kept out. The assertion counts rows: `pytest.raises` alone
    would also pass for a writer that inserted the row and then raised.
    """
    marker = f"job:{uuid.uuid4()}"
    assert "cdp_url" not in ALLOWED_PAYLOAD_KEYS

    async with pool.acquire() as connection:
        before = await connection.fetchval("SELECT count(*) FROM audit_events")
        async with connection.transaction():
            with pytest.raises(ForbiddenAuditEntry, match="cdp_url"):
                await AuditWriter(connection).record(
                    kind="JOB_STATE_CHANGED",
                    organization_id=tenant["organization"],
                    subject_ref=marker,
                    actor_ref="operator:test",
                    payload={"from_state": "READY", "cdp_url": "http://127.0.0.1:9222"},
                )
        after = await connection.fetchval("SELECT count(*) FROM audit_events")

    assert after == before, f"odrzucony payload dopisał {after - before} wierszy"


@pytest.mark.db
async def test_an_unknown_kind_is_rejected_before_the_insert(pool, tenant) -> None:
    """The `kind` vocabulary is closed, and a typo is a refusal rather than a new category.

    An open vocabulary makes the audit log unqueryable in the only way that matters: a reader
    filtering by kind cannot tell "no such events" from "somebody spelled it differently".
    """
    async with pool.acquire() as connection:
        before = await connection.fetchval("SELECT count(*) FROM audit_events")
        async with connection.transaction():
            with pytest.raises(ForbiddenAuditEntry, match="JOB_STATE_CHANGE"):
                await AuditWriter(connection).record(
                    kind="JOB_STATE_CHANGE",  # brak końcowego D
                    organization_id=tenant["organization"],
                    subject_ref="job:literówka",
                    actor_ref="operator:test",
                )
        after = await connection.fetchval("SELECT count(*) FROM audit_events")
    assert after == before, after - before


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({"reason_code": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"}, "argon2"),
        ({"reason_code": "wenext_operator_session=abc"}, "sesji"),
        ({"reason_code": "Authorization: Bearer abc"}, "Authorization"),
        ({"reason_code": "-----BEGIN PRIVATE KEY-----"}, "prywatny"),
    ],
)
@pytest.mark.db
async def test_a_secret_shaped_value_is_rejected_even_under_an_allowed_key(
    pool, tenant, payload: dict[str, str], expected_fragment: str
) -> None:
    """The scan looks at the VALUE, exactly like the sidecar's `forbidden_field_violations`.

    An allowlist of keys alone is not enough: every value here sits under `reason_code`, which
    is an allowed key, and every one of them would be unremovable once written.
    """
    assert set(payload) <= ALLOWED_PAYLOAD_KEYS, sorted(payload)
    violations = forbidden_payload_violations(payload)
    assert violations, f"skan nie zauważył {payload}"

    async with pool.acquire() as connection:
        before = await connection.fetchval("SELECT count(*) FROM audit_events")
        async with connection.transaction():
            with pytest.raises(ForbiddenAuditEntry):
                await AuditWriter(connection).record(
                    kind="APPROVAL_DECISION_RECORDED",
                    organization_id=tenant["organization"],
                    subject_ref="rewizja:test",
                    actor_ref="operator:test",
                    payload=payload,
                )
        after = await connection.fetchval("SELECT count(*) FROM audit_events")
    assert after == before, after - before
    assert expected_fragment.lower() in " ".join(violations).lower(), violations


@pytest.mark.db
async def test_there_is_one_writer_and_the_payload_round_trips(pool, tenant) -> None:
    """A written entry comes back with the payload it was given, under the tenant it was given.

    Without this the file could be satisfied entirely by refusals — a writer that rejects
    everything passes every negative test in it.
    """
    marker = f"rewizja:{uuid.uuid4()}"
    payload = {"payload_hash": "a" * 64, "decision": "APPROVED"}

    async with pool.acquire() as connection:
        async with connection.transaction():
            event_id = await AuditWriter(connection).record(
                kind="APPROVAL_DECISION_RECORDED",
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                subject_ref=marker,
                actor_ref=f"operator:{tenant['operator']}",
                payload=payload,
            )
        row = await connection.fetchrow(
            "SELECT organization_id, workspace_id, kind, subject_ref, payload::text AS payload "
            "FROM audit_events WHERE id = $1",
            event_id,
        )

    assert row is not None
    assert row["organization_id"] == tenant["organization"]
    assert row["workspace_id"] == tenant["workspace"]
    assert row["kind"] == "APPROVAL_DECISION_RECORDED"
    assert row["subject_ref"] == marker
    assert "APPROVED" in row["payload"], row["payload"]
