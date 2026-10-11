"""HTTP for delegating a project, and for reading what happened while nobody watched (P4).

WHAT THIS FILE IS FOR. The screens of this milestone are the only place a person can see that
the agent is acting on their behalf, so the guarantees have to hold at the HTTP boundary and not
only in the store:

  * a delegation from another organization is indistinguishable from one that does not exist;
  * SIGNING IS WHOLE. There is no route that edits a scope, so a signature can never come to
    cover something its signer did not read. Signing over a live delegation is refused;
  * PAUSING AND REVOKING ARE DIFFERENT ROUTES with different promises, and pausing interrupts
    whatever is running rather than waiting politely for it to finish;
  * THE PANEL SHOWS THE LIMITS THAT BIND. A delegation that named no publication limit still
    has one, and the response says so explicitly, because a founder must not sign a scope whose
    real pace is invisible.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from control_plane.presence.budget import (
    DEFAULT_OWN_POSTS_PER_DAY,
    DEFAULT_PUBLICATIONS_PER_DAY,
    DEFAULT_PUBLICATIONS_PER_HOUR,
)

pytestmark = [pytest.mark.db, pytest.mark.p4]


async def test_registered_user_can_delegate_research_without_a_legacy_operator(pool, client):
    registered = await client.post(
        "/api/auth/register",
        json={
            "email": f"research-{uuid4().hex}@example.test",
            "password": "research-test-password-123",
            "organization_name": "Research project",
        },
    )
    assert registered.status_code == 201, registered.text
    workspace = await client.post("/api/workspaces", json={"name": "Research"})
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["workspace"]["workspace_id"]
    project = await client.post(
        f"/api/workspaces/{workspace_id}/projects", json={"name": "Discovery", "goal": "Research"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project"]["project_id"]
    path = f"/api/workspaces/{workspace_id}/projects/{project_id}/delegation"
    response = await client.post(
        path,
        json={
            "mode": "RESEARCH",
            "accounts": [],
            "communities": [],
            "actions": ["RESEARCH"],
            "budget": {"model_turns_per_day": 4, "publications_per_day": 0, "scheduled_returns": 3},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["delegation"]["scope"]["accounts"] == []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT signed_by, signed_membership_id FROM project_delegations WHERE id=$1::uuid",
            response.json()["delegation"]["delegation_id"],
        )
    assert row["signed_by"] is None and row["signed_membership_id"] is not None
    assert (
        await client.post(
            path,
            json={"mode": "RESEARCH", "accounts": [], "communities": [], "actions": ["RESEARCH"]},
        )
    ).status_code == 409


async def _project(pool, tenant) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                "INSERT INTO projects (organization_id, workspace_id, name, goal) "
                "VALUES ($1, $2, $3, 'cel') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                f"projekt-{uuid4().hex[:8]}",
            )
        )


async def test_research_zero_publications_does_not_exhaust_research_and_plan_is_not_a_run(
    pool, tenant, logged_in_client
):
    project_id = await _project(pool, tenant)
    response = await logged_in_client.post(
        _path(tenant, project_id),
        json={"mode": "RESEARCH", "accounts": [], "communities": [], "actions": ["RESEARCH"],
              "budget": {"model_turns_per_day": 4, "publications_per_day": 0,
                         "publications_per_hour": 0}},
    )
    assert response.status_code == 201, response.text
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wakeups (organization_id,workspace_id,project_id,reason,due_at) "
            "VALUES ($1,$2,$3::uuid,'DUE',now()+interval '30 minutes')",
            tenant["organization"], tenant["workspace"], project_id,
        )
    base = f"/api/workspaces/{tenant['workspace']}/projects/{project_id}"
    view = (await logged_in_client.get(base + "/presence")).json()
    assert view["budget"]["wyczerpany"] is None
    assert len(view["planned"]) == 1
    assert view["wakeups"] == []
    report = (await logged_in_client.get(base + "/return")).json()
    assert report["obudzenia"]["razem"] == 0
    assert report["obudzenia"]["z_sesja"] == 0


async def _account(pool, tenant, project_id) -> str:
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
            "VALUES ($1, 'REDDIT', $2) RETURNING id",
            tenant["organization"],
            f"konto-{uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO project_account_assignments (organization_id, workspace_id, project_id, "
            "social_account_id) VALUES ($1, $2, $3, $4)",
            tenant["organization"],
            tenant["workspace"],
            project_id,
            account_id,
        )
    return str(account_id)


def _path(tenant, project_id: str, suffix: str = "") -> str:
    return f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/delegation{suffix}"


def _body(account_id: str, **overrides):
    return {
        "mode": "AUTONOMOUS",
        "accounts": [account_id],
        "communities": ["r/devops"],
        "actions": ["REPLY"],
        "budget": {"publications_per_day": 6},
        **overrides,
    }


async def test_signing_a_delegation_returns_what_was_signed(pool, tenant, logged_in_client) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)

    response = await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    assert response.status_code == 201, response.text
    delegation = response.json()["delegation"]
    assert delegation["mode"] == "AUTONOMOUS"
    assert delegation["state"] == "aktywna" and delegation["live"] is True
    assert delegation["scope"]["accounts"] == [account_id]
    assert len(delegation["scope_sha256"]) == 64

    read = await logged_in_client.get(_path(tenant, project_id))
    assert read.status_code == 200
    assert read.json()["delegation"]["delegation_id"] == delegation["delegation_id"]


async def test_the_panel_shows_the_limits_that_bind_even_when_nobody_chose_them(
    pool, tenant, logged_in_client
) -> None:
    """A budget with no publication key still has a pace. If this response hid it, a founder
    could sign a scope believing there was no limit, or believing there was one they never set."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)

    response = await logged_in_client.post(
        _path(tenant, project_id), json=_body(account_id, budget={})
    )
    assert response.status_code == 201, response.text
    delegation = response.json()["delegation"]
    assert delegation["budget"] == {}, "podpisano bez limitu publikacji"
    assert delegation["limits"]["publikacje_godzina"] == DEFAULT_PUBLICATIONS_PER_HOUR
    assert delegation["limits"]["publikacje_doba"] == DEFAULT_PUBLICATIONS_PER_DAY
    # 0023: własne posty mają SWÓJ sufit i on też wiąże, więc też musi być widać, że nikt go nie
    # wybrał. Sufit niewidoczny jest tym samym problemem, co sufit zmyślony.
    assert delegation["limits"]["wlasne_posty_doba"] == DEFAULT_OWN_POSTS_PER_DAY
    assert set(delegation["limits"]["domyslne"]) == {
        "publikacje_godzina",
        "publikacje_doba",
        "wlasne_posty_doba",
    }


async def test_a_second_signature_over_a_live_one_is_refused(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    assert (
        await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    ).status_code == 201
    again = await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    assert again.status_code == 409, again.text
    assert "odwołaj" in again.json()["detail"].lower()


async def test_pausing_and_resuming_keep_the_same_signature(pool, tenant, logged_in_client) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    signed = (
        await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    ).json()["delegation"]

    paused = await logged_in_client.post(_path(tenant, project_id, "/pause"))
    assert paused.status_code == 200, paused.text
    assert paused.json()["delegation"]["state"] == "wstrzymana"
    assert paused.json()["delegation"]["live"] is False

    resumed = await logged_in_client.post(_path(tenant, project_id, "/resume"))
    assert resumed.status_code == 200
    assert resumed.json()["delegation"]["live"] is True
    assert resumed.json()["delegation"]["scope_sha256"] == signed["scope_sha256"], (
        "wraca tym samym podpisem, nie nowym"
    )


async def test_revoking_ends_it_and_a_new_signature_is_a_new_delegation(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    first = (await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))).json()[
        "delegation"
    ]

    revoked = await logged_in_client.post(_path(tenant, project_id, "/revoke"))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["delegation"]["revoked_at"] is not None

    read = await logged_in_client.get(_path(tenant, project_id))
    assert read.json()["delegation"] is None, "odwołana nie rządzi już niczym"
    assert len(read.json()["history"]) == 1, "ale zostaje w historii"

    second = await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    assert second.status_code == 201
    assert second.json()["delegation"]["delegation_id"] != first["delegation_id"]


async def test_pausing_interrupts_a_presence_session_that_is_still_running(
    pool, tenant, logged_in_client
) -> None:
    """A stop button that waits for the turn to finish is not a stop button. The interrupt is an
    event in the trace, which is how a turn in another process learns about it."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))

    from control_plane.sessions.store import SESSION_PRESENCE, SessionStore

    sessions = SessionStore(pool)
    session = await sessions.create(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=project_id,
        goal="pracuj, nikt nie patrzy",
        budget={},
        kind=SESSION_PRESENCE,
    )

    assert (await logged_in_client.post(_path(tenant, project_id, "/pause"))).status_code == 200
    events = await sessions.events(organization_id=tenant["organization"], session_id=session.id)
    assert any(event.kind == "INTERRUPT" for event in events), [e.kind for e in events]


async def test_a_delegation_of_another_organization_does_not_exist(
    pool, tenant, logged_in_client
) -> None:
    async with pool.acquire() as conn:
        other_org = await conn.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-p4') RETURNING id"
        )
        other_ws = await conn.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'ws') RETURNING id",
            other_org,
        )
        other_project = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name) "
            "VALUES ($1, $2, 'cudzy') RETURNING id",
            other_org,
            other_ws,
        )
    response = await logged_in_client.get(
        f"/api/workspaces/{other_ws}/projects/{other_project}/delegation"
    )
    assert response.status_code == 404


async def test_the_presence_read_says_what_state_the_project_is_in(
    pool, tenant, logged_in_client
) -> None:
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    presence_path = f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/presence"

    before = await logged_in_client.get(presence_path)
    assert before.status_code == 200, before.text
    assert before.json()["state"] == "niepowierzony"
    assert before.json()["budget"] is None

    await logged_in_client.post(_path(tenant, project_id), json=_body(account_id))
    after = await logged_in_client.get(presence_path)
    assert after.json()["state"] == "czuwa"
    assert after.json()["budget"]["limit_publikacji_doba"] == 6
    assert after.json()["budget"]["wyczerpany"] is None
    assert after.json()["wakeups"] == []

    await logged_in_client.post(_path(tenant, project_id, "/pause"))
    assert (await logged_in_client.get(presence_path)).json()["state"] == "wstrzymany"


async def test_the_collector_is_reported_next_to_the_state_and_not_inside_it(
    pool, tenant, logged_in_client
) -> None:
    """„Czuwa" czyta się jak „nic się nie dzieje", a kolektor w tym czasie pobiera wątki (E6).

    Liczba wraca pod własną nazwą, bo kolektor należy do PRZESTRZENI: jedna obserwowana
    społeczność karmi wszystkie projekty, więc dopisanie jej pracy do stanu jednego z nich
    byłoby tą samą pomyłką, co pokazywanie cudzych publikacji pod nazwą nowego projektu.

    `ostatni_odczyt` równe `None` przy obserwowanej społeczności znaczy „włączono i jeszcze nic
    nie pobrano", czyli trzecią odpowiedź, nie zero.
    """
    project_id = await _project(pool, tenant)
    presence_path = f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/presence"

    empty = (await logged_in_client.get(presence_path)).json()
    assert empty["collector"] == {"obserwowanych": 0, "znanych": 0, "ostatni_odczyt": None}

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO communities (organization_id, workspace_id, platform, community_key, "
            "display_name, access_mode, poll_interval_seconds) "
            "VALUES ($1, $2, 'REDDIT', 'r/saas', 'r/SaaS', 'PUBLIC_API_NO_ACCOUNT', 600)",
            tenant["organization"],
            tenant["workspace"],
        )

    waiting = (await logged_in_client.get(presence_path)).json()
    assert waiting["collector"]["obserwowanych"] == 1
    assert waiting["collector"]["ostatni_odczyt"] is None, "włączono, ale nic jeszcze nie pobrano"
    assert waiting["state"] == "niepowierzony", "kolektor nie zmienia stanu POWIERZENIA"

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE communities SET last_polled_at = now() WHERE organization_id = $1",
            tenant["organization"],
        )

    reading = (await logged_in_client.get(presence_path)).json()
    assert reading["collector"]["ostatni_odczyt"] is not None
    assert reading["state"] == "niepowierzony"


async def test_the_decision_queue_lists_what_still_waits_for_a_person(
    pool, tenant, logged_in_client
) -> None:
    """REVIEW mode's whole workflow, and in AUTONOMOUS the half that was refused."""
    project_id = await _project(pool, tenant)
    account_id = await _account(pool, tenant, project_id)
    decisions_path = f"/api/workspaces/{tenant['workspace']}/projects/{project_id}/decisions"
    assert (await logged_in_client.get(decisions_path)).json()["decisions"] == []

    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from community_os_contracts.enums import SemanticCapability
    from community_os_contracts.execution import ActionProposalState, ApprovalHashInput
    from control_plane.approval.store import ApprovalStore

    store = ApprovalStore(pool)
    proposal_id = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=UUID(account_id),
    )
    await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=proposal_id,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid4(),
            account=UUID(account_id),
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id="t3_abc999",
            target_url="https://www.reddit.com/r/devops/comments/abc999/x/",
            source_fingerprint=f"session:{uuid4()}",
            text_and_media="tekst, który ktoś ma przeczytać",
            intelligence_profile_version=uuid4(),
            plan_version=UUID(project_id),
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )

    listed = (await logged_in_client.get(decisions_path)).json()["decisions"]
    assert len(listed) == 1, listed
    assert listed[0]["text"] == "tekst, który ktoś ma przeczytać"
    assert listed[0]["target_external_id"] == "t3_abc999"
    assert len(listed[0]["payload_hash"]) == 64
    assert listed[0]["image"] is None

    # 27.09: a draft with a screenshot shows the text that would go out and names the image
    # apart; the consented field's last line is never shown to a person as if it were text.
    from control_plane.pilot.publication import MediaRef, join_media

    media = MediaRef(uuid4(), "sha256:" + "ab" * 32)
    second = await store.create_proposal(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        social_account_id=UUID(account_id),
    )
    await store.record_revision(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        proposal_id=second,
        revision_no=1,
        hash_input=ApprovalHashInput(
            organization=tenant["organization"],
            workspace=tenant["workspace"],
            assignment_version=uuid4(),
            account=UUID(account_id),
            platform="REDDIT",
            action_type=SemanticCapability.SUBMIT_COMMENT,
            target_external_id="t3_abc998",
            target_url="https://www.reddit.com/r/devops/comments/abc998/x/",
            source_fingerprint=f"session:{uuid4()}",
            text_and_media=join_media("tekst ze zrzutem", media),
            intelligence_profile_version=uuid4(),
            plan_version=UUID(project_id),
            memory_snapshot=uuid4(),
            adapter_version="wenext-session-draft/1",
        ),
        state=ActionProposalState.READY_FOR_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    listed = (await logged_in_client.get(decisions_path)).json()["decisions"]
    with_image = next(one for one in listed if one["target_external_id"] == "t3_abc998")
    assert with_image["text"] == "tekst ze zrzutem"
    assert with_image["image"] == str(media.artifact_id)
