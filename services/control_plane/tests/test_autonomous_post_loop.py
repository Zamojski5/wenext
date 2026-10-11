"""The new identity can publish, and the resulting feedback is useful in the next session.

PostgreSQL and the product publisher are real; the platform actuator below is a fixture.
These tests do not claim a Reddit publication or a model-quality evaluation.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from control_plane.approval.store import APPROVED_DECISION, ApprovalStore, ApprovalStoreError
from control_plane.knowledge.experience import measure_one
from control_plane.knowledge.store import KnowledgeStore
from control_plane.observe.store import ObservationStore
from control_plane.pilot.publisher import OUTCOME_PUBLISHED
from control_plane.presence.approval import ISSUED
from control_plane.presence.collector import EventCollector
from control_plane.presence.store import PresenceStore
from control_plane.tools.capability import issue
from control_plane.tools.registry import ToolRegistry
from test_knowledge_experience import claim, handlers_for, observe, scope_of
from test_presence_own_post import (
    ACCOUNT,
    BODY,
    COMMUNITY,
    TITLE,
    FakeOwnPostPort,
    _approver,
    _propose_own_post,
    _requests,
    _seed,
    _world,
    now,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


async def new_signer(pool, tenant, seeded):
    async with pool.acquire() as conn:
        user = await conn.fetchval(
            "INSERT INTO users(email, credential_hash) SELECT $1, credential_hash "
            "FROM operators WHERE id=$2 RETURNING id",
            f"post-{uuid4().hex}@example.test",
            tenant["operator"],
        )
        membership = await conn.fetchval(
            "INSERT INTO memberships(user_id, organization_id, role) "
            "VALUES ($1, $2, 'OWNER') RETURNING id",
            user,
            tenant["organization"],
        )
    store = PresenceStore(pool)
    old = seeded["delegation"]
    await store.revoke(old.id, organization_id=tenant["organization"], at=now())
    delegation = await store.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        mode=old.mode,
        scope=old.scope,
        budget=old.budget,
        operator_id=user,
        user_id=user,
    )
    assert delegation.signed_by is None and delegation.signed_membership_id == membership
    await ObservationStore(pool).decide_own_posts(
        seeded["community"],
        organization_id=tenant["organization"],
        allowed=True,
        operator_id=user,
        user_id=user,
        now=now(),
    )
    return user, membership, delegation


async def test_new_member_post_feedback_and_next_session_memory(pool, tenant):
    seeded = await _seed(pool, tenant)
    _, member, delegation = await new_signer(pool, tenant, seeded)
    revision = await _propose_own_post(pool, tenant, seeded)
    considered = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        revision_id=revision,
    )
    assert [one.outcome for one in considered] == [ISSUED]
    decision = await ApprovalStore(pool).find_approval(
        organization_id=tenant["organization"],
        revision_id=revision,
        payload_hash=(
            await ApprovalStore(pool).fetch_revision(
                revision,
                organization_id=tenant["organization"],
            )
        ).payload_hash,
    )
    assert decision.approved_by is None and decision.approved_membership_id == member
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())
    result = await world.handlers.run_once(now())
    assert result.outcome == OUTCOME_PUBLISHED, result
    request = (await _requests(pool, seeded))[0]["id"]
    await observe(pool, tenant, request, score=8, replies=2)
    measured = await measure_one(pool, organization_id=tenant["organization"], request_id=request)
    assert measured.draft == f"{TITLE}\n\n{BODY}"
    assert (measured.community_key, measured.platform, measured.account) == (
        COMMUNITY,
        "REDDIT",
        ACCOUNT,
    )
    assert measured.thread_title == TITLE and measured.project_id == seeded["project"]
    await handlers_for(pool, tenant).sweep_experience(claim(tenant))
    items = await KnowledgeStore(pool).list(scope_of(tenant, seeded["project"]), kind="EXPERIENCE")
    assert len(items) == 1
    assert BODY in items[0].payload["decision"]["excerpt"]
    assert items[0].payload["situation"]["decision_kind"] == "OWN_POST"
    assert items[0].payload["outcome"]["replies"] == 2
    next_session_tools = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=uuid4(),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            dsn="postgresql://fixture",
        ),
    )
    remembered = await next_session_tools._memory_search({"query": COMMUNITY})
    assert any(item["id"] == str(items[0].id) for item in remembered["ustalenia"])
    collector = EventCollector(pool)
    events = await collector.collect(delegation, since=now() - timedelta(minutes=5))
    feedback = [e for e in events if e.kind == "OUTCOME"]
    assert len(feedback) == 1
    assert feedback[0].payload["publication"] == str(request)
    # An extra vote and a fresh collector after restart do not spend another model turn.
    await observe(pool, tenant, request, score=9, replies=2)
    again = await EventCollector(pool).collect(
        delegation,
        since=now() - timedelta(minutes=5),
        seen={e.event_key for e in events},
    )
    assert not again
    await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
    )
    await world.handlers.run_once(now())
    assert len(world.port.calls) == 1


@pytest.mark.parametrize("when", ["before_approval", "before_execution"])
async def test_revoked_member_cannot_publish_even_with_a_signed_delegation(pool, tenant, when):
    seeded = await _seed(pool, tenant)
    _, member, _ = await new_signer(pool, tenant, seeded)
    revision = await _propose_own_post(pool, tenant, seeded)
    if when == "before_execution":
        await _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
        )
    async with pool.acquire() as conn:
        await conn.execute("UPDATE memberships SET revoked_at=now() WHERE id=$1", member)
    if when == "before_approval":
        with pytest.raises(ApprovalStoreError, match="aktywnego"):
            await ApprovalStore(pool).record_decision(
                revision_id=revision,
                organization_id=tenant["organization"],
                workspace_id=tenant["workspace"],
                decision=APPROVED_DECISION,
                membership_id=member,
            )
        considered = await _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
        )
        assert all(one.outcome != ISSUED for one in considered)
    world = _world(pool, tenant, own_post_port=FakeOwnPostPort())
    await world.handlers.run_once(now())
    assert world.port.calls == []


async def test_publish_one_revision_does_not_issue_other_pending_posts(pool, tenant):
    seeded = await _seed(
        pool,
        tenant,
        budget={
            "publications_per_day": 6,
            "publications_per_hour": 4,
            "own_posts_per_day": 3,
        },
    )
    first = await _propose_own_post(pool, tenant, seeded, title="First insight")
    second = await _propose_own_post(pool, tenant, seeded, title="Second insight")
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=uuid4(),
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            dsn="postgresql://fixture",
        ),
    )
    result = await registry._drafts_publish({"revision_id": str(second)})
    assert result["rozstrzygniecia"][0]["rewizja"] == str(second)
    rows = await _requests(pool, seeded)
    assert len(rows) == 1
    async with pool.acquire() as conn:
        issued = await conn.fetchval(
            "SELECT proposal_revision_id FROM publication_requests WHERE id=$1",
            rows[0]["id"],
        )
    assert issued == second
    untouched = await ApprovalStore(pool).fetch_revision(
        first, organization_id=tenant["organization"]
    )
    assert untouched.state.value == "READY_FOR_APPROVAL"


async def test_failed_queue_insert_rolls_back_consent_and_can_be_retried(pool, tenant, monkeypatch):
    from control_plane.pilot.publication import PublicationStore

    seeded = await _seed(pool, tenant)
    revision = await _propose_own_post(pool, tenant, seeded)
    original = PublicationStore.create_project_request

    async def fail(*args, **kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(PublicationStore, "create_project_request", fail)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await _approver(pool, tenant).consider_pending(
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
        )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM approval_decisions WHERE proposal_revision_id=$1",
                revision,
            )
            == 0
        )
    state = await ApprovalStore(pool).fetch_revision(
        revision, organization_id=tenant["organization"]
    )
    assert state.state.value == "READY_FOR_APPROVAL"
    monkeypatch.setattr(PublicationStore, "create_project_request", original)
    retried = await _approver(pool, tenant).consider_pending(
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
    )
    assert [one.outcome for one in retried] == [ISSUED]


@pytest.mark.parametrize("existing_permission", [None, False])
async def test_chat_card_upgrades_research_atomically_and_preserves_explicit_bans(
    pool,
    tenant,
    logged_in_client,
    existing_permission,
):
    from control_plane.presence.scope import parse_budget, parse_scope
    from test_session_cards import _path, _session, _tool_result

    seeded = await _seed(pool, tenant, own_posts=existing_permission)
    store = PresenceStore(pool)
    await store.revoke(seeded["delegation"].id, organization_id=tenant["organization"], at=now())
    research = await store.sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        mode="RESEARCH",
        operator_id=tenant["operator"],
        scope=parse_scope({"accounts": [], "communities": [], "actions": ["RESEARCH"]}),
        budget=parse_budget({"publications_per_day": 0}),
    )
    session = await _session(pool, tenant, seeded["project"])
    registry = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session.id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            project_id=seeded["project"],
            dsn="postgresql://fixture",
        ),
    )
    proposed = await registry._delegation_propose(
        {
            "communities": [COMMUNITY],
            "actions": ["OWN_POST"],
            "budget": {"publications_per_day": 3, "model_turns_per_day": 6},
        }
    )
    card = proposed["karty"][0]
    assert card["stan"] == "do_podpisu"
    assert ACCOUNT in card["uzasadnienie"]
    body = card["akcje"][0]["payload"]
    assert body["accounts"] == [str(seeded["account"])], "The API needs IDs, not logins."
    assert body["replaces"] == str(research.id) and body["allow_own_posts"]
    assert body["budget"]["model_turns_per_day"] == card["limity"]["tury_modelu_doba"] == 6
    assert body["budget"]["publications_per_day"] == card["limity"]["publikacje_doba"] == 3
    await _tool_result(
        pool, tenant, session.id, tool="delegation.propose", cards=[card], summary="Posty"
    )
    chat = _path(tenant, seeded["project"], session.id)
    before = (await logged_in_client.get(chat)).json()["karty"][0]
    assert before["stan"] == "do_podpisu", "The old research scope must not hide the new offer."
    route = card["akcje"][0]["trasa"]["sciezka"]
    response = await logged_in_client.post(route, json=body)
    current = await store.delegation_for(
        organization_id=tenant["organization"],
        project_id=seeded["project"],
    )
    if existing_permission is False:
        assert response.status_code == 409, response.text
        assert current.id == research.id and current.live
        return
    assert response.status_code == 201, response.text
    assert current.mode == "AUTONOMOUS" and current.id != research.id
    assert current.budget.publications_per_day == 3
    assert current.budget.model_turns_per_day == 6
    after = (await logged_in_client.get(chat)).json()["karty"][0]
    assert after["stan"] == "podpisana" and after["akcje"] == []
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT own_posts_allowed FROM communities WHERE id=$1",
                seeded["community"],
            )
            is True
        )
    # A stale card cannot revoke a newer scope or quietly grant the signature twice.
    assert (await logged_in_client.post(route, json=body)).status_code == 409
    unchanged = await registry._delegation_propose({
        "communities": [COMMUNITY], "actions": ["OWN_POST"],
        "budget": {"publications_per_day": 3, "model_turns_per_day": 6},
    })
    assert unchanged["karty"][0]["stan"] == "podpisana"
    reduced = await registry._delegation_propose({
        "communities": [COMMUNITY], "actions": ["OWN_POST"],
        "budget": {"publications_per_day": 0, "model_turns_per_day": 6},
    })
    reduction = reduced["karty"][0]
    assert reduction["stan"] == "do_podpisu"
    assert reduction["limity"]["publikacje_doba"] == 0
    assert reduction["akcje"][0]["payload"]["replaces"] == str(current.id)


@pytest.mark.parametrize("budget", [
    {"publications_per_day": -1}, {"publications_per_day": True},
    {"unknown_limit": 1}, {"publications_per_day": "3"},
])
async def test_invalid_chat_limits_do_not_produce_signable_cards(pool, tenant, budget):
    from test_product_tools import _account, _project, _registry

    project = await _project(pool, tenant)
    await _account(pool, tenant, project, state="READY")
    answer = await _registry(pool, tenant, project)._delegation_propose({
        "communities": [COMMUNITY], "actions": ["REPLY"], "budget": budget,
    })
    assert answer["stan"] == "BLAD" and answer["karty"] == []
