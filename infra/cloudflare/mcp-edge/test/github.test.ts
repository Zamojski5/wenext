import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Env } from '../src/env';
import { exchangeCode, fetchIdentity, githubAuthorizeUrl, isAllowed, s256 } from '../src/github';
import { consentPage, escapeHtml } from '../src/pages';

const env = {
  PUBLIC_BASE_URL: 'https://mcp.example.test',
  GITHUB_CLIENT_ID: 'cid',
  GITHUB_CLIENT_SECRET: 'csecret',
} as Env;

afterEach(() => vi.unstubAllGlobals());

describe('github', () => {
  it('builds the authorize URL with the Worker callback, scopes, state and an S256 challenge', async () => {
    const url = new URL(await githubAuthorizeUrl(env, 'STATE', 'verifier-value'));
    expect(url.origin + url.pathname).toBe('https://github.com/login/oauth/authorize');
    expect(url.searchParams.get('redirect_uri')).toBe('https://mcp.example.test/callback');
    expect(url.searchParams.get('client_id')).toBe('cid');
    expect(url.searchParams.get('scope')).toBe('read:user user:email');
    expect(url.searchParams.get('state')).toBe('STATE');
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    expect(url.searchParams.get('code_challenge')).toBe(await s256('verifier-value'));
  });

  it('s256 matches RFC 7636 appendix B', async () => {
    expect(await s256('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
  });

  it('exchanges the code, sending the verifier and never the client secret to anyone else', async () => {
    const fetchMock = vi.fn(async () => Response.json({ access_token: 'gho_x' }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await exchangeCode(env, 'CODE', 'VERIFIER')).toBe('gho_x');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://github.com/login/oauth/access_token');
    expect(JSON.parse(init.body as string)).toMatchObject({ client_id: 'cid', client_secret: 'csecret', code: 'CODE', code_verifier: 'VERIFIER', redirect_uri: 'https://mcp.example.test/callback' });
  });

  it('refuses an exchange that gives no token', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: 'bad_verification_code' })));
    await expect(exchangeCode(env, 'CODE', 'V')).rejects.toThrow(/bad_verification_code/);
  });

  it('identifies the person by the numeric id and takes the public e-mail when there is one', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ id: 4242, login: 'alice', email: 'alice@example.test' })));
    expect(await fetchIdentity(env, 'tok')).toEqual({ provider: 'github', subject: '4242', login: 'alice', email: 'alice@example.test' });
  });

  it('falls back to the verified primary e-mail, and survives not getting one', async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith('/user')
        ? Response.json({ id: 1, login: 'bob', email: null })
        : Response.json([{ email: 'x@example.test', primary: false, verified: true }, { email: 'bob@example.test', primary: true, verified: true }]),
    );
    vi.stubGlobal('fetch', fetchMock);
    expect((await fetchIdentity(env, 'tok')).email).toBe('bob@example.test');

    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.endsWith('/user') ? Response.json({ id: 1, login: 'bob' }) : new Response('no', { status: 404 }))));
    expect((await fetchIdentity(env, 'tok')).email).toBeNull();
  });

  it('refuses a user without an id', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ login: 'ghost' })));
    await expect(fetchIdentity(env, 'tok')).rejects.toThrow(/id or login/);
  });
});

describe('allowlist', () => {
  it('is open when empty and case-insensitive when set', () => {
    expect(isAllowed({ ...env } as Env, 'anyone')).toBe(true);
    expect(isAllowed({ ...env, ALLOWED_GITHUB_LOGINS: ' ' } as Env, 'anyone')).toBe(true);
    const closed = { ...env, ALLOWED_GITHUB_LOGINS: 'Alice, bob' } as Env;
    expect(isAllowed(closed, 'alice')).toBe(true);
    expect(isAllowed(closed, 'BOB')).toBe(true);
    expect(isAllowed(closed, 'mallory')).toBe(false);
  });
});

describe('consent page', () => {
  const details = {
    clientId: 'c',
    clientName: '<script>alert(1)</script>',
    redirectUri: 'http://127.0.0.1:1/cb',
    redirectHost: '"><img src=x>',
    redirectIsLoopback: true,
    scope: [],
  };

  it('escapes everything a client chose', () => {
    const page = consentPage(details, 'HANDLE"><b>');
    expect(page).not.toContain('<script>alert(1)</script>');
    expect(page).not.toContain('<img src=x>');
    expect(page).not.toContain('HANDLE"><b>');
    expect(page).toContain('&#60;script&#62;');
    expect(page).toContain('name="decision" value="approve"');
    expect(page).toContain('name="decision" value="deny"');
    expect(page).toContain('an app on your computer');
  });

  it('escapeHtml covers the five characters', () => {
    expect(escapeHtml(`&<>"'`)).toBe('&#38;&#60;&#62;&#34;&#39;');
  });
});
