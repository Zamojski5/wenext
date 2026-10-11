# ADR-0037: środowisko agentowe realizujące cel

- **Status:** PRZYJĘTA DECYZJA; migracja implementacji do wykonania.
- **Data:** 2026-09-17.
- **Źródło:** bezpośrednia decyzja założyciela, zapisana na jego polecenie po przeglądzie
  architektury. Wpis w dzienniku: [D-037](../DECYZJE.md#d-037--nadrzędny-filar-architektury-środowisko-i-warstwa-agentowa-realizujące-cel).

Produkt i interfejs dla tej warstwy opisuje [WIZJA-WORKSPACE](../produkt/WIZJA-WORKSPACE.md),
D-038 i D-039. Ten ADR jest źródłem szczegółów runtime'u, kontekstu, narzędzi i pamięci.

Od 2026-09-19 rezultaty, kolejność i dowody wdrożenia całego pivotu określa
[ROADMAP-PIVOT](../ROADMAP-PIVOT.md), D-040. [START-PIVOT](../../START-PIVOT.md)
jest krótkim wejściem dla kolejnych wykonawców; ten ADR zachowuje rolę
źródła zasad architektury.

## Najważniejszy filar architektury

**Wenext buduje środowisko, narzędzia i trwałą warstwę agentową, dzięki którym
inteligentny model samodzielnie prowadzi pracę do celu.** Model rozpoznaje sytuację,
ustala, czego potrzebuje, dobiera informacje i narzędzia, podejmuje decyzje,
sprawdza ich skutki i dostosowuje dalsze działania.

To nadrzędna zasada projektowania projektu. Każdą większą zmianę oceniamy przez to,
czy zwiększa zdolność agenta do osiągnięcia celu użytkownika. Dotyczy to także pracy
nad kontekstem, pamięcią, obserwacją, runtime'em, promptami, jakością i ewaluacjami.

Pivot obejmuje sposób działania systemu. Samo uproszczenie promptu, zwiększenie
limitu znaków albo wymiana modelu nie realizuje tej decyzji. D-036 jest pierwszym
krokiem w tym kierunku; D-037 rozszerza go na architekturę całego wykonania.

## Pierwszeństwo i zakres

Wizja Community Presence → Autonomous Organic GTM z
[kierunku produktu](../KIERUNEK-PRODUKTU.md) pozostaje celem biznesowym.
Ten ADR określa nadrzędny sposób jego realizacji i ma pierwszeństwo przed starszymi
planami, promptami i założeniami implementacyjnymi, które zamykają model
w z góry wyznaczonej sekwencji decyzji typu A → B.

Diagramy pętli produktu opisują potrzebne zdolności i sprzężenia zwrotne.
Kolejność rozpoznania, researchu, wyboru rozmowy i przygotowania propozycji może
wynikać z decyzji modelu. Techniczne zależności i warunki wykonania pozostają jawne.

D-039 doprecyzowuje docelowy produkt: autonomiczny SaaS, w którym człowiek
nadaje kierunek, a agent sam prowadzi działania w powierzonym zakresie,
w tym komentarze i publikacje. Delegacja może być trwała i obejmować całą
pracę projektu; techniczna autoryzacja nie oznacza ręcznego zatwierdzania każdej
treści. Przegląd przed publikacją jest opcjonalnym trybem użytkownika.
Izolacja, idempotencja i niezawodność umożliwiają tę autonomię. Faktyczny stan
aktywnych workerów i kont opisuje STAN-PRAC; ten ADR opisuje ich docelową ewolucję.

## Architektura, do której zmierzamy

### Ciągłość usługi i koszt pracy

Agent działa dla projektu także bez otwartego dashboardu. Trwały cel, tryb,
zadania, pamięć i wyniki należą do backendu. Użytkownik przez rozmowę zmienia
kierunek; kolejne sesje uwzględniają tę zmianę zamiast rozpoczynać od pustego wycinka.

Stała obserwacja korzysta z obecnych kolektorów, kolejki i harmonogramu:
pobierania przyrostów, deduplikacji, cache'u źródeł, aktualizacji metryk
i rejestrowania zdarzeń. Nowa odpowiedź, zmiana istotna dla celu, termin zadania
lub wiadomość człowieka mogą wznowić sesję Codexa. Zdarzenia można grupować;
niezmienione źródła nie wymagają nowego pełnego researchu. Uruchomienia mają
budżet, możliwość wznowienia i odstęp po błędzie; limit kosztu daje czytelny stan
oczekiwania zamiast zapętlenia.

Mechaniczne filtrowanie oszczędza odczyty i powtórzenia. Dobór strategii,
ocena sensu rozmowy, research i treść pozostają pracą modelu. Agent ma możliwość
poszerzenia wyszukiwania i sięgnięcia po pełne materiały; tani etap nie może
trwale odcinać wiedzy wyłącznie na podstawie wąskiej heurystyki.

Interfejs otrzymuje zdarzenia z wykonania: obserwuje, analizuje, działa, czeka,
wstrzymany, zakończony rezultat lub problem. Historia pokazuje konkretne skutki
i źródła; status 24/7 nie oznacza nieprzerwanej inferencji. Mierzymy koszt
osiągniętego wyniku, zbędne przebudzenia, czas reakcji, ciągłość po restarcie
i wykorzystanie nowych ustaleń człowieka. Szczegóły trybów są w WIZJA-WORKSPACE.

### Codex jako silnik, workspace jako granica kontekstu

Doprecyzowanie założyciela: silnikiem Wenext pozostaje Codex, z możliwością
podłączania kolejnych kont z subskrypcją przez dashboard. Runtime Codexa,
wybrany model, połączenie uwierzytelniające i konto społecznościowe to odrębne
pojęcia. Zmiana modelu na Astrę nie oznacza zmiany tej decyzji o silniku.

[Dokumentacja OpenAI App Server](https://learn.chatgpt.com/docs/app-server)
opisuje integrację Codexa z własnym produktem, obejmującą uwierzytelnianie,
historię rozmów i zdarzenia agenta. Jest to punkt wyjścia do rozwijania istniejącego
adaptera; zgodność konkretnych metod sprawdzamy na przypiętej wersji runtime'u.

Każde połączenie ma własny stan uwierzytelnienia i wykonania. Utworzenie kolejnego
nie nadpisuje aktywnego. Sesję wiążemy z workspace'em, projektem, celem, wybranym
połączeniem i zakresem narzędzi. Kontekst budujemy z tych obiektów, zachowując
możliwość doczytania wiedzy. Połączenie silnika nie wyznacza dostępu do pamięci
produktu. Trwałe ustalenia i źródła muszą przeżyć ponowne logowanie lub zmianę
połączenia; wznowienie wątku wymaga sprawdzenia zgodności tożsamości i zakresu.

Katalog połączeń, modele, stan autoryzacji i limity są dostępne przez backend.
Poświadczenia pozostają po stronie serwera. Limity odczytujemy dla podłączonego
konta, a budżety zadań rozliczamy w Wenext. Obsługa wielu kont w dashboardzie
wymaga wdrożenia; obecny `observe/wiring.py` buduje runner z jednego
`WENEXT_CODEX_HOME`. Istniejący adapter w `services/codex_runtime` obsługuje
logowanie, odczyt konta, katalog modeli i limity i może zostać wykorzystany.

### Warstwy środowiska

| Warstwa | Odpowiedzialność |
| --- | --- |
| Środowisko wiedzy | Pełne dostępne obserwacje, źródła, profil i preferencje użytkownika, historia rozmów i relacji, poprawki człowieka, wyniki oraz sygnały moderacji. Dane mają pochodzenie, czas i informację o brakach. |
| Narzędzia | Odkrywalny katalog możliwości produktu: dostęp do projektów, źródeł, kont, rozmów, pamięci, zadań, artefaktów, wyników i wykonywania działań. Model dobiera narzędzia do celu; każde ma jasny kontrakt i zakres dostępu. |
| Warstwa agentowa | Sesja pracy nad celem z kryterium ukończenia, budżetem i trwałym stanem. Model dobiera kroki, zbiera kontekst, porównuje możliwości, korzysta z informacji zwrotnej i replanuje. |
| Warstwa wykonania | Kolejka, harmonogram zdarzeń, wznowienia, limity, izolacja, autoryzacja, idempotencja, wykonanie działań objętych delegacją i dowód ich skutku. |
| Pamięć i pomiar | Zapis faktów, decyzji, źródeł, zmian człowieka i obserwowalnych skutków. Te informacje wracają do następnej sesji i do oceny jakości. |

### Cały system jako środowisko pracy modelu

**Doprecyzowanie 20.09:** wykorzystujemy pętlę i przydatne możliwości Codexa,
rozszerzając je usługami Wenext. Obecna konfiguracja ograniczonego pilota nie
wyznacza docelowego katalogu możliwości rozmowy. Każda codzienna operacja
produktu powinna być osiągalna narzędziem na tych samych obiektach co UI;
brak takiej ścieżki jest luką funkcjonalną, nie powodem do odesłania człowieka
do formularza. Dostępne zdolności zależą od rzeczywistego środowiska i wersji
silnika; funkcje desktopu nie są automatycznie funkcjami naszego App Servera.
Diagnozę i propozycję kolejności rozszerzeń zawiera
[research Codex-first](../research/CODEX-FIRST-WENEXT-2026-09-20.md).

Doprecyzowanie założyciela z 2026-09-17: agent ma znać możliwości systemu
i sam sięgać po to, czego potrzebuje do realizacji celu. Powyższe grupy oraz
pierwszy wycinek wdrożenia nie wyznaczają zamkniętej listy narzędzi.
Projektujemy dostęp do możliwości całego produktu w zakresie uprawnień sesji.

Na początku model otrzymuje mapę dostępnych obszarów i sposób odkrywania narzędzi.
Może wyszukać potrzebną zdolność, doczytać jej kontrakt i wywołać ją w tej samej
sesji. Pełne definicje większego katalogu wczytujemy według potrzeb; oszczędzanie
kontekstu nie może ukrywać istnienia dostępnych możliwości. Katalog rozróżnia
narzędzia dostępne, wymagające połączenia lub zgody i jeszcze niewdrożone.
Opis zawiera przeznaczenie, parametry, wynik, zakres i istotne skutki wywołania.

Przykładowe obszary obejmują projekty i ich cele, dokumenty i wyszukiwanie źródeł,
konta i platformy, rozmowy i skrzynki, analitykę, pamięć i wcześniejsze decyzje,
zadania i sesje, tworzenie artefaktów oraz przygotowanie i wykonanie działań.
Model może łączyć narzędzia, ponawiać odczyt, zmienić podejście albo pominąć
zbędne wywołanie. Dobór i kolejność wynikają z celu i uzyskanych informacji;
zależności techniczne opisujemy w kontraktach.

MCP jest możliwym standardowym interfejsem udostępnienia tych zdolności.
Funkcje natywne runtime'u mogą korzystać z tych samych usług. Dashboard i agent
pracują na tych samych obiektach, uprawnieniach i wynikach; nie utrzymujemy
osobnej, uboższej wersji produktu dla modelu. Dobór transportu i obsługę
odkrywania sprawdzamy w używanym runtime'ie podczas implementacji.
Samo podłączenie MCP nie zapewnia pamięci, wznowień ani prowadzenia celu.

Współpraca sesji odbywa się przez dostępne narzędzia: odczyt ustaleń projektu,
przekazanie zadania lub pytania i odbiór wyniku z odwołaniem do źródeł.
Agent dobiera współpracę do potrzeby; nie wymuszamy jej w każdym zadaniu.
Katalog i wyniki narzędzi muszą pozwalać odróżnić działanie wykonane,
oczekujące, niedostępne i zakończone błędem. Zdarzenia mogą wznowić pracę nad celem.

### Kontekst dostępny według potrzeb

Sesja zaczyna od celu, istotnego przeglądu sytuacji i informacji o dostępnych źródłach.
Model może rozszerzyć kontekst, gdy potrzebuje pełnego posta, wcześniejszej gałęzi,
historii kontaktu albo dowodu. Wynik odczytu pokazuje, czy dane są niepełne,
przycięte lub nieaktualne, i udostępnia drogę do dalszego odczytu, jeśli źródło na to pozwala.

Budżet kontekstu służy zarządzaniu pracą. Nie zakładamy, że aplikacja potrafi przed
rozpoczęciem zadania wybrać wszystkie informacje potrzebne modelowi.
Pamięć produktu pozostaje trwała, z odwołaniami do źródeł; kompresja historii sesji
ma zachowywać cel, ustalenia, otwarte kwestie i możliwość ponownego odczytu.

### Zdobywanie wiedzy i ciągłość obecności

Założyciel rozszerzył kierunek o dostęp do internetu, newsletterów i innych źródeł
wiedzy. Agent sam rozpoznaje lukę, dobiera źródło, wyszukuje, otwiera materiał,
podąża za odnośnikami i sprawdza istotne twierdzenia. Ma też móc wrócić do wcześniej
poznanych źródeł. Newslettery mogą trafiać przez publiczne archiwa, RSS, materiały
przekazane przez użytkownika lub udostępnioną skrzynkę. Wybór konkretnych źródeł
i połączeń pozostaje częścią wdrożenia; źródło niedostępne jest jawnym brakiem.

Wiedzę zapisujemy z pochodzeniem i datą, odróżniając treść źródła, wniosek agenta
i hipotezę. Model może odświeżyć, skorygować lub odrzucić wcześniejsze ustalenie.
Materiały zewnętrzne są danymi do analizy, a nie instrukcjami zmieniającymi cel
lub uprawnienia sesji. Przeczytane doświadczenie innej osoby nie staje się
doświadczeniem właściciela konta.

Obecność w społeczności obejmuje powroty do rozmów, odpowiedzi i powiadomienia,
pamięć relacji oraz obserwację losu własnych treści. Agent ma móc poznać nowe
miejsce, sprawdzić jego zasady i historię, ocenić sens udziału, przygotować post
i po działaniu wrócić do reakcji oraz kontynuacji. Wysłanie treści, jej widoczność
dla odbiorców i rezultat rozmowy to odrębne obserwacje. Dobór tych czynności
wynika z sytuacji; nie są obowiązkową sekwencją dla każdego posta.

### Uczenie przez pamięć i sprawdzane doświadczenie

Rozróżniamy trzy zasoby: wiedzę o świecie i branży, pamięć projektu oraz
doświadczenie z własnych decyzji i działań. Pamięć projektu obejmuje cele,
preferencje, ustalenia i relacje. Doświadczenie wiąże sytuację, podjętą decyzję,
użyte źródła, poprawki człowieka i zaobserwowany wynik, także błąd lub milczenie.
Następna sesja ma móc odnaleźć przydatne doświadczenie i zmienić na tej podstawie
decyzję. Sam zapis podsumowania bez późniejszego wykorzystania nie realizuje celu.

Na tym etapie uczenie oznacza rozwój pamięci i sposobu działania systemu.
Zapis notatki nie zmienia wag modelu. Ewentualny trening modelu to osobna decyzja
wymagająca danych i pomiaru. Wnioski z pojedynczych reakcji pozostają hipotezami;
liczba polubień sama nie dowodzi jakości ani przyczyny wyniku. Ustalenia muszą
zachowywać źródła, zakres zastosowania i możliwość korekty.

Poprawę sprawdzamy na kolejnych zadaniach: czy agent wykorzystuje korektę,
unika wcześniej rozpoznanego błędu, aktualizuje nieaktualny fakt i utrzymuje
ciągłość rozmowy. Porównanie pamięci włączonej i wyłączonej na tym samym modelu
i odłożonych zadaniach ma pokazać wpływ pamięci na wynik, przy porównywalnym budżecie.

### Sandbox jako możliwe rozszerzenie

Sandbox jest rozważaną przez założyciela późniejszą możliwością. Może dać agentowi
miejsce na analizę danych, uruchomienie przykładu, sprawdzenie hipotezy lub
przygotowanie wykresu i innego materiału, który wnosi wartość do publikacji.
Wyniki i artefakty powinny wracać do projektu razem z danymi potrzebnymi do ich
sprawdzenia. Zakres plików, sieci i budżetu określa środowisko wykonania.
Ta opcja nie oznacza dostępu do hosta produkcyjnego ani wdrożonego sandboxa;
nie jest też warunkiem rozpoczęcia pracy nad źródłami i pamięcią.

### Sesja prowadząca pracę do wyniku

Jednostką pracy jest cel wraz ze stanem jego realizacji. Model może wykonać kilka
odczytów, zweryfikować hipotezę, poprawić szkic po ocenie albo uzasadnić odroczenie.
Zakończenie wynika z osiągnięcia kryterium ukończenia, uzasadnionej decyzji o milczeniu,
potrzeby informacji lub zgody, albo wyczerpania budżetu.

Restart procesu nie może gubić ustaleń zadania. Zdarzenia, odpowiedzi narzędzi,
stan oczekiwania i wyniki wykonania muszą pozwalać na wznowienie w tym samym zakresie
uprawnień. Trwały stan sesji nie oznacza jednego nieograniczonego wątku dla wszystkich kont.

### Podział decyzji

Model odpowiada za interpretację rozmowy, dobór okazji, strategię udziału,
potrzebę researchu, treść propozycji i sposób jej poprawy. Kod egzekwuje zakres
dostępu, budżety, stan techniczny, zgodę i warunki działania na zewnątrz.

Deterministyczne narzędzia, walidacja danych i strukturalne wyniki są potrzebne.
Reguła, która rozstrzyga za model sens wypowiedzi lub strategię rozmowy, wymaga
osobnego uzasadnienia: jawnej preferencji użytkownika albo dowodu z ewaluacji.
Obowiązujące preferencje stylistyczne opisujemy jako preferencje, a nie jako
techniczne niezmienniki bezpieczeństwa.

## Wskazówki dla kolejnych agentów

- Przy projektowaniu wskaż cel użytkownika, informacje dostępne modelowi,
  decyzje należące do niego i granicę wykonania. Utrzymuj ten podział w kodzie.
- Traktuj obecne pojedyncze wywołania z gotowym pakietem jako stan przejściowy.
  Nie utrwalaj ich jako docelowej architektury przez nowe sztywne etapy i wyjątki.
- Jeżeli modelowi brakuje wiedzy, rozważ narzędzie i dostęp do źródła.
  Jeżeli popełnia błąd, sprawdź kontekst, informację zwrotną i ewaluację przed
  dopisaniem kolejnej reguły sterującej jego sądem.
- Rozwijaj niezawodne środowisko pracy: trwałość, wznowienia, użyteczne narzędzia,
  pamięć i dowody. Liczba agentów i prostota samej pętli wywołań nie są celem.
- Projektuj zdolności przydatne także po zmianie modelu na Astrę. Dostępność modelu
  i zgodność runtime'u weryfikuj przy migracji; ta decyzja nie przełącza modelu.

## Pierwszy wycinek wdrożenia i dowód

Pierwszy wycinek obejmuje sesję dla konkretnej rozmowy: dostęp do źródeł przez narzędzia
odczytu, doczytanie braków, propozycję, informację zwrotną i możliwość poprawy.
Kolejne kroki rozszerzają to na porównywanie rozmów, wznowienia, relacje i uczenie z wyników.
Wykorzystujemy istniejące komponenty tam, gdzie wspierają te zdolności.

Przed pomiarem ustalamy zadania, budżety i kryteria. Porównujemy dotychczasowy przepływ
z nową sesją na tym samym modelu i materiale, a wpływ wymiany modelu mierzymy osobno.
Oceniamy realizację celu, trafność udziału lub milczenia, wierność profilowi,
korzystanie ze źródeł, poprawę po uwagach, ciągłość pracy oraz koszt i czas wyniku.
Same poprawne JSON-y i przejście testów technicznych nie dowodzą jakości tych decyzji.

Pierwszy wycinek powinien sprawdzić również, czy agent sam rozpoznaje brak wiedzy,
dociera do potrzebnego źródła i wykorzystuje jego treść oraz czy późniejsza sesja
potrafi zastosować zapisaną korektę. Internet i newslettery rozszerzają katalog
źródeł; sandbox pozostaje osobną, późniejszą możliwością.

**Stan na przyjęcie decyzji:** przegląd kodu potwierdził świeże tymczasowe wątki,
wejście budowane przez aplikację bez narzędzi doczytywania w ścieżce `observe`
oraz brak pętli poprawy po odrzuceniu szkicu. D-036 uprościła prompty i zdjęła
część reguł. Nowa warstwa agentowa nie została jeszcze wdrożona ani zmierzona.
Ta aktualizacja dotyczy dokumentacji i instrukcji projektu.

**Rozwinięcie z 22.09.2026:** D-048 doprecyzowuje wspólną wiedzę marketingową, własne
hipotezy agenta, badanie publicznych stron i newslettera oraz uczenie się mierzone zmianą
decyzji. Bieżący zakres implementacji, próby na modelu i ograniczenia są w
[audycie AI-first](../AUDYT-AI-FIRST-2026-09-22.md); stan powyżej pozostaje historycznym
punktem przyjęcia decyzji, nie opisem dzisiejszego wdrożenia.

**Potwierdzenie kierunku po pierwszych testach, 22.09.2026:** założyciel podtrzymał
swobodę osądu modelu, kontekst dobierany do zadania i kompletne narzędzia prowadzenia
pracy. Walidatory mają egzekwować granice wykonania; nie zastępować rozumowania
sztywnym scenariuszem. Obawę o szum kontekstu należy zmierzyć, nie traktować jako
już dowiedzioną przyczynę. Zaakceptowane wnioski, kolejność rezultatów i kryteria
ich odbioru są w sekcji „Wnioski zaakceptowane przez założyciela”
[dziennika testu bojowego](../TEST-BOJOWY-5H-2026-09-22.md).
