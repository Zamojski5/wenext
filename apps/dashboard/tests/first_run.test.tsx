/**
 * Pomoc pierwszego uruchomienia: pięć zdań w pustych stanach, jedna kolejność.
 *
 * CZEGO PILNUJĄ TE TESTY, bo pomoc psuje się ciszej niż kod:
 *
 *   * zdanie wskazuje ekran, którego już nie ma pod tą nazwą, więc czytający szuka czegoś, czego
 *     nie znajdzie. Nazwy są sprawdzane z `labelOf`, czyli z tym, co naprawdę jest w nawigacji;
 *   * któryś krok wypada z ekranów przy przebudowie i zostaje dziura w kolejności;
 *   * do tekstu wraca długa pauza (zasada z 4 września);
 *   * pomoc ZASTĘPUJE zdanie pustego stanu, przez co znika informacja „odczyt się udał, po prostu
 *     nic tu nie ma", odróżniająca pustkę od awarii odczytu.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  FIRST_RUN_SENTENCES,
  FIRST_RUN_TOTAL,
  FirstRun,
  type FirstRunStep,
} from "../components/studio/first-run";
import { renderToDocument } from "./render";

const STEPS: readonly FirstRunStep[] = [1, 2, 3, 4, 5];
/**
 * Gdzie szukamy użycia kroków: ekrany ORAZ komponenty studia.
 *
 * Do 20.09 wystarczyły same strony, bo każdy krok stał w pliku ekranu. Po przeniesieniu wyboru
 * kont pod tytuł projektu (E2) krok 3 mieszka w `components/studio/assignment.tsx`, który jest
 * renderowany przez stronę projektu. Skan po samych stronach ogłosiłby go nieużywanym, choć
 * człowiek go widzi, a to jest dokładnie odwrotność tego, czego ten test pilnuje.
 */
const SCREENS = [
  path.join(process.cwd(), "app/(studio)"),
  path.join(process.cwd(), "components/studio"),
];

/** Wszystkie pliki ekranów studia, żeby skan nie chodził po katalogu, którego nie ma. */
function screenFiles(directory?: string): readonly string[] {
  if (directory === undefined) return SCREENS.flatMap((one) => screenFiles(one));
  const found: string[] = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...screenFiles(full));
    else if (entry.name.endsWith(".tsx")) found.push(full);
  }
  return found;
}

describe("pomoc pierwszego uruchomienia", () => {
  it("renders_contextual_help_without_a_mandatory_sequence", () => {
    expect(STEPS.length).toBe(FIRST_RUN_TOTAL);
    for (const step of STEPS) {
      const rendered = renderToDocument(<FirstRun step={step} />);
      const hint = rendered.querySelector(`[data-first-run="${step}"]`);
      expect(hint, `krok ${step} nie wyrenderował się`).not.toBeNull();
      expect(hint?.textContent).not.toContain(`krok ${step} z 5`);
      expect(hint?.textContent).toContain(FIRST_RUN_SENTENCES[step]);
    }
  });

  it("research_does_not_require_social_accounts_or_a_delegation", () => {
    expect(FIRST_RUN_SENTENCES[1]).toContain("bez kont społecznościowych");
    expect(FIRST_RUN_SENTENCES[3]).toContain("także bez nich");
    const rendered = renderToDocument(<FirstRun step={3} />);
    expect(rendered.body.textContent).not.toContain("krok 3 z 5");
  });

  it("every_step_is_used_on_a_screen_and_no_step_is_invented", () => {
    const sources = screenFiles();
    expect(sources.length).toBeGreaterThan(5);
    const used = new Set<number>();
    for (const file of sources) {
      const text = fs.readFileSync(file, "utf8");
      for (const match of text.matchAll(/<FirstRun step=\{(\d)\}/g)) {
        used.add(Number(match[1]));
      }
    }
    // Krok 3 wrócił na ekran razem z odsyłaczem „Przypisz konta" pod tytułem projektu (E2).
    // Przez jedną turę nie miał gdzie stać, bo formularz przypisania zniknął z wejścia do
    // projektu razem z blokiem administracyjnym; referencja ma ten wybór przy tytule i tam
    // wrócił, jako cicha linia z popoverem, a nie jako sekcja.
    expect([...used].sort()).toEqual([1, 2, 3, 4, 5]);
  });

  it("adds_to_the_empty_state_instead_of_replacing_what_it_said", () => {
    // Zdanie „odczyt się udał, po prostu nic tu nie ma" odróżnia pustkę od awarii i musi zostać
    // na ekranach, na których było przed dopisaniem pomocy.
    const kept = [
      "app/(studio)/w/[workspaceId]/page.tsx",
      "app/(studio)/w/[workspaceId]/people/page.tsx",
      "app/(studio)/w/[workspaceId]/codex/page.tsx",
    ];
    for (const file of kept) {
      const text = fs.readFileSync(path.join(process.cwd(), file), "utf8");
      expect(text, file).toContain("Odczyt się udał");
      expect(text, file).toContain("FirstRun");
    }
  });

  it("keeps_the_founders_rule_about_long_dashes", () => {
    for (const step of STEPS) {
      expect(FIRST_RUN_SENTENCES[step]).not.toContain("—");
      expect(FIRST_RUN_SENTENCES[step]).not.toContain("–");
    }
  });

  it("stays_short_enough_that_somebody_reads_it", () => {
    for (const step of STEPS) {
      // Dwa zdania, wyjątkowo trzy. Instrukcja na akapit jest instrukcją, którą się pomija.
      expect(FIRST_RUN_SENTENCES[step].length).toBeLessThanOrEqual(220);
      expect(FIRST_RUN_SENTENCES[step].split(". ").length).toBeLessThanOrEqual(3);
    }
  });
});
