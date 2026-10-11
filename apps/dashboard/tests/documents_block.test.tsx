/**
 * Sekcja „Dokumenty projektu" i jej odczyt (P8 etap A, tylko do odczytu).
 *
 * CZEGO PILNUJĄ TE TESTY. Dokument jest WIDOKIEM rejestru ustaleń, nie drugą pamięcią, więc
 * każdy przypadek niżej to jedna droga, którą ekran mógłby to zatrzeć:
 *
 *   * zdanie pokazane bez tego, ile ustaleń za nim stoi, czyli wniosek nie do odróżnienia od
 *     wrażenia;
 *   * pusta sekcja wyglądająca jak awaria zamiast jak „jeszcze nic nie domknięto";
 *   * nieudany odczyt pokazany jako brak dokumentów;
 *   * przycisk „Przepisz" albo porównywanie wersji, które należą dopiero do etapu C;
 *   * słowa z bazy (`WHAT_WORKS`, `RULES`) na ekranie.
 */
import { describe, expect, it } from "vitest";

import { DocumentsBlock } from "../components/studio/documents";
import { parseDocumentDetail, parseDocumentIndex } from "../lib/documents-schema";
import type { DocumentDetail, DocumentEntry } from "../lib/documents-schema";
import { renderToDocument } from "./render";

const ENTRY: DocumentEntry = {
  document_id: "dddddddd-0000-4000-8000-000000000001",
  rola: "WHAT_WORKS",
  platforma: null,
  tytul: "Co działa w tym projekcie",
  wersja: 3,
  wygenerowano: "2026-09-20T07:00:00+00:00",
  zrodlo: "RULES",
  liczba_zdan: 2,
};

const DETAIL: DocumentDetail = {
  ...ENTRY,
  zdania: [
    {
      pozycja: 1,
      sekcja: "Działa",
      tekst: "Odpowiedzi z liczbami z produkcji zostają widoczne dłużej niż dobę.",
      podstawa: ["11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002"],
      liczba_podstaw: 2,
    },
    {
      pozycja: 2,
      sekcja: "Nie działa",
      tekst: "Komentarze z linkiem w pierwszym zdaniu znikają.",
      podstawa: ["33333333-0000-4000-8000-000000000003"],
      liczba_podstaw: 1,
    },
  ],
};

function render(props: Parameters<typeof DocumentsBlock>[0]) {
  return renderToDocument(<DocumentsBlock {...props} />);
}

describe("sekcja dokumentów projektu", () => {
  it("an_empty_section_points_at_the_conversation_and_asks_for_nothing", () => {
    // D-044: rozmowa jest głównym interfejsem, a ten ekran jest widokiem tego, co z niej
    // zostało. Pusta sekcja ma powiedzieć, skąd wezmą się dokumenty, a nie prosić o wpisanie
    // czegokolwiek: żadna funkcja nie może wymagać formularza tam, gdzie wystarczy rozmowa.
    const rendered = render({ documents: [] });
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("Nie ma jeszcze żadnego dokumentu");
    expect(text).toContain("z rozmowy");
    expect(text).toContain("Nie ma tu nic do wpisania");
    for (const forbidden of ["Wpisz", "Uzupełnij", "Dodaj dokument", "Wypełnij"]) {
      expect(text, forbidden).not.toContain(forbidden);
    }
    // Pustka to nie awaria i nie ma o niej mówić tonem awarii.
    expect(rendered.querySelector("[data-tone='absent']")).toBeNull();
  });

  it("a_failed_read_is_not_shown_as_no_documents", () => {
    const rendered = render({ reason: "control plane odpowiedział 503" });
    expect(rendered.body.textContent).toContain("Nie udało się odczytać dokumentów");
    expect(rendered.body.textContent).toContain("To nie znaczy, że ich nie ma");
    expect(rendered.body.textContent).not.toContain("Nie ma jeszcze żadnego dokumentu");
  });

  it("the_list_names_the_role_in_human_words_and_never_in_database_words", () => {
    const rendered = render({ documents: [ENTRY] });
    expect(rendered.body.textContent).toContain("Co działa");
    expect(rendered.body.textContent).toContain("wersja 3");
    expect(rendered.body.textContent).toContain("2 zdania");
    expect(rendered.body.textContent).toContain("z reguł, bez modelu");
    expect(rendered.body.textContent).not.toContain("WHAT_WORKS");
    expect(rendered.body.textContent).not.toContain("RULES");
  });

  it("every_sentence_says_how_many_findings_stand_behind_it", () => {
    const rendered = render({ documents: [ENTRY], open: DETAIL });
    const sentences = rendered.querySelectorAll("[data-basis]");
    expect(sentences.length).toBe(2);
    expect(sentences[0]?.textContent).toContain("(2 ustalenia)");
    expect(sentences[1]?.textContent).toContain("(1 ustalenie)");
    // Sekcje dokumentu są nagłówkami, nie zdaniem z dwukropkiem.
    expect(rendered.querySelector(".st-doc__section")?.textContent).toBe("Działa");
  });

  it("stage_A_has_no_rewrite_button_and_no_version_comparison", () => {
    // Jedno i drugie wchodzi w etapie C. Sekcja tylko do odczytu nie ma mieć nic, co zmienia stan.
    const rendered = render({ documents: [ENTRY], open: DETAIL });
    expect(rendered.querySelector("form")).toBeNull();
    expect(rendered.querySelector("button")).toBeNull();
    expect(rendered.body.textContent).not.toContain("Przepisz");
    expect(rendered.body.textContent).not.toContain("Porównaj");
  });

  it("a_document_without_sections_gets_no_empty_heading", () => {
    // 0028 pozwala na `section = ''`: nie każdy dokument dzieli się na części. Nagłówek bez
    // tekstu byłby pustą linią udającą podział.
    const rendered = render({
      documents: [ENTRY],
      open: {
        ...ENTRY,
        zdania: [
          {
            pozycja: 0,
            sekcja: "",
            tekst: "Jedno zdanie bez sekcji.",
            podstawa: ["11111111-0000-4000-8000-000000000001"],
            liczba_podstaw: 1,
          },
        ],
      },
    });
    expect(rendered.querySelector(".st-doc__section")).toBeNull();
    expect(rendered.body.textContent).toContain("Jedno zdanie bez sekcji.");
  });

  it("sentences_keep_the_order_the_generator_gave_them_starting_at_zero", () => {
    // Pozycje są liczone OD ZERA (więz z 0028); ekran nie ma ich przenumerowywać ani sortować
    // po czymkolwiek innym niż to, co przyszło.
    const rendered = render({
      documents: [ENTRY],
      open: {
        ...ENTRY,
        zdania: [
          { pozycja: 0, sekcja: null, tekst: "pierwsze", podstawa: ["a"], liczba_podstaw: 1 },
          { pozycja: 1, sekcja: null, tekst: "drugie", podstawa: ["b"], liczba_podstaw: 1 },
        ],
      },
    });
    const texts = Array.from(rendered.querySelectorAll("[data-basis]")).map((node) =>
      (node.textContent ?? "").trim(),
    );
    expect(texts[0]).toContain("pierwsze");
    expect(texts[1]).toContain("drugie");
  });
});

describe("odczyt dokumentów", () => {
  const entryJson = {
    document_id: ENTRY.document_id,
    rola: "WHAT_WORKS",
    platforma: null,
    tytul: ENTRY.tytul,
    wersja: 3,
    wygenerowano: ENTRY.wygenerowano,
    zrodlo: "RULES",
    liczba_zdan: 2,
  } as Record<string, unknown>;

  it("accepts_the_shape_the_contract_promises", () => {
    expect(parseDocumentIndex({ dokumenty: [entryJson] }).ok).toBe(true);
    const detail = parseDocumentDetail({
      ...entryJson,
      zdania: [
        {
          pozycja: 1,
          sekcja: "Działa",
          tekst: "cokolwiek",
          podstawa: ["11111111-0000-4000-8000-000000000001"],
          liczba_podstaw: 1,
        },
      ],
    });
    expect(detail.ok).toBe(true);
  });

  it("a_sentence_without_a_basis_breaks_the_read_instead_of_showing_up_bare", () => {
    // Baza zabrania zdania bez podstawy (więz z 0028). Jeśli takie przyszło, rozmawiamy z czymś,
    // co nie jest tym API, i lepiej powiedzieć to wprost niż pokazać twierdzenie bez oparcia.
    const parsed = parseDocumentDetail({
      ...entryJson,
      zdania: [{ pozycja: 1, sekcja: null, tekst: "bez oparcia", podstawa: [], liczba_podstaw: 0 }],
    });
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("podstawą");
  });

  it("an_unknown_role_or_source_is_a_broken_read", () => {
    for (const broken of [{ rola: "COS_INNEGO" }, { zrodlo: "RECZNIE" }]) {
      const parsed = parseDocumentIndex({ dokumenty: [{ ...entryJson, ...broken }] });
      expect(parsed.ok, JSON.stringify(broken)).toBe(false);
    }
  });

  it("an_empty_index_is_a_valid_answer_not_a_failure", () => {
    const parsed = parseDocumentIndex({ dokumenty: [] });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value).toEqual([]);
  });
});
