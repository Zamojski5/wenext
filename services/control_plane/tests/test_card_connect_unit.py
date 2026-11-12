"""Connecting an account from the card, and the health in its footer: ONLY A CLICK OPENS THE WINDOW (v10).

The founder (12.10): "I hate the dashboard. I don't know what to click ... you click, it connects nicely,
done." His X session in the agent's browser was lost overnight. `tools/card_connect.py` holds the threat model
(11 to 17, after the ten of `card_send` and `card_actions`); this file holds each layer, from the outside in,
without a database (the half that needs one is `test_card_connect_db.py`):

  * the four tools are the card's: not in the catalog the model reads, listed (app-only) only for a Codex host,
    not even named for Claude Code, a Wenext-run session or a hosted one;
  * the server refuses an open that did not come from a click: no token, a wrong one, a spent one, an expired
    one, one minted for the other platform; and the address of the window travels in `_meta` and nowhere else
    (not in the text a model reads, not in the structured result, not in the trace);
  * the order that matters is kept: the window first, the ticket second; and on "done" the window is CLOSED and
    the streams revoked BEFORE the session is read (the person's browser has no DevTools endpoint while they
    are at it), and a session of ANOTHER account is never a connection;
  * the status makes no platform request while the window is open, and at most one in `MIN_READ_GAP` after;
  * a model-visible tool that fails with a lost session carries the hint that names Reconnect, not Limits.

NOT HELD HERE: that a live gateway issues the ticket and a live capsule opens the page (this repository's
capsule suites hold their halves), that a Codex host opens the link, and how the footer looks.
"""

from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from control_plane.observe.capsule_client import CapsuleState, CapsuleUnavailable, StreamTicket
from control_plane.sessions.trace_payloads import TOOL_VERBS
from control_plane.tools import (
    APP_ONLY_TOOLS,
    BY_NAME,
    PLUGIN_EXCLUDED,
    PLUGIN_TOOLS,
    NoSuchTool,
    ToolRegistry,
    issue,
    listing,
    manifest,
)
from control_plane.tools import card_connect as cc
from control_plane.tools.card_actions import (
    SCOPE_HINT,
    SESSION_HINT,
    SESSION_LOST_CODE,
    with_scope_hint,
)
from control_plane.tools.card_connect import (
    CARD_CONNECT_TOOL_SPECS,
    HEALTH_AWAITING,
    HEALTH_CONNECTED,
    HEALTH_HELD,
    HEALTH_LOST,
    HEALTH_NEVER,
    HEALTH_OTHER,
    HEALTH_UNKNOWN,
    MAX_OUTSTANDING,
    META_LOGIN_URL,
    MIN_READ_GAP,
    ConnectTokens,
    Reading,
    can_connect,
    health_of,
    reading_of_reddit,
    reading_of_x,
)
from control_plane.tools.card_send import META_TOKEN, SECRET_ARGS, TOKEN_TTL, TokenRefused
from control_plane.tools.catalog import allowed_for_host, app_manifest
from control_plane.tools.mcp_server import HOST_INSTRUCTIONS, MCPToolServer, _Loop
from control_plane.tools.registry import render_content

CONTROL_PLANE = Path(__file__).resolve().parents[1]
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
CONNECT_TOOLS = {spec["name"] for spec in CARD_CONNECT_TOOL_SPECS}
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
HANDLE = "demo_founder"
SECRET_TICKET = "TICKET-" + "s" * 30
ORIGIN_X = "http://127.0.0.1:8761"
ORIGIN_SHARED = "http://127.0.0.1:8760"


# ---------------------------------------------------------------------------
# The tools are the card's: who sees them, who can call them
# ---------------------------------------------------------------------------


def test_the_four_connect_tools_are_app_only_and_nothing_else() -> None:
    assert {
        "room.connect_token",
        "room.connect_account",
        "room.connect_done",
        "room.account_status",
    } == CONNECT_TOOLS
    assert CONNECT_TOOLS <= APP_ONLY_TOOLS
    assert not CONNECT_TOOLS & set(BY_NAME), "narzędzie karty stoi w katalogu modelu"
    assert not CONNECT_TOOLS & PLUGIN_TOOLS and not CONNECT_TOOLS & set(PLUGIN_EXCLUDED)
    for name in CONNECT_TOOLS:
        assert allowed_for_host(name, "codex") is True
        assert allowed_for_host(name, "codex", hosted=True) is False, "hosted nie ma kont człowieka"
        assert allowed_for_host(name, "claude-code") is False
        assert allowed_for_host(name, None) is False
        assert name in TOOL_VERBS, "zapis w śladzie ma zdanie, nie nazwę techniczną"
    assert {e["name"] for e in app_manifest("codex")} >= CONNECT_TOOLS
    assert app_manifest("claude-code") == [] and app_manifest(None) == []
    assert app_manifest("codex", hosted=True) == []
    for host in (None, "codex", "claude-code"):
        assert not {e["name"] for e in manifest(host=host)} & CONNECT_TOOLS, host
        assert not {e["name"] for e in listing(host=host)} & CONNECT_TOOLS, host


def test_the_specs_are_honest_strict_and_dash_free() -> None:
    by_name = {spec["name"]: spec for spec in CARD_CONNECT_TOOL_SPECS}
    # Only the act is a write. Asking must not be replayed from the trace (an old, dead token),
    # and "done" is repeatable: a repeated identical call must run again, not return the first.
    assert by_name["room.connect_account"]["writes"] is True
    for name in ("room.connect_token", "room.connect_done", "room.account_status"):
        assert "writes" not in by_name[name], name
    for spec in CARD_CONNECT_TOOL_SPECS:
        assert spec["state"] == "DOSTEPNE"
        assert spec["input_schema"]["additionalProperties"] is False
        assert "TYLKO DLA KARTY" in spec["summary"]
        assert not LONG_DASHES.search(spec["summary"] + spec["detail"])
    # The act takes the platform and the token and NOTHING else: no address, no account, no user.
    act = by_name["room.connect_account"]["input_schema"]
    assert set(act["properties"]) == {"platforma", "token"}
    assert set(act["required"]) == {"platforma", "token"}
    assert act["properties"]["platforma"]["enum"] == ["X", "REDDIT"]
    assert set(by_name["room.connect_token"]["input_schema"]["properties"]) == {"platforma"}
    assert set(by_name["room.connect_done"]["input_schema"]["properties"]) == {"platforma"}


def test_the_files_have_no_long_dash() -> None:
    for name in (
        "control_plane/tools/card_connect.py",
        "tests/test_card_connect_unit.py",
        "tests/test_card_connect_db.py",
    ):
        path = CONTROL_PLANE / name
        if path.exists():
            assert not LONG_DASHES.search(path.read_text(encoding="utf-8")), name


def _capability(**changes):
    base = {
        "session_id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "dsn": "postgresql://nieuzywane-w-tescie",
    }
    return issue(**{**base, **changes})


class _StubRegistry:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result if result is not None else {"stan": "WYKONANE"}

    async def platform_capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, args: dict) -> dict:
        return self._result


class _Sessions:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def append_event(self, *, organization_id, session_id, kind, payload) -> None:
        self.events.append((kind, payload))


def _serve(host, frames, registry=None, sessions=None) -> list[dict]:
    runner = _Loop()
    runner.start()
    out = io.StringIO()
    try:
        server = MCPToolServer(
            capability=_capability(host=host),
            registry=registry or _StubRegistry(),  # type: ignore[arg-type]
            runner=runner,
            sessions=(sessions or _Sessions()) if host else None,  # type: ignore[arg-type]
            stream_in=io.StringIO(),
            stream_out=out,
        )
        for frame in frames:
            server.handle(frame)
    finally:
        runner.stop()
    return [json.loads(line) for line in out.getvalue().splitlines()]


_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.mark.parametrize("host", [None, "codex", "claude-code"])
def test_the_connect_tools_are_never_model_visible_and_only_a_codex_host_lists_them(host) -> None:
    listed = _serve(host, [_INIT, _LIST])[1]["result"]["tools"]
    names = {tool["name"] for tool in listed}
    visible = {
        tool["name"]
        for tool in listed
        if "model" in tool.get("_meta", {}).get("ui", {}).get("visibility", ["model", "app"])
    }
    assert not visible & CONNECT_TOOLS, host
    if host == "codex":
        assert names >= CONNECT_TOOLS
        for tool in listed:
            if tool["name"] in CONNECT_TOOLS:
                assert tool["_meta"] == {"ui": {"visibility": ["app"]}}
                assert "resourceUri" not in tool["_meta"]["ui"]
                assert tool["annotations"]["readOnlyHint"] is (
                    tool["name"] not in {"room.connect_account"}
                )
    else:
        assert not names & CONNECT_TOOLS, host


@pytest.mark.parametrize("host", [None, "claude-code"])
@pytest.mark.parametrize("name", sorted(CONNECT_TOOLS))
async def test_a_connect_tool_is_no_such_tool_for_a_session_without_a_card(host, name) -> None:
    registry = ToolRegistry(capability=_capability(host=host), pool=None)  # type: ignore[arg-type]
    with pytest.raises(NoSuchTool):
        await registry.call(name, {"platforma": "X", "token": "x"})


async def test_catalog_describe_does_not_tell_the_model_how_a_connect_works() -> None:
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    hidden = await registry.call("catalog.describe", {"tool": "room.connect_account"})
    missing = await registry.call("catalog.describe", {"tool": "no.such.tool"})
    assert hidden["stan"] == "BLAD"
    assert hidden["powod"].replace("room.connect_account", "X") == missing["powod"].replace(
        "no.such.tool", "X"
    )


def test_the_token_is_redacted_from_the_trace() -> None:
    from control_plane.tools.card_send import redacted_args

    assert SECRET_ARGS["room.connect_account"] == ("token",)
    shown = redacted_args("room.connect_account", {"platforma": "X", "token": "sekret-sekret"})
    assert "sekret" not in json.dumps(shown) and shown["platforma"] == "X"
    # The question mints it and carries none; the other two take no secret at all.
    for name in ("room.connect_token", "room.connect_done", "room.account_status"):
        assert name not in SECRET_ARGS


def test_the_address_and_the_confirmation_travel_in_meta_only_and_the_trace_carries_neither() -> None:
    """The whole server loop, with the shape the real tools answer in: the card gets the address in
    `_meta`; the text a model reads, the structured result and the two trace rows have none of it."""
    href = f"{ORIGIN_X}/login-capsule#ticket={SECRET_TICKET}"
    token = "tajne-potwierdzenie-otwarcia"
    registry = _StubRegistry(
        {
            "stan": "WYKONANE",
            "platforma": "X",
            "konto": HANDLE,
            "ekran": "otwarty",
            "_mcp_meta": {META_LOGIN_URL: href},
        }
    )
    sessions = _Sessions()
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "room.connect_account",
            "arguments": {"platforma": "X", "token": token},
        },
    }
    answer = _serve("codex", [_INIT, call], registry, sessions)[1]["result"]
    assert answer["_meta"] == {META_LOGIN_URL: href}
    for part in ("content", "structuredContent"):
        shown = json.dumps(answer[part], ensure_ascii=False)
        assert SECRET_TICKET not in shown and "login-capsule" not in shown, part
    assert answer["structuredContent"]["stan"] == "WYKONANE"
    traced = json.dumps(sessions.events, ensure_ascii=False)
    assert SECRET_TICKET not in traced and "login-capsule" not in traced
    assert token not in traced, "potwierdzenie nie trafia do śladu"
    (_, call_row), (_, result_row) = sessions.events
    assert call_row["arguments"]["token"] == "[pominięte w śladzie]"
    assert call_row["arguments"]["platforma"] == "X"
    assert "_meta" not in result_row["result"]


# ---------------------------------------------------------------------------
# The one-time confirmation
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def test_a_confirmation_works_once_for_its_platform_and_is_stored_hashed() -> None:
    clock = _Clock()
    tokens = ConnectTokens(clock=clock)
    account = uuid4()
    token, expires = tokens.mint(platform="X", account_id=account, handle=HANDLE)
    assert len(token) >= 40 and expires == NOW + TOKEN_TTL
    assert token not in tokens._grants, "w pamięci leży odcisk, nie działający token"
    grant = tokens.redeem(token, platform="X")
    assert grant.account_id == account and grant.handle == HANDLE
    with pytest.raises(TokenRefused) as spent:
        tokens.redeem(token, platform="X")
    assert spent.value.code == "POTWIERDZENIE"


def test_a_wrong_an_expired_a_replaced_and_a_cross_platform_token_are_refused() -> None:
    clock = _Clock()
    tokens = ConnectTokens(clock=clock)
    with pytest.raises(TokenRefused):
        tokens.redeem("not-a-token", platform="X")
    old, _ = tokens.mint(platform="X", account_id=uuid4(), handle=HANDLE)
    new, _ = tokens.mint(platform="X", account_id=uuid4(), handle=HANDLE)
    with pytest.raises(TokenRefused):
        tokens.redeem(old, platform="X")  # a new confirmation kills the old one of the platform
    reddit, _ = tokens.mint(platform="REDDIT", account_id=uuid4(), handle="u")
    assert tokens.redeem(reddit, platform="REDDIT").platform == "REDDIT"  # another platform's lives
    with pytest.raises(TokenRefused) as wrong:
        tokens.redeem(new, platform="REDDIT")  # shown to the wrong platform: refused and burned
    assert wrong.value.code == "POTWIERDZENIE"
    with pytest.raises(TokenRefused):
        tokens.redeem(new, platform="X")
    late, _ = tokens.mint(platform="X", account_id=uuid4(), handle=HANDLE)
    clock.now = NOW + TOKEN_TTL + timedelta(seconds=1)
    with pytest.raises(TokenRefused) as expired:
        tokens.redeem(late, platform="X")
    assert "wygasło" in expired.value.detail


def test_the_store_of_confirmations_is_bounded() -> None:
    tokens = ConnectTokens(clock=_Clock())
    for index in range(MAX_OUTSTANDING * 3):
        tokens.mint(platform=f"P{index}", account_id=uuid4(), handle="h")
    assert len(tokens._grants) <= MAX_OUTSTANDING


# ---------------------------------------------------------------------------
# What the dot says: pure
# ---------------------------------------------------------------------------

ME = Reading(True, HANDLE, None, 1.0)
SOMEONE = Reading(True, "SomebodyElse", None, 1.0)
WALL = Reading(False, None, "ściana logowania", 1.0)
BLIND = Reading(None, None, "kapsuła milczy", 1.0)


@pytest.mark.parametrize(
    ("row", "reading", "screen", "expected"),
    [
        # What the browser says wins over what the row remembers.
        ("READY", ME, False, HEALTH_CONNECTED),
        ("PROVISIONED", ME, False, HEALTH_CONNECTED),
        ("READY", WALL, False, HEALTH_LOST),
        ("REAUTH_REQUIRED", WALL, False, HEALTH_LOST),
        ("PROVISIONED", WALL, False, HEALTH_NEVER),
        ("VERIFYING", WALL, False, HEALTH_NEVER),
        # Somebody else's session is never a connection: it is the one that looks like success.
        ("READY", SOMEONE, False, HEALTH_OTHER),
        # A read that failed is nobody's lost login: nothing is claimed, the row decides "never".
        ("READY", BLIND, False, HEALTH_UNKNOWN),
        ("READY", None, False, HEALTH_UNKNOWN),
        ("PROVISIONED", BLIND, False, HEALTH_NEVER),
        # A person's decision, and the window being open, come before any reading.
        ("PAUSED", ME, False, HEALTH_HELD),
        ("QUARANTINED", WALL, False, HEALTH_HELD),
        ("READY", ME, True, HEALTH_AWAITING),
        ("PAUSED", None, True, HEALTH_AWAITING),
    ],
)
def test_the_dot_follows_the_browser_then_the_row(row, reading, screen, expected) -> None:
    assert health_of(row, HANDLE, reading, screen_open=screen)[0] == expected


def test_handles_are_compared_without_case_and_without_at() -> None:
    assert health_of("READY", "@demo_founder", Reading(True, "DEMO_FOUNDER", None, 1.0))[0] == (
        HEALTH_CONNECTED
    )


def test_a_click_is_offered_only_where_it_helps() -> None:
    for state in (HEALTH_LOST, HEALTH_NEVER, HEALTH_OTHER):
        assert can_connect("X", state) and can_connect("REDDIT", state)
    for state in (HEALTH_CONNECTED, HEALTH_AWAITING, HEALTH_HELD, HEALTH_UNKNOWN):
        assert not can_connect("X", state)
    assert not can_connect("LINKEDIN", HEALTH_LOST), "tylko X i Reddit mają okno w kapsule"


@dataclass
class _XState:
    logged_in: bool
    handle: str | None
    error: str | None = None
    login_wall: bool = False


@dataclass
class _RedditState:
    logged_in: bool | None
    username: str | None
    error: str | None = None


def test_an_x_state_becomes_a_reading_and_an_unread_handle_is_not_a_lost_login() -> None:
    assert reading_of_x(_XState(True, "a"), 1.0) == Reading(True, "a", None, 1.0)
    wall = reading_of_x(_XState(False, None, "ściana", login_wall=True), 1.0)
    assert wall.logged_in is False and wall.error == "ściana"
    # "A session, handle not read" (api/x_platform.py): OUR defect, never "sign in again".
    blind = reading_of_x(_XState(False, None, "nie odczytano uchwytu", login_wall=False), 1.0)
    assert blind.logged_in is None


def test_a_reddit_reading_keeps_the_difference_between_lost_and_unknown() -> None:
    assert reading_of_reddit(_RedditState(True, "u"), 1.0).logged_in is True  # type: ignore[arg-type]
    assert reading_of_reddit(_RedditState(False, None, "x"), 1.0).logged_in is False  # type: ignore[arg-type]
    assert reading_of_reddit(_RedditState(None, None, "x"), 1.0).logged_in is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reading", "closed", "verdict", "says"),
    [
        (ME, True, cc.VERDICT_CONNECTED, HANDLE),
        (SOMEONE, True, cc.CODE_OTHER_ACCOUNT, "SomebodyElse"),
        (WALL, True, cc.CODE_SIGN_IN, "zamknięte"),
        (WALL, False, cc.CODE_SIGN_IN, "Otwórz okno"),
        (BLIND, True, cc.CODE_UNREADABLE, "kapsuła milczy"),
    ],
)
def test_done_says_one_of_four_things_and_none_passes_for_another(
    reading, closed, verdict, says
) -> None:
    got, handle, detail = cc._verdict_of(
        "X", reading, [{"konto": HANDLE, "platforma": "X"}], closed=closed
    )
    assert got == verdict and says in detail
    assert (handle == HANDLE) is (verdict == cc.VERDICT_CONNECTED)


def test_only_an_address_on_the_configured_screen_is_given_out() -> None:
    assert cc._same_origin(f"{ORIGIN_X}/login-capsule#ticket=t", ORIGIN_X)
    for bad in (
        "http://evil.example/login-capsule#ticket=t",
        "https://127.0.0.1:8761/login-capsule",
        "http://127.0.0.1:9999/login-capsule",
        "http://user:pw@127.0.0.1:8761/login-capsule",
        "javascript:alert(1)",
        "http://127.0.0.1:8761@evil.example/",
    ):
        assert not cc._same_origin(bad, ORIGIN_X), bad


# ---------------------------------------------------------------------------
# The tools, run against a fake capsule, gateway, probe and audit (no database)
# ---------------------------------------------------------------------------


@dataclass
class _World:
    """The agent's browser and its screen, as the card's tools meet them."""

    steer_open: bool = False
    calls: list[tuple] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)
    x_state: Any = None
    reddit_state: Any = None
    x_reads: int = 0
    #: The `retries` each live X read of the card asked for (see `_read_live`).
    x_retries: list[int] = field(default_factory=list)
    reddit_reads: int = 0
    unreachable: bool = False
    operators: int = 1
    accounts: list[dict[str, Any]] = field(default_factory=list)
    resumed: int = 0

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class _FakeCapsule:
    def __init__(self, world: _World, base_url: str) -> None:
        self.world = world
        self.base_url = base_url

    async def state(self) -> CapsuleState:
        self.world.calls.append(("state", self.base_url))
        if self.world.unreachable:
            raise CapsuleUnavailable("kapsuła nieosiągalna")
        return CapsuleState(
            mode="INTERACTIVE",
            reason=None,
            display_only=not self.world.steer_open,
            alive=True,
            viewer_ready=True,
            human_steer_open=self.world.steer_open,
            since=None,
        )

    async def open_human_session(self, reason: str = "FIRST_LOGIN", *, start_url=None):
        self.world.calls.append(("open", self.base_url, reason, start_url))
        self.world.steer_open = True
        return await self.state()

    async def close_human_session(self):
        self.world.calls.append(("close", self.base_url))
        self.world.steer_open = False

    async def aclose(self) -> None:
        return None


class _FakeGateway:
    def __init__(self, world: _World, base_url: str) -> None:
        self.world = world
        self.base_url = base_url

    async def issue_control_ticket(self, *, user_id: UUID) -> StreamTicket:
        self.world.calls.append(("ticket", self.base_url, user_id))
        return StreamTicket(
            ticket=SECRET_TICKET,
            stream_session_id="s",
            expires_at=(NOW + timedelta(minutes=5)).isoformat(),
            profile_lease_generation=1,
            interaction_lease_generation=1,
        )

    async def revoke_sessions(self) -> int:
        self.world.calls.append(("revoke", self.base_url))
        return 1

    async def aclose(self) -> None:
        return None


class _FakeSource:
    """`XBrowserSource` as `x_probe_from_env` returns it."""

    def __init__(self, world: _World) -> None:
        self.world = world

    async def verify_identity(self, expected: str, *, retries: int = 1):
        self.world.x_reads += 1
        self.world.x_retries.append(retries)
        self.world.calls.append(("read-x", self.world.steer_open))
        return self.world.x_state

    async def aclose(self) -> None:
        return None


class _FakeProbe:
    """`RedditBrowserSource` as `probe_from_env` returns it."""

    def __init__(self, world: _World) -> None:
        self.world = world

    async def session_state(self):
        self.world.reddit_reads += 1
        self.world.calls.append(("read-reddit", self.world.steer_open))
        return self.world.reddit_state

    async def aclose(self) -> None:
        return None


class _FakeAudit:
    world: _World

    def __init__(self, connection: Any) -> None:
        pass

    async def record(self, **fields: Any) -> None:
        type(self).world.audits.append(fields)


class _FakePool:
    """Enough of an asyncpg pool for the one place the unit tests reach it: the audit row."""

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.fixture
def world(monkeypatch) -> _World:
    w = _World()
    w.x_state = _XState(True, HANDLE)
    w.reddit_state = _RedditState(True, "demo_u")
    w.accounts = [
        {"id": uuid4(), "platforma": "X", "konto": HANDLE, "stan_konta": "READY"},
    ]
    for key, value in {
        "WENEXT_BROWSER_READER_URL": "http://capsule:8765",
        "WENEXT_BROWSER_READER_TOKEN": "reader-token",
        "WENEXT_GATEWAY_URL": "http://gateway:8760",
        "WENEXT_GATEWAY_ISSUER_TOKEN": "issuer-token",
        "WENEXT_STREAM_PUBLIC_ORIGIN": ORIGIN_SHARED,
        "WENEXT_X_BROWSER_READER_URL": "http://capsule-x:8765",
        "WENEXT_X_GATEWAY_URL": "http://gateway-x:8760",
        "WENEXT_X_STREAM_PUBLIC_ORIGIN": ORIGIN_X,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cc, "CapsuleClient", lambda url, token: _FakeCapsule(w, url))
    monkeypatch.setattr(
        cc, "GatewayClient", lambda url, issuer_token: _FakeGateway(w, url)
    )
    _FakeAudit.world = w
    monkeypatch.setattr(cc, "AuditWriter", _FakeAudit)
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: _FakeSource(w)
    )
    monkeypatch.setattr(
        "control_plane.observe.capsule_session.probe_from_env", lambda env=None: _FakeProbe(w)
    )

    async def noted(pool, **fields):
        w.calls.append(("note", fields["platform"], fields["username"], fields["actor_ref"]))
        from control_plane.workspace.account_state import AccountNoted

        return AccountNoted(uuid4(), fields["username"], True, None)

    monkeypatch.setattr(cc, "note_capsule_login", noted)
    return w


def _registry(world: _World, *, host: str = "codex") -> ToolRegistry:
    registry = ToolRegistry(capability=_capability(host=host), pool=_FakePool())  # type: ignore[arg-type]
    operator = uuid4()

    async def accounts() -> list[dict[str, Any]]:
        return list(world.accounts)

    async def sole() -> UUID:
        if world.operators != 1:
            raise cc.ConnectRefused(cc.CODE_OPERATOR, "organizacja ma 2 operatorów")
        return operator

    async def resumed() -> int:
        world.resumed += 1
        return 1

    registry._assigned_for_connect = accounts  # type: ignore[method-assign]
    registry._sole_operator = sole  # type: ignore[method-assign]
    registry._resume_parked_jobs = resumed  # type: ignore[method-assign]
    registry.__dict__["operator"] = operator
    return registry


async def _token(registry: ToolRegistry, platform: str = "X") -> tuple[dict, str | None]:
    answer = await registry._room_connect_token({"platforma": platform})
    return answer, (answer.get("_mcp_meta") or {}).get(META_TOKEN)


async def test_asking_mints_a_confirmation_in_meta_and_opens_nothing(world) -> None:
    registry = _registry(world)
    answer, token = await _token(registry)
    assert answer["stan"] == "WYKONANE" and answer["konto"] == HANDLE and token
    assert answer["platforma"] == "X" and answer["ekran"] == cc.SCREEN_CLOSED
    # The token is in `_meta` ONLY: not in what a model reads, not in the structured result.
    assert token not in json.dumps(render_content(answer))
    assert token not in json.dumps({k: v for k, v in answer.items() if not k.startswith("_mcp_")})
    assert "open" not in world.names() and "ticket" not in world.names(), "pytanie niczego nie otwiera"


@pytest.mark.parametrize("platform", ["", "LINKEDIN", "XX", "Reddit2"])
async def test_a_platform_outside_the_closed_set_is_refused(world, platform) -> None:
    from control_plane.tools.registry import ToolError

    registry = _registry(world)
    with pytest.raises(ToolError):
        await registry._room_connect_token({"platforma": platform})
    assert world.calls == []


async def test_the_open_needs_a_token_and_refuses_each_kind_of_bad_one(world) -> None:
    registry = _registry(world)
    for args in ({"platforma": "X"}, {"platforma": "X", "token": ""}):
        refused = await registry._room_connect_account(args)
        assert refused["stan"] == "NIEDOSTEPNE" and refused["kod"] == "POTWIERDZENIE", args
    garbage = await registry._room_connect_account({"platforma": "X", "token": "x" * 43})
    assert garbage["kod"] == "POTWIERDZENIE"
    _, token = await _token(registry, "X")
    wrong = await registry._room_connect_account({"platforma": "REDDIT", "token": token})
    assert wrong["kod"] == "POTWIERDZENIE", "potwierdzenie z X nie otwiera okna Reddita"
    spent = await registry._room_connect_account({"platforma": "X", "token": token})
    assert spent["kod"] == "POTWIERDZENIE", "pomyłka zużywa potwierdzenie"
    assert "open" not in world.names() and "ticket" not in world.names()
    assert world.audits == [], "odmowa niczego nie zapisuje w dzienniku jako otwarcia"


async def test_the_open_goes_screen_first_ticket_second_and_the_address_is_only_in_meta(
    world,
) -> None:
    registry = _registry(world)
    _, token = await _token(registry)
    world.calls.clear()
    answer = await registry._room_connect_account({"platforma": "X", "token": token})
    assert answer["stan"] == "WYKONANE" and answer["ekran"] == cc.SCREEN_OPEN
    # THE ORDER: a ticket for a screen that never came up is a dead link.
    assert world.names() == ["state", "open", "state", "ticket"]
    opened = next(call for call in world.calls if call[0] == "open")
    assert opened[1] == "http://capsule-x:8765", "X ma własną kapsułę"
    assert opened[2] == "FIRST_LOGIN" and opened[3] == "https://x.com/i/flow/login"
    ticket = next(call for call in world.calls if call[0] == "ticket")
    assert ticket[1] == "http://gateway-x:8760" and ticket[2] == registry.__dict__["operator"], (
        "bilet na tożsamość jedynego operatora, nie sesji agenta"
    )
    href = answer["_mcp_meta"][META_LOGIN_URL]
    assert href.startswith(f"{ORIGIN_X}/login-capsule#ticket={SECRET_TICKET}")
    # NOT in anything a model reads, nor in the structured result, nor in the trace.
    seen = json.dumps(render_content(answer)) + json.dumps(
        {k: v for k, v in answer.items() if not k.startswith("_mcp_")}
    )
    assert SECRET_TICKET not in seen and "login-capsule" not in seen
    assert META_LOGIN_URL not in seen
    # Audited as the dashboard audits it, with the plugin as the actor.
    assert [a["kind"] for a in world.audits] == ["CAPSULE_SESSION_OPENED"]
    assert world.audits[0]["actor_ref"] == "plugin:codex"
    assert world.audits[0]["payload"] == {"reason_code": "FIRST_LOGIN", "platform": "X"}
    assert world.audits[0]["subject_ref"] == "capsule:x-01"
    assert SECRET_TICKET not in json.dumps(world.audits, default=str)


async def test_a_token_opens_once(world) -> None:
    registry = _registry(world)
    _, token = await _token(registry)
    first = await registry._room_connect_account({"platforma": "X", "token": token})
    second = await registry._room_connect_account({"platforma": "X", "token": token})
    assert first["stan"] == "WYKONANE" and second["kod"] == "POTWIERDZENIE"
    assert world.names().count("ticket") == 1


async def test_an_open_window_is_not_opened_twice_only_a_new_ticket_is_issued(world) -> None:
    registry = _registry(world)
    world.steer_open = True
    _, token = await _token(registry)
    answer = await registry._room_connect_account({"platforma": "X", "token": token})
    assert answer["stan"] == "WYKONANE"
    assert "open" not in world.names(), "okno już jest: ponowne przełączenie zrestartowałoby przeglądarkę"
    assert world.names().count("ticket") == 1


async def test_reddit_uses_the_shared_capsule_and_its_own_login_page(world) -> None:
    world.accounts = [{"id": uuid4(), "platforma": "REDDIT", "konto": "demo_u", "stan_konta": "READY"}]
    registry = _registry(world)
    _, token = await _token(registry, "REDDIT")
    world.calls.clear()
    answer = await registry._room_connect_account({"platforma": "REDDIT", "token": token})
    opened = next(call for call in world.calls if call[0] == "open")
    assert opened[1] == "http://capsule:8765" and opened[3] == "https://www.reddit.com/login/"
    assert answer["_mcp_meta"][META_LOGIN_URL].startswith(ORIGIN_SHARED)


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("no_account", "BRAK_KONTA"),
        ("held", "BRAK_KONTA"),
        ("no_capsule", "KAPSULA"),
        ("no_issuer", "KAPSULA"),
        ("two_operators", "OPERATOR"),
    ],
)
async def test_the_question_refuses_what_cannot_work_and_mints_nothing(
    world, monkeypatch, setup, code
) -> None:
    registry = _registry(world)
    if setup == "no_account":
        world.accounts = []
    elif setup == "held":
        world.accounts[0]["stan_konta"] = "PAUSED"
    elif setup == "no_capsule":
        monkeypatch.delenv("WENEXT_BROWSER_READER_URL")
        monkeypatch.delenv("WENEXT_X_BROWSER_READER_URL")
    elif setup == "no_issuer":
        monkeypatch.delenv("WENEXT_GATEWAY_ISSUER_TOKEN")
    elif setup == "two_operators":
        world.operators = 2
    answer, token = await _token(registry)
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == code and token is None
    assert registry._connect_tokens._grants == {}


async def test_an_unreachable_capsule_is_a_refusal_with_a_code_and_no_audit_row(world) -> None:
    registry = _registry(world)
    _, token = await _token(registry)
    world.unreachable = True
    answer = await registry._room_connect_account({"platforma": "X", "token": token})
    assert answer["stan"] == "NIEDOSTEPNE" and answer["kod"] == "KAPSULA"
    assert world.audits == [] and "ticket" not in world.names()


async def test_a_ticket_address_off_the_configured_origin_is_not_given_out(
    world, monkeypatch
) -> None:
    registry = _registry(world)
    _, token = await _token(registry)
    monkeypatch.setattr(
        StreamTicket,
        "viewer_url",
        lambda self, origin: "http://evil.example/login-capsule#ticket=" + self.ticket,
    )
    answer = await registry._room_connect_account({"platforma": "X", "token": token})
    assert answer["kod"] == "KAPSULA" and "_mcp_meta" not in answer
    assert "evil.example" not in json.dumps(answer)
    assert world.audits == []


# ---- done: close first, read second ----------------------------------------------------------


async def test_done_closes_the_window_and_revokes_the_streams_BEFORE_it_reads(world) -> None:
    registry = _registry(world)
    world.steer_open = True
    answer = await registry._room_connect_done({"platforma": "X"})
    names = world.names()
    assert names[: names.index("read-x")] == ["state", "close", "revoke"], (
        "przeglądarka człowieka nie ma DevTools: najpierw zamykamy okno, dopiero potem czytamy"
    )
    assert world.calls[names.index("read-x")] == ("read-x", False), "odczyt przy zamkniętym oknie"
    assert answer["polaczone"] is True and answer["konto"] == HANDLE
    assert answer["okno_zamkniete"] is True and answer["kod_odczytu"] == "POLACZONE"
    # Recorded through the one write path, and audited with the plugin as the actor.
    assert ("note", "X", HANDLE, "plugin:codex") in world.calls
    assert [a["kind"] for a in world.audits] == ["CAPSULE_SESSION_CONFIRMED"]
    assert world.audits[0]["payload"] == {"platform": "X", "verdict": "SIGNED_IN"}
    assert world.resumed == 0, "wznawianie zadań to Reddit"


async def test_done_for_reddit_resumes_the_jobs_a_missing_login_parked(world) -> None:
    world.accounts = [{"id": uuid4(), "platforma": "REDDIT", "konto": "demo_u", "stan_konta": "READY"}]
    registry = _registry(world)
    world.steer_open = True
    answer = await registry._room_connect_done({"platforma": "REDDIT"})
    assert answer["polaczone"] is True and world.resumed == 1
    assert world.names().index("close") < world.names().index("read-reddit")


async def test_done_does_not_close_a_window_that_is_already_closed(world) -> None:
    registry = _registry(world)
    answer = await registry._room_connect_done({"platforma": "X"})
    assert "close" not in world.names() and "revoke" not in world.names()
    assert answer["okno_zamkniete"] is False and answer["polaczone"] is True


async def test_done_does_not_take_somebody_elses_session_for_the_account(world) -> None:
    registry = _registry(world)
    world.x_state = _XState(True, "SomebodyElse")
    answer = await registry._room_connect_done({"platforma": "X"})
    assert answer["polaczone"] is False and answer["kod_odczytu"] == "INNE_KONTO"
    assert "SomebodyElse" in answer["detail"]
    assert not any(call[0] == "note" for call in world.calls), "cudza sesja niczego nie zapisuje"
    assert world.audits[0]["payload"]["verdict"] == "SIGNED_OUT"


async def test_done_says_to_open_the_window_again_when_it_closed_without_a_sign_in(world) -> None:
    registry = _registry(world)
    world.steer_open = True
    world.x_state = _XState(False, None, "ściana logowania", login_wall=True)
    answer = await registry._room_connect_done({"platforma": "X"})
    assert answer["polaczone"] is False and answer["kod_odczytu"] == "ZALOGUJ"
    assert "ponownie" in answer["detail"]
    assert not any(call[0] == "note" for call in world.calls)


async def test_a_failed_read_after_the_window_is_ours_not_the_persons(world) -> None:
    registry = _registry(world)
    world.x_state = _XState(False, None, "strona się nie wczytała", login_wall=False)
    answer = await registry._room_connect_done({"platforma": "X"})
    assert answer["kod_odczytu"] == "NIECZYTELNE" and "nie wczytała" in answer["detail"]


async def test_done_refreshes_what_the_x_tools_believe(world) -> None:
    """A lost session stayed lost for the life of the tool server (v10 fixes it): after a sign-in the
    next `x.read` must see the session, not the reading from before."""
    registry = _registry(world)
    registry._x_identity_noted(_XState(False, None, "ściana", login_wall=True))
    assert registry._x_identity_cache.logged_in is False
    await registry._room_connect_done({"platforma": "X"})
    assert registry._x_identity_cache.logged_in is True
    assert registry._x_identity_usable() is True


# ---- the status: no platform request while the window is open ---------------------------------


async def test_the_status_makes_no_platform_request_while_the_window_is_open(world) -> None:
    registry = _registry(world)
    world.steer_open = True
    for _ in range(10):  # a card asking every 4 seconds
        answer = await registry._room_account_status({"platforma": "X"})
    assert world.x_reads == 0 and world.reddit_reads == 0
    item = answer["konta"][0]
    assert item["stan"] == HEALTH_AWAITING and item["ekran"] == cc.SCREEN_OPEN
    assert item["mozna_polaczyc"] is False


async def test_the_status_reads_at_most_once_in_the_gap_when_the_window_is_closed(world) -> None:
    registry = _registry(world)
    for _ in range(10):
        answer = await registry._room_account_status({"platforma": "X"})
    assert world.x_reads == 1, "dziesięć pytań w kilka milisekund to jeden odczyt platformy"
    assert answer["konta"][0]["stan"] == HEALTH_CONNECTED
    # After the gap it asks again, and sees what changed.
    registry._health_reads["X"] = Reading(True, HANDLE, None, time.monotonic() - MIN_READ_GAP - 1)
    world.x_state = _XState(False, None, "ściana", login_wall=True)
    registry._x_identity_cache = None
    again = await registry._room_account_status({"platforma": "X"})
    assert world.x_reads == 2 and again["konta"][0]["stan"] == HEALTH_LOST
    assert again["konta"][0]["mozna_polaczyc"] is True


async def test_a_polled_card_asks_for_no_retry_and_a_person_waiting_for_one(world) -> None:
    """The source retries a failed look once; a card with a few seconds in all would only have it
    cancelled mid-flight (the capsule would finish a read nobody waits for), so it asks for none,
    and the next poll is its second look. "I have signed in" has 90 s and is waited for."""
    registry = _registry(world)
    await registry._room_account_status({"platforma": "X"})
    assert world.x_retries == [0]
    world.steer_open = True
    await registry._room_connect_done({"platforma": "X"})
    assert world.x_retries == [0, 1]


async def test_the_status_of_a_platform_asks_only_that_platform(world) -> None:
    world.accounts.append(
        {"id": uuid4(), "platforma": "REDDIT", "konto": "demo_u", "stan_konta": "READY"}
    )
    registry = _registry(world)
    answer = await registry._room_account_status({"platforma": "REDDIT"})
    assert [item["platforma"] for item in answer["konta"]] == ["REDDIT"]
    assert world.x_reads == 0 and world.reddit_reads == 1


async def test_the_status_without_a_capsule_claims_nothing(world, monkeypatch) -> None:
    monkeypatch.delenv("WENEXT_BROWSER_READER_URL")
    monkeypatch.delenv("WENEXT_X_BROWSER_READER_URL")
    monkeypatch.setattr("control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: None)
    registry = _registry(world)
    item = (await registry._room_account_status({}))["konta"][0]
    assert item["stan"] == HEALTH_UNKNOWN and item["ekran"] == cc.SCREEN_NONE
    assert item["mozna_polaczyc"] is False, "bez kapsuły przycisk nic by nie zrobił"


# ---- room.show's footer ------------------------------------------------------------------------


async def test_the_room_reuses_the_reading_the_x_tools_made_and_asks_otherwise(world) -> None:
    registry = _registry(world)
    registry._x_identity_noted(_XState(False, None, "ściana", login_wall=True))
    first = await registry._room_health()
    assert world.x_reads == 0, "odczyt z tej tury wystarcza"
    assert first[0]["stan"] == HEALTH_LOST and first[0]["mozna_polaczyc"] is True
    fresh = _registry(world)
    second = await fresh._room_health()
    assert world.x_reads == 1 and second[0]["stan"] == HEALTH_CONNECTED


async def test_the_room_never_raises_for_the_footer(world, monkeypatch) -> None:
    registry = _registry(world)

    async def boom() -> list:
        raise RuntimeError("baza padła")

    registry._assigned_for_connect = boom  # type: ignore[method-assign]
    assert await registry._room_health() == []


# ---------------------------------------------------------------------------
# The agent side: a lost session points the model at the card
# ---------------------------------------------------------------------------


def test_a_lost_session_tells_the_model_to_say_it_and_call_room_show() -> None:
    result = {"stan": "NIEDOSTEPNE", "kod": SESSION_LOST_CODE, "powod": "x", "wskazowka": "Poproś."}
    hinted = with_scope_hint("x.read", result)
    assert hinted["wskazowka"].startswith("Poproś.") and SESSION_HINT in hinted["wskazowka"]
    assert "room.show" in SESSION_HINT and "Połącz ponownie" in SESSION_HINT
    assert "nie loguj się za człowieka" in SESSION_HINT
    assert SCOPE_HINT not in hinted["wskazowka"], "limity nie naprawią sesji"
    assert with_scope_hint("x.read", hinted) == hinted, "nie dopisuje dwa razy"
    assert not LONG_DASHES.search(SESSION_HINT)


def test_a_scope_refusal_still_says_limits_and_a_success_is_left_alone() -> None:
    scope = with_scope_hint("drafts.publish", {"stan": "NIEDOSTEPNE", "kod": "BUDZET", "powod": "x"})
    assert SCOPE_HINT in scope["wskazowka"] and SESSION_HINT not in scope["wskazowka"]
    assert with_scope_hint("x.read", {"stan": "WYKONANE", "kod": SESSION_LOST_CODE}) == {
        "stan": "WYKONANE",
        "kod": SESSION_LOST_CODE,
    }


def test_the_instructions_to_the_agent_name_the_reconnect_and_forbid_logging_in() -> None:
    assert "Reconnect" in HOST_INSTRUCTIONS and "room.show" in HOST_INSTRUCTIONS
    assert "never sign in" in HOST_INSTRUCTIONS
    assert not LONG_DASHES.search(HOST_INSTRUCTIONS)


async def test_the_x_tools_say_the_session_is_lost_with_the_code_the_hint_keys_on(monkeypatch) -> None:
    from community_observer.x_browser import XSessionState

    class Source:
        async def verify_identity(self, expected: str):
            return XSessionState(False, None, datetime.now(UTC), "ściana", login_wall=True)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "control_plane.observe.capsule_session.x_probe_from_env", lambda env=None: Source()
    )
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]

    async def account() -> dict:
        return {"id": uuid4(), "handle": HANDLE, "state": "READY"}

    registry._x_account = account  # type: ignore[method-assign]
    refusal = await registry._x_search({"query": "agent approvals"})
    assert refusal["stan"] == "NIEDOSTEPNE" and refusal["kod"] == SESSION_LOST_CODE
    assert SESSION_HINT in with_scope_hint("x.search", refusal)["wskazowka"]
    # And somebody else's session is the same kind of refusal: only a sign-in fixes it.
    other = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    other._x_account = account  # type: ignore[method-assign]
    other._x_identity_noted(XSessionState(True, "Somebody", datetime.now(UTC)))
    assert (await other._x_search({"query": "q"}))["kod"] == SESSION_LOST_CODE


def test_a_reddit_login_wall_carries_the_code_and_a_rate_limit_does_not() -> None:
    from control_plane.tools.opportunity_tools import _session_code

    class Wall(Exception):
        kind = "login_required"

    class Limited(Exception):
        kind = "rate_limited"

    assert _session_code(Wall()) == {"kod": SESSION_LOST_CODE}
    assert _session_code(Limited()) == {}


# ---------------------------------------------------------------------------
# A lost session is no longer believed for the life of the tool server
# ---------------------------------------------------------------------------


def test_a_lost_reading_is_asked_again_soon_and_a_good_one_is_believed_for_minutes() -> None:
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    assert registry._x_identity_usable() is False, "nic nie czytano"
    # A LOST session is a login wall (`login_wall=True`): the one proof of it. (Until 13.10.2026 this
    # test built the lost reading without the wall, i.e. what a timeout looks like, which is the
    # confusion it now guards against: see the next test.)
    registry._x_identity_noted(_XState(False, None, login_wall=True))
    assert registry._x_identity_usable() is True
    registry._x_identity_at = time.monotonic() - registry.X_IDENTITY_TTL_LOST - 1
    assert registry._x_identity_usable() is False, "utraconą sesję pytamy o nowo"
    registry._x_identity_noted(_XState(True, HANDLE))
    registry._x_identity_at = time.monotonic() - registry.X_IDENTITY_TTL_LOST - 1
    assert registry._x_identity_usable() is True, "dobra stoi dłużej"
    registry._x_identity_at = time.monotonic() - registry.X_IDENTITY_TTL_OK - 1
    assert registry._x_identity_usable() is False
    # A reading nobody dated (a test's, or one set by hand) stands as it always did.
    undated = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    undated._x_identity_cache = _XState(False, None, login_wall=True)
    assert undated._x_identity_usable() is True


def test_an_unknown_reading_is_never_believed_not_even_for_a_moment() -> None:
    """12 and 13.10.2026: a card poll that hit a slow page noted `logged_in=False` and the next
    `x.read` believed it for 20 s, then the next poll refreshed it. An unknown carries no
    information, so nobody may be handed one as if it were an answer."""
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    registry._x_identity_noted(_XState(False, None, "strona się nie wczytała"))
    assert registry._x_identity_usable() is False
    undated = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    undated._x_identity_cache = _XState(False, None, "nie odczytano uchwytu")
    assert undated._x_identity_usable() is False
