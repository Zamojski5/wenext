"""The observe routes through a real session cookie, against PostgreSQL.

`logged_in_client` and `tenant` come from `conftest.py`: a real `POST /api/session`, one
organization with exactly one workspace. The auth boundary of every route here is covered by
the generated test in `test_auth_boundary.py`; this file is about what the routes DO.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from community_os_contracts import JudgmentDecision, JudgmentRecord, ObservationPlatform
from control_plane.api.communities import COMMUNITIES_PATH
from control_plane.api.judgments import JUDGMENTS_PATH
from control_plane.api.user_profile import USER_PROFILE_PATH
from control_plane.api.waiting_jobs import RESUME_WAITING_PATH
from control_plane.observe.scheduling import JOB_JUDGE, JOB_POLL
from control_plane.observe.store import ObservationStore
from tests.test_observe_store import ASK, observed_thread

pytestmark = pytest.mark.db

PROFILE = {
    "knows_deeply": "kolejki na Postgresie, fencing, lease'y",
    "positions": "nudna technologia wygrywa; broker dopiero gdy zmierzony",
    "would_never_claim": "benchmarków, których nie uruchomił",
    "voice_samples": "Krótko. Konkretnie. Liczby.",
}


async def _jobs(pool, organization_id, job_type):
    async with pool.acquire() as conn:
        return [
            dict(r)
            for r in await conn.fetch(
                "SELECT idempotency_key, status FROM jobs WHERE organization_id = $1 AND job_type = $2",
                organization_id,
                job_type,
            )
        ]


async def test_enabling_a_community_upserts_audits_and_schedules_the_first_poll(
    logged_in_client, pool, tenant
) -> None:
    response = await logged_in_client.post(
        COMMUNITIES_PATH, json={"platform": "HACKER_NEWS", "community_key": "ask"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["community"]["ref"] == "HACKER_NEWS:ask"
    assert body["community"]["display_name"] == "Hacker News · Ask HN"
    assert body["community"]["access_mode"] == "PUBLIC_API_NO_ACCOUNT"
    assert body["community"]["poll_interval_seconds"] == 600
    assert body["first_poll_created"] is True

    polls = await _jobs(pool, tenant["organization"], JOB_POLL)
    assert len(polls) == 1 and polls[0]["status"] == "READY"

    again = await logged_in_client.post(
        COMMUNITIES_PATH, json={"platform": "HACKER_NEWS", "community_key": "ask"}
    )
    assert again.status_code == 201
    assert again.json()["community"]["community_id"] == body["community"]["community_id"]
    assert again.json()["first_poll_created"] is False, (
        "ten sam interwał, ten sam slot → ten sam job"
    )

    # A changed interval is a different slot key, so a poll on the NEW cadence is enqueued at once;
    # the two chains converge on the next slot, because both handlers read the row's interval.
    faster = await logged_in_client.post(
        COMMUNITIES_PATH,
        json={"platform": "HACKER_NEWS", "community_key": "ask", "poll_interval_seconds": 900},
    )
    assert faster.status_code == 201 and faster.json()["first_poll_created"] is True
    assert faster.json()["community"]["poll_interval_seconds"] == 900

    async with pool.acquire() as conn:
        kinds = await conn.fetch(
            "SELECT kind, payload FROM audit_events WHERE organization_id = $1 AND kind = 'COMMUNITY_OBSERVATION_ENABLED'",
            tenant["organization"],
        )
    assert len(kinds) == 3

    listing = await logged_in_client.get(COMMUNITIES_PATH)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["communities"][0]["counts"] == {
        "threads": 0,
        "comments": 0,
        "snapshots": 0,
        "judgments": 0,
        "pictures": 0,
    }


async def test_an_unknown_community_key_is_a_400_not_a_row(logged_in_client, pool, tenant) -> None:
    response = await logged_in_client.post(
        COMMUNITIES_PATH, json={"platform": "HACKER_NEWS", "community_key": "r/devops"}
    )
    assert response.status_code == 400
    assert "r/devops" in response.json()["detail"]
    assert (
        await ObservationStore(pool).list_communities(organization_id=tenant["organization"]) == ()
    )
    too_fast = await logged_in_client.post(
        COMMUNITIES_PATH,
        json={"platform": "HACKER_NEWS", "community_key": "ask", "poll_interval_seconds": 5},
    )
    assert too_fast.status_code == 422


async def test_reddit_without_credentials_still_registers_the_community(
    logged_in_client, pool, tenant
) -> None:
    """The adapter exists; the missing credentials show up as a parked poll, not as a 400."""
    response = await logged_in_client.post(
        COMMUNITIES_PATH, json={"platform": "REDDIT", "community_key": "r/devops"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["community"]["access_mode"] == "ACCOUNT_REQUIRED"


async def test_picture_is_404_until_one_exists(logged_in_client, pool, tenant) -> None:
    created = await logged_in_client.post(
        COMMUNITIES_PATH, json={"platform": "HACKER_NEWS", "community_key": "ask"}
    )
    community_id = created.json()["community"]["community_id"]
    missing = await logged_in_client.get(f"{COMMUNITIES_PATH}/{community_id}/picture")
    assert missing.status_code == 404
    detail = await logged_in_client.get(f"{COMMUNITIES_PATH}/{community_id}")
    assert detail.status_code == 200 and detail.json()["latest_picture"] is None

    store = ObservationStore(pool)
    community = await store.get_community(
        __import__("uuid").UUID(community_id), organization_id=tenant["organization"]
    )
    await store.save_picture(
        community=community,
        window_start=datetime(2026, 9, 2, tzinfo=UTC),
        window_end=datetime(2026, 9, 3, tzinfo=UTC),
        content={"summary": "spokojnie"},
        thread_count=0,
        invalid_ref_count=0,
        model="m",
        effort="e",
        prompt_name="community_picture",
        prompt_sha256="d" * 64,
    )
    found = await logged_in_client.get(f"{COMMUNITIES_PATH}/{community_id}/picture")
    assert found.status_code == 200
    assert found.json()["content"] == {"summary": "spokojnie"} and found.json()["version"] == 1
    unknown = await logged_in_client.get(f"{COMMUNITIES_PATH}/{uuid4()}")
    assert unknown.status_code == 404


async def test_saving_a_profile_versions_audits_and_schedules_judgments(
    logged_in_client, pool, tenant
) -> None:
    missing = await logged_in_client.get(USER_PROFILE_PATH)
    assert missing.status_code == 404

    store = ObservationStore(pool)
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    now = datetime.now(UTC)
    await store.record_thread(
        community=community, thread=observed_thread("1", created_at=now, observed_at=now)
    )
    await store.record_thread(
        community=community, thread=observed_thread("2", created_at=now, observed_at=now)
    )

    saved = await logged_in_client.post(USER_PROFILE_PATH, json=PROFILE)
    assert saved.status_code == 201, saved.text
    assert saved.json()["profile"]["version"] == 1
    assert saved.json()["judgments_scheduled"] == 2, "profil odblokowuje osądy istniejących wątków"
    assert len(await _jobs(pool, tenant["organization"], JOB_JUDGE)) == 2

    again = await logged_in_client.post(
        USER_PROFILE_PATH, json={**PROFILE, "positions": "zmienione"}
    )
    assert again.json()["profile"]["version"] == 2
    current = await logged_in_client.get(USER_PROFILE_PATH)
    assert current.status_code == 200 and current.json()["positions"] == "zmienione"

    invalid = await logged_in_client.post(USER_PROFILE_PATH, json={"knows_deeply": "x"})
    assert invalid.status_code == 422


async def test_judgments_list_filters_and_feedback_is_recorded_with_audit(
    logged_in_client, pool, tenant
) -> None:
    store = ObservationStore(pool)
    community = await store.upsert_community(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        ref=ASK,
        poll_interval_seconds=600,
    )
    now = datetime.now(UTC)
    thread_id, _ = await store.record_thread(
        community=community, thread=observed_thread("1", created_at=now, observed_at=now)
    )
    profile = await store.save_profile(
        organization_id=tenant["organization"], workspace_id=tenant["workspace"], **PROFILE
    )
    for decision, contribution in (
        (JudgmentDecision.IGNORE, None),
        (JudgmentDecision.ENGAGE, "Liczby z roku produkcji kolejki."),
    ):
        await store.save_judgment(
            community=community,
            thread_id=thread_id,
            record=JudgmentRecord(
                thread_ref="HACKER_NEWS:ask:1",
                decision=decision,
                reasoning="Pytanie o kolejki w produkcji: dokładnie doświadczenie użytkownika.",
                what_user_could_add=contribution,
                confidence=0.6,
                user_profile_version=profile.version,
                prompt_name="judge_thread",
                prompt_sha256="e" * 64,
                model="m",
                effort="low",
                created_at=now,
            ),
            user_profile_version_id=profile.id,
            community_picture_id=None,
        )

    everything = await logged_in_client.get(JUDGMENTS_PATH)
    assert everything.status_code == 200
    assert everything.json()["count"] == 2
    assert everything.json()["summary"]["decision_IGNORE"] == 1
    engage_only = await logged_in_client.get(JUDGMENTS_PATH, params={"decision": "ENGAGE"})
    assert engage_only.json()["count"] == 1
    engage = engage_only.json()["judgments"][0]
    assert engage["what_user_could_add"].startswith("Liczby") and engage["latest_feedback"] is None
    assert engage["thread_url"].startswith("https://news.ycombinator.com/")
    bad_filter = await logged_in_client.get(JUDGMENTS_PATH, params={"decision": "MAYBE"})
    assert bad_filter.status_code == 422

    feedback = await logged_in_client.post(
        f"{JUDGMENTS_PATH}/{engage['judgment_id']}/feedback",
        json={"verdict": "AGREE", "note": "tak"},
    )
    assert feedback.status_code == 201, feedback.text
    after = await logged_in_client.get(JUDGMENTS_PATH, params={"decision": "ENGAGE"})
    assert after.json()["judgments"][0]["latest_feedback"] == "AGREE"
    assert after.json()["summary"]["feedback_AGREE"] == 1
    async with pool.acquire() as conn:
        audit = await conn.fetchrow(
            "SELECT payload FROM audit_events WHERE organization_id = $1 AND kind = 'JUDGMENT_FEEDBACK_RECORDED'",
            tenant["organization"],
        )
    assert audit is not None
    missing = await logged_in_client.post(
        f"{JUDGMENTS_PATH}/{uuid4()}/feedback", json={"verdict": "AGREE"}
    )
    assert missing.status_code == 404
    bad = await logged_in_client.post(
        f"{JUDGMENTS_PATH}/{engage['judgment_id']}/feedback", json={"verdict": "MEH"}
    )
    assert bad.status_code == 422


async def test_resuming_waiting_jobs_republishes_only_the_human_unblocked_states(
    logged_in_client, pool, tenant
) -> None:
    async with pool.acquire() as conn:
        ids = {}
        for status in ("WAITING_AUTH", "WAITING_CAPABILITY", "WAITING_RETRY", "READY", "FAILED"):
            ids[status] = await conn.fetchval(
                """
                INSERT INTO jobs (organization_id, workspace_id, job_type, routing_key, priority, status,
                                  attempt_limit, idempotency_key)
                VALUES ($1, $2, 'observe.poll_community', 'observe', 1, $3, 5, $4) RETURNING id
                """,
                tenant["organization"],
                tenant["workspace"],
                status,
                f"resume:{status}",
            )
        # A parked job of a type this process has no handler for (the 15 030 F1 soak jobs of
        # 2026-09-04) is NOT resumed: waking it would only fail it again. Counted, not hidden.
        ids["probe"] = await conn.fetchval(
            """
            INSERT INTO jobs (organization_id, workspace_id, job_type, routing_key, priority, status,
                              attempt_limit, idempotency_key)
            VALUES ($1, $2, 'probe', 'probe', 1, 'WAITING_AUTH', 5, 'resume:probe') RETURNING id
            """,
            tenant["organization"],
            tenant["workspace"],
        )
    response = await logged_in_client.post(RESUME_WAITING_PATH)
    assert response.status_code == 200, response.text
    assert response.json()["resumed"] == 2
    assert response.json()["skipped_without_handler"] == 1
    assert set(response.json()["job_ids"]) == {
        str(ids["WAITING_AUTH"]),
        str(ids["WAITING_CAPABILITY"]),
    }
    async with pool.acquire() as conn:
        statuses = {
            row["idempotency_key"]: row["status"]
            for row in await conn.fetch(
                "SELECT idempotency_key, status FROM jobs WHERE organization_id = $1",
                tenant["organization"],
            )
        }
    assert (
        statuses["resume:WAITING_AUTH"] == "READY"
        and statuses["resume:WAITING_CAPABILITY"] == "READY"
    )
    assert statuses["resume:WAITING_RETRY"] == "WAITING_RETRY", "stan reapera nie jest ruszany"
    assert statuses["resume:FAILED"] == "FAILED"
    assert statuses["resume:probe"] == "WAITING_AUTH", "bez handlera nie wznawiamy"
    assert ObservationPlatform.HACKER_NEWS.value == "HACKER_NEWS"
