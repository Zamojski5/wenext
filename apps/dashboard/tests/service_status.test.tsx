/**
 * Pasek stanu usługi: kiedy się pokazuje i czego nigdy nie mówi (P7 etap B).
 *
 * DWIE ZASADY, OBIE ŁATWE DO ZŁAMANIA:
 *
 *   1. NIEUDANY ODCZYT NIE JEST ZAPEWNIENIEM. `null` z klienta znaczy „nie wiem", a ekran, który
 *      w tej sytuacji napisałby „usługa działa", zamieniłby niewiedzę w gwarancję. Pasek wtedy
 *      po prostu znika.
 *   2. ZDANIE POCHODZI Z BACKENDU. Ten moduł go nie składa i nie poprawia. Dwa miejsca liczące
 *      „czy usługa działa" rozjechałyby się, a to jest zdanie, przy którym rozjechanie się
 *      kosztuje najwięcej: ktoś patrzy na zielony pasek, gdy nic nie chodzi.
 */
import { describe, expect, it } from "vitest";

import { parseServiceStatus } from "../lib/presence-schema";

describe("stan usługi", () => {
  it("a_healthy_service_parses_with_its_sentence", () => {
    const status = parseServiceStatus({ stan: "usługa działa", dziala: true, niedostepne: [] });
    expect(status).not.toBeNull();
    expect(status?.healthy).toBe(true);
    expect(status?.sentence).toBe("usługa działa");
    expect(status?.incidents).toEqual([]);
  });

  it("an_outage_carries_the_component_and_since_when", () => {
    const status = parseServiceStatus({
      stan: "część niedostępna: CAPSULE od 2026-09-19T10:00:00+00:00",
      dziala: false,
      niedostepne: [
        {
          komponent: "CAPSULE",
          od: "2026-09-19T10:00:00+00:00",
          powod: "Reddit odpowiedział 403",
        },
      ],
    });
    expect(status?.healthy).toBe(false);
    expect(status?.incidents[0]?.component).toBe("CAPSULE");
    expect(status?.incidents[0]?.reason).toBe("Reddit odpowiedział 403");
  });

  it("a_body_without_a_sentence_is_not_a_status", () => {
    // Bez zdania nie ma czego pokazać, a wymyślenie go tutaj byłoby drugim miejscem,
    // w którym powstaje odpowiedź na „czy usługa działa".
    expect(parseServiceStatus({ dziala: true })).toBeNull();
    expect(parseServiceStatus({ stan: "" })).toBeNull();
    expect(parseServiceStatus(null)).toBeNull();
    expect(parseServiceStatus("usługa działa")).toBeNull();
  });

  it("dziala_is_only_true_when_the_backend_says_true", () => {
    // Nie „cokolwiek prawdziwego": brakujące pole ma znaczyć „nie działa", bo to jest
    // bezpieczniejsza strona pomyłki.
    expect(parseServiceStatus({ stan: "coś", dziala: "tak" })?.healthy).toBe(false);
    expect(parseServiceStatus({ stan: "coś" })?.healthy).toBe(false);
  });

  it("a_malformed_incident_row_does_not_lose_the_others", () => {
    const status = parseServiceStatus({
      stan: "część niedostępna: CAPSULE, COLLECTOR",
      dziala: false,
      niedostepne: [null, { komponent: "COLLECTOR", od: "kiedyś", powod: "cisza" }],
    });
    expect(status?.incidents).toHaveLength(1);
    expect(status?.incidents[0]?.component).toBe("COLLECTOR");
  });
});
