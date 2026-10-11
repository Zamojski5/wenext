/**
 * The guard that guards the guard.
 *
 * `tools/verify_run.mjs` exists because vitest exits 0 when it collects nothing — "zebrane" is not
 * "wykonane", the rule `tools/dev/run_db_tests.sh` enforces for the Python suites. But until
 * 2026-08-31 the guard's own floor had NO check: it had been raised 10 → 18 when two files landed,
 * and editing the constant back to `2` left the entire suite green. The only thing the number is
 * compared against is the report it is meant to police, so lowering it disables it silently. A
 * guard whose value can be edited to nothing is a declaration.
 *
 * This file closes that, in two independent ways — and both are needed, because they fail for
 * different edits:
 *
 *   1. **The number.** Each floor must be at least the number of test declarations present in the
 *      corresponding directory. Setting `MINIMUM_TESTS = 2` fails here even though every other test
 *      in the package still passes.
 *   2. **The comparison.** The script is EXECUTED against synthetic reports and watched refusing
 *      each way it must refuse, and accepting a good one. Deleting the `total < floor` branch fails
 *      here even though the constant is untouched.
 *
 * The static count is deliberately a LOWER bound rather than an equality: `it` inside a loop runs
 * once per iteration, so the runtime total is always at least the number of declarations. The
 * runtime side is policed from the other direction by the script itself, so the floor ends up
 * pinned between the two.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

const APP_ROOT = path.resolve(import.meta.dirname, "..");
const SCRIPT = path.join(APP_ROOT, "tools", "verify_run.mjs");
const SCRIPT_TEXT = fs.readFileSync(SCRIPT, "utf8");

let workspace: string;

beforeAll(() => {
  workspace = fs.mkdtempSync(path.join(os.tmpdir(), "wenext-verify-run-"));
});

afterAll(() => {
  fs.rmSync(workspace, { recursive: true, force: true });
});

function floorFromScript(name: string): number {
  const match = new RegExp(`^const ${name} = (\\d+);$`, "m").exec(SCRIPT_TEXT);
  if (match?.[1] === undefined) {
    throw new Error(
      `nie znaleziono stałej ${name} w ${SCRIPT} — próg, którego nie da się odczytać, nie da ` +
        "się też pilnować",
    );
  }
  return Number.parseInt(match[1], 10);
}

/**
 * How many test declarations live in one directory.
 *
 * Counts `it(` and `it.each(` at the start of a statement. Intentionally crude: this is a FLOOR, so
 * undercounting is safe and overcounting would be the dangerous direction. A parametrised `it.each`
 * counts once here while running many times, which is exactly why the comparison below is `>=`.
 */
function declaredTests(directory: string): number {
  const root = path.join(APP_ROOT, directory);
  if (!fs.existsSync(root)) {
    throw new Error(`nie ma katalogu ${root} — nie ma czego policzyć`);
  }
  let count = 0;
  let files = 0;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.test\.tsx?$/.test(entry.name)) {
      continue;
    }
    files += 1;
    const content = fs.readFileSync(path.join(root, entry.name), "utf8");
    count += (content.match(/^\s*it(?:\.each\([\s\S]*?\))?\s*\(/gm) ?? []).length;
  }
  if (files === 0) {
    throw new Error(
      `w ${root} nie ma ani jednego pliku *.test.ts(x) — zero policzonych testów zamieniłoby ` +
        "próg w formalność",
    );
  }
  return count;
}

function runScript(reportName: string, body: unknown, suite?: string): { code: number; output: string } {
  const file = path.join(workspace, reportName);
  if (body !== undefined) {
    fs.writeFileSync(file, typeof body === "string" ? body : JSON.stringify(body));
  }
  const argv = suite === undefined ? [SCRIPT, file] : [SCRIPT, file, suite];
  try {
    const stdout = execFileSync(process.execPath, argv, { encoding: "utf8", stdio: "pipe" });
    return { code: 0, output: stdout };
  } catch (error) {
    const failure = error as { status?: number; stdout?: string; stderr?: string };
    return {
      code: failure.status ?? -1,
      output: `${failure.stdout ?? ""}${failure.stderr ?? ""}`,
    };
  }
}

function reportOf(total: number, extra: Record<string, number> = {}) {
  return {
    numTotalTests: total,
    numPassedTests: total,
    numFailedTests: 0,
    numPendingTests: 0,
    numTodoTests: 0,
    ...extra,
  };
}

describe("tools/verify_run.mjs", () => {
  it("both_floors_are_declared_and_readable", () => {
    // Read first, compare later: a renamed constant must fail loudly here rather than make every
    // comparison below run against a default nobody chose.
    expect(floorFromScript("MINIMUM_TESTS")).toBeGreaterThan(0);
    expect(floorFromScript("MINIMUM_E2E_TESTS")).toBeGreaterThan(0);
  });

  it("the_unit_floor_is_at_least_the_number_of_declared_unit_tests", () => {
    // THE assertion this file exists for. `MINIMUM_TESTS = 2` fails right here.
    const declared = declaredTests("tests");
    expect(declared).toBeGreaterThan(20);
    expect(
      floorFromScript("MINIMUM_TESTS"),
      `w tests/ zadeklarowano ${declared} testów, a próg jest niższy — próg, który przepuszcza ` +
        "utratę pliku z testami, nie jest progiem",
    ).toBeGreaterThanOrEqual(declared);
  });

  it("the_e2e_floor_is_at_least_the_number_of_declared_e2e_tests", () => {
    const declared = declaredTests("e2e");
    expect(declared).toBeGreaterThan(0);
    expect(floorFromScript("MINIMUM_E2E_TESTS")).toBeGreaterThanOrEqual(declared);
  });

  it("a_report_below_the_floor_is_refused", () => {
    // Watched refusing, not assumed to refuse. `floor - 1` is the interesting number: one test
    // short of the suite is exactly the shape of a lost file.
    const floor = floorFromScript("MINIMUM_TESTS");
    const short = runScript("short.json", reportOf(floor - 1));
    expect(short.code).toBe(1);
    expect(short.output).toContain("NIE ZALICZONA");
    expect(short.output).toContain(`próg suity 'unit' to ${floor}`);

    const exact = runScript("exact.json", reportOf(floor));
    expect(exact.code).toBe(0);
    expect(exact.output).toContain(`${floor}/${floor}`);
  });

  it("a_skipped_test_is_refused_even_when_the_count_is_high_enough", () => {
    // The failure mode `run_db_tests.sh` was written for: a skipped test is indistinguishable from
    // a passing one in the summary, and the collected count includes it.
    const floor = floorFromScript("MINIMUM_TESTS");
    const skipped = runScript(
      "skipped.json",
      reportOf(floor, { numPassedTests: floor - 1, numPendingTests: 1 }),
    );
    expect(skipped.code).toBe(1);
    expect(skipped.output).toContain("pominiętych: 1");

    const todo = runScript("todo.json", reportOf(floor, { numTodoTests: 2 }));
    expect(todo.code).toBe(1);
    expect(todo.output).toContain("todo: 2");
  });

  it("a_failing_run_and_a_run_with_no_passes_are_both_refused", () => {
    const floor = floorFromScript("MINIMUM_TESTS");
    const failing = runScript("failing.json", reportOf(floor, { numFailedTests: 3 }));
    expect(failing.code).toBe(1);
    expect(failing.output).toContain("testów z porażką: 3");

    const nothingPassed = runScript(
      "nopass.json",
      reportOf(floor, { numPassedTests: 0, numFailedTests: floor }),
    );
    expect(nothingPassed.code).toBe(1);
    expect(nothingPassed.output).toContain("ani jeden test nie przeszedł");
  });

  it("a_missing_or_unparsable_report_exits_two_rather_than_zero", () => {
    // No evidence is not a pass. Exit code 2 rather than 1, so a caller can tell "the suite failed"
    // from "there is nothing to read" — the same distinction as NO_GO versus NIE_URUCHOMIONO.
    const absent = runScript("nie-ma-mnie.json", undefined);
    expect(absent.code).toBe(2);
    expect(absent.output).toContain("nie zostawił dowodu");

    const garbage = runScript("garbage.json", "to nie jest json");
    expect(garbage.code).toBe(2);
    expect(garbage.output).toContain("nie jest poprawnym JSON-em");
  });

  it("an_unknown_suite_name_is_refused_instead_of_falling_back_to_a_lower_floor", () => {
    // A typo in the suite name must not silently select the smaller floor — that is how an e2e run
    // would end up policed by no threshold at all.
    const floor = floorFromScript("MINIMUM_TESTS");
    const unknown = runScript("unit.json", reportOf(floor), "jednostkowa");
    expect(unknown.code).toBe(2);
    expect(unknown.output).toContain("nieznana suita");
  });

  it("the_e2e_suite_is_judged_by_its_own_floor", () => {
    const e2eFloor = floorFromScript("MINIMUM_E2E_TESTS");
    const unitFloor = floorFromScript("MINIMUM_TESTS");
    expect(e2eFloor).toBeLessThan(unitFloor);

    const good = runScript("e2e-ok.json", reportOf(e2eFloor), "e2e");
    expect(good.code).toBe(0);
    expect(good.output).toContain("(e2e)");

    const short = runScript("e2e-short.json", reportOf(e2eFloor - 1), "e2e");
    expect(short.code).toBe(1);
    expect(short.output).toContain(`próg suity 'e2e' to ${e2eFloor}`);
  });
});
