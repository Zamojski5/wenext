import type { OAuthHelpers } from '@cloudflare/workers-oauth-provider';

/** Bindings, variables and secrets of the Worker. See wrangler.toml and README.md. */
export interface Env {
  /** KV namespace the OAuth provider keeps its state in. */
  OAUTH_KV: KVNamespace;
  /** Injected by the provider into the default handler (the /authorize pages). */
  OAUTH_PROVIDER: OAuthHelpers;

  /** Issuer and base of the MCP resource, e.g. https://mcp.getwenext.com (no trailing slash). */
  PUBLIC_BASE_URL: string;
  /** The origin's MCP endpoint, e.g. https://origin.getwenext.com/mcp. */
  ORIGIN_MCP_URL: string;
  /** Comma-separated GitHub logins allowed in; empty or absent means open. */
  ALLOWED_GITHUB_LOGINS?: string;

  /** Secrets (wrangler secret put). */
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  /** Shared with the origin (WENEXT_EDGE_SECRET there). 32+ characters. */
  WENEXT_EDGE_SECRET: string;

  /** Overrides for a local trial against a stub GitHub; the real GitHub when absent. */
  GITHUB_AUTHORIZE_URL?: string;
  GITHUB_TOKEN_URL?: string;
  GITHUB_API_URL?: string;
}

/**
 * What the provider stores with a grant (encrypted) and hands to the MCP handler as `ctx.props`
 * on every request: who signed in. `subject` is GitHub's numeric user id, which never changes,
 * unlike the login.
 */
export interface Props {
  provider: 'github';
  subject: string;
  login: string;
  email: string | null;
}
