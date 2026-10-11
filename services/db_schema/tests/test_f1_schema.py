"""Migration 0003 — the schema of the E1.F1 correctness kernel.

What this file proves, in the order the Definition of Done asks for it:

* the job queue exists with every column the document names (L3823-3838), one active lease per
  job, and an attempt number that cannot be reused — the schema half of "dwa workery nie
  przejmują tego samego rekordu" (L5211),
* the approval chain is immutable where the document says immutable: editing the content of a
  revision is refused by a trigger, because "każda edycja tworzy nową rewizję i unieważnia
  poprzedni approval" (L2795, L5218),
* at most one `execution_attempts` row per `execution_intents` row, enforced by a UNIQUE INDEX
  and not by a code path — "maksymalnie jedna automatyczna próba submitu na ExecutionIntent"
  (L2821),
* artifacts carry a checksum, an ACL and a retention deadline that has NO default, so nothing
  gets stored without somebody deciding how long it lives (L5196),
* the tenant columns and composite foreign keys exist AND are enforced with row level security
  switched off — the literal wording of DoD L5215,
* and, in the last section of this file, six invariants that migration 0003 argued for in a
  comment and that no test measured until 2026-08-31 — including the one the migration's own
  comment calls out by name: an attempt pointing at a job that does not exist.

The trap this file is written against, stated once: a test shaped
"for every table that has a `workspace_id`, assert it has a composite FK" passes over a schema
where no table has that column. It is `all([])` with SQL in front of it, and it has been the
shape of two of this project's false passes. Every loop below therefore runs over a
hand-written list from `db_schema.expected_schema` whose length is asserted first, and a second
test walks the whole schema in the opposite direction to catch a table that grew the column
without joining the list.
"""

from __future__ import annotations

import hashlib
import uuid

import asyncpg
import pytest
import pytest_asyncio
from db_schema.expected_schema import (
    ACTION_PROPOSAL_REVISION_COLUMNS,
    EXECUTION_INTENT_COLUMNS,
    JOB_ATTEMPT_FIELDS,
    JOBS_COLUMNS,
    MIXED_SCOPE_TABLES,
    OBSERVE_TRIPLE_PARENTS,
    QUEUE_PAIR_KEYS,
    WORKSPACE_TABLES,
    WORKSPACE_TRIPLE_PARENTS,
)

pytestmark = pytest.mark.db

#: An Argon2id encoded hash shaped like the real thing; `operators.credential_hash` has a CHECK
#: on the prefix (D-016), so a placeholder string would be refused.
ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$0000000000000000000000000000"


def payload_hash(text: str) -> str:
    """The hex SHA-256 the schema expects — same shape as `ApprovalHashInput.payload_hash`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def world(pool):
    """A whole tenant: organization, workspace, operator, social account, browser profile.

    Defined here and not in `conftest.py` because that file belongs to another package of this
    phase. Every identifier is fresh per test, so the tests do not interfere with each other in
    a database that lives for the whole run.
    """
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('fixture-f1') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            organization_id,
        )
        operator_id = await connection.fetchval(
            "INSERT INTO operators (organization_id, credential_hash) VALUES ($1, $2) RETURNING id",
            organization_id,
            ARGON2_HASH,
        )
        account_id = await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'fixture', $2) RETURNING id
            """,
            organization_id,
            f"konto-{uuid.uuid4()}",
        )
        profile_id = await connection.fetchval(
            """
            INSERT INTO browser_profiles
                (organization_id, social_account_id, state, profile_build_marker, profile_platform)
            VALUES ($1, $2, 'AVAILABLE', 'chrome-for-testing/153.0.8010.12', 'linux-arm64')
            RETURNING id
            """,
            organization_id,
            account_id,
        )
    return {
        "organization": organization_id,
        "workspace": workspace_id,
        "operator": operator_id,
        "account": account_id,
        "profile": profile_id,
    }


async def _insert_job(connection, world, *, workspace_id=None, status: str = "READY") -> uuid.UUID:
    return await connection.fetchval(
        """
        INSERT INTO jobs
            (organization_id, workspace_id, job_type, routing_key, priority, payload_ref,
             dispatch_version, status, not_before, attempt_limit, idempotency_key, lock_version)
        VALUES ($1, $2, 'FIXTURE', 'fixture.key', 100, NULL, 1, $3, now(), 5, $4, 1)
        RETURNING id
        """,
        world["organization"],
        workspace_id,
        status,
        f"idem-{uuid.uuid4()}",
    )


async def _insert_attempt(connection, world, job_id, *, attempt_no: int) -> uuid.UUID:
    # `attempt_no` and `lease_generation` get separate placeholders on purpose: reusing one
    # made PostgreSQL deduce `integer versus bigint` for the same parameter and refuse to
    # prepare the statement.
    return await connection.fetchval(
        """
        INSERT INTO job_attempts
            (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
             lease_owner, status)
        VALUES ($1, $2, $3, 1, $4, $5, 'RUNNING')
        RETURNING attempt_id
        """,
        world["organization"],
        job_id,
        attempt_no,
        attempt_no,
        uuid.uuid4(),
    )


async def _insert_proposal_chain(connection, world, *, workspace_id=None, content: str = "tresc"):
    """proposal -> revision -> approval -> intent, all in one workspace. Returns their ids."""
    workspace_id = workspace_id or world["workspace"]
    proposal_id = await connection.fetchval(
        """
        INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)
        VALUES ($1, $2, $3) RETURNING id
        """,
        world["organization"],
        workspace_id,
        world["account"],
    )
    digest = payload_hash(content)
    revision_id = await connection.fetchval(
        """
        INSERT INTO action_proposal_revisions
            (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
             source_fingerprint, state, expires_at)
        VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'fingerprint-1', 'APPROVED',
                now() + interval '1 hour')
        RETURNING id
        """,
        world["organization"],
        workspace_id,
        proposal_id,
        '{"text": "' + content + '"}',
        digest,
    )
    approval_id = await connection.fetchval(
        """
        INSERT INTO approval_decisions
            (organization_id, workspace_id, proposal_revision_id, decision,
             approved_payload_hash, approved_by)
        VALUES ($1, $2, $3, 'APPROVED', $4, $5)
        RETURNING id
        """,
        world["organization"],
        workspace_id,
        revision_id,
        digest,
        world["operator"],
    )
    intent_id = await connection.fetchval(
        """
        INSERT INTO execution_intents
            (organization_id, workspace_id, proposal_revision_id, approval_decision_id,
             one_time_grant_hash, expires_at, status, payload_hash, account_id)
        VALUES ($1, $2, $3, $4, $5, now() + interval '1 hour', 'ISSUED', $6, $7)
        RETURNING id
        """,
        world["organization"],
        workspace_id,
        revision_id,
        approval_id,
        payload_hash(f"grant-{uuid.uuid4()}"),
        digest,
        world["account"],
    )
    return {
        "proposal": proposal_id,
        "revision": revision_id,
        "approval": approval_id,
        "intent": intent_id,
        "hash": digest,
    }


async def _columns(connection, table: str) -> dict[str, str]:
    rows = await connection.fetch(
        """
        SELECT column_name, is_nullable FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    columns = {row["column_name"]: row["is_nullable"] for row in rows}
    # A table that does not exist yields an EMPTY dict, not an error. Without this guard every
    # "the column is there" assertion below would read `KeyError` as a missing column and every
    # "the column list is a superset" assertion would silently be a comparison against nothing.
    assert columns, f"tabeli {table} nie ma w schemacie public — migracja 0003 jej nie utworzyła"
    return columns


# ---------------------------------------------------------------------------
# The job queue — L3820-3838, DoD L5211-5214
# ---------------------------------------------------------------------------


async def test_jobs_has_every_column_the_document_lists(pool) -> None:
    assert len(JOBS_COLUMNS) == 13, (
        f"L3823-3835 wymienia trzynaście kolumn, lista ma {len(JOBS_COLUMNS)}"
    )
    async with pool.acquire() as connection:
        columns = await _columns(connection, "jobs")
    missing = [name for name in JOBS_COLUMNS if name not in columns]
    assert not missing, f"jobs bez kolumn z L3823-3835: {missing}"


async def test_job_workspace_column_stays_nullable(pool, world) -> None:
    """L3825 says "workspace_id nullable" and L3884 depends on it.

    "Job organizacyjny z `workspace_id IS NULL` przechodzi wyłącznie przez org-scope policy."
    Tightening this column to NOT NULL would make an organization-scoped job unwritable, which
    is why `jobs` is in `MIXED_SCOPE_TABLES` and not in `WORKSPACE_TABLES`.
    """
    async with pool.acquire() as connection:
        columns = await _columns(connection, "jobs")
        assert columns["workspace_id"] == "YES", (
            "jobs.workspace_id jest NOT NULL — L3825 i L3884 wymagają, żeby job organizacyjny "
            "dał się zapisać bez workspace'u"
        )
        assert columns["organization_id"] == "NO", "jobs.organization_id musi być NOT NULL"
        organization_scoped = await _insert_job(connection, world, workspace_id=None)
        workspace_scoped = await _insert_job(connection, world, workspace_id=world["workspace"])
        assert organization_scoped is not None
        assert workspace_scoped is not None


async def test_job_cannot_point_at_a_workspace_of_another_organization(pool, world) -> None:
    async with pool.acquire() as connection:
        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-f1') RETURNING id"
        )
        other_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization_id,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _insert_job(connection, world, workspace_id=other_workspace_id)


async def test_job_attempts_and_job_leases_record_every_field_from_l3838(pool) -> None:
    assert len(JOB_ATTEMPT_FIELDS) == 13, JOB_ATTEMPT_FIELDS
    async with pool.acquire() as connection:
        attempts = await _columns(connection, "job_attempts")
        leases = await _columns(connection, "job_leases")
    for table, columns in (("job_attempts", attempts), ("job_leases", leases)):
        missing = [name for name in JOB_ATTEMPT_FIELDS if name not in columns]
        assert not missing, f"{table} bez pól wymaganych przez L3838: {missing}"


async def test_two_attempts_cannot_share_an_attempt_number(pool, world) -> None:
    """`UNIQUE(job_id, attempt_no)` — stated outright at L3838.

    This is the schema half of the attempt limit: if two rows could claim attempt 3, counting
    attempts against `attempt_limit` (D-017: five) would be counting rows that mean nothing.
    """
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        await _insert_attempt(connection, world, job_id, attempt_no=1)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_attempt(connection, world, job_id, attempt_no=1)


async def test_only_one_active_lease_per_job(pool, world) -> None:
    """The partial unique index that makes "dwa workery nie przejmują tego samego rekordu"
    (L5211) an invariant of the database rather than a property of the dispatcher's code."""
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        first_attempt = await _insert_attempt(connection, world, job_id, attempt_no=1)
        second_attempt = await _insert_attempt(connection, world, job_id, attempt_no=2)
        await connection.execute(
            """
            INSERT INTO job_leases
                (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                 lease_generation, lease_owner, status, expires_at)
            VALUES ($1, $2, $3, 1, 1, 1, $4, 'ACTIVE', now() + interval '60 seconds')
            """,
            world["organization"],
            job_id,
            first_attempt,
            uuid.uuid4(),
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO job_leases
                    (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                     lease_generation, lease_owner, status, expires_at)
                VALUES ($1, $2, $3, 2, 1, 2, $4, 'ACTIVE', now() + interval '60 seconds')
                """,
                world["organization"],
                job_id,
                second_attempt,
                uuid.uuid4(),
            )


async def test_lease_must_expire_after_it_was_acquired(pool, world) -> None:
    """`CHECK (expires_at > acquired_at)`. A lease already expired when written is a lease the
    reaper reclaims instantly, which turns the attempt limit into a redispatch loop (L5214)."""
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        attempt_id = await _insert_attempt(connection, world, job_id, attempt_no=1)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO job_leases
                    (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                     lease_generation, lease_owner, status, acquired_at, expires_at)
                VALUES ($1, $2, $3, 1, 1, 1, $4, 'ACTIVE', now(), now() - interval '1 second')
                """,
                world["organization"],
                job_id,
                attempt_id,
                uuid.uuid4(),
            )


async def test_a_running_attempt_cannot_already_be_finished(pool, world) -> None:
    """`(status = 'RUNNING') = (completed_at IS NULL)`.

    An attempt that reports RUNNING while carrying a completion timestamp is the row shape that
    made `successful_cycles` grow before the resume test in GATE-3 — accounting that counts a
    finished thing as still in flight, or the other way round.
    """
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status, completed_at)
                VALUES ($1, $2, 1, 1, 1, $3, 'RUNNING', now())
                """,
                world["organization"],
                job_id,
                uuid.uuid4(),
            )


# ---------------------------------------------------------------------------
# The approval chain — L2795, L2821, L3686-3719, DoD L5217-5218
# ---------------------------------------------------------------------------


async def test_action_proposal_revisions_has_all_sixteen_columns(pool) -> None:
    assert len(ACTION_PROPOSAL_REVISION_COLUMNS) == 16, (
        f"L3686-3703 wymienia szesnaście kolumn, lista ma {len(ACTION_PROPOSAL_REVISION_COLUMNS)}"
    )
    async with pool.acquire() as connection:
        columns = await _columns(connection, "action_proposal_revisions")
    missing = [name for name in ACTION_PROPOSAL_REVISION_COLUMNS if name not in columns]
    assert not missing, f"action_proposal_revisions bez kolumn z L3686-3703: {missing}"


async def test_execution_intents_has_all_nine_columns(pool) -> None:
    assert len(EXECUTION_INTENT_COLUMNS) == 9, (
        f"L3707-3719 wiąże dokładnie dziewięć kolumn, lista ma {len(EXECUTION_INTENT_COLUMNS)}"
    )
    async with pool.acquire() as connection:
        columns = await _columns(connection, "execution_intents")
    missing = [name for name in EXECUTION_INTENT_COLUMNS if name not in columns]
    assert not missing, f"execution_intents bez kolumn z L3707-3719: {missing}"


async def test_revision_content_cannot_be_edited_in_place(pool, world) -> None:
    """L2795: "Każda edycja tworzy nową rewizję i unieważnia poprzedni approval."

    Editing the payload of an approved revision is the exact leak `supersede` was written to
    close on the Python side. Here the database refuses it, so a raw UPDATE — from a script, a
    console, or a route nobody reviewed — cannot slip content past a consent that already
    points at a hash (L5218).
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world)
        with pytest.raises(asyncpg.RaiseError, match="niezmienna"):
            await connection.execute(
                'UPDATE action_proposal_revisions SET payload = \'{"text": "inna"}\'::jsonb '
                "WHERE id = $1",
                chain["revision"],
            )
        with pytest.raises(asyncpg.RaiseError, match="niezmienna"):
            await connection.execute(
                "UPDATE action_proposal_revisions SET payload_hash = $2 WHERE id = $1",
                chain["revision"],
                payload_hash("inna"),
            )
        with pytest.raises(asyncpg.RaiseError, match="niezmienna"):
            await connection.execute(
                "UPDATE action_proposal_revisions SET expires_at = now() + interval '10 days' "
                "WHERE id = $1",
                chain["revision"],
            )


async def test_revision_state_can_still_move(pool, world) -> None:
    """The immutability trigger must guard the CONTENT, not freeze the row.

    Without this test the trigger could be written to reject every UPDATE, and the six-state
    machine of L2773-2775 — including `APPROVED → SUPERSEDED` — would have nowhere to live.
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world)
        await connection.execute(
            "UPDATE action_proposal_revisions SET state = 'SUPERSEDED' WHERE id = $1",
            chain["revision"],
        )
        state = await connection.fetchval(
            "SELECT state FROM action_proposal_revisions WHERE id = $1", chain["revision"]
        )
        assert state == "SUPERSEDED"


async def test_a_rejection_without_a_reason_is_refused(pool, world) -> None:
    """L5311: a rejection carries a reason, which goes into preference memory.

    A `REJECTED` row with a NULL reason is a decision nobody can learn from.
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world, content="do-odrzucenia")
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO approval_decisions
                    (organization_id, workspace_id, proposal_revision_id, decision,
                     approved_payload_hash, approved_by, reason)
                VALUES ($1, $2, $3, 'REJECTED', $4, $5, NULL)
                """,
                world["organization"],
                world["workspace"],
                chain["revision"],
                chain["hash"],
                world["operator"],
            )


async def test_one_execution_attempt_per_intent(pool, world) -> None:
    """L2821 enforced by a UNIQUE INDEX.

    "maksymalnie jedna automatyczna próba submitu na ExecutionIntent". In the Python contracts
    this is `ExecutionIntent.consume` raising on a second call — which protects one process
    holding one object. A second attempt written by another process, or by the same process
    after a restart, is stopped here or not at all.
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world, content="jednorazowy")
        await connection.execute(
            """
            INSERT INTO execution_attempts
                (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
                 attempt_no, status, adapter_version)
            VALUES ($1, $2, $3, $4, $5, 1, 'PREPARED', 'fixture-1.0.0')
            """,
            world["organization"],
            world["workspace"],
            chain["intent"],
            chain["revision"],
            chain["hash"],
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO execution_attempts
                    (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
                     attempt_no, status, adapter_version)
                VALUES ($1, $2, $3, $4, $5, 2, 'PREPARED', 'fixture-1.0.0')
                """,
                world["organization"],
                world["workspace"],
                chain["intent"],
                chain["revision"],
                chain["hash"],
            )


async def test_the_one_attempt_per_intent_rule_is_an_index_not_a_promise(pool) -> None:
    """The task says L2821 is to be enforced by an INDEX; this reads the catalog to confirm it.

    A test that only inserts twice would also pass if the rule were implemented as, say, a
    trigger that some later migration disabled. The name is asserted so the object can be
    found again by the next reader.
    """
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'execution_attempts'
              AND indexname = 'execution_attempts_one_per_intent'
            """
        )
    assert row is not None, "brak indeksu execution_attempts_one_per_intent (L2821)"
    assert "UNIQUE" in row["indexdef"], row["indexdef"]
    assert "intent_id" in row["indexdef"], row["indexdef"]


async def test_a_second_intent_cannot_reuse_a_one_time_grant_hash(pool, world) -> None:
    """ "one-time ExecutionIntent" (L2755, L5692). The grant hash is unique per organization."""
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world, content="grant")
        grant = await connection.fetchval(
            "SELECT one_time_grant_hash FROM execution_intents WHERE id = $1", chain["intent"]
        )
        second = await _insert_proposal_chain(connection, world, content="grant-drugi")
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "UPDATE execution_intents SET one_time_grant_hash = $2 WHERE id = $1",
                second["intent"],
                grant,
            )


async def test_a_consumed_intent_must_carry_the_moment_it_was_consumed(pool, world) -> None:
    """`(status = 'CONSUMED') = (consumed_at IS NOT NULL)`.

    An intent marked CONSUMED with no timestamp cannot be told apart from one whose consumption
    was never recorded — and that difference is the whole of L2821.
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world, content="zuzycie")
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "UPDATE execution_intents SET status = 'CONSUMED' WHERE id = $1", chain["intent"]
            )
        await connection.execute(
            "UPDATE execution_intents SET status = 'CONSUMED', consumed_at = now() WHERE id = $1",
            chain["intent"],
        )


# ---------------------------------------------------------------------------
# Artifacts and profile DEKs — L5196, L3914, D-018
# ---------------------------------------------------------------------------


async def _insert_artifact(connection, world, **overrides):
    values = {
        "kind": "REPORT",
        "storage_ref": f"artifacts/{uuid.uuid4()}",
        "checksum": "sha256:" + payload_hash("plik"),
        "size_bytes": 128,
        "acl": "OPERATOR_ONLY",
    }
    values.update(overrides)
    return await connection.fetchval(
        """
        INSERT INTO artifacts
            (organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, acl,
             retention_until)
        VALUES ($1, $2, $3, $4, $5, $6, $7, now() + interval '30 days')
        RETURNING id
        """,
        world["organization"],
        world["workspace"],
        values["kind"],
        values["storage_ref"],
        values["checksum"],
        values["size_bytes"],
        values["acl"],
    )


async def test_artifact_retention_deadline_is_mandatory_and_has_no_default(pool, world) -> None:
    """ "artifacts ACL i retention" (L5196).

    NOT NULL and NO DEFAULT together. A default would mean the retention of every artifact was
    decided once, in a migration, by whoever typed the number — and D-017 fixed thirty days for
    the general case and twenty-four hours for a post-login trace (L3898). Those cannot both be
    a column default, so neither is.
    """
    async with pool.acquire() as connection:
        column = await connection.fetchrow(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'artifacts'
              AND column_name = 'retention_until'
            """
        )
        assert column is not None, "brak kolumny artifacts.retention_until (L5196)"
        assert column["is_nullable"] == "NO", "artifacts.retention_until musi być NOT NULL"
        assert column["column_default"] is None, (
            f"artifacts.retention_until ma DEFAULT {column['column_default']} — retencja jest "
            "decyzją zapisującego, nie wartością wpisaną raz w migracji"
        )
        with pytest.raises(asyncpg.NotNullViolationError):
            await connection.execute(
                """
                INSERT INTO artifacts
                    (organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, acl)
                VALUES ($1, $2, 'REPORT', 'x', $3, 1, 'OPERATOR_ONLY')
                """,
                world["organization"],
                world["workspace"],
                "sha256:" + payload_hash("plik"),
            )


async def test_artifact_checksum_must_be_a_sha256_digest(pool, world) -> None:
    """A checksum column accepting anything is a column that proves nothing about the bytes."""
    async with pool.acquire() as connection:
        assert await _insert_artifact(connection, world) is not None
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_artifact(connection, world, checksum="nie-jest-suma")


async def test_artifact_acl_is_a_closed_list(pool, world) -> None:
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_artifact(connection, world, acl="EVERYONE")


async def test_artifact_producer_is_checked_whenever_it_is_present(pool, world) -> None:
    """`produced_by_execution_attempt_id` is optional, and MATCH SIMPLE makes that work.

    The subtlety worth a test rather than a comment: with MATCH SIMPLE a key is satisfied as
    soon as ANY of its columns is NULL, so an optional parent is unchecked while it is NULL —
    which is what we want — and fully checked once it is set, because the other two columns of
    the triple are NOT NULL. Asserting only the NULL case would leave the "fully checked" half
    of that sentence as a claim in a comment.
    """
    async with pool.acquire() as connection:
        second_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-art') RETURNING id",
            world["organization"],
        )
        chain = await _insert_proposal_chain(connection, world, content="producent")
        attempt_id = await connection.fetchval(
            """
            INSERT INTO execution_attempts
                (organization_id, workspace_id, intent_id, proposal_revision_id, payload_hash,
                 attempt_no, status, adapter_version)
            VALUES ($1, $2, $3, $4, $5, 1, 'CONFIRMED', 'fixture-1.0.0')
            RETURNING id
            """,
            world["organization"],
            world["workspace"],
            chain["intent"],
            chain["revision"],
            chain["hash"],
        )
        # Same organization, foreign workspace: refused, because all three columns are present.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO artifacts
                    (organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, acl,
                     produced_by_execution_attempt_id, retention_until)
                VALUES ($1, $2, 'TRACE', 'a/1', $3, 1, 'RUNNER_ONLY', $4,
                        now() + interval '1 day')
                """,
                world["organization"],
                second_workspace_id,
                "sha256:" + payload_hash("plik"),
                attempt_id,
            )
        # The matching triple goes in.
        stored = await connection.fetchval(
            """
            INSERT INTO artifacts
                (organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, acl,
                 produced_by_execution_attempt_id, retention_until)
            VALUES ($1, $2, 'TRACE', 'a/2', $3, 1, 'RUNNER_ONLY', $4, now() + interval '1 day')
            RETURNING id
            """,
            world["organization"],
            world["workspace"],
            "sha256:" + payload_hash("plik"),
            attempt_id,
        )
        assert stored is not None


async def test_an_unencrypted_trace_cannot_be_stored(pool, world) -> None:
    """L3898: a post-login trace has a short TTL, an ACL and encryption.

    The row shape this refuses is a trace filed as plaintext — the one artifact kind that can
    contain a session's whole content.
    """
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO artifacts
                    (organization_id, workspace_id, kind, storage_ref, checksum, size_bytes, acl,
                     encrypted, retention_until)
                VALUES ($1, $2, 'TRACE', 'a/3', $3, 1, 'RUNNER_ONLY', false,
                        now() + interval '1 day')
                """,
                world["organization"],
                world["workspace"],
                "sha256:" + payload_hash("plik"),
            )


async def test_profile_dek_belongs_to_exactly_one_profile(pool, world) -> None:
    """L3914 / D-018: separate DEKs per profile, and only one live DEK for a given profile.

    Two live DEKs for one profile means one of them cannot decrypt it, and nothing in the
    schema would say which. The wrapping key itself is never in this database (D-018).
    """
    async with pool.acquire() as connection:
        first = await connection.fetchval(
            """
            INSERT INTO profile_deks
                (organization_id, profile_id, wrapped_dek, wrapping_key_id, algorithm, generation)
            VALUES ($1, $2, $3, 'klucz-hosta-1', 'AES-256-GCM', 1)
            RETURNING id
            """,
            world["organization"],
            world["profile"],
            b"\x01\x02\x03",
        )
        assert first is not None
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO profile_deks
                    (organization_id, profile_id, wrapped_dek, wrapping_key_id, algorithm,
                     generation)
                VALUES ($1, $2, $3, 'klucz-hosta-1', 'AES-256-GCM', 2)
                """,
                world["organization"],
                world["profile"],
                b"\x04\x05\x06",
            )


async def test_profile_dek_cannot_be_stored_empty(pool, world) -> None:
    """An empty `wrapped_dek` is a row that looks like a key and decrypts nothing."""
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO profile_deks
                    (organization_id, profile_id, wrapped_dek, wrapping_key_id, algorithm,
                     generation)
                VALUES ($1, $2, $3, 'klucz-hosta-1', 'AES-256-GCM', 1)
                """,
                world["organization"],
                world["profile"],
                b"",
            )


# ---------------------------------------------------------------------------
# Tenant isolation — L3871-3876, DoD L5215, decision D-020
# ---------------------------------------------------------------------------


async def _composite_foreign_keys(connection, table: str) -> list[tuple[str, tuple[str, ...], str]]:
    rows = await connection.fetch(
        """
        SELECT c.conname,
               (SELECT array_agg(a.attname ORDER BY k.ord)
                  FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum) AS local_columns,
               c.confrelid::regclass::text AS parent_table
        FROM pg_constraint c
        WHERE c.conrelid = ('public.' || $1)::regclass AND c.contype = 'f'
        """,
        table,
    )
    # `regclass::text` drops the schema when it is on the search path and keeps it when it is
    # not, so the name is normalised here. Comparing the raw value would make this helper
    # depend on the connection's `search_path` — a difference that shows up as a mysterious
    # failure in one environment and nowhere else.
    return [
        (
            row["conname"],
            tuple(row["local_columns"]),
            row["parent_table"].removeprefix("public."),
        )
        for row in rows
    ]


async def test_every_workspace_table_carries_the_tenant_triple(pool) -> None:
    """L3871: a workspace table has `organization_id NOT NULL` and `workspace_id NOT NULL`.
    L3874-3876: relations use the composite key
    `(organization_id, workspace_id, parent_id) -> parent(organization_id, workspace_id, id)`.

    The list is asserted to have six entries BEFORE the loop. That assertion is the whole point
    of the test: the same loop over an empty list passes, and D-020 says exactly that
    ("lista pusta nie może przejść — `all([])` to pułapka, która trafiła tu już dwa razy").
    """
    # Six from 0003 plus eight from 0004 (observation and judgment, docs/OUTCOMES.md §5).
    assert len(WORKSPACE_TABLES) == 45, (
        f"jawna lista tabel workspace'owych ma {len(WORKSPACE_TABLES)} pozycji, oczekiwano "
        "czterdziestu pięciu (0005 dodała community_voice_cards, 0006 trzy tabele pilota, "
        "0007 trzy tabele publikacji, 0008 dwie tabele polityki publikacji, 0013 cztery tabele "
        "P1: projekty, profile osób i dwa przypięcia, 0015 dwie tabele P2: agent_sessions "
        "i session_events, 0016 dwie tabele wiedzy, 0017 trzy tabele obecności: "
        "delegacje, obudzenia i ich zdarzenia, 0022 person_voice_versions, "
        "0024 project_visits, 0028 trzy tabele dokumentów projektu: tożsamość, wersje i zdania, "
        "0035 zapytania X agenta i zebrane posty X, 0036 okazje projektu, 0037 tokeny "
        "hostowanego MCP i tożsamości z logowania OAuth, 0038 manual_publications)"
    )
    async with pool.acquire() as connection:
        for table in WORKSPACE_TABLES:
            columns = await _columns(connection, table)
            assert columns.get("organization_id") == "NO", (
                f"{table}.organization_id musi być NOT NULL (L3871)"
            )
            assert columns.get("workspace_id") == "NO", (
                f"{table}.workspace_id musi być NOT NULL (L3871)"
            )

            keys = await _composite_foreign_keys(connection, table)
            assert keys, f"{table} nie ma ANI JEDNEGO klucza obcego — a musi mieć tenantowy"
            tenant_leading = [
                key for key in keys if key[1][:2] == ("organization_id", "workspace_id")
            ]
            assert tenant_leading, (
                f"{table} nie ma klucza obcego zaczynającego się od (organization_id, "
                f"workspace_id); ma tylko: {[(name, cols) for name, cols, _ in keys]}"
            )


async def test_workspace_tables_relate_to_their_parents_through_the_full_triple(pool) -> None:
    """The TRIPLE, not the pair. D-020 is explicit: "TRÓJKA, nie para".

    Four of the six workspace tables have a workspace-scoped parent, and each of those four has
    to reach it with three columns. `action_proposals` and `artifacts` have no such parent — the
    workspace itself is their parent — so they are excluded here by name rather than by the loop
    happening to find nothing.
    """
    expected_parents = {
        "action_proposal_revisions": "action_proposals",
        "approval_decisions": "action_proposal_revisions",
        "execution_intents": "action_proposal_revisions",
        "execution_attempts": "execution_intents",
        # 0004: six more, declared once in `expected_schema` so the reverse check can use them.
        **OBSERVE_TRIPLE_PARENTS,
        # 0013: the two attachment tables of P1. `projects` and `person_profiles` hang off the
        # workspace itself and are excluded by name, like `action_proposals` above.
        **WORKSPACE_TRIPLE_PARENTS,
    }
    assert len(expected_parents) == 20, expected_parents
    async with pool.acquire() as connection:
        for table, parent in expected_parents.items():
            keys = await _composite_foreign_keys(connection, table)
            triples = [
                key
                for key in keys
                if len(key[1]) == 3
                and key[1][:2] == ("organization_id", "workspace_id")
                and key[2] == parent
            ]
            assert triples, (
                f"{table} nie wskazuje na {parent} trójką (organization_id, workspace_id, "
                f"parent_id); klucze: {[(name, cols, par) for name, cols, par in keys]}"
            )


async def test_no_undeclared_table_carries_a_workspace_column(pool) -> None:
    """The reverse direction, and the reason the two lists in `expected_schema` both exist.

    A future table that grows a `workspace_id` without joining `WORKSPACE_TABLES` would never be
    checked by the test above — it iterates the list, not the schema. So this one iterates the
    schema. Silence about a table is the failure mode; a new one must be classified, not ignored.
    """
    declared = set(WORKSPACE_TABLES) | set(MIXED_SCOPE_TABLES)
    # 0022 person_voice_versions, 0024 project_visits, 0028 trzy tabele dokumentów projektu,
    # 0035 x_watch_queries i x_trend_posts, 0036 project_opportunities, 0037 hosted_tokens i external_identities,
    # 0038 manual_publications.
    assert len(declared) == 48, declared
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT c.table_name FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND c.column_name = 'workspace_id'
              AND t.table_type = 'BASE TABLE'
            """
        )
    carrying = {row["table_name"] for row in rows}
    assert carrying, "żadna tabela nie ma kolumny workspace_id — to nie jest schemat F1"
    undeclared = carrying - declared
    assert not undeclared, (
        f"tabele z workspace_id niesklasyfikowane w expected_schema: {sorted(undeclared)}. "
        "Dopisz je do WORKSPACE_TABLES albo do MIXED_SCOPE_TABLES z powodem"
    )


async def test_row_level_security_is_off_and_composite_keys_still_hold(pool, world) -> None:
    """The literal wording of DoD L5215: "kolumny tenantowe i composite FKs istnieją, nawet gdy
    RLS jest wyłączone".

    Both halves in one test on purpose. RLS is deferred to E3 (L5206), so the isolation E1 has
    is the composite key — and a passing "no RLS" check next to a failing key check would read
    as good news.
    """
    async with pool.acquire() as connection:
        with_rls = await connection.fetch("SELECT relname FROM pg_class WHERE relrowsecurity")
        assert with_rls == [], (
            f"RLS włączone na: {[row['relname'] for row in with_rls]}. W E1 RLS nie ma (L5206), "
            "a izolacją jest composite FK"
        )

        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-rls') RETURNING id"
        )
        other_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization_id,
        )
        # Same organization, foreign workspace: the pair key to `workspaces` must refuse it.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)
                VALUES ($1, $2, $3)
                """,
                world["organization"],
                other_workspace_id,
                world["account"],
            )


async def test_child_cannot_point_at_a_parent_from_another_workspace(pool, world) -> None:
    """The triple, doing the job the pair cannot.

    Two workspaces of the SAME organization: a pair key `(organization_id, parent_id)` would let
    a revision of workspace A hang under a proposal of workspace B. The third column is what
    refuses it, and this is the test that would fail if somebody "simplified" the key.
    """
    async with pool.acquire() as connection:
        second_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-2') RETURNING id",
            world["organization"],
        )
        chain = await _insert_proposal_chain(connection, world, content="w-pierwszym")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO action_proposal_revisions
                    (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
                     source_fingerprint, state, expires_at)
                VALUES ($1, $2, $3, 2, '{}'::jsonb, $4, 'fingerprint-2', 'DRAFT',
                        now() + interval '1 hour')
                """,
                world["organization"],
                second_workspace_id,
                chain["proposal"],
                payload_hash("obcy-workspace"),
            )


async def test_child_cannot_point_at_a_parent_from_another_organization(pool, world) -> None:
    """D-020: "dziecko wskazujące na rodzica z innej organizacji jest odrzucane przez bazę,
    nie przez kod aplikacji"."""
    async with pool.acquire() as connection:
        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-org-chain') RETURNING id"
        )
        other_workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            other_organization_id,
        )
        chain = await _insert_proposal_chain(connection, world, content="u-siebie")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO action_proposal_revisions
                    (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
                     source_fingerprint, state, expires_at)
                VALUES ($1, $2, $3, 2, '{}'::jsonb, $4, 'fingerprint-3', 'DRAFT',
                        now() + interval '1 hour')
                """,
                other_organization_id,
                other_workspace_id,
                chain["proposal"],
                payload_hash("obca-organizacja"),
            )


# ===========================================================================
# Package FIX-B, 2026-08-31: six invariants that migration 0003 DECLARES IN A
# COMMENT and that nothing measured.
# ===========================================================================
#
# How they were found and why the count is exactly six: the red team removed each mechanism
# from a copy of the schema and re-ran this suite. All six removals left it at `54 passed`.
# A rule argued at length in a comment and enforced by nobody is the same defect as a gate
# announcing GO with no measurement (D-007), one layer down — the reasoning is on record, the
# measurement is not, and the next reader has no way to tell the two apart.
#
# The mutation used against each test is named in that test's docstring, and each one was seen
# FAILING before this section was accepted. A test nobody watched fail is not evidence.


async def _foreign_key_details(connection, table: str) -> list[dict]:
    """Every foreign key on `table`: name, local columns in order, parent, and MATCH TYPE.

    `_composite_foreign_keys` above deliberately stays as it is — it answers "which columns
    reach which parent" for the tenant-triple tests. This helper exists because the invariant
    below is about something that function cannot see: `confmatchtype`. PostgreSQL's default is
    MATCH SIMPLE ('s'), under which a key with ANY null column is satisfied WITHOUT the parent
    being looked up at all. That is not a footnote, it is the difference between a key that
    refuses a row and a key that waves it through.
    """
    rows = await connection.fetch(
        """
        SELECT c.conname,
               (SELECT array_agg(a.attname ORDER BY k.ord)
                  FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum) AS local_columns,
               c.confrelid::regclass::text AS parent_table,
               c.confmatchtype::text AS match_type
        FROM pg_constraint c
        WHERE c.conrelid = ('public.' || $1)::regclass AND c.contype = 'f'
        """,
        table,
    )
    # No foreign key at all on a queue table is the very defect this file measures, so an empty
    # result must never reach a comprehension that would then iterate zero times and pass.
    assert rows, f"{table} nie ma ANI JEDNEGO klucza obcego — a kolejka jobów bez nich nie jest"
    return [
        {
            "name": row["conname"],
            "columns": tuple(row["local_columns"]),
            "parent": row["parent_table"].removeprefix("public."),
            "match_type": row["match_type"],
        }
        for row in rows
    ]


async def test_queue_pair_keys_have_no_null_column_to_escape_through(pool) -> None:
    """INVARIANT 1 (0003, lines 121-126). The structural half, and the sharper one.

    The comment argues: "Trójka z NULL-owym `workspace_id` i MATCH SIMPLE byłaby GORSZA niż
    para: przy NULL-u MATCH SIMPLE uznaje klucz za spełniony BEZ SPRAWDZANIA `job_id`, czyli
    próba mogłaby wskazywać na nieistniejący job." All three queue keys are MATCH SIMPLE — that
    is PostgreSQL's default and 0003 does not override it — so the ONLY reason they are enforced
    at all is that no column in them can be NULL. This test asserts that reason, rather than
    trusting the shape of the key.

    Two assertions carry it. The columns of every key are NOT NULL, and `job_attempts` /
    `job_leases` have NO `workspace_id` column at all — so the nullable column the comment warns
    about cannot be quietly added to a key later.

    Mutation seen failing: `job_attempts` pair replaced by
    `(organization_id, workspace_id, job_id) -> jobs(organization_id, workspace_id, id)
    MATCH SIMPLE` with `workspace_id` nullable.
    """
    assert len(QUEUE_PAIR_KEYS) == 3, (
        f"jawna lista kluczy kolejki ma {len(QUEUE_PAIR_KEYS)} pozycji, oczekiwano trzech"
    )
    async with pool.acquire() as connection:
        for expected in QUEUE_PAIR_KEYS:
            columns = await _columns(connection, expected.table)
            assert "workspace_id" not in columns, (
                f"{expected.table} ma kolumnę workspace_id. Ta tabela jest ORGANIZACYJNA "
                "(0003, linie 121-126): próba należy do joba, a job może być organizacyjny. "
                "Nullowalna kolumna w kluczu pod MATCH SIMPLE spełnia klucz BEZ sprawdzenia "
                "rodzica — czyli próba mogłaby wskazywać na nieistniejący job"
            )

            keys = await _foreign_key_details(connection, expected.table)
            matching = [key for key in keys if key["parent"] == expected.parent]
            assert matching, (
                f"{expected.table} nie ma klucza obcego do {expected.parent}; ma tylko: "
                f"{[(key['name'], key['columns'], key['parent']) for key in keys]}"
            )
            assert len(matching) == 1, (
                f"{expected.table} ma {len(matching)} kluczy do {expected.parent}: {matching}"
            )
            key = matching[0]
            assert key["columns"] == expected.columns, (
                f"{expected.table} -> {expected.parent} idzie po {key['columns']}, "
                f"a niezmiennik wymaga pary {expected.columns}"
            )
            nullable = [name for name in key["columns"] if columns.get(name) != "NO"]
            assert not nullable, (
                f"kolumny {nullable} klucza {key['name']} są NULLOWALNE, a klucz jest "
                f"MATCH {key['match_type']!r}. Pod MATCH SIMPLE NULL w którejkolwiek kolumnie "
                "spełnia klucz bez sprawdzenia rodzica — dokładnie to, przed czym ostrzegają "
                "linie 121-126 migracji 0003"
            )


async def test_attempt_cannot_point_at_a_job_that_does_not_exist(pool, world) -> None:
    """INVARIANT 1, the behavioural half — and the row the comment names by hand.

    "próba mogłaby wskazywać na nieistniejący job". No test in this suite had ever written such
    a row, so the pair key could be dropped outright and the whole file stayed green.

    Mutation seen failing: `job_attempts_organization_id_job_id_fkey` dropped.
    """
    async with pool.acquire() as connection:
        nonexistent_job_id = uuid.uuid4()
        # The job really is absent — asserted, because a fixture that happened to create it
        # would turn this test into a check of nothing.
        assert (
            await connection.fetchval("SELECT count(*) FROM jobs WHERE id = $1", nonexistent_job_id)
            == 0
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status)
                VALUES ($1, $2, 1, 1, 1, $3, 'RUNNING')
                """,
                world["organization"],
                nonexistent_job_id,
                uuid.uuid4(),
            )


async def test_attempt_cannot_point_at_a_job_of_another_organization(pool, world) -> None:
    """INVARIANT 1, the second direction the package asks for: a job that EXISTS, elsewhere.

    A dangling `job_id` is caught by any foreign key at all. A job that exists in a DIFFERENT
    organization is caught only because the key is composite — and it is the case that matters
    for tenant isolation, since `job_id` is a real, resolvable identifier on the other side.
    Both directions are here on purpose: the first proves the key is enforced, the second proves
    it is enforced over the tenant.

    Mutation seen failing: `job_attempts_organization_id_job_id_fkey` dropped.
    """
    async with pool.acquire() as connection:
        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-kolejka') RETURNING id"
        )
        foreign_job_id = await connection.fetchval(
            """
            INSERT INTO jobs
                (organization_id, workspace_id, job_type, routing_key, priority, dispatch_version,
                 status, attempt_limit, idempotency_key, lock_version)
            VALUES ($1, NULL, 'FIXTURE', 'fixture.key', 100, 1, 'READY', 5, $2, 1)
            RETURNING id
            """,
            other_organization_id,
            f"idem-obcy-{uuid.uuid4()}",
        )
        assert foreign_job_id is not None

        # Our organization, their job.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status)
                VALUES ($1, $2, 1, 1, 1, $3, 'RUNNING')
                """,
                world["organization"],
                foreign_job_id,
                uuid.uuid4(),
            )

        # Their organization, our job — the same key read the other way round.
        our_job_id = await _insert_job(connection, world)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO job_attempts
                    (organization_id, job_id, attempt_no, dispatch_version, lease_generation,
                     lease_owner, status)
                VALUES ($1, $2, 1, 1, 1, $3, 'RUNNING')
                """,
                other_organization_id,
                our_job_id,
                uuid.uuid4(),
            )


async def test_lease_cannot_point_at_a_job_or_an_attempt_that_does_not_exist(pool, world) -> None:
    """INVARIANT 1 on `job_leases`, which carries TWO pair keys and had a test for neither.

    A lease is the record that says "this worker owns this job right now" (L5211). A lease row
    whose `attempt_id` resolves to nothing is a fencing token with no subject: the reaper would
    find it, the attempt it claims to fence would not exist, and the attempt limit of L5214
    would be counted over rows that mean nothing.

    Mutation seen failing: both `job_leases` foreign keys dropped.
    """
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        attempt_id = await _insert_attempt(connection, world, job_id, attempt_no=1)

        # Absent job, real attempt.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO job_leases
                    (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                     lease_generation, lease_owner, status, expires_at)
                VALUES ($1, $2, $3, 1, 1, 1, $4, 'ACTIVE', now() + interval '60 seconds')
                """,
                world["organization"],
                uuid.uuid4(),
                attempt_id,
                uuid.uuid4(),
            )

        # Real job, absent attempt.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO job_leases
                    (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
                     lease_generation, lease_owner, status, expires_at)
                VALUES ($1, $2, $3, 1, 1, 1, $4, 'ACTIVE', now() + interval '60 seconds')
                """,
                world["organization"],
                job_id,
                uuid.uuid4(),
                uuid.uuid4(),
            )


async def test_two_jobs_cannot_share_an_idempotency_key_in_one_organization(pool, world) -> None:
    """INVARIANT 2 (0003, line 98): "internal deduplication (L2820): ten sam klucz nie tworzy
    drugiego joba."

    This is the schema half of deduplication, and it was enforced by nobody's test: the UNIQUE
    could be dropped and the suite stayed at 54 passed. Deduplication living only in the
    dispatcher is deduplication that two dispatchers, or one dispatcher across a restart, do not
    have — and L5212 ("restart komponentu nie gubi taska") is about exactly that restart.

    The third insert is the half that keeps the key from being "fixed" in the other direction:
    the scope is PER ORGANIZATION, so the same key in another tenant must go in. A global
    UNIQUE would make one tenant's key collide with another's, which is a cross-tenant leak
    dressed as a correctness measure.

    Mutation seen failing: `jobs_organization_id_idempotency_key_key` dropped.
    """
    shared_key = f"idem-wspolny-{uuid.uuid4()}"
    async with pool.acquire() as connection:
        first = await connection.fetchval(
            """
            INSERT INTO jobs
                (organization_id, workspace_id, job_type, routing_key, priority, dispatch_version,
                 status, attempt_limit, idempotency_key, lock_version)
            VALUES ($1, NULL, 'FIXTURE', 'fixture.key', 100, 1, 'READY', 5, $2, 1)
            RETURNING id
            """,
            world["organization"],
            shared_key,
        )
        assert first is not None

        with pytest.raises(asyncpg.UniqueViolationError) as refused:
            await connection.execute(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority,
                     dispatch_version, status, attempt_limit, idempotency_key, lock_version)
                VALUES ($1, NULL, 'INNY', 'inna.key', 7, 1, 'READY', 5, $2, 1)
                """,
                world["organization"],
                shared_key,
            )
        # WHICH constraint refused matters: without this the test would also pass if the row had
        # been rejected by, say, the primary key, and the deduplication key were gone.
        assert refused.value.constraint_name == "jobs_organization_id_idempotency_key_key", (
            f"odrzucił inny obiekt: {refused.value.constraint_name}"
        )

        other_organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('inna-dedup') RETURNING id"
        )
        accepted = await connection.fetchval(
            """
            INSERT INTO jobs
                (organization_id, workspace_id, job_type, routing_key, priority, dispatch_version,
                 status, attempt_limit, idempotency_key, lock_version)
            VALUES ($1, NULL, 'FIXTURE', 'fixture.key', 100, 1, 'READY', 5, $2, 1)
            RETURNING id
            """,
            other_organization_id,
            shared_key,
        )
        assert accepted is not None, (
            "ten sam klucz idempotencji w INNEJ organizacji został odrzucony — UNIQUE jest "
            "globalny, a ma być per organizacja"
        )


async def test_jobs_attempt_limit_is_mandatory_and_has_no_default(pool, world) -> None:
    """INVARIANT 3 (0003, lines 91-92): "BEZ DEFAULT-u."

    "D-017 zamraża wartość 5, ale trzyma ją w module konfiguracji control plane — DEFAULT tutaj
    byłby drugim źródłem tego samego progu." The same reasoning already has a test one table
    over (`artifacts.retention_until`), and this column had none: adding `DEFAULT 5` left the
    suite at 54 passed, and from that moment the threshold would live in two places that drift
    apart without a word. D-017 says a changed threshold has to BREAK a test, not pass quietly.

    Mutation seen failing: `ALTER TABLE jobs ALTER COLUMN attempt_limit SET DEFAULT 5`.
    """
    async with pool.acquire() as connection:
        column = await connection.fetchrow(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'jobs'
              AND column_name = 'attempt_limit'
            """
        )
        assert column is not None, "brak kolumny jobs.attempt_limit (L3833)"
        assert column["is_nullable"] == "NO", "jobs.attempt_limit musi być NOT NULL"
        assert column["column_default"] is None, (
            f"jobs.attempt_limit ma DEFAULT {column['column_default']} — próg attempt_limit "
            "zamraża D-017 w JEDNYM module konfiguracji control plane. DEFAULT w schemacie jest "
            "drugim źródłem tej samej wartości i rozjeżdża się z tamtym bez ostrzeżenia"
        )
        # And the column really is unwritable without a value — the assertion above reads the
        # catalog, this one reads the server's behaviour.
        with pytest.raises(asyncpg.NotNullViolationError):
            await connection.execute(
                """
                INSERT INTO jobs
                    (organization_id, workspace_id, job_type, routing_key, priority,
                     dispatch_version, status, idempotency_key, lock_version)
                VALUES ($1, NULL, 'FIXTURE', 'fixture.key', 100, 1, 'READY', $2, 1)
                """,
                world["organization"],
                f"idem-bez-limitu-{uuid.uuid4()}",
            )


async def _insert_lease(connection, world, job_id, attempt_id, **values):
    payload = {"attempt_no": 1, "lease_generation": 1, "status": "ACTIVE"}
    payload.update(values)
    return await connection.fetchval(
        """
        INSERT INTO job_leases
            (organization_id, job_id, attempt_id, attempt_no, dispatch_version,
             lease_generation, lease_owner, status, expires_at)
        VALUES ($1, $2, $3, $4, 1, $5, $6, $7, now() + interval '60 seconds')
        RETURNING id
        """,
        world["organization"],
        job_id,
        attempt_id,
        payload["attempt_no"],
        payload["lease_generation"],
        uuid.uuid4(),
        payload["status"],
    )


async def test_lease_takeover_keeps_the_attempt_number_and_bumps_the_generation(
    pool, world
) -> None:
    """INVARIANT 4 (0003, lines 178-182), both halves of a decision argued and measured by nobody.

    "UNIQUE(job_id, attempt_no) NIE jest tu powtarzane, i to jest decyzja, nie przeoczenie:
    przejęcie lease'u dla TEJ SAMEJ próby podbija `lease_generation`, więc unikalność po numerze
    próby zablokowałaby przejęcie. (...) tutaj monotoniczność generacji pilnuje
    UNIQUE(job_id, lease_generation)."

    So there are two ways to break this table and the suite noticed neither:

    * ADD `UNIQUE(job_id, attempt_no)` — the "obvious" tightening, copied from `job_attempts` —
      and lease takeover after a lost heartbeat becomes impossible. That is D-004
      reconciliation dead in the water, and it fails CLOSED in the worst way: the job is never
      picked up again and nothing raises at schema-creation time.
    * DROP `UNIQUE(job_id, lease_generation)` and two leases can claim the same generation,
      which is the fencing token no longer fencing anything (L2372, `JobFencingToken`).

    The positive case is therefore as load-bearing as the negative one, and this test asserts
    both — a test that only checked the rejection would pass on a schema where takeover is
    forbidden.

    Mutations seen failing: `job_leases_job_id_lease_generation_key` dropped (rejection stops
    happening) and `UNIQUE (job_id, attempt_no)` added (takeover stops being possible).
    """
    async with pool.acquire() as connection:
        job_id = await _insert_job(connection, world)
        attempt_id = await _insert_attempt(connection, world, job_id, attempt_no=1)

        first = await _insert_lease(connection, world, job_id, attempt_id, lease_generation=1)
        assert first is not None

        # The heartbeat is lost and the reaper closes the lease. `job_leases_one_active_per_job`
        # covers only ACTIVE rows, so the slot is now free — that index has its own test above.
        released = await connection.execute(
            "UPDATE job_leases SET status = 'EXPIRED' WHERE id = $1", first
        )
        # asyncpg returns the string "UPDATE 0" for a statement that matched nothing AND DOES NOT
        # RAISE. Zero updated rows announced as success is the commonest form of this project's
        # illness in database code, so the tag is read, not assumed.
        assert released == "UPDATE 1", (
            f"zwolnienie lease'u nie ruszyło ani jednego wiersza: {released}"
        )

        # THE POSITIVE HALF: the same attempt, the same attempt_no, a higher generation.
        # The narrow `except` is here for the diagnostic only — it re-raises, so this branch
        # cannot turn a blocked takeover into a pass.
        try:
            taken_over = await _insert_lease(
                connection, world, job_id, attempt_id, attempt_no=1, lease_generation=2
            )
        except asyncpg.UniqueViolationError as refusal:
            raise AssertionError(
                "przejęcie lease'u dla TEJ SAMEJ próby zostało odrzucone przez "
                f"{refusal.constraint_name}. job_leases NIE MA mieć unikalności po numerze "
                "próby (0003, linie 178-181): przejęcie podbija lease_generation, a taki "
                "indeks blokuje reconciliation z D-004 i job nigdy nie wraca do puli"
            ) from refusal
        assert taken_over is not None, "INSERT lease'u przejmującego nie zwrócił identyfikatora"

        # THE NEGATIVE HALF: the generation cannot be reused. `status` is deliberately NOT
        # 'ACTIVE' here, so `job_leases_one_active_per_job` cannot be the thing that refuses the
        # row and steal the credit for this assertion.
        with pytest.raises(asyncpg.UniqueViolationError) as refused:
            await _insert_lease(
                connection,
                world,
                job_id,
                attempt_id,
                attempt_no=1,
                lease_generation=2,
                status="RECONCILING",
            )
        assert refused.value.constraint_name == "job_leases_job_id_lease_generation_key", (
            f"generację odrzucił inny obiekt: {refused.value.constraint_name}. Monotoniczności "
            "generacji ma pilnować UNIQUE(job_id, lease_generation), nie indeks aktywnego lease'u"
        )


async def test_one_approval_decision_per_revision(pool, world) -> None:
    """INVARIANT 5 (0003, lines 390-391): "Jedna decyzja na rewizję."

    "Zmiana zdania wymaga NOWEJ rewizji, a nie drugiej decyzji nad tą samą treścią — to jest
    L2795 i L5310 zapisane indeksem." Dropping that index left the suite green, and with it gone
    a revision can carry an APPROVED and a REJECTED decision at once: the approval queue has two
    answers to one question and no rule says which is current.

    The second half — a decision on a DIFFERENT revision of the same proposal IS accepted — is
    what keeps the index from being widened to the proposal. Widened that way, the second
    revision of an edited proposal could never be decided at all, and L2795 ("każda edycja
    tworzy nową rewizję") would have nowhere to land.

    Mutation seen failing: `approval_decisions_one_per_revision` dropped.
    """
    async with pool.acquire() as connection:
        chain = await _insert_proposal_chain(connection, world, content="jedna-decyzja")

        # The chain already holds one APPROVED decision on this revision.
        with pytest.raises(asyncpg.UniqueViolationError) as refused:
            await connection.execute(
                """
                INSERT INTO approval_decisions
                    (organization_id, workspace_id, proposal_revision_id, decision,
                     approved_payload_hash, approved_by, reason)
                VALUES ($1, $2, $3, 'REJECTED', $4, $5, 'zmiana zdania')
                """,
                world["organization"],
                world["workspace"],
                chain["revision"],
                chain["hash"],
                world["operator"],
            )
        assert refused.value.constraint_name == "approval_decisions_one_per_revision", (
            f"odrzucił inny obiekt: {refused.value.constraint_name}"
        )

        # A NEW revision of the same proposal takes its own decision — that is the whole shape
        # of "zmiana zdania wymaga nowej rewizji".
        second_content = "po-edycji"
        second_hash = payload_hash(second_content)
        second_revision = await connection.fetchval(
            """
            INSERT INTO action_proposal_revisions
                (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
                 source_fingerprint, state, expires_at)
            VALUES ($1, $2, $3, 2, $4::jsonb, $5, 'fingerprint-2', 'READY_FOR_APPROVAL',
                    now() + interval '1 hour')
            RETURNING id
            """,
            world["organization"],
            world["workspace"],
            chain["proposal"],
            '{"text": "' + second_content + '"}',
            second_hash,
        )
        decided = await connection.fetchval(
            """
            INSERT INTO approval_decisions
                (organization_id, workspace_id, proposal_revision_id, decision,
                 approved_payload_hash, approved_by, reason)
            VALUES ($1, $2, $3, 'REJECTED', $4, $5, 'nie ta treść')
            RETURNING id
            """,
            world["organization"],
            world["workspace"],
            second_revision,
            second_hash,
            world["operator"],
        )
        assert decided is not None, (
            "decyzja o DRUGIEJ rewizji tej samej propozycji została odrzucona — indeks pilnuje "
            "propozycji, a ma pilnować rewizji (L2795)"
        )


async def test_revision_immutability_also_covers_a_column_added_later(pool, world) -> None:
    """INVARIANT 6 (0003, lines 324-327), the reason the trigger is written the way it is.

    "POROWNANIE PRZEZ to_jsonb MINUS 'state', A NIE LISTA KOLUMN — to jest istotne: lista kolumn
    nie obejmuje kolumny dołożonej przyszłym ALTER TABLE, więc taka kolumna byłaby edytowalna
    i nikt by tego nie zauważył."

    Nothing measured that. Rewriting the function as an explicit list of today's columns left
    the suite at 54 passed, because every existing test edits a column that any such list would
    contain. The mechanism the comment defends — coverage of columns that DO NOT EXIST YET — had
    zero tests, which is precisely the case where "nikt by tego nie zauważył" comes true.

    So the test adds a column, inside a transaction that is rolled back whatever happens, and
    edits it. Under `to_jsonb(NEW) - 'state'` the edit is refused; under a column list it goes
    through, and F1's DoD "approval wskazuje dokładny payload hash; edycja treści unieważnia
    zgodę" (L5218) is quietly false for that column.

    Mutation seen failing: the trigger function replaced by an equivalent column-list version.
    """
    async with pool.acquire() as connection:
        outer = connection.transaction()
        await outer.start()
        try:
            # A column that migration 0003 does not have. Every write below is rolled back, so
            # the shared test database keeps the schema the migrations produced.
            await connection.execute(
                "ALTER TABLE action_proposal_revisions ADD COLUMN dodana_pozniej text"
            )
            chain = await _insert_proposal_chain(connection, world, content="kolumna-z-przyszlosci")

            # A savepoint, because the expected exception aborts the transaction it runs in and
            # every later statement would then fail with "current transaction is aborted" —
            # which would look like a passing test for the wrong reason.
            probe = connection.transaction()
            await probe.start()
            try:
                with pytest.raises(asyncpg.RaiseError, match="niezmienna"):
                    await connection.execute(
                        "UPDATE action_proposal_revisions SET dodana_pozniej = 'przemycone' "
                        "WHERE id = $1",
                        chain["revision"],
                    )
            finally:
                await probe.rollback()

            # And `state` still moves — the trigger guards content, it does not freeze the row.
            # Asserted here as well as in its own test, because a mutation that made the trigger
            # reject every UPDATE would otherwise make THIS test pass for the wrong reason.
            moved = await connection.execute(
                "UPDATE action_proposal_revisions SET state = 'SUPERSEDED' WHERE id = $1",
                chain["revision"],
            )
            assert moved == "UPDATE 1", f"przejście stanu nie ruszyło żadnego wiersza: {moved}"
        finally:
            await outer.rollback()

        # The column is gone with the rollback. Checked, because a leaked column would change
        # the schema every later test in this run reads.
        columns = await _columns(connection, "action_proposal_revisions")
        assert "dodana_pozniej" not in columns, (
            "kolumna testowa została w schemacie — rollback nie objął ALTER TABLE"
        )
