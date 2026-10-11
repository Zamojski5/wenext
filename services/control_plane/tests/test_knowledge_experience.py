"""What came of what we published, written down as EXPERIENCE, against PostgreSQL (P5 etap A).

WHAT THIS IS ABOUT. The milestone asks that a correction or a moderation signal CHANGE the next
decision. Before anything can change, what happened has to be data, and the ways that data can
lie are the cases below:

  * a publication nobody reacted to leaves no row, so "we never looked" and "nobody cared" look
    the same to the next session;
  * "nothing happened" is recorded twenty minutes in, when nothing was supposed to have happened
    yet, and the next session reads it as a result;
  * a newer measurement overwrites the older one, so "it looked fine, then it was removed" stops
    being visible as a sequence — which is the sequence worth learning from;
  * the person's correction is stored as the agent's own conclusion, and the agent then "knows"
    a preference nobody stated.

THE PUBLICATION IS REAL. The fixture goes through the publisher used in P0
(`test_pilot_publisher.approved` and `world`), so the request, the attempt, the evidence and the
account are the rows the product really writes. Seeding them by hand would drift from the schema
and would prove nothing about the join the sweep does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from control_plane.domain.models import ClaimedJob
from control_plane.knowledge.experience import (
    NO_REACTION_AFTER,
    OUTCOME_NO_REACTION,
    OUTCOME_REMOVED,
    OUTCOME_REPLIED,
    measure,
    measure_one,
    outcome_of,
    preference_of,
)
from control_plane.knowledge.handlers import JOB_EXPERIENCE, KnowledgeHandlers
from control_plane.knowledge.ingest import Ingestor
from control_plane.knowledge.store import KnowledgeStore, Scope
from control_plane.pilot.publisher import OUTCOME_PUBLISHED
from test_pilot_publisher import ACCOUNT, approved, thread_json, world

pytestmark = pytest.mark.db


async def publish_one(pool, tenant, *, external_id: str, draft: str):
    """One really published request, through the real publisher. Returns its row id.

    The run's publication pace is raised out of the way first. The pace is a parameter of the
    pilot manifest and it has its own tests; a test about what came of a publication must not
    fail because the second one in the same hour was held back, which is what happens without
    this (measured: the second call comes back PACED).
    """
    (reaction,) = await approved(pool, tenant, (external_id, draft))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pilot_runs SET max_publications_per_hour = 20, max_publications_per_day = 60 "
            "WHERE organization_id = $1",
            tenant["organization"],
        )
    w = await world(pool, tenant)
    w.source.threads[external_id] = thread_json(external_id, comments=[{"id": "k1"}])
    result = await w.handlers.run_once(datetime.now(UTC))
    assert result.outcome == OUTCOME_PUBLISHED, result
    request = await w.publication.request_for_reaction(reaction.id)
    assert request is not None and request.status == "PUBLISHED"
    return request.id


async def observe(pool, tenant, request_id, *, score=None, replies=0, present=True, when=None):
    observed_at = when or datetime.now(UTC)
    async with pool.acquire() as conn:
        # The fixture models two distinct reads: anonymous visibility and the author's
        # reaction counts. The production sweep must never infer either from the other.
        await conn.execute(
            "UPDATE publication_requests SET public_visibility = $2, "
            "public_visibility_checked_at = $3 WHERE id = $1",
            request_id,
            "CONFIRMED" if present else "REMOVED",
            observed_at,
        )
        await conn.execute(
            "INSERT INTO publication_observations (organization_id, workspace_id, request_id, "
            "observed_at, still_present, score, reply_count, viewer) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, 'ANONYMOUS')",
            tenant["organization"],
            tenant["workspace"],
            request_id,
            observed_at,
            present,
            None,
            0,
        )
        await conn.execute(
            "INSERT INTO publication_observations (organization_id, workspace_id, request_id, "
            "observed_at, still_present, score, reply_count, viewer) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, 'AUTHOR')",
            tenant["organization"],
            tenant["workspace"],
            request_id,
            observed_at,
            present,
            score,
            replies,
        )


async def age_publication(pool, request_id, hours: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET published_at = $1 WHERE id = $2",
            datetime.now(UTC) - timedelta(hours=hours),
            request_id,
        )


def handlers_for(pool, tenant, queue=None) -> KnowledgeHandlers:
    return KnowledgeHandlers(
        pool=pool,
        ingestor=Ingestor(pool=pool),
        organization_id=tenant["organization"],
        queue=queue,
    )


def claim(tenant) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid.uuid4(),
        organization_id=tenant["organization"],
        workspace_id=None,
        job_type=JOB_EXPERIENCE,
        routing_key="knowledge.experience",
        priority=20,
        payload_ref=None,
        dispatch_version=1,
        attempt_limit=1,
        attempt_id=uuid.uuid4(),
        attempt_no=1,
        lease_generation=1,
        lease_owner=uuid.uuid4(),
        expires_at=None,
    )


def scope_of(tenant, project_id=None) -> Scope:
    return Scope(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# The measurement itself
# ---------------------------------------------------------------------------


async def test_a_publication_with_replies_becomes_an_experience_with_all_three_parts(
    pool, tenant
) -> None:
    request_id = await publish_one(pool, tenant, external_id="1p5a01", draft="Nasza odpowiedź.")
    await observe(pool, tenant, request_id, score=4, replies=2)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    items = await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE")
    assert len(items) == 1
    payload = items[0].payload
    assert payload["situation"]["community"].startswith("r/")
    assert payload["decision"]["account"] == ACCOUNT
    assert payload["decision"]["length_chars"] == len("Nasza odpowiedź.")
    assert payload["outcome"]["kind"] == OUTCOME_REPLIED
    assert payload["outcome"]["replies"] == 2
    assert payload["outcome"]["measured_at"]
    assert payload["measured_by"] == "job knowledge.experience.sweep"
    assert items[0].kind == "EXPERIENCE" and items[0].stated_by == "AGENT"
    # The statement is readable on its own: a person scanning the screen has to understand it.
    assert "Wynik:" in items[0].statement


async def test_silence_after_the_threshold_is_a_recorded_result_not_a_missing_row(
    pool, tenant
) -> None:
    """THE case the milestone names. "Nobody reacted" is the useful half of what we know."""
    request_id = await publish_one(pool, tenant, external_id="1p5a02", draft="Cisza potem.")
    await age_publication(pool, request_id, hours=30)
    await observe(pool, tenant, request_id, score=1, replies=0)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    items = await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE")
    assert len(items) == 1
    assert items[0].payload["outcome"]["kind"] == OUTCOME_NO_REACTION
    assert "nikt nie zareagował" in items[0].statement


async def test_nothing_is_written_before_the_threshold(pool, tenant) -> None:
    """Twenty minutes in, "no reaction" is not a finding. A row here would be read as one."""
    request_id = await publish_one(pool, tenant, external_id="1p5a03", draft="Świeże.")
    await age_publication(pool, request_id, hours=0.3)
    await observe(pool, tenant, request_id, score=1, replies=0)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    assert await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE") == ()


async def test_a_removal_is_recorded_with_what_reported_it(pool, tenant) -> None:
    request_id = await publish_one(pool, tenant, external_id="1p5a04", draft="Zdjęte.")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET public_visibility = 'REMOVED', "
            "public_visibility_checked_at = now(), removal_reported_at = now(), "
            "removal_source = 'ANONYMOUS_READ' WHERE id = $1",
            request_id,
        )
    await observe(pool, tenant, request_id, present=False)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    (item,) = await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE")
    assert item.payload["outcome"]["kind"] == OUTCOME_REMOVED
    assert item.payload["outcome"]["removal_source"] == "ANONYMOUS_READ"
    assert "zniknęła z platformy" in item.statement


async def test_a_changed_outcome_supersedes_and_the_older_measurement_stays(pool, tenant) -> None:
    """ "Wyglądało dobrze, potem zniknęło" is the sequence worth learning from."""
    store = KnowledgeStore(pool)
    request_id = await publish_one(pool, tenant, external_id="1p5a05", draft="Najpierw dobrze.")
    await observe(pool, tenant, request_id, score=5, replies=1)
    await handlers_for(pool, tenant).sweep_experience(claim(tenant))
    (first,) = await store.list(scope_of(tenant), kind="EXPERIENCE")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE publication_requests SET public_visibility = 'REMOVED', "
            "public_visibility_checked_at = now(), removal_reported_at = now(), "
            "removal_source = 'INBOX' WHERE id = $1",
            request_id,
        )
    await observe(pool, tenant, request_id, present=False, when=datetime.now(UTC))
    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    live = await store.list(scope_of(tenant), kind="EXPERIENCE")
    assert len(live) == 1 and live[0].payload["outcome"]["kind"] == OUTCOME_REMOVED
    history = await store.list(scope_of(tenant), kind="EXPERIENCE", include_retired=True)
    assert len(history) == 2
    old = await store.item(first.id, scope_of(tenant))
    assert old is not None and old.superseded_by == live[0].id
    assert old.payload["outcome"]["kind"] == OUTCOME_REPLIED


async def test_an_unchanged_measurement_writes_nothing_new(pool, tenant) -> None:
    """A sweep that rewrote everything hourly would bury the two rows that changed."""
    request_id = await publish_one(pool, tenant, external_id="1p5a06", draft="Bez zmian.")
    await observe(pool, tenant, request_id, score=3, replies=1)
    handlers = handlers_for(pool, tenant)

    await handlers.sweep_experience(claim(tenant))
    await handlers.sweep_experience(claim(tenant))

    history = await KnowledgeStore(pool).list(
        scope_of(tenant), kind="EXPERIENCE", include_retired=True
    )
    assert len(history) == 1


async def test_the_sweep_chains_itself_while_there_is_anything_published(pool, tenant) -> None:
    from control_plane.jobs.postgres_transport import PostgresJobTransport
    from control_plane.jobs.store import JobStore

    request_id = await publish_one(pool, tenant, external_id="1p5a07", draft="Łańcuch.")
    await observe(pool, tenant, request_id, score=2, replies=1)
    queue = PostgresJobTransport(JobStore(pool), organization_id=tenant["organization"])
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM jobs WHERE organization_id = $1", tenant["organization"])

    await handlers_for(pool, tenant, queue=queue).sweep_experience(claim(tenant))

    async with pool.acquire() as conn:
        types = [
            row["job_type"]
            for row in await conn.fetch(
                "SELECT job_type FROM jobs WHERE organization_id = $1", tenant["organization"]
            )
        ]
    assert types == [JOB_EXPERIENCE]


async def test_the_measurement_carries_the_person_behind_the_account(pool, tenant) -> None:
    """Whose experience it is decides where it may be read. See the isolation test in the tools."""
    request_id = await publish_one(pool, tenant, external_id="1p5a08", draft="Czyje to.")
    async with pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "INSERT INTO person_profiles (organization_id, workspace_id, name) "
            "VALUES ($1, $2, 'Anna') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        account_id = await conn.fetchval(
            "SELECT id FROM social_accounts WHERE organization_id = $1 AND external_account_id = $2",
            tenant["organization"],
            ACCOUNT,
        )
        await conn.execute(
            "INSERT INTO person_profile_accounts (organization_id, workspace_id, "
            "person_profile_id, social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            profile_id,
            account_id,
        )
    await observe(pool, tenant, request_id, score=4, replies=1)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    (item,) = await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE")
    assert item.person_profile_id == profile_id
    assert item.payload["decision"]["person_profile_id"] == str(profile_id)


async def test_a_publication_made_under_a_project_delegation_is_measured_too(pool, tenant) -> None:
    """Since 0017 a request comes from a pilot run OR from a project delegation, never both.

    Reported by the P4 session while this sweep was being written: an inner join on the pilot
    reaction would skip every project publication in silence, and the screen would read as "the
    agent published nothing" rather than as a missing join.
    """
    request_id = await publish_one(pool, tenant, external_id="1p5a10", draft="Z delegacji.")
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) "
            "VALUES ($1, $2, 'projekt-delegacji') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
        )
        delegation_id = await conn.fetchval(
            "INSERT INTO project_delegations (organization_id, workspace_id, project_id, mode, "
            "scope, scope_sha256, signed_by) VALUES ($1, $2, $3, 'AUTONOMOUS', '{}'::jsonb, $4, $5)"
            " RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            "a" * 64,
            tenant["operator"],
        )
        # The shape 0017 allows: no run, no reaction, a project and its delegation instead.
        await conn.execute(
            "UPDATE publication_requests SET run_id = NULL, reaction_id = NULL, "
            "project_id = $2, delegation_id = $3 WHERE id = $1",
            request_id,
            project_id,
            delegation_id,
        )
    await observe(pool, tenant, request_id, score=6, replies=3)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    items = await KnowledgeStore(pool).list(scope_of(tenant, project_id), kind="EXPERIENCE")
    assert len(items) == 1, "wniosek z delegacji projektu został pominięty"
    assert items[0].project_id == project_id
    assert items[0].payload["situation"]["decision_kind"] == "REPLY"
    assert items[0].payload["outcome"]["replies"] == 3


# ---------------------------------------------------------------------------
# The rules, without a database
# ---------------------------------------------------------------------------


def test_the_threshold_is_stated_once_and_is_a_day() -> None:
    assert timedelta(hours=24) == NO_REACTION_AFTER


def test_a_score_of_one_with_no_replies_is_silence_not_a_reaction() -> None:
    """One point is the author's own upvote on most platforms."""
    from control_plane.knowledge.experience import Measured

    row = Measured(
        request_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        published_at=datetime.now(UTC) - timedelta(hours=40),
        target_url="https://example.test/x",
        public_visibility="CONFIRMED",
        removal_source=None,
        decision="HELP",
        move="NUMBER",
        draft="tekst",
        community_key="r/devops",
        platform="REDDIT",
        thread_title="t",
        thread_external_id="1x",
        thread_created_at=datetime.now(UTC) - timedelta(hours=44),
        account="konto",
        person_profile_id=None,
        project_id=None,
        observed_at=datetime.now(UTC),
        still_present=True,
        score=1,
        reply_count=0,
    )
    assert outcome_of(row)["kind"] == OUTCOME_NO_REACTION


def test_author_view_never_turns_unverified_or_stale_measurement_into_silence() -> None:
    from dataclasses import replace

    from control_plane.knowledge.experience import Measured

    moment = datetime.now(UTC)
    row = Measured(
        request_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        published_at=moment - timedelta(hours=30),
        target_url="https://example.test/x",
        public_visibility="UNVERIFIED",
        removal_source=None,
        decision="HELP",
        move=None,
        draft="tekst",
        community_key="r/devops",
        platform="REDDIT",
        thread_title="t",
        thread_external_id="1x",
        thread_created_at=None,
        account="konto",
        person_profile_id=None,
        project_id=None,
        observed_at=moment,
        still_present=True,
        score=1,
        reply_count=0,
    )
    assert outcome_of(row, now=moment) is None
    assert outcome_of(
        replace(row, public_visibility="CONFIRMED", observed_at=moment - timedelta(hours=10)),
        now=moment,
    ) is None
    assert outcome_of(replace(row, still_present=False), now=moment) is None

    measured_later = replace(
        row,
        public_visibility="CONFIRMED",
        observed_at=moment - timedelta(hours=5),
    )
    outcome = outcome_of(measured_later, now=moment)
    assert outcome is not None and outcome["kind"] == OUTCOME_NO_REACTION
    assert outcome["hours_after_publication"] == 25
    assert "po 25 godzinach" in outcome["sentence"]


def test_the_four_verdicts_are_named_and_two_of_them_answer_the_same_question() -> None:
    """`dobrze` and `nie w ten sposób` share an `about`, so a later one does not win quietly."""
    from control_plane.knowledge.experience import Measured

    row = Measured(
        request_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        published_at=datetime.now(UTC),
        target_url="https://example.test/x",
        public_visibility="UNVERIFIED",
        removal_source=None,
        decision="HELP",
        move=None,
        draft="x" * 1400,
        community_key="r/devops",
        platform="REDDIT",
        thread_title="t",
        thread_external_id="1x",
        thread_created_at=None,
        account="konto",
        person_profile_id=None,
        project_id=None,
        observed_at=None,
        still_present=None,
        score=None,
        reply_count=0,
    )
    good, good_payload = preference_of(row, "GOOD")
    bad, bad_payload = preference_of(row, "NOT_LIKE_THIS")
    assert good_payload["about"] == bad_payload["about"]
    assert "r/devops" in good and "r/devops" in bad

    long_statement, long_payload = preference_of(row, "TOO_LONG")
    assert "1400" in long_statement
    assert long_payload["about"] != good_payload["about"]


async def test_measure_one_and_the_sweep_see_the_same_publication(pool, tenant) -> None:
    request_id = await publish_one(pool, tenant, external_id="1p5a09", draft="Ten sam widok.")
    swept = {
        row.request_id: row for row in await measure(pool, organization_id=tenant["organization"])
    }
    one = await measure_one(pool, organization_id=tenant["organization"], request_id=request_id)
    assert one is not None
    assert swept[request_id].account == one.account
    assert swept[request_id].community_key == one.community_key


async def test_a_publication_nobody_could_verify_leaves_no_experience(pool, tenant) -> None:
    """`UNVERIFIED` NIE JEST DOŚWIADCZENIEM (21.09), zmierzone na całej drodze przemiatania.

    Publikacja stara o dobę, bez ani jednego udanego odczytu anonimowego: nie wiadomo, czy
    ktokolwiek ją widział. Zdanie „nikt nie zareagował" byłoby tu wiedzą o cudzym zachowaniu
    wyciągniętą z naszego braku wiedzy, a następna sesja czytałaby je jako lekcję.
    """
    request_id = await publish_one(pool, tenant, external_id="1p5a09", draft="Bez odczytu.")
    await age_publication(pool, request_id, hours=30)

    await handlers_for(pool, tenant).sweep_experience(claim(tenant))

    items = await KnowledgeStore(pool).list(scope_of(tenant), kind="EXPERIENCE")
    # `not items`, a nie `== []`: store oddaje KROTKĘ, więc porównanie z listą jest zawsze
    # fałszywe i test padał na `() == []` zamiast mierzyć regułę (przebieg z bazą 21.09).
    assert not items, "przemiatanie zapisało doświadczenie o publikacji, której nie widziało"


async def test_the_seed_puts_the_first_sweep_in_the_queue_for_a_published_organization(
    pool, tenant
) -> None:
    """Zasiew przy starcie procesu: łańcuch ma wstać po wdrożeniu, bez klikania człowieka.

    Idempotentny po kluczu godziny, więc drugie wywołanie (restart, dwa procesy) nie buduje
    drugiego łańcucha obok.
    """
    from control_plane.knowledge.handlers import seed_experience_chain

    await publish_one(pool, tenant, external_id="1p5a10", draft="Cokolwiek.")

    first = await seed_experience_chain(pool, organization_id=tenant["organization"])
    second = await seed_experience_chain(pool, organization_id=tenant["organization"])

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT idempotency_key FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_EXPERIENCE,
        )
    assert first is True and second is True
    assert len(rows) == 1, "drugi zasiew zbudował drugi łańcuch obok pierwszego"


async def test_the_seed_does_nothing_for_an_organization_that_published_nothing(
    pool, tenant
) -> None:
    """Pusta organizacja nie dostaje jobu, który i tak nic by nie zapisał."""
    from control_plane.knowledge.handlers import seed_experience_chain

    seeded = await seed_experience_chain(pool, organization_id=tenant["organization"])

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT 1 FROM jobs WHERE organization_id = $1 AND job_type = $2",
            tenant["organization"],
            JOB_EXPERIENCE,
        )
    assert seeded is False
    assert not rows
