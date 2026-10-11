import { defineConfig } from "vitest/config";

/**
 * The end-to-end suite, kept in a SEPARATE config from the unit suite on purpose.
 *
 * The unit suite (`vitest.config.mts`) must be runnable anywhere, with nothing running. This one
 * requires PostgreSQL, the control plane and a built Next.js server, and it FAILS rather than skips
 * when they are absent (`e2e/dashboard_reads_postgres.e2e.test.ts` throws in `beforeAll`). Merging
 * the two would force one of those properties to give: either the unit suite would start needing a
 * database, or these tests would have to be skippable — and a skipped database test reads as green
 * in the summary, which is the exact hole `tools/dev/run_db_tests.sh` was written to close.
 *
 * `environment: "node"`: nothing here parses HTML with `DOMParser`. The assertions are made against
 * the raw bytes a real server sent, because that is what a browser would receive; a DOM built from
 * it would let a helpful parser repair markup the server got wrong.
 *
 * The timeout is generous because each case makes several real HTTP round trips through two
 * processes and a database.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["e2e/**/*.e2e.test.ts"],
    // A run that collected no file is NOT a green run. Vitest's default is to exit 0 with "No test
    // files found", which is `all([]) == True` wearing a different hat (D-011).
    passWithNoTests: false,
    testTimeout: 30_000,
    hookTimeout: 30_000,
    // One file, one server, shared session — running the cases in parallel against a single Next.js
    // process would interleave logins and logouts and make the logout test flaky for a reason that
    // has nothing to do with the code.
    fileParallelism: false,
    sequence: { concurrent: false },
  },
});
