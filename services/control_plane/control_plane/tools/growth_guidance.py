"""Reusable domain knowledge, deliberately separate from private project memory.

These are curated decision aids, not instructions overriding the founder. They contain
no customer examples, source-book excerpts or universal numeric success thresholds.
Provenance stays available for inspection without making every answer a book report.
"""

VERSION = "2026-09-27.2"
GUIDES = {
    "stage-and-discovery": {
        "title": "Etap produktu i najbliższa niewiadoma",
        "when": "Nowy projekt, plan dotarcia, brak odpowiedzi lub niejasny popyt.",
        "body": """Najpierw rozpoznaj, co rzeczywiście istnieje i co już wiadomo o popycie.
Pomysł, działający prototyp, powracający użytkownicy i powtarzalna sprzedaż wymagają innych
działań. Etap jest wnioskiem z dowodów, nie etykietą przyznawaną po jednym komplementującym
komentarzu. Nie obiecuj dema, instalacji ani rezultatów, których produkt nie ma.

Przed produktem szukaj ostatnich konkretnych przypadków problemu: kto go miał, w jakiej
sytuacji, czym się ratował, jaki poniósł koszt i co już próbował zmienić. Deklaracja
„używałbym” jest słabsza od opisu zachowania, poświęconego czasu lub zobowiązania do pilotażu.
Wczesny produkt sprawdzaj przez osiągnięcie pierwszej wartości i powrót do użycia. Przy
rosnącej sprzedaży oceniaj powtarzalność segmentu, retencję i koszt pozyskania.

Zacznij od dostępnej pamięci. Gdy brakuje informacji zmieniającej decyzję, zadaj jedno
konkretne pytanie, nie ankietę. Równolegle możesz przygotować hipotezę i niezależny research.
Nie pytaj ponownie o fakty już ustalone. Zaproponuj najbliższy wynik uczenia się, mały sposób
sprawdzenia i sygnał zmiany kierunku. Rozmowa, analiza istniejących zachowań lub ręczna usługa
mogą dać go szybciej niż budowa funkcji. Ograniczenia foundera mają pierwszeństwo.

Rozróżniaj: brak obserwacji, brak odpowiedzi, odpowiedź bez dopasowania i dowód braku problemu.
Nie zamieniaj ciszy w dowód, że produkt jest niepotrzebny, ani w pretekst do większej liczby
postów. Najpierw sprawdź odbiorcę, dostarczenie, obietnicę i koszt oczekiwanej odpowiedzi.""",
        "basis": (
            "Synteza autorska na podstawie The Five, Tomasz Karwatka, strony PDF 169-180; "
            "rozszerzenie o rozróżnienie dowodu i hipotezy."
        ),
    },
    "audience-and-adoption": {
        "title": "Persona robocza i gotowość do zmiany",
        "when": "Dobór segmentu, rozmówców, obietnicy lub przejście poza pierwszych entuzjastów.",
        "body": """Persona ma pomagać wybrać rozmowę, nie ozdabiać prezentację. Opisz rolę, zadanie,
sytuację uruchamiającą potrzebę, bolesny problem, obecne obejście, kryterium sukcesu i bariery
zmiany. W B2B odróżnij użytkownika, osobę polecającą i kupującego, jeśli to zmienia dotarcie.
Nie dopisuj wieku, budżetu czy cech psychologicznych bez podstaw. Zaznacz, co pochodzi od
foundera lub ze źródeł, a co jest roboczym przypuszczeniem. Zawężenie segmentu jest hipotezą,
którą aktualizujesz po rozmowach, nie nieodwracalnym wyborem użytkownika.

Innowator może chcieć eksperymentować z technologią. Wczesny odbiorca szuka istotnej przewagi
i może zaakceptować niedojrzałość. Pragmatyczny kupujący oczekuje dowodu w podobnej organizacji,
łatwego wdrożenia, wsparcia i ograniczenia ryzyka. To perspektywa oceny sytuacji, nie sztywne
typy ludzi. Sama obecność na X albo Reddicie nie dowodzi, do której grupy ktoś należy.

Dopasuj zaproszenie i dowód: rozmowa o ostatnim problemie przy niezwalidowanym pomyśle;
wspólna próba rozwiązania przy realnej potrzebie i gotowości do eksperymentu; wiarygodny
przypadek użycia i droga wdrożenia przy ostrożnym odbiorcy. Nie przenoś entuzjazmu developerów
na popyt przedsiębiorstw bez sprawdzenia. Nie traktuj teoretycznych proporcji grup jako
pomiaru rynku tego projektu. Wybierz mały segment z dostępnymi rozmówcami i możliwością
weryfikacji, a nie największą abstrakcyjną grupę docelową.

Design partner potrzebuje rzeczywistego problemu, czasu na współpracę oraz pasującego
kierunku produktu. Zainteresowanie nowością samo nie wystarcza. Szukaj także dowodów, które
podważą naszą personę; po zmianie pokaż, jaki sygnał wpłynął na nową decyzję.""",
        "basis": (
            "Synteza autorska: The Five, Tomasz Karwatka, strony PDF 169-180 i 182-187; "
            "omawiany tam model adopcji Geoffrey'a Moore'a. Nie pomiar konkretnego rynku."
        ),
    },
    "channel-strategy": {
        "title": "Rola kanału i jakość wejścia w rozmowę",
        "when": "Wybór społeczności i kanałów, przygotowanie treści, ocena słabego odzewu.",
        "body": """Wybieraj miejsce po obecności właściwych osób, ich aktualnych problemach i
dostępności sensownego wkładu. Liczba członków jest tylko tłem. Najpierw przeczytaj aktualne
rozmowy i zasady miejsca. Poniższe różnice są hipotezami do sprawdzenia w konkretnej niszy,
nie aktualną wiedzą o algorytmach platform.

Reddit: konkretna społeczność, problem i kontekst wątku często znaczą więcej niż profil
autora. Wkład powinien być użyteczny sam w sobie. Sprawdź lokalne reguły promocji, ton i
reakcje; nie doklejaj produktu do każdej odpowiedzi. X: rozmowy i sieci osób mogą pomóc
dotrzeć do wczesnych odbiorców, budować ciągłość i pokazywać pracę. Oceniaj jakość rozmów
i powroty, nie tylko wyświetlenia. Na X liczy się świeżość: odpowiedź po godzinach od
posta ma inną szansę niż po dniach. Nowe konto bez obserwujących jest mało widoczne; rozmowa
pod postem praktyka z niszy daje częściej odpowiedź niż komentarz pod postem wielkiego konta.
Krótko i konkretnie, jedna myśl, bez hasztagów i linków, których nikt nie prosił. Seria
podobnych odpowiedzi wygląda na automat i platforma może ją ograniczyć; kilka wartościowych
rozmów dziennie jest górną granicą, nie celem. Własny post na etapie pomysłu to raczej
pytanie do budujących niż zapowiedź produktu. LinkedIn: znaczenie ma rola zawodowa, kontekst firmy
i wiarygodność doświadczenia; język problemu biznesowego bywa ważniejszy niż nowość
techniczna. Dopasuj dowód i propozycję do roli, nie kopiuj posta między platformami.

Dostępność wykonania sprawdź w narzędziach. X działa przez przeglądarkę agenta na
połączonym koncie projektu (x.search, x.read, accounts.status); LinkedIn nie ma integracji.
Możesz badać publiczne materiały i przygotować propozycję, ale nie twierdzić, że opublikowałeś
lub obserwujesz konto bez wyniku narzędzia. Brak konta nie blokuje researchu.

Mały zespół potrzebuje skupienia: jeden uzasadniony punkt wejścia często jest lepszy od
planowania obecności wszędzie. Wybierz kanał główny i argument, który mógłby zmienić wybór.
Przy braku produktu celem może być rozmowa problemowa, przy prototypie pierwsza wartość,
przy działającym produkcie odpowiedni pilotaż lub aktywacja. Nie myl komentarzy i obserwujących
z klientami. Newslettery i archiwa pomagają odkrywać źródła, ale wiek linku oraz editorialny
opis nie dowodzą aktualności ani dopasowania do odbiorcy.

Relacja powinna wnosić wartość bez ukrywania związku z produktem. Nie fabrykuj osobistych
doświadczeń, rekomendacji, wyników ani zainteresowania. Jeśli nie ma dobrego powodu, żeby się
odezwać, zachowaj obserwację i poczekaj. Natarczywość nie naprawia złego segmentu.""",
        "basis": (
            "Synteza autorska inspirowana The Five, Tomasz Karwatka, strony PDF 273-283; "
            "role platform są hipotezami operacyjnymi wymagającymi bieżącego researchu."
        ),
    },
    "community-access": {
        "title": "Droga do ważnej społeczności, gdy konto nie może jeszcze pisać",
        "when": "Najlepiej dopasowana społeczność odrzuca treść lub ogranicza nowe konto.",
        "body": """Zachowaj najlepiej dopasowaną społeczność jako cel. Najpierw ustal, co wiadomo:
aktualne reguły, komunikat platformy lub moderatora, publiczną widoczność konkretnej treści,
stan konta i daty odczytów. Ograniczenie wieku, karmy, karmy w danej społeczności, weryfikacji
konta, zakaz tematu i zwykła awaria to różne hipotezy. Bez dowodu nie wpisuj żadnej jako
przyczyny i nie sprawdzaj domniemanego progu serią podobnych publikacji.

Jeśli przeszkodą wydaje się niedojrzałość konta, zaplanuj uczciwe uczestnictwo w pokrewnych
społecznościach, gdzie konto może pisać i gdzie naprawdę ma coś przydatnego do dodania.
Wybieraj rozmowy po problemie i wartości odpowiedzi, nie po łatwości zdobycia punktów.
Odpowiedzi mają być użyteczne bez wzmianki o produkcie; nie wymyślaj osobistych doświadczeń,
nie pisz komentarzy-wypełniaczy i nie proś o głosy. Karma i rozpoznawalność mogą być skutkiem
dobrego uczestnictwa, nie zadaniem do mechanicznego nabicia.

Zapisz plan przy projekcie: społeczność docelowa, rozpoznana przeszkoda i niepewność, miejsca
sensownego uczestnictwa, dlaczego tam można pomóc, dowody widoczności i reakcji oraz sygnał
powrotu do celu. Sprawdzaj po zmianie stanu konta lub nowej informacji o regułach. Jeżeli
moderatorzy zabraniają danego tematu lub promocji, dojrzalsze konto nie zmieni tej reguły;
przemyśl sposób dotarcia bez obchodzenia decyzji. Brak trafnej rozmowy oznacza czekanie.

Research i plan mogą powstać teraz. Pisanie w nowych społecznościach musi mieścić się w
podpisanym zakresie konta, społeczności i działania; jeśli nie, zaproponuj zmianę zakresu
w rozmowie. Po jej powierzeniu prowadź dozwoloną pracę bez akceptacji każdego komentarza.""",
        "basis": (
            "Reddit Help: Poster Eligibility Guide & Post Check, Full automoderator "
            "documentation, Disrupting Communities (odczyt 23.09.2026); "
            "synteza strategii produktu Wenext, nie stwierdzenie przyczyny konkretnego usunięcia."
        ),
    },
    "experiments-and-learning": {
        "title": "Eksperyment, wynik i powrót do pracy",
        "when": "Plan działania, feedback, wyniki kampanii, decyzja czy działać czy poczekać.",
        "body": """Plan ma wskazywać niewiadomą, odbiorcę, małe działanie, oczekiwany sygnał oraz
warunek oceny. Dobierz horyzont do kanału i możliwości foundera. Nie wymyślaj naukowej
pewności z kilku odpowiedzi. Liczby mogą być roboczym kryterium decyzji; nazwij je tak.
Koszt, czas i ryzyko reputacji są częścią decyzji. Duża liczba czynności nie jest sukcesem.

W pamięci oddzielaj obserwację od interpretacji. Dla doświadczenia zapisz sytuację, decyzję
i faktyczny wynik wraz z datą, zakresem próby i źródłem, kiedy jest dostępne. Plan bez wyniku
nie jest doświadczeniem. Wniosek roboczy możesz zapisać jako INTERPRETATION z payload:
working_hypothesis=true, evidence, uncertainty, next_check. Nie przedstawiaj go jako zgody
foundera. Jego potwierdzone wybory mają osobne pochodzenie; nie zmieniaj ich w jego imieniu.

Po feedbacku zmień konkretny element następnej decyzji: segment, pytanie, obietnicę, dowód,
moment albo kanał. Skoryguj własne nieaktualne ustalenie zamiast dopisywać sprzeczne notatki.
W nowej rozmowie sięgnij do tego doświadczenia. Uczenie się oznacza wykorzystaną korektę,
a nie sam zapis. Nie zmieniasz wag modelu i nie przenosisz prywatnych wyników między projektami.

Gdy następny sensowny krok zależy od odpowiedzi lub zdarzenia, zapisz czego oczekujesz,
termin sprawdzenia i co wtedy zmieni decyzję. Jeśli dostępne narzędzie pozwala, ustaw powrót;
jeżeli nie, powiedz uczciwie, że powrót nie został uruchomiony. Nie obiecuj czuwania bez
zapisanego zadania. Nie generuj postów tylko po to, by wyglądać na aktywnego. Tanie zbieranie
sygnałów może pracować stale; ponowne rozumowanie uruchamiaj po nowym materiale lub terminie.

Wynik pracy pokaż krótko w rozmowie. Pełny plan lub materiał zachowaj przez materials.save
z linkiem. Obserwujące osoby są wskaźnikiem pomocniczym; sukces mierz stosownie do etapu:
trafnymi rozmowami, dowodami problemu, pierwszą wartością, powrotem, pilotażem lub sprzedażą.""",
        "basis": (
            "Synteza autorska: The Five, Tomasz Karwatka, strony PDF 169-180, 271-281, "
            "oraz architektura pamięci i wykonania Wenext. "
            "Nie sztywny proces ani gwarancja rezultatu."
        ),
    },
    "x-posts": {
        "title": "Post na X, który ktoś przeczyta: nauka na najlepszych postach niszy",
        "when": (
            "Przed własnym postem albo odpowiedzią na X, przy budowaniu marki osobistej "
            "i po poście, który przeszedł bez reakcji."
        ),
        "body": """Zanim napiszesz, sprawdź, co w tej niszy działa teraz, zamiast zgadywać.
x.search w trybie top przyjmuje operatory X: temat z filtrem zaangażowania
(min_faves:300, min_replies:20), lang:en, -filter:replies, from:konto. x.read czyta profil
razem z licznikami. Przejrzyj 20 do 30 postów z ostatnich 7 dni i kilka kont, które szybko
rosną w tej niszy. Przy każdym poście zanotuj: pierwsze zdanie i czym zatrzymuje, długość,
formę (jedno zdanie, lista, wątek, obraz), temat, ton, czy niesie własne doświadczenie,
liczbę albo tezę, z którą można się nie zgodzić, oraz liczniki. Wzorce zapisz jako materiał
z datą. To hipoteza o tej niszy w tym tygodniu, nie prawo algorytmu.

Marka osobista to autor, nie produkt. Pisz jako człowiek, do którego należy konto: jego
doświadczenie, obserwacja z pracy, teza. Pierwsze zdanie ma mieć sens samo, bez kodu
i żargonu. Jedna myśl na post, konkret zamiast ogólnika: liczba, przykład, porażka. Produkt,
którego nikt nie zna, nie jest tematem posta; najwyżej tłem doświadczenia.

Po 24 godzinach porównaj wynik z linią bazową konta i z postami, na których się wzorowałeś,
zapisz, co zadziałało, i zmień następny post. Nowe konto z kilkoma obserwującymi zbiera
kilka wyświetleń na godzinę; to punkt wyjścia, nie porażka treści.

Nie kopiuj cudzych postów ani zdań. Nie wymyślaj doświadczeń ani wyników, których autor nie
ma. Bez próśb o obserwowanie, hasztagów i przynęt na zaangażowanie. Zasady automatyzacji X
zabraniają automatycznych odpowiedzi pod postami znalezionymi wyszukiwaniem i wymagają zgody
X na bota odpowiedzi AI; bezpieczniejsza droga odpowiedzi pod cudzym postem to szkic do
akceptacji człowieka.""",
        "basis": (
            "Metoda robocza Wenext z 27.09.2026: nauka na bieżących postach niszy i pomiar "
            "po 24 h. Zasady: https://help.x.com/en/rules-and-policies/x-automation "
            "(kwiecień 2026). Nie pomiar algorytmu X ani gwarancja zasięgu."
        ),
    },
}
