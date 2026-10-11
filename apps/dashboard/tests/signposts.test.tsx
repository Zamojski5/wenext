/**
 * A screen that reads nothing must not be able to say "brak danych".
 *
 * `/`, `/transitions` and `/approvals` render no list, because E1.F1 exposes no reader for what
 * they are about (the control plane mounts `/api/session`, `/api/jobs`, `/api/jobs/{job_id}` and
 * `/api/audit-events`, and that is all). The temptation this file removes is the comfortable one:
 * render the same empty table there so the panel looks uniform. That table would report a MEASURED
 * empty over a read that never happened — the fourth repetition of this project's signature defect,
 * this time with nothing behind it at all.
 *
 * So the assertions run in both directions: a signpost has no table, no `data-state`, no success
 * indicator AND no "brak danych" anywhere in its text.
 */
import { describe, expect, it } from "vitest";

import { Signpost } from "../components/views/signpost";
import { allElements, renderToDocument } from "./render";
import { successIndicators } from "./success-indicators";

const SIGNPOSTS: readonly { readonly heading: string; readonly paragraphs: readonly string[] }[] = [
  {
    heading: "Przejścia stanów",
    paragraphs: ["Ten ekran nie czyta niczego.", "Przejścia widać w dzienniku audytu."],
  },
  {
    heading: "Ścieżka zgody",
    paragraphs: ["Ten ekran nie czyta niczego.", "Kolejka approvali jako produkt to E1.F4."],
  },
];

describe("Signpost", () => {
  it("a_signpost_renders_no_list_and_claims_no_measured_empty", () => {
    expect(SIGNPOSTS.length).toBe(2);

    for (const signpost of SIGNPOSTS) {
      const rendered = renderToDocument(
        <Signpost heading={signpost.heading} paragraphs={signpost.paragraphs} />,
      );
      const text = rendered.documentElement.textContent ?? "";

      // The document exists first; only then do the negative assertions mean anything.
      expect(allElements(rendered).length, signpost.heading).toBeGreaterThan(0);
      expect(text, signpost.heading).toContain(signpost.heading);

      expect(rendered.querySelectorAll("table").length, signpost.heading).toBe(0);
      expect(rendered.querySelectorAll("[data-state]").length, signpost.heading).toBe(0);
      expect(text, signpost.heading).not.toContain("brak danych");
      expect(text, signpost.heading).not.toContain("brak odpowiedzi");
      expect(successIndicators(rendered), signpost.heading).toEqual([]);

      // The machine-readable marker, so a test does not have to infer "this is not a list screen"
      // from prose.
      expect(
        rendered.querySelector("[data-screen]")?.getAttribute("data-screen"),
        signpost.heading,
      ).toBe("signpost");
    }
  });

  it("a_signpost_with_no_sentences_throws", () => {
    // An empty signpost is worse than no signpost: the reader stops looking. Same rule as a
    // docstring promising a control nobody measures.
    expect(() => renderToDocument(<Signpost heading="Puste" paragraphs={[]} />)).toThrow(
      /ani jednego zdania/,
    );
  });

  it("every_sentence_reaches_the_markup", () => {
    // A component that dropped a paragraph would satisfy the test above and lose the explanation
    // that is the entire content of this screen.
    const paragraphs = ["Pierwsze zdanie.", "Drugie zdanie.", "Trzecie zdanie."];
    const rendered = renderToDocument(<Signpost heading="Trzy" paragraphs={paragraphs} />);
    expect(rendered.querySelectorAll("p").length).toBe(paragraphs.length);
    for (const paragraph of paragraphs) {
      expect(rendered.documentElement.textContent ?? "").toContain(paragraph);
    }
  });
});
