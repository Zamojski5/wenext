/**
 * Przebieg pracy agenta na ekranie (D-046, makieta `agent-progress-detail.html`).
 *
 * CO JEST TU MIERZONE, A CZEGO NIE DA SIĘ ZMIERZYĆ OKIEM: że ruch należy do STANU, a nie do
 * zegara. W makiecie animacja jest sterowana czasem, bo makieta nie ma agenta. Tutaj `data-playing`
 * ma być prawdą wtedy i tylko wtedy, gdy jakiś etap naprawdę trwa. Ekran, który miga, gdy nic
 * nie działa, jest tym, czego zabrania ZAKRES-FUNKCJONALNY §5, i wygląda dokładnie tak samo jak
 * ekran, który pracuje.
 */
import { describe, expect, it } from "vitest";

import { AgentProgress } from "../components/studio/progress";
import type { ConversationProgress } from "../lib/cards-schema";
import { renderToDocument } from "./render";

function progress(overrides: Partial<ConversationProgress> = {}): ConversationProgress {
  return {
    naglowek: "Poznaję projekt",
    licznik: { zrobione: 1, wszystkie: 4 },
    pozycje: [
      { etykieta: "Poznaję cel i odbiorców", stan: "zrobione", podlinia: "Czytam ustalenia", zrodla: [] },
      { etykieta: "Szukam rozmów", stan: "trwa", podlinia: null, zrodla: ["r/SaaS", "r/startups"] },
      { etykieta: "Oceniam, gdzie odpowiedzieć", stan: "oczekuje", podlinia: null, zrodla: [] },
    ],
    linia_zdarzenia: "Czytam pytania o pierwszych klientów",
    ...overrides,
  };
}

describe("przebieg pracy", () => {
  it("the_movement_follows_a_real_running_stage_and_nothing_else", () => {
    const running = renderToDocument(<AgentProgress progress={progress()} />);
    expect(running.querySelector(".ap-progress")?.getAttribute("data-playing")).toBe("true");
    expect(running.querySelector(".ap-progress")?.getAttribute("data-finished")).toBe("false");

    const done = renderToDocument(
      <AgentProgress
        progress={progress({
          pozycje: [
            { etykieta: "Poznaję cel", stan: "zrobione", podlinia: null, zrodla: [] },
          ],
        })}
      />,
    );
    // Nic nie trwa, więc nic się nie rusza, choć etapy są.
    expect(done.querySelector(".ap-progress")?.getAttribute("data-playing")).toBe("false");
    expect(done.querySelector(".ap-progress")?.getAttribute("data-finished")).toBe("true");
  });

  it("every_stage_carries_the_state_the_server_gave_it", () => {
    const screen = renderToDocument(<AgentProgress progress={progress()} />);
    const states = Array.from(screen.querySelectorAll(".ap-item")).map((item) =>
      item.getAttribute("data-state"),
    );
    expect(states).toEqual(["done", "running", "pending"]);
  });

  it("the_counter_is_the_number_from_the_server_and_not_a_count_of_rows", () => {
    // Licznik liczy etapy ROZPOCZĘTE w tej turze, a nie wiersze na ekranie. Przeliczanie go
    // tutaj dałoby „3 / 3" tam, gdzie serwer mówi „1 z 4", i po tygodniu nikt by tej liczbie
    // nie wierzył.
    const screen = renderToDocument(<AgentProgress progress={progress()} />);
    expect(screen.querySelector(".ap-count")?.textContent).toBe("1 / 4");
  });

  it("sources_become_chips_and_a_subline_becomes_a_line", () => {
    const screen = renderToDocument(<AgentProgress progress={progress()} />);
    const chips = Array.from(screen.querySelectorAll(".ap-chip")).map((chip) => chip.textContent);
    expect(chips).toEqual(["r/SaaS", "r/startups"]);
    expect(screen.querySelector(".ap-subline")?.textContent).toContain("Czytam ustalenia");
  });

  it("the_footer_says_the_field_stays_open_while_the_agent_works", () => {
    const screen = renderToDocument(<AgentProgress progress={progress({ linia_zdarzenia: null })} />);
    expect(screen.querySelector(".ap-footer")?.textContent).toContain(
      "Możesz pisać dalej, kiedy pracuję",
    );
  });

  it("a_turn_with_nothing_to_show_renders_nothing", () => {
    // `postep: null` z kontraktu. Pusta ramka z licznikiem „0 / 0" wyglądałaby jak praca,
    // która stanęła, a stanem jest „ta tura nie wywołała niczego, co umiemy nazwać".
    const screen = renderToDocument(
      <div>
        <AgentProgress progress={null} />
      </div>,
    );
    expect(screen.querySelector(".ap-progress")).toBeNull();
  });
});
