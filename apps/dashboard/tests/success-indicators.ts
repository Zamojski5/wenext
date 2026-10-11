/**
 * The vocabulary an empty screen may never use, and the scanner that looks for it.
 *
 * Shared by `empty_state.test.tsx` and `list_views.test.tsx` because the rule is the same for the
 * component and for each of the three list views, and a second hand-written copy of these lists
 * would drift from the first.
 *
 * EVERY LIST HERE IS WRITTEN OUT, ONE ENTRY PER LINE. A list computed from something else is a list
 * that can quietly become empty, and an empty list of forbidden strings makes every assertion built
 * on it VACUOUSLY TRUE — `all([]) == True`, four times paid for in this repository (D-011). The
 * length of each list is asserted before any predicate iterates it, in every file that uses it.
 *
 * SHORT TOKENS ARE ANCHORED ON WORD BOUNDARIES. Polish "blokad" contains "ok" as a substring, so a
 * naive `includes("ok")` would report a violation on the very sentence that says there are no data.
 * A scanner with false positives gets deleted, and that is how this rule dies.
 */

/** Success phrases. Text channel. */
export const FORBIDDEN_SUCCESS_TEXTS: readonly RegExp[] = [
  /\bok\b/i,
  /\bokej\b/i,
  /wszystko w porządku/i,
  /wszystko w porzadku/i,
  /brak blokad/i,
  /0 błędów/i,
  /0 bledow/i,
  /bez błędów/i,
  /\bsukces\b/i,
  /\bzaliczone\b/i,
  /\bhealthy\b/i,
  /\bpassed?\b/i,
];

/**
 * Class name PARTS (split on `-`, `_` and whitespace) that would paint a success state even when
 * the text is honest. CSS is a second channel for the same lie: `class="status ok"` with a green
 * rule attached says "fine" louder than any sentence.
 */
export const FORBIDDEN_SUCCESS_CLASS_PARTS: readonly string[] = [
  "ok",
  "okay",
  "success",
  "sukces",
  "green",
  "zielony",
  "healthy",
  "pass",
  "passed",
  "good",
  "positive",
  "clear",
];

/** Glyphs that mean "done" without a single word of text. */
export const FORBIDDEN_SUCCESS_ICONS: readonly string[] = ["✓", "✔", "✅", "☑", "🟢", "👍", "✨"];

export interface Violation {
  readonly where: string;
  readonly what: string;
}

/** Every success indicator in the document, across all three channels. */
export function successIndicators(document_: Document): readonly Violation[] {
  const found: Violation[] = [];
  const text = document_.documentElement.textContent ?? "";

  for (const pattern of FORBIDDEN_SUCCESS_TEXTS) {
    const hit = pattern.exec(text);
    if (hit) {
      found.push({ where: "tekst", what: `${String(pattern)} dopasowało "${hit[0]}"` });
    }
  }
  for (const icon of FORBIDDEN_SUCCESS_ICONS) {
    if (text.includes(icon)) {
      found.push({ where: "tekst", what: `ikona ${icon}` });
    }
  }
  for (const element of Array.from(document_.querySelectorAll("*"))) {
    for (const token of Array.from(element.classList)) {
      for (const part of token.split(/[-_\s]+/)) {
        if (part && FORBIDDEN_SUCCESS_CLASS_PARTS.includes(part.toLowerCase())) {
          found.push({ where: `<${element.tagName.toLowerCase()} class>`, what: token });
        }
      }
    }
    for (const attribute of Array.from(element.attributes)) {
      if (attribute.name === "class") continue;
      for (const icon of FORBIDDEN_SUCCESS_ICONS) {
        if (attribute.value.includes(icon)) {
          found.push({ where: `${element.tagName.toLowerCase()}@${attribute.name}`, what: icon });
        }
      }
    }
  }
  return found;
}

/**
 * Affordances that would let a human do what L2673 forbids the machine to do.
 *
 * The dashboard of E1.F1 has NO mutating action on jobs at all, so this scanner looks for two
 * things: any interactive element inside a screen (button, form, input, an `<a>` whose href is not
 * a plain internal navigation), and any of the words a retry control would carry. The word list is
 * separate from the element list because a "Ponów" rendered as a `<span>` with an onClick would be
 * caught by the second and not the first, and a nameless button by the first and not the second.
 */
export const FORBIDDEN_ACTION_TEXTS: readonly RegExp[] = [
  /ponów/i,
  /ponow/i,
  /wymuś/i,
  /wymus/i,
  /redispatch/i,
  /\bretry\b/i,
  /\bretries\b/i,
  /uruchom ponownie/i,
  /spróbuj ponownie/i,
  /anuluj job/i,
  /\bkill\b/i,
];

/**
 * Words the RETRY VERDICT is allowed to contain even though the scanner above would flag them.
 *
 * This exists because the verdict has to be able to SAY "automatyczne ponowienie: ZABRONIONE", and
 * a scanner that forbade the word everywhere would forbid the screen from explaining itself. So the
 * scanner is applied to ELEMENTS THAT COULD BE ACTIONS, and the prose is checked separately — see
 * `retryAffordances`.
 */
export interface Affordance {
  readonly kind: string;
  readonly detail: string;
}

/**
 * Interactive elements in the document that could perform an action, with the retry words they
 * carry. A `<a href="/jobs/…">` is navigation and is NOT an affordance; anything else is.
 */
export function retryAffordances(document_: Document): readonly Affordance[] {
  const found: Affordance[] = [];

  for (const element of Array.from(document_.querySelectorAll("button, form, input, select, textarea"))) {
    found.push({
      kind: element.tagName.toLowerCase(),
      detail: (element.textContent ?? "").trim() || element.outerHTML.slice(0, 120),
    });
  }
  for (const element of Array.from(document_.querySelectorAll("a"))) {
    const href = element.getAttribute("href") ?? "";
    if (!href.startsWith("/")) {
      found.push({ kind: "a[href]", detail: href });
      continue;
    }
    for (const pattern of FORBIDDEN_ACTION_TEXTS) {
      if (pattern.test(href)) {
        found.push({ kind: "a[href]", detail: href });
      }
    }
  }
  for (const element of Array.from(document_.querySelectorAll("*"))) {
    for (const attribute of Array.from(element.attributes)) {
      if (attribute.name.startsWith("on")) {
        found.push({ kind: `${element.tagName.toLowerCase()}@${attribute.name}`, detail: attribute.value });
      }
    }
  }
  return found;
}
