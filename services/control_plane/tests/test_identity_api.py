"""Two customers on one deployment, end to end against PostgreSQL (P7 etap A).

THE MILESTONE'S CRITERION, AS CASES. A new user creates their own space, connects, entrusts a
goal and comes back to results WITHOUT the founder touching the database; a second customer
shares nothing with the first; data can be exported and deleted; onboarding needs no operator
console. What is measured here is the backend half of that, through the real HTTP surface:

  * registration mints a session and an organization nobody had to provision;
  * a workspace of another organization answers 404 — the same answer as one that does not
    exist, because 403 confirms the identifier is real;
  * an invited third person joins the second organization and still sees nothing of the first;
  * an export of A contains A and nothing of B;
  * deleting B blocks B's sign-in at once, and after the grace period B's rows are gone while
    A's are untouched;
  * the founder's operator password keeps working, because migration 0018 moved that account
    rather than replacing it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from control_plane.auth import SESSIONS

pytestmark = pytest.mark.db

PASSWORD = "wystarczajaco-dlugie-haslo"


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://control-plane")


async def register(client, *, email: str, name: str) -> dict:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "organization_name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]


async def workspace_of(client, *, name: str) -> str:
    created = await client.post("/api/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["workspace"]["workspace_id"]


@pytest.fixture
def stamp() -> str:
    """A suffix nothing else can collide with.

    The throwaway PostgreSQL lives for the WHOLE run, so two tests registering "ala@pierwsza.test"
    are two tests fighting over one unique index. Measured the hard way: the second case in the
    file came back 400 with the refusal this module deliberately makes indistinguishable from a
    mistyped password.
    """
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def two_customers(app, pool, stamp):
    """Two organizations, each with an owner, a workspace and a project. No console anywhere."""
    first = await client_for(app)
    second = await client_for(app)
    email_a, email_b = f"ala-{stamp}@pierwsza.test", f"bogdan-{stamp}@druga.test"
    session_a = await register(first, email=email_a, name="Pierwsza")
    session_b = await register(second, email=email_b, name="Druga")
    workspace_a = await workspace_of(first, name="Przestrzeń A")
    workspace_b = await workspace_of(second, name="Przestrzeń B")
    await first.post(
        f"/api/workspaces/{workspace_a}/projects", json={"name": "Projekt A", "goal": "cel A"}
    )
    await second.post(
        f"/api/workspaces/{workspace_b}/projects", json={"name": "Projekt B", "goal": "cel B"}
    )
    try:
        yield {
            "a": {
                "client": first,
                "session": session_a,
                "workspace": workspace_a,
                "email": email_a,
            },
            "b": {
                "client": second,
                "session": session_b,
                "workspace": workspace_b,
                "email": email_b,
            },
        }
    finally:
        await first.aclose()
        await second.aclose()


# ---------------------------------------------------------------------------
# Registration and login
# ---------------------------------------------------------------------------


async def test_a_new_customer_creates_their_space_without_anybody_provisioning_it(
    app, stamp
) -> None:
    async with await client_for(app) as client:
        session = await register(client, email=f"nowa-{stamp}@firma.test", name="Nowa firma")

        assert session["role"] == "OWNER"
        assert session["organization_name"] == "Nowa firma"
        assert session["needs_email"] is False
        # The session works straight away: no second step, no operator password.
        who = await client.get("/api/session")
        assert who.status_code == 200
        assert who.json()["organization_id"] == session["organization_id"]


async def test_registration_can_be_closed_by_the_deployment_and_says_so(app, monkeypatch) -> None:
    from control_plane.api.auth import REGISTRATION_VARIABLE

    monkeypatch.setenv(REGISTRATION_VARIABLE, "0")
    async with await client_for(app) as client:
        refused = await client.post(
            "/api/auth/register",
            json={"email": "za@pozno.test", "password": PASSWORD, "organization_name": "X"},
        )
    assert refused.status_code == 403
    assert "zaproszenie" in refused.json()["detail"]


async def test_login_returns_a_session_and_a_wrong_password_returns_an_empty_401(
    app, stamp
) -> None:
    async with await client_for(app) as client:
        await register(client, email=f"logujaca-{stamp}@firma.test", name="Logująca")
        SESSIONS.clear()

        good = await client.post(
            "/api/auth/login", json={"email": f"logujaca-{stamp}@firma.test", "password": PASSWORD}
        )
        assert good.status_code == 200
        assert good.json()["session"]["email"] == f"logujaca-{stamp}@firma.test"

        bad = await client.post(
            "/api/auth/login", json={"email": f"logujaca-{stamp}@firma.test", "password": "nie to"}
        )
        assert bad.status_code == 401
        assert bad.content == b"", "odmowa nie może nieść ciała ani wskazówki"


async def test_the_founders_operator_password_still_opens_a_session(client, tenant) -> None:
    """0018 moved the operator to `users`; it did not take the old door away."""
    opened = await client.post("/api/session", json={"password": tenant["password"]})
    assert opened.status_code == 204
    who = await client.get("/api/session")
    assert who.status_code == 200
    assert who.json()["organization_id"] == str(tenant["organization"])


# ---------------------------------------------------------------------------
# The boundary between two customers
# ---------------------------------------------------------------------------


async def test_the_second_customer_sees_nothing_of_the_first(two_customers) -> None:
    a, b = two_customers["a"], two_customers["b"]

    # Every route that takes a workspace answers 404 for the other organization's one. 404 and
    # not 403: a 403 would confirm that the identifier names something real.
    for path in (
        f"/api/workspaces/{a['workspace']}",
        f"/api/workspaces/{a['workspace']}/projects",
        f"/api/workspaces/{a['workspace']}/profiles",
        f"/api/workspaces/{a['workspace']}/accounts",
        f"/api/workspaces/{a['workspace']}/results",
        f"/api/workspaces/{a['workspace']}/knowledge",
    ):
        answer = await b["client"].get(path)
        assert answer.status_code == 404, (path, answer.status_code)

    # And the listing shows only one's own.
    mine = await b["client"].get("/api/workspaces")
    ids = [row["workspace_id"] for row in mine.json()["workspaces"]]
    assert ids == [b["workspace"]], ids


async def test_an_invited_third_person_joins_one_space_and_only_that_one(
    app, two_customers, stamp
) -> None:
    a, b = two_customers["a"], two_customers["b"]
    invited = await b["client"].post(
        f"/api/organizations/{b['session']['organization_id']}/invitations",
        json={"email": f"czeslaw-{stamp}@druga.test"},
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["token_to_pass_on"]
    assert token and token not in invited.json()["invitation"].get("token_hash", "")

    async with await client_for(app) as third:
        joined = await third.post(
            "/api/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        assert joined.status_code == 201, joined.text
        assert joined.json()["session"]["role"] == "MEMBER"
        assert joined.json()["session"]["organization_id"] == b["session"]["organization_id"]

        # B's data, yes. A's, not even its existence.
        assert (await third.get(f"/api/workspaces/{b['workspace']}")).status_code == 200
        assert (await third.get(f"/api/workspaces/{a['workspace']}")).status_code == 404


async def test_only_an_owner_invites_and_revokes(app, two_customers, stamp) -> None:
    b = two_customers["b"]
    organization = b["session"]["organization_id"]
    invited = await b["client"].post(
        f"/api/organizations/{organization}/invitations",
        json={"email": f"czlonek-{stamp}@druga.test"},
    )
    token = invited.json()["token_to_pass_on"]

    async with await client_for(app) as member:
        await member.post(
            "/api/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        refused = await member.post(
            f"/api/organizations/{organization}/invitations",
            json={"email": f"kolejny-{stamp}@druga.test"},
        )
        assert refused.status_code == 403
        assert "właściciel" in refused.json()["detail"]


async def test_revoking_access_stops_the_next_request_not_the_next_login(
    app, two_customers, stamp
) -> None:
    """The cookie stays valid for hours; the membership does not. The check is per request."""
    b = two_customers["b"]
    organization = b["session"]["organization_id"]
    invited = await b["client"].post(
        f"/api/organizations/{organization}/invitations",
        json={"email": f"wyrzucony-{stamp}@druga.test"},
    )
    token = invited.json()["token_to_pass_on"]

    async with await client_for(app) as member:
        joined = await member.post(
            "/api/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        )
        user_id = joined.json()["session"]["user_id"]
        assert (await member.get(f"/api/workspaces/{b['workspace']}")).status_code == 200

        removed = await b["client"].delete(f"/api/organizations/{organization}/members/{user_id}")
        assert removed.status_code == 204

        after = await member.get(f"/api/workspaces/{b['workspace']}")
        assert after.status_code == 401, "odebrany dostęp musi działać od następnego żądania"


async def test_an_organization_in_the_path_that_is_not_yours_is_a_404(two_customers) -> None:
    a, b = two_customers["a"], two_customers["b"]
    answer = await b["client"].get(f"/api/organizations/{a['session']['organization_id']}/members")
    assert answer.status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_an_export_holds_this_organization_and_nothing_of_the_other(
    app, two_customers, pool, tmp_path
) -> None:
    from control_plane.identity.data import DataStore
    from control_plane.storage.artifacts import ArtifactVolume

    a, b = two_customers["a"], two_customers["b"]
    # Something recognisable on both sides.
    await a["client"].post(
        f"/api/workspaces/{a['workspace']}/knowledge/items",
        json={"kind": "INTERPRETATION", "statement": "ustalenie-alicji", "stated_by": "USER"},
    )
    await b["client"].post(
        f"/api/workspaces/{b['workspace']}/knowledge/items",
        json={"kind": "INTERPRETATION", "statement": "ustalenie-bogdana", "stated_by": "USER"},
    )

    requested = await a["client"].post(
        f"/api/organizations/{a['session']['organization_id']}/data/exports"
    )
    assert requested.status_code == 202, requested.text
    job_id = requested.json()["export"]["job_id"]

    volume = ArtifactVolume(tmp_path / "artifacts")
    app.state.artifact_volume = volume
    finished = await DataStore(pool).run_export(
        organization_id=uuid.UUID(a["session"]["organization_id"]),
        job_id=uuid.UUID(job_id),
        volume=volume,
    )
    assert finished.status == "READY", finished.detail

    downloaded = await a["client"].get(
        f"/api/organizations/{a['session']['organization_id']}/data/exports/{job_id}/file"
    )
    assert downloaded.status_code == 200, downloaded.text
    payload = json.loads(downloaded.content)

    assert payload["eksport"]["nazwa"] == "Pierwsza"
    statements = [row["statement"] for row in payload["ustalenia"]]
    assert "ustalenie-alicji" in statements
    assert "ustalenie-bogdana" not in statements
    assert [row["name"] for row in payload["projekty"]] == ["Projekt A"]
    # No credentials anywhere in the file, whatever the person does with it afterwards.
    assert "credential_hash" not in downloaded.text
    assert "$argon2id$" not in downloaded.text


async def test_an_export_that_is_not_ready_is_a_404_with_its_state(two_customers) -> None:
    a = two_customers["a"]
    requested = await a["client"].post(
        f"/api/organizations/{a['session']['organization_id']}/data/exports"
    )
    job_id = requested.json()["export"]["job_id"]
    answer = await a["client"].get(
        f"/api/organizations/{a['session']['organization_id']}/data/exports/{job_id}/file"
    )
    assert answer.status_code == 404
    assert "REQUESTED" in answer.json()["detail"]


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_deleting_blocks_sign_in_at_once_and_removes_the_data_after_the_grace_period(
    app, two_customers, pool
) -> None:
    from control_plane.identity.data import DataStore

    a, b = two_customers["a"], two_customers["b"]
    organization_b = uuid.UUID(b["session"]["organization_id"])

    asked = await b["client"].post(
        f"/api/organizations/{organization_b}/data/deletion", json={"password": PASSWORD}
    )
    assert asked.status_code == 202, asked.text
    assert asked.json()["secrets_destroyed"] is True

    # Immediately: the open session stops working and a fresh login is refused.
    assert (await b["client"].get(f"/api/workspaces/{b['workspace']}")).status_code == 401
    async with await client_for(app) as again:
        refused = await again.post(
            "/api/auth/login", json={"email": b["email"], "password": PASSWORD}
        )
        assert refused.status_code == 401

    # A still works, and still has its data.
    assert (await a["client"].get(f"/api/workspaces/{a['workspace']}")).status_code == 200

    # The data is still there during the grace period: that is what the grace period is for.
    async with pool.acquire() as connection:
        before = await connection.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1", organization_b
        )
    assert before == 1

    # Time passes. Simulated by asking the store what it would do LATER rather than by moving
    # `purge_after` into the past: the schema refuses a grace period that ends before the request
    # was made, and it is right to — a test that edits its way around a constraint stops testing
    # the thing the constraint protects.
    purged = await DataStore(pool).purge(
        organization_id=organization_b, now=datetime.now(UTC) + timedelta(days=8)
    )
    assert purged is not None and purged.status == "PURGED"

    async with pool.acquire() as connection:
        left = await connection.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1", organization_b
        )
        theirs = await connection.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1",
            uuid.UUID(a["session"]["organization_id"]),
        )
        audit = await connection.fetchval(
            "SELECT count(*) FROM audit_events WHERE organization_id = $1", organization_b
        )
    assert left == 0, "dane usuniętej organizacji zostały"
    assert theirs == 1, "usunięcie jednej organizacji ruszyło dane drugiej"
    # Said out loud rather than attempted: the audit log is append-only by trigger, so a purge
    # cannot remove it and does not pretend to.
    assert audit > 0


async def test_deleting_needs_the_password_again(two_customers) -> None:
    b = two_customers["b"]
    refused = await b["client"].post(
        f"/api/organizations/{b['session']['organization_id']}/data/deletion",
        json={"password": "nie-to-haslo"},
    )
    assert refused.status_code == 403
    assert "hasło" in refused.json()["detail"]


async def test_a_deletion_can_be_undone_while_the_grace_period_lasts(
    app, two_customers, pool
) -> None:
    from control_plane.identity.data import DataStore

    b = two_customers["b"]
    organization_b = uuid.UUID(b["session"]["organization_id"])
    await b["client"].post(
        f"/api/organizations/{organization_b}/data/deletion", json={"password": PASSWORD}
    )

    assert await DataStore(pool).cancel_deletion(organization_id=organization_b)

    async with await client_for(app) as again:
        back = await again.post("/api/auth/login", json={"email": b["email"], "password": PASSWORD})
        assert back.status_code == 200, "po cofnięciu usunięcia logowanie musi wrócić"


async def test_purging_before_the_grace_period_does_nothing(two_customers, pool) -> None:
    from control_plane.identity.data import DataStore

    b = two_customers["b"]
    organization_b = uuid.UUID(b["session"]["organization_id"])
    await b["client"].post(
        f"/api/organizations/{organization_b}/data/deletion", json={"password": PASSWORD}
    )

    assert await DataStore(pool).purge(organization_id=organization_b) is None
    async with pool.acquire() as connection:
        left = await connection.fetchval(
            "SELECT count(*) FROM projects WHERE organization_id = $1", organization_b
        )
    assert left == 1, "karencja nie może być pozorna"


# ---------------------------------------------------------------------------
# Switching between spaces
# ---------------------------------------------------------------------------


async def test_a_person_in_two_spaces_can_move_between_them(app, two_customers) -> None:
    a, b = two_customers["a"], two_customers["b"]
    invited = await b["client"].post(
        f"/api/organizations/{b['session']['organization_id']}/invitations",
        json={"email": a["email"]},
    )
    token = invited.json()["token_to_pass_on"]
    await a["client"].post(
        "/api/auth/invitations/accept", json={"token": token, "password": "nieistotne"}
    )

    # Still in A, where the session was opened.
    spaces = await a["client"].get("/api/organizations")
    assert spaces.status_code == 200
    names = [row["name"] for row in spaces.json()["organizations"]]
    assert names == ["Pierwsza", "Druga"]

    moved = await a["client"].post(
        "/api/organizations/switch", json={"organization_id": b["session"]["organization_id"]}
    )
    assert moved.status_code == 200 and moved.json()["role"] == "MEMBER"
    # And now B's workspace is the one that answers.
    assert (await a["client"].get(f"/api/workspaces/{b['workspace']}")).status_code == 200
    assert (await a["client"].get(f"/api/workspaces/{a['workspace']}")).status_code == 404


async def test_switching_into_a_space_you_do_not_belong_to_is_a_404(two_customers) -> None:
    a, b = two_customers["a"], two_customers["b"]
    answer = await a["client"].post(
        "/api/organizations/switch", json={"organization_id": b["session"]["organization_id"]}
    )
    assert answer.status_code == 404
