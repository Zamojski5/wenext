/**
 * Sekcja „Od Twojej ostatniej wizyty" i jej odczyt (P4 i P5).
 *
 * CZEGO PILNUJĄ TE TESTY. Każdy przypadek to jedno zdanie, które ten ekran mógłby powiedzieć
 * nieprawdziwie, a człowiek podjąłby na jego podstawie decyzję o tym, czy agent ma działać dalej:
 *
 *   * „nic się nie wydarzyło" powiedziane wtedy, gdy odczyt się nie udał;
 *   * publikacja, której nikt nie oglądał, pokazana jako publikacja, która zniknęła;
 *   * korekta pokazana jako zastosowana, choć widać tylko, że agent mógł ją zobaczyć;
 *   * decyzja czekająca na człowieka schowana pod sesjami, czyli pod rzeczą mniej pilną.
 */
import { describe, expect, it } from "vitest";

import { ReturnsBlock } from "../components/studio/returns";
import { parseReturnView } from "../lib/returns-schema";
import type { ReturnView } from "../lib/returns-schema";
import { renderToDocument } from "./render";

const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";
const PROJECT = "bbbbbbbb-0000-4000-8000-000000000001";

const EMPTY: ReturnView = {
  okno: {
    od: "2026-09-18T20:00:00+00:00",
    do: "2026-09-19T20:00:00+00:00",
    pierwsza_wizyta: false,
    zdanie: "Od Twojej ostatniej wizyty, czyli przez ostatnie 24 h.",
  },
  obudzenia: { razem: 0, pominiete: 0, z_sesja: 0, powody: {}, powody_pominiecia: {} },
  sesje: [],
  decyzje: [],
  publikacje: [],
  korekty: [],
  incydenty: [],
  uwagi: [],
  cisza: true,
};

function render(view?: ReturnView, reason?: string, autonomous = false) {
  return renderToDocument(
    <ReturnsBlock
      autonomous={autonomous}
      projectId={PROJECT}
      reason={reason}
      returnTo={`/w/${WORKSPACE}/p/${PROJECT}`}
      view={view}
      workspaceId={WORKSPACE}
    />,
  );
}

describe("sekcja powrotu", () => {
  it("does not turn an autonomous draft into a request for individual approval", () => {
    const screen = render({ ...EMPTY, decyzje: [{
      revision_id: "r1", tekst: "Draft waiting for the hourly budget", adres: null,
      konto: "Demo_Maintainer", czeka_od: "2026-09-22T16:56:00Z", wygasa: "2026-09-25T16:56:00Z",
    }] }, undefined, true);
    expect(screen.body.textContent).toContain("Przygotowany szkic");
    expect(screen.body.textContent).not.toContain("Czeka na Twoją decyzję");
    expect(screen.querySelector("[data-decision] a")?.getAttribute("href")).toContain("/publications");
  });

  it("says_when_a_waiting_draft_carries_a_screenshot", () => {
    // 27.09: an own post on X may carry a screenshot; the server names it apart from the text.
    const screen = render({ ...EMPTY, decyzje: [{
      revision_id: "r2", tekst: "News z komentarzem", obraz: "0f8fad5b-d9cb-469f-a165-70867728950e",
      adres: null, konto: "demo_founder", czeka_od: "2026-09-27T20:00:00Z", wygasa: "2026-09-30T20:00:00Z",
    }] });
    expect(screen.body.textContent).toContain("News z komentarzem Z obrazem: zrzut ekranu źródła.");
    const plain = render({ ...EMPTY, decyzje: [{
      revision_id: "r3", tekst: "Bez obrazu", obraz: null, adres: null, konto: "demo_founder",
      czeka_od: "2026-09-27T20:00:00Z", wygasa: "2026-09-30T20:00:00Z",
    }] });
    expect(plain.body.textContent).not.toContain("Z obrazem");
  });

  it("does not suggest that a blocked pre-submit attempt has a published post", () => {
    const screen = render({ ...EMPTY, publikacje: [{
      request_id: "q1", status: "BLOCKED", rodzaj: "REPLY", adres: "https://reddit.test/thread",
      opublikowano: null, powod: "[submit_started=false] platform block", widocznosc: null,
    }] });
    expect(screen.body.textContent).toContain("Zatrzymano przed wysłaniem");
    expect(screen.body.textContent).not.toContain("wpis zniknął");
    expect(screen.querySelector("[data-publication] a")?.textContent).not.toBe("Zobacz wpis");
  });

  it("shows_an_excerpt_and_opens_the_actual_conversation", () => {
    const screen = render({...EMPTY, sesje: [{session_id: "s1", status: "WAITING_USER", cel: "Długi cel", wynik: "Pełny materiał, który ma zostać w rozmowie", wynik_zdanie: "Przygotowałem pytania.", tytul: "Rozmowy", narzedzia: ["materials.save"], zaczeto: "2026-09-22T12:00:00Z", skonczono: null}]});
    expect(screen.body.textContent).toContain("Przygotowałem pytania.");
    expect(screen.body.textContent).not.toContain("Pełny materiał");
    expect(screen.body.textContent).not.toContain("materials.save");
    expect(screen.querySelector(`[href="/w/${WORKSPACE}/p/${PROJECT}/c/s1"]`)).not.toBeNull();
  });
  it("a_long_dash_in_the_sessions_sentence_is_softened_on_screen", () => {
    // Measured 27.09 on the presence screen: "Opublikowałem wyłącznie wskazaną rewizję \u2014 bez ...".
    const screen = render({...EMPTY, sesje: [{session_id: "s2", status: "WAITING_USER", cel: "Cel", wynik: "", wynik_zdanie: "Opublikowałem wskazaną rewizję \u2014 bez ponawiania.", tytul: "Rozmowa", narzedzia: [], zaczeto: "2026-09-27T02:09:00Z", skonczono: null}]});
    expect(screen.body.textContent).toContain("Opublikowałem wskazaną rewizję, bez ponawiania.");
    expect(screen.body.textContent).not.toMatch(/[\u2014\u2013]/);
  });
  it("a_failed_read_is_not_shown_as_a_quiet_period", () => {
    const rendered = render(undefined, "control plane odpowiedział 503");
    expect(rendered.body.textContent).toContain("Nie udało się policzyć");
    expect(rendered.body.textContent).toContain("To nie znaczy, że nic się nie wydarzyło");
  });

  it("says_how_long_the_window_is", () => {
    const rendered = render(EMPTY);
    const sentence = rendered.querySelector("[data-window-sentence]");
    expect(sentence?.textContent).toContain("przez ostatnie 24 h");
  });

  it("names_a_closed_gap_as_over_and_in_words_a_person_uses", () => {
    const rendered = render({
      ...EMPTY,
      incydenty: [
        {
          incident_id: "i-1",
          komponent: "DISPATCHER",
          powod: "brak pulsu od 2026-09-26T22:35:00+00:00",
          od: "2026-09-26T22:45:00+00:00",
          do: "2026-09-26T22:50:00+00:00",
          trwa: false,
        },
        { incident_id: "i-2", komponent: "NEW_PART", powod: "cisza", od: "2026-09-26T23:00:00+00:00", do: null, trwa: true },
      ],
    });
    const text = rendered.body.textContent ?? "";
    expect(text).toContain("Kolejka pracy agenta: przerwa zakończona");
    expect(text).toContain("praca wróciła");
    expect(text).not.toContain("DISPATCHER:");
    // An unknown part keeps its own name rather than disappearing from the list.
    expect(text).toContain("NEW_PART: przerwa trwa");
  });

  it("repeats_the_measured_silence_sentence_from_the_backend", () => {
    // Zdanie o ciszy jest POLICZONE po stronie control plane i tu tylko pokazane. Gdyby ekran
    // dopisywał je sam do pustej listy, mówiłby „kolektor pracował", nie wiedząc, czy pracował.
    const rendered = render({
      ...EMPTY,
      obudzenia: { razem: 6, pominiete: 6, z_sesja: 0, powody: { DUE: 6 }, powody_pominiecia: { BEZ_ZDARZEN: 6 } },
      uwagi: ["Nic się nie wydarzyło, a kolektor pracował 6 razy. Cisza jest zmierzona, nie domyślona."],
    });
    expect(rendered.body.textContent).toContain("kolektor pracował 6 razy");
    expect(rendered.body.textContent).toContain("Obudzeń: 6");
    expect(rendered.body.textContent).toContain("BEZ_ZDARZEN 6");
  });

  it("puts_what_waits_for_a_decision_above_what_the_agent_already_did", () => {
    const rendered = render({
      ...EMPTY,
      cisza: false,
      decyzje: [
        {
          revision_id: "r1",
          tekst: "propozycja odpowiedzi",
          adres: "https://reddit.test/1",
          konto: "Demo_Maintainer",
          czeka_od: "2026-09-19T10:00:00+00:00",
          wygasa: "2026-09-20T10:00:00+00:00",
        },
      ],
      sesje: [
        {
          session_id: "s1",
          status: "DONE",
          cel: "obserwuj",
          wynik: "przeczytałem wątek",
          narzedzia: ["sources.read"],
          zaczeto: "2026-09-19T09:00:00+00:00",
          skonczono: "2026-09-19T09:30:00+00:00",
        },
      ],
    });
    const parts = Array.from(rendered.querySelectorAll("[data-part]")).map((node) =>
      node.getAttribute("data-part"),
    );
    expect(parts.indexOf("decyzje")).toBeLessThan(parts.indexOf("sesje"));
    // Rozstrzyga się w Obecności i nigdzie indziej: jedna zgoda, jedno miejsce.
    const button = rendered.querySelector("[data-decision] a");
    expect(button?.getAttribute("href")).toBe(`/w/${WORKSPACE}/p/${PROJECT}/presence`);
  });

  it("a_publication_nobody_checked_is_not_a_publication_that_vanished", () => {
    const rendered = render({
      ...EMPTY,
      cisza: false,
      publikacje: [
        {
          request_id: "q1",
          status: "PUBLISHED",
          rodzaj: "REPLY",
          adres: "https://reddit.test/abc",
          opublikowano: "2026-09-19T11:00:00+00:00",
          powod: null,
          widocznosc: null,
        },
      ],
    });
    expect(rendered.body.textContent).toContain("Widoczności nikt jeszcze nie sprawdzał");
    expect(rendered.body.textContent).not.toContain("nie ma go już");
  });

  it("a_measured_publication_shows_what_was_measured", () => {
    const rendered = render({
      ...EMPTY,
      cisza: false,
      publikacje: [
        {
          request_id: "q1",
          status: "PUBLISHED",
          rodzaj: "REPLY",
          adres: "https://reddit.test/abc",
          opublikowano: "2026-09-19T11:00:00+00:00",
          powod: null,
          widocznosc: {
            sprawdzono: "2026-09-19T12:00:00+00:00",
            jest: true,
            punkty: 7,
            odpowiedzi: 2,
            wyswietlenia: null,
          },
        },
      ],
    });
    expect(rendered.body.textContent).toContain("jest na miejscu");
    expect(rendered.body.textContent).toContain("odpowiedzi: 2");
    expect(rendered.body.textContent).toContain("punkty: 7");
  });

  it("an_x_post_counts_likes_not_points", () => {
    const rendered = render({
      ...EMPTY,
      cisza: false,
      publikacje: [
        {
          request_id: "q3",
          status: "PUBLISHED",
          rodzaj: "OWN_POST",
          adres: "https://x.com/demo_founder/status/2104030943193858549",
          opublikowano: "2026-09-27T02:10:29+00:00",
          powod: null,
          widocznosc: {
            sprawdzono: "2026-09-27T02:10:53+00:00",
            jest: true,
            punkty: 0,
            odpowiedzi: 0,
            wyswietlenia: 5,
          },
        },
      ],
    });
    expect(rendered.body.textContent).toContain("polubienia: 0");
    expect(rendered.body.textContent).toContain("wyświetlenia: 5");
    expect(rendered.body.textContent).not.toContain("punkty");
  });

  it("views_nobody_measured_are_left_out_not_shown_as_zero", () => {
    const withoutViews = parseReturnView({
      okno: EMPTY.okno,
      obudzenia: EMPTY.obudzenia,
      sesje: [],
      decyzje: [],
      publikacje: [
        {
          request_id: "q4",
          status: "PUBLISHED",
          rodzaj: "REPLY",
          adres: "https://www.reddit.com/r/x/comments/abc/",
          opublikowano: "2026-09-19T11:00:00+00:00",
          powod: null,
          // An older control plane: no `wyswietlenia` at all.
          widocznosc: { sprawdzono: "2026-09-19T12:00:00+00:00", jest: true, punkty: 3, odpowiedzi: 1 },
        },
      ],
      korekty: [],
      incydenty: [],
      uwagi: [],
      cisza: false,
    } as unknown as Record<string, unknown>);
    expect(withoutViews.ok).toBe(true);
    if (!withoutViews.ok) return;
    expect(withoutViews.value.publikacje[0]?.widocznosc?.wyswietlenia).toBeNull();
    const rendered = render(withoutViews.value);
    expect(rendered.body.textContent).toContain("punkty: 3");
    expect(rendered.body.textContent).not.toContain("wyświetlenia");
  });

  it("says_a_correction_was_in_reach_and_never_that_it_was_obeyed", () => {
    const rendered = render({
      ...EMPTY,
      cisza: false,
      korekty: [
        {
          item_id: "k1",
          tresc: "Nie odpowiadaj w starych wątkach",
          zapisano: "2026-09-19T08:00:00+00:00",
          w_zasiegu_sesji: true,
        },
      ],
    });
    expect(rendered.body.textContent).toContain("miała ją przed sobą");
    expect(rendered.body.textContent).not.toContain("zastosował");
  });

  it("offers_one_sentence_back_to_the_agent", () => {
    const rendered = render(EMPTY);
    const form = rendered.querySelector("form");
    expect(form?.getAttribute("action")).toBe("/studio/return");
    const names = Array.from(rendered.querySelectorAll("input")).map((node) =>
      node.getAttribute("name"),
    );
    expect(names).toEqual(["action", "workspace_id", "project_id", "return_to", "text"]);
  });
});

describe("odczyt widoku powrotu", () => {
  const envelope = {
    okno: EMPTY.okno,
    obudzenia: EMPTY.obudzenia,
    sesje: [],
    decyzje: [],
    publikacje: [],
    korekty: [],
    incydenty: [],
    uwagi: [],
    cisza: true,
  } as unknown as Record<string, unknown>;

  it("accepts_the_shape_the_control_plane_sends", () => {
    const parsed = parseReturnView(envelope);
    expect(parsed.ok).toBe(true);
  });

  it("a_missing_field_fails_the_whole_read_instead_of_becoming_zero", () => {
    const broken = { ...envelope, obudzenia: { razem: 3 } };
    const parsed = parseReturnView(broken);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.reason).toContain("obudzenia");
  });

  it("says_when_the_thread_was_ours_instead_of_showing_a_bare_status", () => {
    // 0023: „nasz wątek" i „odpowiedź" to dwa różne działania wobec społeczności. Sam status
    // PUBLISHED nie mówi, czy agent założył coś swojego, a to jest pierwsza rzecz, o którą
    // człowiek pyta po powrocie.
    const rendered = render({
      ...EMPTY,
      cisza: false,
      publikacje: [
        {
          request_id: "q2",
          status: "PUBLISHED",
          rodzaj: "OWN_POST",
          adres: "https://reddit.test/own",
          opublikowano: "2026-09-19T11:00:00+00:00",
          powod: null,
          widocznosc: null,
        },
      ],
    });
    expect(rendered.body.textContent).toContain("nasz wątek: wysłana");
    expect(rendered.body.textContent).not.toContain("OWN_POST");
    expect(rendered.body.textContent).not.toContain("PUBLISHED");
  });

  it("keeps_an_unmeasured_visibility_as_null_rather_than_dropping_it", () => {
    const withPublication = {
      ...envelope,
      publikacje: [
        {
          request_id: "q1",
          status: "PUBLISHED",
          rodzaj: "REPLY",
          adres: "https://reddit.test/abc",
          opublikowano: null,
          powod: null,
          widocznosc: null,
        },
      ],
    };
    const parsed = parseReturnView(withPublication);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.value.publikacje[0]?.widocznosc).toBeNull();
  });
});
