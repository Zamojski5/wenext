"""`ObservationStore` against PostgreSQL: upserts, append-only snapshots, versions, tenancy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from community_observer import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    raw_digest,
)
from community_os_contracts import (
    AccessMode,
    JudgmentDecision,
    JudgmentFeedbackVerdict,
    JudgmentRecord,
    ObservationPlatform,
    Observed,
    UnknownReason,
)
from control_plane.observe.store import ObservationStore

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
SHA = "c" * 64
ASK = CommunityRef(
    platform=ObservationPlatform.HACKER_NEWS,
    key="ask",
    display_name="Hacker News · Ask HN",
    access_mode=AccessMode.PUBLIC_API_NO_ACCOUNT,
)


def observed_thread(
    external_id: str, *, created_at: datetime = T0, observed_at: datetime = T0, **overrides
) -> ObservedThread:
    base = {
        "platform": ObservationPlatform.HACKER_NEWS,
        "community_key": "ask",
        "external_id": external_id,
        "url": f"https://news.ycombinator.com/item?id={external_id}",
        "title": f"Ask HN: {external_id}",
        "body": "first body",
        "author": "someone",
        "created_at": created_at,
        "observed_at": observed_at,
        "metrics": ThreadMetrics(
            score=Observed[int].known(3), comment_count=Observed[int].known(0)
        ),
        "raw_sha256": raw_digest({"id": external_id, "v": 1}),
    }
    base.update(overrides)
    return ObservedThread(**base)


def comment(
    external_id: str,
    *,
    parent: str | None = None,
    depth: int = 0,
    body: str = "hi",
    score: int | None = None,
) -> ObservedComment:
    return ObservedComment(
        external_id=external_id,
        parent_external_id=parent,
        author="alice",
        body=body,
        created_at=T0 + timedelta(minutes=1),
        observed_at=T0 + timedelta(minutes=2),
        depth=depth,
        score=Observed[int].known(score)
        if score is not None
        else Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM),
    )


def record(**overrides) -> JudgmentRecord:
    base = {
        "thread_ref": "HACKER_NEWS:ask:1",
        "decision": JudgmentDecision.IGNORE,
        "reasoning": "Autor pyta o rzecz spoza doświadczenia użytkownika; nic do dodania.",
        "confidence": 0.7,
        "user_profile_version": 1,
        "prompt_name": "judge_thread",
        "prompt_sha256": SHA,
        "model": "m",
        "effort": "low",
        "created_at": T0,
    }
    base.update(overrides)
    return JudgmentRecord(**base)


@pytest.fixture
def store(pool) -> ObservationStore:
    return ObservationStore(pool)


async def test_sole_workspace_is_the_one_or_none(store: ObservationStore, tenant, pool) -> None:
    assert await store.sole_workspace(tenant["organization"]) == tenant["workspace"]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'second')",
            tenant["organization"],
        )
    assert await store.sole_workspace(tenant["organization"]) is None, (
        "dwa workspace'y to nie „jakiś”"
    )


async def test_upsert_community_is_idempotent_and_re_enables(
    store: ObservationStore, tenant, pool
) -> None:
    first = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    async with pool.acquire() as conn:
        await conn.execute("UPDATE communities SET observe_enabled = false WHERE id = $1", first.id)
    again = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=900,
    )
    assert again.id == first.id
    assert again.observe_enabled and again.poll_interval_seconds == 900
    assert len(await store.list_communities(organization_id=tenant["organization"])) == 1
    assert await store.get_community(first.id, organization_id=tenant["organization"]) is not None


async def test_record_thread_upserts_and_appends_a_snapshot_each_time(
    store: ObservationStore, tenant, pool
) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    thread_id, created = await store.record_thread(community=community, thread=observed_thread("1"))
    assert created
    later = observed_thread(
        "1",
        observed_at=T0 + timedelta(hours=1),
        body="edited body",
        metrics=ThreadMetrics(
            score=Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM),
            comment_count=Observed[int].known(4),
        ),
        raw_sha256=raw_digest({"id": "1", "v": 2}),
    )
    same_id, created_again = await store.record_thread(community=community, thread=later)
    assert same_id == thread_id and not created_again

    row = await store.get_thread(thread_id, organization_id=tenant["organization"])
    assert row is not None
    assert row.body == "edited body"
    assert row.first_observed_at == T0, "first_observed_at nigdy się nie cofa ani nie przesuwa"
    assert row.last_observed_at == T0 + timedelta(hours=1)

    async with pool.acquire() as conn:
        snapshots = await conn.fetch(
            "SELECT score, score_unknown_reason, comment_count FROM thread_metric_snapshots "
            "WHERE thread_id = $1 ORDER BY observed_at",
            thread_id,
        )
    assert [dict(s) for s in snapshots] == [
        {"score": 3, "score_unknown_reason": None, "comment_count": 0},
        {"score": None, "score_unknown_reason": "NOT_EXPOSED_BY_PLATFORM", "comment_count": 4},
    ]


async def test_record_thread_refuses_a_thread_from_another_community(
    store: ObservationStore, tenant
) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    with pytest.raises(ValueError, match="nie należy"):
        await store.record_thread(
            community=community, thread=observed_thread("1", community_key="show")
        )


async def test_comments_upsert_and_thread_context_carries_them_with_latest_metrics(
    store: ObservationStore, tenant
) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    thread_id, _ = await store.record_thread(community=community, thread=observed_thread("7"))
    written = await store.record_comments(
        community=community,
        thread_id=thread_id,
        comments=[comment("a"), comment("b", parent="a", depth=1)],
    )
    assert written == 2
    await store.record_comments(
        community=community,
        thread_id=thread_id,
        comments=[comment("a", body="edited"), comment("c")],
    )
    context = await store.thread_context(thread_id, organization_id=tenant["organization"])
    assert context is not None
    assert context.ref == "HACKER_NEWS:ask:7"
    assert {c.external_id: c.body for c in context.comments} == {
        "a": "edited",
        "b": "hi",
        "c": "hi",
    }
    assert context.score.require() == 3

    await store.append_snapshot(
        community=community,
        thread_id=thread_id,
        observed_at=T0 + timedelta(hours=6),
        metrics=ThreadMetrics(score=Observed[int].known(40), comment_count=Observed[int].known(3)),
    )
    context = await store.thread_context(thread_id, organization_id=tenant["organization"])
    assert context is not None and context.score.require() == 40, (
        "kontekst niesie NAJNOWSZY snapshot"
    )
    assert await store.record_comments(community=community, thread_id=thread_id, comments=[]) == 0


async def test_threads_in_window_filters_by_platform_created_at(
    store: ObservationStore, tenant
) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    for n, hours in (("1", 0), ("2", 5), ("3", 30)):
        await store.record_thread(
            community=community, thread=observed_thread(n, created_at=T0 + timedelta(hours=hours))
        )
    window = await store.threads_in_window(
        community=community,
        window_start=T0 - timedelta(hours=1),
        window_end=T0 + timedelta(hours=24),
    )
    assert [t.external_id for t in window] == ["2", "1"], (
        "najnowsze najpierw, wątek spoza okna pominięty"
    )


async def test_unjudged_thread_ids_and_the_judgment_round_trip(
    store: ObservationStore, tenant
) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    thread_id, _ = await store.record_thread(community=community, thread=observed_thread("1"))
    older_id, _ = await store.record_thread(
        community=community,
        thread=observed_thread(
            "0", created_at=T0 - timedelta(days=5), observed_at=T0 - timedelta(days=5)
        ),
    )
    since = T0 - timedelta(hours=48)
    assert await store.unjudged_thread_ids(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        first_observed_since=since,
    ) == (thread_id,), "stary wątek jest poza oknem sweepu"

    profile = await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="kolejki",
        positions="nudna technologia wygrywa",
        would_never_claim="benchmarków, których nie robił",
        voice_samples="",
    )
    assert profile.version == 1
    judgment_id = await store.save_judgment(
        community=community,
        thread_id=thread_id,
        record=record(),
        user_profile_version_id=profile.id,
        community_picture_id=None,
    )
    assert (
        await store.unjudged_thread_ids(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            first_observed_since=since,
        )
        == ()
    )

    rows = await store.list_judgments(
        organization_id=tenant["organization"], decision=None, limit=10
    )
    assert [r.id for r in rows] == [judgment_id]
    row = rows[0]
    assert row.decision is JudgmentDecision.IGNORE
    assert row.thread_ref == "HACKER_NEWS:ask:1" and row.thread_title == "Ask HN: 1"
    assert row.user_profile_version == 1 and row.latest_feedback is None
    assert row.workspace_id == tenant["workspace"]
    assert (
        await store.list_judgments(
            organization_id=tenant["organization"], decision=JudgmentDecision.ENGAGE, limit=10
        )
        == ()
    )

    await store.record_feedback(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        judgment_id=judgment_id,
        verdict=JudgmentFeedbackVerdict.DISAGREE,
        note="miałbym coś",
    )
    await store.record_feedback(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        judgment_id=judgment_id,
        verdict=JudgmentFeedbackVerdict.AGREE,
        note=None,
    )
    fetched = await store.get_judgment(judgment_id, organization_id=tenant["organization"])
    assert fetched is not None and fetched.latest_feedback is JudgmentFeedbackVerdict.AGREE, (
        "ostatni głos wygrywa w odczycie"
    )
    summary = await store.agreement_summary(organization_id=tenant["organization"])
    assert summary["decision_IGNORE"] == 1 and summary["decision_ENGAGE"] == 0
    assert summary["feedback_AGREE"] == 1 and summary["feedback_DISAGREE"] == 0, (
        "liczony tylko ostatni feedback"
    )
    assert older_id != thread_id


async def test_pictures_are_versioned_by_the_database(store: ObservationStore, tenant) -> None:
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    assert await store.latest_picture(community.id, organization_id=tenant["organization"]) is None
    for n in (1, 2):
        saved = await store.save_picture(
            community=community,
            window_start=T0 - timedelta(days=1),
            window_end=T0,
            content={"summary": f"v{n}", "topics": []},
            thread_count=n,
            invalid_ref_count=0,
            model="m",
            effort="e",
            prompt_name="community_picture",
            prompt_sha256=SHA,
        )
        assert saved.version == n
    latest = await store.latest_picture(community.id, organization_id=tenant["organization"])
    assert latest is not None and latest.version == 2 and latest.content["summary"] == "v2"
    counts = await store.counts(organization_id=tenant["organization"], community_id=community.id)
    assert counts == {"threads": 0, "comments": 0, "snapshots": 0, "judgments": 0, "pictures": 2}


async def test_another_organization_sees_nothing(store: ObservationStore, tenant, pool) -> None:
    """With RLS off, the `WHERE organization_id = $1` in every query IS the tenant boundary."""
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    thread_id, _ = await store.record_thread(community=community, thread=observed_thread("1"))
    async with pool.acquire() as conn:
        other = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('other') RETURNING id"
        )
    assert await store.list_communities(organization_id=other) == ()
    assert await store.get_community(community.id, organization_id=other) is None
    assert await store.get_thread(thread_id, organization_id=other) is None
    assert await store.thread_context(thread_id, organization_id=other) is None
    assert await store.list_judgments(organization_id=other, decision=None, limit=10) == ()
    with pytest.raises(LookupError):
        await store.set_polled(community.id, organization_id=other, polled_at=T0, watermark=None)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        # A profile of another organization cannot ground a judgment of this one.
        await store.save_judgment(
            community=community,
            thread_id=thread_id,
            record=record(),
            user_profile_version_id=thread_id,  # any uuid that is not a profile of this workspace
            community_picture_id=None,
        )
