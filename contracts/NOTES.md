# Contracts — notatki wykonawcze

Status: **F0 domknięte, warstwa F1 dopisana i uruchomiona.**
Wynik z 2026-08-31: `347 passed` (`.venv/bin/python -m pytest tests -q -rs`, zero pominiętych).
Poprzedni zapis w tym pliku mówił „63 testy" i pochodził sprzed pakietu F1 — był nieaktualny
o dwa pakiety. Liczba tutaj ma być przepisana z outputu, nie z pamięci.

## Co tu jest

| Moduł | Co zawiera | Sekcje dokumentu |
| --- | --- | --- |
| `observed.py` | `Observed[T]` i `UnknownReason` — rozróżnienie `UNKNOWN` od zera | 22.2 niezm. 7 (L4299), E1.F2 reg. 5 (L5242) |
| `enums.py` | wszystkie enumy przepisane dosłownie, z numerami linii | 13.1, 14.1, 14.5, 14.7, 14.8, 22.5, 22.7, 22.8 |
| `scope.py` | granica zakresu jako dane + `ScopeViolation` | 4 (L237-253) |
| `capability.py` | `CapabilitySnapshot`, capability mapping E1, `ResourceRequirement` | 14.7 (L2523-2580), 22.3, 22.5 |
| `browser.py` | trzy lease'y, `BrowserSession`, `StreamTicket`, `ModeTransition` | 14.3-14.5 (L2315-2470) |
| `execution.py` | payload hash, niezmienna rewizja, `supersede`, jednorazowy `ExecutionIntent`, `SideEffectAuthorization`, `AutomationRun` | 16 (L2736-2828), L5198, L5217-5218, L5539 |
| `quality.py` | `ThreadQualityAssessment`, porządek klas sygnału | 22.7 (L4430-4492) |
| `codex.py` | `ThreadSecurityBinding`, `CodexRunThread`, resume fail-closed | 13.3-13.4 (L2191-2266) |
| `transitions.py` | **F1.** Sześć tabel przejść + `require_transition` | 14.8 (L2582-2606), 16.1 (L2773-2775) |
| `jobs.py` | **F1.** `JobFencingToken`, `StaleJobFencingToken`, `JobTransportPort` | L5202, L5206, L5208, L5211, L5216 |

Liczność tabel przejść (stan / liczba par), sprawdzana testem:
`SocialAccount` 6/5, `BrowserProfile` 6/9, `BrowserSession` 6/9, `HumanTakeover` 7/12,
`ExecutionAttempt` 8/10, `ActionProposal` 6/5. Każdy stan jest kluczem — także terminalny,
z pustym zbiorem celów. Brak klucza zamieniłby odmowę przejścia w `KeyError`, czyli awarię
wyglądającą jak błąd programu zamiast jak decyzja.

## Niezmienniki egzekwowane typem, nie komentarzem

Te rzeczy są w tym pakiecie **niewykonalne**, a nie tylko odradzane:

- zbudowanie `Observed` bez wartości i bez powodu, albo z obiema naraz;
- odczytanie `UNKNOWN` jako liczby (`require()` rzuca);
- rewizja, której `payload_hash` nie zgadza się z `hash_input`;
- wykonanie akcji zapisującej bez `ApprovalDecision` i `ExecutionIntent`;
- **wykonanie akcji na rewizji, którą zastąpiła nowsza** (`supersede` → `SUPERSEDED`);
- **przedłużenie pierwotnego TTL przez edycję** — `supersede` nie przyjmuje `expires_at`
  jako argumentu, więc nie ma czym przedłużyć (L5539);
- drugie zużycie tego samego `ExecutionIntent`;
- zbudowanie `SideEffectAuthorization` dla intentu, który nie został zużyty;
- automatyczny retry po `SUBMIT_STARTED` / `UNKNOWN` (`may_retry_automatically`);
- przejście stanu spoza tabeli sekcji 14.8 / 16.1 (`require_transition` → `IllegalTransition`);
- użycie starej generacji lease'u profilu (`ProfileLease.verify` → `StaleFencingToken`)
  i lease'u joba (`require_fresh_job_lease` → `StaleJobFencingToken`);
- sesja `HEADLESS` z displayem albo stream gatewayem;
- `CONTROL` StreamTicket bez powiązanego `InteractionLease` i jego generacji;
- capability `MANUAL_ONLY` z `required_browser_mode=HEADLESS`;
- akcja `IRREVERSIBLE` z `execution_mode=AUTOMATED` albo `retry_class=SAFE_RETRY`;
- ocena wątku z sygnałem negatywnym i `priority_tier` innym niż `REJECT`;
- `REJECT` uzasadniony samą flagą fake engagementu (w E1 detekcja jest log-only);
- resume wątku Codexa przy jakiejkolwiek różnicy stabilnego bindingu.

## Dwie funkcje autoryzacji i dlaczego to nie jest duplikat

`authorize_side_effect()` jest **kontrolą**: zwraca `None` i zostawia intent niezużyty.
Najmocniejsze zdanie, jakie można o niej napisać w teście, brzmi „nie rzuciło" — a to jest
ta sama klasa dowodu, co bramka ogłaszająca `GO` przed pomiarem. Zostaje, bo używa jej
`tests/test_invariants.py` do sprawdzania pojedynczych luk.

`authorize_and_consume()` jest **drogą produkcyjną** (L5217, L5334). Wykonuje te same kontrole,
zużywa intent w tym samym wywołaniu i zwraca `SideEffectAuthorization` — dowód wskazujący
`revision_id`, `payload_hash` i `intent_id`. Bez zużycia w tym samym wywołaniu druga próba
submitu wraca jeden restart później, a L2821 dopuszcza najwyżej jedną.

Poprzednia wersja tego pliku wskazywała `authorize_side_effect()` jako „jedyną drogę do akcji
zapisującej". To była nieprawda w chwili pisania i dokładnie ten wariant choroby, który
`STAN-PRAC.md` sekcja 7 opisuje jako „dokument twierdzący coś, czego kod nie ma".

## Rozbieżności w dokumencie, których NIE rozstrzygnąłem po cichu

1. **Dwa słowniki nazw capability.** Sekcja 14.7 (L2566-2578) używa nazw adapterowych
   (`SUBMIT_COMMENT`, `PREPARE_COMMENT`, `READ_ACCOUNT_METRICS`…), a sekcja 22.3 (L4347-4359)
   nazw etapowych (`PUBLISH_REPLY`, `REACT_TO_CONTENT`, `READ_THREAD`…). Oba zbiory opisują
   ten sam byt `SemanticCapability`. **Oba są w enumie**, bo oba występują w normatywnych
   fragmentach i oba są cytowane w testach akceptacyjnych z sekcji 26.
   Do rozstrzygnięcia przez człowieka przed F3: czy scalić w jeden słownik z aliasami, czy
   utrzymać rozdział adapter/etap. Nie zmieniałem żadnej nazwy.

2. **`side_effect` i `retry_class` mają w dokumencie tylko po jednej wartości przykładowej**
   (`IRREVERSIBLE`, L2543; `RECONCILE_ONLY`, L2544). Dodałem `NONE`/`REVERSIBLE` oraz
   `SAFE_RETRY`/`NO_RETRY`, bo bez nich nie da się odróżnić capability odczytowej od zapisującej.
   To jest **rozszerzenie ponad dokument** — jedyne w tym pakiecie. Jeżeli ma być inaczej,
   to jest miejsce do poprawki.

3. **Enum wyniku bramki E1 celowo NIE domknięty.** Sekcja 22.11 zna `PASS`/`BLOCK`/`NO_DATA`,
   a 25.2 zna `PASS`/`CONDITIONAL_PASS`/`BLOCK`. Żadne miejsce nie zna wszystkich czterech.
   Domknięcie musi zapaść **przed zamrożeniem progów**, czyli przed startem okna F6 —
   zamrożenie jest nieodwracalne (L4619). Nie ma tu takiego enuma.

4. **`LeaseState` nie jest wyliczony w dokumencie.** Sekcja 14.4 wymienia pole `state`
   (L2366) bez wartości. Wyprowadziłem pięć stanów z reguł tej samej sekcji (L2371-2379).
   Oznaczone w docstringu.

5. **Powody trybu INTERACTIVE: 4 czy 6.** L5161 i L5518 wymieniają cztery
   (`LOGIN`, `MFA`, `CHALLENGE`, `HUMAN_TAKEOVER`), a L4370-4376 sześć operacji.
   Zaimplementowałem pełny enum `InteractiveReason` (6 wartości) + węższą stałą
   `E1_ALLOWED_INTERACTIVE_REASONS` (5 wartości: `LOGIN` z L5161 rozwinięty na
   `FIRST_LOGIN` i `REAUTH`, bo obie to logowanie człowieka przy ekranie).
   `IDENTITY_VERIFICATION` jest w enumie, ale poza listą dopuszczoną w E1.
   **To jest interpretacja** — jeżeli intencją było dopuszczenie sześciu powodów w E1,
   wystarczy dopisać jedną wartość do stałej.

6. **Maszyna `ActionProposal` nie zna wygaśnięcia draftu ani edycji przed zgodą.** *(F1)*
   L2773-2775 rysuje dokładnie pięć strzałek i żadna z nich nie prowadzi z `DRAFT` do
   `EXPIRED`, mimo że L5540 wymaga, żeby „draft po `draft_ttl` przechodził w `EXPIRED`".
   Tak samo `SUPERSEDED` ma w dokumencie **jedno** źródło — `APPROVED` (L2775) — więc
   edycja rewizji, która nie została jeszcze zatwierdzona, nie ma w tabeli reprezentacji.
   **Nie dorysowałem tych strzałek.** Tabela jest transkrypcją, a nie interpretacją;
   dorysowanie przejścia, którego dokument nie ma, jest tą samą operacją co zmiana progu
   bramki po zobaczeniu wyniku. Konsekwencja w kodzie: `ActionProposalRevision.supersede`
   pyta tabelę i odmawia na rewizji w stanie `DRAFT` (`IllegalTransition`), co jest
   sprawdzone testem `test_supersede_obeys_the_action_proposal_transition_table`.
   **Rozstrzygnąć przed E1.F4** — tam powstaje kolejka approvali (L5299) i to ona pierwsza
   będzie musiała wygasić draft. Do tego czasu nic w F1 tego przejścia nie potrzebuje.

7. **`AutomationRun` nie ma w dokumencie własnej tabeli.** *(F1)* L5198 wymienia ten typ,
   ale łańcuch `automation_runs` nie występuje w roadmapie ani razu; model danych zna
   wyłącznie `execution_attempts` (L3129), a maszyna stanów nazywa się `ExecutionAttempt`
   (L2600-2605). **D-020** rozstrzyga to na korzyść typu nad istniejącą tabelą.
   Pomyłka w drugą stronę kosztowałaby migrację na bazie już utworzonej.

## Zależności

Ten pakiet nie zależy od niczego poza `pydantic>=2.12`. Celowo — importuje go prawie wszystko.
`jobs.py` dodatkowo nie zna **żadnego** sterownika bazy: to jest granica, dzięki której
dołożenie brokera w E3 nie rusza komend domenowych (L5206, L5208). Pilnuje tego skan AST
i skan tekstu modułu w `tests/test_job_contracts.py`, nie grep w recepcie Makefile'a.

Czego **inne deliverables mają stąd używać, zamiast pisać własne**:
- `authorize_and_consume()` — jedyna droga do akcji zapisującej (D08, D11, D14);
- `require_transition()` — jedyne sprawdzenie przejścia stanu (D07, D08, control plane F1);
- `require_fresh_job_lease()` — jedyne sprawdzenie fencing tokenu joba (kolejka jobów F1);
- `ProfileLease.verify()` — jedyne sprawdzenie fencing tokenu profilu (D08, D09);
- `ApprovalHashInput.payload_hash()` — jedyna definicja hasha (D07 przechowuje, D19 raportuje);
- `FORBIDDEN_PRACTICES` — jedyne źródło granicy zakresu (D14 egzekwuje, D03 cytuje).

## Jak uruchomić

```bash
cd contracts
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest tests -q -rs
```

`-rs` nie jest ozdobą: „zebrane" to nie „wykonane", a pytest liczy jako zebrane również testy
pominięte przez `skipif`. W podsumowaniu tego pakietu nie ma prawa pojawić się `skipped`.

**Wszystkie testy w tym pakiecie są synchroniczne** — `grep -rn "async def" contracts/tests`
zwraca zero. To nie jest upodobanie: `contracts/.venv` nie ma `pytest-asyncio`, więc `async def`
kończy się tam błędem `async def functions are not natively supported` (pytest wywala taki test
**głośno**, nie pomija go — sprawdzone sondą, patrz korekta w D-021). Wersja interpretera tego
środowiska jest przedmiotem **D-021** i zmienia się poza tym pakietem; nic tutaj nie zależy od
składni nowszej niż `requires-python` z korzenia repo.

Testy w `tests/test_document_conformance.py` czytają `ROADMAP_CODEX_CDP_COMMUNITY_OS.md`
i porównują wartości enumów oraz cytaty granicy zakresu z jego treścią. Jeżeli dokument się
zmieni, te testy padną — to jest zamierzone.

## Testy, które widziano padające

Żaden test w tym pakiecie nie jest przyjmowany na słowo. Każdy z poniższych uruchomiono
na kopii pakietu z wprowadzoną usterką i zobaczono, jak pada:

| Usterka wprowadzona celowo | Test, który ją złapał |
| --- | --- |
| `supersede` zostawia poprzednią rewizję w `APPROVED` | `test_a_superseded_revision_is_no_longer_executable` |
| `authorize_side_effect` przestaje sprawdzać stan rewizji | `test_a_superseded_revision_is_no_longer_executable` |
| edycja liczy TTL od nowa | `test_an_edit_does_not_extend_the_original_ttl` |
| `authorize_and_consume` znów zwraca `None` | `test_authorization_returns_a_proof_not_none` |
| `jobs.py` wspomina sterownik bazy | `test_job_transport_port_is_a_protocol_without_a_driver_import` |
| port dostaje metodę wydającą połączenie | `test_job_transport_port_is_a_protocol_without_a_driver_import` |
| token fencingowy traci `lease_owner` | `test_job_fencing_token_carries_all_four_fields` |
| z tabeli znika jedno przejście z dokumentu | `test_the_allowed_set_equals_the_document` |
| do tabeli dochodzi retry po `UNKNOWN` | `test_the_table_agrees_with_states_forbidding_automatic_retry` |
| `AutomationRun` przestaje być frozen | `test_automation_run_is_frozen_and_carries_the_payload_hash` |
| maszyna wypada z iloczynu kartezjańskiego | `test_all_six_machines_are_covered_by_every_mapping` |
| maszyna wypada z ręcznej transkrypcji dokumentu | `test_all_six_machines_are_covered_by_every_mapping` |

Dwa ostatnie wiersze są powodem, dla którego `EXPECTED_CASE_COUNT` jest **literałem 257**,
a nie sumą liczoną z tego samego słownika, z którego budowany jest iloczyn. W wersji liczonej
usunięcie maszyny `ActionProposal` ze słownika dawało `230 passed` i zero ostrzeżeń — 36
przypadków przestawało istnieć, a zbiór oczekiwany kurczył się razem z nimi.
