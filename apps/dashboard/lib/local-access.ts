/**
 * Tryb lokalny po stronie studia: wejście bez hasła na maszynie, na której produkt stoi.
 *
 * DWA WARUNKI, OBA SPRAWDZANE PRZY KAŻDYM ŻĄDANIU, nigdy raz przy starcie:
 *
 *   1. `WENEXT_LOCAL_AUTOLOGIN` jest ustawione (domyślnie nie jest, więc domyślnie nic się nie
 *      zmienia i logowanie działa jak dotąd);
 *   2. przeglądarka przyszła po adresie z pętli zwrotnej. To jest ten warunek, którego nie da
 *      się obejść zmienną: żądanie z domeny albo z adresu w sieci ma inny nagłówek `Host`, więc
 *      nawet włączony przełącznik na wystawionej maszynie nie otworzy sesji.
 *
 * Control plane sprawdza to jeszcze raz po swojej stronie (`identity/local_access.py`) i dokłada
 * warunek o braku skonfigurowanych drzwi z internetu. Dwa niezależne sprawdzenia, bo jedno z nich
 * zawsze da się kiedyś ominąć zmianą, o której druga strona nie wie.
 */

/** Adres albo nazwa organizacji właściciela, albo pusty łańcuch, gdy tryb jest wyłączony. */
export function localModeWho(env: Record<string, string | undefined> = process.env): string {
  return (env["WENEXT_LOCAL_AUTOLOGIN"] ?? "").trim();
}

/**
 * Czy `Host` żądania wskazuje tę samą maszynę.
 *
 * Nazwa hosta inna niż `localhost` jest NIE, także wtedy, gdy w czyimś `/etc/hosts` wskazuje na
 * pętlę zwrotną: tego z serwera nie widać, a zgadywanie tutaj to wpuszczenie kogoś bez hasła.
 */
export function hostIsLoopback(host: string | null): boolean {
  if (host === null) return false;
  const bare = host.trim().toLowerCase();
  if (bare === "") return false;
  // `[::1]:3000` albo `127.0.0.1:3000` albo `localhost`. Port odcinamy dopiero po adresie IPv6,
  // bo w nim też są dwukropki.
  const withoutPort = bare.startsWith("[")
    ? (bare.split("]")[0] ?? "").slice(1)
    : (bare.split(":")[0] ?? "");
  return withoutPort === "127.0.0.1" || withoutPort === "localhost" || withoutPort === "::1";
}

/** Zdanie na pasku w studiu. Jedno miejsce, bo ma brzmieć tak samo wszędzie. */
export const LOCAL_MODE_BANNER = "Logowanie wyłączone: tryb lokalny na tej maszynie.";
