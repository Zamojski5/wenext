"""Hosted MCP without a database: the dispatch refactor, the capability flag, token shapes.

THE STDIO PATH MUST NOT MOVE. `MCPToolServer.handle` was the whole protocol; it is now a wrapper
around `async dispatch`. The existing plugin and card tests hold the stdio behaviour in detail;
what is added here is the equivalence itself (the same frames out of `handle` and of `dispatch`),
the new things `dispatch` must do for the hosted door (no handshake gate when told so, no card
tools, the hosted instructions), and the bound a slow tool call has always had.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from control_plane.hosted.http_app import (
    DEFAULT_HOST,
    _ScrubTokens,
    host_from_initialize,
)
from control_plane.hosted.invite import base_url, install_commands
from control_plane.hosted.tokens import (
    TOKEN_PREFIX,
    digest_of,
    looks_like_a_token,
    mint_token,
)
from control_plane.tools import mcp_server
from control_plane.tools.capability import (
    CapabilityError,
    SessionCapability,
    issue,
    parse_capability,
)
from control_plane.tools.catalog import APP_ONLY_TOOLS, HOSTED_INSTRUCTIONS, HOSTED_TOOLS
from control_plane.tools.mcp_server import MCPToolServer, _Loop

DSN = "postgresql://unused-in-this-test"


def _capability(*, host: str | None = "codex", hosted: bool = False) -> SessionCapability:
    return issue(
        session_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        dsn=DSN,
        host=host,
        hosted=hosted,
    )


class _Registry:
    """The two things `dispatch` asks of a registry, with a call that can be slow."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.finished: list[str] = []
        self.called: list[tuple[str, dict]] = []

    async def platform_capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, args: dict) -> dict:
        self.called.append((name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        self.finished.append(name)
        return {"stan": "WYKONANE", "nazwa": name}


class _Sessions:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def append_event(self, *, organization_id, session_id, kind, payload) -> None:
        self.events.append((kind, payload))


def _server(host=None, hosted=False, registry=None, **extra) -> MCPToolServer:
    return MCPToolServer(
        capability=_capability(host=host, hosted=hosted),
        registry=registry or _Registry(),  # type: ignore[arg-type]
        sessions=_Sessions() if host else None,  # type: ignore[arg-type]
        stream_in=io.StringIO(),
        stream_out=io.StringIO(),
        **extra,
    )


_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
_CALL = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {"name": "project.read", "arguments": {}},
}


# ---------------------------------------------------------------------------
# The refactor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", [None, "codex", "claude-code"])
def test_the_stdio_wrapper_writes_exactly_what_dispatch_returns(host) -> None:
    frames = [
        _INIT,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        _LIST,
        _CALL,
        {"jsonrpc": "2.0", "id": 5, "method": "nope/never"},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"arguments": {}}},
        {"jsonrpc": "2.0", "method": "notifications/unknown"},
    ]

    runner = _Loop()
    runner.start()
    try:
        via_stdio = _server(host, runner=runner)
        for frame in frames:
            via_stdio.handle(frame)
        written = via_stdio.stream_out.getvalue()  # type: ignore[union-attr]
    finally:
        runner.stop()

    async def _direct() -> str:
        server = _server(host)
        lines = []
        for frame in frames:
            answer = await server.dispatch(frame)
            if answer is not None:
                lines.append(json.dumps(answer, ensure_ascii=False) + "\n")
        return "".join(lines)

    assert written == asyncio.run(_direct())
    assert written.count("\n") == 6, "two of the eight frames are notifications and get no answer"


def test_a_notification_gets_no_answer_and_the_handshake_gate_is_the_default() -> None:
    async def _go() -> None:
        server = _server("codex")
        assert (
            await server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        )
        refused = await server.dispatch(_LIST)
        assert refused is not None and refused["error"]["code"] == mcp_server.CODE_INTERNAL_ERROR
        assert "initialize" in refused["error"]["message"]
        await server.dispatch(_INIT)
        assert "result" in await server.dispatch(_LIST)

    asyncio.run(_go())


def test_the_gate_can_be_off_for_a_door_that_has_no_connection_to_remember() -> None:
    async def _go() -> None:
        server = _server("codex", hosted=True, require_handshake=False)
        answer = await server.dispatch(_LIST)
        assert answer is not None and "result" in answer, answer

    asyncio.run(_go())


def test_a_tool_call_slower_than_its_bound_is_reported_dead_and_still_finishes(monkeypatch) -> None:
    """The bound always existed (the thread future's timeout). The call it abandons must keep
    running, or its result row would never be written and `_already_done` could not read it."""
    monkeypatch.setattr(mcp_server, "TOOL_TIMEOUT_S", 0.05)

    async def _go() -> None:
        registry = _Registry(delay=0.2)
        server = _server(registry=registry)
        await server.dispatch(_INIT)
        answer = await server.dispatch(_CALL)
        assert answer is not None
        assert answer["result"]["isError"] is True
        assert answer["result"]["content"][0]["text"].startswith("narzędzie padło: TimeoutError")
        assert registry.finished == [], "not finished yet at the moment of the answer"
        await asyncio.sleep(0.3)
        assert registry.finished == ["project.read"], "the abandoned call ran to the end"

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# What a hosted session is
# ---------------------------------------------------------------------------


def test_hosted_needs_an_agent_and_round_trips_through_a_file() -> None:
    with pytest.raises(CapabilityError, match="agent"):
        _capability(host=None, hosted=True)
    cap = _capability(host="claude-code", hosted=True)
    again = parse_capability(cap.to_json())
    assert again.hosted is True and again.host == "claude-code"
    # A file from before the field existed is not hosted.
    legacy = _capability(host="codex").to_json()
    del legacy["hosted"]
    assert parse_capability(legacy).hosted is False
    assert _capability(host=None).to_json()["hosted"] is False


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_a_hosted_session_is_served_the_hosted_surface_and_instructions(host) -> None:
    async def _go() -> None:
        server = _server(host, hosted=True, require_handshake=False)
        init = await server.dispatch(_INIT)
        assert init is not None
        assert init["result"]["instructions"] == HOSTED_INSTRUCTIONS
        assert "\u2014" not in HOSTED_INSTRUCTIONS and "\u2013" not in HOSTED_INSTRUCTIONS
        listed = await server.dispatch(_LIST)
        assert listed is not None
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == HOSTED_TOOLS
        assert not names & APP_ONLY_TOOLS

    asyncio.run(_go())


def test_a_plugin_session_keeps_the_plugin_instructions() -> None:
    async def _go() -> None:
        server = _server("codex")
        init = await server.dispatch(_INIT)
        assert init is not None
        assert init["result"]["instructions"] == mcp_server.HOST_INSTRUCTIONS

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Tokens and the small pure helpers
# ---------------------------------------------------------------------------


def test_a_minted_token_has_the_shape_the_door_accepts_and_a_stable_digest() -> None:
    tokens = {mint_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        assert token.startswith(TOKEN_PREFIX) and looks_like_a_token(token)
        assert len(digest_of(token)) == 64
    assert digest_of("wnx_x") == digest_of("wnx_x") != digest_of("wnx_y")


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "wnx_",
        "wnx_" + "a" * 42,
        "wnx_" + "a" * 44,
        "xnw_" + "a" * 43,
        "wnx_" + "a" * 42 + "!",
        "wnx_" + "a" * 42 + " ",
        "../" + "a" * 43,
        "wnx_" + "a" * 43 + "\n",
    ],
)
def test_a_malformed_token_is_not_looked_up(candidate) -> None:
    assert looks_like_a_token(candidate) is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("codex-mcp-client", "codex"),
        ("Codex", "codex"),
        ("claude-code", "claude-code"),
        ("Claude Code", "claude-code"),
        ("cursor", DEFAULT_HOST),
        ("", DEFAULT_HOST),
    ],
)
def test_the_agent_is_read_from_the_client_name(name, expected) -> None:
    frame = {"method": "initialize", "params": {"clientInfo": {"name": name}}}
    assert host_from_initialize(frame) == expected
    for odd in (
        {},
        {"params": None},
        {"params": {"clientInfo": "x"}},
        {"params": {"clientInfo": {}}},
    ):
        assert host_from_initialize(odd) == DEFAULT_HOST


def test_the_default_agent_is_the_one_with_no_card_tools() -> None:
    from control_plane.tools.catalog import APP_ONLY_HOSTS

    assert DEFAULT_HOST not in APP_ONLY_HOSTS


def test_the_log_filter_removes_the_token_from_a_path() -> None:
    token = mint_token()
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/1.1" %d',
        ("127.0.0.1:1", "POST", f"/mcp/{token}", 200),
        None,
    )
    assert _ScrubTokens().filter(record) is True
    assert token not in record.getMessage()
    assert "/mcp/<redacted>" in record.getMessage()


def test_the_install_commands_and_the_base_address() -> None:
    codex, claude = install_commands("https://w.example", "wnx_T")
    assert codex == "codex mcp add wenext --url https://w.example/mcp/wnx_T"
    assert claude == "claude mcp add --transport http wenext https://w.example/mcp/wnx_T"
    assert base_url(None, {"WENEXT_PUBLIC_DOMAIN": "w.example"}) == "https://w.example"
    assert base_url("http://127.0.0.1:8780/", {}) == "http://127.0.0.1:8780"
    with pytest.raises(ValueError, match="WENEXT_PUBLIC_DOMAIN"):
        base_url(None, {})
    with pytest.raises(ValueError, match="http"):
        base_url("w.example", {})


def test_no_long_dash_in_the_hosted_package() -> None:
    from pathlib import Path

    import control_plane.hosted as package

    forbidden = ("\u2014", "\u2013")
    for path in Path(package.__file__).parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(mark in text for mark in forbidden), path.name


def test_the_capability_window_is_the_only_clock_in_a_hosted_capability() -> None:
    cap = _capability(host="codex", hosted=True)
    assert cap.expires_at - cap.issued_at <= timedelta(hours=1)
    assert cap.issued_at <= datetime.now(UTC)
