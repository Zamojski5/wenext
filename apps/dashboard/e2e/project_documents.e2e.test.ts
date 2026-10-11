/**
 * „Dokumenty projektu" od strony człowieka, przez prawdziwy stack (P8 etap A).
 *
 *   przeglądarka → strona projektu → /api/.../documents → PostgreSQL
 *
 * CZEGO NIE SPRAWDZI TEST KOMPONENTU. `tests/documents_block.test.tsx` renderuje sekcję z
 * podanych danych, więc przechodzi także wtedy, gdy strona projektu nigdy nie zapyta control
 * plane'u, gdy zapyta o złą przestrzeń, albo gdy `?dokument=` nie doczyta treści. Tu jedynym
 * źródłem zdań jest baza, a zdania są zasiane przez generator, czyli przez jedyny kod, który
 * potrafi je zapisać.
 *
 * DLACZEGO WERSJA 2 JEST W ZASIEWIE. „Co działa" dostaje drugą wersję, a pozostałe dokumenty
 * zostają na pierwszej. Bez tej różnicy suita nie odróżniłaby „ekran pokazuje najnowszą wersję"
 * od „ekran pokazuje jedyną, jaka jest", a to jest dokładnie ta pomyłka, która ujawnia się
 * dopiero po pierwszym przepisaniu dokumentów na żywo.
 *
 * ETAP A JEST TYLKO DO ODCZYTU i to też jest tu sprawdzane. Przycisk „Przepisz" oraz porównanie
 * wersji wchodzą w etapie C, więc dopóki nie wejdą, sekcja nie ma prawa mieć niczego, co zmienia
 * stan.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  "WENEXT_E2E_LIVE_PROJECT_ID",
  "WENEXT_E2E_DOCUMENT_ID",
  "WENEXT_E2E_DOCUMENT_SENTENCE",
  "WENEXT_E2E_DOCUMENT_REMOVED_SENTENCE",
] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};

function required(name: Required): string {
  const value = environment[name];
  if (value === undefined) throw new Error(`brak ${name}`);
  return value;
}

function url(pathname: string): string {
  return `${required("WENEXT_E2E_BASE_URL").replace(/\/+$/, "")}${pathname}`;
}

async function logIn(): Promise<string> {
  const response = await fetch(url("/login/submit"), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      password: required("WENEXT_E2E_OPERATOR_PASSWORD"),
    }).toString(),
    redirect: "manual",
  });
  const header = response.headers
    .getSetCookie()
    .find((value) => value.startsWith("wenext_dashboard_handle="));
  if (header === undefined) throw new Error("logowanie nie odesłało ciasteczka");
  return header.split(";")[0] ?? "";
}

let cookie = "";
let workspaceId = "";
let projectId = "";

/**
 * Ekran „Wiedza" projektu, czyli od E2 miejsce dokumentów.
 *
 * Do 20.09 sekcja stała na WEJŚCIU do projektu i ten test czytał właśnie tamtą stronę. Element
 * E2 ściął wejście do pytania i listy rozmów, a dokumenty przeniósł tam, dokąd kieruje pozycja
 * „Wiedza" w pasku. Test poszedł za nimi: gdyby został pod starym adresem, przestałby mierzyć
 * cokolwiek o dokumentach, a mówiłby tylko, że na wejściu ich nie ma.
 */
async function documentsPage(query = ""): Promise<string> {
  const response = await fetch(url(`/w/${workspaceId}/p/${projectId}/knowledge${query}`), {
    headers: { cookie },
    redirect: "manual",
  });
  expect(response.status).toBe(200);
  return await response.text();
}

/** Sam kawałek HTML-u należący do sekcji dokumentów, żeby asercje nie łapały reszty strony. */
function documentsSection(body: string): string {
  const start = body.indexOf('data-block="dokumenty"');
  expect(start).toBeGreaterThan(-1);
  const end = body.indexOf("</section>", start);
  expect(end).toBeGreaterThan(start);
  return body.slice(start, end);
}

beforeAll(async () => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") missing.push(name);
    else environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(
      `BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie.`,
    );
  }
  cookie = await logIn();
  workspaceId = required("WENEXT_E2E_WORKSPACE_ID");
  projectId = required("WENEXT_E2E_LIVE_PROJECT_ID");
});

describe("P8 etap A: dokumenty projektu na ekranie", () => {
  it("the_knowledge_screen_lists_the_documents_the_generator_built", async () => {
    const section = documentsSection(await documentsPage());

    expect(section).toContain("Dokumenty projektu");
    // Cztery role, każda nazwana po ludzku. Kolejność jest decyzją produktu i widać ją tutaj.
    for (const label of ["Karta projektu", "Co działa", "Decyzje", "Społeczności"]) {
      expect(section, label).toContain(label);
    }
    expect(section.indexOf("Karta projektu")).toBeLessThan(section.indexOf("Co działa"));
    expect(section.indexOf("Co działa")).toBeLessThan(section.indexOf("Decyzje"));
  });

  it("the_rewritten_document_shows_its_newest_version_and_the_others_show_theirs", async () => {
    // Zasiew woła generator dwa razy z jednym nowym wynikiem pomiędzy, więc „Co działa" ma
    // wersję 2, a reszta wersję 1. Ekran, który bierze pierwszą lepszą wersję, pokaże tu 1.
    const section = documentsSection(await documentsPage());
    // Wycinek OD identyfikatora dokumentu DO końca jego nagłówka. Bez domknięcia na `</summary>`
    // asercja łapałaby też wersje dokumentów stojących niżej i przechodziłaby przez pomyłkę.
    const start = section.indexOf(required("WENEXT_E2E_DOCUMENT_ID"));
    expect(start).toBeGreaterThan(-1);
    const head = section.slice(start, section.indexOf("</summary>", start));

    expect(head).toContain("Co działa");
    expect(head).toContain("wersja 2");
    expect(head).not.toContain("wersja 1");
    // Reszta dokumentów została na pierwszej wersji, bo ich wejście się nie zmieniło.
    expect(section).toContain("wersja 1");
  });

  it("opening_a_document_shows_its_sentences_from_the_database", async () => {
    const section = documentsSection(
      await documentsPage(`?dokument=${required("WENEXT_E2E_DOCUMENT_ID")}`),
    );

    // Oba zdania z zasiewu: jedno z pierwszej wersji, jedno dołożone w drugiej. Drugie jest
    // dowodem, że ekran czyta wersję 2, a nie tylko jej numer.
    expect(section).toContain(required("WENEXT_E2E_DOCUMENT_SENTENCE"));
    expect(section).toContain(required("WENEXT_E2E_DOCUMENT_REMOVED_SENTENCE"));
    expect(section).toContain("Zadziałało");
    expect(section).toContain("Nie zadziałało");
  });

  it("every_sentence_on_screen_says_how_many_findings_stand_behind_it", async () => {
    const section = documentsSection(
      await documentsPage(`?dokument=${required("WENEXT_E2E_DOCUMENT_ID")}`),
    );

    const sentences = section.match(/data-basis="(\d+)"/g) ?? [];
    expect(sentences.length).toBeGreaterThan(0);
    // Zero znaczyłoby zdanie bez oparcia. Baza tego nie przyjmie, ale ekran liczy tę liczbę
    // sam i mógłby ją zgubić po drodze, a wtedy nikt by się nie dowiedział.
    for (const sentence of sentences) expect(sentence).not.toBe('data-basis="0"');
    expect(section).toContain("ustalenie");
  });

  it("the_section_is_read_only_and_speaks_no_database_words", async () => {
    const section = documentsSection(
      await documentsPage(`?dokument=${required("WENEXT_E2E_DOCUMENT_ID")}`),
    );

    for (const forbidden of ["<form", "<button", "Przepisz", "Porównaj"]) {
      expect(section, forbidden).not.toContain(forbidden);
    }
    for (const raw of ["WHAT_WORKS", "PROJECT_CARD", "RULES"]) {
      // `data-role` niesie rolę maszynowo i to jest w porządku; chodzi o TEKST dla człowieka.
      const visible = section.replace(/data-role="[A-Z_]+"/g, "");
      expect(visible, raw).not.toContain(raw);
    }
  });

  it("an_unknown_document_id_does_not_break_the_page", async () => {
    // Odsyłacz z zakładki po skasowaniu dokumentu albo przeklejony z innego projektu. Strona ma
    // się wyświetlić z listą, a nie oddać 500 ani zniknąć razem z rozmową.
    const body = await documentsPage("?dokument=11111111-2222-4333-8444-555555555555");
    const section = documentsSection(body);

    expect(section).toContain("Dokumenty projektu");
    expect(section).toContain("Co działa");
    // Reszta ekranu „Wiedza" też stoi. Sprawdzane po liczniku materiałów, bo on jest tym, co na
    // tym ekranie jest zawsze; lista rozmów została na wejściu do projektu (E2).
    expect(body).toContain('data-count="sources"');
  });
});
