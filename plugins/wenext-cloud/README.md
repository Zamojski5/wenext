# Wenext (cloud) plugin for Codex and Claude Code

The hosted Wenext: nothing to install, no Docker, one sign-in in the browser. It finds people on Reddit
who are describing the problem your code solves, saves the ones worth answering and drafts replies in
your voice. **Wenext never posts.** You press "Copy and open thread" on the card, paste the reply in
the thread and post it from your own account, then tell the agent you did.

This is a separate plugin from `plugins/wenext` (the local stack: Docker, your own accounts, real
publishing). The two do not share files, and the local one is not touched by anything here.

## Install (once the repository is public)

Codex:

```bash
codex plugin marketplace add Zamojski5/wenext
codex plugin add wenext-cloud@wenext
```

Claude Code:

```bash
claude plugin marketplace add Zamojski5/wenext
claude plugin install wenext-cloud@wenext
```

The first time the agent calls a Wenext tool the host opens the browser for the sign-in (GitHub). After that,
in the folder of what you built, say: "find my first users".

## What it does

| Skill | What it does |
| --- | --- |
| `first-users` | "Find my first users": reads the repo, searches Reddit for the last two days, saves 3 to 6 people, drafts a reply for each and shows the card |
| `reply-in-thread` | One reply for one Reddit thread you point at |
| `posted-it` | You say "I posted it": Wenext records it, unverified, and the card shows the reply as posted |

Not in the cloud version yet: X (search and posting), the weekly results, update posts, the
community map. They need a connected account or a browser Wenext does not have for you.

## One source of truth

The skills are rendered from the same text the server sends as its MCP `instructions`
(`HOSTED_WORKFLOW`, `HOSTED_REPLY` and `HOSTED_RULES` in
`services/control_plane/control_plane/tools/catalog.py`), and `.mcp.json` from one constant,
`DOMAIN`, in `build.py`. Do not edit those files by hand:

```bash
services/control_plane/.venv/bin/python plugins/wenext-cloud/build.py          # write
services/control_plane/.venv/bin/python plugins/wenext-cloud/build.py --check  # exit 1 if stale
```

The address of the server is `https://getwenext.com/mcp`: `DOMAIN` and `MCP_PATH` in `build.py`,
the only two places that know it. The path is provisional until the server side confirms it; change
it there, run the script, commit the result. `.mcp.json` carries the URL and nothing else: the
sign-in (GitHub, through the edge) is done by the host the first time a tool is called.
