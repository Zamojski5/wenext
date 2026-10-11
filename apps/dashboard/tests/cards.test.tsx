/**
 * Karty rozmowy na ekranie: każdy stan każdej karty (D-046).
 *
 * TRZY RZECZY, KTÓRYCH TE PRZYPADKI PILNUJĄ, i wszystkie trzy są z kontraktu:
 *
 *   1. **sukces jest napisem serwera.** „Połączono", „Dołączono", „Opublikowano" pojawia się
 *      wyłącznie w stanie docelowym. Karta w stanie trwającym mówi formą niedokonaną;
 *   2. **przycisk bierze się z `akcje`, nie ze stanu.** „Opublikuj" bez delegacji NIE jest
 *      wystawiane przez serwer, więc ekran go nie rysuje i nie musi znać reguły delegacji;
 *   3. **odmowa jest treścią.** Stan `blad` pokazuje powód i zostawia ponowienie, gdy przyszło.
 *
 * I jedna rzecz przez wszystkie: ŻADNEGO IDENTYFIKATORA W TEKŚCIE. `karta_id` jest w polu
 * ukrytym i w `data-`, bo formularz musi wiedzieć, czego dotyczy; człowiek go nie widzi.
 */
import { describe, expect, it } from "vitest";

import { ConversationCards, cardsDigestOf } from "../components/studio/cards";
import type { CardAction, ConversationCard } from "../lib/cards-schema";
import { renderToStaticMarkup } from "react-dom/server";

import { renderToDocument } from "./render";

const BASE = {
  workspaceId: "aaaaaaaa-0000-4000-8000-000000000001",
  projectId: "bbbbbbbb-0000-4000-8000-000000000001",
  sessionId: "cccccccc-0000-4000-8000-000000000001",
  returnTo: "/w/a/p/b/c/s",
} as const;

function action(nazwa: string, sciezka = "/api/workspaces/w/act"): CardAction {
  return { nazwa, trasa: { metoda: "POST", sciezka }, payload: { co: nazwa } };
}

function card(overrides: Partial<ConversationCard> = {}): ConversationCard {
  return {
    karta_id: "konto:reddit:anna",
    rodzaj: "KONTO",
    stan: "brak_polaczenia",
    powod: null,
    akcje: [action("polacz")],
    od_seq: 3,
    dane: { platforma: "REDDIT", profil: "Anna" },
    ...overrides,
  };
}

function render(cards: readonly ConversationCard[]) {
  return renderToDocument(<ConversationCards cards={cards} {...BASE} />);
}

/** An action with an explicit payload, for the cards whose payload the backend really sends. */
function routed(
  nazwa: string,
  sciezka: string,
  payload: Record<string, unknown> = {},
): CardAction {
  return { nazwa, trasa: { metoda: "POST", sciezka }, payload };
}

/** An X account card as the backend sends it on 26.09: open the browser, then check. */
function xCard(overrides: Partial<ConversationCard> = {}): ConversationCard {
  return card({
    karta_id: "konto:X:anna",
    akcje: [
      routed("polacz", "/api/capsule/session", { platform: "X" }),
      routed("sprawdz", "/api/x/session/confirm", { platform: "X" }),
    ],
    dane: { platforma: "X", profil: "Anna", konto: "anna" },
    ...overrides,
  });
}

/** The hidden fields of one form, as the browser would send them. */
function hiddenFields(form: Element | null): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const input of Array.from(form?.querySelectorAll('input[type="hidden"]') ?? [])) {
    fields[input.getAttribute("name") ?? ""] = input.getAttribute("value") ?? "";
  }
  return fields;
}

describe("karta konta", () => {
  it("asks_to_connect_and_carries_the_route_from_the_card", () => {
    // A `polacz` whose route is NOT the capsule session keeps the ordinary card road: the card
    // carries its route and the screen sends it, as for every other action.
    const screen = render([card()]);
    expect(screen.body.textContent).toContain("Brakuje połączenia");
    expect(screen.body.textContent).toContain("Połącz Reddita");
    const form = screen.querySelector('form[action="/studio/card"]');
    expect(form?.querySelector('input[name="sciezka"]')?.getAttribute("value")).toBe(
      "/api/workspaces/w/act",
    );
    expect(form?.querySelector('input[name="payload"]')?.getAttribute("value")).toBe(
      '{"co":"polacz"}',
    );
  });

  it("says_connecting_while_it_connects_and_never_says_connected", () => {
    const screen = render([
      card({ stan: "lacze", akcje: [action("anuluj")], dane: { profil: "M", adres_logowania: "/kapsula/login" } }),
    ]);
    expect(screen.body.textContent).toContain("Wracam do rozmowy…");
    expect(screen.body.textContent).not.toContain("Połączono");
    expect(screen.body.textContent).toContain("Anuluj");
    // Okno logowania otwiera się osobno, a rozmowa zostaje otwarta.
    expect(screen.querySelector("[data-open-login]")?.getAttribute("target")).toBe("_blank");
  });

  it("says_connected_only_in_the_target_state", () => {
    const screen = render([card({ stan: "polaczone", akcje: [] })]);
    expect(screen.querySelector("[data-state='polaczone']")?.textContent).toContain("Połączono");
    // I nie ma już czego kliknąć: pusta lista akcji to brak przycisku, nie przycisk wyszarzony.
    expect(screen.querySelector('form[action="/studio/card"]')).toBeNull();
  });

  it("a_paused_account_gets_no_button_because_logging_in_would_not_help", () => {
    // Czwarty stan konta (PAUSED i QUARANTINED po stronie produktu). `brak_polaczenia` znaczy
    // „kliknij, a pomoże"; tutaj nie pomoże, więc przycisku nie ma wcale, a zamiast niego stoi
    // droga do miejsca, w którym da się z tym coś zrobić naprawdę.
    const screen = render([card({ stan: "niedostepne", akcje: [] })]);
    expect(screen.body.textContent).toContain("Konto wstrzymane");
    expect(screen.body.textContent).toContain("Tym kontem teraz nie zadziałam");
    expect(screen.querySelector("button")).toBeNull();
    expect(screen.querySelector("[data-state='niedostepne'] a")?.getAttribute("href")).toContain(
      "/people",
    );
  });

  it("an_error_shows_the_reason_and_keeps_the_retry_the_server_sent", () => {
    const screen = render([
      card({ stan: "blad", powod: "Kapsuła zamknęła logowanie bez potwierdzenia" }),
    ]);
    expect(screen.querySelector("[data-reason]")?.textContent).toContain("bez potwierdzenia");
    expect(screen.body.textContent).toContain("Połącz Reddita");
  });
});

describe("account card: connecting any platform, X included (26.09)", () => {
  it("an_x_card_names_the_platform_and_offers_both_steps", () => {
    const screen = render([xCard()]);
    const text = screen.body.textContent ?? "";
    expect(text).toContain("X · Anna");
    // The button and the popover head in the platform's grammar: "Połącz X", not "Połącz Xa".
    expect(screen.querySelector("summary.st-button")?.textContent).toContain("Połącz X");
    expect(screen.querySelector(".st-pophead")?.textContent).toBe("Połącz X");
    expect(text).not.toContain("Połącz Xa");
    expect(text).not.toContain("Połącz konto");
    expect(text).toContain("Gotowe, sprawdź połączenie");
  });

  it("connecting_opens_the_agents_browser_in_a_new_tab_with_the_platform_from_the_payload", () => {
    // THE BUG THIS REPLACES: `polacz` went to `/studio/card`, the ticket URL in the answer was
    // dropped, and the person was never shown a login screen. The form now posts in a NEW tab to
    // the handler that answers with a 303 to the agent's browser.
    const screen = render([xCard()]);
    const form = screen.querySelector('form[action="/studio/capsule/open"]');
    expect(form).not.toBeNull();
    expect(form?.getAttribute("method")).toBe("post");
    expect(form?.getAttribute("target")).toBe("_blank");
    expect(form?.getAttribute("rel")).toContain("noopener");
    expect(hiddenFields(form)).toEqual({
      workspace_id: BASE.workspaceId,
      project_id: BASE.projectId,
      session_id: BASE.sessionId,
      karta_id: "konto:X:anna",
      platform: "X",
      return_to: BASE.returnTo,
    });
    // Nothing of the card's route or payload rides along: that handler acts on the platform only.
    expect(form?.querySelector('input[name="sciezka"]')).toBeNull();
    expect(form?.querySelector('input[name="payload"]')).toBeNull();
  });

  it("an_older_reddit_card_without_a_payload_opens_reddit", () => {
    const screen = render([card({ akcje: [routed("polacz", "/api/capsule/session")] })]);
    const form = screen.querySelector('form[action="/studio/capsule/open"]');
    expect(hiddenFields(form)["platform"]).toBe("REDDIT");
    expect(screen.querySelector("summary.st-button")?.textContent).toContain("Połącz Reddita");
  });

  it("the_check_is_an_ordinary_card_action_carrying_the_route_from_the_card", () => {
    const screen = render([xCard()]);
    const forms = Array.from(screen.querySelectorAll('form[action="/studio/card"]'));
    const check = forms.find(
      (one) => one.querySelector('input[name="akcja"]')?.getAttribute("value") === "sprawdz",
    );
    expect(check).toBeDefined();
    const fields = hiddenFields(check ?? null);
    expect(fields["sciezka"]).toBe("/api/x/session/confirm");
    expect(fields["metoda"]).toBe("POST");
    expect(fields["payload"]).toBe('{"platform":"X"}');
    expect(check?.querySelector("button")?.textContent).toBe("Gotowe, sprawdź połączenie");
    // Secondary, not a second primary: one emphasised button answers "what do I press now".
    expect(check?.querySelector("button")?.className).toContain("st-soft");
  });

  it("the_instruction_names_the_handle_and_the_button_that_closes_the_loop", () => {
    const line = render([xCard()]).querySelector("[data-instruction]")?.textContent ?? "";
    expect(line).toBe(
      "1. W oknie przeglądarki agenta zaloguj się jako @anna. 2. Wróć tutaj i kliknij Gotowe.",
    );
  });

  it("with_no_account_yet_the_instruction_asks_for_the_right_person_not_an_empty_handle", () => {
    // A new X account is created from whoever signs in, so `konto` arrives empty. An empty
    // handle would read as a broken card; the sentence says whom to sign in as instead.
    const screen = render([xCard({ dane: { platforma: "X", profil: "Anna", konto: "" } })]);
    const line = screen.querySelector("[data-instruction]")?.textContent ?? "";
    expect(line).toContain("zaloguj się kontem X, którym agent ma działać");
    expect(line).not.toContain("@");
    expect(screen.body.textContent).not.toContain("jako @");
  });

  it("a_card_without_the_check_does_not_point_at_a_missing_button", () => {
    // An older Reddit card has `polacz` only. "Kliknij Gotowe" would send the person looking for
    // a button that is not there.
    const line =
      render([card({ akcje: [routed("polacz", "/api/capsule/session")] })])
        .querySelector("[data-instruction]")?.textContent ?? "";
    expect(line).toContain("potem wróć do tej rozmowy");
    expect(line).not.toContain("Gotowe");
  });

  it("while_the_browser_is_open_the_card_still_offers_the_check", () => {
    const screen = render([
      xCard({
        stan: "lacze",
        akcje: [
          routed("sprawdz", "/api/x/session/confirm", { platform: "X" }),
          routed("anuluj", "/api/capsule/session/cancel"),
        ],
      }),
    ]);
    expect(screen.body.textContent).toContain("Gotowe, sprawdź połączenie");
    expect(screen.querySelector("[data-instruction]")).not.toBeNull();
    expect(screen.body.textContent).not.toContain("Połączono");
  });

  it("the_capsules_reading_of_why_it_is_not_connected_is_shown", () => {
    const screen = render([
      xCard({
        dane: {
          platforma: "X",
          profil: "Anna",
          konto: "anna",
          odczyt_kapsuly: "Kapsuła nie zgłasza zalogowanej sesji X.",
        },
      }),
    ]);
    expect(screen.querySelector("[data-capsule-reading]")?.textContent).toBe(
      "Kapsuła nie zgłasza zalogowanej sesji X.",
    );
  });

  it("connected_shows_the_handle_it_was_connected_as_and_nothing_left_to_do", () => {
    const screen = render([
      xCard({
        stan: "polaczone",
        akcje: [],
        dane: {
          platforma: "X",
          profil: "Anna",
          konto: "anna",
          polaczone_jako: "@anna_real",
          odczyt_kapsuly: "stare zdanie sprzed połączenia",
        },
      }),
    ]);
    // `polaczone_jako` wins over `konto`, and the `@` is not doubled when it arrives written.
    expect(screen.querySelector("[data-state='polaczone']")?.textContent).toBe(
      " Połączono jako @anna_real",
    );
    expect(screen.querySelector("[data-instruction]")).toBeNull();
    expect(screen.querySelector("[data-capsule-reading]")).toBeNull();
    expect(screen.querySelector("form")).toBeNull();
  });

  it("connected_without_polaczone_jako_falls_back_to_the_account", () => {
    const screen = render([xCard({ stan: "polaczone", akcje: [] })]);
    expect(screen.querySelector("[data-state='polaczone']")?.textContent).toBe(
      " Połączono jako @anna",
    );
    // A Reddit handle is written as the platform writes it: no `@`.
    const reddit = render([
      card({ stan: "polaczone", akcje: [], dane: { platforma: "REDDIT", profil: "M", konto: "anna" } }),
    ]);
    expect(reddit.querySelector("[data-state='polaczone']")?.textContent).toBe(
      " Połączono jako anna",
    );
  });

  it("a_platform_outside_the_map_prints_itself", () => {
    const text = (platforma: string) =>
      render([card({ dane: { platforma, profil: "M" }, akcje: [routed("polacz", "/api/capsule/session")] })])
        .body.textContent ?? "";
    expect(text("LINKEDIN")).toContain("LinkedIn · M");
    expect(text("LINKEDIN")).toContain("Połącz LinkedIn");
    expect(text("HACKER_NEWS")).toContain("Hacker News · M");
    // Unknown stays unknown and visible, never dressed up as Reddit.
    expect(text("MASTODON")).toContain("MASTODON · M");
    expect(text("MASTODON")).toContain("Połącz MASTODON");
  });
});

describe("karta społeczności", () => {
  const community = (overrides: Partial<ConversationCard> = {}) =>
    card({
      karta_id: "spolecznosc:r/SaaS",
      rodzaj: "SPOLECZNOSC",
      stan: "propozycja",
      // JEDYNA DZISIEJSZA AKCJA (E3). „Dołącz kontem" nie istnieje, bo nie ma trasy, która by
      // to wykonała, a akcja bez trasy jest przyciskiem, który kłamie po kliknięciu.
      akcje: [action("obserwuj")],
      dane: { klucz: "r/SaaS", uzasadnienie: "Rozmowy o pierwszych klientach" },
      ...overrides,
    });

  it("two_communities_share_one_panel_because_they_are_one_result", () => {
    const screen = render([
      community(),
      community({ karta_id: "spolecznosc:r/startups", dane: { klucz: "r/startups" }, od_seq: 4 }),
    ]);
    expect(screen.querySelectorAll(".st-communities").length).toBe(1);
    expect(screen.querySelectorAll(".st-community").length).toBe(2);
  });

  it("observing_is_offered_and_joining_an_account_is_not_offered_at_all", () => {
    // SEDNO E3. Do 20.09 przycisk mówił „Dołącz", a robił obserwację, czyli czynność, która na
    // platformie nie zostawia śladu. Dziś przycisk mówi to, co robi, a „Dołącz kontem" NIE
    // ISTNIEJE, dopóki nie ma wykonawcy: nieosiągalny przycisk jest obietnicą odkrywaną po
    // kliknięciu, a wyszarzony jest tą samą obietnicą napisaną szarym.
    const screen = render([community()]);
    expect(screen.body.textContent).toContain("Obserwuj");
    expect(screen.body.textContent).not.toContain("Dołącz");
  });

  it("starting_to_observe_says_so_in_the_present_tense_and_offers_nothing_to_click", () => {
    const screen = render([community({ stan: "obserwuje", akcje: [] })]);
    expect(screen.body.textContent).toContain("Włączam obserwację…");
    expect(screen.body.textContent).not.toContain("Obserwuję");
    expect(screen.querySelector("button")).toBeNull();
  });

  it("observed_says_what_observing_actually_means", () => {
    // „Obserwowana bez dołączenia" to dla człowieka nowe pojęcie i karta jest jedynym miejscem,
    // w którym je spotyka. Zdanie jest z serwera, bo to on wie, co naprawdę robi.
    const screen = render([
      community({
        stan: "obserwowana",
        akcje: [],
        dane: {
          klucz: "r/SaaS",
          obserwacja: true,
          co_to_znaczy: "Czytam tu publiczne wątki. Nie zapisuję tam Twojego konta.",
        },
      }),
    ]);
    expect(screen.querySelector("[data-state='obserwowana']")?.textContent).toContain("Obserwuję");
    expect(screen.querySelector("[data-meaning]")?.textContent).toContain("publiczne wątki");
  });

  it("an_account_written_there_without_observation_says_nothing_is_being_read", () => {
    // Stan dziś NIEOSIĄGALNY (brak wykonawcy dołączania), rysowany po to, żeby dzień jego
    // włączenia nie był dniem pisania komponentu. `dolaczono` przykrywa na karcie informację
    // o obserwacji, więc musi ją oddać słowami, inaczej „konto zapisane, a agent nic nie
    // czyta" byłoby stanem niewidocznym na żadnym ekranie.
    const screen = render([
      community({ stan: "dolaczono", akcje: [], dane: { klucz: "r/SaaS", obserwacja: false } }),
    ]);
    expect(screen.querySelector("[data-state='dolaczono']")?.textContent).toContain(
      "Konto zapisane",
    );
    expect(screen.querySelector("[data-meaning='bez-obserwacji']")?.textContent).toContain(
      "nie czytam stamtąd",
    );
  });

  it("a_refusal_shows_the_reason_and_the_button_says_it_is_a_retry", () => {
    const screen = render([
      community({ stan: "blad", powod: "Brak połączenia z kontem", akcje: [action("obserwuj")] }),
    ]);
    expect(screen.querySelector("[data-reason]")?.textContent).toContain("Brak połączenia");
    expect(screen.body.textContent).toContain("Spróbuj ponownie");
  });
});

describe("karta szkicu", () => {
  const draft = (overrides: Partial<ConversationCard> = {}) =>
    card({
      karta_id: "szkic:1",
      rodzaj: "SZKIC",
      stan: "szkic",
      akcje: [action("popraw")],
      dane: {
        tytul: "Szkic odpowiedzi",
        spolecznosc: "r/SaaS",
        profil: "Anna",
        tresc: "Zacząłbym od jednej potrzeby.",
        delegacja_pozwala: false,
      },
      ...overrides,
    });

  it("without_a_signed_delegation_there_is_no_publish_button_at_all", () => {
    // Serwer NIE wystawia akcji `opublikuj` bez delegacji, więc ekran nie musi znać tej reguły.
    const screen = render([draft()]);
    expect(screen.body.textContent).toContain("Popraw");
    expect(screen.body.textContent).not.toContain("Opublikuj");
  });

  it("a_missing_publish_button_ALWAYS_says_why_it_is_missing", () => {
    // ODBIÓR 1, RÓŻNICA NR 10, i wymóg PLAN 4.5. Zdanie o braku publikacji pokazywało się tylko
    // wtedy, gdy karta nie miała ŻADNEJ akcji, więc szkic z samym „Popraw" wyglądał, jakby
    // publikacji po prostu zapomniano. Brak przycisku bez powodu jest gorszy od przycisku:
    // przycisk można kliknąć i dowiedzieć się, czego brakuje, a pustego miejsca nie.
    const zKarty = render([
      draft({ dane: { ...draft().dane, dlaczego_bez_akcji: "Ten projekt nie ma powierzenia." } }),
    ]);
    expect(zKarty.querySelector("[data-no-publish]")?.textContent).toBe(
      "Ten projekt nie ma powierzenia.",
    );

    // Gdy karta nie niesie powodu, mówimy najprostszą prawdę, a nie nic.
    const bezPowodu = render([draft()]);
    expect(bezPowodu.querySelector("[data-no-publish]")?.textContent).toContain("powierzenia");

    // A gdy publikacja JEST, nie ma o czym mówić.
    const zPublikacja = render([
      draft({ akcje: [action("popraw"), action("opublikuj", "/api/workspaces/w/publish")] }),
    ]);
    expect(zPublikacja.querySelector("[data-no-publish]")).toBeNull();
  });

  it("the_toggle_says_Zobacz_when_closed_and_Zwin_when_open", () => {
    // Dwa napisy w znaczniku, przełączane przez `details[open]` w arkuszu (odbiór 1, nr 10).
    // Napis, który po otwarciu nadal mówi „Zobacz", każe zgadywać, co zrobi kliknięcie.
    const screen = render([draft()]);
    const toggle = screen.querySelector(".st-draft-toggle");
    expect(toggle?.querySelector('[data-when="zamkniete"]')?.textContent).toContain("Zobacz");
    expect(toggle?.querySelector('[data-when="otwarte"]')?.textContent).toContain("Zwiń");
  });

  it("with_the_action_from_the_server_the_publish_button_is_there", () => {
    const screen = render([
      draft({ akcje: [action("popraw"), action("opublikuj", "/api/workspaces/w/publish")] }),
    ]);
    expect(screen.body.textContent).toContain("Opublikuj");
  });

  it("publishing_says_so_and_published_says_it_only_after_the_server_does", () => {
    expect(render([draft({ stan: "publikuje", akcje: [] })]).body.textContent).toContain(
      "Publikuję…",
    );
    const done = render([draft({ stan: "opublikowano", akcje: [] })]);
    expect(done.body.textContent).toContain("Opublikowano");
  });

  it("the_screen_never_claims_visibility_the_server_did_not_confirm", () => {
    // Stało tu kiedyś „Widoczna w rozmowie", wymyślone przez ekran. To jest twierdzenie o
    // skutku na cudzej platformie, a publikacja bywa zdjęta przez moderację minutę po wyjściu.
    // Zdanie o widoczności liczy projekcja z odczytu zwrotnego; bez niego ekran MILCZY.
    const quiet = render([draft({ stan: "opublikowano", akcje: [], dane: { tresc: "x" } })]);
    expect(quiet.body.textContent).not.toContain("Widoczna");
    expect(quiet.querySelector(".st-draft-visibility")).toBeNull();
    expect(quiet.querySelector(".st-draft-address")).toBeNull();
  });

  it("a_published_draft_links_to_the_publication_and_repeats_the_servers_sentence", () => {
    const done = render([
      draft({
        stan: "opublikowano",
        akcje: [],
        dane: {
          tresc: "x",
          adres: "https://example.test/r/saas/comments/abc",
          widocznosc: "Skutek potwierdzony odczytem 04:12.",
        },
      }),
    ]);
    const link = done.querySelector(".st-draft-address");
    expect(link?.getAttribute("href")).toBe("https://example.test/r/saas/comments/abc");
    // Cudza strona otwiera się osobno, a rozmowa zostaje tam, gdzie była.
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(done.querySelector(".st-draft-visibility")?.textContent).toContain("04:12");
  });

  it("while_publishing_the_card_says_why_there_is_nothing_to_click", () => {
    const busy = render([
      draft({
        stan: "publikuje",
        akcje: [],
        dane: { tresc: "x", dlaczego_bez_akcji: "Wniosek stoi w kolejce publikatora." },
      }),
    ]);
    expect(busy.body.textContent).toContain("kolejce publikatora");
    expect(busy.querySelector("button")).toBeNull();
  });

  it("a_draft_with_no_actions_says_why_instead_of_showing_an_empty_row", () => {
    // Dziś w API nie ma trasy, którą człowiek zatwierdza szkic z ekranu, więc karta przychodzi
    // bez akcji. Pusty pasek przycisków wyglądałby jak coś, co się nie doczytało; zdanie z
    // karty mówi, czego brakuje, i pochodzi z serwera, bo to on wie.
    const screen = render([
      draft({
        akcje: [],
        dane: {
          tresc: "Zacząłbym od jednej potrzeby.",
          dlaczego_bez_akcji: "Zatwierdzanie szkiców chodzi dziś kanałem zgody, nie z ekranu.",
        },
      }),
    ]);
    expect(screen.querySelector("[data-no-actions]")?.textContent).toContain("kanałem zgody");
    expect(screen.querySelector("button")).toBeNull();
    // I nadal widać treść: szkic bez przycisków jest do PRZECZYTANIA, nie do ukrycia.
    expect(screen.body.textContent).toContain("Zacząłbym od jednej potrzeby.");
  });

  it("the_correction_field_starts_with_the_text_that_is_there", () => {
    const screen = render([draft()]);
    const field = screen.querySelector('textarea[name="text"]');
    expect(field?.textContent).toContain("Zacząłbym od jednej potrzeby.");
  });

  it("an_own_x_post_with_a_screenshot_says_so_under_the_text_and_only_then", () => {
    // 27.09: własny post na X może nieść zrzut ekranu. Serwer podaje go w `obraz`, osobno od
    // tekstu, bo zgoda obejmuje oba. Bez tego zdania człowiek zatwierdzałby obraz, o którym
    // nie wie; bez `obraz` zdania nie ma, bo nie ma obrazu.
    const artifact = "0f8fad5b-d9cb-469f-a165-70867728950e";
    const withImage = render([draft({ dane: { ...draft().dane, obraz: artifact } })]);
    const note = withImage.querySelector(".st-draft-image");
    expect(note?.textContent).toBe("Z obrazem: zrzut ekranu źródła");
    expect(note?.getAttribute("data-image")).toBe(artifact);
    expect(withImage.body.textContent).not.toContain("[obraz");
    expect(render([draft()]).querySelector(".st-draft-image")).toBeNull();
  });
});

describe("karta powierzenia", () => {
  it("the_signature_is_the_one_thing_only_a_person_does", () => {
    const screen = render([
      card({
        karta_id: "powierzenie:1",
        rodzaj: "POWIERZENIE",
        stan: "do_podpisu",
        akcje: [action("powierz")],
        dane: { spolecznosci: ["r/SaaS"], dzialania: ["REPLY"] },
      }),
    ]);
    expect(screen.body.textContent).toContain("powierzyć mi resztę");
    // Zakres jest PRZY przycisku: podpis bez widocznego zakresu jest podpisem in blanco.
    expect(screen.querySelector(".st-autonomy-scope")?.textContent).toContain("r/SaaS");
    expect(screen.querySelector(".st-autonomy-scope")?.textContent).toContain("Odpowiedzi");
  });

  it("offers_background_research_with_no_publication_permission", () => {
    const screen = render([card({
      karta_id: "powierzenie:research", rodzaj: "POWIERZENIE", stan: "do_podpisu",
      akcje: [action("powierz")],
      dane: { spolecznosci: [], dzialania: ["RESEARCH"], limity: { publikacje_doba: 0, tury_modelu_doba: 4 } },
    })]);
    expect(screen.body.textContent).toContain("Uruchom research w tle");
    expect(screen.querySelector(".st-autonomy-scope")?.textContent).toContain("Research bez publikacji");
    expect(screen.querySelector(".st-autonomy-scope")?.textContent).toContain("publikacji na dobę: 0");
    expect(screen.querySelector(".st-autonomy-scope")?.textContent).toContain("uruchomień agenta na dobę: 4");
  });

  it("the_ceilings_are_next_to_the_button_and_named_in_words", () => {
    // Limity są tą częścią zakresu, o którą człowiek pyta najpóźniej i żałuje najbardziej.
    // Nazwy kluczy z API („publikacje_doba") nie są zdaniem dla człowieka i nie wychodzą na ekran.
    const screen = render([
      card({
        karta_id: "powierzenie",
        rodzaj: "POWIERZENIE",
        stan: "do_podpisu",
        akcje: [action("powierz")],
        dane: {
          spolecznosci: ["r/SaaS"],
          dzialania: ["REPLY"],
          limity: { publikacje_godzina: 2, publikacje_doba: 6, wlasne_posty_doba: 1 },
          uzasadnienie: "Dwie społeczności, same odpowiedzi, bez własnych postów.",
        },
      }),
    ]);
    const scope = screen.querySelector(".st-autonomy-scope")?.textContent ?? "";
    expect(scope).toContain("publikacji na godzinę: 2");
    expect(scope).toContain("własnych postów na dobę: 1");
    expect(scope).not.toContain("publikacje_doba");
    expect(screen.querySelector(".st-autonomy-why")?.textContent).toContain("bez własnych postów");
  });

  it("a_signed_delegation_says_the_agent_leads", () => {
    const screen = render([
      card({ karta_id: "p", rodzaj: "POWIERZENIE", stan: "podpisana", akcje: [], dane: {} }),
    ]);
    expect(screen.body.textContent).toContain("Agent prowadzi dalej sam");
    // No deadline on the card, no deadline on the screen: absence is not invented.
    expect(screen.querySelector("[data-deadline]")).toBeNull();
    expect(screen.querySelector("[data-goal]")).toBeNull();
  });

  it("the_deadline_is_on_the_wall_clock_of_warsaw_summer_and_winter", () => {
    // 12:00 UTC on 3 October is 14:00 in Warsaw (CEST); 08:05 UTC on 1 December is 09:05 (CET).
    // Two dates, so a formatter that ignored daylight saving could not pass both.
    const deadlineOf = (wygasa: string) =>
      render([
        card({
          karta_id: "powierzenie",
          rodzaj: "POWIERZENIE",
          stan: "do_podpisu",
          akcje: [action("powierz")],
          dane: { spolecznosci: ["r/SaaS"], dzialania: ["REPLY"], wygasa },
        }),
      ]).querySelector("[data-deadline]")?.textContent;
    expect(deadlineOf("2026-10-03T12:00:00+00:00")).toBe("Ważne do 3 października 2026, 14:00");
    expect(deadlineOf("2026-12-01T08:05:00Z")).toBe("Ważne do 1 grudnia 2026, 09:05");
  });

  it("the_goal_and_each_accounts_platform_stand_next_to_the_signature", () => {
    const screen = render([
      card({
        karta_id: "powierzenie",
        rodzaj: "POWIERZENIE",
        stan: "do_podpisu",
        akcje: [action("powierz")],
        dane: {
          spolecznosci: ["r/SaaS"],
          dzialania: ["REPLY"],
          cel: "Dziesięć rozmów z CTO do końca miesiąca.",
          konta: [
            { konto: "anna", platforma: "X" },
            { konto: "anna_r", platforma: "REDDIT" },
          ],
          wygasa: "2026-10-03T12:00:00+00:00",
        },
      }),
    ]);
    expect(screen.querySelector("[data-goal]")?.textContent).toBe(
      "Cel: Dziesięć rozmów z CTO do końca miesiąca.",
    );
    const scope = screen.querySelector(".st-autonomy-scope")?.textContent ?? "";
    expect(scope).toContain("konta: @anna (X), anna_r (Reddit)");
    expect(scope).toContain("r/SaaS");
    // The deadline sits with the button, inside the form the person submits.
    expect(screen.querySelector("form [data-deadline]")?.textContent).toContain("14:00");
  });

  it("accounts_without_a_platform_still_render_and_one_account_is_singular", () => {
    const scope = render([
      card({
        karta_id: "powierzenie",
        rodzaj: "POWIERZENIE",
        stan: "do_podpisu",
        akcje: [action("powierz")],
        dane: { spolecznosci: ["r/SaaS"], dzialania: ["REPLY"], konta: [{ konto: "anna" }] },
      }),
    ]).querySelector(".st-autonomy-scope")?.textContent ?? "";
    expect(scope).toContain("konto: anna");
    expect(scope).not.toContain("(");
  });

  it("a_signed_delegation_keeps_its_deadline_visible", () => {
    const screen = render([
      card({
        karta_id: "p",
        rodzaj: "POWIERZENIE",
        stan: "podpisana",
        akcje: [],
        dane: { wygasa: "2026-10-03T12:00:00+00:00" },
      }),
    ]);
    expect(screen.body.textContent).toContain("Agent prowadzi dalej sam");
    expect(screen.querySelector("[data-deadline]")?.textContent).toBe(
      "Ważne do 3 października 2026, 14:00",
    );
  });
});

describe("karty jako całość", () => {
  it("no_identifier_ever_reaches_the_text_on_screen", () => {
    const screen = render([card(), card({ karta_id: "szkic:7", rodzaj: "SZKIC", stan: "szkic", akcje: [], dane: { tresc: "x" } })]);
    const visible = screen.body.textContent ?? "";
    expect(visible).not.toContain("konto:reddit:anna");
    expect(visible).not.toContain("szkic:7");
    // A formularz i tak wie, czego dotyczy.
    expect(screen.querySelector('input[name="karta_id"]')?.getAttribute("value")).toBe(
      "konto:reddit:anna",
    );
  });

  it("nothing_at_all_renders_when_there_are_no_cards", () => {
    // Nie pusty blok, nie zdanie „brak kart": NIC. Rozmowa bez kart jest zwykłą rozmową, a
    // ramka z informacją o tym, że kart nie ma, byłaby elementem, którego makieta nie zna.
    // `renderToStaticMarkup` oddaje wtedy pusty napis i to jest tu mierzone wprost, bo
    // `renderToDocument` słusznie odmawia parsowania pustki.
    const markup = renderToStaticMarkup(<ConversationCards cards={[]} {...BASE} />);
    expect(markup).toBe("");
  });

  it("the_digest_changes_with_state_and_not_with_text", () => {
    // Po tym odcisku strumień poznaje, że warto przeładować stronę. Poprawiony szkic zmienia
    // tekst, a nie stan, i nie ma powodu wyrywać strony spod czytającego.
    const before = cardsDigestOf([card()]);
    expect(cardsDigestOf([card({ dane: { profil: "Ktoś inny" } })])).toBe(before);
    expect(cardsDigestOf([card({ stan: "polaczone" })])).not.toBe(before);
  });
});
