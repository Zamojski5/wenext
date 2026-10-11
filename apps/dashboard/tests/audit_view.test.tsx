/**
 * The audit screen hides no kind of event, and the screen itself has to prove it.
 *
 * `api/audit.py` states the danger and this file is the other half of it: "A default `kind` filter
 * is the most comfortable way to build a log nobody can trust — the page returns rows, looks alive,
 * and a whole category is invisible with no error and no alarm. That is the effect of having no
 * audit at all, with none of the visibility."
 *
 * The request is asserted in `tests/control_plane_client.test.ts` (no `kind`, no `since` in the
 * URL). Here the RENDERING is asserted: every event handed to the view reaches the table, and the
 * tally under it names every kind present. A view that dropped a category would have to drop it
 * from both, and both are checked.
 */
import { describe, expect, it } from "vitest";

import { AUDIT_COLUMNS, AuditView } from "../components/views/audit-view";
import { auditEvent } from "./fixtures";
import { renderToDocument } from "./render";

const KINDS = ["OPERATOR_SESSION_OPENED", "JOB_ENQUEUED", "JOB_LEASE_EXPIRED"] as const;

describe("AuditView", () => {
  it("no_kind_of_event_is_dropped_from_the_table_or_from_the_tally", () => {
    expect(KINDS.length).toBe(3);

    const events = KINDS.map((kind, index) => auditEvent({ id: index + 1, kind }));
    const document_ = renderToDocument(<AuditView events={events} />);
    const text = document_.documentElement.textContent ?? "";

    // Count before predicate: with zero rows every "contains" below would be checked against a
    // document that rendered nothing.
    expect(document_.querySelectorAll("tbody tr").length).toBe(KINDS.length);

    for (const kind of KINDS) {
      expect(text, kind).toContain(kind);
      // The tally is the second channel. A filter applied in the view would leave the kind in
      // neither, but a filter applied only to the tally would leave it in the table alone — so
      // both are named explicitly.
      // The tally is fine print behind "Szczegóły strony" since the second redesign iteration —
      // still in the document (the e2e suite asserts it), just not on the surface.
      expect(document_.querySelector(".screen__pagenote--quiet")?.textContent ?? "", kind).toContain(kind);
    }
  });

  it("the_tally_counts_repetitions_rather_than_collapsing_them", () => {
    const events = [
      auditEvent({ id: 1, kind: "JOB_ENQUEUED" }),
      auditEvent({ id: 2, kind: "JOB_ENQUEUED" }),
      auditEvent({ id: 3, kind: "OPERATOR_SESSION_OPENED" }),
    ];
    const document_ = renderToDocument(<AuditView events={events} />);
    const note = document_.querySelector(".screen__pagenote--quiet")?.textContent ?? "";

    expect(note).toContain("JOB_ENQUEUED: 2");
    expect(note).toContain("OPERATOR_SESSION_OPENED: 1");
  });

  it("the_view_does_not_reorder_what_the_control_plane_sent", () => {
    // `ORDER BY occurred_at DESC, id DESC` happens in SQL, and the tie-break exists because
    // `occurred_at` defaults to the TRANSACTION's start time — two rows written by one command
    // share a timestamp. A second sort here would be a second opinion about which came first.
    const events = [
      auditEvent({ id: 30, occurred_at: "2026-08-31T11:00:00.000Z" }),
      auditEvent({ id: 10, occurred_at: "2026-08-31T11:00:00.000Z" }),
      auditEvent({ id: 20, occurred_at: "2026-08-31T11:00:00.000Z" }),
    ];
    const document_ = renderToDocument(<AuditView events={events} />);
    const ids = Array.from(document_.querySelectorAll("tbody tr")).map(
      (row) => (row.querySelector("td")?.textContent ?? "").trim(),
    );

    expect(ids).toEqual(["30", "10", "20"]);
  });

  it("a_null_column_is_marked_as_absent_rather_than_rendered_blank", () => {
    const document_ = renderToDocument(
      <AuditView events={[auditEvent({ subject_ref: null, actor_ref: null, workspace_id: null })]} />,
    );
    const marked = Array.from(document_.querySelectorAll("td[data-missing]"));

    // Three nullable columns were given as NULL, so exactly three cells must be marked. A blank
    // cell would read as "no value" without saying that anybody looked.
    expect(marked.length).toBe(3);
    for (const cell of marked) {
      expect((cell.textContent ?? "").trim().length).toBeGreaterThan(0);
    }
  });

  it("a_long_payload_is_cut_and_says_that_it_was_cut", () => {
    const long = { blob: "x".repeat(2000) };
    const document_ = renderToDocument(<AuditView events={[auditEvent({ payload: long })]} />);
    const text = document_.documentElement.textContent ?? "";

    expect(text).toContain("SKRÓCONE");
    // The full length is stated, so the reader knows how much was hidden rather than guessing.
    expect(text).toMatch(/pełne \d+ znaków/);
  });

  it("the_columns_are_the_declared_ones_in_order", () => {
    const document_ = renderToDocument(<AuditView events={[auditEvent()]} />);
    expect(
      Array.from(document_.querySelectorAll("thead th")).map((th) => (th.textContent ?? "").trim()),
    ).toEqual(AUDIT_COLUMNS.slice());
    expect(AUDIT_COLUMNS).toContain("kind");
    expect(AUDIT_COLUMNS).toContain("occurred_at");
  });
});
