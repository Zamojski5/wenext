/**
 * Społeczności przestrzeni pokazane tym samym językiem, co karta w rozmowie (E2).
 *
 * PO CO PROJEKCJA, SKORO JEST JUŻ `ResultsBlock`. Bo to są dwa różne zdania o tej samej tabeli.
 * Blok rezultatów mówi „tyle wątków, ostatni odczyt wtedy" i jest sprawozdaniem. Karta mówi
 * „obserwuję" albo „mogę zacząć obserwować" i jest miejscem, w którym człowiek coś robi. Audyt
 * UX (§3) nazwał rozjazd tych dwóch języków wprost: to samo pojęcie miało na dwóch ekranach dwie
 * różne etykiety, a jedna z nich obiecywała skutek, którego produkt nie osiągał.
 *
 * REGUŁA STANU JEST PRZEPISANA Z SERWERA, nie wymyślona tutaj:
 * `control_plane/sessions/projection.py::refresh_states` (gałąź `CARD_COMMUNITY`). Trzy stany
 * i dokładnie ta sama granica między nimi:
 *
 *   * `observe_enabled = false` albo brak wiersza  → `propozycja`, z akcją „obserwuj";
 *   * obserwowana, ale `last_polled_at IS NULL`    → `obserwuje` („włączyłem", jeszcze nic nie
 *     przeczytałem). Ta różnica jest widoczna dla człowieka i dlatego jest osobnym stanem;
 *   * obserwowana i już czytana                    → `obserwowana`.
 *
 * TO JEST DRUGA KOPIA TEJ REGUŁY W DRZEWIE I TAK MA NIE ZOSTAĆ. Właściwym rozwiązaniem jest
 * odczyt, który zwraca karty gotowe (tak jak robi to sesja); dopóki go nie ma, kopia jest tutaj,
 * z testem, który porównuje ją z zachowaniem serwera opisanym wyżej. Gdyby się rozjechały,
 * człowiek zobaczyłby „obserwuję" na ekranie społeczności i „propozycja" na karcie w rozmowie,
 * o tej samej społeczności, w tej samej minucie.
 *
 * AKCJA JEST PRZEPISANA RAZEM ZE STANEM, z `projection.py::_observe_action`: `POST
 * /api/communities` z `{platform, community_key}`. Ekran nie wymyśla trasy — bierze tę, którą
 * produkt już dziś daje karcie w rozmowie, i wysyła ją tą samą drogą (`/studio/card`).
 * „Dołącz kontem" nie stoi obok z tego samego powodu, co tam: nie ma wykonawcy.
 */
import type { ConversationCard } from "./cards-schema";
import type { ResultCommunity } from "./workspace-schema";

/**
 * Zdanie z `control_plane/tools/cards.py::OBSERVATION_MEANS`, słowo w słowo.
 *
 * Jest na karcie dlatego, że „obserwowana" samo z siebie nie mówi, czy produkt gdzieś zapisał
 * konto człowieka, a to jest pierwsze pytanie, jakie ma prawo zadać po tym, jak przez jakiś czas
 * odpowiadaliśmy na nie źle.
 */
export const OBSERVATION_MEANS =
  "Czytam tu publiczne wątki. Nie zapisuję tam Twojego konta i nikt w tej społeczności nie " +
  "widzi, że ją obserwujemy.";

/** Trasa akcji „obserwuj". Jedna stała, żeby test miał co porównać z serwerem. */
export const OBSERVE_ROUTE = { metoda: "POST", sciezka: "/api/communities" } as const;

export function communityCard(community: ResultCommunity): ConversationCard {
  const observing = community.observe_enabled;
  const readAlready = community.last_polled_at !== null;
  const stan = !observing ? "propozycja" : readAlready ? "obserwowana" : "obserwuje";
  return {
    // Identyfikator wiersza, nie wymyślony numer: po nim odsyłacz z rozmowy trafia w tę samą
    // kartę, a `data-card` w zrzucie da się związać z bazą.
    karta_id: community.community_id,
    rodzaj: "SPOLECZNOSC",
    stan,
    powod: null,
    akcje: observing
      ? []
      : [
          {
            nazwa: "obserwuj",
            trasa: { ...OBSERVE_ROUTE },
            payload: { platform: community.platform, community_key: community.community_key },
          },
        ],
    // Ekran nie jest śladem sesji, więc kolejności z tury tu nie ma. Zero dla każdej karty, a
    // porządek listy ustala ekran (najpierw te, które czekają na decyzję).
    od_seq: 0,
    dane: {
      klucz: community.community_key,
      platforma: community.platform,
      // `nazwa` to opis z rejestru społeczności. `uzasadnienie` świadomie NIE JEST wypełnione:
      // zdanie „po co tam wchodzić" pisze agent w rozmowie, a ekran nie ma go skąd wziąć i nie
      // będzie go układał z liczby wątków.
      nazwa: community.display_name,
      obserwacja: observing,
      ...(observing ? { co_to_znaczy: OBSERVATION_MEANS } : {}),
    },
  };
}

/**
 * Karty w kolejności, w jakiej człowiek ich potrzebuje: najpierw te, które czekają na decyzję.
 *
 * Nie alfabetycznie i nie po dacie. Ekran ma jedno zadanie — pokazać, gdzie jesteśmy i co da się
 * z tym zrobić — więc społeczność, która czeka na włączenie obserwacji, stoi wyżej niż ta, która
 * od tygodnia działa i nic od nikogo nie potrzebuje.
 */
export function communityCards(communities: readonly ResultCommunity[]): ConversationCard[] {
  const cards = communities.map(communityCard);
  return [
    ...cards.filter((card) => card.stan === "propozycja"),
    ...cards.filter((card) => card.stan !== "propozycja"),
  ];
}
