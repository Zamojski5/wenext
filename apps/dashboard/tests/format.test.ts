/**
 * The cell helpers, and the one thing they must never do: turn an absence into a value.
 *
 * L5241 point 5: "Brak danych nigdy nie jest zamieniany na zero; jest UNKNOWN z powodem." Every
 * assertion here is a case of that sentence — a missing timestamp is not "now", a missing heartbeat
 * is not "0 s temu", an unknown status is not "not terminal".
 *
 * The last test is a cross-file consistency check: the terminal/non-terminal split in
 * `lib/format.ts` has to partition the nine statuses of migration 0003 exactly. A status added to
 * the schema and forgotten here would render as "w toku" forever — the queue's own DoD ("ostatnia
 * próba terminalizuje job zamiast tworzyć nieskończony redispatch", L5214) made invisible on the
 * one screen meant to show it.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  NON_TERMINAL_JOB_STATUSES,
  TERMINAL_JOB_STATUSES,
  ageCell,
  describeAge,
  formatInstant,
  instantCell,
  linkCell,
  missingCell,
  terminalisationMarker,
  valueCell,
} from "../lib/format";

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
const MIGRATION = path.join(REPO_ROOT, "db", "migrations");
const NOW = new Date("2026-08-31T12:00:00.000Z");

describe("cells", () => {
  it("a_value_cell_is_not_marked_missing_and_a_missing_cell_is", () => {
    expect(valueCell("status", "READY").missing).toBeUndefined();
    expect(missingCell("status", "brak w odpowiedzi").missing).toBe(true);
  });

  it("a_missing_cell_without_a_reason_throws", () => {
    // An absence with no stated reason is the silence this repository treats as a false pass. The
    // helper refuses it rather than rendering an empty italic cell.
    expect(() => missingCell("status", "")).toThrow(/bez podanego powodu/);
  });

  it("a_link_cell_refuses_anything_but_an_internal_path", () => {
    expect(linkCell("job_id", "x", "/jobs/x").href).toBe("/jobs/x");
    expect(() => linkCell("job_id", "x", "https://example.test/x")).toThrow(/ścieżki wewnętrzne/);
  });

  it("a_null_timestamp_becomes_a_stated_absence_never_a_zero", () => {
    const cell = instantCell("expires_at", null, "próba nie zapisała expires_at");
    expect(cell.missing).toBe(true);
    expect(cell.value).toBe("próba nie zapisała expires_at");
    // The two things it must not be.
    expect(cell.value).not.toContain("1970");
    expect(cell.value).not.toBe("");
  });

  it("a_null_heartbeat_becomes_a_stated_absence_never_zero_seconds_ago", () => {
    const cell = ageCell("wiek heartbeatu", null, "brak heartbeat_at", NOW);
    expect(cell.missing).toBe(true);
    expect(cell.value).toBe("brak heartbeat_at");
    expect(cell.value).not.toContain("0 s");
  });

  it("an_unparsable_timestamp_is_reported_as_broken_not_as_absent", () => {
    // A value that came back and cannot be parsed is evidence of a real defect. Hiding it as
    // "brak" would erase that evidence; hiding it as a date would invent one.
    expect(formatInstant("to nie jest data")).toContain("nieparsowalna");
    expect(describeAge("to nie jest data", NOW)).toContain("nieparsowalna");
    expect(instantCell("expires_at", "to nie jest data", "brak").missing).toBeUndefined();
  });

  it("timestamps_are_printed_in_utc", () => {
    // The control plane, PostgreSQL and the artefacts all speak UTC. A screen quietly shifting
    // timestamps makes a lease expiry impossible to line up with a log line.
    expect(formatInstant("2026-08-31T12:34:56.000Z")).toBe("2026-08-31 12:34:56Z");
  });

  it("an_age_in_the_future_is_reported_as_such_and_not_clamped_to_zero", () => {
    // A heartbeat timestamped ahead of this process means the two clocks disagree, and with a 15 s
    // heartbeat against a 60 s TTL (D-017) a skew of seconds is not academic. Clamping it to zero
    // would hide the only symptom.
    const future = describeAge("2026-08-31T12:00:30.000Z", NOW);
    expect(future).toContain("W PRZYSZŁOŚCI");
    expect(future).toContain("30 s");
  });

  it("ages_are_phrased_in_seconds_minutes_and_hours", () => {
    expect(describeAge("2026-08-31T11:59:59.000Z", NOW)).toBe("1 s temu");
    expect(describeAge("2026-08-31T11:50:00.000Z", NOW)).toBe("10 min temu");
    expect(describeAge("2026-08-31T04:00:00.000Z", NOW)).toBe("8 h temu");
  });
});

describe("terminalisation", () => {
  it("the_two_status_lists_partition_the_statuses_of_migration_0003", () => {
    const files = fs
      .readdirSync(MIGRATION)
      .filter((name) => name.startsWith("0003") && name.endsWith(".sql"));
    expect(files.length, `nie ma migracji 0003 w ${MIGRATION}`).toBe(1);

    const sql = fs.readFileSync(path.join(MIGRATION, files[0] as string), "utf8");
    const check = /CONSTRAINT jobs_status_is_known CHECK \(status IN \(([\s\S]*?)\)\)/.exec(sql);
    expect(check, "nie znaleziono CHECK-u jobs_status_is_known").not.toBeNull();

    const statuses = Array.from((check?.[1] ?? "").matchAll(/'([A-Z_]+)'/g)).map(
      (match) => match[1] as string,
    );

    // COUNT BEFORE COMPARISON. A regex that matched nothing leaves `statuses` empty, and every set
    // comparison below then holds trivially — `all([]) == True` in its usual disguise.
    expect(statuses.length).toBe(9);

    const declared = [...TERMINAL_JOB_STATUSES, ...NON_TERMINAL_JOB_STATUSES];
    expect(declared.length).toBe(statuses.length);
    expect(new Set(declared).size).toBe(declared.length);
    expect(declared.slice().sort()).toEqual(statuses.slice().sort());
  });

  it("an_unknown_status_is_neither_terminal_nor_running", () => {
    const cell = terminalisationMarker("STATUS_Z_PRZYSZŁOŚCI");
    expect(cell.missing).toBe(true);
    expect(cell.value).toContain("nie jest znany");
  });

  it("every_declared_terminal_status_reads_as_terminal", () => {
    expect(TERMINAL_JOB_STATUSES.length).toBe(3);
    for (const status of TERMINAL_JOB_STATUSES) {
      const cell = terminalisationMarker(status);
      expect(cell.missing, status).toBeUndefined();
      expect(cell.value, status).toContain("TAK");
    }
    expect(NON_TERMINAL_JOB_STATUSES.length).toBe(6);
    for (const status of NON_TERMINAL_JOB_STATUSES) {
      expect(terminalisationMarker(status).value, status).toBe("nie");
    }
  });
});
