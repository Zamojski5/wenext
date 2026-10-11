import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { renderToDocument } from "./render";
import { describe, expect, it } from "vitest";
import { MaterialReader } from "../components/studio/material-reader";
import { parseSourceSlice } from "../lib/knowledge-schema";

const BACK = "/w/aaaaaaaa-0000-4000-8000-000000000001/p/bbbbbbbb-0000-4000-8000-000000000001/knowledge";
const ID = "cccccccc-0000-4000-8000-000000000001";

describe("material prepared in conversation", () => {
  it("opens a readable document with safe links, pagination and no publication action", () => {
    const document = renderToDocument(<MaterialReader title="Plan rozmów" back={BACK} generated
      slice={{ source_id: ID, text: `## Pytania\n\n1. Ostatni przypadek?\n\n[Wróć do materiału](${BACK}?source=${ID})\n\n[Zły](javascript:alert)\n\n<script>alert(1)</script>`, offset: 0, length: 100, total_bytes: 200, truncated: true }} />);
    expect(document.querySelector("h2")?.textContent).toBe("Plan rozmów");
    expect(document.querySelector("ol li")?.textContent).toBe("Ostatni przypadek?");
    expect(document.querySelector(`a[href='${BACK}?source=${ID}']`)?.getAttribute("target")).toBeNull();
    expect(document.querySelector(`a[href='?source=${ID}&offset=100']`)).not.toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(document.querySelector("form")).toBeNull();
  });

  it("reports missing bytes without presenting an empty document as a successful read", () => {
    const html = renderToStaticMarkup(<MaterialReader title="Książka" back={BACK}
      slice={{ source_id: ID, text: "", offset: 0, length: 0, total_bytes: 0, truncated: false }} />);
    expect(html).toContain("nie udało się odczytać pliku");
    expect(html).not.toContain("Czytaj dalej");
    expect(parseSourceSlice({ text: "looks readable" }).ok).toBe(false);
  });
});
