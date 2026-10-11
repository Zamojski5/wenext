"""Milestone B of R4 minimal: the publication worker. No capsule and no Reddit here: a scripted
source answers `/api/me.json` and the thread JSON, a fake capsule state answers `/state`, and a fake
port stands where `RedditCapsuleCommentPort` would talk to the actuator. What is measured is the
order of the preconditions, the pace, the three outcomes of a write, and that nothing is ever
published twice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from community_observer import CredentialsMissing, ThreadNotFound
from community_observer.reddit_browser import SessionState
from community_os_contracts.enums import ExecutionAttemptState
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.domain.models import ClaimedJob
from control_plane.execution.adapter import (
    PublicationBlocked,
    PublicationRefused,
    PublicationUnknown,
    SideEffectEvidence,
)
from control_plane.execution.store import ExecutionStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.store import ObservationStore
from control_plane.pilot.publication import PublicationStore, approve_consents
from control_plane.pilot.publisher import (
    JOB_PUBLISH,
    OUTCOME_BLOCKED,
    OUTCOME_CAPSULE,
    OUTCOME_NO_REQUEST,
    OUTCOME_PACED,
    OUTCOME_PUBLISHED,
    OUTCOME_SKIPPED,
    OUTCOME_UNKNOWN,
    PublishHandlers,
    thread_facts,
)
from control_plane.pilot.store import PilotStore
from test_pilot_publication import consent_for, seeded_draft

pytestmark = pytest.mark.db

ACCOUNT = "Demo_Maintainer"
READY = {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": False, "alive": True}


def thread_json(
    post_id: str,
    *,
    comments: list[dict[str, Any]] | None = None,
    locked: bool = False,
    archived: bool = False,
) -> list[dict[str, Any]]:
    """The shape Reddit returns for `/r/<sub>/comments/<id>.json`: a post listing and a comment
    listing. Enough of it for `parse_thread_pair` and `thread_facts`."""
    post = {
        "kind": "t3",
        "data": {
            "id": post_id,
            "name": f"t3_{post_id}",
            "subreddit": "kubernetes",
            "title": f"Post {post_id}",
            "selftext": "the question body",
            "author": "op",
            "created_utc": 1_788_800_000,
            "permalink": f"/r/kubernetes/comments/{post_id}/a_post/",
            "url": f"https://www.reddit.com/r/kubernetes/comments/{post_id}/a_post/",
            "score": 3,
            "num_comments": len(comments or []),
            "locked": locked,
            "archived": archived,
        },
    }
    children = []
    for n, c in enumerate(comments or []):
        children.append(
            {
                "kind": "t1",
                "data": {
                    "id": c.get("id", f"c{n}"),
                    "name": f"t1_{c.get('id', f'c{n}')}",
                    "parent_id": c.get("parent_id", f"t3_{post_id}"),
                    "author": c.get("author", "someone"),
                    "body": c.get("body", "a comment"),
                    "created_utc": 1_788_800_600 + n,
                    "score": c.get("score", 1),
                    "depth": 0 if c.get("parent_id", f"t3_{post_id}").startswith("t3_") else 1,
                    "permalink": f"/r/kubernetes/comments/{post_id}/a_post/{c.get('id', f'c{n}')}/",
                    "replies": "",
                },
            }
        )
    return [
        {"kind": "Listing", "data": {"children": [post]}},
        {"kind": "Listing", "data": {"children": children}},
    ]


class ScriptedRedditSource:
    def __init__(self, *, username: str | None = ACCOUNT) -> None:
        self.username = username
        self.threads: dict[str, list[dict[str, Any]]] = {}
        self.reads: list[str] = []
        self.session_error: Exception | None = None

    async def session_state(self) -> SessionState:
        if self.session_error is not None:
            raise self.session_error
        return SessionState(
            logged_in=self.username is not None,
            username=self.username,
            checked_at=datetime.now(UTC),
        )

    async def read_thread_payload(self, community_key: str, external_id: str) -> Any:
        self.reads.append(external_id)
        if external_id not in self.threads:
            raise ThreadNotFound(external_id)
        return self.threads[external_id]


class FakePort:
    def __init__(self) -> None:
        self.calls: list[ApprovalHashInput] = []
        self.raise_with: Exception | None = None
        self.evidence = SideEffectEvidence(
            external_id="t1_ours01",
            permalink="https://www.reddit.com/r/kubernetes/comments/x/a_post/ours01/",
            account=ACCOUNT,
            observed_at=datetime.now(UTC),
            raw={"from": "fake"},
        )

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        self.calls.append(content)
        if self.raise_with is not None:
            raise self.raise_with
        return self.evidence


@dataclass
class World:
    handlers: PublishHandlers
    source: ScriptedRedditSource
    port: FakePort
    capsule: dict[str, Any]
    publication: PublicationStore
    approvals: ApprovalStore
    execution: ExecutionStore


async def world(pool, tenant, *, port: FakePort | None = None, source=None) -> World:
    source = source or ScriptedRedditSource()
    port = port or FakePort()
    capsule = dict(READY)

    async def capsule_state():
        return dict(capsule)

    handlers = PublishHandlers(
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
    )
    return World(
        handlers,
        source,
        port,
        capsule,
        PublicationStore(pool),
        ApprovalStore(pool),
        ExecutionStore(pool),
    )


async def approved(pool, tenant, *drafts: tuple[str, str]):
    """Seed drafts and record their consents in document order. Returns the reactions."""
    reactions = []
    for external_id, draft in drafts:
        _, reaction = await seeded_draft(pool, tenant, external_id=external_id, draft=draft)
        reactions.append(reaction)
    await approve_consents(
        pool,
        rows=tuple(consent_for(r, i + 1) for i, r in enumerate(reactions)),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_handle=ACCOUNT,
        consent_source="test consent",
        operator_id=tenant["operator"],
        now=datetime.now(UTC),
    )
    return reactions


def test_thread_facts_flattens_replies_and_reads_the_flags() -> None:
    payload = thread_json(
        "1abc",
        comments=[{"id": "a", "author": "x"}, {"id": "b", "author": ACCOUNT, "parent_id": "t1_a"}],
        locked=True,
    )
    facts = thread_facts(payload)
    assert facts.locked and not facts.archived and facts.post_fullname == "t3_1abc"
    assert [c["name"] for c in facts.comments] == ["t1_a", "t1_b"]
    assert [c["name"] for c in facts.by_author("demo_maintainer")] == ["t1_b"]
    assert [c["name"] for c in facts.replies_to("t1_a")] == ["t1_b"]
    assert facts.by_fullname("t1_zzz") is None


async def test_the_first_consent_is_published_through_the_intent_gate_with_evidence(
    pool, tenant
) -> None:
    (reaction,) = await approved(pool, tenant, ("1pub001", "Published text one."))
    w = await world(pool, tenant)
    w.source.threads["1pub001"] = thread_json("1pub001", comments=[{"id": "k1"}])
    now = datetime.now(UTC)

    result = await w.handlers.run_once(now)

    assert result.outcome == OUTCOME_PUBLISHED, result
    assert len(w.port.calls) == 1
    content = w.port.calls[0]
    assert content.text_and_media == "Published text one."
    assert content.target_external_id == "t3_1pub001"
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "PUBLISHED"
    assert request.intent_id is not None and request.attempt_id is not None
    attempt = await w.execution.fetch_run(
        request.attempt_id, organization_id=tenant["organization"]
    )
    assert attempt is not None and attempt.state is ExecutionAttemptState.CONFIRMED
    evidence = await w.publication.evidence_for(request.attempt_id)
    assert evidence is not None and evidence["external_id"] == "t1_ours01"
    assert evidence["source"] == "ACTUATOR" and evidence["account"] == ACCOUNT
    intent = await w.approvals.fetch_intent(
        request.intent_id, organization_id=tenant["organization"]
    )
    assert intent is not None and intent.consumed_at is not None, "grant spalony"

    # A second tick in the same hour: the pace holds the next request back, nothing is written.
    (second,) = await approved(pool, tenant, ("1pub002", "Published text two."))
    w.source.threads["1pub002"] = thread_json("1pub002", comments=[{"id": "k2"}])
    paced = await w.handlers.run_once(now + timedelta(minutes=5))
    assert paced.outcome == OUTCOME_PACED and len(w.port.calls) == 1
    waiting = await w.publication.request_for_reaction(second.id)
    assert waiting is not None and waiting.status == "QUEUED" and "tempo" in (waiting.detail or "")


async def test_without_display_only_or_with_a_foreign_session_nothing_is_written(
    pool, tenant
) -> None:
    (reaction,) = await approved(pool, tenant, ("1cap001", "Capsule text."))
    w = await world(pool, tenant)
    w.source.threads["1cap001"] = thread_json("1cap001", comments=[{"id": "k"}])
    now = datetime.now(UTC)

    w.capsule.update({"human_steer_open": True, "display_only": False})
    result = await w.handlers.run_once(now)
    assert result.outcome == OUTCOME_CAPSULE and w.port.calls == []
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "QUEUED", "wniosek czeka, nie znika"

    # The capsule is fine again, but the page belongs to another account: the queue STOPS.
    w.capsule.update(READY)
    w.source.username = "SomebodyElse"
    result = await w.handlers.run_once(now)
    assert result.outcome == OUTCOME_BLOCKED and w.port.calls == []
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "BLOCKED"
    run = await PilotStore(pool).get_run(reaction.run_id)
    assert run is not None and run.publishing_stopped_at is not None
    assert [e["kind"] for e in run.host_events] == ["PUBLISHING_BLOCKED"]


#: SCENARIUSZ 4 P0: komentarz naszego konta już w wątku wyklucza drugi, także po restarcie.
@pytest.mark.p0
async def test_a_locked_thread_or_an_own_comment_already_there_skips_without_a_write(
    pool, tenant
) -> None:
    first, second = await approved(
        pool, tenant, ("1skp001", "Skip text one."), ("1skp002", "Skip text two.")
    )
    w = await world(pool, tenant)
    w.source.threads["1skp001"] = thread_json("1skp001", comments=[{"id": "k"}], locked=True)
    w.source.threads["1skp002"] = thread_json(
        "1skp002", comments=[{"id": "m", "author": ACCOUNT, "body": "earlier by us"}]
    )
    now = datetime.now(UTC)

    a = await w.handlers.run_once(now)
    b = await w.handlers.run_once(now)
    c = await w.handlers.run_once(now)

    assert (a.outcome, b.outcome, c.outcome) == (
        OUTCOME_SKIPPED,
        OUTCOME_SKIPPED,
        OUTCOME_NO_REQUEST,
    )
    assert w.port.calls == []
    r1 = await w.publication.request_for_reaction(first.id)
    r2 = await w.publication.request_for_reaction(second.id)
    assert r1 is not None and r1.status == "SKIPPED" and "zablokowany" in (r1.detail or "")
    assert r2 is not None and r2.status == "SKIPPED" and "naszego konta" in (r2.detail or "")


async def test_an_edited_consent_is_refused_at_the_grant_and_skipped(pool, tenant) -> None:
    (reaction,) = await approved(pool, tenant, ("1edt001", "Approved wording."))
    w = await world(pool, tenant)
    w.source.threads["1edt001"] = thread_json("1edt001", comments=[{"id": "k"}])
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None
    revision = await w.approvals.fetch_revision(
        request.proposal_revision_id, organization_id=tenant["organization"]
    )
    assert revision is not None
    await w.approvals.edit_into_new_revision(
        revision_id=revision.id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=revision.hash_input.model_copy(update={"text_and_media": "Edited wording."}),
        actor_ref="operator:test",
    )

    result = await w.handlers.run_once(datetime.now(UTC))

    assert result.outcome == OUTCOME_SKIPPED and w.port.calls == []
    after = await w.publication.request_for_reaction(reaction.id)
    assert after is not None and after.status == "SKIPPED"
    assert "nie autoryzuje" in (after.detail or "")
    assert revision.state is ActionProposalState.APPROVED, "obiekt sprzed edycji nic nie znaczy"


#: SCENARIUSZ 4 P0: po SUBMIT_STARTED bez wyniku nie ma ponownej wysyłki, jest rekoncyliacja.
@pytest.mark.p0
async def test_no_evidence_after_submit_started_is_unknown_and_never_republished(
    pool, tenant
) -> None:
    (reaction,) = await approved(pool, tenant, ("1unk001", "Unknown outcome text."))
    port = FakePort()
    port.raise_with = PublicationUnknown("capsule timed out")
    w = await world(pool, tenant, port=port)
    w.source.threads["1unk001"] = thread_json("1unk001", comments=[{"id": "k"}])
    now = datetime.now(UTC)

    result = await w.handlers.run_once(now)

    assert result.outcome == OUTCOME_UNKNOWN and len(port.calls) == 1
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "UNKNOWN" and request.attempt_id is not None
    attempt = await w.execution.fetch_run(
        request.attempt_id, organization_id=tenant["organization"]
    )
    assert attempt is not None and attempt.state is ExecutionAttemptState.UNKNOWN
    assert not attempt.may_retry()

    # Next ticks: the thread shows no comment by us yet. Nothing is sent again.
    port.raise_with = None
    later = await w.handlers.run_once(now + timedelta(minutes=6))
    assert later.outcome == OUTCOME_NO_REQUEST and len(port.calls) == 1
    still = await w.publication.request_for_reaction(reaction.id)
    assert (
        still is not None
        and still.status == "UNKNOWN"
        and "brak komentarza" in (still.detail or "")
    )

    # Then the thread shows our comment with the consented text: reconciled from a READ, not a retry.
    w.source.threads["1unk001"] = thread_json(
        "1unk001",
        comments=[
            {"id": "k"},
            {"id": "ours9", "author": ACCOUNT, "body": "Unknown outcome text.", "score": 4},
        ],
    )
    reconciled = await w.handlers.run_once(now + timedelta(minutes=12))
    assert reconciled.outcome == OUTCOME_NO_REQUEST and len(port.calls) == 1
    done = await w.publication.request_for_reaction(reaction.id)
    assert done is not None and done.status == "PUBLISHED"
    evidence = await w.publication.evidence_for(request.attempt_id)
    assert evidence is not None and evidence["external_id"] == "t1_ours9"
    assert evidence["source"] == "RECONCILIATION"
    # And the observation of our comment (score, replies) is recorded on the following tick.
    observed = await w.handlers.run_once(now + timedelta(minutes=70))
    assert observed.outcome == OUTCOME_NO_REQUEST
    observation = await w.publication.latest_observation(done.id)
    assert observation is not None and observation["still_present"] and observation["score"] == 4


async def test_a_platform_block_stops_the_whole_queue(pool, tenant) -> None:
    first, second = await approved(
        pool, tenant, ("1blk001", "Blocked text one."), ("1blk002", "Blocked text two.")
    )
    port = FakePort()
    port.raise_with = PublicationBlocked("you are doing that too much")
    w = await world(pool, tenant, port=port)
    w.source.threads["1blk001"] = thread_json("1blk001", comments=[{"id": "k"}])
    w.source.threads["1blk002"] = thread_json("1blk002", comments=[{"id": "k"}])
    now = datetime.now(UTC)

    result = await w.handlers.run_once(now)

    assert result.outcome == OUTCOME_BLOCKED and len(port.calls) == 1
    r1 = await w.publication.request_for_reaction(first.id)
    r2 = await w.publication.request_for_reaction(second.id)
    assert r1 is not None and r1.status == "BLOCKED"
    assert r2 is not None and r2.status == "STOPPED", "reszta kolejki stoi, nie próbuje"
    port.raise_with = None
    again = await w.handlers.run_once(now + timedelta(hours=2))
    assert again.outcome == OUTCOME_NO_REQUEST and len(port.calls) == 1


async def test_the_youngest_thread_is_published_first_whatever_the_consent_position(
    pool, tenant
) -> None:
    """D-030: a comment is read while the post is on `new`. The queue serves the request whose
    thread the platform dates youngest, and falls back to document order only between threads of
    the same age; the consent that came first in the document is NOT first out when its post is
    older."""
    older, younger, same_age = await approved(
        pool,
        tenant,
        ("1old001", "On the older post."),
        ("1new001", "On the post from ten minutes ago."),
        ("1new002", "Same age as the first, later in the document."),
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE observed_threads SET created_at_source = now() - interval '10 minutes' "
            "WHERE id = $1",
            younger.thread_id,
        )
    w = await world(pool, tenant)
    for external_id in ("1old001", "1new001", "1new002"):
        w.source.threads[external_id] = thread_json(external_id, comments=[{"id": "k"}])

    first = await w.handlers.run_once(datetime.now(UTC))

    assert first.outcome == OUTCOME_PUBLISHED and first.request_id is not None
    assert (await w.publication.request_for_reaction(younger.id)).id == first.request_id, (
        "najmłodszy wątek pierwszy, choć jego zgoda była druga w dokumencie"
    )
    queued = await w.publication.next_queued(organization_id=tenant["organization"])
    assert queued is not None and queued.reaction_id == older.id, (
        "między wątkami w tym samym wieku decyduje pozycja w dokumencie"
    )
    assert (await w.publication.request_for_reaction(same_age.id)).status == "QUEUED"


async def test_an_actuator_refusal_costs_a_pace_slot_because_the_page_was_opened(
    pool, tenant
) -> None:
    """D-035, from an incident: the actuator opens the thread and only then decides, so its refusal
    is a page load Reddit saw. It used to be free, and eleven runs cycling at the same second put
    four page loads on the account in ninety seconds under a policy of two an hour, all answered
    403. A refusal by the actuator now costs a slot like a publication does.

    A refusal decided BEFORE the intent (a locked thread, our comment already there) stays free:
    there is no attempt then, and the publisher never went to the actuator at all.
    """
    first, second = await approved(
        pool, tenant, ("1pac001", "First draft."), ("1pac002", "Second draft.")
    )
    w = await world(pool, tenant)
    w.source.threads["1pac001"] = thread_json("1pac001", comments=[{"id": "k"}])
    w.source.threads["1pac002"] = thread_json("1pac002", comments=[{"id": "k"}])
    w.port.raise_with = PublicationRefused("aktuator odmówił (409): wątek odpowiedział 403")
    now = datetime.now(UTC)

    refused = await w.handlers.run_once(now)

    assert refused.outcome == OUTCOME_SKIPPED, refused
    request = await w.publication.request_for_reaction(first.id)
    assert request is not None and request.status == "SKIPPED"
    assert request.attempt_id is not None, "próba istnieje: aktuator był wołany"
    assert len(w.port.calls) == 1, "aktuator otworzył stronę i dopiero wtedy odmówił"

    # The manifest allows one publication an hour, and the refusal already used that hour's slot.
    w.port.raise_with = None
    paced = await w.handlers.run_once(now + timedelta(minutes=1))

    assert paced.outcome == "PACED", paced
    assert len(w.port.calls) == 1, "po odmowie nic nowego nie idzie do aktuatora w tej godzinie"
    assert (await w.publication.request_for_reaction(second.id)).status == "QUEUED"


async def test_the_job_chains_itself_while_anything_is_left(pool, tenant) -> None:
    (reaction,) = await approved(pool, tenant, ("1job001", "Job text."))
    w = await world(pool, tenant)
    w.source.threads["1job001"] = thread_json("1job001", comments=[{"id": "k"}])
    claim = ClaimedJob(
        job_id=UUID(int=41),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        job_type=JOB_PUBLISH,
        routing_key="observe",
        priority=5,
        payload_ref=f"publish/{tenant['organization']}",
        dispatch_version=1,
        attempt_limit=3,
        attempt_id=UUID(int=42),
        attempt_no=1,
        lease_generation=1,
        lease_owner=UUID(int=43),
        expires_at=None,
    )

    await w.handlers.publish(claim)

    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "PUBLISHED"
    async with pool.acquire() as conn:
        pending = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = $2 "
            "AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')",
            tenant["organization"],
            JOB_PUBLISH,
        )
    assert pending == 1, "następny tik zlecony, bo są opublikowane wnioski do obserwacji"


async def test_a_session_read_failure_leaves_the_request_queued(pool, tenant) -> None:
    (reaction,) = await approved(pool, tenant, ("1ses001", "Session text."))
    w = await world(pool, tenant)
    w.source.session_error = CredentialsMissing("reader down")
    result = await w.handlers.run_once(datetime.now(UTC))
    assert result.outcome == "PLATFORM_UNAVAILABLE" and w.port.calls == []
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "QUEUED"


async def test_a_finished_run_still_publishes_but_a_stopped_run_does_not(pool, tenant) -> None:
    """The pilot measured drafting for 24 h; publication of what it approved may come the day
    after. Only the human's stop (pilot-stop, publish-stop) closes the queue, not the clock."""
    first, second = await approved(
        pool, tenant, ("1fin001", "After the window one."), ("1fin002", "After the window two.")
    )
    pilot = PilotStore(pool)
    await pilot.finish_run(first.run_id, status="FINISHED", at=datetime.now(UTC))
    w = await world(pool, tenant)
    w.source.threads["1fin001"] = thread_json("1fin001", comments=[{"id": "k"}])
    w.source.threads["1fin002"] = thread_json("1fin002", comments=[{"id": "k"}])

    result = await w.handlers.run_once(datetime.now(UTC))
    assert result.outcome == OUTCOME_PUBLISHED and len(w.port.calls) == 1

    # The human stops the pilot by hand: the rest of the queue is closed, nothing more is written.
    await w.publication.stop_publishing(
        organization_id=tenant["organization"], at=datetime.now(UTC)
    )
    later = await w.handlers.run_once(datetime.now(UTC) + timedelta(hours=2))
    assert later.outcome == "STOPPED" and len(w.port.calls) == 1
    rest = await w.publication.request_for_reaction(second.id)
    assert rest is not None and rest.status == "STOPPED"


# -- the operator's requeue after a failure BEFORE anything was sent ------------------------------


async def _unknown_before_submit(
    pool, tenant, w: World, reaction, *, detail: str, submit_started: bool | None = False
):
    """Drive one request into UNKNOWN through the real path: the port raises with the actuator's
    own flag, and the worker writes the marker into the request's detail."""
    from control_plane.execution.adapter import PublicationUnknown

    # Several attempts inside one test hour: the pace is a manifest parameter, so raise it here.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pilot_runs SET max_publications_per_hour = 10, max_publications_per_day = 60 "
            "WHERE id = $1",
            reaction.run_id,
        )
    w.port.raise_with = PublicationUnknown(detail, submit_started=submit_started)
    result = await w.handlers.run_once(datetime.now(UTC))
    assert result.outcome == OUTCOME_UNKNOWN, result
    w.port.raise_with = None
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "UNKNOWN"
    return request


#: SCENARIUSZ 4 P0: ponowienie tylko przed wysyłką, po czystym odczycie i na tym samym odcisku.
@pytest.mark.p0
async def test_requeue_after_a_pre_submit_failure_needs_a_clean_read_and_the_same_digest(
    pool, tenant
) -> None:
    from control_plane.pilot.publisher import requeue_request

    (reaction,) = await approved(pool, tenant, ("1req001", "Requeue text."))
    w = await world(pool, tenant)
    w.source.threads["1req001"] = thread_json("1req001", comments=[{"id": "k"}])
    request = await _unknown_before_submit(
        pool,
        tenant,
        w,
        reaction,
        detail="aktuator odpowiedział 502: Playwright Sync API inside loop",
    )
    original_revision = request.proposal_revision_id
    original_decision = request.approval_decision_id
    now = datetime.now(UTC)

    requeued = await requeue_request(
        w.handlers, request_id=request.id, operator_id=tenant["operator"], now=now
    )

    assert (
        requeued.status == "QUEUED" and requeued.intent_id is None and requeued.attempt_id is None
    )
    assert requeued.proposal_revision_id != original_revision
    new_revision = await w.approvals.fetch_revision(
        requeued.proposal_revision_id, organization_id=tenant["organization"]
    )
    old_revision = await w.approvals.fetch_revision(
        original_revision, organization_id=tenant["organization"]
    )
    assert new_revision is not None and old_revision is not None
    assert new_revision.payload_hash == old_revision.payload_hash, "ta sama treść, ten sam digest"
    assert new_revision.state is ActionProposalState.APPROVED
    approval = await w.approvals.find_approval(
        revision_id=new_revision.id,
        payload_hash=new_revision.payload_hash,
        organization_id=tenant["organization"],
    )
    assert approval is not None and str(original_decision) in (approval.reason or "")
    assert "0 komentarzy konta" in (approval.reason or "")

    # The next tick publishes it through a FRESH grant; the old intent stays consumed history.
    w.source.threads["1req001"] = thread_json("1req001", comments=[{"id": "k"}])
    published = await w.handlers.run_once(now + timedelta(hours=2))
    assert published.outcome == OUTCOME_PUBLISHED and len(w.port.calls) == 2
    done = await w.publication.request_for_reaction(reaction.id)
    assert done is not None and done.status == "PUBLISHED" and done.intent_id != request.intent_id


#: SCENARIUSZ 4 P0: nasz komentarz w wątku albo błąd po wysyłce zamykają drogę do ponowienia.
@pytest.mark.p0
async def test_requeue_is_refused_when_our_comment_is_in_the_thread_or_the_error_was_not_pre_submit(
    pool, tenant
) -> None:
    from control_plane.pilot.publisher import RequeueRefused, requeue_request

    first, second = await approved(
        pool, tenant, ("1ref001", "Refuse text one."), ("1ref002", "Refuse text two.")
    )
    w = await world(pool, tenant)
    w.source.threads["1ref001"] = thread_json("1ref001", comments=[{"id": "k"}])
    w.source.threads["1ref002"] = thread_json("1ref002", comments=[{"id": "k"}])
    now = datetime.now(UTC)

    # (a) UNKNOWN after the click: a genuine unknown, never requeued.
    genuine = await _unknown_before_submit(
        pool,
        tenant,
        w,
        first,
        detail="po kliknięciu zapisu nie pojawił się nowy komentarz",
        submit_started=True,
    )
    with pytest.raises(RequeueRefused, match="znacznika"):
        await requeue_request(
            w.handlers, request_id=genuine.id, operator_id=tenant["operator"], now=now
        )

    # (b) A pre-submit error, but the thread already shows a comment by our account.
    w.source.threads["1ref002"] = thread_json("1ref002", comments=[{"id": "k"}])
    pre = await _unknown_before_submit(
        pool, tenant, w, second, detail="aktuator odpowiedział 502: cdp gone"
    )
    w.source.threads["1ref002"] = thread_json(
        "1ref002",
        comments=[{"id": "k"}, {"id": "m", "author": ACCOUNT, "body": "Refuse text two."}],
    )
    with pytest.raises(RequeueRefused, match="JEST komentarz"):
        await requeue_request(
            w.handlers, request_id=pre.id, operator_id=tenant["operator"], now=now
        )

    # (c) The thread cannot be read at all: no reconciliation, no requeue.
    del w.source.threads["1ref002"]
    with pytest.raises(RequeueRefused, match="nieczytelny"):
        await requeue_request(
            w.handlers, request_id=pre.id, operator_id=tenant["operator"], now=now
        )

    # (d) A request that is not UNKNOWN is never requeued.
    queued = await w.publication.request_for_reaction(first.id)
    assert queued is not None
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET status = 'SKIPPED' WHERE id = $1", queued.id
        )
    with pytest.raises(RequeueRefused, match="tylko UNKNOWN"):
        await requeue_request(
            w.handlers, request_id=queued.id, operator_id=tenant["operator"], now=now
        )
    assert len(w.port.calls) == 2, "żadne ponowienie nie dotknęło portu"


async def test_requeue_refuses_a_digest_that_differs_from_the_original(pool, tenant) -> None:
    from control_plane.pilot.publisher import RequeueRefused, requeue_request

    (reaction,) = await approved(pool, tenant, ("1dig001", "Digest text."))
    w = await world(pool, tenant)
    w.source.threads["1dig001"] = thread_json("1dig001", comments=[{"id": "k"}])
    request = await _unknown_before_submit(
        pool, tenant, w, reaction, detail="aktuator odmówił (409): strona nie pokazuje konta"
    )
    # The account the consent named is renamed underneath: the recomputed digest would differ.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE observed_threads SET url = url || 'moved/' WHERE id = $1", reaction.thread_id
        )
    with pytest.raises(RequeueRefused, match="digest"):
        await requeue_request(
            w.handlers, request_id=request.id, operator_id=tenant["operator"], now=datetime.now(UTC)
        )
    still = await w.publication.request_for_reaction(reaction.id)
    assert still is not None and still.status == "UNKNOWN"


async def test_requeue_needs_the_actuators_own_false_flag_not_an_error_text(pool, tenant) -> None:
    """A 502 or a client timeout can happen after the click as well as before it. Only the
    capsule's explicit `submit_started=false` opens the requeue; "nobody knows" keeps it shut."""
    from control_plane.pilot.publisher import RequeueRefused, requeue_request

    (reaction,) = await approved(pool, tenant, ("1flg001", "Flag text."))
    w = await world(pool, tenant)
    w.source.threads["1flg001"] = thread_json("1flg001", comments=[{"id": "k"}])
    request = await _unknown_before_submit(
        pool,
        tenant,
        w,
        reaction,
        detail="aktuator odpowiedział 502: connection reset",
        submit_started=None,
    )
    assert (request.detail or "").startswith("[submit_started=unknown]")
    with pytest.raises(RequeueRefused, match="znacznika"):
        await requeue_request(
            w.handlers, request_id=request.id, operator_id=tenant["operator"], now=datetime.now(UTC)
        )
    # Reconciliation notes keep the marker at the head instead of erasing it.
    await w.handlers.reconcile_only(datetime.now(UTC) + timedelta(minutes=6))
    after = await w.publication.request_for_reaction(reaction.id)
    assert after is not None and (after.detail or "").startswith("[submit_started=unknown]")
    assert "brak komentarza" in (after.detail or "")


#: SCENARIUSZ 4 P0: ponowienie najwyżej raz, więc pętla nie zamienia się w serię publikacji.
@pytest.mark.p0
async def test_a_request_is_requeued_at_most_once(pool, tenant) -> None:
    from control_plane.pilot.publisher import RequeueRefused, requeue_request

    (reaction,) = await approved(pool, tenant, ("1lim001", "Limit text."))
    w = await world(pool, tenant)
    w.source.threads["1lim001"] = thread_json("1lim001", comments=[{"id": "k"}])
    first = await _unknown_before_submit(
        pool, tenant, w, reaction, detail="aktuator odmówił (409): brak formularza"
    )
    requeued = await requeue_request(
        w.handlers, request_id=first.id, operator_id=tenant["operator"], now=datetime.now(UTC)
    )
    assert requeued.status == "QUEUED"
    second = await _unknown_before_submit(
        pool, tenant, w, reaction, detail="aktuator odmówił (409): brak formularza"
    )
    with pytest.raises(RequeueRefused, match="już ponawiany"):
        await requeue_request(
            w.handlers, request_id=second.id, operator_id=tenant["operator"], now=datetime.now(UTC)
        )


async def test_a_publication_starts_the_chain_that_will_measure_its_result(pool, tenant) -> None:
    """P8 etap B: po publikacji ma stanąć w kolejce pierwsze przemiatanie doświadczeń.

    Dotąd łańcuch startował wyłącznie po kliknięciu człowieka, więc skutki publikacji nie
    trafiały do pamięci projektu, dopóki ktoś o nie nie poprosił. Mierzone tu, a nie na atrapie
    kolejki, bo pytanie brzmi „czy w `jobs` jest wiersz", a to jest fakt o bazie.
    """
    from control_plane.knowledge.handlers import JOB_EXPERIENCE

    await approved(pool, tenant, ("1pub009", "Tekst do zmierzenia."))
    w = await world(pool, tenant)
    w.source.threads["1pub009"] = thread_json("1pub009", comments=[{"id": "k9"}])

    result = await w.handlers.run_once(datetime.now(UTC))

    assert result.outcome == OUTCOME_PUBLISHED, result
    async with pool.acquire() as conn:
        sweeps = await conn.fetch(
            "SELECT idempotency_key FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_EXPERIENCE,
        )
    assert len(sweeps) == 1, "publikacja nie zamówiła pierwszego przemiatania doświadczeń"
