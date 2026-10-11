"use client";

/**
 * JEDYNY komponent kliencki w tym pakiecie, i ma dokładnie jedno zadanie: poprosić Next o
 * ponowne narysowanie strony, gdy karty rozmowy zmieniły stan (D-046).
 *
 * DLACZEGO WYJĄTEK OD REGUŁY „ŻADNEGO «use client»". Reguła stoi w `tests/source_scan.test.ts`
 * i jej powodem jest D-016: sesja operatora i adres prywatnego API nie mają prawa trafić do
 * paczki przeglądarki. Ten plik nie przyjmuje ani jednego, ani drugiego: nie ma propsów, nie
 * czyta `process.env`, nie woła `fetch` i nie zna żadnej ścieżki. Skaner pilnuje tego osobno,
 * więc powód reguły obowiązuje dalej, a wyjątek jest jeden i wymieniony z nazwy.
 *
 * DLACZEGO NIE `location.reload()`, które było tu pierwsze. Pełne przeładowanie w czasie, gdy
 * człowiek pisze dopowiedzenie, ZABIERA MU TEKST, kasuje zaznaczenie i pozycję przewinięcia, a
 * przejścia CSS po zmianie `data-state` („Łączę…" na „Połączono") nigdy się nie odtwarzają, bo
 * nowy stan jest stanem początkowym świeżego dokumentu. `router.refresh()` rysuje stronę NA
 * SERWERZE i różnicuje DOM w miejscu, więc tekst, fokus i przewinięcie zostają, a animacje
 * dzieją się naprawdę.
 *
 * ZASADA „ODŚWIEŻENIE DAJE TO SAMO" NIE ZMIENIA SIĘ ANI O SŁOWO. O tym, co widać, nadal
 * decyduje wyłącznie serwer; to jest to samo rysowanie, tylko bez wyrzucania dokumentu.
 */
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Nazwa zdarzenia, którym skrypt strumienia woła o odświeżenie. Jedna, w jednym miejscu. */
export const CARDS_CHANGED = "wenext:karty";

/** Znacznik dla skryptu: „jest komu odświeżyć". Bez niego skrypt wraca do przeładowania. */
export const SOFT_REFRESH_FLAG = "wenextSoftRefresh";

export function RefreshOnCards() {
  const router = useRouter();

  useEffect(() => {
    const window_ = window as unknown as Record<string, unknown>;
    // Znacznik stawiamy PO zamontowaniu i zdejmujemy przy odmontowaniu: skrypt ma wrócić do
    // przeładowania, gdy hydratacja się nie udała, zamiast zostawić karty w starym stanie.
    window_[SOFT_REFRESH_FLAG] = true;
    const refresh = () => router.refresh();
    window.addEventListener(CARDS_CHANGED, refresh);
    return () => {
      window.removeEventListener(CARDS_CHANGED, refresh);
      delete window_[SOFT_REFRESH_FLAG];
    };
  }, [router]);

  return null;
}
