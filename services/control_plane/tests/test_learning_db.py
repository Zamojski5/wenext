"""The learning loop against PostgreSQL: snapshots on a schedule, lessons that upsert, the tool (13.10).

THE QUESTION: "does what we publish teach the next draft, and is every number on the way honest".
Each case is one way the loop could lie or lose work:

  * a published item that never gets a learning row, or gets two, or an item that is not on a
    platform at all (the fixture site) that gets tracked;
  * a snapshot taken at the wrong horizon, twice, or in place of one the publisher can still give;
  * a live read that is made when the publisher's own observation would have done, or that is not
    bounded, or whose failure becomes a row of zeros;
  * a lesson written twice, left standing after its numbers moved, or kept after the gap closed;
  * a seeded rule retracted by the sweep that only knows measured lessons;
  * a model (or a person) that writes a LESSON with a number nobody computed.

THE PUBLICATION IS REAL, down to the delegation and the execution attempt: the rows of a published
request are the ones the product writes, so the discovery join is tested against the shape it meets
in production and not against a fixture invented for the test.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from control_plane.knowledge.handlers import KnowledgeHandlers
from control_plane.knowledge.ingest import Ingestor
from control_plane.knowledge.store import KnowledgeRefused, KnowledgeStore, NewItem, Scope
from control_plane.learning import diary
from control_plane.learning.playbook import read_playbook
from control_plane.learning.service import MAX_LIVE_READS, LearningService
from control_plane.learning.sources import LearningSources, Reading
from control_plane.learning.store import LearningStore, NewPost
from control_plane.learning.voice import HUMOR_NEVER_PITCHES, VOICE_RULES
from control_plane.tools import ToolRegistry, issue

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


async def _project(pool, tenant) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal)"
            " VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid.uuid4().hex[:8]}",
        )


async def _delegation(pool, tenant, project_id) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO project_delegations (organization_id, workspace_id, project_id, mode,"
            " scope, scope_sha256, signed_by) VALUES ($1, $2, $3, 'REVIEW', '{}'::jsonb, $4, $5)"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            tenant["operator"],
        )


async def _publish(
    pool,
    tenant,
    project_id,
    *,
    delegation_id,
    text: str,
    published_at: datetime,
    our_id: str,
    target: str,
    kind: str = "REPLY",
    permalink: str | None = None,
    target_url: str | None = None,
    account: str = "demo_founder",
    visibility: str | None = None,
) -> uuid.UUID:
    """One PUBLISHED request with its evidence, through the whole chain 0007 and 0017 demand."""
    on_x = target.startswith("x:")
    digest = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id)"
            " VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            "X" if on_x else "REDDIT",
            f"{account}-{uuid.uuid4().hex[:6]}",
        )
        proposal_id = await conn.fetchval(
            "INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)"
            " VALUES ($1, $2, $3) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            account_id,
        )
        revision_id = await conn.fetchval(
            "INSERT INTO action_proposal_revisions (organization_id, workspace_id, proposal_id,"
            " revision, payload, payload_hash, source_fingerprint, expires_at, state)"
            " VALUES ($1, $2, $3, 1, $4::jsonb, $5, 'odcisk', now() + interval '1 day',"
            " 'APPROVED') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            proposal_id,
            json.dumps(
                {"text_and_media": text, "target_url": target_url or "https://x.com/a/status/1"}
            ),
            digest,
        )
        decision_id = await conn.fetchval(
            "INSERT INTO approval_decisions (organization_id, workspace_id, proposal_revision_id,"
            " decision, approved_payload_hash, approved_by)"
            " VALUES ($1, $2, $3, 'APPROVED', $4, $5) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            revision_id,
            digest,
            tenant["operator"],
        )
        intent_id = await conn.fetchval(
            "INSERT INTO execution_intents (organization_id, workspace_id, proposal_revision_id,"
            " approval_decision_id, one_time_grant_hash, expires_at, payload_hash, account_id,"
            " status, consumed_at) VALUES ($1, $2, $3, $4, $5, now() + interval '1 hour', $6, $7,"
            " 'CONSUMED', now()) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            revision_id,
            decision_id,
            hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            digest,
            account_id,
        )
        attempt_id = await conn.fetchval(
            "INSERT INTO execution_attempts (organization_id, workspace_id, intent_id,"
            " proposal_revision_id, payload_hash, attempt_no, status, adapter_version, finished_at)"
            " VALUES ($1, $2, $3, $4, $5, 1, 'CONFIRMED', 'test-1', now()) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            intent_id,
            revision_id,
            digest,
        )
        request_id = await conn.fetchval(
            "INSERT INTO publication_requests (organization_id, workspace_id, project_id,"
            " delegation_id, proposal_revision_id, approval_decision_id, intent_id, attempt_id,"
            " position, status, consent_source, draft_sha256, target_external_id, target_url,"
            " published_at, kind) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, 'PUBLISHED',"
            " 'delegacja', $9, $10, $11, $12, $13) RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            delegation_id,
            revision_id,
            decision_id,
            intent_id,
            attempt_id,
            digest,
            target,
            target_url or "https://x.com/a/status/1",
            published_at,
            kind,
        )
        await conn.execute(
            "INSERT INTO execution_evidence (organization_id, workspace_id, attempt_id,"
            " external_id, permalink, account, observed_at, source)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'ACTUATOR')",
            tenant["organization"],
            tenant["workspace"],
            attempt_id,
            our_id,
            permalink or f"https://x.com/{account}/status/{our_id.removeprefix('x:')}",
            account,
            published_at,
        )
        if visibility:
            await conn.execute(
                "UPDATE publication_requests SET public_visibility = $2,"
                " public_visibility_checked_at = now() WHERE id = $1",
                request_id,
                visibility,
            )
    return request_id


async def _observe(pool, tenant, request_id, when: datetime, **metrics) -> None:
    score = metrics.get("likes")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO publication_observations (organization_id, workspace_id, request_id,"
            " observed_at, still_present, score, reply_count, raw, viewer)"
            " VALUES ($1, $2, $3, $4, true, $5, 0, $6::jsonb, 'AUTHOR')",
            tenant["organization"],
            tenant["workspace"],
            request_id,
            when,
            score,
            json.dumps({"metrics": metrics}),
        )


class FakeSources(LearningSources):
    """The readers of a sweep, scripted: what the capsule says per post and what the mirror says."""

    def __init__(self) -> None:
        super().__init__()
        self.item_reads: list[str] = []
        self.target_reads: list[str] = []
        self.items: dict[str, Reading | None] = {}
        self.targets: dict[str, SimpleNamespace | None] = {}
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1

    async def read_x_item(self, permalink: str, *, post_id: str):
        self.item_reads.append(post_id)
        return self.items.get(post_id)

    async def read_x_target(self, target_url, *, target_id: str):
        self.target_reads.append(target_id)
        return self.targets.get(target_id)


def service(pool, tenant, sources: LearningSources | None = None) -> LearningService:
    async def no_sleep(_seconds: float) -> None:
        return None

    return LearningService(
        pool=pool,
        organization_id=tenant["organization"],
        sources=sources or FakeSources(),
        clock=lambda: NOW,
        sleep=no_sleep,
    )


def scope_of(tenant, project_id) -> Scope:
    return Scope(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )


async def _snapshots(pool, post_id) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT horizon_hours, views, likes, replies, reposts, source, age_minutes"
            "  FROM learning_snapshots WHERE post_id = $1 ORDER BY age_minutes",
            post_id,
        )
    return {row["horizon_hours"]: dict(row) for row in rows}


# --------------------------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------------------------


async def test_every_published_item_gets_one_row_with_what_was_known_when_we_replied(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    published = datetime(
        2026, 10, 13, 13, 53, tzinfo=UTC
    )  # 33 minutes after the target was written
    request = await _publish(
        pool, tenant, project, delegation_id=delegation,
        text="$2,000 to $5,000 and the honest line is scaling, isnt it",
        published_at=published, our_id="x:2110006135294136605", target="x:2109997331097833513",
        target_url="https://x.com/alexcooldev/status/2109997331097833513",
    )  # fmt: skip
    # An own post, and one on the fixture site that is not on any platform.
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="Wenext, https://getwenext.com",
        published_at=published, our_id="x:2109976668630913530", target="x:@demo_founder",
        kind="OWN_POST", target_url="https://x.com/demo_founder",
    )  # fmt: skip
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="fixture", published_at=published,
        our_id="x:42", target="x:43", permalink="https://fixture-site.test/c/x?posted=1",
    )  # fmt: skip
    svc = service(pool, tenant)
    result = await svc.sweep(NOW)
    again = await svc.sweep(NOW)
    assert (result.registered, again.registered) == (2, 0)

    store = LearningStore(pool)
    reply = await store.post_by_external(tenant["organization"], "X", "x:2110006135294136605")
    assert reply is not None and reply.request_id == request and reply.origin == "PUBLICATION"
    assert (reply.kind, reply.project_id, reply.account) == ("REPLY", project, reply.account)
    assert reply.hook == "LICZBA" and reply.hook_source == "CLASSIFIER"
    assert reply.target_author == "alexcooldev" and reply.target_followers is None
    assert reply.target_age_minutes is not None and 30 <= reply.target_age_minutes <= 40
    own = await store.post_by_external(tenant["organization"], "X", "x:2109976668630913530")
    assert own is not None and own.kind == "OWN_POST" and own.target_external_id is None
    assert own.has_link and own.mentions_wenext
    assert await store.post_by_external(tenant["organization"], "X", "x:42") is None


async def test_the_position_is_the_reply_count_read_before_our_reply_and_not_after(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    published = NOW - timedelta(hours=2)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO x_trend_posts (organization_id, workspace_id, project_id, collected_at,"
            " source, post_id, replies) VALUES ($1, $2, $3, $4, 'home', '2109997331097833513', 4),"
            " ($1, $2, $3, $5, 'home', '2109997331097833513', 90)",
            tenant["organization"],
            tenant["workspace"],
            project,
            published - timedelta(minutes=20),
            published + timedelta(minutes=20),
        )
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a reply", published_at=published,
        our_id="x:2110006135294136605", target="x:2109997331097833513",
        target_url="https://x.com/alexcooldev/status/2109997331097833513",
    )  # fmt: skip
    await service(pool, tenant).sweep(NOW)
    post = await LearningStore(pool).post_by_external(
        tenant["organization"], "X", "x:2110006135294136605"
    )
    assert post is not None and post.position_replies == 4
    assert post.facts["position_source"] == "x_trends"


async def test_a_hand_posted_reply_with_a_link_is_tracked_and_one_without_is_not(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    async with pool.acquire() as conn:
        for name, url in (
            ("with", "https://x.com/demo_founder/status/2110006135294136605"),
            ("without", None),
        ):
            account_id = await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id)"
                " VALUES ($1, 'X', $2) RETURNING id",
                tenant["organization"],
                f"{name}-{uuid.uuid4().hex[:6]}",
            )
            proposal_id = await conn.fetchval(
                "INSERT INTO action_proposals (organization_id, workspace_id, social_account_id)"
                " VALUES ($1, $2, $3) RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                account_id,
            )
            revision_id = await conn.fetchval(
                "INSERT INTO action_proposal_revisions (organization_id, workspace_id, proposal_id,"
                " revision, payload, payload_hash, source_fingerprint, expires_at, state)"
                " VALUES ($1, $2, $3, 1, '{}'::jsonb, $4, 'o', now() + interval '1 day',"
                " 'READY_FOR_APPROVAL') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                proposal_id,
                hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            )
            await conn.execute(
                "INSERT INTO manual_publications (organization_id, workspace_id, project_id,"
                " revision_id, platform, target_external_id, target_url, text, url, posted_at)"
                " VALUES ($1, $2, $3, $4, 'X', 'x:2109997331097833513',"
                " 'https://x.com/alexcooldev/status/2109997331097833513', $5, $6, $7)",
                tenant["organization"],
                tenant["workspace"],
                project,
                revision_id,
                f"typed {name}",
                url,
                NOW,
            )
    result = await service(pool, tenant).sweep(NOW)
    assert result.registered == 1
    posts = await LearningStore(pool).posts(tenant["organization"])
    assert [(p.origin, p.external_id, p.body) for p in posts] == [
        ("MANUAL", "x:2110006135294136605", "typed with")
    ]


# --------------------------------------------------------------------------------------------
# The schedule of snapshots
# --------------------------------------------------------------------------------------------


async def test_the_publishers_observations_become_the_1_6_and_24_hour_snapshots(
    pool, tenant
) -> None:
    """The 7,000 observations already on file are the backfill: a published item that is days old
    gets its snapshots from what the publisher read while it was young, at no read of ours."""
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    published = NOW - timedelta(hours=40)
    request = await _publish(
        pool, tenant, project, delegation_id=delegation, text="post", published_at=published,
        our_id="x:2109976668630913530", target="x:@demo_founder", kind="OWN_POST",
        target_url="https://x.com/demo_founder",
    )  # fmt: skip
    for minutes, metrics in (
        (20, {"views": 3}),  # before any horizon
        (65, {"views": 7}),
        (80, {"likes": 1, "replies": 0, "reposts": 0}),
        (400, {"views": 30, "likes": 2}),
        (1450, {"views": 90, "likes": 4, "replies": 1, "reposts": 1}),
        (1500, {"views": 99}),
    ):
        await _observe(pool, tenant, request, published + timedelta(minutes=minutes), **metrics)
    result = await service(pool, tenant).sweep(NOW)
    assert result.harvested == 3 and result.read == 0
    post = await LearningStore(pool).post_by_external(
        tenant["organization"], "X", "x:2109976668630913530"
    )
    snaps = await _snapshots(pool, post.id)
    assert sorted(snaps) == [1, 6, 24]  # 72 h is not due yet at 40 h
    assert (snaps[1]["views"], snaps[1]["likes"], snaps[1]["replies"]) == (7, 1, 0)
    assert snaps[1]["age_minutes"] == 65 and snaps[1]["source"] == "OBSERVATION"
    assert (snaps[24]["views"], snaps[24]["likes"], snaps[24]["reposts"]) == (90, 4, 1)
    # Run again: nothing is written twice.
    again = await service(pool, tenant).sweep(NOW)
    assert again.harvested == 0 and sorted(await _snapshots(pool, post.id)) == [1, 6, 24]


async def test_a_due_horizon_is_read_live_only_after_the_publisher_had_its_chance(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    # Published 70 minutes ago: the 1 h horizon is due for 10 minutes, inside the publisher's
    # 26 h window and inside the grace, so the sweep waits for an observation.
    soon = NOW - timedelta(minutes=70)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a", published_at=soon,
        our_id="x:11", target="x:@a", kind="OWN_POST", target_url="https://x.com/a",
        permalink="https://x.com/a/status/11",
    )  # fmt: skip
    # Published 100 minutes ago: 40 minutes late, past the grace: it is read.
    later = NOW - timedelta(minutes=100)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="b", published_at=later,
        our_id="x:12", target="x:@a", kind="OWN_POST", target_url="https://x.com/a",
        permalink="https://x.com/a/status/12",
    )  # fmt: skip
    sources = FakeSources()
    sources.items["12"] = Reading(views=9, likes=1, replies=0, reposts=0, source="CAPSULE")
    result = await service(pool, tenant, sources).sweep(NOW)
    assert sources.item_reads == ["12"] and result.read == 1
    store = LearningStore(pool)
    read = await store.post_by_external(tenant["organization"], "X", "x:12")
    snaps = await _snapshots(pool, read.id)
    assert snaps[1]["source"] == "CAPSULE" and snaps[1]["age_minutes"] == 100
    assert snaps[1]["views"] == 9 and snaps[1]["likes"] == 1
    waiting = await store.post_by_external(tenant["organization"], "X", "x:11")
    assert await _snapshots(pool, waiting.id) == {}
    assert sources.closed == 1, "the readers of a sweep are closed at its end"


async def test_live_reads_are_bounded_oldest_due_first_and_a_failure_is_not_a_row(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    sources = FakeSources()
    count = MAX_LIVE_READS + 3
    for index in range(count):
        # Each one older than the last, so the order of reading is checkable.
        published = NOW - timedelta(minutes=100 + index * 5)
        await _publish(
            pool, tenant, project, delegation_id=delegation, text=f"post {index}",
            published_at=published, our_id=f"x:{100 + index}", target="x:@a", kind="OWN_POST",
            target_url="https://x.com/a", permalink=f"https://x.com/a/status/{100 + index}",
        )  # fmt: skip
        sources.items[str(100 + index)] = Reading(views=5, source="CAPSULE")
    sources.items["106"] = None  # the oldest: the capsule could not read it
    result = await service(pool, tenant, sources).sweep(NOW)
    assert len(sources.item_reads) == MAX_LIVE_READS
    assert sources.item_reads == ["106", "105", "104", "103"], "oldest due first"
    assert result.read_failed == 1 and result.read == MAX_LIVE_READS - 1
    store = LearningStore(pool)
    failed = await store.post_by_external(tenant["organization"], "X", "x:106")
    assert await _snapshots(pool, failed.id) == {}, "a failed read is no snapshot, not zeros"


async def test_an_item_the_platform_says_is_gone_gets_no_snapshot_and_is_not_read_again(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a", published_at=NOW - timedelta(minutes=100),
        our_id="x:21", target="x:@a", kind="OWN_POST", target_url="https://x.com/a",
        permalink="https://x.com/a/status/21",
    )  # fmt: skip
    sources = FakeSources()
    sources.items["21"] = Reading(gone=True)
    first = await service(pool, tenant, sources).sweep(NOW)
    second = await service(pool, tenant, sources).sweep(NOW)
    assert first.gone == 1 and second.gone == 0 and sources.item_reads == ["21"]
    post = await LearningStore(pool).post_by_external(tenant["organization"], "X", "x:21")
    assert post.facts.get("gone_at") and await _snapshots(pool, post.id) == {}


async def test_a_removed_item_is_registered_but_never_part_of_a_comparison(pool, tenant) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    request = await _publish(
        pool, tenant, project, delegation_id=delegation, text="gone", published_at=NOW - timedelta(hours=30),
        our_id="t1_pfb0k9f", target="t3_1x2gigk", target_url="https://www.reddit.com/r/SideProject/comments/1x2gigk/t/",
        permalink="https://www.reddit.com/r/SideProject/comments/1x2gigk/comment/pfb0k9f/",
        visibility="REMOVED",
    )  # fmt: skip
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO publication_observations (organization_id, workspace_id, request_id,"
            " observed_at, still_present, score, reply_count, raw, viewer)"
            " VALUES ($1, $2, $3, $4, true, 0, 0, '{}'::jsonb, 'AUTHOR')",
            tenant["organization"],
            tenant["workspace"],
            request,
            NOW - timedelta(hours=29),
        )
    await service(pool, tenant).sweep(NOW)
    store = LearningStore(pool)
    post = await store.post_by_external(tenant["organization"], "REDDIT", "t1_pfb0k9f")
    assert post is not None and sorted(await _snapshots(pool, post.id)) == [1]
    assert await store.items(tenant["organization"]) == []


async def test_an_x_replys_target_followers_are_read_from_the_mirror_three_times_at_most(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="reply", published_at=NOW - timedelta(minutes=5),
        our_id="x:31", target="x:2109997331097833513", target_url="https://x.com/alexcooldev/status/2109997331097833513",
        permalink="https://x.com/demo_founder/status/31",
    )  # fmt: skip
    sources = FakeSources()
    store = LearningStore(pool)
    for _ in range(4):  # the target is unreadable: after three tries it is left as unknown
        await service(pool, tenant, sources).sweep(NOW)
    post = await store.post_by_external(tenant["organization"], "X", "x:31")
    assert sources.target_reads == ["2109997331097833513"] * 3
    assert post.target_followers is None and post.facts["followers_attempts"] == 3
    # A later one that CAN be read fills it, says where it came from and when.
    sources = FakeSources()
    await store.note_attempt(post.id, "followers_attempts", 0)
    sources.targets["2109997331097833513"] = SimpleNamespace(
        followers=19_800, handle="alexcooldev", read_at=NOW
    )
    result = await service(pool, tenant, sources).sweep(NOW)
    post = await store.post_by_external(tenant["organization"], "X", "x:31")
    assert result.followers == 1 and post.target_followers == 19_800
    assert post.facts["followers_source"] == "fxtwitter" and post.facts["followers_read_at"]


# --------------------------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------------------------


async def _tracked(pool, tenant, project, n: int, *, age: int, views: int, hook: str = "OPINIA"):
    """A tracked reply with its 24 h snapshot, straight into the learning tables."""
    store = LearningStore(pool)
    published = NOW - timedelta(days=3, minutes=n)
    post_id, _ = await store.register(
        NewPost(
            organization_id=tenant["organization"], workspace_id=tenant["workspace"],
            project_id=project, platform="X", kind="REPLY", origin="PUBLICATION",
            external_id=f"x:9{n:05d}", permalink=f"https://x.com/demo_founder/status/9{n:05d}",
            body=f"text number {n}", published_at=published, target_external_id=f"x:8{n:05d}",
            target_age_minutes=age, hook=hook, hook_source="CLASSIFIER",
        )
    )  # fmt: skip
    await store.record_snapshot(
        next(p for p in await store.posts(tenant["organization"]) if p.id == post_id),
        horizon_hours=24, measured_at=published + timedelta(hours=25),
        views=views, likes=views // 50, replies=0, reposts=0, source="OBSERVATION",
    )  # fmt: skip
    return post_id


async def test_a_clear_gap_becomes_one_lesson_with_its_evidence_and_is_not_written_twice(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160, 130)):
        await _tracked(pool, tenant, project, i, age=10, views=views)
    for i, views in enumerate((20, 25, 30)):
        await _tracked(pool, tenant, project, 10 + i, age=200, views=views)
    first = await service(pool, tenant).sweep(NOW)
    second = await service(pool, tenant).sweep(NOW)
    assert (first.lessons_written, second.lessons_written, second.lessons_corrected) == (1, 0, 0)

    knowledge = KnowledgeStore(pool)
    (lesson,) = await knowledge.list(scope_of(tenant, project), kind="LESSON")
    assert lesson.stated_by == "AGENT" and lesson.project_id == project
    assert lesson.statement == (
        "On X, at about 24 h after posting: replies under posts younger than 30 min got a median "
        "of 135 views (n=4) versus 25 views for replies under posts older than 2 h (n=3)."
    )
    assert lesson.payload["winner"]["n"] == 4 and lesson.payload["loser"]["n"] == 3
    assert lesson.payload["lesson_key"] == "X:target_age" and lesson.payload["horizon_hours"] == 24
    assert lesson.payload["measured_by"] == "job learning.sweep"
    assert lesson.payload["winner"]["evidence"][0].startswith("x:9")


async def test_a_lesson_whose_numbers_moved_is_corrected_and_one_whose_gap_closed_is_retracted(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160)):
        await _tracked(pool, tenant, project, i, age=10, views=views)
    for i, views in enumerate((20, 25, 30)):
        await _tracked(pool, tenant, project, 10 + i, age=200, views=views)
    await service(pool, tenant).sweep(NOW)
    knowledge = KnowledgeStore(pool)
    scope = scope_of(tenant, project)
    (before,) = await knowledge.list(scope, kind="LESSON")

    # One more young reply with fewer views: the median and the n change, the lesson holds.
    await _tracked(pool, tenant, project, 3, age=10, views=60)
    corrected = await service(pool, tenant).sweep(NOW)
    assert corrected.lessons_corrected == 1 and corrected.lessons_written == 0
    (after,) = await knowledge.list(scope, kind="LESSON")
    assert after.id != before.id and "n=4" in after.statement
    history = await knowledge.list(scope, kind="LESSON", include_retired=True)
    assert len(history) == 2 and [one.in_force for one in history].count(True) == 1
    assert (await knowledge.item(before.id, scope)).superseded_by == after.id, "the old one stays"

    # The old group catches up: the gap closes and the lesson is retracted WITH a reason.
    for i in range(3):
        await _tracked(pool, tenant, project, 20 + i, age=200, views=140)
    closed = await service(pool, tenant).sweep(NOW)
    assert closed.lessons_retracted == 1
    assert await knowledge.list(scope, kind="LESSON") == ()
    retired = [
        one
        for one in await knowledge.list(scope, kind="LESSON", include_retired=True)
        if one.retracted_at
    ]
    assert len(retired) == 1 and "no longer shows" in retired[0].retracted_reason


async def test_numbers_that_return_to_an_earlier_sentence_bring_that_lesson_back(
    pool, tenant
) -> None:
    """The same sentence cannot exist twice (0027), so a measurement that goes A, B, A must
    reinstate A, not leave the project with no lesson in force."""
    project = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160)):
        await _tracked(pool, tenant, project, i, age=10, views=views)
    for i, views in enumerate((20, 25, 30)):
        await _tracked(pool, tenant, project, 10 + i, age=200, views=views)
    knowledge = KnowledgeStore(pool)
    scope = scope_of(tenant, project)
    await service(pool, tenant).sweep(NOW)
    (first,) = await knowledge.list(scope, kind="LESSON")

    extra = await _tracked(pool, tenant, project, 3, age=10, views=60)  # B: n=4
    await service(pool, tenant).sweep(NOW)
    (second,) = await knowledge.list(scope, kind="LESSON")
    assert second.id != first.id

    async with pool.acquire() as conn:  # the extra reply goes away: back to A
        await conn.execute("DELETE FROM learning_posts WHERE id = $1", extra)
    back = await service(pool, tenant).sweep(NOW)
    (holding,) = await knowledge.list(scope, kind="LESSON")
    assert holding.id == first.id and holding.statement == first.statement
    assert back.lessons_corrected == 1
    assert (await knowledge.item(second.id, scope)).superseded_by == first.id

    # And the same after a RETRACTION: A holds, the gap closes (retracted), A's numbers return.
    async with pool.acquire() as conn:
        await conn.execute("UPDATE learning_snapshots SET views = 140 WHERE views < 40")
    await service(pool, tenant).sweep(NOW)
    assert await knowledge.list(scope, kind="LESSON") == ()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE learning_snapshots SET views = 25 WHERE views = 140 AND post_id"
            " IN (SELECT id FROM learning_posts WHERE target_age_minutes = 200)"
        )
    again = await service(pool, tenant).sweep(NOW)
    (restored,) = await knowledge.list(scope, kind="LESSON")
    assert again.lessons_written == 1 and restored.in_force


async def test_a_property_with_one_group_under_three_makes_no_lesson(pool, tenant) -> None:
    project = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160)):
        await _tracked(pool, tenant, project, i, age=10, views=views)
    for i, views in enumerate((20, 25)):  # only two
        await _tracked(pool, tenant, project, 10 + i, age=200, views=views)
    result = await service(pool, tenant).sweep(NOW)
    assert (result.lessons_written, result.lessons_corrected) == (0, 0)
    assert await KnowledgeStore(pool).list(scope_of(tenant, project), kind="LESSON") == ()


async def test_only_the_product_writes_a_lesson_and_a_seeded_rule_survives_the_sweep(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    knowledge = KnowledgeStore(pool)
    scope = scope_of(tenant, project)
    invented = NewItem(
        kind="LESSON", statement="Replies with a question get 500 views.", stated_by="AGENT",
        project_id=project, payload={"lesson_key": "X:hook", "winner": {"n": 9}, "loser": {"n": 9}},
    )  # fmt: skip
    with pytest.raises(KnowledgeRefused, match="liczy produkt"):
        await knowledge.write(invented, scope)  # the path of memory.write and the knowledge API
    rule = NewItem(
        kind="LESSON", statement=HUMOR_NEVER_PITCHES.statement, stated_by="AGENT", project_id=project,
        payload={
            "lesson_key": HUMOR_NEVER_PITCHES.key, "rule": True, "measured": False,
            "source": HUMOR_NEVER_PITCHES.source,
        },
    )  # fmt: skip
    written = await knowledge.write(rule, scope, measured=True)
    # No data at all: a sweep that only knows measured lessons must not retract a rule.
    result = await service(pool, tenant).sweep(NOW)
    assert result.lessons_retracted == 0
    (still,) = await knowledge.list(scope, kind="LESSON")
    assert still.id == written.id and still.in_force


async def test_a_model_can_neither_write_nor_correct_a_lesson_through_the_memory_tools(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    registry = await _registry(pool, tenant, project)
    fabricated = await registry.call(
        "memory.write",
        {
            "kind": "LESSON",
            "statement": "Replies with a question get 500 views.",
            "payload": {"lesson_key": "X:hook", "winner": {"n": 9}, "loser": {"n": 9}},
        },
    )
    assert fabricated["stan"] == "BLAD" and "liczy produkt" in fabricated["powod"]
    knowledge = KnowledgeStore(pool)
    scope = scope_of(tenant, project)
    real = await knowledge.write(
        NewItem(
            kind="LESSON",
            statement="On X, at about 24 h after posting: a measured sentence (n=3 versus n=3).",
            stated_by="AGENT",
            project_id=project,
            payload={"lesson_key": "X:hook", "winner": {"n": 3}, "loser": {"n": 3}},
        ),
        scope,
        measured=True,
    )
    corrected = await registry.call(
        "memory.correct", {"item_id": str(real.id), "statement": "Now it says 900 views."}
    )
    assert corrected["stan"] == "BLAD" and "liczy produkt" in corrected["powod"]
    (still,) = await knowledge.list(scope, kind="LESSON")
    assert still.id == real.id and still.in_force
    listed = await registry.call("memory.list", {"kind": "LESSON"})
    assert listed["liczba"] == 1 and "nasz wynik" in listed["ustalenia"][0]["uwaga_rodzaj"]


# --------------------------------------------------------------------------------------------
# playbook.read
# --------------------------------------------------------------------------------------------


async def _registry(pool, tenant, project, *, host="codex", hosted=False) -> ToolRegistry:
    async with pool.acquire() as conn:
        session_id = await conn.fetchval(
            "INSERT INTO agent_sessions (organization_id, workspace_id, project_id, goal)"
            " VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project,
        )
    return ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=session_id, organization_id=tenant["organization"],
            workspace_id=tenant["workspace"], project_id=project, dsn="postgresql://atrapa/x",
            host=host, hosted=hosted,
        ),
    )  # fmt: skip


async def test_playbook_read_says_when_nothing_is_measured_and_still_gives_the_voice(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    result = await (await _registry(pool, tenant, project)).call("playbook.read", {})
    assert result["stan"] == "WYKONANE"
    assert result["lekcje"] == [] and result["zasady"] == []
    assert "brak_lekcji" in result and "nie zgaduj" in result["brak_lekcji"]
    assert result["glos"] == VOICE_RULES
    assert result["najlepsze_odpowiedzi"] == [] and result["najlepsze_posty"] == []
    assert result["pokrycie"]["sledzone"] == 0


async def test_playbook_read_returns_lessons_rules_and_the_best_texts_with_their_numbers(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160)):
        await _tracked(pool, tenant, project, i, age=10, views=views)
    for i, views in enumerate((20, 25, 30)):
        await _tracked(pool, tenant, project, 10 + i, age=200, views=views)
    await service(pool, tenant).sweep(NOW)
    knowledge = KnowledgeStore(pool)
    await knowledge.write(
        NewItem(
            kind="LESSON", statement=HUMOR_NEVER_PITCHES.statement, stated_by="AGENT",
            project_id=project,
            payload={"lesson_key": HUMOR_NEVER_PITCHES.key, "rule": True,
                     "source": HUMOR_NEVER_PITCHES.source},
        ),
        scope_of(tenant, project), measured=True,
    )  # fmt: skip
    result = await (await _registry(pool, tenant, project)).call("playbook.read", {"limit": 2})
    (lesson,) = result["lekcje"]
    assert lesson["klucz"] == "X:target_age" and lesson["horyzont_godzin"] == 24
    assert lesson["lepsza"]["grupa"] == "<30 min" and lesson["lepsza"]["n"] == 3
    assert lesson["lepsza"]["mediana_wyswietlen"] == 140 and lesson["gorsza"]["n"] == 3
    assert "brak_lekcji" not in result
    (rule,) = result["zasady"]
    assert "reguła, nie pomiar" in rule["uwaga"] and rule["zrodlo"] == HUMOR_NEVER_PITCHES.source
    best = result["najlepsze_odpowiedzi"]
    assert (
        len(best) == 2
        and best[0]["wyswietlenia"] == 160
        and best[0]["tekst"].startswith("text number")
    )
    assert best[0]["wiek_celu_min"] == 10 and best[0]["horyzont_godzin"] == 24
    assert result["pokrycie"]["z_migawka"]["24h"] == 6 and result["pokrycie"]["sledzone"] == 6
    only_reddit = await (await _registry(pool, tenant, project)).call(
        "playbook.read", {"platform": "REDDIT"}
    )
    assert only_reddit["lekcje"] == [] and only_reddit["najlepsze_odpowiedzi"] == []
    refused = await (await _registry(pool, tenant, project)).call(
        "playbook.read", {"platform": "TIKTOK"}
    )
    assert refused["stan"] == "BLAD"


async def test_playbook_read_is_a_tool_of_the_plugin_and_of_a_hosted_session_and_only_reads(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    for host, hosted in (("codex", False), ("claude-code", False), ("codex", True)):
        registry = await _registry(pool, tenant, project, host=host, hosted=hosted)
        result = await registry.call("playbook.read", {})
        assert result["stan"] == "WYKONANE", (host, hosted)
    async with pool.acquire() as conn:
        written = await conn.fetchval(
            "SELECT count(*) FROM learning_posts WHERE organization_id = $1", tenant["organization"]
        )
    assert written == 0
    # Another project's lesson is not this project's playbook.
    other = await _project(pool, tenant)
    for i, views in enumerate((120, 140, 160)):
        await _tracked(pool, tenant, other, i, age=10, views=views)
    for i, views in enumerate((20, 25, 30)):
        await _tracked(pool, tenant, other, 10 + i, age=200, views=views)
    await service(pool, tenant).sweep(NOW)
    mine = await read_playbook(
        pool,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project,
    )
    assert mine["lekcje"] == [] and mine["najlepsze_odpowiedzi"] == []


# --------------------------------------------------------------------------------------------
# The seed from the founder's diary
# --------------------------------------------------------------------------------------------

DIARY = """\
| # | Data | Kanał | Format | Hook | Treść (początek) | Link | 1 h | 24 h | 72 h | Zapisy |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 12.10 21:05 | X reply | odpowiedź | POMOC | do @simonbs (19,8k) | x:2109722713741029594 |  | ok. 18 h: 69 wyśw., 0 lajków, 0 odp. |  |  |
| 6 | 12.10 ok. 21:30 | X @demo_founder | film v3 | DEMO z liczbą | "Everyone has a coding agent now..." | https://x.com/demo_founder/status/2109728567655571651 | 18 wyśw., 2 lajki, 1 repost, 2 odp. | ok. 18 h: 47 wyśw., 2 lajki, 1 repost, 2 odp. |  |  |
| 30 | 13.10 ok. 15:50 | X reply | odpowiedź (szkic) | OPINIA | do @alexcooldev (68k): "x" | szkic, x:2109997331097833513 |  |  |  |  |
| 18 | 13.10 13:56 | X @demo_founder | własny post | PROWOKACJA | "software is dead, says everyone currently rea..." | https://x.com/demo_founder/status/2109976668630913530 | ok. 1,7 h: 11 wyśw., 0 lajków, 0 odp. |  |  |  |
"""


async def test_the_diary_labels_tracked_items_imports_the_unknown_and_adds_only_honest_readings(
    pool, tenant
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    day = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a reply", published_at=day,
        our_id="x:2109722713741029594", target="x:2109609101357682912",
        target_url="https://x.com/simonbs/status/2109609101357682912",
    )  # fmt: skip
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a reply to alex", published_at=day,
        our_id="x:2110006135294136605", target="x:2109997331097833513",
        target_url="https://x.com/alexcooldev/status/2109997331097833513",
    )  # fmt: skip
    await _publish(
        pool, tenant, project, delegation_id=delegation,
        text="software is dead, says everyone currently reading a pricing page line by line",
        published_at=day, our_id="x:2109976668630913530", target="x:@demo_founder", kind="OWN_POST",
        target_url="https://x.com/demo_founder",
    )  # fmt: skip
    svc = service(pool, tenant)
    await svc.sweep(day + timedelta(minutes=5))
    store = LearningStore(pool)

    rows = diary.parse_diary(DIARY)
    dry = await diary.apply_diary(
        store, rows, organization_id=tenant["organization"], workspace_id=tenant["workspace"],
        project_id=project, now=NOW, dry_run=True,
    )  # fmt: skip
    assert dry.imported == 0 and any("would import x:2109728567655571651" in n for n in dry.notes)
    assert (
        await store.post_by_external(tenant["organization"], "X", "x:2109728567655571651") is None
    )

    report = await diary.apply_diary(
        store, rows, organization_id=tenant["organization"], workspace_id=tenant["workspace"],
        project_id=project, now=NOW,
    )  # fmt: skip
    assert report.imported == 1 and report.labelled == 4

    simon = await store.post_by_external(tenant["organization"], "X", "x:2109722713741029594")
    assert (simon.hook, simon.hook_source, simon.target_followers) == ("POMOC", "DIARY", 19_800)
    assert simon.facts["followers_source"] == "diary"
    alex = await store.post_by_external(tenant["organization"], "X", "x:2110006135294136605")
    assert (alex.hook, alex.target_followers) == (
        "OPINIA",
        68_000,
    )  # a draft row, matched by target
    own = await store.post_by_external(tenant["organization"], "X", "x:2109976668630913530")
    assert own.hook == "PROWOKACJA" and own.hook_source == "DIARY"
    film = await store.post_by_external(tenant["organization"], "X", "x:2109728567655571651")
    assert (
        film is not None
        and film.origin == "DIARY"
        and film.hook == "DEMO"
        and film.facts["body_is_a_prefix"]
    )

    # The film's readings: 1 h (approximate, nominal) and 18 h (a real reading of the 24 h horizon
    # only when that horizon can no longer be taken by the sweep; the film is a day old, so it
    # still can: the 18 h reading is kept as evidence with no horizon).
    snaps = await _snapshots(pool, film.id)
    assert snaps[1]["views"] == 18 and snaps[1]["source"] == "DIARY"
    assert None in snaps and snaps[None]["age_minutes"] == 1080 and 24 not in snaps
    # simon's 18 h reading must not occupy the 24 h slot of a reply the sweep will still read.
    assert 24 not in await _snapshots(pool, simon.id)
    # A second application writes nothing new.
    again = await diary.apply_diary(
        store, rows, organization_id=tenant["organization"], workspace_id=tenant["workspace"],
        project_id=project, now=NOW,
    )  # fmt: skip
    assert again.imported == 0 and again.snapshots == 0


# --------------------------------------------------------------------------------------------
# It rides the hourly experience sweep and never stops it
# --------------------------------------------------------------------------------------------


def _claim(tenant):
    from control_plane.domain.models import ClaimedJob
    from control_plane.knowledge.handlers import JOB_EXPERIENCE

    return ClaimedJob(
        job_id=uuid.uuid4(), organization_id=tenant["organization"], workspace_id=None,
        job_type=JOB_EXPERIENCE, routing_key="knowledge.experience", priority=20, payload_ref=None,
        dispatch_version=1, attempt_limit=1, attempt_id=uuid.uuid4(), attempt_no=1,
        lease_generation=1, lease_owner=uuid.uuid4(), expires_at=None,
    )  # fmt: skip


async def test_the_experience_sweep_runs_the_learning_loop_and_survives_its_failure(
    pool, tenant, caplog
) -> None:
    project = await _project(pool, tenant)
    delegation = await _delegation(pool, tenant, project)
    await _publish(
        pool, tenant, project, delegation_id=delegation, text="a", published_at=datetime.now(UTC) - timedelta(hours=2),
        our_id="x:51", target="x:@a", kind="OWN_POST", target_url="https://x.com/a",
        permalink="https://x.com/a/status/51",
    )  # fmt: skip
    sources = FakeSources()
    handlers = KnowledgeHandlers(
        pool=pool, ingestor=Ingestor(pool=pool), organization_id=tenant["organization"],
        learning_sources=lambda: sources,
    )  # fmt: skip
    await handlers.sweep_experience(_claim(tenant))
    assert (
        await LearningStore(pool).post_by_external(tenant["organization"], "X", "x:51")
    ) is not None

    def broken():
        raise RuntimeError("the capsule factory broke")

    failing = KnowledgeHandlers(
        pool=pool, ingestor=Ingestor(pool=pool), organization_id=tenant["organization"],
        learning_sources=broken,
    )  # fmt: skip
    with caplog.at_level("WARNING"):
        await failing.sweep_experience(_claim(tenant))  # does not raise
    assert "learning.sweep nie powiódł się" in caplog.text
    # Off by default: a handler built the old way learns nothing and says nothing.
    plain = KnowledgeHandlers(
        pool=pool, ingestor=Ingestor(pool=pool), organization_id=tenant["organization"]
    )
    assert plain.learning_sources is None
