/**
 * Trasy, które akcja karty może wskazać (D-046).
 *
 * TRASA PRZYCHODZI Z KARTY, W POLU UKRYTYM FORMULARZA, i to jest wygodne dla karty oraz wygodne
 * dla kogoś, kto to pole podmieni. Bez tej listy formularz byłby sposobem na wskazanie dowolnego
 * adresu API i wysłanie tam sesji operatora.
 *
 * LISTA JEST KOMPLETNA I KRÓTKA Z ROZMYSŁU: to zbiór rzeczy, które da się zrobić jednym
 * kliknięciem w rozmowie, a nie lista wszystkiego, co API potrafi. Dopisanie pozycji ma być
 * świadomym ruchem, widocznym w przeglądzie zmian.
 *
 * PIERWSZA WERSJA TEJ REGUŁY BYŁA BŁĘDNA i warto, żeby to zostało zapisane. Brzmiała „ścieżka
 * musi zaczynać się od `/api/workspaces/<id>/`" i odrzucałaby DWIE z trzech tras, których karty
 * naprawdę używają, bo połączenie konta i dołączenie do społeczności są w zakresie organizacji,
 * nie przestrzeni. Reguła strukturalna, która nie pasuje do produktu, jest gorsza od braku
 * reguły: wygląda na zabezpieczenie, a działa jak awaria, i to awaria po kliknięciu.
 *
 * Mieszka w `lib/`, a nie w handlerze, wyłącznie po to, żeby test sprawdzał TĘ funkcję, a nie
 * jej kopię: plik trasy Next może eksportować tylko metody HTTP.
 */
export const CARD_ROUTES: readonly { readonly prefix: string; readonly wPrzestrzeni: boolean }[] = [
  // Połączenie konta: otwarcie sesji kapsuły z ekranem logowania platformy.
  { prefix: "/api/capsule/session", wPrzestrzeni: false },
  // "Gotowe, sprawdź połączenie" (card action `sprawdz`, 26.09): after the person signed in by
  // hand, the control plane measures the platform session and records who is signed in. Scoped to
  // the ORGANIZATION like the open above, because the agent's browser and the platform accounts
  // belong to the organization, not to a workspace: a workspace prefix would refuse every click,
  // which is the mistake the first version of this list made. Listed on its own although the entry
  // above already covers it by prefix, so that this list names every route a card really uses.
  { prefix: "/api/capsule/session/confirm", wPrzestrzeni: false },
  // The same check for X. A route of its own on the backend (`api/x_platform.py`), because an X
  // session is verified against the expected handle, not only "somebody is signed in".
  { prefix: "/api/x/session/confirm", wPrzestrzeni: false },
  // Dołączenie do społeczności. Zakres organizacji, bo społeczność jest wspólna dla przestrzeni.
  { prefix: "/api/communities", wPrzestrzeni: false },
  // Podpis powierzenia. Jedyna trasa kart związana z konkretnym projektem.
  { prefix: "/api/workspaces/", wPrzestrzeni: true },
];

/**
 * Czy karta może wskazać tę ścieżkę z tej przestrzeni.
 *
 * `wPrzestrzeni` dokłada drugi warunek: trasa musi należeć do przestrzeni z formularza, więc
 * karta z jednej przestrzeni nie sięgnie do drugiej nawet przy podmienionym polu.
 */
export function allowedCardRoute(path: string, workspaceId: string): boolean {
  // Prefiks bywa zapisany z ukośnikiem na końcu (rodzina tras) albo bez (jedna trasa), więc
  // granicę segmentu dokładamy tylko wtedy, gdy jej tam nie ma. Bez tego `/api/workspaces/`
  // szukałoby `/api/workspaces//` i nie pasowało do niczego, a delegacja przestałaby działać.
  const entry = CARD_ROUTES.find((one) => {
    const boundary = one.prefix.endsWith("/") ? one.prefix : `${one.prefix}/`;
    return path === one.prefix || path.startsWith(boundary);
  });
  if (entry === undefined) return false;
  if (!entry.wPrzestrzeni) return true;
  return path.startsWith(`/api/workspaces/${encodeURIComponent(workspaceId)}/`);
}

/**
 * The session checks among the routes above, and the platform each one measures.
 *
 * TWO THINGS IN `/studio/card` HANG ON THIS MAP. A check reads the platform (Reddit's twice) and
 * the backend measured up to a minute, so the default five second wait would give up on every
 * click and report "no answer" about a check that went on to succeed; these routes get
 * `CAPSULE_CONFIRM_TIMEOUT_MS` instead. And a check that found the account connected continues the
 * conversation with a sentence naming the platform, taken from here when the card's payload does
 * not name it.
 */
export const SESSION_CHECK_ROUTES: Readonly<Record<string, string>> = {
  "/api/capsule/session/confirm": "REDDIT",
  "/api/x/session/confirm": "X",
};

/** The platform this path checks, or `null` when the path is not a session check. Exact match. */
export function sessionCheckPlatform(path: string): string | null {
  return Object.prototype.hasOwnProperty.call(SESSION_CHECK_ROUTES, path)
    ? (SESSION_CHECK_ROUTES[path] ?? null)
    : null;
}
