import { defineConfig } from "vitest/config";

/**
 * The suite renders server components with `react-dom/server` and then parses the produced
 * markup with `DOMParser`, which is why a DOM environment is needed even though not one line
 * of this dashboard runs in a browser (D-016 — the operator session never reaches client
 * code, so there is no client code to test).
 *
 * `include` is narrow on purpose: `.next/` holds generated output and `node_modules/` holds
 * other people's suites, and a runner that picks those up reports numbers nobody can read.
 */
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // A run that collected no file is NOT a green run. Vitest's default is to exit 0 with
    // "No test files found", which is `all([]) == True` wearing a different hat — the exact
    // failure that has already produced false passes in this repository (D-011).
    passWithNoTests: false,
  },
});
