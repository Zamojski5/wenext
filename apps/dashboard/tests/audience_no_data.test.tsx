/**
 * Zasięg: brak danych, nigdy zero, i nigdy suma (P6 etap A).
 *
 * DWIE ZASADY, OBIE ŁATWE DO ZŁAMANIA PRZY DOBRYCH INTENCJACH:
 *
 *   1. ZAKRES-FUNKCJONALNY §6 zasada 2: pomiar, którego nie było, pokazuje się jako brak, nie
 *      jako zero. Liczba obserwujących jest najbardziej kuszącym miejscem na zero, bo zero
 *      wygląda jak liczba, a „0 obserwujących" czyta się jak fakt o koncie, gdy jest faktem
 *      o nas: nikt tego nie odczytał i nie ma czym.
 *   2. Suma kont nie jest liczbą ludzi. Jedna osoba miewa trzy konta, a dwa konta bywają
 *      obserwowane przez tę samą osobę. Dodanie tych liczb daje coś, co wygląda na zasięg i nim
 *      nie jest. Ekran pisze zdanie zamiast sumy.
 */
import { describe, expect, it } from "vitest";

import { AUDIENCE_SUM_IS_NOT_PEOPLE, audienceNote } from "../lib/presence-schema";

const REDDIT = { label: "Reddit", audience_metrics: false };
const SOMEDAY = { label: "Kiedyś", audience_metrics: true };

describe("liczby o zasięgu", () => {
  it("an_account_whose_platform_cannot_be_read_says_brak_danych", () => {
    const note = audienceNote(REDDIT);
    expect(note).toContain("brak danych");
    // A zero must not appear anywhere in that sentence, in any shape.
    expect(note).not.toMatch(/\b0\b/);
  });

  it("an_unknown_platform_also_says_brak_danych", () => {
    expect(audienceNote(null)).toContain("brak danych");
  });

  it("the_sentence_blames_the_product_not_the_account", () => {
    // "ten produkt jeszcze nie umie" — a fact about us. "brak obserwujących" would be a claim
    // about the account, and we have not measured it.
    expect(audienceNote(REDDIT)).toContain("ten produkt jeszcze nie umie");
    expect(audienceNote(REDDIT)).not.toContain("nie ma obserwujących");
  });

  it("a_platform_that_could_be_read_says_only_that_nothing_was_read_yet", () => {
    // The day an adapter lands, this branch is what the screen shows until the first job runs.
    // Still not a zero.
    expect(audienceNote(SOMEDAY)).toBe("zasięg: brak odczytów");
  });

  it("a_measured_account_shows_its_count_the_moment_and_a_change_only_when_known", () => {
    // 27.09: X is read every hour from our own profile. The number comes with the moment of the
    // reading; the change over a day is said only when a reading a day older exists.
    const X = { label: "X", audience_metrics: true };
    const first = audienceNote(X, {
      obserwujacy: 2,
      odczyt: "2026-09-27T18:04:00+00:00",
      zmiana_doby: null,
    });
    expect(first).toBe("obserwujący: 2 (odczyt 27 września 2026, 20:04), zmiany od doby jeszcze nie znamy");
    const later = audienceNote(X, {
      obserwujacy: 7,
      odczyt: "2026-09-28T18:04:00+00:00",
      zmiana_doby: 5,
    });
    expect(later).toContain("obserwujący: 7");
    expect(later).toContain("od doby +5");
    // A platform nothing reads keeps its sentence even if a reading were somehow passed.
    expect(audienceNote(REDDIT, { obserwujacy: 3, odczyt: "2026-09-27T18:04:00+00:00", zmiana_doby: null })).toContain(
      "brak danych",
    );
  });

  it("the_screen_states_that_accounts_are_never_summed_into_people", () => {
    expect(AUDIENCE_SUM_IS_NOT_PEOPLE).toContain("nie sumujemy");
    expect(AUDIENCE_SUM_IS_NOT_PEOPLE).toContain("ta sama osoba");
    // And it says what the sum WOULD look like, because "nie sumujemy" alone reads as a
    // limitation rather than as a deliberate refusal.
    expect(AUDIENCE_SUM_IS_NOT_PEOPLE).toContain("zasięg");
  });
});
