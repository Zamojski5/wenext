/**
 * Granica między projekcją serwera a ekranem kart (D-046).
 *
 * KAŻDY PRZYPADEK TO JEDEN SPOSÓB, W JAKI KARTA MOGŁABY SKŁAMAĆ, gdyby ten plik jej nie
 * zatrzymał. Wszystkie pochodzą z `docs/produkt/KARTY-ROZMOWY.md` i wszystkie są o tym samym:
 * ekran nie ma prawa twierdzić więcej, niż dostał.
 */
import { describe, expect, it } from "vitest";

import { actionOf, cardText, parseCard, parseCards, parseProgress } from "../lib/cards-schema";

const ACTION = {
  nazwa: "polacz",
  trasa: { metoda: "POST", sciezka: "/api/workspaces/w1/accounts/connect" },
  payload: { platforma: "REDDIT" },
};

function card(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    karta_id: "konto:reddit:anna",
    rodzaj: "KONTO",
    stan: "brak_polaczenia",
    powod: null,
    akcje: [ACTION],
    od_seq: 4,
    dane: { platforma: "REDDIT", profil: "Anna" },
    ...overrides,
  };
}

describe("karta rozmowy", () => {
  it("accepts_the_shape_the_contract_promises", () => {
    const parsed = parseCard(card());
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.rodzaj).toBe("KONTO");
      expect(cardText(parsed.value, "profil")).toBe("Anna");
      expect(actionOf(parsed.value, "polacz")?.trasa.metoda).toBe("POST");
    }
  });

  it("a_state_outside_the_dictionary_breaks_the_read", () => {
    // Karta o nieznanym stanie musiałaby zostać narysowana jakkolwiek, a „jakkolwiek" przy
    // koncie znaczy „Połączono" nad kontem, które połączone nie jest.
    expect(parseCard(card({ stan: "prawie_polaczone" })).ok).toBe(false);
    // I stan Z INNEGO RODZAJU też jest nieznany: `dolaczono` na karcie konta nie znaczy nic.
    expect(parseCard(card({ stan: "dolaczono" })).ok).toBe(false);
  });

  it("an_error_without_a_reason_is_a_broken_read", () => {
    // Błąd bez powodu jest gorszy niż brak karty: widać, że nie wyszło, i nie ma z tym co zrobić.
    expect(parseCard(card({ stan: "blad", powod: null })).ok).toBe(false);
    expect(parseCard(card({ stan: "blad", powod: "   " })).ok).toBe(false);
    expect(parseCard(card({ stan: "blad", powod: "Konto wymaga ponownego logowania" })).ok).toBe(
      true,
    );
  });

  it("an_action_without_a_route_is_refused", () => {
    // Przycisk, który po kliknięciu musiałby zgadnąć, co wysłać, nie jest przyciskiem.
    const broken = [{ nazwa: "polacz", payload: {} }, { nazwa: "polacz", trasa: {}, payload: {} }];
    for (const akcja of broken) {
      expect(parseCard(card({ akcje: [akcja] })).ok, JSON.stringify(akcja)).toBe(false);
    }
  });

  it("an_action_pointing_off_this_origin_is_refused", () => {
    // Karta idzie przez przeglądarkę. Adres bezwzględny w jej ładunku byłby sposobem na
    // wysłanie sesji operatora gdzie indziej; handler sprawdza to drugi raz, po swojemu.
    for (const sciezka of ["https://example.test/api", "//example.test/api", "api/relative"]) {
      const akcja = { ...ACTION, trasa: { metoda: "POST", sciezka } };
      expect(parseCard(card({ akcje: [akcja] })).ok, sciezka).toBe(false);
    }
  });

  it("cards_come_back_in_the_order_the_trace_gave_them", () => {
    const parsed = parseCards([
      card({ karta_id: "b", od_seq: 9 }),
      card({ karta_id: "a", od_seq: 2 }),
    ]);
    expect(parsed.ok).toBe(true);
    // Karta, która po odświeżeniu wskakuje nad poprzednią, wygląda jak nowa karta.
    if (parsed.ok) expect(parsed.value.map((one) => one.karta_id)).toEqual(["a", "b"]);
  });

  it("an_empty_list_is_a_valid_answer", () => {
    const parsed = parseCards([]);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value).toEqual([]);
  });
});

describe("postęp tury", () => {
  const progress = {
    naglowek: "Poznaję projekt",
    licznik: { zrobione: 1, wszystkie: 3 },
    pozycje: [
      { etykieta: "Poznaję cel", stan: "zrobione", podlinia: "Czytam ustalenia", zrodla: [] },
      { etykieta: "Szukam rozmów", stan: "trwa", podlinia: null, zrodla: ["r/SaaS"] },
    ],
    linia_zdarzenia: "Czytam pytania o pierwszych klientów",
  };

  it("accepts_the_shape_the_contract_promises", () => {
    const parsed = parseProgress(progress);
    expect(parsed.ok).toBe(true);
    if (parsed.ok && parsed.value !== null) {
      expect(parsed.value.licznik.wszystkie).toBe(3);
      expect(parsed.value.pozycje[1]?.zrodla).toEqual(["r/SaaS"]);
    }
  });

  it("a_turn_with_nothing_to_show_is_null_and_not_an_empty_shell", () => {
    // Kontrakt: gdy tura nie wywołała żadnego narzędzia z tabeli, `postep` jest `null`. Pusty
    // obiekt z licznikiem „0 / 0" wyglądałby jak praca, która stanęła.
    for (const empty of [null, undefined]) {
      const parsed = parseProgress(empty);
      expect(parsed.ok).toBe(true);
      if (parsed.ok) expect(parsed.value).toBeNull();
    }
  });

  it("a_stage_in_an_unknown_state_breaks_the_read", () => {
    const broken = { ...progress, pozycje: [{ ...progress.pozycje[0], stan: "prawie" }] };
    expect(parseProgress(broken).ok).toBe(false);
  });

  it("a_counter_that_is_not_two_numbers_breaks_the_read", () => {
    // Licznik jest jedyną liczbą na tym ekranie. Zgadnięty byłby liczbą, w którą ktoś uwierzy.
    expect(parseProgress({ ...progress, licznik: { zrobione: 1 } }).ok).toBe(false);
    expect(parseProgress({ ...progress, licznik: null }).ok).toBe(false);
  });
});
