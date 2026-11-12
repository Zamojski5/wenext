"""A reply under a Reddit thread nobody recorded is still publishable (11.10.2026).

THE FAILURE. On the day the founder used the plugin for real, five of six approved Reddit replies
ended SKIPPED with "brak wątku albo community": the agent had read each thread through
`reddit.read` or `reddit.search` and written the draft under it, but nothing put the thread into
`observed_threads` (only the sixth, in r/micro_saas, happened to be there), and the publisher found
its target through that table. The person's approval is real in every one of those cases; the
publisher's own checks never needed the thread to have been OBSERVED, only READ.

WHAT IS MEASURED, through the real path (`approve_and_queue`, the founder's click, then
`PublishHandlers.run_once`), against a real database and a scripted Reddit source:

  * the reply goes out, in ONE read of the thread, and the thread and its community are recorded
    QUIET (no polling, no analysis) so `target_for` and the visibility checks find them later;
  * every check the publisher runs on a known thread still runs on a resolved one: a locked
    thread, a replied-to comment that is gone and an answer of ours already there SKIP the request
    and nothing is written;
  * what cannot be established SKIPS with the part that is missing (an address that names no
    Reddit thread, an id that disagrees with the address) and reads nothing; a thread Reddit does
    not know SKIPS and records nothing; a failed read keeps the request queued;
  * a community that exists is used as it is, whatever its case, with its flags untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from community_observer import SourceUnavailable
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.execution.store import ExecutionStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.store import ObservationStore
from control_plane.pilot.publication import PublicationStore
from control_plane.pilot.publisher import (
    OUTCOME_PUBLISHED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
    PublishHandlers,
)
from control_plane.pilot.store import PilotStore
from control_plane.presence.human_approval import approve_and_queue, revision_row
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore
from test_pilot_publisher import ACCOUNT, READY, FakePort, ScriptedRedditSource, thread_json

pytestmark = [pytest.mark.db]

POST_ID = "1unrec1"
URL = f"https://www.reddit.com/r/kubernetes/comments/{POST_ID}/a_post/"
TEXT = "Measure the queue depth first, then decide whether it is a scheduling problem."


def now() -> datetime:
    return datetime.now(UTC)


async def _seed(pool, tenant) -> dict[str, Any]:
    """A project with a Reddit account and a live delegation. NO observed thread, NO community."""
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'pomagaj w rozmowach o kolejkach') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'REDDIT', $2, 'READY') RETURNING id",
            tenant["organization"],
            ACCOUNT,
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode="REVIEW",
        scope=parse_scope(
            {"accounts": [str(account_id)], "communities": ["r/kubernetes"], "actions": ["REPLY"]}
        ),
        budget=parse_budget({"publications_per_day": 6, "publications_per_hour": 4}),
        operator_id=tenant["operator"],
        expires_at=now() + timedelta(days=2),
    )
    return {"project": project_id, "account": account_id}


async def _approved_reply(
    pool, tenant, seeded, *, target: str = f"t3_{POST_ID}", url: str = URL, text: str = TEXT
) -> UUID:
    """The founder's click: a draft exactly as `draft.create` leaves it, approved by a person and
    queued. Returns the request id."""
    approvals = ApprovalStore(pool)
    proposal_id = await approvals.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=seeded["account"],
    )
    revision = await approvals.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid4(),
            account=seeded["account"],
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id=target,
            target_url=url,
            source_fingerprint=f"session:{uuid4()}",
            text_and_media=text,
            intelligence_profile_version=uuid4(),
            plan_version=seeded["project"],
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=now() + timedelta(hours=6),
    )
    row = await revision_row(
        pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        revision_id=revision.id,
    )
    assert row is not None
    approval = await approve_and_queue(
        pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=seeded["project"],
        row=row,
        signer=None,
        reason="zatwierdził człowiek",
    )
    return approval.request_id


def _publisher(pool, tenant, source: ScriptedRedditSource, port: FakePort) -> PublishHandlers:
    async def capsule_state():
        return dict(READY)

    return PublishHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        execution=ExecutionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        source=source,
        capsule_state=capsule_state,
        port_factory=lambda account: port,
        organization_id=tenant["organization"],
        delegations=PresenceStore(pool),
    )


async def _request(pool, request_id: UUID):
    return await PublicationStore(pool).get_request(request_id)


async def _communities(pool, tenant) -> list[Any]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT community_key, observe_enabled, thread_analysis_enabled, display_name "
            "FROM communities WHERE organization_id = $1 AND workspace_id = $2 ORDER BY created_at",
            tenant["organization"],
            tenant["workspace"],
        )


async def _threads(pool, tenant) -> list[Any]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT t.external_id, t.url, c.community_key FROM observed_threads t "
            "JOIN communities c ON c.id = t.community_id "
            "WHERE t.organization_id = $1 AND t.workspace_id = $2",
            tenant["organization"],
            tenant["workspace"],
        )


async def test_the_reply_goes_out_in_one_read_and_the_thread_is_recorded_quiet(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(pool, tenant, seeded)
    store = PublicationStore(pool)
    queued = await _request(pool, request_id)
    assert queued is not None and queued.status == "QUEUED"
    assert await store.target_for(queued) is None, "the premise: nothing recorded the thread"
    assert await _communities(pool, tenant) == [] and await _threads(pool, tenant) == []

    source, port = ScriptedRedditSource(), FakePort()
    source.threads[POST_ID] = thread_json(POST_ID, comments=[{"id": "k1"}])
    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    assert len(port.calls) == 1 and port.calls[0].text_and_media == TEXT
    assert source.reads == [POST_ID], "one read served the resolution and every check"
    published = await _request(pool, request_id)
    assert published is not None and published.status == "PUBLISHED"
    assert published.intent_id is not None and published.attempt_id is not None
    # The community exists so the thread has somewhere to live, and it watches nothing.
    (community,) = await _communities(pool, tenant)
    assert community["community_key"] == "r/kubernetes"
    assert community["observe_enabled"] is False and community["thread_analysis_enabled"] is False
    (thread,) = await _threads(pool, tenant)
    assert thread["external_id"] == POST_ID and thread["community_key"] == "r/kubernetes"
    # And the rest of the machinery finds it now: the snapshot of the people's replies, the
    # visibility checks, the reconciliation.
    target = await store.target_for(published)
    assert target is not None and target.thread_external_id == POST_ID
    async with pool.acquire() as conn:
        comments = await conn.fetchval(
            "SELECT count(*) FROM observed_comments WHERE organization_id = $1 "
            "AND thread_id = $2",
            tenant["organization"],
            target.thread_id,
        )
    assert comments == 1, "the snapshot taken at publication recorded the thread's comments"


async def test_a_reply_to_a_comment_is_resolved_through_its_thread(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    comment_url = f"{URL}k1/"
    await _approved_reply(pool, tenant, seeded, target="t1_k1", url=comment_url)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[POST_ID] = thread_json(POST_ID, comments=[{"id": "k1"}])

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    assert port.calls[0].target_external_id == "t1_k1"
    assert source.reads == [POST_ID]


@pytest.mark.parametrize(
    ("script", "fragment"),
    [
        ("locked", "zablokowany"),
        ("archived", "zarchiwizowany"),
        ("ours_already_there", "naszego konta"),
    ],
)
async def test_every_check_still_runs_on_a_resolved_thread(
    pool, tenant, script: str, fragment: str
) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[POST_ID] = thread_json(
        POST_ID,
        comments=[{"id": "k1", "author": ACCOUNT}] if script == "ours_already_there" else [],
        locked=script == "locked",
        archived=script == "archived",
    )

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED, result
    assert port.calls == [], "nothing was written"
    skipped = await _request(pool, request_id)
    assert skipped is not None and skipped.status == "SKIPPED"
    assert fragment in (skipped.detail or "")


async def test_a_replied_to_comment_that_is_gone_skips_the_request(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(
        pool, tenant, seeded, target="t1_gone", url=f"{URL}gone/"
    )
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[POST_ID] = thread_json(POST_ID, comments=[{"id": "k1"}])

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED and port.calls == []
    skipped = await _request(pool, request_id)
    assert skipped is not None and "zniknął" in (skipped.detail or "")


async def test_a_thread_reddit_does_not_know_is_skipped_and_nothing_is_recorded(
    pool, tenant
) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()  # no thread scripted: Reddit says 404

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED and port.calls == []
    skipped = await _request(pool, request_id)
    assert skipped is not None and "zniknął z Reddita" in (skipped.detail or "")
    assert await _communities(pool, tenant) == [] and await _threads(pool, tenant) == []


async def test_a_failed_read_keeps_the_request_queued_with_the_reason(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()

    async def broken(community_key: str, external_id: str):
        raise SourceUnavailable("Reddit 429")

    source.read_thread_payload = broken  # type: ignore[method-assign]
    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_UNAVAILABLE and port.calls == []
    waiting = await _request(pool, request_id)
    assert waiting is not None and waiting.status == "QUEUED" and "429" in (waiting.detail or "")
    assert await _threads(pool, tenant) == []


@pytest.mark.parametrize(
    ("target", "url", "fragment"),
    [
        (f"t3_{POST_ID}", "https://example.com/r/kubernetes/comments/1unrec1/x/", "adres celu"),
        (f"t3_{POST_ID}", "https://www.reddit.com/r/kubernetes/", "adres celu"),
        (f"t3_{POST_ID}", f"https://www.reddit.com/r/kubernetes/comments/{POST_ID}x/y/", "różne"),
        ("t3_other99", URL, "różne posty"),
    ],
)
async def test_what_cannot_be_established_skips_with_the_missing_part_and_reads_nothing(
    pool, tenant, target: str, url: str, fragment: str
) -> None:
    seeded = await _seed(pool, tenant)
    request_id = await _approved_reply(pool, tenant, seeded, target=target, url=url)
    source, port = ScriptedRedditSource(), FakePort()
    source.threads[POST_ID] = thread_json(POST_ID)

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_SKIPPED and port.calls == []
    skipped = await _request(pool, request_id)
    assert skipped is not None and skipped.status == "SKIPPED"
    assert "brak wątku" in (skipped.detail or "") and fragment in (skipped.detail or "")
    assert source.reads == [], "an address or an id that does not hold up costs no read"
    assert await _threads(pool, tenant) == []


async def test_a_community_that_exists_is_used_as_it_is_whatever_its_case(pool, tenant) -> None:
    """The founder watches `r/Kubernetes` and the address says `r/kubernetes`: one community, its
    flags untouched (the publisher must not start or stop observing anything)."""
    seeded = await _seed(pool, tenant)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, observe_enabled, poll_interval_seconds, "
            "thread_analysis_enabled) "
            "VALUES ($1, $2, 'REDDIT', 'r/Kubernetes', 'Kubernetes', 'ACCOUNT_REQUIRED', "
            "true, 1800, true)",
            tenant["organization"],
            tenant["workspace"],
        )
    await _approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    payload = thread_json(POST_ID)
    payload[0]["data"]["children"][0]["data"]["subreddit"] = "Kubernetes"
    source.threads[POST_ID] = payload

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    (community,) = await _communities(pool, tenant)
    assert community["community_key"] == "r/Kubernetes"
    assert community["observe_enabled"] is True and community["thread_analysis_enabled"] is True
    (thread,) = await _threads(pool, tenant)
    assert thread["community_key"] == "r/Kubernetes"


async def test_a_new_community_takes_the_spelling_reddit_reports(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _approved_reply(pool, tenant, seeded)
    source, port = ScriptedRedditSource(), FakePort()
    payload = thread_json(POST_ID)
    payload[0]["data"]["children"][0]["data"]["subreddit"] = "Kubernetes"
    source.threads[POST_ID] = payload

    result = await _publisher(pool, tenant, source, port).run_once(now())

    assert result.outcome == OUTCOME_PUBLISHED, result
    (community,) = await _communities(pool, tenant)
    assert community["community_key"] == "r/Kubernetes"
    assert community["observe_enabled"] is False


async def test_ensure_quiet_community_never_flips_flags_and_creates_once(pool, tenant) -> None:
    store = ObservationStore(pool)
    args = {
        "organization_id": tenant["organization"],
        "workspace_id": tenant["workspace"],
        "platform": "REDDIT",
        "community_key": "r/saas",
        "display_name": "Reddit · r/saas",
        "access_mode": "ACCOUNT_REQUIRED",
    }
    first = await store.ensure_quiet_community(**args)
    assert first is not None
    assert first.observe_enabled is False and first.thread_analysis_enabled is False
    # Somebody (the founder) later turns observation on; asking again must leave it on.
    await store.set_observing(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        community_id=first.id,
        enabled=True,
    )
    again = await store.ensure_quiet_community(**{**args, "community_key": "r/SaaS"})
    assert again is not None and again.id == first.id and again.observe_enabled is True
    assert len(await _communities(pool, tenant)) == 1
