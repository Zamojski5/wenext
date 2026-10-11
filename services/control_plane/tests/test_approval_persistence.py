"""The approval path against a REAL PostgreSQL — DoD L5217-5218, L5539, L2795.

WHAT THIS FILE IS FOR, in one sentence: every rule of the approval path was already modelled by
type in `contracts.execution`, and every one of those types is FROZEN, so until package P9 the
whole path was a set of correct objects that nobody wrote down.

THE TEST THAT DOES NOT EXIST ELSEWHERE, and why the one that looks like it is not it.
`contracts/tests/test_invariants.py::test_editing_content_invalidates_the_approval` asserts

    approval.matches(original) is True
    approval.matches(after_edit) is False

— that is, the OLD consent does not match the NEW revision. Both objects are built by hand in
that test, and `after_edit` is an independent revision, not the product of `supersede`. That
direction is real and it is not the one that leaks. The leaking direction is the other one: the
OLD consent still matches the OLD revision perfectly — same id, same digest — and the ONLY thing
that stops the old content from being published is the previous revision's row having moved to
`SUPERSEDED`. `supersede()` hands that row back as a second return value, and a caller who
ignores it leaves `APPROVED` in the database. Nothing in `contracts` can notice, because nothing
in `contracts` has a database. `test_the_previous_approval_no_longer_authorizes_execution` below
loads the revision back OUT OF POSTGRES and asks for an authorization.

EVERY TEST HERE CAN FAIL. That is not a figure of speech in this repository: the red team measured
an egress-policy suite in which deleting the real mechanism stayed green through 36 tests, because
every case in the table was already caught by an earlier condition. So each test below either
carries a POSITIVE CONTROL (the mechanism is shown saying "yes" as well as "no") or asserts a
COUNT before asserting a property — `all([])` is `True` and has produced four false passes here.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from community_os_contracts import SemanticCapability
from community_os_contracts.execution import (
    ActionProposalRevision,
    ActionProposalState,
    ApprovalHashInput,
)
from community_os_contracts.transitions import IllegalTransition
from control_plane.approval import store as store_module
from control_plane.approval.store import (
    GRANTABLE_REVISION_STATES,
    ApprovalStore,
    ApprovalStoreError,
    RevisionNotGrantable,
    RevisionTransitionRefused,
)
from control_plane.audit import AuditWriter, ForbiddenAuditEntry
from pydantic import ValidationError

pytestmark = [pytest.mark.db]

#: FIXED identifiers, not `uuid4()` defaults. Two calls to `_hash_input()` must produce the SAME
#: digest, because one whole test is about two revisions that share one — with random defaults that
#: test would pass by never having built the case it describes.
_ORGANIZATION = UUID("11111111-1111-4111-8111-111111111111")
_WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
_ASSIGNMENT = UUID("33333333-3333-4333-8333-333333333333")
_ACCOUNT = UUID("44444444-4444-4444-8444-444444444444")
_PROFILE_VERSION = UUID("55555555-5555-4555-8555-555555555555")
_PLAN_VERSION = UUID("66666666-6666-4666-8666-666666666666")
_MEMORY_SNAPSHOT = UUID("77777777-7777-4777-8777-777777777777")


def _hash_input(**overrides) -> ApprovalHashInput:
    """The fourteen inputs of the approval digest (L2778-2793), with everything pinned."""
    fields: dict[str, object] = {
        "organization": _ORGANIZATION,
        "workspace": _WORKSPACE,
        "assignment_version": _ASSIGNMENT,
        "account": _ACCOUNT,
        "platform": "linkedin",
        "action_type": SemanticCapability.PUBLISH_REPLY,
        "target_external_id": "urn:li:activity:7000000000000000001",
        "target_url": "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/",
        "source_fingerprint": "sha256:zrodlo-1",
        "text_and_media": "Pierwsza wersja odpowiedzi.",
        "intelligence_profile_version": _PROFILE_VERSION,
        "plan_version": _PLAN_VERSION,
        "memory_snapshot": _MEMORY_SNAPSHOT,
        "adapter_version": "linkedin-adapter@0.1.0",
    }
    fields.update(overrides)
    return ApprovalHashInput(**fields)


async def _db_now(pool):
    """The DATABASE clock. Every deadline in this file is computed from it, never from the host.

    Not a preference: `action_proposal_revisions` has `CHECK (expires_at > created_at)` and
    `created_at` is written from `now()` on the server. A TTL computed from the test process's
    clock is wrong by however much the two disagree, and it fails as a constraint violation that
    looks like a bug in the code under test.
    """
    async with pool.acquire() as connection:
        return await connection.fetchval("SELECT now()")


async def _social_account(pool, organization_id: UUID) -> UUID:
    """One `social_accounts` row — `action_proposals` has a composite FK to it (D-020)."""
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO social_accounts (organization_id, platform, external_account_id)
            VALUES ($1, 'linkedin', $2)
            RETURNING id
            """,
            organization_id,
            f"external-{uuid4()}",
        )


async def _seed_revision(
    store: ApprovalStore,
    pool,
    tenant,
    *,
    proposal_id: UUID,
    state: ActionProposalState,
    ttl: timedelta,
    revision_no: int = 1,
    hash_input: ApprovalHashInput | None = None,
    source_observed_at=None,
) -> ActionProposalRevision:
    """One revision through the store, with `expires_at` taken off the database clock."""
    moment = await _db_now(pool)
    return await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=revision_no,
        hash_input=hash_input or _hash_input(),
        state=state,
        expires_at=moment + ttl,
        source_observed_at=source_observed_at,
    )


async def _approved_revision(store: ApprovalStore, tenant, pool, **overrides):
    """A proposal plus one revision the operator has really approved. Returns the revision.

    Goes through `record_decision`, not through raw SQL: the pair "consent row plus the revision
    in `APPROVED`" is one fact and the tests below depend on it being written as one.
    """
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
        **overrides,
    )
    _decision, approved = await store.record_decision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=ActionProposalState.APPROVED.value,
        operator_id=tenant["operator"],
    )
    return account, proposal_id, approved


async def _force_state(pool, revision_id: UUID, state: ActionProposalState) -> None:
    """Put one revision into `state` with raw SQL, bypassing the transition machine on purpose.

    These tests are about what `issue_intent` does with a revision IN a state, not about how it
    got there — the arrows themselves are `contracts.transitions.require_transition`'s job and
    `test_a_transition_the_row_has_already_left_is_refused_not_reported_as_success` measures the
    CAS. Driving every state through legal arrows would also be impossible: nothing legal reaches
    `EXPIRED` from `APPROVED` in this module.

    The update touches `state` and nothing else, which is exactly what the immutability trigger of
    migration 0003 permits — so this helper cannot be used to smuggle content past it. The row
    count is asserted: `asyncpg` reports an `UPDATE` that matched nothing as `"UPDATE 0"` and
    raises nothing, and a preparation step that silently changed no row would leave every
    assertion below describing a case that was never built.
    """
    async with pool.acquire() as connection:
        changed = await connection.fetchval(
            "UPDATE action_proposal_revisions SET state = $2 WHERE id = $1 RETURNING id",
            revision_id,
            state.value,
        )
    assert changed == revision_id, (
        f"przygotowanie testu nie przestawiło rewizji {revision_id} na {state.value} — nie "
        "zmieniło ani jednego wiersza, więc dalsze asercje opisują przypadek, którego nie ma"
    )


async def _insert_decision(
    pool,
    tenant,
    *,
    revision_id: UUID,
    decision: str,
    digest: str,
    reason: str | None = None,
) -> UUID:
    """One `approval_decisions` row written PAST the store, so its columns can be chosen freely.

    `record_decision` reads `approved_payload_hash` off the revision and derives `decision` from
    the transition it performs — which is correct, and which is also why it cannot build the rows
    the tests below need: a consent naming a digest the revision does not carry. Those rows are
    what a botched migration, a console session or a future second writer would leave behind, and
    the point of the tests is that the lookup refuses them.

    `approval_decisions_one_per_revision` allows exactly one row per revision, so a caller of this
    helper must pick a revision the store has not decided yet.
    """
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO approval_decisions
                (organization_id, workspace_id, proposal_revision_id, decision,
                 approved_payload_hash, approved_by, reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            revision_id,
            decision,
            digest,
            tenant["operator"],
            reason,
        )


async def _stranger_tenant(pool) -> dict[str, UUID]:
    """A SECOND organization with its own workspace. No operator: nothing here logs in.

    E1 runs with exactly one organization (D-016), so this second one is built by hand and proves
    the SCOPE of the queries, not the behaviour of a real second tenant — the module docstring of
    `approval/store.py` says the same thing in the same words.
    """
    async with pool.acquire() as connection:
        organization_id = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obcy-tenant-sciezka-zgody') RETURNING id"
        )
        workspace_id = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws-obcy') RETURNING id",
            organization_id,
        )
    return {"organization": organization_id, "workspace": workspace_id}


async def _count_grants(pool, revision_id: UUID) -> int:
    """How many `execution_intents` rows point at this revision. Zero is a real answer here."""
    async with pool.acquire() as connection:
        return await connection.fetchval(
            "SELECT count(*) FROM execution_intents WHERE proposal_revision_id = $1", revision_id
        )


#: Content nobody consented to. Its digest is well-formed (the column has
#: `CHECK (approved_payload_hash ~ '^[0-9a-f]{64}$')`), 64 characters long and DIFFERENT from the
#: one `_hash_input()` produces — every test using it asserts that difference rather than assuming
#: it, because a digest that accidentally matched would make the case vanish while the test stayed
#: green.
_UNAPPROVED_TEXT = "Treść, na którą operator nigdy się nie zgodził."


# ---------------------------------------------------------------------------
# The edit path — L2795, L5310, L5539
# ---------------------------------------------------------------------------


async def test_editing_content_creates_a_new_revision_and_supersedes_the_previous(
    pool, tenant
) -> None:
    """Both facts are read back OUT OF POSTGRES, not off the objects `supersede` returned.

    Reading the return values would prove that `contracts` works, which
    `contracts/tests/test_supersede.py` already does. What is unproven until here is that the
    previous revision's row LEFT `APPROVED` — the frozen model cannot do that, and the second
    return value of `supersede` is trivially droppable.
    """
    store = ApprovalStore(pool)
    _account, proposal_id, approved = await _approved_revision(store, tenant, pool)
    assert approved.state is ActionProposalState.APPROVED

    await store.edit_into_new_revision(
        revision_id=approved.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=_hash_input(text_and_media="Zredagowana odpowiedź."),
        actor_ref=f"operator:{tenant['operator']}",
    )

    # Re-read. Both of these are fresh SELECTs.
    previous = await store.fetch_revision(approved.id, organization_id=tenant["organization"])
    assert previous is not None
    assert previous.state is ActionProposalState.SUPERSEDED, (
        "poprzednia rewizja została w APPROVED po edycji — zgoda operatora nadal autoryzuje "
        "treść, której on już nie widzi (L2795, L5218)"
    )

    entries = await store.fetch_queue(organization_id=tenant["organization"])
    # Cardinality BEFORE any property: with an empty tuple every statement below is vacuous.
    assert len(entries) == 2, entries
    successors = [entry for entry in entries if entry.revision_id != approved.id]
    assert len(successors) == 1, entries
    successor = successors[0]

    assert successor.proposal_id == proposal_id
    assert successor.revision_no == approved.revision_no + 1 == 2
    assert successor.state is ActionProposalState.DRAFT, (
        "następca powstał w stanie innym niż DRAFT — nowa rewizja wymaga WŁASNEJ zgody, i to "
        "jest dokładnie znaczenie zdania „edycja unieważnia poprzedni approval” (L2795)"
    )
    assert successor.payload_hash != previous.payload_hash
    assert len(successor.payload_hash) == 64

    # An edit of an already superseded revision is refused, so a chain of edits cannot fork.
    with pytest.raises(IllegalTransition):
        await store.edit_into_new_revision(
            revision_id=approved.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            new_hash_input=_hash_input(text_and_media="Trzecia wersja."),
            actor_ref=f"operator:{tenant['operator']}",
        )


async def test_the_previous_approval_no_longer_authorizes_execution(pool, tenant) -> None:
    """The direction along which stale content leaks. THIS is the requirement's own test.

    The old consent still matches the old revision — same id, same digest, `matches()` returns
    `True` — so nothing about the consent stops the old text from going out. What stops it is the
    previous revision's row no longer being `APPROVED`, and only a persisted transition can do
    that.

    The POSITIVE CONTROL is the second half of this test and it is not decoration: without it,
    "the authorization was refused" would also be satisfied by an authorization path that refuses
    everything, which is a test with zero detection power dressed as a strong one.
    """
    store = ApprovalStore(pool)
    account, _proposal_id, approved = await _approved_revision(store, tenant, pool)
    moment = await _db_now(pool)
    intent = await store.issue_intent(
        revision_id=approved.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account,
        expires_at=moment + timedelta(minutes=30),
        actor_ref=f"operator:{tenant['operator']}",
    )

    # The consent is intact and still points at exactly this revision and this content.
    consent = await store.find_approval(
        revision_id=approved.id,
        payload_hash=approved.payload_hash,
        organization_id=tenant["organization"],
    )
    assert consent is not None
    assert consent.matches(approved) is True, (
        "zgoda przestała pasować do rewizji, którą naprawdę zatwierdzono — wtedy ten test "
        "mierzyłby coś innego, niż opisuje"
    )

    await store.edit_into_new_revision(
        revision_id=approved.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=_hash_input(text_and_media="Zredagowana odpowiedź."),
        actor_ref=f"operator:{tenant['operator']}",
    )

    with pytest.raises(PermissionError, match="SUPERSEDED"):
        await store.authorize_and_consume_intent(
            intent.id,
            organization_id=tenant["organization"],
            actor_ref=f"operator:{tenant['operator']}",
        )

    # The refused authorization did NOT burn the grant. A refusal that consumed the intent would
    # be indistinguishable from a successful submit for every later reader.
    unused = await store.fetch_intent(intent.id, organization_id=tenant["organization"])
    assert unused is not None
    assert unused.consumed_at is None, (
        "odmowa autoryzacji zużyła intent — po restarcie nikt nie odróżni „odmówiono” od „wykonano”"
    )

    # -- the positive control: the same path says YES for a revision nobody edited -------------
    fresh_account, _fresh_proposal, fresh = await _approved_revision(store, tenant, pool)
    fresh_intent = await store.issue_intent(
        revision_id=fresh.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=fresh_account,
        expires_at=moment + timedelta(minutes=30),
        actor_ref=f"operator:{tenant['operator']}",
    )
    authorization = await store.authorize_and_consume_intent(
        fresh_intent.id,
        organization_id=tenant["organization"],
        actor_ref=f"operator:{tenant['operator']}",
    )
    assert authorization.revision_id == fresh.id
    assert authorization.payload_hash == fresh.payload_hash
    assert authorization.consumed_intent.consumed_at is not None


async def test_a_new_revision_does_not_extend_the_original_ttl(pool, tenant) -> None:
    """L5539: "edycja draftu tworzy nową rewizję … i nie przedłuża pierwotnego TTL".

    Compared against the value the ORIGINAL revision was created with, read back from both rows.
    Comparing the two rows with each other alone would pass an implementation that extended both.
    """
    store = ApprovalStore(pool)
    _account, _proposal_id, approved = await _approved_revision(store, tenant, pool)
    original_expiry = approved.expires_at

    await store.edit_into_new_revision(
        revision_id=approved.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=_hash_input(text_and_media="Zredagowana odpowiedź."),
        actor_ref=f"operator:{tenant['operator']}",
    )

    entries = await store.fetch_queue(organization_id=tenant["organization"])
    assert len(entries) == 2, entries
    by_id = {entry.revision_id: entry for entry in entries}
    previous = by_id[approved.id]
    successor = next(entry for entry in entries if entry.revision_id != approved.id)

    assert previous.expires_at == original_expiry
    assert successor.expires_at == original_expiry, (
        f"następca wygasa o {successor.expires_at.isoformat()}, a pierwotna rewizja o "
        f"{original_expiry.isoformat()} — TTL został przedłużony edycją, czyli pętla edycji "
        "trzyma draft żywy poza oknem wątku bez końca (L5539, L5540)"
    )
    # Time really did pass between the two rows, so an extension would have been visible.
    assert successor.created_at >= previous.created_at
    assert successor.expires_at - successor.created_at <= previous.expires_at - previous.created_at


# ---------------------------------------------------------------------------
# The digest is not a scope — L5218
# ---------------------------------------------------------------------------


async def test_two_revisions_with_identical_content_and_different_source_observed_at_share_a_hash(
    pool, tenant
) -> None:
    """Checked in `contracts` FIRST, then in the database.

    `source_observed_at` is a field of `ActionProposalRevision` and it is NOT one of the inputs of
    `ApprovalHashInput`. So a revision built over a FRESH read of the source and one built over a
    STALE read of the same content have ONE digest between them, and the "obvious" index — find
    the consent by `payload_hash` — would let the operator's consent to the fresh read authorize
    the stale one. That is a wrong-content publication with a perfectly matching hash.
    """
    # The premise, asserted rather than assumed. If contracts ever put `source_observed_at` into
    # the digest, this test would be describing a case that no longer exists — and it must fail
    # here rather than keep passing for the wrong reason.
    assert "source_observed_at" in ActionProposalRevision.model_fields
    assert "source_observed_at" not in ApprovalHashInput.model_fields, (
        "source_observed_at wszedł do ApprovalHashInput — cały ten test opisuje wtedy sytuację, "
        "która nie istnieje, i trzeba go przepisać, a nie usunąć"
    )

    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    moment = await _db_now(pool)
    shared_input = _hash_input()

    stale = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
        revision_no=1,
        hash_input=shared_input,
        source_observed_at=moment - timedelta(hours=6),
    )
    fresh = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
        revision_no=2,
        hash_input=shared_input,
        source_observed_at=moment,
    )

    assert stale.id != fresh.id
    assert stale.payload_hash == fresh.payload_hash, (
        "dwie rewizje o identycznej treści i różnym source_observed_at dostały różne hashe — "
        "premisa tego testu przestała obowiązywać"
    )
    assert stale.source_observed_at < fresh.source_observed_at

    # The operator approves the FRESH read only.
    await store.record_decision(
        revision_id=fresh.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=ActionProposalState.APPROVED.value,
        operator_id=tenant["operator"],
    )

    # Positive control first: the pair that WAS approved is found.
    found = await store.find_approval(
        revision_id=fresh.id,
        payload_hash=fresh.payload_hash,
        organization_id=tenant["organization"],
    )
    assert found is not None
    assert found.revision_id == fresh.id

    # And the digest alone is NOT a key: the stale revision has no consent, even though its digest
    # is byte-for-byte the approved one.
    assert (
        await store.find_approval(
            revision_id=stale.id,
            payload_hash=stale.payload_hash,
            organization_id=tenant["organization"],
        )
        is None
    ), (
        "zgoda ze świeżego odczytu została znaleziona dla rewizji z odczytu NIEAKTUALNEGO — "
        "wyszukiwanie idzie po samym payload_hash, a hash nie niesie source_observed_at"
    )

    # The consequence, at the boundary that matters: no grant can be issued for the stale read.
    with pytest.raises(ApprovalStoreError, match="nie ma zgody"):
        await store.issue_intent(
            revision_id=stale.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )


def test_no_statement_looks_up_a_consent_by_the_payload_hash_alone() -> None:
    """A structural read of `approval/store.py`: the DoD's grep, as a test that runs every time.

    Every SQL string in that module which touches `approval_decisions` and mentions
    `approved_payload_hash` must name `proposal_revision_id` IN ITS `WHERE` CLAUSE. A statement
    keyed on the digest alone is the hole the test above describes, and a convention nobody checks
    stops holding — measured in this repository more than once (D-014, `rename_identifiers.py`).

    THE CLAUSE, not the statement. The first version of this test looked for the identifier
    anywhere in the string, and every one of these statements carries `proposal_revision_id` in its
    SELECT list — so a `WHERE` narrowed to the digest alone passed it. That was a scanner with zero
    detection power for the one edit it exists to catch; `probe.py`'s `consent_found_by_hash_alone`
    is what showed it.
    """
    source = pathlib.Path(inspect.getfile(store_module)).read_text("utf-8")
    tree = ast.parse(source)

    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "approval_decisions" in node.value
    ]
    # Cardinality before the predicate: an empty list satisfies every `all()` ever written.
    assert len(statements) >= 2, (
        f"znaleziono {len(statements)} zapytań dotykających approval_decisions — ten skaner "
        "przestał widzieć SQL tego modułu i od tej pory nie mierzy niczego"
    )

    keyed_on_the_digest = [
        statement for statement in statements if "approved_payload_hash" in statement
    ]
    assert len(keyed_on_the_digest) >= 1, statements

    offenders = []
    for statement in keyed_on_the_digest:
        _before, _sep, condition = statement.partition("WHERE")
        # An INSERT lists the column and has no WHERE at all; that is not a lookup.
        if not _sep:
            continue
        if "proposal_revision_id" not in condition:
            offenders.append(statement)
    assert not offenders, (
        "zapytanie szuka zgody po SAMYM payload_hash — para (revision_id, payload_hash) jest "
        "kluczem, a hash sam nie niesie source_observed_at: "
        + " | ".join(" ".join(statement.split()) for statement in offenders)
    )


async def test_a_consent_is_not_found_for_a_different_digest_under_the_same_revision_id(
    pool, tenant
) -> None:
    """The SECOND half of the pair `(revision_id, payload_hash)`, which nothing measured before.

    HOW THIS TEST WAS BUILT, because the construction is the whole of its detection power. The two
    calls below differ in the `payload_hash` argument and in NOTHING else — same store, same
    organization, same revision identifier. A case that also changed the revision id would be
    refused by `proposal_revision_id = $2` and would never reach `approved_payload_hash = $3`; it
    would look like a test of the digest and measure the identifier. That is the exact shape of the
    egress-policy suite in which 36 green tests survived the deletion of the real mechanism.

    Deleting `AND approved_payload_hash = $3` from `_DECISION_BY_REVISION_AND_HASH_SQL` left all 25
    tests of this package green before this one existed — so the DoD sentence "approval wskazuje
    DOKŁADNY payload hash" (L5218) rested on an unexercised condition.
    """
    store = ApprovalStore(pool)
    _account, _proposal_id, approved = await _approved_revision(store, tenant, pool)

    other_digest = _hash_input(text_and_media=_UNAPPROVED_TEXT).payload_hash()
    # The premise: the two digests really differ, and the second one is a well-formed digest and
    # not a placeholder the column would have rejected anyway.
    assert other_digest != approved.payload_hash
    assert len(other_digest) == len(approved.payload_hash) == 64

    # -- positive control: the pair the operator really approved IS found ----------------------
    found = await store.find_approval(
        revision_id=approved.id,
        payload_hash=approved.payload_hash,
        organization_id=tenant["organization"],
    )
    assert found is not None, (
        "zgoda na parę, którą operator naprawdę zatwierdził, nie została znaleziona — wtedy "
        "asercja poniżej byłaby spełniona przez wyszukiwanie, które nie znajduje NICZEGO"
    )
    assert found.approved_payload_hash == approved.payload_hash

    # -- the measurement: only the digest changes ----------------------------------------------
    missing = await store.find_approval(
        revision_id=approved.id,
        payload_hash=other_digest,
        organization_id=tenant["organization"],
    )
    assert missing is None, (
        "zgoda została znaleziona dla INNEGO payload hash pod tym samym revision_id — druga "
        "połowa pary nie jest w WHERE, więc zgoda wydana na jedną treść autoryzuje inną (L5218)"
    )


async def test_no_grant_is_issued_when_the_consent_names_another_digest(pool, tenant) -> None:
    """The same condition at the boundary that matters: `issue_intent`, not a read.

    `issue_intent` takes the digest off the revision, so the ARGUMENT cannot be varied here. What
    varies instead is the row: the consent names a digest of different content. Such a row cannot
    be produced by `record_decision` — which is the point, and the reason `_insert_decision`
    exists — but it is exactly what a console session, a botched migration or a second writer
    would leave, and the lookup is the only thing standing between it and a grant.

    The positive control is a SIBLING revision prepared identically, through the same raw insert,
    differing only in the digest the consent row carries. Without it, "the grant was refused" would
    also be satisfied by a `_insert_decision` that writes rows the lookup can never match — a
    broken fixture reading as a passing test.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )

    async def prepare(revision_no: int, *, consent_names_the_revision: bool):
        """One APPROVED revision whose consent row is written by hand with a chosen digest."""
        revision = await _seed_revision(
            store,
            pool,
            tenant,
            proposal_id=proposal_id,
            state=ActionProposalState.READY_FOR_APPROVAL,
            ttl=timedelta(hours=1),
            revision_no=revision_no,
        )
        digest = (
            revision.payload_hash
            if consent_names_the_revision
            else _hash_input(text_and_media=_UNAPPROVED_TEXT).payload_hash()
        )
        await _insert_decision(
            pool,
            tenant,
            revision_id=revision.id,
            decision=ActionProposalState.APPROVED.value,
            digest=digest,
        )
        await _force_state(pool, revision.id, ActionProposalState.APPROVED)
        return revision

    honest = await prepare(1, consent_names_the_revision=True)
    lying = await prepare(2, consent_names_the_revision=False)
    assert honest.payload_hash == lying.payload_hash, (
        "obie rewizje mają tę samą treść, więc jedyną różnicą między nimi jest hash wpisany do "
        "wiersza zgody — jeżeli hashe rewizji się rozjechały, ten test mierzy coś innego"
    )

    moment = await _db_now(pool)

    # -- positive control: the consent naming the revision's OWN digest buys a grant ------------
    granted = await store.issue_intent(
        revision_id=honest.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account,
        expires_at=moment + timedelta(minutes=30),
        actor_ref=f"operator:{tenant['operator']}",
    )
    assert granted.revision_id == honest.id
    assert granted.payload_hash == honest.payload_hash

    # -- the measurement: the consent names a digest of content nobody approved -----------------
    with pytest.raises(ApprovalStoreError, match="nie ma zgody"):
        await store.issue_intent(
            revision_id=lying.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )
    assert await _count_grants(pool, lying.id) == 0, (
        "grant powstał dla rewizji, której zgoda wskazuje INNY payload hash — to jest publikacja "
        "treści, na którą operator się nie zgodził, z poprawnie wyglądającym śladem zgody (L5218)"
    )


async def test_a_rejection_is_not_a_consent(pool, tenant) -> None:
    """`decision = $4` — the ONE condition telling an approval from a refusal in this module.

    A rejected revision carries a decision row whose `proposal_revision_id` and
    `approved_payload_hash` are exactly the pair a consent would carry: `record_decision` writes
    the revision's own digest for both outcomes, because the row records WHAT was decided about,
    not only that something was agreed. So the pair matches and the only column that differs is
    `decision` — which is what makes this the right shape for measuring that column, and what made
    its absence invisible: deleting `AND decision = $4` kept all 25 tests green, while
    `find_approval` returned the operator's REFUSAL to every caller that reads a returned
    `ApprovalDecision` as consent.

    `reason` is non-empty because migration 0003 has `rejection_carries_a_reason` (L5311). That
    constraint belongs to the schema, not to this test.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
    )
    decision, rejected = await store.record_decision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=ActionProposalState.REJECTED.value,
        operator_id=tenant["operator"],
        reason="Odpowiedź jest nie na temat wątku.",
    )
    assert rejected.state is ActionProposalState.REJECTED
    # The premise: the refusal row carries the SAME pair a consent would, so the lookup really
    # does have to distinguish them by `decision` and by nothing else.
    assert decision.revision_id == revision.id
    assert decision.approved_payload_hash == revision.payload_hash

    assert (
        await store.find_approval(
            revision_id=revision.id,
            payload_hash=revision.payload_hash,
            organization_id=tenant["organization"],
        )
        is None
    ), (
        "find_approval zwróciło ODRZUCENIE jako zgodę. Para (revision_id, payload_hash) pasuje w "
        "obu przypadkach — jedyne, co odróżnia zgodę od odmowy, to kolumna decision (L2774)"
    )

    # -- positive control: a sibling that WAS approved is found by the same call ----------------
    _sibling_account, _sibling_proposal, approved = await _approved_revision(store, tenant, pool)
    assert (
        await store.find_approval(
            revision_id=approved.id,
            payload_hash=approved.payload_hash,
            organization_id=tenant["organization"],
        )
        is not None
    ), "zgoda na zatwierdzoną rewizję nie została znaleziona — asercja powyżej byłaby wtedy pusta"

    # -- and at the boundary: with the state gate satisfied, the refusal still buys nothing -----
    # The state is forced so the refusal below comes from the DECISION column and not from
    # `GRANTABLE_REVISION_STATES` — a rejected revision would otherwise be stopped one gate later
    # and this half of the test would measure the wrong mechanism.
    await _force_state(pool, revision.id, ActionProposalState.APPROVED)
    moment = await _db_now(pool)
    with pytest.raises(ApprovalStoreError, match="nie ma zgody"):
        await store.issue_intent(
            revision_id=revision.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )
    assert await _count_grants(pool, revision.id) == 0, (
        "odrzucona treść dostała grant wykonania — odmowa operatora zadziałała jak zgoda"
    )


async def test_a_revision_of_another_organization_is_invisible_through_the_tenant_scope(
    pool, tenant
) -> None:
    """`AND organization_id = $2` in `_REVISION_BY_ID_SQL`, which every read of this module uses.

    `fetch_revision`'s docstring said "The scope is required, never a default" while deleting that
    condition kept all 25 tests of the package green: the sentence was a promise with no
    measurement behind it. It is measured here by asking for ONE revision twice and changing
    nothing but `organization_id`.

    TWO POSITIVE CONTROLS, and the second one is the less obvious and more important. The first is
    the usual one: the revision is readable under its OWN organization, so `None` below is not the
    answer this method gives to everything. The second proves the stranger's identifier is a LIVE
    scope and not a value nothing matches — a revision of the stranger's own is read back through
    it. Without that, an organization identifier that happened to be wrong would make the
    assertion pass for the wrong reason.
    """
    store = ApprovalStore(pool)
    account, _proposal_id, approved = await _approved_revision(store, tenant, pool)
    stranger = await _stranger_tenant(pool)
    assert stranger["organization"] != tenant["organization"]

    own = await store.fetch_revision(approved.id, organization_id=tenant["organization"])
    assert own is not None and own.id == approved.id

    # -- the stranger's scope is real: it reads the stranger's own revision back ----------------
    stranger_account = await _social_account(pool, stranger["organization"])
    stranger_proposal = await store.create_proposal(
        organization_id=stranger["organization"],
        workspace_id=stranger["workspace"],
        social_account_id=stranger_account,
    )
    stranger_revision = await _seed_revision(
        store,
        pool,
        stranger,
        proposal_id=stranger_proposal,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
    )
    assert (
        await store.fetch_revision(stranger_revision.id, organization_id=stranger["organization"])
        is not None
    ), "obca organizacja nie czyta nawet własnej rewizji — ten identyfikator nie jest zakresem"

    # -- the measurement: the same revision, the other organization -----------------------------
    assert (
        await store.fetch_revision(approved.id, organization_id=stranger["organization"]) is None
    ), (
        "rewizja tej organizacji jest czytelna pod cudzym organization_id — granica najemcy nie "
        "jest w WHERE, a ta ścieżka niesie treść draftów"
    )
    assert (
        await store.fetch_revision(stranger_revision.id, organization_id=tenant["organization"])
        is None
    ), "obca rewizja jest czytelna pod naszym organization_id — granica działa tylko w jedną stronę"

    # -- and at the boundary: no grant is issued through the wrong scope -------------------------
    # The message is matched on purpose. With the tenant condition gone, `issue_intent` finds the
    # revision and then fails to find its consent — it still refuses, with ANOTHER message, and an
    # assertion that only demanded "it raised" would stay green through the deletion.
    moment = await _db_now(pool)
    with pytest.raises(ApprovalStoreError, match="nie ma rewizji"):
        await store.issue_intent(
            revision_id=approved.id,
            organization_id=stranger["organization"],
            workspace_id=stranger["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref="operator:obcy",
        )
    assert await _count_grants(pool, approved.id) == 0


# ---------------------------------------------------------------------------
# The grant gate — a grant only for a revision that is STILL approved (L5321, L2795)
# ---------------------------------------------------------------------------


async def test_no_grant_is_issued_for_a_revision_superseded_by_an_edit(pool, tenant) -> None:
    """The leak this module's first paragraph promises to close, measured through the real path.

    `edit_into_new_revision` moves the previous revision to `SUPERSEDED` and KEEPS its consent row
    — correctly: the row records a decision that was really taken, and deleting it would destroy
    the record. So a lookup keyed on `(revision_id, payload_hash)` still finds a consent for the
    edited-away revision, and until this package `issue_intent` handed out a fresh grant for
    content the operator had already replaced. `authorize_side_effect` refuses that grant later,
    at consumption, but a grant that exists is a grant in the log and in every future dispatcher.

    Nothing here is forced with raw SQL: the `SUPERSEDED` state comes from the edit path itself.
    """
    store = ApprovalStore(pool)
    account, _proposal_id, approved = await _approved_revision(store, tenant, pool)
    moment = await _db_now(pool)

    # -- positive control first, on a sibling nobody edits: the same call says YES --------------
    fresh_account, _fresh_proposal, fresh = await _approved_revision(store, tenant, pool)
    granted = await store.issue_intent(
        revision_id=fresh.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=fresh_account,
        expires_at=moment + timedelta(minutes=30),
        actor_ref=f"operator:{tenant['operator']}",
    )
    assert granted.revision_id == fresh.id

    _superseded, successor = await store.edit_into_new_revision(
        revision_id=approved.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=_hash_input(text_and_media="Zredagowana odpowiedź."),
        actor_ref=f"operator:{tenant['operator']}",
    )

    # The consent for the edited-away revision is STILL there — that is the premise, not a defect.
    consent = await store.find_approval(
        revision_id=approved.id,
        payload_hash=approved.payload_hash,
        organization_id=tenant["organization"],
    )
    assert consent is not None, (
        "zgoda na poprzednią rewizję zniknęła po edycji — wtedy grant blokuje wyszukiwanie zgody, "
        "a nie bramka stanu, i ten test mierzy inny mechanizm, niż opisuje"
    )

    with pytest.raises(RevisionNotGrantable, match="SUPERSEDED"):
        await store.issue_intent(
            revision_id=approved.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )
    assert await _count_grants(pool, approved.id) == 0, (
        "grant powstał dla rewizji w stanie SUPERSEDED — właśnie tędy wycieka treść, którą "
        "operator zastąpił edycją (L2795, L5218)"
    )

    # The successor is refused for the OTHER reason: it is a DRAFT and nobody has approved it.
    with pytest.raises(ApprovalStoreError, match="nie ma zgody"):
        await store.issue_intent(
            revision_id=successor.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )

    async with pool.acquire() as connection:
        issued = await connection.fetchval(
            """
            SELECT count(*) FROM audit_events
            WHERE organization_id = $1 AND kind = 'EXECUTION_INTENT_ISSUED'
            """,
            tenant["organization"],
        )
    assert issued == 1, (
        f"{issued} wpisów o wystawieniu grantu przy jednym udanym wystawieniu — odmowa zostawiła "
        "ślad w dzienniku, czyli transakcja nie została wycofana"
    )


@pytest.mark.parametrize("state", list(ActionProposalState))
async def test_a_grant_is_issued_only_for_the_single_grantable_state(pool, tenant, state) -> None:
    """Every state of `ActionProposalState`, with the size of the allowed set asserted.

    THE CARDINALITY ASSERTIONS ARE THE POINT. The parametrization is exhaustive by construction —
    it iterates the enum — so a new state arrives as a new case. What that does NOT catch is the
    other edit: adding a member to `GRANTABLE_REVISION_STATES`, which widens the gate in one token
    and would leave every case here passing. Both counts are therefore written down as numbers, so
    either change arrives as a failure and has to be argued for.

    EVERY CASE CARRIES A CONSENT, and that is what makes this a test of the STATE gate. A revision
    put into `DRAFT` with no consent row is refused by the consent lookup one gate earlier, and the
    case would prove nothing about the state — the same trap that made an egress-policy suite
    survive the deletion of its mechanism through 36 tests. So each revision here goes through
    `record_decision` first and is only then forced into the state under test, and the consent is
    re-read afterwards to prove it survived.
    """
    assert len(ActionProposalState) == 6, (
        f"ActionProposalState ma {len(ActionProposalState)} stanów, a ten test opisuje sześć "
        "(L2773-2775) — dopisany stan wymaga decyzji, czy wolno dla niego wystawić grant"
    )
    assert len(GRANTABLE_REVISION_STATES) == 1, (
        f"GRANTABLE_REVISION_STATES ma {len(GRANTABLE_REVISION_STATES)} elementów zamiast "
        "jednego — bramka została poszerzona. To musi być decyzja, nie skutek uboczny"
    )
    assert frozenset({ActionProposalState.APPROVED}) == GRANTABLE_REVISION_STATES

    store = ApprovalStore(pool)
    account, _proposal_id, approved = await _approved_revision(store, tenant, pool)
    assert approved.state is ActionProposalState.APPROVED

    if state is not ActionProposalState.APPROVED:
        await _force_state(pool, approved.id, state)
    current = await store.fetch_revision(approved.id, organization_id=tenant["organization"])
    assert current is not None and current.state is state

    # The consent survived the state change. Without this the case would be stopped by the consent
    # lookup and would say nothing at all about the state gate.
    assert (
        await store.find_approval(
            revision_id=approved.id,
            payload_hash=approved.payload_hash,
            organization_id=tenant["organization"],
        )
        is not None
    ), (
        f"zgoda zniknęła przy przestawieniu rewizji na {state.value} — ten przypadek nie mierzy stanu"
    )

    moment = await _db_now(pool)

    async def issue():
        return await store.issue_intent(
            revision_id=approved.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account,
            expires_at=moment + timedelta(minutes=30),
            actor_ref=f"operator:{tenant['operator']}",
        )

    if state in GRANTABLE_REVISION_STATES:
        granted = await issue()
        assert granted.revision_id == approved.id
        assert await _count_grants(pool, approved.id) == 1
        return

    with pytest.raises(RevisionNotGrantable, match=state.value):
        await issue()
    assert await _count_grants(pool, approved.id) == 0, (
        f"grant powstał dla rewizji w stanie {state.value}, a jedynym stanem, w którym wolno go "
        "wystawić, jest APPROVED (L5321)"
    )


async def test_two_concurrent_edits_produce_one_successor_not_two(pool, tenant) -> None:
    """Two edits of one approved revision, in parallel, with DIFFERENT new content.

    Exactly one succeeds and exactly one is refused — both halves asserted, because "at least one
    failed" is also satisfied by a run in which everything failed.

    WHAT THIS TEST DOES AND DOES NOT MEASURE, established by breaking the code rather than by
    reasoning about it:

    *   MEASURED — the OUTCOME. One successor, one `SUPERSEDED` predecessor, two rows for the
        proposal. Two successors of one parent would be a fork in content the operator's consent
        pointed at singly.
    *   NOT MEASURED — WHICH layer refuses the loser. Three guards stand behind this outcome: the
        `FOR UPDATE` in the edit path's read, the state CAS (zero rows is a refusal) and
        `UNIQUE (proposal_id, revision)` in migration 0003. Which one fires depends on the
        interleaving, so the refusal arrives as `IllegalTransition` or as
        `RevisionTransitionRefused` and this test accepts either. An earlier version of it insisted
        on `IllegalTransition`; deleting the row lock left the whole suite GREEN anyway (probe
        `edit_without_row_lock`), which showed the insistence was timing-dependent rather than a
        measurement — a flaky assertion that would eventually be "repaired" into a vacuous one.
        Deleting the row lock is not a correctness regression here; it only moves the refusal a
        layer down. That is reported, not hidden.
    """
    store = ApprovalStore(pool)
    _account, proposal_id, approved = await _approved_revision(store, tenant, pool)

    async def edit(text: str):
        return await store.edit_into_new_revision(
            revision_id=approved.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            new_hash_input=_hash_input(text_and_media=text),
            actor_ref=f"operator:{tenant['operator']}",
        )

    outcomes = await asyncio.gather(
        edit("Wersja redaktora A."), edit("Wersja redaktora B."), return_exceptions=True
    )
    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]

    assert len(successes) == 1, f"{len(successes)} edycji przeszło jednocześnie: {outcomes}"
    assert len(failures) == 1, outcomes
    assert isinstance(failures[0], IllegalTransition | RevisionTransitionRefused), (
        f"przegrana edycja została odrzucona jako {type(failures[0]).__name__}: {failures[0]}. "
        "Oczekiwana jest odmowa z tabeli przejść albo z CAS-a stanu — cokolwiek innego znaczy, że "
        "przegrała z innego powodu, niż ten test opisuje"
    )

    async with pool.acquire() as connection:
        revisions = await connection.fetchval(
            "SELECT count(*) FROM action_proposal_revisions WHERE proposal_id = $1", proposal_id
        )
        superseded = await connection.fetchval(
            """
            SELECT count(*) FROM action_proposal_revisions
            WHERE proposal_id = $1 AND state = 'SUPERSEDED'
            """,
            proposal_id,
        )
    assert revisions == 2, (
        f"{revisions} rewizji po dwóch równoległych edycjach jednej rewizji — dwóch następców "
        "jednego rodzica to rozwidlenie treści, na którą zgoda wskazywała pojedynczo"
    )
    assert superseded == 1, superseded


#: The spellings of "ask this process what time it is". Listed here, in the TEST file, and
#: deliberately nowhere in `approval/store.py` — a docstring quoting them would make the scan below
#: find its own prose. `jobs/store.py` says the same thing about the same trap, having fallen into
#: it once.
_PROCESS_CLOCK_SPELLINGS: tuple[str, ...] = (
    "datetime.now(",
    "datetime.utcnow(",
    ".utcnow(",
    "time.time(",
    "time.monotonic(",
    "date.today(",
    "datetime.today(",
)


def test_no_moment_in_the_store_comes_from_the_process_clock() -> None:
    """Every deadline in the approval path is the DATABASE's, and this is how that is enforced.

    Migration 0003 compares these columns against each other — `expires_at > created_at` on a
    revision, `expires_at > issued_at` on an intent — so a moment taken from this process's clock
    while the neighbouring column comes from the server's is a constraint violation waiting for the
    two clocks to drift, and it will surface as a bug in whatever code happens to run at the time.
    """
    source = pathlib.Path(inspect.getfile(store_module)).read_text("utf-8")
    assert len(_PROCESS_CLOCK_SPELLINGS) >= 5, _PROCESS_CLOCK_SPELLINGS
    found = [spelling for spelling in _PROCESS_CLOCK_SPELLINGS if spelling in source]
    assert not found, (
        f"approval/store.py pyta o czas PROCES, nie bazę: {', '.join(found)}. Wszystkie terminy "
        "w tej ścieżce pochodzą z serwera (L2668), bo schemat porównuje te kolumny między sobą"
    )


async def test_a_row_whose_payload_and_digest_disagree_cannot_be_read_back(pool, tenant) -> None:
    """The digest column is verified against the stored content on every read.

    Why it is worth the SHA-256: `payload_hash` is what every check downstream compares against —
    the consent, the grant, the attempt. A row in which the column and the content disagree is a
    payload nobody approved wearing an approved digest, and it must be unreadable rather than
    plausible. The `CHECK` in migration 0003 only constrains the SHAPE of the column (64 hex
    characters), so it cannot notice this; `contracts.ActionProposalRevision`'s validator does.

    The mismatching row goes in with raw SQL, because no method of this store can produce one —
    which is the point.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    honest = _hash_input()
    async with pool.acquire() as connection:
        moment = await connection.fetchval("SELECT now()")
        forged = await connection.fetchval(
            """
            INSERT INTO action_proposal_revisions
                (organization_id, workspace_id, proposal_id, revision, payload, payload_hash,
                 source_fingerprint, expires_at, state, created_at, source_observed_at)
            VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'sha256:podmiana', $6, 'READY_FOR_APPROVAL',
                    $7, $7)
            RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
            proposal_id,
            honest.canonical_bytes().decode("utf-8"),
            # Well-formed and WRONG: it is the digest of different content.
            _hash_input(text_and_media="Treść, której nikt nie zatwierdził.").payload_hash(),
            moment + timedelta(hours=1),
            moment,
        )

    # The premise: the two really do differ, so the test is not vacuous.
    assert (
        _hash_input(text_and_media="Treść, której nikt nie zatwierdził.").payload_hash()
        != honest.payload_hash()
    )

    with pytest.raises(ValidationError, match="payload_hash"):
        await store.fetch_revision(forged, organization_id=tenant["organization"])


# ---------------------------------------------------------------------------
# Audit — one row per transition, in the transaction of the transition
# ---------------------------------------------------------------------------

#: The five transitions the sequence below performs, as (kind, from_state, to_state). Written out
#: by hand rather than derived from the code, so the assertion is a statement about the REQUIREMENT
#: and not a restatement of the implementation.
_EXPECTED_AUDIT: tuple[tuple[str, str | None, str | None], ...] = (
    ("APPROVAL_DECISION_RECORDED", "DRAFT", "READY_FOR_APPROVAL"),
    ("APPROVAL_DECISION_RECORDED", "READY_FOR_APPROVAL", "APPROVED"),
    ("EXECUTION_INTENT_ISSUED", None, None),
    ("EXECUTION_INTENT_CONSUMED", None, None),
    ("APPROVAL_DECISION_RECORDED", "APPROVED", "SUPERSEDED"),
)


async def test_every_state_transition_leaves_exactly_one_audit_row(pool, tenant) -> None:
    """Five transitions, five rows. The assertion COUNTS ROWS.

    Not "an entry exists" and not "at least one": both of those pass while a transition writes
    nothing, as long as some other transition wrote something. The organization comes fresh from
    the `tenant` fixture, so the count is of this test's own rows and of nothing else.

    KNOWN IMPRECISION, and it belongs in the test rather than only in the module: three of these
    five rows carry the kind `APPROVAL_DECISION_RECORDED` for something that is not a decision.
    `control_plane.audit.AUDIT_KINDS` is a closed vocabulary owned by another package and it has
    no value for "a revision changed state"; the payload carries `from_state`/`to_state`, so the
    log is queryable, but the category is wrong for `DRAFT → READY_FOR_APPROVAL` and for
    `APPROVED → SUPERSEDED`. Recorded here so the next reader knows it was seen and not missed.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.DRAFT,
        ttl=timedelta(hours=1),
    )
    actor = f"operator:{tenant['operator']}"

    await store.move_revision(
        revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        target=ActionProposalState.READY_FOR_APPROVAL,
        actor_ref=actor,
    )
    await store.record_decision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        decision=ActionProposalState.APPROVED.value,
        operator_id=tenant["operator"],
    )
    moment = await _db_now(pool)
    intent = await store.issue_intent(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account,
        expires_at=moment + timedelta(minutes=30),
        actor_ref=actor,
    )
    await store.consume_intent(intent.id, organization_id=tenant["organization"], actor_ref=actor)
    await store.edit_into_new_revision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=_hash_input(text_and_media="Zredagowana odpowiedź."),
        actor_ref=actor,
    )

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT kind, payload->>'from_state' AS from_state, payload->>'to_state' AS to_state
            FROM audit_events
            WHERE organization_id = $1
            ORDER BY occurred_at, id
            """,
            tenant["organization"],
        )

    observed = [(row["kind"], row["from_state"], row["to_state"]) for row in rows]
    # THE COUNT, first and on its own line, so a failure says how many rows were missing.
    assert len(observed) == len(_EXPECTED_AUDIT) == 5, (
        f"pięć przejść stanu zostawiło {len(observed)} wpisów audytowych: {observed}. "
        "Przejście bez wpisu to zmiana stanu, o której dziennik milczy"
    )
    assert sorted(observed) == sorted(_EXPECTED_AUDIT), observed


async def test_a_failing_audit_entry_rolls_back_the_decision_it_describes(
    pool, tenant, monkeypatch
) -> None:
    """The audit row and the change it describes share ONE commit. Measured, not asserted.

    `AuditWriter` is handed the caller's open transaction precisely so this holds; a writer with
    its own connection would leave the decision committed and the log silent, or the log written
    and the decision rolled back. Here the write is forced to fail and BOTH rows must be gone.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.READY_FOR_APPROVAL,
        ttl=timedelta(hours=1),
    )

    async def _explode(self, **_kwargs):  # a stub, not an implementation
        raise ForbiddenAuditEntry("wymuszona porażka zapisu audytu")

    monkeypatch.setattr(AuditWriter, "record", _explode)

    with pytest.raises(ForbiddenAuditEntry):
        await store.record_decision(
            revision_id=revision.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            decision=ActionProposalState.APPROVED.value,
            operator_id=tenant["operator"],
        )

    monkeypatch.undo()

    still_waiting = await store.fetch_revision(revision.id, organization_id=tenant["organization"])
    assert still_waiting is not None
    assert still_waiting.state is ActionProposalState.READY_FOR_APPROVAL, (
        "rewizja została w APPROVED, choć wpis audytowy padł — zmiana stanu przeżyła dziennik, "
        "który miał ją opisać"
    )
    async with pool.acquire() as connection:
        decisions = await connection.fetchval(
            "SELECT count(*) FROM approval_decisions WHERE proposal_revision_id = $1", revision.id
        )
    assert decisions == 0, (
        f"{decisions} wierszy zgody przeżyło porażkę zapisu audytu — obie zmiany nie są w jednej "
        "transakcji"
    )


async def test_a_transition_the_row_has_already_left_is_refused_not_reported_as_success(
    pool, tenant
) -> None:
    """Zero updated rows is a REFUSAL. `asyncpg` returns the string "UPDATE 0" and raises nothing.

    WHY THIS TEST REACHES PAST THE PUBLIC METHODS. `move_revision`, `record_decision` and
    `edit_into_new_revision` all read the row `FOR UPDATE` and run the CAS inside the SAME
    transaction, so the state they compare against cannot move under them — the zero-row branch is
    unreachable through the public surface. That makes it defence in depth, and defence in depth
    that nobody measures is a comment. So the CAS is driven directly, with a revision object whose
    state is deliberately stale, which is exactly the shape a future caller reusing a cached object
    would produce.

    A positive control comes first: the same call with a CURRENT object writes the row. Without it,
    "it raised" would also be satisfied by a method that raises unconditionally.
    """
    store = ApprovalStore(pool)
    account = await _social_account(pool, tenant["organization"])
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await _seed_revision(
        store,
        pool,
        tenant,
        proposal_id=proposal_id,
        state=ActionProposalState.DRAFT,
        ttl=timedelta(hours=1),
    )

    # -- positive control: a CURRENT object moves the row -------------------------------------
    async with pool.acquire() as connection, connection.transaction():
        moved = (
            await store._transition_within_transaction(  # private on purpose — see the docstring
                connection,
                revision=revision,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                target=ActionProposalState.READY_FOR_APPROVAL,
                actor_ref=f"operator:{tenant['operator']}",
                payload={},
            )
        )
    assert moved.state is ActionProposalState.READY_FOR_APPROVAL

    # -- the refusal: the SAME stale object, whose `state` the row has already left ------------
    with pytest.raises(RevisionTransitionRefused, match="ODMOWA"):
        async with pool.acquire() as connection, connection.transaction():
            await store._transition_within_transaction(  # private on purpose
                connection,
                # `revision` still says DRAFT; the row says READY_FOR_APPROVAL.
                revision=revision,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                target=ActionProposalState.READY_FOR_APPROVAL,
                actor_ref=f"operator:{tenant['operator']}",
                payload={},
            )

    # The refused CAS wrote no audit row for a transition that did not happen. Exactly one entry
    # exists: the one from the positive control above.
    async with pool.acquire() as connection:
        entries = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1",
            tenant["organization"],
        )
    assert entries == 1, (
        f"{entries} wpisów audytowych po jednym udanym i jednym odmówionym przejściu — dziennik "
        "opisuje zmianę stanu, która się nie stała"
    )

    # A terminal source state is refused earlier still, by contracts, before any SQL is sent.
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE action_proposal_revisions SET state = 'REJECTED' WHERE id = $1", revision.id
        )
    with pytest.raises(IllegalTransition):
        await store.move_revision(
            revision.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            target=ActionProposalState.READY_FOR_APPROVAL,
            actor_ref=f"operator:{tenant['operator']}",
        )

    # And a revision that is not there at all is a refusal too, never a silent no-op.
    with pytest.raises(RevisionTransitionRefused):
        await store.move_revision(
            uuid4(),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            target=ActionProposalState.READY_FOR_APPROVAL,
            actor_ref=f"operator:{tenant['operator']}",
        )
