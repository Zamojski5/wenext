# Wenext plugin for Codex and Claude Code

The agent that built your product now finds its first users.

## What you get in 5 minutes

Say "find my first users" in Codex or Claude Code, in the folder of what you built. In about
five minutes the agent:

1. reads your repo (read only) and tells you in one line who needs this and what pain it removes;
2. searches Reddit and X live for people describing that pain right now, and throws away promo,
   old and closed threads;
3. saves the few conversations worth joining to the **Pokój** page of your project, each with why
   it matters and the angle to take;
4. drafts replies to the best three in your voice: helpful first, your product only where it
   truly answers, with disclosure;
5. ends with a link to the Pokój page and three lines: how many people it found, the best one,
   what to click.

You approve with one click on the Pokój page. Nothing is published without you, and the agent
cannot publish. A few days later, ask "how did it go?" and it reports replies, upvotes,
removals, opportunities answered and, if the repo is on GitHub, the star count and how it moved.
An honest "nobody worth answering today" is a valid result, with what to try next.

The Pokój link is `http://127.0.0.1:3000` plus the path the agent gives you (the port can differ,
see `WENEXT_DASHBOARD_HOST_PORT` below). The first run saves several drafts and notes; if your agent
asks to approve every write, allow the Wenext tools for the session. None of them publishes.

Wenext also drafts update posts in your voice, publishes from your own accounts only after you
approve, and remembers what worked. This plugin gives Codex and Claude Code access to it:
one MCP server (`wenext`) and seven skills.

| Skill | What it does |
| --- | --- |
| `first-users` | The flagship: "find my first users". Reads your code, searches Reddit and X live, saves the best conversations to the Pokój page and drafts replies for the top three |
| `start-project` | Onboarding in about a minute: reads your repo, proposes audience, positioning and voice, then offers `first-users` |
| `find-conversations` | Up to five live threads where the product helps, ranked, saved to the Pokój page with a suggested angle |
| `reply-in-thread` | Drafts a reply to a specific thread and attaches it to its opportunity on the Pokój page |
| `ship-update-post` | Picks the one change worth a post and saves one draft |
| `community-map` | Maps the communities worth showing up in |
| `weekly-results` | The week in numbers: replies, upvotes, removed, opportunities answered, GitHub stars; what worked |

## Prerequisites

- Docker, and a checkout of this repository (the plugin lives in `plugins/wenext`).
- The Wenext stack running locally. From the repo root (see the `Makefile`):

```bash
make doctor              # is this host fit to run the stack
make env                 # creates .env and the Docker config
make up-observe          # db, api and dashboard (no browser capsule)
make migrate             # applies database migrations
WENEXT_OPERATOR_PASSWORD='...' make bootstrap-operator   # one-time operator account
```

Dashboard: http://127.0.0.1:3000/. To read Reddit through the browser capsule use
`make up-observe-reddit` instead of `make up-observe`; it needs
`WENEXT_BROWSER_READER_TOKEN`, `WENEXT_BROWSER_READER_URL` and
`WENEXT_GATEWAY_ISSUER_TOKEN` in `.env`. `make ps` shows service state, `make down` stops it.

## Install

Both marketplaces live at the repo root and point at `./plugins/wenext`.
Replace `/path/to/wenext` with your checkout.

### Codex CLI

```bash
codex plugin marketplace add /path/to/wenext
```

This registers the catalog. OpenAI documents the ChatGPT desktop app (next section) as
the place to install and test a local plugin. `/plugins` in the CLI may also list
"wenext" from the "wenext" marketplace; if it does, install it there and start a new
session so the skills and the MCP server load.

### ChatGPT desktop app (Codex)

Open the repository as a project, restart the app, open the Plugins tab, choose the
"wenext" marketplace and install the plugin (plus button). Start a new chat.
After you change plugin files, restart the app so the local install is refreshed.

### Claude Code

```bash
claude plugin marketplace add /path/to/wenext
claude plugin install wenext@wenext
```

Inside a session the same steps are `/plugin marketplace add /path/to/wenext` and
`/plugin install wenext@wenext`. To try it without installing:

```bash
claude --plugin-dir /path/to/wenext/plugins/wenext
```

Skills are namespaced in Claude Code, for example `/wenext:first-users` and `/wenext:start-project`.

## First run

1. Run the launcher once: `plugins/wenext/bin/wenext setup`. It records where your Wenext
   checkout is and which project to use in `~/.wenext/config.json`.
   Alternatively set `WENEXT_HOME` (path to the checkout) and `WENEXT_PROJECT` (project)
   in the environment that starts your agent. The plugin works without either variable
   as long as the config file exists.
2. Restart your agent session, or reload plugins.
3. Say "find my first users", or `/wenext:first-users` in Claude Code. For a short brief first, ask for
   the onboarding skill: "Use the Wenext start-project skill", or `/wenext:start-project`.

## How approvals work

Nothing is published without your decision. Drafts wait on the Pokój page of your project and in
the Wenext dashboard (http://127.0.0.1:3000/) until you approve them, or until a delegation you signed covers
them: a time-limited policy with limits you set and can revoke. Posts go out from your
own accounts, so what your agent prepares is only ever a draft until one of those two
things is true.

## The launcher

Codex and Claude Code both start the same script, `bin/wenext mcp`. It checks that Docker
answers and that the `api` container is healthy, then runs the Wenext tool server inside that
container and speaks MCP on stdin and stdout. Everything it says goes to stderr, in Polish.
The server prints the link to the conversation where you approve drafts; it lands in the
host's MCP log, so the skills also tell you where to click.

| Command | What it does |
| --- | --- |
| `bin/wenext setup [PATH] [--project NAME] [--lang pl\|en]` | Run once. Writes `~/.wenext/config.json` with the absolute path of your checkout and, if given, the project and the card language. |
| `bin/wenext doctor` | Runs the start checks step by step; exits 0 only when all is well. Run it first when the tools do not appear. |
| `bin/wenext projects` | Lists the projects of your local Wenext, one `id<TAB>name` per line. |
| `bin/wenext mcp [--host codex\|claude-code]` | What the host starts. Not meant to be typed. |

Configuration is read from the environment first, then from the config file:

| Environment | Config key | Default |
| --- | --- | --- |
| `WENEXT_HOME` | `home` | none: run `setup` |
| `WENEXT_PROJECT` (id or exact name) | `project` | the only project; with several, the server refuses and lists them |
| `WENEXT_HOST` | none | `claude-code` when `CLAUDE_PLUGIN_ROOT` is set, else `codex` |
| `WENEXT_PLUGIN_TTL_HOURS` | none | 8 (capped at 24); after expiry restart the agent |
| `WENEXT_DASHBOARD_HOST_PORT` | none | the value in `.env`, else 3000 |
| `WENEXT_LANG` (`pl` or `en`) | `lang` | none: the card follows your host's locale, Polish for a Polish locale and English for any other |

One project folder on its own Wenext project (and its own card language), without touching the
rest: Codex reads `<folder>/.codex/config.toml` for a TRUSTED project, and an `mcp_servers.wenext`
defined there replaces the plugin's server in that folder only. Put `WENEXT_PROJECT`,
`WENEXT_ORGANIZATION` and `WENEXT_LANG` in its `env`, and `command` is this launcher. The `env` wins
over `~/.wenext/config.json`, so the folder can be English next to a Polish global config. Measured
with the Codex app server: the plugin's own server cannot be configured per folder (it starts in the
plugin's directory with the app's environment), and an untrusted folder's file is ignored.
`scripts/demo/codex_project.py` writes exactly this file for the recording sandbox.

From the plugin the agent cannot publish (`drafts.publish` is not exposed) and cannot start or
wake Wenext sessions (the five `session.*` tools are not exposed). Preferences and confirmed
hypotheses are recorded only in the dashboard. The plugin conversation appears in the
dashboard and is read only there, except for approving drafts. After updating Wenext,
rebuild the `api` image so the container has `control_plane.tools.plugin_server`.

Timeouts: Codex uses `startup_timeout_sec` 60 and `tool_timeout_sec` 120 from
`.codex-mcp.json`. In Claude Code the startup limit is raised with an environment variable,
for example `MCP_TIMEOUT=60000 claude`. A start on a healthy stack takes about a second.
Exit codes: 64 usage, 69 Docker or the stack, 78 configuration; the server's 2 (capability),
3 (database), 4 (project cannot be chosen) and 5 (configuration) pass through.

Several organizations in one database (for example an end to end test seed next to your own):
the server refuses until you name one. Run `bin/wenext setup --organization <id>` or set
`WENEXT_ORGANIZATION`; `bin/wenext doctor` shows what is in use.

Updating: Codex and Claude Code run a cached copy of the plugin made at install time. After
changing anything under `plugins/wenext/`, reinstall it, for example
`codex plugin remove wenext@wenext && codex plugin add wenext@wenext`.

Approvals in Codex: every tool carries the MCP `readOnlyHint` annotation, so reads run without
asking and only writes (drafts, memory, observation settings) ask for your approval.

## Troubleshooting

**The tools fail or the server will not start.** The launcher needs the local stack.
Check Docker is running, then `make ps` in the repo; start it with `make up-observe`.
In Claude Code, `/mcp` lists the server as `plugin:wenext:wenext`; in Codex use `/mcp`.

**Wrong checkout (`WENEXT_HOME`).** A stale variable pointing at an old checkout is the
usual cause. Run `echo $WENEXT_HOME`, fix or unset it, or rerun
`plugins/wenext/bin/wenext setup`. The path must contain `docker-compose.yml`.

**No project.** If neither `WENEXT_PROJECT` nor the config names a project, run
`plugins/wenext/bin/wenext setup`, then the `start-project` skill.

**Permission denied on `bin/wenext`.** Run `chmod +x plugins/wenext/bin/wenext`.

**Plugin not visible.** Codex and the desktop app need a new session or a restart after
install. In Claude Code run `/reload-plugins`, or start a new session.
