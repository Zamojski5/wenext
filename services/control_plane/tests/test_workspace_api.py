"""`/api/workspaces` end to end against PostgreSQL — the P1 completion criteria, one per test.

ROADMAP-PIVOT P1 lists what has to be true before the milestone counts, and every sentence of
that list is a test here:

  * two workspaces do not see each other's data,
  * two person profiles do not mix identities,
  * a project takes more than one assignment,
  * detaching does not delete history,
  * an account on a platform with no integration does not pretend to have one.

Everything goes through HTTP, with a session obtained by presenting the password — never a
hand-made cookie and never a direct call into the store. A test that reached past the router
would prove the store works and say nothing about whether the route is reachable, scoped to the
session's organization, or mounted at all.

THE READS ARE CHECKED FOR ABSENCE AS WELL AS PRESENCE. "Workspace B does not show A's project"
is only evidence if B's read succeeded and returned a list that is genuinely missing it, so each
isolation test asserts the 200, asserts what B DOES see, and only then asserts what it does not.
A 404 or an error would otherwise pass as isolation.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db

WORKSPACES = "/api/workspaces"


async def _create_workspace(client, name: str) -> str:
    response = await client.post(WORKSPACES, json={"name": name})
    assert response.status_code == 201, response.content[:300]
    return response.json()["workspace"]["workspace_id"]


async def _create_project(client, workspace_id: str, name: str, **extra) -> dict:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/projects", json={"name": name, **extra}
    )
    assert response.status_code == 201, response.content[:300]
    return response.json()["project"]


async def _create_profile(client, workspace_id: str, name: str, **extra) -> dict:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/profiles", json={"name": name, **extra}
    )
    assert response.status_code == 201, response.content[:300]
    return response.json()["profile"]


async def _add_account(client, workspace_id: str, platform: str, handle: str, profile_id=None):
    body = {"platform": platform, "external_account_id": handle}
    if profile_id is not None:
        body["person_profile_id"] = profile_id
    return await client.post(f"{WORKSPACES}/{workspace_id}/accounts", json=body)


async def test_the_sole_workspace_of_the_organization_is_listed(logged_in_client, tenant) -> None:
    """The fixture's workspace comes back, and it comes back from the SESSION's organization.

    The count is asserted before anything else: an empty list would make every "does not
    contain" assertion in this file pass for the wrong reason.
    """
    response = await logged_in_client.get(WORKSPACES)
    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["count"] >= 1, body
    ids = [row["workspace_id"] for row in body["workspaces"]]
    assert str(tenant["workspace"]) in ids, ids


async def test_a_workspace_of_another_organization_is_a_404_not_a_403(
    logged_in_client, pool
) -> None:
    """A foreign identifier and a non-existent one answer identically.

    A 403 would confirm that the workspace exists, which is a free map of another tenant for
    anybody willing to try identifiers.
    """
    async with pool.acquire() as connection:
        other_organization = await connection.fetchval(
            "INSERT INTO organizations (name) VALUES ('obca-p1') RETURNING id"
        )
        foreign_workspace = await connection.fetchval(
            "INSERT INTO workspaces (organization_id, name) VALUES ($1, 'obcy') RETURNING id",
            other_organization,
        )

    foreign = await logged_in_client.get(f"{WORKSPACES}/{foreign_workspace}")
    imaginary = await logged_in_client.get(f"{WORKSPACES}/{uuid.uuid4()}")
    assert foreign.status_code == 404, foreign.content[:300]
    assert imaginary.status_code == 404, imaginary.content[:300]


async def test_two_workspaces_do_not_see_each_other(logged_in_client, tenant) -> None:
    """P1: "dwa workspace'y nie widzą swoich danych" — asserted in both directions."""
    first = str(tenant["workspace"])
    second = await _create_workspace(logged_in_client, "Druga przestrzeń")
    assert second != first

    here = await _create_project(logged_in_client, first, "Obecność devops")
    there = await _create_project(logged_in_client, second, "Marka osobista")
    await _create_profile(logged_in_client, first, "Anna")
    await _create_profile(logged_in_client, second, "Jan")

    in_first = await logged_in_client.get(f"{WORKSPACES}/{first}/projects")
    in_second = await logged_in_client.get(f"{WORKSPACES}/{second}/projects")
    assert in_first.status_code == 200 and in_second.status_code == 200

    first_ids = [row["project_id"] for row in in_first.json()["projects"]]
    second_ids = [row["project_id"] for row in in_second.json()["projects"]]
    # Presence first: an empty list would satisfy every "not in" below.
    assert here["project_id"] in first_ids, first_ids
    assert there["project_id"] in second_ids, second_ids
    assert there["project_id"] not in first_ids, first_ids
    assert here["project_id"] not in second_ids, second_ids

    names_here = [
        row["name"]
        for row in (await logged_in_client.get(f"{WORKSPACES}/{first}/profiles")).json()["profiles"]
    ]
    names_there = [
        row["name"]
        for row in (await logged_in_client.get(f"{WORKSPACES}/{second}/profiles")).json()[
            "profiles"
        ]
    ]
    assert names_here == ["Anna"], names_here
    assert names_there == ["Jan"], names_there


async def test_a_project_of_another_workspace_is_a_404(logged_in_client, tenant) -> None:
    """The completion criterion names this one outright: reaching a project across workspaces."""
    first = str(tenant["workspace"])
    second = await _create_workspace(logged_in_client, "Druga przestrzeń")
    project = await _create_project(logged_in_client, first, "Obecność devops")

    through_own = await logged_in_client.get(
        f"{WORKSPACES}/{first}/projects/{project['project_id']}"
    )
    through_other = await logged_in_client.get(
        f"{WORKSPACES}/{second}/projects/{project['project_id']}"
    )
    assert through_own.status_code == 200, through_own.content[:300]
    assert through_other.status_code == 404, through_other.content[:300]


async def test_two_profiles_do_not_mix_identities(logged_in_client, tenant) -> None:
    """One account, one active profile. The second attach is a 409 with a sentence, not a crash.

    And the first attachment is still intact afterwards: a refusal that damaged the existing
    link would be worse than one that let the second one through.
    """
    workspace = str(tenant["workspace"])
    anna = await _create_profile(logged_in_client, workspace, "Anna")
    jan = await _create_profile(logged_in_client, workspace, "Jan")

    added = await _add_account(
        logged_in_client, workspace, "REDDIT", "Demo_Maintainer", anna["person_profile_id"]
    )
    assert added.status_code == 201, added.content[:300]
    account_id = added.json()["account"]["social_account_id"]

    stolen = await logged_in_client.post(
        f"{WORKSPACES}/{workspace}/profiles/{jan['person_profile_id']}/accounts",
        json={"social_account_id": account_id},
    )
    assert stolen.status_code == 409, stolen.content[:300]
    assert "profilu" in stolen.json()["detail"], stolen.json()

    profiles = (await logged_in_client.get(f"{WORKSPACES}/{workspace}/profiles")).json()
    active = [row for row in profiles["attachments"] if row["active"]]
    assert len(active) == 1, active
    assert active[0]["person_profile_id"] == anna["person_profile_id"], active


async def test_an_account_attached_in_one_workspace_is_invisible_in_the_other(
    logged_in_client, tenant
) -> None:
    """The half of isolation that concerns accounts, stated exactly as it is implemented.

    `social_accounts` stays organization-scoped (0001), so an UNATTACHED account is visible from
    both workspaces on purpose — there is one organization and one operator behind the boundary.
    The moment it is attached it belongs to one workspace and leaves the other one's list, and
    that transition is what this test measures.
    """
    first = str(tenant["workspace"])
    second = await _create_workspace(logged_in_client, "Druga przestrzeń")

    free = await _add_account(logged_in_client, first, "REDDIT", "WolneKonto")
    assert free.status_code == 201, free.content[:300]
    free_id = free.json()["account"]["social_account_id"]

    def handles(body):
        return {row["social_account_id"] for row in body["accounts"]}

    before_second = (await logged_in_client.get(f"{WORKSPACES}/{second}/accounts")).json()
    assert free_id in handles(before_second), before_second

    anna = await _create_profile(logged_in_client, first, "Anna")
    attached = await logged_in_client.post(
        f"{WORKSPACES}/{first}/profiles/{anna['person_profile_id']}/accounts",
        json={"social_account_id": free_id},
    )
    assert attached.status_code == 201, attached.content[:300]

    after_first = (await logged_in_client.get(f"{WORKSPACES}/{first}/accounts")).json()
    after_second = (await logged_in_client.get(f"{WORKSPACES}/{second}/accounts")).json()
    assert free_id in handles(after_first), after_first
    assert free_id not in handles(after_second), after_second


async def test_detaching_keeps_the_history_and_frees_the_account(logged_in_client, tenant) -> None:
    """P1: "odłączenie nie usuwa historii". Three assertions, and the first one is the point."""
    workspace = str(tenant["workspace"])
    anna = await _create_profile(logged_in_client, workspace, "Anna")
    jan = await _create_profile(logged_in_client, workspace, "Jan")
    added = await _add_account(
        logged_in_client, workspace, "REDDIT", "Demo_Maintainer", anna["person_profile_id"]
    )
    account_id = added.json()["account"]["social_account_id"]

    detached = await logged_in_client.delete(
        f"{WORKSPACES}/{workspace}/profiles/{anna['person_profile_id']}/accounts/{account_id}"
    )
    assert detached.status_code == 204, detached.content[:300]

    body = (await logged_in_client.get(f"{WORKSPACES}/{workspace}/profiles")).json()
    history = [row for row in body["attachments"] if row["social_account_id"] == account_id]
    assert len(history) == 1, history
    assert history[0]["active"] is False, history
    assert history[0]["detached_at"] is not None, history
    assert history[0]["person_profile_id"] == anna["person_profile_id"], history

    # Freed, so it can belong to somebody else now.
    moved = await logged_in_client.post(
        f"{WORKSPACES}/{workspace}/profiles/{jan['person_profile_id']}/accounts",
        json={"social_account_id": account_id},
    )
    assert moved.status_code == 201, moved.content[:300]

    # And a second detach of something already detached is a 404, not a second cheerful 204.
    again = await logged_in_client.delete(
        f"{WORKSPACES}/{workspace}/profiles/{anna['person_profile_id']}/accounts/{account_id}"
    )
    assert again.status_code == 404, again.content[:300]


async def test_a_project_takes_more_than_one_account(logged_in_client, tenant) -> None:
    """ "Projekt obsługuje więcej niż jedno przypisanie", and one account serves two projects."""
    workspace = str(tenant["workspace"])
    anna = await _create_profile(logged_in_client, workspace, "Anna")
    jan = await _create_profile(logged_in_client, workspace, "Jan")
    first_account = (
        await _add_account(
            logged_in_client, workspace, "REDDIT", "Demo_Maintainer", anna["person_profile_id"]
        )
    ).json()["account"]["social_account_id"]
    second_account = (
        await _add_account(
            logged_in_client, workspace, "HACKER_NEWS", "jan_hn", jan["person_profile_id"]
        )
    ).json()["account"]["social_account_id"]

    project = await _create_project(logged_in_client, workspace, "Obecność devops")
    other_project = await _create_project(logged_in_client, workspace, "Research")
    base = f"{WORKSPACES}/{workspace}/projects/{project['project_id']}/accounts"

    for account in (first_account, second_account):
        assigned = await logged_in_client.post(base, json={"social_account_id": account})
        assert assigned.status_code == 201, assigned.content[:300]

    detail = (
        await logged_in_client.get(f"{WORKSPACES}/{workspace}/projects/{project['project_id']}")
    ).json()
    active = [row for row in detail["assignments"] if row["active"]]
    assert len(active) == 2, active

    # The same account in a second project is legal; only the same PAIR twice is not.
    shared = await logged_in_client.post(
        f"{WORKSPACES}/{workspace}/projects/{other_project['project_id']}/accounts",
        json={"social_account_id": first_account},
    )
    assert shared.status_code == 201, shared.content[:300]
    twice = await logged_in_client.post(base, json={"social_account_id": first_account})
    assert twice.status_code == 409, twice.content[:300]


async def test_unassigning_keeps_the_row(logged_in_client, tenant) -> None:
    workspace = str(tenant["workspace"])
    profile = await _create_profile(logged_in_client, workspace, "Anna")
    account = (
        await _add_account(
            logged_in_client, workspace, "REDDIT", "Demo_Maintainer", profile["person_profile_id"]
        )
    ).json()["account"]["social_account_id"]
    project = await _create_project(logged_in_client, workspace, "Obecność devops")
    base = f"{WORKSPACES}/{workspace}/projects/{project['project_id']}/accounts"
    await logged_in_client.post(base, json={"social_account_id": account})

    removed = await logged_in_client.delete(f"{base}/{account}")
    assert removed.status_code == 204, removed.content[:300]

    detail = (
        await logged_in_client.get(f"{WORKSPACES}/{workspace}/projects/{project['project_id']}")
    ).json()
    assert len(detail["assignments"]) == 1, detail["assignments"]
    assert detail["assignments"][0]["active"] is False, detail["assignments"]
    assert detail["assignments"][0]["unassigned_at"] is not None, detail["assignments"]


async def test_an_account_of_no_profile_cannot_be_assigned_to_a_project(
    logged_in_client, tenant
) -> None:
    """A project may only use an identity its workspace owns.

    Without this, a project could publish under an account nobody in this workspace claims, and
    the identity is the one thing in this area that costs reputation rather than time.
    """
    workspace = str(tenant["workspace"])
    free = (await _add_account(logged_in_client, workspace, "REDDIT", "Niczyje")).json()
    project = await _create_project(logged_in_client, workspace, "Obecność devops")
    refused = await logged_in_client.post(
        f"{WORKSPACES}/{workspace}/projects/{project['project_id']}/accounts",
        json={"social_account_id": free["account"]["social_account_id"]},
    )
    assert refused.status_code == 400, refused.content[:300]
    assert "profilu" in refused.json()["detail"], refused.json()


async def test_an_account_on_an_unsupported_platform_says_so(logged_in_client, tenant) -> None:
    """P1's last sentence: "konto nieobsługiwanej platformy nie udaje aktywnej integracji".

    The account is ACCEPTED — X and LinkedIn are real identities and the model covers them from
    the start — and every capability comes back false with a sentence the screen prints.
    """
    workspace = str(tenant["workspace"])
    profile = await _create_profile(logged_in_client, workspace, "Anna")
    added = await _add_account(
        logged_in_client, workspace, "LINKEDIN", "anna", profile["person_profile_id"]
    )
    assert added.status_code == 201, added.content[:300]
    support = added.json()["account"]["support"]
    assert support["integrated"] is False, support
    assert support["read"] is False and support["comment"] is False, support
    assert support["note"] == "integracja niedostępna", support

    reddit = await _add_account(
        logged_in_client, workspace, "REDDIT", "Demo_Maintainer", profile["person_profile_id"]
    )
    reddit_support = reddit.json()["account"]["support"]
    assert reddit_support["integrated"] is True, reddit_support
    assert reddit_support["comment"] is True, reddit_support
    # Nothing reads follower counts on Reddit (only X does, since 27.09), and the matrix says so
    # rather than letting the screen print a zero (ZAKRES-FUNKCJONALNY §6 rule 2).
    assert reddit_support["audience_metrics"] is False, reddit_support


async def test_the_accounts_screen_gets_the_latest_follower_reading_and_no_zero(
    logged_in_client, pool, tenant
) -> None:
    """P6 etap A (27.09): the "Profile i konta" read carries the latest follower reading of each
    account that has one. An account nobody measured is ABSENT from the list, not listed with 0."""
    from datetime import UTC, datetime, timedelta

    from control_plane.workspace.audience import record_followers

    workspace = str(tenant["workspace"])
    profile = await _create_profile(logged_in_client, workspace, "Anna")
    x = (
        await _add_account(
            logged_in_client, workspace, "X", "demo_founder", profile["person_profile_id"]
        )
    ).json()["account"]
    await _add_account(
        logged_in_client, workspace, "REDDIT", "Demo_Maintainer", profile["person_profile_id"]
    )
    async with pool.acquire() as conn:
        await record_followers(
            conn,
            organization_id=tenant["organization"],
            social_account_id=uuid.UUID(x["social_account_id"]),
            followers=2,
            observed_at=datetime.now(UTC) - timedelta(minutes=5),
        )

    body = (await logged_in_client.get(f"{WORKSPACES}/{workspace}/profiles")).json()

    assert [(row["social_account_id"], row["obserwujacy"]) for row in body["audience"]] == [
        (x["social_account_id"], 2)
    ], body["audience"]
    assert body["audience"][0]["zmiana_doby"] is None, "zmiany doby nie znamy po jednym odczycie"
    assert x["support"]["audience_metrics"] is True


async def test_an_unknown_platform_is_refused(logged_in_client, tenant) -> None:
    """A typo is a refusal, not a fifth platform nobody declared."""
    workspace = str(tenant["workspace"])
    refused = await _add_account(logged_in_client, workspace, "MASTODON", "ktos")
    assert refused.status_code == 400, refused.content[:300]
    assert "MASTODON" in refused.json()["detail"], refused.json()


async def test_registering_the_same_handle_twice_does_not_create_a_second_account(
    logged_in_client, tenant
) -> None:
    """`created: false` is the honest answer: 0001 already makes the handle unique per org."""
    workspace = str(tenant["workspace"])
    first = await _add_account(logged_in_client, workspace, "REDDIT", "Demo_Maintainer")
    second = await _add_account(logged_in_client, workspace, "REDDIT", "Demo_Maintainer")
    assert first.json()["created"] is True, first.json()
    assert second.json()["created"] is False, second.json()
    assert (
        first.json()["account"]["social_account_id"]
        == second.json()["account"]["social_account_id"]
    )


async def test_the_project_mode_is_stored_and_changed(logged_in_client, tenant) -> None:
    """Three modes, per project, surviving between requests (WIZJA-WORKSPACE, D-038)."""
    workspace = str(tenant["workspace"])
    project = await _create_project(
        logged_in_client, workspace, "Obecność devops", mode="RESEARCH", goal="Zbadaj odzew"
    )
    assert project["mode"] == "RESEARCH", project
    assert project["goal"] == "Zbadaj odzew", project

    changed = await logged_in_client.patch(
        f"{WORKSPACES}/{workspace}/projects/{project['project_id']}",
        json={"mode": "AUTONOMOUS", "paused": True},
    )
    assert changed.status_code == 200, changed.content[:300]
    assert changed.json()["project"]["mode"] == "AUTONOMOUS", changed.json()
    assert changed.json()["project"]["paused"] is True, changed.json()

    # Read it back through a second request: a value kept only in the response is not storage.
    again = await logged_in_client.get(f"{WORKSPACES}/{workspace}/projects/{project['project_id']}")
    assert again.json()["project"]["mode"] == "AUTONOMOUS", again.json()
    assert again.json()["project"]["paused"] is True, again.json()
    assert again.json()["project"]["goal"] == "Zbadaj odzew", again.json()


async def test_an_unknown_mode_is_refused_and_an_unknown_field_is_refused(
    logged_in_client, tenant
) -> None:
    """Both halves of "a typo must not become a category", on the PATCH that is easiest to widen."""
    workspace = str(tenant["workspace"])
    project = await _create_project(logged_in_client, workspace, "Obecność devops")
    path = f"{WORKSPACES}/{workspace}/projects/{project['project_id']}"

    bad_mode = await logged_in_client.patch(path, json={"mode": "AUTOPILOT"})
    assert bad_mode.status_code == 400, bad_mode.content[:300]

    # `extra="forbid"`: a misspelled field must not be ignored while the caller is told it worked.
    bad_field = await logged_in_client.patch(path, json={"modee": "REVIEW"})
    assert bad_field.status_code == 422, bad_field.content[:300]


@pytest.mark.parametrize("blank", ["   ", "\t", "\n", " \t\n "])
async def test_a_blank_name_is_refused(logged_in_client, tenant, blank) -> None:
    """Every kind of whitespace, not just the space.

    `_checked_name` uses Python's `str.strip()`, which trims all of these; the database CHECK
    behind it had to be widened separately, because `btrim(x)` with no second argument trims
    SPACES ONLY and let `E'\t'` through (measured by the P2 session). Two layers, one rule, and
    this table is the one that would catch the API half drifting back.
    """
    workspace = str(tenant["workspace"])
    for path in (f"{WORKSPACES}/{workspace}/projects", f"{WORKSPACES}/{workspace}/profiles"):
        refused = await logged_in_client.post(path, json={"name": blank})
        assert refused.status_code == 400, f"{path}: {refused.content[:300]!r}"


async def test_every_write_leaves_an_audit_entry(logged_in_client, tenant, pool) -> None:
    """The five writes of P1, each with its own kind, attributed to the operator.

    The set is compared for EQUALITY of what the scenario should have produced, not for
    containment: an endpoint that wrote no entry at all would pass a `>=` check as long as some
    other one did.
    """
    workspace = str(tenant["workspace"])
    profile = await _create_profile(logged_in_client, workspace, "Anna")
    account = (
        await _add_account(
            logged_in_client, workspace, "REDDIT", "Demo_Maintainer", profile["person_profile_id"]
        )
    ).json()["account"]["social_account_id"]
    project = await _create_project(logged_in_client, workspace, "Obecność devops")
    base = f"{WORKSPACES}/{workspace}/projects/{project['project_id']}/accounts"
    await logged_in_client.post(base, json={"social_account_id": account})
    await logged_in_client.delete(f"{base}/{account}")
    await logged_in_client.delete(
        f"{WORKSPACES}/{workspace}/profiles/{profile['person_profile_id']}/accounts/{account}"
    )

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT kind, actor_ref FROM audit_events WHERE organization_id = $1"
            " AND kind LIKE ANY (ARRAY['PROJECT%', 'PERSON%', 'ACCOUNT%', 'SOCIAL%', 'WORKSPACE%'])",
            tenant["organization"],
        )
    kinds = sorted({row["kind"] for row in rows})
    assert kinds == [
        "ACCOUNT_ASSIGNED_TO_PROJECT",
        "ACCOUNT_ATTACHED_TO_PROFILE",
        "ACCOUNT_DETACHED_FROM_PROFILE",
        "ACCOUNT_UNASSIGNED_FROM_PROJECT",
        "PERSON_PROFILE_CREATED",
        "PROJECT_CREATED",
        "SOCIAL_ACCOUNT_REGISTERED",
    ], kinds
    assert {row["actor_ref"] for row in rows} == {f"operator:{tenant['operator']}"}


async def test_no_route_of_this_module_is_reachable_without_a_session(client, tenant) -> None:
    """The boundary, measured on THESE routes rather than assumed from the discovery loop.

    Every answer is an empty 401 — including the ones that would be 404 with a session, so a
    caller without one cannot tell an existing workspace from an invented identifier.
    """
    workspace = str(tenant["workspace"])
    paths = [
        ("GET", WORKSPACES),
        ("POST", WORKSPACES),
        ("GET", f"{WORKSPACES}/{workspace}"),
        ("GET", f"{WORKSPACES}/{workspace}/projects"),
        ("POST", f"{WORKSPACES}/{workspace}/projects"),
        ("GET", f"{WORKSPACES}/{workspace}/profiles"),
        ("POST", f"{WORKSPACES}/{workspace}/profiles"),
        ("GET", f"{WORKSPACES}/{workspace}/accounts"),
        ("POST", f"{WORKSPACES}/{workspace}/accounts"),
        ("GET", f"{WORKSPACES}/{workspace}/results"),
    ]
    assert len(paths) == 10, paths
    for method, path in paths:
        response = await client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"
        assert response.content == b"", f"{method} {path}: {response.content[:200]!r}"


async def test_results_report_an_empty_workspace_as_measured_zero(logged_in_client, tenant) -> None:
    """A workspace with nothing in it answers with empty lists and zero totals, and the read
    SUCCEEDS. The screen turns that into "brak danych"; a failed read would have to say
    something else entirely, and the two must not look alike."""
    workspace = str(tenant["workspace"])
    response = await logged_in_client.get(f"{WORKSPACES}/{workspace}/results")
    assert response.status_code == 200, response.content[:300]
    body = response.json()
    assert body["communities"] == [], body
    assert body["publications"] == [], body
    assert body["totals"] == {
        "communities": 0,
        "threads": 0,
        "published": 0,
        "confirmed_visible": 0,
    }, body


async def test_results_read_real_rows_of_this_workspace_only(
    logged_in_client, tenant, pool
) -> None:
    """A community written with raw SQL shows up in the workspace that owns it, and nowhere else.

    Raw SQL rather than the observe API on purpose: the point is that `results` reads the
    tables the rest of the product already writes, not a table of its own.
    """
    workspace = str(tenant["workspace"])
    other = await _create_workspace(logged_in_client, "Druga przestrzeń")
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO communities
                (organization_id, workspace_id, platform, community_key, display_name,
                 access_mode, observe_enabled, poll_interval_seconds)
            VALUES ($1, $2, 'REDDIT', 'kubernetes', 'r/kubernetes', 'PUBLIC_API_NO_ACCOUNT',
                    true, 600)
            """,
            tenant["organization"],
            tenant["workspace"],
        )

    mine = (await logged_in_client.get(f"{WORKSPACES}/{workspace}/results")).json()
    theirs = (await logged_in_client.get(f"{WORKSPACES}/{other}/results")).json()
    assert [row["community_key"] for row in mine["communities"]] == ["kubernetes"], mine
    assert mine["totals"]["communities"] == 1, mine
    assert theirs["communities"] == [], theirs
    assert theirs["totals"]["communities"] == 0, theirs


async def test_a_second_workspace_does_not_break_the_operator_screens(
    logged_in_client, tenant
) -> None:
    """The regression P1 created and this fixes, measured on the route that broke.

    `/api/user-profile` resolved its workspace with `ObservationStore.sole_workspace`, which
    answers None as soon as there are two — so the first workspace a user created from the studio
    turned the whole operator panel's profile read into a 409, and `/community` rendered "brak
    odpowiedzi". Found by the end-to-end suite, not by reasoning.

    The assertion is a 200 with the SAME profile, not merely "not a 409": the route now picks the
    organization's oldest workspace, and picking a different one would answer 404 here — which
    would be the same screen going dark by another route.
    """
    saved = await logged_in_client.post(
        "/api/user-profile",
        json={
            "knows_deeply": "kolejki na Postgresie",
            "positions": "nudna technologia wygrywa",
            "would_never_claim": "benchmarków, których nie robił",
            "voice_samples": "",
        },
    )
    assert saved.status_code == 201, saved.content[:300]

    before = await logged_in_client.get("/api/user-profile")
    assert before.status_code == 200, before.content[:300]

    second = await _create_workspace(logged_in_client, "Druga przestrzeń")
    assert second != str(tenant["workspace"])

    after = await logged_in_client.get("/api/user-profile")
    assert after.status_code == 200, (
        f"po dodaniu drugiego workspace'u profil odpowiada {after.status_code}. Ekrany operatora "
        "mają dalej działać na najstarszym workspace organizacji"
    )
    assert after.json()["profile_id"] == before.json()["profile_id"], after.json()
