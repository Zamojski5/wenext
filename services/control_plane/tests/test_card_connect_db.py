"""Connecting an account from the card, against PostgreSQL (v10): the rows, the audit, the footer.

Nothing here reaches a real capsule, gateway or platform: they are the fakes of
`test_card_connect_unit.py`. What the database adds, and what is held here:

  * the account the click is about is read from the project's own assignments, never from the card; an
    account of another project, a paused one and a platform without a window are refused;
  * the ticket is filed under the organization's single operator and the open leaves an audit row with the
    plugin as the actor and only the closed keys; no operator, or two, is a refusal and not a guess;
  * "done" records the account as connected through the ONE write path (`note_capsule_login`: PROVISIONED,
    VERIFYING, READY, with its audit row), is harmless when repeated, and writes NOTHING for a session that
    belongs to another account;
  * `room.show` carries the footer's dots, read from the browser first and the row second, and a hosted
    session carries neither the dots nor the address.
"""

from __future__ import annotations

import json
import uuid

import pytest
from control_plane.tools import ToolRegistry, issue
from control_plane.tools import card_connect as cc
from control_plane.tools.card_connect import META_LOGIN_URL
from control_plane.tools.card_send import META_TOKEN
from tests.test_card_connect_unit import (
    ORIGIN_X,
    SECRET_TICKET,
    _FakeCapsule,
    _FakeGateway,
    _FakeProbe,
    _FakeSource,
    _RedditState,
    _World,
    _XState,
)
from tests.test_opportunities_db import World, _account, _world

pytestmark = pytest.mark.db


@pytest.fixture
def browser(monkeypatch) -> _World:
    """The agent's browser, its window and the probes, faked; the database is real."""
    w = _World()
    for key, value in {
        "WENEXT_BROWSER_READER_URL": "http://capsule:8765",
        "WENEXT_BROWSER_READER_TOKEN": "reader-token",
        "WENEXT_GATEWAY_URL": "http://gateway:8760",
        "WENEXT_GATEWAY_ISSUER_TOKEN": "issuer-token",
        "WENEXT_STREAM_PUBLIC_ORIGIN": "http://127.0.0.1:8760",
        "WENEXT_X_BROWSER_READER_URL": "http://capsule-x:8765",
        "WENEXT_X_GATEWAY_URL": "http://gateway-x:8760",
        "WENEXT_X_STREAM_PUBLIC_ORIGIN": ORIGIN_X,
        "WENEXT_DASHBOARD_URL": "http://127.0.0.1:3000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cc, "CapsuleClient", lambda url, token: _FakeCapsule(w, url))
    monkeypatch.setattr(cc, "GatewayClient", lambda url, issuer_token: _FakeGateway(w, url))
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: _FakeSource(w)
    )
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.probe_from_env", lambda env=None: _FakeProbe(w)
    )
    return w


async def _x_world(pool, tenant, browser: _World, *, state: str = "READY") -> World:
    world = await _world(pool, tenant, platform="X")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = $2 WHERE id = $1", world.account_id, state)
    browser.x_state = _XState(True, world.login)
    return world


async def _connect(world: World, platform: str = "X") -> tuple[dict, dict]:
    """The card's one press: ask, then act with what it was given."""
    asked = await world.registry.call("room.connect_token", {"platforma": platform})
    token = (asked.get("_mcp_meta") or {}).get(META_TOKEN)
    assert asked["stan"] == "WYKONANE" and token, asked
    acted = await world.registry.call("room.connect_account", {"platforma": platform, "token": token})
    return asked, acted


async def _audit(pool, kind: str, tenant) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT kind, actor_ref, subject_ref, payload FROM audit_events "
            "WHERE kind = $1 AND organization_id = $2 ORDER BY id",
            kind,
            tenant["organization"],
        )


async def _state(pool, account_id) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT state FROM social_accounts WHERE id = $1", account_id)


# ---------------------------------------------------------------------------
# The open
# ---------------------------------------------------------------------------


async def test_the_whole_press_files_the_ticket_under_the_sole_operator_and_audits_it(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser)
    asked, acted = await _connect(world)
    assert asked["konto"] == world.login and acted["stan"] == "WYKONANE"
    href = acted["_mcp_meta"][META_LOGIN_URL]
    assert href.startswith(f"{ORIGIN_X}/login-capsule#ticket={SECRET_TICKET}")
    ticket_call = next(call for call in browser.calls if call[0] == "ticket")
    assert ticket_call[2] == tenant["operator"], "bilet na jedynego operatora organizacji"
    rows = await _audit(pool, "CAPSULE_SESSION_OPENED", tenant)
    assert len(rows) == 1 and rows[0]["actor_ref"] == "plugin:codex"
    assert rows[0]["subject_ref"] == "capsule:x-01"
    assert SECRET_TICKET not in str(dict(rows[0]))


async def test_a_second_press_with_the_same_confirmation_opens_nothing(pool, tenant, browser) -> None:
    world = await _x_world(pool, tenant, browser)
    asked = await world.registry.call("room.connect_token", {"platforma": "X"})
    token = asked["_mcp_meta"][META_TOKEN]
    first = await world.registry.call("room.connect_account", {"platforma": "X", "token": token})
    second = await world.registry.call("room.connect_account", {"platforma": "X", "token": token})
    assert first["stan"] == "WYKONANE" and second["stan"] == "NIEDOSTEPNE"
    assert second["kod"] == "POTWIERDZENIE"
    assert len(await _audit(pool, "CAPSULE_SESSION_OPENED", tenant)) == 1


@pytest.mark.parametrize("state", ["PAUSED", "QUARANTINED"])
async def test_a_paused_account_cannot_be_connected_and_nothing_is_minted(
    pool, tenant, browser, state
) -> None:
    world = await _x_world(pool, tenant, browser, state=state)
    asked = await world.registry.call("room.connect_token", {"platforma": "X"})
    assert asked["stan"] == "NIEDOSTEPNE" and asked["kod"] == "BRAK_KONTA"
    assert "_mcp_meta" not in asked


async def test_a_platform_the_project_has_no_account_on_is_refused(pool, tenant, browser) -> None:
    world = await _x_world(pool, tenant, browser)
    asked = await world.registry.call("room.connect_token", {"platforma": "REDDIT"})
    assert asked["kod"] == "BRAK_KONTA" and "REDDIT" in asked["powod"]
    # An account of ANOTHER project is not this project's, whatever the card asks.
    await _world(pool, tenant, platform="REDDIT")
    again = await world.registry.call("room.connect_token", {"platforma": "REDDIT"})
    assert again["kod"] == "BRAK_KONTA"


async def test_no_operator_is_a_refusal_not_a_guess(pool, tenant, browser) -> None:
    world = await _x_world(pool, tenant, browser)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE operators SET disabled_at = now() WHERE organization_id = $1", tenant["organization"])
    asked = await world.registry.call("room.connect_token", {"platforma": "X"})
    assert asked["stan"] == "NIEDOSTEPNE" and asked["kod"] == "OPERATOR"
    assert "dashboardzie" in asked["powod"]
    assert not any(call[0] == "ticket" for call in browser.calls)


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------


async def test_done_records_the_account_through_the_one_write_path_and_is_harmless_twice(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser, state="PROVISIONED")
    browser.steer_open = True
    first = await world.registry.call("room.connect_done", {"platforma": "X"})
    assert first["stan"] == "WYKONANE" and first["polaczone"] is True and first["konto"] == world.login
    assert first["okno_zamkniete"] is True and first["konto_gotowe"] is True
    assert await _state(pool, world.account_id) == "READY", "PROVISIONED, VERIFYING, READY, w jednej transakcji"
    verified = await _audit(pool, "SOCIAL_ACCOUNT_VERIFIED", tenant)
    assert len(verified) == 1 and verified[0]["actor_ref"] == "plugin:codex"
    confirmed = await _audit(pool, "CAPSULE_SESSION_CONFIRMED", tenant)
    payload = confirmed[0]["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert len(confirmed) == 1 and payload == {"platform": "X", "verdict": "SIGNED_IN"}
    # Repeated: still connected, no second verification, no window to close.
    again = await world.registry.call("room.connect_done", {"platforma": "X"})
    assert again["polaczone"] is True and again["okno_zamkniete"] is False
    assert len(await _audit(pool, "SOCIAL_ACCOUNT_VERIFIED", tenant)) == 1
    names = browser.names()
    assert names.count("close") == 1 and names.count("revoke") == 1


async def test_done_writes_nothing_for_a_session_that_belongs_to_another_account(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser, state="PROVISIONED")
    browser.x_state = _XState(True, "SomebodyElse")
    answer = await world.registry.call("room.connect_done", {"platforma": "X"})
    assert answer["polaczone"] is False and answer["kod_odczytu"] == "INNE_KONTO"
    assert await _state(pool, world.account_id) == "PROVISIONED"
    assert await _audit(pool, "SOCIAL_ACCOUNT_VERIFIED", tenant) == []


async def test_done_for_reddit_reads_through_the_reddit_probe(pool, tenant, browser) -> None:
    world = await _world(pool, tenant, platform="REDDIT")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = 'PROVISIONED' WHERE id = $1", world.account_id)
    browser.reddit_state = _RedditState(True, world.login)
    answer = await world.registry.call("room.connect_done", {"platforma": "REDDIT"})
    assert answer["polaczone"] is True and browser.reddit_reads == 1 and browser.x_reads == 0
    assert await _state(pool, world.account_id) == "READY"


# ---------------------------------------------------------------------------
# The status
# ---------------------------------------------------------------------------


async def test_the_status_through_the_registry_asks_no_platform_while_the_window_is_open(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser)
    browser.steer_open = True
    for _ in range(5):
        answer = await world.registry.call("room.account_status", {"platforma": "X"})
    assert answer["stan"] == "WYKONANE" and answer["konta"][0]["stan"] == cc.HEALTH_AWAITING
    assert browser.x_reads == 0
    browser.steer_open = False
    closed = await world.registry.call("room.account_status", {"platforma": "X"})
    assert closed["konta"][0]["stan"] == cc.HEALTH_CONNECTED and browser.x_reads == 1


# ---------------------------------------------------------------------------
# room.show carries the footer
# ---------------------------------------------------------------------------


async def test_room_show_carries_a_dot_for_each_account_read_from_the_browser(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser)
    _, reddit_login = await _account(pool, tenant, world.project_id, "REDDIT")
    stopped_id, stopped_login = await _account(pool, tenant, world.project_id, "REDDIT")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE social_accounts SET state = 'PAUSED' WHERE id = $1", stopped_id)
    browser.x_state = _XState(False, None, "ściana logowania", login_wall=True)  # lost overnight
    browser.reddit_state = _RedditState(True, reddit_login)
    shown = await world.registry.call("room.show", {})
    by_login = {item["konto"]: item for item in shown["konta_stan"]}
    assert by_login[world.login]["stan"] == cc.HEALTH_LOST, "wiersz mówi READY, przeglądarka nie ma sesji"
    assert by_login[world.login]["mozna_polaczyc"] is True
    assert by_login[reddit_login]["stan"] == cc.HEALTH_CONNECTED
    assert by_login[reddit_login]["mozna_polaczyc"] is False
    assert by_login[stopped_login]["stan"] == cc.HEALTH_HELD and by_login[stopped_login]["mozna_polaczyc"] is False
    assert shown["konta_adres"] == "http://127.0.0.1:3000/community"
    assert {item["platforma"] for item in shown["konta_stan"]} == {"X", "REDDIT"}


async def test_room_show_never_calls_an_account_that_was_never_connected_lost(
    pool, tenant, browser
) -> None:
    world = await _x_world(pool, tenant, browser, state="PROVISIONED")
    browser.x_state = _XState(False, None, "ściana logowania", login_wall=True)
    item = (await world.registry.call("room.show", {}))["konta_stan"][0]
    assert item["stan"] == cc.HEALTH_NEVER and item["mozna_polaczyc"] is True


async def test_room_show_says_it_cannot_check_when_the_browser_is_not_here(
    pool, tenant, monkeypatch
) -> None:
    for key in ("WENEXT_BROWSER_READER_URL", "WENEXT_X_BROWSER_READER_URL"):
        monkeypatch.delenv(key, raising=False)
    world = await _world(pool, tenant, platform="X")
    item = (await world.registry.call("room.show", {}))["konta_stan"][0]
    assert item["stan"] == cc.HEALTH_UNKNOWN and item["mozna_polaczyc"] is False
    assert item["ekran"] == cc.SCREEN_NONE


async def test_a_hosted_session_has_no_accounts_and_no_footer(pool, tenant, browser) -> None:
    world = await _x_world(pool, tenant, browser)
    capability = world.registry.capability
    hosted = ToolRegistry(
        pool=pool,
        capability=issue(
            session_id=capability.session_id,
            organization_id=capability.organization_id,
            workspace_id=capability.workspace_id,
            project_id=capability.project_id,
            dsn="postgresql://atrapa/nieuzywane",
            host="claude-code",
            hosted=True,
        ),
    )
    shown = await hosted.call("room.show", {})
    assert shown["stan"] == "WYKONANE" and shown["wysylka"] == "reczna"
    assert "konta_stan" not in shown and "konta_adres" not in shown
    assert not any(call[0] in ("state", "read-x", "read-reddit") for call in browser.calls), (
        "hosted nie pyta przeglądarki, której nie ma"
    )
    assert uuid.UUID(str(capability.project_id))


async def test_room_show_says_demo_only_for_the_recording_sandbox(pool, tenant, browser, monkeypatch) -> None:
    """The Claude Code mod may rehearse a send on the sandbox (nothing can act for it) and on nothing else."""
    from control_plane.demo import DEMO_ORGANIZATION_ID

    world = await _x_world(pool, tenant, browser)
    assert "demo" not in await world.registry.call("room.show", {})
    monkeypatch.setattr(
        "control_plane.tools.opportunity_tools.is_demo_organization",
        lambda organization_id: organization_id == world.registry.capability.organization_id,
    )
    assert (await world.registry.call("room.show", {}))["demo"] is True
    # The real predicate: only the sandbox's own id.
    from control_plane.demo import is_demo_organization

    assert is_demo_organization(DEMO_ORGANIZATION_ID) and not is_demo_organization(uuid.uuid4())
