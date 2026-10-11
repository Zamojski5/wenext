import type { Env, Props } from './env';

const DEFAULTS = {
  authorize: 'https://github.com/login/oauth/authorize',
  token: 'https://github.com/login/oauth/access_token',
  api: 'https://api.github.com',
};

export const GITHUB_SCOPE = 'read:user user:email';

/** The Worker's own callback URL, which must equal the GitHub OAuth App's callback setting. */
export function callbackUrl(env: Env): string {
  return `${env.PUBLIC_BASE_URL}/callback`;
}

export async function s256(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  let binary = '';
  for (const byte of new Uint8Array(digest)) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

export async function githubAuthorizeUrl(
  env: Env,
  state: string,
  verifier: string,
): Promise<string> {
  const url = new URL(env.GITHUB_AUTHORIZE_URL || DEFAULTS.authorize);
  url.searchParams.set('client_id', env.GITHUB_CLIENT_ID);
  url.searchParams.set('redirect_uri', callbackUrl(env));
  url.searchParams.set('scope', GITHUB_SCOPE);
  url.searchParams.set('state', state);
  url.searchParams.set('code_challenge', await s256(verifier));
  url.searchParams.set('code_challenge_method', 'S256');
  return url.href;
}

export class GithubError extends Error {}

/** Exchange GitHub's code for its access token. The token is used once, to read who signed in. */
export async function exchangeCode(env: Env, code: string, verifier: string): Promise<string> {
  const response = await fetch(env.GITHUB_TOKEN_URL || DEFAULTS.token, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      code,
      redirect_uri: callbackUrl(env),
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new GithubError(`github token endpoint answered ${response.status}`);
  const body = (await response.json()) as { access_token?: unknown; error?: unknown };
  if (typeof body.access_token !== 'string' || !body.access_token) {
    throw new GithubError(`github gave no access token (${String(body.error ?? 'unknown')})`);
  }
  return body.access_token;
}

async function api<T>(env: Env, path: string, token: string): Promise<T> {
  const response = await fetch(`${env.GITHUB_API_URL || DEFAULTS.api}${path}`, {
    headers: {
      authorization: `Bearer ${token}`,
      accept: 'application/vnd.github+json',
      'user-agent': 'wenext-mcp-edge',
    },
  });
  if (!response.ok) throw new GithubError(`github api ${path} answered ${response.status}`);
  return (await response.json()) as T;
}

interface GithubUser {
  id?: number;
  login?: string;
  name?: string | null;
  email?: string | null;
}

/** The person who just signed in: GitHub's stable id, login and a verified primary e-mail. */
export async function fetchIdentity(env: Env, token: string): Promise<Props> {
  const user = await api<GithubUser>(env, '/user', token);
  if (typeof user.id !== 'number' || typeof user.login !== 'string') {
    throw new GithubError('github user has no id or login');
  }
  let email = typeof user.email === 'string' && user.email ? user.email : null;
  if (!email) {
    try {
      const emails = await api<{ email: string; primary: boolean; verified: boolean }[]>(
        env,
        '/user/emails',
        token,
      );
      email = emails.find((one) => one.primary && one.verified)?.email ?? null;
    } catch {
      email = null; // the e-mail is for the operator's list; its absence must not block sign-in
    }
  }
  return { provider: 'github', subject: String(user.id), login: user.login, email };
}

/** Whether this login may sign in: no allowlist configured means everybody with a GitHub account. */
export function isAllowed(env: Env, login: string): boolean {
  const list = (env.ALLOWED_GITHUB_LOGINS ?? '')
    .split(',')
    .map((one) => one.trim().toLowerCase())
    .filter(Boolean);
  return list.length === 0 || list.includes(login.toLowerCase());
}
