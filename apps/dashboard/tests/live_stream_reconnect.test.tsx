/**
 * Zachowanie skryptu strumienia, uruchomionego naprawdę, a nie przeczytanego jako tekst.
 *
 * Z AUDYTU NIEZALEŻNEGO AGENTA, 19.09.2026 (`output/audyt-pivot-2026-09-19/check_stream.mjs`).
 * Audyt uruchomił skrypt z `conversation-live.tsx` w maszynie wirtualnej Node z atrapą
 * `EventSource` i zmierzył coś, czego żaden z ówczesnych testów nie sprawdzał: po ramce kończącej
 * połączenie po limicie czasu skrypt zamykał strumień i przechodził w „idle”, choć sesja nadal
 * była RUNNING. Pomiar audytu: `{"connections":1,"closed":true,"timers":0,"states":["running",
 * "idle"]}`. Po dwóch minutach ekran przestawał pokazywać pracującą turę i nic już go nie budziło.
 *
 * Poprawka rozdzieliła dwie rzeczy, które wcześniej nazywały się tak samo: `wznow` znaczy
 * „połącz się znowu od ostatniego numeru”, `koniec` znaczy „praca się skończyła”. Te testy
 * pilnują obu, plus zgodności ze starszym control plane, który po limicie czasu przysyłał
 * jeszcze `koniec` z powodem `deadline`.
 *
 * DLACZEGO TU, A NIE W `conversation_live.test.tsx`. Tamten plik sprawdza, co jest w ŹRÓDLE
 * skryptu (brak `innerHTML`, adres względny). Ten wykonuje skrypt i patrzy, co robi. Pierwsze nie
 * zastąpi drugiego: cały błąd, który znalazł audyt, był poprawnie napisanym `source.close()`.
 */
import { createContext, runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

import { ConversationLive } from "../components/studio/conversation-live";
import { renderToDocument } from "./render";

interface FakeStream {
  readonly url: string;
  readonly handlers: Record<string, (message: { data: string }) => void>;
  closed: boolean;
}

interface Harness {
  readonly streams: readonly FakeStream[];
  /** Stan znacznika TERAZ, czytany z DOM-u. Nie log wywołań: log mówiłby o funkcji, nie o ekranie. */
  state: () => string;
  readonly timers: readonly (() => void)[];
  readonly reloads: () => number;
  readonly document: Document;
}

/**
 * Renderuje komponent, wyjmuje jego skrypt Z RENDERU i uruchamia go na prawdziwym DOM-ie.
 *
 * WCZEŚNIEJ TEN HARNESS CZYTAŁ ŹRÓDŁO I PODSTAWIAŁ ATRAPY DOM-u. Przestało to działać w chwili,
 * w której skrypt zaczął mieć wstawkę z szablonu (`${...}`): surowe źródło nie jest już poprawnym
 * JavaScriptem, a atrapy i tak dowodziły czegoś innego niż to, co dostaje przeglądarka. Teraz
 * uruchamiamy dokładnie ten tekst, który trafia do HTML, na dokładnie tym drzewie, które
 * renderuje serwer, więc test widzi to, co zobaczy człowiek.
 */
function run(status: "RUNNING" | "DONE" = "RUNNING", afterSeq = 0): Harness {
  const rendered = renderToDocument(
    <ConversationLive
      afterSeq={afterSeq}
      projectId="bbbbbbbb-0000-4000-8000-000000000001"
      sessionId="cccccccc-0000-4000-8000-000000000001"
      status={status}
      workspaceId="aaaaaaaa-0000-4000-8000-000000000001"
    />,
  );
  const script = rendered.querySelector("script")?.textContent ?? "";
  if (script === "") throw new Error("komponent nie wyrenderował skryptu");

  const streams: FakeStream[] = [];
  const timers: (() => void)[] = [];
  let reloads = 0;

  // Znacznik stanu jest atrybutem na elemencie, więc stan czytamy z DOM-u, a nie z podstawionej
  // funkcji: to jest ta sama droga, którą widzi człowiek.
  const mark = rendered.querySelector("[data-live-state]");
  const observed = () => mark?.getAttribute("data-live-state") ?? "";

  class FakeEventSource {
    handlers: Record<string, (message: { data: string }) => void> = {};
    closed = false;
    constructor(public url: string) {
      streams.push(this as unknown as FakeStream);
    }
    addEventListener(kind: string, fn: (message: { data: string }) => void) {
      this.handlers[kind] = fn;
    }
    close() {
      this.closed = true;
    }
  }

  const context = createContext({
    document: rendered,
    EventSource: FakeEventSource,
    CustomEvent: class {
      constructor(public type: string) {}
    },
    window: {
      setTimeout: (fn: () => void) => timers.push(fn),
      // A hydrated page: the script binds at once (27.09, it waits for hydration otherwise).
      wenextSoftRefresh: true,
      dispatchEvent: () => true,
      location: {
        reload() {
          reloads += 1;
        },
      },
    },
  });
  runInNewContext(script, context);

  return { streams, state: observed, timers, reloads: () => reloads, document: rendered };
}

function send(stream: FakeStream, kind: string, data: unknown): void {
  const handler = stream.handlers[kind];
  if (handler === undefined) throw new Error(`skrypt nie słucha zdarzenia ${kind}`);
  handler({ data: JSON.stringify(data) });
}

describe("strumień rozmowy po limicie czasu połączenia", () => {
  it("opens_the_stream_again_after_a_deadline_instead_of_going_quiet", () => {
    const harness = run();
    expect(harness.streams.length).toBe(1);
    send(harness.streams[0]!, "status", { status: "RUNNING" });

    send(harness.streams[0]!, "wznow", { reason: "deadline", last_seq: 10 });

    // To jest dokładnie to, czego audyt nie zobaczył: DRUGIE połączenie, bez czekania na cokolwiek.
    expect(harness.streams.length).toBe(2);
    expect(harness.streams[0]!.closed).toBe(true);
    expect(harness.streams[1]!.closed).toBe(false);
    // Sesja nadal pracuje, więc znacznika nie wolno przestawić na „cicho”.
    expect(harness.state()).toBe("running");
    expect(harness.reloads()).toBe(0);
  });

  it("resumes_from_the_last_sequence_number_it_saw", () => {
    const harness = run("RUNNING", 7);
    expect(harness.streams[0]!.url).toContain("after_seq=7");
    send(harness.streams[0]!, "event", {
      seq: 12,
      kind: "ASSISTANT_MESSAGE",
      payload: { text: "gotowa odpowiedź", message_id: "m1" },
    });

    send(harness.streams[0]!, "wznow", { reason: "deadline", last_seq: 12 });

    expect(harness.streams[1]!.url).toContain("after_seq=12");
  });

  it("an_old_backend_saying_koniec_with_reason_deadline_also_reconnects", () => {
    const harness = run();
    send(harness.streams[0]!, "status", { status: "RUNNING" });

    send(harness.streams[0]!, "koniec", { reason: "deadline", last_seq: 10 });

    expect(harness.streams.length).toBe(2);
    expect(harness.state()).toBe("running");
  });

  it("a_finished_session_still_stops_the_stream_and_the_mark", () => {
    const harness = run();
    send(harness.streams[0]!, "status", { status: "RUNNING" });

    send(harness.streams[0]!, "koniec", { reason: "session-not-running", last_seq: 10 });

    expect(harness.streams.length).toBe(1);
    expect(harness.streams[0]!.closed).toBe(true);
    expect(harness.state()).toBe("idle");
  });

  it("two_real_failures_still_fall_back_to_reloading", () => {
    const harness = run();
    const fail = () => harness.streams[harness.streams.length - 1]!.handlers["error"]!({ data: "" });

    fail();
    // Pierwsza awaria: próba za dwie sekundy, bez odświeżania strony.
    expect(harness.timers.length).toBe(1);
    // Jedna awaria niczego nie ogłasza: sesja nadal pracuje, dopóki wiemy inaczej.
    expect(harness.state()).toBe("running");
    harness.timers[0]!();

    fail();
    expect(harness.state()).toBe("polling");
    harness.timers[harness.timers.length - 1]!();
    expect(harness.reloads()).toBe(1);
  });

  it("a_planned_reconnect_does_not_count_towards_the_failure_budget", () => {
    const harness = run();
    send(harness.streams[0]!, "status", { status: "RUNNING" });
    // Jedna awaria, potem planowane wznowienie, potem znowu awaria. Gdyby licznik nie zerował
    // się na wznowieniu, druga awaria zrzuciłaby ekran w odświeżanie po kwadransie normalnej pracy.
    harness.streams[0]!.handlers["error"]!({ data: "" });
    harness.timers[0]!();
    send(harness.streams[harness.streams.length - 1]!, "wznow", { reason: "deadline" });
    harness.streams[harness.streams.length - 1]!.handlers["error"]!({ data: "" });

    expect(harness.state()).toBe("running");
    expect(harness.reloads()).toBe(0);
  });
});

describe("kształt rozmowy na żywo", () => {
  // Uwaga założyciela z 00:21, 20.09: rozmowa była zalana wierszami „zdarzenie silnika:
  // item/agentMessage/delta", a odpowiedź w nich tonęła. Każdy przypadek niżej to jedna część
  // tamtej skargi.

  it("an_answer_is_one_paragraph_not_a_row_per_frame", () => {
    const harness = run();
    send(harness.streams[0]!, "event", {
      seq: 1,
      kind: "ASSISTANT_MESSAGE",
      payload: { text: "Odpowiedź w całości.", message_id: "m1" },
    });

    const answers = harness.document.querySelectorAll("[data-live-answer]");
    expect(answers.length).toBe(1);
    expect(answers[0]?.textContent).toBe("Odpowiedź w całości.");
  });

  it("the_same_message_arriving_twice_replaces_itself_instead_of_doubling", () => {
    // Zdarza się przy wznowieniu strumienia i przy ponownym czytaniu śladu.
    const harness = run();
    const frame = (text: string) => ({
      seq: 1,
      kind: "ASSISTANT_MESSAGE",
      payload: { text, message_id: "m1" },
    });
    send(harness.streams[0]!, "event", frame("pierwsza wersja"));
    send(harness.streams[0]!, "event", frame("pierwsza wersja, poprawiona"));

    const answers = harness.document.querySelectorAll("[data-live-answer]");
    expect(answers.length).toBe(1);
    expect(answers[0]?.textContent).toBe("pierwsza wersja, poprawiona");
  });

  it("engine_noise_sits_one_level_deeper_and_outside_the_step_count", () => {
    // Uzgodnione z sesją, która trzyma historię: licznik kroków liczy PRACĘ, mechanika ma własne
    // podsumowanie. Jeden licznik na obie rzeczy znaczyłby, że ta sama tura pokazuje na dwóch
    // ekranach dwie różne liczby, a pierwsze pytanie brzmi, która kłamie.
    const harness = run();
    send(harness.streams[0]!, "event", {
      seq: 1,
      kind: "STATUS",
      payload: { level: "engine", summary: "silnik: 312 ramek pominiętych w zapisie" },
    });

    expect(harness.document.querySelector("[data-live-flow]")?.children.length).toBe(0);
    expect(harness.document.querySelectorAll("[data-live-step-list] li").length).toBe(0);
    const noise = harness.document.querySelectorAll("[data-live-engine-list] li");
    expect(noise.length).toBe(1);
    expect(noise[0]?.textContent).toContain("312 ramek");
    expect(harness.document.querySelector("[data-live-step-count]")?.textContent).toBe(
      "Przebieg pracy",
    );
    expect(harness.document.querySelector("[data-live-engine-count]")?.textContent).toBe(
      "Szczegóły techniczne: 1 wpis silnika",
    );
    // Oba bloki przestają być schowane dopiero wtedy, gdy mają co pokazać.
    expect(harness.document.querySelector("[data-live-steps]")?.hasAttribute("hidden")).toBe(false);
    expect(harness.document.querySelector("[data-live-engine]")?.hasAttribute("hidden")).toBe(false);
  });

  it("a_session_state_sentence_is_content_not_a_step", () => {
    // „Sesja nie ma połączenia Codex, na którym mogłaby ruszyć" to odpowiedź produktu do
    // człowieka. Schowana pod zapadką byłaby odpowiedzią ukrytą przed tym, kto na nią czeka.
    const harness = run();
    send(harness.streams[0]!, "event", {
      seq: 1,
      kind: "STATUS",
      payload: {
        status: "WAITING_USER",
        detail: "Sesja nie ma połączenia Codex, na którym mogłaby ruszyć.",
      },
    });

    expect(harness.document.querySelector("[data-live-flow]")?.textContent).toContain(
      "nie ma połączenia Codex",
    );
    expect(harness.document.querySelectorAll("[data-live-step-list] li").length).toBe(0);
  });

  it("tool_events_stay_under_one_quiet_summary_without_a_misleading_step_count", () => {
    const harness = run();
    for (const [index, summary] of ["przeczytał wątek: t3_1w934kh", "narzędzie: gotowe, 1.2 s"].entries()) {
      send(harness.streams[0]!, "event", {
        seq: index + 1,
        kind: index === 0 ? "TOOL_CALL" : "TOOL_RESULT",
        payload: { tool: "sources.read", summary },
      });
    }

    const count = harness.document.querySelector("[data-live-step-count]");
    expect(count?.textContent).toBe("Przebieg pracy");
    expect(harness.document.querySelector("[data-live-flow]")?.children.length).toBe(0);
    // Mechaniki nie było, więc jej blok zostaje schowany i pusty.
    expect(harness.document.querySelectorAll("[data-live-engine-list] li").length).toBe(0);
    expect(harness.document.querySelector("[data-live-engine]")?.hasAttribute("hidden")).toBe(true);
  });

  it("the_typing_marker_shows_and_then_gets_out_of_the_way", () => {
    // Silnik przysyła go raz na wypowiedź, żeby ekran nie milczał przez pół minuty generowania.
    const harness = run();
    const pen = () => harness.document.querySelector("[data-live-typing]");
    expect(pen?.()?.hasAttribute("hidden")).toBe(true);

    send(harness.streams[0]!, "event", {
      seq: 1,
      kind: "STATUS",
      payload: { level: "engine", wenext_pisze: true, summary: "agent pisze odpowiedź" },
    });
    expect(pen()?.hasAttribute("hidden")).toBe(false);
    // I nie zostawia po sobie wiersza: to jest stan, nie zdarzenie.
    expect(harness.document.querySelectorAll("[data-live-step-list] li").length).toBe(0);

    send(harness.streams[0]!, "event", {
      seq: 2,
      kind: "ASSISTANT_MESSAGE",
      payload: { text: "już gotowe", message_id: "m1" },
    });
    expect(pen()?.hasAttribute("hidden")).toBe(true);
  });

  it("what_the_person_said_stays_in_the_conversation", () => {
    const harness = run();
    send(harness.streams[0]!, "event", {
      seq: 1,
      kind: "USER_MESSAGE",
      payload: { text: "nie pisz dłużej niż trzy zdania" },
    });

    const flow = harness.document.querySelector("[data-live-flow]");
    expect(flow?.textContent).toContain("nie pisz dłużej niż trzy zdania");
    expect(harness.document.querySelectorAll("[data-live-step-list] li").length).toBe(0);
  });

  it("a_status_frame_moves_the_mark_and_touches_nothing_else", () => {
    const harness = run();
    send(harness.streams[0]!, "status", { status: "DONE", session_id: "s1" });

    expect(harness.state()).toBe("idle");
    expect(harness.document.querySelector("[data-live-flow]")?.children.length).toBe(0);
    expect(harness.document.querySelectorAll("[data-live-step-list] li").length).toBe(0);
  });
});
