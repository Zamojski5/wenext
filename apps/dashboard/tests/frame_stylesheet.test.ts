/**
 * Arkusz ramy kontra referencja i kontra komponenty (E2).
 *
 * TE DWA SPRAWDZENIA ISTNIEJĄ Z POWODU JEDNEJ USTERKI Z 20.09 WIECZOREM, której nie zobaczył
 * żaden inny test: `app/frame.css` był przeniesiony z `docs/produkt/prototyp/workspace-studio.html`
 * maszynowo, ale BEZ opakowań `@media`. Reguły odsłony telefonowej obowiązywały więc zawsze i
 * `.wm-sidebar{display:none}` chowało CAŁY pasek boczny na ekranie 1533 px. Testy komponentów
 * tego nie widzą (`renderToStaticMarkup` nie ładuje CSS), e2e nie widzi (pasek JEST w HTML-u,
 * tylko niewidoczny), a `studio_style.test.ts` pilnuje reguł, które są, a nie tego, w jakim
 * kontekście obowiązują. Zobaczył dopiero pierwszy zrzut ekranu.
 *
 * Czego więc pilnujemy tutaj, bo to da się sprawdzić bez przeglądarki:
 *
 *   1. arkusz NADAL NIESIE zapytania mediów z referencji, co do treści warunku. Spłaszczenie
 *      ich jest dokładnie tą usterką i musi zepsuć test, a nie zrzut za trzy dni;
 *   2. każda klasa ramy użyta w komponencie MA regułę w arkuszu. Odwrotnie nie: referencja ma
 *      więcej elementów niż produkt i reguła bez elementu w nic nie trafia, a element bez
 *      reguły to surowy HTML na ekranie.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = process.cwd();
const FRAME = fs.readFileSync(path.join(ROOT, "app/frame.css"), "utf8");

/** Warunki mediów z referencji. Lista jest z pliku makiety, nie z arkusza. */
const MEDIA = [
  "@media(max-width:1000px)",
  "@media(max-width:800px)",
  "@media(max-width:760px)",
  "@media(max-width:580px)",
  "@media(pointer:coarse)",
  "@media(prefers-reduced-motion:reduce)",
] as const;

/** Wszystkie pliki, w których mogą stać klasy ramy. */
function sources(): string[] {
  const found: string[] = [];
  const walk = (directory: string) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".tsx")) found.push(full);
    }
  };
  walk(path.join(ROOT, "components"));
  walk(path.join(ROOT, "app"));
  return found;
}

describe("arkusz ramy", () => {
  it("keeps_every_media_query_the_reference_had_instead_of_flattening_them", () => {
    for (const condition of MEDIA) {
      expect(FRAME, `${condition} zniknęło z arkusza`).toContain(`${condition}{`);
    }
    // I NIE MA ANI JEDNEJ reguły chowającej pasek poza zapytaniem mediów. To jest dokładnie ta
    // deklaracja, która położyła układ: w referencji stoi w odsłonie telefonowej, a bez
    // opakowania obowiązuje wszędzie.
    const outsideMedia = FRAME.split("\n").filter(
      (line) => line.startsWith(".st .wm-sidebar") && line.includes("display:none"),
    );
    expect(outsideMedia, "pasek chowany bezwarunkowo").toEqual([]);
  });

  it("the_elements_the_reference_styles_carry_BOTH_names", () => {
    // DWIE NAZWY KLAS, I OBIE SĄ POTRZEBNE (E2, pozycje 5, 6 i 8 listy różnic). `st-*` zostaje,
    // bo po niej rozpoznają element testy i arkusz Studia; `wm-*` włącza regułę referencji, bez
    // której wygląd tego elementu bierze się z innego arkusza niż reszta ramy. Skasowanie
    // drugiej nazwy nie psuje niczego, co widać w teście treści: psuje tylko wygląd, a to
    // zauważy dopiero ktoś przy zrzucie. Stąd ten test.
    const shell = fs.readFileSync(path.join(ROOT, "components/studio/shell.tsx"), "utf8");
    expect(shell, "przełącznik przestrzeni").toContain('frame="wm-workspace-switch"');
    expect(shell, "nowy projekt").toContain('frame="wm-circle"');

    const composer = fs.readFileSync(path.join(ROOT, "components/studio/composer.tsx"), "utf8");
    for (const both of [
      'className="st-start wm-chat"',
      'className="st-composer wm-composer"',
      'className="st-composer__bar wm-composerbar"',
      'className="st-send wm-send"',
    ]) {
      expect(composer, both).toContain(both);
    }
  });

  it("the_navigation_rules_reach_links_and_not_only_buttons", () => {
    // Referencja stylizuje `.wm-navigation button` i `.wm-projects button`, bo makieta przełącza
    // widoki skryptem i nie ma adresów. Studio renderuje `<a href>`, bo to są prawdziwe miejsca
    // (D-045: rozmowa ma adres). Bez tej adaptacji reguły referencji nie mają w co trafić i cały
    // pasek wygląda tak, jak chce arkusz Studia. Adaptacja siedzi w generatorze
    // (`tools/frame_css.py --links`), więc regeneracja arkusza jej nie zgubi.
    expect(FRAME).toContain(".st .wm-navigation :is(button, a.st__item){");
    expect(FRAME).toContain(".st .wm-projects :is(button, a.st__item){");
    // I nie zamieniliśmy linków na przyciski: to byłaby regresja produktu (brak adresu, brak
    // otwarcia w nowej karcie) w imię zgodności z makietą, która przycisków użyła z braku adresów.
    const shell = fs.readFileSync(path.join(ROOT, "components/studio/shell.tsx"), "utf8");
    expect(shell).toContain('className="st__item"');
    expect(shell).not.toContain('<button className="st__item"');
  });

  it("styles_every_frame_class_a_component_actually_renders", () => {
    const styled = new Set(Array.from(FRAME.matchAll(/\.(wm-[a-z0-9-]+)/g)).map((m) => m[1]));
    const used = new Set<string>();
    for (const file of sources()) {
      const text = fs.readFileSync(file, "utf8");
      for (const match of text.matchAll(/wm-[a-z0-9-]+/g)) used.add(match[0]);
    }
    // Liczba najpierw: pusty zbiór przeszedłby pętlę niżej i nie sprawdziłby niczego.
    expect(used.size).toBeGreaterThan(10);
    const missing = [...used].filter((one) => !styled.has(one)).sort();
    expect(missing, "klasy ramy bez reguły w arkuszu").toEqual([]);
  });
});
