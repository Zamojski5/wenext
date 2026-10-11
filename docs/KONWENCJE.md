# Konwencje pracy w tym repozytorium

Krótki plik. Wszystko tu wynika z konkretnego błędu, który już raz kosztował czas —
nie z upodobań.

Egzekwowane przez `tools/dev/check_docs.py` i `tests/test_repo_consistency.py`.

---

## Kod

**Kod w całości po angielsku** — identyfikatory, komentarze, docstringi i nazwy testów.
Decyzja: **D-010**, uchyla podział z D-006.

Komentarze nadal cytują `ROADMAP_CODEX_CDP_COMMUNITY_OS.md` **z numerami linii** i te
kotwice (`L2129`, `L5152-5183`, `sekcja 13.3`) zostają dosłownie — one, a nie język,
wiążą kod z dokumentem.

**Po polsku zostają cztery rzeczy i żadna z nich nie jest kodem:**

1. klucze w JSON-ie artefaktów i nazwy kontroli bramek — lista `FORBIDDEN_NAMES`;
2. pola dataclass, które `asdict()` wypisuje do artefaktów (`UnattendedRun`, `RunEvent`);
3. komunikaty dla operatora, komunikaty błędów i teksty raportu — czyta je założyciel,
   a część testów dopasowuje je przez `pytest.raises(match=...)`;
4. dokumenty `.md` i `tools/gonogo/criteria.yaml` (zamrożony).

**Zmiana nazw wyłącznie narzędziem `tools/dev/rename_identifiers.py`.** Zamienia tokeny
nazw, nigdy stringów ani komentarzy. Ma listę `FORBIDDEN_NAMES` — nazwy będące jednocześnie
kluczami w danych (identyfikatory kontroli bramek, klucze artefaktów JSON). Ich zmiana
rozspaja raport od dowodów.

**Po zmianie nazw sprawdź `parametrize`.** Pytest dopasowuje argumenty po nazwie
**ze stringa**, więc `("stan", "wynik")` nie zmieni się razem z sygnaturą. Skaner:
`tests/test_repo_consistency.py::test_parametrize_argnames_match_the_function_signature`.
Przez pierwsze miesiące tego skanera **nie było**, mimo że ten akapit twierdził inaczej —
i migracja D-010 rozbiła się dokładnie o to, w dziewięciu miejscach.

**Czego tokenizacja nie widzi w ogóle** — po każdej zmianie nazw sprawdź ręcznie:
argumenty `parametrize`, nazwy atrybutów w `hasattr(...)`, pozycje w `__all__`
(test: `test_every_all_entry_is_actually_bound`), nazwy w backtickach w komentarzach
oraz — piąta pozycja, dopisana 2026-08-30 — **kod Pythona w stringach Dockerfile'i
i compose'a**. Migracja D-010 zostawiła tam dwa martwe odwołania:
`services/mcp_sidecar/Dockerfile` (kontrakt obrazu importował `NAZWY_NARZEDZI`,
`NARZEDZIA_ZAKAZANE` — build padał) i `services/codex_runtime/Dockerfile`
(`CMD` sięgał po `codex_runtime.DOZWOLONE_METODY` — kontener padał przy starcie).
`make test-all` był przy tym zielony, bo testy jadą na hoście i **nie wykonują ani
jednego Dockerfile'a**. Sprawdzaj każde `python -c`, `python -m`, `HEALTHCHECK`,
`ENTRYPOINT` i `CMD`.

**Zmiana nazw wyłącznie przez narzędzie, ale skan kolizji rób osobno i poprawnie.**
`rename_identifiers.py` sprawdza tylko `FORBIDDEN_NAMES` — kolizje są poza nim.
Skan kolizji musi patrzeć **wyłącznie na przestrzeń nazw lokalnych** (definicje,
argumenty, przypisania). Liczenie `ast.Attribute` jako kolizji daje fałszywe alarmy:
lokalne `value` nie koliduje z `enum.value`, `parts` z `path.parts`, `read` z `sock.read()`.
Przy migracji z 2026-08-30 surowy skan dał 16 kolizji, z czego prawdziwe były 3.

**Uwaga do CLI narzędzia:** flaga musi iść **przed** mapą —
`rename_identifiers.py --apply mapa.json ścieżka…`. Kolejność z docstringa
(`mapa.json --apply ścieżka…`) kończy się `unrecognized arguments`.

**Komentuj „dlaczego", nie „co".** Kod mówi, co robi. Komentarz ma mówić, czego nie
widać: który wymóg dokumentu to realizuje, jaki błąd tu już był, czego nie wolno zmienić.

---

## Zanim napiszesz kod korzystający z cudzej klasy

**Przeczytaj jej definicję.** `grep -n "class X" -A 15`.

W sesji 2026-08-30 czterokrotnie napisano kod pod zgadniętą sygnaturę:
`CodexAccountAdapter(codex_home=)` (przyjmuje `client: AppServerClient`),
`ThreadRegistry(client=)` (wymaga pięciu argumentów), `binding_for`
(jest `binding_fingerprint`), `active_principal_generation` (wtedy nazywało się
`aktywna_principal_generation`; po D-010 jest już `active_principal_generation`).
Każdy ujawnił się dopiero w kontenerze, po przebudowie obrazu.

**Ten akapit sam był przykładem swojej własnej choroby.** Do 2026-08-30 wieczorem podawał
`binding_dla` i `aktywna_principal_generation` jako nazwy „prawdziwe" — po migracji D-010
to były nazwy, których w kodzie już nie ma, więc dokument uczący czytać definicje odsyłał
do nieistniejącej. Jeżeli poprawiasz nazwy w kodzie, **przeczytaj też ten akapit.**

---

## Bramki, pomiary i werdykty

**Pytanie kontrolne przy każdej zmianie w logice bramek: czy ten kod potrafi ogłosić
sukces bez dowodu?**

Najgroźniejsze błędy w tym projekcie nie były awariami — były fałszywymi zaliczeniami
(D-007). Bramka raportująca `GO` przed pomiarem jest gorsza niż bramka, która się wywala,
bo awarię widać, a fałszywy `GO` wygląda jak sukces.

Reguły:
- **Brak dowodu to `NIE_URUCHOMIONO`**, nigdy `GO` ani `NO_GO`.
- Kontrolę wolno zaliczyć **wyłącznie temu kodowi, który wykonał pomiar**.
- Kontrola wymagająca człowieka ma znacznik `wymaga_czlowieka` w artefakcie.
- Progi zapisujemy **przed** pomiarem (`tools/gonogo/criteria.yaml`). Zmiana progu po
  zobaczeniu wyników unieważnia wynik — ta sama reguła, co przy bramce E1 (L4619).
  Zmiana **przed** pomiarem jest legalna: okno GATE-3 skrócono z 6 h na 3 h po czterech
  awariach infrastruktury, ale deklarację zapisano przed startem (D-012).
- **`all()` i `any()` po pustym zbiorze to pułapka.** `all([])` jest `True`. Dwa z sześciu
  fałszywych zaliczeń w tym projekcie to dokładnie to: `brak_shell_i_exec` liczyło
  `all()` po pustym słowniku odrzuceń (D-011 w gate3), a `_evaluate_gate` w raporcie
  ogłaszało `GO` przy pustej liście kontroli wymaganych — dla trzech bramek naraz.
  **Zawsze sprawdzaj liczność osobno od predykatu.**
- **Parser, który po cichu gubi dane, jest tą samą chorobą co kontrola bez pomiaru.**
  Ręczny parser `criteria.yaml` porzucał elementy listy przy poprawnym przeformatowaniu
  pliku, zostawiając `kontrole_wymagane: []` bez błędu. Ma rzucać, nie `continue`.

**Polecenie, które kończy się kodem 0, nie znaczy, że coś zrobiło.** Sprawdzaj, czy
zrobiło. `make build` przez wiele tygodni budował 4 z 8 obrazów i wychodził na zero.
Nie ufaj `| tail` ani `| head` w skryptach — zjadają kod wyjścia; użyj `set -o pipefail`
albo `${PIPESTATUS[0]}`.

**Test ma mierzyć gwarancję, którą produkt daje, i nazywać jej granicę.** Powtórzone
żądanie zapisu w jednej rozmowie wykonuje się raz, O ILE PIERWSZY WYNIK ZDĄŻYŁ TRAFIĆ DO
ŚLADU: rozpoznanie powtórki stoi na PARZE zdarzeń, wywołaniu i wyniku, więc worker zabity
między nimi zostawia powtórkę nierozpoznawalną. Skutek w pamięci i tak jest jeden (0027);
znika znacznik, że to było powtórzenie. Test restartu w połowie tury czeka więc na WYNIK,
a jego docstring mówi to zdanie wprost (E8, 21.09). Test czekający na wywołanie mierzyłby
przypadek, którego produkt nie obiecuje, i mrugałby pod obciążeniem hosta zamiast tę
różnicę pokazać.

**Mrugający test bywa usterką PRODUKTU i rozstrzyga to pomiar, nie rozumowanie.** Mierz
rodzinę kilkanaście razy i zapisuj przy każdym przebiegu WARUNKI, nie tylko werdykt: czas i
obciążenie hosta. Bez tej kolumny widać „dwie porażki bez wzoru", z nią widać wzór. Zmierzone
21.09 na turze przerwanej w locie: obie porażki na osiem przebiegów padły przy obciążeniu 4,2
i 4,4, a wszystkie przebiegi zaliczone w paśmie 5,0 do 8,6. Odwrotna zależność, czyli „im
SZYBCIEJ, tym gorzej", jest podpisem wyścigu, w którym obserwator zaczyna patrzeć ZA PÓŹNO:
pętla tury liczyła granicę śladu po starcie tury, więc przerwanie wciśnięte wcześniej dostawało
niższy numer i było dla niej niewidoczne na zawsze. Człowiek naciskał „przerwij", zdarzenie
stało w śladzie, a tura pracowała dalej. Poprawka usuwa OKNO (granica liczona przed startem), a
nie zmniejsza szansę, i dlatego dowodem jest kolejność zdarzeń, a nie sama seria zielonych
przebiegów. Przy częstości jednej czwartej osiem czystych przebiegów zdarza się raz na dziesięć
prób, więc samo „osiem razy przeszło" nie jest jeszcze zdaniem „naprawione".

**Czekaj na ZDARZENIE, nie na liczbę obrotów pętli.** `for _ in range(100)` po
`sleep(0.05)` to najwyżej pięć sekund według licznika, ale każdy obrót robi też zapytanie
do bazy: na obciążonym hoście pętla wychodzi, zanim rzecz zdąży się wydarzyć, a test
przewraca się na asercji o czymś zupełnie innym. Termin liczony zegarem, koniec w chwili
zdarzenia i GŁOŚNA porażka z nazwą tego, na co czekaliśmy (`_until` w
`test_session_turn_e2e.py`).

---

## Raportowanie

**Nie raportuj sukcesu, którego nie uruchomiłeś.** „Testy powinny przechodzić" nie jest
wynikiem. Wynikiem jest output.

**Zastrzeżenie idzie przy werdykcie, nie w przypisie.** Jeżeli pomiar był węższy niż
wymaga dokument (krótsze okno, mniejsza próbka, jedno środowisko), to jest część wyniku.

**Czego nie zrobiłeś, mów wprost.** Cisza o brakach jest gorsza niż jawna lista.

---

## Zakres

**Zakres wynika z celu użytkownika i aktualnego rezultatu**
([ROADMAP-PIVOT](ROADMAP-PIVOT.md), D-040; wcześniejsze outcome'y w OUTCOMES są historią).
Najważniejszym filarem architektury jest środowisko, narzędzia i trwała warstwa agentowa
realizująca cel: [ADR-0037](adr/ADR-0037-srodowisko-agentowe-realizujace-cel.md), D-037.
Nowe mechanizmy mają wspierać konkretny wynik i samodzielność modelu w jego osiąganiu.
Fazy starej roadmapy i `docs/f0-limits/` opisują historyczny zakres; obowiązujące granice
wykonania odczytujemy z nowszymi decyzjami założyciela.

**Produkt i UX:** [WIZJA-WORKSPACE](produkt/WIZJA-WORKSPACE.md), D-038.
Instrukcje mają wskazywać cel, granice i kryterium wyniku. Szczegóły utrzymujemy
w odpowiednim dokumencie i doczytujemy zależnie od zadania. Punkty wejścia są
odsyłaczami; nie powielamy w nich całej specyfikacji ani sztywnej sekwencji pracy.

**`ROADMAP_CODEX_CDP_COMMUNITY_OS.md` i `STAN-PROJEKTU.md` zmienia tylko człowiek.**

---

## Środowisko (rzeczy zmierzone, nie założone)

- `make env` przed czymkolwiek — helper poświadczeń Dockera na tym hoście wisi (D-008).
- macOS ma GNU Make **3.81**, bez `.ONESHELL`. Wielolinijkowe recepty rozpadają się na
  osobne powłoki — wszystko dłuższe mieszka w `tools/dev/` jako skrypt.
- Nie używaj `--dump-dom` jako smoke testu Chrome; wiesza się. Używaj `/json/version`.
- Dane Postgresa i profile Chromium **tylko na named volume**, nigdy bind mount z macOS.
- **`docker compose --profile a,b` nie wybiera niczego.** Compose szuka jednego profilu
  o dosłownej nazwie `"a,b"`. Listę po przecinku przyjmuje wyłącznie zmienna
  `COMPOSE_PROFILES`; we fladze trzeba `--profile a --profile b`. Do 2026-08-30 `make build`,
  `down`, `ps` i `clean` po cichu pomijały przez to cztery usługi — **i kończyły się kodem 0**.
- **Przebieg dłuższy niż kilkanaście minut uruchamiaj `docker run -d`, nigdy
  `docker compose run` z podłączonym klientem.** Klient trzymany przez godziny zrywa się
  przy każdym zająknięciu demona (`error waiting for container: unexpected EOF`) i zabija
  przebieg. Cztery pomiary GATE-3 padły właśnie tak. Czytaj `docker logs`, nie strumień.
- **Wyłącz healthcheck kontenera na czas długiego pomiaru** (`docker run --no-healthcheck`).
  `codex --version` co 10 s z timeoutem 10 s piętrzy wywołania i wywraca VM Dockera.
  Kontrakt D12 sprawdza i tak `gate3.py`, więc nic przez to nie tracimy.
- **Obciążenie hosta powyżej liczby rdzeni zabija demona Dockera na macOS.** Zmierzone dwa razy
  19.09.2026 przy obciążeniu około 20 na 14 rdzeniach (gra plus maszyna wirtualna): `docker ps`
  przestawał odpowiadać, a stack wracał po samoczynnym restarcie Docker Desktop. `make doctor`
  ostrzega. Przed ciężkim przebiegiem zwolnij maszynę.
- **Ciężkie suity uruchamiaj pojedynczo i po sprawdzeniu, czy nie chodzi cudza.** `docker ps`
  pokazujący `wenext-test-pg-*` albo `wenext-e2e-pg-*` znaczy, że ktoś już mierzy; poczekaj.
- **`codex_runtime` uruchamiaj z `-m "not live"`**, chyba że naprawdę chcesz wołać model:
  testy `live` bez konta startują prawdziwy app-server i wiszą bez końca (§8.44, §8.45).
- **Commit weryfikuj na czystym worktree, nie na drzewie roboczym.** Niezacommitowane pliki
  robocze potrafią zazielenić suitę, która na samym commicie pada (§8.45: tak złapano wadliwą
  listę rodzajów artefaktów przed trwałym zastosowaniem migracji).
- **Nowa migracja nie jest zmierzona bez przebiegu z bazą**, choćby testy bez bazy były
  szczegółowe. 20.09 (§8.49, §8.50): `CHECK (array_length(basis, 1) >= 1)` przepuszczał pustą
  tablicę (`array_length` pustej tablicy to NULL), a wyszło to dopiero na pierwszym przebiegu
  z bazą; tej samej nocy czysta baza złapała rzutowanie z `NULL` w `CASE` (0025) i `CHECK`
  z podzapytaniem (0026). Migracja idzie do commita dopiero po `run_db_tests.sh` na db_schema.
- **Suita dashboardu ma jedną drogę: kontener node.** `apps/dashboard/node_modules` jest
  zbudowane pod obraz linuksowy, więc `vitest` z hosta pada na natywnym module. Nie ma „lekkiego
  uruchomienia na hoście"; `check.sh` kolejkuje się jak każda inna suita.
- **Nowa trasa formularza na ekranie Community musi wejść do DWÓCH list** (`tests/source_scan.test.ts`
  i `e2e/community_reads_postgres.e2e.test.ts`), dopóki nie zostaną scalone (dług z 20.09).
  Druga zatrzymuje dopiero e2e, czyli najdroższy przebieg.
- **Dwie sesje w jednym drzewie roboczym: podział katalogów jest dosłowny**, a sweep
  „na całym repo” jest zakazany, bo 20.09 dwie sesje zderzyły się o minutę przy RUF100.
  Cofanie cudzych zmian przez `git checkout --` w dzielonym drzewie jest zakazane; cofa się
  edycją, po ustaleniu, czyje to.
- **`ruff check --fix` nie łączy się z `--select` przy włączonym RUF100**: przy zawężonym
  zestawie reguł ruff uznaje za zbędne także `noqa` reguł, których nie wybrano, i kasuje
  potrzebne wyciszenia (20.09: siedem, przywrócone porównaniem z listą wyjściową). RUF100
  usuwa się z zachowaniem powodu jako zwykłego komentarza tym samym zdaniem.
- **Asercja, która nigdy nie upada, nie jest testem** (`assert x or True`): kasuje się ją,
  a powód zostaje komentarzem (20.09, `test_presence_tick.py`).
- **Makieta pokazuje ścieżkę, która się udaje.** Produkt musi pokazać też odmowę, brak
  uprawnienia i stan, w którym nie da się nic zrobić; tych elementów w makiecie nie ma i nikt
  ich nie ostyluje, dopóki ktoś nie zobaczy ich na ekranie z prawdziwymi danymi (20.09: powód
  odmowy i konto wstrzymane nachodziły na nazwy; 503 testów jednostkowych i 111 e2e tego nie
  widziało, bo sprawdzają treść, nie położenie). Pierwszy render na prawdziwych danych jest
  częścią zadania, nie odbioru.
- **Ekran ma trzy niezależne sposoby na skłamanie i każdy łapie co innego:** trasa łapie
  niezgodność kształtu, prawdziwe dane łapią stan, którego nikt nie przewidział, a zrzut ekranu
  łapie to, czego nie widać w HTML-u (szerokość, nachodzenie). 20.09 każdy z nich złapał coś,
  czego nie złapały dwa pozostałe; odbiór ekranu obejmuje wszystkie trzy.
- **Kontekst nie jest częścią skopiowanego fragmentu.** Kod przepisany 1:1 (arkusz z makiety,
  popover, nazwa pola) niesie ukryte założenie o tym, w czym stał u autora; samo przepisanie tego
  założenia nie przenosi (20.09: popover w szerokości przycisku, bo opakowaniem był `details`,
  a w makiecie karta). Pierwszy render w docelowym opakowaniu jest częścią przeniesienia.
- **Przenoszenie maszynowe też ma kontekst i on ginie pierwszy.** Arkusz z makiety przepisany
  podmianą korzenia, ale BEZ opakowań `@media`, daje reguły odsłony telefonowej obowiązujące
  zawsze: 20.09 schowało to CAŁY pasek boczny na 1533 px, a układ desktopowy ocalał przypadkiem,
  bo późniejszy blok nadpisań wpisywał kolumny drugi raz. Nie zobaczył tego żaden z 516 testów
  jednostkowych (nie ładują CSS), ani e2e (pasek JEST w HTML-u), ani `studio_style` (pilnuje
  reguł, które są, a nie tego, w jakim kontekście obowiązują). Zobaczył pierwszy ZRZUT. Przy
  przenoszeniu arkusza sprawdź osobno: opakowania warunkowe, `@keyframes`, kolejność bloków.
- **Zamknięta zapadka wygląda na zrzucie jak zwykły przycisk.** 20.09 wpisałem na listę różnic
  „karta konta nie ma popovera", patrząc na `summary` zamkniętego `details`. Zanim napiszesz
  „tego nie ma", kliknij albo sprawdź w komponencie: brak `open` to nie brak elementu.
- **Trasa, która zmienia bazę i wywala się PO zapisie, mówi wołającemu nieprawdę.** 21.09
  `revise` zapisywał nową rewizję szkicu, a potem leciał `AttributeError` w budowaniu tytułu:
  rewizja została, wołający dostał 500, a w rozmowie nie było po poprawce śladu. Człowiek
  poprawiał drugi raz to samo. Zapis i wszystko, co z niego wynika dla człowieka, należą do
  jednej operacji; jeśli druga część potrafi się wywrócić, jej NIEPOWODZENIE ma dojechać do
  ekranu jako osobne zdanie („zapisane, ale jeszcze tego nie widać"), a nie jako sukces ani
  jako cisza.
- **Jeśli obok stoi sprawdzony sposób zadania tego samego pytania, zadaj je tak samo**
  (sformułowanie wenext-84, 21.09). Tamten `AttributeError` wziął się z własnej listy nazw obok
  gotowej mapy `ACTION_OF_CAPABILITY`, której używa sąsiednia trasa; dwa słowniki na jedno
  pojęcie (`SUBMIT_POST` w zdolnościach, `OWN_POST` w kolumnie `kind`) spotkały się dopiero na
  produkcji. To samo dotyczy dwóch list akcji dla jednej karty: były dwiema definicjami tego,
  co wolno.
- **PRZENOSZĄC WYGLĄD Z REFERENCJI, PATRZ NA SWOISTOŚĆ, A NIE NA KOLEJNOŚĆ.** Reguły referencji
  mają w tym drzewie postać `.st .wm-cos` (0,2,0), a arkusz Studia często `.st element` albo
  `.st__cos [data-pop] > summary` (0,1,1 i 0,3,1). Dopisanie drugiej nazwy klasy do znacznika NIE
  WYSTARCZY, gdy obok stoi mocniejszy selektor mówiący co innego, i nie widać tego w kodzie, tylko
  w zmierzonej wartości. Trzy razy jednej nocy (21.09): nazwa przestrzeni została 28-punktowym
  logotypem mimo `wm-workspace-switch`, odstępy w oknie kont wyszły zerowe mimo `margin-bottom:
  34px` (bił je `.st p{margin:0}`), a pozycje paska brały wygląd ze Studia, bo referencja
  stylizuje `button`, a my renderujemy `<a>`. Wniosek praktyczny: **zdejmij regułę Studia albo
  podnieś swoistość świadomie, zamiast dokładać klasę i mieć nadzieję.**
- **Reguła, która DODAJE do układu referencji, jest tą samą chorobą, co reguła, która go
  nadpisuje.** `.st__side{gap:22px}` nie nadpisywał niczego, tylko dokładał 22 px do marginesów z
  makiety, więc wszystko pod nagłówkiem paska stało niżej, a w odbiorze wyglądało to jak jedna
  różnica w położeniu, nie jak własna reguła. Przy przenoszeniu układu sprawdza się też to, co
  arkusz produktu DOKŁADA: `gap`, `margin` na kontenerze, `min-height`.
- **Reguła strukturalna, która nie pasuje do produktu, jest gorsza od braku reguły** (20.09:
  handler kart wymagał prefiksu przestrzeni i odrzuciłby połączenie konta i dołączenie, bo te
  trasy są w zakresie organizacji). Listy dozwolonych tras i pól trzyma się w jednym pliku
  z komentarzem po co, a nie w warunku „wygląda rozsądnie”.
- **Wynik narzędzia w śladzie jest przycinany**, więc cokolwiek ma dojechać w całości (karty,
  adresy), jedzie OBOK wyniku, w osobnym polu zdarzenia; wynik narzędzia wraca też w kopercie
  MCP (`content[0].text` z JSON-em), więc odczyt projekcji bierze pole z koperty, nie z wierzchu
  (20.09: karty nie docierały do rozmowy, testy pojedynczych narzędzi były zielone).
- **Klej między sprawdzonymi częściami to najdroższe miejsce w tym repozytorium** (trzecia
  powtórka 21.09). Za każdym razem kształt był ten sam: funkcje czyste miały testy, a miejsce,
  w którym się spotykają, nie miało żadnego. `--probe-url` wywracał się na `list(None)`,
  `OWN_POST` nie istniało w enumie, a odczyt dowodu na nowym Reddicie pytał o
  `shreddit-comment[author="<konto małymi literami>"]`, czyli o selektor, który nie trafia nigdy,
  bo wartości atrybutów rozróżniają wielkość liter. Ostatnia kosztowałaby najwięcej: komentarz
  zapisany, a w kolejce „skutek nieznany", czyli człowiek wezwany do czegoś, co się udało.
  Wniosek na przyszłość: jeżeli droga kończy się w cudzym DOM albo w cudzym procesie, ma mieć
  test z ATRAPĄ STEROWNIKA (`driver=`, `--config`, fałszywa strona), a nie tylko testy części
  i kontrolę z prawdziwą przeglądarką na końcu. Atrapa ma być przy tym GŁUPIA: strona, która
  umie selektory z atrybutami, przepuściłaby wszystkie trzy usterki.
- **Zapytaj czystą funkcję, zanim zapytasz bazę.** Który dokument zmienia się po nowym ustaleniu,
  mówi `build_documents` w milisekundę; test z bazą, który pyta o złą rolę, kosztuje cały przebieg
  suity i wygląda jak usterka produktu (21.09, wersja karty projektu „nie rosła", bo nie miała
  prawa rosnąć: `input_digest` bez zmiany wejścia to niezmiennik, nie błąd).
- **Konstrukcja cudzego kontraktu z pamięci to ten sam klej, tylko w testach** (21.09, czwarta
  powtórka tej rodziny). `ClaimedJob` ma trzynaście pól; ręcznie przepisana krotka bez czterech
  z nich przeszła `ruff`, import i zbieranie testów, a padła dopiero w przebiegu z bazą, bo do
  tego czasu nikt jej nie ZBUDOWAŁ. Zdanie „sprawdzone statycznie" nie zastępuje wywołania.
  Reguła: jeżeli test składa cudzą strukturę, ma istnieć test bez bazy, który ją buduje, albo
  wspólny pomocnik, który robi to w jednym miejscu (`tests/job_claims.py`).
- **Test regresji pokazuje się na zepsutym kodzie.** Kopia pliku, przywrócenie usterki, przebieg,
  przywrócenie kopii: bez tego nie wiadomo, czy nowy test pilnuje usterki, czy własnej atrapy.
- **`UNVERIFIED` nie jest doświadczeniem** (21.09, P8 etap B). Przemiatanie zapisywało po dobie
  „po N godzinach nikt nie zareagował" także dla publikacji, której nigdy nie udało się odczytać
  anonimowo. To zdanie mówi o CUDZYM zachowaniu, a zmierzony jest wyłącznie nasz brak wiedzy;
  następna sesja czyta je jako lekcję o społeczności i o sobie. Reguła: brak odczytu zostaje
  brakiem ustalenia, a nie porażką. Szerzej: to samo dotyczy każdego miejsca, w którym „nie
  wiemy" da się pomylić z „nie zadziałało".
- Docker Desktop **sam się pauzuje** przy niskim obciążeniu (Resource Saver). Przy pomiarze
  z długimi przerwami między cyklami trzymaj VM zajętą albo wyłącz tę funkcję w GUI.
  `caffeinate -ims` osobno blokuje uśpienie samego macOS-a.
- **Suita `codex_runtime` tylko z `-m "not live"`.** Trzy testy z `@pytest.mark.live`
  (`test_full_chatgpt_login`, `test_rate_limits_on_a_logged_in_account`,
  `test_resume_after_a_completed_turn`) wymagają prawdziwego konta ChatGPT; bez niego suita
  wygląda na zawieszoną i kończy się trzema porażkami, które nie są regresją.
- **Handshake app-servera nie dowodzi użycia wpisu konfiguracji.** Przy `required = true`
  z martwą komendą serwera narzędzi app-server Codexa 0.147.0 wstaje mimo to. Pomiar, czy wpis
  `mcp_servers` dotarł do procesu, czyta `mcpServerStatus/list` i znacznik przekazany przez
  środowisko do procesu potomnego (`config_probe --mcp-overrides`), nie kod wyjścia.
- **Zrzut ekranu bez wtyczki Chrome.** Headless Chromium z `.venv` repozytorium (Playwright, ten
  sam co w `render_review.py`) robi zrzuty na hoście macOS, więc kroje pisma są jak w Chrome.
  Makietę i aplikację zrzuca się TYM SAMYM narzędziem w tym samym oknie; tekst kolumny obok
  zrzutu dowodzi istnienia zdania, nie jego widoczności w kadrze.

---

## Przed przerwą i przed czyszczeniem kontekstu

`/kontekst checkpoint` — uruchamia testy, aktualizuje `docs/STAN-PRAC.md`, sprawdza
spójność dokumentów i przypomina o commicie.
