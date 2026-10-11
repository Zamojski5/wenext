"""A consent issued by a delegation instead of by a person at a screen (P4).

THIS FILE GUARDS THE ONE PLACE WHERE NOBODY READ THE TEXT. Everywhere else in this product a
publication rests on a human decision about that exact payload hash. Here it rests on a signature
about a SCOPE, and the whole question is whether the scope is honoured exactly, at the moment of
the decision, and not a moment earlier.

WHAT IS MEASURED, and the failure each one is the opposite of:

  * AUTONOMOUS issues a consent and a queue entry, citing the delegation AND the scope digest.
    A decision that cited only "the delegation allowed it" could not say what it allowed;
  * REVIEW issues nothing and leaves the proposal for a person. A mode that publishes anyway is
    the worst bug this milestone could have;
  * RESEARCH refuses outright;
  * REVOKING STOPS PROPOSALS THAT ARE ALREADY PENDING. The operator's stop button is useless if
    it only applies to work that has not been thought of yet;
  * OUT OF SCOPE is refused per dimension: another account, another community;
  * THE BUDGET HOLDS UNDER CONCURRENCY. Two ticks considering at the same time must not both
    find room for the last publication of the day. Run concurrently, because the sequential
    version of this test passes against an implementation that counts on one connection and
    writes on another.

Nothing here talks to a model or to Reddit. The proposal is seeded directly, exactly as the
`draft.create` tool would have left it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.pilot.publication import PublicationStore
from control_plane.presence.approval import FOR_A_HUMAN, ISSUED, REFUSED, DelegatedApprover
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore

pytestmark = [pytest.mark.db, pytest.mark.p4]

COMMUNITY = "r/devops"
THREAD = "abc123"


def now() -> datetime:
    return datetime.now(UTC)


async def _seed(pool, tenant, *, mode: str = "AUTONOMOUS", budget=None, communities=None):
    """A project with an account, a delegation, and one observed thread to aim at."""
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'bądź pomocny') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
        community_id = await conn.fetchval(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', $3, 'Reddit', 'ACCOUNT_REQUIRED', 600) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            COMMUNITY,
        )
        await conn.execute(
            """
            INSERT INTO observed_threads
                (organization_id, workspace_id, community_id, external_id, url, title, body,
                 author, created_at_source, first_observed_at, last_observed_at, raw_sha256)
            VALUES ($1, $2, $3, $4, $5, 'tytuł', 'treść', 'op', now(), now(), now(), $6)
            """,
            tenant["organization"],
            tenant["workspace"],
            community_id,
            THREAD,
            f"https://www.reddit.com/{COMMUNITY}/comments/{THREAD}/x/",
            "0" * 64,
        )

    presence = PresenceStore(pool)
    delegation = await presence.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode=mode,
        scope=parse_scope(
            {
                "accounts": [str(account_id)],
                "communities": [COMMUNITY] if communities is None else communities,
                "actions": ["REPLY"],
            }
        ),
        budget=parse_budget(budget if budget is not None else {"publications_per_day": 6}),
        operator_id=tenant["operator"],
    )
    return {"project": project_id, "account": account_id, "delegation": delegation}


async def _propose(
    pool,
    tenant,
    seeded,
    *,
    text: str = "odpowiedź agenta",
    account_id: UUID | None = None,
    target: str | None = None,
    expires_in: timedelta = timedelta(hours=6),
) -> UUID:
    """One proposal waiting for a consent, exactly as `draft.create` leaves it."""
    store = ApprovalStore(pool)
    account = account_id or seeded["account"]
    hash_input = ApprovalHashInput(
        organization=tenant["organization"],
        workspace=tenant["workspace"],
        assignment_version=uuid4(),
        account=account,
        platform="REDDIT",
        action_type=SemanticCapability.SUBMIT_COMMENT,
        target_external_id=target or f"t3_{THREAD}",
        target_url=f"https://www.reddit.com/{COMMUNITY}/comments/{THREAD}/x/",
        source_fingerprint=f"session:{uuid4()}",
        text_and_media=text,
        intelligence_profile_version=uuid4(),
        plan_version=seeded["project"],
        memory_snapshot=uuid4(),
        adapter_version="wenext-session-draft/1",
    )
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=account,
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=hash_input,
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=now() + expires_in,
    )
    return revision.id


async def _queued(pool, seeded) -> int:
    """How many requests THIS project has in the queue.

    Scoped to the project, never `count(*)`: the throwaway PostgreSQL lives for the whole run and
    every pilot test leaves its own rows behind, so a global count measures the suite's history
    rather than this test. Measured on 2026-09-19, when it counted 37 strangers.
    """
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM publication_requests WHERE project_id = $1", seeded["project"]
        )


def _approver(pool, tenant) -> DelegatedApprover:
    return DelegatedApprover(
        pool=pool,
        presence=PresenceStore(pool),
        approvals=ApprovalStore(pool),
        publication=PublicationStore(pool),
        organization_id=tenant["organization"],
    )


async def test_autonomous_issues_a_consent_that_names_what_permitted_it(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    revision_id = await _propose(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [ISSUED], considered
    one = considered[0]
    assert one.revision_id == revision_id
    assert one.request_id is not None

    delegation = seeded["delegation"]
    assert str(delegation.id) in one.reason, "decyzja wskazuje delegację"
    assert delegation.scope_sha256 in one.reason, "i dokładnie tę wersję zakresu"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, delegation_id, run_id, reaction_id, status, consent_source "
            "FROM publication_requests WHERE id = $1",
            one.request_id,
        )
    assert row["project_id"] == seeded["project"]
    assert row["delegation_id"] == delegation.id
    assert row["run_id"] is None and row["reaction_id"] is None, "to nie jest wniosek pilota"
    assert row["status"] == "QUEUED", "nic nie zostało opublikowane, tylko ustawione w kolejce"
    assert delegation.scope_sha256 in row["consent_source"]


async def test_review_mode_leaves_every_text_to_a_person(pool, tenant) -> None:
    """The mode a careful founder starts with. If this ever issues, P4 publishes behind a person
    who explicitly asked to see everything first."""
    seeded = await _seed(pool, tenant, mode="REVIEW")
    await _propose(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [FOR_A_HUMAN]
    assert all(one.request_id is None for one in considered)
    assert await _queued(pool, seeded) == 0


async def test_research_mode_refuses_to_publish(pool, tenant) -> None:
    seeded = await _seed(pool, tenant, mode="RESEARCH")
    await _propose(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [REFUSED]
    assert "RESEARCH" in considered[0].reason


async def test_revoking_stops_a_proposal_that_is_already_pending(pool, tenant) -> None:
    """THE STOP BUTTON, measured on work that already exists.

    The proposal is written first and the delegation revoked afterwards, which is the order that
    actually happens: the agent drafts while the founder is asleep, and they revoke in the
    morning before anything went out.
    """
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await PresenceStore(pool).revoke(
        seeded["delegation"].id, organization_id=tenant["organization"], at=now()
    )

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [FOR_A_HUMAN]
    assert "delegacj" in considered[0].reason.lower()
    assert await _queued(pool, seeded) == 0


async def test_pausing_stops_a_proposal_that_is_already_pending(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await PresenceStore(pool).set_paused(
        seeded["delegation"].id, organization_id=tenant["organization"], paused=True, at=now()
    )

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [FOR_A_HUMAN]
    assert "wstrzymana" in considered[0].reason


async def test_an_account_outside_the_scope_is_refused(pool, tenant) -> None:
    """A second account assigned to the same project, and absent from the signature."""
    seeded = await _seed(pool, tenant)
    async with pool.acquire() as conn:
        other = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"inne-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            seeded["project"],
            other,
        )
    await _propose(pool, tenant, seeded, account_id=other)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [REFUSED]
    assert str(other) in considered[0].reason


async def test_a_community_outside_the_scope_is_refused(pool, tenant) -> None:
    seeded = await _seed(pool, tenant, communities=["r/gdzie-indziej"])
    await _propose(pool, tenant, seeded)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [REFUSED]
    assert COMMUNITY in considered[0].reason


async def test_a_target_we_cannot_place_in_a_community_is_refused(pool, tenant) -> None:
    """A scope names communities, so a target that is in none of ours cannot be checked against
    it. Refusing is the only reading that does not quietly widen the signature."""
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded, target="t3_nieznany")

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert [one.outcome for one in considered] == [REFUSED]
    assert "nieznany" in considered[0].reason


async def test_the_budget_holds_when_two_ticks_consider_at_the_same_time(pool, tenant) -> None:
    """TWO TICKS, ONE SLOT LEFT, RUN CONCURRENTLY.

    This is the test the whole shape of `budget.py` exists for. Counting on one connection and
    writing on another passes every sequential test and fails here: both ticks count zero
    publications, both find room, and the delegation's limit of one is spent twice.
    """
    seeded = await _seed(pool, tenant, budget={"publications_per_day": 1})
    await _propose(pool, tenant, seeded, text="pierwszy")
    await _propose(pool, tenant, seeded, text="drugi")

    moment = now()
    first, second = await asyncio.gather(
        _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"], project_id=seeded["project"], now=moment
        ),
        _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"], project_id=seeded["project"], now=moment
        ),
    )
    issued = [one for one in (*first, *second) if one.outcome == ISSUED]
    queued = await _queued(pool, seeded)
    assert queued == 1, f"limit doby = 1, a w kolejce {queued}"
    assert len(issued) == 1, issued

    refused = [one for one in (*first, *second) if one.outcome == REFUSED]
    assert refused, "druga tura musi POWIEDZIEĆ, że budżet jest wyczerpany"
    assert "budżet" in refused[0].reason.lower()


async def test_an_expired_proposal_is_not_revived_by_a_delegation(pool, tenant) -> None:
    """A consent window that closed on its own stays closed. A delegation buys a decision on a
    live proposal, never a second life for a dead one."""
    seeded = await _seed(pool, tenant)
    # A SHORT WINDOW AND A REAL WAIT, because neither shortcut is available: the revision body is
    # immutable by trigger (L2795, `state` is the only updatable column), and a CHECK refuses a
    # revision that is born expired. So the consent gets a second of life and this test spends
    # it. The alternative is a fake clock in the database, which would measure the fake.
    await _propose(pool, tenant, seeded, expires_in=timedelta(seconds=1))
    await asyncio.sleep(1.2)

    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
    )
    assert considered == ()
    assert await _queued(pool, seeded) == 0


async def test_a_proposal_already_in_the_queue_is_not_considered_twice(pool, tenant) -> None:
    """Two ticks over the same finished draft must not queue it twice: the revision is unique in
    `publication_requests`, but arriving at that constraint means a consent was recorded first."""
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    approver = _approver(pool, tenant)
    assert [
        one.outcome
        for one in await approver.consider_pending(
            workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
        )
    ] == [ISSUED]
    assert (
        await approver.consider_pending(
            workspace_id=tenant["workspace"], project_id=seeded["project"], now=now()
        )
        == ()
    )
