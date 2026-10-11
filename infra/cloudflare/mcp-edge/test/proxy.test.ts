import { afterEach, describe, expect, it, vi } from 'vitest';
import { EDGE_HEADER } from '../src/edge-token';
import type { Env, Props } from '../src/env';
import { MAX_BODY_BYTES, mcpHandler } from '../src/proxy';

const env = {
  WENEXT_EDGE_SECRET: 'e'.repeat(40),
  ORIGIN_MCP_URL: 'https://origin.example.test/mcp',
} as Env;

const props: Props = { provider: 'github', subject: '4242', login: 'alice', email: 'a@example.test' };
const ctx = (p: unknown = props) => ({ props: p, waitUntil() {}, passThroughOnException() {} }) as unknown as ExecutionContext;

const decode = (segment: string) =>
  JSON.parse(Buffer.from(segment.replaceAll('-', '+').replaceAll('_', '/'), 'base64').toString());

function post(body: string, headers: Record<string, string> = {}) {
  return new Request('https://mcp.example.test/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer SECRET-OAUTH-TOKEN', cookie: 'a=b', ...headers },
    body,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('mcpHandler', () => {
  it('forwards the body with the signed identity and nothing of the OAuth session', async () => {
    const fetchMock = vi.fn(async () => new Response('{"jsonrpc":"2.0","id":1,"result":{}}', { status: 200, headers: { 'content-type': 'application/json', 'set-cookie': 'x=y', server: 'uvicorn' } }));
    vi.stubGlobal('fetch', fetchMock);

    const response = await mcpHandler.fetch(post('{"jsonrpc":"2.0","id":1,"method":"ping"}'), env, ctx());

    expect(response.status).toBe(200);
    expect(await response.text()).toBe('{"jsonrpc":"2.0","id":1,"result":{}}');
    expect(response.headers.get('set-cookie')).toBeNull();
    expect(response.headers.get('server')).toBeNull();
    expect(response.headers.get('content-type')).toBe('application/json');

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://origin.example.test/mcp');
    expect(init.method).toBe('POST');
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"jsonrpc":"2.0","id":1,"method":"ping"}');
    const sent = new Headers(init.headers);
    expect(sent.get('authorization')).toBeNull();
    expect(sent.get('cookie')).toBeNull();
    const [, body] = (sent.get(EDGE_HEADER) as string).split('.');
    expect(decode(body)).toMatchObject({ provider: 'github', subject: '4242', login: 'alice', email: 'a@example.test' });
    expect(JSON.stringify([...sent.entries()])).not.toContain('SECRET-OAUTH-TOKEN');
  });

  it('keeps a 202 bodiless', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 202 })));
    const response = await mcpHandler.fetch(post('{"jsonrpc":"2.0","method":"notifications/initialized"}'), env, ctx());
    expect(response.status).toBe(202);
    expect(await response.text()).toBe('');
  });

  it('passes the origin refusals through as they are', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error":"forbidden"}', { status: 403, headers: { 'content-type': 'application/json' } })));
    const response = await mcpHandler.fetch(post('{}'), env, ctx());
    expect(response.status).toBe(403);
  });

  it('answers 405 to anything but POST without calling the origin', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    for (const method of ['GET', 'DELETE', 'PUT']) {
      const response = await mcpHandler.fetch(new Request('https://mcp.example.test/mcp', { method }), env, ctx());
      expect(response.status).toBe(405);
      expect(response.headers.get('allow')).toBe('POST');
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refuses a grant without an identity instead of guessing one', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    for (const bad of [null, {}, { provider: 'github' }, { provider: 'github', subject: '' }]) {
      const response = await mcpHandler.fetch(post('{}'), env, ctx(bad));
      expect(response.status).toBe(500);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refuses a body over the bound before touching the origin', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const response = await mcpHandler.fetch(post('x'.repeat(MAX_BODY_BYTES + 1)), env, ctx());
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stops reading a chunked body at the bound, with no Content-Length to go by', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const chunk = new Uint8Array(64 * 1024).fill(120);
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent > MAX_BODY_BYTES * 4) return controller.close();
        sent += chunk.byteLength;
        controller.enqueue(chunk);
      },
    });
    const request = new Request('https://mcp.example.test/mcp', {
      method: 'POST',
      body: stream,
      duplex: 'half',
    } as RequestInit);
    const response = await mcpHandler.fetch(request, env, ctx());
    expect(response.status).toBe(413);
    expect(sent).toBeLessThan(MAX_BODY_BYTES * 2);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('turns an unreachable origin into a 502', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('connect failed'); }));
    const response = await mcpHandler.fetch(post('{}'), env, ctx());
    expect(response.status).toBe(502);
  });
});
