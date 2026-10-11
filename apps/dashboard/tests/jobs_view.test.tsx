/**
 * The queue screen: the columns the operator needs, and the three ways a lease cell can be honest.
 *
 * The lease columns are the reason this file is long. `GET /api/jobs` does not carry `lease_owner`,
 * `expires_at` or `heartbeat_at` — that is measured, in `api/jobs.py::_job_payload` — so
 * `app/jobs/page.tsx` reads each job's detail to fill them, and a per-row read can fail on its own.
 * Three outcomes, three renderings, and NONE of them may impersonate another:
 *
 *   | the row's detail read      | the three lease cells                    |
 *   | -------------------------- | ---------------------------------------- |
 *   | succeeded, attempts exist  | owner, expiry, heartbeat age             |
 *   | succeeded, no attempts     | "brak prób", marked as missing           |
 *   | failed                     | "nie odczytano: <reason>", marked missing|
 *
 * "There is no lease" and "I do not know whether there is a lease" are different facts about a
 * queue whose reaper takes leases away from dead workers (D-017: TTL 60 s, heartbeat 15 s). The
 * second dressed as the first is how an operator concludes a worker died when nothing was measured.
 */
import { describe, expect, it } from "vitest";

import { JOBS_COLUMNS, JobsView, type JobsRow } from "../components/views/jobs-view";
import { NOW, ONE_MINUTE_AGO, SOON, jobSummary } from "./fixtures";
import { renderToDocument } from "./render";

function cellsByColumn(document_: Document): Record<string, { text: string; missing: string | null }> {
  const headers = Array.from(document_.querySelectorAll("thead th")).map(
    (header) => (header.textContent ?? "").trim(),
  );
  const cells = Array.from(document_.querySelectorAll("tbody tr td"));
  expect(cells.length).toBe(headers.length);

  const byColumn: Record<string, { text: string; missing: string | null }> = {};
  headers.forEach((header, index) => {
    const cell = cells[index];
    byColumn[header] = {
      text: (cell?.textContent ?? "").trim(),
      missing: cell?.getAttribute("data-missing") ?? null,
    };
  });
  return byColumn;
}

function render(rows: readonly JobsRow[], truncated = false): Document {
  return renderToDocument(<JobsView now={NOW} rows={rows} truncated={truncated} />);
}

describe("JobsView", () => {
  it("the_queue_declares_every_column_the_phase_asks_for", () => {
    // The columns are named in the package brief: job_id, job_type, status, attempt/attempt_limit,
    // lease_owner, lease_expires_at, heartbeat age, not_before and the terminalisation marker.
    // Written out here so dropping one is a conscious edit to this test.
    const required = [
      "job_id",
      "job_type",
      "status",
      "próby",
      "terminalny",
      "lease_owner (ostatnia próba)",
      "lease_expires_at",
      "wiek heartbeatu",
      "not_before",
    ];
    expect(required.length).toBe(9);
    for (const column of required) {
      expect(JOBS_COLUMNS, column).toContain(column);
    }

    // And they actually reach the markup, in the declared order.
    const document_ = render([
      { job: jobSummary(), lease: { kind: "read", latestAttempt: null } },
    ]);
    expect(
      Array.from(document_.querySelectorAll("thead th")).map((th) => (th.textContent ?? "").trim()),
    ).toEqual(JOBS_COLUMNS.slice());
  });

  it("a_row_with_a_live_lease_prints_the_owner_the_expiry_and_the_heartbeat_age", () => {
    const document_ = render([
      {
        job: jobSummary({ status: "RUNNING", holds_live_lease: true }),
        lease: {
          kind: "read",
          latestAttempt: {
            leaseOwner: "owner-abc",
            expiresAt: SOON,
            heartbeatAt: ONE_MINUTE_AGO,
            leaseGeneration: 7,
          },
        },
      },
    ]);
    const cells = cellsByColumn(document_);

    expect(cells["lease_owner (ostatnia próba)"]?.text).toContain("owner-abc");
    expect(cells["lease_owner (ostatnia próba)"]?.text).toContain("gen. 7");
    expect(cells["lease_owner (ostatnia próba)"]?.missing).toBeNull();
    expect(cells["lease_expires_at"]?.text).toContain("2026-08-31 12:01");
    // 12:00:00 minus 11:59:00 — exactly 60 s, computed against the injected clock, so this
    // assertion does not depend on when the suite ran.
    expect(cells["wiek heartbeatu"]?.text).toBe("60 s temu");
    expect(cells["lease żywy"]?.text).toBe("TAK");
  });

  it("a_job_with_no_attempts_says_so_and_does_not_print_an_empty_lease", () => {
    const document_ = render([
      { job: jobSummary({ status: "READY", holds_live_lease: false }), lease: { kind: "read", latestAttempt: null } },
    ]);
    const cells = cellsByColumn(document_);

    for (const column of ["lease_owner (ostatnia próba)", "lease_expires_at", "wiek heartbeatu"]) {
      expect(cells[column]?.missing, column).toBe("tak");
      expect(cells[column]?.text, column).toContain("brak prób");
      // The one thing it must never be: a zero. "0 s temu" would say the worker checked in now.
      expect(cells[column]?.text, column).not.toContain("0 s temu");
    }
    expect(cells["lease żywy"]?.text).toBe("nie");
  });

  it("an_attempt_without_a_heartbeat_says_so_rather_than_printing_zero_seconds_ago", () => {
    // A GAP FOUND BY MUTATION TESTING, not by reading the code. Changing `ageCell` to return
    // "0 s temu" for a null timestamp failed `tests/format.test.ts` and passed THIS file, because
    // every case here reached the missing-heartbeat cell through the "no attempts at all" branch —
    // which uses `missingCell` directly and never calls `ageCell`. An attempt that EXISTS while its
    // `heartbeat_at` is NULL (`job_attempts.heartbeat_at` is nullable in migration 0003, and a
    // claim that never renewed leaves it that way) was rendered by code no view test covered.
    const document_ = render([
      {
        job: jobSummary({ status: "RUNNING" }),
        lease: {
          kind: "read",
          latestAttempt: {
            leaseOwner: "owner-xyz",
            expiresAt: SOON,
            heartbeatAt: null,
            leaseGeneration: 1,
          },
        },
      },
    ]);
    const cell = cellsByColumn(document_)["wiek heartbeatu"];

    expect(cell?.missing).toBe("tak");
    expect(cell?.text).toContain("nie zapisała heartbeat_at");
    // The one rendering it must never have: a zero, which reads as "the worker checked in now".
    expect(cell?.text).not.toContain("0 s temu");
    // The other two cells still hold their values — a broken heartbeat is not a broken lease.
    expect(cellsByColumn(document_)["lease_owner (ostatnia próba)"]?.missing).toBeNull();
    expect(cellsByColumn(document_)["lease_expires_at"]?.missing).toBeNull();
  });

  it("a_failed_detail_read_says_nie_odczytano_with_the_reason_not_brak", () => {
    // THE distinction this view exists for. The three cells must carry the reason, be marked as
    // missing, and must NOT read like "there is no lease".
    const reason = "control plane odpowiedział 503";
    const document_ = render([
      { job: jobSummary(), lease: { kind: "unread", reason } },
    ]);
    const cells = cellsByColumn(document_);

    for (const column of ["lease_owner (ostatnia próba)", "lease_expires_at", "wiek heartbeatu"]) {
      expect(cells[column]?.missing, column).toBe("tak");
      expect(cells[column]?.text, column).toContain("nie odczytano");
      expect(cells[column]?.text, column).toContain(reason);
      expect(cells[column]?.text, column).not.toContain("brak prób");
    }
  });

  it("the_attempt_counter_shows_the_limit_next_to_the_count", () => {
    // The DoD sentence is about the PAIR: "lease expiry respektuje attempt limit; ostatnia próba
    // terminalizuje job" (L5214). One number without the other says nothing about how close the
    // job is to the end.
    const document_ = render([
      {
        job: jobSummary({ attempt_count: 4, attempt_limit: 5 }),
        lease: { kind: "read", latestAttempt: null },
      },
    ]);
    expect(cellsByColumn(document_)["próby"]?.text).toBe("4 / 5");
  });

  it("the_terminalisation_marker_distinguishes_terminal_running_and_unknown_statuses", () => {
    const cases: readonly (readonly [string, string])[] = [
      ["SUCCEEDED", "TAK"],
      ["FAILED", "TAK"],
      ["CANCELLED", "TAK"],
      ["READY", "nie"],
      ["RUNNING", "nie"],
      ["WAITING_RETRY", "nie"],
    ];
    expect(cases.length).toBe(6);

    for (const [status, expected] of cases) {
      const document_ = render([
        { job: jobSummary({ status }), lease: { kind: "read", latestAttempt: null } },
      ]);
      expect(cellsByColumn(document_)["terminalny"]?.text, status).toContain(expected);
    }

    // A status this screen does not know is NEITHER — it is reported as unrecognised. Defaulting
    // it to "nie" would tell the operator the queue is still working on something it may have
    // abandoned.
    const strange = render([
      { job: jobSummary({ status: "COŚ_NOWEGO" }), lease: { kind: "read", latestAttempt: null } },
    ]);
    const cell = cellsByColumn(strange)["terminalny"];
    expect(cell?.missing).toBe("tak");
    expect(cell?.text).toContain("nie jest znany");
  });

  it("the_job_id_links_to_the_detail_screen_and_it_is_the_only_link_in_the_row", () => {
    const job = jobSummary({ job_id: "abc-123" });
    const document_ = render([{ job, lease: { kind: "read", latestAttempt: null } }]);
    const links = Array.from(document_.querySelectorAll("tbody a"));

    expect(links.length).toBe(1);
    expect(links[0]?.getAttribute("href")).toBe("/jobs/abc-123");
    expect((links[0]?.textContent ?? "").trim()).toBe("abc-123");
  });

  it("a_full_page_says_there_may_be_more_and_never_says_this_is_everything", () => {
    const rows: readonly JobsRow[] = [
      { job: jobSummary(), lease: { kind: "read", latestAttempt: null } },
    ];
    const truncated = render(rows, true);
    const complete = render(rows, false);

    expect(truncated.documentElement.textContent ?? "").toContain("MOŻE BYĆ WIĘCEJ");
    expect(complete.querySelectorAll(".screen__pagenote").length).toBe(0);
  });
});
