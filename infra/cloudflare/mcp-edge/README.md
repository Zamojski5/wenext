# Wenext MCP edge (Cloudflare Worker)

One Worker, two jobs, both for hosted Wenext in Codex and Claude Code:

1. **OAuth 2.1 authorization server** towards the agent. Built on
   [`@cloudflare/workers-oauth-provider`](https://github.com/cloudflare/workers-oauth-provider)
   (v1.2.x): discovery (RFC 9728 and RFC 8414), dynamic client registration, PKCE, tokens in KV.
   The person signs in with **GitHub** once, in a browser.
2. **Signed proxy** for `POST /mcp`. After the provider has verified the agent's token, the Worker
   forwards the JSON-RPC body to the origin (the Python `mcp-edge` service on the VPS) with the
   person's identity signed into `X-Wenext-Edge`, a 60 second HS256 JWT. The origin never sees an
   OAuth token and verifies none; it trusts only that header
   (`services/control_plane/control_plane/hosted/edge.py`).

```
agent --OAuth--> mcp.getwenext.com (Worker) --signed header--> origin.getwenext.com (Caddy -> mcp-edge)
```

What the person types (one command, then GitHub in the browser):

```sh
codex mcp add wenext --url https://mcp.getwenext.com/mcp                  # Codex starts the sign-in itself
claude mcp add --transport http wenext https://mcp.getwenext.com/mcp      # then /mcp inside Claude Code
```

Two hostnames on purpose: the Worker's (`mcp.`) and the origin's (`origin.`). A Worker must not
call its own hostname. If the domain is not `getwenext.com`, change it in `wrangler.toml` and in
the steps below.

## Deploy (the founder runs these)

Prerequisites: a Cloudflare account with the zone `getwenext.com` on it, Node 20 or newer, a GitHub
account, and the VPS with the Wenext stack (`docs/WDROZENIE-SERWER.md`).

1. **Log in to Cloudflare.**

   ```sh
   cd infra/cloudflare/mcp-edge
   npm install
   npx wrangler login
   ```

2. **Create the KV namespace** (OAuth clients, grants, tokens; tokens are stored hashed and the
   props encrypted). Paste the printed `id` into `wrangler.toml`, replacing `<KV-NAMESPACE-ID>`.

   ```sh
   npx wrangler kv namespace create OAUTH_KV
   ```

3. **Create a GitHub OAuth App** (GitHub, Settings, Developer settings, OAuth Apps, New OAuth App):
   - Homepage URL: `https://mcp.getwenext.com`
   - Authorization callback URL: **`https://mcp.getwenext.com/callback`** (exactly this)

   Copy the Client ID and generate a Client secret.

4. **Set the three secrets** (each command asks for the value). `WENEXT_EDGE_SECRET` is a new random
   string, and the SAME value goes to the VPS in step 6.

   ```sh
   openssl rand -hex 32                      # copy this output
   npx wrangler secret put GITHUB_CLIENT_ID
   npx wrangler secret put GITHUB_CLIENT_SECRET
   npx wrangler secret put WENEXT_EDGE_SECRET
   ```

5. **Check `wrangler.toml` `[vars]`:** `PUBLIC_BASE_URL = "https://mcp.getwenext.com"` and
   `ORIGIN_MCP_URL = "https://origin.getwenext.com/mcp"`. For a closed beta set
   `ALLOWED_GITHUB_LOGINS = "login1,login2"`; empty means anybody with a GitHub account.

6. **Bring up the origin on the VPS.** DNS: an `A` record `origin.getwenext.com` pointing at the VPS
   (grey cloud, DNS only, so Caddy can get its certificate; orange cloud also works later with
   SSL/TLS mode "Full (strict)"). In the VPS `.env`:

   ```sh
   WENEXT_PUBLIC_DOMAIN=origin.getwenext.com
   WENEXT_TLS_EMAIL=you@getwenext.com
   WENEXT_EDGE_SECRET=<the same value as step 4>
   SERPER_API_KEY=<Serper key>
   POSTGRES_PASSWORD=<as for the rest of the stack>
   ```

   Then, on the VPS, in the repo (the migration `0037` must be applied, `tools/dev/apply_migrations.sh`):

   ```sh
   docker compose -f docker-compose.yml -f compose.prod.yml --profile hosted \
     up -d --no-deps db mcp-edge caddy
   ```

   `--no-deps` keeps the dashboard, gateway and capsule off this machine (Caddy lists them as
   dependencies; the hosted deployment does not use them, so `/` answers 502 and that is fine).

7. **Deploy the Worker.** `custom_domain = true` in `wrangler.toml` makes Cloudflare create the DNS
   record and the certificate for `mcp.getwenext.com`.

   ```sh
   npx wrangler deploy
   ```

8. **Check it works, in this order:**

   ```sh
   # The Worker challenges an unauthenticated agent and says where to sign in:
   curl -si -X POST https://mcp.getwenext.com/mcp | head -5        # 401, WWW-Authenticate: ... resource_metadata=
   curl -s https://mcp.getwenext.com/.well-known/oauth-protected-resource/mcp
   curl -s https://mcp.getwenext.com/.well-known/oauth-authorization-server
   # The origin refuses what the edge did not sign (and is reachable):
   curl -si -X POST https://origin.getwenext.com/mcp | head -1      # 401
   # Then the real thing, from a machine with Codex:
   codex mcp add wenext --url https://mcp.getwenext.com/mcp
   ```

   The first successful sign-in creates the person's organization, workspace and project on the
   origin (`python -m control_plane.hosted.invite list` shows them).

## Local trial (no Cloudflare account needed)

```sh
cp .dev.vars.example .dev.vars        # fill in; for a stub GitHub see the vars at the bottom of env.ts
npx wrangler dev --port 8787 --local --local-upstream localhost:8787
```

`--local-upstream` is needed because the custom-domain route would otherwise make the Worker see
itself at `mcp.getwenext.com`. `PUBLIC_BASE_URL=http://localhost:8787` (plain http is accepted on
loopback only). `GITHUB_AUTHORIZE_URL`, `GITHUB_TOKEN_URL` and `GITHUB_API_URL` can point at a stub
GitHub; leave them out for the real one. Point Codex at it with
`mcp_oauth_credentials_store = "file"` in a scratch `CODEX_HOME`, otherwise it writes the OAuth
credentials to the macOS Keychain.

## Operations

- **Logs:** `npx wrangler tail`. Nothing the Worker logs contains a token or a secret.
- **Switch a person off:** on the VPS,
  `docker compose exec -T mcp-edge python -m control_plane.hosted.invite revoke --subject <github-id>`
  (the id is in `invite list`). Their tokens still verify at the Worker, but the origin answers 403 on
  the next request.
- **Rotate `WENEXT_EDGE_SECRET`:** `wrangler secret put` and the VPS `.env` at the same time;
  requests in between get 401 for a minute.
- **Tests:** `npm test` (signing vector shared with the Python tests, the proxy, the GitHub
  identity, the consent page) and `npm run type-check`.

## What is verified and what is not

Verified locally against the real libraries and the real Codex CLI (`wrangler dev`, a stub GitHub,
the Python origin): discovery, dynamic registration, PKCE, consent, token exchange, the signed header
accepted by the origin, first-request provisioning, tool listing and a tool call through the Worker.

Not verified until the accounts exist: the real GitHub OAuth App (callback URL, PKCE acceptance, the
`/user/emails` fallback), the Cloudflare custom domain and certificate, Claude Code's own
sign-in (`/mcp` in a Claude Code session; locally it was only seen to recognise the 401 challenge),
and the origin behind a real Caddy certificate. Open question for the first live run: whether
orange-cloud proxying of `origin.` is wanted (then the origin should accept `/mcp` only from
Cloudflare addresses).
