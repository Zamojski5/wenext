"""The hosted plugin (`plugins/wenext-cloud`): one source of truth, a separate directory, valid shapes.

The founder's local plugin (`plugins/wenext`, stdio, real publishing) must stay as it is, and the
hosted one must never say anything the server's own instructions do not. What is held here:

  * THE GENERATED FILES ARE CURRENT: `.mcp.json` is what the one constant `DOMAIN` says, and every
    skill is what `build.py` renders from `HOSTED_WORKFLOW`, `HOSTED_REPLY` and `HOSTED_RULES`, the
    pieces of the server's `HOSTED_INSTRUCTIONS`;
  * THE SKILLS DO NOT CONTRADICT THE INSTRUCTIONS: they carry the rules verbatim, name no tool the
    hosted server does not serve, never say a reply is posted by Wenext, and have no Docker, no X
    posting and no long dash;
  * THE SHAPES: the manifests have what the hosts read (name, skills, an http server with no
    command), the two marketplaces list the plugin next to the local one, every path they name
    exists, and the local entries are as they were.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from control_plane.tools.catalog import (
    HOSTED_INSTRUCTIONS,
    HOSTED_REPLY,
    HOSTED_RULES,
    HOSTED_TOOLS,
    HOSTED_WORKFLOW,
)

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugins" / "wenext-cloud"
LOCAL = REPO / "plugins" / "wenext"
LONG_DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
SKILLS = ("first-users", "reply-in-thread", "posted-it")

pytestmark = pytest.mark.skipif(not PLUGIN.is_dir(), reason="plugins/wenext-cloud is not here")


def _build():
    spec = importlib.util.spec_from_file_location("wenext_cloud_build", PLUGIN / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _all_text_files() -> list[Path]:
    return [p for p in PLUGIN.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# One source of truth
# ---------------------------------------------------------------------------


def test_the_generated_files_are_what_the_build_script_renders() -> None:
    build = _build()
    rendered = build.render_all()
    assert set(rendered) == {".mcp.json", *(f"skills/{name}/SKILL.md" for name in SKILLS)}
    for name, text in rendered.items():
        assert (PLUGIN / name).read_text(encoding="utf-8") == text, (
            f"{name} is stale: run services/control_plane/.venv/bin/python plugins/wenext-cloud/build.py"
        )
    assert build.stale() == []


def test_the_address_is_one_constant_and_the_file_follows_it(monkeypatch) -> None:
    build = _build()
    assert _json(PLUGIN / ".mcp.json") == {
        "mcpServers": {"wenext": {"type": "http", "url": f"https://{build.DOMAIN}/mcp"}}
    }
    monkeypatch.setattr(build, "DOMAIN", "mcp.example.org")
    assert (
        json.loads(build.render_mcp())["mcpServers"]["wenext"]["url"]
        == "https://mcp.example.org/mcp"
    )
    assert build.stale() == [".mcp.json"], "changing the constant makes the committed file stale"
    server = _json(PLUGIN / ".mcp.json")["mcpServers"]["wenext"]
    assert set(server) == {"type", "url"}, (
        "an http server: no command, no env, no secret in the file"
    )


def test_every_skill_carries_the_servers_rules_verbatim() -> None:
    for name in SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert HOSTED_RULES in text, name
    first = (PLUGIN / "skills" / "first-users" / "SKILL.md").read_text(encoding="utf-8")
    assert HOSTED_WORKFLOW in first, "the flagship skill is the server's own procedure"
    assert first.index(HOSTED_WORKFLOW) < first.index(HOSTED_RULES)
    reply = (PLUGIN / "skills" / "reply-in-thread" / "SKILL.md").read_text(encoding="utf-8")
    assert HOSTED_REPLY in reply, "one thread, the same way of writing a reply"
    assert f"{HOSTED_WORKFLOW}\n{HOSTED_RULES}" == HOSTED_INSTRUCTIONS


def test_a_skill_names_only_tools_the_hosted_server_serves() -> None:
    served = HOSTED_TOOLS
    named_anywhere = set()
    for name in SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for tool in re.findall(r"`?\b([a-z]+\.[a-z_]+)\b`?", text):
            if tool in {"x.com", "e.g", "i.e"} or "." not in tool:
                continue
            if tool.split(".")[0] in {"reddit", "opportunities", "draft", "drafts", "room", "project",
                                      "memory", "catalog"}:  # fmt: skip
                named_anywhere.add(tool)
                assert tool in served, f"{name} names {tool}, which a hosted session does not have"
    assert {"reddit.search", "opportunities.mark", "draft.create", "room.show",
            "draft.mark_posted"} <= named_anywhere  # fmt: skip


def test_the_skills_promise_nothing_the_hosted_product_cannot_do() -> None:
    for name in SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        lowered = text.lower()
        assert lowered.count("docker") == lowered.count("no docker"), (
            f"{name}: Docker only as 'no Docker'"
        )
        for banned in ("dashboard", "pokój", "approve in", "one click", "x.search", "x.read",
                       "reddit.read", "drafts.publish", "person.voice",
                       "published", "approve with"):  # fmt: skip
            assert banned not in lowered, f"{name}: {banned}"
        assert not re.search(r"\b(accounts|browser|communities|sources)\.[a-z_]+", lowered), name
        assert not LONG_DASHES.search(text), name
        assert "!" not in text.replace("!=", ""), f"{name}: no coaching exclamations"
    first = (PLUGIN / "skills" / "first-users" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(first.split())
    assert "no Docker" in flat and "cannot post for them" in flat
    assert "Copy and open thread" in first
    posted = (PLUGIN / "skills" / "posted-it" / "SKILL.md").read_text(encoding="utf-8")
    assert "did not check it on the platform" in " ".join(posted.split())
    assert "draft.mark_posted" in posted


def test_every_skill_has_a_frontmatter_a_host_can_load() -> None:
    for name in SKILLS:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(
            r"^---\nname: (?P<name>[a-z-]+)\ndescription: (?P<desc>[^\n]+)\n---\n", text
        )
        assert match is not None, name
        assert match["name"] == name
        assert match["desc"].startswith("Use when") and 60 < len(match["desc"]) < 700, name


# ---------------------------------------------------------------------------
# The manifests and the marketplaces
# ---------------------------------------------------------------------------


def test_the_two_manifests_name_the_plugin_and_find_its_skills() -> None:
    codex = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert codex["name"] == claude["name"] == "wenext-cloud"
    assert codex["version"] == claude["version"] and codex["description"] == claude["description"]
    assert codex["skills"] == "./skills/" and (PLUGIN / "skills").is_dir()
    assert "mcpServers" not in codex, (
        "the hosts find .mcp.json at the plugin root, like the shipped ones"
    )
    interface = codex["interface"]
    assert interface["displayName"] == "Wenext" and interface["category"] == "Productivity"
    assert interface["capabilities"] == ["Read", "Write"]
    assert all(isinstance(p, str) and p for p in interface["defaultPrompt"])
    assert "X is not available yet" in interface["longDescription"]
    assert "never posts" in interface["longDescription"]
    assert not LONG_DASHES.search(json.dumps(codex)) and not LONG_DASHES.search(json.dumps(claude))


def test_the_marketplaces_list_the_hosted_plugin_beside_the_local_one() -> None:
    codex = _json(REPO / ".agents" / "plugins" / "marketplace.json")
    entries = {e["name"]: e for e in codex["plugins"]}
    assert {"wenext", "wenext-cloud"} <= set(entries)
    assert entries["wenext"]["source"] == {"source": "local", "path": "./plugins/wenext"}, (
        "the founder's local plugin keeps its entry and its path"
    )
    cloud = entries["wenext-cloud"]
    assert cloud["source"] == {"source": "local", "path": "./plugins/wenext-cloud"}
    assert cloud["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    assert (REPO / cloud["source"]["path"] / ".codex-plugin" / "plugin.json").is_file()

    claude = _json(REPO / ".claude-plugin" / "marketplace.json")
    assert claude["name"] == codex["name"] == "wenext"
    entries = {e["name"]: e for e in claude["plugins"]}
    assert {"wenext", "wenext-cloud"} <= set(entries)
    assert entries["wenext"]["source"] == "./plugins/wenext"
    assert entries["wenext-cloud"]["source"] == "./plugins/wenext-cloud"
    assert (REPO / entries["wenext-cloud"]["source"] / ".claude-plugin" / "plugin.json").is_file()
    assert len({e["name"] for e in claude["plugins"]}) == len(claude["plugins"])


def test_the_local_plugin_is_still_the_stdio_one() -> None:
    local = _json(LOCAL / ".mcp.json")["mcpServers"]["wenext"]
    assert local["command"].endswith("/bin/wenext") and "type" not in local
    assert _json(LOCAL / ".claude-plugin" / "plugin.json")["name"] == "wenext"
    assert (LOCAL / "skills" / "first-users" / "SKILL.md").is_file()
    assert not (LOCAL / "build.py").exists(), (
        "nothing of the hosted build lives in the local plugin"
    )


def test_the_two_plugins_share_no_file() -> None:
    mine = {p.relative_to(PLUGIN) for p in _all_text_files()}
    theirs = {p.relative_to(LOCAL) for p in LOCAL.rglob("*") if p.is_file()}
    shared_by_design = {
        Path(".codex-plugin/plugin.json"), Path(".claude-plugin/plugin.json"), Path(".mcp.json"),
        Path("README.md"),
    }  # fmt: skip
    assert mine & theirs <= shared_by_design | {
        Path(f"skills/{name}/SKILL.md") for name in set(SKILLS) & {"first-users", "reply-in-thread"}
    }
    for name in ("first-users", "reply-in-thread"):
        assert (PLUGIN / "skills" / name / "SKILL.md").read_text() != (
            LOCAL / "skills" / name / "SKILL.md"
        ).read_text()


def test_no_file_of_the_hosted_plugin_has_a_long_dash_or_a_secret() -> None:
    for path in _all_text_files():
        text = path.read_text(encoding="utf-8")
        assert not LONG_DASHES.search(text), path
        assert not re.search(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]{8,}", text), path
