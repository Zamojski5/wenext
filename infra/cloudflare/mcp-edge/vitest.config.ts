import { defineConfig } from 'vitest/config';

// Plain Node: everything under test (WebCrypto, Request, Response, fetch) is a Node 24 global.
// The OAuth provider's own flows are covered by its package; what is tested here is what this
// Worker adds: the signed header, the proxy, the GitHub identity and the pages.
export default defineConfig({
  test: { environment: 'node', include: ['test/**/*.test.ts'] },
});
