"""The approval path in PostgreSQL — revisions, decisions and single-use intents (L5217-5218).

    "żaden zapisujący side effect nie omija `ExecutionIntent`" (L5217)
    "approval wskazuje dokładny payload hash; edycja treści unieważnia zgodę" (L5218)
    "edycja draftu tworzy nową rewizję, unieważnia zgodę i nie przedłuża pierwotnego TTL" (L5539)

WHY THIS MODULE EXISTS AT ALL. `contracts.execution` already models every rule of this path by
type — and every one of those types is FROZEN, which means the whole path was, until this module,
a set of correct objects nobody persisted. Two consequences, both measured rather than imagined:

*   `ExecutionIntent.consume()` returns a NEW object through `model_copy`. A caller that ignores
    the return value keeps an intent whose `consumed_at` is still `None` and authorizes an
    unbounded number of submits — the exact thing L2821 forbids. In-memory single use is not
    single use; it is a convention.
*   `ActionProposalRevision.supersede()` returns the previous revision moved to `SUPERSEDED`
    **as a second object**. Drop it on the floor and the row in the database is still `APPROVED`,
    still matched by its old `ApprovalDecision`, and still executable after an edit. That is the
    direction along which stale content leaks, and it is the direction
    `contracts/tests/test_invariants.py::test_editing_content_invalidates_the_approval` does NOT
    look: that test asks whether the OLD approval matches the NEW revision (it does not), never
    whether the OLD revision still authorizes anything (it does, until somebody writes the
    `SUPERSEDED` row).

So the single rule of this module: **the losing side of every race loses in PostgreSQL, not in a
Python object.**

SIX THINGS THAT ARE LOAD-BEARING HERE.

1.  **Consumption is a compare-and-set, and zero rows RAISES.** `asyncpg` reports an `UPDATE`
    that matched nothing as the string `"UPDATE 0"` and raises nothing, so a consumption that
    trusted the driver would report the second submit as authorized. Measured by
    `tests/test_intent_single_use_db.py::test_two_concurrent_consumptions_leave_exactly_one_winner`
    on two genuinely parallel transactions.

2.  **An approval is looked up by the PAIR `(revision_id, payload_hash)`, never by the hash
    alone.** The hash cannot carry that scope by itself: `source_observed_at` is a field of the
    revision and is NOT one of the fourteen inputs of `ApprovalHashInput`, so two revisions built
    from the same content over a FRESH and a STALE read of the source share one digest. A lookup
    by digest would therefore let the operator's consent to the fresh read authorize the stale
    one. `tests/test_approval_persistence.py::test_two_revisions_with_identical_content_and_
    different_source_observed_at_share_a_hash` measures both halves of that sentence.

3.  **The TTL of a new revision is inherited, never recomputed.** L5539. `supersede` refuses to
    take a TTL as an argument, and the INSERT below passes the value that came out of it; an
    editing loop must not be a way to keep a draft alive past the thread window.

4.  **Every state transition of a revision, of a decision and of an intent writes ONE audit row,
    inside the same transaction as the change itself.** `control_plane.audit.AuditWriter` refuses
    a connection with no open transaction, precisely so an audit entry cannot survive the
    rollback of the thing it describes.

5.  **No moment here comes from the process clock.** Every timestamp is either read with
    `SELECT now()` inside the transaction that uses it (where it is the transaction's start time
    and therefore stable across the whole unit of work), or written by the server inside the
    statement itself, or left to a column default. Two clocks in one transaction are two orders of
    the same events, and the schema compares these columns against each other with CHECKs.
    `tests/test_approval_persistence.py::test_no_moment_in_the_store_comes_from_the_process_clock`
    greps this file for the usual spellings, which is why this paragraph names none of them —
    a trap `jobs/store.py`'s docstring fell into on its first run.

6.  **A grant is issued only for a revision that is STILL `APPROVED`.** The paragraph above about
    `supersede` describes the leak; this is the door on it, and it was missing until the DoD
    verification of L5218 probed for it. `edit_into_new_revision` moves the previous revision to
    `SUPERSEDED` and deliberately keeps its consent row, so a lookup keyed on `(revision_id,
    payload_hash)` alone still finds a consent and `issue_intent` still wrote a grant — for the
    content the operator had just replaced. The gate is `GRANTABLE_REVISION_STATES`, a set whose
    SIZE a test asserts, and it sits AFTER the consent lookup on purpose (see `issue_intent`).

WHAT IS NOT MEASURED HERE, listed rather than left to silence, because in this repository the
usual failure is a docstring promising a control nobody exercises:

*   the revision's own `expires_at` is not checked at issuance — an APPROVED revision past its
    draft TTL still gets a grant, and only `authorize_side_effect` refuses it at consumption;
*   `record_decision` refuses to write a `REJECTED` row without a reason only because migration
    0003 has `rejection_carries_a_reason`; this module contains no check of its own for it and no
    test of this package drives that constraint;
*   nothing here measures what happens when the SAME revision is read through two organizations
    that both legitimately exist — E1 has exactly one organization (D-016), so the cross-tenant
    tests build the second one by hand and prove the SCOPE, not the behaviour of a real second
    tenant.

WHAT THIS MODULE CANNOT DO, WRITTEN DOWN RATHER THAN LEFT SILENT (the D-019 pattern).
`control_plane.audit.AUDIT_KINDS` is a CLOSED vocabulary owned by another package, and it holds
no value for "a revision changed state" nor for "a revision was created". Two consequences, both
real and both visible in the log:

*   a revision transition is recorded under `APPROVAL_DECISION_RECORDED` with `from_state` and
    `to_state` in the payload. A reader can tell exactly what happened; the CATEGORY is
    imprecise, and `APPROVED → SUPERSEDED` is not a decision anybody made.
*   the CREATION of a revision leaves NO audit row at all. `record_revision` writes the row and
    nothing else. This is a GAP, not a design.

Both close the day the vocabulary gains something like `APPROVAL_REVISION_TRANSITIONED`, which
is an edit to `audit.py` and therefore a decision for whoever owns it.

WHAT IS NOT HERE, on purpose (L5500-5501). No route approves, rejects, edits, snoozes or asks the
model anything — those are the five actions of the approval queue as a PRODUCT and they are
E1.F4 (L5296-5311). `api/approvals.py` is read-only. The writing methods below exist because the
DoD lines L5217-5218 cannot be demonstrated without them and because the development path of
D-013 needs them; they are not an operator-facing feature and must not be described as one.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import NoReturn
from uuid import UUID, uuid4

import asyncpg
from community_os_contracts.execution import (
    ActionProposalRevision,
    ActionProposalState,
    ApprovalDecision,
    ApprovalHashInput,
    ExecutionIntent,
    SideEffectAuthorization,
    authorize_side_effect,
)
from community_os_contracts.transitions import require_transition

from control_plane.approval.models import ApprovalQueueEntry
from control_plane.approval.signers import SignerUnavailable, resolve_signer
from control_plane.audit import AuditWriter

#: The two decisions `approval_decisions.decision` accepts (CHECK in migration 0003, L2774).
#: Taken from the enum of proposal states rather than typed as strings, so a rename in contracts
#: arrives here as an import error instead of as a constraint violation at run time.
APPROVED_DECISION = ActionProposalState.APPROVED.value
REJECTED_DECISION = ActionProposalState.REJECTED.value

#: The ONLY revision state a one-time grant may be issued for (L5321, and the same rule
#: `contracts.authorize_side_effect` applies one step later).
#:
#: Spelled as a SET of one rather than as `revision.state is ActionProposalState.APPROVED`, so a
#: test can assert its CARDINALITY. Widening a gate is a one-token edit — adding a member here —
#: and a widening nobody counted is how a gate quietly stops being one.
#: `tests/test_approval_persistence.py::test_a_grant_is_issued_only_for_the_single_grantable_state`
#: enumerates every member of `ActionProposalState`, asserts how many there are and asserts how
#: many of them this set holds, so both a new state and a new member of this set arrive as a
#: failing test rather than as a wider gate.
GRANTABLE_REVISION_STATES: frozenset[ActionProposalState] = frozenset(
    {ActionProposalState.APPROVED}
)

#: Audit kinds this module writes. Named here so a test can enumerate them without importing the
#: whole closed vocabulary, and so the imprecision documented above is visible in one place.
REVISION_TRANSITION_KIND = "APPROVAL_DECISION_RECORDED"
DECISION_KIND = "APPROVAL_DECISION_RECORDED"
INTENT_ISSUED_KIND = "EXECUTION_INTENT_ISSUED"
INTENT_CONSUMED_KIND = "EXECUTION_INTENT_CONSUMED"


@asynccontextmanager
async def _decision_transaction(pool, connection):
    # The delegation queue and its consent must commit together; a failed queue insert
    # must not strand an approved revision that no worker can ever pick up.
    if connection is not None:
        if not connection.is_in_transaction():
            raise ValueError("A supplied decision connection must be in a transaction.")
        yield connection
    else:
        async with pool.acquire() as acquired, acquired.transaction():
            yield acquired


class ApprovalStoreError(Exception):
    """Base for every refusal of this module. Never a return value that reads like success."""


class RevisionTransitionRefused(ApprovalStoreError):
    """The state CAS matched no row: the revision moved on, or it is not there.

    `action_proposal_revisions` has NO `lock_version` column in migrations 0001-0003, so
    `control_plane.locking.apply_transition` cannot be used — it refuses a table with no version
    column rather than sending an `UPDATE` on a column that does not exist. The guard available
    here is the SOURCE STATE, checked inside the same statement that writes the target state, and
    that is what the CAS below carries. The arrows themselves still come from
    `contracts.transitions.require_transition`; there is no second copy of the machine here.
    """


class IntentAlreadyConsumed(ApprovalStoreError):
    """A second consumption of the same intent. L2821: at most one automatic submit per intent."""


class IntentExpired(ApprovalStoreError):
    """The grant is past its deadline. L5692: a stale execution grant is single-use AND expires."""


class GrantAlreadyIssued(ApprovalStoreError):
    """A grant with this one-time hash already exists in this organization (L2755, L5692)."""


class RevisionNotGrantable(ApprovalStoreError):
    """The revision is not in the one state a grant may be issued for (`GRANTABLE_REVISION_STATES`).

    A SEPARATE class rather than a bare `ApprovalStoreError`, because the distinction from "there
    is no consent" is the whole content of the refusal: a revision with no consent was never
    approved, while a revision in this branch WAS approved and then left that state — by an edit,
    by expiry, by a later rejection. Only the second one is a leak of content the operator has
    already replaced, and only a distinct type lets a test assert which of the two fired.
    """


# ---------------------------------------------------------------------------
# SQL. Every statement that reads a consent carries `proposal_revision_id`.
# ---------------------------------------------------------------------------

_INSERT_PROPOSAL_SQL = """
INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)
VALUES ($1, $2, $3)
RETURNING id
"""

#: `created_at` is passed explicitly instead of being left to the column default, and it carries
#: the value the caller read out of `now()` in this very transaction. The row and the returned
#: object then hold the SAME moment: with the default, the object would carry the moment Python
#: saw and the row the moment PostgreSQL wrote, and the two would disagree by however long the
#: statement took — on a revision whose `expires_at` is compared against `created_at` by a CHECK.
_INSERT_REVISION_SQL = """
INSERT INTO action_proposal_revisions
    (id, organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
     source_fingerprint, expires_at, state, created_at, source_observed_at)
VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
RETURNING id, organization_id, workspace_id, proposal_id, revision, payload::text AS payload,
          payload_hash, state, created_at, expires_at, source_observed_at
"""

_REVISION_COLUMNS_SQL = """
SELECT id, organization_id, workspace_id, proposal_id, revision, payload::text AS payload,
       payload_hash, state, created_at, expires_at, source_observed_at
FROM action_proposal_revisions
"""

#: `AND organization_id = $2` is the tenant boundary of the whole read side of this module: every
#: revision read — `fetch_revision`, the `FOR UPDATE` of the edit and decision paths, the re-read
#: after a CAS, and the one `issue_intent` starts from — goes through this string. It was
#: unmeasured until `tests/test_approval_persistence.py::
#: test_a_revision_of_another_organization_is_invisible_through_the_tenant_scope`, which deletes
#: nothing and instead asks for one revision under the WRONG organization and under the right one.
_REVISION_BY_ID_SQL = _REVISION_COLUMNS_SQL + " WHERE id = $1 AND organization_id = $2"

#: The edit path reads the previous revision `FOR UPDATE`, so two concurrent edits serialize
#: instead of both computing a successor from the same `APPROVED` row. The second one then finds
#: the state already `SUPERSEDED` and `require_transition` refuses it — a refusal, not two
#: successors of one revision.
_REVISION_FOR_UPDATE_SQL = _REVISION_BY_ID_SQL + " FOR UPDATE"

#: The state CAS. The source state is inside the WHERE, so the check and the write cannot be
#: separated; zero rows is a refusal (see `RevisionTransitionRefused`). Only `state` is written,
#: which is also all the immutability trigger of migration 0003 permits.
_TRANSITION_REVISION_SQL = """
UPDATE action_proposal_revisions
SET state = $4
WHERE id = $1 AND organization_id = $2 AND state = $3
RETURNING id, state
"""

_INSERT_DECISION_SQL = """
INSERT INTO approval_decisions
    (id, organization_id, workspace_id, proposal_revision_id, decision, approved_payload_hash,
     approved_by, decided_at, reason, approved_membership_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
RETURNING id, proposal_revision_id, approved_payload_hash, approved_by, decided_at, reason,
       approved_membership_id
"""

#: THE ONLY read of a consent in this module, and it is keyed on the PAIR. Both halves are in the
#: WHERE, and there is no sibling statement keyed on the digest alone — see rule 2 of the module
#: docstring, and `tests/test_approval_persistence.py::
#: test_no_statement_looks_up_a_consent_by_the_payload_hash_alone`, which reads this file and
#: fails if one ever appears.
#:
#: FOUR CONDITIONS, AND EACH ONE HAS ITS OWN TEST. That list is here because the three below it
#: were, until this package, decoration: deleting `approved_payload_hash`, deleting `decision` or
#: deleting `organization_id` from the equivalent revision lookup each left the whole suite green.
#: A condition no test can kill is a comment that happens to be executable.
#:
#: *   `organization_id = $1` — `test_a_revision_of_another_organization_is_invisible_through_the_
#:     tenant_scope` (that one measures `_REVISION_BY_ID_SQL`, which is the scope this statement
#:     inherits: reach the revision across tenants and this lookup is the next door).
#: *   `proposal_revision_id = $2` — `test_two_revisions_with_identical_content_and_different_
#:     source_observed_at_share_a_hash`.
#: *   `approved_payload_hash = $3` — `test_a_consent_is_not_found_for_a_different_digest_under_
#:     the_same_revision_id` and `test_no_grant_is_issued_when_the_consent_names_another_digest`.
#: *   `decision = $4` — `test_a_rejection_is_not_a_consent`. This is the ONE condition that tells
#:     an approval from a refusal anywhere in this module; without it `find_approval` returns the
#:     operator's REJECTION as though it were consent.
_DECISION_BY_REVISION_AND_HASH_SQL = """
SELECT id, proposal_revision_id, approved_payload_hash, approved_by, decided_at, reason,
       approved_membership_id
FROM approval_decisions
WHERE organization_id = $1
  AND proposal_revision_id = $2
  AND approved_payload_hash = $3
  AND decision = $4
"""

_INSERT_INTENT_SQL = """
INSERT INTO execution_intents
    (id, organization_id, workspace_id, proposal_revision_id, approval_decision_id,
     one_time_grant_hash, expires_at, status, payload_hash, account_id, assignment_version_id,
     issued_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, 'ISSUED', $8, $9, $10, $11)
RETURNING id, proposal_revision_id, payload_hash, account_id, assignment_version_id,
          issued_at, expires_at, consumed_at
"""

_INTENT_BY_ID_SQL = """
SELECT id, organization_id, workspace_id, proposal_revision_id, payload_hash, account_id,
       assignment_version_id, issued_at, expires_at, consumed_at, status
FROM execution_intents
WHERE id = $1 AND organization_id = $2
"""

#: THE consumption. `consumed_at IS NULL` is the whole of single use: the second transaction to
#: reach this row blocks on its lock, re-reads the committed version, finds the column set and
#: matches NOTHING. It loses in PostgreSQL, which is the only place a loss survives a restart and
#: a second process.
#:
#: `expires_at > now()` is in the same WHERE rather than in a preceding SELECT, so a grant that
#: dies between the check and the write cannot be burned. Which of the two conditions failed is
#: established afterwards, by re-reading the row, only in order to name the refusal.
_CONSUME_INTENT_SQL = """
UPDATE execution_intents
SET consumed_at = now(), status = 'CONSUMED'
WHERE id = $1
  AND organization_id = $2
  AND consumed_at IS NULL
  AND expires_at > now()
RETURNING id, proposal_revision_id, payload_hash, account_id, assignment_version_id,
          issued_at, expires_at, consumed_at
"""

#: The queue, sorted ASCENDING BY `expires_at` (L4595): the operator must not be handed the cards
#: that will outlive everything first, and must not be handed cards that expire in a minute last.
#:
#: `created_at` appears ONLY as a tie-break behind `expires_at`, and never as the leading key.
#: Leading with it is the mistake this ordering exists to avoid — it looks natural, it survives
#: every eyeball review, and it breaks L4595 in exactly the case that matters, a queue whose
#: cards were created in one order and expire in another. `tests/test_approvals_api.py::
#: test_the_queue_is_sorted_ascending_by_expires_at` builds precisely that queue.
_QUEUE_SQL = (
    _REVISION_COLUMNS_SQL
    + """
WHERE organization_id = $1
ORDER BY expires_at ASC, created_at ASC, id ASC
LIMIT $2
"""
)


def one_time_grant_hash(
    *,
    revision_id: UUID,
    payload_hash: str,
    account_id: UUID,
    assignment_version_id: UUID | None,
) -> str:
    """The digest under which `execution_intents` refuses a second grant (L2755, L5692).

    DERIVED, not random, and that is the point. `UNIQUE (organization_id, one_time_grant_hash)`
    in migration 0003 is described there as "grant nie da się wystawić dwa razy" — a random value
    would satisfy the index and mean nothing, because every issuance would be unique by
    construction. Deriving it from the four things the grant BINDS makes the index enforce the
    sentence: a second intent for the same revision, content, account and assignment is refused
    by PostgreSQL.

    `assignment_version_id` is typed optional only because the COLUMN it mirrors is nullable
    (migration 0003, D-020). `issue_intent` never passes `None`: it reads the value off the
    revision's approved hash input, where it is required.
    """
    canonical = json.dumps(
        {
            "revision_id": str(revision_id),
            "payload_hash": payload_hash,
            "account_id": str(account_id),
            "assignment_version_id": str(assignment_version_id) if assignment_version_id else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revision_of(row: asyncpg.Record) -> ActionProposalRevision:
    """One `action_proposal_revisions` row as the contracts type. The only transcription of it.

    `ApprovalHashInput` is rebuilt from the stored `payload`, and `ActionProposalRevision`'s own
    validator then recomputes the digest and compares it with the stored `payload_hash`. So a row
    whose content and digest disagree — a hand-edited payload, a botched migration — cannot be
    read back as a valid revision at all. That check costs one SHA-256 per row and it is the
    reason the payload is stored in full rather than only its digest.
    """
    return ActionProposalRevision(
        id=row["id"],
        proposal_id=row["proposal_id"],
        revision_no=row["revision"],
        state=ActionProposalState(row["state"]),
        hash_input=ApprovalHashInput.model_validate_json(row["payload"]),
        payload_hash=row["payload_hash"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        source_observed_at=row["source_observed_at"],
    )


def _queue_entry_of(row: asyncpg.Record) -> ApprovalQueueEntry:
    """One `action_proposal_revisions` row as a queue entry — the full digest, never a prefix."""
    return ApprovalQueueEntry(
        revision_id=row["id"],
        proposal_id=row["proposal_id"],
        organization_id=row["organization_id"],
        workspace_id=row["workspace_id"],
        revision_no=row["revision"],
        state=ActionProposalState(row["state"]),
        payload_hash=row["payload_hash"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        source_observed_at=row["source_observed_at"],
    )


def _intent_of(row: asyncpg.Record) -> ExecutionIntent:
    """One `execution_intents` row as the contracts type.

    Built from the row and never with `model_copy`. A consumed intent handed back to the caller
    is therefore the one PostgreSQL committed — the whole difference this module exists for.

    A CONFLICT BETWEEN TWO PARTS OF THE PROJECT SURFACES HERE, and it is resolved loudly rather
    than papered over. `execution_intents.assignment_version_id` is NULLABLE, and migration 0003
    says why in as many words: there is no assignments table in F1, so "wpisywanie tam wymyślonego
    UUID-a byłoby udawaniem powiązania". `contracts.ExecutionIntent.assignment_version_id`,
    meanwhile, is a REQUIRED `UUID` — L5321 binds the grant to "the exact revision, account AND
    assignment", and `ApprovalHashInput.assignment_version` is required for the same reason.

    So a row with a NULL there cannot be read back as a valid `ExecutionIntent` at all, and this
    function REFUSES instead of substituting a zero UUID. A substituted identifier would be a
    grant that claims to be bound to an assignment nobody named — the shape of evidence pointing
    at something that did not happen. `issue_intent` never writes such a row: it takes the value
    off the revision's own hash input, which is the assignment the operator actually consented to.
    """
    if row["assignment_version_id"] is None:
        raise ApprovalStoreError(
            f"execution_intents.assignment_version_id jest NULL dla intentu {row['id']}. Kolumna "
            "jest nullowalna (migracja 0003: w F1 nie ma tabeli assignmentów), ale "
            "contracts.ExecutionIntent wymaga tej wartości, bo L5321 wiąże grant z DOKŁADNYM "
            "assignmentem. Podstawienie tu dowolnego UUID-a byłoby udawaniem powiązania — ten "
            "wiersz powstał poza `issue_intent` i nie da się go odczytać jako ważnego grantu"
        )
    return ExecutionIntent(
        id=row["id"],
        revision_id=row["proposal_revision_id"],
        payload_hash=row["payload_hash"],
        account_id=row["account_id"],
        assignment_version_id=row["assignment_version_id"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
    )


def _decision_of(row: asyncpg.Record) -> ApprovalDecision:
    """One `approval_decisions` row as the contracts type."""
    return ApprovalDecision(
        id=row["id"],
        revision_id=row["proposal_revision_id"],
        approved_payload_hash=row["approved_payload_hash"],
        approved_by=row["approved_by"],
        approved_membership_id=row["approved_membership_id"],
        approved_at=row["decided_at"],
        reason=row["reason"],
    )


class ApprovalStore:
    """Reads and writes the four tables of the approval path. Holds a pool, never a connection.

    Same rule as `jobs.store.JobStore`: every method acquires a connection, uses it and gives it
    back, so nothing here keeps a transaction open across an `await` the caller controls.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- writing -------------------------------------------------------------

    async def create_proposal(
        self, *, organization_id: UUID, workspace_id: UUID, social_account_id: UUID
    ) -> UUID:
        """Insert the parent row a revision hangs under. No state column — it lives on revisions.

        D-013: F1 has NO producer of proposals. This is the development path, and it must not be
        called production data.
        """
        async with self._pool.acquire() as connection:
            return await connection.fetchval(
                _INSERT_PROPOSAL_SQL, organization_id, workspace_id, social_account_id
            )

    async def record_revision(
        self,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        proposal_id: UUID,
        revision_no: int,
        hash_input: ApprovalHashInput,
        state: ActionProposalState,
        expires_at: datetime,
        source_observed_at: datetime | None = None,
        revision_id: UUID | None = None,
    ) -> ActionProposalRevision:
        """Write one immutable revision and return it as read back from the row.

        The digest is computed by `ApprovalHashInput.payload_hash()` and never accepted from a
        caller: a revision whose stored digest was supplied alongside its content could carry a
        digest of something else, and every check downstream compares against that column.

        NO AUDIT ROW IS WRITTEN HERE — see the module docstring. The closed vocabulary of
        `control_plane.audit` has no kind for the creation of a revision, and inventing one is an
        edit to a file this package does not own.
        """
        payload_text = hash_input.canonical_bytes().decode("utf-8")
        digest = hash_input.payload_hash()
        async with self._pool.acquire() as connection, connection.transaction():
            moment = await connection.fetchval("SELECT now()")
            row = await connection.fetchrow(
                _INSERT_REVISION_SQL,
                revision_id or uuid4(),
                organization_id,
                workspace_id,
                proposal_id,
                revision_no,
                payload_text,
                digest,
                hash_input.source_fingerprint,
                expires_at,
                state.value,
                moment,
                source_observed_at or moment,
            )
        return _revision_of(row)

    async def move_revision(
        self,
        revision_id: UUID,
        *,
        organization_id: UUID,
        workspace_id: UUID,
        target: ActionProposalState,
        actor_ref: str,
    ) -> ActionProposalRevision:
        """Move one revision along the machine of L2773-2775, under a CAS on its source state.

        The arrow is checked by `contracts.transitions.require_transition` BEFORE any statement
        is sent, so an illegal transition never reaches PostgreSQL — and one audit row is written
        in the same transaction as the change.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                _REVISION_FOR_UPDATE_SQL, revision_id, organization_id
            )
            if current is None:
                raise RevisionTransitionRefused(
                    f"nie ma rewizji {revision_id} w organizacji {organization_id} — nie ma "
                    "czego przenosić"
                )
            revision = _revision_of(current)
            require_transition(revision.state, target)
            return await self._transition_within_transaction(
                connection,
                revision=revision,
                organization_id=organization_id,
                workspace_id=workspace_id,
                target=target,
                actor_ref=actor_ref,
                payload={},
            )

    async def record_decision(
        self,
        *,
        revision_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        decision: str,
        operator_id: UUID | None = None,
        membership_id: UUID | None = None,
        user_id: UUID | None = None,
        reason: str | None = None,
        decision_id: UUID | None = None,
        connection: asyncpg.Connection | None = None,
    ) -> tuple[ApprovalDecision, ActionProposalRevision]:
        """Record the human's consent (or refusal) AND move the revision, in one transaction.

        Two rows and one audit entry, because this is ONE fact: the operator decided, and the
        revision is in the state that decision put it in. Written as two calls the pair could
        commit apart, and a consent pointing at a revision still waiting for one is a hole in
        exactly the direction L5218 guards.

        The digest written into `approved_payload_hash` is READ OFF THE REVISION, never taken as
        an argument. That is L5218 in one line: the consent points at the exact payload hash, and
        a caller must not be able to name a different one.
        """
        if decision not in (APPROVED_DECISION, REJECTED_DECISION):
            raise ApprovalStoreError(
                f"decyzja {decision!r} nie jest ani {APPROVED_DECISION}, ani {REJECTED_DECISION}. "
                "L2774 rysuje dokładnie te dwie strzałki z READY_FOR_APPROVAL"
            )
        target = ActionProposalState(decision)

        async with _decision_transaction(self._pool, connection) as connection:
            try:
                operator_id, membership_id = await resolve_signer(
                    connection,
                    organization_id=organization_id,
                    operator_id=operator_id,
                    membership_id=membership_id,
                    user_id=user_id,
                )
            except SignerUnavailable as exc:
                raise ApprovalStoreError(str(exc)) from exc
            current = await connection.fetchrow(
                _REVISION_FOR_UPDATE_SQL, revision_id, organization_id
            )
            if current is None:
                raise RevisionTransitionRefused(
                    f"nie ma rewizji {revision_id} w organizacji {organization_id} — zgoda nie ma "
                    "na co wskazywać"
                )
            revision = _revision_of(current)
            require_transition(revision.state, target)

            moment = await connection.fetchval("SELECT now()")
            decision_row = await connection.fetchrow(
                _INSERT_DECISION_SQL,
                decision_id or uuid4(),
                organization_id,
                workspace_id,
                revision_id,
                decision,
                # From the revision. Not an argument. L5218.
                revision.payload_hash,
                operator_id,
                moment,
                reason,
                membership_id,
            )
            moved = await self._transition_within_transaction(
                connection,
                revision=revision,
                organization_id=organization_id,
                workspace_id=workspace_id,
                target=target,
                actor_ref=(
                    f"membership:{membership_id}" if membership_id else f"operator:{operator_id}"
                ),
                payload={"decision": decision},
            )
        return _decision_of(decision_row), moved

    async def edit_into_new_revision(
        self,
        *,
        revision_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        new_hash_input: ApprovalHashInput,
        actor_ref: str,
        source_observed_at: datetime | None = None,
        new_revision_id: UUID | None = None,
    ) -> tuple[ActionProposalRevision, ActionProposalRevision]:
        """The edit path: previous revision → `SUPERSEDED`, successor → `DRAFT`. L2795, L5539.

        Returns `(superseded_previous, successor)`, both as read back from their rows.

        `contracts.ActionProposalRevision.supersede` decides everything that could be decided
        wrongly here, and this method persists its answer without adding to it:

        *   the arrow `APPROVED → SUPERSEDED` is the only one L2775 draws into that state, and
            `supersede` asks `require_transition` for it;
        *   an edit that changes nothing is refused there, not here;
        *   **the successor inherits `expires_at` unchanged.** The value below comes out of
            `supersede`; there is no argument for it and no expression computing one. L5539:
            "edycja draftu … nie przedłuża pierwotnego TTL". `tests/test_approval_persistence.py::
            test_a_new_revision_does_not_extend_the_original_ttl` reads both rows back and
            compares them.

        TWO CONCURRENT EDITS PRODUCE ONE SUCCESSOR, and it is worth being precise about what
        makes that true, because the obvious answer is the wrong one. The `FOR UPDATE` below
        serializes the two readers, so the second one sees `SUPERSEDED` and is refused by
        `require_transition` — but that only decides WHICH refusal is reported. What makes a
        second successor impossible is the pair of guards underneath: the state CAS (zero rows is
        a refusal) and `UNIQUE (proposal_id, revision)` in migration 0003. Remove the lock and the
        second caller is still refused, one layer down.
        `tests/test_approval_persistence.py::test_two_concurrent_edits_produce_one_successor_not_two`
        measures the outcome and the refusal.

        The consent that pointed at the previous revision is NOT deleted and NOT rewritten. It
        stays in `approval_decisions` describing what the operator really approved, and it stops
        authorizing anything because the revision it names is no longer `APPROVED` — which is the
        check `contracts.authorize_side_effect` makes. Deleting it would destroy the record of a
        decision that was genuinely taken.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                _REVISION_FOR_UPDATE_SQL, revision_id, organization_id
            )
            if current is None:
                raise RevisionTransitionRefused(
                    f"nie ma rewizji {revision_id} w organizacji {organization_id} — nie ma czego "
                    "edytować"
                )
            previous = _revision_of(current)
            moment = await connection.fetchval("SELECT now()")

            successor_id = new_revision_id or uuid4()
            # Every rule of the edit is decided here, in contracts, and this module only writes
            # down the answer.
            superseded, successor = previous.supersede(
                new_id=successor_id,
                new_hash_input=new_hash_input,
                now=moment,
                source_observed_at=source_observed_at,
            )

            written_previous = await self._transition_within_transaction(
                connection,
                revision=previous,
                organization_id=organization_id,
                workspace_id=workspace_id,
                target=superseded.state,
                actor_ref=actor_ref,
                payload={},
            )
            successor_row = await connection.fetchrow(
                _INSERT_REVISION_SQL,
                successor.id,
                organization_id,
                workspace_id,
                successor.proposal_id,
                successor.revision_no,
                successor.hash_input.canonical_bytes().decode("utf-8"),
                successor.payload_hash,
                successor.hash_input.source_fingerprint,
                # INHERITED. Not `moment + something`.
                successor.expires_at,
                successor.state.value,
                successor.created_at,
                successor.source_observed_at,
            )
        return written_previous, _revision_of(successor_row)

    async def issue_intent(
        self,
        *,
        revision_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        account_id: UUID,
        expires_at: datetime,
        actor_ref: str,
        intent_id: UUID | None = None,
    ) -> ExecutionIntent:
        """Issue the single-use grant for one APPROVED revision (L5321). One audit row.

        TWO GATES, IN THIS ORDER, AND THE ORDER IS LOAD-BEARING.

        1.  **A consent for the exact pair `(revision_id, payload_hash)` must exist**, with the
            digest read off the revision rather than taken as an argument, so a grant cannot be
            issued against a consent for different content. No consent means no grant, and that
            refusal is the L5217 boundary at its narrowest point.
        2.  **The revision must be in `GRANTABLE_REVISION_STATES`** — that is, in `APPROVED` and
            in nothing else. Until this package there was no such check here, and the consequence
            was measured rather than reasoned about: `edit_into_new_revision` moves the previous
            revision to `SUPERSEDED` and LEAVES ITS CONSENT ROW in place (correctly — it records a
            decision that was really taken), so `issue_intent` handed out a fresh grant for content
            the operator had already replaced. That is precisely "the direction along which stale
            content leaks" this module's first paragraph claims to close.
            `contracts.authorize_side_effect` rejects such a grant one step later, at consumption,
            but a grant that exists is a grant that appears in the log, in the queue and in any
            future dispatcher — the refusal belongs at issuance.

        WHY THE CONSENT LOOKUP COMES FIRST. Put the state gate in front and it swallows every
        revision that was never approved at all — including the one in
        `test_two_revisions_with_identical_content_and_different_source_observed_at_share_a_hash`,
        which sits in `READY_FOR_APPROVAL`. That test would keep passing while measuring the state
        gate instead of the consent lookup it exists for: a green test whose detection power
        silently moved to another mechanism. With the consent lookup first, "never approved" and
        "approved and then left that state" are two different refusals of two different types, and
        each has a test that only the right deletion can turn red.

        **`assignment_version_id` is NOT an argument.** It is taken off the revision's own
        `ApprovalHashInput`, where it is a required field and one of the fourteen inputs of the
        digest the operator consented to (L2780). An argument here would let a caller bind the
        grant to a different assignment than the approval names, which is the one thing L5321
        exists to prevent — and it would also be the invented identifier migration 0003 refuses.

        NOT CHECKED HERE, and said out loud rather than left to be discovered: the revision's own
        `expires_at`. A grant can still be issued for an APPROVED revision whose draft TTL has
        already passed; `authorize_side_effect` refuses it at consumption (`revision.is_expired`),
        so nothing executes, but the row is written. No test of this package measures that, and it
        is not claimed above.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            revision_row = await connection.fetchrow(
                _REVISION_BY_ID_SQL, revision_id, organization_id
            )
            if revision_row is None:
                raise ApprovalStoreError(
                    f"nie ma rewizji {revision_id} w organizacji {organization_id} — grant nie ma "
                    "na co wskazywać"
                )
            revision = _revision_of(revision_row)
            decision_row = await connection.fetchrow(
                _DECISION_BY_REVISION_AND_HASH_SQL,
                organization_id,
                revision_id,
                revision.payload_hash,
                APPROVED_DECISION,
            )
            if decision_row is None:
                raise ApprovalStoreError(
                    f"rewizja {revision_id} nie ma zgody na tę dokładną treść. Żaden zapisujący "
                    "side effect nie omija ExecutionIntent, a intent nie powstaje bez zgody "
                    "wskazującej dokładny payload hash (L5217, L5218)"
                )

            # THE SECOND GATE. The consent exists, so this revision WAS approved once; the
            # question left is whether it still is. `SUPERSEDED` is the case that leaks.
            if revision.state not in GRANTABLE_REVISION_STATES:
                raise RevisionNotGrantable(
                    f"rewizja {revision_id} jest w stanie {revision.state.value}, a grant wolno "
                    f"wystawić wyłącznie dla stanu "
                    f"{', '.join(sorted(state.value for state in GRANTABLE_REVISION_STATES))}. "
                    "Zgoda na tę rewizję nadal istnieje i opisuje decyzję, która naprawdę zapadła "
                    "— ale rewizja tego stanu już nie ma. Dla SUPERSEDED jest to dokładnie treść, "
                    "którą operator zastąpił edycją (L2795, L5218, L5321)"
                )

            # From the approved digest's own inputs, not from an argument. See the docstring.
            assignment_version_id = revision.hash_input.assignment_version
            grant = one_time_grant_hash(
                revision_id=revision_id,
                payload_hash=revision.payload_hash,
                account_id=account_id,
                assignment_version_id=assignment_version_id,
            )
            moment = await connection.fetchval("SELECT now()")
            try:
                row = await connection.fetchrow(
                    _INSERT_INTENT_SQL,
                    intent_id or uuid4(),
                    organization_id,
                    workspace_id,
                    revision_id,
                    decision_row["id"],
                    grant,
                    expires_at,
                    revision.payload_hash,
                    account_id,
                    assignment_version_id,
                    moment,
                )
            except asyncpg.UniqueViolationError as violation:
                raise GrantAlreadyIssued(
                    f"grant jednorazowy dla rewizji {revision_id} i konta {account_id} już "
                    "istnieje. one-time ExecutionIntent nie wystawia się dwa razy (L2755, L5692)"
                ) from violation

            intent = _intent_of(row)
            await AuditWriter(connection).record(
                kind=INTENT_ISSUED_KIND,
                organization_id=organization_id,
                workspace_id=workspace_id,
                subject_ref=f"intent:{intent.id}",
                actor_ref=actor_ref,
                payload={
                    "intent_id": str(intent.id),
                    "proposal_revision_id": str(revision_id),
                    "payload_hash": intent.payload_hash,
                    "expires_at": intent.expires_at.isoformat(),
                },
            )
        return intent

    async def consume_intent(
        self, intent_id: UUID, *, organization_id: UUID, actor_ref: str
    ) -> ExecutionIntent:
        """Burn the grant with a compare-and-set. Returns the intent PostgreSQL committed.

        Zero updated rows RAISES. `asyncpg` does not: an `UPDATE` that matched nothing comes back
        as the string `"UPDATE 0"` and the call succeeds, which is how a second submit would be
        reported as authorized. The refusal is named afterwards by re-reading the row, so the
        operator learns whether the grant was already spent or had expired.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(_CONSUME_INTENT_SQL, intent_id, organization_id)
            if row is None:
                await self._refuse_consumption(connection, intent_id, organization_id)
            intent = _intent_of(row)
            await self._record_consumption(
                connection,
                intent=intent,
                organization_id=organization_id,
                actor_ref=actor_ref,
            )
        return intent

    async def authorize_and_consume_intent(
        self,
        intent_id: UUID,
        *,
        organization_id: UUID,
        actor_ref: str,
    ) -> SideEffectAuthorization:
        """The only path from stored rows to a writing side effect (L5217, L5334).

        Reads the intent, its revision and the consent for that exact pair OUT OF THE DATABASE,
        runs every check of `contracts.authorize_side_effect` against them, and only then burns
        the grant with the CAS. The proof it returns carries the intent as PostgreSQL committed it.

        This is the direction along which stale content leaks, and the reason this method loads
        the revision instead of taking it as an argument: after an edit the row is `SUPERSEDED`,
        so the consent that still names it — correctly, it is a record of a real decision — no
        longer authorizes anything. A caller holding an `ActionProposalRevision` object from
        before the edit would still see `APPROVED` and pass every check.
        `tests/test_approval_persistence.py::
        test_the_previous_approval_no_longer_authorizes_execution` measures exactly that.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            intent_row = await connection.fetchrow(_INTENT_BY_ID_SQL, intent_id, organization_id)
            if intent_row is None:
                raise ApprovalStoreError(
                    f"nie ma intentu {intent_id} w organizacji {organization_id}"
                )
            intent = _intent_of(intent_row)
            revision_row = await connection.fetchrow(
                _REVISION_BY_ID_SQL, intent.revision_id, organization_id
            )
            if revision_row is None:
                raise ApprovalStoreError(
                    f"intent {intent_id} wskazuje rewizję {intent.revision_id}, której nie ma"
                )
            revision = _revision_of(revision_row)
            decision_row = await connection.fetchrow(
                _DECISION_BY_REVISION_AND_HASH_SQL,
                organization_id,
                revision.id,
                revision.payload_hash,
                APPROVED_DECISION,
            )
            approval = _decision_of(decision_row) if decision_row is not None else None

            moment = await connection.fetchval("SELECT now()")
            # Raises `PermissionError` on any gap. Nothing below runs unless it returns.
            authorize_side_effect(revision=revision, approval=approval, intent=intent, now=moment)

            consumed_row = await connection.fetchrow(
                _CONSUME_INTENT_SQL, intent_id, organization_id
            )
            if consumed_row is None:
                await self._refuse_consumption(connection, intent_id, organization_id)
            consumed = _intent_of(consumed_row)
            await self._record_consumption(
                connection,
                intent=consumed,
                organization_id=organization_id,
                actor_ref=actor_ref,
            )
            return SideEffectAuthorization(
                revision_id=revision.id,
                payload_hash=revision.payload_hash,
                intent_id=consumed.id,
                authorized_at=moment,
                consumed_intent=consumed,
            )

    # -- reading -------------------------------------------------------------

    async def fetch_revision(
        self, revision_id: UUID, *, organization_id: UUID
    ) -> ActionProposalRevision | None:
        """One revision of THIS organization, or `None`. The scope is required, never a default.

        MEASURED, and it had to be said that way because it was not:
        `tests/test_approval_persistence.py::
        test_a_revision_of_another_organization_is_invisible_through_the_tenant_scope` asks for one
        revision twice, changing NOTHING but `organization_id`, and requires the row under its own
        organization and `None` under a stranger's. Before that test, deleting
        `AND organization_id = $2` from `_REVISION_BY_ID_SQL` left the whole approval suite green —
        the sentence above was a promise with no measurement behind it, which is worse than no
        sentence, because a reader stops looking.
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(_REVISION_BY_ID_SQL, revision_id, organization_id)
        return _revision_of(row) if row is not None else None

    async def find_approval(
        self, *, revision_id: UUID, payload_hash: str, organization_id: UUID
    ) -> ApprovalDecision | None:
        """The consent for this exact `(revision_id, payload_hash)` pair, or `None`.

        BOTH halves are part of the key and neither has a default. The revision identifier alone
        would not protect against content swapped under one id (L5218, and migration 0003 says
        the same at `approval_decisions`); the digest alone is worse, because
        `source_observed_at` is a field of the revision and NOT an input of `ApprovalHashInput`,
        so a fresh read and a stale read of the same content share one digest. Searching by
        digest would let the consent given for the fresh read authorize the stale one.

        AND IT RETURNS CONSENT ONLY — never a refusal. `decision = APPROVED` is in the same WHERE,
        which is the single condition telling the two apart anywhere in this module: without it
        this method hands back the operator's REJECTION, and every caller reads a returned
        `ApprovalDecision` as "the human agreed". `tests/test_approval_persistence.py::
        test_a_rejection_is_not_a_consent` builds a revision whose only decision row says
        `REJECTED` and asks for exactly the pair that row carries.

        The digest half is measured by `test_a_consent_is_not_found_for_a_different_digest_under_
        the_same_revision_id`, whose two calls differ in the hash and in nothing else — a case
        that differed in the revision id as well would be refused by the other half of the key and
        would never reach the condition it claims to test.
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                _DECISION_BY_REVISION_AND_HASH_SQL,
                organization_id,
                revision_id,
                payload_hash,
                APPROVED_DECISION,
            )
        return _decision_of(row) if row is not None else None

    async def fetch_intent(
        self, intent_id: UUID, *, organization_id: UUID
    ) -> ExecutionIntent | None:
        """One intent of THIS organization, read from the row — never from a cached object."""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(_INTENT_BY_ID_SQL, intent_id, organization_id)
        return _intent_of(row) if row is not None else None

    async def fetch_queue(
        self, *, organization_id: UUID, limit: int = 200
    ) -> tuple[ApprovalQueueEntry, ...]:
        """The approval queue of this organization, ascending by `expires_at` (L4595).

        NO STATE FILTER, by default or otherwise. The rule is `api/audit.py`'s, for the same
        reason: a default filter is the most comfortable way to build a list nobody can trust —
        the page returns rows, looks alive, and a whole category is invisible with no error. The
        state is a COLUMN of every entry, so a reader can see what each row is.
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(_QUEUE_SQL, organization_id, limit)
        return tuple(_queue_entry_of(row) for row in rows)

    # -- internals -----------------------------------------------------------

    async def _transition_within_transaction(
        self,
        connection: asyncpg.Connection,
        *,
        revision: ActionProposalRevision,
        organization_id: UUID,
        workspace_id: UUID,
        target: ActionProposalState,
        actor_ref: str,
        payload: dict[str, str],
    ) -> ActionProposalRevision:
        """The CAS plus its one audit row. Called only from inside an open transaction.

        Zero updated rows is a refusal: the source state in the WHERE means the row moved under
        our hands, and `"UPDATE 0"` from the driver would otherwise read as a state change that
        never happened.
        """
        moved = await connection.fetchrow(
            _TRANSITION_REVISION_SQL,
            revision.id,
            organization_id,
            revision.state.value,
            target.value,
        )
        if moved is None:
            raise RevisionTransitionRefused(
                f"rewizja {revision.id} nie jest już w stanie {revision.state.value} — zero "
                f"zaktualizowanych wierszy przy przejściu do {target.value}. To ODMOWA, nie "
                "sukces: wiersz zmienił się pod ręką. Odczytaj go ponownie i zdecyduj"
            )
        await AuditWriter(connection).record(
            kind=REVISION_TRANSITION_KIND,
            organization_id=organization_id,
            workspace_id=workspace_id,
            subject_ref=f"revision:{revision.id}",
            actor_ref=actor_ref,
            payload={
                "proposal_revision_id": str(revision.id),
                "payload_hash": revision.payload_hash,
                "from_state": revision.state.value,
                "to_state": target.value,
                **payload,
            },
        )
        row = await connection.fetchrow(_REVISION_BY_ID_SQL, revision.id, organization_id)
        return _revision_of(row)

    async def _record_consumption(
        self,
        connection: asyncpg.Connection,
        *,
        intent: ExecutionIntent,
        organization_id: UUID,
        actor_ref: str,
    ) -> None:
        """The one audit row of a consumption, in the transaction that burned the grant."""
        await AuditWriter(connection).record(
            kind=INTENT_CONSUMED_KIND,
            organization_id=organization_id,
            subject_ref=f"intent:{intent.id}",
            actor_ref=actor_ref,
            payload={
                "intent_id": str(intent.id),
                "proposal_revision_id": str(intent.revision_id),
                "payload_hash": intent.payload_hash,
            },
        )

    async def _refuse_consumption(
        self, connection: asyncpg.Connection, intent_id: UUID, organization_id: UUID
    ) -> NoReturn:
        """Name the reason the CAS matched nothing. Always raises; never returns.

        The row is re-read only to build the message. The DECISION was already made by the CAS —
        a refusal deduced from a later read would be a second, weaker check that could disagree
        with the one that counted.
        """
        row = await connection.fetchrow(_INTENT_BY_ID_SQL, intent_id, organization_id)
        if row is None:
            raise ApprovalStoreError(
                f"nie ma intentu {intent_id} w organizacji {organization_id} — nie ma czego zużyć"
            )
        if row["consumed_at"] is not None:
            raise IntentAlreadyConsumed(
                f"ExecutionIntent {intent_id} został już zużyty o "
                f"{row['consumed_at'].isoformat()}. Jeden intent = najwyżej jedna próba submitu "
                "(L2821). Ta odmowa pochodzi z bazy, nie z obiektu w pamięci"
            )
        raise IntentExpired(
            f"ExecutionIntent {intent_id} wygasł o {row['expires_at'].isoformat()}. Stary grant "
            "wykonania jest jednorazowy i wygasa (L5692)"
        )
