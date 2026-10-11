/**
 * The two shells and the route table, checked against each other in both directions.
 *
 * After the pivot (D-038 to D-040) there are two of them: the studio, which is the product, and
 * the operator panel, which keeps its own chrome inside the `(operations)` route group. The root
 * layout owns neither — it is the document and nothing else — so the navigation test renders
 * `OperationsLayout`, not `RootLayout`.
 *
 * `href` and `file` are separate fields in `lib/routes.ts` since the pivot, because a route
 * group's parentheses are part of the path on disk and absent from the URL. The "does the page
 * exist" check therefore reads `file`; deriving it from `href` would assert against paths that
 * are not there.
 *
 * `labelOf` is here too: every list view takes its heading from it rather than writing one, so a
 * label renamed in the navigation is renamed on the screen. An unknown id THROWS, because a
 * screen quietly rendering an empty heading is the same class of defect as a nav entry with no
 * page.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import OperationsLayout from "../app/(operations)/layout";
import RootLayout from "../app/layout";
import {
  AFTER_LOGIN_PATH,
  ALL_PAGES,
  LOGIN_PATH,
  NAV_ROUTES,
  NON_NAV_ROUTES,
  STUDIO_ROUTES,
  labelOf,
} from "../lib/routes";
import { renderToDocument } from "./render";

const APP_DIR = path.resolve(import.meta.dirname, "..", "app");

describe("RootLayout", () => {
  it("the_root_layout_is_the_document_and_carries_no_navigation", () => {
    // The whole point of the pivot's layout change: the root must not impose the operator panel's
    // chrome on the studio. A `<nav>` reappearing here is that regression.
    const rendered = renderToDocument(
      <RootLayout>
        <section id="content-under-test" />
      </RootLayout>,
    );
    expect(rendered.querySelectorAll("nav").length).toBe(0);
    expect(rendered.querySelectorAll("form").length).toBe(0);
    expect(rendered.querySelector("#content-under-test")).not.toBeNull();
    expect(rendered.documentElement.getAttribute("lang")).toBe("pl");
  });
});

describe("OperationsLayout", () => {
  it("the_layout_renders_every_declared_route", () => {
    // Count first. With an empty NAV_ROUTES every "for each route …" assertion below passes and
    // the navigation is gone — `all([]) == True`, which has cost this project three false passes.
    expect(NAV_ROUTES.length).toBe(6);

    const rendered = renderToDocument(
      <OperationsLayout>
        {/* A `<section>`, not a `<main>`: the layout already owns the single `<main>` of the
            document, and nesting a second one would make the assertion pass against invalid
            markup. */}
        <section id="content-under-test" />
      </OperationsLayout>,
    );

    const links = Array.from(rendered.querySelectorAll("nav a"));
    expect(links.length).toBe(NAV_ROUTES.length);

    const renderedHrefs = links.map((link) => link.getAttribute("href"));
    const renderedLabels = links.map((link) => (link.textContent ?? "").trim());

    // Both directions. Only the first would let a hand-written extra link into the navigation;
    // only the second would let a declared route go unlinked.
    expect(renderedHrefs.slice().sort()).toEqual(NAV_ROUTES.map((route) => route.href).sort());
    for (const route of NAV_ROUTES) {
      expect(renderedHrefs).toContain(route.href);
      expect(renderedLabels).toContain(route.label);
    }

    // The children the layout was handed have to survive it — a layout that dropped its own
    // content would satisfy every assertion above.
    expect(rendered.querySelector("#content-under-test")).not.toBeNull();
  });

  it("the_navigation_links_to_no_route_outside_the_nav_table", () => {
    // The login screen and the job detail are routes, not nav entries: you are sent to one and
    // you reach the other from a row. A link to `/login` in the navigation would invite an
    // operator to log out by accident.
    const rendered = renderToDocument(
      <OperationsLayout>
        <section />
      </OperationsLayout>,
    );
    const hrefs = Array.from(rendered.querySelectorAll("nav a")).map((link) =>
      link.getAttribute("href"),
    );
    expect(NON_NAV_ROUTES.length).toBe(4);
    for (const route of NON_NAV_ROUTES) {
      expect(hrefs).not.toContain(route.href);
    }
  });

  it("the_operator_panel_offers_a_way_back_to_the_studio", () => {
    // The panel is now reached FROM the studio, so it has to be leavable. Outside <nav>, so the
    // assertion above still holds.
    const rendered = renderToDocument(
      <OperationsLayout>
        <section />
      </OperationsLayout>,
    );
    const brand = rendered.querySelector(".shell__brand");
    expect(brand?.getAttribute("href")).toBe("/");
  });

  it("the_only_form_in_the_layout_is_the_logout_and_it_uses_post", () => {
    // The shell must not grow an action. The logout is the one exception and it mutates nothing
    // but the session; POST because a logout reachable by GET is a logout any prefetch can
    // perform.
    const rendered = renderToDocument(
      <OperationsLayout>
        <section />
      </OperationsLayout>,
    );
    const forms = Array.from(rendered.querySelectorAll("form"));
    expect(forms.length).toBe(1);
    expect(forms[0]?.getAttribute("method")).toBe("post");
    expect(forms[0]?.getAttribute("action")).toBe("/logout");
  });
});

describe("the route table", () => {
  it("every_declared_route_has_a_page_file", () => {
    // 22 od D-055: doszedł „Pokój", pierwszy ekran wtyczki do Codexa.
    expect(ALL_PAGES.length).toBe(22);

    for (const route of ALL_PAGES) {
      const pageFile = path.join(APP_DIR, route.file);
      expect(
        fs.existsSync(pageFile),
        `trasa ${route.href} deklaruje ${route.file}, a tego pliku nie ma`,
      ).toBe(true);
    }
  });

  it("route_hrefs_and_files_are_unique_and_absolute", () => {
    // 22 od D-055: doszedł „Pokój", pierwszy ekran wtyczki do Codexa.
    expect(ALL_PAGES.length).toBe(22);

    const hrefs = ALL_PAGES.map((route) => route.href);
    const files = ALL_PAGES.map((route) => route.file);
    expect(new Set(hrefs).size).toBe(hrefs.length);
    expect(new Set(files).size).toBe(files.length);
    for (const href of hrefs) {
      expect(href.startsWith("/")).toBe(true);
    }
    for (const file of files) {
      expect(file.endsWith("page.tsx")).toBe(true);
      expect(file.startsWith("/")).toBe(false);
    }

    // The two landing paths must be real routes, or the guard redirects into a 404.
    expect(NON_NAV_ROUTES.map((route) => route.href)).toContain(LOGIN_PATH);
    expect(hrefs).toContain(AFTER_LOGIN_PATH);
    // After the pivot a successful login lands in the studio, not in the operator queue.
    expect(AFTER_LOGIN_PATH).toBe("/");
  });

  it("exactly_two_routes_declare_that_they_read_nothing", () => {
    // The `reads` flag is what tells a signpost from a list screen, and it has to match reality.
    // `/transitions` and `/approvals` still measure nothing — `grep -rn "APIRouter"
    // services/control_plane/control_plane/api/` is the check. `codex` left this list when P1
    // etap 2 gave it something to read.
    const reading = [...STUDIO_ROUTES, ...NAV_ROUTES]
      .filter((route) => route.reads)
      .map((route) => route.id);
    const notReading = [...STUDIO_ROUTES, ...NAV_ROUTES]
      .filter((route) => !route.reads)
      .map((route) => route.id);

    expect(reading.slice().sort()).toEqual([
      "audit",
      "codex",
      "communities",
      "community",
      // D-045: jedna rozmowa projektu. Czyta ślad sesji z control plane'u, więc `reads: true`.
      "conversation",
      "jobs",
      "knowledge",
      "organization",
      "overview",
      "people",
      "presence",
      "project",
      "publications",
      // D-055: czyta pokój projektu (okazje, szkice, wyniki), więc `reads: true`.
      "room",
      "studio",
      "workspace",
    ]);
    expect(notReading.slice().sort()).toEqual(["approvals", "transitions"]);
  });

  it("labelOf_returns_the_navigation_label_and_throws_on_an_unknown_id", () => {
    for (const route of [...STUDIO_ROUTES, ...NAV_ROUTES]) {
      expect(labelOf(route.id)).toBe(route.label);
    }
    // The message has to name both the id and the file, because that is what the reader needs.
    expect(() => labelOf("nie-ma-takiej-trasy")).toThrow(/nie-ma-takiej-trasy/);
    expect(() => labelOf("nie-ma-takiej-trasy")).toThrow(/lib\/routes\.ts/);
  });
});
