"""The Claude Code mod of the Wenext plugin draws the Codex card without ever being able to publish (v10).

Claude Code is not an MCP Apps host, so `room.show` has no card to draw there. `plugins/wenext/hooks` is a
mod (plugin JS hooks) that draws the same room as a pane beside the transcript, from the same data. What the
mod may NOT do is the whole point, and it is held here, from the outside in:

  * IT CALLS NO MCP TOOL AND REGISTERS NONE. Measured on Claude Code 2.1.296 (12.10.2026): `$.mcp.call` does
    not reach a tool marked `_meta.ui.visibility: ["app"]` (`no connected MCP tool`), nor one the server does
    not list, and a listed one is called with the model's own permission. So the only tools a mod could call
    are the ones the MODEL can call, and the send must stay the person's click: the mod's Send opens the
    Wenext page. Held by what `claude plugin validate` says the module calls, and by a scan of its source;
  * the only program it ever runs is the system's opener (`open`, `xdg-open`), with an http(s) address as one
    argument and no shell;
  * the three messages it asks the agent (Edit, Skip, and `/wenext` when there is no card yet) name a DRAFT
    tool, a SKIP tool and a READ (`room.show`), forbid publishing, and no tool that sends or signs;
  * what it reads of the card is what the server writes: the field names and the health states are the
    server's (`room.show`, `tools/card_connect.py`);
  * the files have no long dash, the module validates and its own tests (`claude plugin test`) pass.

NOT HELD HERE: how the pane looks in a real terminal (the mod is drawn by Claude Code, which this suite does
not run), and that the founder's Claude Code loads it (it is not installed anywhere: `--plugin-dir`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from control_plane.tools import apps_ui, card_connect

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugins" / "wenext"
HOOKS = PLUGIN / "hooks"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
CLAUDE = shutil.which("claude")


def _source(*names: str) -> str:
    return "\n".join((HOOKS / name).read_text(encoding="utf-8") for name in names)


def _code(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


MODULES = ("register.ts", "draw.ts", "room.ts", "words.ts")


# ---------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------


def test_the_mod_is_three_files_and_a_contract_inside_the_local_plugin() -> None:
    assert json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8")) == {
        "modules": ["./register.ts"]
    }
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "wenext" and manifest["types"] == "./types/index.d.ts"
    assert (PLUGIN / "types" / "index.d.ts").is_file()
    for name in MODULES:
        assert (HOOKS / name).is_file(), name
    # The Codex side is untouched: its manifest knows nothing of hooks.
    codex = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in codex and "modules" not in codex


# ---------------------------------------------------------------------------
# It cannot publish: it reaches no MCP tool, registers none, runs only the opener
# ---------------------------------------------------------------------------

FORBIDDEN_APIS = (
    r"\$\.mcp\b",
    r"\$\.tool\b",
    r"\$\.agent\b",
    r"\$\.http\b",
    r"\$\.fs\b",
    r"\$\.settings\b",
    r"\$\.config\b",
    r"\$\.model\b",
    r"\$\.session\.(?:append|send|authorize|compact)\b",
    r"\$\.store\b",
    r"\bimport\s*\(",
    r"\beval\s*\(",
    r"\bnew Function\b",
)


@pytest.mark.parametrize("pattern", FORBIDDEN_APIS)
def test_the_mod_never_uses_an_api_that_could_reach_a_tool_or_the_machine_beyond_its_job(
    pattern: str,
) -> None:
    code = _code(_source(*MODULES))
    assert not re.search(pattern, code), pattern


def test_the_only_program_it_runs_is_the_systems_opener_with_one_address() -> None:
    code = _code(_source(*MODULES))
    runs = re.findall(r"\$\.process\.(\w+)\(([^)]*)\)", code)
    assert [name for name, _ in runs] == ["run"], "jedno miejsce, w którym mod uruchamia program"
    assert "[...argv, address]" in runs[0][1], "adres jest jednym argumentem, bez powłoki"
    assert "const OPENERS: readonly (readonly string[])[] = [['open'], ['xdg-open']]" in code
    # And the address is checked before it is handed over: http(s) only, no space, bounded.
    web = _source("room.ts")
    assert "/^https?:\\/\\/[^\\s]+$/i" in web and "text.length <= 2000" in web
    assert "const address = web(url)" in code and "if (address === null) return 'none'" in code


def test_the_pane_reads_one_event_the_room_tool_of_the_local_server() -> None:
    code = _code(_source("register.ts"))
    assert re.findall(r"\bon\('([\w.]+)'", code) == [
        "session.start",
        "tool.call",
        "command.run",
        "ui.render",
        "ui.render",
    ]
    assert "const ROOM_TOOL = /^mcp__(?:plugin_wenext_)?wenext__room_show$/" in code
    regex = re.compile(r"^mcp__(?:plugin_wenext_)?wenext__room_show$")
    assert regex.match("mcp__plugin_wenext_wenext__room_show") and regex.match("mcp__wenext__room_show")
    # Not the hosted plugin (no accounts there), not another tool of the server, not another server.
    for other in (
        "mcp__plugin_wenext-cloud_wenext__room_show",
        "mcp__plugin_wenext_wenext__room_send_draft",
        "mcp__plugin_wenext_wenext__room_show_all",
        "mcp__github__room_show",
    ):
        assert not regex.match(other), other
    # The server's tool is `room.show`; Claude Code spells a dot as an underscore (measured).
    assert "room.show" in apps_ui.TOOL_UI
    # The hook hands the result back as it came.
    hook = code[code.index("on('tool.call'") : code.index("on('command.run'")]
    assert "return ran" in hook and "next(e)" in hook and "deny: " not in hook


def test_the_requests_it_makes_in_the_persons_name_ask_for_a_draft_a_skip_and_a_read_only() -> None:
    words = _source("words.ts")
    pairs = re.findall(r"'(ask\.\w+)':\s*'((?:[^'\\]|\\.)*)'", words)
    english = dict(pairs[: len(pairs) // 2])
    polish = dict(pairs[len(pairs) // 2 :])
    assert set(english) == set(polish) >= {"ask.editReply", "ask.editPost", "ask.skip"}
    for asked in (english, polish):
        assert "draft.create" in asked["ask.editReply"] and "draft.create" in asked["ask.editPost"]
        assert "opportunities.mark" in asked["ask.skip"] and "pomin=true" in asked["ask.skip"]
        for text in asked.values():
            assert not re.search(
                r"send_|drafts\.publish|delegation|sign_scope|approve|zatwierd|podpisz|wyślij",
                text,
                flags=re.I,
            ), text
    assert "publish nothing" in english["ask.editReply"]
    assert "niczego nie publikuj" in polish["ask.editReply"]
    # Both go out as the person's own words, and are not awaited (the pane must not wait for the agent).
    code = _code(_source("register.ts"))
    # The third is the `/wenext` command asking the agent to READ the room (`room.show`), from a timer.
    assert code.count("asUser: true") == 3
    assert code.count(".catch(() => undefined)") == 3
    ask = code[code.index("$.clock.after(50"):]
    assert "room.show" in _source("words.ts") and "$.prompt.submit({ text: ask, asUser: true })" in ask[:200]


def test_the_demo_rehearsal_is_the_servers_word_and_touches_nothing() -> None:
    """The one place the mod says Posted itself: the recording sandbox, said to be a demo, calling nothing."""
    code = _code(_source("register.ts"))
    # The flag is the server's alone (`room.show` says `demo: true` for the recording organization only) and
    # it is never read as text, nor taken for hosted data.
    room = _code(_source("room.ts"))
    assert "demo: data.demo === true && !manual" in room
    assert 'demo=True' not in code
    server = (
        REPO / "services/control_plane/control_plane/tools/opportunity_tools.py"
    ).read_text(encoding="utf-8")
    assert '"demo"' in server
    # Every step of the rehearsal is a state change and a timer: no program, no clipboard, no message.
    start = code.index("async function demoYes(")
    end = code.index("async function demoCancel(")
    rehearsal = code[start:end]
    for forbidden in ("$.process", "$.prompt", "$.ui.copy", "$.mcp", "$.tool", "$.agent", "$.model", "openUrl"):
        assert forbidden not in rehearsal, forbidden
    assert "view.demo" in rehearsal and "view.manual" in rehearsal
    # It starts only from a confirm sentence that was shown first, in this person's own card.
    assert "phase !== 'confirm'" in rehearsal
    # The words say it is a demo, every time the word Posted is used for it.
    words = _source("words.ts")
    assert "(demo)" in words and "demo sandbox" in words and "piaskownica demo" in words


def test_the_send_button_opens_a_page_and_never_asks_the_agent_to_send() -> None:
    code = _code(_source("register.ts"))
    send = code[code.index("async function send(") : code.index("async function copy(")]
    assert "$.prompt" not in send and "$.mcp" not in send
    assert "#szkic-" in send and "openAndSay" in send


@pytest.mark.skipif(CLAUDE is None, reason="claude nie jest zainstalowany")
def test_validate_lists_the_calls_the_module_makes_and_none_of_them_reaches_a_tool() -> None:
    done = subprocess.run(
        [str(CLAUDE), "plugin", "validate", str(PLUGIN), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = json.loads(done.stdout)
    assert report["success"] is True, report
    assert not report["manifest"]["errors"] and not any(c["errors"] for c in report["contents"])
    notes = "\n".join(note for c in report["contents"] for note in c["notes"])
    calls = re.search(r"calls: (.*)", notes)
    assert calls is not None
    named = {part.strip() for part in re.sub(r"\([^)]*\)", "", calls.group(1)).split(",")}
    assert named == {
        "$.clock.after",
        "$.clock.every",
        "$.clock.now",
        "$.command.register",
        "$.env.get",
        "$.process.run",
        "$.prompt.submit",
        "$.state.get",
        "$.state.set",
        "$.ui.copy",
        "$.ui.open",
        "$.ui.resolve",
        "$.ui.toast",
    }, named
    # The one gating hook is the room tool's, and it fails open on purpose: it only watches.
    gating = [g for c in report["contents"] for g in c["gatingHooks"]]
    assert [g["pattern"] for g in gating] == ["tool.call"] and gating[0]["hasCatch"] is True


@pytest.mark.skipif(CLAUDE is None, reason="claude nie jest zainstalowany")
def test_the_mods_own_tests_pass_in_the_engines_kit() -> None:
    done = subprocess.run(
        [str(CLAUDE), "plugin", "test", str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-2000:]
    passed = re.search(r"(\d+) pass", out)
    assert passed is not None and int(passed.group(1)) >= 30, out[-500:]
    assert re.search(r"\b0 fail\b", out), out[-500:]


# ---------------------------------------------------------------------------
# What it reads is what the server writes
# ---------------------------------------------------------------------------


def test_the_health_states_and_field_names_are_the_servers() -> None:
    room = _source("room.ts")
    states = set(re.findall(r"'([a-z_]+)'", room[room.index("const HEALTH") : room.index("function obj(")]))
    assert states == set(card_connect.HEALTH_STATES)
    types = (PLUGIN / "types" / "index.d.ts").read_text(encoding="utf-8")
    for state in card_connect.HEALTH_STATES:
        assert f"'{state}'" in types, state
    base = REPO / "services/control_plane/control_plane"
    server = "\n".join(
        (base / name).read_text(encoding="utf-8")
        for name in ("tools/opportunity_tools.py", "opportunities/room.py")
    )
    for field in (
        "konta_stan",
        "konta_adres",
        "obecnosc_adres",
        "pokoj_adres",
        "zakres",
        "wysylka",
        "jezyk",
        "pominiete_lacznie",
    ):
        assert f'"{field}"' in server, f"serwer nie pisze pola {field}"
        assert field in room, f"mod nie czyta pola {field}"
    # The item keys of `konta_stan`, as `card_connect` writes them.
    health = (REPO / "services/control_plane/control_plane/tools/card_connect.py").read_text(
        encoding="utf-8"
    )
    for key in ("id", "platforma", "konto", "stan", "ekran", "powod", "mozna_polaczyc"):
        assert f'"{key}"' in health, key
    for key in ("konto", "stan", "mozna_polaczyc", "platforma", "id"):
        assert f"row.{key}" in room, key


def test_the_mods_headline_counts_what_the_card_counts() -> None:
    """The card counts the conversations waiting for an answer, not an own post's page."""
    assert "one.key.startsWith('o')" in _source("room.ts")
    assert 'o.stan === "NOWA" || o.stan === "SZKIC"' in apps_ui.OPPORTUNITIES_HTML


def test_the_files_have_no_long_dash() -> None:
    files = [
        *PLUGIN.glob("hooks/*.ts"),
        *PLUGIN.glob("hooks/*.json"),
        *PLUGIN.glob("types/*.ts"),
        *PLUGIN.glob("tests/*.ts"),
        PLUGIN / "README.md",
        PLUGIN / ".claude-plugin" / "plugin.json",
    ]
    assert len(files) >= 10
    for path in files:
        assert not LONG_DASHES.search(path.read_text(encoding="utf-8")), path.name
