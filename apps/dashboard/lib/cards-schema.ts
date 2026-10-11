/**
 * Karty rozmowy i postęp tury, sprawdzane polami przed wejściem na ekran (D-046).
 *
 * KONTRAKT: `docs/produkt/KARTY-ROZMOWY.md`. Karty są PROJEKCJĄ śladu sesji, nie stanem
 * przeglądarki, i przychodzą w całości w każdym odczycie sesji obok `session`, `events`,
 * `last_seq` i `budzet`. Ten plik jest granicą: wszystko, co niżej rysuje karty, ma już
 * pewność, że stan jest ze zbioru zamkniętego, a akcja niesie trasę i ładunek.
 *
 * TRZY RZECZY, KTÓRYCH TU PILNUJEMY, I POWÓD KAŻDEJ:
 *
 *   1. **stan spoza słownika zrywa odczyt.** Karta o nieznanym stanie musiałaby zostać
 *      narysowana jakkolwiek, a „jakkolwiek" przy karcie konta znaczy „Połączono" obok konta,
 *      które nie jest połączone. Kontrakt mówi wprost: sukces tylko po potwierdzeniu.
 *   2. **akcja bez trasy jest odrzucana.** Ekran nie buduje ciała żądania z własnej wiedzy o
 *      produkcie; bierze `trasa` i `payload` z karty. Akcja bez nich byłaby przyciskiem,
 *      który po kliknięciu musiałby coś zgadnąć.
 *   3. **brak pól to brak kart, a nie pusta lista.** Starszy obraz api nie przysyła `karty`
 *      wcale. `undefined` znaczy „ten obraz ich nie liczy" i ekran rysuje samą rozmowę; pusta
 *      tablica znaczy „policzone, nie ma żadnej" i to są dwa różne zdania.
 */
import type { Read } from "./control-plane";

export const CARD_KINDS = [
  "KONTO",
  "SPOLECZNOSC",
  "SZKIC",
  "POWIERZENIE",
  // E1: subskrypcja Codexa, czyli SILNIK, na którym rozmowa w ogóle rusza. Osobny rodzaj od
  // KONTA z rozmysłem: KONTO to profil na platformie społecznościowej, a mylenie tych dwóch
  // jest pierwszą rzeczą, która spotyka nowego użytkownika (audyt UX z 20.09).
  "SILNIK",
] as const;
export type CardKind = (typeof CARD_KINDS)[number];

/** Stany dozwolone dla każdego rodzaju. Osobno, bo `dolaczono` na karcie szkicu nie znaczy nic. */
const STATES: Readonly<Record<CardKind, readonly string[]>> = {
  // `niedostepne` to PAUSED i QUARANTINED z `social_accounts`, i jest osobnym stanem, a nie
  // odmianą braku połączenia (uzgodnione z wenext-84). Powód: `brak_polaczenia` znaczy dokładnie
  // tyle, że kliknięcie pomoże, a tu nie pomoże, bo logowanie niczego nie odblokuje.
  KONTO: ["brak_polaczenia", "lacze", "polaczone", "niedostepne", "blad"],
  // E3: OBSERWACJA I CZŁONKOSTWO TO DWIE RÓŻNE RZECZY. Do 20.09 karta mówiła „Dołączono" po
  // samym włączeniu obserwacji w Wenext, czyli po czynności, która na platformie nie zostawia
  // śladu. Audyt nazwał to kłamstwem etykiety. `obserwuje` kontra `obserwowana` rozstrzyga
  // `last_polled_at`, a nie powodzenie wywołania: kolejka odczytów potrafi stać.
  SPOLECZNOSC: ["propozycja", "obserwuje", "obserwowana", "dolaczam", "dolaczono", "blad"],
  SZKIC: ["szkic", "publikuje", "opublikowano", "blad"],
  POWIERZENIE: ["do_podpisu", "podpisana", "odrzucona", "blad"],
  SILNIK: ["brak_polaczenia", "lacze", "polaczone", "blad"],
};

export interface CardAction {
  readonly nazwa: string;
  readonly trasa: { readonly metoda: string; readonly sciezka: string };
  readonly payload: Record<string, unknown>;
}

export interface ConversationCard {
  readonly karta_id: string;
  readonly rodzaj: CardKind;
  readonly stan: string;
  readonly powod: string | null;
  readonly akcje: readonly CardAction[];
  readonly od_seq: number;
  /** Pola zależne od rodzaju, sprawdzone co do typu, bez zgadywania brakujących. */
  readonly dane: Record<string, unknown>;
}

export const PROGRESS_STATES = ["oczekuje", "trwa", "zrobione", "blad"] as const;
export type ProgressState = (typeof PROGRESS_STATES)[number];

export interface ProgressItem {
  readonly etykieta: string;
  readonly stan: ProgressState;
  readonly podlinia: string | null;
  readonly zrodla: readonly string[];
}

export interface ConversationProgress {
  readonly naglowek: string;
  readonly licznik: { readonly zrobione: number; readonly wszystkie: number };
  readonly pozycje: readonly ProgressItem[];
  readonly linia_zdarzenia: string | null;
}

function failure(reason: string): Read<never> {
  return { ok: false, unauthorized: false, reason };
}

function object(raw: unknown): Record<string, unknown> | null {
  return typeof raw === "object" && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

function text(row: Record<string, unknown>, field: string): string | null {
  const value = row[field];
  return typeof value === "string" ? value : null;
}

function nullableText(row: Record<string, unknown>, field: string): string | null | undefined {
  if (!(field in row)) return undefined;
  const value = row[field];
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

function parseAction(raw: unknown): Read<CardAction> {
  const row = object(raw);
  if (row === null) return failure("akcja karty: nie jest obiektem");
  const nazwa = text(row, "nazwa");
  const trasa = object(row["trasa"]);
  const payload = object(row["payload"]) ?? {};
  const metoda = trasa === null ? null : text(trasa, "metoda");
  const sciezka = trasa === null ? null : text(trasa, "sciezka");
  if (nazwa === null || metoda === null || sciezka === null) {
    return failure(
      "akcja karty bez nazwy albo bez trasy. Przycisk, który po kliknięciu musiałby zgadnąć, " +
        "co wysłać, nie jest przyciskiem, tylko pułapką",
    );
  }
  // Ścieżka MUSI być na tym origin. Karta pochodzi z control plane'u, ale przechodzi przez
  // przeglądarkę, a adres bezwzględny w ładunku karty byłby drogą do wysłania sesji gdzie indziej.
  if (!sciezka.startsWith("/") || sciezka.startsWith("//")) {
    return failure(`akcja karty prowadzi poza ten origin: ${sciezka}`);
  }
  return { ok: true, value: { nazwa, trasa: { metoda, sciezka }, payload } };
}

export function parseCard(raw: unknown): Read<ConversationCard> {
  const row = object(raw);
  if (row === null) return failure("karta: nie jest obiektem");

  const id = text(row, "karta_id");
  const rodzaj = text(row, "rodzaj");
  const stan = text(row, "stan");
  const seq = row["od_seq"];
  const powod = nullableText(row, "powod");

  if (id === null) return failure("karta bez `karta_id`");
  if (rodzaj === null || !CARD_KINDS.some((known) => known === rodzaj)) {
    return failure(`karta o nieznanym rodzaju: ${String(rodzaj)}`);
  }
  const kind = rodzaj as CardKind;
  if (stan === null || !STATES[kind].some((known) => known === stan)) {
    return failure(`karta ${kind} w nieznanym stanie: ${String(stan)}`);
  }
  if (typeof seq !== "number" || !Number.isInteger(seq)) return failure("karta bez `od_seq`");
  if (powod === undefined) return failure("karta bez pola `powod` (ma być tekstem albo null)");
  if (stan === "blad" && (powod === null || powod.trim() === "")) {
    // Błąd bez powodu jest gorszy niż brak karty: człowiek widzi, że nie wyszło, i nie ma z tym
    // co zrobić. Kontrakt wymaga powodu, więc jego brak jest zepsutym odczytem, nie stanem.
    return failure(`karta ${kind} w stanie błędu bez powodu`);
  }

  const rawActions = row["akcje"];
  if (!Array.isArray(rawActions)) return failure("karta bez listy `akcje`");
  const akcje: CardAction[] = [];
  for (const entry of rawActions) {
    const parsed = parseAction(entry);
    if (!parsed.ok) return parsed;
    akcje.push(parsed.value);
  }

  const dane = object(row["dane"]) ?? row;
  return {
    ok: true,
    value: { karta_id: id, rodzaj: kind, stan, powod, akcje, od_seq: seq, dane },
  };
}

export function parseCards(raw: unknown): Read<readonly ConversationCard[]> {
  if (!Array.isArray(raw)) return failure("`karty` nie są listą");
  const cards: ConversationCard[] = [];
  for (const entry of raw) {
    const parsed = parseCard(entry);
    if (!parsed.ok) return parsed;
    cards.push(parsed.value);
  }
  // Kolejność jest z serwera (`od_seq`), a nie z tego, jak lista przyszła: karta, która wskakuje
  // nad poprzednią po odświeżeniu, wygląda jak nowa karta.
  return { ok: true, value: [...cards].sort((a, b) => a.od_seq - b.od_seq) };
}

export function parseProgress(raw: unknown): Read<ConversationProgress | null> {
  if (raw === null || raw === undefined) return { ok: true, value: null };
  const row = object(raw);
  if (row === null) return failure("`postep` nie jest obiektem");
  const naglowek = text(row, "naglowek");
  const licznik = object(row["licznik"]);
  const done = licznik === null ? null : licznik["zrobione"];
  const all = licznik === null ? null : licznik["wszystkie"];
  const pozycje = row["pozycje"];
  const linia = nullableText(row, "linia_zdarzenia");

  if (naglowek === null) return failure("postęp bez nagłówka");
  if (typeof done !== "number" || typeof all !== "number") {
    return failure("postęp bez licznika {zrobione, wszystkie}");
  }
  if (!Array.isArray(pozycje)) return failure("postęp bez listy pozycji");
  if (linia === undefined) return failure("postęp bez pola `linia_zdarzenia`");

  const items: ProgressItem[] = [];
  for (const entry of pozycje) {
    const item = object(entry);
    if (item === null) return failure("pozycja postępu nie jest obiektem");
    const etykieta = text(item, "etykieta");
    const stan = text(item, "stan");
    const podlinia = nullableText(item, "podlinia");
    const zrodla = item["zrodla"];
    if (etykieta === null) return failure("pozycja postępu bez etykiety");
    if (stan === null || !PROGRESS_STATES.some((known) => known === stan)) {
      return failure(`pozycja postępu w nieznanym stanie: ${String(stan)}`);
    }
    if (!Array.isArray(zrodla) || zrodla.some((one) => typeof one !== "string")) {
      return failure("pozycja postępu bez listy źródeł");
    }
    items.push({
      etykieta,
      stan: stan as ProgressState,
      podlinia: podlinia ?? null,
      zrodla: zrodla as readonly string[],
    });
  }

  return {
    ok: true,
    value: {
      naglowek,
      licznik: { zrobione: done, wszystkie: all },
      pozycje: items,
      linia_zdarzenia: linia,
    },
  };
}

/** Pole karty jako tekst, albo `null`. Karta nie twierdzi tego, czego nie dostała. */
export function cardText(card: ConversationCard, field: string): string | null {
  const value = card.dane[field];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function cardFlag(card: ConversationCard, field: string): boolean {
  return card.dane[field] === true;
}

export function cardList(card: ConversationCard, field: string): readonly string[] {
  const value = card.dane[field];
  return Array.isArray(value) ? value.filter((one): one is string => typeof one === "string") : [];
}

/** Akcja o tej nazwie, albo `null`. Brak akcji znaczy „nie ma co kliknąć", nie „wyszarz". */
export function actionOf(card: ConversationCard, name: string): CardAction | null {
  return card.akcje.find((action) => action.nazwa === name) ?? null;
}
