/**
 * Linia aktywności nad polem rozmowy (E-H): jedno zdanie zamiast raportu.
 *
 * SKĄD SIĘ WZIĘŁA. Referencja ramy ma nad polem „• Obserwuje · 3 nowe działania ↗ 23:12". My
 * mieliśmy w tym miejscu blok „Od Twojej ostatniej wizyty" z licznikami obudzeń, przez który
 * audyt UX nazwał wejście do projektu ekranem administracyjnym. Blok poszedł na „Aktywność";
 * ta linia jest tym, co z niego zostaje tam, gdzie człowiek zaczyna pisać.
 *
 * CZEGO PILNUJĄ TE TESTY, bo każdy z tych błędów byłby zdaniem o produkcie, którego nikt nie
 * zmierzył:
 *
 *   1. liczba to DZIAŁANIA, nie obudzenia. Obudzenie pominięte („nic nowego") jest pracą
 *      kolektora; policzone jako działanie mówiłoby „trzy nowe działania" o nocy, w której nic
 *      się nie stało;
 *   2. cisza MÓWI, że jest cisza. Linia, która znika, czyta się jak „nie wiadomo", a to jest
 *      inna odpowiedź;
 *   3. nieudany odczyt nie udaje ciszy: wtedy linii nie ma w ogóle.
 */
import { describe, expect, it } from "vitest";

import { ActivityLine } from "../components/studio/activity-line";
import type { ReturnView } from "../lib/returns-schema";
import { renderToDocument } from "./render";

const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const PROJECT = "22222222-2222-4222-8222-222222222222";

function view(overrides: Partial<ReturnView> = {}): ReturnView {
  return {
    okno: { od: null, do: "2026-09-21T06:00:00+00:00", pierwsza_wizyta: false, zdanie: "x" },
    obudzenia: { razem: 3, pominiete: 2, z_sesja: 1, powody: {}, powody_pominiecia: {} },
    sesje: [],
    decyzje: [],
    publikacje: [],
    korekty: [],
    incydenty: [],
    uwagi: [],
    cisza: false,
    ...overrides,
  };
}

function render(props: Record<string, unknown> = {}) {
  return renderToDocument(
    <ActivityLine projectId={PROJECT} view={view()} workspaceId={WORKSPACE} {...props} />,
  );
}

describe("linia aktywności", () => {
  it("does_not_claim_observation_or_silence_without_wakeups", () => {
    const screen = render({view: view({obudzenia: {razem: 0, pominiete: 0, z_sesja: 0, powody: {}, powody_pominiecia: {}}})});
    expect(screen.body.textContent).toContain("brak uruchomień");
    expect(screen.body.textContent).not.toContain("Cicho");
    expect(screen.body.textContent).not.toContain("Obserwuję");
  });
  it("counts_what_happened_and_not_how_often_the_collector_woke_up", () => {
    // Trzy obudzenia, z tego dwa pominięte: jedno działanie, nie trzy.
    const screen = render();
    expect(screen.querySelector("[data-activity]")?.getAttribute("data-activity")).toBe("1");
    expect(screen.body.textContent).toContain("1 nowe działanie");
    expect(screen.body.textContent).toContain("Praca w tle");
  });

  it("silence_says_so_instead_of_disappearing", () => {
    const cisza = render({
      view: view({ obudzenia: { razem: 4, pominiete: 4, z_sesja: 0, powody: {}, powody_pominiecia: {} } }),
    });
    expect(cisza.querySelector(".st-activity")).not.toBeNull();
    expect(cisza.body.textContent).toContain("Cicho");
    expect(cisza.body.textContent).toContain("nic nowego");
    // I nigdy „0 działań": zero mówi się słowem, bo liczba każe liczyć.
    expect(cisza.body.textContent).not.toContain("0 ");
  });

  it("a_failed_read_shows_nothing_rather_than_pretending_it_was_quiet", () => {
    // Komponent oddaje `null`, więc markup jest PUSTY i `renderToDocument` (słusznie) na to
    // krzyczy: jego zadaniem jest łapać ekran, który nic nie wyrenderował. Opakowanie w `div`
    // daje czego szukać, a pustka zostaje pustką.
    const brak = renderToDocument(
      <div data-test-wrapper="1">
        <ActivityLine projectId={PROJECT} view={undefined} workspaceId={WORKSPACE} />
      </div>,
    );
    expect(brak.querySelector("[data-test-wrapper]")).not.toBeNull();
    expect(brak.querySelector(".st-activity")).toBeNull();
    // Cisza i brak odczytu to DWIE różne odpowiedzi: pierwsza mówi „nic się nie działo", druga
    // nie mówi nic, bo nie wie.
    expect(brak.body.textContent).not.toContain("Cicho");
  });

  it("the_number_leads_to_the_screen_that_has_the_whole_story", () => {
    const screen = render();
    expect(screen.querySelector(".st-activity a")?.getAttribute("href")).toBe(
      `/w/${WORKSPACE}/p/${PROJECT}/presence`,
    );
  });

  it("declines_the_plural_like_a_person_writes_it", () => {
    const five = render({
      view: view({ obudzenia: { razem: 9, pominiete: 4, z_sesja: 5, powody: {}, powody_pominiecia: {} } }),
    });
    expect(five.body.textContent).toContain("5 nowych działań");
    const three = render({
      view: view({ obudzenia: { razem: 3, pominiete: 0, z_sesja: 3, powody: {}, powody_pominiecia: {} } }),
    });
    expect(three.body.textContent).toContain("3 nowe działania");
  });
});
