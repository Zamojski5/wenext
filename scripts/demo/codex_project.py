#!/usr/bin/env python3
"""Point ONE Codex project (the tallymark folder) at the Wenext demo sandbox, in English.

    python scripts/demo/codex_project.py --repo ~/Dev/demo/tallymark          write the files
    python scripts/demo/codex_project.py --repo ~/Dev/demo/tallymark --print  show them, write nothing

WHY A PROJECT FILE AND NOT AN ENVIRONMENT VARIABLE. The plugin's MCP server is started by Codex
once per chat, in the plugin's own directory, and its environment is the one Codex was started with
(measured with Codex 0.162, 11.10: `cwd: "."` of the plugin manifest wins over the project folder,
and the desktop app's environment is a snapshot of the login shell, not of the folder). So nothing
the folder holds reaches the plugin's server. What DOES work is Codex's own project layer:
`<project>/.codex/config.toml` is read for a TRUSTED project only, may define `mcp_servers`, and a
server defined there REPLACES a plugin's server of the same name in that project and nowhere else.
Every other folder keeps the installed plugin as it was, and the founder's global
`~/.wenext/config.json` (Polish card, the real project) is not touched. `WENEXT_*` in the server's
`env` come before that file in the launcher (`plugins/wenext/bin/wenext`), so `WENEXT_LANG=en` wins
over a global `lang: pl`.

WHAT THE FILE SWITCHES OFF. The tools that read Reddit, X and the web from the founder's capsules
(`disabled_tools`) and Codex's own web search, because "nothing reaches a real platform" has to
hold for the agent's reads too. The sandbox has the conversations it needs. The tool server itself
is unchanged, so this is a guardrail for a cooperative agent, like the rest of the plugin profile.

AGENTS.md (also written, also kept out of git) tells the agent to show what the sandbox already
holds instead of searching, so the film does not wait on a model deciding how to look.

Needs, once, by hand: the folder opened as a Codex project and TRUSTED. An untrusted project's
`.codex/config.toml` is ignored (measured), and Codex then runs the global plugin against the real
project.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "plugins" / "wenext" / "bin" / "wenext"

#: Spelled again here (this script is stdlib only, no control_plane); `seed_tallymark.py` and a test
#: hold the same values.
DEMO_ORGANIZATION = "dddddddd-0000-4000-8000-000000000001"
DEMO_PROJECT = "tallymark"

#: Tools of the plugin profile that read a platform or the web, or open a login. A test checks each
#: name against `catalog.PLUGIN_TOOLS`, so a rename cannot leave one switched on.
DISABLED_TOOLS = (
    "reddit.search",
    "reddit.read",
    "x.search",
    "x.read",
    "x.watch",
    "x.trends",
    "browser.inspect",
    "news.search",
    "sources.find",
    "sources.search",
    "sources.read",
    "accounts.connect",
)

AGENTS_MD = """\
# Demo sandbox for Wenext

This folder is a sandbox for a Wenext demo. The Wenext project "tallymark" already holds a few
fictional conversations, each with a drafted reply. Searching Reddit, X and the web is switched
off on purpose.

When the user asks you to find their first users, or anything close to it:

1. Read README.md and `git log --oneline -n 8`, nothing else in the repo.
2. Say in one line who tallymark is for and what pain it removes.
3. Call `opportunities.list` once, then `room.show`, and hand back what the card shows: how many
   people, the best one in one line, what to click.

Do not search the web, Reddit or X, do not open a browser, and do not write new drafts yourself.
Never say anything was posted: the card says so when it was.
"""

EXCLUDES = (".codex/", "AGENTS.md")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_config(*, launcher: Path = LAUNCHER, home: Path = REPO_ROOT) -> str:
    """The project's `.codex/config.toml`. Plain TOML written by hand: no dependency, and a person
    can read it."""
    tools = ",\n".join(f"  {_toml_string(name)}" for name in DISABLED_TOOLS)
    env = {
        "WENEXT_HOME": str(home),
        "WENEXT_PROJECT": DEMO_PROJECT,
        "WENEXT_ORGANIZATION": DEMO_ORGANIZATION,
        "WENEXT_LANG": "en",
        "WENEXT_HOST": "codex",
    }
    env_lines = "\n".join(f"{key} = {_toml_string(value)}" for key, value in env.items())
    return (
        "# Written by scripts/demo/codex_project.py for the Wenext demo. Machine specific, kept out\n"
        "# of git. Codex reads it only for a TRUSTED project. It replaces the plugin's `wenext`\n"
        "# server in this folder: the sandbox project, the card in English, no live search.\n"
        'web_search = "disabled"\n'
        "\n"
        "[mcp_servers.wenext]\n"
        f"command = {_toml_string(str(launcher))}\n"
        'args = ["mcp"]\n'
        "startup_timeout_sec = 60\n"
        "tool_timeout_sec = 120\n"
        f"disabled_tools = [\n{tools},\n]\n"
        "\n"
        "[mcp_servers.wenext.env]\n"
        f"{env_lines}\n"
    )


def _exclude(repo: Path) -> list[str]:
    """Keep both files out of `git status` without touching the repo's history or .gitignore."""
    info = repo / ".git" / "info"
    if not info.is_dir():
        return []
    path = info / "exclude"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    have = {line.strip() for line in current.splitlines()}
    added = [entry for entry in EXCLUDES if entry not in have]
    if added:
        path.write_text(current + ("" if current.endswith("\n") or not current else "\n") + "\n".join(added) + "\n", encoding="utf-8")
    return added


def write(repo: Path) -> list[str]:
    if not repo.is_dir():
        raise SystemExit(f"codex_project: {repo} is not a folder")
    if not LAUNCHER.is_file():
        raise SystemExit(f"codex_project: no launcher at {LAUNCHER}")
    done: list[str] = []
    (repo / ".codex").mkdir(exist_ok=True)
    config = repo / ".codex" / "config.toml"
    config.write_text(render_config(), encoding="utf-8")
    done.append(f"wrote {config}")
    agents = repo / "AGENTS.md"
    agents.write_text(AGENTS_MD, encoding="utf-8")
    done.append(f"wrote {agents}")
    added = _exclude(repo)
    if added:
        done.append("added to .git/info/exclude: " + ", ".join(added))
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, type=Path, help="the tallymark folder")
    parser.add_argument("--print", action="store_true", dest="show", help="print, write nothing")
    arguments = parser.parse_args(argv)
    if arguments.show:
        print(render_config())
        print("--- AGENTS.md ---")
        print(AGENTS_MD)
        return 0
    for line in write(arguments.repo.expanduser().resolve()):
        print(line)
    print("Next, once: open the folder in Codex as a project and choose Trust.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
