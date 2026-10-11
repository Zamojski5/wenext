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
