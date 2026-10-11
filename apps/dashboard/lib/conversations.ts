/**
 * Jak rozmowa nazywa się na ekranie, co o niej mówi jedna linia i czy w ogóle jest rozmową.
 *
 * JEDNO MIEJSCE, BO INACZEJ NAZWA ROZMOWY ROZJEŻDŻA SIĘ MIĘDZY PASKIEM A NAGŁÓWKIEM. Po D-045
 * projekt jest zbiorem rozmów, więc nazwa rozmowy pojawia się w pasku bocznym, w nagłówku ekranu
 * rozmowy i w sekcji „Ostatnie". Trzy miejsca liczące ją osobno dałyby trzy różne napisy dla
 * jednej rozmowy, a człowiek wziąłby to za trzy rozmowy.
 *
 * TYTUŁ LICZY SERWER, I TYLKO SERWER. `sessions/summary.py::title_of` bierze pierwsze zdanie celu,
 * przycina je i oddaje `null`, gdy z celu nie da się zrobić nazwy (cel pusty, same białe znaki,
 * krótszy niż trzy znaki). Ekran czyta ten wynik i nie liczy niczego równolegle.
 *
 * TU BYŁO RUSZTOWANIE I ZOSTAŁO USUNIĘTE 20.09, po wdrożeniu api z polem `tytul`. Przez jedno
 * wdrożenie `conversationTitle` umiało wyliczyć pierwsze zdanie celu samo, na wypadek starszego
 * obrazu api bez tego pola. Po co ta notatka: żeby nikt nie dopisał tego z powrotem „na wszelki
 * wypadek". Wyliczenie w przeglądarce na `null` liczyłoby z TYCH SAMYCH danych, na których serwer
 * właśnie stwierdził, że nazwy nie ma, więc mogłoby dać tylko to samo nic albo napis inny niż
 * serwerowy, czyli jedną rozmowę pod dwiema nazwami w pasku i w nagłówku.
 */
import { softenDashes } from "./conversation-render";
import type { SessionRow } from "./session-schema";

/** Co widać, gdy z celu nie da się zrobić nazwy. Jedno zdanie produktu, nie napis awaryjny. */
export const UNNAMED_CONVERSATION = "Rozmowa bez nazwy";

/** Nazwa tej rozmowy na ekranie. Zawsze coś zwraca, bo pusty wiersz w pasku jest nie do kliknięcia. */
export function conversationTitle(session: SessionRow): string {
  const title = session.tytul;
  return typeof title === "string" && title.trim() !== "" ? title.trim() : UNNAMED_CONVERSATION;
}

/**
 * Jedno zdanie o tym, czym ta rozmowa się skończyła, albo `null`.
 *
 * `null` znaczy „jeszcze nic nie powiedziała" i ekran ma wtedy MILCZEĆ, a nie pisać „brak
 * wyniku": rozmowa w toku bez wyniku jest normalna, a napis o braku wygląda jak usterka.
 * Pełne `result_summary` zostaje nietknięte i pokazuje je ekran rozmowy; tu jest krótkie.
 */
export function conversationResult(session: SessionRow): string | null {
  // Skracane WYŁĄCZNIE na serwerze (`sessions/summary.py::one_sentence`), z tego samego powodu
  // co tytuł. Pełne `result_summary` jedzie obok i pokazuje je ekran rozmowy.
  const sentence = session.wynik_zdanie;
  // The model's words, softened where they are SHOWN, like every other text of the model on this
  // screen (`softenDashes`): the list of conversations read "Gotowe \u2014 wybrałem..." (27.09).
  return typeof sentence === "string" && sentence.trim() !== "" ? softenDashes(sentence.trim()) : null;
}

/**
 * Czy to rozmowa, a nie tik obecności.
 *
 * Gdy pola `rodzaj` NIE MA, odpowiedź brzmi „tak", i to jest świadomy wybór: filtr po stronie
 * serwera jest w kontrakcie, a do czasu jego wejścia lepiej pokazać za dużo niż ukryć rozmowę,
 * której nie da się odróżnić. Ukrycie byłoby zgadywaniem po statusie albo po celu.
 */
export function isConversation(session: SessionRow): boolean {
  return session.rodzaj !== "PRESENCE";
}

/** Rozmowy projektu do paska: bez tików obecności, najnowsze pierwsze. */
export function conversationsOf(sessions: readonly SessionRow[]): readonly SessionRow[] {
  return sessions.filter(isConversation);
}

/** Statusy, po których do rozmowy się nie wraca (D-045): skończyła się, na dobre albo źle. */
const CLOSED = ["DONE", "FAILED", "BUDGET_EXHAUSTED"] as const;

export function conversationIsOver(session: SessionRow): boolean {
  return CLOSED.some((status) => status === session.status);
}

/**
 * Rozmowy projektu dla PASKA na ekranach, które same ich nie potrzebują.
 *
 * PO CO OSOBNA FUNKCJA. Pasek pokazuje rozmowy pod otwartym projektem i to jest kontekst, a nie
 * treść ekranu: człowiek ma widzieć, gdzie jest, także wtedy, gdy patrzy na Publikacje albo na
 * Wiedzę. Do 21.09 czytała je TYLKO strona projektu, więc na czterech pozostałych ekranach
 * projektu pasek gubił listę rozmów (odbiór 1, różnica nr 5 mówi o dwóch z nich, bo tylko te
 * miał na zrzutach; brakowało ich na wszystkich czterech).
 *
 * `undefined` przy nieudanym odczycie, nie pusta lista: pusta znaczy „ten projekt nie ma rozmów"
 * i tak wygląda na ekranie. Nieudany odczyt ma nie udawać pustego projektu.
 */
export async function projectConversations(
  token: string,
  workspaceId: string,
  projectId: string,
): Promise<readonly SessionRow[] | undefined> {
  const { getSessions } = await import("./sessions-client");
  const sessions = await getSessions(token, workspaceId, projectId, true);
  return sessions.ok ? conversationsOf(sessions.value) : undefined;
}
