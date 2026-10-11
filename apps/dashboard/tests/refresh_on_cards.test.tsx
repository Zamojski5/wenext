/**
 * Miękkie odświeżenie po zmianie kart (D-046), sprawdzone na PRAWDZIWYM skrypcie strumienia.
 *
 * DWIE RZECZY, KTÓRE MUSZĄ BYĆ PRAWDĄ NARAZ, i tylko razem coś znaczą:
 *
 *   1. zmiana odcisku kart prosi o odświeżenie. Bez tego karta zostaje w starym stanie i
 *      człowiek widzi „Łączę…" nad kontem, które jest już połączone;
 *   2. TA SAMA ramka drugi raz nie robi nic. Odświeżanie przy każdej ramce wyrywałoby stronę
 *      spod czytającego co kilka sekund, a przy wpisywaniu dopowiedzenia gubiłoby tekst.
 *
 * Skrypt jest brany Z KOMPONENTU i uruchamiany w `node:vm` na prawdziwym DOM-ie, a nie
 * przepisywany do testu: przepisany skrypt sprawdza to, co ktoś przepisał, a nie to, co jedzie
 * do przeglądarki (ta sama metoda, co w `tests/live_stream_reconnect.test.tsx`).
 */
import { createContext, runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

import { ConversationLive } from "../components/studio/conversation-live";
import { renderToDocument } from "./render";

interface FakeStream {
  handlers: Record<string, (message: { data: string }) => void>;
}

/**
 * Skrypt Z KOMPONENTU, uruchomiony w kontekście z podstawionym `window` i prawdziwym DOM-em.
 *
 * Ta sama metoda, co w `tests/live_stream_reconnect.test.tsx`, i z tego samego powodu: skrypt
 * przepisany do testu sprawdza to, co ktoś przepisał, a nie to, co jedzie do przeglądarki.
 */
function harness(soft: boolean) {
  const rendered = renderToDocument(
    <ConversationLive
      afterSeq={3}
      cardsDigest="konto:brak_polaczenia"
      projectId="p"
      sessionId="s"
      status="RUNNING"
      workspaceId="w"
    />,
  );
  const script = rendered.querySelector("script")?.textContent ?? "";
  if (script === "") throw new Error("komponent nie wyrenderował skryptu");

  const streams: FakeStream[] = [];
  const asked: string[] = [];
  const timers: (() => void)[] = [];
  let reloads = 0;

  class FakeEventSource {
    handlers: Record<string, (message: { data: string }) => void> = {};
    constructor(public url: string) {
      streams.push(this as unknown as FakeStream);
    }
    addEventListener(kind: string, fn: (message: { data: string }) => void) {
      this.handlers[kind] = fn;
    }
    close() {}
  }

  const context = createContext({
    document: rendered,
    EventSource: FakeEventSource,
    CustomEvent: class {
      constructor(public type: string) {}
    },
    window: {
      setTimeout: (fn: () => void) => {
        timers.push(fn);
        return 0;
      },
      // Znacznik stawia komponent kliencki po zamontowaniu; tutaj ustawiamy go wprost, bo
      // przedmiotem tego testu jest SKRYPT, a nie hydratacja.
      wenextSoftRefresh: soft ? true : undefined,
      dispatchEvent: (event: { type: string }) => asked.push(event.type),
      location: {
        reload() {
          reloads += 1;
        },
      },
    },
  });
  runInNewContext(script, context);
  // Without hydration the script waits its budget (27.09) and then binds anyway: run the clock out.
  for (let tick = 0; streams.length === 0 && tick < 100 && timers.length > 0; tick += 1) {
    (timers.shift() as () => void)();
  }

  return { stream: streams[0] as FakeStream, asked, reloads: () => reloads };
}

function send(stream: FakeStream, kind: string, data: unknown): void {
  const handler = stream.handlers[kind];
  if (handler === undefined) throw new Error(`skrypt nie słucha ramki ${kind}`);
  handler({ data: JSON.stringify(data) });
}

describe("odświeżenie po zmianie kart", () => {
  it("a_changed_digest_asks_for_a_soft_refresh_and_never_reloads", () => {
    const test = harness(true);
    send(test.stream, "karty", { odcisk: "konto:polaczone" });
    expect(test.asked).toEqual(["wenext:karty"]);
    // Pełne przeładowanie zabrałoby tekst wpisany w polu i przejścia CSS z makiety.
    expect(test.reloads()).toBe(0);
  });

  it("the_same_digest_twice_does_nothing_at_all", () => {
    const test = harness(true);
    send(test.stream, "karty", { odcisk: "konto:brak_polaczenia" });
    send(test.stream, "karty", { odcisk: "konto:brak_polaczenia" });
    expect(test.asked).toEqual([]);
    expect(test.reloads()).toBe(0);
  });

  it("a_second_change_asks_again", () => {
    const test = harness(true);
    send(test.stream, "karty", { odcisk: "konto:lacze" });
    send(test.stream, "karty", { odcisk: "konto:polaczone" });
    expect(test.asked.length).toBe(2);
  });

  it("without_hydration_it_falls_back_to_a_reload_instead_of_showing_a_stale_card", () => {
    // Gdy hydratacja się nie udała, lepiej zabrać komuś wpisane zdanie niż pokazywać stan karty
    // sprzed zmiany: pierwsze jest przykrością, drugie jest nieprawdą na ekranie.
    const test = harness(false);
    send(test.stream, "karty", { odcisk: "konto:polaczone" });
    expect(test.asked).toEqual([]);
    expect(test.reloads()).toBe(1);
  });

  it("a_frame_without_a_digest_is_ignored", () => {
    const test = harness(true);
    send(test.stream, "karty", {});
    expect(test.asked).toEqual([]);
    expect(test.reloads()).toBe(0);
  });
});
