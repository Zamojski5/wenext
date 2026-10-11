/**
 * `all([]) == True`, moved into the visual layer.
 *
 * Three of the false passes in this project came from a predicate evaluated over an empty
 * collection (D-011). The dashboard is where that mistake becomes a picture: on a fresh database
 * every list here is EMPTY, because E1.F1 writes the mechanics and has no producer of content
 * (D-013). A screen that answers an empty response with a tick, a "wszystko w porządku" or a
 * "0 błędów" is not a cosmetic problem — it is a gate announcing GO without a measurement, drawn
 * in HTML.
 *
 * So the rule for this component is the gates' own rule: an empty result is a REPORTED STATE,
 * never a silent pass. And a FAILED READ is a third thing, reported differently again.
 *
 * WHAT THIS FILE COVERS: the component, on its own. What it does NOT cover, and what nothing
 * covered on 2026-08-31: the screens that USE it. Those are `tests/entity_screen.test.tsx` and
 * `tests/list_views.test.tsx`. The three files are not interchangeable — deleting any of them
 * leaves a real hole, and the hole this one leaves is exactly the one that let `items ?? []` one
 * layer up pass the whole suite green.
 */
import { describe, expect, it } from "vitest";

import { EmptyState } from "../components/empty-state";
import { allElements, renderToDocument } from "./render";
import {
  FORBIDDEN_SUCCESS_CLASS_PARTS,
  FORBIDDEN_SUCCESS_ICONS,
  FORBIDDEN_SUCCESS_TEXTS,
  successIndicators,
} from "./success-indicators";

const NO_DATA = "brak danych";
const NO_ANSWER = "brak odpowiedzi";

describe("EmptyState", () => {
  it("the_forbidden_signal_list_is_not_empty", () => {
    // The length assertions come BEFORE any predicate that iterates these lists. A scanner built
    // over an empty list reports a clean screen for every input and reads exactly like a passing
    // test — the same order (count first, predicate second) that D-011 forced on the gate code.
    expect(FORBIDDEN_SUCCESS_TEXTS.length).toBeGreaterThanOrEqual(10);
    expect(FORBIDDEN_SUCCESS_CLASS_PARTS.length).toBeGreaterThanOrEqual(10);
    expect(FORBIDDEN_SUCCESS_ICONS.length).toBeGreaterThanOrEqual(5);
  });

  it("the_forbidden_signal_scanner_actually_detects_a_success_indicator", () => {
    // A scanner nobody has seen catch anything is not a scanner. Feed it a screen that IS lying
    // and require every one of the three channels — text, class, icon — to be reported.
    const lying = renderToDocument(
      <p className="status-ok" data-mark="✅">
        wszystko w porządku, 0 błędów
      </p>,
    );
    const found = successIndicators(lying);

    expect(found.length).toBeGreaterThanOrEqual(3);
    expect(found.map((violation) => violation.where)).toContain("tekst");
    expect(found.some((violation) => violation.where.includes("class"))).toBe(true);
    expect(found.some((violation) => violation.what.includes("✅"))).toBe(true);
  });

  it("a_measured_empty_says_no_data_and_shows_no_success_indicator", () => {
    const rendered = renderToDocument(<EmptyState measured what="Kolejka jobów" />);
    const text = rendered.documentElement.textContent ?? "";

    // Length before predicate, again: an empty document satisfies "contains no success indicator"
    // perfectly.
    expect(text.length).toBeGreaterThan(0);
    expect(allElements(rendered).length).toBeGreaterThan(0);

    expect(text).toContain(NO_DATA);
    expect(text).not.toContain(NO_ANSWER);
    expect(successIndicators(rendered)).toEqual([]);
  });

  it("a_failed_read_says_no_answer_and_prints_the_reason", () => {
    const reason = "połączenie odrzucone przez control plane na 127.0.0.1:8000";
    const rendered = renderToDocument(
      <EmptyState measured={false} reason={reason} what="Kolejka jobów" />,
    );
    const text = rendered.documentElement.textContent ?? "";

    expect(text).toContain(NO_ANSWER);
    expect(text).not.toContain(NO_DATA);
    // The reason is the difference between "something went wrong" and a fact somebody can act on.
    expect(text).toContain(reason);
    expect(successIndicators(rendered)).toEqual([]);
  });

  it("a_failed_read_with_no_reason_says_that_this_itself_is_a_defect", () => {
    // The honest fallback. A missing reason must not render as a blank line that reads like "no
    // further information available" — silence about an absence is what this repository treats as
    // a false pass, so the screen names it.
    const rendered = renderToDocument(<EmptyState measured={false} what="Kolejka jobów" />);
    const text = rendered.documentElement.textContent ?? "";

    expect(text).toContain(NO_ANSWER);
    expect(text).toContain("powód nie został podany");
  });

  it("the_two_states_carry_different_machine_readable_markers", () => {
    // The sentence is for the operator; `data-state` is for anything that has to tell the two
    // apart mechanically. Both channels, because either alone is weaker than the pair.
    const measured = renderToDocument(<EmptyState measured what="X" />);
    const unread = renderToDocument(<EmptyState measured={false} reason="r" what="X" />);

    expect(measured.querySelectorAll("[data-state]").length).toBe(1);
    expect(unread.querySelectorAll("[data-state]").length).toBe(1);
    expect(measured.querySelector("[data-state]")?.getAttribute("data-state")).toBe(NO_DATA);
    expect(unread.querySelector("[data-state]")?.getAttribute("data-state")).toBe(NO_ANSWER);
  });
});
