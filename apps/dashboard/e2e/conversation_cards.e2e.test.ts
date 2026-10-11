/**
 * Karty w rozmowie przez prawdziwy stack (D-046).
 *
 *   przeglądarka → strona rozmowy → GET sesji (karty z projekcji) → PostgreSQL
 *   przeglądarka → POST /studio/card → trasa z karty → PostgreSQL
 *
 * CZEGO NIE SPRAWDZI TEST KOMPONENTU. `tests/cards.test.tsx` rysuje karty z podanych danych, więc
 * przechodzi także wtedy, gdy projekcja nie dowozi kart, gdy przycisk wysyła ładunek, którego
 * API nie czyta, albo gdy kliknięcie robi coś dwa razy. Tutaj karty pochodzą ze śladu w bazie,
 * a ich STAN z produktu, i to samo zapytanie, które wywołuje agent, wywołuje przycisk.
 *
 * DWIE RZECZY, KTÓRE TU JUŻ RAZ NIE ZADZIAŁAŁY i dlatego mają własne przypadki: trasa akcji
 * (mój handler odrzucał dwie z trzech, bo są w zakresie organizacji, nie przestrzeni) oraz nazwa
 * pola treści (`tresc` kontra `text`, czyli poprawka, która zapisuje się jako niezmieniona).
 * Obie klasy usterek są niewidoczne dla testów jednostkowych po obu stronach osobno.
 */
import { beforeAll, describe, expect, it } from "vitest";

const REQUIRED = [
  "WENEXT_E2E_BASE_URL",
  "WENEXT_E2E_OPERATOR_PASSWORD",
  "WENEXT_E2E_WORKSPACE_ID",
  "WENEXT_E2E_LIVE_PROJECT_ID",
  "WENEXT_E2E_CARDS_SESSION_ID",
  "WENEXT_E2E_CARDS_LOGIN",
  "WENEXT_E2E_CARDS_PAUSED_LOGIN",
  "WENEXT_E2E_CARDS_COMMUNITY",
  "WENEXT_E2E_CARDS_WHY",
  "WENEXT_E2E_CARDS_ERROR",
  "WENEXT_E2E_DRAFT_TEXT",
  "WENEXT_E2E_DRAFT_REVISION",
] as const;

type Required = (typeof REQUIRED)[number];

const environment: Record<string, string> = {};
const LONG_DASHES = ["—", "–"] as const;

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
    body: new URLSearchParams({ password: required("WENEXT_E2E_OPERATOR_PASSWORD") }).toString(),
    redirect: "manual",
  });
  const header = response.headers
    .getSetCookie()
    .find((value) => value.startsWith("wenext_dashboard_handle="));
  if (header === undefined) throw new Error("logowanie nie odesłało ciasteczka");
  return header.split(";")[0] ?? "";
}

let cookie = "";
let conversation = "";

async function page(): Promise<string> {
  const response = await fetch(url(conversation), { headers: { cookie }, redirect: "manual" });
  expect(response.status, conversation).toBe(200);
  return await response.text();
}

/** Kawałek HTML-u jednej karty, po jej identyfikatorze w `data-card`. */
function cardOf(body: string, id: string): string {
  const at = body.indexOf(`data-card="${id}"`);
  expect(at, `nie ma karty ${id}`).toBeGreaterThan(-1);
  const start = body.lastIndexOf("<", at);
  // Koniec karty konta i społeczności wypada przed następną kartą albo przed końcem bloku kart.
  const next = body.indexOf('data-card="', at + 12);
  return body.slice(start, next === -1 ? body.indexOf("</section>", at) : next);
}

/**
 * Pola ukryte formularza akcji, dokładnie tak, jak wysłałaby je przeglądarka.
 *
 * Czytane Z RENDERU, nie budowane w teście: gdyby test składał ładunek sam, sprawdzałby własne
 * wyobrażenie o karcie, a nie to, co ekran naprawdę wysyła. Tą drogą właśnie wyszłaby nazwa
 * pola, która nie zgadza się z API.
 */
function fieldsOf(fragment: string): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const match of fragment.matchAll(/<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"[^>]*>/g)) {
    fields[match[1] as string] = (match[2] as string)
      .replaceAll("&quot;", '"')
      .replaceAll("&#x27;", "'")
      .replaceAll("&amp;", "&");
  }
  return fields;
}

async function clickCard(fields: Record<string, string>): Promise<number> {
  const response = await fetch(url("/studio/card"), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", cookie },
    body: new URLSearchParams(fields).toString(),
    redirect: "manual",
  });
  return response.status;
}

beforeAll(async () => {
  const missing: string[] = [];
  for (const name of REQUIRED) {
    const value = process.env[name];
    if (value === undefined || value === "") missing.push(name);
    else environment[name] = value;
  }
  if (missing.length > 0) {
    throw new Error(`BRAK ŚRODOWISKA E2E: ${missing.join(", ")}. To jest PORAŻKA, nie pominięcie.`);
  }
  cookie = await logIn();
  conversation =
    `/w/${required("WENEXT_E2E_WORKSPACE_ID")}/p/${required("WENEXT_E2E_LIVE_PROJECT_ID")}` +
    `/c/${required("WENEXT_E2E_CARDS_SESSION_ID")}`;
});

describe("D-046: karty w rozmowie", () => {
  it("the_cards_from_the_trace_reach_the_screen_with_states_from_the_product", async () => {
    const body = await page();
    const account = cardOf(body, `konto:REDDIT:${required("WENEXT_E2E_CARDS_LOGIN")}`);
    // Konto zasiane jako PROVISIONED, czyli „przypisane, bez żywej sesji": brak połączenia.
    expect(account).toContain("Brakuje połączenia");
    // The button in the platform's grammar since 26.09 ("Połącz Reddita", "Połącz X").
    expect(account).toContain("Połącz Reddita");

    const community = cardOf(body, `spolecznosc:REDDIT:${required("WENEXT_E2E_CARDS_COMMUNITY")}`);
    // „OBSERWUJ", NIE „DOŁĄCZ" (E3, audyt UX 20.09). Do 20.09 karta mówiła „Dołącz" i po
    // kliknięciu „Dołączono", choć operacja włącza tylko czytanie publicznych wątków i nie
    // zapisuje konta w społeczności. Audyt nazwał to kłamstwem etykiety, więc ten test pilnuje
    // dziś czegoś odwrotnego niż w dniu, w którym powstał: słowa o dołączeniu mają NIE paść.
    expect(community).toContain("Obserwuj");
    expect(community).not.toContain("Dołącz");
    expect(community).toContain(required("WENEXT_E2E_CARDS_WHY"));
  });

  it("a_paused_account_has_no_button_because_logging_in_would_not_help", async () => {
    const paused = cardOf(await page(), `konto:REDDIT:${required("WENEXT_E2E_CARDS_PAUSED_LOGIN")}`);
    expect(paused).toContain("Konto wstrzymane");
    expect(paused).toContain("Tym kontem teraz nie zadziałam");
    expect(paused).not.toContain("Połącz Reddita");
  });

  it("a_refusal_shows_its_reason_and_never_looks_like_a_success", async () => {
    const body = await page();
    expect(body).toContain(required("WENEXT_E2E_CARDS_ERROR"));
    // I nie ma ani jednego słowa sukcesu przy tej karcie.
    const broken = cardOf(body, "spolecznosc:REDDIT:r/startups_e2e");
    expect(broken).not.toContain("Obserwuję");
    expect(broken).not.toContain("Dołączono");
  });

  it("joining_from_the_card_takes_the_same_road_as_the_tool_and_the_state_follows", async () => {
    const key = required("WENEXT_E2E_CARDS_COMMUNITY");
    const fields = fieldsOf(cardOf(await page(), `spolecznosc:REDDIT:${key}`));
    // Trasa jest Z KARTY. Ta akurat jest w zakresie ORGANIZACJI, nie przestrzeni, i to o nią
    // rozbiła się pierwsza wersja reguły w handlerze.
    expect(fields["sciezka"]).toBe("/api/communities");
    expect(fields["payload"]).toContain(key);

    expect([302, 303]).toContain(await clickCard(fields));

    const after = cardOf(await page(), `spolecznosc:REDDIT:${key}`);
    // Stan po kliknięciu: „Obserwuję" (obserwacja włączona, kolejka odczytów jeszcze nie
    // wróciła) albo „Włączam obserwację…" — nigdy nic o dołączeniu.
    expect(after).toMatch(/Obserwuję|Włączam obserwację/);
    expect(after).not.toContain(">Obserwuj<");
    expect(after).not.toContain("Dołączono");
  });

  it("clicking_the_same_thing_twice_changes_nothing_and_makes_no_second_card", async () => {
    const key = required("WENEXT_E2E_CARDS_COMMUNITY");
    const before = await page();
    // Po dołączeniu karta nie ma już akcji, więc powtórzenie robimy TYM SAMYM ładunkiem, co
    // pierwsze kliknięcie: tak wygląda dwa razy kliknięty przycisk i tak wygląda podwójne
    // wysłanie formularza.
    expect([302, 303]).toContain(
      await clickCard({
        workspace_id: required("WENEXT_E2E_WORKSPACE_ID"),
        project_id: required("WENEXT_E2E_LIVE_PROJECT_ID"),
        session_id: required("WENEXT_E2E_CARDS_SESSION_ID"),
        karta_id: `spolecznosc:REDDIT:${key}`,
        akcja: "obserwuj",
        metoda: "POST",
        sciezka: "/api/communities",
        payload: JSON.stringify({ platform: "REDDIT", community_key: key }),
        return_to: conversation,
      }),
    );

    const after = await page();
    expect(after).toMatch(/Obserwuję|Włączam obserwację/);
    // Jedna karta tej społeczności, nie dwie: `karta_id` bierze się z przedmiotu karty.
    const occurrences = after.split(`data-card="spolecznosc:REDDIT:${key}"`).length - 1;
    expect(occurrences).toBe(1);
    expect(before.split('data-card="').length).toBe(after.split('data-card="').length);
  });

  it("a_reload_shows_exactly_what_was_there", async () => {
    const first = await page();
    const second = await page();
    const cards = (body: string) =>
      Array.from(body.matchAll(/data-card="([^"]+)"/g)).map((match) => match[1]);
    expect(cards(second)).toEqual(cards(first));
    expect(second).toMatch(/Obserwuję|Włączam obserwację/);
  });

  it("an_action_pointing_outside_the_list_is_refused_and_changes_nothing", async () => {
    // Trasa jedzie w polu ukrytym, więc da się ją podmienić. Handler ma na to listę dozwolonych
    // tras; ten przypadek jest dowodem, że lista działa na prawdziwym serwerze, a nie tylko
    // w teście jednostkowym funkcji.
    const status = await clickCard({
      workspace_id: required("WENEXT_E2E_WORKSPACE_ID"),
      project_id: required("WENEXT_E2E_LIVE_PROJECT_ID"),
      session_id: required("WENEXT_E2E_CARDS_SESSION_ID"),
      karta_id: "konto:REDDIT:cokolwiek",
      akcja: "polacz",
      metoda: "POST",
      sciezka: "/api/session",
      payload: "{}",
      return_to: conversation,
    });
    expect([302, 303]).toContain(status);
    // Strona nadal stoi i nadal pokazuje to samo: odmowa nie zepsuła stanu.
    expect(await page()).toMatch(/Obserwuję|Włączam obserwację/);
  });

  it("no_identifier_and_no_long_dash_reaches_the_text_on_screen", async () => {
    const body = await page();
    const visible = body.replace(/<script[\s\S]*?<\/script>/g, " ").replace(/<[^>]*>/g, " ");
    expect(visible).not.toContain("karta_id");
    expect(visible).not.toContain("spolecznosc:REDDIT:");
    expect(visible).not.toContain(required("WENEXT_E2E_CARDS_SESSION_ID"));
    for (const dash of LONG_DASHES) expect(visible, dash).not.toContain(dash);
  });
});

/**
 * Szkic odpowiedzi: poprawka, brak publikacji bez powierzenia, drugie kliknięcie (D-046).
 *
 * TRZY RZECZY, KTÓRYCH NIE DA SIĘ ZMIERZYĆ BEZ PRAWDZIWEJ TRASY ZATWIERDZANIA:
 *
 *   1. poprawka tworzy NOWĄ REWIZJĘ, a nie zmienia starą. Rewizja jest przedmiotem zgody, więc
 *      nadpisanie jej znaczyłoby, że człowiek zatwierdza tekst, który może się potem zmienić;
 *   2. „Opublikuj" NIE ISTNIEJE bez żywego powierzenia. Nie jest wyszarzone i nie prowadzi do
 *      odmowy: serwer nie wystawia akcji, więc ekran nie ma czego narysować;
 *   3. drugie kliknięcie tej samej poprawki nie robi drugiego wniosku. Stara rewizja jest już
 *      zastąpiona, więc usługa odmawia, a ekran zostaje przy jednej karcie.
 */
describe("D-046: szkic odpowiedzi", () => {
  function draftCard(body: string): string {
    const at = body.indexOf('data-card="szkic:');
    expect(at, "nie ma karty szkicu").toBeGreaterThan(-1);
    return body.slice(body.lastIndexOf("<", at), body.indexOf("</section>", at));
  }

  it("the_draft_is_readable_and_has_no_publish_button_without_a_delegation", async () => {
    const card = draftCard(await page());
    expect(card).toContain("Szkic odpowiedzi");
    expect(card).toContain(required("WENEXT_E2E_DRAFT_TEXT"));
    expect(card).toContain("Popraw");
    // Bez powierzenia serwer NIE wystawia akcji, więc na ekranie nie ma jej wcale.
    expect(card).not.toContain("Opublikuj");
    expect(card).not.toContain("/approve");
  });

  it("a_correction_creates_a_new_revision_and_the_old_card_gives_way", async () => {
    const before = required("WENEXT_E2E_DRAFT_REVISION");
    const fields = fieldsOf(draftCard(await page()));
    expect(fields["sciezka"]).toContain(`/approvals/${before}/revise`);
    // Pole nazywa się `text`, tak jak klucz, którego oczekuje API. Nazwa tłumaczona w połowie
    // drogi była tu już raz usterką i to jest przypadek, który jej pilnuje.
    const poprawione = `${required("WENEXT_E2E_DRAFT_TEXT")} Krocej.`;
    expect([302, 303]).toContain(await clickCard({ ...fields, text: poprawione }));

    const body = await page();
    const after = draftCard(body);
    // NOWA REWIZJA JEST NA EKRANIE, ze zmienioną treścią. Przez jeden przebieg tego tu nie było:
    // trasa `revise` tworzyła rewizję w bazie, ale nie dopisywała nic do śladu rozmowy, więc
    // karta nowej wersji nie miała skąd powstać, a człowiek widział starą treść z adnotacją, że
    // gdzieś istnieje nowsza. wenext-84 dopisało zdarzenie STATUS z kartą, i o tym jest ten
    // przypadek: poprawka ma być WIDOCZNA, nie tylko zapisana.
    expect(after).toContain(poprawione);
    // Stara tożsamość znika, bo dwie karty jednej propozycji to dla człowieka jedna rzecz
    // pokazana dwa razy.
    expect(body).not.toContain(`data-card="szkic:${before}"`);
    // I jedna karta szkicu, nie dwie.
    expect((body.match(/data-card="szkic:/g) ?? []).length).toBe(1);
  });

  it("clicking_the_stale_correction_again_makes_no_second_proposal", async () => {
    const stale = required("WENEXT_E2E_DRAFT_REVISION");
    const cards = (html: string) => (html.match(/data-card="szkic:/g) ?? []).length;
    expect(cards(await page())).toBe(1);

    // Ten sam ładunek co poprzednio, czyli dokładnie to, co wysyła dwa razy kliknięty przycisk
    // albo cofnięcie i ponowne wysłanie formularza. Rewizja jest już zastąpiona.
    expect([302, 303]).toContain(
      await clickCard({
        workspace_id: required("WENEXT_E2E_WORKSPACE_ID"),
        project_id: required("WENEXT_E2E_LIVE_PROJECT_ID"),
        session_id: required("WENEXT_E2E_CARDS_SESSION_ID"),
        karta_id: `szkic:${stale}`,
        akcja: "popraw",
        metoda: "POST",
        sciezka:
          `/api/workspaces/${required("WENEXT_E2E_WORKSPACE_ID")}` +
          `/projects/${required("WENEXT_E2E_LIVE_PROJECT_ID")}/approvals/${stale}/revise`,
        payload: JSON.stringify({ text: "jeszcze raz to samo" }),
        return_to: conversation,
      }),
    );

    const after = await page();
    expect(cards(after)).toBe(1);
    expect(after).not.toContain("jeszcze raz to samo");
  });
});
