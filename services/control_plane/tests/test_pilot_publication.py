"""Milestone A of R4 minimal: the human's consent (a table in a Markdown document) lands in
the EXISTING approval path, pinned to the exact draft by its digest, and an edit voids it."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from community_observer import (
    CommunityRef,
    ObservedComment,
    ObservedThread,
    ThreadMetrics,
    raw_digest,
)
from community_os_contracts import AccessMode, ObservationPlatform, Observed, UnknownReason
from community_os_contracts.execution import ActionProposalState
from control_plane.approval.store import APPROVED_DECISION, ApprovalStore, RevisionNotGrantable
from control_plane.observe import prompts
from control_plane.observe.reactions import PROMPT_NAME
from control_plane.observe.store import ObservationStore
from control_plane.pilot.handlers import CYCLE_INTERVAL_SECONDS, PAUSED_JOB_TYPES
from control_plane.pilot.publication import (
    ConsentError,
    ConsentRow,
    PublicationStore,
    approve_consents,
    draft_sha256,
    hash_input_for,
    parse_consent_table,
)
from control_plane.pilot.store import PilotStore

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
CONSENT_DOCUMENT = pathlib.Path(__file__).resolve().parent / "fixtures" / "consent_table.md"
NOT_EXPOSED = Observed[int].unknown(UnknownReason.NOT_EXPOSED_BY_PLATFORM)
SUB = CommunityRef(
    platform=ObservationPlatform.REDDIT,
    key="r/kubernetes",
    display_name="Reddit, r/kubernetes",
    access_mode=AccessMode.ACCOUNT_REQUIRED,
)


def observed(external_id: str) -> ObservedThread:
    return ObservedThread(
        platform=ObservationPlatform.REDDIT,
        community_key="r/kubernetes",
        external_id=external_id,
        url=f"https://www.reddit.com/r/kubernetes/comments/{external_id}/a_post/",
        title=f"Post {external_id}",
        body="the question body",
        author="op",
        created_at=NOW - timedelta(hours=2),
        observed_at=NOW,
        metrics=ThreadMetrics(score=Observed[int].known(3), comment_count=Observed[int].known(1)),
        raw_sha256=raw_digest({"id": external_id}),
    )


async def seeded_draft(pool, tenant, *, external_id: str, draft: str):
    """A run with one drafted reaction on one thread; returns (run, reaction)."""
    store = ObservationStore(pool)
    pilot = PilotStore(pool)
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=SUB,
        poll_interval_seconds=1800,
    )
    profile = await store.save_profile(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        knows_deeply="queues",
        positions="measure first",
        would_never_claim="legal advice",
        voice_samples="short, dry",
    )
    thread_id, _ = await store.record_thread(community=community, thread=observed(external_id))
    await store.record_comments(
        community=community,
        thread_id=thread_id,
        comments=(
            ObservedComment(
                external_id=f"c{external_id}",
                parent_external_id=None,
                author="someone",
                body="a comment",
                created_at=NOW - timedelta(minutes=30),
                observed_at=NOW,
                depth=0,
                score=NOT_EXPOSED,
            ),
        ),
    )
    run = await pilot.active_run_for_community(community.id)
    if run is None:
        prompt = prompts.get(PROMPT_NAME)
        run = await pilot.create_run(
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            community_id=community.id,
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(hours=23),
            poll_interval_seconds=CYCLE_INTERVAL_SECONDS,
            model_calls_budget=12,
            max_generations_per_thread=2,
            profile_version_id=profile.id,
            prompt_name=prompt.name,
            prompt_sha256=prompt.sha256,
            paused_job_types=PAUSED_JOB_TYPES,
            restore={"poll_interval_seconds": 1800},
        )
    reaction = await pilot.save_reaction(
        run,
        thread_id=thread_id,
        origin="CYCLE",
        target_message_external_id=external_id,
        decision="HELP",
        draft=draft,
        contribution="one concrete thing",
        brief_rationale="because",
        confidence=0.8,
        model="fake",
        effort="medium",
        profile_version_id=profile.id,
        prompt_name=PROMPT_NAME,
        prompt_sha256=prompts.get(PROMPT_NAME).sha256,
        input_sha256="a" * 64,
        situation_sha256=draft_sha256(f"situation-{external_id}"),
        read_at=NOW,
        context_complete=True,
        comments_seen=1,
        wait_reason=None,
        next_check_at=None,
    )
    assert reaction is not None
    return run, reaction


def consent_for(reaction, position: int, *, sha: str | None = None) -> ConsentRow:
    return ConsentRow(
        position=position,
        reaction_id=reaction.id,
        thread_external_id=reaction.thread_external_id,
        draft_sha256=sha or draft_sha256(reaction.draft),
        chars=len(reaction.draft),
    )


def test_the_consent_document_in_the_repository_parses_into_eight_ordered_rows() -> None:
    rows = parse_consent_table(CONSENT_DOCUMENT.read_text(encoding="utf-8"))
    assert [r.position for r in rows] == list(range(1, 9))
    assert rows[0].reaction_id == UUID("c0ffee00-0000-0000-0000-000000000001")
    assert rows[0].thread_external_id == "1aaaaa1" and rows[0].chars == 55
    assert all(len(r.draft_sha256) == 64 for r in rows)


def test_a_row_that_looks_like_data_but_does_not_parse_is_an_error_not_a_shorter_list() -> None:
    with pytest.raises(ConsentError):
        parse_consent_table("| # | id |\n| --- | --- |\n| 1 | not-a-uuid | x |\n")
    with pytest.raises(ConsentError):
        parse_consent_table("no table here")


async def test_consents_land_in_the_existing_approval_path_pinned_to_the_draft_digest(
    pool, tenant
) -> None:
    run, first = await seeded_draft(pool, tenant, external_id="1aaaaaa", draft="First draft text.")
    _, second = await seeded_draft(pool, tenant, external_id="1bbbbbb", draft="Second draft text.")
    rows = (consent_for(first, 1), consent_for(second, 2))

    results = await approve_consents(
        pool,
        rows=rows,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_handle="Demo_Maintainer",
        consent_source="test consent",
        operator_id=tenant["operator"],
        now=datetime.now(UTC),
    )

    assert [r.created for r in results] == [True, True]
    approvals = ApprovalStore(pool)
    publication = PublicationStore(pool)
    account_id = await publication.social_account_id(
        organization_id=tenant["organization"], platform="REDDIT", handle="Demo_Maintainer"
    )
    for result, reaction in zip(results, (first, second), strict=True):
        revision = await approvals.fetch_revision(
            result.request.proposal_revision_id, organization_id=tenant["organization"]
        )
        assert revision is not None and revision.state is ActionProposalState.APPROVED
        expected = hash_input_for(
            reaction,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=account_id,
        )
        assert revision.payload_hash == expected.payload_hash() == result.payload_hash, (
            "zgoda wskazuje dokładnie digest 14 pól, którego text_and_media jest szkicem"
        )
        assert revision.hash_input.text_and_media == reaction.draft
        approval = await approvals.find_approval(
            revision_id=revision.id,
            payload_hash=revision.payload_hash,
            organization_id=tenant["organization"],
        )
        assert approval is not None and approval.approved_by == tenant["operator"]
        assert result.request.status == "QUEUED" and result.request.run_id == run.id
        assert result.request.target_external_id == f"t3_{reaction.thread_external_id}"
    assert [r.request.position for r in results] == [1, 2]

    # Running the same document again writes nothing new and reports the existing rows.
    again = await approve_consents(
        pool,
        rows=rows,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_handle="Demo_Maintainer",
        consent_source="test consent",
        operator_id=tenant["operator"],
        now=datetime.now(UTC),
    )
    assert [r.created for r in again] == [False, False]
    assert [r.request.id for r in again] == [r.request.id for r in results]
    async with pool.acquire() as conn:
        decisions = await conn.fetchval(
            "SELECT count(*) FROM approval_decisions WHERE organization_id = $1 AND decision = $2",
            tenant["organization"],
            APPROVED_DECISION,
        )
    assert decisions == 2


async def test_a_digest_that_does_not_match_the_draft_refuses_the_whole_batch(pool, tenant) -> None:
    _, first = await seeded_draft(pool, tenant, external_id="1ccccccc", draft="Good draft.")
    _, second = await seeded_draft(pool, tenant, external_id="1ddddddd", draft="Other draft.")
    rows = (consent_for(first, 1), consent_for(second, 2, sha=draft_sha256("edited elsewhere")))

    with pytest.raises(ConsentError, match="sha256"):
        await approve_consents(
            pool,
            rows=rows,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_handle="Demo_Maintainer",
            consent_source="test consent",
            operator_id=tenant["operator"],
            now=datetime.now(UTC),
        )

    publication = PublicationStore(pool)
    assert await publication.request_for_reaction(first.id) is None, (
        "pierwszy wiersz był poprawny, ale partia jest jedna: nic nie zapisano"
    )
    async with pool.acquire() as conn:
        proposals = await conn.fetchval(
            "SELECT count(*) FROM action_proposals WHERE organization_id = $1",
            tenant["organization"],
        )
    assert proposals == 0


#: SCENARIUSZ 4 P0: zgoda jest przypięta do treści, więc zmieniony szkic nie publikuje się na
#: starej zgodzie.
@pytest.mark.p0
async def test_editing_the_draft_voids_the_consent_so_no_grant_can_be_issued(pool, tenant) -> None:
    _, reaction = await seeded_draft(pool, tenant, external_id="1eeeeeee", draft="Approved text.")
    (result,) = await approve_consents(
        pool,
        rows=(consent_for(reaction, 1),),
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_handle="Demo_Maintainer",
        consent_source="test consent",
        operator_id=tenant["operator"],
        now=datetime.now(UTC),
    )
    approvals = ApprovalStore(pool)
    publication = PublicationStore(pool)
    account_id = await publication.social_account_id(
        organization_id=tenant["organization"], platform="REDDIT", handle="Demo_Maintainer"
    )

    # Before the edit a grant can be issued for the approved revision.
    intent = await approvals.issue_intent(
        revision_id=result.request.proposal_revision_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        actor_ref="test",
    )
    assert intent.payload_hash == result.payload_hash

    # The operator edits one word: a new DRAFT revision, the approved one is SUPERSEDED.
    edited = hash_input_for(
        reaction,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        account_id=account_id,
    ).model_copy(update={"text_and_media": "Approved text, edited."})
    previous, successor = await approvals.edit_into_new_revision(
        revision_id=result.request.proposal_revision_id,
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        new_hash_input=edited,
        actor_ref=f"operator:{tenant['operator']}",
    )
    assert previous.state is ActionProposalState.SUPERSEDED
    assert successor.state is ActionProposalState.DRAFT

    # The consent row still exists (it records a real decision) but authorizes nothing new.
    with pytest.raises(RevisionNotGrantable):
        await approvals.issue_intent(
            revision_id=result.request.proposal_revision_id,
            organization_id=tenant["organization"],
            workspace_id=tenant["workspace"],
            account_id=uuid4(),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            actor_ref="test",
        )
    with pytest.raises(PermissionError):
        await approvals.authorize_and_consume_intent(
            intent.id, organization_id=tenant["organization"], actor_ref="test"
        )
