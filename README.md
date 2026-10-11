# Wenext

Wenext finds the first users for what you built. From inside Codex or Claude Code it reads your
repository (read only), searches Reddit and X for people describing the problem your product
solves right now, and drafts replies in your voice. You approve each reply with a click, or sign a
time-limited delegation with limits you can revoke; nothing is published otherwise, and the agent
itself cannot publish. A few days later it reports what happened (replies, upvotes, removals, and
GitHub stars if the repo is public) and keeps a memory of what worked.

![The Wenext card: people writing about the problem the code solves, each with a drafted reply waiting for approval. A demo project with invented people.](docs/images/readme-v3.gif)

## Status

Early stage. It was built by one person and used in real runs by its author, so expect rough
edges. Issues and pull requests are welcome.

| | |
| --- | --- |
| Works today | Local install with Docker, on your own machine (see below). Supports Reddit and X. |
| Coming soon | A hosted, one-command install: no clone and no Docker. |
| Waitlist | [getwenext.com](https://getwenext.com) |

## How it works

- **Plugin.** One MCP server (`wenext`) and seven skills for Codex and Claude Code:
  `first-users`, `start-project`, `find-conversations`, `reply-in-thread`, `ship-update-post`,
  `community-map` and `weekly-results`. Details in [plugins/wenext/README.md](plugins/wenext/README.md).
- **Local stack.** PostgreSQL, a Python API (`services/control_plane`), a Next.js dashboard
  (`apps/dashboard`) and an optional hardened browser capsule used to read Reddit and X.
- **Approvals.** Drafts wait on the "Pokój" page of your project (the dashboard, by default
  `http://127.0.0.1:3000`). Posts go out from your own accounts, with disclosure, and every
  publication is read back to confirm it is actually visible.

## Install locally

Prerequisites: Docker (running natively, not under emulation), `make`, and one of Codex or
Claude Code. `uv` is only needed to run the Python test suites on the host. Optionally, a
ChatGPT/Codex account, which the stack can use as its own background engine.

The repository address below is provisional.

```bash
git clone https://github.com/Zamojski5/wenext.git
cd wenext

make doctor          # is this host fit to run the stack
make env             # creates .env and the Docker config
make up-observe      # db, api and dashboard (no browser capsule)
make migrate         # applies database migrations
WENEXT_OPERATOR_PASSWORD='choose-a-password' make bootstrap-operator   # one-time operator account
```

The dashboard is then at <http://127.0.0.1:3000/>. `make ps` shows service state and `make down`
stops everything. To read Reddit through the browser capsule use `make up-observe-reddit` instead
of `make up-observe`; it needs the reader and gateway tokens described in `.env.example`.

Then add the plugin to your agent, pointing at your checkout:

```bash
# Claude Code
claude plugin marketplace add /path/to/wenext
claude plugin install wenext@wenext

# Codex
codex plugin marketplace add /path/to/wenext
```

Codex then installs the plugin from its plugin list; the steps for the CLI and the ChatGPT desktop
app are in [plugins/wenext/README.md](plugins/wenext/README.md). Run the launcher once so the plugin
knows where your checkout is, then restart your agent:

```bash
plugins/wenext/bin/wenext setup /path/to/wenext
```

In your agent, in the folder of the product you built, say "find my first users" (in Claude
Code: `/wenext:first-users`). If the tools do not appear, run `plugins/wenext/bin/wenext doctor`.

## Repository layout

| Path | What it holds |
| --- | --- |
| `plugins/wenext/` | The Codex and Claude Code plugin: MCP launcher and skills |
| `apps/dashboard/` | Next.js dashboard (projects, the Pokój page, approvals, accounts) |
| `services/control_plane/` | API, tools, jobs, policy and the agent session engine |
| `services/browser_supervisor/`, `services/stream_gateway/` | Browser capsule control and its viewing gateway |
| `services/community_observer/` | Read-only observers for Reddit, Hacker News and X |
| `services/codex_runtime/`, `services/mcp_sidecar/` | Codex app-server adapter and a read-only MCP sidecar |
| `contracts/`, `db/migrations/` | Shared types and invariants, database schema |
| `evals/`, `fixtures/site/` | Conversation evals and a deterministic fixture site |
| `scripts/demo/` | Seed for a fictional demo organization, used to record the demo above |
| `infra/`, `tools/` | Pinned browser build, capsule image, operations and test scripts |

Tests: `make test-all` runs every suite that can run on the host; `make test-db` runs the suites
that need PostgreSQL. Pinned tool versions live in `versions.lock.toml`.

Parts of the documentation, the Makefile help text and some of the interface copy are in Polish.

## Responsible use

Wenext posts from your own accounts. Follow the rules of each community and each platform,
disclose that you built the product you mention, and read what you approve. You are responsible
for what is published under your accounts and for complying with the terms of the platforms you
connect.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE). Third-party material and the note on the name and mark are in
[NOTICE](NOTICE).
