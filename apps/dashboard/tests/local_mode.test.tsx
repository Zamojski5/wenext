/**
 * Tryb lokalny w studiu: wejście bez hasła na tej maszynie i nigdzie indziej.
 *
 * CZTERY DROGI, KTÓRE TEN TEST ZAMYKA:
 *   * przełącznik wyłączony, a ekran i tak wpuszcza (domyślnie ma nie wpuszczać);
 *   * przeglądarka przyszła po domenie albo po adresie w sieci, a ekran traktuje to jak maszynę
 *     lokalną (nagłówek `Host` jest jedynym sygnałem, którego nie da się ustawić zmienną);
 *   * pasek „logowanie wyłączone" nie pojawia się, więc człowiek nie wie, że tak jest;
 *   * wylogowanie wraca prosto w tryb auto, czyli przycisk nic nie robi.
 */
import { describe, expect, it } from "vitest";

import { LOCAL_MODE_BANNER, hostIsLoopback, localModeWho } from "../lib/local-access";

describe("tryb lokalny", () => {
  it("is_off_when_nobody_set_the_switch", () => {
    expect(localModeWho({})).toBe("");
    expect(localModeWho({ WENEXT_LOCAL_AUTOLOGIN: "   " })).toBe("");
    expect(localModeWho({ WENEXT_LOCAL_AUTOLOGIN: "wlasciciel@firma.test" })).toBe(
      "wlasciciel@firma.test",
    );
  });

  it.each([
    ["127.0.0.1", true],
    ["127.0.0.1:3000", true],
    ["localhost", true],
    ["localhost:3000", true],
    ["[::1]:3000", true],
    ["wenext.example", false],
    ["wenext.example:3000", false],
    ["192.168.1.14:3000", false],
    ["127.0.0.1.evil.test", false],
    ["", false],
  ])("decides %s is local: %s", (host, expected) => {
    expect(hostIsLoopback(host)).toBe(expected);
  });

  it("treats_a_missing_host_header_as_not_local", () => {
    // Brak nagłówka to brak dowodu, a nie dowód na korzyść.
    expect(hostIsLoopback(null)).toBe(false);
  });

  it("says_the_same_sentence_everywhere", () => {
    // Pasek na ekranie logowania i pasek w studiu biorą tekst z jednej stałej, żeby nie
    // rozjechały się na dwa zdania mówiące co innego o tym samym.
    expect(LOCAL_MODE_BANNER).toContain("Logowanie wyłączone");
    expect(LOCAL_MODE_BANNER).toContain("tryb lokalny");
  });

  it("the_logout_route_stops_the_automatic_entry_for_one_visit", async () => {
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync(`${process.cwd()}/app/logout/route.ts`, "utf8"),
    );
    // Bez tego parametru ekran logowania natychmiast otworzyłby sesję z powrotem.
    expect(source).toContain("wylogowano=1");
  });

  it("the_local_entry_route_checks_both_the_switch_and_the_host", async () => {
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync(`${process.cwd()}/app/login/local/route.ts`, "utf8"),
    );
    expect(source).toContain("localModeWho()");
    expect(source).toContain("hostIsLoopback(request.headers.get(\"host\"))");
    // Konto wybiera control plane po swojej zmiennej: tutaj nie ma i nie może być nazwy konta.
    expect(source).not.toContain("email");
  });
});

it("preserves a conversation after login and rejects external redirect variants", async () => {
  const { loginNext } = await import("../lib/login-next");
  expect(loginNext("/w/a/p/b/c/c?info=ready")).toBe("/w/a/p/b/c/c?info=ready");
  for (const value of ["https://evil.test", "//evil.test", "/\\evil.test", "/\nevil", undefined]) {
    expect(loginNext(value)).toBe("/");
  }
});
