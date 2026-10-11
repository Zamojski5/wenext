# Rejestr niewykonalnego w F0 — jawne granice spike'u

- **Status:** ACCEPTED
- **Data:** 2026-08-30
- **Faza:** E1.F0 (deliverable D04)
- **Cytowany przez:** `tools/gonogo/report.py`, sekcja „Czego ten raport NIE dowodzi"
  (linie 260–263 tamtego pliku — generator sprawdza istnienie tego pliku i podaje do niego
  ścieżkę w każdym raporcie Go/No-Go)
- **Test spójności:** [`test_registry.py`](test_registry.py)

Zasada, z której ten dokument wynika: **jeżeli coś jest niewykonalne, to jest deliverable
z jawnym uzasadnieniem, nie cisza.** Rejestr wylicza, czego F0 nie dowodzi, dlaczego się
nie da, w której fazie to wchodzi i jaki test to wtedy zamknie.

Każda pozycja ma cztery stałe pola. Żadna pozycja nie może mieć `Faza docelowa: E1.F0` —
to byłoby stwierdzenie, że F0 jednocześnie coś dowodzi i nie dowodzi. Pilnuje tego test.

---

## 1. Zachowanie prawdziwych platform w headless

- **Czego F0 nie dowodzi:** że Reddit, LinkedIn, X, Discord czy Slack dają się obsłużyć
  headless. Fixture site (`fixtures/site/`) jest deterministycznym stubem pod kontrakt CDP,
  nie repliką żadnego serwisu.
- **Dlaczego się nie da w F0:** capability probe jest ważny wyłącznie dla builda i trybu,
  w którym powstał (L4381). Probe na fixture'cie nie mówi nic o Reddicie, a probe na
  Reddicie wymaga prawdziwego, zalogowanego konta — czyli D20 i człowieka. GATE-1 dowodzi
  kontraktu CDP: persistent context, locatory, popupy, screenshoty, traces, reattach,
  odrzucenie niezgodnego builda, stale `DevToolsActivePort`, brak publicznego CDP.
- **Faza docelowa:** E1.F2
- **Test, który to zamknie:** `probe_capabilities()` uruchomiony w trybie `HEADLESS` na
  prawdziwym surface, z wynikiem zapisanym w `capability_snapshots`; rozbieżność wobec
  trybu docelowego daje `DEGRADED` albo `UNKNOWN`, nie cichą kontynuację (L4381).

## 2. Unattended-auth Codeksa bez konta ChatGPT

- **Czego F0 nie dowodzi:** że Codex logowany kontem ChatGPT wytrzymuje wielogodzinną pracę
  bez otwartego dashboardu (DoD F0, L5182).
- **Dlaczego się nie da w F0:** nie ma czego zasymulować. Wymaga człowieka z kontem ChatGPT
  i włączonego device-code login po stronie administratora workspace'u (D20). Obejście
  loginu jest wykluczone przez sekcję 4.
- **Uwaga na granicę z DoD F0:** L5182 wymaga, żeby tryb wielogodzinnej pracy został
  „potwierdzony albo odrzucony". Ten rejestr nie kwestionuje tego wymogu — stwierdza węższą
  rzecz: **nie da się go zamknąć bez człowieka**, więc żaden agent ani żaden bieg CI nie
  postawi tu wyniku. GATE-3 pozostaje `NIE_URUCHOMIONO` do czasu ludzkiego uruchomienia,
  a `NIE_URUCHOMIONO` nie jest ani GO, ani NO_GO.
- **Faza docelowa:** E1.F0 z udziałem człowieka (D20)
- **Test, który to zamknie:** `make codex-gonogo` produkujący `artifacts/gate3/codex-runtime.json`
  z kontrolą `unattended_rozstrzygniete`. Wynik `NO_GO` jest wynikiem poprawnym; brak wyniku
  nie jest (`tools/gonogo/report.py` daje wtedy `NIE_URUCHOMIONO`, nigdy `GO`).

## 3. Jakość predykcyjna evali

- **Czego F0 nie dowodzi:** że model osiąga jakikolwiek poziom trafności w discovery,
  ocenie jakości dyskusji albo draftowaniu odpowiedzi.
- **Dlaczego się nie da w F0:** nie ma ground truth. 30–50 fixture evali (L5168) dowodzi,
  że **harness działa** i że **definicja jakości jest zakodowana**, a nie że model ją spełnia.
  Dlatego `tools/gonogo/criteria.yaml` ma `evale.prog_zdawalnosci: 0.0` — to decyzja, nie
  przeoczenie.
- **Faza docelowa:** E1.F3 (harness na prawdziwych danych), E1.F6 (progi bramki)
- **Test, który to zamknie:** metryki z 22.11 liczone z rekordów kolejki: precision
  discovery = `ENTER / (ENTER+SKIP+IRRELEVANT)` ≥ 40%, drafty ≥ 50% zatwierdzonych bez
  `MATERIAL_EDIT` i ze znormalizowanym dystansem edycji ≤ 0,15.

## 4. Kalibracja do tła kanału

- **Czego F0 nie dowodzi:** że umiemy odróżnić wynik od tła kanału — normalizacji do mediany
  surface dla danego formatu, godziny i wielkości sieci autora (L4443).
- **Dlaczego się nie da w F0:** kalibracja wymaga baseline'u surface, a baseline wymaga
  min. 5 sesji odczytu w różnych porach przez połączone konto (L4501). W F0 nie ma
  połączonych kont. W fixture'ach kalibracja jest **daną wejściową**, nie liczoną wartością —
  fixture site generuje deterministyczne metryki, więc „mediana surface" jest tam ustawiona,
  nie zmierzona.
- **Faza docelowa:** E1.F3
- **Test, który to zamknie:** `ChannelSurfaceBaseline` z `data_quality ≥ MEDIUM` zbudowany
  z append-only snapshotów (7.6) i użyty jako mianownik w ocenie wątku; brak baseline'u
  musi dawać `UNKNOWN`, nie surową liczbę.

## 5. Detektor fake engagementu w rankingu

- **Czego F0 nie dowodzi:** że flagi `fake_engagement_flags` mają jakąkolwiek precision
  albo recall.
- **Dlaczego się nie da w F0:** detektor jest odblokowany dopiero po **minimum 200 wątkach
  na archetyp**, etykietowanych ręcznie przez człowieka, z zapisanym rozstrzyganiem sporów
  (L4492). Do tego czasu detektor działa w trybie **log-only**: flaguje, ale nie obniża
  `priority_tier` i nie odrzuca wątku. Etykietowanie ma własny `labeling_budget_minutes`
  i jest liczone **poza** metryką „minut pracy człowieka na wkład" z 22.11, więc nie zjada
  budżetu z pozycji 8 poniżej.
- **Faza docelowa:** poza E1 (próbka może być zbierana także poza oknem bramki)
- **Test, który to zamknie:** zmierzone precision/recall na ≥ 200 ręcznie zaetykietowanych
  wątkach na archetyp; dopiero wtedy decyzja, czy detektor wchodzi do rankingu. W F0 jest
  jedynie test, że flaga **nie odrzuca** wątku (`test_fake_engagement_flag_does_not_reject_the_thread`).

## 6. Pełna kolejka jobów, RLS i outbox

- **Czego F0 nie dowodzi:** at-least-once transportu między procesami ani defense-in-depth
  przy błędnym zapytaniu do bazy.
- **Dlaczego się nie da w F0:** to jest świadome odroczenie z L5205–5206, nie brak.
  Uzasadnienie dokumentu: jeden operator, jeden runner, jedna kolejka, jedna organizacja,
  instalacja lokalna. Odroczenie jest ważne wyłącznie dlatego, że oba elementy są schowane
  za portami — dispatcher za `JobTransportPort`, dostęp do danych za workspace-scoped
  Domain Data Gateway — i dołożenie ich w E3 nie może zmienić komend domenowych ani schematu.
- **Faza docelowa:** E1.F1 (kolejka jobów w wersji minimalnej), E3 (RabbitMQ, outbox, RLS)
- **Test, który to zamknie:** harness dwóch tenantów z osobnymi login roles (E3);
  publisher confirms i inbox receipts na duplikatach. W F0 zamknięte jest tylko to, co
  umożliwia późniejsze dołożenie: kolumny `organization_id`/`workspace_id` i composite FK
  od pierwszej migracji (L5199), sprawdzone testem `test_composite_fk_blocks_foreign_organization`.

## 7. TargetDefinition, registry platform i presence track

- **Czego F0 nie dowodzi:** że orkiestrator potrafi ustalić `TargetDefinitionVersion`,
  że registry rozróżnia `access_mode` surface'ów i że presence track awansuje na dowodach.
- **Dlaczego się nie da w F0:** wszystkie trzy artefakty wymagają połączonych kont i
  prawdziwych surface'ów. `access_mode` to własność zweryfikowana empirycznie, nie deklaracja
  (L4395–4400); presence track awansuje wyłącznie na dowodach, nie po upływie czasu (L4496).
- **Faza docelowa:** E1.F2 (konta, `access_mode`), E1.F3 (registry, TargetDefinition),
  E1.F4–F6 (presence track)
- **Test, który to zamknie:** `StageTransitionProposal` oceniany formułą stage gate z 8.3,
  z brakującym dowodem blokującym przejście niezależnie od tego, ile dni minęło (L4509).

## 8. Arytmetyka bramki E1 — liczby, nie kod

To jest jedyna pozycja rejestru, która jest **wyliczeniem**, a nie odroczeniem. Wchodzi do
Go/No-Go harmonogramu (L5343), bo decyduje, czy okno pomiarowe jest w ogóle wykonalne przy
limitach z 22.8.

- **Czego F0 nie dowodzi:** że harmonogram bramki E1 domyka się przy dziennych capach
  z 22.8 i przy capie kolejki approvali z 22.10 — F0 nie ma ani jednego wkładu, na którym
  dałoby się to zmierzyć.
- **Dlaczego się nie da w F0:** pomiar wymaga pełnego okna 6–8 tygodni i dwóch kont
  zbudowanych od zera. Można natomiast **przeliczyć** to z góry i to jest treścią tej
  pozycji: arytmetyka nie wymaga danych, wymaga limitów, które dokument już podaje.
- **Faza docelowa:** E1.F6 (pomiar), tu i teraz (wyliczenie)
- **Test, który to zamknie:** `ContributionMixReport` i logi kolejki z pełnego okna;
  faktyczne minuty operatora na `CONFIRMED` wobec progu 6 min z L4614.

### 8.1. Ile wkładów trzeba zebrać

| Wejście | Wartość | Źródło |
| --- | --- | --- |
| potwierdzone `PUBLISH_REPLY` z evidence na archetyp | ≥ 40 | L4608 |
| różne `ChannelArchetype` | ≥ 2 | L4602 |
| konta zbudowane od zera | ≥ 2 | L4602 |
| **łącznie wkładów** | **≥ 80** | 40 × 2 |
| długość okna | 6–8 tygodni kalendarzowych = 42–56 dni | L4602 |

`UNKNOWN` nie liczy się jako wkład (L4608). Reakcje nie są wkładem: `ContributionMixReport`
klasyfikuje wkłady wyłącznie jako odpowiedź merytoryczną, pytanie doprecyzowujące,
sprostowanie, polemikę i wkład z odniesieniem do własnego doświadczenia (L4622).

### 8.2. Ile dni minimum zajmie ich zebranie

Dzienne capy komentarzy per konto × surface (L4503–4504) i dowody wymagane do awansu
(ta sama tabela):

| Stan | Cap komentarzy/dobę | Wkłady zebrane w tym stanie | Dni |
| --- | --- | --- | --- |
| `UNASSESSED` → `BASELINED` | 0 | 0 | ≥ 1 (konto `READY`, `AccountAssessment`, capability snapshot, `rules_snapshot`) |
| `BASELINED` → `OBSERVE_ONLY` | 0 | 0 | ≥ 2 (Community Map v1 `data_quality ≥ MEDIUM`, min. 5 sesji odczytu w różnych porach) |
| `OBSERVE_ONLY` → `VISIBILITY_BUILDING` | 0 | 0 | ≥ 2 (min. 30 ocenionych wątków, `entry_thesis`, członkostwo, profil kompletny) |
| `VISIBILITY_BUILDING` | **1** | 10 (dowód wymagany do awansu: min. 10 wkładów z evidence) | **10** |
| `SOFT_PARTICIPATION` | **2** | 30 (reszta do 40) | **15** |
| | | **40** | **≥ 30 dni** |

Oba archetypy prowadzą osobne konta równolegle, więc **minimum kalendarzowe to 30 dni, nie 60.**

| Okno | Dni | Zapas nad minimum 30 dni |
| --- | --- | --- |
| 6 tygodni | 42 | **12 dni** |
| 8 tygodni | 56 | **26 dni** |

To potwierdza liczbowo zdanie z L5343 („samo okno to zawsze 6–8 tygodni kalendarzowych —
tyle wymaga arytmetyka bramki z 22.11 przy limitach dziennych z 22.8"). Dodaje też wniosek,
którego dokument nie wyciąga:

> **Zapas 12 dni w oknie 6-tygodniowym nie wystarcza na jedno zdarzenie degradacji.**
> Usunięcie przez moderację, ostrzeżenie, challenge, `IDENTITY_MISMATCH` albo
> `CAPABILITY_LOST` przenoszą track do `REASSESSMENT_REQUIRED` lub `BLOCKED`
> natychmiast i automatycznie (L4510). Odbudowa od `VISIBILITY_BUILDING` kosztuje
> 10 + 15 = 25 dni. Przy oknie 42-dniowym degradacja na dowolnym z dwóch kont po dniu 17
> czyni próg ≥ 40 wkładów na archetyp nieosiągalnym w oknie. Przy oknie 56-dniowym
> mieści się jeszcze degradacja do dnia 31. **Planuj 8 tygodni, nie 6.**

Do zapasu trzeba wliczyć także: opóźnienie odpowiedzi ludzi (snapshoty po 1/6/24 h, L4610)
i cotygodniowy rytm przeglądów (L5349) — awans jest `StageTransitionProposal` ocenianym
przez człowieka, więc każde z dwóch przejść może się przesunąć nawet o 6 dni.

### 8.3. Gdzie jest wąskie gardło

Każda akcja zewnętrzna w E1 — komentarz, odpowiedź, reakcja/lajk — jest
`AUTOMATED_AFTER_APPROVAL` i powstaje jako `ActionProposal` z jednorazowym `ExecutionIntent`
po zgodzie człowieka (L5152). **Każda akcja to jedna karta w kolejce approvali.**

Capy z L4593: maks. **12 kart na operatora dziennie**, maks. **8 kart na konto dziennie**.
Cap kolejki jest nadrzędny wobec budżetu presence tracku z 22.8. Nadmiar nie jest
kolejkowany „na jutro" — jest odrzucany z powodem `APPROVAL_CAPACITY_EXCEEDED`.

Popyt na karty przy pełnym wykorzystaniu budżetu `SOFT_PARTICIPATION` (2 komentarze + 5 reakcji
= 7 kart na konto na dobę):

| Kont w `SOFT_PARTICIPATION` | Kart/dobę | Cap operatora | Deficyt | Same komentarze |
| --- | --- | --- | --- | --- |
| 1 | 7 | 12 | 0 | 2 |
| 2 (minimum bramki) | 14 | 12 | **2** | 4 |
| 3 | 21 | 12 | **9** | 6 |
| **4** (2 archetypy × konto od zera + konto z historią) | **28** | **12** | **16** | 8 |
| 6 | 42 | 12 | 30 | 12 |

Cap per konto (8/dobę) nigdy nie wiąże w E1: 7 ≤ 8. Wiąże **wyłącznie cap operatora**.

Same komentarze mieszczą się w capie do 6 kont (2 × 6 = 12). Przy 2–4 kontach metryka bramki
nie jest więc dławiona bezpośrednio — **klasą poświęcaną są reakcje.** Ale to działa tylko
wtedy, gdy kolejka wypycha reakcje przed komentarzami, a reguła sortowania z L4595 sortuje
rosnąco po `expires_at` w obrębie `priority_tier` i **nie zna różnicy między komentarzem
a reakcją**. Reakcja z krótszym TTL wyprzedzi komentarz i zje limit operatora. Dokument
tego nie rozstrzyga — to jest luka, nie interpretacja.

### 8.4. Prawdziwe wąskie gardło: bilans czasu operatora

Próg PASS: ≤ 6 min pracy człowieka na jeden opublikowany wkład. Próg BLOCK: > 15 min.
Mierzone jako suma czasu w kolejce approvali + takeover + edycje, dzielona przez liczbę
`CONFIRMED` (L4614).

| Wielkość | 42 dni (6 tyg.) | 56 dni (8 tyg.) |
| --- | --- | --- |
| budżet czasu przy progu PASS (80 × 6 min) | 480 min = **8,0 h** | 480 min = **8,0 h** |
| średnio na dobę | **11,4 min** | **8,6 min** |
| sufit BLOCK (80 × 15 min) | 1200 min = 20 h → 28,6 min/dobę | 1200 min = 20 h → 21,4 min/dobę |

Z tego wynikają dwie liczby, których nie ma nigdzie indziej w dokumencie:

1. **Przy pełnym wykorzystaniu capu 12 kart/dobę przez 42 dni operator obsłuży 504 karty.**
   Budżet 480 minut daje wtedy **0,95 min ≈ 57 sekund na kartę**, wliczając takeovery
   i edycje. To jest nieosiągalne. **Cap 12 kart/dobę jest sufitem, którego nie wolno
   dotknąć — nie targetem.**
2. **Przy realistycznych 2 min na kartę budżet PASS mieści 240 kart w całym oknie, czyli
   5,7 karty na dobę.** Z tych 240 kart 80 musi skończyć jako `CONFIRMED` — czyli
   **co najmniej co trzecia karta musi stać się potwierdzonym wkładem.** Reakcje
   w maksymalnym budżecie (5/konto/dobę × 2 konta × 25 dni publikowania = 250 kart)
   przekraczają cały budżet czasu **same z siebie**.

> **Wniosek: reakcje w E1 trzeba traktować jako opcjonalne i trzymać blisko zera.**
> Żaden dowód wymagany do awansu presence tracku w 22.8 nie liczy reakcji — liczy wkłady,
> odpowiedzi ludzi i powracających rozmówców. Reakcje kosztują kartę i minuty operatora,
> a nie wnoszą nic do bramki.

To jest też odpowiedź na otwarte pytanie nr 3 ze `STAN-PROJEKTU.md` („ile realnie minut
dziennie operator poświęci na kolejkę approvali"): **żeby zdać próg PASS, średnia dzienna
suma pracy człowieka przez całe okno musi mieścić się w 8,6–11,4 minuty; przekroczenie
średniej 21–29 minut dziennie oznacza BLOCK.** `daily_operator_review_minutes`
w `TargetDefinition` musi zostać ustawione w tym przedziale, zanim okno wystartuje.

## 9. Zakaz koordynowanego podbijania nie jest niczym egzekwowany

- **Czego F0 nie dowodzi:** że system uniemożliwia dwóm własnym kontom działanie w tym
  samym wątku (L4305, L4513).
- **Dlaczego się nie da w F0:** reguła jest zapisana jako dana
  (`ForbiddenPractice.COORDINATED_SELF_AMPLIFICATION` w
  `contracts/src/community_os_contracts/scope.py`), ale nie ma czym jej sprawdzić: nie ma
  Conflict Engine (sekcja 19.6), nie ma tabeli wątków, nie ma kolejki approvali. To jedyna
  z dziewięciu pozycji granicy zakresu, którą w F0 egzekwuje wyłącznie człowiek —
  kontrola **C-30** w `docs/threat-model/controls.yaml` ma z tego powodu `enforced_by: []`
  i `status: PLANOWANE`.
- **Faza docelowa:** E1.F4
- **Test, który to zamknie:** Conflict Engine odrzucający `ActionProposal` dla wątku,
  w którym inne konto tego samego workspace'u ma już `CONFIRMED` wkład; przypadek testowy
  z dwoma kontami i jednym `target_external_id`.

## 10. Pin Chrome for Testing z kanału Beta

- **Czego F0 nie dowodzi:** że build, na którym powstały GATE-1 i GATE-2, będzie dostępny
  i aktualny przez cały etap E1.
- **Dlaczego się nie da w F0:** `linux-arm64` istnieje w Chrome for Testing dopiero od
  153.0.8001.0. Stable 152.0.7977.64 go nie ma. Jedyną wersją mającą jednocześnie
  `linux-arm64` i `linux64` — a więc jedyną, przy której dev na Apple Silicon i CI na amd64
  używają tego samego exact executable (L2307) — jest **153.0.8010.12 z kanału Beta**.
  Uzasadnienie i odrzucone warianty: [ADR-0002](../adr/ADR-0002-build-przegladarki-na-arm64.md).
- **Ryzyko, które przyjmujemy:**
  1. Beta jest kanałem ruchomym; build może zostać wycofany z rejestru CfT.
  2. Platformy testują się wobec Stable, nie Bety — różnica zachowania obciąża adapter.
  3. Dokument wymaga polityki maksymalnego dopuszczalnego patch lagu przypiętego
     Chrome for Testing (L2313). **Takiej polityki jeszcze nie ma** — to jest dług.
- **Reguła wygaśnięcia pinu:** gdy M153 (albo wyższa) wejdzie na kanał Stable, pin
  **musi** zostać przeniesiony na build Stable z linii 153 lub wyższej. Zostawienie pinu
  na Becie „bo działa" jest naruszeniem tej reguły. Podniesienie pinu wymaga:
  nowej wersji w `versions.lock.toml` z pobranymi na nowo metadanymi z GCS (`md5Hash`,
  `size`, `generation`); ponownego `make gonogo`, bo capability snapshot poprzedniego builda
  traci ważność (L4381); decyzji o istniejących profilach — domyślnym recovery jest reauth
  na nowym profilu, nie przenoszenie cookies (L2312), a downgrade profilu otwartego nowszą
  wersją jest zakazany (L2311).
- **Ograniczenie terminowe:** podniesienie pinu unieważnia wszystkie capability snapshoty,
  więc **nie wolno go wykonać w trakcie okna F6** — okno ma feature freeze (L5349), a
  wymiana runtime'u przeglądarki nie jest poprawką. Jeżeli M153 wejdzie na Stable w trakcie
  okna, pin zostaje do końca okna i jest podnoszony natychmiast po podpisaniu `E1ExitReport`.
- **Faza docelowa:** E1.F1 (polityka patch lagu z L2313), po zamknięciu okna F6 (podniesienie pinu)
- **Test, który to zamknie:** `tools/dev/check_versions.py` rozszerzony o sprawdzenie
  maksymalnego patch lagu wobec aktualnego Stable oraz o kanał, z którego pochodzi pin.

## 11. Enum wyniku bramki E1 nie jest domknięty — pozycja z terminem

- **Czego F0 nie dowodzi:** niczego; to jest sprzeczność w samym dokumencie, którą F0 ma
  obowiązek zgłosić, a nie rozstrzygnąć po cichu.
- **Na czym polega:** dwa miejsca dokumentu podają **różne, niepokrywające się** zbiory
  możliwych wyników bramki E1.

  | Sekcja | Wyniki | Reguła |
  | --- | --- | --- |
  | 22.11 (L4620–4621) | `PASS` / `BLOCK` / `NO_DATA` | PASS wymaga wszystkich progów PASS; BLOCK po dowolnym progu BLOCK; stan pośredni to `NO_DATA` z jednym przedłużeniem o maks. 2 review windows |
  | 25.2 (L4219–4221, L5371–5381) | `PASS` / `CONDITIONAL_PASS` / `BLOCK` | wszystkie twarde + wszystkie miękkie → PASS; wszystkie twarde + dokładnie jedno miękkie naruszone → `CONDITIONAL_PASS`; dowolne twarde niespełnione → BLOCK |

  `NO_DATA` nie występuje w regule decyzji z 25.2. `CONDITIONAL_PASS` nie występuje
  w regułach oceny z 22.11. Pytania bez odpowiedzi w dokumencie: co się dzieje, gdy
  wszystkie twarde kryteria są spełnione, jedno miękkie jest naruszone, **a część metryk
  jest `NO_DATA`**? Czy drugie `NO_DATA` — traktowane jak `BLOCK` (L4602) — wyklucza
  `CONDITIONAL_PASS`? Czy `CONDITIONAL_PASS` zużywa przedłużenie z reguły 3 w 22.11?
- **Dlaczego to nie może czekać:** progi są zapisywane w `MarketingPlanVersion` **przed**
  startem etapu jako immutable `stage_gate_criteria` i po fakcie nie podlegają negocjacji;
  zmiana tworzy nową wersję planu z `THRESHOLD_CHANGED` i **unieważnia bieżący wynik etapu**
  (L4619). Zbiór dopuszczalnych wyników jest częścią kryteriów. Domknięcie enuma po
  zamrożeniu = zmiana kryteriów = unieważnienie okna, na które poszło 6–8 tygodni.
- **Termin:** **przed startem okna F6**, czyli przed zamrożeniem `stage_gate_criteria`.
  Nie „w trakcie F6", nie „przy pisaniu `E1ExitReport`".
- **Kto rozstrzyga:** człowiek. To nie jest zadanie do zaimplementowania — to decyzja
  produktowa założycieli, która następnie zostaje zakodowana jako enum.
- **Faza docelowa:** E1.F5 (ostatnia faza przed oknem F6)
- **Test, który to zamknie:** enum `E1GateOutcome` w `contracts/` z testem zgodności
  z dokumentem — analogicznym do `test_every_enum_value_appears_in_the_document` —
  plus test, że `stage_gate_criteria` nie da się zapisać bez kompletu wartości enuma
  i że zmiana po zamrożeniu wymusza `THRESHOLD_CHANGED`.

## 12. Security baseline nie jest kompletny

- **Czego F0 nie dowodzi:** że warunek wejścia do F2 z L3890 („security baseline powstaje
  przed pierwszym prawdziwym logowaniem, a nie na końcu roadmapy") jest spełniony.
- **Czego brakuje:** pięć pozycji, wypisanych jako niezaznaczona checklista w
  [`docs/threat-model/README.md`](../threat-model/README.md), rozdz. 7 — szyfrowanie storage
  profili osobnym DEK (L3894, L3914); wyłączenie trace, screenshotów, nagrywania ekranu,
  klawiszy i clipboardu podczas loginu i MFA (L3897, L3907); `acceptDownloads=false` i
  wyłączony clipboard/upload/download/druk (L3899, L3908); RFB-aware Stream Gateway
  z wyłącznie serwerowym credentialem TigerVNC (L2470); iframe na osobnym originie z CSP
  i `frame-ancestors` (L3910); proces `DRAINING → DELETING → DELETED` z crypto-erasure
  klucza (L3902–3903).
- **Dlaczego się nie da w F0:** w F0 nie ma ani jednego prawdziwego logowania, więc nie ma
  ścieżki, na której te kontrole miałyby działać. Nie jest to jednak powód do odłożenia ich
  „na F2" — L3890 stawia je **przed** pierwszym logowaniem, czyli przed czymkolwiek innym w F2.
- **Faza docelowa:** E1.F2, jako warunek wejścia
- **Test, który to zamknie:** odhaczona checklista z rozdz. 7 threat modelu; kontrole
  C-15, C-24 i C-26 w `docs/threat-model/controls.yaml` przechodzące ze statusu
  `PLANOWANE`/`CZESCIOWO` na `EGZEKWOWANE` wraz ze wskazanym testem — czego pilnuje
  `docs/threat-model/test_controls.py`.

---

## Czego ten rejestr NIE jest

Nie jest listą rzeczy, które poszły źle. Pozycje 1–7 i 12 to **zaplanowane granice spike'u**:
F0 dowodzi kontraktu wykonania i poprawności jądra, a nie skuteczności marketingowej.
Pozycja 8 to wyliczenie. Pozycje 9–11 to trzy realne długi: jedna reguła sekcji 4 bez
egzekwowania, jeden pin z kanału Beta bez polityki patch lagu i jedna sprzeczność w samym
dokumencie z twardym terminem rozstrzygnięcia.

Żadna pozycja tego rejestru nie figuruje jednocześnie jako Definition of Done fazy F0
(L5175–5182) — pilnuje tego [`test_registry.py`](test_registry.py).
