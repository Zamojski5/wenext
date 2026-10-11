"""The four job handlers of Outcome A — the first producers of real work this queue has had.

`docs/OUTCOMES.md` §5 row 5. D-013 said F1 had no handlers because nothing produced real
content; these do, and they produce it from a real community.

    observe.poll_community      list what is new, record it, schedule the rest, chain the next poll
    observe.snapshot_thread     re-read one thread (comments + metrics) at 1 h / 6 h / 24 h
    understand.community_picture  once a day, draw the picture of the window (model turn)
    judge.thread                ENGAGE / IGNORE / WAIT for one thread (model turn)

HOW A HANDLER SAYS "NOT NOW" VERSUS "FAILED", because the queue treats the two differently:

* `WaitFor(state, reason)` — the job CANNOT run until a human acts: no adapter for the platform,
  no credentials, no user profile, no logged-in model. The dispatcher parks the job in the named
  `WAITING_*` state with its lease released (L2147) and the operator sees it on the queue screen.
  `POST /api/jobs/waiting/resume` brings parked jobs back once the cause is fixed.
* any other exception — the attempt FAILED (terminal, `HANDLER_ERROR`). Transient platform
  errors land here on purpose: the poll chain survives because the next poll is scheduled BEFORE
  the exception propagates, and a failed judgment is retried by the daily sweep, so nothing
  needs a retry policy of its own and nothing retries an external write.

WHAT THE HANDLERS NEVER DO: write to a platform, open a browser, or call the model without
compiling its whole input first (`context.py`). The model sees data; it does not fetch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from community_observer import (
    MAX_LISTING,
    CommunitySource,
    CredentialsMissing,
    SourceUnavailable,
    ThreadMetrics,
    ThreadNotFound,
)
from community_os_contracts import JobWaitingState, ObservationPlatform, Observed, UnknownReason

from control_plane.domain.models import ClaimedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.jobs.dispatcher import Handler, WaitFor

from . import prompts
from .context import (
    JUDGE_OUTPUT_SCHEMA,
    PICTURE_OUTPUT_SCHEMA,
    judge_input,
    judgment_from_output,
    picture_input,
    validate_picture,
)
from .scheduling import (
    JOB_JUDGE,
    JOB_PICTURE,
    JOB_POLL,
    JOB_SAMPLE,
    JOB_SNAPSHOT,
    JOB_VOICE,
    JUDGE_SWEEP_HOURS,
    parse_ref,
    picture_is_due,
    schedule_judgment,
    schedule_picture,
    schedule_poll,
    schedule_sample,
    schedule_snapshots,
    schedule_voice,
)
from .store import CommunityRow, ObservationStore
from .turns import ModelTurnPort, ModelUnavailable
from .voice import (
    VOICE_MIN_COMMENTS,
    VOICE_OUTPUT_SCHEMA,
    VOICE_WINDOW,
    validate_voice,
    voice_input,
    voice_metrics,
    voice_sample,
)

_LOG = logging.getLogger(__name__)

#: How far back the daily picture looks when there is no previous picture to continue from.
PICTURE_WINDOW = timedelta(hours=24)
#: D-033: how many threads of a listen-only community are read WITH their comments each day, and
#: how far back the pick looks. Enough for the voice card's `VOICE_MIN_COMMENTS`, far from the
#: three reads per thread that flooded the queue on 2026-09-16.
SAMPLE_THREADS = 6
SAMPLE_LOOKBACK = timedelta(hours=36)
#: A previous picture older than this does not extend the window — the gap is reported instead.
PICTURE_MAX_CONTINUATION = timedelta(hours=48)

Clock = Callable[[], datetime]


class PilotGuardPort(Protocol):
    """What the observe handlers ask the 24 h pilot (`control_plane.pilot.handlers.PilotGuard`)."""

    async def active_run(self, community_id: UUID, now: datetime) -> Any | None: ...

    async def paused_community_ids(self, now: datetime) -> set[UUID]: ...

    async def ensure_chain(self, run: Any, now: datetime) -> bool: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ObserveHandlers:
    def __init__(
        self,
        *,
        store: ObservationStore,
        queue: JobQueuePort,
        sources: Mapping[ObservationPlatform, CommunitySource],
        turns: ModelTurnPort | None,
        organization_id: UUID,
        clock: Clock = _utc_now,
        pilot_guard: PilotGuardPort | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._sources = dict(sources)
        self._turns = turns
        self._organization_id = organization_id
        self._clock = clock
        #: Pilot 24 h (docs/START-PILOTA-24H.md): while a community has an ACTIVE pilot run, the
        #: poll keeps reading and snapshotting but schedules no judgments, picture or voice card
        #: for it: the pilot's cycles are the only model calls, and its manifest holds the budget.
        #: The same guard repairs the pilot's cycle chain when a crash left no job in the queue.
        self._pilot_guard = pilot_guard

    def as_mapping(self) -> dict[str, Handler]:
        return {
            JOB_POLL: self.poll_community,
            JOB_SNAPSHOT: self.snapshot_thread,
            JOB_PICTURE: self.community_picture,
            JOB_JUDGE: self.judge_thread,
            JOB_SAMPLE: self.sample_comments,
            JOB_VOICE: self.community_voice,
        }

    # -- helpers ------------------------------------------------------------------

    async def _community(self, community_id: UUID) -> CommunityRow:
        community = await self._store.get_community(
            community_id, organization_id=self._organization_id
        )
        if community is None:
            raise LookupError(
                f"community {community_id} nie istnieje w organizacji {self._organization_id}"
            )
        return community

    def _source(self, community: CommunityRow) -> CommunitySource:
        source = self._sources.get(community.platform)
        if source is None:
            raise WaitFor(
                JobWaitingState.WAITING_CAPABILITY,
                f"brak adaptera dla platformy {community.platform.value} w tym procesie",
            )
        return source

    def _model(self) -> ModelTurnPort:
        if self._turns is None:
            raise WaitFor(
                JobWaitingState.WAITING_CAPABILITY,
                "model nie jest skonfigurowany (WENEXT_CODEX_HOME) — obraz community i osądy "
                "czekają, obserwacja idzie dalej",
            )
        return self._turns

    async def _schedule_next_poll(self, community: CommunityRow, now: datetime) -> None:
        await schedule_poll(
            self._queue,
            organization_id=community.organization_id,
            workspace_id=community.workspace_id,
            community_id=community.id,
            interval_seconds=community.poll_interval_seconds,
            now=now,
            immediately=False,
        )

    async def sweep_judgments(self, *, workspace_id: UUID, now: datetime) -> int:
        """Enqueue a judgment for every young, unjudged thread — once per day per thread.

        Public because saving a user profile calls it too: the first profile is what unblocks
        every judgment, and waiting for the next poll to notice would be a silent delay.
        """
        profile = await self._store.latest_profile(
            organization_id=self._organization_id, workspace_id=workspace_id
        )
        if profile is None:
            _LOG.info("brak profilu użytkownika — osądy nie są jeszcze zlecane")
            return 0
        since = now - timedelta(hours=JUDGE_SWEEP_HOURS)
        excluded: set[UUID] = set()
        if self._pilot_guard is not None:
            excluded = await self._pilot_guard.paused_community_ids(now)
        thread_ids = await self._store.unjudged_thread_ids(
            organization_id=self._organization_id,
            workspace_id=workspace_id,
            first_observed_since=since,
            exclude_community_ids=sorted(excluded),
        )
        for thread_id in thread_ids:
            await schedule_judgment(
                self._queue,
                organization_id=self._organization_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                now=now,
            )
        return len(thread_ids)

    # -- handlers -----------------------------------------------------------------

    async def _pilot_pauses(self, community: CommunityRow, what: str, job_type: str) -> bool:
        """Whether an ACTIVE pilot run holds this kind of work back in this community.

        The run's manifest says which job types it pauses, and that list is now read here instead
        of assumed: a run used to stop every model call in its community, which from 2026-09-17
        also froze the daily voice card of the eleven communities the policy had just taken in
        (D-034). Jobs queued before the run started (the first poll of a fresh community sweeps
        every thread) or resumed by hand still land here and end without a model call.
        """
        if self._pilot_guard is None:
            return False
        run = await self._pilot_guard.active_run(community.id, self._clock())
        if run is None or job_type not in run.paused_job_types:
            return False
        _LOG.info("%s %s: aktywny pilot %s, model nie jest wołany", what, community.ref, run.id)
        return True

    async def poll_community(self, claim: ClaimedJob) -> None:
        community = await self._community(parse_ref(claim.payload_ref, "community"))
        if not community.observe_enabled:
            _LOG.info("community %s ma wyłączoną obserwację — poll bez skutku", community.ref)
            return
        source = self._source(community)
        now = self._clock()
        try:
            threads = await source.list_recent_threads(
                community.community_key, since=community.watermark_created_at, limit=MAX_LISTING
            )
        except CredentialsMissing as exc:
            # The parked job IS the continuation of the chain: no next poll is scheduled here.
            raise WaitFor(JobWaitingState.WAITING_CAPABILITY, str(exc)) from exc
        except SourceUnavailable:
            # Transient. Keep the chain alive, then let this attempt fail visibly.
            await self._schedule_next_poll(community, now)
            raise

        new_threads = 0
        watermark = community.watermark_created_at
        for thread in threads:
            thread_id, created = await self._store.record_thread(community=community, thread=thread)
            if created:
                new_threads += 1
                if community.thread_analysis_enabled:
                    # 0011 (D-032): a community entered to learn how people write there costs the
                    # listing and the daily cards. Three metric reads per new thread turned one
                    # poll of eleven such communities into 2 196 queued reads, with the pilot's
                    # own cycles behind them.
                    await schedule_snapshots(
                        self._queue,
                        organization_id=community.organization_id,
                        workspace_id=community.workspace_id,
                        thread_id=thread_id,
                    )
            if watermark is None or thread.created_at > watermark:
                watermark = thread.created_at
        await self._store.set_polled(
            community.id, organization_id=self._organization_id, polled_at=now, watermark=watermark
        )
        pilot_run = (
            await self._pilot_guard.active_run(community.id, now)
            if self._pilot_guard is not None
            else None
        )
        if pilot_run is not None:
            _LOG.info(
                "poll %s: aktywny pilot, osądy / obraz / karta głosu wstrzymane (manifest runu)",
                community.ref,
            )
            if await self._pilot_guard.ensure_chain(pilot_run, now):
                _LOG.warning("poll %s: łańcuch cykli pilota odtworzony", community.ref)
            await self._schedule_next_poll(community, now)
            return
        judged = await self.sweep_judgments(workspace_id=community.workspace_id, now=now)
        latest_picture = await self._store.latest_picture(
            community.id, organization_id=self._organization_id
        )
        if picture_is_due(now, latest_picture.window_end if latest_picture else None):
            await schedule_picture(
                self._queue,
                organization_id=community.organization_id,
                workspace_id=community.workspace_id,
                community_id=community.id,
                now=now,
            )
        latest_voice = await self._store.latest_voice_card(
            community.id, organization_id=self._organization_id
        )
        if picture_is_due(now, latest_voice.window_end if latest_voice else None):
            if community.thread_analysis_enabled:
                await schedule_voice(
                    self._queue,
                    organization_id=community.organization_id,
                    workspace_id=community.workspace_id,
                    community_id=community.id,
                    now=now,
                )
            else:
                # D-033: a community we only listen to has no comments in the database, because
                # the listing carries posts only. Read a few of its liveliest threads first; that
                # handler asks for the card once the comments are in.
                await schedule_sample(
                    self._queue,
                    organization_id=community.organization_id,
                    workspace_id=community.workspace_id,
                    community_id=community.id,
                    now=now,
                )
        await self._schedule_next_poll(community, now)
        _LOG.info(
            "poll %s: %d wątków w odpowiedzi, %d nowych, %d osądów zleconych, watermark %s",
            community.ref,
            len(threads),
            new_threads,
            judged,
            watermark.isoformat() if watermark else "brak",
        )

    async def sample_comments(self, claim: ClaimedJob) -> None:
        """D-033: read the comments of a few threads of a community we only listen to.

        The daily picture is built from posts and works without this; the voice card is built from
        comments and cannot exist without it (measured 2026-09-17: eleven communities, 0 comments,
        0 cards). The read is bounded on purpose: `SAMPLE_THREADS` threads, the liveliest of the
        last day and a half, so one community costs a handful of reads instead of three per thread.
        No model call happens here. The voice card is asked for when the comments are in: that is
        causality, not a timer.
        """
        community_id = parse_ref(claim.payload_ref, "community")
        community = await self._store.get_community(
            community_id, organization_id=self._organization_id
        )
        if community is None:
            raise LookupError(f"community {community_id} nie istnieje")
        source = self._sources.get(community.platform)
        if source is None:
            raise LookupError(f"brak adaptera dla {community.platform.value}")
        now = self._clock()
        candidates = await self._store.liveliest_threads(
            community.id,
            organization_id=self._organization_id,
            since=now - SAMPLE_LOOKBACK,
            limit=SAMPLE_THREADS,
        )
        read = 0
        comments = 0
        for external_id in candidates:
            try:
                snapshot = await source.fetch_thread(community.community_key, external_id)
            except ThreadNotFound:
                continue
            except CredentialsMissing as exc:
                raise WaitFor(JobWaitingState.WAITING_CAPABILITY, str(exc)) from exc
            thread_id, _ = await self._store.record_thread(
                community=community, thread=snapshot.thread
            )
            await self._store.record_comments(
                community=community, thread_id=thread_id, comments=snapshot.comments
            )
            read += 1
            comments += len(snapshot.comments)
        _LOG.info(
            "próbka %s: %d wątków przeczytanych, %d komentarzy zapisanych",
            community.ref,
            read,
            comments,
        )
        if comments:
            await schedule_voice(
                self._queue,
                organization_id=community.organization_id,
                workspace_id=community.workspace_id,
                community_id=community.id,
                now=now,
            )

    async def snapshot_thread(self, claim: ClaimedJob) -> None:
        thread_id = parse_ref(claim.payload_ref, "thread")
        thread = await self._store.get_thread(thread_id, organization_id=self._organization_id)
        if thread is None:
            raise LookupError(
                f"wątek {thread_id} nie istnieje w organizacji {self._organization_id}"
            )
        community = await self._community(thread.community_id)
        source = self._source(community)
        now = self._clock()
        try:
            snapshot = await source.fetch_thread(community.community_key, thread.external_id)
        except CredentialsMissing as exc:
            raise WaitFor(JobWaitingState.WAITING_CAPABILITY, str(exc)) from exc
        except ThreadNotFound as exc:
            # The thread is gone — deleted or removed. That is a fact about the thread and it is
            # recorded as a snapshot with UNKNOWN metrics, not as a missing data point.
            _LOG.info("wątek %s zniknął z platformy: %s", thread.external_id, exc)
            gone = Observed[int].unknown(
                UnknownReason.NOT_EXPOSED_BY_PLATFORM, "wątek niedostępny na platformie"
            )
            await self._store.append_snapshot(
                community=community,
                thread_id=thread_id,
                observed_at=now,
                metrics=ThreadMetrics(score=gone, comment_count=gone),
            )
            return
        await self._store.record_thread(community=community, thread=snapshot.thread)
        written = await self._store.record_comments(
            community=community, thread_id=thread_id, comments=snapshot.comments
        )
        _LOG.info(
            "snapshot %s: %d komentarzy zapisanych%s",
            snapshot.thread.ref,
            written,
            " (drzewo obcięte przez platformę)" if snapshot.comments_truncated else "",
        )

    async def community_picture(self, claim: ClaimedJob) -> None:
        community = await self._community(parse_ref(claim.payload_ref, "community"))
        if await self._pilot_pauses(community, "obraz", JOB_PICTURE):
            return
        model = self._model()
        now = self._clock()
        previous = await self._store.latest_picture(
            community.id, organization_id=self._organization_id
        )
        if previous is not None and now - previous.window_end <= PICTURE_MAX_CONTINUATION:
            window_start = previous.window_end
        else:
            window_start = now - PICTURE_WINDOW
        threads = await self._store.threads_in_window(
            community=community, window_start=window_start, window_end=now
        )
        if not threads:
            _LOG.info(
                "obraz %s: zero wątków w oknie %s → %s, model nie jest wołany",
                community.ref,
                window_start.isoformat(),
                now.isoformat(),
            )
            return
        prompt = prompts.get("community_picture")
        payload = picture_input(
            community_display_name=community.display_name,
            platform=community.platform.value,
            window_start=window_start,
            window_end=now,
            threads=threads,
            previous_picture=previous.content if previous else None,
        )
        try:
            outcome = await model.run(
                role=prompt.role,
                instructions=prompt.text,
                user_input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                output_schema=PICTURE_OUTPUT_SCHEMA,
            )
        except ModelUnavailable as exc:
            raise WaitFor(JobWaitingState.WAITING_AUTH, str(exc)) from exc
        validated = validate_picture(outcome.structured_output, known_refs=[t.ref for t in threads])
        saved = await self._store.save_picture(
            community=community,
            window_start=window_start,
            window_end=now,
            content=validated.content,
            thread_count=len(threads),
            invalid_ref_count=validated.invalid_ref_count,
            model=outcome.model,
            effort=outcome.effort,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
        )
        _LOG.info(
            "obraz %s v%d: %d wątków, %d odwołań nieistniejących, %d obserwacji odrzuconych, "
            "%d długich pauz przepisanych",
            community.ref,
            saved.version,
            len(threads),
            validated.invalid_ref_count,
            validated.dropped_items,
            validated.long_dash_count,
        )

    async def community_voice(self, claim: ClaimedJob) -> None:
        """Etap 3 M0: measure how people write in this community over the last week, have the
        model describe it in plain Polish with citations, store the card as the next version.

        Unlike the picture the window does not continue from the previous card: a voice is a
        property of the last seven days, whichever day the card is drawn on."""
        community = await self._community(parse_ref(claim.payload_ref, "community"))
        if await self._pilot_pauses(community, "karta głosu", JOB_VOICE):
            return
        model = self._model()
        now = self._clock()
        window_start = now - VOICE_WINDOW
        comments = await self._store.comments_in_window(
            community=community, window_start=window_start, window_end=now
        )
        if len(comments) < VOICE_MIN_COMMENTS:
            _LOG.info(
                "karta głosu %s: %d komentarzy w oknie %s → %s, poniżej progu %d, model nie jest "
                "wołany",
                community.ref,
                len(comments),
                window_start.isoformat(),
                now.isoformat(),
                VOICE_MIN_COMMENTS,
            )
            return
        previous = await self._store.latest_voice_card(
            community.id, organization_id=self._organization_id
        )
        prompt = prompts.get("community_voice")
        metrics = voice_metrics(comments)
        sample = voice_sample(comments)
        payload = voice_input(
            community_display_name=community.display_name,
            platform=community.platform.value,
            window_start=window_start,
            window_end=now,
            metrics=metrics,
            sample=sample,
            previous_card=previous.content if previous else None,
        )
        try:
            outcome = await model.run(
                role=prompt.role,
                instructions=prompt.text,
                user_input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                output_schema=VOICE_OUTPUT_SCHEMA,
            )
        except ModelUnavailable as exc:
            raise WaitFor(JobWaitingState.WAITING_AUTH, str(exc)) from exc
        validated = validate_voice(
            outcome.structured_output, known_ids=[c.external_id for c in sample]
        )
        content = {**validated.content, "metrics": metrics, "sample_size": len(sample)}
        saved = await self._store.save_voice_card(
            community=community,
            window_start=window_start,
            window_end=now,
            content=content,
            comment_count=len(comments),
            invalid_quote_count=validated.invalid_quote_count,
            model=outcome.model,
            effort=outcome.effort,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
        )
        _LOG.info(
            "karta głosu %s v%d: %d komentarzy, %d w próbce, %d cytatów spoza próbki, "
            "%d pozycji odrzuconych, %d długich pauz przepisanych",
            community.ref,
            saved.version,
            len(comments),
            len(sample),
            validated.invalid_quote_count,
            validated.dropped_items,
            validated.long_dash_count,
        )

    async def judge_thread(self, claim: ClaimedJob) -> None:
        thread_id = parse_ref(claim.payload_ref, "thread")
        thread = await self._store.get_thread(thread_id, organization_id=self._organization_id)
        if thread is None:
            raise LookupError(
                f"wątek {thread_id} nie istnieje w organizacji {self._organization_id}"
            )
        community = await self._community(thread.community_id)
        if await self._pilot_pauses(community, "osąd", JOB_JUDGE):
            return
        profile = await self._store.latest_profile(
            organization_id=self._organization_id, workspace_id=community.workspace_id
        )
        if profile is None:
            raise WaitFor(
                JobWaitingState.WAITING_CAPABILITY,
                "brak profilu użytkownika — bez niego nie ma kogo reprezentować; "
                "uzupełnij profil (POST /api/user-profile)",
            )
        model = self._model()
        context = await self._store.thread_context(thread_id, organization_id=self._organization_id)
        if context is None:
            raise LookupError(f"wątek {thread_id} bez kontekstu — zniknął między odczytami")
        picture = await self._store.latest_picture(
            community.id, organization_id=self._organization_id
        )
        now = self._clock()
        prompt = prompts.get("judge_thread")
        payload = judge_input(
            profile=profile.as_mapping(),
            community_display_name=community.display_name,
            platform=community.platform.value,
            picture=picture.content if picture else None,
            thread=context,
            now=now,
        )
        try:
            outcome = await model.run(
                role=prompt.role,
                instructions=prompt.text,
                user_input=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                output_schema=JUDGE_OUTPUT_SCHEMA,
            )
        except ModelUnavailable as exc:
            raise WaitFor(JobWaitingState.WAITING_AUTH, str(exc)) from exc
        record = judgment_from_output(
            outcome.structured_output,
            thread_ref=context.ref,
            user_profile_version=profile.version,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
            model=outcome.model,
            effort=outcome.effort,
            created_at=now,
        )
        judgment_id = await self._store.save_judgment(
            community=community,
            thread_id=thread_id,
            record=record,
            user_profile_version_id=profile.id,
            community_picture_id=picture.id if picture else None,
        )
        _LOG.info("osąd %s: %s (%s)", context.ref, record.decision.value, judgment_id)
