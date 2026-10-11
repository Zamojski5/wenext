/**
 * The visual contract of D-040, checked against the stylesheet that actually ships.
 *
 * WHY A TEST AND NOT AN EYE. Two things here are invisible to every other test in this package:
 *
 *   1. **the import.** `tests/*.tsx` render components with `renderToStaticMarkup`, which never
 *      loads CSS. A deleted `import "../studio.css"` would leave all 188 of them green and ship a
 *      studio with the operator panel's light surfaces. Measured on the running server after this
 *      test was written: the page links two stylesheets, and the studio's is the second one.
 *   2. **the palette.** WIZJA-WORKSPACE names the accepted values literally — `#080808`,
 *      `#0b0b0b`, `#131313` and the single accent `#d3e4af` — and a decision written in a
 *      document with nothing checking it is a decision that drifts.
 *
 * WHAT THIS DOES NOT CLAIM. It is not a render next to the reference screenshot; spacing,
 * hierarchy and contrast are judged on a render and that judgement is the founder's. This test
 * only refuses the two failures that would be silent.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const APP_ROOT = path.resolve(import.meta.dirname, "..");
const STYLESHEET = path.join(APP_ROOT, "app", "studio.css");
const STUDIO_LAYOUT = path.join(APP_ROOT, "app", "(studio)", "layout.tsx");

const source = fs.readFileSync(STYLESHEET, "utf8");

/** WCAG relative luminance and the contrast ratio, so a colour decision can be asserted. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
  const [r, g, b] = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * (r as number) + 0.7152 * (g as number) + 0.0722 * (b as number);
}

function contrast(foreground: string, background: string): number {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * The stylesheet WITHOUT its comments, which is what every assertion below reads.
 *
 * Measured the first time this file ran: the checks matched the prose. The module comment names
 * `#d3e4af` while explaining the accent rule, so "the accent is written literally once" failed at
 * two; and it says "neutral greys", in which `green` is a substring, so "no colour vocabulary for
 * good" failed on the sentence that documents the rule. A test that a file's own documentation
 * breaks is a test somebody deletes, and the rule goes with it (the same reasoning that put word
 * boundaries on the short tokens in `tests/success-indicators.ts`).
 */
const css = source.replace(/\/\*[\s\S]*?\*\//g, "");

/** The values WIZJA-WORKSPACE names outright, section „Język wizualny" (D-040). */
const PALETTE: readonly string[] = ["#080808", "#0b0b0b", "#131313", "#d3e4af"];

describe("the studio stylesheet", () => {
  it("the_comment_stripper_leaves_the_rules_and_removes_the_prose", () => {
    // The stripper is what every other case reads through, so it is shown working first: a
    // regex that ate the whole file would make each assertion below pass over nothing.
    expect(source).toContain("the visual language of WIZJA-WORKSPACE");
    expect(css).not.toContain("the visual language of WIZJA-WORKSPACE");
    expect(css).toContain("--st-accent: #d3e4af;");
    // LICZBA REGUŁ, NIE POŁOWA DŁUGOŚCI PLIKU. Warunek brzmiał „po obcięciu zostaje więcej niż
    // połowa znaków" i był proxy na „regex nie zjadł arkusza". Proxy przestało działać 21.09,
    // gdy komentarze urosły: ten arkusz ma tłumaczyć KAŻDĄ regułę powodem (tak działa cała
    // reszta drzewa), więc proza może być dłuższa od reguł i to jest w porządku. Liczba
    // otwierających klamer mierzy to samo wprost: zjedzony arkusz ma ich zero.
    expect((css.match(/\{/g) ?? []).length).toBeGreaterThan(150);
    expect(css.length).toBeGreaterThan(10000);
  });

  it("is_imported_by_the_studio_layout", () => {
    // The one line that makes every rule below reach a browser.
    const layout = fs.readFileSync(STUDIO_LAYOUT, "utf8");
    expect(layout).toContain('import "../studio.css"');
  });

  it("carries_the_accepted_palette", () => {
    expect(PALETTE.length).toBe(4);
    for (const value of PALETTE) {
      expect(css, `brak koloru ${value} z WIZJA-WORKSPACE`).toContain(value);
    }
  });

  it("uses_the_accent_for_state_and_never_for_a_hover", () => {
    // "Niewielki akcent `#d3e4af` oznacza stan; nie zalewamy nim kart ani hoverów." The literal
    // value appears once, where the token is defined; everything else refers to the token, so a
    // rule that painted a hover with it would have to name it again — which is what this catches.
    const literal = css.split("#d3e4af").length - 1;
    expect(literal, "akcent wpisany na sztywno więcej niż raz").toBe(1);

    const hoverRules = css
      .split("}")
      .filter((block) => block.includes(":hover"))
      .join("\n");
    expect(hoverRules.length).toBeGreaterThan(0);
    expect(hoverRules).not.toContain("--st-accent");
  });

  it("switches_off_every_animation_under_prefers_reduced_motion", () => {
    // WIZJA-WORKSPACE asks for it twice: the mark is static under this preference, and so is the
    // panel motion. One block, covering the whole studio subtree.
    const index = css.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(index, "brak bloku ograniczonego ruchu").toBeGreaterThan(-1);
    const block = css.slice(index);
    expect(block).toContain("animation: none !important");
    expect(block).toContain("transition: none !important");
    expect(block).toContain(".st *");
  });

  it("has_no_colour_vocabulary_for_good", () => {
    // Inherited from `globals.css` and still in force: no green, no success accent. An empty
    // screen is a normal state here, and a stylesheet that painted it reassuringly would be
    // `all([]) == True` in the visual layer (D-011).
    for (const forbidden of ["green", "#0f0", "#00ff00", "success"]) {
      expect(css.toLowerCase(), forbidden).not.toContain(forbidden);
    }
  });

  it("keeps_the_muted_ink_above_the_contrast_floor", () => {
    /**
     * MEASURED IN THE RENDER REVIEW of 2026-09-19, not chosen by eye: `--st-ink-3` was `#777772`
     * and came out at 3.87:1 on the raised surface and 4.45:1 on the base, under the 4.5:1 WCAG
     * asks of body text — and it is the colour of sentences a person has to read (empty states,
     * a project's second line). The ratio is recomputed here so the token cannot drift back.
     */
    const token = css.match(/--st-ink-3:\s*(#[0-9a-f]{6})/i);
    expect(token, "nie ma tokenu --st-ink-3").not.toBeNull();
    const surfaces = ["#080808", "#0b0b0b", "#131313", "#1b1b1b"];
    for (const surface of surfaces) {
      expect(contrast(token![1] as string, surface), `${token![1]} na ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("gives_a_link_inside_a_sentence_something_to_see", () => {
    /**
     * A row that IS a link keeps `color: inherit` and no underline: the whole row is the target
     * and underlines would turn a calm list into a page of blue. A link inside prose without
     * either is invisible, which is what the render review found on "Wróć do projektu".
     */
    const rule = css.split("}").find((block) => block.includes(".st__lede a"));
    expect(rule, "brak reguły dla linku w zdaniu").toBeTruthy();
    expect(rule).toContain("text-decoration: underline");
    expect(rule).toContain("var(--st-accent)");
  });

  it("stays_readable_on_a_narrow_screen", () => {
    // "Czytelność, obsługa klawiaturą i węższy ekran są częścią oceny gotowego widoku."
    expect(css).toContain("@media (max-width: 620px)");
    expect(css).toContain("grid-template-columns: minmax(0, 1fr)");
    // And a coarse pointer gets targets it can actually hit.
    expect(css).toContain("@media (pointer: coarse)");
    expect(css).toContain("min-height: 44px");
  });
});

/** Reguła po nazwie selektora, do zamykającej klamry. Tyle wystarczy: plik jest płaski. */
function ruleFor(css: string, selector: string): string {
  const index = css.indexOf(`\n${selector} {`);
  if (index < 0) throw new Error(`nie ma reguły ${selector}`);
  return css.slice(index, css.indexOf("}", index));
}

describe("lewa kolumna zostaje na ekranie", () => {
  // Uwaga założyciela z 20.09: po przewinięciu długiej rozmowy kolumna była pusta, zostawało
  // samo logo i „Wyloguj". Projekty i nawigacja mają być widoczne przez cały czas.
  const css = source;

  it("the_column_is_sticky_and_can_actually_stick", () => {
    const rule = ruleFor(css, ".st__side");
    expect(rule).toContain("position: sticky");
    expect(rule).toContain("top: 0");
    // BEZ `align-self: start` element siatki rozciąga się na wysokość wiersza i `sticky` nie
    // robi NIC. Ten warunek jest tu po to, żeby funkcja nie wyglądała na wdrożoną, nie będąc.
    expect(rule).toContain("align-self: start");
    // Wysokość RAMY, nie okna: `.wm-shell` ma po pikselu obramowania, więc `100vh` w pasku
    // dawało dokument 786 px w oknie 784 i cała strona przewijała się o dwa piksele (odbiór 3,
    // N5).
    expect(rule).toContain("height: 100%");
  });

  it("only_the_project_list_scrolls_and_shows_it", () => {
    // Pasek NIE przewija się jako całość (odbiór 2, R1) i NICZEGO nie przycina (odbiór 3, R4):
    // `overflow: hidden` na kolumnie ucinało panele trzech zapadek przy jej krawędzi, a ucięta
    // część nie istnieje dla kliknięcia. Przewija się wyłącznie lista projektów i musi to być
    // widać: pasek przewijania rysuje pseudoelement, bo `scrollbar-width` wyłącza te reguły
    // w Chrome, a nakładkowy pasek nie zajmuje ani piksela.
    // Bez komentarzy: słowo „overflow" pada w wyjaśnieniu obok i sprawdzamy DEKLARACJE.
    const pasek = ruleFor(css, ".st__side").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(pasek).not.toContain("overflow");
    const region = ruleFor(css, ".st__scroll").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(region).toContain("overflow-y: auto");
    expect(region).toContain("flex: 1 1 auto");
    expect(region).toContain("min-height: 0");
    expect(region).not.toContain("scrollbar-width");
    expect(css).toContain(".st__scroll::-webkit-scrollbar {");
  });

  it("the_main_column_scrolls_instead_of_swallowing_its_content", () => {
    // Stała wysokość sceny (odbiór 2, R2/N3) trzyma pole rozmowy na dole, ale z `overflow:
    // hidden` ekrany listy chowały treść bez śladu: Aktywność 1676 px w oknie 784, Wiedza 1511,
    // Ludzie 1370, i nic tego nie przewijało. `auto` zostawia dno i oddaje przewijanie.
    const glowna = ruleFor(css, ".st__main").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(glowna).toContain("height: 100%");
    expect(glowna).toContain("overflow-y: auto");
    expect(glowna).not.toContain("overflow: hidden");
  });

  it("the_logout_sits_at_the_bottom_of_the_column", () => {
    // Znak przeniósł się 20.09 do górnego paska, obok nazwy przestrzeni (D-045, smak prototypu);
    // na dole zostaje wyjście, i ono ma zostać na dole niezależnie od długości listy projektów.
    expect(ruleFor(css, ".st__foot")).toContain("margin-top: auto");
  });

  it("a_narrow_screen_turns_the_sticky_column_off", () => {
    // Na wąskim ekranie kolumna stoi NAD treścią; przyklejona zabrałaby pół ekranu rozmowie.
    const narrow = css.slice(css.indexOf("@media (max-width: 620px)"));
    expect(narrow).toContain("position: static");
  });
});

describe("smak prototypu w pasku i na pustym projekcie (D-045)", () => {
  // Założyciel 20.09: „bardziej mi się podoba jego smak", o `docs/produkt/prototyp/`. Cztery
  // rzeczy z tamtego pliku dają się sprawdzić w arkuszu, a nie tylko ocenić okiem, i tylko te
  // są tutaj. Reszta (odstępy, hierarchia, kontrast) jest oceną na renderze i należy do
  // założyciela.
  const css = source;

  it("the_sidebar_items_are_pills_like_in_the_prototype", () => {
    const rule = ruleFor(css, ".st__item");
    expect(rule).toContain("border-radius: 999px");
    expect(rule).toContain("font-size: 12px");
  });

  it("a_conversation_row_is_quieter_than_the_project_above_it", () => {
    // Drugi poziom listy. Gdyby rozmowy miały rozmiar projektów, pasek stałby się jedną listą
    // bez hierarchii i nie dałoby się zobaczyć, co do czego należy.
    const conv = ruleFor(css, ".st-conv");
    const item = ruleFor(css, ".st__item");
    expect(conv).toContain("font-size: 11px");
    expect(item).toContain("font-size: 12px");
    // Wcięcie z lewą kreską zamiast ramki: rozmowy należą do projektu nad nimi.
    expect(ruleFor(css, ".st__convs")).toContain("border-left");
  });

  it("the_question_on_an_empty_project_is_the_biggest_thing_on_it", () => {
    // `wm-chat h1` w prototypie ma 28px i ciasny odstęp liter. To jedyne zdanie na pustym
    // ekranie i ma być pierwszą rzeczą, którą się czyta.
    const rule = ruleFor(css, ".st-start__ask");
    expect(rule).toContain("font-size: 28px");
    expect(rule).toContain("letter-spacing");
  });

  it("the_send_button_is_the_one_bright_thing_and_it_is_round", () => {
    const rule = ruleFor(css, ".st-send");
    expect(rule).toContain("border-radius: 50%");
    expect(rule).toContain("background: var(--st-ink)");
    // Chip jest etykietą, nie stanem, więc nie bierze akcentu (D-040).
    expect(ruleFor(css, ".st-chip")).not.toContain("--st-accent");
  });
});
