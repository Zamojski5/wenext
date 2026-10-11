/**
 * Jak rozmowa nazywa się na ekranie (D-045), i dlaczego to jedno miejsce.
 *
 * NAZWĘ LICZY SERWER, I TYLKO SERWER (`sessions/summary.py::title_of`). Ten plik pilnuje, że
 * ekran bierze to, co dostał, i nie liczy niczego równolegle: gdyby liczył, jedna rozmowa
 * miałaby dwie nazwy, w pasku inną niż w nagłówku, i nikt by nie wiedział, która jest prawdziwa.
 * Wyliczanie tytułu z celu istniało tu przez jedno wdrożenie i zostało usunięte 20.09.
 */
import { describe, expect, it } from "vitest";

import {
  UNNAMED_CONVERSATION,
  conversationIsOver,
  conversationResult,
  conversationTitle,
  conversationsOf,
  isConversation,
} from "../lib/conversations";
import { session } from "./studio-fixtures";

describe("nazwa rozmowy", () => {
  it("the_title_from_the_server_is_used_verbatim", () => {
    const row = session({
      goal: "Sprawdź, dlaczego odzew jest mały. Potem zaproponuj zmianę.",
      tytul: "Odzew w r/devops",
    });
    // Cel jest tu dłuższy i ładniejszy od tytułu z rozmysłem: ekran ma wziąć tytuł, a nie
    // policzyć sobie własny, choćby wyszedł lepszy.
    expect(conversationTitle(row)).toBe("Odzew w r/devops");
  });

  it("a_server_that_says_there_is_no_title_gets_one_sentence_not_a_guess", () => {
    // `null` to odpowiedź serwera „z tego celu nie da się wyciąć zdania". Wyliczanie czegokolwiek
    // w tym przypadku liczyłoby z tych samych danych i podważało odpowiedź, którą dostaliśmy.
    const row = session({ goal: "cokolwiek tu jest, całkiem długie zdanie", tytul: null });
    expect(conversationTitle(row)).toBe(UNNAMED_CONVERSATION);
  });

  it("a_row_never_ends_up_without_a_label_to_click", () => {
    // Pusty wiersz w pasku nie ma w co kliknąć i wygląda na usterkę renderowania. Sprawdzane na
    // wszystkim, co serwer może przysłać zamiast nazwy.
    for (const tytul of [null, "", "   "]) {
      expect(conversationTitle(session({ tytul })), JSON.stringify(tytul)).toBe(
        UNNAMED_CONVERSATION,
      );
    }
  });
});

describe("wynik rozmowy jednym zdaniem", () => {
  it("the_sentence_from_the_server_is_used_and_the_long_summary_is_not_cut_here", () => {
    const row = session({
      result_summary: "Długie podsumowanie z wieloma zdaniami. I jeszcze jedno.",
      wynik_zdanie: "Zmieniliśmy porę publikacji.",
    });
    // Skracanie jest na serwerze. Gdyby ekran skracał `result_summary` sam, pasek pokazywałby
    // inne zdanie niż lista rozmów przy tej samej rozmowie.
    expect(conversationResult(row)).toBe("Zmieniliśmy porę publikacji.");
  });

  it("the_models_long_dash_is_softened_where_the_sentence_is_shown", () => {
    // Measured 27.09 on the project page: "Gotowe \u2014 wybrałem roboczo developerów React".
    const row = session({ wynik_zdanie: "Gotowe \u2014 wybrałem roboczo developerów React." });
    expect(conversationResult(row)).toBe("Gotowe, wybrałem roboczo developerów React.");
  });

  it("a_conversation_that_has_said_nothing_yet_says_nothing", () => {
    // NIE „brak wyniku": rozmowa w toku bez wyniku jest normalna, a napis o braku wygląda jak
    // usterka i po tygodniu ludzie przestają czytać tę kolumnę. Pełne podsumowanie NIE zastępuje
    // zdania: jeśli serwer go nie policzył, ekran milczy.
    expect(conversationResult(session())).toBeNull();
    expect(conversationResult(session({ wynik_zdanie: null }))).toBeNull();
    expect(
      conversationResult(session({ wynik_zdanie: null, result_summary: "Coś się wydarzyło." })),
    ).toBeNull();
  });
});

describe("co jest rozmową, a co tikiem obecności", () => {
  it("a_presence_tick_is_not_a_conversation", () => {
    expect(isConversation(session({ rodzaj: "PRESENCE" }))).toBe(false);
    expect(isConversation(session({ rodzaj: "CONVERSATION" }))).toBe(true);
  });

  it("without_the_field_nothing_is_hidden", () => {
    // Filtr jest po stronie serwera. Dopóki nie działa, lepiej pokazać za dużo niż ukryć
    // rozmowę, której nie da się odróżnić: zgadywanie po statusie albo po celu byłoby ukryciem
    // czyjejś pracy na podstawie przeczucia.
    expect(isConversation(session())).toBe(true);
    const rows = [session(), session({ session_id: "x", rodzaj: "PRESENCE" })];
    expect(conversationsOf(rows).length).toBe(1);
  });
});

describe("rozmowa zamknięta", () => {
  it("three_statuses_end_a_conversation_and_the_rest_do_not", () => {
    for (const status of ["DONE", "FAILED", "BUDGET_EXHAUSTED"] as const) {
      expect(conversationIsOver(session({ status })), status).toBe(true);
    }
    for (const status of ["QUEUED", "RUNNING", "WAITING_USER", "WAITING_EVENT", "PAUSED"] as const) {
      expect(conversationIsOver(session({ status })), status).toBe(false);
    }
  });
});
