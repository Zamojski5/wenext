/**
 * The closed set of sentences a studio write may leave behind in `?info=`.
 *
 * A CLOSED SET, not free text in the query string. Two reasons, and the second one is why it is a
 * module of its own: a message carried in the URL is a message anybody can put there, so it must
 * be a KEY the server looks up, never a string the server prints; and the tone has to be decided
 * next to the sentence, because the difference between "zrobione", "nie zrobiono" and "nie wiemy,
 * czy zrobiono" is the whole point of these messages.
 *
 * `unreachable` is deliberately not phrased as a failure of the write. The dashboard stops
 * waiting after `REQUEST_TIMEOUT_MS`; the control plane may well have finished. Telling the user
 * "nie zapisano" would be a claim about something nobody measured.
 */
export type InfoTone = "plain" | "absent" | "danger";

export interface InfoMessage {
  readonly text: string;
  readonly tone: InfoTone;
}

export const STUDIO_INFO: Readonly<Record<string, InfoMessage>> = {
  workspace_created: { text: "Nowy workspace jest gotowy.", tone: "plain" },
  project_created: { text: "Projekt założony.", tone: "plain" },
  project_updated: { text: "Zmiany w projekcie zapisane.", tone: "plain" },
  profile_created: { text: "Profil dodany.", tone: "plain" },
  account_added: { text: "Konto platformy dodane.", tone: "plain" },
  account_attached: { text: "Konto przypięte do profilu.", tone: "plain" },
  account_detached: {
    text: "Konto odpięte od profilu. Historia przypięcia zostaje.",
    tone: "plain",
  },
  // 0022: zapis głosu zawsze tworzy nową wersję, i zdanie mówi to wprost. „Głos zapisany"
  // sugerowałoby nadpisanie poprzedniego, a poprzedni został i to jest cała zaleta tej tabeli.
  voice_saved: {
    text: "Nowa wersja głosu zapisana. Poprzednia zostaje w historii profilu.",
    tone: "plain",
  },
  // P4 i P5: korekta po powrocie. Zdanie mówi, co NA PEWNO się stało (poszła do sesji), i nie
  // obiecuje, że agent ją zastosuje: to zobaczysz w widoku powrotu następnym razem.
  correction_sent: {
    text: "Korekta poszła do sesji. Czy zadziałała, zobaczysz tutaj po następnej turze.",
    tone: "plain",
  },
  // 0023: odwołanie terminu, na który agent sam się umówił. Ton spokojny, bo nic się nie
  // popsuło, ale zdanie mówi wprost, czego odwołanie NIE robi: nie kasuje sprawy i nie
  // wstrzymuje agenta.
  return_cancelled: {
    text:
      "Umówiony powrót odwołany. Agent pracuje dalej, po prostu nie wróci o tej porze; " +
      "wiersz zostaje w historii.",
    tone: "plain",
  },
  account_assigned: { text: "Konto przypisane do projektu.", tone: "plain" },
  account_unassigned: {
    text: "Konto zdjęte z projektu. Historia przypisania zostaje.",
    tone: "plain",
  },
  // P2: powierzenie celu i trzy rzeczy, które człowiek robi z trwającą pracą. Osobne klucze,
  // bo „wysłano” i „przerwano” to różne zdania, a ton przerwania nie jest tonem sukcesu.
  session_started: {
    text: "Rozmowa rozpoczęta.",
    tone: "plain",
  },
  session_message: { text: "Wiadomość dopisana do rozmowy.", tone: "plain" },
  // D-046: przycisk na karcie. Zdanie mówi „zlecone", a NIE „zrobione", bo skutek potwierdza
  // dopiero stan karty po odczycie. „Połączono" i „Dołączono" pisze karta, nie ten pasek.
  card_done: {
    text: "Zlecone. Karta w rozmowie pokaże wynik, gdy przyjdzie potwierdzenie.",
    tone: "plain",
  },
  card_refused: {
    text: "Nie udało się wykonać działania z karty. Nie uznaję go za wykonane; możesz kontynuować rozmowę.",
    tone: "absent",
  },
  // 26.09, connecting an account from its card. "Połącz X" opens the agent's browser in a NEW TAB,
  // so the two `capsule_open_*` sentences land in that tab, on the conversation. Their keys are
  // the ones `lib/capsule-flow.ts::openDecision` returns, spelled the same on purpose: the handler
  // passes the key through instead of translating it, and a test pins that every key it can
  // return has a sentence here.
  capsule_open_refused: {
    text:
      "Nie udało się otworzyć przeglądarki agenta, więc logowanie nie ruszyło. Spróbuj za " +
      "chwilę, a jeśli to się powtórzy, napisz o tym w rozmowie.",
    tone: "absent",
  },
  capsule_open_blocked: {
    text:
      "Przeglądarka agenta podała adres spoza skonfigurowanego, więc panel celowo go nie " +
      "otworzył. To ustawienie środowiska, nie coś, co naprawi kliknięcie.",
    tone: "danger",
  },
  // "Gotowe, sprawdź połączenie". Four outcomes and none may impersonate another: connected is
  // the control plane's measurement, not the click; a check that found nobody signed in is not a
  // failure of the check; and a check that outlived our wait may still have succeeded.
  account_connected: {
    text: "Konto połączone. Agent dostał o tym wiadomość i kontynuuje pracę.",
    tone: "plain",
  },
  account_connected_unsent: {
    text:
      "Konto połączone, ale wiadomość do agenta nie doszła. Napisz w rozmowie, żeby " +
      "kontynuował.",
    tone: "absent",
  },
  // 27.09: "Gotowe" closes the agent's browser window before it reads the session (a person
  // signs in in a browser with no DevTools endpoint, so nothing can read it while it is open).
  // "Finish in the window" would send the person to a window that no longer exists.
  account_not_connected: {
    text:
      "Sprawdzone: konto nie jest jeszcze połączone, a okno logowania jest już zamknięte. " +
      "Kliknij przycisk „Połącz” na karcie konta, zaloguj się w oknie przeglądarki agenta " +
      "i dopiero wtedy kliknij Gotowe.",
    tone: "absent",
  },
  account_check_pending: {
    text:
      "Sprawdzanie połączenia trwa dłużej niż zwykle i mogło się już udać. Nie loguj się " +
      "drugi raz: odśwież rozmowę za chwilę, karta konta pokaże wynik.",
    tone: "absent",
  },
  /**
   * ZAPISANE, ALE NIEWIDOCZNE. Jedyny komunikat, który mówi o RÓŻNICY między tym, co produkt
   * zrobił, a tym, co człowiek zobaczy, i powstał z konkretnego przebiegu (noc 21.09): trasa
   * poprawki zapisała nową rewizję i wywaliła się PO zapisie, więc poprawka istniała w bazie, a
   * w rozmowie nie było po niej śladu. Ekran mówił wtedy „zlecone", czyli zdanie prawdziwe o
   * wysłaniu i mylące o skutku, a człowiek poprawiał drugi raz to samo.
   */
  card_saved_unseen: {
    text:
      "Zapisane, ale jeszcze tego nie widać w rozmowie. Nie poprawiaj drugi raz: " +
      "odśwież za chwilę, a jeśli nadal nie widać, powiedz o tym w rozmowie.",
    tone: "absent",
  },
  session_interrupted: {
    text: "Poproszono o przerwanie. Czy tura naprawdę stanęła, powie jej wynik.",
    tone: "absent",
  },
  session_resumed: { text: "Sesja wznowiona na tym samym wątku.", tone: "plain" },
  // 0026: rozmowa po limicie ma wyjście, a nie ekran bez wyjścia.
  session_continued: {
    text: "Nowa rozmowa ruszyła od tego samego celu; poprzednia zostaje w historii.",
    tone: "plain",
  },
  // P3: materiały i ustalenia. „Dodane" nie znaczy „przeczytane" i ten klucz tego nie sugeruje:
  // control plane odpowiada 201 także wtedy, gdy adresu nie dało się odczytać, a powód czeka
  // przy materiale. Zdanie, które mówiłoby „pobrano", byłoby twierdzeniem, którego nikt nie
  // zmierzył.
  knowledge_added: {
    text: "Materiał jest na liście. Jeśli adresu nie dało się przeczytać, powód stoi przy nim.",
    tone: "plain",
  },
  knowledge_refresh_queued: {
    text: "Odczyt kanału zlecony. Wynik pojawi się przy materiale po wykonaniu.",
    tone: "plain",
  },
  knowledge_searched: {
    text: "Szukanie zapisane jako materiał. Przy nim widać, która wyszukiwarka odpowiedziała.",
    tone: "plain",
  },
  knowledge_item_written: { text: "Ustalenie dopisane.", tone: "plain" },
  knowledge_item_corrected: {
    text: "Poprawka zapisana. Stare ustalenie zostaje w historii ze wskazaniem na nowe.",
    tone: "plain",
  },
  // P5: korekta człowieka pod publikacją i prośba o pomiar wyników. „Zapisana" mówi dokładnie
  // tyle, ile się stało: ustalenie jest w pamięci projektu. Czy agent naprawdę zachowa się
  // inaczej, pokaże następna sesja, więc tego zdania tu nie ma.
  knowledge_feedback: {
    text: "Korekta zapisana przy publikacji. Następna sesja przeczyta ją, zanim coś napisze.",
    tone: "plain",
  },
  knowledge_measure_queued: {
    text: "Pomiar wyników zlecony. Doświadczenia pojawią się w Materiałach po wykonaniu.",
    tone: "plain",
  },
  knowledge_retracted: {
    text: "Ustalenie wycofane. Wiersz i powód zostają.",
    tone: "absent",
  },
  // P7: przestrzeń i dostęp do niej. „Zaproszenie utworzone" nie mówi „wysłane", bo nic nie
  // wysyłamy: link jest na ekranie i to człowiek go przekazuje.
  member_invited: {
    text: "Zaproszenie utworzone. Link jest niżej, pokazujemy go raz i nie wysyłamy mailem.",
    tone: "plain",
  },
  member_revoked: {
    text: "Dostęp odebrany. Ta osoba przestaje widzieć przestrzeń od następnego żądania.",
    tone: "absent",
  },
  email_saved: { text: "Adres zapisany. Od następnego razu zaloguj się nim.", tone: "plain" },
  export_requested: {
    text: "Eksport zlecony. Plik pojawi się na tej liście, gdy kolejka go przygotuje.",
    tone: "plain",
  },
  organization_deleted: {
    text:
      "Usuwanie przestrzeni rozpoczęte: dostęp i poświadczenia już odcięte, dane znikną po " +
      "karencji.",
    tone: "danger",
  },
  codex_adopted: {
    text: "Połączenie z konfiguracji przyjęte. Nic nie zapisano w jego katalogu.",
    tone: "plain",
  },
  codex_created: {
    text:
      "Nowe połączenie utworzone. Logowanie device-code trwa: kod pojawi się przy tym " +
      "połączeniu, gdy app-server go wyda.",
    tone: "plain",
  },
  codex_refreshed: {
    text: "Odczyt stanu połączenia zlecony. Wynik pojawi się przy nim po wykonaniu.",
    tone: "plain",
  },
  codex_login_started: {
    text: "Logowanie uruchomione ponownie. Poprzedni kod przestaje działać.",
    tone: "plain",
  },
  codex_disconnected: {
    text: "Połączenie odłączone. Katalog, auth.json i historia zostają; konto nie zostało wylogowane.",
    tone: "plain",
  },
  // D-055: "Pokój". Cztery odmowy mają cztery różne lekarstwa, więc cztery zdania, a nie jedno
  // „nie udało się". Żadne z nich nie mówi „opublikowano": wpis, który poszedł do publikatora,
  // jest widoczny dopiero po odczycie zwrotnym i to ekran Wyników powie, kiedy.
  room_published: {
    text:
      "Poszło do publikatora. „Opublikowano” napiszemy dopiero wtedy, gdy wpis będzie " +
      "widoczny na platformie.",
    tone: "plain",
  },
  room_revised: {
    text:
      "Poprawka zapisana jako nowa wersja szkicu. Poprzednia zostaje w historii, a ta nadal " +
      "czeka na Twoją zgodę.",
    tone: "plain",
  },
  room_dismissed: {
    text: "Pominięte. Znajdziesz je na dole listy, w sekcji Pominięte.",
    tone: "plain",
  },
  room_scope_unsigned: {
    text:
      "Nic nie opublikowano: ten projekt nie ma podpisanego zakresu. Podpisz go na stronie " +
      "Obecność w trybie „Z przeglądem”, potem wróć tutaj i kliknij Opublikuj jeszcze raz.",
    tone: "absent",
  },
  room_budget_spent: {
    text:
      "Nic nie opublikowano: limit publikacji z podpisanego zakresu jest na dziś wyczerpany. " +
      "Spróbuj później albo zmień limity na stronie Obecność.",
    tone: "absent",
  },
  room_window_passed: {
    text:
      "Nic nie opublikowano: okno zgody tego szkicu minęło. Poproś agenta w Codexie o nową " +
      "wersję.",
    tone: "absent",
  },
  room_draft_stale: {
    text:
      "Ten szkic ma już nowszą wersję albo został zatwierdzony. Odśwież Pokój i zdecyduj o " +
      "aktualnej.",
    tone: "absent",
  },
  room_revise_unchanged: {
    text:
      "Poprawka nie zmienia treści, więc nic nie zapisano. Zmień tekst w oknie Popraw albo " +
      "kliknij Opublikuj, jeśli szkic jest dobry.",
    tone: "absent",
  },
  room_refused: {
    text:
      "Nie udało się wykonać tej akcji. Odśwież Pokój i sprawdź, co się zmieniło, zanim " +
      "spróbujesz jeszcze raz.",
    tone: "absent",
  },
  refused: {
    text:
      "Control plane odmówił zapisu. Najczęstszy powód: konto jest już przypięte do innego " +
      "profilu albo nazwa jest pusta.",
    tone: "danger",
  },
  unreachable: {
    text:
      "Nie ma odpowiedzi z control plane. Zapis mógł się wykonać po tamtej stronie, więc " +
      "sprawdź stan przed powtórzeniem.",
    tone: "absent",
  },
} as const;

/** The message for this key, or `null` when the key is not one this product writes. */
export function infoFor(key: string | undefined): InfoMessage | null {
  if (key === undefined) return null;
  return Object.prototype.hasOwnProperty.call(STUDIO_INFO, key) ? STUDIO_INFO[key] ?? null : null;
}
