"""The Codex project file of the demo: what it pins, what it switches off, and that it is quiet.

`scripts/demo/codex_project.py` writes `<tallymark>/.codex/config.toml`, the layer Codex reads for a
TRUSTED project only. The mechanism itself was measured with the real Codex binary (an app-server
with a scratch CODEX_HOME, 11.10): a project's `mcp_servers.wenext` replaces the plugin's server in
that project, its `env` reaches the launcher, `disabled_tools` hides tools, and an untrusted project
is ignored. This file keeps the generated file true to that: valid TOML, the demo's ids, English,
and no tool name that does not exist (a renamed tool would otherwise stay switched on).
"""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
from control_plane import demo
from control_plane.tools import PLUGIN_TOOLS

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "demo" / "codex_project.py"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")


@pytest.fixture(scope="module")
def project():
    spec = importlib.util.spec_from_file_location("codex_project_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_config_is_valid_toml_that_replaces_the_plugin_server_in_this_project(project) -> None:
    config = tomllib.loads(project.render_config())
    server = config["mcp_servers"]["wenext"]
    assert server["command"] == str(project.LAUNCHER) and Path(server["command"]).is_absolute()
    assert Path(server["command"]).is_file()
    assert server["args"] == ["mcp"]
    assert config["web_search"] == "disabled"


def test_it_pins_the_sandbox_project_and_an_english_card(project) -> None:
    env = tomllib.loads(project.render_config())["mcp_servers"]["wenext"]["env"]
    assert env["WENEXT_PROJECT"] == "tallymark"
    assert env["WENEXT_ORGANIZATION"] == str(demo.DEMO_ORGANIZATION_ID)
    assert env["WENEXT_LANG"] == "en"
    assert env["WENEXT_HOST"] == "codex"
    assert Path(env["WENEXT_HOME"], "docker-compose.yml").is_file()


def test_it_switches_off_the_tools_that_read_a_platform_or_the_web_and_only_real_ones(project) -> None:
    off = tomllib.loads(project.render_config())["mcp_servers"]["wenext"]["disabled_tools"]
    assert {"reddit.search", "reddit.read", "x.search", "x.read", "browser.inspect"} <= set(off)
    assert not set(off) - PLUGIN_TOOLS, f"names the plugin profile does not have: {set(off) - PLUGIN_TOOLS}"
    # What the film needs stays on.
    assert not {"room.show", "opportunities.list", "opportunities.mark", "project.read"} & set(off)


def test_the_words_it_writes_are_plain_and_have_no_long_dash(project) -> None:
    for text in (project.render_config(), project.AGENTS_MD):
        assert not LONG_DASHES.search(text)
    assert "opportunities.list" in project.AGENTS_MD and "room.show" in project.AGENTS_MD
    assert "Never say anything was posted" in project.AGENTS_MD


def test_writing_the_files_is_idempotent_and_keeps_them_out_of_git(project, tmp_path) -> None:
    repo = tmp_path / "tallymark"
    (repo / ".git" / "info").mkdir(parents=True)
    (repo / ".git" / "info" / "exclude").write_text("# comment\n", encoding="utf-8")
    first = project.write(repo)
    second = project.write(repo)
    assert (repo / ".codex" / "config.toml").is_file() and (repo / "AGENTS.md").is_file()
    assert any("exclude" in line for line in first) and not any("exclude" in line for line in second)
    lines = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert lines.count(".codex/") == 1 and lines.count("AGENTS.md") == 1 and lines[0] == "# comment"


def test_a_folder_that_does_not_exist_is_refused(project, tmp_path) -> None:
    with pytest.raises(SystemExit):
        project.write(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# The Claude Code half (`--host claude-code`): `.claude/settings.local.json` and CLAUDE.md
# ---------------------------------------------------------------------------

#: Anything that sends, signs, approves, connects an account or publishes. Never allowed on the take.
FORBIDDEN_TO_ALLOW = re.compile(r"send|sign|approve|publish|delegat|connect|login|grant|decid|room_post", re.I)


def test_claude_settings_pin_the_sandbox_in_english_for_this_folder_only(project) -> None:
    settings = json.loads(project.render_claude_settings())
    env = settings["env"]
    assert env["WENEXT_PROJECT"] == "tallymark"
    assert env["WENEXT_ORGANIZATION"] == str(demo.DEMO_ORGANIZATION_ID)
    assert env["WENEXT_LANG"] == "en" and env["WENEXT_HOST"] == "claude-code"
    assert Path(env["WENEXT_HOME"], "docker-compose.yml").is_file()
    # Only keys the launcher knows, and nothing that carries a secret.
    assert set(env) == {"WENEXT_HOME", "WENEXT_PROJECT", "WENEXT_ORGANIZATION", "WENEXT_LANG", "WENEXT_HOST"}


def test_claude_settings_deny_every_reading_tool_and_the_web_and_allow_no_send(project) -> None:
    permissions = json.loads(project.render_claude_settings())["permissions"]
    prefix = project.CLAUDE_TOOL_PREFIX
    deny = set(permissions["deny"])
    assert {"WebSearch", "WebFetch"} <= deny
    assert {prefix + "reddit_search", prefix + "x_read", prefix + "browser_inspect", prefix + "accounts_connect"} <= deny
    # Every denied name is a real tool of the plugin profile, spelled the way Claude Code spells it.
    spelled = {prefix + name.replace(".", "_") for name in PLUGIN_TOOLS}
    assert {name for name in deny if name.startswith(prefix)} <= spelled
    allow = permissions["allow"]
    assert set(allow) <= spelled and not set(allow) & deny
    assert allow == [prefix + "room_show", prefix + "opportunities_list", prefix + "draft_create", prefix + "opportunities_mark"]
    for name in allow:
        assert not FORBIDDEN_TO_ALLOW.search(name[len(prefix) :]), name
    # What the film needs is on the list and only that.
    assert set(project.CLAUDE_ALLOWED_TOOLS) <= PLUGIN_TOOLS


def test_claude_md_is_plain_says_never_to_send_and_has_no_long_dash(project) -> None:
    text = project.CLAUDE_MD
    assert not LONG_DASHES.search(text) and not LONG_DASHES.search(project.render_claude_settings())
    assert "/wenext" in text and "room.show" in text
    assert "Never send or publish anything" in text and "never say anything was posted" in text


def test_writing_the_claude_files_is_idempotent_and_keeps_them_out_of_git(project, tmp_path) -> None:
    repo = tmp_path / "tallymark"
    (repo / ".git" / "info").mkdir(parents=True)
    first = project.write_claude(repo)
    second = project.write_claude(repo)
    assert json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))["env"]
    assert (repo / "CLAUDE.md").is_file()
    assert any("exclude" in line for line in first) and not any("exclude" in line for line in second)
    lines = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert lines.count(".claude/settings.local.json") == 1 and lines.count("CLAUDE.md") == 1
    # The Codex half was not written.
    assert not (repo / ".codex").exists() and not (repo / "AGENTS.md").exists()


def test_the_host_flag_picks_the_half_and_defaults_to_codex(project, tmp_path, capsys) -> None:
    repo = tmp_path / "tallymark"
    repo.mkdir()
    assert project.main(["--repo", str(repo)]) == 0
    assert (repo / ".codex" / "config.toml").is_file() and not (repo / ".claude").exists()
    other = tmp_path / "other"
    other.mkdir()
    assert project.main(["--repo", str(other), "--host", "claude-code"]) == 0
    out = capsys.readouterr().out
    assert (other / ".claude" / "settings.local.json").is_file() and not (other / ".codex").exists()
    assert "claude --plugin-dir" in out and "/wenext" in out
    third = tmp_path / "third"
    third.mkdir()
    assert project.main(["--repo", str(third), "--host", "both"]) == 0
    assert (third / ".codex" / "config.toml").is_file() and (third / ".claude" / "settings.local.json").is_file()
    assert project.main(["--repo", str(third), "--host", "claude-code", "--print"]) == 0
    assert "settings.local.json" in capsys.readouterr().out
