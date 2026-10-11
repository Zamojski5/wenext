import type { ReactNode } from "react";

import type { Cell } from "../lib/format";
import { EmptyState } from "./empty-state";

/**
 * One screen: heading, one sentence of context ONE CLICK AWAY, whatever the caller puts in front,
 * and ONE table in a card — or an honest statement that there is no table to draw.
 *
 * THIS FILE IS WHERE THE THREE STATES ARE TOLD APART, and it is the file where a mutation is least
 * visible. Every list screen of this dashboard renders through here, and until 2026-08-31 nothing
 * rendered it at all: writing `rows ?? []` below passed the entire suite green, because the test
 * covering emptiness rendered the list component DIRECTLY and no page did that. That is why the
 * discrimination now lives in this component rather than one layer down, and why
 * `tests/entity_screen.test.tsx` renders this component and every view that uses it.
 *
 *   | `rows`      | screen                          | fact                                    |
 *   | ----------- | ------------------------------- | --------------------------------------- |
 *   | `undefined` | "brak odpowiedzi" + the reason  | nothing was read                        |
 *   | `[]`        | "brak danych"                   | the read succeeded and found nothing    |
 *   | `[row, …]`  | the table                       | there is something to show              |
 *
 * `rows` IS TESTED FOR NOT-AN-ARRAY FIRST. `(rows ?? []).length === 0` would turn a failed read
 * into a tidy empty screen — `all([]) == True` in the visual layer (D-011, L5241 point 5).
 *
 * A ROW THAT DOES NOT CARRY A DECLARED COLUMN THROWS. The alternative is a silently blank cell,
 * and a blank cell on this dashboard reads as "there is no value" — a claim about a measurement
 * that was never made. Loud is the correct direction here.
 *
 * CLICKING, NOT READING (docs/DASHBOARD-DESIGN.md §1, zasada nr 1). The lede sits inside a
 * collapsed "Co tu widzę" — still `.screen__lede`, still the whole sentence, one click away. A
 * list view may ask for its table COLLAPSED (`tableCollapsed`): the queue and the audit log put
 * human number tiles on the surface (their `children`) and the technical columns under "Pokaż
 * tabelę". The table is in the DOM either way — the suites and e2e read it there — and the job
 * detail keeps it open, because there the table is the point.
 *
 * PRESENTATION: a table of seven or more columns marks its card `data-wide="tak"`, and the
 * stylesheet answers with a denser grid and a "scroll sideways" line on laptop widths. A cell
 * with a `tone` is drawn as a pill, the text untouched; every `<td>` carries `data-column` so the
 * stylesheet can set identifiers, hashes and timestamps in monospace WITHOUT this component
 * knowing which columns those are. A missing cell keeps `data-missing` and `.rows__cell--missing`.
 */
export interface TableRow {
  /** Stable key. Use the entity's identifier, never the array index. */
  readonly key: string;
  readonly cells: readonly Cell[];
}

interface EntityScreenProps {
  /** Operator-facing heading. List views take it from `lib/routes.ts` via `labelOf`. */
  readonly heading: string;
  /** One sentence, operator-facing (Polish, D-010): what this screen shows and where it is from. */
  readonly lede: string;
  /** Subject of the empty state, e.g. "Kolejka jobów". */
  readonly what: string;
  /** Column headers, in order. The table renders exactly these. */
  readonly columns: readonly string[];
  /** `undefined` — the read failed. `[]` — measured empty. Never defaulted. */
  readonly rows: readonly TableRow[] | undefined;
  /** Why the read failed. Passed straight to `EmptyState`; ignored when `rows` is an array. */
  readonly reason?: string;
  /**
   * What the control plane said about the PAGE, e.g. that it filled up and there may be more.
   * Printed under the table. Never phrased as "this is everything".
   */
  readonly pageNote?: string;
  /**
   * A sentence that must be IN the document but not on the surface — the audit screen's tally of
   * event kinds, which the e2e suite asserts on and which nobody reads at a glance. Rendered as
   * fine print inside a collapsed "Szczegóły strony".
   */
  readonly pageNoteQuiet?: string;
  /**
   * The nav route this screen belongs to (`lib/routes.ts` id). Lands on the root element as
   * `data-route`, which is how the layout's stylesheet highlights the active nav item without the
   * layout reading the request. Optional so the component tests need not name a route.
   */
  readonly routeId?: string;
  /** `true` — the table sits under a collapsed "Pokaż tabelę" (the caller puts a human summary in front). */
  readonly tableCollapsed?: boolean;
  /** Extra sections rendered between the lede and the table (tiles, the job detail's header). */
  readonly children?: ReactNode;
}

function cellFor(row: TableRow, column: string): Cell {
  const found = row.cells.find((candidate) => candidate.column === column);
  if (found === undefined) {
    throw new Error(
      `wiersz '${row.key}' nie ma komórki dla kolumny '${column}'. Puste miejsce w tabeli ` +
        "czyta się jak „brak wartości”, a to jest twierdzenie o pomiarze, którego nie było",
    );
  }
  return found;
}

function CellContent({ cell }: { readonly cell: Cell }) {
  if (cell.href !== undefined) {
    return (
      <a className="rows__link" href={cell.href}>
        {cell.value}
      </a>
    );
  }
  if (cell.tone !== undefined && cell.missing !== true) {
    return (
      <span className="pill" data-tone={cell.tone}>
        {cell.value}
      </span>
    );
  }
  return <>{cell.value}</>;
}

function Table({
  what,
  columns,
  rows,
  nested,
}: {
  readonly what: string;
  readonly columns: readonly string[];
  readonly rows: readonly TableRow[];
  readonly nested: boolean;
}) {
  return (
    <div className={nested ? "card card--table card--nested" : "card card--table"} data-wide={columns.length >= 7 ? "tak" : undefined}>
      <div className="rows__scroll">
        <table className="rows">
          <caption className="rows__caption">
            {what}: {rows.length} wiersz(y) odczytanych z control plane.
          </caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr className="rows__row" key={row.key}>
                {columns.map((column) => {
                  const cell = cellFor(row, column);
                  return (
                    <td
                      className={cell.missing === true ? "rows__cell rows__cell--missing" : "rows__cell"}
                      data-column={column}
                      data-missing={cell.missing === true ? "tak" : undefined}
                      key={column}
                    >
                      <CellContent cell={cell} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EntityScreen({
  heading,
  lede,
  what,
  columns,
  rows,
  reason,
  pageNote,
  routeId,
  pageNoteQuiet,
  tableCollapsed = false,
  children,
}: EntityScreenProps) {
  if (columns.length === 0) {
    // Count before predicate, in the component too: a table with no columns renders every row as
    // an empty stripe and asserts nothing about the data behind it.
    throw new Error(`ekran '${heading}' nie deklaruje ani jednej kolumny`);
  }

  return (
    <section className="screen" data-route={routeId}>
      <header className="screen__head">
        <h1 className="screen__title">{heading}</h1>
        <details className="disclosure disclosure--inline">
          <summary className="disclosure__summary">Co tu widzę</summary>
          <p className="screen__lede">{lede}</p>
        </details>
      </header>
      {children}
      {/* Order matters: not-an-array FIRST. See the docstring. */}
      {!Array.isArray(rows) ? (
        <EmptyState what={what} measured={false} reason={reason} />
      ) : rows.length === 0 ? (
        <EmptyState what={what} measured />
      ) : tableCollapsed ? (
        <details className="disclosure">
          {/* No counts here: the caption inside the table already says how many rows were read,
              and a number on the surface is a number somebody has to read. */}
          <summary className="disclosure__summary">Pokaż tabelę</summary>
          <Table columns={columns} nested rows={rows} what={what} />
        </details>
      ) : (
        <Table columns={columns} nested={false} rows={rows} what={what} />
      )}
      {pageNote === undefined ? null : <p className="screen__pagenote">{pageNote}</p>}
      {pageNoteQuiet === undefined ? null : (
        <details className="disclosure disclosure--inline">
          <summary className="disclosure__summary">Szczegóły strony</summary>
          <p className="screen__pagenote screen__pagenote--quiet">{pageNoteQuiet}</p>
        </details>
      )}
    </section>
  );
}
