/**
 * „Od Twojej ostatniej wizyty" od strony człowieka, przez prawdziwy stack (P4 i P5).
 *
 *   przeglądarka → strona projektu → /api/.../return → PostgreSQL
 *
 * CO TO SPRAWDZA, CZEGO NIE SPRAWDZI TEST KOMPONENTU: że sekcja naprawdę stoi na ekranie
 * „Aktywność" (od E2; wcześniej na wejściu do projektu), liczy z zasianych wierszy i że jedno
 * zdanie korekty wysłane formularzem dociera do rozmowy (od E5; wcześniej wracało do tego
 * samego widoku jako zapisana reguła). Trzy rzeczy, z których każda działa osobno w testach
 * jednostkowych i mogła nigdy nie zostać połączona.
 *
 * ZASIEW: projekt z sesją RUNNING i trzema obudzeniami (jedno z sesją, dwa pominięte) z
 * `tools/e2e_seed.py`. Pominięte są tam po to, żeby widok miał czym odróżnić spokojną dobę od
 * zepsutego kolektora.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  "WENEXT_E2E_LIVE_PROJECT_ID",
] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};
const STAMP = Math.random().toString(36).slice(2, 8);

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
 * Ekran „Aktywność" projektu, czyli od E2 miejsce podsumowania powrotu.
 *
 * Sekcja „Od Twojej ostatniej wizyty" stała na WEJŚCIU do projektu i test czytał tamtą stronę.
 * Element E2 ściął wejście do pytania i listy rozmów; podsumowanie wróciło tam, gdzie mieszka
 * pytanie „czy muszę coś zrobić", czyli na obecność. Test poszedł za nim, bo test pod starym
 * adresem mierzyłby już tylko to, że na wejściu go nie ma.
 */
async function returnPage(): Promise<string> {
  const response = await fetch(url(`/w/${workspaceId}/p/${projectId}/presence`), {
    headers: { cookie },
    redirect: "manual",
  });
  expect(response.status).toBe(200);
  return await response.text();
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

describe("P4 i P5: powrót po nieobecności", () => {
  it("the_project_page_counts_the_collector_from_seeded_rows", async () => {
    const body = await returnPage();

    expect(body).toContain('data-block="powrot"');
    expect(body).toContain("Od Twojej ostatniej wizyty");
    // Trzy obudzenia z zasiewu: jedno z sesją, dwa pominięte, i wszystkie trzy policzone.
    expect(body).toContain("Obudzeń: 3");
    expect(body).toContain("z tego z sesją: 1");
    expect(body).toContain("pominiętych: 2");
    expect(body).toContain("BEZ_ZDARZEN 2");
  });

  it("the_first_visit_says_so_instead_of_pretending_to_know_the_window", async () => {
    // Pierwsze wejście w tej bazie już było (przypadek wyżej), więc drugie ma pokazać to samo
    // okno: karencja nie pozwala skasować go odświeżeniem.
    const body = await returnPage();
    expect(body).toContain("Obudzeń: 3");
  });

  it("a_correction_typed_here_lands_in_the_conversation_as_an_ordinary_message", async () => {
    // ODWRÓCONE PRZEZ E5 (audyt UX 20.09 punkt 5). Ten przypadek wymagał kiedyś, żeby zdanie
    // wróciło W TYM SAMYM WIDOKU jako zapisana korekta, bo trasa zapisywała je dodatkowo jako
    // `PREFERENCE`, gdy ZACZYNAŁO SIĘ od jednego z sześciu słów sterujących. Reguła po pierwszym
    // słowie nie rozumie zdania, tylko jego początek, więc E5 ją usunęło: korekta jest dziś
    // zwykłą wypowiedzią w rozmowie, a zapisanie jej jako woli na przyszłość należy do agenta,
    // który ją przeczyta. Test pilnuje więc TEGO, co produkt naprawdę obiecuje: zdanie dociera
    // do wątku. Gdyby wróciło do widoku powrotu jako „zapisana korekta", znaczyłoby to, że
    // mechanizm po pierwszym słowie wrócił tylnymi drzwiami.
    const sentence = `Nie pisz dłużej niż trzy zdania ${STAMP}`;
    const sent = await fetch(url("/studio/return"), {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", cookie },
      body: new URLSearchParams({
        action: "correct",
        workspace_id: workspaceId,
        project_id: projectId,
        text: sentence,
        return_to: `/w/${workspaceId}/p/${projectId}`,
      }).toString(),
      redirect: "manual",
    });
    expect([302, 303]).toContain(sent.status);

    // W KTÓREJ rozmowie, tego test nie zgaduje: trasa wysyła zdanie do bieżącej sesji projektu,
    // a gdy żadna nie żyje, zakłada nową. Bierzemy więc WSZYSTKIE rozmowy projektu z paska i
    // wymagamy, żeby zdanie stało dokładnie w jednej. Wpisanie tu identyfikatora z zasiewu
    // znaczyłoby, że test wie o wyborze sesji więcej niż produkt.
    const project = await fetch(url(`/w/${workspaceId}/p/${projectId}`), {
      headers: { cookie },
      redirect: "manual",
    });
    const projectBody = await project.text();
    const links = new Set(
      Array.from(
        projectBody.matchAll(
          new RegExp(`/w/${workspaceId}/p/${projectId}/c/([0-9a-f-]{36})`, "g"),
        ),
      ).map((match) => match[0]),
    );
    expect(links.size).toBeGreaterThan(0);

    let withSentence = 0;
    for (const href of links) {
      const conversation = await fetch(url(href), { headers: { cookie }, redirect: "manual" });
      if ((await conversation.text()).includes(sentence)) withSentence += 1;
    }
    expect(withSentence, "zdanie korekty w rozmowie").toBe(1);

    // I NIE WRACA jako zapisana reguła: widok powrotu nie twierdzi, że produkt to zapamiętał.
    expect(await returnPage()).not.toContain(sentence);
  });

  it("the_section_is_not_inside_the_conversation_and_stands_above_the_counters", async () => {
    const body = await returnPage();
    const returnAt = body.indexOf('data-block="powrot"');
    const conversationAt = body.indexOf('data-live-root="1"');
    expect(returnAt).toBeGreaterThan(-1);
    // Rozmowy na tym ekranie nie ma i mieć nie ma: podsumowanie powrotu nie jest turą.
    expect(conversationAt).toBe(-1);
    // Zdanie przed liczbami. Kolejność jest tym, po co ta sekcja istnieje: człowiek po powrocie
    // czyta najpierw, co się wydarzyło, a dopiero potem, ile tego było.
    const counters = body.indexOf('data-count="wakeups"');
    if (counters > -1) expect(returnAt).toBeLessThan(counters);
  });
});
