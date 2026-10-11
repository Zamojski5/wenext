"""The pilot's cycle against PostgreSQL, a scripted platform and a scripted model
(docs/START-PILOTA-24H.md, point 5: fresh context, WAIT after its deadline, duplicate, restart with
the budget kept, end of window)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from community_observer import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    SourceUnavailable,
    ThreadMetrics,
    ThreadSnapshot,
    raw_digest,
)
from community_os_contracts import AccessMode, ObservationPlatform, Observed, UnknownReason
from control_plane.api.waiting_jobs import deferred_for_pilot
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe import prompts
from control_plane.observe.handlers import SAMPLE_LOOKBACK, ObserveHandlers
from control_plane.observe.reactions import PROMPT_NAME
from control_plane.observe.scheduling import JOB_JUDGE, JOB_PICTURE, community_ref, thread_ref
from control_plane.observe.store import ObservationStore
from control_plane.observe.turns import FakeTurnRunner, ModelUnavailable
from control_plane.pilot.handlers import (
    CANDIDATE_LOOKBACK,
    CANDIDATE_MIN_COMMENTS,
    CANDIDATES_PER_CYCLE,
    CYCLE_INTERVAL_SECONDS,
    FRESH_CANDIDATE_AGE,
    FRESH_WAIT_DELAY,
    JOB_PILOT_CYCLE,
    OUTCOME_DUPLICATE,
    OUTCOME_GENERATED,
    OUTCOME_MODEL_UNAVAILABLE,
    OUTCOME_NO_CANDIDATE,
    OUTCOME_PACING,
    OUTCOME_PLATFORM_UNAVAILABLE,
    PAUSED_JOB_TYPES,
    RENEWAL_CYCLE_INTERVAL_SECONDS,
    PilotGuard,
    PilotHandlers,
    cycle_interval,
    pilot_ref,
    planned_at,
    slot_of,
)
from control_plane.pilot.policy import PolicyStore
from control_plane.pilot.publisher import JOB_PUBLISH
from control_plane.pilot.store import RUN_ACTIVE, RUN_FINISHED, RUN_STOPPED, PilotStore

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 6, 21, 0, tzinfo=UTC)
ASK = CommunityRef(
    platform=ObservationPlatform.HACKER_NEWS,
    key="ask",
    display_name="Hacker News, Ask HN",
    access_mode=AccessMode.PUBLIC_API_NO_ACCOUNT,
)
NOT_EXPOSED = Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)


def observed(
    external_id: str,
    *,
    created_at: datetime,
    community_key: str = "ask",
    observed_at: datetime = NOW,
) -> ObservedThread:
    return ObservedThread(
        platform=ObservationPlatform.HACKER_NEWS,
        community_key=community_key,
        external_id=external_id,
        url=f"https://news.ycombinator.com/item?id={external_id}",
        title=f"Ask HN: {external_id}",
        body="the question body",
        author="op",
        created_at=created_at,
        observed_at=observed_at,
        metrics=ThreadMetrics(score=Observed[int].known(3), comment_count=Observed[int].known(1)),
        raw_sha256=raw_digest({"id": external_id}),
    )


def comment(external_id: str, *, minutes_before: int, body: str = "a comment") -> ObservedComment:
    """A top-level comment: its parent is the thread, spelled as `None` in the observer model."""
    return ObservedComment(
        external_id=external_id,
        parent_external_id=None,
        author=f"u_{external_id}",
        body=body,
        created_at=NOW - timedelta(minutes=minutes_before),
        observed_at=NOW,
        depth=0,
        score=NOT_EXPOSED,
    )


class ScriptedSource:
    platform = ObservationPlatform.HACKER_NEWS
    access_mode = AccessMode.PUBLIC_API_NO_ACCOUNT

    def __init__(self, snapshots: dict[str, ThreadSnapshot], *, error: Exception | None = None):
        self.snapshots = dict(snapshots)
        self.error = error
        self.fetches: list[str] = []

    def describe(self, community_key: str) -> CommunityRef:
        return ASK

    async def list_recent_threads(self, community_key, *, since, limit):
        return ()

    async def fetch_thread(self, community_key, external_id):
        self.fetches.append(external_id)
        if self.error is not None:
            raise self.error
        return self.snapshots[external_id]


def output(decision: str = "HELP", **overrides) -> dict:
    base = {
        "decision": decision,
        "draft": "One concrete thing: count pops against completions before touching config."
        if decision not in ("WAIT", "SILENCE")
        else None,
        "target_message_id": "m1",
        "contribution": "names one measurement the thread does not have yet"
        if decision not in ("WAIT", "SILENCE")
        else None,
        "brief_rationale": "The profile supports operating queues; the thread lacks a measurement.",
        "sources_used": [],
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def claim_for(run_id: UUID, tenant) -> ClaimedJob:
    return ClaimedJob(
        job_id=UUID(int=11),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_PILOT_CYCLE,
        routing_key="observe",
        priority=5,
        payload_ref=pilot_ref(run_id),
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=UUID(int=12),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=13),
        expires_at=None,
    )


@pytest.fixture
def store(pool) -> ObservationStore:
    return ObservationStore(pool)


@pytest.fixture
def pilot(pool) -> PilotStore:
    return PilotStore(pool)


@pytest.fixture
def queue(pool, tenant):
    return PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])


@pytest.fixture
async def community(store, tenant):
    return await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=300,
    )


@pytest.fixture
async def profile(store, tenant):
    return await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="queues in production",
        positions="measure before tuning",
        would_never_claim="legal advice",
        voice_samples="short, dry",
    )


async def make_run(
    pilot,
    tenant,
    community,
    profile,
    *,
    starts_at=NOW,
    hours=24,
    budget=24,
    interval=CYCLE_INTERVAL_SECONDS,
):
    prompt = prompts.get(PROMPT_NAME)
    return await pilot.create_run(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        community_id=community.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=hours),
        poll_interval_seconds=interval,
        model_calls_budget=budget,
        max_generations_per_thread=2,
        profile_version_id=profile.id,
        prompt_name=prompt.name,
        prompt_sha256=prompt.sha256,
        paused_job_types=PAUSED_JOB_TYPES,
        restore={"poll_interval_seconds": 300},
    )


def handlers_for(
    store, pilot, queue, tenant, *, source, turns, now=NOW, policies=None
) -> PilotHandlers:
    return PilotHandlers(
        store=store,
        pilot=pilot,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: source},
        turns=turns,
        organization_id=tenant["organization"],
        clock=lambda: now,
        policies=policies,
    )


async def seed_thread(
    store, community, external_id: str, *, comments: int = 1, observed_at: datetime = NOW
):
    thread = observed(
        external_id, created_at=observed_at - timedelta(hours=2), observed_at=observed_at
    )
    thread_id, _ = await store.record_thread(community=community, thread=thread)
    await store.record_comments(
        community=community,
        thread_id=thread_id,
        comments=tuple(
            comment(f"c{external_id}-{i}", minutes_before=60 - i) for i in range(comments)
        ),
    )
    return thread_id, thread


async def pilot_jobs(pool, organization_id) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT idempotency_key, status FROM jobs WHERE organization_id = $1 AND job_type = $2 "
            "ORDER BY idempotency_key",
            organization_id,
            JOB_PILOT_CYCLE,
        )
    return [dict(r) for r in rows]


# -- one attempt: fresh read, saved reaction, budget reserved first ---------------------------


async def test_run_once_reads_fresh_reserves_budget_and_saves_the_reaction(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    thread_id, thread = await seed_thread(store, community, "100")
    fresh_snapshot = ThreadSnapshot(
        thread=thread,
        comments=(
            comment("c100-0", minutes_before=60),
            comment("c100-9", minutes_before=1, body="a brand new comment"),
        ),
    )
    source = ScriptedSource({"100": fresh_snapshot})
    fake = FakeTurnRunner(outputs=[output("HELP")], model="fake-terra", effort="medium")
    run = await make_run(pilot, tenant, community, profile)

    result = await handlers_for(store, pilot, queue, tenant, source=source, turns=fake).run_once(
        run, now=NOW, origin="SMOKE"
    )

    assert result.outcome == OUTCOME_GENERATED and source.fetches == ["100"]
    (call,) = fake.calls
    assert '"a brand new comment"' in call.user_input, "świeży odczyt, nie stan z bazy"
    assert '"account_recent_drafts": []' in call.user_input
    assert '"exemplars": []' in call.user_input, (
        "prompt v3: przykłady społeczności zawsze w wejściu (tu puste: komentarze bez punktów)"
    )
    saved = result.reaction
    assert saved is not None and saved.origin == "SMOKE" and saved.decision == "HELP"
    assert saved.move == "NONE", "wyjście sprzed v3 bez ruchu zapisuje się jako NONE"
    assert saved.target_message_external_id == "100", "cel = post (neutralne m1 → prawdziwy id)"
    assert saved.thread_url.endswith("id=100") and saved.review_status == "HUMAN_REVIEW_PENDING"
    assert saved.context_complete is True and saved.comments_seen == 2
    assert saved.profile_version_id == profile.id and saved.prompt_sha256 == run.prompt_sha256
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == 1
    context = await store.thread_context(thread_id, organization_id=tenant["organization"])
    assert context is not None and len(context.comments) == 2, "świeży odczyt zapisany w bazie"


async def test_the_same_conversation_version_is_a_duplicate_without_a_model_call(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    _, thread = await seed_thread(store, community, "101")
    snapshot = ThreadSnapshot(thread=thread, comments=(comment("c101-0", minutes_before=30),))
    source = ScriptedSource({"101": snapshot})
    fake = FakeTurnRunner(outputs=[output("HELP")])  # ONE scripted answer: a second call would fail
    run = await make_run(pilot, tenant, community, profile)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    first = await handlers.run_once(run, now=NOW, origin="SMOKE")
    second = await handlers.run_once(run, now=NOW + timedelta(minutes=30), origin="CYCLE")

    assert first.outcome == OUTCOME_GENERATED
    assert second.outcome in (OUTCOME_DUPLICATE, OUTCOME_NO_CANDIDATE), second
    assert len(fake.calls) == 1, "duplikat sytuacji nie woła modelu"
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == 1


async def test_a_wait_comes_back_after_its_deadline_when_the_conversation_changed(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    _, thread = await seed_thread(store, community, "102")
    before = ThreadSnapshot(thread=thread, comments=(comment("c102-0", minutes_before=30),))
    source = ScriptedSource({"102": before})
    fake = FakeTurnRunner(outputs=[output("WAIT"), output("HELP")])
    run = await make_run(pilot, tenant, community, profile)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    first = await handlers.run_once(run, now=NOW, origin="SMOKE")
    assert first.outcome == OUTCOME_GENERATED and first.reaction is not None
    assert first.reaction.decision == "WAIT" and first.reaction.next_check_at == NOW + timedelta(
        hours=3
    )
    assert first.reaction.wait_reason

    # Before the deadline the thread is neither a fresh candidate nor a due WAIT.
    early = await handlers.run_once(run, now=NOW + timedelta(hours=1), origin="CYCLE")
    assert early.outcome == OUTCOME_NO_CANDIDATE and len(fake.calls) == 1

    # After the deadline, with a new comment, it is read again and gets a new reaction.
    source.snapshots["102"] = ThreadSnapshot(
        thread=thread,
        comments=(
            comment("c102-0", minutes_before=30),
            comment("c102-1", minutes_before=-200, body="the OP came back"),
        ),
    )
    late = await handlers.run_once(run, now=NOW + timedelta(hours=4), origin="CYCLE")
    assert late.outcome == OUTCOME_GENERATED and late.reaction is not None
    assert late.reaction.decision == "HELP" and len(fake.calls) == 2
    assert (await pilot.generations_for_thread(run.id, late.reaction.thread_id)) == 2

    # Two generations per thread is the cap: a third due WAIT would not be generated.
    third = await handlers.run_once(run, now=NOW + timedelta(hours=8), origin="CYCLE")
    assert third.outcome == OUTCOME_NO_CANDIDATE and len(fake.calls) == 2


async def test_pacing_holds_a_candidate_back_and_budget_exhaustion_is_named(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    _, thread = await seed_thread(store, community, "103")
    source = ScriptedSource(
        {"103": ThreadSnapshot(thread=thread, comments=(comment("c103-0", minutes_before=5),))}
    )
    fake = FakeTurnRunner(outputs=[output("SILENCE")] * 5)
    run = await make_run(pilot, tenant, community, profile, budget=3)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    # At t0 two calls are allowed (smoke + one); the third must wait for the clock.
    assert (await handlers.run_once(run, now=NOW, origin="SMOKE")).outcome == OUTCOME_GENERATED
    # a different thread so the situation hash differs
    _, thread2 = await seed_thread(store, community, "104")
    source.snapshots["104"] = ThreadSnapshot(
        thread=thread2, comments=(comment("c104-0", minutes_before=5),)
    )
    assert (await handlers.run_once(run, now=NOW, origin="CYCLE")).outcome == OUTCOME_GENERATED
    _, thread3 = await seed_thread(store, community, "105")
    source.snapshots["105"] = ThreadSnapshot(
        thread=thread3, comments=(comment("c105-0", minutes_before=5),)
    )
    paced = await handlers.run_once(run, now=NOW + timedelta(minutes=1), origin="CYCLE")
    assert paced.outcome == OUTCOME_PACING and len(fake.calls) == 2
    # Late in the window the pacing allows the last call; after it the budget is exhausted.
    # Candidates come from the last 12 h, so the late threads are observed late in the window.
    late_seen = NOW + timedelta(hours=22)
    _, thread5 = await seed_thread(store, community, "107", observed_at=late_seen)
    source.snapshots["107"] = ThreadSnapshot(
        thread=thread5, comments=(comment("c107-0", minutes_before=-60 * 22),)
    )
    assert (
        await handlers.run_once(run, now=NOW + timedelta(hours=23), origin="CYCLE")
    ).outcome == OUTCOME_GENERATED
    _, thread4 = await seed_thread(store, community, "106", observed_at=late_seen)
    source.snapshots["106"] = ThreadSnapshot(
        thread=thread4, comments=(comment("c106-0", minutes_before=-60 * 22),)
    )
    exhausted = await handlers.run_once(
        run, now=NOW + timedelta(hours=23, minutes=30), origin="CYCLE"
    )
    assert exhausted.outcome == "BUDGET_EXHAUSTED" and len(fake.calls) == 3
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == 3


async def test_the_budget_counter_survives_a_restart_because_it_is_a_row(
    pool, pilot, tenant, community, profile
) -> None:
    run = await make_run(pilot, tenant, community, profile, budget=2)
    assert await pilot.reserve_model_call(run.id) == 1
    fresh_process = PilotStore(pool)  # "restart": a new store over the same database
    assert await fresh_process.reserve_model_call(run.id) == 2
    assert await fresh_process.reserve_model_call(run.id) is None, "budżet w bazie, nie w pamięci"
    after = await fresh_process.get_run(run.id)
    assert after is not None and after.model_calls_used == 2 and after.budget_left == 0


async def test_model_and_platform_outages_are_named_outcomes_and_still_cost_what_they_cost(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    _, thread = await seed_thread(store, community, "107")
    snapshot = ThreadSnapshot(thread=thread, comments=(comment("c107-0", minutes_before=5),))
    run = await make_run(pilot, tenant, community, profile)

    down_model = FakeTurnRunner(outputs=[ModelUnavailable("usage limit, try again at 7:24 PM")])
    result = await handlers_for(
        store, pilot, queue, tenant, source=ScriptedSource({"107": snapshot}), turns=down_model
    ).run_once(run, now=NOW, origin="CYCLE")
    assert result.outcome == OUTCOME_MODEL_UNAVAILABLE and "usage limit" in result.detail
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == 0, (
        "odmowa dostawcy przed pracą zwraca rezerwację: limit dostawcy nie zjada budżetu pilota"
    )

    down_platform = ScriptedSource({}, error=SourceUnavailable("HN 503"))
    result = await handlers_for(
        store, pilot, queue, tenant, source=down_platform, turns=FakeTurnRunner(outputs=[])
    ).run_once(run, now=NOW + timedelta(minutes=30), origin="CYCLE")
    assert result.outcome == OUTCOME_PLATFORM_UNAVAILABLE and "503" in result.detail
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and refreshed.model_calls_used == 0, (
        "bez odczytu nie ma rezerwacji"
    )


# -- the job: cycles, chaining, the end of the window -----------------------------------------


async def test_a_cycle_records_itself_and_chains_the_next_slot(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    _, thread = await seed_thread(store, community, "108")
    source = ScriptedSource(
        {"108": ThreadSnapshot(thread=thread, comments=(comment("c108-0", minutes_before=5),))}
    )
    fake = FakeTurnRunner(outputs=[output("SILENCE")])
    run = await make_run(pilot, tenant, community, profile)
    now = NOW + timedelta(minutes=30)  # slot 1
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake, now=now)

    await handlers.cycle(claim_for(run.id, tenant))

    cycles = await pilot.cycles(run.id)
    assert [c.slot for c in cycles] == [1]
    assert cycles[0].outcome == OUTCOME_GENERATED and cycles[0].finished_at is not None
    assert cycles[0].model_calls_used_after == 1
    jobs = await pilot_jobs(pool, tenant["organization"])
    assert [j["idempotency_key"] for j in jobs] == [f"pilot:{run.id}:2"], (
        "następny slot zlecony raz"
    )

    # The same slot again (a duplicate job): no second row, no second call, and the next slot
    # is still there exactly once (the idempotency key absorbs the repeat).
    await handlers.cycle(claim_for(run.id, tenant))
    assert len(await pilot.cycles(run.id)) == 1 and len(fake.calls) == 1
    assert [j["idempotency_key"] for j in await pilot_jobs(pool, tenant["organization"])] == [
        f"pilot:{run.id}:2"
    ]


async def test_an_hourly_run_measures_its_slots_by_its_own_interval_not_the_module_default(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """`--cycle-interval 3600`: the manifest's `poll_interval_seconds` is the slot length. Half an
    hour into an hourly run is still slot 0, the cycle at one hour is slot 1, and the next job is
    due about an hour later, not 30 min (the module constant is only the default)."""
    _, thread = await seed_thread(store, community, "109")
    source = ScriptedSource(
        {"109": ThreadSnapshot(thread=thread, comments=(comment("c109-0", minutes_before=5),))}
    )
    fake = FakeTurnRunner(outputs=[output("SILENCE")])
    run = await make_run(pilot, tenant, community, profile, interval=3600)
    assert cycle_interval(run) == 3600 and run.poll_interval_seconds == 3600
    assert slot_of(run, NOW + timedelta(minutes=30)) == 0, "pół godziny to wciąż slot 0"
    assert slot_of(run, NOW + timedelta(hours=1)) == 1
    assert planned_at(run, 2) == NOW + timedelta(hours=2)

    now = NOW + timedelta(hours=1)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake, now=now)
    await handlers.cycle(claim_for(run.id, tenant))

    cycles = await pilot.cycles(run.id)
    assert [c.slot for c in cycles] == [1] and cycles[0].planned_at == now
    assert cycles[0].outcome == OUTCOME_GENERATED
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT idempotency_key, EXTRACT(EPOCH FROM (not_before - now())) AS due_in "
            "FROM jobs WHERE organization_id = $1 AND job_type = $2 ORDER BY idempotency_key",
            tenant["organization"],
            JOB_PILOT_CYCLE,
        )
    assert [r["idempotency_key"] for r in rows] == [f"pilot:{run.id}:2"]
    due_in = float(rows[0]["due_in"])
    assert 3300 <= due_in <= 3600, f"następny slot za ~godzinę, nie za 30 min: {due_in:.0f} s"


async def test_after_the_window_the_run_is_finished_the_poll_restored_and_nothing_scheduled(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    run = await make_run(pilot, tenant, community, profile, hours=1)
    await store.set_poll_interval(community.id, CYCLE_INTERVAL_SECONDS)
    late = NOW + timedelta(hours=1, seconds=5)
    handlers = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=ScriptedSource({}),
        turns=FakeTurnRunner(outputs=[]),
        now=late,
    )

    await handlers.cycle(claim_for(run.id, tenant))

    finished = await pilot.get_run(run.id)
    assert finished is not None and finished.status == RUN_FINISHED and finished.stopped_at == late
    restored = await store.get_community(community.id, organization_id=tenant["organization"])
    assert restored is not None and restored.poll_interval_seconds == 300, (
        "konfiguracja przywrócona"
    )
    cycles = await pilot.cycles(run.id)
    assert cycles and cycles[-1].outcome == "RUN_ENDED"
    assert await pilot_jobs(pool, tenant["organization"]) == [], "po ends_at żadnego nowego cyklu"
    assert await pilot.active_run_for_community(community.id) is None


async def test_after_the_window_a_run_covered_by_an_active_policy_gets_a_successor(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """D-029: one signature, seven days. A run that ends by the clock under an active policy is
    followed at once by a new run for the same community: the ten-minute cycle of D-030 (the
    hourly predecessor does not slow it down), the newest profile version, window to the end of
    the policy, budget two calls per hour (generation plus judgment), the schedule to restore
    carried over, and its first cycle already in the queue. A run a human STOPPED is never
    renewed."""
    run = await make_run(pilot, tenant, community, profile, hours=1, interval=3600)
    await store.set_poll_interval(community.id, 3600)
    prompt = prompts.get("draft_judge")
    policy = await PolicyStore(pool).create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        operator_id=tenant["operator"],
        valid_from=NOW,
        valid_until=NOW + timedelta(days=3),
        community_ids=[community.id],
        account_handle="Demo_Maintainer",
        allowed_kinds=("HELP",),
        min_confidence=0.8,
        max_chars=700,
        max_per_hour=1,
        max_per_day=6,
        judge_prompt_name=prompt.name,
        judge_prompt_sha256=prompt.sha256,
        source_text="test: podpis założyciela",
    )
    late = NOW + timedelta(hours=1, seconds=5)
    # A profile saved after the run started (`pilot-profile-save`): the successor writes with it.
    newer_profile = await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="queues in production",
        positions="measure before tuning",
        would_never_claim="legal advice",
        voice_samples="blunt, dry, one number",
    )
    handlers = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=ScriptedSource({}),
        turns=FakeTurnRunner(outputs=[]),
        now=late,
        policies=PolicyStore(pool),
    )

    await handlers.cycle(claim_for(run.id, tenant))

    finished = await pilot.get_run(run.id)
    assert finished is not None and finished.status == RUN_FINISHED
    successor = await pilot.active_run_for_community(community.id)
    assert successor is not None and successor.id != run.id, "następca istnieje"
    assert successor.starts_at == late.replace(microsecond=0)
    assert successor.ends_at == policy.valid_until, "okno następcy kończy się z polityką"
    assert successor.poll_interval_seconds == RENEWAL_CYCLE_INTERVAL_SECONDS == 600, (
        "D-030: następca czyta `new` i cykluje co 10 min, nie co godzinę jak poprzednik"
    )
    assert successor.profile_version_id == newer_profile.id != profile.id, (
        "następca pisze najnowszym zapisanym profilem"
    )
    hours = math.ceil((policy.valid_until - late).total_seconds() / 3600)
    assert successor.model_calls_budget == 2 * hours
    assert successor.restore == {"poll_interval_seconds": 300}, "wie, co przywrócić po polityce"
    assert successor.host_events and successor.host_events[0]["kind"] == "RUN_RENEWED"
    current = await store.get_community(community.id, organization_id=tenant["organization"])
    assert current is not None and current.poll_interval_seconds == 600, "poll w tempie cyklu"
    jobs = await pilot_jobs(pool, tenant["organization"])
    assert [j["idempotency_key"] for j in jobs] == [f"pilot:{successor.id}:1"]
    assert planned_at(successor, 1) == successor.starts_at + timedelta(seconds=600)

    # The human's stop is final: a STOPPED run's closing cycle renews nothing.
    stopped_at = late + timedelta(minutes=1)
    await handlers.finish(successor, now=stopped_at, status=RUN_STOPPED)
    handlers_later = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=ScriptedSource({}),
        turns=FakeTurnRunner(outputs=[]),
        now=stopped_at + timedelta(hours=1),
        policies=PolicyStore(pool),
    )
    await handlers_later.cycle(claim_for(successor.id, tenant))
    assert await pilot.active_run_for_community(community.id) is None, "po pilot-stop bez następcy"


async def test_a_fresh_post_without_comments_is_a_candidate_and_an_old_one_is_not(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """D-030: the founder's complaint was that comments land when nobody reads the post any more.
    A post the platform dates inside `FRESH_CANDIDATE_AGE` is a candidate with zero comments and
    comes BEFORE an older thread with a discussion; a post older than that still needs a comment.
    A WAIT on the fresh post comes back after `FRESH_WAIT_DELAY`, not three hours later."""
    run = await make_run(pilot, tenant, community, profile)
    # `observed()` reports one comment in the listing metrics; these two posts report none.
    no_comments = ThreadMetrics(score=Observed[int].known(1), comment_count=Observed[int].known(0))
    fresh = observed("fresh", created_at=NOW - timedelta(minutes=12)).model_copy(
        update={"metrics": no_comments}
    )
    fresh_id, _ = await store.record_thread(community=community, thread=fresh)
    stale = observed("stale", created_at=NOW - timedelta(hours=3)).model_copy(
        update={"metrics": no_comments}
    )
    await store.record_thread(community=community, thread=stale)
    discussed_id, _ = await seed_thread(store, community, "discussed")

    ids = await pilot.candidate_thread_ids(
        run,
        since=NOW - CANDIDATE_LOOKBACK,
        min_comments=CANDIDATE_MIN_COMMENTS,
        limit=CANDIDATES_PER_CYCLE,
        fresh_since=NOW - FRESH_CANDIDATE_AGE,
    )
    assert ids == (fresh_id, discussed_id), (
        "świeży post bez komentarzy pierwszy, potem wątek z komentarzem; stary bez komentarzy nie"
    )
    without_lane = await pilot.candidate_thread_ids(
        run,
        since=NOW - CANDIDATE_LOOKBACK,
        min_comments=CANDIDATE_MIN_COMMENTS,
        limit=CANDIDATES_PER_CYCLE,
    )
    assert without_lane == (discussed_id,), "bez `fresh_since` obowiązuje sama reguła komentarza"

    source = ScriptedSource({"fresh": ThreadSnapshot(thread=fresh, comments=())})
    fake = FakeTurnRunner(outputs=[output("WAIT")])
    result = await handlers_for(store, pilot, queue, tenant, source=source, turns=fake).run_once(
        run, now=NOW, origin="CYCLE"
    )

    assert result.outcome == OUTCOME_GENERATED and source.fetches == ["fresh"]
    (call,) = fake.calls
    assert '"transcript": [{' in call.user_input and call.user_input.count('"message_id"') == 1, (
        "sytuacja z samym postem, bez komentarzy"
    )
    saved = result.reaction
    assert saved is not None and saved.decision == "WAIT" and saved.comments_seen == 0
    assert saved.next_check_at == NOW + FRESH_WAIT_DELAY, "WAIT na świeżym poście wraca po 30 min"


async def test_a_cycle_hands_the_model_what_the_account_knows_and_records_what_it_chose(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """D-036: nothing is assigned to the model. The cycle supplies the community's voice card and
    what this account's moves earned here, then records the move the model chose. No requested
    move, no allowed-move table, no post kind computed by a regex."""
    await store.save_voice_card(
        community=community,
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        content={
            "avoid": ["Marketing lists."],
            "how_people_write": "Short, first person, one number.",
            "metrics": {"median": 160},
        },
        comment_count=40,
        invalid_quote_count=0,
        model="fake",
        effort="low",
        prompt_name="community_voice",
        prompt_sha256="0" * 64,
    )
    _, thread_a = await seed_thread(store, community, "130")
    _, thread_b = await seed_thread(store, community, "131")
    source = ScriptedSource(
        {
            "130": ThreadSnapshot(thread=thread_a, comments=(comment("c130-0", minutes_before=5),)),
            "131": ThreadSnapshot(thread=thread_b, comments=(comment("c131-0", minutes_before=5),)),
        }
    )
    fake = FakeTurnRunner(outputs=[output("HELP", move="IRONY"), output("HELP", move="IMAGE")])
    run = await make_run(pilot, tenant, community, profile)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    first = await handlers.run_once(run, now=NOW, origin="CYCLE")
    second = await handlers.run_once(run, now=NOW + timedelta(minutes=30), origin="CYCLE")

    assert first.outcome == OUTCOME_GENERATED and second.outcome == OUTCOME_GENERATED
    payload = json.loads(fake.calls[0].user_input)
    assert payload["community"]["voice_card"] == {
        "avoid": ["Marketing lists."],
        "how_people_write": "Short, first person, one number.",
    }, "karta głosu bez metryk i cytatów: słowa, nie tabele"
    assert payload["community"]["move_outcomes"] == [], "bez publikacji nie ma wyników"
    for assigned in ("requested_move", "move_alternatives", "post_kind"):
        assert assigned not in payload["situation"], assigned
    saved_first, saved_second = first.reaction, second.reaction
    assert saved_first is not None and saved_second is not None
    assert (saved_first.move, saved_second.move) == ("IRONY", "IMAGE"), "zapisany ruch modelu"
    assert saved_first.requested_move is None, "nic nie jest przydzielane"
    assert "ruch IRONY" in first.detail


async def test_a_text_draft_asks_the_publisher_for_a_tick_right_away(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """D-030: the publish chain ticks every ten minutes; a draft written a minute after a slot
    would wait for the next one. A GENERATED text draft enqueues one `pilot.publish` job now,
    keyed by the draft; a SILENCE enqueues nothing."""
    _, thread = await seed_thread(store, community, "120")
    _, silent = await seed_thread(store, community, "121")
    source = ScriptedSource(
        {
            "120": ThreadSnapshot(thread=thread, comments=(comment("c120-0", minutes_before=5),)),
            "121": ThreadSnapshot(thread=silent, comments=(comment("c121-0", minutes_before=5),)),
        }
    )
    fake = FakeTurnRunner(outputs=[output("HELP"), output("SILENCE")])
    run = await make_run(pilot, tenant, community, profile)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    await handlers.cycle(claim_for(run.id, tenant))
    later = handlers_for(
        store, pilot, queue, tenant, source=source, turns=fake, now=NOW + timedelta(hours=2)
    )
    await later.cycle(claim_for(run.id, tenant))

    reactions = await pilot.reactions(run.id)
    assert [r.decision for r in reactions] == ["HELP", "SILENCE"]
    async with pool.acquire() as conn:
        keys = await conn.fetch(
            "SELECT idempotency_key FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_PUBLISH,
        )
    text_draft = next(r for r in reactions if r.decision == "HELP")
    assert [k["idempotency_key"] for k in keys] == [
        f"publish:{tenant['organization']}:nudge:{text_draft.id}"
    ], "jeden tik publikacji na szkic z tekstem, żaden na SILENCE"


async def test_a_gap_between_cycles_is_written_to_the_manifest(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    run = await make_run(pilot, tenant, community, profile)
    source = ScriptedSource({})
    first = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=source,
        turns=FakeTurnRunner(outputs=[]),
        now=NOW + timedelta(minutes=30),
    )
    await first.cycle(claim_for(run.id, tenant))
    later = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=source,
        turns=FakeTurnRunner(outputs=[]),
        now=NOW + timedelta(hours=3),
    )
    await later.cycle(claim_for(run.id, tenant))
    refreshed = await pilot.get_run(run.id)
    assert refreshed is not None and len(refreshed.host_events) == 1
    assert (
        refreshed.host_events[0]["kind"] == "GAP"
        and "150 min" in refreshed.host_events[0]["detail"]
    )


async def test_an_active_pilot_pauses_the_old_automations_for_its_community(
    pool, pilot, queue, tenant, community, profile
) -> None:
    guard = PilotGuard(pilot, queue, organization_id=tenant["organization"])
    now = datetime.now(UTC)
    assert await guard.active_run(community.id, now) is None
    assert await guard.paused_community_ids(now) == set()
    run = await make_run(pilot, tenant, community, profile, starts_at=now - timedelta(minutes=1))
    active = await guard.active_run(community.id, now)
    assert run.status == RUN_ACTIVE and active is not None and active.id == run.id
    assert await guard.paused_community_ids(now) == {community.id}
    await pilot.finish_run(run.id, status="STOPPED", at=now)
    assert await guard.active_run(community.id, now) is None
    assert await guard.paused_community_ids(now) == set()


async def test_a_thread_whose_listing_reports_comments_is_a_candidate_before_its_snapshot(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    # A freshly enabled community: the poll recorded the thread and its listing metrics, the first
    # snapshot job (comments) is an hour away. The cycle fetches the comments itself.
    thread = observed("201", created_at=NOW - timedelta(hours=1))
    await store.record_thread(community=community, thread=thread)
    source = ScriptedSource(
        {"201": ThreadSnapshot(thread=thread, comments=(comment("c201-0", minutes_before=5),))}
    )
    fake = FakeTurnRunner(outputs=[output("HELP")])
    run = await make_run(pilot, tenant, community, profile)
    handlers = handlers_for(store, pilot, queue, tenant, source=source, turns=fake)

    result = await handlers.run_once(run, now=NOW, origin="SMOKE")

    assert result.outcome == OUTCOME_GENERATED and source.fetches == ["201"]
    assert result.reaction is not None and result.reaction.comments_seen == 1


# -- chain resilience: a crash inside a cycle or a lost job must not end the pilot quietly ------


async def test_a_slot_left_started_by_a_crash_still_chains_the_next_slot(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    run = await make_run(pilot, tenant, community, profile)
    crashed_at = NOW + timedelta(minutes=30)
    # The first attempt inserted its cycle row and died before scheduling anything.
    assert await pilot.start_cycle(
        run, slot=1, planned_at=planned_at(run, 1), started_at=crashed_at
    )
    assert await pilot_jobs(pool, tenant["organization"]) == []

    fake = FakeTurnRunner(outputs=[output("HELP")])
    handlers = handlers_for(
        store,
        pilot,
        queue,
        tenant,
        source=ScriptedSource({}),
        turns=fake,
        now=crashed_at + timedelta(minutes=2),
    )
    await handlers.cycle(claim_for(run.id, tenant))  # the retried attempt of the same slot

    assert len(await pilot.cycles(run.id)) == 1 and fake.calls == [], "slot nie jest powtarzany"
    assert [j["idempotency_key"] for j in await pilot_jobs(pool, tenant["organization"])] == [
        f"pilot:{run.id}:2"
    ], "ale łańcuch żyje: następny slot zlecony"


async def test_the_poll_guard_puts_a_lost_cycle_job_back_and_records_it(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    guard = PilotGuard(pilot, queue, organization_id=tenant["organization"])
    run = await make_run(pilot, tenant, community, profile)
    now = NOW + timedelta(minutes=70)  # slot 2 is current; a restart lost every queued job
    assert await pilot_jobs(pool, tenant["organization"]) == []

    assert await guard.ensure_chain(run, now) is True
    jobs = await pilot_jobs(pool, tenant["organization"])
    assert [j["idempotency_key"] for j in jobs] == [f"pilot:{run.id}:2"]
    refreshed = await pilot.get_run(run.id)
    assert [e["kind"] for e in refreshed.host_events] == ["CHAIN_REPAIRED"]

    # A pending job means the chain is alive: the guard adds nothing.
    assert await guard.ensure_chain(refreshed, now) is False
    assert len(await pilot_jobs(pool, tenant["organization"])) == 1

    # The job ran and the slot has its row, but the next slot was never scheduled: slot 3 it is.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'SUCCEEDED' WHERE job_type = $1 AND payload_ref = $2",
            JOB_PILOT_CYCLE,
            pilot_ref(run.id),
        )
    await pilot.start_cycle(run, slot=2, planned_at=planned_at(run, 2), started_at=now)
    assert await guard.ensure_chain(refreshed, now + timedelta(minutes=1)) is True
    keys = [j["idempotency_key"] for j in await pilot_jobs(pool, tenant["organization"])]
    assert sorted(keys) == sorted([f"pilot:{run.id}:2", f"pilot:{run.id}:3"])

    # Through the poll itself: slot 3 ran (its row exists) and the process died before scheduling
    # slot 4. The poll of an active pilot skips judgments and repairs the chain.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'SUCCEEDED' WHERE job_type = $1 AND payload_ref = $2",
            JOB_PILOT_CYCLE,
            pilot_ref(run.id),
        )
    poll_now = now + timedelta(minutes=25)  # slot 3
    await pilot.start_cycle(run, slot=3, planned_at=planned_at(run, 3), started_at=poll_now)
    observe = ObserveHandlers(
        store=store,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: ScriptedSource({})},
        turns=None,
        organization_id=tenant["organization"],
        clock=lambda: poll_now,
        pilot_guard=guard,
    )
    poll_claim = ClaimedJob(
        job_id=UUID(int=21),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="observe.poll_community",
        routing_key="observe",
        priority=5,
        payload_ref=community_ref(community.id),
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=UUID(int=22),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=23),
        expires_at=None,
    )
    await observe.poll_community(poll_claim)
    pending = await pilot.pending_cycle_jobs(run.id)
    assert pending == 1, "poll odtworzył łańcuch"
    keys = [j["idempotency_key"] for j in await pilot_jobs(pool, tenant["organization"])]
    assert f"pilot:{run.id}:4" in keys
    events = (await pilot.get_run(run.id)).host_events
    assert [e["kind"] for e in events] == ["CHAIN_REPAIRED"] * 3


# -- the pause holds on the two paths that bypass the poll ------------------------------------


async def test_an_active_run_pauses_only_what_its_manifest_names_so_the_voice_card_lives(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    """D-034: a run used to stop every model call in its community. From 2026-09-17 the pause is
    read from the run's manifest, and the daily voice card is not on that list: it is how the
    generator knows the register of a community it writes in, and pausing it froze the cards of
    eleven communities on the day the policy took them in. The picture and the per-thread judgment
    stay paused."""
    assert "understand.community_voice" not in PAUSED_JOB_TYPES
    assert {"judge.thread", "understand.community_picture"} == set(PAUSED_JOB_TYPES)
    run = await make_run(pilot, tenant, community, profile)
    assert run.paused_job_types == PAUSED_JOB_TYPES
    guard = PilotGuard(pilot, queue, organization_id=tenant["organization"])
    handlers = ObserveHandlers(
        store=store,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: ScriptedSource({})},
        turns=None,
        organization_id=tenant["organization"],
        clock=lambda: NOW,
        pilot_guard=guard,
    )

    assert await handlers._pilot_pauses(community, "obraz", "understand.community_picture") is True
    assert await handlers._pilot_pauses(community, "osąd", "judge.thread") is True
    assert await handlers._pilot_pauses(community, "karta", "understand.community_voice") is False

    # The manifest decides, not the module constant: a run signed BEFORE this decision still names
    # the card in its own `paused_job_types` and keeps pausing it.
    await pilot.finish_run(run.id, status=RUN_STOPPED, at=NOW + timedelta(minutes=1))
    old_style = await pilot.create_run(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        community_id=community.id,
        starts_at=NOW,
        ends_at=NOW + timedelta(days=1),
        poll_interval_seconds=3600,
        model_calls_budget=2,
        max_generations_per_thread=1,
        profile_version_id=profile.id,
        prompt_name=run.prompt_name,
        prompt_sha256=run.prompt_sha256,
        paused_job_types=(*PAUSED_JOB_TYPES, "understand.community_voice"),
        restore={},
    )
    assert "understand.community_voice" in old_style.paused_job_types
    assert await handlers._pilot_pauses(community, "karta", "understand.community_voice") is True


async def test_a_listen_only_community_is_sampled_for_comments_and_then_asks_for_its_card(
    pool, store, queue, tenant
) -> None:
    """D-033: the listing carries posts, never comments, so a community entered to learn how people
    write there had nothing to build a voice card from (measured 2026-09-17: eleven communities,
    0 comments, 0 cards). The sample reads the liveliest threads WITH their comments and only then
    asks for the card, so the card's existence follows from the data, not from a timer."""
    learn = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=1800,
        thread_analysis_enabled=False,
    )
    busy_metrics = ThreadMetrics(score=Observed[int].known(3), comment_count=Observed[int].known(9))
    quiet_metrics = ThreadMetrics(
        score=Observed[int].known(1), comment_count=Observed[int].known(0)
    )
    busy = observed("busy", created_at=NOW - timedelta(hours=2)).model_copy(
        update={"metrics": busy_metrics}
    )
    quiet = observed("quiet", created_at=NOW - timedelta(hours=1)).model_copy(
        update={"metrics": quiet_metrics}
    )
    busy_id, _ = await store.record_thread(community=learn, thread=busy)
    await store.record_thread(community=learn, thread=quiet)

    liveliest = await store.liveliest_threads(
        learn.id, organization_id=tenant["organization"], since=NOW - SAMPLE_LOOKBACK, limit=1
    )
    assert liveliest == ("busy",), "najżywszy wątek pierwszy, nie najnowszy"

    snapshots = {
        "busy": ThreadSnapshot(
            thread=busy,
            comments=(comment("cbusy1", minutes_before=30), comment("cbusy2", minutes_before=20)),
        ),
        "quiet": ThreadSnapshot(thread=quiet, comments=()),
    }
    handlers = ObserveHandlers(
        store=store,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: ScriptedSource(snapshots)},
        turns=None,
        organization_id=tenant["organization"],
        clock=lambda: NOW,
    )
    claim = ClaimedJob(
        job_id=UUID(int=71),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type="observe.sample_comments",
        routing_key="observe",
        priority=44,
        payload_ref=community_ref(learn.id),
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=UUID(int=72),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=73),
        expires_at=None,
    )

    await handlers.sample_comments(claim)

    context = await store.thread_context(busy_id, organization_id=tenant["organization"])
    assert context is not None and len(context.comments) == 2, "komentarze wątku zapisane"
    async with pool.acquire() as conn:
        keys = [
            r["idempotency_key"]
            for r in await conn.fetch(
                "SELECT idempotency_key FROM jobs WHERE organization_id = $1 AND job_type = $2",
                tenant["organization"],
                "understand.community_voice",
            )
        ]
    assert len(keys) == 1, "po zapisaniu komentarzy karta głosu jest zlecona raz"


async def test_a_community_read_only_to_learn_gets_no_judgment_and_no_snapshot_per_thread(
    pool, store, pilot, queue, tenant
) -> None:
    """0011, D-032, from an incident: enabling eleven communities to learn how people write there
    scheduled 676 judgments (a model call each) and 2 196 metric reads in minutes, because the
    judgment sweep is workspace-wide. A community with `thread_analysis_enabled = False` costs the
    listing and the daily cards, never a model call per thread."""
    learn = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=1800,
        thread_analysis_enabled=False,
    )
    assert learn.thread_analysis_enabled is False
    thread_id, _ = await seed_thread(store, learn, "learn-1", comments=0)

    unjudged = await store.unjudged_thread_ids(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        first_observed_since=NOW - timedelta(hours=48),
    )
    assert thread_id not in unjudged, "wątek społeczności czytanej do nauki nie idzie do sędziego"

    # The same community switched back on is judged again: the flag is the only difference.
    working = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=1800,
        thread_analysis_enabled=True,
    )
    assert working.id == learn.id and working.thread_analysis_enabled is True
    unjudged_now = await store.unjudged_thread_ids(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        first_observed_since=NOW - timedelta(hours=48),
    )
    assert thread_id in unjudged_now


async def test_a_queued_judgment_of_a_piloted_community_ends_without_a_model_call(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    # The first poll of a fresh community sweeps every thread into judge jobs BEFORE the run exists
    # (start incident 2026-09-06 23:22 CEST): the handler itself has to refuse the model call.
    now = datetime.now(UTC)
    thread_id, _ = await store.record_thread(
        community=community, thread=observed("501", created_at=now - timedelta(hours=1))
    )
    await store.record_comments(
        community=community,
        thread_id=thread_id,
        comments=(comment("c501-0", minutes_before=30),),
    )
    await make_run(pilot, tenant, community, profile, starts_at=now - timedelta(minutes=1))
    fake = FakeTurnRunner(outputs=[output("HELP")])
    observe = ObserveHandlers(
        store=store,
        queue=queue,
        sources={ObservationPlatform.HACKER_NEWS: ScriptedSource({})},
        turns=fake,
        organization_id=tenant["organization"],
        clock=lambda: now,
        pilot_guard=PilotGuard(pilot, queue, organization_id=tenant["organization"]),
    )
    judge_claim = ClaimedJob(
        job_id=UUID(int=31),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_JUDGE,
        routing_key="observe",
        priority=5,
        payload_ref=thread_ref(thread_id),
        dispatch_version=1,
        attempt_limit=5,
        attempt_id=UUID(int=32),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=33),
        expires_at=None,
    )
    picture_claim = ClaimedJob(
        **{
            **judge_claim.__dict__,
            "job_type": JOB_PICTURE,
            "payload_ref": community_ref(community.id),
        }
    )

    await observe.judge_thread(judge_claim)
    await observe.community_picture(picture_claim)

    assert fake.calls == [], "aktywny pilot: ani osąd, ani obraz nie wołają modelu"
    async with pool.acquire() as conn:
        judged = await conn.fetchval(
            "SELECT count(*) FROM thread_judgments WHERE thread_id = $1", thread_id
        )
    assert judged == 0


async def test_the_profile_save_sweep_skips_threads_of_the_piloted_community(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    other = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=CommunityRef(
            platform=ObservationPlatform.HACKER_NEWS,
            key="show",
            display_name="Hacker News, Show HN",
            access_mode=AccessMode.PUBLIC_API_NO_ACCOUNT,
        ),
        poll_interval_seconds=300,
    )
    now = datetime.now(UTC)
    # `observed_at` must be NOW-real: the sweep looks 48 h back from the clock, and a thread
    # first seen at the fixed test constant would fall out of the window as the days pass.
    piloted_thread = observed("301", created_at=now - timedelta(hours=1), observed_at=now)
    other_thread = observed(
        "302", created_at=now - timedelta(hours=1), community_key="show", observed_at=now
    )
    await store.record_thread(community=community, thread=piloted_thread)
    other_id, _ = await store.record_thread(community=other, thread=other_thread)
    await make_run(pilot, tenant, community, profile, starts_at=now - timedelta(minutes=1))

    observe = ObserveHandlers(
        store=store,
        queue=queue,
        sources={},
        turns=None,
        organization_id=tenant["organization"],
        clock=lambda: now,
        pilot_guard=PilotGuard(pilot, queue, organization_id=tenant["organization"]),
    )
    scheduled = await observe.sweep_judgments(workspace_id=tenant["workspace"], now=now)

    assert scheduled == 1
    async with pool.acquire() as conn:
        refs = await conn.fetch(
            "SELECT payload_ref FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_JUDGE,
        )
    assert [r["payload_ref"] for r in refs] == [thread_ref(other_id)], (
        "osąd tylko dla community bez pilota"
    )


async def test_resuming_parked_jobs_defers_the_piloted_communitys_judge_and_picture(
    pool, store, pilot, queue, tenant, community, profile
) -> None:
    other = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=CommunityRef(
            platform=ObservationPlatform.HACKER_NEWS,
            key="show",
            display_name="Hacker News, Show HN",
            access_mode=AccessMode.PUBLIC_API_NO_ACCOUNT,
        ),
        poll_interval_seconds=300,
    )
    now = datetime.now(UTC)
    piloted_id, _ = await store.record_thread(
        community=community, thread=observed("401", created_at=now - timedelta(hours=1))
    )
    other_id, _ = await store.record_thread(
        community=other,
        thread=observed("402", created_at=now - timedelta(hours=1), community_key="show"),
    )
    await make_run(pilot, tenant, community, profile, starts_at=now - timedelta(minutes=1))

    async def enqueue(job_type: str, payload_ref: str, key: str):
        return await commands.enqueue_job(
            queue,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            job_type=job_type,
            routing_key="observe",
            priority=5,
            attempt_limit=3,
            idempotency_key=key,
            payload_ref=payload_ref,
        )

    await enqueue(JOB_JUDGE, thread_ref(piloted_id), "judge:piloted")
    await enqueue(JOB_PICTURE, community_ref(community.id), "picture:piloted")
    await enqueue(JOB_JUDGE, thread_ref(other_id), "judge:other")
    await enqueue(JOB_PICTURE, community_ref(other.id), "picture:other")
    await enqueue(JOB_PILOT_CYCLE, pilot_ref(UUID(int=99)), "pilot:x:1")
    jobs = await commands.list_jobs(queue, organization_id=tenant["organization"], limit=50)

    deferred = await deferred_for_pilot(pool, jobs, organization_id=tenant["organization"], now=now)

    by_key = {}
    async with pool.acquire() as conn:
        for row in await conn.fetch(
            "SELECT id, idempotency_key FROM jobs WHERE organization_id = $1",
            tenant["organization"],
        ):
            by_key[row["idempotency_key"]] = row["id"]
    assert deferred == {by_key["judge:piloted"], by_key["picture:piloted"]}, (
        "wstrzymane tylko osąd i obraz community pilota; reszta wraca do kolejki"
    )
    assert (
        await deferred_for_pilot(
            pool, jobs, organization_id=tenant["organization"], now=now + timedelta(hours=25)
        )
        == set()
    ), "po oknie pilota nic nie jest wstrzymywane"
