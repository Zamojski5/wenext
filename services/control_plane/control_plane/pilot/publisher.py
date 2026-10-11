"""The publication worker (R4 minimal): approved pilot drafts onto Reddit, one at a time, through
`ExecutionIntent` and `SideEffectAdapter.perform`, with the platform's answer read back as evidence.

One tick (`run_once`) publishes AT MOST one request, in the order of the consents, and only when
every precondition holds at that moment:

  1. publishing is not stopped (`publish-stop`, or `pilot-stop` which STOPPED the run); a run
     that merely ENDED leaves its approved drafts publishable while their consent lives;
  2. the pace: at most `max_publications_per_hour` and `max_publications_per_day` attempts that
     reached the platform (SUBMIT_STARTED, CONFIRMED, UNKNOWN all count; a refusal before the port
     does not), read from `execution_attempts`, never from memory;
  3. the capsule is INTERACTIVE + display_only with the human steer closed and Chrome alive (D-026);
  4. the capsule's Reddit session is logged in AS the account the consent names; another account,
     or none, is BLOCKED and stops the whole queue (the session belongs to someone else);
  5. the thread still exists, is neither locked nor archived, the target comment (if any) is still
     there, and no comment by our account is already on the thread (in the database or on the page);
  6. `ApprovalStore.issue_intent` accepts the revision (a SUPERSEDED or expired consent is refused
     there and the request is SKIPPED with the reason), `authorize_and_consume_intent` burns it.

Only then does `SideEffectAdapter.perform` run, with the consumed intent in its signature. Its three
ways of ending map to three request statuses: CONFIRMED with evidence = PUBLISHED; the port raised
`PublicationBlocked` = BLOCKED and the queue stops; anything else after SUBMIT_STARTED = UNKNOWN,
which is never retried: the next ticks RECONCILE it by reading the thread and looking for a comment
by our account with the consented text, and only a read-back turns it into PUBLISHED.

Every tick also observes the published comments (still there? score? replies?), because "does it
resonate" is the one question the founder asked, and records the thread's comments through the
observation store so the people's replies (parent = our comment) land in `observed_comments`.

AND, since migration 0012, every tick asks the OTHER question, the one that broke on 17.09: is
the comment visible to anybody but us. Both reads above go through the capsule's logged-in
Chrome, so they are the author looking at their own comment, and Reddit shows an author their own
removed comments. An hour after two comments were taken down by AutoModerator our read-back still
said `still_present = true` (STAN-PRAC §8.22). So a PUBLISHED request now also carries
`public_visibility`, computed from two sources that do not share that blind spot: an ANONYMOUS
read of the thread, and removal reports from the account's inbox. Neither transport is required
to exist: without one, the request stays UNVERIFIED, which is what we actually know about it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from community_observer import (
    CredentialsMissing,
    # One definition of "the platform says our comment is gone", and it lives with the adapter
    # that parses it out of the inbox (D-005: a second copy is a second answer).
    RemovalReport,
    SourceUnavailable,
    ThreadNotFound,
)
from community_observer.reddit_payload import parse_thread_pair

from control_plane.approval.store import (
    ApprovalStore,
    ApprovalStoreError,
    RevisionNotGrantable,
)
from control_plane.domain import commands
from control_plane.domain.models import ClaimedJob, EnqueuedJob
from control_plane.domain.ports import JobQueuePort
from control_plane.execution.adapter import (
    PublicationBlocked,
    PublicationRefused,
    SideEffectAdapter,
    SideEffectEvidence,
    UnknownSideEffectOutcome,
    WritingSideEffectPort,
)
from control_plane.execution.store import ExecutionStore
from control_plane.observe.store import ObservationStore
from control_plane.presence.budget import effective_limits

from .policy import PolicyAutoApprover
from .publication import (
    EVIDENCE_ACTUATOR,
    EVIDENCE_RECONCILIATION,
    KIND_OWN_POST,
    REMOVAL_FROM_INBOX,
    REMOVAL_SCOPE_COMMENT,
    REMOVAL_SCOPE_PARENT_POST,
    REQUEST_BLOCKED,
    REQUEST_PUBLISHED,
    REQUEST_PUBLISHING,
    REQUEST_QUEUED,
    REQUEST_SKIPPED,
    REQUEST_STOPPED,
    REQUEST_UNKNOWN,
    VIEWER_AUTHOR,
    VISIBILITY_REMOVED,
    AnonymousLook,
    PublicationRequest,
    PublicationStore,
    PublishTarget,
    normalised_draft,
    split_media,
    split_own_post,
)
from .store import RUN_STOPPED, PilotStore

_LOG = logging.getLogger(__name__)

JOB_PUBLISH = "pilot.publish"
PUBLISH_PRIORITY = 5
PUBLISH_INTERVAL_SECONDS = 600
INTENT_TTL = timedelta(minutes=10)
#: An UNKNOWN attempt is reconciled by reading for this long; afterwards it stays UNKNOWN for
#: a human to look at.
RECONCILE_WINDOW = timedelta(hours=24)
#: Published comments are observed (presence, score, replies) for this long.
# The experience sweep may call a publication "quiet after 24h" only after an
# AUTHOR reaction read made at or after that threshold. Keep the cheap reader
# alive for two more hours so the hourly/ten-minute jobs cannot miss the edge.
OBSERVE_WINDOW = timedelta(hours=26)
#: How long a published request's last observation stands before it is read again.
OBSERVE_INTERVAL = timedelta(minutes=55)
#: A young X post is read more often (27.09): a reply under it now wakes the agent
#: (`presence/collector.py::_x_replies`), and a reply noticed an hour late is a conversation that
#: has moved on. The publisher ticks every 10 minutes, so this reads it about every 20.
YOUNG_X_POST = timedelta(hours=6)
YOUNG_X_OBSERVE_INTERVAL = timedelta(minutes=15)
#: A request stuck in PUBLISHING longer than this is a dead tick: treated as UNKNOWN.
STALE_PUBLISHING = timedelta(minutes=15)
ATTEMPT_LIMIT = 3

#: How long a public-visibility verdict stands before it is checked again. An hour, the same
#: order as the author-side observation, because a removal can happen an hour after publication
#: (STAN-PRAC §8.22: both removals landed roughly an hour in).
ANONYMOUS_CHECK_INTERVAL = timedelta(hours=1)
FRESH_VISIBILITY_WINDOW = timedelta(hours=1)
#: And at most this many anonymous reads in ONE tick, over all requests. Both bounds are needed:
#: the interval alone would let a backlog of thirty-eight published requests turn a single tick
#: into thirty-eight reads at Reddit's door, which is the shape of the 403s measured on 17.09
#: (D-035). One per tick, oldest verdict first.
MAX_ANONYMOUS_READS_PER_TICK = 1

OUTCOME_PUBLISHED = "PUBLISHED"
OUTCOME_NO_REQUEST = "NO_REQUEST"
OUTCOME_STOPPED = "STOPPED"
OUTCOME_PACED = "PACED"
OUTCOME_SKIPPED = "SKIPPED"
OUTCOME_CAPSULE = "CAPSULE_NOT_READY"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_UNKNOWN = "UNKNOWN"
OUTCOME_UNAVAILABLE = "PLATFORM_UNAVAILABLE"

Clock = Callable[[], datetime]
CapsuleStateReader = Callable[[], Awaitable[Mapping[str, Any]]]
PortFactory = Callable[[str], WritingSideEffectPort]


@runtime_checkable
class AnonymousThreadReader(Protocol):
    """Reads a thread WITHOUT the account's session: the only read that answers "can anyone else
    see this". Raises (`SourceUnavailable`, `ThreadNotFound`) when the read did not happen; it
    never answers "not there" for a 403, a 429 or an unreachable capsule, because that answer
    would turn Reddit's rate limiter into a verdict that our comment was removed."""

    async def read_thread_payload_anonymously(
        self, community_key: str, thread_external_id: str
    ) -> Any: ...


@runtime_checkable
class RemovalReportReader(Protocol):
    """The account's inbox, as removal reports. The second, independent source of the same truth:
    on 17.09 this was the ONLY place that knew both comments had been taken down."""

    async def read_removal_reports(self) -> tuple[RemovalReport, ...]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_x_target(target_external_id: str) -> bool:
    """An X publication: `x:<status id>` for a reply, `x:@<handle>` for an own post (0034)."""
    return str(target_external_id).startswith("x:")


def visibility_check_key(request: PublicationRequest, now: datetime) -> tuple[bool, bool, datetime]:
    """Newly published work first, then fair rotation of older unknown verdicts."""
    fresh = (
        request.public_visibility_checked_at is None
        and request.published_at is not None
        and timedelta(0) <= now - request.published_at <= FRESH_VISIBILITY_WINDOW
    )
    return (
        not fresh,
        request.public_visibility_checked_at is not None,
        request.published_at
        if fresh
        else request.public_visibility_checked_at or request.last_checked_at or request.queued_at,
    )


@dataclass(frozen=True)
class PublishOutcome:
    outcome: str
    detail: str
    request_id: UUID | None = None
    evidence: SideEffectEvidence | None = None


@dataclass(frozen=True)
class ThreadFacts:
    """What the thread's JSON says right now, for the preconditions and the read-back."""

    locked: bool
    archived: bool
    post_fullname: str
    comments: tuple[Mapping[str, Any], ...]  # flattened comment `data` objects
    #: Sam post, czyli to, co przy własnym wątku JEST publikacją, a nie jej tłem (0023). Puste
    #: dla wątku, którego nie dało się przeczytać; domyślna wartość, żeby nie ruszać wywołań.
    post: Mapping[str, Any] = field(default_factory=dict)

    def by_author(self, account: str) -> tuple[Mapping[str, Any], ...]:
        wanted = account.lower()
        return tuple(c for c in self.comments if str(c.get("author", "")).lower() == wanted)

    def by_fullname(self, fullname: str) -> Mapping[str, Any] | None:
        for c in self.comments:
            if c.get("name") == fullname:
                return c
        return None

    def replies_to(self, fullname: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(c for c in self.comments if c.get("parent_id") == fullname)


def thread_facts(payload: Any) -> ThreadFacts:
    """Flatten Reddit's `[post listing, comment listing]` pair. Tolerant of `more` stubs and of
    missing fields: a comment without an author is a comment by nobody, not a crash."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise SourceUnavailable("JSON wątku nie jest parą [post, komentarze]")
    post_children = payload[0].get("data", {}).get("children", [])
    post = post_children[0].get("data", {}) if post_children else {}
    comments: list[Mapping[str, Any]] = []

    def walk(children: Any) -> None:
        if not isinstance(children, list):
            return
        for child in children:
            if not isinstance(child, Mapping) or child.get("kind") != "t1":
                continue
            data = child.get("data", {})
            if isinstance(data, Mapping):
                comments.append(data)
                replies = data.get("replies")
                if isinstance(replies, Mapping):
                    walk(replies.get("data", {}).get("children", []))

    walk(payload[1].get("data", {}).get("children", []))
    return ThreadFacts(
        locked=bool(post.get("locked")),
        archived=bool(post.get("archived")),
        post_fullname=str(post.get("name") or ""),
        comments=tuple(comments),
        post=post if isinstance(post, Mapping) else {},
    )


#: "The same text, whatever the spacing" lives in ONE place (P6). The publisher asked the
#: question first, for matching our own comment on a thread; `tools/registry.py` asks it now too,
#: to stop two collaborating sessions proposing one reply twice. Two spellings of this rule would
#: agree until somebody edited one.
_normalised = normalised_draft

#: Zdania, którymi Reddit mówi „nie ma już POSTA”, a nie „nie ma twojego komentarza”. Bierzemy
#: je z tekstu wyrenderowanej strony permalinku, bo dokładnie tam czyta je człowiek. Apostrof
#: bywa prosty albo typograficzny (U+2019) zależnie od szablonu, więc porównujemy bez niego.
#: Lista jest zamknięta i krótka świadomie: dopasowanie „czegoś o usunięciu” złapałoby również
#: komunikat o NASZYM komentarzu i zamieniło rozróżnienie w jego przeciwieństwo.
_ZNIKNAL_POST = (
    "sorry, this post was removed by reddits filters",
    "sorry, this post has been removed by the moderators",
    "this post was removed by reddits filters",
    "post removed by moderator",
)


def _without_apostrophes(text: str) -> str:
    # U+2019 (typographic apostrophe) and the ASCII one: Reddit renders either in its notices.
    return text.replace("\u2019", "").replace("'", "").lower()


def _removal_scope(page: Any, comment: Mapping[str, Any], *, removed: bool) -> str | None:
    """Czy zniknęła NASZA odpowiedź, czy cały wątek pod nią. `None`, gdy nie da się rozstrzygnąć.

    `None` jest tu pełnoprawną odpowiedzią, nie brakiem: zakres zgadnięty byłby dokładnie tym
    błędem, który ta funkcja zamyka. Zwracamy zakres WYŁĄCZNIE przy usunięciu, bo przy widocznej
    odpowiedzi nie ma czego zakresować, a CHECK z 0032 i tak by go odrzucił.
    """
    if not removed:
        return None
    strona = _without_apostrophes(str(page.get("text") or "") if isinstance(page, Mapping) else "")
    if any(marker in strona for marker in _ZNIKNAL_POST):
        return REMOVAL_SCOPE_PARENT_POST
    # Post stoi, a naszego komentarza nie widać: usunięto komentarz. Czytamy to z tej samej
    # strony, na której widać „Comment removed by moderator”, więc dowód jest jeden.
    return REMOVAL_SCOPE_COMMENT


async def schedule_publish(
    port: JobQueuePort, *, organization_id: UUID, workspace_id: UUID, delay_seconds: float
) -> None:
    """One `pilot.publish` tick per interval, idempotent per interval slot."""
    slot = (
        int((_utc_now() + timedelta(seconds=delay_seconds)).timestamp()) // PUBLISH_INTERVAL_SECONDS
    )
    await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_PUBLISH,
        routing_key="observe",
        priority=PUBLISH_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"publish:{organization_id}:{slot}",
        payload_ref=f"publish/{organization_id}",
        delay_seconds=max(0.0, delay_seconds),
    )


async def nudge_publish(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    key: str,
    delay_seconds: float = 0.0,
) -> EnqueuedJob:
    """D-030: one extra `pilot.publish` tick, keyed by what asks for it (a draft's id, a policy's
    id), so a draft minutes old is judged and posted at once instead of waiting for the chain's
    next slot. `schedule_publish` cannot do this: its key is the ten-minute slot, and the slot's
    job has usually run already, so an enqueue "in 15 s" adds nothing (measured 2026-09-13 17:42
    CEST after `policy-sign`). The chain's slot keys are untouched: this tick schedules the next
    slot like any tick does (a no-op when the chain already has it), and the dispatcher runs jobs
    one at a time, so two ticks never publish side by side."""
    return await commands.enqueue_job(
        port,
        organization_id=organization_id,
        workspace_id=workspace_id,
        job_type=JOB_PUBLISH,
        routing_key="observe",
        priority=PUBLISH_PRIORITY,
        attempt_limit=ATTEMPT_LIMIT,
        idempotency_key=f"publish:{organization_id}:nudge:{key}",
        payload_ref=f"publish/{organization_id}",
        delay_seconds=max(0.0, delay_seconds),
    )


async def nudge_issued_revision(
    port: JobQueuePort,
    *,
    organization_id: UUID,
    workspace_id: UUID,
    revision_id: UUID,
) -> bool:
    """D-030 for a request that has just been issued for ONE revision: tick the publisher now.

    THE KEY FORMAT LIVES HERE, once. Two places issue requests on a person's or a policy's
    consent, `drafts.publish` (the agent, through the delegation) and the approval route (the
    person, on the dashboard), and both want the same thing: the draft is minutes old and must
    not wait up to ten minutes for the chain's next slot. Keyed by the revision, so asking twice
    for the same one enqueues one job.

    A FAILED NUDGE IS NOT THE CALLER'S FAILURE: the request is issued either way and the chain
    still picks it up at its next slot. Returns whether the tick was enqueued, so a caller that
    cares can say so, and logs the reason when it was not.
    """
    try:
        await nudge_publish(
            port,
            organization_id=organization_id,
            workspace_id=workspace_id,
            key=f"issued:{revision_id}",
        )
    except Exception:
        _LOG.exception("nie udało się zlecić tiku publikacji dla rewizji %s", revision_id)
        return False
    return True


@dataclass(frozen=True)
class Governance:
    """What lets this request out, and how fast. The ONE place the two provenances differ.

    Everything after this in `_publish_request` is the same code for a pilot request and for a
    project's: the same identity check, the same thread read, the same intent, the same read-back.
    The difference is which signature authorises the publication and whose pace bounds it.
    """

    #: Why publication is stopped, or `None`. A sentence for a person, not a code.
    stopped: str | None
    per_hour: int
    per_day: int
    #: Whose pace this is, for the queue's detail line: "polityka <id>", "delegacja <id>".
    whose: str
    #: The pilot run, when there is one. Kept because `_block_everything` writes a host event on
    #: it; a project request has none and writes none.
    run: Any | None = None


class DelegationGate(Protocol):
    """The slice of `presence.PresenceStore` the publisher needs. A Protocol, not an import, for
    the reason `AnonymousThreadReader` above is one: the publisher must not depend on P4 being
    wired in order to publish a pilot's queue."""

    async def delegation_for(self, *, organization_id: UUID, project_id: UUID) -> Any | None: ...


class PublishHandlers:
    """The `pilot.publish` handler and the one-tick logic behind it."""

    def __init__(
        self,
        *,
        store: ObservationStore,
        pilot: PilotStore,
        publication: PublicationStore,
        approvals: ApprovalStore,
        execution: ExecutionStore,
        queue: JobQueuePort,
        source: Any | None,
        capsule_state: CapsuleStateReader | None,
        port_factory: PortFactory | None,
        own_post_port_factory: PortFactory | None = None,
        organization_id: UUID,
        clock: Clock = _utc_now,
        autoapprover: PolicyAutoApprover | None = None,
        anonymous: AnonymousThreadReader | None = None,
        removals: RemovalReportReader | None = None,
        delegations: DelegationGate | None = None,
        x_source: Any | None = None,
        x_port_factory: PortFactory | None = None,
        x_own_post_port_factory: PortFactory | None = None,
        x_capsule_state: CapsuleStateReader | None = None,
    ) -> None:
        # Both default to None, and None means the worker cannot answer "is this visible to
        # anyone else". Every request then keeps `public_visibility = UNVERIFIED`. That is the
        # point: an unconfigured transport must produce "not measured", never a success
        # (docs/KONWENCJE.md, D-007).
        self._anonymous = anonymous
        self._removals = removals
        # None means this worker publishes the pilot's queue only. A project's request then stops
        # with a reason instead of going out on a signature nobody read (see `_governance`).
        self._delegations = delegations
        self._store = store
        self._autoapprover = autoapprover
        self._pilot = pilot
        self._publication = publication
        self._approvals = approvals
        self._execution = execution
        self._queue = queue
        self._source = source
        self._capsule_state = capsule_state
        # D-051: the X account's own capsule, when it has one. None = X writes through the
        # capsule above, so its state is the one to check.
        self._x_capsule_state = x_capsule_state
        self._port_factory = port_factory
        # 0023: własny post idzie innym adresem aktuatora, więc ma własny wytwórca portu. Brak
        # tego wytwórcy nie blokuje odpowiedzi: wniosek OWN_POST zostaje wtedy w kolejce z
        # powodem, a REPLY jedzie dalej. Jeden wytwórca dla obu rodzajów znaczyłby, że wyłączenie
        # własnych postów wyłącza też odpowiadanie.
        self._own_post_port_factory = own_post_port_factory
        # X (26.09): its own reader (`community_observer.x_browser.XBrowserSource` through the
        # capsule) and its own two ports. None means this worker cannot publish or check X, and
        # an X request then waits with that reason; Reddit is not affected either way.
        self._x_source = x_source
        self._x_port_factory = x_port_factory
        self._x_own_post_port_factory = x_own_post_port_factory
        self._organization_id = organization_id
        self._clock = clock

    def as_mapping(self) -> dict[str, Callable[[ClaimedJob], Awaitable[None]]]:
        return {JOB_PUBLISH: self.publish}

    # -- the job ---------------------------------------------------------------------------------

    async def publish(self, claim: ClaimedJob) -> None:
        now = self._clock()
        try:
            result = await self.run_once(now)
            _LOG.info("pilot.publish: %s (%s)", result.outcome, result.detail[:200])
        finally:
            # THE CHAIN SURVIVES A FAILED TICK (27.09). Each tick schedules the next; a tick that
            # raised used to schedule nothing, and nothing else restarts the chain, so every
            # queued request then waited for ever. The exception still ends THIS job as a
            # handler error, where it is seen; the next tick comes as it would have.
            if await self._anything_left():
                await schedule_publish(
                    self._queue,
                    organization_id=self._organization_id,
                    workspace_id=claim.workspace_id or self._organization_id,
                    delay_seconds=PUBLISH_INTERVAL_SECONDS,
                )

    async def _anything_left(self) -> bool:
        """Whether another tick has work: something waiting, in flight, unknown, or published
        recently enough to still observe (through the 24h checkpoint). Without the age bound,
        the chain would tick forever over old publications."""
        now = self._clock()
        if self._autoapprover is not None and await self._autoapprover.active(now) is not None:
            return True
        requests = await self._publication.requests(organization_id=self._organization_id)
        for r in requests:
            if r.status in (REQUEST_QUEUED, REQUEST_PUBLISHING, REQUEST_UNKNOWN):
                return True
            if (
                r.status == REQUEST_PUBLISHED
                and r.published_at is not None
                and now - r.published_at < OBSERVE_WINDOW
            ):
                return True
        return False

    # -- one tick ------------------------------------------------------------------------------

    async def run_once(self, now: datetime) -> PublishOutcome:
        await self.reconcile_only(now)
        if self._autoapprover is not None:
            # D-029: the signed policy judges pending drafts and queues the ones that pass. A
            # failure here must not stop publication of what is already approved.
            try:
                for event in await self._autoapprover.tick(now):
                    _LOG.info("pilot.publish/policy: %s", event)
            except Exception:
                _LOG.exception("pilot.publish: autozatwierdzanie z polityki padło")
        request = await self._publication.next_queued(organization_id=self._organization_id)
        if request is None:
            return PublishOutcome(OUTCOME_NO_REQUEST, "kolejka publikacji pusta")
        return await self._publish_request(request, now)

    async def reconcile_only(self, now: datetime) -> None:
        """The reading half of a tick and nothing else: stale PUBLISHING to UNKNOWN, UNKNOWN
        reconciled against the thread, PUBLISHED observed. Never issues an intent."""
        await self._recover_stale_publishing(now)
        await self._reconcile_unknown(now)
        await self._observe_published(now)
        await self._collect_removal_reports(now)
        await self._check_public_visibility(now)

    async def thread_has_our_comment(self, request: PublicationRequest) -> tuple[bool, int] | None:
        """`(found, comments_seen)` from a live read of the request's thread, or `None` when the
        thread could not be read. The operator's requeue refuses without this read."""
        if self._source is None:
            return None
        facts, _target, _community, _payload = await self._facts_for(request)
        if facts is None:
            return None
        revision = await self._approvals.fetch_revision(
            request.proposal_revision_id, organization_id=self._organization_id
        )
        if revision is None:
            return None
        account = await self._publication.account_handle(revision.hash_input.account)
        if account is None:
            return None
        return bool(facts.by_author(account)), len(facts.comments)

    async def _recover_stale_publishing(self, now: datetime) -> None:
        """A request left in PUBLISHING is a tick that died between the intent and the outcome.
        After STALE_PUBLISHING it becomes UNKNOWN (the write may have happened) and is reconciled by
        reading, like every other unknown; it is never picked up for a second write."""
        for request in await self._publication.requests(organization_id=self._organization_id):
            if request.status != REQUEST_PUBLISHING or request.last_checked_at is None:
                continue
            if now - request.last_checked_at < STALE_PUBLISHING:
                continue
            await self._publication.set_status(
                request.id,
                status=REQUEST_UNKNOWN,
                detail="tik przerwany między intentem a wynikiem; skutek nieznany, rekoncyliacja",
            )

    async def _governance(self, request: PublicationRequest, now: datetime) -> Governance:
        """Which signature authorises this request, and at what pace.

        PILOT: the run must not be stopped by hand, and D-030 gives the pace to the signed policy
        when one is active (the policy is what the founder signed; the run's manifest pace is for
        runs without one).

        PROJECT: the delegation the request CITES must still be the project's live one. Three
        things stop it, and they are different sentences on purpose, because a person reading the
        queue needs to know which of them to undo: revoked (the row is gone, and a newer signature
        does not inherit what the old one authorised), paused, and no delegation at all.
        """
        if not request.from_a_project:
            run = await self._pilot.get_run(request.run_id) if request.run_id else None
            if run is None or run.publishing_stopped_at is not None or run.status == RUN_STOPPED:
                return Governance(
                    stopped="publikacja zatrzymana (publish-stop albo pilot-stop)",
                    per_hour=0,
                    per_day=0,
                    whose="",
                )
            policy = (
                await self._autoapprover.active(now) if self._autoapprover is not None else None
            )
            return Governance(
                stopped=None,
                per_hour=policy.max_per_hour if policy else run.max_publications_per_hour,
                per_day=policy.max_per_day if policy else run.max_publications_per_day,
                whose=f"polityka {policy.id}" if policy else "",
                run=run,
            )

        assert request.project_id is not None  # the CHECK in 0017 guarantees it
        if self._delegations is None:
            # Not a crash and not a publication: a worker wired without P4 cannot read the
            # signature this request stands on, and publishing without reading it is the one
            # outcome that must not happen.
            return Governance(
                stopped="wniosek projektu, a ten proces nie czyta delegacji",
                per_hour=0,
                per_day=0,
                whose="",
            )
        delegation = await self._delegations.delegation_for(
            organization_id=self._organization_id, project_id=request.project_id
        )
        if delegation is None:
            return Governance(
                stopped="delegacja projektu została odwołana", per_hour=0, per_day=0, whose=""
            )
        if delegation.id != request.delegation_id:
            return Governance(
                stopped=(
                    f"wniosek powołuje się na delegację {request.delegation_id}, a projekt ma "
                    f"teraz {delegation.id}: nowy podpis nie dziedziczy starych zgód"
                ),
                per_hour=0,
                per_day=0,
                whose="",
            )
        if not delegation.live:
            return Governance(
                stopped=f"delegacja projektu jest {delegation.state}",
                per_hour=0,
                per_day=0,
                whose="",
            )
        # THE SAME READING THE DECISION USED. `effective_limits` is imported rather than
        # re-derived here: the queue and the decision disagreeing about what a delegation
        # permits is the whole failure this single function exists to prevent, and a delegation
        # that named no limit gets the conservative default in both places, not "no limit" in
        # one of them.
        limits = effective_limits(delegation)
        return Governance(
            stopped=None,
            per_hour=limits.publications_per_hour,
            per_day=limits.publications_per_day,
            whose=f"delegacja {delegation.id}",
        )

    async def _publish_request(self, request: PublicationRequest, now: datetime) -> PublishOutcome:
        rule = await self._governance(request, now)
        # The gate is the human's stop, not the pilot's clock: a run that ENDED (FINISHED) leaves
        # its approved drafts publishable while their consent lives (the revision's TTL), a run
        # STOPPED by hand or flagged by publish-stop does not. The 24 h pilot measured drafting;
        # publication of what it produced may legitimately happen the day after. For a project the
        # same sentence is about the delegation: revoked or paused stops it, signed lets it out.
        if rule.stopped is not None:
            await self._publication.set_status(
                request.id, status=REQUEST_STOPPED, detail=rule.stopped
            )
            return PublishOutcome(OUTCOME_STOPPED, rule.stopped, request.id)
        run = rule.run

        hour = await self._publication.attempts_since(
            organization_id=self._organization_id, since=now - timedelta(hours=1)
        )
        day = await self._publication.attempts_since(
            organization_id=self._organization_id, since=now - timedelta(hours=24)
        )
        per_hour, per_day = rule.per_hour, rule.per_day
        if hour >= per_hour or day >= per_day:
            detail = (
                f"tempo: {hour}/{per_hour} w ostatniej godzinie, {day}/{per_day} w ostatniej dobie"
                + (f" ({rule.whose})" if rule.whose else "")
            )
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_PACED, detail, request.id)

        on_x = is_x_target(request.target_external_id)
        writer_missing = (
            (self._x_port_factory is None or self._x_source is None)
            if on_x
            else (self._port_factory is None or self._source is None)
        )
        if writer_missing:
            detail = (
                "brak aktuatora (WENEXT_BROWSER_WRITER_TOKEN / czytnik kapsuły nieskonfigurowane)"
                + (" dla X" if on_x else "")
            )
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_CAPSULE, detail, request.id)

        if on_x:
            # The X capsule boots HEADLESS after a restart and only a read switches it; without
            # this the gate below would park an X publication until somebody happened to read X.
            await self._x_source.prepare()
        capsule = await self._read_capsule_state(on_x=on_x)
        if capsule is None:
            detail = "kapsuła nie odpowiada na /state"
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_CAPSULE, detail, request.id)
        if (
            capsule.get("mode") != "INTERACTIVE"
            or not capsule.get("display_only")
            or capsule.get("human_steer_open")
            or not capsule.get("alive")
        ):
            detail = (
                f"kapsuła nie jest w INTERACTIVE display_only z zamkniętym sterem: mode="
                f"{capsule.get('mode')}, display_only={capsule.get('display_only')}, "
                f"steer={capsule.get('human_steer_open')}, alive={capsule.get('alive')}"
            )
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_CAPSULE, detail, request.id)

        revision = await self._approvals.fetch_revision(
            request.proposal_revision_id, organization_id=self._organization_id
        )
        if revision is None:
            await self._publication.set_status(
                request.id, status=REQUEST_SKIPPED, detail="rewizja zgody nie istnieje"
            )
            return PublishOutcome(OUTCOME_SKIPPED, "brak rewizji", request.id)
        content = revision.hash_input
        account_id = content.account
        account = await self._publication.account_handle(account_id)
        if account is None:
            await self._publication.set_status(
                request.id, status=REQUEST_SKIPPED, detail=f"konto {account_id} nie istnieje"
            )
            return PublishOutcome(OUTCOME_SKIPPED, "brak konta", request.id)

        if on_x:
            return await self._publish_x(
                request,
                revision=revision,
                content=content,
                account_id=account_id,
                account=account,
                run=run,
                now=now,
            )

        # 4. The session must be OUR account. Anything else stops the queue.
        try:
            session = await self._source.session_state()
        except (SourceUnavailable, CredentialsMissing) as exc:
            detail = f"sesja czytnika niedostępna: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_UNAVAILABLE, detail, request.id)
        if not session.logged_in or (session.username or "").lower() != account.lower():
            detail = (
                f"sesja w kapsule to {session.username or 'anonim'}, zgoda dotyczy konta "
                f"{account}: "
                "kolejka zatrzymana"
            )
            await self._block_everything(request, run.id if run else None, detail, now)
            return PublishOutcome(OUTCOME_BLOCKED, detail, request.id)

        # 5. WŁASNY POST NIE MA WĄTKU, w który miałby trafić: ma go dopiero założyć. Wszystko
        # poniżej aż do kroku 6 sprawdza cudzy wątek, więc dla tego rodzaju idzie inna droga,
        # a wspólny zostaje krok 6: zgoda, dowód, zapis. Rozgałęzienie jest TUTAJ, a nie w
        # osobnej metodzie od początku, bo kroki 1 do 4 (tempo, kapsuła, konto, sesja) dotyczą
        # obu rodzajów tak samo i dwie ich kopie rozjechałyby się przy pierwszej zmianie.
        if request.kind == KIND_OWN_POST:
            own_port = self._own_post_port_factory
            if own_port is None:
                detail = "brak aktuatora własnych postów; wniosek czeka"
                await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
                return PublishOutcome(OUTCOME_CAPSULE, detail, request.id)
            community = await self._publication.community_by_key(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                platform=str(content.platform),
                key=request.target_external_id,
            )
            if community is None:
                detail = f"społeczność {request.target_external_id} nie jest obserwowana"
                await self._publication.set_status(
                    request.id, status=REQUEST_SKIPPED, detail=detail
                )
                return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
            if await self._publication.draft_already_published(
                organization_id=self._organization_id, draft_sha256=request.draft_sha256
            ):
                detail = "ta sama treść już stoi jako nasz wątek; nowy post = nowa zgoda"
                await self._publication.set_status(
                    request.id, status=REQUEST_SKIPPED, detail=detail
                )
                return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
            return await self._write_it(
                request,
                revision=revision,
                content=content,
                account_id=account_id,
                run=run,
                port=own_port(account),
                now=now,
            )

        target = await self._publication.target_for(request)
        community = (
            await self._store.get_community(
                target.community_id, organization_id=self._organization_id
            )
            if target is not None
            else None
        )
        if target is None or community is None:
            await self._publication.set_status(
                request.id, status=REQUEST_SKIPPED, detail="brak wątku albo community"
            )
            return PublishOutcome(OUTCOME_SKIPPED, "brak wątku", request.id)
        try:
            payload = await self._source.read_thread_payload(
                community.community_key, target.thread_external_id
            )
        except ThreadNotFound:
            await self._publication.set_status(
                request.id, status=REQUEST_SKIPPED, detail="wątek zniknął z Reddita"
            )
            return PublishOutcome(OUTCOME_SKIPPED, "wątek zniknął", request.id)
        except (SourceUnavailable, CredentialsMissing) as exc:
            detail = f"odczyt wątku nie powiódł się: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_UNAVAILABLE, detail, request.id)
        facts = thread_facts(payload)
        if facts.locked or facts.archived:
            detail = "wątek zablokowany" if facts.locked else "wątek zarchiwizowany"
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        if (
            request.target_external_id.startswith("t1_")
            and facts.by_fullname(request.target_external_id) is None
        ):
            detail = f"komentarz docelowy {request.target_external_id} zniknął"
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        if facts.by_author(account) or await self._publication.published_on_thread(
            organization_id=self._organization_id, thread_url=target.thread_url
        ):
            detail = "na tym wątku jest już komentarz naszego konta; nowa reakcja = nowa zgoda"
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        await self._record_snapshot(community, target, payload, now)

        return await self._write_it(
            request,
            revision=revision,
            content=content,
            account_id=account_id,
            run=run,
            port=self._port_factory(account),
            now=now,
        )

    async def _publish_x(
        self,
        request: PublicationRequest,
        *,
        revision: Any,
        content: Any,
        account_id: UUID,
        account: str,
        run: Any,
        now: datetime,
    ) -> PublishOutcome:
        """Steps 4 to 6 for X (26.09): identity, the post, duplicates, then the common write.

        THE SAME RULES AS REDDIT, READ FROM X. The browser must be logged into THE consented
        account: another identity stops the whole queue (it is the one failure a retry makes
        worse), a missing session keeps the request waiting with the reason, because a human
        logging in again is the fix. A reply needs its post to still be there, and our account
        must not have answered under it already, whether we can see it on the page or only in
        our own queue (UNKNOWN counts: an unknown outcome is not permission to repeat, L5335).
        """
        assert self._x_source is not None
        state = await self._x_source.verify_identity(account)
        if not state.logged_in or (state.handle or "").lower() != account.lower():
            if state.logged_in and state.handle:
                detail = (
                    f"przeglądarka agenta jest zalogowana na X jako @{state.handle}, zgoda "
                    f"dotyczy @{account}: kolejka zatrzymana"
                )
                await self._block_everything(request, run.id if run else None, detail, now)
                return PublishOutcome(OUTCOME_BLOCKED, detail, request.id)
            detail = f"brak sesji X konta @{account}: {state.error or 'nie odczytano'}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_UNAVAILABLE, detail, request.id)

        if request.kind == KIND_OWN_POST:
            if self._x_own_post_port_factory is None:
                detail = "brak aktuatora postów na X; wniosek czeka"
                await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
                return PublishOutcome(OUTCOME_CAPSULE, detail, request.id)
            if await self._publication.draft_already_published(
                organization_id=self._organization_id, draft_sha256=request.draft_sha256
            ):
                detail = "ta sama treść już stoi jako nasz post na X; nowy post = nowa zgoda"
                await self._publication.set_status(
                    request.id, status=REQUEST_SKIPPED, detail=detail
                )
                return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
            return await self._write_it(
                request,
                revision=revision,
                content=content,
                account_id=account_id,
                run=run,
                port=self._x_own_post_port_factory(account),
                now=now,
            )

        status_id = request.target_external_id.removeprefix("x:")
        try:
            page = await self._x_source.read_post(request.target_url)
        except CredentialsMissing as exc:
            detail = f"brak sesji X przy odczycie posta: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_UNAVAILABLE, detail, request.id)
        except (SourceUnavailable, ValueError) as exc:
            detail = f"odczyt posta X nie powiódł się: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_QUEUED, detail=detail)
            return PublishOutcome(OUTCOME_UNAVAILABLE, detail, request.id)
        focal = page.by_id(status_id)
        if page.unavailable or focal is None:
            why = page.unavailable or "nie ma go na stronie"
            detail = f"post X {status_id} jest niedostępny ({why})"
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        ours_below = [one for one in page.by_author(account) if one.relation == "reply"]
        if ours_below or await self._publication.published_on_thread(
            organization_id=self._organization_id, thread_url=request.target_url
        ):
            detail = "pod tym postem X jest już nasza odpowiedź; nowa reakcja = nowa zgoda"
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        assert self._x_port_factory is not None
        return await self._write_it(
            request,
            revision=revision,
            content=content,
            account_id=account_id,
            run=run,
            port=self._x_port_factory(account),
            now=now,
        )

    async def _write_it(
        self,
        request: PublicationRequest,
        *,
        revision: Any,
        content: Any,
        account_id: UUID,
        run: Any,
        port: WritingSideEffectPort,
        now: datetime,
    ) -> PublishOutcome:
        """Krok 6 dla OBU rodzajów wniosku: zgoda, dowód, zapis, i jedno czytanie wyniku.

        Wspólny, bo to jest ta część, w której nie wolno się pomylić raz na dwa sposoby:
        intent wystawiony i skonsumowany, zatrzymanie całej kolejki przy blokadzie platformy,
        i trzy różne stany po próbie. Co jedzie na platformę, decyduje `port`, nie ta metoda.
        """
        # 6. The grant, then the proof, then the write.
        try:
            intent = await self._approvals.issue_intent(
                revision_id=revision.id,
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                account_id=account_id,
                expires_at=now + INTENT_TTL,
                actor_ref="job:pilot.publish",
            )
        except (RevisionNotGrantable, ApprovalStoreError) as exc:
            detail = f"zgoda już nie autoryzuje: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
        await self._publication.set_status(
            request.id, status=REQUEST_PUBLISHING, intent_id=intent.id, detail="intent wystawiony"
        )
        try:
            proof = await self._approvals.authorize_and_consume_intent(
                intent.id, organization_id=self._organization_id, actor_ref="job:pilot.publish"
            )
        except (PermissionError, ApprovalStoreError) as exc:
            detail = f"autoryzacja odmówiona: {exc}"[:300]
            await self._publication.set_status(request.id, status=REQUEST_SKIPPED, detail=detail)
            return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)

        captured: dict[str, Any] = {}

        async def sink(run_row: Any, evidence: SideEffectEvidence) -> None:
            captured["evidence"] = evidence
            await self._publication.record_evidence(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                attempt_id=run_row.execution_attempt_id,
                external_id=evidence.external_id,
                permalink=evidence.permalink,
                account=evidence.account,
                observed_at=evidence.observed_at,
                source=EVIDENCE_ACTUATOR,
                raw=dict(evidence.raw),
            )

        adapter = SideEffectAdapter(self._execution, port, evidence=sink)
        try:
            attempt = await adapter.perform(content, authorization=proof)
        except UnknownSideEffectOutcome as exc:
            cause = exc.__cause__
            attempt_id = exc.run.execution_attempt_id
            # The actuator's own account of whether the save button was clicked, carried into
            # the request's detail as a marker the requeue can read. `None` is "nobody knows".
            marker = submit_marker(getattr(cause, "submit_started", None))
            if isinstance(cause, PublicationBlocked):
                detail = f"{marker} platforma zablokowała: {cause}"[:300]
                await self._publication.set_status(
                    request.id, status=REQUEST_BLOCKED, attempt_id=attempt_id, detail=detail
                )
                await self._block_everything(None, run.id if run else None, detail, now)
                return PublishOutcome(OUTCOME_BLOCKED, detail, request.id)
            if isinstance(cause, PublicationRefused):
                detail = f"{marker} aktuator odmówił, nic nie wysłano: {cause}"[:300]
                await self._publication.set_status(
                    request.id, status=REQUEST_SKIPPED, attempt_id=attempt_id, detail=detail
                )
                return PublishOutcome(OUTCOME_SKIPPED, detail, request.id)
            detail = f"{marker} skutek nieznany po SUBMIT_STARTED: {cause}"[:300]
            await self._publication.set_status(
                request.id, status=REQUEST_UNKNOWN, attempt_id=attempt_id, detail=detail
            )
            return PublishOutcome(OUTCOME_UNKNOWN, detail, request.id)
        finally:
            aclose = getattr(port, "aclose", None)
            if aclose is not None:
                await aclose()

        evidence = captured.get("evidence")
        await self._publication.set_status(
            request.id,
            status=REQUEST_PUBLISHED,
            attempt_id=attempt.execution_attempt_id,
            published_at=now,
            detail=f"opublikowano {evidence.external_id if evidence else '?'}",
        )
        await self._start_experience_chain()
        return PublishOutcome(
            OUTCOME_PUBLISHED, f"opublikowano {request.target_external_id}", request.id, evidence
        )

    async def _start_experience_chain(self) -> None:
        """Pierwsza publikacja organizacji zaczyna łańcuch mierzenia jej skutków (P8 etap B).

        DLACZEGO TUTAJ, a nie w zasiewie przy starcie procesu (ten też jest, `runtime.py`).
        Zasiew biegnie raz, przy starcie; organizacja, która opublikowała cokolwiek godzinę
        później, czekałaby z pierwszym pomiarem do następnego restartu. Publikacja jest jedynym
        zdarzeniem, po którym w ogóle JEST co mierzyć, więc to ona ma zaczynać.

        Idempotentne po kluczu godziny, tym samym, którego używa łańcuch: gdy przemiatanie już
        biegnie, to wywołanie nie dodaje niczego i drugiego łańcucha nie będzie. Wyjątek z kolejki
        nie ma prawa zmienić wyniku publikacji, która właśnie się udała: publikacja jest faktem
        o świecie, a nieumówione przemiatanie tylko opóźnieniem pomiaru.
        """
        from control_plane.knowledge.handlers import EXPERIENCE_INTERVAL, schedule_experience_sweep

        try:
            await schedule_experience_sweep(
                self._queue,
                organization_id=self._organization_id,
                delay_seconds=EXPERIENCE_INTERVAL.total_seconds(),
            )
        except Exception as exc:  # publikacja już się wydarzyła, pomiar może poczekać
            _LOG.warning(
                "nie udało się zamówić pierwszego przemiatania doświadczeń: %s: %s",
                type(exc).__name__,
                exc,
            )

    # -- after the fact --------------------------------------------------------------------------

    async def _reconcile_unknown(self, now: datetime) -> None:
        """An UNKNOWN attempt is a question for the platform, not for a retry (L5335)."""
        if self._source is None and self._x_source is None:
            return
        for request in await self._publication.requests(organization_id=self._organization_id):
            if request.status != REQUEST_UNKNOWN:
                continue
            if request.last_checked_at and now - request.last_checked_at < timedelta(minutes=5):
                continue
            if is_x_target(request.target_external_id):
                if self._x_source is not None:
                    await self._reconcile_unknown_x(request, now)
                continue
            if self._source is None:
                continue
            if request.kind == KIND_OWN_POST:
                await self._reconcile_unknown_own_post(request, now)
                continue
            facts, target, community, payload = await self._facts_for(request)
            if facts is None or target is None or community is None:
                continue
            revision = await self._approvals.fetch_revision(
                request.proposal_revision_id, organization_id=self._organization_id
            )
            account = (
                await self._publication.account_handle(revision.hash_input.account)
                if revision
                else None
            )
            if revision is None or account is None:
                continue
            wanted = _normalised(revision.hash_input.text_and_media)
            found = next(
                (
                    c
                    for c in facts.by_author(account)
                    if _normalised(str(c.get("body", ""))) == wanted
                ),
                None,
            )
            if found is None:
                age = now - (request.published_at or request.queued_at)
                await self._publication.set_status(
                    request.id,
                    status=REQUEST_UNKNOWN,
                    detail=keep_marker(
                        request.detail,
                        f"brak komentarza konta {account} z tą treścią w wątku "
                        f"({int(age.total_seconds() // 60)} min po próbie)",
                    ),
                )
                if age > RECONCILE_WINDOW:
                    _LOG.warning("pilot.publish: wniosek %s pozostaje UNKNOWN po 24 h", request.id)
                continue
            fullname = str(found.get("name") or "")
            permalink = "https://www.reddit.com" + str(found.get("permalink") or "")
            if request.attempt_id is not None:
                await self._publication.record_evidence(
                    organization_id=self._organization_id,
                    workspace_id=request.workspace_id,
                    attempt_id=request.attempt_id,
                    external_id=fullname,
                    permalink=permalink,
                    account=account,
                    observed_at=now,
                    source=EVIDENCE_RECONCILIATION,
                    raw={"score": found.get("score"), "created_utc": found.get("created_utc")},
                )
            await self._publication.set_status(
                request.id,
                status=REQUEST_PUBLISHED,
                published_at=now,
                detail=f"rekoncyliacja: komentarz {fullname} odczytany z wątku",
            )
            await self._record_snapshot(community, target, payload, now)

    async def _reconcile_unknown_own_post(self, request: PublicationRequest, now: datetime) -> None:
        """Czy nasz wątek jednak stanął. Pytanie do platformy, nigdy ponowienie (L5335).

        NIE MA identyfikatora, po którym można by go odczytać wprost: nieznany skutek to właśnie
        ta sytuacja, w której platforma nic nie odpowiedziała. Zostaje więc jedyne wiarygodne
        pytanie: czy w tej społeczności stoi świeży wątek NASZEGO konta o TYM tytule. Dwa warunki
        naraz, bo sam tytuł napisać może każdy, a samo konto publikuje też co innego.
        """
        revision = await self._approvals.fetch_revision(
            request.proposal_revision_id, organization_id=self._organization_id
        )
        account = (
            await self._publication.account_handle(revision.hash_input.account)
            if revision
            else None
        )
        if revision is None or account is None:
            return
        wanted_title, _ = split_own_post(revision.hash_input.text_and_media)
        try:
            recent = await self._source.list_recent_threads(request.target_external_id, since=None)
        except (ThreadNotFound, SourceUnavailable, CredentialsMissing) as exc:
            _LOG.info("pilot.publish: listing %s nieczytelny: %s", request.target_external_id, exc)
            return
        found = next(
            (
                one
                for one in recent
                if (one.author or "").lower() == account.lower()
                and _normalised(one.title) == _normalised(wanted_title)
            ),
            None,
        )
        if found is None:
            age = now - (request.published_at or request.queued_at)
            await self._publication.set_status(
                request.id,
                status=REQUEST_UNKNOWN,
                detail=keep_marker(
                    request.detail,
                    f"brak wątku konta {account} o tym tytule w {request.target_external_id} "
                    f"({int(age.total_seconds() // 60)} min po próbie)",
                ),
            )
            if age > RECONCILE_WINDOW:
                _LOG.warning("pilot.publish: wniosek %s pozostaje UNKNOWN po 24 h", request.id)
            return
        fullname = (
            found.external_id if found.external_id.startswith("t3_") else f"t3_{found.external_id}"
        )
        if request.attempt_id is not None:
            await self._publication.record_evidence(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                attempt_id=request.attempt_id,
                external_id=fullname,
                permalink=found.url,
                account=account,
                observed_at=now,
                source=EVIDENCE_RECONCILIATION,
                raw={"tytul": found.title, "created_at": found.created_at.isoformat()},
            )
        await self._publication.set_status(
            request.id,
            status=REQUEST_PUBLISHED,
            published_at=now,
            detail=f"rekoncyliacja: wątek {fullname} odczytany z listingu społeczności",
        )

    async def _observe_published(self, now: datetime) -> None:
        if self._source is None and self._x_source is None:
            return
        for request in await self._publication.requests(organization_id=self._organization_id):
            if request.status != REQUEST_PUBLISHED or request.attempt_id is None:
                continue
            if request.published_at is None or now - request.published_at >= OBSERVE_WINDOW:
                continue
            last = await self._publication.latest_observation(request.id)
            young_x = (
                is_x_target(request.target_external_id)
                and now - request.published_at < YOUNG_X_POST
            )
            interval = YOUNG_X_OBSERVE_INTERVAL if young_x else OBSERVE_INTERVAL
            if last is not None and now - last["observed_at"] < interval:
                continue
            evidence = await self._publication.evidence_for(request.attempt_id)
            if evidence is None:
                continue
            if is_x_target(request.target_external_id):
                if self._x_source is not None:
                    await self._observe_x(request, evidence, now)
                continue
            if self._source is None:
                continue
            if request.kind == KIND_OWN_POST:
                await self._observe_own_post(request, evidence, now)
                continue
            facts, target, community, payload = await self._facts_for(request)
            if facts is None or target is None or community is None:
                continue
            ours = facts.by_fullname(evidence["external_id"])
            replies = facts.replies_to(evidence["external_id"])
            await self._publication.record_observation(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                request_id=request.id,
                observed_at=now,
                still_present=ours is not None,
                score=int(ours["score"])
                if ours is not None and ours.get("score") is not None
                else None,
                reply_count=len(replies),
                raw={"reply_ids": [r.get("name") for r in replies]},
                # Spelled out although it is the default: this read goes through the capsule's
                # logged-in Chrome, so it is the AUTHOR looking, and Reddit shows an author their
                # own removed comments. Reading it as "the community sees it" is the incident of
                # 17.09 (STAN-PRAC §8.22).
                viewer=VIEWER_AUTHOR,
            )
            await self._record_snapshot(community, target, payload, now)

    # -- is it visible to anyone but us (migration 0012, STAN-PRAC §8.22) ------------------------

    async def _observe_own_post(
        self, request: PublicationRequest, evidence: Mapping[str, Any], now: datetime
    ) -> None:
        """To samo co dla komentarza, tylko obserwowaną rzeczą jest sam wątek.

        Bez migawki wątku: `thread_snapshots` opisuje wątek OBSERWOWANY, a naszego własnego nikt
        nie obserwował, więc nie ma wiersza, do którego migawka miałaby się odwołać. Zapisanie
        jej pod cudzym wątkiem byłoby wpisem o czymś, co się nie wydarzyło.
        """
        try:
            facts = await self._own_post_facts(request, evidence, anonymous=False)
        except (ThreadNotFound, SourceUnavailable, CredentialsMissing) as exc:
            _LOG.info("pilot.publish: odczyt naszego wątku %s nie powiódł się: %s", request.id, exc)
            return
        present = facts.post_fullname == str(evidence["external_id"])
        await self._publication.record_observation(
            organization_id=self._organization_id,
            workspace_id=request.workspace_id,
            request_id=request.id,
            observed_at=now,
            still_present=present,
            score=(
                int(facts.post["score"])
                if present and facts.post.get("score") is not None
                else None
            ),
            reply_count=len(facts.comments),
            raw={"post": facts.post_fullname, "komentarze": len(facts.comments)},
            # Tak samo jak przy komentarzu: to patrzy AUTOR, zalogowaną przeglądarką kapsuły.
            viewer=VIEWER_AUTHOR,
        )

    async def _check_own_post_anonymously(
        self, request: PublicationRequest, evidence: Mapping[str, Any], now: datetime
    ) -> None:
        """Czy nasz wątek widzi ktoś, kto nie jest nami. Ta sama zasada co przy komentarzu:
        odmowa czytnika NIE JEST odpowiedzią „nie ma" (403 i 429 też tu wpadają)."""
        try:
            facts = await self._own_post_facts(request, evidence, anonymous=True)
        except (ThreadNotFound, SourceUnavailable, CredentialsMissing) as exc:
            await self._note_unverified(request, f"odczyt anonimowy nie powiódł się: {exc}")
            return
        except Exception as exc:  # strona nie do odczytania to też nie jest odpowiedź
            await self._note_unverified(request, f"odczyt anonimowy nieczytelny: {exc}")
            return
        present = facts.post_fullname == str(evidence["external_id"])
        look = AnonymousLook(
            observed_at=now,
            present=present,
            body=(
                str(facts.post.get("selftext"))
                if present and facts.post.get("selftext") is not None
                else None
            ),
            score=(
                int(facts.post["score"])
                if present and facts.post.get("score") is not None
                else None
            ),
            reply_count=len(facts.comments),
        )
        visibility = await self._publication.record_anonymous_look(
            organization_id=self._organization_id,
            workspace_id=request.workspace_id,
            request_id=request.id,
            look=look,
            raw={"post": str(evidence["external_id"]), "tytul": facts.post.get("title")},
        )
        if visibility != "CONFIRMED":
            _LOG.warning(
                "pilot.publish: wątek %s nie jest widoczny anonimowo (%s) mimo PUBLISHED",
                request.id,
                visibility,
            )

    async def _collect_removal_reports(self, now: datetime) -> None:
        """The account's inbox: an AutoModerator reply about our comment is a removal, full stop.

        Independent of any read of ours, and cheap: one inbox read per tick for the whole account,
        not one per request. A report naming a comment we have no evidence row for is ignored and
        logged; it belongs to a comment this queue did not publish.
        """
        if self._removals is None:
            return
        try:
            reports = await self._removals.read_removal_reports()
        except (SourceUnavailable, CredentialsMissing, ThreadNotFound) as exc:
            _LOG.info("pilot.publish: skrzynka konta nieczytelna: %s", exc)
            return
        for report in reports:
            request = await self._publication.request_by_comment_external_id(
                organization_id=self._organization_id,
                external_id=report.comment_external_id,
            )
            if request is None:
                _LOG.info(
                    "pilot.publish: zgłoszenie usunięcia dotyczy komentarza %s, którego nie ma "
                    "w dowodach tej kolejki",
                    report.comment_external_id,
                )
                continue
            await self._publication.report_removal(
                request.id,
                reported_at=report.reported_at,
                source=REMOVAL_FROM_INBOX,
                detail=keep_marker(
                    request.detail, f"skrzynka konta zgłasza usunięcie: {report.note[:160]}"
                ),
            )
            _LOG.warning(
                "pilot.publish: wniosek %s ma zgłoszone usunięcie ze skrzynki (%s)",
                request.id,
                report.comment_external_id,
            )

    async def _check_public_visibility(self, now: datetime) -> None:
        """At most `MAX_ANONYMOUS_READS_PER_TICK` anonymous reads per tick, oldest verdict first.

        A request whose visibility was checked within `ANONYMOUS_CHECK_INTERVAL` is left alone,
        and one already REMOVED is not read again: a removal does not un-happen, and re-reading it
        every hour would spend the tick's single read on a settled case instead of an open one.

        A read that FAILED records nothing. The request stays UNVERIFIED and the reason goes into
        its detail, because "Reddit refused to answer" and "the comment is gone" are two different
        facts and the whole point of this column is not to confuse them.
        """
        if self._anonymous is None and self._x_source is None:
            return
        due = [
            r
            for r in await self._publication.requests(organization_id=self._organization_id)
            if r.status == REQUEST_PUBLISHED
            and r.attempt_id is not None
            and r.removal_reported_at is None
            and r.public_visibility != VISIBILITY_REMOVED
            and (
                r.public_visibility_checked_at is None
                or now - r.public_visibility_checked_at >= ANONYMOUS_CHECK_INTERVAL
            )
        ]
        # A freshly sent, never checked comment gets the next read. Otherwise a backlog of old
        # UNVERIFIED publications can postpone today's public verdict for hours. Outside that
        # short window the old fairness rule applies: a failed read bumps last_checked_at, so
        # one stubborn 403 does not monopolise every tick.
        due.sort(key=lambda r: visibility_check_key(r, now))
        for request in due[:MAX_ANONYMOUS_READS_PER_TICK]:
            await self._check_one_request_anonymously(request, now)

    async def _check_one_request_anonymously(
        self, request: PublicationRequest, now: datetime
    ) -> None:
        assert request.attempt_id is not None
        evidence = await self._publication.evidence_for(request.attempt_id)
        if evidence is None:
            return
        if is_x_target(request.target_external_id):
            if self._x_source is not None:
                await self._check_x_anonymously(request, evidence, now)
            return
        if self._anonymous is None:
            return
        if request.kind == KIND_OWN_POST:
            await self._check_own_post_anonymously(request, evidence, now)
            return
        target = await self._publication.target_for(request)
        community = (
            await self._store.get_community(
                target.community_id, organization_id=self._organization_id
            )
            if target is not None
            else None
        )
        if target is None or community is None:
            return
        try:
            payload = await self._anonymous.read_thread_payload_anonymously(
                community.community_key, target.thread_external_id
            )
            facts = thread_facts(payload)
        except (ThreadNotFound, SourceUnavailable, CredentialsMissing) as exc:
            # Includes 403 and 429: the reader raises rather than returning an empty thread, so
            # nothing here can read a refusal as "the comment is not there".
            await self._check_rendered_comment(request, evidence, target, community, now, str(exc))
            return
        except Exception as exc:  # an unparseable page is also not an answer about the comment
            await self._note_unverified(request, f"odczyt anonimowy nieczytelny: {exc}")
            return
        ours = facts.by_fullname(str(evidence["external_id"]))
        look = AnonymousLook(
            observed_at=now,
            present=ours is not None,
            body=(
                str(ours.get("body")) if ours is not None and ours.get("body") is not None else None
            ),
            score=(
                int(ours["score"]) if ours is not None and ours.get("score") is not None else None
            ),
            reply_count=len(facts.replies_to(str(evidence["external_id"]))),
        )
        visibility = await self._publication.record_anonymous_look(
            organization_id=self._organization_id,
            workspace_id=request.workspace_id,
            request_id=request.id,
            look=look,
            raw={"body": look.body, "comment": evidence["external_id"]},
        )
        if visibility != "CONFIRMED":
            _LOG.warning(
                "pilot.publish: wniosek %s nie jest widoczny anonimowo (%s) mimo PUBLISHED",
                request.id,
                visibility,
            )

    async def _check_rendered_comment(
        self,
        request: PublicationRequest,
        evidence: Mapping[str, Any],
        target: Any,
        community: Any,
        now: datetime,
        json_error: str,
    ) -> None:
        """After Reddit refuses logged-out JSON, inspect the exact public permalink in a browser.

        A missing element or a different author is UNKNOWN. A partial page can still prove the
        state of the exact element; blocked unrelated assets cannot change its text.
        """
        read_publicly = getattr(self._anonymous, "read_comment_publicly", None)
        if read_publicly is None:
            await self._note_unverified(request, f"odczyt anonimowy nie powiódł się: {json_error}")
            return
        comment_id = str(evidence["external_id"])
        try:
            page = await read_publicly(
                community.community_key, target.thread_external_id, comment_id
            )
        except Exception as exc:
            await self._note_unverified(
                request, f"JSON: {json_error}; publiczny permalink: {type(exc).__name__}"
            )
            return
        comment = page.get("target_comment") if isinstance(page, Mapping) else None
        if not isinstance(comment, Mapping) or comment.get("external_id") != comment_id:
            await self._note_unverified(request, "Publiczny permalink nie pokazał komentarza.")
            return
        if (
            not str(comment.get("permalink") or "")
            .rstrip("/")
            .endswith(f"/comment/{comment_id[3:]}")
        ):
            await self._note_unverified(request, "Publiczny permalink nie wskazał tego komentarza.")
            return
        rendered = str(comment.get("text") or "")
        removed = "Comment removed by moderator" in rendered
        # CZY ZNIKNĘŁA NASZA ODPOWIEDŹ, CZY CAŁY WĄTEK POD NIĄ. Reddit pokazuje usunięty komentarz
        # tak samo niezależnie od powodu, więc bez tego pytania oba przypadki trafiają do pamięci
        # jako jeden. Zmierzone 26.09 na `t1_pblmb3d`: post `1woa28h` miał
        # `removed_by_category = reddit`, a renderowany permalink mówił „Sorry, this post was
        # removed by Reddit's filters”. Nasz komentarz nie został oceniony przez nikogo, tylko
        # zabrany razem z wątkiem, a zapis bez tej różnicy zrodził hipotezę o karmie konta.
        removal_scope = _removal_scope(page, comment, removed=removed)
        if not removed:
            revision = await self._approvals.fetch_revision(
                request.proposal_revision_id, organization_id=self._organization_id
            )
            if revision is None:
                await self._note_unverified(
                    request, "Brak rewizji do porównania z publicznym widokiem."
                )
                return
            account = await self._publication.account_handle(revision.hash_input.account)
            wanted = _normalised(revision.hash_input.text_and_media)
            if (
                not account
                or str(comment.get("author") or "").lower() != account.lower()
                or not wanted
                or wanted not in _normalised(rendered)
            ):
                await self._note_unverified(
                    request, "Publiczny widok nie potwierdził autora i treści odpowiedzi."
                )
                return
        score_raw = str(comment.get("score") or "")
        score = int(score_raw) if score_raw.isdigit() and not removed else None
        look = AnonymousLook(
            observed_at=now,
            present=True,
            body="[removed]" if removed else rendered,
            score=score,
            reply_count=0,
        )
        visibility = await self._publication.record_anonymous_look(
            organization_id=self._organization_id,
            workspace_id=request.workspace_id,
            request_id=request.id,
            look=look,
            raw={
                "source": "rendered_public_permalink",
                "url": page.get("url"),
                "comment": comment_id,
                "text": rendered[:1000],
                "reply_count_measured": False,
                "partial": page.get("partial"),
                "removal_scope": removal_scope,
            },
            removal_scope=removal_scope,
        )
        _LOG.info("pilot.publish: publiczny widok %s: %s", request.id, visibility)

    # -- X after the fact (26.09) ---------------------------------------------------------------

    async def _reconcile_unknown_x(self, request: PublicationRequest, now: datetime) -> None:
        """Did our X post appear after all. A question to the platform, never a retry (L5335).

        Read from our own account's page with replies, as the author: the one place where a post
        of ours shows up whatever it answered. Matched on the consented text, normalised the same
        way as on Reddit; found means PUBLISHED with evidence from this read, not found keeps
        UNKNOWN with the age, and after the window it stays UNKNOWN with a warning.
        """
        assert self._x_source is not None
        revision = await self._approvals.fetch_revision(
            request.proposal_revision_id, organization_id=self._organization_id
        )
        account = (
            await self._publication.account_handle(revision.hash_input.account)
            if revision
            else None
        )
        if revision is None or account is None:
            return
        try:
            page = await self._x_source.profile_replies(account)
        except (CredentialsMissing, SourceUnavailable, ValueError) as exc:
            await self._note_unverified(request, f"rekoncyliacja X nieudana: {exc}"[:200])
            return
        # The text typed on X, without the image line a consent may end in (27.09).
        wanted = _normalised(split_media(revision.hash_input.text_and_media)[0])
        found = next(
            (
                post
                for post in page.by_author(account)
                if post.id and post.text_source == "post" and _normalised(post.text or "") == wanted
            ),
            None,
        )
        if found is None:
            age = now - (request.published_at or request.queued_at)
            await self._publication.set_status(
                request.id,
                status=REQUEST_UNKNOWN,
                detail=keep_marker(
                    request.detail,
                    f"brak posta konta @{account} z tą treścią na X "
                    f"({int(age.total_seconds() // 60)} min po próbie)",
                ),
            )
            if age > RECONCILE_WINDOW:
                _LOG.warning("pilot.publish: wniosek X %s pozostaje UNKNOWN po 24 h", request.id)
            return
        if request.attempt_id is not None:
            await self._publication.record_evidence(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                attempt_id=request.attempt_id,
                external_id=f"x:{found.id}",
                permalink=found.permalink or f"https://x.com/{account}/status/{found.id}",
                account=account,
                observed_at=now,
                source=EVIDENCE_RECONCILIATION,
                raw={"metrics": dict(found.metrics), "published_at": found.published_at},
            )
        await self._publication.set_status(
            request.id,
            status=REQUEST_PUBLISHED,
            published_at=now,
            detail=f"rekoncyliacja: post X {found.id} odczytany z profilu konta",
        )

    async def _observe_x(
        self, request: PublicationRequest, evidence: Mapping[str, Any], now: datetime
    ) -> None:
        """Read our X post back AS THE AUTHOR: is it there, its counters, who answered.

        The counters are what X showed our own account and they are REACTIONS, not visibility:
        whether a stranger can see the post is `_check_x_anonymously`'s question (0012's rule,
        the same as Reddit's). A counter X did not show stays absent in `raw`, never zero.
        """
        assert self._x_source is not None
        post_id = str(evidence["external_id"]).removeprefix("x:")
        try:
            page = await self._x_source.read_post(str(evidence["permalink"]))
        except (CredentialsMissing, SourceUnavailable, ValueError) as exc:
            _LOG.info("pilot.publish: odczyt naszego posta X %s nieudany: %s", request.id, exc)
            return
        ours = page.by_id(post_id)
        replies = [one for one in page.posts if one.relation == "reply" and one.id != post_id]
        await self._publication.record_observation(
            organization_id=self._organization_id,
            workspace_id=request.workspace_id,
            request_id=request.id,
            observed_at=now,
            still_present=ours is not None and not page.unavailable,
            score=ours.metrics.get("likes") if ours is not None else None,
            reply_count=(
                ours.metrics["replies"]
                if ours is not None and "replies" in ours.metrics
                else len(replies)
            ),
            raw={
                "platforma": "X",
                "metrics": dict(ours.metrics) if ours is not None else {},
                "odpowiedzi": [
                    {"id": one.id, "autor": one.author, "tekst": (one.text or "")[:280]}
                    for one in replies[:20]
                ],
                "niedostepna": page.unavailable,
            },
            viewer=VIEWER_AUTHOR,
        )

    async def _check_x_anonymously(
        self, request: PublicationRequest, evidence: Mapping[str, Any], now: datetime
    ) -> None:
        """Can a stranger see our X post: a fresh browser context, no cookies, the permalink.

        Three answers, kept apart: the post rendered with our id (CONFIRMED), X says the post is
        gone (REMOVED, and it is OUR post that is gone), or neither, including X showing a
        stranger only its login wall (UNVERIFIED with the reason, never a verdict). A refusal of
        the read is not "invisible".
        """
        assert self._x_source is not None
        post_id = str(evidence["external_id"]).removeprefix("x:")
        try:
            page = await self._x_source.read_post(str(evidence["permalink"]), anonymous=True)
        except (CredentialsMissing, SourceUnavailable, ValueError) as exc:
            await self._note_unverified(request, f"odczyt anonimowy X nieudany: {exc}"[:200])
            return
        ours = page.by_id(post_id)
        if ours is not None:
            look = AnonymousLook(
                observed_at=now,
                present=True,
                body=ours.text,
                score=ours.metrics.get("likes"),
                reply_count=int(ours.metrics.get("replies", 0)),
            )
            await self._publication.record_anonymous_look(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                request_id=request.id,
                look=look,
                raw={"platforma": "X", "post": post_id, "text_source": ours.text_source},
            )
            return
        if page.unavailable in ("not_found", "deleted"):
            look = AnonymousLook(observed_at=now, present=False, body=None)
            await self._publication.record_anonymous_look(
                organization_id=self._organization_id,
                workspace_id=request.workspace_id,
                request_id=request.id,
                look=look,
                raw={"platforma": "X", "post": post_id, "niedostepna": page.unavailable},
                removal_scope=REMOVAL_SCOPE_COMMENT,
            )
            return
        reason = (
            "X pokazał obcemu ekran logowania zamiast posta"
            if page.login_wall
            else f"post nie pojawił się w widoku anonimowym ({page.unavailable or 'brak'})"
        )
        await self._note_unverified(request, f"widoczność X nieustalona: {reason}")

    async def _note_unverified(self, request: PublicationRequest, reason: str) -> None:
        """Say WHY the visibility is still unknown, and record that we tried.

        The verdict does not move: UNVERIFIED stays UNVERIFIED, and `public_visibility_checked_at`
        stays empty, because a read that failed is not a check. What DOES move is
        `last_checked_at`, which the ordering above uses to give the next request its turn. The
        reason text is only rewritten when it changed, so a reader that is down for an hour does
        not fill the detail with six copies of the same sentence.
        """
        detail = keep_marker(request.detail, reason)
        await self._publication.set_status(
            request.id,
            status=request.status,
            detail=None if detail == request.detail else detail,
        )

    async def _facts_for(self, request: PublicationRequest) -> tuple[Any, Any, Any, Any]:
        """`(facts, target, community, payload)`, or four `None`s when the thread cannot be read.

        The second element is the request's THREAD (`PublishTarget`), not its pilot reaction: the
        reconciliation and the visibility checks run on project requests as well, and asking the
        pilot chain for a row that does not exist was the one thing standing in their way.
        """
        target = await self._publication.target_for(request)
        community = (
            await self._store.get_community(
                target.community_id, organization_id=self._organization_id
            )
            if target is not None
            else None
        )
        if target is None or community is None:
            return None, None, None, None
        try:
            payload = await self._source.read_thread_payload(
                community.community_key, target.thread_external_id
            )
        except (ThreadNotFound, SourceUnavailable, CredentialsMissing) as exc:
            _LOG.info(
                "pilot.publish: odczyt wątku %s nie powiódł się: %s",
                target.thread_external_id,
                exc,
            )
            return None, None, None, None
        return thread_facts(payload), target, community, payload

    async def _own_post_facts(
        self, request: PublicationRequest, evidence: Mapping[str, Any], *, anonymous: bool
    ) -> ThreadFacts:
        """Nasz własny wątek, przeczytany po identyfikatorze z dowodu.

        Przy odpowiedzi celem był cudzy wątek i to jego czytamy; przy własnym poście celem była
        społeczność, a wątek POWSTAŁ dopiero z publikacji, więc jedyne, co go wskazuje, to dowód
        wykonania. `target_for` tego nie znajdzie i nie ma powodu, żeby znajdowało: wątku nie ma
        wśród obserwowanych, bo nikt go nie obserwował, tylko my go założyliśmy.

        Rzuca tym, czym rzuca czytnik: rozróżnienie „nie ma" od „nie dało się przeczytać" należy
        do wywołującego, bo dla dwóch spojrzeń znaczy co innego.
        """
        post_id = str(evidence["external_id"]).removeprefix("t3_")
        reader = (
            self._anonymous.read_thread_payload_anonymously
            if anonymous
            else self._source.read_thread_payload
        )
        return thread_facts(await reader(request.target_external_id, post_id))

    async def _record_snapshot(
        self, community: Any, target: PublishTarget, payload: Any, now: datetime
    ) -> None:
        """The thread as read, into the observation tables: this is how the people's replies to
        our comment reach `observed_comments` (parent_external_id = our fullname's id)."""
        external_id = target.thread_external_id
        try:
            snapshot = parse_thread_pair(
                payload,
                community_key=community.community_key,
                external_id=external_id,
                observed_at=now,
                where=f"/r/{community.community_key}/comments/{external_id}.json",
            )
        except Exception as exc:  # a snapshot we cannot parse is not a reason to stop publishing
            _LOG.info("pilot.publish: snapshot wątku nieczytelny: %s", exc)
            return
        await self._store.record_thread(community=community, thread=snapshot.thread)
        await self._store.record_comments(
            community=community, thread_id=target.thread_id, comments=snapshot.comments
        )

    async def _read_capsule_state(self, *, on_x: bool = False) -> Mapping[str, Any] | None:
        reader = self._x_capsule_state if on_x and self._x_capsule_state else self._capsule_state
        if reader is None:
            return None
        try:
            return await reader()
        except Exception as exc:  # the capsule being down is an outcome, not a crash of the tick
            _LOG.info("pilot.publish: /state kapsuły nie odpowiada: %s", exc)
            return None

    async def _block_everything(
        self, request: PublicationRequest | None, run_id: UUID | None, detail: str, now: datetime
    ) -> None:
        """Stop the WHOLE queue, both kinds of request, and say so where a person will look.

        `run_id` is None for a project's request, and then the host event is the only step that
        is skipped: it belongs to a pilot run's timeline and there is no run. The stop itself is
        organization-wide either way, which is the point of it: an identity mismatch in the
        capsule is not a fact about one request.
        """
        if request is not None:
            await self._publication.set_status(request.id, status=REQUEST_BLOCKED, detail=detail)
        await self._publication.stop_all_queued(
            organization_id=self._organization_id, detail=f"zatrzymane: {detail}"[:300]
        )
        await self._publication.stop_publishing(organization_id=self._organization_id, at=now)
        if run_id is not None:
            await self._pilot.append_host_event(
                run_id, at=now, kind="PUBLISHING_BLOCKED", detail=detail[:300]
            )
        if self._autoapprover is not None:
            await self._autoapprover.stop(reason=f"BLOCKED: {detail}"[:300], now=now)


#: The actuator's own account of whether the save button was clicked, written at the head of the
#: request's detail. Error TEXT is never read for this: a 502 or a client timeout can happen after
#: the click just as well as before it. Only `[submit_started=false]`, which the capsule sends when
#: its actuator failed before clicking, lets a request be requeued.
SUBMIT_FALSE_MARKER = "[submit_started=false]"
SUBMIT_TRUE_MARKER = "[submit_started=true]"
SUBMIT_UNKNOWN_MARKER = "[submit_started=unknown]"
_MARKERS = (SUBMIT_FALSE_MARKER, SUBMIT_TRUE_MARKER, SUBMIT_UNKNOWN_MARKER)
#: One requeue per request. A second failure before the click is a pattern for a human.
MAX_REQUEUES = 1


def submit_marker(flag: bool | None) -> str:
    if flag is True:
        return SUBMIT_TRUE_MARKER
    if flag is False:
        return SUBMIT_FALSE_MARKER
    return SUBMIT_UNKNOWN_MARKER


def keep_marker(previous_detail: str | None, text: str) -> str:
    """`text` with the submit marker of `previous_detail` kept at its head, so a later note
    (reconciliation, observation) never erases what the actuator said."""
    head = next((m for m in _MARKERS if previous_detail and previous_detail.startswith(m)), "")
    return f"{head} {text}".strip()


class RequeueRefused(Exception):
    """The operator's requeue does not meet one of its conditions. Nothing changed."""


async def requeue_request(
    handlers: PublishHandlers,
    *,
    request_id: UUID,
    operator_id: UUID,
    now: datetime,
) -> PublicationRequest:
    """Put ONE UNKNOWN request back in the queue under a fresh consent, without bypassing the gate.

    A single-use grant cannot be issued twice for the same revision (`one_time_grant_hash` is
    UNIQUE), so a retry needs a new revision. The new revision is built from the reaction exactly
    as the first one was and must have the IDENTICAL `payload_hash`: same text, target, account,
    profile. The operator's decision on it names the original decision, the reconciliation read
    (zero comments by our account in the thread, and when) and the error class, so the founder's
    consent from the document (pinned by the draft's sha256) still covers what is published.

    Refused unless ALL hold: the request is UNKNOWN; its detail names a failure before anything
    was sent (`PRE_SUBMIT_MARKERS`); a live read of the thread shows no comment by our account;
    the recomputed digest equals the original revision's.
    """
    publication = handlers._publication
    approvals = handlers._approvals
    pilot = handlers._pilot
    organization_id = handlers._organization_id

    request = await publication.get_request(request_id)
    if request is None:
        raise RequeueRefused(f"nie ma wniosku {request_id}")
    if request.status != REQUEST_UNKNOWN:
        raise RequeueRefused(f"wniosek jest {request.status}, ponowić wolno tylko UNKNOWN")
    detail = request.detail or ""
    if not detail.startswith(SUBMIT_FALSE_MARKER):
        raise RequeueRefused(
            "wniosek nie ma znacznika aktuatora [submit_started=false]; bez potwierdzenia, że "
            "przycisk zapisu NIE został kliknięty, ponowienie jest zabronione (skutek mógł "
            f"zaistnieć): {detail[:160]!r}"
        )
    already = await publication.requeue_count(request.id)
    if already >= MAX_REQUEUES:
        raise RequeueRefused(
            f"wniosek był już ponawiany {already} raz; drugi błąd przed wysłaniem to sprawa dla "
            "człowieka, nie dla kolejnego ponowienia"
        )
    read = await handlers.thread_has_our_comment(request)
    if read is None:
        raise RequeueRefused(
            "rekoncyliacja odczytem nie powiodła się (wątek nieczytelny); bez niej nie ma "
            "ponowienia"
        )
    found, seen = read
    if found:
        raise RequeueRefused(
            "w wątku JEST komentarz naszego konta; wniosek pozostaje UNKNOWN do rekoncyliacji, "
            "nie do ponowienia"
        )

    original = await approvals.fetch_revision(
        request.proposal_revision_id, organization_id=organization_id
    )
    reaction = await pilot.get_reaction(request.reaction_id)
    if original is None or reaction is None:
        raise RequeueRefused("brak pierwotnej rewizji albo reakcji")
    from .publication import CONSENT_TTL, hash_input_for

    content = hash_input_for(
        reaction,
        organization_id=organization_id,
        workspace_id=request.workspace_id,
        account_id=original.hash_input.account,
    )
    if content.payload_hash() != original.payload_hash:
        raise RequeueRefused(
            f"digest nowej rewizji {content.payload_hash()[:12]} różni się od pierwotnego "
            f"{original.payload_hash[:12]}: to nie jest ta sama treść, potrzebna nowa zgoda"
        )

    from community_os_contracts.execution import ActionProposalState

    from control_plane.approval.store import APPROVED_DECISION

    proposal_id = await approvals.create_proposal(
        organization_id=organization_id,
        workspace_id=request.workspace_id,
        social_account_id=original.hash_input.account,
    )
    draft = await approvals.record_revision(
        organization_id=organization_id,
        workspace_id=request.workspace_id,
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=content,
        state=ActionProposalState.DRAFT,
        expires_at=now + CONSENT_TTL,
        source_observed_at=reaction.read_at,
    )
    await approvals.move_revision(
        draft.id,
        organization_id=organization_id,
        workspace_id=request.workspace_id,
        target=ActionProposalState.READY_FOR_APPROVAL,
        actor_ref=f"operator:{operator_id}",
    )
    reason = (
        f"ponowienie wniosku {request.id} po błędzie przed wysłaniem ({detail[:120]}); "
        f"pierwotna zgoda {request.approval_decision_id}; rekoncyliacja {now.isoformat()}: "
        f"0 komentarzy konta w wątku ({seen} komentarzy odczytanych); ta sama treść "
        f"(payload_hash {original.payload_hash[:12]})"
    )
    decision, approved = await approvals.record_decision(
        revision_id=draft.id,
        organization_id=organization_id,
        workspace_id=request.workspace_id,
        decision=APPROVED_DECISION,
        operator_id=operator_id,
        reason=reason,
    )
    return await publication.requeue(
        request.id,
        proposal_revision_id=approved.id,
        approval_decision_id=decision.id,
        detail=f"ponowione {now.isoformat()}: nowa rewizja {approved.id} tej samej treści",
    )


__all__ = [
    "JOB_PUBLISH",
    "MAX_REQUEUES",
    "PUBLISH_INTERVAL_SECONDS",
    "SUBMIT_FALSE_MARKER",
    "PublishHandlers",
    "PublishOutcome",
    "RequeueRefused",
    "ThreadFacts",
    "keep_marker",
    "nudge_issued_revision",
    "nudge_publish",
    "requeue_request",
    "schedule_publish",
    "submit_marker",
    "thread_facts",
]
