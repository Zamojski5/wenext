import { EDGE_HEADER, signEdgeHeader } from './edge-token';
import type { Env, Props } from './env';

/** The same bound the origin applies: a JSON-RPC frame is a few kilobytes. */
export const MAX_BODY_BYTES = 1024 * 1024;
const ORIGIN_TIMEOUT_MS = 110_000;

function json(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...extra },
  });
}

/**
 * The request body, or `null` when it is longer than `limit`. Read as a stream and stopped at the
 * limit: a body with no Content-Length (chunked) must not be buffered whole before it is judged.
 */
async function readBounded(request: Request, limit: number): Promise<Uint8Array | null> {
  const declared = Number(request.headers.get('content-length') ?? '0');
  if (declared > limit) return null;
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

/** The identity the provider stored with the grant, or `null` when it is not what `callback` wrote. */
function propsOf(ctx: ExecutionContext): Props | null {
  const props: unknown = ctx.props;
  if (typeof props !== 'object' || props === null) return null;
  const { provider, subject, login, email } = props as Record<string, unknown>;
  if (typeof provider !== 'string' || typeof subject !== 'string' || !subject) return null;
  return {
    provider: provider as Props['provider'],
    subject,
    login: typeof login === 'string' ? login : '',
    email: typeof email === 'string' ? email : null,
  };
}

/**
 * The MCP API handler: runs only for requests the OAuth provider has already authenticated, and
 * forwards each one to the origin with the person's identity signed into `X-Wenext-Edge`.
 *
 * WHAT IS FORWARDED: the JSON-RPC body, its content type, and the signed header. NOT forwarded:
 * the `Authorization` header (the origin must never see an OAuth token), cookies, or any other
 * header the client sent. What comes back: the status, the content type and the body, nothing else.
 */
export const mcpHandler = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    // The origin has no server-initiated stream and no session to end.
    if (request.method !== 'POST') {
      return new Response(null, { status: 405, headers: { Allow: 'POST' } });
    }
    const props = propsOf(ctx);
    if (!props) {
      // A grant written by something other than `callback`: refuse rather than guess who it is.
      return json(500, { error: 'grant carries no identity' });
    }
    const body = await readBounded(request, MAX_BODY_BYTES);
    if (body === null) return json(413, { error: 'too large' });

    const signed = await signEdgeHeader(env.WENEXT_EDGE_SECRET, {
      provider: props.provider,
      subject: props.subject,
      email: props.email,
      login: props.login || null,
    });
    const headers = new Headers({
      'content-type': request.headers.get('content-type') ?? 'application/json',
      accept: 'application/json',
    });
    headers.set(EDGE_HEADER, signed);

    let upstream: Response;
    try {
      upstream = await fetch(env.ORIGIN_MCP_URL, {
        method: 'POST',
        headers,
        body,
        redirect: 'manual',
        signal: AbortSignal.timeout(ORIGIN_TIMEOUT_MS),
      });
    } catch {
      return json(502, { error: 'origin unavailable' });
    }

    const out = new Headers({ 'cache-control': 'no-store' });
    const type = upstream.headers.get('content-type');
    if (type) out.set('content-type', type);
    // 202 and other bodiless answers must stay bodiless.
    const hasBody = upstream.status !== 202 && upstream.status !== 204;
    return new Response(hasBody ? upstream.body : null, { status: upstream.status, headers: out });
  },
} satisfies ExportedHandler<Env>;
