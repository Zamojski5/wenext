"""The `pilot.cycle` job: one chained job per slot for the whole window; the slot length is the
run's `poll_interval_seconds` from the manifest (30 min by default, `--cycle-interval` at start).

One cycle does, in order: check the manifest (a run past `ends_at` or stopped is finished here,
its configuration restored, and no next job is scheduled); open a cycle row (a duplicate slot is a
no-op); pick at most ONE candidate (a WAIT whose deadline has passed first, then the newest thread
of the community that is either younger than `FRESH_CANDIDATE_AGE` or has at least one observed
comment, and has no reaction in this run); read it FRESH
from the platform and record the read; build the situation with the target and its parent chain
kept; refuse a duplicate version of the conversation (same situation hash) and a third generation
for one thread; check the pacing rule; RESERVE one model call in the database; call the same
`generate_reaction` the evals measure; persist the reaction with `HUMAN_REVIEW_PENDING`; close the
cycle with its outcome; schedule the next slot (with a backoff when the model or the platform was
unavailable, never a `WaitFor`: nobody is awake to press "resume" at 3 a.m.).

Pacing is a ceiling, not a target: calls allowed so far = 1 (the smoke test) + floor(remaining
budget x elapsed / window) + 1. With 24 calls over 24 h that is about one generation per hour; a
cycle with no candidate simply passes. `SILENCE` and `WAIT` are complete results and cost a call
like any other.

D-030 (2026-09-13, the founder: comments land when nobody reads the post any more): a run renewed
under a policy cycles every ten minutes and its poll of `new` follows, a post minutes old is a
candidate before anybody else has commented, and a text draft asks the publisher for a tick right
away instead of waiting for the chain's next slot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from community_observer import (
    CommunitySource,
    CredentialsMissing,
    SourceUnavailable,
    ThreadNotFound,
)
from community_os_contracts import ObservationPlatform, ReactionKind

from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob, EnqueuedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.backoff import ATTEMPT_LIMIT
from control_plane.jobs.dispatcher import Handler
from control_plane.observe import prompts
from control_plane.observe.reactions import (
    PROMPT_NAME,
    ReactionRejected,
    generate_reaction,
    reaction_input,
)
from control_plane.observe.scheduling import ROUTING_KEY, parse_ref
from control_plane.observe.store import CommunityRow, ObservationStore
from control_plane.observe.turns import ModelTurnPort, ModelUnavailable, TurnFailed

from .policy import TEXT_KINDS, PolicyStore
from .publisher import nudge_publish
from .situation import build_situation
from .store import RUN_FINISHED, PilotReaction, PilotRun, PilotStore

_LOG = logging.getLogger(__name__)

JOB_PILOT_CYCLE = "pilot.cycle"
PILOT_PRIORITY = 5
CYCLE_INTERVAL_SECONDS = 1800
#: Shortest distance between cycles a manifest may declare (one read of the community per cycle).
MIN_CYCLE_INTERVAL_SECONDS = 300
#: A cycle that starts this much later than planned is a gap the report must show.
GAP_THRESHOLD = timedelta(minutes=45)
#: How far back the candidate search looks for threads first seen in the window.
CANDIDATE_LOOKBACK = timedelta(hours=12)
CANDIDATE_MIN_COMMENTS = 1
#: D-030: a thread younger than this is a candidate with NO comment yet. Waiting for somebody else
#: to comment first costs exactly the hour in which a post is read; older threads still need one.
FRESH_CANDIDATE_AGE = timedelta(minutes=60)
CANDIDATES_PER_CYCLE = 6
#: When the model says WAIT without a machine-readable deadline, look again after this long.
WAIT_DELAY = timedelta(hours=3)
#: A WAIT on a thread still inside `FRESH_CANDIDATE_AGE` comes back sooner: three hours later the
#: post is off `new` and the wait was a silence (D-030).
FRESH_WAIT_DELAY = timedelta(minutes=30)
#: Next-cycle delays after a cycle that could not reach the model or the platform, in a row.
BACKOFF_SECONDS: tuple[int, ...] = (3600, 7200, 14400)
#: Budget per hour of a run renewed under a policy (D-029): one generation and one judgment.
RENEWAL_CALLS_PER_HOUR = 2
#: D-033: a run the SIGNATURE starts in a community that had none (as opposed to a successor of a
#: run that ended). It looks once an hour and is allowed this many model calls a day, so thirteen
#: communities cost about as much as two did before: the publication ceiling (2/h, 12/day from the
#: policy) is what bounds output anyway, and drafting far past it only burns the Codex limit.
POLICY_RUN_CYCLE_SECONDS = 3600
POLICY_RUN_CALLS_PER_DAY = 2

#: D-030: cycle length of a run renewed under a policy; the poll of `new` follows it. Ten minutes
#: is the difference between seeing a post while it is read and an hour later. A predecessor that
#: was started faster keeps its own interval.
RENEWAL_CYCLE_INTERVAL_SECONDS = 600
#: How many of the community's most upvoted comments the generator sees as register (prompt v3).
EXEMPLARS_PER_DRAFT = 6
#: Keys of the community's voice card (0005) the generator sees (D-031): what readers here punish
#: and how they write. The metrics and quoted ids stay out; the model gets words, not tables.
VOICE_CARD_KEYS: tuple[str, ...] = ("avoid", "how_people_write")
#: Job types an active pilot run holds back in its community, because its manifest owns that
#: community's model calls. D-034 (2026-09-17): the daily VOICE CARD is no longer among them. It
#: is how the generator knows the register of a community it writes in, it costs one call per
#: community per UTC day, and pausing it froze the cards of eleven communities on the very day the
#: policy took them in. The daily picture and the per-thread judgment stay paused: the picture
#: describes topics the run already reads, and a judgment per thread is the expensive one.
PAUSED_JOB_TYPES: tuple[str, ...] = (
    "judge.thread",
    "understand.community_picture",
)

OUTCOME_GENERATED = "GENERATED"
OUTCOME_NO_CANDIDATE = "NO_CANDIDATE"
OUTCOME_DUPLICATE = "DUPLICATE"
OUTCOME_BUDGET = "BUDGET_EXHAUSTED"
OUTCOME_PACING = "PACING"
OUTCOME_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
OUTCOME_PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_FAILED = "FAILED"
OUTCOME_RUN_ENDED = "RUN_ENDED"
UNAVAILABLE_OUTCOMES: frozenset[str] = frozenset(
    {OUTCOME_MODEL_UNAVAILABLE, OUTCOME_PLATFORM_UNAVAILABLE}
)

Clock = Callable[[], datetime]


def pilot_ref(run_id: UUID) -> str:
    return f"pilot/{run_id}"


def cycle_interval(run: PilotRun) -> int:
    """The slot length of THIS run, read from its manifest; the module constant is only the default
    a manifest gets when nobody asked for another one."""
    seconds = int(run.poll_interval_seconds or 0)
    return seconds if seconds > 0 else CYCLE_INTERVAL_SECONDS


def slot_of(run: PilotRun, now: datetime) -> int:
    return max(0, int((now - run.starts_at).total_seconds() // cycle_interval(run)))


def planned_at(run: PilotRun, slot: int) -> datetime:
    return run.starts_at + timedelta(seconds=slot * cycle_interval(run))


def calls_allowed_so_far(run: PilotRun, now: datetime, *, smoke_reserve: int = 1) -> int:
    """The pacing ceiling: how many calls the run may have used by `now`."""
    window = (run.ends_at - run.starts_at).total_seconds()
    elapsed = min(max((now - run.starts_at).total_seconds(), 0.0), window)
    fraction = elapsed / window if window > 0 else 1.0
    spread = max(run.model_calls_budget - smoke_reserve, 0)
    return min(run.model_calls_budget, smoke_reserve + math.floor(spread * fraction) + 1)


async def schedule_cycle(
    port: JobQueuePort, run: PilotRun, *, slot: int, delay_seconds: float
) -> EnqueuedJob:
    """The cycle for `slot`, idempotent per (run, slot): a duplicate enqueue adds nothing."""
    return await commands.enqueue_job(
        port,
        organization_id=run.organization_id,
        workspace_id=run.workspace_id,
        job_type=JOB_PILOT_CYCLE,
        routing_key=ROUTING_KEY,
        priority=PILOT_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"pilot:{run.id}:{slot}",
        payload_ref=pilot_ref(run.id),
        delay_seconds=max(0.0, delay_seconds),
    )


@dataclass(frozen=True)
class CycleResult:
    outcome: str
    detail: str
    reaction: PilotReaction | None = None


class PilotHandlers:
    def __init__(
        self,
        *,
        store: ObservationStore,
        pilot: PilotStore,
        queue: JobQueuePort,
        sources: Mapping[ObservationPlatform, CommunitySource],
        turns: ModelTurnPort | None,
        organization_id: UUID,
        clock: Clock = lambda: datetime.now(UTC),
        policies: PolicyStore | None = None,
    ) -> None:
        self._store = store
        self._pilot = pilot
        self._queue = queue
        self._sources = dict(sources)
        self._turns = turns
        self._organization_id = organization_id
        self._clock = clock
        self._policies = policies

    def as_mapping(self) -> dict[str, Handler]:
        return {JOB_PILOT_CYCLE: self.cycle}

    # -- the job --------------------------------------------------------------------------

    async def cycle(self, claim: ClaimedJob) -> None:
        run = await self._pilot.get_run(parse_ref(claim.payload_ref, "pilot"))
        if run is None:
            raise LookupError(f"run pilota {claim.payload_ref} nie istnieje")
        now = self._clock()
        slot = slot_of(run, now)
        previous = await self._pilot.last_cycle(run.id)
        if previous is not None and now - previous.started_at > (
            GAP_THRESHOLD + timedelta(seconds=cycle_interval(run))
        ):
            minutes = int((now - previous.started_at).total_seconds() // 60)
            await self._pilot.append_host_event(
                run.id,
                at=now,
                kind="GAP",
                detail=f"{minutes} min od poprzedniego cyklu (slot {previous.slot} → {slot})",
            )
        if not run.active_at(now):
            # `finished` is the run THIS cycle closed by the clock; a run a human stopped earlier
            # comes back None here and is never renewed (the stop is theirs, not the clock's).
            finished = await self.finish(run, now=now, status=RUN_FINISHED)
            cycle = await self._pilot.start_cycle(
                run, slot=slot, planned_at=planned_at(run, slot), started_at=now
            )
            if cycle is not None:
                await self._pilot.finish_cycle(
                    cycle.id,
                    outcome=OUTCOME_RUN_ENDED,
                    detail="okno pilota zamknięte; konfiguracja przywrócona, bez kolejnego cyklu",
                    finished_at=self._clock(),
                    model_calls_used_after=run.model_calls_used,
                )
            if finished is not None and run.stopped_at is None:
                try:
                    await self._renew_under_policy(run, now)
                except Exception:  # the old run is closed either way; a failed renewal is logged
                    _LOG.exception("pilot %s: przedłużenie z polityki padło", run.id)
            return
        cycle = await self._pilot.start_cycle(
            run, slot=slot, planned_at=planned_at(run, slot), started_at=now
        )
        if cycle is None:
            # The slot ran (or a crashed process left it STARTED). The generation is not
            # repeated, but the chain must not die here: the next slot is scheduled idempotently.
            _LOG.info(
                "pilot %s: slot %d już istnieje, duplikat joba; zlecam następny slot", run.id, slot
            )
            await self._schedule_next(run, slot=slot, now=now, outcome="DUPLICATE_SLOT")
            return
        try:
            result = await self.run_once(run, now=now, origin="CYCLE")
        except Exception as exc:  # the cycle row must close whatever happened
            _LOG.exception("pilot %s: cykl %d padł", run.id, slot)
            result = CycleResult(OUTCOME_FAILED, f"{type(exc).__name__}: {exc}")
        refreshed = await self._pilot.get_run(run.id) or run
        await self._pilot.finish_cycle(
            cycle.id,
            outcome=result.outcome,
            detail=result.detail,
            finished_at=self._clock(),
            model_calls_used_after=refreshed.model_calls_used,
        )
        _LOG.info("pilot %s: cykl %d → %s (%s)", run.id, slot, result.outcome, result.detail)
        await self._schedule_next(refreshed, slot=slot, now=self._clock(), outcome=result.outcome)
        reaction = result.reaction
        if reaction is not None and reaction.decision in TEXT_KINDS:
            # D-030: the draft is minutes old; the publisher's next slot may be ten minutes away.
            # One extra tick now judges and posts it while the post is still read. Failing to
            # enqueue it must not fail the cycle: the chain's slot catches the draft anyway.
            try:
                await nudge_publish(
                    self._queue,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    key=str(reaction.id),
                )
            except Exception:
                _LOG.exception("pilot %s: nie udało się zlecić tiku publikacji", run.id)

    async def _schedule_next(
        self, run: PilotRun, *, slot: int, now: datetime, outcome: str
    ) -> None:
        if not run.active_at(now):
            return
        delay = (planned_at(run, slot + 1) - now).total_seconds()
        if outcome in UNAVAILABLE_OUTCOMES:
            streak = await self._unavailable_streak(run.id)
            delay = max(delay, BACKOFF_SECONDS[min(streak, len(BACKOFF_SECONDS)) - 1])
        interval = cycle_interval(run)
        next_slot = slot + 1 + int(max(0.0, delay - interval) // interval)
        if planned_at(run, next_slot) >= run.ends_at:
            # The last slot inside the window still runs, to finish the run and restore config.
            next_slot = slot_of(run, run.ends_at) + 1
            delay = (run.ends_at - now).total_seconds() + 5
        await schedule_cycle(self._queue, run, slot=next_slot, delay_seconds=delay)

    async def _unavailable_streak(self, run_id: UUID) -> int:
        cycles = await self._pilot.cycles(run_id)
        streak = 0
        for cycle in reversed(cycles):
            if cycle.outcome in UNAVAILABLE_OUTCOMES:
                streak += 1
            elif cycle.outcome != "STARTED":
                break
        return max(1, streak)

    async def finish(self, run: PilotRun, *, now: datetime, status: str) -> PilotRun | None:
        """Close the run and put the community's schedule back the way the manifest found it."""
        finished = await self._pilot.finish_run(run.id, status=status, at=now)
        if finished is None:
            return None
        before = run.restore.get("poll_interval_seconds")
        if isinstance(before, int):
            await self._store.set_poll_interval(run.community_id, before)
        _LOG.info("pilot %s: run %s, poll community przywrócony do %s s", run.id, status, before)
        return finished

    async def _renew_under_policy(self, run: PilotRun, now: datetime) -> PilotRun | None:
        """D-029: a run whose window closed NATURALLY while a signed policy still covers its
        community gets a successor right away, so one signature keeps the pilot reading and
        drafting for the whole policy window without a `pilot-start` per day. The successor keeps
        the profile version and the schedule to restore; it cycles every
        `RENEWAL_CYCLE_INTERVAL_SECONDS` unless the predecessor was faster (D-030); its window
        ends with the policy; its budget is `RENEWAL_CALLS_PER_HOUR` per hour (one generation
        plus one judgment). `pilot-stop` (STOPPED) never renews: that is the human's stop, not the
        clock's."""
        if self._policies is None:
            return None
        policy = await self._policies.active(organization_id=self._organization_id, now=now)
        if policy is None or run.community_id not in policy.community_ids:
            return None
        if await self._pilot.active_run_for_community(run.community_id) is not None:
            return None
        interval = min(cycle_interval(run), RENEWAL_CYCLE_INTERVAL_SECONDS)
        ends_at = min(policy.valid_until, now + timedelta(days=7))
        if ends_at - now < timedelta(seconds=interval):
            return None
        hours = max(1, math.ceil((ends_at - now).total_seconds() / 3600))
        prompt = prompts.get(PROMPT_NAME)
        # The newest profile the founder saved (`pilot-profile-save`), so a voice fixed today is
        # what tomorrow's run writes with; the predecessor's version is the fallback.
        latest_profile = await self._store.latest_profile(
            organization_id=run.organization_id, workspace_id=run.workspace_id
        )
        profile_version_id = latest_profile.id if latest_profile else run.profile_version_id
        successor = await self._pilot.create_run(
            organization_id=run.organization_id,
            workspace_id=run.workspace_id,
            community_id=run.community_id,
            starts_at=now.replace(microsecond=0),
            ends_at=ends_at,
            poll_interval_seconds=interval,
            model_calls_budget=hours * RENEWAL_CALLS_PER_HOUR,
            max_generations_per_thread=run.max_generations_per_thread,
            profile_version_id=profile_version_id,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
            paused_job_types=run.paused_job_types,
            restore=dict(run.restore or {}),
        )
        await self._store.set_poll_interval(run.community_id, interval)
        await self._pilot.append_host_event(
            successor.id,
            at=now,
            kind="RUN_RENEWED",
            detail=(
                f"następca runu {run.id} z polityki {policy.id}; okno do {ends_at.isoformat()}, "
                f"budżet {successor.model_calls_budget}"
            ),
        )
        delay = (planned_at(successor, 1) - now).total_seconds()
        await schedule_cycle(self._queue, successor, slot=1, delay_seconds=delay)
        _LOG.info(
            "pilot %s: run zakończony, polityka %s aktywna: następca %s do %s",
            run.id,
            policy.id,
            successor.id,
            ends_at.isoformat(),
        )
        return successor

    # -- one attempt at a reaction ----------------------------------------------------------

    async def run_once(self, run: PilotRun, *, now: datetime, origin: str) -> CycleResult:
        community = await self._store.get_community(
            run.community_id, organization_id=self._organization_id
        )
        if community is None:
            raise LookupError(f"community {run.community_id} runu pilota nie istnieje")
        source = self._sources.get(community.platform)
        if source is None:
            return CycleResult(
                OUTCOME_PLATFORM_UNAVAILABLE,
                f"brak adaptera dla platformy {community.platform.value} w tym procesie",
            )
        if self._turns is None:
            return CycleResult(OUTCOME_MODEL_UNAVAILABLE, "model nie jest skonfigurowany")
        profile = await self._pilot.profile_by_id(run.profile_version_id)
        if profile is None:
            raise LookupError(f"wersja profilu {run.profile_version_id} z manifestu nie istnieje")

        due = await self._pilot.due_wait_thread_ids(run.id, now=now)
        fresh = await self._pilot.candidate_thread_ids(
            run,
            since=now - CANDIDATE_LOOKBACK,
            min_comments=CANDIDATE_MIN_COMMENTS,
            limit=CANDIDATES_PER_CYCLE,
            fresh_since=now - FRESH_CANDIDATE_AGE,
        )
        skipped: list[str] = []
        for thread_id in (*due, *fresh):
            thread = await self._store.get_thread(thread_id, organization_id=self._organization_id)
            if thread is None:
                continue
            generations = await self._pilot.generations_for_thread(run.id, thread_id)
            if generations >= run.max_generations_per_thread:
                skipped.append(f"{thread.external_id}: limit {generations} generacji")
                continue
            try:
                snapshot = await source.fetch_thread(community.community_key, thread.external_id)
            except ThreadNotFound as exc:
                skipped.append(f"{thread.external_id}: zniknął ({exc})")
                continue
            except CredentialsMissing as exc:
                return CycleResult(OUTCOME_PLATFORM_UNAVAILABLE, f"brak sesji platformy: {exc}")
            except SourceUnavailable as exc:
                return CycleResult(OUTCOME_PLATFORM_UNAVAILABLE, str(exc)[:300])
            read_at = now
            await self._store.record_thread(community=community, thread=snapshot.thread)
            await self._store.record_comments(
                community=community, thread_id=thread_id, comments=snapshot.comments
            )
            recent = await self._pilot.recent_drafts(run.id)
            exemplars = await self._store.top_comments(
                community.id, organization_id=self._organization_id, limit=EXEMPLARS_PER_DRAFT
            )
            # D-031: the learning loop's input side. The digest of the conversation decides the
            # tie-break, so it is computed first, on the conversation alone.
            voice_card = await self._store.latest_voice_card(
                community.id, organization_id=self._organization_id
            )
            voice = (
                {k: voice_card.content[k] for k in VOICE_CARD_KEYS if voice_card.content.get(k)}
                if voice_card is not None
                else None
            )
            move_outcomes = await self._pilot.move_outcomes(
                community.id, organization_id=self._organization_id
            )
            situation, digest, complete = build_situation(
                situation_id=f"pilot-{run.id}-{thread.external_id}",
                platform=community.platform.value,
                community_display_name=community.display_name,
                language="en",
                thread=snapshot.thread,
                comments=snapshot.comments,
                target_id=snapshot.thread.external_id,
                read_at=read_at,
                comments_truncated=snapshot.comments_truncated,
                recent_own_drafts=recent,
                community_exemplars=exemplars,
                community_voice_card=voice or None,
                community_move_outcomes=move_outcomes,
            )
            if await self._pilot.situation_exists(run.id, digest):
                skipped.append(f"{thread.external_id}: ta sama wersja rozmowy")
                continue

            current = await self._pilot.get_run(run.id) or run
            if current.model_calls_used >= current.model_calls_budget:
                return CycleResult(
                    OUTCOME_BUDGET, f"budżet {run.model_calls_budget} wywołań wyczerpany"
                )
            if current.model_calls_used >= calls_allowed_so_far(current, now):
                return CycleResult(
                    OUTCOME_PACING,
                    f"tempo: zużyto {current.model_calls_used}, dozwolone dotąd "
                    f"{calls_allowed_so_far(current, now)}; kandydat {thread.external_id} czeka",
                )
            reserved = await self._pilot.reserve_model_call(run.id)
            if reserved is None:
                return CycleResult(
                    OUTCOME_BUDGET, f"budżet {run.model_calls_budget} wywołań wyczerpany"
                )
            input_payload = reaction_input(situation, profile=profile, now=read_at)
            input_sha256 = hashlib.sha256(
                json.dumps(input_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            try:
                result = await generate_reaction(
                    self._turns, situation=situation, profile=profile, now=read_at
                )
            except ModelUnavailable as exc:
                # The provider refused before doing any work (usage limit, outage): the
                # reservation goes back, or the provider's limit would eat the pilot's budget
                # (measured 2026-09-07: r/b2b_sales slot 34 paid 1 of 12 for nothing).
                await self._pilot.release_model_call(run.id)
                return CycleResult(OUTCOME_MODEL_UNAVAILABLE, str(exc)[:300])
            except ReactionRejected as exc:
                return CycleResult(OUTCOME_REJECTED, f"{thread.external_id}: {exc}"[:300])
            except TurnFailed as exc:
                return CycleResult(OUTCOME_FAILED, f"{thread.external_id}: {exc}"[:300])
            reaction = result.reaction
            is_wait = reaction.decision is ReactionKind.WAIT
            is_fresh = snapshot.thread.created_at >= now - FRESH_CANDIDATE_AGE
            wait_delay = FRESH_WAIT_DELAY if is_fresh else WAIT_DELAY
            saved = await self._pilot.save_reaction(
                run,
                thread_id=thread_id,
                origin=origin,
                target_message_external_id=reaction.target_message_id,
                decision=reaction.decision.value,
                draft=reaction.draft,
                contribution=reaction.contribution,
                brief_rationale=reaction.brief_rationale,
                confidence=reaction.confidence,
                model=result.model,
                effort=result.effort,
                profile_version_id=run.profile_version_id,
                prompt_name=result.prompt_name,
                prompt_sha256=result.prompt_sha256,
                input_sha256=input_sha256,
                situation_sha256=digest,
                read_at=read_at,
                context_complete=complete,
                comments_seen=len(snapshot.comments),
                wait_reason=reaction.brief_rationale if is_wait else None,
                next_check_at=(read_at + wait_delay) if is_wait else None,
                move=reaction.move,
            )
            if saved is None:
                return CycleResult(
                    OUTCOME_DUPLICATE,
                    f"{thread.external_id}: ta sama wersja rozmowy zapisana równolegle",
                )
            return CycleResult(
                OUTCOME_GENERATED,
                f"{thread.external_id}: {reaction.decision.value}"
                f" (ruch {reaction.move})"
                f"{'' if complete else ' (kontekst niepełny)'}",
                reaction=saved,
            )
        detail = (
            "; ".join(skipped)
            if skipped
            else "brak świeżych wątków ani wątków z komentarzem w oknie"
        )
        if skipped and all("ta sama wersja" in s for s in skipped):
            return CycleResult(OUTCOME_DUPLICATE, detail[:600])
        return CycleResult(OUTCOME_NO_CANDIDATE, detail[:600])


class PilotGuard:
    """What the observe module asks about the pilot (`observe.handlers.PilotGuardPort`): which
    communities are paused, and whether the cycle chain of a run is still alive. The poll runs
    at the cycle interval anyway, so it is the natural place to notice a chain that a crash or a
    Docker outage cut, and to put the next slot back into the queue (`host_events` gets
    CHAIN_REPAIRED)."""

    def __init__(self, pilot: PilotStore, queue: JobQueuePort, *, organization_id: UUID) -> None:
        self._pilot = pilot
        self._queue = queue
        self._organization_id = organization_id

    async def active_run(self, community_id: UUID, now: datetime) -> PilotRun | None:
        run = await self._pilot.active_run_for_community(community_id)
        if run is None or run.organization_id != self._organization_id:
            return None
        return run if run.active_at(now) else None

    async def paused_community_ids(self, now: datetime) -> set[UUID]:
        return await self._pilot.paused_community_ids(
            organization_id=self._organization_id, now=now
        )

    async def ensure_chain(self, run: PilotRun, now: datetime) -> bool:
        """True when a missing `pilot.cycle` job was put back. A pending job means all is well."""
        if await self._pilot.pending_cycle_jobs(run.id) > 0:
            return False
        slot = slot_of(run, now)
        if await self._pilot.has_cycle(run.id, slot):
            slot += 1
        # A slot whose job already exists in a terminal state (it failed before writing its cycle
        # row) keeps its idempotency key, so scheduling it again is a no-op: move one slot on.
        for _ in range(3):
            target = planned_at(run, slot)
            if target >= run.ends_at:
                slot = slot_of(run, run.ends_at) + 1
                target = run.ends_at + timedelta(seconds=5)
            await schedule_cycle(
                self._queue, run, slot=slot, delay_seconds=(target - now).total_seconds()
            )
            if await self._pilot.pending_cycle_jobs(run.id) > 0:
                break
            slot += 1
        await self._pilot.append_host_event(
            run.id,
            at=now,
            kind="CHAIN_REPAIRED",
            detail=f"brak oczekującego joba pilot.cycle; zlecony slot {slot}",
        )
        return True


__all__ = [
    "BACKOFF_SECONDS",
    "CYCLE_INTERVAL_SECONDS",
    "FRESH_CANDIDATE_AGE",
    "FRESH_WAIT_DELAY",
    "JOB_PILOT_CYCLE",
    "MIN_CYCLE_INTERVAL_SECONDS",
    "PAUSED_JOB_TYPES",
    "RENEWAL_CYCLE_INTERVAL_SECONDS",
    "VOICE_CARD_KEYS",
    "CommunityRow",
    "CycleResult",
    "PilotGuard",
    "PilotHandlers",
    "calls_allowed_so_far",
    "cycle_interval",
    "pilot_ref",
    "planned_at",
    "schedule_cycle",
    "slot_of",
]
