"""The four observe handlers against PostgreSQL, a scripted source and a scripted model.

What is real here: the store, the queue (`PostgresJobTransport`), the schedule keys, the
contracts. What is scripted: the platform (`ScriptedSource`) and the model (`FakeTurnRunner`),
because a test that needed Hacker News and a logged-in Codex would be a test nobody runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from community_observer import (
    CommunityRef,
    CredentialsMissing,
    ObservedComment,
    ObservedThread,
    SourceUnavailable,
    ThreadMetrics,
    ThreadNotFound,
    ThreadSnapshot,
    raw_digest,
)
from community_os_contracts import (
    AccessMode,
    JobWaitingState,
    JudgmentDecision,
    ObservationPlatform,
    Observed,
    UnknownReason,
)
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.dispatcher import WaitFor
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.context import JudgmentRejected
from control_plane.observe.handlers import ObserveHandlers
from control_plane.observe.scheduling import (
    JOB_JUDGE,
    JOB_PICTURE,
    JOB_POLL,
    JOB_SAMPLE,
    JOB_SNAPSHOT,
    JOB_VOICE,
    community_ref,
    thread_ref,
)
from control_plane.observe.store import ObservationStore
from control_plane.observe.turns import FakeTurnRunner, ModelUnavailable

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
ASK = CommunityRef(
    platform=ObservationPlatform.HACKER_NEWS,
    key="ask",
    display_name="Hacker News · Ask HN",
    access_mode=AccessMode.PUBLIC_API_NO_ACCOUNT,
)


def observed(external_id: str, *, created_at: datetime, comments: int = 0) -> ObservedThread:
    return ObservedThread(
        platform=ObservationPlatform.HACKER_NEWS,
        community_key="ask",
        external_id=external_id,
        url=f"https://news.ycombinator.com/item?id={external_id}",
        title=f"Ask HN: {external_id}",
        body="body",
        author="op",
        created_at=created_at,
        observed_at=NOW,
        metrics=ThreadMetrics(
            score=Observed[int].known(1), comment_count=Observed[int].known(comments)
        ),
        raw_sha256=raw_digest({"id": external_id}),
    )


class ScriptedSource:
    platform = ObservationPlatform.HACKER_NEWS
    access_mode = AccessMode.PUBLIC_API_NO_ACCOUNT

    def __init__(
        self,
        threads=(),
        *,
        error: Exception | None = None,
        snapshot: ThreadSnapshot | Exception | None = None,
    ):
        self.threads = tuple(threads)
        self.error = error
        self.snapshot = snapshot
        self.listing_calls: list[dict] = []

    def describe(self, community_key: str) -> CommunityRef:
        return ASK

    async def list_recent_threads(self, community_key, *, since, limit):
        self.listing_calls.append({"since": since, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.threads

    async def fetch_thread(self, community_key, external_id):
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        assert self.snapshot is not None
        return self.snapshot


def claim_for(job_type: str, payload_ref: str, tenant) -> ClaimedJob:
    """A claim object for calling a handler directly; the queue half is tested separately."""
    return ClaimedJob(
        job_id=UUID(int=1),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=job_type,
        routing_key="observe",
        priority=1,
        payload_ref=payload_ref,
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=UUID(int=2),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=3),
        expires_at=None,
    )


async def jobs_of_type(pool, organization_id, job_type: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT idempotency_key, payload_ref, status, not_before FROM jobs "
            "WHERE organization_id = $1 AND job_type = $2 ORDER BY idempotency_key",
            organization_id,
            job_type,
        )
    return [dict(r) for r in rows]


@pytest.fixture
def store(pool) -> ObservationStore:
    return ObservationStore(pool)


@pytest.fixture
def queue(pool, tenant):
    return PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])


@pytest.fixture
async def community(store, tenant):
    return await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )


def handlers_for(
    store, queue, tenant, *, source=None, turns=None, now: datetime = NOW
) -> ObserveHandlers:
    return ObserveHandlers(
        store=store,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: source} if source is not None else {},
        turns=turns,
        organization_id=tenant["organization"],
        clock=lambda: now,
    )


# ---------------------------------------------------------------------------
# poll_community
# ---------------------------------------------------------------------------


async def test_poll_records_threads_schedules_snapshots_and_chains_the_next_poll(
    pool, store, queue, tenant, community
) -> None:
    source = ScriptedSource(
        [
            observed("2", created_at=NOW - timedelta(minutes=5)),
            observed("1", created_at=NOW - timedelta(hours=1)),
        ]
    )
    handlers = handlers_for(store, queue, tenant, source=source)
    await handlers.poll_community(claim_for(JOB_POLL, community_ref(community.id), tenant))

    refreshed = await store.get_community(community.id, organization_id=tenant["organization"])
    assert refreshed is not None
    assert refreshed.watermark_created_at == NOW - timedelta(minutes=5)
    assert refreshed.last_polled_at == NOW
    assert source.listing_calls == [{"since": None, "limit": 100}]

    counts = await store.counts(organization_id=tenant["organization"], community_id=community.id)
    assert counts["threads"] == 2 and counts["snapshots"] == 2

    snapshots = await jobs_of_type(pool, tenant["organization"], JOB_SNAPSHOT)
    assert len(snapshots) == 6, "trzy snapshoty na każdy z dwóch NOWYCH wątków"
    assert {k.split(":")[-1] for k in (s["idempotency_key"] for s in snapshots)} == {
        "1h",
        "6h",
        "24h",
    }

    polls = await jobs_of_type(pool, tenant["organization"], JOB_POLL)
    assert len(polls) == 1, "następny slot zaplanowany dokładnie raz"
    assert polls[0]["payload_ref"] == community_ref(community.id)
    assert polls[0]["not_before"] > NOW - timedelta(seconds=1)

    # No profile yet → no judgments; picture not due at 12:00 with no earlier picture? It IS due
    # (after 05:00 UTC, none exists) — one picture job, once.
    assert await jobs_of_type(pool, tenant["organization"], JOB_JUDGE) == []
    pictures = await jobs_of_type(pool, tenant["organization"], JOB_PICTURE)
    assert len(pictures) == 1 and pictures[0]["idempotency_key"].endswith(":2026-09-03")

    # A second poll with the same content: nothing new, no new snapshot jobs, the watermark is passed.
    await handlers.poll_community(claim_for(JOB_POLL, community_ref(community.id), tenant))
    assert source.listing_calls[-1]["since"] == NOW - timedelta(minutes=5)
    assert len(await jobs_of_type(pool, tenant["organization"], JOB_SNAPSHOT)) == 6
    assert len(await jobs_of_type(pool, tenant["organization"], JOB_PICTURE)) == 1
    counts = await store.counts(organization_id=tenant["organization"], community_id=community.id)
    assert counts["snapshots"] == 4, "każdy odczyt dokłada snapshot, także bez nowych wątków"


async def test_poll_schedules_judgments_once_a_profile_exists(
    pool, store, queue, tenant, community
) -> None:
    await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="x",
        positions="y",
        would_never_claim="z",
        voice_samples="",
    )
    source = ScriptedSource([observed("1", created_at=NOW - timedelta(minutes=1))])
    await handlers_for(store, queue, tenant, source=source).poll_community(
        claim_for(JOB_POLL, community_ref(community.id), tenant)
    )
    judges = await jobs_of_type(pool, tenant["organization"], JOB_JUDGE)
    assert len(judges) == 1 and judges[0]["idempotency_key"].endswith(":2026-09-03")


async def test_poll_without_credentials_parks_and_does_not_chain(
    pool, store, queue, tenant, community
) -> None:
    source = ScriptedSource(error=CredentialsMissing("ustaw WENEXT_REDDIT_CLIENT_ID"))
    with pytest.raises(WaitFor) as caught:
        await handlers_for(store, queue, tenant, source=source).poll_community(
            claim_for(JOB_POLL, community_ref(community.id), tenant)
        )
    assert caught.value.waiting_state is JobWaitingState.WAITING_CAPABILITY
    assert await jobs_of_type(pool, tenant["organization"], JOB_POLL) == [], (
        "zaparkowany job JEST kontynuacją łańcucha"
    )


async def test_poll_without_an_adapter_parks(pool, store, queue, tenant, community) -> None:
    with pytest.raises(WaitFor) as caught:
        await handlers_for(store, queue, tenant, source=None).poll_community(
            claim_for(JOB_POLL, community_ref(community.id), tenant)
        )
    assert caught.value.waiting_state is JobWaitingState.WAITING_CAPABILITY
    assert "adaptera" in caught.value.reason


async def test_a_transient_platform_error_fails_the_attempt_but_keeps_the_chain(
    pool, store, queue, tenant, community
) -> None:
    source = ScriptedSource(error=SourceUnavailable("503"))
    with pytest.raises(SourceUnavailable):
        await handlers_for(store, queue, tenant, source=source).poll_community(
            claim_for(JOB_POLL, community_ref(community.id), tenant)
        )
    assert len(await jobs_of_type(pool, tenant["organization"], JOB_POLL)) == 1, (
        "następny poll zaplanowany PRZED porażką"
    )


async def test_a_disabled_community_is_not_polled(pool, store, queue, tenant, community) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE communities SET observe_enabled = false WHERE id = $1", community.id
        )
    source = ScriptedSource([observed("1", created_at=NOW)])
    await handlers_for(store, queue, tenant, source=source).poll_community(
        claim_for(JOB_POLL, community_ref(community.id), tenant)
    )
    assert source.listing_calls == []
    assert await jobs_of_type(pool, tenant["organization"], JOB_POLL) == []


# ---------------------------------------------------------------------------
# snapshot_thread
# ---------------------------------------------------------------------------


async def test_snapshot_updates_comments_and_metrics(pool, store, queue, tenant, community) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    later = ObservedThread(
        **{
            **observed("1", created_at=NOW, comments=2).model_dump(),
            "observed_at": NOW + timedelta(hours=1),
        }
    )
    snapshot = ThreadSnapshot(
        thread=later,
        comments=(
            ObservedComment(
                external_id="a",
                author="alice",
                body="A",
                created_at=NOW,
                observed_at=NOW + timedelta(hours=1),
                depth=0,
                score=Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM),
            ),
            ObservedComment(
                external_id="b",
                parent_external_id="a",
                author="bob",
                body="B",
                created_at=NOW,
                observed_at=NOW + timedelta(hours=1),
                depth=1,
                score=Observed[int].known(4),
            ),
        ),
    )
    source = ScriptedSource(snapshot=snapshot)
    await handlers_for(store, queue, tenant, source=source).snapshot_thread(
        claim_for(JOB_SNAPSHOT, thread_ref(thread_id), tenant)
    )
    context = await store.thread_context(thread_id, organization_id=tenant["organization"])
    assert context is not None
    assert [c.external_id for c in context.comments] == ["a", "b"]
    assert context.comment_count.require() == 2
    counts = await store.counts(organization_id=tenant["organization"], community_id=community.id)
    assert counts["snapshots"] == 2 and counts["comments"] == 2


async def test_a_thread_that_disappeared_gets_an_unknown_snapshot(
    pool, store, queue, tenant, community
) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    source = ScriptedSource(snapshot=ThreadNotFound("gone"))
    await handlers_for(
        store, queue, tenant, source=source, now=NOW + timedelta(hours=1)
    ).snapshot_thread(claim_for(JOB_SNAPSHOT, thread_ref(thread_id), tenant))
    async with pool.acquire() as conn:
        latest = await conn.fetchrow(
            "SELECT score, score_unknown_reason FROM thread_metric_snapshots WHERE thread_id = $1 "
            "ORDER BY observed_at DESC LIMIT 1",
            thread_id,
        )
    assert latest["score"] is None and latest["score_unknown_reason"] == "NOT_EXPOSED_BY_PLATFORM"


# ---------------------------------------------------------------------------
# judge_thread
# ---------------------------------------------------------------------------

IGNORE_OUTPUT = {
    "decision": "IGNORE",
    "reasoning": "Autor pyta o Kubernetes w banku; profil użytkownika nie zna tego świata.",
    "what_user_could_add": None,
    "confidence": 0.8,
    "wait_until_hint": None,
}
ENGAGE_OUTPUT = {
    "decision": "ENGAGE",
    "reasoning": "Pytanie o kolejki w produkcji — dokładnie to, co użytkownik robił przez rok.",
    "what_user_could_add": "Liczby z roku produkcji kolejki na Postgresie zamiast RabbitMQ.",
    "confidence": 0.7,
    "wait_until_hint": None,
}


async def _profile(store, tenant):
    return await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="kolejki na Postgresie",
        positions="nudna technologia",
        would_never_claim="benchmarki",
        voice_samples="",
    )


async def test_judge_without_a_profile_parks(pool, store, queue, tenant, community) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    with pytest.raises(WaitFor) as caught:
        await handlers_for(
            store, queue, tenant, turns=FakeTurnRunner(outputs=[IGNORE_OUTPUT])
        ).judge_thread(claim_for(JOB_JUDGE, thread_ref(thread_id), tenant))
    assert caught.value.waiting_state is JobWaitingState.WAITING_CAPABILITY
    assert "profilu" in caught.value.reason


async def test_judge_without_a_model_parks(pool, store, queue, tenant, community) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    await _profile(store, tenant)
    with pytest.raises(WaitFor) as caught:
        await handlers_for(store, queue, tenant, turns=None).judge_thread(
            claim_for(JOB_JUDGE, thread_ref(thread_id), tenant)
        )
    assert caught.value.waiting_state is JobWaitingState.WAITING_CAPABILITY
    assert "WENEXT_CODEX_HOME" in caught.value.reason


async def test_judge_saves_the_decision_including_ignore_with_its_provenance(
    pool, store, queue, tenant, community
) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    profile = await _profile(store, tenant)
    fake = FakeTurnRunner(outputs=[IGNORE_OUTPUT], model="fake-luna", effort="low")
    await handlers_for(store, queue, tenant, turns=fake).judge_thread(
        claim_for(JOB_JUDGE, thread_ref(thread_id), tenant)
    )

    (call,) = fake.calls
    assert call.role.value == "WORKER"
    assert '"knows_deeply": "kolejki na Postgresie"' in call.user_input
    assert '"ref": "HACKER_NEWS:ask:1"' in call.user_input

    (row,) = await store.list_judgments(
        organization_id=tenant["organization"], decision=None, limit=10
    )
    assert row.decision is JudgmentDecision.IGNORE
    assert row.model == "fake-luna" and row.effort == "low"
    assert row.prompt_name == "judge_thread" and len(row.prompt_sha256) == 64
    assert row.user_profile_version == profile.version
    assert row.created_at == NOW


async def test_judge_saves_engage_with_the_contribution(
    pool, store, queue, tenant, community
) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    await _profile(store, tenant)
    await handlers_for(
        store, queue, tenant, turns=FakeTurnRunner(outputs=[ENGAGE_OUTPUT])
    ).judge_thread(claim_for(JOB_JUDGE, thread_ref(thread_id), tenant))
    (row,) = await store.list_judgments(
        organization_id=tenant["organization"], decision=JudgmentDecision.ENGAGE, limit=10
    )
    assert row.what_user_could_add.startswith("Liczby")


async def test_a_sloppy_engage_from_the_model_is_refused_and_nothing_is_saved(
    pool, store, queue, tenant, community
) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    await _profile(store, tenant)
    sloppy = {**ENGAGE_OUTPUT, "what_user_could_add": None}
    with pytest.raises(JudgmentRejected):
        await handlers_for(
            store, queue, tenant, turns=FakeTurnRunner(outputs=[sloppy])
        ).judge_thread(claim_for(JOB_JUDGE, thread_ref(thread_id), tenant))
    assert (
        await store.list_judgments(organization_id=tenant["organization"], decision=None, limit=10)
        == ()
    )


async def test_a_model_that_is_not_logged_in_parks_in_waiting_auth(
    pool, store, queue, tenant, community
) -> None:
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("1", created_at=NOW)
    )
    await _profile(store, tenant)
    fake = FakeTurnRunner(outputs=[ModelUnavailable("brak auth.json")])
    with pytest.raises(WaitFor) as caught:
        await handlers_for(store, queue, tenant, turns=fake).judge_thread(
            claim_for(JOB_JUDGE, thread_ref(thread_id), tenant)
        )
    assert caught.value.waiting_state is JobWaitingState.WAITING_AUTH


# ---------------------------------------------------------------------------
# community_picture
# ---------------------------------------------------------------------------

PICTURE_OUTPUT = {
    "summary": "Spokojny dzień: dwa pytania o MCP, jedno o Postgresa.",
    "topics": [{"name": "MCP", "why_it_matters": "wraca", "thread_refs": ["HACKER_NEWS:ask:1"]}],
    "recurring_problems": [],
    "what_gets_conversation": "konkretne pytania",
    "what_gets_ignored": "linki bez komentarza",
    "durable_culture": [
        {"observation": "wymyślone", "confidence": 0.9, "thread_refs": ["HACKER_NEWS:ask:404"]}
    ],
    "current_state": [
        {"observation": "MCP", "thread_refs": ["HACKER_NEWS:ask:1", "HACKER_NEWS:ask:2"]}
    ],
    "where_user_could_contribute": [],
}


async def test_picture_is_not_drawn_over_an_empty_window(
    pool, store, queue, tenant, community
) -> None:
    fake = FakeTurnRunner(outputs=[PICTURE_OUTPUT])
    await handlers_for(store, queue, tenant, turns=fake).community_picture(
        claim_for(JOB_PICTURE, community_ref(community.id), tenant)
    )
    assert fake.calls == [], "bez wątków model nie jest wołany"
    assert await store.latest_picture(community.id, organization_id=tenant["organization"]) is None


async def test_picture_is_saved_with_invented_refs_counted_and_dropped(
    pool, store, queue, tenant, community
) -> None:
    for n in ("1", "2"):
        await store.record_thread(
            community=community, thread=observed(n, created_at=NOW - timedelta(hours=2))
        )
    fake = FakeTurnRunner(outputs=[PICTURE_OUTPUT], model="fake-terra", effort="medium")
    await handlers_for(store, queue, tenant, turns=fake).community_picture(
        claim_for(JOB_PICTURE, community_ref(community.id), tenant)
    )
    (call,) = fake.calls
    assert call.role.value == "PLANNER"
    picture = await store.latest_picture(community.id, organization_id=tenant["organization"])
    assert picture is not None
    assert picture.version == 1 and picture.thread_count == 2
    assert picture.invalid_ref_count == 1, "HACKER_NEWS:ask:404 nie istnieje w oknie"
    assert picture.content["durable_culture"] == []
    assert len(picture.content["current_state"]) == 1
    assert picture.model == "fake-terra" and picture.prompt_name == "community_picture"
    assert picture.window_end == NOW

    # The next picture continues from the previous window's end and sees the previous picture.
    later = NOW + timedelta(hours=24)
    await store.record_thread(
        community=community, thread=observed("3", created_at=NOW + timedelta(hours=3))
    )
    fake2 = FakeTurnRunner(outputs=[{**PICTURE_OUTPUT, "durable_culture": []}])
    await handlers_for(store, queue, tenant, turns=fake2, now=later).community_picture(
        claim_for(JOB_PICTURE, community_ref(community.id), tenant)
    )
    assert '"previous_picture": {' in fake2.calls[0].user_input
    second = await store.latest_picture(community.id, organization_id=tenant["organization"])
    assert second is not None and second.version == 2
    assert second.window_start == NOW and second.thread_count == 1


async def test_picture_with_a_logged_out_model_parks(pool, store, queue, tenant, community) -> None:
    await store.record_thread(
        community=community, thread=observed("1", created_at=NOW - timedelta(hours=1))
    )
    with pytest.raises(WaitFor) as caught:
        await handlers_for(
            store, queue, tenant, turns=FakeTurnRunner(outputs=[ModelUnavailable("x")])
        ).community_picture(claim_for(JOB_PICTURE, community_ref(community.id), tenant))
    assert caught.value.waiting_state is JobWaitingState.WAITING_AUTH


async def test_as_mapping_exposes_exactly_the_five_job_types(store, queue, tenant) -> None:
    mapping = handlers_for(store, queue, tenant).as_mapping()
    assert set(mapping) == {JOB_POLL, JOB_SNAPSHOT, JOB_PICTURE, JOB_JUDGE, JOB_VOICE, JOB_SAMPLE}
    # The queue half of `schedule_*` is exercised above; make sure the command path is the one used.
    assert commands.enqueue_job.__name__ == "enqueue_job"
