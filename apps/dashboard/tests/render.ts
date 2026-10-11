import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";

/**
 * Render a server component and hand back a parsed DOM.
 *
 * `DOMParser` rather than `document.body.innerHTML = markup`: the root layout renders a whole
 * `<html>` document, and assigning that to `innerHTML` makes jsdom throw away the `<html>`
 * and `<body>` wrappers. The assertions would then run against a silently truncated tree —
 * a scanner that reads less than it thinks it does, which is the failure mode
 * `docs/KONWENCJE.md` names for parsers that lose data quietly.
 */
export function renderToDocument(element: ReactElement): Document {
  const markup = renderToStaticMarkup(element);
  if (markup.length === 0) {
    throw new Error("render zwrócił pusty markup — nie ma czego sprawdzać");
  }
  return new DOMParser().parseFromString(markup, "text/html");
}

/** Every element in the document, root included. */
export function allElements(document_: Document): readonly Element[] {
  return Array.from(document_.querySelectorAll("*"));
}
