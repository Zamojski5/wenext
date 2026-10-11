import OAuthProvider from '@cloudflare/workers-oauth-provider';
import { authHandler } from './auth';
import type { Env } from './env';
import { mcpHandler } from './proxy';

/**
 * ONE WORKER, TWO ROLES (the library's `OAuthProvider`): it is the OAuth 2.1 authorization server
 * towards Codex and Claude Code (discovery, dynamic client registration, PKCE, tokens, GitHub as
 * the upstream sign-in) and the only resource, `/mcp`, which it proxies to the origin with the
 * person's identity signed into a header (`proxy.ts`). The origin never sees an OAuth token.
 *
 * WHY THE PROVIDER IS BUILT ON THE FIRST REQUEST AND NOT AT MODULE LOAD. The resource metadata
 * needs the public address, which is configuration (`PUBLIC_BASE_URL`), and a Worker reads its
 * variables from `env`, which does not exist until a request arrives. The instance is then reused
 * for as long as the address does not change.
 */

let cached: { base: string; provider: OAuthProvider<Env> } | undefined;

function providerFor(env: Env): OAuthProvider<Env> {
  const base = (env.PUBLIC_BASE_URL ?? '').trim().replace(/\/+$/, '');
  if (!base) throw new Error('PUBLIC_BASE_URL is not set (wrangler.toml [vars])');
  if (cached?.base === base) return cached.provider;
  const provider = new OAuthProvider<Env>({
    apiRoute: '/mcp',
    apiHandler: mcpHandler,
    defaultHandler: authHandler,
    authorizeEndpoint: '/authorize',
    tokenEndpoint: '/oauth/token',
    // Dynamic Client Registration: Codex and Claude Code register themselves on first use.
    clientRegistrationEndpoint: '/oauth/register',
    // RFC 9728: where an unauthenticated agent learns which server signs people in.
    resourceMetadata: {
      resource: `${base}/mcp`,
      authorization_servers: [base],
      bearer_methods_supported: ['header'],
      resource_name: 'Wenext',
    },
  });
  cached = { base, provider };
  return provider;
}

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return providerFor(env).fetch(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
