/**
 * `EntityScreen` is the component every screen of this dashboard renders, and it is where the three
 * states are told apart.
 *
 * The reason this file exists was MEASURED, not suspected. Until 2026-08-31 no test rendered this
 * component at all: the mutation the package's own notes warn about — collapsing "nothing was read"
 * into "the list is empty" — passed the entire suite green, because the test covering emptiness
 * rendered the list component DIRECTLY and no page did that. The guard sat one layer below the code
 * that ships.
 *
 * What this file proves is the rule L5241 point 5 states outright — "brak danych nigdy nie jest
 * zamieniany na zero; jest UNKNOWN z powodem" — enforced in the visual layer:
 *
 *   | `rows`      | screen                            | fact                                  |
 *   | ----------- | --------------------------------- | ------------------------------------- |
 *   | `undefined` | "brak odpowiedzi" + the reason    | nothing was read at all               |
 *   | `[]`        | "brak danych"                     | the read succeeded and found nothing  |
 *   | `[row, …]`  | the table, NO `data-state` marker | there is something to show            |
 */
import { describe, expect, it } from "vitest";

import { EntityScreen, type TableRow } from "../components/entity-screen";
import { linkCell, missingCell, valueCell } from "../lib/format";
import { allElements, renderToDocument } from "./render";

const NO_ANSWER = "brak odpowiedzi";
const EMPTY_ANSWER = "brak danych";

const LEDE = "Zdanie kontekstu wpisane przez test, do znalezienia w markupie.";
const COLUMNS = ["job_id", "status"] as const;

const ROWS: readonly TableRow[] = [
  { key: "job-1", cells: [valueCell("job_id", "job-1"), valueCell("status", "READY")] },
  { key: "job-2", cells: [valueCell("job_id", "job-2"), valueCell("status", "RUNNING")] },
];

function stateMarkers(document_: Document): readonly string[] {
  return allElements(document_)
    .map((element) => element.getAttribute("data-state"))
    .filter((value): value is string => value !== null);
}

function textOf(document_: Document): string {
  return document_.documentElement.textContent ?? "";
}

function screen(rows: readonly TableRow[] | undefined, reason?: string) {
  return renderToDocument(
    <EntityScreen
      columns={COLUMNS}
      heading="Kolejka jobów"
      lede={LEDE}
      reason={reason}
      rows={rows}
      what="Kolejka jobów"
    />,
  );
}

describe("EntityScreen", () => {
  it("a_missing_response_is_not_collapsed_into_an_empty_list", () => {
    // THE test this file exists for. `undefined` and `[]` are two different facts and must reach
    // the operator as two different screens — the same line the gates draw between
    // NIE_URUCHOMIONO and NO_GO. Any `rows ?? []` between this component and `EmptyState` makes
    // the two documents below identical.
    const noAnswer = screen(undefined, "połączenie odrzucone");
    const emptyAnswer = screen([]);

    const noAnswerMarkers = stateMarkers(noAnswer);
    const emptyAnswerMarkers = stateMarkers(emptyAnswer);

    // Count first, compare second: an empty document must never be the thing that satisfies the
    // `toEqual` below.
    expect(noAnswerMarkers.length).toBe(1);
    expect(emptyAnswerMarkers.length).toBe(1);
    expect(noAnswerMarkers).toEqual([NO_ANSWER]);
    expect(emptyAnswerMarkers).toEqual([EMPTY_ANSWER]);
    expect(noAnswerMarkers).not.toEqual(emptyAnswerMarkers);

    // The sentence too, in both directions: each screen says its own headline and NOT the other
    // one. One-directional assertions would pass on a component that printed both.
    expect(textOf(noAnswer)).toContain(NO_ANSWER);
    expect(textOf(noAnswer)).not.toContain(EMPTY_ANSWER);
    expect(textOf(emptyAnswer)).toContain(EMPTY_ANSWER);
    expect(textOf(emptyAnswer)).not.toContain(NO_ANSWER);
  });

  it("neither_empty_screen_renders_a_table_or_a_placeholder_row", () => {
    // Mock rows are forbidden outright in this phase (D-013): nobody should have to guess whether
    // a row on this screen came from PostgreSQL or from a developer's imagination.
    const cases: readonly (readonly TableRow[] | undefined)[] = [undefined, []];
    expect(cases.length).toBe(2);

    for (const rows of cases) {
      const rendered = screen(rows, "powód");
      // Length before the negative predicate: "no rows" is trivially true of a blank document.
      expect(rendered.querySelectorAll("section.state").length).toBe(1);
      expect(stateMarkers(rendered).length).toBe(1);
      expect(rendered.querySelectorAll("table").length).toBe(0);
      expect(rendered.querySelectorAll(".rows__row").length).toBe(0);
    }
  });

  it("a_non_empty_list_renders_one_row_per_entry_and_no_state_marker", () => {
    // Control for the two tests above. A component hardcoding one of the empty screens would
    // satisfy them and be useless the moment a row exists.
    expect(ROWS.length).toBe(2);

    const rendered = screen(ROWS);
    const text = textOf(rendered);

    expect(rendered.querySelectorAll("tbody tr").length).toBe(ROWS.length);
    expect(rendered.querySelectorAll("thead th").length).toBe(COLUMNS.length);
    for (const row of ROWS) {
      expect(text).toContain(row.key);
    }
    expect(stateMarkers(rendered)).toEqual([]);
    expect(text).not.toContain(EMPTY_ANSWER);
    expect(text).not.toContain(NO_ANSWER);
  });

  it("the_heading_and_the_lede_survive_the_component", () => {
    const rendered = screen(ROWS);
    const headings = Array.from(rendered.querySelectorAll("h1"));
    expect(headings.length).toBe(1);
    expect((headings[0]?.textContent ?? "").trim()).toBe("Kolejka jobów");
    expect((rendered.querySelector(".screen__lede")?.textContent ?? "").trim()).toBe(LEDE);
  });

  it("cells_are_placed_by_column_name_not_by_position", () => {
    // A table that trusted array order would silently print the status under `job_id` the day a
    // view reorders its cells. Reversed cells must still land in the right columns.
    const reversed: TableRow = {
      key: "job-3",
      cells: [valueCell("status", "FAILED"), valueCell("job_id", "job-3")],
    };
    const rendered = screen([reversed]);
    const cells = Array.from(rendered.querySelectorAll("tbody td")).map(
      (cell) => (cell.textContent ?? "").trim(),
    );
    expect(cells).toEqual(["job-3", "FAILED"]);
  });

  it("a_row_missing_a_declared_column_throws_instead_of_rendering_a_blank_cell", () => {
    // A blank cell on this dashboard reads as "there is no value", which is a claim about a
    // measurement nobody made. Loud is the correct direction.
    const incomplete: TableRow = { key: "job-4", cells: [valueCell("job_id", "job-4")] };
    expect(() => screen([incomplete])).toThrow(/status/);
    expect(() => screen([incomplete])).toThrow(/job-4/);
  });

  it("a_screen_with_no_columns_throws", () => {
    // A table with no columns renders every row as an empty stripe and asserts nothing about the
    // data behind it — count before predicate, in the component itself.
    expect(() =>
      renderToDocument(
        <EntityScreen columns={[]} heading="X" lede={LEDE} rows={ROWS} what="X" />,
      ),
    ).toThrow(/ani jednej kolumny/);
  });

  it("a_missing_cell_is_marked_in_the_markup_and_not_only_in_its_text", () => {
    // `missing` has to be visible to a machine, not just phrased in Polish: a test (and the
    // stylesheet) must be able to tell an absence from a value that happens to contain "brak".
    const row: TableRow = {
      key: "job-5",
      cells: [valueCell("job_id", "job-5"), missingCell("status", "brak statusu w odpowiedzi")],
    };
    const rendered = screen([row]);

    const marked = Array.from(rendered.querySelectorAll("td[data-missing]"));
    expect(marked.length).toBe(1);
    expect((marked[0]?.textContent ?? "").trim()).toBe("brak statusu w odpowiedzi");
    expect(marked[0]?.className).toContain("rows__cell--missing");
  });

  it("a_link_cell_renders_an_internal_anchor_and_nothing_else_does", () => {
    const row: TableRow = {
      key: "job-6",
      cells: [linkCell("job_id", "job-6", "/jobs/job-6"), valueCell("status", "READY")],
    };
    const rendered = screen([row]);

    const links = Array.from(rendered.querySelectorAll("tbody a"));
    expect(links.length).toBe(1);
    expect(links[0]?.getAttribute("href")).toBe("/jobs/job-6");
  });

  it("the_page_note_is_printed_when_given_and_absent_when_not", () => {
    // "The page filled up" must be sayable, and must not be invented when it is not true — a
    // page that quietly ends is how a reader concludes a row does not exist.
    const withNote = renderToDocument(
      <EntityScreen
        columns={COLUMNS}
        heading="Kolejka jobów"
        lede={LEDE}
        pageNote="Strona zapełniła się do limitu."
        rows={ROWS}
        what="Kolejka jobów"
      />,
    );
    expect(textOf(withNote)).toContain("Strona zapełniła się do limitu.");
    expect(screen(ROWS).querySelectorAll(".screen__pagenote").length).toBe(0);
  });
});
