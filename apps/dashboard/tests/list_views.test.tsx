/**
 * The three list views of this dashboard, each asked the same two questions.
 *
 * WHY THREE AND WHICH THREE. The panel has exactly three screens that render a list of rows read
 * from PostgreSQL: the queue (`JobsView`), the audit log (`AuditView`) and the attempt history on
 * the job detail (`JobDetailView`). `/`, `/transitions` and `/approvals` are signposts — they read
 * nothing and therefore render no list, which is checked separately in `tests/signposts.test.tsx`
 * (a screen that measures nothing must not be able to say "brak danych" either).
 *
 * THE TWO QUESTIONS, asked of every one of the three:
 *
 *   1. an empty answer says "brak danych" and carries NO success indicator — no tick, no green, no
 *      "0 błędów". Through most of E1.F1 this is what these screens look like (D-013), so a
 *      success accent here would be the phase's default state reported as a pass. That is
 *      `all([]) == True` drawn in HTML (D-011);
 *   2. a FAILED read says "brak odpowiedzi" and prints the reason — never "brak danych". Collapsing
 *      the two is how a broken read starts looking like a quiet Tuesday (L5241 point 5).
 *
 * THE LIST OF VIEWS IS WRITTEN OUT AND ITS LENGTH IS ASSERTED. A list of screens computed from the
 * routes could quietly become empty, and then this whole file passes while testing nothing — the
 * exact shape of failure the tests here exist to catch.
 */
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { AuditView } from "../components/views/audit-view";
import { JobDetailView } from "../components/views/job-detail-view";
import { JobsView } from "../components/views/jobs-view";
import { NOW, auditEvent, jobDetail, jobSummary } from "./fixtures";
import { allElements, renderToDocument } from "./render";
import {
  FORBIDDEN_SUCCESS_CLASS_PARTS,
  FORBIDDEN_SUCCESS_ICONS,
  FORBIDDEN_SUCCESS_TEXTS,
  successIndicators,
} from "./success-indicators";

const NO_DATA = "brak danych";
const NO_ANSWER = "brak odpowiedzi";
const REASON = "control plane odrzucił połączenie na 127.0.0.1:8000";

interface ListView {
  readonly name: string;
  /** The screen after a read that returned nothing. */
  readonly empty: () => ReactElement;
  /** The screen after a read that did not happen. */
  readonly failed: () => ReactElement;
  /** The screen with at least one row, as a control. */
  readonly filled: () => ReactElement;
}

const LIST_VIEWS: readonly ListView[] = [
  {
    name: "Kolejka jobów",
    empty: () => <JobsView now={NOW} rows={[]} />,
    failed: () => <JobsView now={NOW} reason={REASON} rows={undefined} />,
    filled: () => (
      <JobsView
        now={NOW}
        rows={[{ job: jobSummary(), lease: { kind: "read", latestAttempt: null } }]}
      />
    ),
  },
  {
    name: "Audit events",
    empty: () => <AuditView events={[]} />,
    failed: () => <AuditView events={undefined} reason={REASON} />,
    filled: () => <AuditView events={[auditEvent()]} />,
  },
  {
    name: "Próby joba (ekran szczegółów)",
    empty: () => (
      <JobDetailView detail={jobDetail({ attempts: [] })} jobId="job-empty" now={NOW} />
    ),
    failed: () => (
      <JobDetailView detail={undefined} jobId="job-broken" now={NOW} reason={REASON} />
    ),
    filled: () => <JobDetailView detail={jobDetail()} jobId="job-full" now={NOW} />,
  },
];

function textOf(document_: Document): string {
  return document_.documentElement.textContent ?? "";
}

function stateMarkers(document_: Document): readonly string[] {
  return allElements(document_)
    .map((element) => element.getAttribute("data-state"))
    .filter((value): value is string => value !== null);
}

describe("the three list views", () => {
  it("there_are_exactly_three_list_views_and_the_forbidden_lists_are_not_empty", () => {
    // Count before predicate, twice over: the views under test and the vocabulary they are tested
    // against. Either list going empty turns every assertion below into a tautology.
    expect(LIST_VIEWS.length).toBe(3);
    expect(new Set(LIST_VIEWS.map((view) => view.name)).size).toBe(3);
    expect(FORBIDDEN_SUCCESS_TEXTS.length).toBeGreaterThanOrEqual(10);
    expect(FORBIDDEN_SUCCESS_CLASS_PARTS.length).toBeGreaterThanOrEqual(10);
    expect(FORBIDDEN_SUCCESS_ICONS.length).toBeGreaterThanOrEqual(5);
  });

  it("an_empty_list_says_no_data_and_shows_no_success_indicator", () => {
    expect(LIST_VIEWS.length).toBe(3);

    for (const view of LIST_VIEWS) {
      const rendered = view.empty();
      const document_ = renderToDocument(rendered);
      const text = textOf(document_);

      // The document has to exist before "it contains no success indicator" means anything.
      expect(text.length, view.name).toBeGreaterThan(0);
      expect(allElements(document_).length, view.name).toBeGreaterThan(0);

      expect(text, view.name).toContain(NO_DATA);
      expect(text, view.name).not.toContain(NO_ANSWER);
      expect(stateMarkers(document_), view.name).toEqual([NO_DATA]);
      expect(document_.querySelectorAll("table").length, view.name).toBe(0);

      // The list of violations, printed in full when it is not empty — a bare `toEqual([])` on a
      // failure tells the reader nothing about which channel lied.
      expect(successIndicators(document_), `${view.name}: ${JSON.stringify(successIndicators(document_))}`).toEqual([]);
    }
  });

  it("a_failed_read_says_no_answer_with_the_reason_and_never_says_no_data", () => {
    expect(LIST_VIEWS.length).toBe(3);

    for (const view of LIST_VIEWS) {
      const document_ = renderToDocument(view.failed());
      const text = textOf(document_);

      expect(text, view.name).toContain(NO_ANSWER);
      expect(text, view.name).not.toContain(NO_DATA);
      expect(text, view.name).toContain(REASON);
      expect(stateMarkers(document_), view.name).toEqual([NO_ANSWER]);
      expect(successIndicators(document_), view.name).toEqual([]);
    }
  });

  it("a_view_with_rows_renders_a_table_and_neither_empty_headline", () => {
    // Control for both tests above: a view hardcoding one of the empty screens would satisfy them
    // and be useless the moment a row exists.
    expect(LIST_VIEWS.length).toBe(3);

    for (const view of LIST_VIEWS) {
      const document_ = renderToDocument(view.filled());
      const text = textOf(document_);

      expect(document_.querySelectorAll("table").length, view.name).toBe(1);
      expect(document_.querySelectorAll("tbody tr").length, view.name).toBeGreaterThanOrEqual(1);
      expect(text, view.name).not.toContain(NO_DATA);
      expect(text, view.name).not.toContain(NO_ANSWER);
      expect(stateMarkers(document_), view.name).toEqual([]);
    }
  });

  it("every_list_view_declares_a_heading_and_a_lede_that_says_where_the_rows_come_from", () => {
    expect(LIST_VIEWS.length).toBe(3);

    for (const view of LIST_VIEWS) {
      const document_ = renderToDocument(view.empty());
      const headings = Array.from(document_.querySelectorAll("h1"));
      expect(headings.length, view.name).toBe(1);
      expect((headings[0]?.textContent ?? "").trim().length, view.name).toBeGreaterThan(0);

      const lede = (document_.querySelector(".screen__lede")?.textContent ?? "").trim();
      expect(lede.length, view.name).toBeGreaterThan(40);
    }
  });
});
