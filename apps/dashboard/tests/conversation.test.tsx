/**
 * The conversation block, which is where the studio either tells the truth about work or does not.
 *
 * FOUR RULES, and each one is a way a screen can claim something that is not happening:
 *
 *   1. the mark waves ONLY while a turn is in flight. `RUNNING` waves, `QUEUED` does not, and
 *      `QUEUED` is the dangerous one: a job exists and nobody has claimed it, which looks like
 *      progress and is not (ZAKRES-FUNKCJONALNY §5);
 *   2. tool calls are collapsed but present. A conversation showing only prose lets the agent's
 *      claims stand with nothing under them;
 *   3. a tool result prints the four-way outcome it was given. `OCZEKUJE` is not rounded to
 *      success and not to failure, because a draft waiting for consent is neither;
 *   4. a finished session offers no composer, and a live one does. A field on a closed session
 *      would accept a message nothing will ever answer.
 *
 * The composer's own refusals are measured here too, and since E1 there is exactly ONE: a paused
 * project disables the field with the reason. Brak połączenia Codex przestał być odmową i stał
 * się obietnicą („zapiszę cel i zacznę, gdy podłączysz"), bo czekanie na zasób, który sam się
 * pojawi, to nie jest powód, żeby nie przyjąć zdania od człowieka.
 */
import { describe, expect, it } from "vitest";

import { Composer } from "../components/studio/composer";
import { Conversation } from "../components/studio/conversation";
import type { SessionDetail, SessionStatus } from "../lib/session-schema";
import { renderToDocument } from "./render";

function detail(overrides: Partial<SessionDetail["session"]> = {}, events: unknown[] = []) {
  return {
    session: {
      session_id: "11111111-1111-1111-1111-111111111111",
      project_id: "22222222-2222-2222-2222-222222222222",
      goal: "sprawdź, dlaczego odzew jest mały",
      status: "WAITING_USER" as SessionStatus,
      engine_thread_id: "th_1",
      result_summary: null,
      result_refs: [],
      created_at: "2026-09-19T12:00:00+00:00",
      updated_at: "2026-09-19T12:05:00+00:00",
      finished_at: null,
      ...overrides,
    },
    events: events as SessionDetail["events"],
    last_seq: events.length,
    budzet: null,
  } satisfies SessionDetail;
}

function render(value: SessionDetail) {
  return renderToDocument(
    <Conversation
      detail={value}
      projectId="22222222-2222-2222-2222-222222222222"
      returnTo="/w/a/p/b"
      workspaceId="33333333-3333-3333-3333-333333333333"
    />,
  );
}

const GOAL_EVENT = {
  seq: 1,
  kind: "USER_MESSAGE",
  payload: { text: "sprawdź odzew", is_goal: true },
  created_at: "2026-09-19T12:00:00+00:00",
};

describe("Conversation: what is moving", () => {
  it("waves only while a turn is running", () => {
    const running = render(detail({ status: "RUNNING" }));
    expect(running.querySelector(".st-wave")?.getAttribute("data-waiting")).toBe("yes");
    expect(running.body.textContent).toContain("pracuje");
  });

  it("does NOT wave for a queued session, which nobody has claimed yet", () => {
    const queued = render(detail({ status: "QUEUED", engine_thread_id: null }));
    expect(queued.querySelector(".st-wave")?.getAttribute("data-waiting")).toBe("no");
    expect(queued.body.textContent).toContain("w kolejce");
  });

  it("names every state in words rather than printing the state name alone", () => {
    for (const status of ["WAITING_USER", "PAUSED", "FAILED", "BUDGET_EXHAUSTED"] as const) {
      const screen = render(detail({ status }));
      expect(screen.querySelector(".st-wave")?.getAttribute("data-waiting")).toBe("no");
      expect(screen.body.textContent?.length ?? 0).toBeGreaterThan(20);
    }
  });
});

describe("Conversation: what the answer rests on", () => {
  it("shows the goal, the answer and the tool call between them", () => {
    const screen = render(
      detail({}, [
        GOAL_EVENT,
        {
          seq: 2,
          kind: "TOOL_CALL",
          payload: { tool: "thread.read", arguments: { thread_id: "e2e001" } },
          created_at: "2026-09-19T12:01:00+00:00",
        },
        {
          seq: 3,
          kind: "TOOL_RESULT",
          payload: { tool: "thread.read", status: "completed", result: { stan: "WYKONANE" } },
          created_at: "2026-09-19T12:01:05+00:00",
        },
        {
          seq: 4,
          kind: "ASSISTANT_MESSAGE",
          payload: { text: "Wchodzimy za późno." },
          created_at: "2026-09-19T12:02:00+00:00",
        },
      ]),
    );
    const body = screen.body.textContent ?? "";
    expect(body).toContain("sprawdź odzew");
    expect(body).toContain("Wchodzimy za późno.");
    expect(body).toContain("thread.read");
    expect(screen.querySelectorAll(".st-tool").length).toBe(2);
    // Collapsed, not hidden: a `details` that is closed is one click from the arguments.
    expect(screen.querySelector(".st-tool")?.hasAttribute("open")).toBe(false);
  });

  it("prints the tool's own outcome and does not round OCZEKUJE to anything", () => {
    const screen = render(
      detail({}, [
        {
          seq: 1,
          kind: "TOOL_RESULT",
          payload: {
            tool: "draft.create",
            status: "completed",
            result: { stan: "OCZEKUJE", powod: "czeka na zgodę" },
          },
          created_at: "2026-09-19T12:00:00+00:00",
        },
      ]),
    );
    const body = screen.body.textContent ?? "";
    expect(body).toContain("OCZEKUJE");
    expect(body).not.toContain("WYKONANE");
  });

  it("shows a failed tool call with its error rather than as a plain result", () => {
    const screen = render(
      detail({}, [
        {
          seq: 1,
          kind: "TOOL_RESULT",
          payload: { tool: "thread.read", status: "failed", error: "nie ma takiego wątku" },
          created_at: "2026-09-19T12:00:00+00:00",
        },
      ]),
    );
    expect(screen.body.textContent).toContain("nie ma takiego wątku");
  });

  it("says a conversation is empty instead of rendering nothing", () => {
    expect(render(detail()).body.textContent).toContain("ani jednego wpisu");
  });
});

describe("Conversation: what the person can do", () => {
  it("offers send and interrupt while a turn runs", () => {
    const screen = render(detail({ status: "RUNNING" }));
    // Przycisk wysyłki jest OKRĄGŁY I BEZ NAPISU (makieta D-046), więc rozpoznajemy go po
    // etykiecie dla czytnika, a nie po treści: napis „Wyślij" zniknął z ekranu celowo i test,
    // który go szuka, pilnowałby wyglądu sprzed makiety.
    const send = screen.querySelector(".st-send");
    expect(send?.getAttribute("aria-label")).toBe("Wyślij wiadomość");
    expect(screen.querySelector('textarea[name="text"]')?.hasAttribute("disabled")).toBe(false);
    const actions = [...screen.querySelectorAll("button")].map((button) => button.textContent);
    expect(actions).toContain("Przerwij");
  });

  it("the_line_under_the_field_says_what_is_happening_instead_of_blocking_typing", () => {
    // D-046: pole zostaje otwarte w czasie pracy agenta, a stan jest zdaniem pod nim.
    const running = render(detail({ status: "RUNNING" }));
    expect(running.querySelector("[data-composer-status]")?.textContent).toContain(
      "możesz dopowiedzieć",
    );
    const waiting = render(detail({ status: "WAITING_USER" }));
    expect(waiting.querySelector("[data-composer-status]")?.textContent).toBe(
      "Rozmowa trwa dalej",
    );
  });

  it("offers resume rather than interrupt when the session is paused", () => {
    const screen = render(detail({ status: "PAUSED" }));
    const actions = [...screen.querySelectorAll("button")].map((button) => button.textContent);
    expect(actions).toContain("Wznów");
    expect(actions).not.toContain("Przerwij");
  });

  it("offers no field at all once the session is finished", () => {
    const screen = render(detail({ status: "DONE", finished_at: "2026-09-19T13:00:00+00:00" }));
    expect(screen.querySelector("textarea")).toBeNull();
    expect(screen.body.textContent).toContain("zakończona");
  });
});

describe("Composer", () => {
  const base = {
    projectName: "Obecność devops",
    workspaceId: "33333333-3333-3333-3333-333333333333",
    projectId: "22222222-2222-2222-2222-222222222222",
    returnTo: "/w/a/p/b",
  };

  it("entrusts a goal when there is a connection to run on", () => {
    const screen = renderToDocument(
      <Composer {...base} connections={[{ id: "c1", label: "Konto założyciela" }]} paused={false} />,
    );
    const field = screen.querySelector("textarea");
    expect(field?.hasAttribute("disabled")).toBe(false);
    // One connection needs no choice, and the choice must still travel with the form.
    expect(screen.querySelector('input[name="codex_connection_id"]')?.getAttribute("value")).toBe(
      "c1",
    );
    expect(screen.querySelector('form[action="/studio/session"]')).not.toBeNull();
    expect(screen.querySelector('input[name="action"]')?.getAttribute("value")).toBe("start");
  });

  it("asks_what_we_are_doing_in_this_project_and_names_it", () => {
    // D-045, smak z prototypu: pusty projekt pyta imieniem projektu, a nie „Nowa rozmowa".
    // Nazwa jest w pytaniu, więc sam przycisk mógłby stać bez podpisu i nadal byłoby wiadomo,
    // w czyim imieniu się pisze.
    const screen = renderToDocument(
      <Composer
        {...base}
        connections={[{ id: "c1", label: "Konto założyciela" }]}
        conversationCount={3}
        mode="AUTONOMOUS"
        paused={false}
      />,
    );
    const text = screen.body.textContent ?? "";
    expect(text).toContain("Co robimy w Obecność devops?");
    expect(text).toContain("3 rozmowy");
    expect(screen.querySelector("textarea")?.getAttribute("placeholder")).toContain(
      "Nadaj kierunek",
    );
    // W PASKU POLA ZOSTAŁO TO, CO MA REFERENCJA, I NIC WIĘCEJ (odbiór 1, różnica nr 2).
    // Chipy „projekt" i „tryb" znikły, bo powtarzały to, co widać wyżej: nazwa projektu stoi w
    // pytaniu nad polem i w okruszkach, a tryb w prawym górnym rogu ramy. Chip, który nie mówi
    // nic nowego, zabiera miejsce zdaniu, które mówi.
    expect(screen.querySelectorAll('[data-chip="project"]').length).toBe(0);
    expect(screen.querySelectorAll('[data-chip="mode"]').length).toBe(0);
    // Silnik ZOSTAJE, bo mówi, na czym ta rozmowa ruszy, ale wygląda jak tekst z referencji.
    expect(screen.querySelector('[data-chip="account"]')?.textContent).toContain("Codex");
    // I kółko „+" z referencji, które naprawdę coś otwiera.
    expect(screen.querySelector('[data-pop="composer-context"]')).not.toBeNull();
    expect(screen.querySelector(".st-send")?.getAttribute("aria-label")).toBe("Zacznij rozmowę");
    // Pole stoi na DNIE kolumny, więc pas dolny z referencji musi istnieć.
    expect(screen.querySelector(".wm-bottom")).not.toBeNull();
  });

  it("a_project_without_conversations_says_so_instead_of_showing_a_zero", () => {
    const screen = renderToDocument(
      <Composer {...base} connections={[{ id: "c1", label: "Konto" }]} paused={false} />,
    );
    expect(screen.body.textContent).toContain("jeszcze bez rozmów");
    expect(screen.body.textContent).not.toContain("0 rozmów");
  });

  it("lets the person choose when there are several connections", () => {
    const screen = renderToDocument(
      <Composer
        {...base}
        connections={[
          { id: "c1", label: "Anna" },
          { id: "c2", label: "Jan" },
        ]}
        paused={false}
      />,
    );
    expect(screen.querySelectorAll('select[name="codex_connection_id"] option').length).toBe(2);
  });

  it("takes_the_first_goal_without_an_engine_and_promises_to_start_later", () => {
    // ODWRÓCONE 20.09 (E1, audyt UX). Wcześniej ten sam przypadek wymagał pola WYŁĄCZONEGO i
    // zdania odsyłającego na ekran połączeń. Audyt nazwał to blokadą startu: człowiek przyszedł
    // napisać, po co ten projekt jest, a dostawał zadanie administracyjne i jego zdanie nie
    // miało gdzie trafić. Cel zapisuje się dziś bez silnika, a rozmowa czeka z kartą SILNIK.
    const screen = renderToDocument(<Composer {...base} connections={[]} paused={false} />);
    expect(screen.querySelector("textarea")?.hasAttribute("disabled")).toBe(false);
    expect(screen.querySelector("form")?.getAttribute("action")).toBe("/studio/session");
    // Chip nie udaje, że jest na czym ruszyć, a pole wyboru połączenia nie powstaje, bo nie ma
    // między czym wybierać.
    expect(screen.querySelector('[data-chip="account"]')?.textContent).toContain(
      "brak połączenia",
    );
    expect(screen.querySelector('select[name="codex_connection_id"]')).toBeNull();
    expect(screen.querySelector('input[name="codex_connection_id"]')).toBeNull();
    // Obietnica przed naciśnięciem, nie po: praca zacznie się później i nikt nie będzie pisał
    // tego drugi raz.
    expect(screen.body.textContent).toContain("Zapiszę cel i zacznę, gdy podłączysz Codexa");
  });

  it("disables the field when the project is paused, and says that instead", () => {
    const screen = renderToDocument(
      <Composer {...base} connections={[{ id: "c1", label: "Konto" }]} paused={true} />,
    );
    expect(screen.querySelector("textarea")?.hasAttribute("disabled")).toBe(true);
    expect(screen.body.textContent).toContain("wstrzymany");
    // Tryb pracy NIE jest pokazany przy wstrzymanym projekcie: opisywałby pracę, której nikt
    // nie wykonuje, a dwa stany obok siebie każą zgadywać, który obowiązuje.
    expect(screen.querySelector('[data-chip="mode"]')).toBeNull();
  });
});

describe("odpowiedź agenta wygląda jak odpowiedź", () => {
  // Uwaga założyciela z 20.09, 01:15, ze zrzutem ekranu: odpowiedź stała jako jedna ściana
  // tekstu z gwiazdkami, kratkami i backtickami. Każdy przypadek niżej to jeden znak z tamtego
  // zrzutu, który ma przestać być widoczny jako znak, a zacząć być widoczny jako kształt.
  const answer = (text: string) =>
    render(
      detail({ status: "WAITING_USER" }, [
        GOAL_EVENT,
        {
          seq: 2,
          kind: "ASSISTANT_MESSAGE",
          payload: { text },
          created_at: "2026-09-19T12:03:00+00:00",
        },
      ]),
    );

  it("a_heading_is_a_heading_and_never_a_line_of_hashes", () => {
    const rendered = answer("### Robocze pozycjonowanie\nWenext pomaga wracać do rozmów.");
    const heading = rendered.querySelector(".st-answer h3");
    expect(heading?.textContent).toBe("Robocze pozycjonowanie");
    expect(rendered.body.textContent).not.toContain("###");
    // Nigdy `h1`: tytułem ekranu jest nazwa projektu, a nie zdanie modelu.
    expect(rendered.querySelector(".st-answer h1")).toBeNull();
  });

  it("a_numbered_list_is_a_list_and_the_browser_draws_the_numbers", () => {
    const rendered = answer("1. **Kategoria:** narzędzie\n2. **Odbiorcy:** zespoły\n");
    const items = rendered.querySelectorAll(".st-answer ol li");
    expect(items.length).toBe(2);
    // Numer nie jest przepisany do treści: w tekście pozycji nie ma „1.".
    expect(items[0]?.textContent).toBe("Kategoria: narzędzie");
    // Etykieta jest pogrubiona, bo to po niej oko skacze w dół listy.
    expect(items[0]?.querySelector("strong")?.textContent).toBe("Kategoria:");
    expect(rendered.body.textContent).not.toContain("**");
  });

  it("a_bulleted_list_stays_a_separate_list_from_a_numbered_one", () => {
    const rendered = answer("- pierwszy\n- drugi\n\n1. krok\n2. krok drugi");
    expect(rendered.querySelectorAll(".st-answer ul li").length).toBe(2);
    expect(rendered.querySelectorAll(".st-answer ol li").length).toBe(2);
  });

  it("a_quote_is_a_quote_and_never_a_line_starting_with_a_bracket", () => {
    const rendered = answer("> Wenext pomaga wracać do rozmów.\n\nDalszy akapit.");
    expect(rendered.querySelector(".st-answer blockquote")?.textContent).toBe(
      "Wenext pomaga wracać do rozmów.",
    );
    expect(rendered.body.textContent).not.toContain("> Wenext");
  });

  it("an_identifier_in_backticks_becomes_code_without_being_shortened", () => {
    const rendered = answer("Projekt `5b70a9f2-0000-4000-8000-000000000001` jest gotowy.");
    const code = rendered.querySelector(".st-answer code");
    expect(code?.textContent).toBe("5b70a9f2-0000-4000-8000-000000000001");
    expect(rendered.body.textContent).not.toContain("`");
  });

  it("a_heading_glued_to_a_paragraph_without_a_blank_line_still_separates", () => {
    // Model pisze tak najczęściej i to jest ta ściana tekstu ze zrzutu.
    const rendered = answer("Cel zapisany.\n### Pozycjonowanie\n- jedno\n- drugie");
    expect(rendered.querySelectorAll(".st-answer p").length).toBe(1);
    expect(rendered.querySelectorAll(".st-answer h3").length).toBe(1);
    expect(rendered.querySelectorAll(".st-answer ul li").length).toBe(2);
  });

  it("the_last_result_is_shown_as_an_answer_not_as_a_grey_note", () => {
    const rendered = render(
      detail({ status: "DONE", result_summary: "### Wynik\nGotowe, projekt zapisany." }, [
        GOAL_EVENT,
      ]),
    );
    const block = rendered.querySelector('[data-kind="summary"]');
    expect(block?.querySelector(".st-answer h3")?.textContent).toBe("Wynik");
    expect(block?.textContent).toContain("Gotowe, projekt zapisany.");
    // Stara notka z prefiksem znika: to była jedna szara linia z markdownem w środku.
    expect(rendered.body.textContent).not.toContain("Ostatni wynik:");
  });

  it("the_result_is_not_repeated_when_the_agent_already_said_it", () => {
    const said = "Cel projektu został zapisany.";
    const rendered = render(
      detail({ status: "DONE", result_summary: said }, [
        GOAL_EVENT,
        {
          seq: 2,
          kind: "ASSISTANT_MESSAGE",
          payload: { text: said },
          created_at: "2026-09-19T12:03:00+00:00",
        },
      ]),
    );
    expect(rendered.querySelector('[data-kind="summary"]')).toBeNull();
    // Raz, nie dwa: to samo zdanie pod sobą czyta się jak usterka.
    const occurrences = (rendered.body.textContent ?? "").split(said).length - 1;
    expect(occurrences).toBe(1);
  });
});

describe("koniec rozmowy prowadzi do następnej (D-045)", () => {
  // Do 20.09 zakończona rozmowa kończyła się zdaniem „Sesja jest zakończona. Zacznij nową" i na
  // tym ekran się urywał: człowiek zostawał z pytaniem, GDZIE ta nowa jest. Po D-045 rozmowa
  // jest tania i jednorazowa, więc stan końcowy ma wyjście, a nie komunikat.
  for (const status of ["DONE", "FAILED", "BUDGET_EXHAUSTED"] as const) {
    it(`a_${status.toLowerCase()}_conversation_offers_a_new_one_in_the_same_project`, () => {
      const screen = render(detail({ status }));
      const form = screen.querySelector('form[action="/studio/session"]');
      expect(form).not.toBeNull();
      expect(form?.querySelector('input[name="action"]')?.getAttribute("value")).toBe("continue");
      // `continue`, nie `start`: nowa rozmowa niesie wtedy `parent_session_id` i po tygodniu
      // widać, która z której wyrosła. Dwie drogi do jednej rzeczy zatarłyby ten ślad.
      expect(form?.querySelector('input[name="session_id"]')).not.toBeNull();
      expect(screen.body.textContent).toContain("Nowa rozmowa");
      // I żadnego pola do pisania: wiadomość wysłana do zamkniętej rozmowy nie ma kto odebrać.
      expect(screen.querySelector('textarea[name="text"]')).toBeNull();
    });
  }

  it("a_closed_conversation_says_the_next_one_starts_from_what_the_project_knows", () => {
    const screen = render(detail({ status: "DONE" }));
    expect(screen.body.textContent).toContain("kontekst projektu");
    expect(screen.body.textContent).not.toContain("Sesja jest zakończona");
  });

  it("the_budget_case_keeps_its_own_sentence_from_the_control_plane", () => {
    // Zdanie o limicie przychodzi z backendu, żeby ekran i ślad mówiły dokładnie to samo.
    const screen = render(
      detail({ status: "BUDGET_EXHAUSTED", result_summary: "Limit 30 wymian wyczerpany." }),
    );
    expect(screen.body.textContent).toContain("Limit 30 wymian wyczerpany.");
    expect(screen.querySelector('[data-kind="exhausted"]')).not.toBeNull();
  });
});

describe("rozmowa wstrzymana czymś z zewnątrz", () => {
  it("waiting_on_an_event_keeps_the_field_open_and_says_what_it_waits_for", () => {
    // Limit konta Codex wstrzymuje rozmowę i wznawia ją sam (D-045). Pole zostaje otwarte, bo
    // człowiek może w tym czasie dopisać kierunek, a wiadomość dojdzie w następnej turze.
    const screen = render(
      detail({
        status: "WAITING_EVENT",
        result_summary: "Limit konta Codex. Rozmowa wznowi się po 05:00.",
      }),
    );
    expect(screen.querySelector('textarea[name="text"]')?.hasAttribute("disabled")).toBe(false);
    expect(screen.querySelector("[data-waiting-reason]")?.textContent).toContain(
      "wznowi się po 05:00",
    );
  });

  it("without_a_sentence_from_the_server_the_screen_invents_none", () => {
    // Godzinę odnowienia zna serwer. Ekran, który by ją zgadywał, podałby porę z innej strefy.
    const screen = render(detail({ status: "WAITING_EVENT" }));
    expect(screen.querySelector("[data-waiting-reason]")).toBeNull();
    expect(screen.body.textContent).toContain("czeka na zdarzenie");
  });
});

describe("jedno zdanie raz, jedna bańka bez drugiego tła (D-046)", () => {
  // Obie uwagi z porównania 1:1 z 20.09. Pierwsza jest o treści, druga o układzie, ale obie
  // wynikają z tego samego: element, który przed makietą pełnił jakąś rolę, po przeniesieniu
  // pełni ją drugi raz obok nowego.

  it("a_status_line_identical_to_the_result_is_shown_once_and_only_as_the_result", () => {
    const sentence = "Sesja nie ma połączenia Codex, na którym mogłaby ruszyć.";
    const screen = render(
      detail({ status: "FAILED", result_summary: sentence }, [
        GOAL_EVENT,
        { seq: 2, kind: "STATUS", payload: { detail: sentence, status: "FAILED" }, created_at: "" },
      ]),
    );
    const text = screen.body.textContent ?? "";
    expect(text).toContain(sentence);
    // Dokładnie raz. Dwa razy to samo zdanie pod sobą czyta się jak usterka renderowania.
    expect(text.split(sentence).length - 1).toBe(1);
  });

  it("a_status_line_that_says_something_else_is_kept", () => {
    // Filtr jest na RÓWNOŚĆ, nie na podobieństwo: wiersz o czymś innym niesie własną treść.
    const screen = render(
      detail({ status: "FAILED", result_summary: "Nie udało się." }, [
        GOAL_EVENT,
        {
          seq: 2,
          kind: "STATUS",
          payload: { detail: "Worker stracił lease w połowie tury.", status: "FAILED" },
          created_at: "",
        },
      ]),
    );
    expect(screen.body.textContent).toContain("Worker stracił lease");
    expect(screen.body.textContent).toContain("Nie udało się.");
  });

  it("the_goal_label_sits_outside_the_bubble_so_there_is_only_one_background", () => {
    // Makieta ma JEDNĄ uniesioną bańkę po prawej. „powierzony cel" zostaje jako mała etykieta,
    // ale poza `st-user`, żeby nie było panelu w panelu.
    const screen = render(detail({}, [GOAL_EVENT]));
    const bubble = screen.querySelector(".st-user");
    expect(bubble).not.toBeNull();
    expect(bubble?.querySelector(".st-note")).toBeNull();
    expect(screen.querySelector('[data-who="user"] > .st-note')?.textContent).toBe(
      "powierzony cel",
    );
  });
});
