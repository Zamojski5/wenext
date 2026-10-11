import {
  AuthorizationError,
  CimdFetchError,
  authorizationErrorRedirect,
  type OAuthHelpers,
} from '@cloudflare/workers-oauth-provider';
import type { Env, Props } from './env';
import {
  exchangeCode,
  fetchIdentity,
  githubAuthorizeUrl,
  GithubError,
  isAllowed,
} from './github';
import { consentPage, landingPage, messagePage } from './pages';

const HTML = { 'content-type': 'text/html; charset=utf-8' };

function html(status: number, body: string, extra?: Headers): Response {
  const headers = new Headers(extra);
  for (const [key, value] of Object.entries(HTML)) headers.set(key, value);
  headers.set('cache-control', 'no-store');
  return new Response(body, { status, headers });
}

/** GET /authorize: validate the client's request, then ask the person to allow it. */
async function showConsent(request: Request, oauth: OAuthHelpers): Promise<Response> {
  const authRequest = await oauth.parseAuthRequest(request);
  const details = await oauth.describeConsent(authRequest); // first: a failed lookup leaves nothing in KV
  const consent = await oauth.beginConsent(authRequest);
  return html(200, consentPage(details, consent.handle), consent.headers);
}

/** POST /authorize: Deny goes back to the client; Allow goes on to GitHub, and only then. */
async function decide(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const form = await request.formData();
  const handle = String(form.get('handle') ?? '');
  if (form.get('decision') !== 'approve') {
    const denied = await oauth.denyConsent(request, handle);
    return new Response(null, { status: 302, headers: denied.headers });
  }
  const approved = await oauth.approveConsent(request, handle);
  const verifier = crypto.randomUUID() + crypto.randomUUID();
  const { state, headers } = await oauth.beginUpstream(approved.request, {
    data: { verifier }, // returned at the callback; never sent to GitHub
    headers: approved.headers,
  });
  headers.set('Location', await githubAuthorizeUrl(env, state, verifier));
  return new Response(null, { status: 302, headers });
}

/** GET /callback: GitHub sent the person back. Finish the OAuth flow with the identity we read. */
async function callback(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const { request: original, data, headers } = await oauth.finishUpstream<{ verifier: string }>(
    request,
  );
  const params = new URL(request.url).searchParams;
  if (params.get('error')) {
    // The person declined at GitHub, or GitHub failed: tell the client, with state and iss.
    headers.set('Location', authorizationErrorRedirect(original, 'access_denied'));
    return new Response(null, { status: 302, headers });
  }
  const code = params.get('code');
  if (!code) return html(400, messagePage('Sign-in failed', 'GitHub sent no code.'), headers);

  let identity: Props;
  try {
    identity = await fetchIdentity(env, await exchangeCode(env, code, data.verifier));
  } catch (error) {
    if (error instanceof GithubError) {
      console.error(JSON.stringify({ event: 'github_sign_in_failed', reason: error.message }));
      return html(502, messagePage('Sign-in failed', 'GitHub did not confirm who you are. Try again.'), headers);
    }
    throw error;
  }
  if (!isAllowed(env, identity.login)) {
    return html(
      403,
      messagePage('Not yet', 'Wenext is in a closed beta and this GitHub account is not on the list yet.'),
      headers,
    );
  }
  const { redirectTo } = await oauth.completeAuthorization({
    request: original,
    userId: identity.subject, // no ":" in a GitHub numeric id
    metadata: { login: identity.login },
    scope: original.scope,
    props: identity satisfies Props,
  });
  headers.set('Location', redirectTo);
  return new Response(null, { status: 302, headers });
}

/** The provider's default handler: the pages and redirects of the sign-in. Everything else is 404. */
export const authHandler = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const oauth = env.OAUTH_PROVIDER;
    try {
      if (url.pathname === '/authorize' && request.method === 'GET') {
        return await showConsent(request, oauth);
      }
      if (url.pathname === '/authorize' && request.method === 'POST') {
        return await decide(request, env, oauth);
      }
      if (url.pathname === '/callback' && request.method === 'GET') {
        return await callback(request, env, oauth);
      }
      if (url.pathname === '/' && request.method === 'GET') {
        return html(200, landingPage(`${env.PUBLIC_BASE_URL}/mcp`));
      }
      return new Response('Not found', { status: 404 });
    } catch (error) {
      // Redirect to the client only when the library says the client and redirect URI were
      // validated; render everything else locally. A bug or an outage stays a 500.
      if (error instanceof AuthorizationError && error.redirectTo) {
        return Response.redirect(error.redirectTo, 302);
      }
      if (error instanceof AuthorizationError || error instanceof CimdFetchError) {
        const message =
          error instanceof AuthorizationError
            ? error.description
            : 'This app could not be verified.';
        return html(400, messagePage('Cannot continue', message || 'The request is not valid.'));
      }
      throw error;
    }
  },
} satisfies ExportedHandler<Env>;
