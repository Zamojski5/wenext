/**
 * L2673 enforced IN THE INTERFACE, over all eight states, plus the proof that the eight are the
 * real eight.
 *
 * THE RULE. After `SUBMIT_STARTED` the side effect may already have happened on the platform, so
 * there is no automatic retry from there; `CONFIRMED` and `UNKNOWN` are in the same set
 * (`contracts.enums.STATES_FORBIDDING_AUTOMATIC_RETRY`, L2673 with L4560 and L5335). A dashboard
 * offering "ponów" would let a human do by hand exactly what the state machine forbids the machine
 * to do — the rule broken through the interface, with the state machine still intact and still
 * green. That is the failure this file exists to make impossible.
 *
 * WHY THE PARAMETRISATION IS NOT DECORATION. This dashboard has no retry action for ANY state, so
 * "no affordance for these three" would pass on a screen with no affordances anywhere — a test with
 * zero detection power in the dimension it claims to measure. The file therefore measures TWO
 * things per state, and only the pair has teeth:
 *
 *   1. zero action affordances, for every one of the eight states. A button gated on
 *      `may_retry_automatically === true` would slip past an assertion made only about the three
 *      forbidding states; it fails here on the other five.
 *   2. the VERDICT the screen prints for that state. A screen that recomputed the rule locally, or
 *      inverted it, or defaulted `null` to "allowed", changes this value — and that is the half
 *      that ties the interface to `contracts.may_retry_automatically`.
 *
 * WHY THE TEST CARRIES ITS OWN COPY OF THE STATE LISTS. It has to: the flag under test arrives from
 * the control plane, so the test must be able to build the payload the control plane WOULD send,
 * and to do that it needs the rule. The copy is then checked against the Python contracts by
 * `the_state_lists_match_the_python_contracts` — which is why this is a cross-language consistency
 * test and not a second source of truth. The SHIPPED code holds no copy: `grep -rn
 * SUBMIT_STARTED app components lib` finds it only in prose.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { JobDetailView, RETRY_ALLOWED, RETRY_FORBIDDEN, RETRY_UNKNOWN } from "../components/views/job-detail-view";
import { NOW, attempt, jobDetail } from "./fixtures";
import { renderToDocument } from "./render";
import { FORBIDDEN_ACTION_TEXTS, retryAffordances } from "./success-indicators";

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const ENUMS_PY = path.join(REPO_ROOT, "contracts", "src", "community_os_contracts", "enums.py");

/** All eight values of `ExecutionAttemptState` (L2601-2605), written out. */
const ALL_EXECUTION_ATTEMPT_STATES: readonly string[] = [
  "PREPARED",
  "DISPATCHED",
  "SUBMIT_STARTED",
  "CONFIRMED",
  "UNKNOWN",
  "FAILED_BEFORE_SIDE_EFFECT",
  "CAPABILITY_BLOCKED",
  "IDENTITY_BLOCKED",
];

/** The three after which no automatic retry is allowed. */
const STATES_FORBIDDING_AUTOMATIC_RETRY: readonly string[] = [
  "SUBMIT_STARTED",
  "CONFIRMED",
  "UNKNOWN",
];

/** What the control plane sends for an attempt in this state. THE RULE, applied once, here. */
function mayRetry(state: string): boolean {
  return !STATES_FORBIDDING_AUTOMATIC_RETRY.includes(state);
}

function screenFor(state: string): Document {
  return renderToDocument(
    <JobDetailView
      detail={jobDetail({
        attempts: [
          attempt({
            status: "FAILED",
            completed_at: NOW.toISOString(),
            failure_class: state,
            execution_attempt_state: state,
            may_retry_automatically: mayRetry(state),
          }),
        ],
      })}
      jobId="job-under-test"
      now={NOW}
    />,
  );
}

function verdictOf(document_: Document): string | null {
  const markers = Array.from(document_.querySelectorAll("[data-retry-verdict]"));
  expect(markers.length).toBe(1);
  return markers[0]?.getAttribute("data-retry-verdict") ?? null;
}

describe("the retry affordance", () => {
  it("the_state_lists_match_the_python_contracts", () => {
    // The parity check that makes the two lists above legitimate rather than a second opinion.
    const source = fs.readFileSync(ENUMS_PY, "utf8");

    const enumBlock = /class ExecutionAttemptState\(StrEnum\):([\s\S]*?)\n\n\n/.exec(source);
    expect(enumBlock, `nie znaleziono ExecutionAttemptState w ${ENUMS_PY}`).not.toBeNull();
    const members = Array.from(
      (enumBlock?.[1] ?? "").matchAll(/^ {4}[A-Z_]+ = "([A-Z_]+)"$/gm),
    ).map((match) => match[1]);

    const setBlock =
      /STATES_FORBIDDING_AUTOMATIC_RETRY[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/.exec(source);
    expect(setBlock, "nie znaleziono STATES_FORBIDDING_AUTOMATIC_RETRY").not.toBeNull();
    const forbidding = Array.from(
      (setBlock?.[1] ?? "").matchAll(/ExecutionAttemptState\.([A-Z_]+)/g),
    ).map((match) => match[1]);

    // COUNT BEFORE COMPARISON. A regex that matched nothing would leave both arrays empty, and
    // `[].sort()` equals `[].sort()` — a green test proving that two files were not read.
    expect(members.length).toBe(8);
    expect(forbidding.length).toBe(3);

    expect(members.slice().sort()).toEqual(ALL_EXECUTION_ATTEMPT_STATES.slice().sort());
    expect(forbidding.slice().sort()).toEqual(STATES_FORBIDDING_AUTOMATIC_RETRY.slice().sort());
  });

  it("the_affordance_scanner_actually_detects_an_action", () => {
    // A scanner nobody has seen catch anything is not a scanner. Two shapes, because a screen can
    // grow either: a real button, and a link dressed as one.
    const withButton = renderToDocument(<button type="button">Ponów próbę</button>);
    const withLink = renderToDocument(<a href="/jobs/x/retry">Ponów</a>);

    expect(retryAffordances(withButton).length).toBeGreaterThanOrEqual(1);
    expect(retryAffordances(withLink).length).toBeGreaterThanOrEqual(1);
    expect(FORBIDDEN_ACTION_TEXTS.length).toBeGreaterThanOrEqual(8);
  });

  it("no_retry_affordance_for_states_that_forbid_automatic_retry", () => {
    // Parametrised over ALL EIGHT states — see the module docstring for why the three forbidding
    // ones alone would be a test with no detection power.
    expect(ALL_EXECUTION_ATTEMPT_STATES.length).toBe(8);
    expect(STATES_FORBIDDING_AUTOMATIC_RETRY.length).toBe(3);

    for (const state of ALL_EXECUTION_ATTEMPT_STATES) {
      const document_ = screenFor(state);

      // The screen has to have rendered before "it has no button" means anything: a component
      // that threw and produced nothing would satisfy every negative assertion below.
      expect(document_.querySelectorAll("tbody tr").length, state).toBe(1);
      expect(document_.documentElement.textContent ?? "", state).toContain(state);

      expect(retryAffordances(document_), `${state}: ${JSON.stringify(retryAffordances(document_))}`).toEqual([]);

      // And the verdict, which is the half that ties this screen to the contracts. The three
      // forbidding states must print ZABRONIONE; the other five DOZWOLONE.
      const expected = STATES_FORBIDDING_AUTOMATIC_RETRY.includes(state)
        ? RETRY_FORBIDDEN
        : RETRY_ALLOWED;
      expect(verdictOf(document_), state).toBe(expected);
    }
  });

  it("the_verdict_for_an_unrecognised_outcome_is_unknown_and_never_permission", () => {
    // `may_retry_automatically: null` is what the control plane sends for an attempt whose
    // `failure_class` is not an `ExecutionAttempt` state. Absence of evidence is NIE_URUCHOMIONO,
    // not GO (docs/KONWENCJE.md) — so the screen must say NIEZNANE, and above all must not say
    // DOZWOLONE. This is the direction the mistake would run in.
    const document_ = renderToDocument(
      <JobDetailView
        detail={jobDetail({
          attempts: [
            attempt({
              status: "FAILED",
              completed_at: NOW.toISOString(),
              failure_class: "COŚ_CZEGO_NIKT_NIE_ZNA",
              execution_attempt_state: null,
              may_retry_automatically: null,
            }),
          ],
        })}
        jobId="job-unknown-outcome"
        now={NOW}
      />,
    );

    expect(verdictOf(document_)).toBe(RETRY_UNKNOWN);
    const text = document_.documentElement.textContent ?? "";
    expect(text).toContain(RETRY_UNKNOWN);
    expect(text).not.toContain(RETRY_ALLOWED);
    expect(retryAffordances(document_)).toEqual([]);
  });

  it("a_job_with_no_attempts_says_so_instead_of_allowing_a_retry", () => {
    // The fourth verdict. "No attempts" is not "retry allowed", and an empty attempt list must not
    // fall through to the permissive branch — `all([])` in the shape it would take here.
    const document_ = renderToDocument(
      <JobDetailView detail={jobDetail({ attempts: [] })} jobId="job-fresh" now={NOW} />,
    );

    expect(verdictOf(document_)).toBe("BRAK PRÓB");
    const text = document_.documentElement.textContent ?? "";
    expect(text).not.toContain(RETRY_ALLOWED);
    expect(retryAffordances(document_)).toEqual([]);
  });

  it("every_attempt_row_prints_the_control_planes_flag_and_marks_unknown_as_missing", () => {
    // Per-attempt, not only for the current state: an operator reading a five-attempt history has
    // to see which of them were retryable. The UNKNOWN cell is marked `data-missing` so an absence
    // of verdict cannot be skimmed as a verdict.
    const document_ = renderToDocument(
      <JobDetailView
        detail={jobDetail({
          attempts: [
            attempt({
              attempt_id: "a-1",
              attempt_no: 1,
              status: "FAILED",
              completed_at: NOW.toISOString(),
              failure_class: "FAILED_BEFORE_SIDE_EFFECT",
              execution_attempt_state: "FAILED_BEFORE_SIDE_EFFECT",
              may_retry_automatically: true,
            }),
            attempt({
              attempt_id: "a-2",
              attempt_no: 2,
              status: "FAILED",
              completed_at: NOW.toISOString(),
              failure_class: "SUBMIT_STARTED",
              execution_attempt_state: "SUBMIT_STARTED",
              may_retry_automatically: false,
            }),
            attempt({
              attempt_id: "a-3",
              attempt_no: 3,
              status: "FAILED",
              completed_at: NOW.toISOString(),
              failure_class: "NIE_ZNANA_KLASA",
              execution_attempt_state: null,
              may_retry_automatically: null,
            }),
          ],
        })}
        jobId="job-history"
        now={NOW}
      />,
    );

    const rows = Array.from(document_.querySelectorAll("tbody tr"));
    expect(rows.length).toBe(3);

    const lastColumn = rows.map((row) => {
      const cells = Array.from(row.querySelectorAll("td"));
      return {
        text: (cells[cells.length - 1]?.textContent ?? "").trim(),
        missing: cells[cells.length - 1]?.getAttribute("data-missing"),
      };
    });

    expect(lastColumn[0]?.text).toBe(RETRY_ALLOWED);
    expect(lastColumn[0]?.missing).toBeNull();
    expect(lastColumn[1]?.text).toBe(RETRY_FORBIDDEN);
    expect(lastColumn[1]?.missing).toBeNull();
    expect(lastColumn[2]?.text).toContain(RETRY_UNKNOWN);
    expect(lastColumn[2]?.missing).toBe("tak");

    // The verdict for the CURRENT state is the LAST attempt's, not the first one's — a screen
    // reading attempt 1 would report DOZWOLONE for a job whose latest outcome is UNKNOWN.
    expect(verdictOf(document_)).toBe(RETRY_UNKNOWN);
  });
});
