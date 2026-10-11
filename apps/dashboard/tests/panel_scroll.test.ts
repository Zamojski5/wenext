/**
 * An opened panel is scrolled into view (27.09).
 *
 * Measured in a real browser at 1440x900 and 1280x720: the account card's "Połącz X" panel opened
 * under the card, behind the composer, and "Kontynuuj" could not be seen. The shell's panel script
 * now scrolls an opened `details.st-pop` panel into view. Executed here on a real DOM, the way the
 * page runs it.
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { CLOSE_PANELS_SCRIPT } from "../components/studio/shell";

describe("the panel script", () => {
  it("scrolls_an_opened_panel_into_view_and_leaves_a_closed_one_alone", () => {
    document.body.innerHTML = `
      <details class="st-pop st-account-pop"><summary>Połącz X</summary>
        <div class="st-popover"><button>Kontynuuj</button></div></details>`;
    const scrolled = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrolled });
    new Function(CLOSE_PANELS_SCRIPT)();

    const details = document.querySelector("details") as HTMLDetailsElement;
    details.dispatchEvent(new Event("toggle"));
    expect(scrolled).not.toHaveBeenCalled();

    details.setAttribute("open", "");
    details.dispatchEvent(new Event("toggle"));
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled.mock.instances[0]).toBe(document.querySelector(".st-popover"));
    expect(scrolled).toHaveBeenCalledWith({ block: "nearest" });
  });
});
