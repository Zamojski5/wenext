import { describe, expect, it } from 'vitest';
import vector from './fixtures/edge-vector.json';
import { EDGE_TTL_SECONDS, signEdgeHeader } from '../src/edge-token';

const decode = (segment: string) =>
  JSON.parse(Buffer.from(segment.replaceAll('-', '+').replaceAll('_', '/'), 'base64').toString());

describe('signEdgeHeader', () => {
  it('produces exactly the header the origin tests verify (cross-language vector)', async () => {
    const header = await signEdgeHeader(vector.secret, vector.identity, {
      now: vector.now,
      jti: vector.jti,
      ttl: vector.ttl,
    });
    // The Python side minted `vector.header`; its own tests verify the same string. If this fails,
    // the two sides no longer agree on the header, the claims or their order.
    expect(header).toBe(vector.header);
  });

  it('carries the claims the origin requires and lives one minute', async () => {
    const header = await signEdgeHeader('x'.repeat(40), {
      provider: 'github',
      subject: '7',
      email: null,
      login: 'seven',
    });
    const [head, body] = header.split('.');
    expect(decode(head)).toEqual({ alg: 'HS256', typ: 'JWT' });
    const claims = decode(body);
    expect(Object.keys(claims)).toEqual([
      'provider',
      'subject',
      'email',
      'login',
      'iat',
      'exp',
      'jti',
    ]);
    expect(claims.exp - claims.iat).toBe(EDGE_TTL_SECONDS);
    expect(claims.email).toBeNull();
    expect(claims.jti.length).toBeGreaterThanOrEqual(16);
  });

  it('uses a fresh jti every time, so no header is ever a replay of another', async () => {
    const identity = { provider: 'github', subject: '7', email: null, login: 'seven' };
    const jtis = new Set<string>();
    for (let i = 0; i < 50; i++) {
      const [, body] = (await signEdgeHeader('x'.repeat(40), identity)).split('.');
      jtis.add(decode(body).jti);
    }
    expect(jtis.size).toBe(50);
  });

  it('depends on the secret', async () => {
    const identity = { provider: 'github', subject: '7', email: null, login: null };
    const options = { now: 1, jti: 'j' };
    const one = await signEdgeHeader('a'.repeat(40), identity, options);
    const two = await signEdgeHeader('b'.repeat(40), identity, options);
    expect(one.split('.').slice(0, 2)).toEqual(two.split('.').slice(0, 2));
    expect(one.split('.')[2]).not.toBe(two.split('.')[2]);
  });
});
