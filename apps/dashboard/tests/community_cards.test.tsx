/**
 * Ekran „Społeczności": karta mówi to samo, co karta w rozmowie o tej samej społeczności (E2).
 *
 * CZEGO PILNUJE TEN PLIK. Reguła stanu jest przepisana z serwera
 * (`control_plane/sessions/projection.py::refresh_states`, gałąź społeczności) i to jest DRUGA
 * kopia tej reguły w drzewie. Kopia bez testu rozjeżdża się cicho, a rozjazd wygląda tak:
 * człowiek widzi „obserwuję" na ekranie społeczności i „propozycja" na karcie w rozmowie, o tej
 * samej społeczności, w tej samej minucie, i nie ma jak rozstrzygnąć, które jest prawdą.
 *
 * Granice sprawdzane niżej są dokładnie tymi z serwera:
 *
 *   * `observe_enabled = false` → `propozycja` Z AKCJĄ. Karta bez akcji w tym stanie to ekran,
 *     który mówi „nie obserwuję" i nie daje nic z tym zrobić;
 *   * obserwowana, `last_polled_at = null` → `obserwuje`. „Włączyłem" to nie „mam stamtąd
 *     wątki", a ta różnica jest jedyną rzeczą, którą człowiek widzi, gdy kolejka odczytów stoi;
 *   * obserwowana i czytana → `obserwowana`, BEZ akcji i ze zdaniem o tym, co obserwacja znaczy.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ConversationCards } from "../components/studio/cards";
import { OBSERVATION_MEANS, OBSERVE_ROUTE, communityCards } from "../lib/community-cards";
import type { ResultCommunity } from "../lib/workspace-schema";
import { renderToDocument } from "./render";

const WORKSPACE_ID = "22222222-2222-2222-2222-222222222222";
const PROJECT_ID = "33333333-3333-3333-3333-333333333333";

function community(overrides: Partial<ResultCommunity> = {}): ResultCommunity {
  return {
    community_id: "11111111-0000-4000-8000-000000000001",
    platform: "REDDIT",
    community_key: "kubernetes",
    display_name: "r/kubernetes",
    access_mode: "PUBLIC_API_NO_ACCOUNT",
    observe_enabled: true,
    last_polled_at: "2026-09-19T07:30:00+00:00",
    thread_count: 12,
    ...overrides,
  };
}

function screen(communities: readonly ResultCommunity[]) {
  return renderToDocument(
    <ConversationCards
      cards={communityCards(communities)}
      projectId={PROJECT_ID}
      returnTo={`/w/${WORKSPACE_ID}/p/${PROJECT_ID}/communities`}
      sessionId={null}
      workspaceId={WORKSPACE_ID}
    />,
  );
}

describe("społeczności przestrzeni jako karty", () => {
  it("the_screen_puts_the_cards_inside_a_root_that_the_card_stylesheet_reaches", () => {
    // NAJWAŻNIEJSZY PRZYPADEK W TYM PLIKU, i jedyny, który nie mówi o treści. Reguły kart
    // mieszkają w `app/chat.css` pod korzeniem `:is(.st-chat-view, .st-cards-host)` razem z
    // tokenami. Karta wyrenderowana bez żadnego z tych korzeni dostaje ZERO reguł i wychodzi
    // surowym HTML-em, czego test komponentu nie widzi: `renderToStaticMarkup` nie ładuje CSS.
    // Dlatego sprawdzamy to, co da się sprawdzić bez przeglądarki: że ekran w ogóle daje kartom
    // korzeń. Wygląd rozstrzyga zrzut.
    //
    // Od 21.09 (odbiór 1, nr 6) ekran nie rysuje już KART, tylko wiersze jednej powierzchni, ale
    // korzeń zostaje: przycisk „Obserwuj" i jego formularz są nadal elementami karty i biorą
    // stamtąd wygląd.
    const file = fs.readFileSync(
      path.join(process.cwd(), "app/(studio)/w/[workspaceId]/p/[projectId]/communities/page.tsx"),
      "utf8",
    );
    expect(file).toContain("st-cards-host");
    const css = fs.readFileSync(path.join(process.cwd(), "app/chat.css"), "utf8");
    expect(css).toContain(":is(.st-chat-view, .st-cards-host) .st-community{");
    expect(css).toContain(":is(.st-chat-view, .st-cards-host){--st-bg:");
  });

  it("the_three_states_match_the_rule_the_server_uses_for_the_same_card", () => {
    const [proposed] = communityCards([community({ observe_enabled: false })]);
    const [observing] = communityCards([community({ last_polled_at: null })]);
    const [observed] = communityCards([community()]);

    expect(proposed?.stan).toBe("propozycja");
    expect(observing?.stan).toBe("obserwuje");
    expect(observed?.stan).toBe("obserwowana");

    // Wyłączona obserwacja to jedyny stan z akcją, i akcja jest tą, którą produkt daje karcie w
    // rozmowie: `POST /api/communities` z platformą i kluczem. Ekran nie wymyśla trasy.
    expect(proposed?.akcje).toHaveLength(1);
    expect(proposed?.akcje[0]?.nazwa).toBe("obserwuj");
    expect(proposed?.akcje[0]?.trasa).toEqual(OBSERVE_ROUTE);
    expect(proposed?.akcje[0]?.payload).toEqual({
      platform: "REDDIT",
      community_key: "kubernetes",
    });
    expect(observing?.akcje).toHaveLength(0);
    expect(observed?.akcje).toHaveLength(0);
  });

  it("an_observed_community_says_what_observing_does_and_what_it_does_not", () => {
    // To zdanie jest z `tools/cards.py::OBSERVATION_MEANS` i ma tu być słowo w słowo: przez
    // jakiś czas produkt odpowiadał na to pytanie źle („Dołączono"), więc odpowiedź musi być
    // jedna, niezależnie od tego, na którym ekranie człowiek ją czyta.
    const rendered = screen([community()]);
    expect(rendered.body.textContent).toContain(OBSERVATION_MEANS);
    expect(rendered.body.textContent).toContain("Obserwuję");
    // Żadne słowo o dołączeniu: obserwacja nie zapisuje konta w społeczności.
    expect(rendered.body.textContent).not.toContain("Dołącz");
    expect(rendered.body.textContent).not.toContain("Konto zapisane");
  });

  it("a_community_that_waits_for_a_decision_stands_above_the_ones_that_work", () => {
    // Ekran ma jedno zadanie i porządek listy jest jego częścią: to, co czeka na człowieka,
    // stoi wyżej niż to, co od tygodnia działa i niczego nie potrzebuje.
    const cards = communityCards([
      community({ community_id: "a", community_key: "devops" }),
      community({ community_id: "b", community_key: "saas", observe_enabled: false }),
      community({ community_id: "c", community_key: "rust", observe_enabled: false }),
    ]);
    expect(cards.map((card) => card.dane["klucz"])).toEqual(["saas", "rust", "devops"]);
  });

  it("a_card_outside_a_conversation_sends_no_session_and_still_carries_the_workspace", () => {
    // Karty tego ekranu nie pochodzą ze śladu tury, więc pole rozmowy nie jedzie w formularzu.
    // Przestrzeń jedzie, bo handler `/studio/card` sprawdza po niej trasę z rodziny przestrzeni.
    const rendered = screen([community({ observe_enabled: false })]);
    const form = rendered.querySelector("form.st-card-form");
    expect(form?.getAttribute("action")).toBe("/studio/card");
    expect(form?.querySelector('input[name="session_id"]')).toBeNull();
    expect(form?.querySelector('input[name="workspace_id"]')?.getAttribute("value")).toBe(
      WORKSPACE_ID,
    );
    expect(form?.querySelector('input[name="sciezka"]')?.getAttribute("value")).toBe(
      "/api/communities",
    );
  });
});
