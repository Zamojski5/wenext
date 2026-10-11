/**
 * Co wolno wskazać karcie, a czego nie (D-046).
 *
 * TRASA PRZYCHODZI Z KARTY, W POLU UKRYTYM FORMULARZA. To jest wygodne dla karty i wygodne dla
 * kogoś, kto to pole podmieni: bez sprawdzenia mielibyśmy formularz, którym da się wskazać
 * dowolny adres API i wysłać tam sesję operatora. Ten plik trzyma listę tego, co karta MOŻE
 * wskazać, i sprawdza ją tą samą funkcją, której używa handler.
 *
 * PIERWSZA WERSJA TEJ REGUŁY BYŁA BŁĘDNA i to jest powód, dla którego ten test istnieje osobno.
 * Brzmiała „ścieżka musi zaczynać się od `/api/workspaces/<id>/`" i odrzucałaby DWIE z trzech
 * tras, których karty naprawdę używają, bo połączenie konta i dołączenie do społeczności są w
 * zakresie organizacji, nie przestrzeni. Reguła strukturalna, która nie pasuje do produktu, jest
 * gorsza od braku reguły: wygląda na zabezpieczenie, a działa jak awaria.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  CARD_ROUTES,
  SESSION_CHECK_ROUTES,
  allowedCardRoute as allowed,
  sessionCheckPlatform,
} from "../lib/card-routes";

const HANDLER = path.resolve(import.meta.dirname, "..", "app", "studio", "card", "route.ts");
const source = fs.readFileSync(HANDLER, "utf8");

const WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001";

describe("trasy dozwolone dla karty", () => {
  it("the_routes_the_cards_really_use_are_allowed", () => {
    // Zmierzone w `tools/registry.py` i `sessions/projection.py`: tyle ich jest i tyle ma być.
    expect(allowed("/api/capsule/session", WORKSPACE)).toBe(true);
    expect(allowed("/api/communities", WORKSPACE)).toBe(true);
    expect(allowed(`/api/workspaces/${WORKSPACE}/projects/p1/delegation`, WORKSPACE)).toBe(true);
    // Zatwierdzanie i poprawka szkicu, też pod ścieżką projektu.
    const approvals = `/api/workspaces/${WORKSPACE}/projects/p1/approvals/rev7`;
    expect(allowed(`${approvals}/approve`, WORKSPACE)).toBe(true);
    expect(allowed(`${approvals}/revise`, WORKSPACE)).toBe(true);
  });

  it("a_workspace_route_of_another_workspace_is_refused", () => {
    // Karta z jednej przestrzeni nie sięga do drugiej, nawet gdy ktoś podmieni pole.
    expect(allowed("/api/workspaces/bbbb/projects/p1/delegation", WORKSPACE)).toBe(false);
  });

  it("anything_outside_the_list_is_refused", () => {
    for (const bad of [
      "/api/session",
      "/api/operators",
      "/api/audit",
      "/api/workspaces",
      "/studio/logout",
      "/",
    ]) {
      expect(allowed(bad, WORKSPACE), bad).toBe(false);
    }
  });

  it("the_handler_refuses_reads_and_path_tricks_before_it_looks_at_the_list", () => {
    // Kolejność ma znaczenie: `..` i `//` odpadają zanim cokolwiek zostanie dopasowane, bo
    // dopasowanie po prefiksie na ścieżce z `..` jest dopasowaniem do czegoś innego, niż się
    // wydaje. Sprawdzane na źródle, bo to jest własność KOLEJNOŚCI, nie wyniku.
    const check = source.indexOf('path.includes("..")');
    const list = source.indexOf("allowedCardRoute(path");
    expect(check).toBeGreaterThan(-1);
    expect(check).toBeLessThan(list);
    // I metody: akcja karty ZMIENIA stan, więc `GET` z tego formularza nie ma sensu.
    expect(source).toContain('const METHODS = ["POST", "PATCH", "DELETE"] as const;');
  });

  it("the_list_has_exactly_the_entries_the_product_needs", () => {
    // Licznik przed zawartością: lista, która po cichu spuchła, przepuszczałaby trasy, których
    // nikt nie przeglądał, a każda asercja wyżej nadal by przechodziła. Five since 26.09: the two
    // session checks behind "Gotowe, sprawdź połączenie" joined the three from D-046.
    expect(CARD_ROUTES.length).toBe(5);
  });

  it("both_session_checks_are_allowed_in_organization_scope", () => {
    // "Gotowe, sprawdź połączenie" on the account card (26.09). Organization scope like the open:
    // the agent's browser and the platform accounts are the organization's, so a workspace
    // prefix here would refuse every click, the exact failure of this rule's first version.
    expect(allowed("/api/capsule/session/confirm", WORKSPACE)).toBe(true);
    expect(allowed("/api/x/session/confirm", WORKSPACE)).toBe(true);
    for (const path of ["/api/capsule/session/confirm", "/api/x/session/confirm"]) {
      const entry = CARD_ROUTES.find((one) => one.prefix === path);
      expect(entry, path).toBeDefined();
      expect(entry?.wPrzestrzeni, path).toBe(false);
    }
    // The READ of the X session is not a card action: it reads the platform on every call and
    // changes nothing, so no card may point at it.
    expect(allowed("/api/x/session", WORKSPACE)).toBe(false);
    expect(allowed("/api/x", WORKSPACE)).toBe(false);
  });

  it("a_session_check_is_recognised_by_exact_path_and_names_its_platform", () => {
    expect(Object.keys(SESSION_CHECK_ROUTES).length).toBe(2);
    expect(sessionCheckPlatform("/api/capsule/session/confirm")).toBe("REDDIT");
    expect(sessionCheckPlatform("/api/x/session/confirm")).toBe("X");
    // Exact, not by prefix: the open itself is not a check and must keep the short wait, and a
    // path with anything after the check is not the check.
    for (const path of ["/api/capsule/session", "/api/x/session", "/api/x/session/confirm/x", "/api/communities"]) {
      expect(sessionCheckPlatform(path), path).toBeNull();
    }
    // Every check is also on the allowlist: a recognised check the handler then refused would be
    // a long wait for a refusal.
    for (const path of Object.keys(SESSION_CHECK_ROUTES)) {
      expect(allowed(path, WORKSPACE), path).toBe(true);
    }
  });

  it("a_session_check_waits_as_long_as_the_operations_confirm_does", () => {
    // The check reads the platform and the backend measured up to a minute; the default five
    // seconds would give up on every click and report "no answer" about a check that succeeded.
    // Read on the source, because it is the CHOICE of the wait that matters here.
    expect(source).toContain("sessionCheckPlatform(path)");
    expect(source).toContain("timeoutMs: CAPSULE_CONFIRM_TIMEOUT_MS");
    // And a check that outlived the wait says so, instead of "unreachable".
    expect(source).toContain('"account_check_pending"');
  });

  it("a_correction_that_saved_but_is_not_visible_says_so_instead_of_saying_done", () => {
    // NOC 21.09: trasa poprawki zapisała nową rewizję i wywaliła się PO zapisie, więc poprawka
    // była w bazie, a w rozmowie nie było po niej śladu. Pasek mówił wtedy „zlecone" — zdanie
    // prawdziwe o wysłaniu i mylące o skutku — a człowiek poprawiał drugi raz to samo. Trasa
    // oddaje dziś `w_rozmowie`, a ekran ma to POWIEDZIEĆ.
    //
    // Sprawdzane na źródle handlera, bo to jest wybór KOMUNIKATU, a nie wynik funkcji: handler
    // jest modułem trasy Next.js i wywołanie go w teście jednostkowym wymagałoby atrapy całego
    // żądania, czyli sprawdzałoby atrapę.
    expect(source).toContain("outcome.value.inConversation === false");
    expect(source).toContain('unseen ? "card_saved_unseen" : "card_done"');
    // I `null` (trasa tego nie mówi) NIE jest traktowane jak `false`: cisza to nie zarzut.
    expect(source).not.toContain("inConversation !== true");
  });

  it("the_payload_from_the_screen_may_only_carry_the_fields_on_a_closed_list", () => {
    // Dziś jedno pole: poprawiona treść szkicu. Lista otwarta znaczyłaby, że formularz może
    // dopisać do ciała żądania cokolwiek, a ciało pochodzi z karty właśnie po to, żeby nie mógł.
    // `text`, bo tak nazywa to API zatwierdzania. Tłumaczenie nazwy w połowie drogi jest
    // miejscem, w którym poprawiony szkic zapisuje się jako pusty.
    expect(source).toContain('const FROM_SCREEN = ["text"] as const;');
  });
});
