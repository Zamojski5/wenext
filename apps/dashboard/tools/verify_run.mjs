/**
 * Refuse a test run that did not actually run the tests.
 *
 * This is `tools/dev/run_db_tests.sh`'s rule ported to the JS suites: that runner reads the JUnit
 * XML and rejects a run with `passed == 0` or `skipped > 0`, because a module whose tests are all
 * skipped exits 0 and reads as green. Vitest has the same hole in a different shape — a bad
 * `include` glob, a `describe.skip` left behind, a suite renamed out of the pattern — and every one
 * of them ends in "no tests" plus exit code 0.
 *
 * A FLOOR ON THE NUMBER OF TESTS, not just `> 0`: a run that suddenly reports two has lost the rest
 * of the suite without a single red line.
 *
 * ---------------------------------------------------------------------------------------------
 * THE FLOOR NOW HAS A GUARD OF ITS OWN, AND UNTIL 2026-08-31 IT DID NOT.
 *
 * That was a real finding, not a tidy-up. The floor was raised 10 → 18 when two test files landed,
 * and nothing checked the constant itself: editing it back to `2` left the whole suite green,
 * because the only thing the number is compared against is the report it is meant to police. A
 * guard whose own value can be lowered to nothing is a declaration, which is exactly what
 * `docs/KONWENCJE.md` says about a convention no test covers.
 *
 * So `tests/verify_run_guard.test.ts` now does two things this file cannot do for itself:
 *
 *   1. it parses the constants below and requires each floor to be at least the number of test
 *      declarations actually present in the corresponding directory — lowering a floor to 2 fails
 *      that assertion instead of passing silently;
 *   2. it RUNS this script against synthetic reports and watches it refuse each way it must refuse
 *      (below the floor, any skipped, any failed, zero passed, missing report) and accept a good
 *      one. A guard nobody has seen refuse anything is not a guard.
 *
 * Both halves are needed. The first stops the number being edited away; the second stops the
 * comparison being edited away.
 * ---------------------------------------------------------------------------------------------
 *
 * Usage: node tools/verify_run.mjs <vitest json report> [unit|e2e]
 */
import fs from "node:fs";

/**
 * Floors, per suite. RAISED WITH THE SUITE — a floor left at an old number stops guarding every
 * test added after it, and the suite could lose a whole file and still clear it.
 * `tests/verify_run_guard.test.ts` compares each of these with a static count of the test
 * declarations in `tests/` and `e2e/`, so raising one is part of adding tests, not an afterthought.
 */
// 686 od D-055: dołożono pięć plików testów „Pokoju" (schemat, słowa, akcje, widok, arkusz).
const MINIMUM_TESTS = 686;
const MINIMUM_E2E_TESTS = 114;

const FLOORS = { unit: MINIMUM_TESTS, e2e: MINIMUM_E2E_TESTS };

const reportPath = process.argv[2];
const suite = process.argv[3] ?? "unit";

if (reportPath === undefined) {
  console.error("BŁĄD: verify_run.mjs wymaga ścieżki do raportu JSON vitesta");
  process.exit(2);
}
if (!Object.hasOwn(FLOORS, suite)) {
  console.error(`BŁĄD: nieznana suita '${suite}' — dozwolone: ${Object.keys(FLOORS).join(", ")}`);
  process.exit(2);
}
if (!fs.existsSync(reportPath)) {
  console.error(`BŁĄD: nie ma raportu ${reportPath} — przebieg testów nie zostawił dowodu`);
  process.exit(2);
}

const floor = FLOORS[suite];

let report;
try {
  report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
} catch (error) {
  console.error(`BŁĄD: raport ${reportPath} nie jest poprawnym JSON-em: ${String(error)}`);
  process.exit(2);
}

const total = report.numTotalTests ?? 0;
const passed = report.numPassedTests ?? 0;
const failed = report.numFailedTests ?? 0;
const pending = report.numPendingTests ?? 0;
const todo = report.numTodoTests ?? 0;

const problems = [];
if (total < floor) {
  problems.push(
    `zebrano ${total} testów, a próg suity '${suite}' to ${floor} — "zebrane" nie znaczy ` +
      '"wykonane", a przebieg, który zgubił połowę suity, wychodzi na zero bez ani jednej ' +
      "czerwonej linii",
  );
}
if (passed === 0) {
  problems.push("ani jeden test nie przeszedł — to nie jest wynik pozytywny");
}
if (failed > 0) {
  problems.push(`testów z porażką: ${failed}`);
}
if (pending > 0 || todo > 0) {
  problems.push(
    `pominiętych: ${pending}, todo: ${todo} — pominięty test w podsumowaniu jest ` +
      "nieodróżnialny od testu, który przeszedł",
  );
}

if (problems.length > 0) {
  console.error(`suita dashboardu (${suite}): NIE ZALICZONA`);
  for (const problem of problems) {
    console.error(`  - ${problem}`);
  }
  process.exit(1);
}

console.log(
  `suita dashboardu (${suite}): ${passed}/${total} testów wykonanych, 0 pominiętych, ` +
    `0 porażek, próg ${floor}`,
);
