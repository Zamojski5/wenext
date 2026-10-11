/**
 * The signed identity header the origin trusts: a short-lived HS256 JWT in `X-Wenext-Edge`.
 *
 * The origin side is `services/control_plane/control_plane/hosted/edge.py`; the two must agree on
 * the header name, the algorithm (HS256 only), the claims and the lifetime. `test/fixtures/
 * edge-vector.json` is checked by BOTH sides' tests, so a change here that the origin would
 * refuse fails a test before it fails in production.
 */

export const EDGE_HEADER = 'X-Wenext-Edge';
/** The origin refuses a header that lives longer than 120 s, and replays of a `jti` within it. */
export const EDGE_TTL_SECONDS = 60;

export interface EdgeIdentity {
  provider: string;
  subject: string;
  email: string | null;
  login: string | null;
}

const encoder = new TextEncoder();

export function base64url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

function randomJti(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(16)));
}

export interface SignOptions {
  /** Unix seconds. Defaults to now. */
  now?: number;
  ttl?: number;
  jti?: string;
}

export async function signEdgeHeader(
  secret: string,
  identity: EdgeIdentity,
  options: SignOptions = {},
): Promise<string> {
  const issued = Math.floor(options.now ?? Date.now() / 1000);
  const header = base64url(encoder.encode(JSON.stringify({ alg: 'HS256', typ: 'JWT' })));
  // The order of the keys is part of the vector test: it is the order the origin's tests mint.
  const claims = {
    provider: identity.provider,
    subject: identity.subject,
    email: identity.email,
    login: identity.login,
    iat: issued,
    exp: issued + (options.ttl ?? EDGE_TTL_SECONDS),
    jti: options.jti ?? randomJti(),
  };
  const body = base64url(encoder.encode(JSON.stringify(claims)));
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(`${header}.${body}`));
  return `${header}.${body}.${base64url(new Uint8Array(mac))}`;
}
