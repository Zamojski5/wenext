/**
 * The Room's stylesheet, checked against the component that uses it.
 *
 * `renderToStaticMarkup` loads no CSS, so three failures are invisible to every other test of this
 * screen, and each cost a day somewhere else in this package: a deleted import (the screen renders,
 * unstyled), a class in the component that the stylesheet never learned (raw HTML where a card
 * should be), and a colour vocabulary the product forbids sneaking into a new file that the
 * palette test of `studio.css` does not read.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(ROOT, relative), "utf8");

const source = read("app/room.css");
const css = source.replace(/\/\*[\s\S]*?\*\//g, "");
const component = read("components/studio/room.tsx");

describe("room.css", () => {
  it("is_imported_by_the_studio_layout", () => {
    expect(read("app/(studio)/layout.tsx")).toContain('import "../room.css"');
  });

  it("the_comment_stripper_leaves_rules_behind", () => {
    // Every case below reads `css`; an empty one would make them all pass over nothing.
    expect((css.match(/\{/g) ?? []).length).toBeGreaterThan(100);
    expect(source).toContain("the screen the founder opens from Codex");
    expect(css).not.toContain("the screen the founder opens from Codex");
  });

  it("every_class_the_component_uses_has_a_rule", () => {
    const used = new Set(component.match(/st-room-[a-z0-9_-]+/g) ?? []);
    expect(used.size).toBeGreaterThan(30);
    const missing = [...used].filter((name) => !css.includes(`.${name}`));
    expect(missing, "klasy bez reguły w room.css").toEqual([]);
  });

  it("redefines_no_studio_token_and_writes_the_accent_only_through_its_token", () => {
    // Tokens come from `.st`; a second definition here would repaint one screen without anybody
    // deciding it. The accent literal exists exactly once in `studio.css`.
    expect(css).not.toMatch(/--st-[a-z0-9-]+\s*:/);
    expect(css.toLowerCase()).not.toContain("#d3e4af");
    expect(css).toContain("var(--st-accent)");
    const hover = css
      .split("}")
      .filter((block) => block.includes(":hover"))
      .join("\n");
    expect(hover.length).toBeGreaterThan(0);
    expect(hover).not.toContain("--st-accent");
  });

  it("has_no_colour_vocabulary_for_good", () => {
    for (const forbidden of ["green", "#0f0", "#00ff00", "success"]) {
      expect(css.toLowerCase(), forbidden).not.toContain(forbidden);
    }
  });

  it("never_holds_the_last_frame_of_an_animation", () => {
    // `animation-fill-mode: both` keeps `transform` after the animation ends, which makes every
    // card a stacking context and paints the next card over the "Popraw" panel of the previous one.
    expect(css).not.toMatch(/animation:[^;]*\bboth\b/);
    expect(css).not.toMatch(/animation:[^;]*\bforwards\b/);
  });

  it("has_no_long_dash_in_the_file", () => {
    const dashes = new RegExp(`[${String.fromCodePoint(0x2014)}${String.fromCodePoint(0x2013)}]`);
    expect(dashes.test(source)).toBe(false);
  });

  it("has_a_narrow_layout", () => {
    expect(css).toContain("@media (max-width: 760px)");
    expect(css).toContain("@media (max-width: 460px)");
  });
});
