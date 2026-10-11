"""A reply and an own post on X, from the delegation to the look as a stranger (26.09).

What each test pins is a sentence of the X branch of `pilot.publisher`, measured against a real
database and a DUMB X source (it answers what it was told, it knows no selectors):

  * a delegation for X names no community and still issues the consent (`scoped_by_community`);
  * the reply goes out through the X port with the consented target, and the evidence is `x:<id>`;
  * the browser logged into ANOTHER account stops the queue; no session keeps it waiting;
  * a post that is gone and a post we already answered are skipped, never answered twice;
  * an unknown outcome is reconciled from our own profile, never retried;
  * the stranger's look has three answers kept apart: seen, gone, and "X would not show it".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from community_observer.source import CredentialsMissing, SourceUnavailable
from community_observer.x_browser import XPage, XPost, XSessionState
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
from control_plane.approval.store import ApprovalStore
from control_plane.execution.adapter import PublicationUnknown, SideEffectEvidence
from control_plane.execution.store import ExecutionStore
from control_plane.jobs.postgres_transport import PostgresJobTransport
from control_plane.jobs.store import JobStore
from control_plane.observe.store import ObservationStore
from control_plane.pilot.publication import PublicationStore
from control_plane.pilot.publisher import (
    OUTCOME_BLOCKED,
    OUTCOME_CAPSULE,
    OUTCOME_PUBLISHED,
    OUTCOME_SKIPPED,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNKNOWN,
    PublishHandlers,
)
from control_plane.pilot.store import PilotStore
from control_plane.presence.approval import ISSUED, DelegatedApprover
from control_plane.presence.scope import parse_budget, parse_scope
from control_plane.presence.store import PresenceStore

pytestmark = [pytest.mark.db]

ACCOUNT = "demo_agent"
POST_ID = "2103587050346250341"
POST_URL = f"https://x.com/jack/status/{POST_ID}"
OUR_ID = "2103999999999999999"
TEXT = "The boundary I would persist is proposal vs effect, with the resource version."
READY = {"mode": "INTERACTIVE", "display_only": True, "human_steer_open": False, "alive": True}


def now() -> datetime:
    return datetime.now(UTC)


def _page(*posts: XPost, **fields: Any) -> XPage:
    values: dict[str, Any] = {
        "requested_url": POST_URL,
        "final_url": POST_URL,
        "capability": "post",
        "anonymous": False,
        "viewer": ACCOUNT,
        "login_wall": False,
        "logged_in": True,
        "unavailable": None,
        "posts": tuple(posts),
        "profile": None,
        "text": "",
        "limitations": (),
    }
    values.update(fields)
    return XPage(**values)


def _post(post_id: str, author: str, *, relation: str | None, text: str = "x", **metrics) -> XPost:
    return XPost(
        id=post_id,
        author=author,
        author_name=author,
        text=text,
        text_source="post",
        permalink=f"https://x.com/{author}/status/{post_id}",
        published_at=None,
        relation=relation,
        metrics=dict(metrics),
    )


@dataclass
class DumbXSource:
    """Answers exactly what the test scripted. Records every read, so tests can count them."""

    identity: XSessionState
    pages: dict[tuple[str, bool], Any] = field(default_factory=dict)
    replies_page: XPage | None = None
    reads: list[tuple[str, bool]] = field(default_factory=list)
    prepared: int = 0

    async def prepare(self) -> None:
        self.prepared += 1

    async def verify_identity(self, expected: str) -> XSessionState:
        return self.identity

    async def read_post(self, url: str, *, anonymous: bool = False) -> XPage:
        self.reads.append((url, anonymous))
        # A page nobody scripted is a failed read, the way a real one fails: the reading half of
        # the publisher must survive it, and a KeyError would pretend it cannot happen.
        answer = self.pages.get((url, anonymous), SourceUnavailable(f"nieskryptowane: {url}"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def profile_replies(self, handle: str) -> XPage:
        self.reads.append((f"replies:{handle}", False))
        assert self.replies_page is not None
        return self.replies_page


@dataclass
class DumbPort:
    raise_with: Exception | None = None
    calls: list[ApprovalHashInput] = field(default_factory=list)

    async def submit(self, content: ApprovalHashInput) -> SideEffectEvidence:
        self.calls.append(content)
        if self.raise_with is not None:
            raise self.raise_with
        return SideEffectEvidence(
            external_id=f"x:{OUR_ID}",
            permalink=f"https://x.com/{ACCOUNT}/status/{OUR_ID}",
            account=ACCOUNT,
            observed_at=now(),
            raw={"post_id": OUR_ID, "evidence": "toast"},
        )


async def _seed(pool, tenant, *, actions=("REPLY", "OWN_POST")) -> dict[str, Any]:
    actions = list(actions)
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'rozmowy o granicy zatwierdzenia') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-x-{uuid4().hex[:8]}",
        )
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id, state) "
            "VALUES ($1, 'X', $2, 'READY') RETURNING id",
            tenant["organization"],
            f"{ACCOUNT}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    delegation = await PresenceStore(pool).sign(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        mode="AUTONOMOUS",
        # NO COMMUNITIES: on X the place is the account, and the scope is still a scope.
        scope=parse_scope({"accounts": [str(account_id)], "communities": [], "actions": actions}),
        budget=parse_budget(
            {"publications_per_day": 6, "publications_per_hour": 4, "own_posts_per_day": 2}
        ),
        operator_id=tenant["operator"],
        expires_at=now() + timedelta(days=2),
    )
    return {"project": project_id, "account": account_id, "delegation": delegation}


async def _propose(pool, tenant, seeded, *, kind: str = "REPLY", text: str = TEXT) -> UUID:
    store = ApprovalStore(pool)
    reply = kind == "REPLY"
    hash_input = ApprovalHashInput(
        organization=tenant["organization"],
        workspace=tenant["workspace"],
        assignment_version=uuid4(),
        account=seeded["account"],
        platform="X",
        action_type=SemanticCapability.SUBMIT_COMMENT if reply else SemanticCapability.SUBMIT_POST,
        target_external_id=f"x:{POST_ID}" if reply else f"x:@{ACCOUNT}",
        target_url=POST_URL if reply else f"https://x.com/{ACCOUNT}",
        source_fingerprint=f"session:{uuid4()}",
        text_and_media=text,
        intelligence_profile_version=uuid4(),
        plan_version=seeded["project"],
        memory_snapshot=uuid4(),
        adapter_version="wenext-session-draft/1",
    )
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=seeded["account"],
    )
    revision = await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=hash_input,
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=now() + timedelta(hours=6),
    )
    return revision.id


async def _issue(pool, tenant, seeded) -> list[Any]:
    return await DelegatedApprover(
        pool=pool,
        presence=PresenceStore(pool),
        approvals=ApprovalStore(pool),
        publication=PublicationStore(pool),
        organization_id=tenant["organization"],
    ).consider_pending(workspace_id=tenant["workspace"], project_id=seeded["project"], now=now())


def _publisher(
    pool,
    tenant,
    source: DumbXSource,
    port: DumbPort,
    own: DumbPort | None = None,
    *,
    reddit_state: dict[str, Any] | None = None,
    x_state: dict[str, Any] | None = None,
):
    async def capsule_state():
        return dict(reddit_state or READY)

    async def x_capsule_state():
        return dict(x_state)

    return PublishHandlers(
        store=ObservationStore(pool),
        pilot=PilotStore(pool),
        publication=PublicationStore(pool),
        approvals=ApprovalStore(pool),
        execution=ExecutionStore(pool),
        queue=PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"]),
        source=None,
        capsule_state=capsule_state,
        port_factory=None,
        organization_id=tenant["organization"],
        delegations=PresenceStore(pool),
        x_source=source,
        x_port_factory=lambda account: port,
        x_own_post_port_factory=(lambda account: own) if own is not None else None,
        x_capsule_state=x_capsule_state if x_state is not None else None,
    )


async def _request(pool, seeded) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM publication_requests WHERE project_id = $1 ORDER BY queued_at DESC",
            seeded["project"],
        )
    return dict(row)


def _logged_in() -> XSessionState:
    return XSessionState(True, ACCOUNT, now())


def _conversation(*extra: XPost) -> XPage:
    return _page(_post(POST_ID, "jack", relation="focal"), *extra)


async def test_a_delegation_for_x_issues_a_reply_without_any_community(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    considered = await _issue(pool, tenant, seeded)
    assert [one.outcome for one in considered] == [ISSUED], considered
    request = await _request(pool, seeded)
    assert request["target_external_id"] == f"x:{POST_ID}"
    assert request["status"] == "QUEUED"


async def test_the_reply_goes_out_through_the_x_port_with_evidence_read_back(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    port = DumbPort()

    outcome = await _publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert len(port.calls) == 1 and port.calls[0].target_url == POST_URL
    assert port.calls[0].text_and_media == TEXT
    request = await _request(pool, seeded)
    assert request["status"] == "PUBLISHED"
    evidence = await PublicationStore(pool).evidence_for(request["attempt_id"])
    assert evidence is not None and evidence["external_id"] == f"x:{OUR_ID}"


async def test_another_account_in_the_browser_stops_the_queue(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(XSessionState(True, "SomeoneElse", now()))
    port = DumbPort()

    outcome = await _publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_BLOCKED, outcome
    assert "@SomeoneElse" in outcome.detail
    assert port.calls == []
    assert (await _request(pool, seeded))["status"] == "BLOCKED"


async def test_a_lost_x_session_keeps_the_request_waiting(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(XSessionState(False, None, now(), error="ekran logowania"))
    port = DumbPort()

    outcome = await _publisher(pool, tenant, source, port).run_once(now())

    assert outcome.outcome == OUTCOME_UNAVAILABLE, outcome
    assert port.calls == []
    request = await _request(pool, seeded)
    assert request["status"] == "QUEUED" and "brak sesji X" in request["detail"]


async def test_a_post_that_is_gone_and_a_post_we_answered_are_skipped(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    gone = DumbXSource(_logged_in(), {(POST_URL, False): _page(unavailable="deleted")})
    port = DumbPort()
    outcome = await _publisher(pool, tenant, gone, port).run_once(now())
    assert outcome.outcome == OUTCOME_SKIPPED and "niedostępny" in outcome.detail
    assert port.calls == []

    await _propose(pool, tenant, seeded, text="another reply, same post")
    await _issue(pool, tenant, seeded)
    answered = DumbXSource(
        _logged_in(),
        {(POST_URL, False): _conversation(_post("555", ACCOUNT, relation="reply"))},
    )
    outcome = await _publisher(pool, tenant, answered, port).run_once(now())
    assert outcome.outcome == OUTCOME_SKIPPED and "nasza odpowiedź" in outcome.detail
    assert port.calls == [], "pod jednym postem nie odpowiadamy dwa razy"


async def test_an_unknown_outcome_is_reconciled_from_our_profile_never_retried(
    pool, tenant
) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    port = DumbPort(raise_with=PublicationUnknown("brak dowodu", submit_started=True))
    publisher = _publisher(pool, tenant, source, port)

    first = await publisher.run_once(now())
    assert first.outcome == OUTCOME_UNKNOWN, first
    assert len(port.calls) == 1

    source.replies_page = _page(
        _post(OUR_ID, ACCOUNT, relation=None, text=TEXT), capability="profile_replies"
    )
    later = now() + timedelta(minutes=6)
    await publisher.reconcile_only(later)

    request = await _request(pool, seeded)
    assert request["status"] == "PUBLISHED", request["detail"]
    assert len(port.calls) == 1, "rekoncyliacja czyta, nigdy nie wysyła drugi raz"
    evidence = await PublicationStore(pool).evidence_for(request["attempt_id"])
    assert evidence is not None and evidence["external_id"] == f"x:{OUR_ID}"


async def test_an_own_post_with_an_image_is_reconciled_on_its_text_alone(pool, tenant) -> None:
    """27.09: the consented field of a post with an image ends in the image line, which is never
    typed on X. Matching the whole field against what X shows would keep such a post UNKNOWN
    forever; the match is on the text the composer received."""
    from control_plane.pilot.publication import MediaRef, join_media

    seeded = await _seed(pool, tenant)
    caption = "Nowy model agentów. Najciekawsze jest to, czego nie pokazali."
    media = MediaRef(uuid4(), "sha256:" + "cd" * 32)
    await _propose(pool, tenant, seeded, kind="OWN_POST", text=join_media(caption, media))
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in())
    own = DumbPort(raise_with=PublicationUnknown("brak dowodu", submit_started=True))
    publisher = _publisher(pool, tenant, source, DumbPort(), own)

    first = await publisher.run_once(now())
    assert first.outcome == OUTCOME_UNKNOWN, first

    source.replies_page = _page(
        _post(OUR_ID, ACCOUNT, relation=None, text=caption), capability="profile_replies"
    )
    await publisher.reconcile_only(now() + timedelta(minutes=6))

    request = await _request(pool, seeded)
    assert request["status"] == "PUBLISHED", request["detail"]
    assert len(own.calls) == 1, "rekoncyliacja czyta, nigdy nie wysyła drugi raz"


async def _artifact(pool, tenant, directory, content: bytes, *, acl: str = "OPERATOR_AND_RUNNER"):
    from control_plane.storage.artifacts import ArtifactStore, ArtifactVolume
    from control_plane.storage.config import ArtifactAcl, ArtifactKind

    async with pool.acquire() as conn:
        return await ArtifactStore().record(
            conn,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            kind=ArtifactKind.SOURCE,
            acl=ArtifactAcl(acl),
            content=content,
            encrypted=False,
            retention_until=now() + timedelta(days=30),
            volume=ArtifactVolume(directory),
        )


async def test_the_publisher_reads_the_consented_image_and_refuses_any_other(
    pool, tenant, tmp_path
) -> None:
    """The loader behind the own post port: the approved bytes come back with their type; bytes
    that are not the approved ones, an artifact the runner may not read, a file that is not an
    image and an artifact that is gone are refusals saying nothing was sent."""
    from control_plane.execution.adapter import PublicationRefused
    from control_plane.observe.wiring import build_media_loader
    from control_plane.pilot.publication import MediaRef

    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
    env = {"WENEXT_ARTIFACTS_DIR": str(tmp_path)}
    load = build_media_loader(pool, organization_id=tenant["organization"], env=env)

    kept = await _artifact(pool, tenant, tmp_path, png)
    assert await load(MediaRef(kept.artifact_id, kept.checksum)) == (png, "image/png")

    with pytest.raises(PublicationRefused, match="różni się od zatwierdzonego"):
        await load(MediaRef(kept.artifact_id, "sha256:" + "00" * 32))

    private = await _artifact(pool, tenant, tmp_path, png + b"x", acl="OPERATOR_ONLY")
    with pytest.raises(PublicationRefused, match="nic nie wysłano"):
        await load(MediaRef(private.artifact_id, private.checksum))

    text = await _artifact(pool, tenant, tmp_path, b"<html>nie obraz</html>")
    with pytest.raises(PublicationRefused, match="nie jest obrazem"):
        await load(MediaRef(text.artifact_id, text.checksum))

    with pytest.raises(PublicationRefused, match="nie da się odczytać"):
        await load(MediaRef(uuid4(), kept.checksum))


async def test_the_strangers_look_keeps_seen_gone_and_not_shown_apart(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    ours = f"https://x.com/{ACCOUNT}/status/{OUR_ID}"
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    publisher = _publisher(pool, tenant, source, DumbPort())
    assert (await publisher.run_once(now())).outcome == OUTCOME_PUBLISHED
    request = await _request(pool, seeded)
    store = PublicationStore(pool)
    target = next(
        r
        for r in await store.requests(organization_id=tenant["organization"])
        if r.id == request["id"]
    )
    evidence = await store.evidence_for(request["attempt_id"])

    # 1. X shows a stranger only its login wall: no verdict.
    source.pages[(ours, True)] = _page(login_wall=True, anonymous=True, logged_in=False)
    await publisher._check_x_anonymously(target, evidence, now())
    assert (await _request(pool, seeded))["public_visibility"] == "UNVERIFIED"

    # 2. The post renders for a stranger with our id: seen.
    source.pages[(ours, True)] = _page(
        _post(OUR_ID, ACCOUNT, relation="focal", text=TEXT, likes=2), anonymous=True
    )
    await publisher._check_x_anonymously(target, evidence, now())
    assert (await _request(pool, seeded))["public_visibility"] == "CONFIRMED"

    # 3. X says the post is gone: it is OUR post that is gone.
    source.pages[(ours, True)] = _page(unavailable="deleted", anonymous=True, logged_in=False)
    await publisher._check_x_anonymously(target, evidence, now())
    after = await _request(pool, seeded)
    assert after["public_visibility"] == "REMOVED" and after["removal_scope"] == "COMMENT"


async def test_the_author_readback_records_counters_and_who_answered(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    ours = f"https://x.com/{ACCOUNT}/status/{OUR_ID}"
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    publisher = _publisher(pool, tenant, source, DumbPort())
    assert (await publisher.run_once(now())).outcome == OUTCOME_PUBLISHED
    request = await _request(pool, seeded)
    store = PublicationStore(pool)
    target = next(
        r
        for r in await store.requests(organization_id=tenant["organization"])
        if r.id == request["id"]
    )
    evidence = await store.evidence_for(request["attempt_id"])
    source.pages[(ours, False)] = _page(
        _post(OUR_ID, ACCOUNT, relation="focal", text=TEXT, likes=3, replies=1),
        _post("777", "jack", relation="reply", text="good point, how do you version it?"),
    )

    await publisher._observe_x(target, evidence, now())

    last = await store.latest_observation(request["id"])
    assert last is not None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT still_present, score, reply_count, raw, viewer FROM publication_observations "
            "WHERE request_id = $1 ORDER BY observed_at DESC LIMIT 1",
            request["id"],
        )
    assert row["still_present"] is True and row["score"] == 3 and row["reply_count"] == 1
    assert row["viewer"] == "AUTHOR"
    assert "how do you version it" in str(row["raw"])


async def _published_reply(pool, tenant):
    """A published X reply with its request, evidence and a scripted source, as the tests need."""
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    publisher = _publisher(pool, tenant, source, DumbPort())
    assert (await publisher.run_once(now())).outcome == OUTCOME_PUBLISHED
    request = await _request(pool, seeded)
    store = PublicationStore(pool)
    target = next(
        r
        for r in await store.requests(organization_id=tenant["organization"])
        if r.id == request["id"]
    )
    evidence = await store.evidence_for(request["attempt_id"])
    return seeded, source, publisher, target, evidence


async def test_a_reply_under_our_x_post_wakes_the_agent_once(pool, tenant) -> None:
    """Measured 27.09: a reply came under the project's X post and nothing woke the agent, because
    REPLY events were built only from Reddit's comments table."""
    from control_plane.presence.collector import EventCollector

    seeded, source, publisher, target, evidence = await _published_reply(pool, tenant)
    ours = f"https://x.com/{ACCOUNT}/status/{OUR_ID}"
    source.pages[(ours, False)] = _page(
        _post(OUR_ID, ACCOUNT, relation="focal", text=TEXT, replies=2),
        _post("777", "demo_partner", relation="reply", text="A czym się różni wiadomość od widoku?"),
        # Our own answer in the conversation is not a reply to us.
        _post("778", ACCOUNT, relation="reply", text="Widok pokazuje, co się zmieni."),
    )
    await publisher._observe_x(target, evidence, now())
    await publisher._observe_x(target, evidence, now() + timedelta(minutes=20))

    events = await EventCollector(pool).collect(
        seeded["delegation"], since=now() - timedelta(hours=1)
    )

    replies = [event for event in events if event.kind == "REPLY"]
    assert [event.event_key for event in replies] == ["REPLY:x:777"], replies
    assert replies[0].payload["autor"] == "@demo_partner"
    assert "wiadomość od widoku" in replies[0].payload["fragment"]
    assert replies[0].payload["pod_naszym_postem"] == ours
    assert replies[0].payload["odpowiedz"] == "https://x.com/demo_partner/status/777"


async def test_a_young_x_post_is_read_again_sooner_than_an_old_one(pool, tenant) -> None:
    """A reply under a fresh post is worth an answer while the conversation is on (27.09)."""
    _seeded, source, publisher, target, _evidence = await _published_reply(pool, tenant)
    ours = f"https://x.com/{ACCOUNT}/status/{OUR_ID}"
    source.pages[(ours, False)] = _page(_post(OUR_ID, ACCOUNT, relation="focal", text=TEXT))

    await publisher._observe_published(now())
    first = source.reads.count((ours, False))
    await publisher._observe_published(now() + timedelta(minutes=20))
    assert source.reads.count((ours, False)) == first + 1, "świeży post: odczyt po 20 minutach"

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET published_at = now() - interval '7 hours' "
            "WHERE id = $1",
            target.id,
        )
    before = source.reads.count((ours, False))
    await publisher._observe_published(now() + timedelta(minutes=40))
    assert source.reads.count((ours, False)) == before, "stary post: najwyżej raz na godzinę"


async def test_an_own_x_post_goes_through_its_own_port_once(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded, kind="OWN_POST", text="Question for agent builders.")
    considered = await _issue(pool, tenant, seeded)
    assert [one.outcome for one in considered] == [ISSUED], considered
    source = DumbXSource(_logged_in())
    reply_port, own_port = DumbPort(), DumbPort()

    outcome = await _publisher(pool, tenant, source, reply_port, own_port).run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert len(own_port.calls) == 1 and reply_port.calls == []
    assert own_port.calls[0].target_external_id == f"x:@{ACCOUNT}"


async def test_the_login_wall_while_reading_the_post_keeps_the_request_waiting(
    pool, tenant
) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): CredentialsMissing("ekran logowania X")})
    port = DumbPort()
    outcome = await _publisher(pool, tenant, source, port).run_once(now())
    assert outcome.outcome == OUTCOME_UNAVAILABLE and port.calls == []
    assert (await _request(pool, seeded))["status"] == "QUEUED"


async def test_x_waits_for_the_x_capsule_not_for_reddits(pool, tenant) -> None:
    """D-051: X has its own capsule. A person at Reddit's screen does not hold an X publication,
    and the X capsule is switched to headful before its state is read."""
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    port = DumbPort()
    reddit_busy = {**READY, "human_steer_open": True}

    outcome = await _publisher(
        pool, tenant, source, port, reddit_state=reddit_busy, x_state=dict(READY)
    ).run_once(now())

    assert outcome.outcome == OUTCOME_PUBLISHED, outcome
    assert source.prepared == 1, "kapsuła X przełączona, zanim publikator zapytał o jej stan"


async def test_a_headless_x_capsule_holds_the_x_publication_with_a_reason(pool, tenant) -> None:
    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    port = DumbPort()
    headless = {**READY, "mode": "HEADLESS", "display_only": False}

    outcome = await _publisher(pool, tenant, source, port, x_state=headless).run_once(now())

    assert outcome.outcome == OUTCOME_CAPSULE, outcome
    assert port.calls == [], "nic nie wyszło z kapsuły bez ekranu"
    request = await _request(pool, seeded)
    assert request["status"] == "QUEUED" and "HEADLESS" in (request["detail"] or "")


async def test_a_failed_tick_still_schedules_the_next_one(pool, tenant) -> None:
    """27.09: a tick raised before anything was sent (the actuator port refused `capsule-x`), the
    tick scheduled nothing, and the first X reply stood QUEUED with no publisher left to take it."""
    from types import SimpleNamespace

    seeded = await _seed(pool, tenant)
    await _propose(pool, tenant, seeded)
    await _issue(pool, tenant, seeded)
    source = DumbXSource(_logged_in(), {(POST_URL, False): _conversation()})
    handlers = _publisher(pool, tenant, source, DumbPort())

    def refusing_factory(account: str):
        raise ValueError("port aktuatora przyjmuje tylko http://<capsule>, nie capsule-x (D-027)")

    handlers._x_port_factory = refusing_factory
    with pytest.raises(ValueError, match="D-027"):
        await handlers.publish(SimpleNamespace(workspace_id=tenant["workspace"]))

    request = await _request(pool, seeded)
    assert request["status"] == "QUEUED", "nic nie wyszło"
    async with pool.acquire() as conn:
        scheduled = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE organization_id = $1 AND job_type = 'pilot.publish'",
            tenant["organization"],
        )
    assert scheduled == 1, "łańcuch publikatora przetrwał nieudany przebieg"
