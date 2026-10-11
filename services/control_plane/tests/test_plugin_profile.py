"""The plugin's trust profile, without a database: capability, tool allowlist, entry point.

A session driven by the person's OWN coding agent (Codex, Claude Code) is a weaker trust domain
than one Codex runs for the control plane: the agent has a shell and its own instructions. These
tests pin the guardrails that do not need a database, and say what each of them does NOT claim.
A cooperative agent is held to the narrowed catalog; an agent that goes around the tool server
through Docker is not stopped by any of this, and the module docstring of `plugin_server.py`
says so too.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.sessions.trace_payloads import (
    args_digest,
    tool_call_payload,
    tool_call_payloads,
    tool_result_payload,
)
from control_plane.tools import (
    CATALOG,
    HOSTS,
    MAX_TTL_MINUTES,
    PLUGIN_EXCLUDED,
    PLUGIN_TOOLS,
    CapabilityError,
    CapabilityExpired,
    NoSuchTool,
    SessionCapability,
    ToolRegistry,
    issue,
    listing,
    load_from_file,
    manifest,
    write_capability,
)
from control_plane.tools.capability import parse_capability
from control_plane.tools.catalog import APP_ONLY_TOOLS, allowed_for_host
from control_plane.tools.mcp_server import MCPToolServer, _Loop
from control_plane.tools.plugin_server import (
    PluginRefusal,
    ProjectChoice,
    choose_project,
)
from control_plane.tools.registry import _digest

CONTROL_PLANE = Path(__file__).resolve().parents[1]
DSN = "postgresql://nieuzywane-w-tescie"

#: The two characters the founder does not want in any text the product shows: em and en dash.
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")


def _capability(**changes) -> SessionCapability:
    base = {
        "session_id": uuid4(),
        "organization_id": uuid4(),
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "dsn": DSN,
    }
    return issue(**{**base, **changes})


# ---------------------------------------------------------------------------
# Capability: the host travels in the file, and nothing about it is guessed
# ---------------------------------------------------------------------------


def test_a_host_capability_survives_the_file_with_mode_0600(tmp_path: Path) -> None:
    capability = _capability(host="claude-code", ttl_minutes=480)
    path = write_capability(tmp_path / "run" / "cap.json", capability)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "plik z DSN czytelny dla innych"
    loaded = load_from_file(path)
    assert loaded == capability
    assert loaded.host == "claude-code"
    assert loaded.expires_at - loaded.issued_at == timedelta(minutes=480)


def test_a_wenext_run_capability_serialises_a_null_host_and_reads_it_back(tmp_path: Path) -> None:
    """`host` is ALWAYS in the file: a reader must tell `null` (Wenext-run) from 'old writer'."""
    capability = _capability()
    path = write_capability(tmp_path / "cap.json", capability)

    assert json.loads(path.read_text("utf-8"))["host"] is None
    assert load_from_file(path).host is None


def test_a_file_without_the_host_key_fails_closed(tmp_path: Path) -> None:
    """An older writer's file has no `host`. Reading it as 'Wenext-run, the whole catalog' would
    hand the widest scope to a file nobody classified."""
    payload = _capability(host="codex").to_json()
    del payload["host"]

    with pytest.raises(CapabilityError, match="host"):
        parse_capability(payload)


@pytest.mark.parametrize("name", ["", "Codex", "claude", "gemini", "codex ", "CODEX"])
def test_an_unknown_host_fails_closed_everywhere_it_can_enter(name: str) -> None:
    """The name selects a trust profile, so an unknown one gets none: not a default, an error."""
    payload = _capability(host="codex").to_json()
    payload["host"] = name
    with pytest.raises(CapabilityError, match="agent"):
        parse_capability(payload)
    with pytest.raises(CapabilityError, match="agent"):
        issue(
            session_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            project_id=uuid4(),
            dsn=DSN,
            host=name,
        )


def test_the_closed_list_of_hosts_is_what_the_launcher_documents() -> None:
    assert {"codex", "claude-code"} == HOSTS


def test_the_lifetime_is_capped_at_a_day_and_never_clamped() -> None:
    assert MAX_TTL_MINUTES == 1440
    assert _capability(host="codex", ttl_minutes=MAX_TTL_MINUTES).host == "codex"
    with pytest.raises(CapabilityError, match="sufit"):
        _capability(host="codex", ttl_minutes=MAX_TTL_MINUTES + 1)
    with pytest.raises(CapabilityError):
        _capability(host="codex", ttl_minutes=0)


def test_an_expired_host_capability_stops_the_call_and_keeps_its_host() -> None:
    capability = _capability(
        host="codex", now=datetime.now(UTC) - timedelta(hours=9), ttl_minutes=480
    )
    with pytest.raises(CapabilityExpired):
        capability.check_live()
    assert capability.host == "codex"


# ---------------------------------------------------------------------------
# The tool profile: an explicit allowlist and an explicit list of exclusions
# ---------------------------------------------------------------------------

EXCLUDED = (
    "session.handoff",
    "session.result",
    "session.list_returns",
    "session.cancel_return",
    "session.schedule_return",
    "drafts.publish",
    "draft.mark_posted",  # hosted only: the person posted by hand (HOSTED_TOOLS)
)


def test_every_catalog_tool_is_on_exactly_one_of_the_two_lists() -> None:
    names = {spec.name for spec in CATALOG}
    assert len(CATALOG) == 54, "katalog Wenext-run zmienił rozmiar; profil wtyczki wymaga decyzji"
    assert PLUGIN_TOOLS & set(PLUGIN_EXCLUDED) == set(), "narzędzie na obu listach"
    assert PLUGIN_TOOLS | set(PLUGIN_EXCLUDED) == names, "narzędzie na żadnej liście"
    assert len(PLUGIN_TOOLS) == 47 and len(PLUGIN_EXCLUDED) == 7
    assert set(PLUGIN_EXCLUDED) == set(EXCLUDED)


def test_every_exclusion_carries_a_reason_a_person_can_read() -> None:
    for name, reason in PLUGIN_EXCLUDED.items():
        assert len(reason.split()) >= 5, f"{name}: powód jest hasłem, nie zdaniem: {reason!r}"
        assert not LONG_DASHES.search(reason), f"{name}: długa pauza w powodzie"


def test_the_plugin_manifest_is_the_allowlist_and_the_default_stays_the_whole_catalog() -> None:
    narrowed = manifest(host="codex")
    assert {entry["name"] for entry in narrowed} == PLUGIN_TOOLS
    assert not {entry["name"] for entry in narrowed} & set(EXCLUDED)

    # The Wenext-run path is untouched: 53 entries, with or without the platform narrowing.
    assert len(manifest()) == 54
    assert len(manifest(frozenset({"comment"}))) == 54
    assert len(listing()) == 54
    assert {entry["name"] for entry in listing(host="claude-code")} == PLUGIN_TOOLS


def test_the_narrowing_by_host_composes_with_the_narrowing_by_platform() -> None:
    """`draft.create` still says WYMAGA_POLACZENIA when no account can comment, host or not."""
    entry = next(
        one for one in manifest(frozenset(), host="codex") if one["name"] == "draft.create"
    )
    assert "WYMAGA_POLACZENIA" in entry["description"]


def test_allowed_for_host() -> None:
    assert allowed_for_host("drafts.publish", None) is True
    assert allowed_for_host("drafts.publish", "codex") is False
    assert allowed_for_host("draft.create", "codex") is True
    assert allowed_for_host("not.a.tool", "codex") is False


@pytest.mark.parametrize("name", EXCLUDED)
async def test_an_excluded_tool_is_not_there_for_a_host_session(name: str) -> None:
    """Refused BEFORE any database access: this registry has no pool at all."""
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    with pytest.raises(NoSuchTool) as raised:
        await registry.call(name, {})
    assert "nie jest dostępne" in str(raised.value)
    assert not LONG_DASHES.search(str(raised.value))


async def test_describing_an_excluded_tool_gives_the_answer_for_a_missing_one() -> None:
    """A plugin agent must not learn what the product keeps from it by asking to describe it."""
    registry = ToolRegistry(capability=_capability(host="codex"), pool=None)  # type: ignore[arg-type]
    excluded = await registry.call("catalog.describe", {"tool": "drafts.publish"})
    missing = await registry.call("catalog.describe", {"tool": "no.such.tool"})
    assert excluded["stan"] == "BLAD"
    assert excluded["powod"].replace("drafts.publish", "X") == missing["powod"].replace(
        "no.such.tool", "X"
    )

    allowed = await registry.call("catalog.describe", {"tool": "draft.create"})
    assert allowed["stan"] == "WYKONANE"


async def test_a_wenext_run_session_still_describes_the_excluded_tools() -> None:
    registry = ToolRegistry(capability=_capability(), pool=None)  # type: ignore[arg-type]
    described = await registry.call("catalog.describe", {"tool": "drafts.publish"})
    assert described["stan"] == "WYKONANE"


# ---------------------------------------------------------------------------
# Trace payloads: the SAME shaping for both writers
# ---------------------------------------------------------------------------


def _envelope(result: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}


def test_the_result_payload_reads_cards_and_outcome_from_the_mcp_envelope() -> None:
    """The projection reads `content[0].text`; a payload that looked for `karty` on the top level
    of the envelope would never find a card (the bug of 20.09, in a new writer)."""
    card = {"karta_id": "szkic:1", "rodzaj": "SZKIC"}
    item = {
        "tool": "draft.create",
        "server": "wenext",
        "arguments": {"text": "t"},
        "status": "completed",
        "durationMs": 12,
        "result": _envelope({"stan": "OCZEKUJE", "karty": [card]}),
    }
    call, result = tool_call_payloads(item)

    assert call["args_sha256"] == args_digest("draft.create", {"text": "t"})
    assert call == tool_call_payload(item)
    assert result == tool_result_payload(item)
    assert result["karty"] == [card]
    assert result["outcome"] == "OCZEKUJE"
    assert result["status"] == "completed"
    assert result["result"]["content"][0]["type"] == "text"


def test_the_digest_the_registry_reads_is_the_one_the_trace_writes() -> None:
    """One implementation now; this pins that nobody re-introduces a second."""
    assert _digest is args_digest
    assert _digest("a", {"b": 1}) == args_digest("a", {"b": 1})


def test_a_failed_call_leaves_a_result_that_is_not_taken_for_done() -> None:
    """`_already_done` skips `status = failed`; the payload must carry exactly that word."""
    result = tool_result_payload(
        {"tool": "draft.create", "status": "failed", "error": "RuntimeError: x", "result": None}
    )
    assert result["status"] == "failed" and result["karty"] == [] and result["outcome"] == ""


# ---------------------------------------------------------------------------
# Project choice: pure, and the refusals say what to set
# ---------------------------------------------------------------------------


def _project(name: str) -> ProjectChoice:
    return ProjectChoice(
        organization_id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        name=name,
        workspace_name="Moja przestrzeń",
    )


def test_the_only_project_is_chosen_without_being_named() -> None:
    only = _project("Jedyny")
    assert choose_project([only], None) is only
    assert choose_project([only], "  ") is only


def test_several_projects_are_refused_and_listed() -> None:
    first, second = _project("Alfa"), _project("Beta")
    with pytest.raises(PluginRefusal) as raised:
        choose_project([first, second], None)
    message = str(raised.value)
    assert message.startswith("Jest kilka projektów. Ustaw WENEXT_PROJECT. Dostępne: ")
    assert "Alfa" in message and "Beta" in message
    assert str(first.project_id) in message and str(second.project_id) in message
    assert not LONG_DASHES.search(message)


def test_a_project_can_be_named_by_id_or_by_name() -> None:
    first, second = _project("Alfa"), _project("Beta")
    assert choose_project([first, second], str(second.project_id)) is second
    assert choose_project([first, second], "alfa") is first


def test_the_exact_name_wins_over_the_same_name_in_another_case() -> None:
    upper, lower = _project("Alfa"), _project("alfa")
    assert choose_project([upper, lower], "Alfa") is upper
    assert choose_project([upper, lower], "alfa") is lower
    with pytest.raises(PluginRefusal, match="kilku projektów"):
        choose_project([upper, lower], "ALFA")


def test_an_unknown_or_ambiguous_project_is_refused() -> None:
    first, twin = _project("Alfa"), _project("Alfa")
    with pytest.raises(PluginRefusal, match="Nie ma projektu"):
        choose_project([first], "Gamma")
    with pytest.raises(PluginRefusal, match="kilku projektów"):
        choose_project([first, twin], "Alfa")
    with pytest.raises(PluginRefusal, match="nie ma jeszcze projektu"):
        choose_project([], None)


# ---------------------------------------------------------------------------
# The entry point works without a database
# ---------------------------------------------------------------------------


def test_the_plugin_server_help_works_without_a_database() -> None:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env["PYTHONPATH"] = str(CONTROL_PLANE)
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "--help"],
        cwd=CONTROL_PLANE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "serve" in done.stdout and "projects" in done.stdout, done.stdout


def test_serving_without_a_database_refuses_plainly_and_keeps_stdout_clean() -> None:
    """stdout is the MCP channel: a refusal there would be read by the host as a bad frame."""
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env["PYTHONPATH"] = str(CONTROL_PLANE)
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "serve"],
        cwd=CONTROL_PLANE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    assert done.stdout == ""
    assert "DATABASE_URL" in done.stderr and not LONG_DASHES.search(done.stderr)


@pytest.mark.parametrize(
    ("env_name", "value", "code"),
    [("WENEXT_HOST", "gemini", 5), ("WENEXT_PLUGIN_TTL_HOURS", "25", 5)],
)
def test_a_bad_host_or_lifetime_is_a_configuration_error(env_name, value, code) -> None:
    """Exit 5 BEFORE the database is touched, so these need none."""
    env = {key: val for key, val in os.environ.items() if key != "DATABASE_URL"}
    env["PYTHONPATH"] = str(CONTROL_PLANE)
    env[env_name] = value
    done = subprocess.run(
        [sys.executable, "-m", "control_plane.tools.plugin_server", "serve"],
        cwd=CONTROL_PLANE,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == code, (done.returncode, done.stdout, done.stderr)
    assert done.stdout == "" and env_name in done.stderr


# ---------------------------------------------------------------------------
# The protocol loop, with a registry that needs no database
# ---------------------------------------------------------------------------


class _StubRegistry:
    """Just enough registry for the loop: a platform answer, and one call that raises."""

    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises

    async def platform_capabilities(self) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, args: dict) -> dict:
        if self._raises is not None:
            raise self._raises
        return {"stan": "WYKONANE"}


class _StubSessions:
    """The trace store, reduced to the one method the loop calls; keeps what it was given."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def append_event(self, *, organization_id, session_id, kind, payload) -> None:
        self.events.append((kind, payload))


def _serve(
    host: str | None,
    registry: _StubRegistry,
    frames: list[dict],
    *,
    sessions: bool | _StubSessions | None = True,
) -> list[dict]:
    """`sessions=True` gives a host server its trace store, as `main()` does; `None` withholds it."""
    runner = _Loop()
    runner.start()
    out = io.StringIO()
    try:
        server = MCPToolServer(
            capability=_capability(host=host),
            registry=registry,  # type: ignore[arg-type]
            runner=runner,
            sessions=(_StubSessions() if sessions is True and host else sessions or None),  # type: ignore[arg-type]
            stream_in=io.StringIO(),
            stream_out=out,
        )
        for frame in frames:
            server.handle(frame)
    finally:
        runner.stop()
    return [json.loads(line) for line in out.getvalue().splitlines()]


_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}


def test_tools_list_is_the_narrow_one_for_a_host_and_the_whole_one_otherwise() -> None:
    listed = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    host_answer = _serve("codex", _StubRegistry(), [_INIT, listed])[1]
    wenext_run_answer = _serve(None, _StubRegistry(), [_INIT, listed])[1]

    # The model-visible list of a host is the allowlist and nothing else. What else Codex gets are
    # the card's own tools, marked `visibility: ["app"]`, which the host must keep from the agent;
    # the tests of `card_send` hold that line (`test_card_send_unit.py`).
    host_names = {tool["name"] for tool in host_answer["result"]["tools"]}
    assert host_names - APP_ONLY_TOOLS == PLUGIN_TOOLS
    assert host_names & APP_ONLY_TOOLS == APP_ONLY_TOOLS
    assert len(wenext_run_answer["result"]["tools"]) == 54
    assert not {t["name"] for t in wenext_run_answer["result"]["tools"]} & APP_ONLY_TOOLS


def test_an_expired_plugin_capability_tells_the_person_to_restart_the_agent() -> None:
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "project.read", "arguments": {}},
    }
    expired = CapabilityExpired("zdolność sesji x wygasła o 2026-10-10T08:00:00+00:00")
    host_error = _serve("codex", _StubRegistry(expired), [_INIT, call])[1]["error"]
    run_error = _serve(None, _StubRegistry(expired), [_INIT, call])[1]["error"]

    assert "Uruchom agenta ponownie" in host_error["message"]
    assert not LONG_DASHES.search(host_error["message"])
    assert "Uruchom agenta ponownie" not in run_error["message"], "tura Wenext nie ma agenta"


class _CountingRegistry(_StubRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def call(self, name: str, args: dict) -> dict:
        self.calls += 1
        return await super().call(name, args)


def test_a_host_server_with_no_trace_store_runs_nothing() -> None:
    """Fail closed: a plugin call that leaves no row has no card and no `_already_done`."""
    registry = _CountingRegistry()
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "project.read", "arguments": {}},
    }
    answer = _serve("codex", registry, [_INIT, call], sessions=None)[1]

    assert answer["result"]["isError"] is True
    assert "nie ma gdzie zapisać śladu" in answer["result"]["content"][0]["text"]
    assert registry.calls == 0, "narzędzie wykonało się bez możliwości zapisania śladu"
    # The Wenext-run server is unaffected: its trace is written by the turn handler.
    assert _serve(None, registry, [_INIT, call])[1]["result"]["isError"] is False
    assert registry.calls == 1


def test_every_listed_tool_says_whether_it_only_reads() -> None:
    # Codex asks the person before any MCP call not marked read-only; a non-interactive run
    # refused `project.read` until this annotation existed (measured 10.10 on codex-cli 0.160.1).
    from control_plane.tools.catalog import CATALOG, manifest

    by_name = {spec.name: spec for spec in CATALOG}
    for entry in manifest(host="codex") + manifest():
        assert entry["annotations"] == {"readOnlyHint": not by_name[entry["name"]].writes}


def test_the_host_gets_instructions_without_long_dashes_and_a_wenext_session_does_not() -> None:
    from control_plane.tools.mcp_server import HOST_INSTRUCTIONS

    assert "U+2014" in HOST_INSTRUCTIONS and "publish" in HOST_INSTRUCTIONS
    assert "\u2014" not in HOST_INSTRUCTIONS and "\u2013" not in HOST_INSTRUCTIONS
    host_init = _serve("codex", _StubRegistry(), [_INIT])[0]["result"]
    wenext_run_init = _serve(None, _StubRegistry(), [_INIT])[0]["result"]
    assert host_init["instructions"] == HOST_INSTRUCTIONS
    assert "instructions" not in wenext_run_init


def test_a_host_gets_the_room_card_and_a_wenext_run_session_does_not() -> None:
    from control_plane.tools import apps_ui

    listed = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resources = {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}
    read = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "resources/read",
        "params": {"uri": apps_ui.OPPORTUNITIES_URI},
    }
    call = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "room.show", "arguments": {}},
    }
    old_card = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "resources/read",
        "params": {"uri": "ui://wenext/okazje-v2.html"},
    }
    host = _serve("codex", _StubRegistry(), [_INIT, listed, resources, read, call, old_card])

    assert "resources" in host[0]["result"]["capabilities"]
    tools = {t["name"]: t for t in host[1]["result"]["tools"]}
    assert tools["room.show"]["_meta"]["ui"]["resourceUri"] == apps_ui.OPPORTUNITIES_URI
    assert tools["room.show"]["annotations"] == {"readOnlyHint": True}
    # The card is the room, so only the room tool draws it: a card fed by a list that the agent
    # calls at the start of its work would show a half-empty room to the person watching. The
    # other entries with a `_meta` are the card's own app-only tools, which draw nothing.
    assert [
        name for name, t in tools.items() if "resourceUri" in t.get("_meta", {}).get("ui", {})
    ] == ["room.show"]
    assert {name for name, t in tools.items() if "_meta" in t} == {"room.show", *APP_ONLY_TOOLS}
    assert [r["uri"] for r in host[2]["result"]["resources"]] == [apps_ui.OPPORTUNITIES_URI]
    assert apps_ui.OPPORTUNITIES_URI.endswith("-v8.html")
    content = host[3]["result"]["contents"][0]
    assert content["mimeType"] == "text/html;profile=mcp-app"
    assert content["_meta"]["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    # The host gets the result as an object next to the text block; the card is drawn from it.
    assert host[4]["result"]["structuredContent"] == {"stan": "WYKONANE"}
    assert host[5]["error"]["code"] == -32602, "v2 jest nieaktualna i nie jest już serwowana"
    older = _serve(
        "codex",
        _StubRegistry(),
        [
            _INIT,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "resources/read",
                "params": {"uri": "ui://wenext/okazje-v3.html"},
            },
        ],
    )
    assert older[1]["error"]["code"] == -32602, "v3 nie umie wysyłać i nie jest już serwowana"
    previous = _serve(
        "codex",
        _StubRegistry(),
        [
            _INIT,
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "resources/read",
                "params": {"uri": "ui://wenext/okazje-v4.html"},
            },
        ],
    )
    assert previous[1]["error"]["code"] == -32602, (
        "v4 mówi tylko po polsku i nie jest już serwowana"
    )
    stale = _serve(
        "codex",
        _StubRegistry(),
        [
            _INIT,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "resources/read",
                "params": {"uri": "ui://wenext/okazje-v5.html"},
            },
        ],
    )
    assert stale[1]["error"]["code"] == -32602, (
        "v5 podpisuje jedno konto i zostawia nieaktualne odmowy, więc nie jest już serwowana"
    )
    columns = _serve(
        "codex",
        _StubRegistry(),
        [
            _INIT,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "resources/read",
                "params": {"uri": "ui://wenext/okazje-v6.html"},
            },
        ],
    )
    assert columns[1]["error"]["code"] == -32602, (
        "v6 układa ludzi w kolumny, których nie widać naraz, więc nie jest już serwowana"
    )

    run = _serve(None, _StubRegistry(), [_INIT, listed, resources, call])
    assert "resources" not in run[0]["result"]["capabilities"]
    assert all("_meta" not in t for t in run[1]["result"]["tools"])
    assert run[2]["error"]["code"] == -32601
    assert "structuredContent" not in run[3]["result"]


def test_the_room_tool_only_reads_and_the_plugin_still_has_no_way_to_publish() -> None:
    from control_plane.tools.catalog import BY_NAME

    assert "room.show" in PLUGIN_TOOLS and BY_NAME["room.show"].writes is False
    assert BY_NAME["room.show"].input_schema["properties"] == {}
    # Nothing that publishes, approves or signs is on the plugin's list, by name; `drafts.publish`
    # stays on the excluded one, with its reason.
    assert "drafts.publish" not in PLUGIN_TOOLS and "drafts.publish" in PLUGIN_EXCLUDED
    for name in PLUGIN_TOOLS:
        assert not re.search(r"publish|approve|sign|submit", name), name
    # `draft.create` is the only writer of a draft and it leaves the draft waiting for a human.
    writers = {n for n in PLUGIN_TOOLS if BY_NAME[n].writes and n.startswith(("draft", "deleg"))}
    assert writers == {"delegation.propose", "draft.create"}


def test_the_card_html_is_self_contained_dash_free_and_cannot_act_but_through_the_host() -> None:
    from control_plane.tools import apps_ui

    html = apps_ui.OPPORTUNITIES_HTML
    assert not LONG_DASHES.search(html)
    # Nothing is loaded or sent from anywhere: no external address in an attribute, a stylesheet or
    # an import, no script file, no network API, and the declared policy is empty.
    assert not re.search(r"""(?:src|href|action)\s*=\s*["']?(?:https?:)?//""", html)
    assert not re.search(r"url\(\s*[\"']?(?:https?:)?//|@import|<link|<script[^>]*\ssrc=", html)
    assert not re.search(r"fetch\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|eval\(", html)
    assert "__MARK__" not in html
    # It speaks to the host with exactly these messages: a handshake, a size, a message to the
    # agent, a link, and (v4) `tools/call`. The only tools it calls are `room.show` (not even
    # that: it draws what it is given) and the three app-only ones of `card_send`, and none of
    # them is a tool the model could call: the card never names `draft.create`, `drafts.publish`
    # or any other tool of the catalog. It never builds a route of the dashboard's API.
    methods = set(re.findall(r"""(?:request|notify)\("(ui/[a-z/-]+)\"""", html))
    assert methods == {
        "ui/initialize",
        "ui/notifications/initialized",
        "ui/notifications/size-changed",
        "ui/message",
        "ui/open-link",
    }
    assert 'rpc("tools/call"' in html
    # (v6: the send machine reaches the host through `env.call`, which is the card's `callTool`.)
    called = set(re.findall(r"""(?:callTool|env\.call)\("([a-z.]+_[a-z]+)\"""", html))
    # v8 (hosted): the manual flow's "Done" calls `draft.mark_posted`, the one catalog tool the card
    # names. It records the person's own statement that they posted by hand and publishes nothing.
    assert called == {
        "room.send_token",
        "room.send_draft",
        "room.send_status",
        "draft.mark_posted",
    }
    assert called - {"draft.mark_posted"} == set(APP_ONLY_TOOLS)
    assert not re.search(r"drafts?\.(publish|create)|delegation\.", html)
    assert "/approvals" not in html and "/approve" not in html
    assert "#szkic-" in html and "Zatwierdź w Pokoju" in html


def test_the_card_has_the_words_of_the_pokoj_and_never_a_zero_for_a_gap() -> None:
    from control_plane.tools import apps_ui

    html = apps_ui.OPPORTUNITIES_HTML
    for word in ("brak pomiaru", "Wygasa za", "Okno zgody minęło", "usunięty z platformy"):
        assert word in html, word
    assert "Opublikowano" in html
    assert "v == null ? " in html, "brakujący licznik to brak pomiaru, nie zero"


def test_the_host_instructions_send_the_agent_to_the_room_card() -> None:
    from control_plane.tools.mcp_server import HOST_INSTRUCTIONS

    assert "room.show" in HOST_INSTRUCTIONS
