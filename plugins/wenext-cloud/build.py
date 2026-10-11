#!/usr/bin/env python3
"""Renders the generated files of the HOSTED plugin (`plugins/wenext-cloud`).

WHAT IS GENERATED, AND WHY. Two things in this plugin must never be edited by hand:

  * `.mcp.json`: the address of the hosted server. THE DOMAIN IS ONE CONSTANT (`DOMAIN` below), and
    this script is the only place that turns it into a URL. A deployment that moves changes one
    line here and runs the script; `tests/test_hosted_plugin_unit.py` fails when the file on disk
    is not what the constant says.
  * `skills/*/SKILL.md`: rendered from `HOSTED_WORKFLOW`, `HOSTED_REPLY` and `HOSTED_RULES` in
    `control_plane/tools/catalog.py`, the same text the hosted server sends as its MCP
    `instructions`. One source of truth: a skill cannot say what the instructions do not, and the
    same test fails when a skill and the instructions drift apart.

Run it with the control plane's interpreter (it imports the catalog):

    services/control_plane/.venv/bin/python plugins/wenext-cloud/build.py          # write
    services/control_plane/.venv/bin/python plugins/wenext-cloud/build.py --check  # exit 1 if stale

The manifests (`.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`) and the README are plain
files, edited by hand. The local plugin (`plugins/wenext`, stdio, real publishing) is not touched by
anything here.
"""

# The skills are prose in a Python string: their lines are the skill's, not Python's.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "services" / "control_plane"))

from control_plane.tools.catalog import HOSTED_REPLY, HOSTED_RULES, HOSTED_WORKFLOW  # noqa: E402

#: THE ONE CONSTANT, and next to it the path: together they are the address of the hosted MCP
#: endpoint. The domain is the founder's (`getwenext.com`); the path is provisional until the server
#: side confirms it (`/mcp`). Sign-in is OAuth done by the host when the first tool is called, so
#: the file carries the URL and nothing else: no `oauth` block, no key.
DOMAIN = "getwenext.com"
MCP_PATH = "/mcp"
SERVER_NAME = "wenext"


def mcp_url() -> str:
    return f"https://{DOMAIN}{MCP_PATH}"


def render_mcp() -> str:
    body = {"mcpServers": {SERVER_NAME: {"type": "http", "url": mcp_url()}}}
    return json.dumps(body, indent=2) + "\n"


FIRST_USERS = """---
name: first-users
description: Use when the founder says "find my first users", "who needs my product", "how do I get users", "where are my customers", "distribution", "launch", or has just built something and asks what to do next. Reads the repo, searches Reddit for people describing the problem it solves right now, saves the few worth answering and drafts replies in the founder's voice for the founder to post by hand. Wenext never posts anything.
---

# Find the founder's first users

Wenext runs in the cloud: there is nothing to install and no Docker, and the founder signed in once in
the browser. It cannot post for them. It finds the people and writes the replies; the founder posts.

## Say this first

Before any tool call, one or two sentences in the founder's language, for example: "In a few minutes I
will read your code, find people on Reddit who are describing the problem it solves right now, and
prepare replies in your voice. You copy and post each one yourself, from your own account." Then work,
with one short status line between steps.

## Procedure

{workflow}

## Always

{rules}

## Hand back

Two sentences, in the founder's language, plain words, no ids and no tool names. First: how many people
you found, and the best one in a line (who, where, what they asked, how old). Second: what to do next,
which is to press "Copy and open thread" on the card, paste the reply in the thread and post it, then
tell you. Do not paste the drafts again: the card carries them.

## When something is missing

- `reddit.search` answers NIEDOSTEPNE (the search is off, or this project's daily allowance is used up):
  say which in one sentence, from the result's `powod`, and work with `opportunities.list`. Do not
  retry in a loop.
- No hits: try a different phrasing before saying nobody writes about it. "No one worth answering right
  now" is an honest result; say what you searched and what you would try next.
- A tool is not in your list: say so in one sentence and use the nearest one. Never fake a result.
"""

REPLY_IN_THREAD = """---
name: reply-in-thread
description: Use when the founder points at one specific Reddit thread (a link, or the pasted text) and wants a reply they can post. Saves the thread as a person worth answering and writes one reply that stands on its own, for the founder to post by hand.
---

# Write one reply that helps

Goal: one reply that would be worth reading even if the product did not exist, saved as a draft for the
founder to post. Short, dry, human. Not replying at all is a valid outcome.

## Steps

1. Wenext cannot open a Reddit thread. If the founder pasted the post and its comments, use them. If
   they gave only a link, run `reddit.search` with the words of its title to get the title and snippet,
   or ask them to paste the thread. Never invent what the thread says.
2. Save the thread with `opportunities.mark` (platform REDDIT, url, community, title, excerpt, author
   when you have them, `dlaczego`, `kat`). Omit created_at unless a search result gave one.
3. Call draft.create with target_url set to the thread's url and {reply} If the founder pasted the
   thread, what they pasted counts as seen.
4. Call `room.show` once, then tell the founder in one or two sentences to press "Copy and open thread"
   on the card.

## Always

{rules}
"""

POSTED_IT = """---
name: posted-it
description: Use when the founder says they posted a reply ("I posted it", "done", "it is up", "wrzuciłem") or pastes the link to their own post. Records it so the card shows the reply as posted. It does not check the platform.
---

# Record that the founder posted a reply

Wenext does not post, so it only knows what the founder tells it. When they say a reply is up:

1. Find the draft's revision_id: from this conversation, or from `room.show` (`szkice`), matching the
   thread or the text. If two drafts fit, ask which one, in one short question.
2. Call draft.mark_posted with the revision_id, and with `url` set to the link of their post if they
   gave one (an https link to Reddit or X). Without a link is fine.
3. Answer in one sentence: it is recorded as posted by the founder, and Wenext did not check it on the
   platform. Do not say the post is visible, and do not promise or invent results.

If they say they have not posted it, change nothing.

## Always

{rules}
"""


def render_skills() -> dict[str, str]:
    values = {"workflow": HOSTED_WORKFLOW, "rules": HOSTED_RULES, "reply": HOSTED_REPLY}
    return {
        "skills/first-users/SKILL.md": FIRST_USERS.format(**values),
        "skills/reply-in-thread/SKILL.md": REPLY_IN_THREAD.format(**values),
        "skills/posted-it/SKILL.md": POSTED_IT.format(**values),
    }


def render_all() -> dict[str, str]:
    """Every generated file, as `relative path -> text`."""
    return {".mcp.json": render_mcp(), **render_skills()}


def stale() -> list[str]:
    return [
        name
        for name, text in render_all().items()
        if not (HERE / name).is_file() or (HERE / name).read_text(encoding="utf-8") != text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when a generated file is stale"
    )
    args = parser.parse_args(argv)
    if args.check:
        behind = stale()
        for name in behind:
            print(f"stale: {name}", file=sys.stderr)
        return 1 if behind else 0
    for name, text in render_all().items():
        path = HERE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
