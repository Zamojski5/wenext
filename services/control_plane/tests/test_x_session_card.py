"""The "Połącz X" card, through the real route: four states, and only one of them connects.

Driven over HTTP with a logged-in operator, so what is measured is what the dashboard's card
receives. The browser and the capsule are dumb doubles: the X source answers the identity it was
given, the capsule client records whether the human's screen was closed.

Since 27.09 the screen closes FIRST: the person signs in in a browser with no DevTools endpoint
(Google refuses a sign-in in one that has it), so the session can be read only after the capsule
returned to display-only on the same profile. One reading, after the restart.

  1. X shows its login screen   -> not connected, and the person is told the window is closed and
                                   how to open it again;
  2. a session, handle unread   -> not connected, and the sentence says it is our defect;
  3. a read that failed         -> not connected, our sentence, never "sign in again";
  4. somebody else's account    -> not connected when the project already has an X account;
  5. the right account          -> the account is created READY, assigned to the project and
                                   attached to the person the project speaks as.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from community_observer.x_browser import XSessionState
from control_plane.api import x_platform

pytestmark = [pytest.mark.db]

HANDLE = "demo_agent"


@dataclass
class DumbProbe:
    """Answers the next identity from a script; counts reads."""

    script: list[XSessionState]
    reads: int = 0

    async def session_state(self) -> XSessionState:
        self.reads += 1
        return self.script[min(self.reads - 1, len(self.script) - 1)]

    async def aclose(self) -> None:
        return None


@dataclass
class DumbCapsule:
    steer_open: bool = True
    closed: int = 0
    revoked: int = 0
    events: list[str] = field(default_factory=list)

    async def state(self) -> Any:
        return type("State", (), {"human_steer_open": self.steer_open})()

    async def close_human_session(self) -> Any:
        self.closed += 1
        self.steer_open = False
        return await self.state()

    async def revoke_sessions(self) -> int:
        self.revoked += 1
        return 1

    async def aclose(self) -> None:
        return None


def _wire(monkeypatch: pytest.MonkeyPatch, probe: DumbProbe, capsule: DumbCapsule) -> None:
    monkeypatch.setenv("WENEXT_BROWSER_READER_URL", "http://capsule:8765")
    monkeypatch.setenv("WENEXT_BROWSER_READER_TOKEN", "t")
    monkeypatch.setattr(x_platform, "x_probe_from_env", lambda env=None: probe)
    monkeypatch.setattr(x_platform, "CapsuleClient", lambda *a, **k: capsule)
    monkeypatch.setattr(x_platform, "GatewayClient", lambda *a, **k: capsule)


def _state(
    logged_in: bool, handle: str | None, error: str | None = None, *, login_wall: bool = False
) -> XSessionState:
    return XSessionState(logged_in, handle, datetime.now(UTC), error=error, login_wall=login_wall)


async def _project(pool, tenant, *, with_person: bool = True) -> dict[str, Any]:
    async with pool.acquire() as conn:
        project_id = await conn.fetchval(
            "INSERT INTO projects (organization_id, workspace_id, name, goal) "
            "VALUES ($1, $2, $3, 'cel') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            f"projekt-{uuid4().hex[:8]}",
        )
        person_id = None
        if with_person:
            person_id = await conn.fetchval(
                "INSERT INTO person_profiles (organization_id, workspace_id, name, kind) "
                "VALUES ($1, $2, $3, 'PERSON') RETURNING id",
                tenant["organization"],
                tenant["workspace"],
                f"osoba-{uuid4().hex[:6]}",
            )
            reddit = await conn.fetchval(
                "INSERT INTO social_accounts (organization_id, platform, external_account_id) "
                "VALUES ($1, 'REDDIT', $2) RETURNING id",
                tenant["organization"],
                f"r-{uuid4().hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO project_account_assignments (organization_id, workspace_id, "
                "project_id, social_account_id) VALUES ($1, $2, $3, $4)",
                tenant["organization"],
                tenant["workspace"],
                project_id,
                reddit,
            )
            await conn.execute(
                "INSERT INTO person_profile_accounts (organization_id, workspace_id, "
                "person_profile_id, social_account_id) VALUES ($1, $2, $3, $4)",
                tenant["organization"],
                tenant["workspace"],
                person_id,
                reddit,
            )
    return {"project": project_id, "person": person_id}


def _body(tenant, project) -> dict[str, str]:
    return {"workspace_id": str(tenant["workspace"]), "project_id": str(project["project"])}


async def test_a_login_screen_after_the_close_says_the_window_is_closed_and_how_to_reopen(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    capsule = DumbCapsule()
    probe = DumbProbe([_state(False, None, "X pokazał ekran logowania.", login_wall=True)])
    _wire(monkeypatch, probe, capsule)

    response = await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["polaczone"] is False and answer["konto"] is None
    assert "Okno logowania jest już zamknięte" in answer["detail"]
    assert "Połącz X" in answer["detail"]
    assert capsule.closed == 1, "najpierw zamknięcie ekranu: bez tego sesji nie da się odczytać"
    assert probe.reads == 1


async def test_an_unread_handle_is_not_a_connection_and_not_the_humans_fault(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    capsule = DumbCapsule()
    probe = DumbProbe([_state(False, None, "Sesja jest, ale nie odczytano uchwytu konta.")])
    _wire(monkeypatch, probe, capsule)

    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is False
    assert "Sesja jest" in answer["detail"]


async def test_a_failed_read_is_our_sentence_not_sign_in_again(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    probe = DumbProbe([_state(False, None, "czytnik kapsuły nieosiągalny (ConnectError)")])
    _wire(monkeypatch, probe, DumbCapsule())

    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is False
    assert "czytnik kapsuły nieosiągalny" in answer["detail"]
    assert "zaloguj" not in answer["detail"].lower()


async def test_the_right_account_is_recorded_ready_assigned_and_attached(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    capsule = DumbCapsule()
    probe = DumbProbe([_state(True, HANDLE)])
    _wire(monkeypatch, probe, capsule)

    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is True and answer["konto"] == HANDLE, answer
    assert answer["przypisano"] is True
    assert capsule.closed == 1 and capsule.revoked == 1, "ekran zamknięty, strumień odwołany"
    assert probe.reads == 1, "jeden odczyt, po restarcie przeglądarki na tym samym profilu"
    async with pool.acquire() as conn:
        account = await conn.fetchrow(
            "SELECT id, state FROM social_accounts WHERE organization_id = $1 "
            "AND platform = 'X' AND external_account_id = $2",
            tenant["organization"],
            HANDLE,
        )
        assigned = await conn.fetchval(
            "SELECT count(*) FROM project_account_assignments WHERE project_id = $1 "
            "AND social_account_id = $2 AND unassigned_at IS NULL",
            project["project"],
            account["id"],
        )
        person = await conn.fetchval(
            "SELECT person_profile_id FROM person_profile_accounts WHERE social_account_id = $1 "
            "AND detached_at IS NULL",
            account["id"],
        )
    assert account["state"] == "READY"
    assert assigned == 1
    assert person == project["person"], "konto X pod tą samą osobą, co reszta kont projektu"

    # A second confirmation changes nothing and assigns nothing twice.
    again = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()
    assert again["polaczone"] is True and again["przypisano"] is False


@pytest.mark.parametrize(
    ("own_capsule", "subject"), [(True, "capsule:x-01"), (False, "capsule:capsule-01")]
)
async def test_the_confirmation_names_the_capsule_that_holds_the_x_account(
    pool, tenant, logged_in_client, monkeypatch, own_capsule, subject
) -> None:
    """Measured 27.09: the X confirmation was audited under the Reddit capsule's profile while
    the account lived in `capsule-x` (profile `x-01`)."""
    project = await _project(pool, tenant)
    _wire(monkeypatch, DumbProbe([_state(True, HANDLE)]), DumbCapsule())
    if own_capsule:
        monkeypatch.setenv("WENEXT_X_BROWSER_READER_URL", "http://capsule-x:8765")
    else:
        monkeypatch.delenv("WENEXT_X_BROWSER_READER_URL", raising=False)

    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is True, answer
    async with pool.acquire() as conn:
        subjects = await conn.fetch(
            "SELECT subject_ref FROM audit_events WHERE organization_id = $1 "
            "AND kind = 'CAPSULE_SESSION_CONFIRMED' AND payload->>'platform' = 'X'",
            tenant["organization"],
        )
    assert [row["subject_ref"] for row in subjects] == [subject]


async def test_somebody_elses_account_is_refused_when_the_project_has_its_own(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    capsule = DumbCapsule()
    _wire(monkeypatch, DumbProbe([_state(True, HANDLE)]), capsule)
    first = await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    assert first.json()["polaczone"] is True

    capsule = DumbCapsule()
    _wire(monkeypatch, DumbProbe([_state(True, "SomeoneElse")]), capsule)
    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is False
    assert "@SomeoneElse" in answer["detail"] and f"@{HANDLE}" in answer["detail"]
    assert "Połącz X" in answer["detail"], "okno jest zamknięte, więc zdanie mówi, jak je otworzyć"


async def test_a_login_not_seen_after_the_restart_creates_no_account(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    project = await _project(pool, tenant)
    probe = DumbProbe([_state(False, None, "ekran logowania", login_wall=True)])
    _wire(monkeypatch, probe, DumbCapsule())

    answer = (
        await logged_in_client.post("/api/x/session/confirm", json=_body(tenant, project))
    ).json()

    assert answer["polaczone"] is False
    async with pool.acquire() as conn:
        created = await conn.fetchval(
            "SELECT count(*) FROM social_accounts WHERE organization_id = $1 AND platform = 'X'",
            tenant["organization"],
        )
    assert created == 0, "konto nie powstaje z logowania, którego nie widać po restarcie"


async def test_a_project_of_another_organization_is_not_found(
    pool, tenant, logged_in_client, monkeypatch
) -> None:
    capsule = DumbCapsule()
    _wire(monkeypatch, DumbProbe([_state(True, HANDLE)]), capsule)
    response = await logged_in_client.post(
        "/api/x/session/confirm",
        json={"workspace_id": str(tenant["workspace"]), "project_id": str(uuid4())},
    )
    assert response.status_code == 404
