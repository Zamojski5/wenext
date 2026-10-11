# Community Marketing Autopilot OS — plan produktu i roadmapa V4

<!-- markdownlint-disable MD013 -->

> Status: plan architektoniczno-produktowy po czwartej rewizji; dokument podzielony na etapy  
> Data: 2026-08-29  
> Wariant: wiele izolowanych kont i zasobów marketingowych, autonomiczny Workspace Principal jako orkiestrator workspace'u, trwała wiedza w PostgreSQL, headless Chrome sterowany przez CDP jako warstwa wykonania, interaktywny widok przeglądarki wyłącznie dla logowania i takeover, Codex logowany kontem ChatGPT — bez własnych wywołań OpenAI API  
> Główna zmiana wobec V3: cały zakres jest rozłożony na pięć etapów, a **Etap 1 jest bramką pass/block całego projektu**. Etap 1 dowodzi, że potrafimy znajdować w community dyskusje zyskujące realne zaufanie i zasięg oraz zbudować konto od zera, które faktycznie uczestniczy w danej społeczności — przy czym każda akcja na zewnątrz wykonywana jest po akceptacji człowieka. Bez zaliczenia Etapu 1 nie budujemy żadnego z dalszych etapów.  
> Zakres systemu pozostaje bez zmian: workspace'y, orkiestrator, dobór mniejszych modeli do pracy, zbieranie danych i wykonanie przez CDP, plan, pomiar i replanning. Zmienia się wyłącznie kolejność dowodzenia i moment, w którym każda warstwa powstaje.

## 1. Decyzja produktowa

Budujemy **AI-assisted Marketing Operations OS**, a nie prosty panel do uruchamiania browser jobów.

Produkt ma pozwalać operatorowi:

- połączyć jedno konto Codex/ChatGPT jako warstwę rozumowania,
- dodać wiele kont i zasobów marketingowych: profile społecznościowe, fora, repozytoria, analytics, business profiles oraz konta reklamowe,
- zachować osobny, trwały profil Chromium dla każdego konta,
- logować się w interaktywnym widoku przeglądarki osadzonym bezpośrednio w dashboardzie,
- tworzyć workspace'y odpowiadające produktom lub klientom,
- przeciągać dostępne konta, zasoby i logicznych AI workerów do wybranego workspace'u,
- dostarczyć README, repozytorium, dokumentację, stronę lub materiały, bez konieczności samodzielnego pisania strategii marketingowej,
- pozwolić Workspace Principalowi zbudować diagnozę, wybrać kanały i przygotować wersjonowany plan wejścia na rynek,
- otrzymywać od AI uzasadnione wymagania typu „połącz konto Hacker News”, „zaloguj Facebook”, „zatwierdź budżet Google Ads” lub „dodaj brakujący materiał”,
- prowadzić kilka torów pracy równolegle, np. research, rozwój sieci, community participation i content,
- zamieniać plan na milestones, cele dzienne, zadania oraz kolejkę Kanban,
- wykonywać zadania kolejno w granicach budżetów, capabilities i approvali,
- stale mierzyć realizację, sentyment, reakcje i sygnały rynkowe oraz proponować korekty planu,
- pokazywać alerty i decyzje z opcjami `Approve`, `Edit`, `Reject`, `Snooze`, `Ask AI` oraz `Resolve with AI`,
- zatrzymywać, wznawiać albo zmieniać plan bez utraty historii,
- dobrać inny marketing mix dla projektu open source, B2B SaaS, e-commerce albo lokalnej restauracji,
- przejść od rozpoznania rynku do promocji, pozyskania użytkowników i uczenia się z wyników.

Główny loop produktu:

~~~text
PROJECT SOURCES AND BUSINESS GOAL
    ↓
POSTGRES WORKSPACE INTELLIGENCE
    ↓
SOL WORKSPACE PRINCIPAL: DIAGNOSIS
    ↓
CHANNEL HYPOTHESES + RESOURCE REQUIREMENTS
    ↓
HUMAN DECISIONS AND CONNECTIONS
    ↓
VERSIONED, MUTABLE MARKETING PLAN
    ↓
PARALLEL WORKSTREAMS + DAILY GOALS + KANBAN
    ↓
LUNA/TERRA EXECUTION + VERIFICATION
    ↓
APPROVAL / AUTHORIZED BROWSER EXECUTION
    ↓
METRICS + COMMUNITY SIGNALS + ALERTS
    ↓
SOL REVIEW + HUMAN-GATED REPLANNING
~~~

Ten loop jest obrazem docelowym, nie zakresem pierwszego wydania. Kolejność, w jakiej powstają jego części, opisuje sekcja 21 „Mapa etapów produktu”. Etap 1 realizuje wyłącznie wycinek: research kanału, wejście w community, wkład zatwierdzany przez człowieka i pomiar jego skutków. Reszta loopu — plan, równoległe workstreamy, budżety, replanning — powstaje dopiero po zaliczeniu bramki Etapu 1.

## 2. Najważniejsze rozdzielenie domeny

~~~text
CodexConnection
    = konto ChatGPT/Codex i dostępne modele

AIWorkerRole
    = logiczna rola modelu, np. Planner, Researcher, Writer, Verifier

SocialAccount
    = publiczne konto osoby albo marki na dowolnej obsługiwanej platformie

ConnectedResource
    = szerszy zasób marketingowy: SocialAccount, ForumAccount, AdAccount,
      BusinessProfile, AnalyticsConnection albo RepositoryConnection

BrowserProfile
    = trwały katalog Chromium przypisany 1:1 do SocialAccount

Workspace
    = jeden produkt, marka albo klient

WorkspacePrincipal
    = logiczna rola Sol odpowiedzialna za diagnozę, plan, delegowanie i review;
      stan tej roli znajduje się w Postgresie, a nie w długowiecznym wątku modelu

WorkspaceIntelligenceProfile
    = wersjonowana, ustrukturyzowana wiedza o projekcie, odbiorcach,
      pozycjonowaniu, głosie, dowodach i ograniczeniach

TargetDefinition
    = wersjonowana odpowiedź na pytania gdzie działamy, do kogo mówimy
      i jakimi motions promujemy; ustala ją orkiestrator, zatwierdza człowiek

ProductStage
    = etap programu E1–E5 (sekcja 21); poziom nadrzędny wobec
      PlanStageInstance, awansowany wyłącznie przez evidence i decyzję człowieka

ResourceRequirement
    = uzasadniona potrzeba zasobu, dostępu, budżetu, materiału albo decyzji człowieka

Alert
    = trwały sygnał wymagający uwagi lub obserwacji

DecisionProposal
    = wersjonowana propozycja decyzji AI z opcjami, dowodami i skutkami

WorkspaceAccountAssignment
    = wyspecjalizowane przypisanie konta browserowego do workspace'u

WorkspaceResourceAssignment
    = generyczne, wersjonowane authority dla dowolnego ConnectedResource

TaskResourceBinding
    = powiązanie taska z dokładną wersją każdego wymaganego assignmentu

MarketingPlan
    = aktywna strategia workspace'u

Workstream
    = równoległy tor realizacji planu

Goal
    = wynik oczekiwany w określonym horyzoncie

DailyCommitment
    = kontrolowalna ilość pracy przyjęta na konkretny dzień

Task
    = jednostka pracy produktowej

ActionProposal
    = dokładna propozycja zewnętrznego działania

ExecutionIntent
    = jednorazowa zgoda na wykonanie konkretnej rewizji

AutomationRun
    = techniczna próba wykonania
~~~

Konto społecznościowe nie jest AI workerem. Model może planować i wykonywać zadania dla konta, ale sam profil, tożsamość i globalne limity konta pozostają niezależnymi zasobami.

## 3. Docelowy model organizacji

~~~text
Organization
│
├── Users
│
├── Codex Connections
│
├── Connected Resource Pool
│   ├── LinkedIn Jan
│   │   └── BrowserProfile A
│   ├── Reddit Jan
│   │   └── BrowserProfile B
│   ├── Hacker News Jan
│   │   └── BrowserProfile C
│   ├── Google Ads / Meta Ads
│   ├── Google Business Profile
│   ├── GitHub Organization
│   └── Analytics Connection
│
└── Workspaces
    ├── Acme
    │   ├── Workspace Principal
    │   ├── Project Sources and Ingestion
    │   ├── Project Definition
    │   ├── Intelligence Profile
    │   ├── Claims and Evidence
    │   ├── Postgres Knowledge and Memory
    │   ├── Channel Hypotheses
    │   ├── Resource Requirements
    │   ├── Marketing Plan
    │   ├── Assigned Resources and Workers
    │   ├── Parallel Workstreams
    │   ├── Goals and Milestones
    │   ├── Daily Commitments
    │   ├── Kanban
    │   ├── Alerts and Decisions
    │   ├── Publications
    │   └── Measurements
    │
    └── Inny produkt
~~~

### 3.1. Niezmienniki domenowe

1. `SocialAccount` istnieje tylko raz w Connected Resource Pool.
2. `SocialAccount` i `BrowserProfile` mają relację 1:1.
3. Przeciągnięcie konta do workspace'u tworzy assignment; nie kopiuje ani nie przenosi profilu.
4. Odpięcie konta od workspace'u nie usuwa konta ani profilu.
5. Jedno konto może mieć kilka assignmentów, ale tylko jedną aktywną sesję przeglądarki.
6. Zadania różnych workspace'ów korzystające z tego samego konta są serializowane.
7. Globalny budżet konta jest nadrzędny wobec budżetów workspace'ów.
8. Task browserowy wskazuje immutable wersję `WorkspaceAccountAssignment` przez `TaskResourceBinding`, a każdy task wskazuje komplet bindingów do dokładnych wersji wszystkich wymaganych zasobów.
9. Backend, a nie model, wybiera konto dla danego runa.
10. Każda akcja zapisująca ponownie sprawdza faktycznie zalogowaną tożsamość.
11. Workspace ma najwyżej jedną aktywną wersję `WorkspaceIntelligenceProfile` i planu; workspace w stanie `ACTIVE` wymaga dokładnie jednej aktywnej wersji obu.
12. Historyczne taski zachowują wersje planu, Intelligence Profile, promptu i snapshotu wiedzy użytego podczas wykonania.
13. Model proponuje trwałe zmiany; aktywacja podlega polityce autonomii i approvalom.
14. Approval dotyczy dokładnej, niezmiennej rewizji akcji.
15. Zmiana payloadu po approvalu unieważnia zgodę.
16. Plan jest wersjonowany; replanning nie mutuje starego planu w miejscu.
17. Metryki są append-only i posiadają źródło, czas oraz confidence.
18. Nieznany wynik zewnętrznego submitu nigdy nie wywołuje automatycznego ponowienia.
19. Stage może trwać wiele dni, ale każdy dzienny plan ma skończony budżet i warunki zatrzymania.
20. Kanban jest projekcją state machines, a nie dowolnie edytowaną kolumną w bazie.
21. Stabilny assignment posiada immutable wersje konfiguracji; task zawsze przypina dokładną wersję.
22. Aktywacja nowej wersji Intelligence Profile, projektu albo planu i supersede starej odbywają się w jednej transakcji.
23. Wiedza workspace'u nie ma plikowego źródła prawdy; kanoniczne dokumenty, fakty, pamięć, decyzje i snapshoty kontekstu są wersjonowane w PostgreSQL.
24. Model nie otrzymuje ogólnego połączenia SQL. Czyta wyłącznie workspace-scoped read model przez Domain Data Gateway/MCP, a zapisuje tylko propozycje przez jawne komendy domenowe.
25. `ResourceRequirement` nie nadaje sam sobie uprawnień. Zasób staje się dostępny dopiero po połączeniu przez człowieka, weryfikacji i przypisaniu.
26. Alert ani propozycja Sol nie mutują aktywnego planu. Zmiana operacyjna musi mieścić się w zatwierdzonym envelope, a zmiana strukturalna wymaga `DecisionReview`.
27. Każdy aktywny profil przeglądarki ma osobny katalog danych i osobny proces Chromium; dwa konta tej samej platformy nigdy nie współdzielą procesu ani `BrowserContext`.
28. PostgreSQL jest źródłem prawdy dla tasków. RabbitMQ jest trwałym transportem `job_id`, nigdy jedynym miejscem istnienia pracy; wchodzi w E3, a do tego czasu tę samą rolę pełni PostgreSQL za `JobTransportPort`.
29. `WorkspaceResourceAssignment` jest generycznym authority; account assignment jest jego wyspecjalizowanym rozszerzeniem. Allocation zawsze wskazuje aktywną wersję assignmentu.
30. Capability odczytu jest funkcją połączonego konta i jego stanu, a nie właściwością platformy. Capability w stanie `SUPPORTED` bez wskazanego konta istnieje wyłącznie dla surface z `access_mode = PUBLIC_API_NO_ACCOUNT` albo `PUBLIC_WEB_NO_ACCOUNT`.
31. Discovery i search na `ChannelSurface` z `access_mode = ACCOUNT_REQUIRED` albo `MEMBERSHIP_REQUIRED` wymagają aktywnej wersji `WorkspaceResourceAssignment` konta do tego surface. Brak assignmentu oznacza brak capability, a nie niższą jakość danych.
32. Utrata sesji, challenge albo niezgodność tożsamości degradują capabilities tego konta i zatrzymują zależne taski, także czysto odczytowe.
33. Budżet odczytu jest budżetem konta i podlega tej samej hierarchii co akcje zapisujące. Odczyt zużywa sesję, rate limit i reputację konta.
34. Brak konta albo członkostwa dla community tworzy `ResourceRequirement`. Plan nie planuje obserwacji surface, do którego nie ma zasobu.
35. `capsule_mode = HEADLESS` jest trybem domyślnym. `INTERACTIVE` wymaga jednego z zapisanych powodów (`FIRST_LOGIN`, `REAUTH`, `MFA`, `CHALLENGE`, `HUMAN_TAKEOVER`, `IDENTITY_VERIFICATION`) i wygasa razem z HUMAN `InteractionLease`.
36. Konta zakłada i loguje człowiek. System nie tworzy tożsamości, a hasło i drugi składnik uwierzytelnienia nigdy nie trafiają do modelu.
37. Surowa odpowiedź źródła jest immutable evidence. Reparsowanie nowszym parserem działa na zapisanym blobie i nie zużywa budżetu odczytu ani sesji konta.
38. Etapy produktu E1–E5 są sekwencyjne pod względem dowodu. Awans etapu wymaga evidence i zgody człowieka; upływ czasu, zamknięty sprint ani skończona faza roadmapy nie są dowodem.
39. Etap 1 jest bramką `PASS/BLOCK` całego programu. `BLOCK` zatrzymuje budowę E2–E5.
40. Wynik etapu, który wymaga generalizacji, jest ważny wyłącznie wtedy, gdy powstał na wymaganej liczbie różnych `ChannelArchetype`; dla Etapu 1 są to co najmniej dwa.
41. Do zaliczenia Etapu 1 nie istnieje mapowanie `execution_mode = AUTOMATED` dla capability zapisującej. Każda akcja zewnętrzna ma `ActionProposal`, zgodę człowieka na dokładną rewizję i jednorazowy `ExecutionIntent`.
42. Progi bramki etapu są zapisane w wersji planu przed startem okna pomiarowego. Zmiana progu tworzy nową wersję planu i unieważnia bieżący wynik etapu.

Niezmienniki 1–29 obowiązują w każdym etapie. Niezmienniki 30–42 dotyczą warstwy zbierania danych, trybu przeglądarki i etapowania; ich rozwinięcie znajduje się w sekcjach 21, 22 i 24.

## 4. Granica zakresu

Roadmapa obejmuje sterowanie przeglądarką, utrzymywanie zalogowanych sesji, planowanie marketingowe, research, przygotowywanie treści, pomiar i kontrolowane działania.

Nie obejmuje:

- omijania CAPTCHA albo MFA,
- spoofingu fingerprintu i mechanizmów ukrywania automatyzacji,
- obchodzenia blokad lub technicznych rate limitów,
- kopiowania cookies między profilami,
- automatycznej rotacji proxy w celu obejścia zabezpieczeń,
- automatycznego tworzenia kont lub fałszywych tożsamości,
- koordynowanego podbijania tej samej treści przez kilka własnych kont,
- automatycznego retry po niejednoznacznym submit,
- nieograniczonych pętli wykonujących akcje aż pojawi się wynik zależny od innych ludzi.

Challenge, blokada, MFA, nieznany ekran albo niezgodność zalogowanej tożsamości kończą się kontrolowanym zatrzymaniem.

## 5. Connected Resource Pool i przypisywanie zasobów

### 5.1. Account Inventory

Główny katalog pokazuje wszystkie połączone zasoby organizacji niezależnie od workspace'u. `SocialAccount` jest najważniejszym typem dla browser automation, ale nie jedynym. Ten sam pool może zawierać `ForumAccount`, `AdAccount`, `BusinessProfile`, `RepositoryConnection`, `AnalyticsConnection` i inne typy zarejestrowane w `ResourceTypeRegistry`.

Przykładowa karta:

~~~text
Jan, LinkedIn
Account: READY
Profile: AVAILABLE
Runner: local-runner-01
Browser: Chrome for Testing 147.x / pinned build
Capabilities: read, metrics, draft, approved actions
Assigned workspaces: Acme
Allocation: 70%
Today: 4/10 planned actions
Connections: 184
Last metrics snapshot: 22 min ago
Queue: 3
~~~

Filtry:

- platforma,
- workspace,
- stan konta i profilu,
- dostępne capabilities,
- aktualny runner,
- aktywny lease,
- dzienny budżet,
- wymagane logowanie lub takeover.

Operacje dla zasobu z profilem przeglądarki:

- `Przypisz do workspace`,
- `Otwórz interaktywnie`,
- `Sprawdź sesję`,
- `Wstrzymaj`,
- `Wznów`,
- `Odłącz`,
- kontrolowana migracja do innego runnera w późniejszej wersji.

### 5.2. Drag-and-drop kont, zasobów i workerów do workspace'u

~~~text
Connected Resource Pool           Acme — Assigned Accounts
─────────────────────             ───────────────────────────
LinkedIn Jan     ───────────────►  FOUNDER / NETWORK
Reddit Jan       ───────────────►  COMMUNITY CONTRIBUTOR
Reddit Acme   ───────────────►  BRAND PUBLISHER

Other Resources                   Acme — Assigned Resources
─────────────────────             ─────────────────────────────
GitHub Acme   ───────────────►  REPOSITORY / CONVERSION
Plausible        ───────────────►  ANALYTICS / ATTRIBUTION
~~~

Drop nie tworzy od razu aktywnego przypisania. Otwiera drawer:

~~~text
Role
Allowed workstreams
Allowed action types
Allocation share
Scheduler fairness weight
Capacity minutes per window
Daily workspace budgets
Approval policy
Working hours and timezone
Assignment priority
Valid from / until
~~~

Flow:

~~~text
DRAG CONNECTED RESOURCE
    ↓
WORKSPACE RESOURCE ASSIGNMENT PROPOSAL
    ↓
VALIDATION
- organization
- ownership
- resource health
- capabilities
- available capacity
- conflicts
- approval policy
    ↓
USER CONFIRMATION
    ↓
ACTIVE ASSIGNMENT
~~~

State machine proposal:

~~~text
WorkspaceResourceAssignmentProposal:
PROPOSED
  → VALIDATING
  → PENDING_CONFIRMATION
  → CONFIRMED

VALIDATING → BLOCKED
BLOCKED → VALIDATING
~~~

To jest kanoniczny parent flow dla konta, repozytorium, analytics, profilu firmy, ad accountu i budżetu. `WorkspaceAccountAssignmentProposal` jest opcjonalnym rozszerzeniem 1:1 parent proposalu i dodaje tylko identity/profile health, approval właściciela oraz reguły działań browserowych. Potwierdzenie tworzy najpierw generyczny `WorkspaceResourceAssignment`; dla konta w tej samej transakcji powstaje jego rozszerzenie `WorkspaceAccountAssignment`.

`DRAINING` blokuje nowe taski i nowe `ExecutionIntent`. Read-only run może się zakończyć, `PREPARED/DISPATCHED` jest anulowany, a `SUBMIT_STARTED` może już tylko przejść przez reconciliation. Po braku aktywnych runów assignment przechodzi do `DETACHED`.

Assignment ma stabilną tożsamość i osobne immutable wersje konfiguracji:

~~~text
WorkspaceResourceAssignment
- lifecycle: ACTIVE ↔ SUSPENDED → DRAINING → DETACHED

WorkspaceResourceAssignmentVersion
- role
- authorization policy
- allocation share
- scheduler weight
- capacity
- approval policy
- valid from

WorkspaceAccountAssignment / Version
- rozszerzenie 1:1 bez własnego lifecycle'u
- social account + browser profile binding
- owner consent/disclosure/identity constraints
- browser eligibility: ELIGIBLE | REAUTH_REQUIRED | QUARANTINED
~~~

Zmiana authority/capacity tworzy nową wersję parenta. Account extension może wyłącznie zawęzić parent przez wymagania identity, consent, disclosure i browser eligibility; effective authorization jest przecięciem parent policy i account constraints. Nigdy nie rozszerza allowed actions, capacity, budget ani approval policy. `lock_version` służy wyłącznie do optimistic concurrency i nie zastępuje historii.

### 5.3. Wiele workspace'ów na jednym koncie

Model danych pozostaje N:M, ale do E5 obowiązuje konserwatywna polityka:

- jedno konto może być primary write identity tylko dla jednego workspace'u naraz,
- assignment `RESEARCH_ONLY` może obsługiwać kilka workspace'ów,
- rozszerzenie na kilka write assignmentów wymaga jawnego podziału capacity i conflict check,
- `allocation_share_bps` w parent `WorkspaceResourceAssignmentVersion` jest twardym udziałem i sumuje się maksymalnie do 10 000,
- `fairness_weight` jest względną wagą schedulera i nie sumuje się do 100%,
- `capacity_minutes_per_window` oraz action budgets są osobnymi limitami,
- scheduler stosuje weighted fairness,
- profil zawsze zachowuje concurrency równą 1.

Zweryfikowane konto ma constraint:

~~~text
UNIQUE(organization_id, platform, external_account_id)
~~~

Jeżeli identity verifier po nowym loginie rozpozna istniejące konto, provisional profile nie przechodzi do `READY` i trafia do kontrolowanego usunięcia albo kwarantanny.

### 5.4. Flow „Dodaj konto”

~~~text
Connected Resource Pool
    ↓
[Dodaj konto]
    ↓
wybór platformy i nazwy roboczej
    ↓
utworzenie PROVISIONAL SocialAccount i BrowserProfile
    ↓
lease + osobny proces Chrome for Testing na wirtualnym displayu
    ↓
krótkotrwały Browser Stream Token
    ↓
interaktywny ekran Chrome w dashboardzie
    ↓
ręczne logowanie i MFA bez przekazywania hasła do AI
    ↓
AccountOnboardingAdapter:
- verify session,
- verify external identity,
- duplicate check,
- probe capabilities,
- collect baseline metrics,
- collect account age, visible reputation/network, memberships and profile completeness,
- detect challenge/block
    ↓
graceful browser close
    ↓
testowy ponowny start na tym samym profilu i executable
    ↓
ponowna identity verification
    ↓
revoke stream/input → stop całej kapsuły → fsync/unmount profilu
    ↓
potwierdzony ProfileLease release
    ↓
Account READY / REAUTH_REQUIRED / QUARANTINED
~~~

Konto w stanie `READY` nie oznacza stale uruchomionej przeglądarki. Profil pozostaje `AVAILABLE` na dysku, aż scheduler przydzieli następny task.

Domyślnym trybem pracy jest headless Chrome sterowany przez CDP; szczegóły i konsekwencje opisuje sekcja 24. Tryb headful na wirtualnym displayu pozostaje wyłącznie ścieżką wyjątkową: pierwsze logowanie, MFA, challenge, weryfikacja tożsamości i human takeover. Technicznie streamowany browser nie działa jako `--headless` — headless nie pokazuje okna — dlatego przejście w tryb interaktywny jest jawną zmianą trybu kapsuły na tym samym profilu, a nie stanem domyślnym.

### 5.5. Osobny AI Worker Pool

UI pokazuje dwa różne zasoby:

~~~text
CONNECTED RESOURCE POOL
- publiczne tożsamości, profile browsera i pozostałe połączenia marketingowe

AI WORKER POOL
- logiczne role wykonawcze i ich model routing policy
~~~

Przykładowi workers:

~~~text
Market Researcher
Community Analyst
Opportunity Scout
Daily Planner
Draft Writer
Policy and Claim Verifier
Result Verifier
Performance Analyst
Plan Critic
~~~

Worker może być przypisany do wielu workspace'ów i workstreamów. Assignment workera określa rolę, dozwolone task types, routing policy, dzienny quota envelope, WIP i priority. Nie daje mu publicznej tożsamości — task wymagający platformy musi niezależnie otrzymać `WorkspaceAccountAssignment`.

`WorkspacePrincipal` nie należy do współdzielonego Worker Pool. Jest pojedynczym, wersjonowanym agregatem należącym do konkretnego workspace'u. Współdzielone są wyłącznie definicja roli, prompt, routing policy i evale; instancja Principala, jej generation, event cursor, authority grants i historia runów zawsze pozostają workspace-scoped.

### 5.6. Dynamiczne wymagania zasobów

Użytkownik nie musi z góry wiedzieć, jakie konta połączyć. Po pierwszej diagnozie Workspace Principal tworzy `ResourceRequirement`:

~~~text
SOCIAL_ACCOUNT
FORUM_ACCOUNT
AD_ACCOUNT
BUSINESS_PROFILE
ANALYTICS_CONNECTION
REPOSITORY_CONNECTION
BUDGET
CONTENT_ASSET
COMMUNITY_MEMBERSHIP
HUMAN_DECISION
~~~

Każde wymaganie zawiera:

~~~text
workspace_id
type
platform_or_channel_hypothesis
required_capabilities
rationale
evidence_refs
required_by_workstream
required_by_date
priority
blocking_level
acceptable_alternatives
status
~~~

Lifecycle potrzeby jest jawny i nie miesza jej z przydziałem ani rezerwacją:

~~~text
ResourceRequirement:
DRAFT → OPEN → MATCHING
MATCHING → WAITING_DECISION | RESERVED | SATISFIED | UNSATISFIED
WAITING_DECISION → MATCHING | REJECTED | WAIVED
RESERVED → SATISFIED | MATCHING
SATISFIED → DEGRADED → MATCHING
SATISFIED → RELEASED
DRAFT/OPEN/MATCHING/WAITING_DECISION/RESERVED → CANCELLED | EXPIRED
~~~

`UNSATISFIED` jest terminalne dla bieżącej wersji i tworzy jeden deduplikowany alert. Nowy dowód, nowy zasób albo zmiana ograniczeń tworzą następną wersję requirementu i dopiero ona wraca do `OPEN`; scheduler nie wykonuje bez końca tego samego matchingu.

Dashboard renderuje działanie odpowiednie do typu: `Połącz konto`, `Zaloguj`, `Dołącz do grupy`, `Zatwierdź budżet`, `Dodaj materiał` albo `Podejmij decyzję`. Przykład:

~~~text
[HIGH] Acme potrzebuje konta Hacker News
Powód: istotna koncentracja platform engineers i founderów.
Potrzebne możliwości: odczyt front page, profile baseline, ręczne komentarze.
Wpływ bez zasobu: workstream HN pozostaje OBSERVE_EXTERNAL.
[Połącz konto] [Wybierz istniejące] [Odrzuć rekomendację] [Zapytaj Sol]
~~~

Po połączeniu `AccountOnboardingAdapter` nie tylko sprawdza login. Tworzy wersjonowany `AccountAssessment`:

~~~text
identity confidence
account age or UNKNOWN
visible karma/reputation/network/followers
profile completeness
prior activity and memberships
observable warnings or challenges
capability snapshot
measurement quality and freshness
recommended readiness stage
~~~

Principal wykorzystuje ten baseline do ułożenia innego toru dla każdego konta. Nowe konto Hacker News może dostać `OBSERVE_ONLY`, konto Reddit z historią — `VISIBILITY_BUILDING`, a dojrzały profil LinkedIn — `SOFT_PARTICIPATION`. Sam wiek ani karma nie odblokowują automatycznie kampanii.

### 5.7. Requirement, allocation, assignment i lease to różne rzeczy

~~~text
ResourceRequirement = czego plan potrzebuje
ResourceAllocation  = jaki konkretny zasób wybrano
WorkspaceResourceAssignment = trwałe, wersjonowane uprawnienie dowolnego zasobu w workspace
CapacityReservation = czasowa rezerwacja części capacity/budżetu
ProfileLease        = techniczna wyłączność aktywnej sesji
~~~

`ResourceAllocator` najpierw szuka już podłączonych kandydatów po semantic capabilities, ownership, health, conflict status, capacity i aktualności snapshotu. Binding mieszczący się w istniejącym assignment/envelope może zostać zaproponowany automatycznie. Nowe konto, nowy assignment, zwiększenie budżetu lub konflikt zawsze tworzą `DecisionProposal`.

Brak kandydata kończy requirement jako `UNSATISFIED` i tworzy deduplikowany `RESOURCE_GAP`; nie uruchamia nieskończonego rematchingu. Po utracie capability allocation przechodzi do `DEGRADED`, taski są zatrzymywane, a Principal dostaje zdarzenie do ponownego planowania.

Pozostałe lifecycle'y:

~~~text
ResourceAllocation:
PROPOSED → VALIDATING → ACTIVE
VALIDATING → REJECTED | FAILED
ACTIVE → DEGRADED | SUSPENDED | RELEASING
DEGRADED → VALIDATING | RELEASING
SUSPENDED → VALIDATING | RELEASING
RELEASING → RELEASED

CapacityReservation:
REQUESTED → HELD → CONSUMED
HELD → RELEASED | EXPIRED
REQUESTED → REJECTED | CANCELLED

WorkspaceResourceAssignment:
PROPOSED → ACTIVE → DRAINING → DETACHED
ACTIVE → SUSPENDED → ACTIVE | DRAINING
~~~

`WorkspaceAccountAssignment` jest restrykcyjnym rozszerzeniem 1:1 generycznego assignmentu i dodaje wyłącznie reguły tożsamości/browsera; nie tworzy równoległego authority. Każda alokacja wskazuje immutable `workspace_resource_assignment_version_id`. Każda rezerwacja zawiera `quantity`, `unit`, `held_until`, `released_at` oraz rosnącą `generation`; consume/release jest wykonywane CAS-em po `(reservation_id, generation, state=HELD)`. Task otrzymuje `TaskResourceBinding` tylko do konkretnej wersji alokacji, z której jednoznacznie wyprowadza assignment oraz reservation — samo istnienie `ResourceHandle` lub requirementu nigdy nie daje authority.

## 6. Postgres-backed Workspace Intelligence

Workspace nie jest pustym folderem ani katalogiem z `SOUL.md`. Po utworzeniu przechodzi ingest źródeł, analizę i onboarding. Kanoniczna wiedza, plan, pamięć, alerty i decyzje żyją w PostgreSQL; model otrzymuje tylko potrzebny, niezmienny snapshot danych.

### 6.1. Lifecycle workspace'u

~~~text
DRAFT
  → ONBOARDING
  → READY
  → ACTIVE

ACTIVE → PAUSING → PAUSED
PAUSED → ACTIVE
ACTIVE/PAUSED → ARCHIVED
~~~

`Emergency stop` ustawia nadrzędny `execution_freeze` i inicjuje kontrolowane zatrzymanie; nie omija state machine ani reconciliation.

Plan i workspace są synchronizowane atomowymi komendami:

~~~text
StartPlan:
Workspace READY + Plan APPROVED
→ Workspace ACTIVE + Plan ACTIVE

PauseAndDrain:
Workspace ACTIVE + Plan ACTIVE
→ Workspace PAUSING + Plan PAUSED
→ Workspace PAUSED po drain

ResumePlan:
Workspace PAUSED + Plan PAUSED
→ Workspace ACTIVE + Plan ACTIVE

CancelPlan:
Plan ACTIVE/PAUSED → CANCELLED
Workspace → READY, jeżeli może przyjąć nowy plan,
albo PAUSED, jeżeli pozostaje execution freeze
~~~

Nie istnieje stan, w którym aktywny workspace nie ma aktywnego planu.

### 6.2. Minimalny input użytkownika i Project Definition

Produkt jest projektowany dla osoby, która nie musi znać się na marketingu. Minimalny input:

~~~text
nazwa projektu lub firmy
główny cel biznesowy
repozytorium / README / strona / dokumenty / materiały
rynek lub lokalizacja, jeśli ma znaczenie
opcjonalny budżet i deadline
~~~

System sam wyprowadza i zapisuje `ProjectDefinitionVersion`:

~~~text
product_name
product_summary
product_stage
business_model
offer_and_pricing
geography_and_service_area
public_urls
target_audiences
jobs_to_be_done
problems_solved
alternatives
differentiators
proof_assets
primary_conversion_events
success_definition
available_budget
constraints
forbidden_topics
open_questions
~~~

AI oznacza każde pole jako `EXTRACTED`, `INFERRED`, `USER_CONFIRMED` albo `UNKNOWN`. Użytkownik nie wypełnia długiego formularza; dostaje krótką listę tylko tych braków, które istotnie zmieniają strategię. Przed aktywacją pierwszego planu zatwierdza wersję lub deleguje pola niskiego ryzyka w autonomy envelope.

### 6.3. Ingest źródeł do PostgreSQL

README i pliki są wejściem, nie trwałą pamięcią modelu. Pipeline:

~~~text
UPLOAD / REPOSITORY / URL / MANUAL NOTE
    ↓
KnowledgeSource + immutable SourceVersion
    ↓
text extraction + checksum + provenance
    ↓
workspace-scoped KnowledgeDocumentVersion
    ↓
chunks + extracted facts + entities + claims candidates
    ↓
deduplication + conflict detection
    ↓
approved canonical knowledge or unresolved hypothesis
~~~

W etapach E1–E2 również treść źródłowa może być przechowywana w Postgresie (`text`/`bytea`) z limitem rozmiaru. Później duże binaria mogą trafić do S3/MinIO, ale rekord, checksum, wersja, retencja, ACL i status pozostają w PostgreSQL. Żaden worker nie traktuje katalogu roboczego, README ani wygenerowanego pliku jako źródła prawdy.

Zmiana repozytorium albo ponowny upload tworzą nową wersję. Usunięcie źródła unieważnia powiązane chunki, embeddings i claim evidence zgodnie z retencją; nie zostawia ukrytej „pamięci w promptach”.

### 6.4. Workspace Intelligence Profile zamiast `SOUL.md`

Idea „SOUL” zostaje jako domenowa, wersjonowana struktura `WorkspaceIntelligenceProfileVersion`, nie jako plik pisany ręcznie:

~~~text
mission
market_archetype_hypotheses
value_proposition
point_of_view
audience_models
positioning
identity_and_disclosure
voice_traits
tone_examples
words_to_prefer
words_to_avoid
approved_ctas
decision_principles
risk_tolerance
prohibited_topics
prohibited_claims
required_human_decisions
~~~

State machine:

~~~text
DRAFT → IN_REVIEW → APPROVED → ACTIVE → SUPERSEDED → RETIRED
~~~

Principal i Terra przygotowują profil z danych. Człowiek może edytować wynik lub zaakceptować go jednym diffem, ale nie musi sam pisać dokumentu od zera.

### 6.5. Claims and Evidence

Claim nie może być tylko tekstem w promptcie:

~~~text
claim
status: UNVERIFIED | VERIFIED | EXPIRED | RETRACTED
evidence_source_version_ids
valid_from
valid_until
allowed_channels
notes
~~~

Draft nie może wykorzystywać claimu `UNVERIFIED` jako faktu. Verifier sprawdza claimy względem dokładnego snapshotu źródeł.

### 6.6. Pamięć workspace'u

Wszystkie rodzaje pamięci są rekordami PostgreSQL:

~~~text
Canonical Memory  — zweryfikowane fakty o produkcie i rynku
Episodic Memory   — wykonane zadania, publikacje i wyniki
Preference Memory — edycje, odrzucenia i styl operatora
Community Memory  — reguły, język, tematy i zachowania kanału
Strategy Memory   — aktywne hipotezy, decyzje i ich uzasadnienia
Learned Insights  — wnioski z danych, które nie stają się automatycznie faktami
~~~

Write flow:

~~~text
OBSERVATION / HUMAN EDIT / RESULT
    ↓
MEMORY CANDIDATE
    ↓
DEDUPLICATION + CONFLICT + PROVENANCE CHECK
    ↓
AUTO-ACCEPT SAFE OPERATIONAL FACT OR HUMAN REVIEW
    ↓
VERSIONED MEMORY ITEM IN POSTGRES
~~~

Codex nie zapisuje bezpośrednio trwałej pamięci. Każdy item posiada `workspace_id`, provenance, confidence, validity, retention i visibility. Retrieval filtruje workspace, zakres, aktualność i retencję, wykonuje keyword/semantic search, reranking, a następnie tworzy immutable `KnowledgeSnapshot` przypięty do planu, draftu i runa.

### 6.7. Workspace Data Gateway i immutable context snapshot

Worker nie dostaje surowego DSN ani swobodnego SQL. Control Plane buduje workspace-scoped read model:

~~~json
{
  "workspace": "acme",
  "project_definition_version": 4,
  "intelligence_profile_version": 3,
  "active_plan_version": 7,
  "task": { "id": "...", "type": "CHANNEL_RESEARCH" },
  "knowledge_items": [],
  "claims": [],
  "assigned_resources": [],
  "channel_surface_assessments": [],
  "recent_signals": [],
  "open_alerts": [],
  "pending_decisions": []
}
~~~

Snapshot jest zapisany w `planning_context_snapshots` jako wersjonowany JSONB + hash zależności. Model czyta go przez task-scoped Domain MCP lub bezpośredni input SDK. Zapis jest możliwy wyłącznie przez komendy typu `propose_memory_candidate`, `create_resource_requirement`, `propose_plan_revision` i `create_alert`. Backend ponownie sprawdza `organization_id`, `workspace_id`, role i schema.

W produkcji wymagamy także PostgreSQL Row-Level Security albo równoważnej, przetestowanej izolacji tenantów jako defense-in-depth. Klucz serwisowy omijający RLS nie trafia do procesu Codexa.

### 6.8. Flow „Utwórz workspace”

~~~text
[Nowy workspace]
    ↓
nazwa + cel + źródła + opcjonalny budżet/lokalizacja
    ↓
ingest i wersjonowanie źródeł w PostgreSQL
    ↓
Terra/Luna ekstrahuje fakty, claimy i braki
    ↓
Sol Workspace Principal tworzy diagnozę projektu
    ↓
ProjectDefinition + IntelligenceProfile drafts
    ↓
krótka lista decyzji istotnych dla strategii
    ↓
Channel Discovery + archetype hypotheses
    ↓
ResourceRequirements, np. konto HN lub Google Business Profile
    ↓
operator łączy, odrzuca albo zastępuje wymagane zasoby
    ↓
Account/Resource Onboarding + baseline + capability simulation
    ↓
MarketingPlanVersion + etapy + workstreamy + modele + budżety
    ↓
operator zatwierdza plan i autonomy envelope
    ↓
Workspace READY → [Start plan] → ACTIVE
~~~

## 7. Generalized Marketing Intelligence i research kanałów

AI ma samodzielnie przygotować rozeznanie, gdzie dany typ produktu lub firmy powinien inwestować uwagę i budżet. LinkedIn i Reddit są adapterami, nie fundamentem domeny.

### 7.1. Channel Discovery

Przykładowy, otwarty zakres:

- LinkedIn,
- Reddit,
- Hacker News,
- GitHub,
- fora branżowe i niszowe community,
- Facebook Groups i lokalne grupy,
- Google Search, Google Ads, Google Business Profile i Maps,
- Meta Ads, TikTok Ads i inne kanały płatne,
- Instagram, YouTube, TikTok i twórcy,
- local SEO, katalogi i review platforms,
- katalogi produktów,
- newslettery i blogi,
- partnerstwa, eventy i outreach wykonywany ręcznie,
- inne kanały odkryte podczas researchu.

Kanały nie są zaszyte w kodzie planera. `PlatformDefinition`, `ChannelSurface`, `ResourceTypeDefinition` i `SemanticCapability` tworzą wersjonowany registry. `ChannelSurface` jest jedynym kanonicznym agregatem miejsca działania: może oznaczać subreddit, feed, grupę, profil firmy, placement reklamowy albo forum. `Community` jest jego wersjonowanym rozszerzeniem z regułami i kulturą, a nie drugim źródłem prawdy. Nowy adapter deklaruje typ zasobu, sposób onboardingu, obserwowalne sygnały, capabilities, wymagania wykonania i jakość pomiaru. Planer operuje na capabilities i evidence, a nie na instrukcji `if platform == reddit`.

Każdy stabilny rekord registry ma `active_version_id`, a treść istnieje w immutable tabeli `*_versions` z `version`, `schema_version`, `effective_from`, `supersedes_id`, `content_hash` i stanem publikacji. Dotyczy to także connectorów, channel surfaces oraz platform-capability mappings. Run planera dostaje jeden `MarketingRegistrySnapshot`, nigdy mieszaninę aktualizowanych w locie wersji.

Dla każdego surface powstaje `ChannelSurfaceAssessment` wskazujący obowiązkowe `channel_surface_id`:

~~~text
audience_fit
problem_fit
content_fit
available_accounts
estimated_reach
activity_level
competition
entry_cost
time_to_signal
available_capabilities
measurement_quality
risk_and_constraints
recommended_role
confidence
evidence
~~~

Wynik:

~~~text
PRIMARY
SECONDARY
EXPERIMENT
OBSERVE_ONLY
REJECTED
~~~

AI proponuje kanały, ale plan zapisuje dowody i confidence. Brak danych nie może być zamieniany na pewną rekomendację.

### 7.2. Archetyp jest hipotezą, nie szablonem na sztywno

Pierwsza diagnoza wybiera jeden lub kilka archetypów i sprawdza je na danych:

| Archetyp | Typowe kanały startowe | Typowy rezultat | Zasoby, o które może poprosić Principal |
| --- | --- | --- | --- |
| `OPEN_SOURCE_DEVTOOL` | GitHub, Hacker News, techniczne fora/Reddit, founder LinkedIn, docs | użytkownicy, stars, issues, contributors | repozytorium, konta techniczne, demo, telemetry/analytics |
| `B2B_SAAS` | LinkedIn, search, branżowe community, content, partnerstwa | qualified conversations, demos, trials | founder account, CRM/analytics, materiały proof, budżet testowy |
| `LOCAL_BUSINESS` | Google Business Profile/Maps, local SEO, Meta/Google Ads, lokalne grupy i twórcy | wizyty, rezerwacje, telefony, kierunki dojazdu | profil firmy, Meta/Google Ads, budżet, geografia, assety |
| `ECOMMERCE` | Shopping/Search, Meta/TikTok, creators, email | zakupy, CAC, ROAS, repeat purchase | katalog produktów, pixel/analytics, ad accounts, budżet |
| `MARKETPLACE_OR_COMMUNITY` | supply/demand channel mix, partnerships, community, search | aktywne strony rynku i liquidity | analityka kohort, oddzielne persony, partner resources |

Restauracja w Poznaniu nie dostaje planu „zdobądź karmę na Reddit”. Principal bierze pod uwagę promień działania, godziny otwarcia, ofertę, sezonowość, opinie, budżet oraz lokalne źródła popytu. Acme nie dostaje z kolei automatycznie kampanii Meta Ads, jeżeli lepszy jest GitHub, techniczne community i dowody produktu.

Wybór kanału tworzy `ChannelHypothesis`:

~~~text
expected audience
job to be done
organic / paid / owned / partner
required resources
entry strategy
leading signals
conversion event
test budget or time budget
minimum evidence
stop-loss
confidence
~~~

Brak zasobu tworzy `ResourceRequirement`, a nie fikcyjne założenie, że connector istnieje. Użytkownik może zaakceptować rekomendację, wskazać alternatywę, odrzucić ją albo poprosić Sol o ponowną analizę z dodatkową instrukcją.

### 7.3. Channel surfaces, marketing motions i semantic capabilities

Oddzielamy dostawcę technicznego od sposobu marketingowego użycia:

~~~text
Platform              = np. LinkedIn, Reddit, Google, Facebook
ChannelArchetype       = np. PROFESSIONAL_GRAPH, THREADED_COMMUNITY, LOCAL_SEARCH
ChannelSurface         = konkretny feed, subreddit, grupa, profil firmy lub placement
WorkspaceChannelBinding = decyzja użycia surface przez workspace
MarketingMotion        = LISTEN, EXPERT_PARTICIPATION, PAID_TEST, LOCAL_VISIBILITY itd.
~~~

Przykładowe channel archetypes:

~~~text
PROFESSIONAL_GRAPH
THREADED_COMMUNITY
CODE_COLLABORATION
LOCAL_SEARCH_AND_MAPS
PAID_SEARCH
PAID_SOCIAL
SHORT_FORM_FEED
LONG_FORM_PUBLISHING
OWNED_BROADCAST
PRODUCT_DIRECTORY
REVIEWS_AND_REPUTATION
PARTNERSHIP_OR_EVENT
~~~

Plan i Principal używają capabilities semantycznych:

~~~text
DISCOVER_SURFACES
SEARCH_CONTENT
READ_CONTENT
READ_THREAD
READ_METRICS
PUBLISH_CONTENT
PUBLISH_REPLY
REACT_TO_CONTENT
JOIN_COMMUNITY
MANAGE_CAMPAIGN
READ_AD_METRICS
REQUEST_HUMAN_ACTION
~~~

`REACT_TO_CONTENT` obejmuje reakcje o niskim koszcie i niskiej wartości sygnału (lajk, upvote, reakcja), `JOIN_COMMUNITY` — dołączenie lub subskrypcję tam, gdzie surface tego wymaga. Obie są akcjami zapisującymi i podlegają tym samym regułom approvalu co publikacja.

Connector mapuje je na `AUTOMATED`, `AUTOMATED_AFTER_APPROVAL`, `MANUAL_ONLY`, `UNSUPPORTED` albo `UNKNOWN`. Dzięki temu dodanie forum Rusta, nowej platformy reklamowej czy lokalnego katalogu nie wymaga zmiany Goal Engine ani Workspace Principal — wymaga definicji surface, adaptera i capability mapping.

### 7.4. Community Map

~~~text
community identity
platform
size snapshot
activity snapshot
topic clusters
common questions
language and vocabulary
content formats
observed engagement
active hours and days
notable authors
entry opportunities
rules snapshot
last verified at
data quality
~~~

### 7.5. Research postów i reklam

Research działa równolegle z budową sieci i obecności.

System analizuje:

- temat,
- format,
- długość,
- autora i obserwowalną wielkość jego sieci,
- czas publikacji,
- dzień tygodnia,
- reakcje, komentarze i rozwój dyskusji,
- widoczny zasięg, jeżeli jest dostępny,
- CTA,
- obecność linku,
- typ wartości dostarczonej odbiorcom.

Dla kanałów płatnych analogiczny model obejmuje kreację, audience, placement, koszt, impressions, clicks, conversion window, spend, CPA/ROAS i jakość danych. Budżet reklamowy jest osobnym, zatwierdzanym ledgerem; model nie może go sam zwiększyć.

Metryki mogą być pobierane w zdefiniowanych oknach, np. po 1, 6 i 24 godzinach, ale wyłącznie gdy adapter posiada taką capability i istnieje budżet odczytu.

Normalizacja powinna uwzględniać:

~~~text
community size
author network size
post age
day and hour
format
topic
baseline channel activity
data completeness
~~~

### 7.6. Snapshoty metryk

Pomiarów nie nadpisujemy.

Przykładowe metryki kont:

~~~text
linkedin.connections
linkedin.followers
linkedin.profile_views
reddit.account_karma
reddit.post_karma
reddit.comment_karma
reddit.account_age
publication.views
publication.replies
publication.reactions
publication.clicks
~~~

Każdy snapshot zawiera:

~~~text
account or entity
metric key
value or UNKNOWN
observed_at
collected_at
source_url
evidence_ref
adapter_version
confidence
freshness
~~~

Brak widocznej wartości oznacza `UNKNOWN`, a nie zero. Spadek metryki jest poprawną obserwacją. Korelacji z publikacją nie nazywamy automatycznie przyczynowością.

Każdy zestaw snapshotów wskazuje `metric_collection_run_id` i idempotency key. Delta używa najbliższego poprawnego snapshotu sprzed początku okna oraz ostatniego poprawnego snapshotu, nigdy przypadkowo nadpisanej wartości.

### 7.7. Community signals, sentyment i wzorce

Principal nie powinien reagować nerwowo na pojedynczy komentarz. System utrzymuje append-only:

~~~text
community_signals
sentiment_observations
topic_trends
content_pattern_insights
channel_health_snapshots
plan_review_windows
~~~

Sygnał posiada źródło, scope, kierunek, siłę, confidence, sample size, observed window, freshness i ewentualny temat. Wnioski porównują co najmniej dwa okna albo przekraczają jawny próg zdarzeniowy. Przykłady:

- rosnąca liczba pytań o konkretny problem,
- zmiana sentymentu po wydaniu funkcji,
- spadek odpowiedzi mimo podobnej jakości i cadence,
- format technicznej retrospektywy wyraźnie przebija ogólne posty,
- wzrost kosztu płatnego kanału ponad stop-loss,
- community zaczyna samodzielnie wspominać produkt.

Wynik może utworzyć `Alert`, zadanie researchowe, bezpieczny `OperationalAdjustment` albo `PlanRevisionProposal`. Nie mutuje planu bezpośrednio.

## 8. Marketing Plan, etapy i równoległe workstreamy

### 8.1. Trzy niezależne poziomy dojrzałości

#### Connection readiness

~~~text
PROVISIONED
  → OWNER_VERIFIED
  → SESSION_VERIFIED
  → CAPABILITIES_DISCOVERED
  → READY

READY → REAUTH_REQUIRED | SUSPENDED | QUARANTINED
~~~

To mówi tylko, czy zasób jest poprawnie połączony. Nie oznacza gotowości marketingowej.

#### Wyliczana dojrzałość marketingowa

~~~text
DISCOVERY
  → BASELINE_READY
  → PRESENCE_BUILDING
  → CONTRIBUTION
  → AUTHORITY_BUILDING
  → PRODUCT_ADJACENT
  → LAUNCH_READY
  → SUSTAIN
~~~

Nie jest to osobny lifecycle workspace'u. Wartość jest projekcją aktywnego `PlanStageInstance` i jego wyników. UI pokazuje jako „current marketing stage” dokładnie aktywny stage planu.

#### Account–workspace–channel readiness track

Lokalny stan relacji:

~~~text
workspace + account assignment + channel surface/community

UNASSESSED
  → BASELINED
  → OBSERVE_ONLY
  → VISIBILITY_BUILDING
  → SOFT_PARTICIPATION
  → ESTABLISHED
  → CAMPAIGN_ELIGIBLE

każdy stan → PAUSED | BLOCKED | REASSESSMENT_REQUIRED
~~~

Stare konto nie oznacza automatycznie dojrzałej obecności w nowej społeczności. Stage bierze pod uwagę baseline konta, wcześniejsze uczestnictwo w konkretnym surface, capabilities, jakość danych i dowody wykonania.

Presence track ma własne rekordy `presence_tracks` i append-only `presence_track_events`. Zmiana stanu wymaga dowodów stage gate.

Przykład po podłączeniu zasobów Acme:

~~~text
Reddit Jan / r/selfhosted
baseline: starsze konto, widoczna historia, umiarkowana karma
start: VISIBILITY_BUILDING
plan: obserwacja + pomocne odpowiedzi bez wzmianki produktowej
worker: Luna research, Terra draft, Sol tylko review gate

Hacker News Jan
baseline: nowe konto, brak historii
start: OBSERVE_ONLY
plan: research tematów i języka; potem ręcznie zatwierdzane SOFT_PARTICIPATION
worker: Luna collection/classification, Terra synthesis

LinkedIn Jan
baseline: istniejąca sieć i aktywność
start: SOFT_PARTICIPATION lub ESTABLISHED zależnie od evidence
plan: równolegle network research, founder content i measurement
~~~

### 8.2. Etapy są kompilowane z archetypu

Nie istnieje jeden lejek dla każdego biznesu. `MarketingMotionCompiler` składa etapy z archetypu, ChannelHypotheses, dostępnych resources i celu.

Domyślny wariant community/open source:

~~~text
DISCOVERY → BASELINE → NETWORK_OR_REPUTATION → PARTICIPATION
→ AUTHORITY → PRODUCT_ADJACENT → LAUNCH → SUSTAIN
~~~

Przykładowy wariant lokalnej restauracji:

~~~text
LOCAL_DISCOVERY → PROFILE_AND_TRACKING_BASELINE → OFFER_AND_ASSET_READINESS
→ LOCAL_VISIBILITY → SMALL_PAID_TESTS → OPTIMIZE_RESERVATIONS
→ SEASONAL_CAMPAIGN → REPUTATION_AND_RETENTION
~~~

Etap może pozostawać aktywny tak długo, jak wymagają tego wyniki, ale nie blokuje równoległego researchu, przygotowania assetów ani innego workstreamu. Każdy etap zapisuje wymagane zasoby, modele wykonawcze, leading indicators, exit criteria, stop-loss i approval class.

### 8.3. Stage Gate

~~~text
stage_can_advance =
    required_milestones_completed
    AND minimum_evidence_reached
    AND required_memory_ready
    AND no_blocking_guardrail
    AND capacity_available
    AND approval_policy_passed
~~~

AI ocenia gate i tworzy `StageTransitionProposal`. Przejście może nastąpić automatycznie tylko wtedy, gdy mieści się w wcześniej zatwierdzonym envelope autonomii.

### 8.4. Parallel Workstreams

Przykładowe tory:

~~~text
MARKET_RESEARCH
CHANNEL_RESEARCH
COMMUNITY_LISTENING
NETWORK_DEVELOPMENT
REPUTATION_BUILDING
OPPORTUNITY_INBOX
COMMUNITY_PARTICIPATION
FOUNDER_CONTENT
BRAND_CONTENT
PRODUCT_PROOF
LAUNCH_PREPARATION
CAMPAIGN_EXECUTION
FEEDBACK_TO_PRODUCT
MEASUREMENT
~~~

Każdy workstream posiada:

~~~text
objective
state
stage envelope
assigned accounts
capacity budget
WIP limit
cadence
dependencies
allowed action types
approval policy
priority
~~~

Workstreamy mogą działać równolegle, jeżeli:

- dependencies są spełnione,
- mają capacity,
- nie przekraczają WIP,
- nie konkurują o ten sam aktywny profil,
- mieszczą się w dozwolonym stage envelope.

„Równolegle” oznacza równoległy postęp w tym samym oknie planowania. Przy `CodexConnection concurrency = 1` i `BrowserProfile concurrency = 1` scheduler przeplata taski workstreamów według capacity i fairness; nie uruchamia dwóch turnów lub sesji tego samego zasobu jednocześnie.

Lifecycle workstreamu:

~~~text
PROPOSED → READY → ACTIVE → COMPLETED → ARCHIVED
                    ↓
               PAUSED | BLOCKED
                    ↓
                  ACTIVE
~~~

Stage instance:

~~~text
PLANNED → READY → ACTIVE → GATE_REVIEW → COMPLETED
                    ↓
               PAUSED | BLOCKED
~~~

Milestone:

~~~text
PLANNED → READY → IN_PROGRESS → ACHIEVED
                         └────→ MISSED

PLANNED/READY/IN_PROGRESS → CANCELLED
~~~

### 8.5. Wersjonowanie planu

~~~text
DRAFT
  → SIMULATED
  → IN_REVIEW
  → APPROVED
  → ACTIVE
  → SUPERSEDED
  → COMPLETED

ACTIVE → PAUSED | CANCELLED
~~~

Termin review jest osobnym `review_status` i `review_due_at`; plan pozostaje `ACTIVE` podczas oceny.

Partial unique indexes gwarantują najwyżej jeden aktywny Intelligence Profile i plan na workspace. Aktywacja nowej wersji oraz `SUPERSEDED` poprzedniej odbywają się atomowo.

Replanning tworzy nową wersję oraz czytelny diff. Po supersede obowiązuje grandfathering:

- nie powstają nowe taski ze starego planu,
- `BACKLOG/READY` są migrowane albo anulowane,
- `IN_PROGRESS` może zakończyć się ze starym snapshotem tylko wtedy, gdy aktualne guardraile nadal przechodzą,
- `SUBMIT_STARTED` może być już wyłącznie reconciliowany.

Stage, workstream, goal i milestone mają stabilne agregaty, immutable `*_definition_versions` związane z `marketing_plan_version_id` oraz osobne `*_runtime_states`. Task przypina dokładne definition-version IDs.

## 9. Goal Engine

### 9.1. Hierarchia

~~~text
Strategic Objective
    ↓
Marketing Plan Version
    ↓
Stage Instance
    ↓
Workstream
    ↓
Goal
    ↓
Milestone
    ↓
Daily Commitment
    ↓
Task
    ↓
Action Proposal
    ↓
Execution Intent
    ↓
Evidence and Measurement
~~~

### 9.2. Trzy składowe celu

| Składowa | Encja | Znaczenie | Przykład |
| --- | --- | --- | --- |
| `ACTION_TARGET` | `goal_action_targets` | Ilość kontrolowalnej pracy, którą chcemy wykonać | ocenić 30 profili |
| `ACTION_BUDGET` | `action_budget_limits` | Nieprzekraczalny limit działania | maksymalnie 10 zaproszeń |
| `EXTERNAL_OUTCOME` | `goal_metric_targets` | Wynik zależny od platformy lub ludzi | 10 zaakceptowanych kontaktów |

Karma, followersi, akceptacje zaproszeń i reakcje są `EXTERNAL_OUTCOME`. System może utrzymywać etap przez kilka tygodni, ale nie może tego samego dnia bez końca zwiększać liczby działań, próbując wymusić wynik.

Każdy goal wiąże się z jednym lub kilkoma workstreamami przez `goal_workstream_links`. Task może wskazać parę goal/workstream wyłącznie wtedy, gdy taka relacja istnieje.

Minimalny target metryki:

~~~text
goal_id
metric_definition_id
role: OUTCOME | LEADING | GUARDRAIL
baseline
target
direction
weight
measurement_window
minimum_sample
minimum_confidence
~~~

### 9.3. Przykład LinkedIn

~~~text
Goal:
Rozwinąć trafną sieć z 145 do 350 kontaktów w 90 dni.

Daily ACTION_TARGET:
- ocenić do 30 kandydatów,
- przygotować do 10 propozycji zaproszeń,
- wysłać maksymalnie 10 zatwierdzonych zaproszeń,
  jeżeli assignment, capability i budżety na to pozwalają.

EXTERNAL_OUTCOME:
- przyrost zaakceptowanych kontaktów,
- udział osób pasujących do ICP,
- odpowiedzi i wejścia na profil.

Stop conditions:
- brak wystarczającej liczby trafnych kandydatów,
- budget exhausted,
- capability degraded,
- challenge lub reauth,
- limit jakości,
- ręczne wstrzymanie.
~~~

Jeżeli cel dnia oznacza „10 wysłanych, zatwierdzonych zaproszeń”, może zostać spełniony przez wykonanie dziesięciu potwierdzonych akcji. Jeżeli oznacza „10 zaakceptowanych zaproszeń”, child task przechodzi do `WAITING_EXTERNAL`, a instancja dnia zamyka pracę i oczekuje na późniejsze okno pomiarowe.

### 9.4. Przykład Reddit

~~~text
Goal:
Zbudować historię wartościowego uczestnictwa i zwiększyć obserwowalną karmę
z 80 do 300 w horyzoncie 60 dni.

Daily ACTION_TARGET:
- przejrzeć 20 nowych dyskusji,
- wybrać maksymalnie 5 dobrze dopasowanych,
- przygotować maksymalnie 3 odpowiedzi,
- opublikować maksymalnie 2 zatwierdzone odpowiedzi.

EXTERNAL_OUTCOME:
- karma delta,
- odpowiedzi innych użytkowników,
- powracający rozmówcy,
- przejścia na profil lub projekt.
~~~

Brak wzrostu karmy nie tworzy automatycznie trzeciego, czwartego i piątego komentarza ponad budżet.

### 9.5. Goal lifecycle

~~~text
DRAFT
  → ACTIVE
  → MEASURING
  → ACHIEVED | NEEDS_REPLAN | BLOCKED | EXPIRED | CANCELLED

NEEDS_REPLAN/BLOCKED → ACTIVE
wyłącznie przez zatwierdzoną komendę

Health:
NO_DATA | ON_TRACK | AT_RISK | OFF_TRACK
~~~

Lifecycle i health są niezależne.

Goal health jest liczony deterministycznie:

~~~text
observed_progress =
    normalize(current - baseline, target - baseline, direction)

expected_progress =
    trajectory_for_current_measurement_window

progress_gap =
    observed_progress - expected_progress
~~~

Brak minimalnej próbki, świeżości albo confidence daje `NO_DATA`. `AT_RISK/OFF_TRACK` wymaga odchylenia przez co najmniej dwa review windows, chyba że naruszono twardy guardrail.

### 9.6. Daily Goal Instance

Definicja celu tworzy dzienną instancję według timezone workspace'u. `DailyGoalInstance` jest oknem pomiarowym z baseline'em i snapshotem budżetów, a `DailyCommitment` jest zatwierdzonym pakietem pracy w tym oknie. Task należy do commitmentu.

~~~text
DailyGoalInstance:
OPEN → ACTIVE → EVALUATING → CLOSED
        └──────────→ PARTIAL | BUDGET_EXHAUSTED | BLOCKED
                         └──────────────────────────────→ CLOSED

DailyCommitment:
PROPOSED → COMMITTED → IN_PROGRESS
                      → FULFILLED | PARTIAL | MISSED
                      → REVIEWED
~~~

`WAITING_APPROVAL`, `WAITING_ACCOUNT` i `WAITING_EXTERNAL` pozostają stanami child tasków/proposali, a dzienna instancja przechowuje zagregowany health.

Daily instance zapisuje:

~~~text
window_start / window_end UTC
baseline_snapshot_set_id
outcome_observation_deadline
evaluation_snapshot_set_id
data_quality
evaluated_at
~~~

Granica dnia zapisuje konkretne okno UTC, aby poprawnie obsłużyć DST. Późna metryka może zaktualizować nadrzędny goal progress, ale nie mutuje po cichu zamkniętego wyniku dnia.

### 9.7. Pętla realizacji celu

Nie implementujemy:

~~~text
while result < target:
    perform_more_external_actions()
~~~

Implementujemy trwały, ograniczony loop:

~~~text
COMPILE DAILY PLAN
    ↓
SELECT ELIGIBLE TASK
    ↓
CHECK HARD CONSTRAINTS
    ↓
CREATE PROPOSAL AND WAIT FOR APPROVAL IF REQUIRED
    ↓
ATOMIC BUDGET RESERVATION + EXECUTION INTENT
    ↓
EXECUTE WITHIN RESERVED BUDGET
    ↓
VERIFY EVIDENCE
    ↓
UPDATE PROGRESS
    ↓
IF DAILY BUDGET REMAINS AND ELIGIBLE WORK EXISTS
    → SELECT NEXT TASK
ELSE
    → CLOSE DAILY INSTANCE
    → WAIT FOR EXTERNAL OUTCOME
    → REVIEW NEXT WINDOW
~~~

Stage pozostaje aktywny przez kolejne dni aż do exit criteria, deadline, stop-loss albo decyzji o replanningu.

Niespełniona część dnia nie jest automatycznie dodawana do następnego limitu. Następny Daily Planner widzi deficyt, przyczynę i goal health, po czym tworzy nowy, nadal ograniczony commitment albo proponuje zmianę strategii.

### 9.8. Hierarchiczne budżety

~~~text
effective_remaining =
    min(
        account_global_remaining,
        assignment_remaining,
        workspace_remaining,
        workstream_remaining,
        goal_instance_remaining,
        adapter_runtime_budget_remaining
    )
~~~

Budżety obejmują:

- liczbę analizowanych URL-i,
- odczyty feedu i metryk,
- minuty browser runnera,
- liczbę draftów,
- liczbę formularzy,
- próby submit,
- zaproszenia,
- wiadomości,
- komentarze,
- posty,
- manual takeovers,
- Codex usage budget.

Budget ledger:

~~~text
RESERVE
CONSUME
RELEASE
ADJUST
~~~

Reguły:

- rezerwacja jest atomowa,
- rezerwacja powstaje dopiero po wymaganym approvalu i ma TTL,
- błąd przed side effectem zwalnia rezerwację,
- expiry albo anulowanie przed side effectem zwalnia rezerwację,
- `SUBMIT_STARTED` zużywa budżet,
- `UNKNOWN` + alert `UNKNOWN_EXTERNAL_RESULT` również zużywa budżet,
- zewnętrzny spadek metryki nigdy nie odnawia budżetu,
- zwiększenie limitu wymaga `PlanRevisionProposal` i policy check.

## 10. Event-driven Daily Planner i scheduler

System realizuje cele, a nie luźne cron joby.

Cron lub timer może obudzić proces, ale nie jest źródłem prawdy. Źródłem prawdy są wersjonowane cele, daily instances, taski, eventy i budżety w Postgresie.

### 10.1. Wyzwalacze

~~~text
start of planning window
goal activated
task completed
metric observed
approval received
account became available
block detected
stage gate due
manual replan requested
~~~

### 10.2. Hard constraints

Nowy task jest odrzucany, jeżeli:

- workspace lub plan nie są aktywne,
- workstream jest paused albo blocked,
- goal jest `ACHIEVED`, `CANCELLED`, `EXPIRED`, `BLOCKED` albo `NEEDS_REPLAN`,
- milestone, daily instance albo commitment nie pozwalają na dalszą pracę,
- dependency nie została zakończona,
- assignment nie jest aktywny,
- profil wymaga reauth lub jest quarantined,
- brakuje capability,
- stage/presence envelope zabrania akcji,
- przekroczono capacity, WIP albo budżet,
- istnieje konflikt kont lub kampanii,
- zadanie wygasło,
- CodexConnection nie ma dostępnego limitu.

Task już działający ze starszym planning snapshotem podlega regule grandfathering z sekcji planu i zawsze ponownie przechodzi aktualne, nieosłabialne guardraile.

### 10.3. Scoring

~~~text
task_score =
    goal_impact
  + milestone_urgency
  + opportunity_value
  + freshness
  + learning_value
  + SLA_pressure
  + confidence
  - risk_penalty
  - repetition_penalty
  - context_switch_penalty
  - account_load_penalty
~~~

Wagi są przechowywane w wersji planu. Etap 1 używa deterministycznego greedy schedulera z tie-breakerem i rezerwacją capacity per workstream.

### 10.4. Concurrency

~~~text
BrowserProfile   = 1 aktywna sesja
SocialAccount    = 1 browser execution
CodexConnection  = 1 aktywny turn do E2 włącznie
Workspace        = konfigurowalny WIP
Runner           = limit według RAM/CPU
Adapter          = limit techniczny i capability
~~~

Draft generation i oczekiwanie na approval nie trzymają lease'u profilu.

Pause działa hierarchicznie:

~~~text
organization
→ workspace
→ plan
→ workstream
→ assignment
→ account
→ goal
→ task
~~~

Task jest runnable tylko wtedy, gdy wszystkie nadrzędne poziomy na to pozwalają.

~~~text
DRAIN
    brak nowych tasków, bieżący dochodzi do bezpiecznej granicy

SAFE_STOP
    zatrzymanie przed następnym side effectem

EMERGENCY_STOP
    natychmiastowe przerwanie; po SUBMIT_STARTED możliwy UNKNOWN_RESULT
~~~

## 11. Kanban i sterowanie realizacją

Domyślne kolumny:

~~~text
BACKLOG
READY
TODAY
DOING
WAITING APPROVAL
WAITING EXTERNAL
BLOCKED
VERIFYING
DONE
CANCELLED
~~~

Task lifecycle:

~~~text
BACKLOG → READY → COMMITTED → RUNNING
RUNNING → WAITING_APPROVAL | WAITING_EXTERNAL | BLOCKED | VERIFYING
VERIFYING → COMPLETED | FAILED | UNKNOWN
BACKLOG/READY/COMMITTED/BLOCKED → CANCELLED
~~~

Kolumny UI mapują te stany, ale nie tworzą nowych znaczeń wykonawczych.

Swimlane'y:

- workstream,
- konto lub assignment,
- goal,
- milestone,
- model role.

Przesunięcie karty wywołuje komendę domenową:

~~~text
Move to TODAY
→ CommitTaskForDay

Move to DOING
→ ClaimTask

Move to DONE
→ CompleteTask
~~~

Serwer sprawdza dependencies, `lock_version`, WIP, capacity, assignment i stan planu. Frontend nie może bezpośrednio ustawiać arbitralnego statusu.

Workspace ma również przyciski:

- `Start plan`,
- `Pause — drain`,
- `Safe stop`,
- `Emergency stop`,
- `Resume`,
- `Request replan`,
- `Approve plan revision`,
- `Cancel plan`.

### 11.1. Alert Center

Alert opisuje fakt wymagający uwagi; nie jest decyzją ani zgodą. Przykładowe typy:

~~~text
NEEDS_ACCOUNT_CONNECTION
THREAD_WINDOW_CLOSING
DRAFT_EXPIRED
APPROVAL_CAPACITY_EXCEEDED
NEGATIVE_COMMUNITY_EVENT
FAKE_ENGAGEMENT_SUSPECTED
E1_GATE_AT_RISK
NEEDS_LOGIN_OR_MFA
NEEDS_BUDGET
NEEDS_CONTENT_ASSET
ACCOUNT_TOO_NEW_OR_OBSERVE_ONLY
CAPABILITY_LOST
IDENTITY_MISMATCH
COMMUNITY_SENTIMENT_SHIFT
HIGH_VALUE_OPPORTUNITY
PLAN_OFF_TRACK
NO_ELIGIBLE_WORK
SOURCE_CHANGED
QUEUE_DELIVERY_FAILURE
REPEATED_WORKER_FAILURE
UNKNOWN_EXTERNAL_RESULT
~~~

Alert ma `INFO | WARNING | HIGH | CRITICAL`, scope (`organization`, `workspace`, `resource`, `channel`, `task`), evidence snapshot i dedupe fingerprint. Lifecycle:

~~~text
OPEN → ACKNOWLEDGED → INVESTIGATING → RESOLVED → CLOSED
OPEN/ACKNOWLEDGED/INVESTIGATING → SNOOZED → OPEN
OPEN → DISMISSED | SUPPRESSED
RESOLVED → OPEN przy nowym wystąpieniu
~~~

Fingerprint łączy typ, workspace, subject, policy version i istotny kontekst. Kolejne wystąpienie zwiększa `occurrence_count`, zamiast produkować lawinę kart. Krytyczny, deterministyczny guardrail może automatycznie zatrzymać wykonanie; wznowienie jest osobną decyzją człowieka.

### 11.2. Decision Center i pomoc człowieka

Rozdzielenie:

~~~text
Alert             = co się stało lub czego brakuje
DecisionProposal  = jakie opcje widzi AI i którą rekomenduje
DecisionReview    = odpowiedź człowieka
DecisionApplication = deterministyczne zastosowanie zatwierdzonego efektu
Action Approval   = zgoda na konkretną zewnętrzną akcję
~~~

Przykładowa karta:

~~~text
[HIGH] Wymagane konto Hacker News
Sol: kanał ma wysokie audience fit, lecz brak zasobu do baseline.

Rekomendacja: połącz istniejące prawdziwe konto i rozpocznij OBSERVE_ONLY.
Alternatywy:
1. Połącz konto teraz.
2. Kontynuuj research bez logowania.
3. Odrzuć kanał z bieżącego planu.
4. Odłóż decyzję do kolejnego review.

Wpływ na plan: odblokuje workstream HN_RESEARCH; nie odblokuje publikacji.
Dowody: 8 źródeł, confidence 0.78, snapshot 2026-08-27.

[Approve] [Edit & Approve] [Reject] [Snooze] [Ask AI]
~~~

Lifecycle decyzji:

~~~text
DRAFT → VALIDATED → WAITING_APPROVAL
WAITING_APPROVAL → APPROVED | APPROVED_WITH_EDITS | REJECTED
WAITING_APPROVAL → NEEDS_INPUT | DEFERRED | EXPIRED | SUPERSEDED
APPROVED/APPROVED_WITH_EDITS → REVALIDATING → APPLYING → APPLIED
REVALIDATING → STALE
APPLYING → APPLY_FAILED
~~~

Każda rewizja ma immutable `revision_hash`, `base_context_snapshot_id`, precondition hash, evidence, options, recommended option, impact/budget/policy diff, risk, expiry i `default_on_expiry = HOLD | SAFE_PAUSE`. Edycja tworzy nową rewizję i unieważnia poprzednią zgodę.

Efekt nie jest dowolnym JSON-em wykonanym przez model. `decision_kind` wskazuje typed handler, np.:

~~~text
CONNECT_RESOURCE
ASSIGN_RESOURCE
PROVIDE_MISSING_INPUT
ACTIVATE_PLAN_VERSION
APPLY_PLAN_REVISION
CHANGE_ACTION_OR_AD_BUDGET
ADVANCE_STAGE
RESOLVE_MEMORY_CONFLICT
APPROVE_MANUAL_TAKEOVER
RESUME_AFTER_HARD_STOP
~~~

Handler ponownie sprawdza preconditions, approvera, aktualne wersje, policy i idempotency key, następnie wykonuje jedną komendę domenową i zapisuje audit + outbox. Sol nie może zatwierdzić własnej rekomendacji.

### 11.3. `Ask AI` i `Resolve with AI`

Operator może dopisać kontekst: „HN na razie pomiń”, „budżet maksymalnie 1500 zł”, „ten profil reprezentuje tylko właściciela restauracji” albo „spróbuj znaleźć inne forum Rusta”. Powstaje `OperatorGuidance`, nigdy nie jest ono dopisywane w miejscu do starego promptu.

~~~text
Alert/Decision + immutable problem snapshot
    ↓
[Resolve with AI]
    ↓
human supplemental instruction
    ↓
new bounded ResolutionRun (Terra lub Sol według ryzyka)
    ↓
resolution proposal + diff + evidence + uncertainties
    ↓
human review
    ↓
typed deterministic application
~~~

Model nie pozostaje uruchomiony, czekając na człowieka. Kończy run wynikiem `DECISION_REQUIRED`; odpowiedź emituje zdarzenie i tworzy nowy job. Zapobiega to blokowaniu CodexConnection, browser lease'u i kolejki.

### 11.4. Jedna skrzynka pracy człowieka

`Decision Center` jest odbudowywalną projekcją łączącą:

- resource gaps,
- decyzje planu i budżetu,
- alerty,
- action approvals,
- memory/claim conflicts,
- reauth/MFA i manual takeovers,
- niejednoznaczne rezultaty zewnętrzne.

Źródłem prawdy pozostają odpowiednie agregaty domenowe. Projekcja pozwala filtrować po workspace, severity, typie decyzji, deadline, zasobie oraz „co zostanie odblokowane”.

## 12. Orkiestracja modeli Codexa

### 12.1. Role

Nie używamy jednego modelu do wszystkiego.

| Rola | Domyślna trasa | Odpowiedzialność |
| --- | --- | --- |
| Workspace Principal | GPT-5.6 Sol, najwyższy eval-approved effort dostępny w danym `model/list` | diagnoza, strategia, wymagania zasobów, plan i trudny replanning |
| Daily Planner | GPT-5.6 Terra, medium/high | zamiana planu na commitments i taski |
| Research / Extraction Worker | GPT-5.6 Luna, low/default-supported | routing, klasyfikacja, ekstrakcja i normalizacja |
| Browser Worker | Luna low/default-supported dla znanych, ograniczonych flow; Terra medium przy niejednoznaczności | wykonanie bounded taska |
| Draft Writer | Terra medium; Sol high dla złożonej treści technicznej | komentarze i posty |
| Policy/Claim Verifier | Terra high albo Sol high | niezależna kontrola |
| Result Verifier | deterministyczny adapter + Luna low; świeża Terra przy niejasności | dowód wykonania |
| Plan Critic | świeża Terra high; opcjonalnie Sol dla wysokiego ryzyka | niezależna krytyka planu i diffu |

Sol jest przeznaczony do najtrudniejszych i niejednoznacznych problemów, Terra do codziennych zadań produkcyjnych, a Luna do wysokowolumenowego routingu, klasyfikacji, ekstrakcji i automatyzacji.

Nie zakładamy, że każda kombinacja model/effort jest dostępna. W szczególności plan nie wymaga `Luna ultra` ani nie ustawia stale `max`. Aktualne możliwości są pobierane z `model/list` dla konkretnego `CodexConnection`. Wyższy effort jest wybierany dopiero po zmierzonej potrzebie i potwierdzeniu w evalach.

Workspace Principal otrzymuje read model zawierający plan, goal health, metryki, budżety, blokady, zasoby, sygnały i decyzje, ale nie otrzymuje tokenów, profili, surowej bazy ani możliwości wykonania side effectu.

### 12.2. Workspace Principal jest rolą logiczną

Jeden workspace ma jednego aktywnego `WorkspacePrincipal`, ale nie oznacza to stale działającego procesu lub modelu. Każde rozumowanie jest skończonym `PrincipalRun`:

~~~text
QUEUED → CONTEXT_COMPILING → MODEL_RUNNING → VALIDATING → COMMITTING
COMMITTING → SUCCEEDED | NO_ACTION
QUEUED/CONTEXT_COMPILING/MODEL_RUNNING/VALIDATING/COMMITTING → FAILED | CANCELLED
~~~

Triggerami są m.in. aktywacja workspace'u, zamknięcie measurement window, ryzyko celu, brak zasobu, odpowiedź człowieka, krytyczny alert, utrata capability i termin stage gate. Event coalescing oraz `last_processed_event_seq` zapobiegają uruchamianiu Sol po każdym pojedynczym sygnale; jeden workspace ma najwyżej jeden aktywny PrincipalRun.

Każdy `PrincipalRun` jest bounded i przed dispatch ma zapisane: `deadline_at`, `max_wall_time_ms`, `max_turns`, `max_tool_calls`, `max_output_bytes`, `heartbeat_interval_ms` oraz `cancellation_requested_at`. Próba runa wskazuje dokładny `job_attempt_id`; heartbeat i cancellation są propagowane przez Durable Job Orchestrator. Po przekroczeniu limitu adapter najpierw wysyła `turn/interrupt`, czeka przez krótki grace period, następnie kończy per-turn MCP sidecar i — jeśli runtime nie odpowiada — zabija izolowany proces App Servera. Niedokończony bundle nie trafia do `COMMITTING`.

Ten sam `BoundedRunEnvelope` obowiązuje `WorkerRun`, `ResolutionRun`, planner i verifier: każdy ma job attempt, deadline, limity turn/tool/output, heartbeat oraz cancellation. `COMMITTING` zapisuje proposal bundle, wszystkie zaakceptowane typed proposals i terminalny stan runa w jednej transakcji; jakikolwiek błąd lub cancel wycofuje całość, więc częściowy bundle nie staje się widoczny.

Ustrukturyzowany output:

~~~json
{
  "base_context_snapshot_id": "...",
  "causal_event_ids": ["..."],
  "diagnosis": {},
  "channel_hypotheses": [],
  "resource_requirement_proposals": [],
  "task_proposals": [],
  "alert_proposals": [],
  "decision_proposals": [],
  "memory_candidates": [],
  "operational_adjustments": [],
  "plan_revision_proposals": [],
  "worker_route_recommendations": [],
  "no_action_reason": null
}
~~~

Backend waliduje każdy element oddzielnie. Principal może proponować, ale nie może zatwierdzić własnej decyzji, przypisać konta, zwiększyć budżetu, obniżyć approval strictness, wykonać zewnętrznej akcji ani zapisać canonical memory.

Nawet proposal bundle nie jest ogólnym uprawnieniem do zapisu. Przed `COMMITTING` backend pobiera aktywny `WorkspacePrincipalAuthorityGrant` i sprawdza deny-by-default: wersję Principala, dozwolone `proposal_kinds`, dozwolone `command_kinds`, workspace scope, maksymalny budget/autonomy envelope, `valid_from`, `expires_at` i `state=ACTIVE`. Grant pozwala wyłącznie na wskazane komendy tworzące propozycje lub pamięć-kandydata; assignment, approval, execution i zwiększenie envelope zawsze pozostają poza zakresem. Mismatch kończy run jako `FAILED/AUTHORITY_DENIED` i tworzy alert.

Wątek Codexa nie jest pamięcią biznesową. Pierwszy plan używa świeżego threadu; review może resume'ować thread tylko w tej samej `principal_generation` i przy zgodnym bindingu. Zatwierdzony replan tworzy nową generację i świeży thread. Cały stan pozwalający odtworzyć decyzję znajduje się w Postgresie, więc utrata threadu nie oznacza utraty planu.

### 12.3. Backend orchestration zamiast niejawnej autonomii

~~~text
Task Engine
    ↓
Durable Job Orchestrator
    ↓
Deterministic Model Router
    ↓
Quota and Capability Gate
    ↓
Optional Planner
    ↓
Bounded Worker
    ↓
Independent Verifier
    ↓
Human Approval when required
    ↓
Narrow Executor
    ↓
Post-action Verification
~~~

Principal, planner, worker i verifier są osobnymi wątkami. Verifier nie dostaje prywatnej argumentacji workera, tylko wynik, źródła, kryteria i evidence. Business approval z Decision Center jest osobną domeną od technicznych approval requests Codex App Servera.

### 12.4. Model Router

Input:

~~~text
task_type
role
risk_level
ambiguity
novel_ui
evidence_size
previous_failures
deadline
available_models
supported_reasoning_efforts
quota_pressure
eval_approved_routes
~~~

Output:

~~~json
{
  "policy_version": "router-v1",
  "role": "DRAFT_WRITER",
  "requested_model": "gpt-5.6-terra",
  "requested_effort": "medium",
  "fallback_chain": [
    ["gpt-5.6-terra", "high"],
    ["gpt-5.6-sol", "high"]
  ],
  "reason_codes": ["STANDARD_RISK", "NATURAL_LANGUAGE_DRAFT"]
}
~~~

Router:

1. pobiera model capabilities dla połączenia,
2. odrzuca niedostępne model/effort,
3. stosuje quality floor dla roli i ryzyka,
4. sprawdza limity,
5. wybiera trasę spełniającą wymagania na podstawie `predicted_quota_cost` liczonego jako EWMA rzeczywistych wywołań,
6. zapisuje decyzję przed turnem,
7. zapisuje requested model/effort oraz obserwowalny effective model,
8. nie obniża po cichu jakości verifiera lub zadania wysokiego ryzyka,
9. po każdym evencie `model/rerouted` ponownie porównuje `toModel` z allowlistą i quality floor roli oraz sprawdza, czy requested effort jest obsługiwany przez model docelowy.

Oficjalny event `model/rerouted` ujawnia zmianę modelu i powód, ale nie potwierdza faktycznie użytego effortu. Dlatego po reroute zapisujemy `effective_model=toModel`, a `effective_effort_observation=UNOBSERVED` (albo `ASSUMED_REQUESTED` wyłącznie jako nieautorytatywną telemetrię). Jeżeli model docelowy nie obsługuje requested effort lub dana rola wymaga potwierdzonego effortu, wynik otrzymuje `ROUTE_MISMATCH`: proposal bundle nie jest commitowany, run jest przerywany i trafia do bezpiecznego retry albo `WAITING_CAPABILITY` z alertem. Sam poprawny JSON nie wystarcza.

### 12.5. Limity Codexa

ChatGPT Work i Codex współdzielą usage. Zużycie zależy od modelu, kontekstu, reasoning, narzędzi i MCP. Szacunki z dokumentacji nie są gwarancją liczby jobów.

Polityka:

~~~text
GREEN          < 60%  normalny routing
AMBER       60–80%   Sol tylko dla wysokiej wartości
RED         80–95%   tylko priorytetowe zadania
DRAIN/RESERVE 95–<100% brak nowych turnów, lokalna rezerwa
EXHAUSTED             rateLimitReachedType lub błąd limitu z runtime'u
~~~

Scheduler korzysta z:

- `account/rateLimits/read`,
- `account/rateLimits/updated`,
- `account/usage/read`,
- `thread/tokenUsage/updated`.

Jeżeli runtime zwraca kilka limit buckets i primary/secondary windows, scheduler wybiera najbardziej restrykcyjny stan. UI pokazuje `usedPercent`, `resetsAt`, czas, latency i token usage. Nie udaje dokładnego kosztu pieniężnego dla joba wykonywanego z planu ChatGPT.

Adapter jawnie wysyła `model`, `effort` oraz `outputSchema` dla każdego turnu i nie polega na wartościach odziedziczonych z poprzedniego turnu. Zapisuje także eventy `model/rerouted` i `thread/tokenUsage/updated`.

### 12.6. Weryfikacja wykonania

Silniejszy model nie powinien sam stwierdzać, że zadanie zostało wykonane bez dowodów.

Completion:

~~~text
deterministic adapter assertions
AND required evidence present
AND schema valid
AND verifier verdict acceptable
AND no unresolved guardrail
~~~

Verifier zwraca:

~~~text
PASS | REVISE | HUMAN_REQUIRED
violations
claim_checks
evidence_refs
uncertainties
decision_rationale
~~~

Nie wymagamy ujawniania wewnętrznego toku rozumowania.

### 12.7. Optymalizacja promptów i workerów

Feedback:

- approve/edit/reject,
- diff człowieka,
- rejection reason,
- verifier disagreement,
- policy block,
- schema failure,
- takeover,
- rezultat i późniejsze metryki.

Flow:

~~~text
PRODUCTION FEEDBACK
    ↓
SANITIZED EVAL CASE
    ↓
BASELINE
    ↓
ONE CHANGE:
MODEL OR EFFORT OR PROMPT
    ↓
SHADOW REPLAY
    ↓
CANARY
    ↓
PROMOTE OR ROLLBACK
~~~

Sol może zaproponować nowy prompt albo routing policy, ale nie modyfikuje aktywnej wersji bez evali i zatwierdzonej promocji.

`PromptVersion` jest immutable i zawiera:

~~~text
role
task_type
body
sha256
output_schema_version
tool_manifest_version
context_compiler_version
router_policy_version
status: DRAFT | CANARY | ACTIVE | RETIRED
~~~

Eval harness działa lokalnie przez Codex SDK na fixture'ach i sanitizowanych snapshotach. Nie zakładamy użycia hostowanego Evals API.

## 13. Integracja Codexa bez klucza API

### 13.1. Podział odpowiedzialności

~~~text
CodexAccountAdapter
    → App Server
    → login ChatGPT, account state, model list, usage i rate limits

CodexExecutionAdapter
    → stabilny Python Codex SDK
    → uruchamianie lokalnych wątków planera, workera i verifiera
~~~

Python SDK steruje lokalnym App Serverem przez JSON-RPC i zawiera przypięty runtime CLI. Użycie SDK nie oznacza własnych wywołań OpenAI API ani konieczności `OPENAI_API_KEY`.

Nie automatyzujemy strony ChatGPT przez CDP. Oficjalny przepływ to lokalny Codex SDK/App Server zalogowany metodą ChatGPT. CDP jest przeznaczone wyłącznie dla połączonych zasobów marketingowych. Dzięki temu system korzysta z sesji Codexa oraz limitów planu ChatGPT, a nie z kruchych interakcji z webowym UI ChatGPT.

Faza E1.F0 musi contract-testować, czy przypięta wersja SDK wystawia wymagane model, effort i output schema. Brakujące funkcje obsługuje wąski adapter niskopoziomowego JSON-RPC, zamiast wymyślonych parametrów SDK.

Surowy App Server pozostaje `EXPERIMENTAL_DEPENDENCY` i posiada E1.F0 Go/No-Go. Działa wyłącznie lokalnie przez stdio albo Unix socket, na przypiętej wersji i z wygenerowanym, contract-testowanym JSON Schema. Fallback logowania to lokalny `codex login --device-auth` oraz ponowny odczyt stanu.

Oficjalny SDK jest przedstawiany przede wszystkim jako runtime dla lokalnych, coding-focused threadów. Użycie go jako silnika wielodniowych operacji marketingowych przez własne, wąskie MCP jest hipotezą produktu, nie gwarantowanym use case'em OpenAI. Faza E1.F0 musi to potwierdzić na reprezentatywnych evalach i może zakończyć się `NO_GO` albo koniecznością zmiany runtime'u bez przebudowy domeny.

Osobny E1.F0 gate uruchamia wielogodzinny test schedulera na dokładnie tym planie ChatGPT, sposobie logowania i środowisku, które mają działać w oknie walidacyjnym E1.F6: restart runtime'u, odświeżenie auth, quota exhaustion, resume i praca bez otwartego dashboardu. Oficjalna dokumentacja preferuje API key w programmatic workflows, a dla zaufanej automatyzacji organizacyjnej opisuje kontrolowane access tokens; dlatego unattended marketing runtime na zwykłym loginie ChatGPT pozostaje hipotezą. Jeśli test nie potwierdzi wspieranego kontraktu uwierzytelnienia, wynik to `NO_GO` dla pracy bez nadzoru — nie automatyczne obchodzenie loginu przez CDP.

Lifecycle połączenia jest oddzielony od chwilowego trybu supervisora:

~~~text
CodexConnection:
DISCONNECTED → AUTH_PENDING → AUTHENTICATED → CAPABILITIES_SYNCING → READY
CAPABILITIES_SYNCING → READY | REAUTH_REQUIRED | SYNC_RETRY | DISABLED
READY → CAPABILITIES_SYNCING | RATE_LIMITED | REAUTH_REQUIRED | DISABLED
SYNC_RETRY → CAPABILITIES_SYNCING | DISABLED
RATE_LIMITED → READY | CAPABILITIES_SYNCING | REAUTH_REQUIRED
REAUTH_REQUIRED → AUTH_PENDING
DISABLED → DISCONNECTED

Job waiting states:
WAITING_AUTH | WAITING_QUOTA | WAITING_CAPABILITY | WAITING_RETRY
~~~

TTL snapshotu, reauth, `model/list` invalidation albo wykrycie nieznanego modelu wywołują `READY → CAPABILITIES_SYNCING`. Po ustaniu rate limitu connection wraca bezpośrednio do `READY` tylko wtedy, gdy snapshot pozostaje kompletny i świeży; w przeciwnym razie synchronizuje katalog. `SYNC_RETRY` ma bounded backoff i alert. Joby w stanach `WAITING_*` nie trzymają runtime lease'u; transition do `READY` emituje event, który atomowo tworzy nową wersję dispatchu dla oczekujących jobów.

Jeden `CodexConnectionSupervisor` posiada ekskluzywne tryby:

~~~text
STOPPED
  → AUTH_CONTROL
  → STOPPED
  → EXECUTION
  → STOPPED
~~~

Account Adapter i SDK nigdy nie uruchamiają jednocześnie dwóch App Serverów na tym samym `CODEX_HOME`. Używają tego samego dokładnego `codex_bin` i wersji runtime.

### 13.2. Logowanie ChatGPT

~~~text
Settings → AI Connections → Connect Codex
    ↓
initialize request
    ↓
initialize response
    ↓
initialized notification
    ↓
account/login/start(chatgptDeviceCode)
    ↓
verification URL + user code
    ↓
account/login/completed
    ↓
account/read
    ↓
model/list
    ↓
account/rateLimits/read
    ↓
READY
~~~

Device code jest beta i może wymagać włączenia przez użytkownika albo administratora workspace'u. To wynik `account/login/start` — w tym jawny błąd niedostępnej metody — rozstrzyga dostępność, nie wymyślony endpoint preflight. Własny `LoginAttempt` posiada lokalny deadline, cancel i status oraz fallback do browser callback albo lokalnego `codex login --device-auth`. Po `account/login/completed` wywołanie `account/read` zapisuje tylko udokumentowane i faktycznie zwrócone pola: auth type, nullable email/plan oraz stabilny lokalny fingerprint połączenia; nie traktujemy go jako niezmiennego dowodu realnego właściciela. Zarządzane środowisko może wymusić `forced_chatgpt_workspace_id`.

Synchronizacja katalogu modeli iteruje `model/list` aż `nextCursor=null`; polityka `includeHidden` jest jawna i w produkcji domyślnie `false`. Snapshot staje się `COMPLETE` dopiero po pobraniu wszystkich stron i zapisaniu jednego content hash. Niekompletny, przeterminowany albo pochodzący sprzed reauth snapshot blokuje routing stanem `WAITING_CAPABILITY` — router nie zgaduje nazw modeli ani effortów.

### 13.3. Izolacja CodexConnection

Etap 1:

- jeden zaufany operator,
- jedno połączenie Codex,
- wiele kont społecznościowych,
- maksymalnie jeden aktywny turn.

Każde połączenie otrzymuje:

~~~text
dedicated OS principal or isolated process
dedicated CODEX_HOME with 0700
cli_auth_credentials_store = "file"
auth.json with 0600 on encrypted storage
forced_login_method = "chatgpt"
singleton runtime lease
~~~

`CODEX_HOME` nie zapewnia pełnej izolacji, jeżeli credential store pozostaje na `auto` lub wspólnym keyringu.

Runtime Codexa:

- nie ma mountów profili Chromium,
- nie widzi bazy i sekretów,
- nie ma Docker socket,
- ma minimalny read-only workspace,
- ma wyłączony shell i unified exec,
- ma wyłączony web search oraz zbędne MCP,
- korzysta tylko z task-scoped Browser/Domain MCP,
- nie może wywołać `thread/shellCommand`, `command/exec` ani metod process/config/import/write.

Wymagany artefakt hardened runtime obejmuje między innymi:

~~~toml
web_search = "disabled"

[features]
shell_tool = false
unified_exec = false
apps = false
multi_agent = false
skill_mcp_dependency_install = false
~~~

`experimentalApi=false`, klient JSON-RPC posiada jawny method allowlist, a konfiguracja MCP używa `required=true` oraz dokładnego `enabled_tools`. OS sandbox i egress policy pozostają nadrzędną granicą.

### 13.4. Thread isolation

Trwałe bezpieczeństwo resume i metadata pojedynczego uruchomienia są osobnymi rekordami:

~~~text
ThreadSecurityBinding =
(
  codex_connection_id,
  workspace_id,
  social_account_id nullable,
  role,
  principal_generation nullable,
  tool_manifest_version,
  security_policy_version
)

CodexRunThread =
(
  thread_security_binding_id,
  automation_run_id,
  job_attempt_id,
  attempt,
  prompt_version_id,
  router_policy_version
)
~~~

`ThreadSecurityBinding` zapisuje `thread_id` i faktyczny `session_id` zwrócony przez runtime. `CodexRunThread` zapisuje requested model/effort, obserwowany effective model, stan obserwowalności effortu, context snapshot oraz wynik. Planner, worker, verifier i post-verifier używają osobnych threadów; nie ma globalnego wątku dla całego workspace'u. Resume jest dozwolony wyłącznie przy dokładnej zgodności stabilnego bindingu i aktywnej principal generation. Nowy run może mieć inne `automation_run_id` lub `job_attempt_id` bez fałszywego konfliktu, ale jakakolwiek różnica security bindingu kończy resume fail-closed.

## 14. Warstwa Chromium/CDP

### 14.1. Wybrany lifecycle

Ponieważ CDP jest wymaganiem projektu, wybieramy jeden spójny wariant. Kapsuła ma dwa tryby na tym samym profilu: `HEADLESS` jest trybem domyślnym i obsługuje całą pracę automatu, `INTERACTIVE` uruchamiany jest na żądanie wyłącznie dla logowania, MFA, challenge i human takeover.

~~~text
Browser Session Broker
    ↓
isolated Session Capsule for one account
    ↓
    ├── HEADLESS (domyślny, cała praca automatu)
    │   pinned Chrome for Testing --headless=new
    │   --user-data-dir=<dedicated account profile>
    │   --remote-debugging-port=0
    │
    └── INTERACTIVE (na żądanie: login, MFA, challenge, takeover)
        Xvnc/TigerVNC virtual display + minimal window manager
        headful pinned Chrome for Testing, ten sam user-data-dir
    ↓
internal CDP Gateway ← read DevToolsActivePort
    ↓
Playwright connectOverCDP()

Dashboard ← WSS + one-time ticket ← Stream Gateway ← noVNC/websockify
        (wyłącznie w trybie INTERACTIVE)
~~~

Nie mieszamy tego flow z `launchPersistentContext()`. CDP i VNC są dwoma niezależnymi transportami do tego samego procesu: CDP służy wąskim adapterom i działa w obu trybach, a VNC wyłącznie obrazowi i inputowi człowieka i istnieje tylko w trybie `INTERACTIVE`. Przełączenie trybu oznacza zatrzymanie procesu Chrome i ponowny start na tym samym profilu; profil, sesja i cookies pozostają, ale trwający run zostaje najpierw doprowadzony do `SAFE_STOP`. Szczegóły warstwy zbierania danych i konsekwencje tej decyzji opisuje sekcja 24.

`connectOverCDP()` działa tylko z Chromium i ma niższą zgodność funkcji niż natywny protokół Playwright, dlatego E1.F0 feature contract jest warunkiem Go/No-Go, a cała reszta systemu widzi wyłącznie stabilny kontrakt Browser Gateway.

### 14.2. Profile i binaria

- jeden profil na konto,
- baza wymusza najwyżej jeden nieretired `BrowserProfile` dla `social_account_id` przez partial unique constraint; równoległe próby utworzenia profilu są testowane,
- jeden osobny proces Chromium i virtual display na aktywny profil,
- dwa konta tej samej platformy nigdy nie współdzielą procesu, `BrowserContext`, `user-data-dir`, displayu ani streamu,
- ten sam exact executable we wszystkich trybach danego profilu,
- zapis pełnej wersji, digestu, OS i architektury,
- zakaz mieszania Chrome, Chrome for Testing, Chromium i headless shell na jednym profilu,
- canary upgrade na cold clone reprezentatywnego istniejącego profilu,
- cold snapshot przed migracją,
- brak downgrade'u profilu otwartego przez nowszą wersję,
- domyślny recovery to reauth, a nie przenoszenie aktywnych cookies,
- polityka określa maksymalny dopuszczalny patch lag przypiętego Chrome for Testing.

### 14.3. Przeglądarka osadzona w dashboardzie

Etap 1 wybiera `InteractiveTransport = NoVncTransport`. Jest prostszy od WebRTC i wystarcza do loginu, MFA, popupów, ręcznego działania oraz debugowania. Transport pozostaje za interfejsem, aby później dodać WebRTC bez zmiany Browser Gatewaya lub domeny profili.

Flow:

~~~text
[Połącz konto] / [Otwórz interaktywnie]
    ↓
POST /browser-sessions
    ↓
RBAC + workspace/resource authorization
    ↓
ProfileLease + start/reuse Session Capsule
    ↓
VIEW_ONLY: issue ticket
CONTROL: atomically acquire HUMAN InteractionLease + issue ticket
    ↓
one-time StreamTicket (30–60 s, stored only as hash,
bound to profile and interaction lease generations)
    ↓
POST exchange on isolated stream origin
    ↓
Secure HttpOnly session cookie
    ↓
dashboard iframe/panel + WSS
~~~

Ticket jest związany z `user_id`, `browser_session_id`, rolą, profile lease generation, a dla `CONTROL` także z `interaction_lease_id` i `interaction_lease_generation`. Nie trafia do query stringu ani logów. Przyznanie HUMAN lease'u, unieważnienie poprzedniego kontrolera i wydanie CONTROL ticketu odbywają się atomowo przed pokazaniem panelu; exchange wyłącznie zużywa istniejący ticket i tworzy stream session/cookie, bez zmiany lease'u. Jeżeli AUTOMATION aktualnie posiada input, `Otwórz interaktywnie` najpierw tworzy `HumanTakeover` i kończy `SAFE_STOP/reconciliation`; nie przeskakuje tej ścieżki. Każdy input frame sprawdza obie generation, a utrata lease'u zamyka controller WebSocket.

`ticket_expires_at`, `stream_session_expires_at`, `idle_timeout_at` i wygaśnięcie cookie są osobnymi wartościami. Reconnect wymaga nadal ważnego Secure/HttpOnly/SameSite cookie powiązanego z sesją albo nowego one-time ticketu. Exchange wymaga dokładnego `Origin`, CSRF tokenu i `POST`; iframe ma restrykcyjne `frame-ancestors` oraz osobny origin.

Profile są runner-affine:

~~~text
browser_profile.home_runner_id
~~~

W local alpha runner może działać na tej samej maszynie co Compose, ale browser capsule, CDP i VNC są dostępne tylko wewnątrz dedykowanej sieci/namespace. Frontend nigdy nie otrzymuje portu CDP ani surowego VNC endpointu.

### 14.4. Lease i fencing

~~~text
profile_leases
- organization_id
- profile_id
- generation
- owner_runner_id
- browser_session_id
- heartbeat_at
- expires_at
- state
~~~

Reguły:

- lease jest autorytatywny w Postgresie,
- `generation` rośnie przy każdym przejęciu,
- istnieje partial unique constraint dla jednego aktywnego lease'u na profil,
- każdy gateway call zawiera fencing token,
- runner posiada dodatkowy lokalny `flock`,
- utrata heartbeat rozpoczyna reconciliation, a nie natychmiastowy drugi start,
- stary fencing token jest odrzucany,
- lease zwalnia się dopiero po revoke stream/input, zatrzymaniu całej kapsuły, `fsync`, bezpiecznym unmount profilu i potwierdzeniu supervisor/container identity,
- niepewne reconciliation przenosi profil do `QUARANTINED`.

`browser_sessions` zapisuje:

~~~text
organization_id
capsule_instance_id
os_pid
process_started_at
process_instance_id or container_id
display_server_pid / display_server_health
window_manager_pid / window_manager_health
stream_gateway_instance_id / stream_gateway_health
capsule_supervisor_identity
browser_build_digest
browser_mode
cdp_endpoint_ref
display_id
stream_transport
last_seen_at
~~~

Po restarcie Gateway sprawdza tożsamość procesu i CDP `/json/version` przed podjęciem decyzji o ponownym starcie.

~~~text
interaction_leases
- id
- organization_id
- browser_session_id
- mode: AUTOMATION | HUMAN
- owner_ref
- generation
- heartbeat_at
- expires_at
- state

partial unique: one ACTIVE interaction lease per browser_session_id

stream_sessions
- browser_session_id
- user_id
- role: VIEW_ONLY | CONTROL
- ticket_hash
- profile_lease_generation
- interaction_lease_id nullable
- interaction_lease_generation nullable
- ticket_expires_at / stream_session_expires_at / idle_timeout_at
- issued_at / exchanged_at / revoked_at
~~~

Session Capsule działa jako osobny non-root principal/container z własnym PID/network namespace, limitami cgroups, read-only root filesystem i Chromium sandboxem. Montuje wyłącznie jeden odszyfrowany profil. Nie widzi Docker socket, KMS, innych profili ani Control Plane. CDP i VNC bindują się tylko do wewnętrznej sieci kapsuły.

Recovery dotyczy całej kapsuły, nie tylko procesu Chrome. Broker najpierw unieważnia stream sessions i oba typy input lease, zamyka controller/viewer WebSockety, zatrzymuje stream gateway, window manager, Chrome i Xvnc, weryfikuje `capsule_instance_id` oraz supervisor/container identity, wykonuje `fsync` i bezpieczny unmount profilu, a dopiero potem zwalnia ProfileLease. Jeżeli nie potrafi potwierdzić któregokolwiek kroku, profil przechodzi do `QUARANTINED`; nowa kapsuła nie startuje równolegle.

### 14.5. Tryb interaktywny, stream i takeover

Chrome Headless uruchamia browser bez widocznego UI. Nie jest więc podstawą pełnego streamowanego loginu. Dlatego kapsuła posiada tryb `INTERACTIVE`: headful Chrome na wirtualnym displayu, którego ekran można przekazać do dashboardu.

Praca automatu odbywa się w trybie `HEADLESS`, ponieważ nie wymaga renderowanego ekranu, zużywa mniej zasobów i nie utrzymuje stale displayu, window managera ani stream gatewaya. Tryb `INTERACTIVE` uruchamiany jest wyłącznie na żądanie: podczas dodawania konta, przy MFA, challenge, weryfikacji tożsamości i human takeover.

Konsekwencja, której nie ukrywamy: takeover wymaga przełączenia trybu, czyli zatrzymania procesu headless i startu headful na tym samym profilu. Dlatego sekwencja takeover zaczyna się od `SAFE_STOP` przed kolejnym side effectem, a nierozstrzygnięty `SUBMIT_STARTED` musi najpierw przejść reconciliation. Profil w spoczynku ma zamknięty proces w obu trybach, więc nie zużywa stale RAM/CPU.

~~~text
capsule_mode: HEADLESS | INTERACTIVE

HEADLESS → SAFE_STOP → stop Chrome → start headful + Xvnc → INTERACTIVE
INTERACTIVE → close stream/input leases → stop Chrome → start headless → HEADLESS

profil, user-data-dir i sesja są wspólne dla obu trybów
przełączenie trybu zapisuje audit event i nową browser_session
~~~

Oddzielamy trzy lease'y:

~~~text
ProfileLease      — który runner posiada profil
InteractionLease  — kto może wysyłać input: AUTOMATION albo HUMAN
StreamSession     — kto może oglądać obraz i czy jest VIEW_ONLY
~~~

Manual takeover:

1. operator prosi o kontrolę,
2. scheduler wykonuje `SAFE_STOP` przed kolejnym side effectem,
3. nierozstrzygnięty `SUBMIT_STARTED` wymaga reconciliation,
4. AUTOMATION InteractionLease zostaje odwołany,
5. HUMAN InteractionLease otrzymuje nową generation i TTL,
6. człowiek działa w osadzonym widoku,
7. po oddaniu kontroli adapter ponownie sprawdza identity, target i capabilities,
8. dopiero nowy job może otrzymać AUTOMATION lease.

`viewOnly` po stronie klienta noVNC nie jest granicą bezpieczeństwa. Wybrany Stream Gateway jest RFB-aware i uwierzytelnia upstream odrębnym, wyłącznie serwerowym credentialem TigerVNC dla view-only albo control; credential nigdy nie trafia do przeglądarki. Zwykły transparentny websockify nie może być jedynym enforcementem. Dodatkowo Gateway waliduje role i lease generation na wejściu, a utrata lease'u natychmiast zamyka controller WebSocket.

### 14.6. Rozdzielenie Browser MCP i Action Adapter

Codex nie wybiera ani nie przekazuje dowolnego `account_id`. Otrzymuje niezmienny `bound_account_ref` wynikający z task-scoped kontekstu, bez ścieżki profilu, portu CDP lub uchwytu pozwalającego przełączyć konto.

Control Plane tworzy przed turnem task-scoped capability:

~~~text
organization
workspace
task
run
assignment
account
profile lease generation
allowed tools
allowed origins
expiry
~~~

Mechanizm egzekucji w Etapie 1:

1. Control Plane tworzy binding po stronie serwera.
2. Uruchamia osobny per-turn sidecar MCP przed runtime'em Codexa.
3. Capability nie trafia do promptu; sidecar otrzymuje ją bezpośrednio od Control Plane.
4. Konfiguracja Codexa wskazuje tylko ten sidecar, `required=true` i exact `enabled_tools`.
5. Brak sidecara albo mismatch bindingu kończy turn fail-closed.
6. Sidecar jest zamykany po turnie.

Nie opieramy granicy bezpieczeństwa na eksperymentalnym `dynamicTools`.

Read-only Browser MCP:

~~~text
open_allowed_url()
snapshot()
extract(schema)
screenshot()
wait_for(condition)
request_human_takeover(reason)
~~~

Action Adapter:

~~~text
prepare_approved_action(proposal_revision_id)
execute_approved_action(execution_intent_id)
reconcile_action(execution_intent_id)
~~~

Model nie otrzymuje surowych `click/fill/press` dla zewnętrznych operacji zapisujących.

### 14.7. Capability Registry

Capability jest wersjonowanym stanem per konto:

~~~text
SUPPORTED
DEGRADED
MANUAL_ONLY
UNSUPPORTED
UNKNOWN
~~~

Przykład:

~~~json
{
  "capability": "SUBMIT_COMMENT",
  "state": "SUPPORTED",
  "execution_mode": "AUTOMATED_AFTER_APPROVAL",
  "side_effect": "IRREVERSIBLE",
  "retry_class": "RECONCILE_ONLY",
  "result_evidence": ["URL", "CONTENT_HASH"],
  "adapter_version": "reddit-1.0.0"
}
~~~

Minimalny kontrakt adaptera:

~~~text
verify_identity()
probe_capabilities()
read_account_metrics()
read_content()
read_publication_metrics()
prepare_action()
execute_action()
reconcile_action()
detect_block_reason()
detect_takeover_requirement()
~~~

Przykładowe capabilities:

~~~text
READ_IDENTITY
READ_ACCOUNT_METRICS
READ_CONTENT
READ_PUBLICATION_METRICS
PREPARE_COMMENT
SUBMIT_COMMENT
PREPARE_POST
SUBMIT_POST
PREPARE_CONNECTION_REQUEST
SUBMIT_CONNECTION_REQUEST
MANUAL_TAKEOVER
~~~

Capability snapshot zawiera `verified_at`, `expires_at`, `browser_build_digest`, `required_browser_mode`, `reason_code` i `confidence`. Po expiry jego stan staje się `UNKNOWN` do ponownego probe. `MANUAL_ONLY` tworzy human task, a nie zwykły błąd capability.

### 14.8. Rozdzielone state machines

~~~text
SocialAccount:
PROVISIONED → VERIFYING → READY
READY → PAUSED | REAUTH_REQUIRED | QUARANTINED

BrowserProfile:
CREATING → AVAILABLE → LEASED → AVAILABLE
AVAILABLE/LEASED → QUARANTINED | CORRUPT | RETIRED

BrowserSession:
STARTING → ACTIVE → STOPPING → CLOSED
STARTING/ACTIVE/STOPPING → CRASHED | ORPHANED

HumanTakeover:
REQUESTED → SAFE_STOPPING → CONTROL_GRANTED → RELEASED
REQUESTED/SAFE_STOPPING/CONTROL_GRANTED → CANCELLED | EXPIRED | RECONCILIATION_REQUIRED

ExecutionAttempt:
PREPARED → DISPATCHED → SUBMIT_STARTED
SUBMIT_STARTED → CONFIRMED | UNKNOWN
PREPARED/DISPATCHED → FAILED_BEFORE_SIDE_EFFECT
PREPARED/DISPATCHED → CAPABILITY_BLOCKED | IDENTITY_BLOCKED
~~~

`READY` i `AVAILABLE` oznaczają, że przeglądarka może być całkowicie zamknięta. `RUNNING` nigdy nie jest stanem konta.

## 15. Architektura techniczna

~~~text
┌────────────────────────────────────────────────────────────────────┐
│ Next.js Dashboard                                                  │
│ Resources · Principal · Plan · Kanban · Alerts · Decisions · View │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTPS / WSS
┌──────────────────────────────▼─────────────────────────────────────┐
│ FastAPI Modular Control Plane                                     │
│ Knowledge · Principal · Allocator · Goals · Policy · Decisions    │
└──────────────┬────────────────────────────┬────────────────────────┘
               │                            │ transactional outbox
       ┌───────▼─────────────┐       ┌──────▼──────────────────────┐
       │ PostgreSQL          │       │ RabbitMQ                    │
       │ canonical truth     │       │ durable execution transport │
       │ RLS · jobs · audit  │       │ role queues · backpressure  │
       └───────┬─────────────┘       └──────┬──────────────────────┘
               │                            │
       ┌───────▼────────────────────────────▼──────────────────────┐
       │ Durable Job Orchestrator                                 │
       │ Principal Runs · Terra/Luna Workers · Verifiers · Reaper │
       └──────────────┬─────────────────────────────┬──────────────┘
                      │ Codex SDK                  │ task-scoped MCP
             ┌────────▼─────────┐       ┌──────────▼──────────────┐
             │ Codex Runtime    │       │ Browser Session Broker  │
             │ ChatGPT auth     │       │ leases · stream · CDP   │
             └──────────────────┘       └──────────┬──────────────┘
                                                   │
                    ┌──────────────────────────────▼──────────────┐
                    │ Isolated Session Capsule / active account  │
                    │ HEADLESS Chrome · profile · adapters       │
                    │ INTERACTIVE (Xvnc + headful) na żądanie    │
                    └──────────────┬──────────────────┬───────────┘
                                   │ internal CDP     │ VNC/WSS
                                   ▼                  ▼ tylko login/MFA/takeover
                             Action Adapter     noVNC in dashboard
~~~

### 15.1. PostgreSQL + RabbitMQ: trwała orkiestracja, nie cron

Wybrany wariant V4:

~~~text
PostgreSQL jobs/job_attempts = stan biznesowy i źródło prawdy
transactional outbox         = atomowe zaplanowanie wysyłki
RabbitMQ                     = durable transport i backpressure
inbox receipts               = deduplikacja dostarczeń
leases/heartbeat/reaper      = odzyskiwanie porzuconych jobów
~~~

Dispatch:

1. Transakcja domenowa zapisuje `job` i `outbox_event`.
2. Outbox publisher wysyła persistent message z `mandatory=true` do wcześniej zadeklarowanej durable topology i czeka na publisher confirm. `basic.return`, nack albo confirm timeout pozostawiają event jako niewysłany; pozytywny confirm bez returnu dopiero ustawia `sent_at`.
3. Wiadomość zawiera `message_id=outbox_event_id`, `job_id`, `job_attempt_id`, `dispatch_version`, routing key i korelację — nie pełną prawdę biznesową.
4. Consumer w transakcji zapisuje `inbox_receipt` z `UNIQUE(consumer_name, message_id)`, sprawdza `dispatch_version`, claimuje wskazany `job_attempt` i zakłada lease z rosnącym `lease_generation` oraz losowym `lease_owner`. Duplikat lub stary dispatch jest bezpiecznym no-opem i zostaje ACKnięty.
5. Rabbit message otrzymuje manual ACK dopiero po commit claimu w Postgresie.
6. Każdy heartbeat, transition, wynik i powiązany outbox zapisuje CAS-em po `(job_id, attempt_id, lease_generation, lease_owner, expected_status)`; expiry i heartbeat używają czasu PostgreSQL, nie zegara workera. Zero zaktualizowanych wierszy oznacza utracony fencing token i wynik starego workera jest odrzucany.
7. Reaper w jednej transakcji blokuje wygasły job, zamyka poprzednią próbę jako `LEASE_EXPIRED` i porównuje `attempt_no` z `attempt_limit`. Poniżej limitu zwiększa `lease_generation` i `dispatch_version`, tworzy replacement `job_attempt(attempt_no+1, not_before=backoff)` oraz `outbox_event`. Po wyczerpaniu limitu terminalizuje job jako `ATTEMPTS_EXHAUSTED` i tworzy alert oraz DLQ/audit outbox — bez kolejnej próby.
8. Reaper nigdy nie publikuje bezpośrednio do RabbitMQ; standardowy outbox publisher wysyła nową wersję dispatchu, a consumer claimuje tylko dokładnie tę próbę.
9. Okresowy reconciler znajduje runnable joby bez aktywnego lease'u i bez żywego dispatch/outbox eventu, po czym atomowo tworzy brakującą wersję dispatchu.

Semantyka to **at-least-once + idempotency**, nie exactly-once. Duplikat wiadomości jest normalnym przypadkiem. Dla side effectu zewnętrznego nadal obowiązuje `SUBMIT_STARTED → UNKNOWN → reconciliation`, bez automatycznego ponowienia.

Routing keys opisują role, nie konkretne modele:

~~~text
principal.plan
principal.review
planning.daily
worker.research
worker.analysis
verification
decision.apply
execution.browser
measurement
~~~

Model Router wybiera Sol/Terra/Luna dopiero po claimie, na podstawie aktualnego `model/list`, quota i quality floor. `prefetch`, concurrency i WIP są ograniczane per worker, `CodexConnection`, BrowserProfile i workspace.

W środowisku wielowęzłowym długowieczne krytyczne kolejki używają quorum queues, publisher confirms i manual acknowledgements. Lokalny development może działać na pojedynczym durable brokerze; nie udaje wtedy wysokiej dostępności. Retry ma limit prób, backoff i osobne retry queues; terminalne błędy trafiają do DLQ oraz alertu, ale DLQ nie zastępuje rekordu joba w Postgresie.

### 15.2. Modularny monolit

~~~text
app/
├── organizations/
├── users/
├── resource_pool/
├── resource_requirements/
├── resource_allocator/
├── accounts/
├── assignments/
├── browser_profiles/
├── runners/
├── codex_connections/
├── workspace_principals/
├── workspace_intelligence/
├── knowledge_ingestion/
├── memory/
├── alerts/
├── decisions/
├── operator_guidance/
├── platform_registry/
├── channel_surfaces/
├── marketing_archetypes/
├── research/
├── communities/
├── planning/
├── stages/
├── workstreams/
├── goals/
├── commitments/
├── work_management/
├── opportunities/
├── proposals/
├── approvals/
├── execution/
├── measurement/
├── replanning/
├── queueing/
├── policies/
└── audit/
~~~

## 16. Bezpieczny pipeline wykonania

Analiza i wykonanie są osobnymi runami.

~~~text
Task
  ↓
PlanningContextSnapshot
  ↓
READ / ANALYZE / DRAFT RUN
  ↓
release browser lease
  ↓
ActionProposalRevision
  ↓
Policy, claim and conflict checks
  ↓
ApprovalDecision
  ↓
one-time ExecutionIntent
  ↓
reacquire profile lease
  ↓
identity + capability + target preflight
  ↓
deterministic Action Adapter
  ↓
SUBMIT_STARTED
  ↓
CONFIRMED | UNKNOWN
  ↓
Publication and Measurements
~~~

### 16.1. Immutable approval

~~~text
ActionProposal:
DRAFT → READY_FOR_APPROVAL → APPROVED | REJECTED | EXPIRED
APPROVED → SUPERSEDED, jeżeli powstanie nowa rewizja
~~~

Hash approvala obejmuje:

~~~text
organization
workspace
assignment version and account
platform
action type
target external ID and URL
source fingerprint
text and media
Intelligence Profile version
plan version
memory snapshot
adapter version
~~~

Każda edycja tworzy nową rewizję i unieważnia poprzedni approval.

Immutable proposal zachowuje historyczny kontekst, ale nie gwarantuje wiecznej wykonywalności. Execution preflight ponownie sprawdza aktualną wersję assignmentu, aktualne guardraile oraz nieosłabialne polityki. Bardziej restrykcyjna zmiana daje `AUTHORIZATION_CONTEXT_CHANGED` i wymaga nowej rewizji lub zgody.

### 16.2. External idempotency

Własny klucz nie gwarantuje exactly-once w zewnętrznym UI.

~~~text
PREPARED
  → DISPATCHED
  → SUBMIT_STARTED
  → CONFIRMED | UNKNOWN

PREPARED / DISPATCHED
  → FAILED_BEFORE_SIDE_EFFECT
  → może otrzymać nową próbę

SUBMIT_STARTED / UNKNOWN
  → bez automatycznego retry
~~~

Obietnica systemu:

~~~text
internal deduplication
+ maksymalnie jedna automatyczna próba submitu na ExecutionIntent
+ pre/post reconciliation
+ manual review dla UNKNOWN
~~~

### 16.3. Zmiana źródła przed wykonaniem

Proposal zapisuje `target_external_id`, fingerprint źródła, `observed_at` i `expires_at`. Przed execution adapter ponownie odczytuje cel. Istotna zmiana kończy się `SOURCE_CHANGED` i wymaga nowego draftu lub approvalu.

## 17. Replanning i autonomia

### 17.1. Częstotliwość review

~~~text
Real-time
- challenge, reauth, identity mismatch, UNKNOWN result, capability loss, hard stop-loss

Daily
- capacity, commitments, carry-over i blokady

Weekly
- goal health, workstream performance, channel evidence, sentiment i topic trends

Stage gate
- decyzja o przejściu etapu

Event-triggered
- brak wymaganego resource, dwa słabe review windows, brak eligible work,
  istotna zmiana źródeł, koszt kanału ponad stop-loss albo seria błędów workerów
~~~

Deterministyczny `ReplanTriggerEvaluator` stosuje minimum sample, hysteresis, cooldown, dedupe i causal-depth limit. Sol nie uruchamia się po każdej zmianie lajka czy pojedynczym komentarzu.

### 17.2. Replanning loop

1. Zamknij measurement window.
2. Sprawdź provenance, freshness i wielkość próbki.
3. Oblicz health goals, milestones i workstreamów.
4. Zsyntetyzuj community signals, sentyment, topic trends, koszty i blokady.
5. Znajdź bottleneck albo nową opportunity.
6. Sol przygotowuje ograniczoną propozycję zmian, nowych resource requirements lub decyzji.
7. Plan Critic w świeżym wątku ocenia zmianę wysokiego ryzyka.
8. Backend sprawdza capacity, konflikty, dependencies, budget, capabilities i policy.
9. Klasyfikator rozdziela zmianę:
   - operacyjna → `OperationalAdjustment`,
   - strukturalna → immutable `PlanRevisionProposal` + `DecisionProposal` z diffem i evidence.
10. `OperationalAdjustment` może wejść bez nowej wersji planu w zatwierdzonym envelope.
11. `PlanRevisionProposal` trafia do Decision Center, a po akceptacji aktywuje nową wersję atomowo.
12. Evale i feedback monitorują wpływ zmiany.

### 17.3. Co AI może zmieniać

Rozdzielamy:

~~~text
OperationalAdjustment
- kolejność tasków,
- dzienny przydział capacity w zatwierdzonych granicach,
- chwilowy pause,
- bez nowej wersji strategii.

PlanRevisionProposal
- goals,
- stages,
- workstream definitions,
- action envelopes,
- budgets,
- gate criteria,
- tworzy nową wersję planu.
~~~

W zatwierdzonym envelope jako `OperationalAdjustment`:

- kolejność tasków,
- priorytet,
- cadence,
- rozkład dziennej capacity,
- nowe research taski,
- obniżenie limitu,
- pause workstreamu po wykryciu blokady.

Wymaga osobnego approvala:

- zwiększenie action budget,
- przypisanie lub odpięcie konta,
- zmiana głównego goal,
- zmiana Workspace Intelligence Profile,
- rozszerzenie capabilities,
- obniżenie approval strictness,
- przejście do launch stage,
- dodanie nowego kanału płatnego lub zwiększenie ad budget,
- aktywacja nowego promptu lub model routing policy.

### 17.4. Autonomy levels

~~~text
0 — OBSERVE
1 — PROPOSE PLAN
2 — MANAGE KANBAN WITHIN APPROVED PLAN
3 — EXECUTE PREAPPROVED TASK CLASSES
~~~

~~~text
autonomy_level =
    min(
        user_selected_level,
        workspace_max_level
    )

effective_actions =
    intersection(
        assignment_action_set,
        account_capabilities,
        stage_action_set,
        presence_track_action_set,
        risk_policy_action_set
    )
~~~

Akcja wymaga jednocześnie wystarczającego `autonomy_level` i członkostwa w `effective_actions`.

## 18. Model danych

### Organizacja i workspace

~~~text
organizations
users
organization_memberships
workspaces
workspace_members
workspace_principals
workspace_principal_versions
workspace_principal_authority_grants
principal_event_inbox
principal_runs
principal_proposal_bundles
project_definitions
project_definition_versions
workspace_intelligence_profiles
workspace_intelligence_profile_versions
workspace_claims
claim_evidence_links
~~~

### Kanoniczna wiedza i pamięć

~~~text
knowledge_sources
knowledge_source_versions
knowledge_documents
knowledge_document_versions
knowledge_chunks
knowledge_ingestion_runs
knowledge_provenance_edges
knowledge_access_grants
extracted_facts
fact_conflicts
memory_items
memory_item_versions
memory_candidates
memory_conflicts
memory_snapshots
task_memory_bindings
planning_context_snapshots
~~~

### Zasoby, konta, profile i capabilities

~~~text
resource_handles
resource_type_definitions
resource_type_definition_versions
resource_requirements
resource_requirement_versions
resource_requirement_constraints
resource_resolution_attempts
resource_match_candidates
resource_allocations
resource_allocation_versions
capacity_reservations
resource_allocation_events
workspace_resource_assignment_proposals
workspace_resource_assignments
workspace_resource_assignment_versions
task_resource_bindings
social_accounts
account_assessments
account_owner_consents
account_owner_consent_versions
workspace_account_assignment_proposals
workspace_account_assignments
workspace_account_assignment_versions
assignment_workstream_authorizations
account_capacity_policies
account_conflicts
browser_profiles
browser_sessions
runner_nodes
profile_leases
interaction_leases
stream_sessions
human_takeovers
account_capability_snapshot_sets
account_capability_values
account_blocks
presence_tracks
presence_track_events
~~~

### Platformy, surfaces, archetypy i motions

~~~text
platform_definitions
platform_definition_versions
connector_definitions
connector_definition_versions
semantic_capabilities
semantic_capability_versions
platform_capability_mappings
platform_capability_mapping_versions
channel_archetypes
channel_archetype_versions
channel_surfaces
channel_surface_versions        # + access_mode, required_browser_mode, read_cost_class
community_rule_snapshots
channel_surface_archetype_bindings
channel_surface_archetype_binding_versions
marketing_registry_snapshots
workspace_channel_bindings
workspace_channel_binding_versions
workspace_channel_routes
channel_hypotheses
business_archetype_definitions
business_archetype_definition_versions
workspace_archetype_hypotheses
marketing_motion_definitions
marketing_motion_versions
motion_resource_requirement_templates
~~~

### Planowanie

~~~text
target_definitions
target_definition_versions
product_stage_gates
product_stage_gate_evaluations
marketing_plans
marketing_plan_versions
plan_stages
plan_stage_definition_versions
plan_stage_runtime_states
stage_gate_definitions
stage_gate_evaluations
stage_transition_proposals
workstreams
workstream_definition_versions
workstream_runtime_states
workstream_dependencies
workstream_account_allocations
goals
goal_definition_versions
goal_runtime_states
goal_objectives
goal_constraints
goal_metric_targets
goal_action_targets
goal_workstream_links
milestones
milestone_definition_versions
milestone_runtime_states
daily_goal_instances
daily_commitments
plan_revision_proposals
plan_revision_changes
operational_adjustments
~~~

### Alerty, decyzje i operator guidance

~~~text
approval_queue_items
alerts
alert_occurrences
alert_links
decision_proposals
decision_proposal_revisions
decision_options
decision_reviews
decision_applications
decision_effect_events
resolution_runs
operator_guidance
human_tasks
decision_center_items
~~~

### Work management i wykonanie

~~~text
tasks
task_dependencies
task_events
action_proposals
action_proposal_revisions
approval_decisions
execution_intents
execution_attempts
publications
publication_metric_snapshots
action_budget_limits
action_budget_ledger
human_feedback
~~~

### Research i pomiar

~~~text
thread_quality_assessments
heuristic_policy_versions
entry_opportunities
raw_source_responses
parse_runs
contribution_mix_reports
channel_surface_assessments
communities
community_snapshot_versions
community_rule_snapshots
observed_content
content_metric_snapshots
account_metric_snapshot_sets
account_metric_values
metric_definitions
metric_observations
measurement_windows
goal_progress_snapshots
attribution_links
insights
experiments
metric_collection_runs
community_signals
sentiment_observations
topic_trends
content_pattern_insights
channel_health_snapshots
plan_review_windows
~~~

### Codex i modele

~~~text
codex_connections
codex_usage_snapshots
ai_worker_definitions
workspace_worker_assignments
workspace_worker_assignment_versions
worker_workstream_authorizations
worker_runs
model_capability_snapshots
model_routing_policies
model_route_decisions
thread_security_bindings
codex_run_threads
model_calls
prompt_templates
prompt_versions
eval_cases
eval_runs
eval_results
~~~

### Infrastruktura i audyt

~~~text
jobs
job_attempts
job_dependencies
job_leases
outbox_events
inbox_receipts
audit_events
artifacts
policy_decisions
queue_dead_letters
~~~

### 18.1. Kluczowe rekordy

`workspace_principals`:

~~~text
id
organization_id
workspace_id
state
health
generation
active_version_id
last_processed_event_seq
last_review_at
lock_version
~~~

`principal_runs`:

~~~text
id
organization_id
workspace_id
principal_version_id
trigger_type
causal_event_ids
planning_context_snapshot_id
model_route_decision_id
job_attempt_id
status
deadline_at
max_wall_time_ms
max_turns
max_tool_calls
max_output_bytes
heartbeat_interval_ms
cancellation_requested_at
started_at / completed_at
proposal_bundle_id
failure_class
~~~

`workspace_principal_authority_grants`:

~~~text
id
organization_id
workspace_id
workspace_principal_id
principal_version_id
principal_generation
allowed_proposal_kinds
allowed_command_kinds
scope_json
budget_envelope_json
autonomy_envelope_json
valid_from / expires_at
state: DRAFT | ACTIVE | REVOKED | EXPIRED
created_by
grant_hash
~~~

`worker_runs`:

~~~text
id / organization_id / workspace_id
worker_assignment_version_id / role
job_attempt_id / planning_context_snapshot_id
status / attempt
deadline_at / max_wall_time_ms
max_turns / max_tool_calls / max_output_bytes
heartbeat_interval_ms / cancellation_requested_at
model_route_decision_id / result_bundle_id / failure_class
started_at / completed_at
~~~

`resource_requirement_versions`:

~~~text
id
organization_id
workspace_id
requirement_id
version
resource_kind
semantic_capabilities
hard_constraints
soft_preferences
required_by_workstream_id
quantity / unit
priority
blocking_level
required_by
expires_at
requirement_hash
supersedes_id
~~~

`workspace_resource_assignments`:

~~~text
id
organization_id
workspace_id
resource_handle_id
state: PROPOSED | ACTIVE | DRAINING | SUSPENDED | DETACHED
active_version_id
valid_from / valid_until
conflict_status
lock_version
~~~

`workspace_resource_assignment_proposals` jest kanonicznym parentem i zapisuje `id`, tenant keys, `resource_handle_id`, proponowany envelope, validation snapshot, conflict results, status, revision hash i decision reference. `workspace_account_assignment_proposals` posiada `resource_assignment_proposal_id UNIQUE`, `social_account_id`, identity/profile checks i account-owner approval requirements; nie może być potwierdzony bez parent proposalu.

`workspace_resource_assignment_versions`:

~~~text
id
organization_id
workspace_id
assignment_id
version
allowed_roles
allowed_semantic_capabilities
allowed_action_types
allocation_share_bps
fairness_weight
capacity_minutes_per_window
capacity_envelope_json
budget_envelope_json
approval_policy_id
timezone
effective_from
supersedes_id
created_by
assignment_hash
~~~

`resource_allocations` i ich immutable wersje:

~~~text
resource_allocations:
  id / organization_id / workspace_id
  requirement_id / resource_assignment_id
  state / active_version_id / lock_version

resource_allocation_versions:
  id / organization_id / workspace_id
  allocation_id / version
  requirement_version_id
  workspace_resource_assignment_version_id
  capacity_reservation_id nullable
  capacity_reservation_generation nullable
  selected_capabilities
  allocation_quantity / allocation_unit
  valid_from / valid_until
  evidence_refs / allocation_hash / supersedes_id
~~~

`capacity_reservations`:

~~~text
id
organization_id
workspace_id
resource_allocation_id
workspace_resource_assignment_version_id
quantity / unit
generation
state: REQUESTED | HELD | CONSUMED | RELEASED | EXPIRED | REJECTED | CANCELLED
held_until
consumed_at / released_at / expired_at
lock_version
~~~

`task_resource_bindings`:

~~~text
id
organization_id
workspace_id
task_id
resource_role
resource_allocation_version_id
required_capabilities
binding_hash
~~~

`TaskResourceBinding` przechowuje tylko kanoniczny `resource_allocation_version_id`; assignment version oraz opcjonalna reservation/generation są wyprowadzane z tej immutable wersji alokacji. `capacity_reservations` ma unique tuple `(id, generation, resource_allocation_id, workspace_resource_assignment_version_id)`, a `resource_allocation_versions` wskazuje go composite FK. Analogiczne composite FKs pilnują tenant keys i requirement/allocation chain. Dispatch envelope oraz `ExecutionIntent` przypinają binding hash; claim i wykonanie ponownie porównują reservation generation przed consume. Nie da się połączyć assignmentu A, allocation B i reservation C osobnymi polami bindingu.

`account_assessments`:

~~~text
id
organization_id
social_account_id
workspace_id nullable
channel_surface_id nullable
account_age_value or UNKNOWN
visible_reputation_metrics
profile_completeness
prior_activity_summary
membership_summary
capability_snapshot_set_id
recommended_readiness_stage
evidence_refs
confidence
observed_at
~~~

`decision_proposal_revisions`:

~~~text
id
organization_id
workspace_id
decision_proposal_id
revision
decision_kind
subject_type / subject_id / subject_version_id
question
options
recommended_option_id
base_context_snapshot_id
evidence_refs
impact_diff / budget_diff / policy_diff
precondition_hash
risk_level
required_approver_role
expires_at
default_on_expiry
revision_hash
~~~

`alerts`:

~~~text
id
organization_id
workspace_id nullable
scope_level: ORGANIZATION | WORKSPACE
type
severity
scope_type / scope_id
fingerprint
summary
evidence_snapshot_id
status
occurrence_count
first_seen_at / last_seen_at
snoozed_until / resolved_at
~~~

Constraint wymusza `(scope_level='ORGANIZATION' AND workspace_id IS NULL)` albo `(scope_level='WORKSPACE' AND workspace_id IS NOT NULL)`. Alert organizacyjny i workspace'owy mają odrębne fingerprint namespaces oraz odrębne polityki RLS.

`planning_context_snapshots` przypina dokładnie:

~~~text
project_definition_version_id
intelligence_profile_version_id
active_plan_version_id
principal_version_id
workspace_resource_assignment_version_ids
resource_allocation_version_ids
workspace_channel_binding_version_ids
marketing_registry_snapshot_id
platform_definition_version_ids
connector_definition_version_ids
channel_surface_version_ids
resource_type_definition_version_ids
semantic_capability_version_ids
platform_capability_mapping_version_ids
channel_archetype_version_ids
channel_surface_archetype_binding_version_ids
business_archetype_definition_version_ids
marketing_motion_version_ids
community_snapshot_version_ids
community_rule_snapshot_ids
knowledge_document_version_ids
memory_snapshot_id
claims_snapshot_id
prompt / router / context compiler versions
compiled_jsonb
content_hash
~~~

`marketing_registry_snapshots` jest immutable manifestem wszystkich wersji użytych przez planowanie: platform, connectorów, channel surfaces, resource types, semantic capabilities, capability mappings, channel archetypes, surface bindings, business archetypes i marketing motions. Planning snapshot kopiuje krytyczne version IDs dla łatwego constraint checku i dodatkowo wskazuje cały manifest przez `marketing_registry_snapshot_id`; registry nie jest odczytywany „na żywo” w połowie runa.

`communities` jest stabilnym rozszerzeniem 1:1 `ChannelSurface`; zmienna treść żyje wyłącznie w snapshotach:

~~~text
id
channel_surface_id UNIQUE
active_system_snapshot_version_id nullable
active_system_rule_snapshot_id nullable
state
lock_version

community_snapshot_versions:
  id / community_id / version
  scope_level: SYSTEM | ORGANIZATION
  organization_id nullable
  culture / promotion_tolerance / moderation_signals
  evidence_refs / observed_at / freshness / content_hash
  supersedes_id
~~~

`community_rule_snapshots` ma ten sam jawny scope i immutable version/hash. Constraint wymusza `organization_id IS NULL` tylko dla read-only `SYSTEM`; organization snapshots podlegają RLS i nie stają się automatycznie globalne. Planning context przypina dokładne community snapshot/rule IDs. Assessment, observed content i signal zawsze wskazują kanoniczny `channel_surface_id`, a nie tworzą drugiego obiektu kanału.

`social_accounts`:

~~~text
id
organization_id
platform
account_type: PERSON | BRAND
label
external_account_id
display_name
real_owner_user_id
ownership_verified_at
status
last_identity_verified_at
created_at
~~~

`workspace_account_assignments`:

~~~text
id
organization_id
workspace_id
resource_assignment_id UNIQUE
social_account_id
browser_profile_id
browser_eligibility_state: ELIGIBLE | REAUTH_REQUIRED | QUARANTINED
last_identity_assessment_id
owner_consent_id
~~~

`workspace_account_assignment_versions`:

~~~text
id
organization_id
workspace_id
assignment_id
workspace_resource_assignment_version_id UNIQUE
version
owner_consent_version_id
required_identity_confidence
disclosure_policy_id
allowed_browser_profile_id
manual_takeover_policy
account_constraint_hash
effective_from
supersedes_id
created_by
~~~

Account version jest wyłącznie restrykcyjnym rozszerzeniem parent version. `effective_authorization = intersection(workspace_resource_assignment_version, account_constraints)`; brak extension version, REAUTH lub QUARANTINED oznacza brak browser eligibility, nigdy fallback do szerszych praw.

`tasks`:

~~~text
id
organization_id
workspace_id
plan_version_id
workstream_id
workstream_definition_version_id
goal_id
goal_definition_version_id
milestone_id
milestone_definition_version_id
stage_definition_version_id
daily_commitment_id
primary_resource_binding_id nullable
worker_assignment_version_id
task_type
required_capabilities
input_payload
priority
due_at
status
blocked_reason
planning_context_snapshot_id
lock_version
~~~

Task może mieć wiele `task_resource_bindings`, np. konto + budżet + repozytorium. `primary_resource_binding_id` służy tylko wygodnemu routingowi i musi wskazywać binding tego samego taska; authority zawsze wynika z kanonicznego łańcucha allocation version → assignment version → opcjonalna reservation generation.

`workspace_worker_assignments`:

~~~text
id
organization_id
workspace_id
ai_worker_definition_id
state
lock_version
~~~

`workspace_worker_assignment_versions`:

~~~text
id
organization_id
workspace_id
worker_assignment_id
version
allowed_task_types
model_routing_policy_id
quota_envelope_id
wip_limit
priority
effective_from
supersedes_id
~~~

`codex_connections`:

~~~text
id
organization_id
state
auth_type
email nullable
plan_type nullable
account_fingerprint nullable
auth_completed_at / reauth_required_at
active_model_capability_snapshot_id nullable
last_rate_limit_state
lock_version
~~~

`model_capability_snapshots` zapisuje `connection_id`, `include_hidden_policy`, `page_count`, `last_cursor`, `completeness_state`, `models_json`, `content_hash`, `observed_at` i `expires_at`. Router akceptuje tylko `completeness_state=COMPLETE` oraz niewygasły snapshot utworzony po ostatnim auth transition.

`thread_security_bindings`:

~~~text
id
organization_id
workspace_id
codex_connection_id
social_account_id nullable
role
principal_generation nullable
tool_manifest_version
security_policy_version
thread_id
runtime_session_id
binding_hash
state
~~~

`codex_run_threads`:

~~~text
id
organization_id
workspace_id
thread_security_binding_id
automation_run_id
job_attempt_id
attempt
prompt_version_id
router_policy_version
requested_model / requested_effort
effective_model
effective_effort_observation: CONFIRMED | ASSUMED_REQUESTED | UNOBSERVED
route_status: MATCHED | REROUTED_ACCEPTABLE | ROUTE_MISMATCH
planning_context_snapshot_id
started_at / completed_at
~~~

`action_proposal_revisions`:

~~~text
id
organization_id
workspace_id
proposal_id
revision
payload
payload_hash
source_fingerprint
intelligence_profile_version_id
plan_version_id
memory_snapshot_id
planning_context_snapshot_id
task_resource_binding_ids
prompt_version_id
created_by_model_call_id
expires_at
~~~

`assignment_workstream_authorizations` określa uprawnienie konkretnej wersji assignmentu do workstreamu. `workstream_account_allocations` określa wyłącznie planowany przydział capacity w wersji planu; nie jest drugim źródłem autoryzacji.

`execution_intents` wiąże dokładnie:

~~~text
organization_id
workspace_id
proposal_revision_id
approval_decision_id
task_resource_binding_ids
planning_context_snapshot_id
one_time_grant_hash
expires_at
status
~~~

Budżet nie ma osobnego, nieokreślonego `budget_reservation_id`. Jest generycznym ConnectedResource typu `BUDGET`; jego kwota i waluta są rezerwowane jako `CapacityReservation` (`unit=CURRENCY_MINOR:<ISO-4217>`) i przypięte przez `TaskResourceBinding`. Consume tworzy jednocześnie wpis `action_budget_ledger`; brak lub zła reservation generation blokuje execution.

`browser_profiles`:

~~~text
id
organization_id
social_account_id
home_runner_id
encrypted_storage_ref
browser_product
browser_build
browser_build_digest
playwright_version
os_arch
profile_schema_version
status
~~~

Obowiązuje partial unique index `UNIQUE(social_account_id) WHERE status NOT IN ('RETIRED','DELETED')`.

Browser chain zachowuje tenant key przez composite FK: `browser_profiles(organization_id, social_account_id) → social_accounts`, `profile_leases(organization_id, profile_id) → browser_profiles`, `browser_sessions(organization_id, profile_id) → browser_profiles` i analogicznie dla interaction/stream sessions. Żaden lookup profilu, lease'u ani kapsuły nie opiera się na samym globalnym `id`.

`profile_leases`:

~~~text
organization_id
profile_id
generation
owner_runner_id
browser_session_id
heartbeat_at
expires_at
state
~~~

`browser_sessions`:

~~~text
id
organization_id
profile_id
lease_generation
runner_id
capsule_instance_id
os_pid
process_started_at
process_instance_id
display_server_pid / display_server_health
window_manager_pid / window_manager_health
stream_gateway_instance_id / stream_gateway_health
capsule_supervisor_identity
browser_build_digest
browser_mode
cdp_endpoint_ref
display_id
stream_transport
last_seen_at
status
~~~

`interaction_leases`:

~~~text
id
organization_id
browser_session_id
mode: AUTOMATION | HUMAN
owner_type / owner_id
generation
heartbeat_at
expires_at
state: ACTIVE | REVOKED | EXPIRED | RELEASED
lock_version
~~~

Obowiązuje partial unique index `UNIQUE(browser_session_id) WHERE state='ACTIVE'`. FK `(organization_id, browser_session_id)` oraz CAS na `generation` uniemożliwiają lease lub input spoza właściwej kapsuły.

`stream_sessions`:

~~~text
id
organization_id
browser_session_id
user_id
role: VIEW_ONLY | CONTROL
ticket_hash
profile_lease_generation
interaction_lease_id nullable
interaction_lease_generation nullable
issued_at
exchanged_at
ticket_expires_at
stream_session_expires_at
idle_timeout_at
cookie_expires_at
revoked_at
~~~

`jobs`:

~~~text
id
organization_id
workspace_id nullable
job_type
routing_key
priority
payload_ref
dispatch_version
status
not_before
attempt_limit
idempotency_key
lock_version
~~~

`job_attempts` i `job_leases` zapisują co najmniej `attempt_id`, `job_id`, `attempt_no`, `dispatch_version`, `lease_generation`, `lease_owner`, `status`, `not_before`, `heartbeat_at`, `expires_at`, `started_at`, `completed_at` i `failure_class`. Obowiązuje `UNIQUE(job_id, attempt_no)`. Każdy mutujący zapis workera używa CAS po pełnym fencing tuple.

`outbox_events` zapisuje `id` używane jako AMQP `message_id`, aggregate/job/attempt IDs, `dispatch_version`, routing key, payload reference, `publish_attempts`, `last_return_reason`, `confirmed_at` i `sent_at`. `inbox_receipts` zapisuje `consumer_name`, `message_id`, `job_id`, `dispatch_version`, `received_at` i wynik deduplikacji; obowiązuje `UNIQUE(consumer_name, message_id)`.

`daily_goal_instances`:

~~~text
id
organization_id
workspace_id
goal_id
window_start
window_end
baseline_snapshot_set_id
budget_snapshot
outcome_observation_deadline
evaluation_snapshot_set_id
data_quality
evaluated_at
status
health
~~~

`daily_commitments` posiada obowiązkowy `daily_goal_instance_id`.

Po weryfikacji konta obowiązuje:

~~~text
UNIQUE(organization_id, platform, external_account_id)
~~~

### 18.2. Izolacja workspace'u w Postgresie

Każda tabela należąca do workspace'u ma `organization_id NOT NULL` i `workspace_id NOT NULL`. Tabele organizacyjne, np. Connected Resource Pool, mają `organization_id NOT NULL` i nie udają rekordu workspace'owego; rekordy o mieszanym scope mają constraint zgodności `scope_level/workspace_id`. Relacje krytyczne używają composite foreign keys, aby błędny kod nie mógł powiązać rekordu z innym tenantem:

~~~text
(organization_id, workspace_id, parent_id)
→ parent(organization_id, workspace_id, id)
~~~

Runtime role nie ma `BYPASSRLS`; produkcja używa `FORCE ROW LEVEL SECURITY` i `SET LOCAL app.organization_id`, `app.workspace_id`, `app.actor_id` w każdej transakcji po pobraniu połączenia z poola. Dla scope organizacji istnieją osobne policies wymagające `app.organization_id` i jawnego braku workspace scope. Osobna rola migracyjna nie jest używana przez API ani workery.

Globalny dispatcher, outbox publisher i allocator nie działają przez szeroki serwisowy login omijający RLS. Istnieją trzy odrębne role `LOGIN NOINHERIT`: `dispatcher_login`, `outbox_publisher_login` i `allocator_login`; nie mają `BYPASSRLS` ani bezpośrednich praw do tabel. Otrzymują tylko `CONNECT`, `USAGE` do wąskiego schematu `control_api` oraz `EXECUTE` na dokładnie wskazanych sygnaturach funkcji swojej roli.

Funkcje `control_api` są `SECURITY DEFINER`, mają `SET search_path = pg_catalog`, używają wyłącznie w pełni kwalifikowanych nazw tabel, nie wykonują dynamic SQL i należą do `control_plane_fn_owner NOLOGIN BYPASSRLS`. Ten owner nie posiada tabel ani prawa logowania; dostaje tylko minimalne `SELECT/INSERT/UPDATE` potrzebne konkretnym funkcjom. Migracja wykonuje `REVOKE ALL ON FUNCTION ... FROM PUBLIC` i jawne `GRANT EXECUTE ON FUNCTION control_api.claim_dispatch_batch(integer) TO dispatcher_login`, analogicznie dla fenced outbox result i allocatora. Kod, sygnatury, owner oraz grants są testowane i auditowane jako uprzywilejowana powierzchnia.

`claim_dispatch_batch(limit)` zwraca jedynie bezpieczny envelope z tenant scope, job/attempt IDs, dispatch/lease generation, task binding hash i reservation generations. `claim_outbox_batch(limit)` oraz `mark_outbox_result(event_id, claim_generation, result)` są fenced. `match_resource_candidates(requirement_version_id)` sam odczytuje organization/workspace z immutable requirement version i zwraca tylko sanitizowane handles — nie ufa przekazanym przez caller parametrom tenant scope. Po claimie worker rozpoczyna nową transakcję, ustawia context z envelope zwróconego przez funkcję i dopiero wtedy odczytuje dane domenowe. Job organizacyjny z `workspace_id IS NULL` przechodzi wyłącznie przez org-scope policy.

Ta sama izolacja obejmuje vector/keyword retrieval, cache keys, outbox/inbox consumers, background jobs, decision center i artifact authorization. Pool hook wykonuje `RESET ALL` po check-in i przed ponownym checkout; testy wstrzykują wyjątek oraz reuse tego samego connection między tenantami i potwierdzają brak pozostałego `app.*` contextu. Zasoby organizacji są widoczne Principalowi dopiero jako sanitizowane `resource_handles` po assignment/allocation; nie widzi on tokenów ani inventory innych workspace'ów.

## 19. Bezpieczeństwo operacyjne

Security baseline powstaje przed pierwszym prawdziwym logowaniem, a nie na końcu roadmapy.

### 19.1. Profile i artefakty

- storage profili jest zaszyfrowany i dostępny tylko dla Browser Runnera,
- profile nie są montowane do procesu Codexa,
- katalogi mają minimalne uprawnienia,
- trace i screenshot są wyłączone podczas loginu i MFA,
- trace po zalogowaniu ma krótki TTL, ACL i szyfrowanie,
- `acceptDownloads=false` domyślnie,
- backup tylko na całkowicie zamkniętym profilu,
- domyślnym recovery jest reauth,
- usunięcie konta używa procesu `DRAINING → DELETING → DELETED`,
- `DELETING` obejmuje profil, cold snapshots, artefakty, metadata CDP oraz crypto-erasure klucza.

### 19.2. Streamowany login i Session Capsule

- ekran, klawisze, clipboard i CDP trace nie są nagrywane podczas loginu lub MFA,
- clipboard dwukierunkowy, file upload/download i druk są domyślnie wyłączone,
- StreamTicket jest jednorazowy, krótko żyjący, przechowywany tylko jako hash i wymieniany przez POST,
- iframe działa na osobnym originie z restrykcyjnym CSP oraz `frame-ancestors` ograniczonym do dashboardu,
- VNC/WebSocket wymaga autoryzowanej `StreamSession`; surowy endpoint nie jest publiczny,
- tylko jeden `InteractionLease` może wysyłać input,
- viewer i controller mają różne uprawnienia egzekwowane przez serwer,
- profile są szyfrowane oddzielnymi DEK; aktywna kapsuła montuje wyłącznie swój odszyfrowany profil,
- passkey/security-key forwarding nie jest obiecywany w E1 i wymaga osobnego projektu.

### 19.3. Chromium

- pinned Chrome for Testing,
- custom `user-data-dir`,
- CDP na losowym porcie loopback,
- port nigdy nie trafia do frontendu lub reverse proxy,
- Chromium sandbox włączony,
- proces non-root,
- zakaz `--no-sandbox`,
- ograniczony filesystem i egress,
- osobny użytkownik dla runnera.

### 19.4. URL i network policy

Kontrola obejmuje:

- canonicalizację URL,
- exact origin/explicit host set jako granicę uprawnień,
- eTLD+1 wyłącznie do klasyfikacji, nie autoryzacji,
- tylko HTTPS poza jawnie testowym fixture,
- redirecty,
- popupy i nowe karty,
- iframe'y,
- WebSockety,
- service workery,
- downloady,
- prywatne i metadata IP sprawdzane po DNS resolution przez egress proxy, również przy rebindingu,
- blokadę top-level navigation do `file:`, `data:`, `javascript:`, `chrome:` i `devtools:`.

Browser routing jest defense-in-depth; nadrzędna jest polityka egress runnera.

### 19.5. Prompt injection

Treść stron jest niezaufanym inputem. Codex:

- nie ma bazy, sekretów, profili ani ogólnego filesystemu,
- nie ma shella ani niekontrolowanego exec,
- nie może zmienić taska, account assignmentu ani budżetu,
- dostaje task-scoped tools i krótki context snapshot,
- nie wykonuje surowych zapisujących click/fill/press,
- nie może rozszerzyć allowlisty,
- nie może sam zatwierdzić własnego proposal.

### 19.6. Konflikty

Conflict Engine wykrywa:

- kilka workspace'ów konkurujących o tę samą identity/capacity,
- kilka kont kierowanych do tego samego wątku,
- bardzo podobne komentarze,
- wzajemne wspieranie własnych kont,
- treść jednego klienta w kontekście innego,
- niezgodność disclosure i roli konta,
- powtarzalne zachowania powstałe przez błędny plan.

## 20. Ekrany produktu

Poniższa lista opisuje komplet ekranów docelowych. Etap 1 wymaga pięciu z nich: Connected Resource Pool (20.2) w wersji „połącz konto i zweryfikuj sesję”, Research and Signals (20.9) jako widok znalezionych dyskusji z oceną jakości, Alert Center (20.10) w zakresie typów alertów z 22.10 — bez pełnego Decision Center z typed handlerami, Action Approvals (20.11) jako kolejka decyzyjna operatora oraz Embedded Browser (20.12) wyłącznie w ścieżce logowania i takeover. Pozostałe ekrany powstają w etapach opisanych w sekcji 21.

### 20.1. Organization Overview

- workspace'y i ich aktywne etapy,
- Principal health i goal health,
- dzisiejsze commitments,
- otwarte alerty i decyzje,
- zasoby wymagające połączenia lub interwencji,
- zużycie Codexa,
- stan kolejek, blokady i guardrails.

### 20.2. Connected Resource Pool

- wszystkie konta społecznościowe, fora, business/ad profiles, repozytoria i analytics,
- drag-and-drop do workspace'u,
- owner, profil, runner, health i AccountAssessment,
- metryki platformowe,
- capabilities,
- allocation i assignmenty,
- globalne budżety,
- kolejka i aktywna sesja,
- `Połącz`, `Zaloguj`, `Otwórz interaktywnie`, `Sprawdź`, `Wstrzymaj`, `Odłącz`.

### 20.3. Resource Needs

- requirements pogrupowane jako `BLOCKING`, `RECOMMENDED`, `OPTIONAL`,
- uzasadnienie Sol, evidence, confidence i planowany workstream,
- możliwe istniejące resource matches,
- `Połącz konto`, `Wybierz istniejące`, `Zatwierdź budżet`, `Dodaj materiał`,
- `Odrzuć`, `Zastąp`, `Snooze`, `Ask AI`.

### 20.4. Workspace Command Center

- Project Definition i aktywny Intelligence Profile,
- status Workspace Principal i czas następnego review,
- obecny stage,
- aktywny plan,
- przypisane zasoby i workers,
- równoległe workstreamy,
- goals i health,
- Today,
- najważniejsze metryki,
- sygnały, alerty i pending decisions,
- Start/Pause/Resume/Replan.

### 20.5. Knowledge and Intelligence

- ingest README, repozytorium, URL, dokumentów i notatek,
- status ekstrakcji i wersje źródeł,
- wersje Project Definition i Intelligence Profile,
- claims i evidence,
- canonical facts, memory candidates i conflicts,
- provenance, freshness, confidence i retention,
- preview dokładnego Postgres context snapshotu użytego przez run.

### 20.6. Marketing Plan

- business archetype i channel hypotheses,
- mutowalny diagram etapów,
- stage gates,
- goals i milestones,
- równoległe workstreamy,
- resource/model allocations,
- dependencies,
- review triggers i stop-loss,
- wersje i diff planu,
- approve/reject revision.

### 20.7. Today

- dzienne commitments,
- capacity per konto, worker i workstream,
- action targets i budgets,
- external outcomes,
- Accept/Edit/Reject,
- przewidywane zużycie Codexa, browsera i ad budgetu.

### 20.8. Kanban

- Backlog, Ready, Today, Doing, Waiting Approval, Waiting External, Blocked, Verifying, Done,
- swimlane po workstreamie, koncie i goalu,
- dowody wykonania,
- blokady,
- model, effort, prompt version i verifier outcome.

### 20.9. Research and Signals

- Channel Discovery,
- surfaces, Community Maps i lokalne/paid channel assessments,
- Opportunity Inbox,
- post performance heatmaps,
- paid metrics i conversion windows,
- sentyment, topic trends, godziny i dni,
- źródła, freshness i confidence.

### 20.10. Alert and Decision Center

- jedna skrzynka alertów, resource gaps, decyzji, konfliktów i takeover,
- opis problemu, rekomendacja Sol, alternatywy i evidence,
- impact/budget/policy diff,
- `Approve`, `Edit & Approve`, `Reject`, `Snooze`, `Ask AI`, `Resolve with AI`,
- pole dodatkowej instrukcji człowieka,
- historia rewizji, approver, application status i audit.

### 20.11. Action Approvals

- source snapshot,
- target i konto,
- immutable draft revision,
- diff edycji,
- claim checks,
- policy decision,
- hash i expiry,
- approve/edit/reject.

### 20.12. Embedded Browser

~~~text
┌──────────────────────────────────────────────────────────────┐
│ Acme / Reddit Jan                     HUMAN CONTROL 04:52 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│       streamowany pełny ekran Chrome / login / MFA           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ Identity: verified   Profile: leased   Stream: encrypted     │
│ [Oddaj kontrolę] [Sprawdź login] [Safe stop] [Zamknij]       │
└──────────────────────────────────────────────────────────────┘
~~~

UI pokazuje, kto ma InteractionLease, TTL, identity i stan reconciliation. Nie pokazuje portów CDP/VNC, cookies ani ścieżki profilu. Login/MFA automatycznie wyłączają screenshot, trace, clipboard i recording.

### 20.13. AI Workers, Connections and Model Operations

- połączenie ChatGPT,
- Workspace Principal per workspace,
- katalog logicznych workers,
- assignmenty workerów do workspace'ów i workstreamów,
- rola, WIP, quota envelope i routing policy,
- taski zakończone, approval rate, latency i verifier outcomes,
- dostępne modele i reasoning efforts,
- limity i reset,
- router policy,
- aktywne turny,
- prompt versions,
- eval results i canary.

### 20.14. Runs, Queue and Audit

- job, attempt, Rabbit dispatch, task, proposal, approval i execution intent,
- tool calls,
- requested model/effort, observed effective model i effort observation state,
- evidence,
- artefakty,
- profile/interaction lease generation,
- wynik i failure classification.

## 21. Mapa etapów produktu

System opisany w tym dokumencie nie zmienia się: workspace'y, Sol jako Workspace Principal i orkiestrator, mniejsze modele wykonawcze, headless Chrome sterowany przez CDP, warstwa zbierania danych, registry kanałów i pipeline approvali zostają w całości. Ta sekcja rozkłada ten sam system na etapy dowodu i ustala, w jakiej kolejności wolno go budować.

Etap nie jest fazą harmonogramu. Faza z roadmapy opisuje pracę do wykonania; etap opisuje twierdzenie, które musi zostać udowodnione danymi, zanim wolno zainwestować w kolejne.

### 21.1. Zasada nadrzędna

Etapy są sekwencyjne pod względem **dowodu**, a nie pod względem kodu. Wolno wcześniej napisać migrację, adapter albo schemat, który przyda się dopiero w E3 — ale nic z tego nie liczy się jako postęp etapu, dopóki bieżący etap nie ma evidence. Etap 1 jest bramką pass/block całego projektu.

Niezmienniki etapowania:

1. Etap 1 jest bramką `PASS/BLOCK`. `BLOCK` zatrzymuje budowę E2–E5; nie przesuwa terminu i nie zamienia się w „zrobimy to później".
2. Kolejny etap nie startuje przed `PASS` poprzedniego. Praca przygotowawcza jest dozwolona, awans — nie.
3. Etap 1 musi zostać zwalidowany na co najmniej dwóch różnych `ChannelArchetype`, np. `THREADED_COMMUNITY` i `PROFESSIONAL_GRAPH`. Jeden kanał nie dowodzi generalizacji, tylko dopasowania do jednej platformy.
4. Każda zewnętrzna akcja w Etapie 1 jest wykonywana po akceptacji człowieka. `PUBLISH_REPLY`, `SUBMIT_COMMENT` i reakcja/„lajk" są w E1 mapowane wyłącznie na `AUTOMATED_AFTER_APPROVAL` z jednorazowym `ExecutionIntent`; mapowanie `AUTOMATED` dla capability zapisującej nie istnieje w E1.
5. Warstwą egzekucji jest `capsule_mode = HEADLESS` sterowany przez CDP. `INTERACTIVE` (headful na wirtualnym displayu, stream do dashboardu) pozostaje ścieżką wyjątkową i jest uruchamiany wyłącznie tam, gdzie jest nieunikniony: pierwsze logowanie, MFA, challenge, weryfikacja tożsamości i human takeover. Mówimy to wprost: logowania i MFA nie da się wykonać headless — headless nie ma okna, które człowiek mógłby zobaczyć i kliknąć, a MFA z definicji wymaga obecności człowieka. Reszta pracy nie potrzebuje renderowanego ekranu i nie dostaje go na stałe.
6. Capability odczytu jest funkcją połączonego konta i jego stanu, a nie założeniem. Rozdzielamy przy tym dwie rzeczy zgodnie z 14.7: `state` capability (`SUPPORTED`, `DEGRADED`, `MANUAL_ONLY`, `UNSUPPORTED`, `UNKNOWN`) mówi, czy da się to zrobić, a `execution_mode` (`AUTOMATED`, `AUTOMATED_AFTER_APPROVAL`) — kto to zatwierdza. Na platformie za login wallem (Reddit, LinkedIn, X) `SEARCH_CONTENT`, `READ_CONTENT`, `READ_THREAD` i `READ_METRICS` bez konta w stanie `READY` mają `state = UNSUPPORTED` albo `UNKNOWN`. Utrata sesji degraduje capability i zatrzymuje zależne taski, zamiast po cichu zwracać puste dane.
7. Registry rozróżnia ścieżkę bez konta (oficjalne, czytelne API, np. Hacker News) od ścieżki wymagającej konta i sesji. Ścieżka tańsza jest wybierana, gdy istnieje i jest zgodna z regulaminem; nie jest zakładana domyślnie i nie jest podstawiana pod platformę, która jej nie ma.
8. Target workspace'u — gdzie działamy, do kogo mówimy i jak promujemy — ustala Workspace Principal jako orkiestrator już w E1, ale na świadomie zawężonej powierzchni opisanej w 23.6: jeden workspace, dwa archetypy, dwa marketing motions, brak automatycznego awansu stage'u. Pełnia roli orkiestratora przychodzi w E2.
9. Etap nie podnosi sam sobie autonomii ani budżetów. Zmiana autonomy level, envelope'u albo mapowania capability na `AUTOMATED` zawsze przechodzi przez `DecisionProposal`.
10. Granica zakresu z sekcji 4 obowiązuje w każdym etapie bez wyjątku. Żaden etap nie kupuje zasięgu obchodzeniem zabezpieczeń, CAPTCHA, fingerprint spoofingiem, tworzeniem kont ani koordynowaniem kilku własnych kont wokół tej samej treści.

### 21.2. Etapy E1–E5

~~~text
E1  COMMUNITY ENTRY & TRUST                            [BRAMKA: PASS/BLOCK]
    discovery wartościowych dyskusji + human-approved komentarz/reakcja
    + budowa konta od zera + pomiar zaufania i zasięgu
    dowodzi: potrafimy z wyprzedzeniem wskazać dyskusje, które zdobywają
    zaufanie i zasięg, i zbudować od zera konto, które community przyjmuje
        │
        │  PASS wymagany — bez niego nie startuje nic poniżej
        ↓
E2  WORKSPACE & ORCHESTRATION
    workspace w dashboardzie, Workspace Principal jako orkiestrator,
    Intelligence Profile, plan, routing mniejszych modeli, assignmenty
    dowodzi: Sol sam ustala target (gdzie / do kogo / jak), dobiera modele
    i zasoby, a plan powstaje z registry i capabilities, nie z nazwy platformy
        ↓
E3  SCALE & MULTI-SURFACE
    więcej platform przez registry i adaptery, równoległe workstreamy,
    Daily Planner, Kanban, budżety, pełny Decision Center
    dowodzi: ten sam plan prowadzi wiele surfaces i torów równolegle
    bez zmiany core'u planera i bez utraty kontroli nad kontem
        ↓
E4  MEASUREMENT & REPLANNING
    pełny pomiar, community signals, goal health, replanning z human gate
    dowodzi: system mierzy skutek, odróżnia sygnał od szumu i sam proponuje
    korektę planu, którą zatwierdza człowiek
        ↓
E5  EXPANSION
    kanały płatne, wiele workspace'ów i klientów, RBAC, zdalne runnery
    dowodzi: model działa poza jednym operatorem i jednym produktem

E1 BLOCK → STOP. Nie budujemy E2–E5; dokument zostaje jako plan referencyjny.
~~~

### 21.3. Etap 1 jest bramką pass/block

`PASS` wymaga obu dowodów łącznie. Jeden bez drugiego nie zalicza bramki:

~~~text
A. DISCOVERY
   potrafimy wskazać dyskusje, które zdobywają zaufanie i duże zasięgi,
   ZANIM je zdobędą — a nie opisać je po fakcie

B. ACCOUNT FROM ZERO
   konto zbudowane od zera realnie porusza się w community:
   odpowiedzi są przyjmowane, wracają rozmówcy, rośnie obserwowalny
   sygnał zaufania, a nie tylko licznik wykonanych akcji

WARUNEK GENERALIZACJI
   A i B potwierdzone na co najmniej dwóch różnych ChannelArchetype
~~~

Reguły dowodu:

- dowodem jest `EXTERNAL_OUTCOME`, nie `ACTION_TARGET`; liczba przygotowanych i zatwierdzonych komentarzy mówi tylko, ile pracy wykonaliśmy,
- pomiar idzie przez append-only snapshoty (7.6) i community signals (7.7), z sample size, oknem, freshness i confidence; `UNKNOWN` nie jest sukcesem,
- konto od zera startuje z `presence_track` w `UNASSESSED/BASELINED` i przechodzi `OBSERVE_ONLY → VISIBILITY_BUILDING → SOFT_PARTICIPATION` (8.1); pominięcie stanu unieważnia dowód B,
- każda akcja zewnętrzna ma `ActionProposal`, approval człowieka, `ExecutionIntent` i evidence wykonania,
- ten sam `AccountAssessment` i te same reguły baseline'u obowiązują na obu archetypach; osobne „ręczne dopieszczenie" jednego kanału nie liczy się jako generalizacja.

Konsekwencje wyniku:

| Wynik bramki | Co się dzieje |
| --- | --- |
| `PASS` | E2 startuje; ustalenia E1 (surfaces, presence tracks, capability mapping, baseline) stają się wejściem orkiestratora |
| `CONDITIONAL_PASS` | etap trwa dalej z nowym stop-loss albo ze zmienioną hipotezą wejścia; nie awansujemy „na zaliczenie" |
| `BLOCK` | projekt się zatrzymuje; nie budujemy E2–E5, a wznowienie wymaga nowej hipotezy wejścia i nowej wersji tej sekcji |

### 21.4. Mapowanie etapów na sekcje dokumentu

| Etap | Co dowodzi | Kluczowe capabilities | Sekcje realizowane |
| --- | --- | --- | --- |
| **E1 COMMUNITY ENTRY & TRUST** | discovery wartościowych dyskusji, human-approved uczestnictwo i zdolność zbudowania konta od zera w co najmniej dwóch archetypach | `DISCOVER_SURFACES`, `SEARCH_CONTENT`, `READ_CONTENT`, `READ_THREAD`, `READ_METRICS` jako `AUTOMATED` tylko tam, gdzie pozwala stan konta albo publiczne API; `PREPARE_COMMENT`/`SUBMIT_COMMENT`, `PUBLISH_REPLY`, `PREPARE_REACTION`/`SUBMIT_REACTION` wyłącznie `AUTOMATED_AFTER_APPROVAL`; `READ_IDENTITY`, `READ_ACCOUNT_METRICS`, `MANUAL_TAKEOVER`, `REQUEST_HUMAN_ACTION` | 4; 5.1; 5.4; 5.6; 7.1; 7.3; 7.4; 7.5; 7.6; 8.1; 8.3; 11.1; 11.2; 6.2–6.5 (Project Definition, ingest, Intelligence Profile); 9.2; 9.7; 9.8; 11.1; 12.1–12.2 (Sol na zawężonej powierzchni wg 23.6, Luna/Terra jako workerzy); 13.1–13.4 (runtime Codexa w minimalnym zakresie); 14.1–14.8; 16.1–16.3; 19.1–19.5; 20.2; 20.9; 20.10; 20.11; 20.12; 23.1 i 23.6 (TargetDefinition w profilu E1); sekcja 24 (warstwa zbierania danych) |
| **E2 WORKSPACE & ORCHESTRATION** | że Workspace Principal sam ustala target, rozdziela pracę na mniejsze modele i wie, który zasób jest do czego potrzebny | `PrincipalRun` z typed proposal bundle i authority grantem; `WorkspaceIntelligenceProfile`; `ChannelHypothesis` i `ChannelSurfaceAssessment`; `ResourceRequirement → allocation → WorkspaceResourceAssignment`; deterministic Model Router (Sol/Terra/Luna); `MarketingRegistrySnapshot` przypięty do runu | 5.2; 5.5; 5.6; 5.7; 6.1–6.8; 7.2; 8.2; 8.5; 9.1–9.3; 9.5; 12.1–12.7; 13.1–13.4; 17.3; 18; 20.3–20.6; 20.13 |
| **E3 SCALE & MULTI-SURFACE** | że kolejna platforma to registry + adapter, a nie zmiana core'u, i że kilka torów pracy mieści się w budżetach i concurrency | rozszerzone capability mappings kolejnych connectorów; `Workstream`, `DailyCommitment`, Kanban jako projekcja state machines; hierarchiczne budżety i weighted fairness; `Alert` + `DecisionProposal` jako jedna skrzynka | 5.3; 7.1; 7.3; 8.4; 9.6; 9.8; 10.1–10.4; 11; 11.1; 11.2; 11.4; 15.1; 15.2; 20.7; 20.8; 20.10; 20.14 |
| **E4 MEASUREMENT & REPLANNING** | że system mierzy skutek, odróżnia sygnał od szumu i proponuje korektę planu, której nie stosuje sam | okna pomiarowe i normalizacja metryk; `community_signals`, `sentiment_observations`, `topic_trends`; goal health `NO_DATA/ON_TRACK/AT_RISK/OFF_TRACK`; `PlanRevisionProposal` i `OperationalAdjustment` w envelope | 7.5; 7.6; 7.7; 9.5; 9.6; 9.7; 17.1; 17.2; 17.3; 20.9; 20.10 |
| **E5 EXPANSION** | że model działa poza jednym operatorem, jednym produktem i kanałami organicznymi | `MANAGE_CAMPAIGN`, `READ_AD_METRICS` i ad budget ledger; wiele write assignmentów na konto; RLS/tenant isolation w produkcji; RBAC i wielu użytkowników; zdalne Browser Runnery | 5.3 (pełne N:M); 6.7 (RLS jako defense-in-depth w produkcji); 7.5 (kanały płatne); 9.8 (budżety reklamowe); 17.4 (autonomy levels); 25.3 (E5 w roadmapie) |

Sekcje wymienione przy etapie nie znikają z pozostałych — E1 również działa na niezmiennikach z 3.1 i pipeline z 16, a E5 nadal podlega granicy zakresu z 4. Tabela mówi, w którym etapie dana sekcja przestaje być planem, a zaczyna być zaimplementowana i przetestowana.

### 21.5. Co świadomie nie wchodzi do E1

E1 ma udowodnić wejście w community, a nie zbudować całą platformę. Wszystko poniżej jest częścią systemu i zostaje w dokumencie — tylko nie jest warunkiem bramki:

| Odłożone z E1 | Wchodzi w | Dlaczego nie w E1 |
| --- | --- | --- |
| Multi-tenant RLS, `FORCE RLS`, composite FK harness i testy dwóch tenantów (6.7) | E3, twardo przed E5 | E1 ma jednego operatora i jeden workspace, więc izolacja tenantów niczego w bramce nie dowodzi ani nie fałszuje; workspace-scoped read model obowiązuje już w E1. |
| RabbitMQ, transactional outbox/inbox, job leases i reaper (3.1 pkt 28, 15.1) | E3 | Przy jednym runnerze i kilkunastu akcjach dziennie PostgreSQL jako źródło prawdy w zupełności wystarcza; durable transport ma sens dopiero przy równoległych workstreamach. |
| Weighted fairness, `allocation_share_bps` i scheduler scoring (5.3, 10.3, 10.4) | E3 | W E1 istnieje jeden workspace i jeden write assignment na konto — nie ma o co konkurować, a fairness bez konkurencji jest martwym kodem. |
| Wiele workspace'ów i wiele write assignmentów na jedno konto (5.3) | E5 | Drugi workspace mnoży ryzyko pomylenia tożsamości, zanim udowodnimy, że pierwsze konto w ogóle działa. |
| Budżety reklamowe, ad accounts, `MANAGE_CAMPAIGN`, `READ_AD_METRICS` (7.5, 9.8) | E5 | Płatny zasięg kupuje uwagę i zamazałby dokładnie ten sygnał, który E1 ma zmierzyć. |
| Daily Planner, `DailyGoalInstance`, `DailyCommitment` i Kanban (9.6, 10, 11) | E3 | W E1 dzienny limit akcji wynika ze stanu presence tracku (22.8) i capu kolejki approvali (22.10); odkładamy kompilowany plan dnia, nie same limity. |
| `MarketingMotionCompiler`, wersjonowanie planu i replanning (8.2, 8.5, 17.1–17.3) | E2 i E4 | Nie ma czego replanować, dopóki nie wiadomo, czy wejście w community w ogóle działa. |
| Model Router z fallback chain, quota policy, evale i canary (12.4–12.7) | E2 | E1 używa stałych, jawnie zapisanych tras model/effort per rola; routing optymalizuje koszt, którego jeszcze nie ma. |
| Decision Center jako pełna skrzynka i typed decision handlers (11.2, 11.4) | E2 i E3 | E1 ma alerty (11.1) i dwa przepływy człowieka: approve konkretnej akcji i takeover przy challenge; pełna skrzynka decyzji z typed handlerami przychodzi później. |
| Mapowanie capability zapisującej na `AUTOMATED` (7.3, 17.4) | nie wcześniej niż po E4 | E1 dowodzi jakości wejścia, a nie przepustowości; autonomia bez zmierzonego skutku jest tylko szybszym sposobem na spalenie konta. |
| WebRTC jako `InteractiveTransport` (14.3) | E5 | noVNC wystarcza dla loginu, MFA i takeover, a to jedyne miejsca, w których E1 uruchamia tryb `INTERACTIVE`. |
| RBAC, wielu użytkowników i wiele `CodexConnection` (pkt 25 w „Decyzjach, których nie wolno rozmyć") | E5 | Bramka jest o community, nie o organizacji pracy zespołu. |
| Zdalne Browser Runnery i migracja profili między runnerami (5.1, 14.3) | E3 i E5 | E1 jest runner-affine na jednej maszynie; migracja profilu to osobne ryzyko utraty sesji. |

Odłożenie nie oznacza „bez planu". Każda pozycja ma etap docelowy i sekcję, w której jest już opisana; jeżeli pozycja wraca do E1, wraca jako zmiana tej sekcji, a nie jako cicho dołożony zakres.

### 21.6. Etap nie awansuje przez upływ czasu, tylko przez evidence

Etapy produktu nie są `PlanStageInstance` konkretnego workspace'u — to poziom programu. Używają jednak tego samego predykatu i tej samej dyscypliny dowodu, co stage gate z 8.3:

~~~text
product_stage_can_advance =
    stage_exit_evidence_complete
    AND minimum_sample_and_confidence_reached
    AND evidence_observed_in_at_least_two_windows
    AND generalization_requirement_met      # dla E1: dwa ChannelArchetype
    AND no_blocking_guardrail
    AND no_open_CRITICAL_alert_in_stage_scope
    AND human_gate_passed                   # zawsze, bez skrótu przez envelope
~~~

Różnice wobec 8.3 są dwie i obie są zaostrzeniem: awans etapu produktu zawsze wymaga człowieka, nawet gdyby mieścił się w zatwierdzonym envelope autonomii, i zawsze wymaga potwierdzenia na wymaganej liczbie archetypów.

Reguły:

1. Upływ czasu, zamknięcie sprintu ani „faza z roadmapy skończona" nie są dowodami. Roadmapa mówi, kiedy praca była planowana; bramkę zalicza wyłącznie evidence.
2. Evidence pochodzi z append-only snapshotów (7.6) i community signals (7.7) i zawiera źródło, okno, sample size, freshness oraz confidence. Brak pomiaru to `UNKNOWN`, nie zaliczenie.
3. `ACTION_TARGET` nigdy nie jest dowodem awansu — jest miarą wykonanej pracy. Awans opiera się na `EXTERNAL_OUTCOME` (9.2).
4. Awans jest zapisywany jako `DecisionProposal` z `decision_kind = ADVANCE_STAGE`, z evidence refs i diffem tego, co etap odblokowuje. Sol może rekomendować, ale nie zatwierdza własnej rekomendacji.
5. Gate review przypina `MarketingRegistrySnapshot`, wersje adapterów i wersje promptów użyte przy zbieraniu dowodów. Zmiana adaptera w trakcie okna unieważnia porównanie okien, a nie „poprawia wynik".
6. Niezaliczony gate ma trzy wyjścia: przedłużenie etapu z nowym stop-loss, zmiana hipotezy wejścia (inny surface albo archetyp) albo `BLOCK`. Nie istnieje wyjście „idziemy dalej mimo to".
7. Dla E1 `BLOCK` jest terminalny dla bieżącej wersji programu. Wznowienie wymaga nowej hipotezy wejścia, nowej wersji tej sekcji i ponownego przejścia bramki od początku.

## 22. Etap 1 — Community Entry & Trust (bramka pass/block)

### 22.1. Teza etapu

Etap 1 nie dowodzi, że potrafimy sterować przeglądarką ani że potrafimy wygenerować tekst. Dowodzi trzech rzeczy naraz: że system potrafi w wybranym community **znajdować dyskusje i posty, które realnie zyskują zaufanie i zasięg**, a nie takie, które mają tylko duże liczniki; że potrafi **wygenerować wkład, który człowiek zatwierdza i firmuje własną tożsamością** bez przepisywania go od zera; oraz że potrafi **poprowadzić konto od zera przez presence track** (`UNASSESSED → BASELINED → OBSERVE_ONLY → VISIBILITY_BUILDING → SOFT_PARTICIPATION`; `ESTABLISHED` jest już poza bramką E1) do stanu, w którym to konto realnie uczestniczy w community i gromadzi wokół siebie powracających rozmówców. Wszystkie trzy dowody muszą powstać równocześnie na co najmniej dwóch różnych archetypach kanału, w oknie pomiarowym z progami zapisanymi przed startem. Etap 1 jest bramką pass/block całego produktu: bez tych dowodów pozostałe etapy nie mają czego automatyzować.

### 22.2. Niezmienniki Etapu 1

1. Każda akcja zewnętrzna w E1 — komentarz, odpowiedź, reakcja, dołączenie do community — jest wykonywana wyłącznie po jednostkowej akceptacji człowieka i wyłącznie przez `ExecutionIntent` dla dokładnej, niezmiennej rewizji `ActionProposal`.
2. Nie istnieje w E1 tryb „auto-publish”, nawet dla akcji uznanych za niskiego ryzyka. Mapping capability dla wszystkich akcji zapisujących to `AUTOMATED_AFTER_APPROVAL`.
3. Konta zakłada i loguje człowiek. System nigdy nie tworzy konta ani tożsamości; „konto od zera” oznacza prawdziwe, ręcznie założone konto z jawną `identity_and_disclosure` z Intelligence Profile.
4. Capability odczytu jest funkcją połączonego konta i jego bieżącego stanu, a nie założeniem planera (sekcja 22.5).
5. Awans presence tracku wymaga dowodów stage gate, nigdy samego upływu czasu ani wieku konta.
6. Ranking wątków jest heurystyką porządkową, nie liczbowym score udającym rachunek platformy (sekcja 22.7).
7. Sygnał, którego nie widzimy z zewnątrz, ma stan `UNKNOWN`; nie jest zerem ani wartością domyślną.
8. Detekcja fake engagementu w E1 działa w trybie log-only: flaguje, nie odrzuca i nie zmienia `priority_tier`.
9. Draft ma TTL i wygasa razem z oknem wątku; wygaśnięcie zużywa budżet i nie uruchamia automatycznej regeneracji.
10. Niejednoznaczny submit kończy się `UNKNOWN` i reconciliation, nigdy automatycznym retry (niezmiennik 18 sekcji 3.1).
11. Progi PASS/BLOCK są zapisane w wersji planu przed startem etapu i nie podlegają negocjacji po zebraniu wyników.
12. Wynik E1 jest ważny tylko wtedy, gdy powstał na co najmniej dwóch różnych `ChannelArchetype`.
13. Jedno konto nie wspiera drugiego własnego konta w tym samym wątku; zakaz koordynowanego podbijania z sekcji 4 obowiązuje bez wyjątków także w E1.

### 22.3. Zakres — co budujemy w E1

Budujemy pionowy wycinek przez cały system, nie osobny prototyp.

~~~text
minimalny Connected Resource Pool
    2 archetypy kanału × 2 konta
    w każdym archetypie: 1 konto zbudowane OD ZERA (dowód główny)
                       + 1 konto z historią (baseline porównawczy, opcjonalne)
    ręczne logowanie i MFA w trybie INTERACTIVE
    AccountAssessment + capability snapshot dla każdego konta

warstwa wykonania
    headless Chrome + CDP dla całej pracy automatu
    INTERACTIVE tylko: login, MFA, challenge, identity check, takeover

registry i wiedza
    ChannelSurface + Community (rozszerzenie) dla wybranych community
    access_mode per surface (sekcja 22.5)
    Community Map (sekcja 7.4) budowana i odświeżana z odczytów
    MarketingRegistrySnapshot przypięty do każdego runu

discovery i ocena
    SEARCH_CONTENT + DISCOVER_SURFACES + READ_CONTENT + READ_THREAD + READ_METRICS
    ThreadQualityAssessment (sekcja 22.7)
    Opportunity Inbox jako kolejka wątków kandydackich

wkład i approval
    Draft Writer (Terra) + Policy/Claim Verifier
    ActionProposal → immutable revision → kolejka approvali operatora
    ExecutionIntent → Action Adapter → jedna próba → evidence

pomiar
    append-only snapshoty metryk wątku i konta (sekcja 7.6)
    presence_track_events
    E1 metrics projection dla tabeli PASS/BLOCK (sekcja 22.11)
~~~

Capability mapping obowiązujący w E1:

| SemanticCapability | Tryb w E1 | Uwaga |
| --- | --- | --- |
| `DISCOVER_SURFACES` | `AUTOMATED` | wyłącznie w granicach budżetu odczytu i access_mode |
| `SEARCH_CONTENT` | `AUTOMATED` | wyszukiwanie kandydatów w community |
| `READ_CONTENT` | `AUTOMATED` | wymaga połączonego konta na surface `ACCOUNT_REQUIRED` |
| `READ_THREAD` | `AUTOMATED` | pełny wątek: głębokość, autorzy, kolejność |
| `READ_METRICS` | `AUTOMATED` | tylko wartości faktycznie widoczne; reszta `UNKNOWN` |
| `PUBLISH_REPLY` | `AUTOMATED_AFTER_APPROVAL` | jedna próba, evidence, brak retry |
| `REACT_TO_CONTENT` (upvote/like) | `AUTOMATED_AFTER_APPROVAL` | osobny, niższy budżet dzienny |
| `JOIN_COMMUNITY` | `AUTOMATED_AFTER_APPROVAL` albo `MANUAL_ONLY` | zależnie od adaptera; wymaga `COMMUNITY_MEMBERSHIP` |
| `PUBLISH_CONTENT` (własny post/wątek) | poza zakresem E1 | wchodzi dopiero po PASS |
| `REQUEST_HUMAN_ACTION` | `AUTOMATED` | tworzy human task, nie błąd |
| `MANAGE_CAMPAIGN`, `READ_AD_METRICS` | `UNSUPPORTED` w E1 | brak kanałów płatnych w tym etapie |

Czego w E1 **nie** budujemy: kanałów płatnych, atrybucji konwersji, wielu operatorów i RBAC, zdalnych runnerów, automatycznego publikowania własnych postów, kampanii i `CAMPAIGN_ELIGIBLE`, adapterów pod trzeci i czwarty archetyp.

### 22.4. Warstwa wykonania: headless CDP, headful tylko tam, gdzie jest nieunikniony

Rdzeniem wykonania jest **headless Chrome sterowany przez CDP**. Discovery, odczyt wątków, odczyt metryk, przygotowanie akcji i sam submit działają w trybie `HEADLESS` (sekcja 14). Nie utrzymujemy stale wirtualnego displayu, window managera ani stream gatewaya dla pracy automatu.

Bądźmy uczciwi co do granicy: **pierwszego logowania i MFA nie da się wykonać headless**. Logowanie wymaga człowieka przy realnym ekranie — wpisania hasła bez przekazywania go do modelu, przejścia MFA, obsługi challenge’u i nietypowych ekranów. Dlatego tryb `INTERACTIVE` (headful na wirtualnym displayu + noVNC) pozostaje w produkcie i jest jedyną ścieżką dla:

~~~text
pierwsze logowanie konta
MFA i weryfikacja urządzenia
challenge / captcha wywołany przez platformę
weryfikacja tożsamości po podejrzeniu mismatchu
human takeover po SAFE_STOP
ręczne uzupełnienie profilu konta (zdjęcie, bio, opis)
~~~

Konsekwencje, których nie ukrywamy:

1. Przełączenie `HEADLESS → INTERACTIVE` zatrzymuje proces Chrome i startuje go ponownie na tym samym profilu; trwający run musi najpierw dojść do `SAFE_STOP`, a nierozstrzygnięty `SUBMIT_STARTED` do reconciliation.
2. Część platform zachowuje się inaczej w headless (inny layout, inne limity, inne ekrany weryfikacji). Traktujemy to jako ryzyko adaptera: capability probe wykonujemy w tym samym trybie, w którym potem działa automat, a rozbieżność daje `DEGRADED`/`UNKNOWN`, nie cichą kontynuację.
3. Nie stosujemy spoofingu fingerprintu, maskowania automatyzacji ani obchodzenia challenge’y — sekcja 4 obowiązuje w całości. Jeżeli headless jest wykrywany i blokowany na danym surface, poprawną odpowiedzią jest `MANUAL_ONLY`, human task albo rezygnacja z surface, a nie ukrywanie automatu.

### 22.5. Odczyt jest funkcją połączonego konta, nie założeniem

Zbieranie danych w większości interesujących nas community **wymaga zalogowanego konta**. Reddit, LinkedIn i X w praktyce zamykają część widoków, wyszukiwarki i wątków za login wallem; grupy i serwery Discord/Slack wymagają dodatkowo członkostwa. To nie jest szczegół implementacyjny — to determinuje, czy kanał w ogóle da się badać, i musi być jawne w registry.

`ChannelSurfaceVersion` dostaje pole `access_mode`:

~~~text
PUBLIC_API_NO_ACCOUNT   oficjalne, czytelne API bez konta (np. Hacker News)
PUBLIC_WEB_NO_ACCOUNT   treść widoczna bez logowania, brak kontraktu API
ACCOUNT_REQUIRED        login wall na odczyt albo istotne obcięcie treści bez konta
MEMBERSHIP_REQUIRED     konto + członkostwo/zaproszenie (serwery, grupy zamknięte)
UNKNOWN                 nie zweryfikowano — traktowane jak ACCOUNT_REQUIRED
~~~

Reguły:

1. `access_mode = PUBLIC_API_NO_ACCOUNT` jest tańszą ścieżką: brak profilu przeglądarki, brak lease’u, wyższy limit odczytu, wyższa `measurement_quality`. Registry ma to jawnie odróżniać i planer ma to preferować przy równym audience fit.
2. Dla `ACCOUNT_REQUIRED` i `MEMBERSHIP_REQUIRED` capability `SEARCH_CONTENT`, `READ_CONTENT`, `READ_THREAD` i `READ_METRICS` są **wyprowadzane ze stanu konta**: `READY` + `SESSION_VERIFIED` + ważny capability snapshot. Utrata sesji, `REAUTH_REQUIRED`, quarantine albo wygaśnięcie snapshotu degradują capability do `DEGRADED`/`UNKNOWN`, zatrzymują zależne taski (sekcja 5.7) i tworzą `ResourceRequirement`, a nie fikcyjny odczyt.
3. Brak konta dla surface `ACCOUNT_REQUIRED` oznacza `ChannelSurfaceAssessment.recommended_role = OBSERVE_ONLY` albo `REJECTED` — nigdy „zeskrobiemy anonimowo”.
4. Odczyt odbywa się w granicach normalnych limitów zalogowanego konta i budżetu odczytu z sekcji 9.8. Nie budujemy rotacji proxy, obchodzenia rate limitów ani równoległych sesji tego samego konta.
5. Członkostwo jest osobnym zasobem: `ResourceRequirement` typu `COMMUNITY_MEMBERSHIP`, z decyzją człowieka, a dołączenie jest akcją zapisującą po approvalu.
6. `measurement_quality` surface zależy od `access_mode`: to, czego konto nie widzi (np. pełne statystyki zasięgu cudzego posta), pozostaje `UNKNOWN` na stałe, a nie „do doszacowania”.

### 22.6. Target E1 ustala orkiestrator workspace’u

Operator nie wskazuje ręcznie subreddita ani listy słów kluczowych. Przy tworzeniu workspace’u `WorkspacePrincipal` (Sol) na podstawie Project Definition, Intelligence Profile i Channel Discovery ustala `TargetDefinitionVersion` — gdzie działamy, do kogo mówimy i jak wchodzimy. Artefakt, jego pola, lifecycle i niezmienniki definiuje sekcja 23.1; E1 używa jego zawężonego profilu:

~~~text
profil E1 TargetDefinition — wypełniane pola:

surfaces.selected              min. 2 różne ChannelArchetype
                               konkretne ChannelSurface + Community + access_mode
audience                       do kogo mówimy (ICP z Intelligence Profile)
motions.allowed                LISTEN, EXPERT_PARTICIPATION — i nic poza tym
not_doing                      topic_scope, forbidden_topics, forbidden_surfaces
                               disclosure: jak konto się przedstawia
required_resources             konta, członkostwa, dostępy (jako ResourceRequirement)
budget                         odczyty, drafty, approvale, akcje, minuty operatora
success                        progi PASS/BLOCK tego etapu (kopia z 22.11)

pola pozostawione puste w E1: kanały płatne, geografia, sezonowość,
budżet reklamowy, motions poza dwoma dozwolonymi
~~~

Dodatkowo E1 zapisuje `entry_thesis` — jaką konkretnie wartość wnosimy w rozmowy na tych surface’ach. Target jest wersjonowany razem z planem i zatwierdzany przez człowieka razem z autonomy envelope. Sol rozdziela pracę między role: Luna zbiera i klasyfikuje wątki (wysoki wolumen, tanio), Terra ocenia wątki niejednoznaczne i pisze drafty, Verifier sprawdza claimy i policy, Sol wchodzi tylko w gate review i replanning. Sol nie wykonuje akcji zewnętrznych i nie zatwierdza własnych propozycji.

### 22.7. Silnik oceny jakości dyskusji

Cel: odróżnić post, który **zyskuje zaufanie i uruchamia realną dyskusję**, od posta z pustym lub kupionym engagementem. Wysoki licznik reakcji nie jest dowodem zaufania — dowodem jest to, że ludzie odpowiadają, wracają i podają dalej z własnym komentarzem.

Wymiary oceny:

| Wymiar | Co obserwujemy | Co oznacza |
| --- | --- | --- |
| Głębokość wątku | liczba poziomów, liczba unikalnych rozmówców, mediana długości odpowiedzi, przyrost po pierwszej godzinie | rozmowa vs jednorazowa ekspozycja |
| Merytoryczność odpowiedzi | udział odpowiedzi z konkretem: liczba, źródło, kod, własne doświadczenie, kontrargument | dyskusja vs „+1” i emoji |
| Udział autora w wątku | czy autor odpowiada, po jakim czasie, ile razy, czy odpowiada krytyce | wątek żywy vs porzucony; wątek bez autora rzadko jest dobrym miejscem wejścia |
| Kto odpowiada vs ICP | udział rozmówców pasujących do `audience_models` | pięć odpowiedzi z ICP jest warte więcej niż dwieście reakcji spoza ICP |
| Amplifikacja z intencją | cytat, repost z komentarzem, crosspost, link z innego wątku/newslettera | ktoś wziął na siebie koszt podania dalej |
| Kalibracja do zasięgu | normalizacja do mediany surface dla tego formatu, godziny i wielkości sieci autora (sekcja 7.5) | odróżnia wynik od tła kanału |
| Sygnały negatywne | usunięcie przez moderację, lock, downvote poniżej zera, oskarżenie o spam/bota, „ratio” | twardy stop, nie ujemny składnik sumy |
| Okno życia | `RISING / PEAK / DECAYING / CLOSED` | czy wejście ma jeszcze sens (sekcja 22.10) |

Rekord oceny:

~~~text
ThreadQualityAssessment
- thread_ref / surface_id / community_id
- observed_at / collected_at / adapter_version
- depth_profile
- unique_participants
- substantive_reply_ratio
- author_participation
- icp_share
- intentional_amplification
- reach_calibration_basis
- negative_signals
- window_state
- fake_engagement_flags        (log-only)
- entry_opportunity            jaką konkretną wartość możemy dodać
- priority_tier                A | B | C | REJECT
- confidence / data_quality / evidence_refs
~~~

**Zasada porządkowa: intencja przed surowym engagementem.** Nie sumujemy punktów w jeden score. Ustalamy porządek klas sygnału i sortujemy kandydatów najpierw po klasie, potem po kalibracji do tła kanału:

~~~text
NEGATIVE_MODERATION / BAN / LOCK
    ≫ (dwa rzędy wielkości) każdy sygnał pozytywny — działa jak stop, nie jak waga

INTENTIONAL_AMPLIFICATION (cytat, crosspost) ≈ SUBSTANTIVE_REPLY (odpowiedź z treścią)
    > REPOST bez komentarza
    > REPLY (odpowiedź krótka)
    > FOLLOW / SUBSCRIBE
    > LIKE / UPVOTE
~~~

Zastrzeżenie, które musi zostać w dokumencie: opublikowane wagi rankingu X (repo `xai-org/x-algorithm`, `home-mixer/params/param.rs`) **mnożą przewidywane prawdopodobieństwa akcji konkretnego widza, nie surowe liczniki**, i dotyczą wyłącznie feedu X. Nie mamy ani tych predykcji, ani danych wejściowych modelu, ani tego samego kontekstu. Dlatego używamy ich wyłącznie jako **porządkowej heurystyki priorytetyzacji** — potwierdzenia, że odpowiedź i podanie dalej ważą wielokrotnie więcej niż lajk, a sygnały negatywne o rzędy wielkości więcej niż pozytywne — i nigdy jako liczbowego score udającego rachunek platformy. Każdy `ThreadQualityAssessment` zapisuje `heuristic_policy_version`, żeby wynik był odtwarzalny.

**Sygnały niewidoczne z zewnątrz** oznaczamy `UNKNOWN` i aproksymujemy wyłącznie obserwowalnymi cieniami; nigdy nie twierdzimy, że je mierzymy:

| Sygnał | Stan | Obserwowalny cień |
| --- | --- | --- |
| report / mute / block | `UNKNOWN` | usunięcie treści, lock wątku, nagły zanik odpowiedzi mimo wysokiej aktywności |
| share przez DM lub kopiowanie linka | `UNKNOWN` | pojawienie się linka w innym wątku/kanale, skok odczytów bez reakcji |
| mutual-follow, siła relacji | `UNKNOWN` | powracający rozmówca w kolejnych wątkach |
| faktyczny zasięg cudzego posta | `UNKNOWN`, jeśli platforma nie pokazuje | pozycja w feedzie, liczba unikalnych rozmówców |

**Detekcja fake engagementu startuje w trybie log-only.** Flagujemy m.in.: burst reakcji w krótkim oknie bez przyrostu odpowiedzi, rozmówców bez historii i bez profilu, powtarzalne, szablonowe komentarze, rozjazd między liczbą reakcji a liczbą unikalnych uczestników, reakcje spoza języka i strefy aktywności community. Flagi trafiają do `fake_engagement_flags` i do Community Memory, ale **nie obniżają `priority_tier` i nie odrzucają wątku**, dopóki nie istnieje ręcznie zaetykietowana próbka (minimum 200 wątków na archetyp, etykietowanych przez człowieka, z zapisanym rozstrzyganiem sporów). Etykietowanie ma własny `labeling_budget_minutes` w `TargetDefinition` i jest liczone poza metryką „minut pracy człowieka na wkład” z 22.11; próbka może być zbierana także poza oknem bramki. Dopiero na niej mierzymy precision/recall i dopiero wtedy decydujemy, czy detektor wchodzi do rankingu. Do tego czasu żaden model nie może twierdzić, że engagement jest kupiony.

### 22.8. Budowa konta od zera — presence track w E1

Używamy istniejącego presence tracku z sekcji 8.1. Nowe konto zaczyna od `UNASSESSED`, przechodzi przez `BASELINED` do `OBSERVE_ONLY` i **awansuje wyłącznie na dowodach**, nie po upływie czasu ani po osiągnięciu wieku konta. `CAMPAIGN_ELIGIBLE` jest poza zakresem E1.

| Stan | Co wolno robić | Evidence wymagane do awansu | Dzienny budżet akcji (per konto × surface) |
| --- | --- | --- | --- |
| `UNASSESSED` | nic zewnętrznego; tylko utworzenie `presence_track` | konto `READY`, `AccountAssessment`, capability snapshot, `rules_snapshot` community | 0 akcji zapisujących |
| `BASELINED` | odczyt przez połączone konto, baseline metryk, budowa Community Map | Community Map v1 z `data_quality ≥ MEDIUM`, min. 5 sesji odczytu w różnych porach, brak challenge’u | 0 akcji zapisujących; do 150 odczytanych URL-i |
| `OBSERVE_ONLY` | pełne discovery i ocena jakości, dołączenie/subskrypcja po approvalu, ręczne uzupełnienie profilu | min. 30 ocenionych wątków z `ThreadQualityAssessment`, zaakceptowany przez operatora `entry_thesis`, potwierdzone członkostwo, profil kompletny | 0 komentarzy; maks. 1 zatwierdzone dołączenie |
| `VISIBILITY_BUILDING` | reakcje po approvalu, pojedyncze krótkie odpowiedzi bez linków i bez wzmianki produktowej | min. 10 opublikowanych wkładów z evidence, brak zdarzeń negatywnych, min. 3 wkłady z odpowiedzią człowieka, moderacja nic nie usunęła | maks. 3 reakcje + 1 komentarz |
| `SOFT_PARTICIPATION` | odpowiedzi merytoryczne, wejście w wątki z aktywnym autorem, odpowiadanie na odpowiedzi | min. 25 wkładów łącznie, ≥ 25% wkładów z merytoryczną odpowiedzią człowieka, min. 3 powracających rozmówców, brak zdarzeń negatywnych przez 2 review windows | maks. 2 komentarze + 5 reakcji |
| `ESTABLISHED` | odpowiedzi z odniesieniem do własnego doświadczenia, wzmianka produktowa wyłącznie w odpowiedzi na jawne pytanie i z disclosure | wyjście poza E1 — decyzja o etapie kolejnym | maks. 3 komentarze + 8 reakcji |

Reguły dodatkowe:

1. Awans jest `StageTransitionProposal` ocenianym formułą stage gate z sekcji 8.3; brakujący dowód blokuje przejście niezależnie od tego, ile dni minęło.
2. Degradacja jest natychmiastowa i automatyczna: usunięcie przez moderację, ostrzeżenie, challenge, wykryty `IDENTITY_MISMATCH` albo `CAPABILITY_LOST` przenoszą track do `REASSESSMENT_REQUIRED` lub `BLOCKED`.
3. Konto od zera nie „nadrabia” budżetem: podwyższenie dziennego limitu wymaga awansu stanu, a awans wymaga dowodów.
4. Budżety w tabeli są maksimami, nie targetami. `ACTION_TARGET` może być niższy; niewykorzystany limit nie przechodzi na następny dzień (sekcja 9.7).
5. Dwa konta tego samego workspace’u nie działają w tym samym wątku.

### 22.9. Pętla operacyjna E1

~~~text
E1 TARGET BRIEF (Sol) + MarketingRegistrySnapshot
    ↓
DISCOVERY
    DISCOVER_SURFACES / SEARCH_CONTENT / READ_CONTENT przez połączone konto
    (albo publiczne API tam, gdzie access_mode = PUBLIC_API_NO_ACCOUNT)
    ↓
OCENA JAKOŚCI (Luna klasyfikuje, Terra rozstrzyga niejednoznaczne)
    READ_THREAD + READ_METRICS → ThreadQualityAssessment
    porządek klas sygnału + kalibracja do tła kanału + window_state
    ↓
WYBÓR WĄTKU
    priority_tier A/B, presence track pozwala, budżet dostępny,
    entry_opportunity opisuje konkretną wartość do dodania
    ↓
DRAFT (Terra) + VERIFIER
    claim checks, forbidden topics, disclosure, reguły community
    ↓
ACTION PROPOSAL → immutable revision + expires_at
    ↓
APPROVAL CZŁOWIEKA
    Approve | Edit & Approve | Reject | Snooze
    edycja tworzy nową rewizję i unieważnia poprzednią zgodę
    ↓
EXECUTION INTENT (jednorazowy, związany z hash payloadu i kontem)
    ↓
HEADLESS CDP: identity preflight → source-change check → JEDNA PRÓBA
    ↓
EVIDENCE
    URL, content hash, screenshot, adapter assertions
    CONFIRMED albo UNKNOWN → reconciliation, NIGDY automatyczny retry
    ↓
POMIAR
    snapshoty wątku po 1 / 6 / 24 h w granicach budżetu odczytu
    odpowiedzi, powracający rozmówcy, sygnały negatywne, metryki konta
    ↓
NAUKA
    presence_track_events, Community Memory, Preference Memory z edycji operatora
    eval case z każdego rejectu i istotnej edycji
    ↓
REVIEW WINDOW → goal health → alert / decyzja / korekta targetu
~~~

Podkreślenie: po `SUBMIT_STARTED` nie istnieje automatyczne ponowienie. Nieznany wynik zużywa budżet, przechodzi w stan `UNKNOWN`, tworzy alert `UNKNOWN_EXTERNAL_RESULT`, a decyzję o ewentualnej ponownej próbie podejmuje człowiek na podstawie reconciliation.

**Kontynuacja rozmowy jest osobnym, priorytetowym typem zadania.** Odpowiedź człowieka na nasz wkład jest najcenniejszym sygnałem w całym etapie — oznacza, że ktoś poświęcił czas i chce rozmawiać. System, który otwiera rozmowy i ich nie kontynuuje, buduje pozór obecności, nie obecność:

~~~text
CONVERSATION_FOLLOW_UP
    trigger: wykryta odpowiedź człowieka na nasz wkład (snapshot 1/6/24 h)
    priorytet: ponad każdym nowym wątkiem z Opportunity Inbox
    TTL: krótsze z okna wątku i 12 h — rozmowa stygnie szybciej niż wątek
    wykonanie: draft dopuszczalny wyłącznie jako podpowiedź kontekstu;
               treść kontynuacji pisze i zatwierdza człowiek
    pomiar: liczba rozmów o długości > 2 tur i liczba powracających rozmówców
~~~

Uzasadnienie tej asymetrii: pierwszy kontakt jest wyborem tematu i może wyjść z draftu, bo mierzalna jest jego trafność. Kontynuacja jest relacją — to w niej powstają powracający rozmówcy, których bramka mierzy w 22.11, i to ona decyduje, czy konto jest w community obecne, czy tylko widoczne.

### 22.10. Okna czasowe, TTL draftu i twarde limity kolejki

Wątek ma skończone okno życia. Na szybkich feedach realne okno wejścia to kilkadziesiąt minut; na forach i w wątkach dojrzałych — godziny albo dni. Draft przygotowany po zamknięciu okna jest kosztem bez wartości.

| Archetyp / kontekst | Typowe okno wejścia | TTL draftu | Uwaga |
| --- | --- | --- | --- |
| `SHORT_FORM_FEED`, `PROFESSIONAL_GRAPH` | 20–90 min | 30 min | późna odpowiedź nie jest widziana przez autora |
| `THREADED_COMMUNITY` — feed `new/rising` | 1–6 h | 90 min | najwyższa wartość wejścia przed `PEAK` |
| `THREADED_COMMUNITY` — wątek dojrzały | 6–48 h | 6 h | wejście sensowne, jeśli autor nadal odpowiada |
| `LONG_FORM_PUBLISHING`, forum branżowe | 1–7 dni | 24 h | wątek indeksowany, wartość długoterminowa |
| `CODE_COLLABORATION` (issue/PR) | dni–tygodnie | 24 h | okno zależy od aktywności maintainerów |

Hard constraints — rozszerzenie listy z sekcji 10.2 (task jest odrzucany, jeżeli):

1. `window_state = CLOSED` albo szacowane zamknięcie okna wypada przed `expires_at` draftu.
2. `draft_ttl` minął — `ActionProposal` przechodzi w `EXPIRED`, budżet draftu jest zużyty, wątek trafia do cooldownu; jedna regeneracja jest możliwa wyłącznie po jawnej decyzji operatora.
3. Przekroczono dzienny cap akcji dla presence tracku konta na tym surface (tabela 22.8).
4. Przekroczono dzienny cap kolejki approvali: maks. 12 kart na operatora dziennie i maks. 8 kart na konto dziennie. Cap kolejki jest nadrzędny wobec budżetu presence tracku z 22.8: jeżeli stan tracku dopuszcza więcej akcji niż cap kolejki, obowiązuje cap kolejki. Nadmiar nie jest kolejkowany „na jutro”, tylko odrzucany z powodem `APPROVAL_CAPACITY_EXCEEDED`.
5. Karta approvalu przekroczyła własne `expires_at` — wygasa, nie czeka bez końca.
6. Kolejka approvali jest sortowana rosnąco po `expires_at` w obrębie `priority_tier`, żeby operator nie dostawał najpierw kart, które i tak wygasną.
7. Odczyt wątku wykraczający poza `effective_remaining` budżetu odczytu (sekcja 9.8) nie jest wykonywany; brak danych daje `UNKNOWN`, nie szacunek.

Nowe typy alertów w E1 (zgodne z 11.1): `THREAD_WINDOW_CLOSING`, `DRAFT_EXPIRED`, `APPROVAL_CAPACITY_EXCEEDED`, `NEGATIVE_COMMUNITY_EVENT`, `FAKE_ENGAGEMENT_SUSPECTED`, `E1_GATE_AT_RISK`.

### 22.11. Kryteria PASS/BLOCK

Okno pomiarowe E1: **6–8 tygodni kalendarzowych, minimum 2 różne `ChannelArchetype`, minimum 2 konta zbudowane od zera, minimum 40 opublikowanych wkładów na archetyp**. Wynik poniżej minimalnej próbki nie jest `BLOCK` — jest `NO_DATA` i przedłuża okno o jeden review window; drugie `NO_DATA` z powodu niemożności zebrania próbki jest traktowane jak `BLOCK`.

| Metryka | Próg PASS | Próg BLOCK | Jak mierzone |
| --- | --- | --- | --- |
| Trafność discovery | ≥ 40% wątków z krótkiej listy operator oznacza „wchodzimy” | < 15% przez dwa kolejne review windows | operator oznacza każdą kartę `ENTER / SKIP / IRRELEVANT`; precision = `ENTER / (ENTER+SKIP+IRRELEVANT)` liczone z rekordów kolejki. Dodatkowy warunek PASS: precision radaru istotnie wyższa niż baseline losowego wyboru wątków z tego samego surface w tym samym oknie — baseline zbierany raz w tygodniu na próbce kontrolnej |
| Jakość draftów | ≥ 50% draftów zatwierdzonych bez istotnej edycji | < 20% | `Approve` bez `MATERIAL_EDIT` operatora **i** znormalizowany dystans edycji ≤ 0,15 bez zmiany tezy; oba zapisywane przy approvalu |
| Realne opublikowane wkłady | ≥ 40 potwierdzonych `PUBLISH_REPLY` z evidence na archetyp | < 20 na archetyp w pełnym oknie | `ExecutionIntent` → `CONFIRMED` + URL + content hash; `UNKNOWN` nie liczy się jako wkład |
| Odpowiedzi ludzi | ≥ 25% wkładów otrzymuje ≥ 1 merytoryczną odpowiedź człowieka | < 8% | snapshoty wątku po 1/6/24 h; klasyfikacja odpowiedzi przez Lunę z próbką weryfikowaną ręcznie |
| Powracający rozmówcy | ≥ 5 unikalnych osób, które odpowiedziały nam w ≥ 2 różnych wątkach, na archetyp | 0–1 na archetyp | dedup po `external_account_id` rozmówcy w `community_signals` |
| Wzrost presence tracku | konto od zera osiąga `SOFT_PARTICIPATION` na każdym archetypie, z evidence stage gate dla każdego przejścia | konto od zera nie wychodzi poza `VISIBILITY_BUILDING` na żadnym archetypie | `presence_track_events` + `StageTransitionProposal` z dowodami |
| Wejścia na profil / zainteresowanie kontem | dodatnia delta tam, gdzie platforma pokazuje wartość | — (metryka wspierająca) | append-only snapshoty konta; brak wartości = `UNKNOWN`, nie wpływa na PASS ani na BLOCK |
| Zdarzenia negatywne — **twardy stop** | ≤ 2% wkładów z usunięciem moderacyjnym lub downvote poniżej zera, zero oskarżeń o bota, zero ostrzeżeń konta | ≥ 5% wkładów, **albo** jakikolwiek ban, shadowban, permanentne wyciszenie, **albo** ≥ 2 publiczne oskarżenia o bota/spam | evidence moderacyjne + klasyfikacja odpowiedzi + potwierdzenie operatora; przekroczenie zatrzymuje etap natychmiast, bez czekania na koniec okna |
| Bilans czasu operatora | ≤ 6 min pracy człowieka na jeden opublikowany wkład | > 15 min na wkład | suma czasu w kolejce approvali + takeover + edycje, z logów UI, dzielona przez liczbę `CONFIRMED` |
| Generalizacja | wszystkie progi PASS spełnione na ≥ 2 różnych archetypach | PASS wyłącznie na jednym archetypie | osobna projekcja metryk per `ChannelArchetype` |

Reguły oceny:

1. Progi są zapisane w `MarketingPlanVersion` **przed** startem etapu jako immutable `stage_gate_criteria` i po fakcie nie podlegają negocjacji. Zmiana progu w trakcie tworzy nową wersję planu z jawnym `THRESHOLD_CHANGED` i **unieważnia bieżący wynik etapu**.
2. PASS wymaga spełnienia wszystkich progów PASS. BLOCK następuje po spełnieniu dowolnego progu BLOCK.
3. Stan pośredni (część metryk między progami) daje `NO_DATA`: jedno przedłużenie o maks. 2 review windows, bez zmiany progów i bez zwiększania budżetów, po czym rozstrzygnięcie musi zapaść.
4. Co tydzień powstaje `ContributionMixReport` — rozkład opublikowanych wkładów według typu (odpowiedź merytoryczna, pytanie doprecyzowujące, sprostowanie, polemika, wkład z odniesieniem do własnego doświadczenia) oraz udział wątków wybranych z powodu wysokiej dynamiki wobec wybranych z powodu dopasowania do ICP. Dryf w stronę polemiki i wątków viralowych nie jest widoczny w pojedynczym approvalu — jest widoczny wyłącznie w rozkładzie. Przekroczenie zapisanego limitu udziału wkładów polemicznych tworzy alert i wymaga decyzji, a nie cichej korekty promptu.
5. Wynik zamyka `E1ExitReport` — evidence per metryka, per archetyp, per konto, z `data_quality` i listą metryk `UNKNOWN`. Raport podpisuje operator; Sol go przygotowuje, ale nie zatwierdza.

### 22.12. Co kończy Etap 1

PASS otwiera kolejny etap i dopiero wtedy budujemy dalszą część systemu — szersze automatyzacje, własne publikacje, kolejne archetypy i kanały płatne.

**BLOCK zatrzymuje projekt.** Nie oznacza „poprawiamy prompt i idziemy dalej”, nie oznacza obniżenia progów ani przedłużania okna do skutku. BLOCK uruchamia `DecisionProposal` o zmianie podejścia z jawnymi opcjami: zmiana archetypów i community, zmiana tezy wejścia i tożsamości konta, przejście na tryb w pełni ręczny z systemem jako narzędziem researchu, albo zakończenie projektu. Wznowienie budowy kolejnych etapów po BLOCK wymaga nowej wersji planu, nowych progów zapisanych przed startem i ponownego przejścia bramki. Dokument nie przewiduje ścieżki „mimo BLOCK budujemy dalej”.

## 23. Orkiestrator workspace'u i ustalanie targetu

Sekcja 12.1 definiuje role modeli, 12.2 granice `WorkspacePrincipal`, 12.4 Model Router, 6.8 flow „Utwórz workspace”, a 8.x etapy i stage gates. Ta sekcja domyka jedną lukę, której tamte nie opisują: **co dokładnie orkiestrator ustala w momencie zakładania workspace'u** i **skąd wie, kto ma wykonać którą pracę i czym**.

Sol Workspace Principal jest orkiestratorem. Nie jest wykonawcą, nie jest publiczną personą i nie jest stale działającym procesem. Orkiestracja oznacza tutaj trzy rzeczy: ustalenie targetu, rozdzielenie pracy na role wykonawcze i pilnowanie, żeby żadne zadanie nie ruszyło bez zasobu, capability i budżetu.

### 23.1. TargetDefinition jako jawny artefakt

Przy zakładaniu workspace'u powstaje nowy, wersjonowany artefakt `TargetDefinitionVersion`. Nie jest to pole w planie ani akapit w prompcie. Odpowiada dokładnie na trzy pytania: **do kogo mówimy**, **gdzie działamy**, **jak promujemy**.

Rozdzielenie względem istniejących artefaktów:

~~~text
ProjectDefinition          = czym jest produkt i jaki jest cel biznesowy
IntelligenceProfile        = jak mówimy, czego nie mówimy, jaki mamy głos
TargetDefinition           = do kogo, na jakich surface'ach i jakimi motions
MarketingPlanVersion       = jak to rozłożyć na etapy, workstreamy i budżety
~~~

Zawartość wersji:

~~~text
audience
  icp_roles
  icp_problems
  icp_language_and_vocabulary
  icp_disqualifiers
  where_they_are            # channel_surface_id + community_id + evidence_refs

surfaces
  selected
    channel_surface_id
    community_id
    channel_archetype       # np. THREADED_COMMUNITY, PROFESSIONAL_GRAPH
    rationale
    evidence_refs
    confidence
    access_mode               # PUBLIC_API_NO_ACCOUNT | ACCOUNT_REQUIRED | MANUAL_ONLY
    required_account_role
    required_presence_track_floor
  rejected
    channel_surface_id
    reason                  # np. brak dowodów, brak konta, MANUAL_ONLY, poza ICP

motions
  allowed_marketing_motions      # np. LISTEN, EXPERT_PARTICIPATION
  forbidden_marketing_motions    # np. PAID_TEST, LOCAL_VISIBILITY na tym etapie
  allowed_semantic_capabilities per surface
  approval_class per action type

not_doing
  forbidden_surfaces
  forbidden_action_types_in_stage
  forbidden_topics_ref           # wskazanie na IntelligenceProfile, nie kopia
  forbidden_claims_ref

success
  leading_indicators             # np. udział wątków trafionych, odsetek zaakceptowanych draftów
  outcome                        # zależny od ludzi, mierzony osobno od action target
  minimum_evidence
  stop_loss
  review_window

budget
  daily_action_budget per (account, surface)
  daily_read_budget per surface
  daily_operator_review_minutes
  codex_quota_envelope
  max_open_approvals
~~~

Każde pole ma status jak w 6.2: `EXTRACTED`, `INFERRED`, `USER_CONFIRMED` albo `UNKNOWN`. Operator nie wypełnia formularza — dostaje diff i krótką listę pól, które faktycznie zmieniają target.

State machine jest taka sama jak dla Intelligence Profile:

~~~text
DRAFT → IN_REVIEW → APPROVED → ACTIVE → SUPERSEDED → RETIRED
~~~

Niezmienniki targetu:

1. Workspace ma najwyżej jedną aktywną wersję `TargetDefinition`; wymusza to partial unique index.
2. `MarketingPlanVersion` przypina dokładny `target_definition_version_id`; zmiana targetu nie mutuje aktywnego planu w miejscu.
3. Aktywacja nowej wersji i `SUPERSEDED` poprzedniej odbywają się w jednej transakcji.
4. Sol tworzy wyłącznie `DRAFT`; aktywacja wymaga `DecisionReview` człowieka, dokładnie jak aktywacja planu.
5. Surface z `access_mode = ACCOUNT_REQUIRED` nie może wejść do targetu bez `ResourceRequirement`; sam wpis w targecie nigdy nie nadaje authority (niezmiennik 25 z 3.1).
6. Surface, dla którego capability mapping daje `UNKNOWN`, wchodzi wyłącznie jako `OBSERVE_ONLY` z zadaniem probe — nie jako podstawa akcji.
7. Historyczne taski zachowują wersję targetu użytą w momencie wykonania.
8. Zawężenie targetu przez człowieka jest zawsze dozwolone i natychmiastowe; rozszerzenie jest `DecisionProposal`.

### 23.2. Co orkiestrator musi wiedzieć dokładnie

Orkiestrator nie zgaduje. Przed każdym `PrincipalRun` context snapshot z 6.7 jest rozszerzany o warstwę operacyjną: `target_definition_version`, `presence_tracks`, `capability_matrix`, `budget_envelope`, `model_catalog`. Brak danej pozycji jest `UNKNOWN` i blokuje planowanie akcji, a nie zamienia się w założenie.

| Pytanie | Źródło prawdy | Konsekwencja braku odpowiedzi |
| --- | --- | --- |
| Które konto obsługuje który surface | aktywna wersja `WorkspaceAccountAssignment` + `ResourceAllocation` | `ResourceRequirement`, surface pozostaje `OBSERVE_EXTERNAL` |
| Jaki jest presence track pary konto–community | `presence_tracks` + `presence_track_events` | brak eligibility dla akcji; tylko read/research |
| Co realnie wolno na tym surface | capability mapping w `MarketingRegistrySnapshot`: `AUTOMATED` / `AUTOMATED_AFTER_APPROVAL` / `MANUAL_ONLY` / `UNSUPPORTED` / `UNKNOWN` | `CAPABILITY_BLOCKED` albo human task, nigdy cicha próba |
| Czy w ogóle da się czytać ten surface | `access_mode` + stan połączonego konta | brak odczytu, `ResourceRequirement` na konto albo ścieżka `PUBLIC_API_NO_ACCOUNT` |
| Jaki jest baseline konta | `AccountAssessment` | startowy presence track = `UNASSESSED`, brak akcji |
| Czego brakuje | `ResourceRequirement` i jego lifecycle | workstream `BLOCKED`, jeden deduplikowany alert |
| Ile wolno dziś zrobić | hierarchiczne budżety planu, `capacity_minutes_per_window`, `allocation_share_bps`, quota Codexa | task odrzucony przez hard constraints z 10.2 |
| Który model do którego zadania | `worker_route_recommendations` → Model Router → Quota and Capability Gate | `WAITING_CAPABILITY` |
| Co się już nie udało | Episodic Memory, `AutomationRun` failure classification, otwarte alerty | powtarzanie tej samej nieudanej akcji |

Niezmiennik odczytu: **capability odczytu jest funkcją połączonego konta i jego stanu, a nie założeniem projektu**. Na Reddicie, LinkedInie i X odczyt na skalę wymaga zalogowanej sesji na przypisanym koncie; utrata sesji degraduje allocation i zatrzymuje zależne taski. Tam, gdzie istnieje oficjalne, czytelne bez konta API (np. Hacker News), registry zapisuje `access_mode = PUBLIC_API_NO_ACCOUNT`, a orkiestrator traktuje to jako wyraźnie tańszą ścieżkę: brak zależności od presence tracku, brak ryzyka konta, wyższa jakość pomiaru. Dwa surface'y o tym samym audience fit, ale różnym `access_mode`, nie są równoważne w rankingu.

### 23.3. Dobór modeli do pracy

Orkiestrator jest drogi i uruchamiany rzadko. Cały wolumen wykonują mniejsze i tańsze role. Podział ról jest w 12.1, trasy wybiera Model Router z 12.4 — tutaj jest mapowanie konkretnych zadań cyklu community na te role.

| Zadanie | Rola z 12.1 | Typowa trasa | Dlaczego |
| --- | --- | --- | --- |
| Ustalenie targetu, wybór surfaces, replanning | Workspace Principal | Sol high | wysokie ryzyko, niejednoznaczne dowody, rzadkie |
| Discovery listy wątków i kandydatów | Research / Extraction Worker | Luna low | wysoki wolumen, wąskie wejście i wyjście |
| Klasyfikacja wątku: czy w ogóle nasz | Research / Extraction Worker | Luna low; Terra medium przy niejednoznaczności | rubryka + krótki kontekst |
| Ekstrakcja i normalizacja pól postu | Research / Extraction Worker | Luna low | deterministyczny schemat |
| Ocena jakości i priorytetu wątku | Research / Extraction Worker | Luna low z rubryką; eskalacja Terra medium przy remisie lub niskiej confidence | scoring, nie strategia |
| Draft komentarza lub odpowiedzi | Draft Writer | Terra medium; Sol high tylko dla złożonej treści technicznej | jakość języka i głosu |
| Kontrola claimów i policy | Policy / Claim Verifier | świeża Terra high | niezależność od autora draftu |
| Dowód wykonania | Result Verifier | deterministyczny adapter + Luna low | fakty, nie interpretacja |
| Krytyka targetu i planu | Plan Critic | świeża Terra high | wyłapanie targetu bez dowodów |

Zasady:

1. Routing jest po **roli**, nie po nazwie modelu. Orkiestrator emituje `worker_route_recommendations`, ale nie wybiera modelu — decyzję podejmuje Deterministic Model Router.
2. Wybór wyłącznie z katalogu dostępnego w `model/list` dla danego `CodexConnection`. Niedostępna kombinacja model/effort jest odrzucana przed turnem.
3. Quality floor roli jest nienaruszalny. Presja quoty obniża liczbę `PrincipalRun`ów i wielkość batcha, nigdy trasy verifiera ani zadania wysokiego ryzyka.
4. Verifier zawsze dostaje świeży wątek i nie widzi prywatnej argumentacji workera.
5. Eskalacja Luna → Terra → Sol wymaga zmierzonej przyczyny (`schema_failure`, `low_confidence`, `ambiguity`, `previous_failures`) i jest zapisywana jako reason code, nie jako domyślne zachowanie.
6. Deeskalacja Terra → Luna dla powtarzalnego, wąskiego zadania przechodzi przez evale i canary z 12.7, nie ad hoc w promptcie.
7. Batch klasyfikacji i ekstrakcji ma jawny rozmiar i budżet; orkiestrator planuje liczbę wątków do oceny, nie „przeanalizuj community”.

### 23.4. Co orkiestrator może, a czego nie może

| Może | Nie może |
| --- | --- |
| Tworzyć `TargetDefinitionVersion` w stanie `DRAFT` | Aktywować własnego targetu ani planu |
| Proponować `ChannelHypothesis` i `ChannelSurfaceAssessment` | Dodać surface bez evidence i confidence |
| Tworzyć `ResourceRequirement` | Utworzyć `WorkspaceResourceAssignment` lub przypisać konto do taska |
| Proponować taski i workstreamy | Wybrać konto dla konkretnego runa — robi to backend (niezmiennik 9) |
| Emitować `worker_route_recommendations` | Wymusić model, effort albo obejść Quota and Capability Gate |
| Tworzyć `Alert` i `DecisionProposal` | Zatwierdzić własną rekomendację |
| Proponować `ActionProposal` | Wydać `ExecutionIntent` albo uruchomić `AutomationRun` |
| Proponować `OperationalAdjustment` w zatwierdzonym envelope | Zwiększyć budżet dzienny, quota, ad spend albo obniżyć approval class |
| Proponować `StageTransitionProposal` | Zmienić presence track bez dowodów stage gate |
| Zgłaszać `memory_candidates` | Zapisać canonical memory |
| Proponować `PlanRevisionProposal` | Mutować aktywny plan lub aktywny target |

Próba commitu proposal kind albo command kind spoza aktywnego `WorkspacePrincipalAuthorityGrant` kończy run jako `FAILED/AUTHORITY_DENIED` i tworzy alert. Sam poprawny JSON nie jest uprawnieniem.

### 23.5. Flow: od „Utwórz workspace” do pierwszego zadania w kolejce

Rozwinięcie 6.8 o warstwę targetu i dispatchu. Kroki oznaczone `[T]` są nowe względem 6.8.

~~~text
[Utwórz workspace] w dashboardzie
    ↓
minimalny input + źródła (6.2) → ingest i wersjonowanie w PostgreSQL (6.3)
    ↓
Terra/Luna: ekstrakcja faktów, claimów i braków
    ↓
ProjectDefinition + IntelligenceProfile drafts
    ↓
[T] PrincipalRun #1 — TARGET_DISCOVERY
    context snapshot: ProjectDefinition draft
                    + MarketingRegistrySnapshot
                    + Connected Resource Pool i wolna capacity
                    + operator budget
    ↓
[T] output bundle:
    TargetDefinitionVersion DRAFT
    ChannelSurfaceAssessment per kandydat (z access_mode)
    ChannelHypothesis
    ResourceRequirement dla braków
    worker_route_recommendations
    ↓
[T] Plan Critic (świeża Terra high) krytykuje target:
    czy surface ma dowody, czy ICP jest rozłączne,
    czy motions mieszczą się w capabilities i budżecie operatora
    ↓
[T] Decision Center: operator zatwierdza / zawęża / odrzuca target
    ↓
[T] TargetDefinition ACTIVE (dokładnie jedna wersja)
    ↓
operator łączy wymagane konta — jedyny headful/interaktywny krok tego flow;
logowania i MFA nie da się wykonać headless
    ↓
AccountOnboardingAdapter → AccountAssessment + capability snapshot
    ↓
[T] capability matrix per (konto, surface) i weryfikacja access_mode
    ↓
[T] presence_track per para konto–community: UNASSESSED → BASELINED
    ↓
PrincipalRun #2 — PLAN_FROM_TARGET
    MarketingPlanVersion + stage + workstreamy + budżety + gates
    ↓
operator zatwierdza plan i autonomy envelope
    ↓
Workspace READY → [Start plan] → ACTIVE
    ↓
Daily Planner (Terra) → DailyCommitment + taski          [E3; w E1–E2 stały limit dzienny]
    ↓
Model Router: rola → model/effort → Quota and Capability Gate  [E2; w E1 stałe trasy]
    ↓
Task Engine → kolejka jobów za JobTransportPort          [E3: RabbitMQ; wcześniej PostgreSQL]
    ↓
pierwsze zadanie w kolejce: read-only discovery na surface z TargetDefinition
~~~

Pierwsze zadanie nigdy nie jest akcją zewnętrzną. Ścieżka do pierwszego komentarza lub reakcji prowadzi zawsze przez `ActionProposal` → immutable approval → `ExecutionIntent` → jeden `AutomationRun` → post-action verification.

### 23.6. Zakres orkiestratora na Etapie 1

Etap 1 nie uruchamia pełnej roli orkiestratora. Świadomie ograniczamy jego powierzchnię, żeby bramka pass/block mierzyła jakość discovery i budowania obecności, a nie jakość autonomii.

| Wymiar | Etap 1 | Docelowo od Etapu 2 |
| --- | --- | --- |
| Liczba workspace'ów | 1 | wiele |
| TargetDefinition | 1 aktywna wersja, wąski target: 2 archetypy kanału, kilka surfaces | wiele surfaces, pełny mix |
| Capabilities | `DISCOVER_SURFACES`, `SEARCH_CONTENT`, `READ_CONTENT`, `READ_THREAD`, `READ_METRICS` jako `AUTOMATED` o ile pozwala na to stan konta; `PUBLISH_REPLY` i reakcje wyłącznie `AUTOMATED_AFTER_APPROVAL` | rozszerzenie po dowodach |
| Marketing motions | `LISTEN`, `EXPERT_PARTICIPATION` | dodatkowe motions po walidacji |
| Kanały płatne | brak; `MANAGE_CAMPAIGN` i `READ_AD_METRICS` poza zakresem | osobna decyzja i ledger |
| Stage advance | wyłącznie po decyzji człowieka; brak automatycznego przejścia w envelope | automatyczne przejście w zatwierdzonym envelope |
| Triggery `PrincipalRun` | utworzenie workspace'u, zamknięcie review window, brak zasobu, termin stage gate, odpowiedź człowieka | pełna lista z 12.2 |
| Operational adjustments | tylko propozycje do akceptacji | bezpieczne korekty w envelope |

Dodatkowe ograniczenia Etapu 1:

1. Target musi się mieścić w zadeklarowanym `daily_operator_review_minutes` — orkiestrator, który proponuje więcej pracy do akceptacji niż operator jest w stanie przejrzeć, dostaje `PLAN_OFF_TRACK`, a nie cichy backlog.
2. Target musi obejmować co najmniej dwa różne `ChannelArchetype`; walidacja na jednym archetypie nie zamyka bramki Etapu 1.
3. Każde konto startuje z presence tracku wynikającego z `AccountAssessment`, a nie z założenia; konto zbudowane od zera startuje w `UNASSESSED` i dochodzi do `OBSERVE_ONLY` przez `BASELINED`.
4. Dopóki bramka Etapu 1 nie przejdzie, orkiestrator nie dostaje szerszego authority grantu ani dodatkowych `proposal_kinds`.

## 24. Warstwa zbierania danych i wykonania: headless CDP, scraper i wymóg połączonego konta

Sekcja domyka dwie decyzje founderów i koryguje wcześniejsze zapisy: rdzeniem wykonania jest headless Chrome sterowany przez CDP, a zdolność odczytu jest funkcją połączonego konta, a nie właściwością platformy. Sekcja 14 (Chromium/CDP) i sekcja 5 (Connected Resource Pool) pozostają w mocy poza punktami wskazanymi niżej jako korekta.

### 24.1. Decyzja: headless CDP jest trybem domyślnym

Stany i przejścia trybu kapsuły (`capsule_mode`) definiuje sekcja 14.5; ta sekcja opisuje wyłącznie ich konsekwencje dla zbierania danych i nie wprowadza drugiego słownika stanów.

Domyślną ścieżką każdego runa — odczytu, discovery, pomiaru i zatwierdzonej akcji zapisującej — jest headless Chrome for Testing sterowany przez CDP. Headful, wirtualny display i stream do dashboardu przestają być trybem pracy automatu i stają się ścieżką wyjątkową.

~~~text
ŚCIEŻKA DOMYŚLNA (HEADLESS)

Browser Session Broker
    ↓
isolated Session Capsule for one account
    ↓
headless Chrome for Testing (--headless=new)
--user-data-dir=<dedicated account profile>
--remote-debugging-port=0
    ↓
internal CDP Gateway ← read DevToolsActivePort
    ↓
Playwright connectOverCDP()
    ↓
Read Adapter / Action Adapter
~~~

~~~text
ŚCIEŻKA WYJĄTKOWA (INTERACTIVE)

INTERACTIVE_REQUIRED
(pierwsze logowanie | MFA | challenge | human takeover | weryfikacja tożsamości)
    ↓
graceful close headless Chrome + fsync profilu
    ↓
restart tej samej kapsuły w trybie headful
(ten sam profil, ten sam exact executable)
    ↓
Xvnc/TigerVNC + minimal window manager
    ↓
Stream Gateway ← noVNC/websockify
    ↓
Dashboard ← WSS + one-time StreamTicket
    ↓
po zakończeniu: powrót do HEADLESS albo zamknięcie kapsuły
~~~

Trzeba to powiedzieć wprost i uczciwie: **logowania i MFA nie da się zrealizować headless**. Headless nie renderuje okna, które moglibyśmy oddać człowiekowi, a challenge i drugi składnik uwierzytelnienia są z założenia przeznaczone dla człowieka w prawdziwym UI. Nie zamierzamy tego omijać ani udawać przeglądarki z ekranem — sekcja 4 pozostaje granicą zakresu. Zamiast tego kapsuła musi umieć na żądanie przełączyć konto w tryb interaktywny, a system musi traktować to przełączenie jako jawny, kosztowny, audytowalny wyjątek, nie jako domyślny tryb pracy.

Konsekwencje dla kosztu i infrastruktury:

| Składnik kapsuły | Ścieżka domyślna (HEADLESS) | Ścieżka wyjątkowa (INTERACTIVE) |
| --- | --- | --- |
| Proces Chrome | headless, jeden na profil | headful, jeden na profil |
| Xvnc/TigerVNC + window manager | nie startuje | wymagany |
| Stream Gateway / noVNC / websockify | nie startuje | wymagany |
| StreamSession, ticket, isolated stream origin | brak | pełny flow z 14.3 |
| InteractionLease | `AUTOMATION` | `HUMAN` |
| Profil i `user-data-dir` | osobny per konto | ten sam profil, bez kopiowania |
| Koszt RAM/CPU na aktywne konto | niższy | wyższy |
| Powierzchnia ataku | mniejsza; brak transportu obrazu i inputu | pełna, z regułami 19.2 |
| Czas do gotowości | start procesu | start procesu + przełączenie trybu |

Czego decyzja **nie** zmienia: niezmiennik 27 zostaje w całości. Każde aktywne konto ma osobny katalog danych, osobny proces Chromium i osobną kapsułę; dwa konta tej samej platformy nigdy nie współdzielą procesu, `BrowserContext`, profilu ani kapsuły. Headless obniża koszt jednej kapsuły, nie pozwala ich łączyć.

Przełączenie trybu jest zimne, bo jeden profil nie może być otwarty przez dwa procesy:

~~~text
BrowserSessionMode:
HEADLESS → INTERACTIVE_REQUESTED → SWITCHING → INTERACTIVE
INTERACTIVE → RELEASING → SWITCHING → HEADLESS
HEADLESS/INTERACTIVE → STOPPING → CLOSED
SWITCHING → SWITCH_FAILED → QUARANTINED
~~~

Reguły przełączenia:

1. `INTERACTIVE_REQUESTED` powstaje wyłącznie z jawnego powodu: `FIRST_LOGIN`, `REAUTH`, `MFA`, `CHALLENGE`, `HUMAN_TAKEOVER`, `IDENTITY_VERIFICATION`. Powód jest zapisany i widoczny w Decision Center.
2. Scheduler wykonuje `SAFE_STOP` przed przełączeniem; nierozstrzygnięty `SUBMIT_STARTED` idzie do reconciliation, nie do przełączenia.
3. AUTOMATION `InteractionLease` zostaje odwołany przed startem headful; HUMAN lease jest przyznawany atomowo razem z CONTROL ticketem, jak w 14.3.
4. `ProfileLease` nie jest zwalniany podczas przełączenia — zmienia się `capsule_instance_id`, `browser_session_id` i `cdp_endpoint_ref`, więc profile lease generation rośnie, a wszystkie stare fencing tokeny są odrzucane.
5. Ten sam exact executable i ten sam profil w obu trybach; zakaz mieszania buildów z 14.2 obowiązuje bez zmian.
6. Po powrocie do `HEADLESS` adapter ponownie wykonuje `verify_identity()` i `probe_capabilities()`; wynik sprzed przełączenia nie jest przenoszony.
7. Nieudane przełączenie w którąkolwiek stronę, którego nie da się potwierdzić, przenosi profil do `QUARANTINED`. Nie startujemy drugiej kapsuły równolegle.

Jeżeli konkretna capability na danej platformie nie działa headless, adapter deklaruje to jawnie przez `required_browser_mode = INTERACTIVE` i płacimy koszt trybu interaktywnego albo oznaczamy capability jako `MANUAL_ONLY`. Nie maskujemy trybu, nie podmieniamy fingerprintu i nie ukrywamy automatyzacji.

### 24.2. Niezmiennik: capability odczytu jest funkcją połączonego konta

Odczyt i wyszukiwanie na większości interesujących nas platform jest za login wallem. Reddit, LinkedIn i X nie dają użytecznego, stabilnego dostępu bez uwierzytelnienia. To nie jest szczegół implementacyjny adaptera, tylko niezmiennik domenowy, bo determinuje, czy plan w ogóle może wejść na dany surface.

Niezmienniki 30–36 są częścią kanonicznej listy z sekcji 3.1 i tam też są zapisane; poniżej powtórzone dla czytelności tej sekcji:

30. Capability odczytu jest funkcją połączonego konta i jego stanu, nie właściwością platformy. `SUPPORTED` bez wskazania konta nie istnieje, poza źródłami oznaczonymi w registry jako `access_mode = PUBLIC_API_NO_ACCOUNT` albo `PUBLIC_WEB_NO_ACCOUNT`.
31. Discovery i search na `ChannelSurface` o `access_mode = ACCOUNT_REQUIRED` lub `MEMBERSHIP_REQUIRED` wymagają aktywnego `WorkspaceResourceAssignment` konta do tego surface. Brak assignmentu oznacza brak capability, a nie „niższą jakość danych”. Surface bez wymogu konta jest jedynym wyjątkiem.
32. Utrata sesji, challenge albo niezgodność tożsamości degradują wszystkie capabilities tego konta do `UNKNOWN` i zatrzymują zależne taski, także czysto odczytowe.
33. Budżet odczytu jest budżetem konta i podlega tej samej hierarchii co akcje zapisujące (9.8). Odczyt nie jest darmowy: zużywa sesję, rate limit i reputację konta.
34. Brak konta do community tworzy `ResourceRequirement`, nigdy fikcyjne założenie o dostępie. Plan nie może zaplanować obserwacji surface, do którego nie mamy zasobu.
35. Tryb domyślny to `HEADLESS`; `INTERACTIVE` wymaga jawnego, zapisanego powodu i wygasa razem z HUMAN `InteractionLease`.
36. Surowa odpowiedź źródła jest immutable evidence. Reparsowanie nowszym parserem nie może wymagać ponownego pobrania danych.

Praktyczne skutki dla planowania:

- `ChannelSurfaceAssessment` nie może wystawić `PRIMARY` dla surface bez pokrycia kontem; brak konta obniża `available_capabilities` i wymusza `OBSERVE_ONLY` albo `ResourceRequirement`,
- `ResourceAllocator` traktuje odczyt jako pełnoprawne wymaganie zasobu, z własnym `required_capabilities` (`SEARCH_CONTENT`, `READ_THREAD`, `READ_METRICS`),
- konto w `REAUTH_REQUIRED` blokuje nie tylko publikację, ale i discovery; Daily Planner nie ma z czego układać dnia na tym surface,
- utrata dostępu do odczytu jest zdarzeniem planistycznym, nie tylko techniczną awarią: przechodzi przez `DEGRADED` allocation i wraca do Principala jako input do replanningu.

Karta zasobu w Account Inventory pokazuje wobec tego również stan odczytu:

~~~text
Jan, LinkedIn
Account: READY
Session: SESSION_VERIFIED (12 min ago)
Mode: HEADLESS
Read access: SEARCH_CONTENT · READ_THREAD · READ_METRICS
Read budget today: 34/120
Assigned surfaces: 3
~~~

### 24.3. Ścieżki dostępu per typ źródła

Registry musi rozróżniać, ile realnie kosztuje dostęp do danego źródła i co za ten koszt widać. Rozszerzenie wersjonowanego rekordu surface:

~~~text
ChannelSurfaceVersion / ConnectorVersion:
- access_mode: patrz 22.5 — jedyna definicja normatywna
      PUBLIC_API_NO_ACCOUNT | PUBLIC_WEB_NO_ACCOUNT
    | ACCOUNT_REQUIRED | MEMBERSHIP_REQUIRED | UNKNOWN
- required_app_registration: bool   # oficjalne API wymagające rejestracji aplikacji
- required_browser_mode: NONE | HEADLESS | INTERACTIVE
- read_cost_class: LOW | MEDIUM | HIGH
- measurement_quality
- observable_fields
~~~

`access_mode` jest jednym atrybutem o wartościach zdefiniowanych w 22.5; ta sekcja go nie redefiniuje, tylko rozszerza rekord surface o koszt dostępu i tryb przeglądarki.

| Typ źródła | Konto połączone wymagane | Ścieżka | Co realnie widać | Jakość pomiaru |
| --- | --- | --- | --- | --- |
| Publiczne API bez uwierzytelnienia (np. Hacker News) | nie | oficjalne API, zwykły HTTP, bez przeglądarki | pełne drzewa dyskusji, itemy, autorzy, timestampy, punkty | wysoka i deterministyczna; stabilny schemat; brak danych o zasięgu i impressions |
| Oficjalne API wymagające uwierzytelnienia (np. Reddit) | tak — konto oraz zarejestrowana aplikacja | oficjalne API na poświadczeniach konta | listingi surface, komentarze, score, metadane i reguły community | wysoka dla treści, ograniczona dla zasięgu; limity naliczane per konto/aplikacja |
| Platforma bez publicznego read API (np. LinkedIn) | tak, obowiązkowo | CDP na zalogowanej sesji, odczyt wewnętrznych endpointów aplikacji | to, co widzi zalogowany użytkownik: spersonalizowany feed, reakcje, komentarze, statystyki własnych publikacji | średnia; próbka jest stronnicza przez personalizację, zasięg cudzych postów zwykle niewidoczny |
| Feed z płatnym API i login wallem (np. X) | tak | oficjalne API w płatnym tierze albo CDP na zalogowanej sesji | posty, wątki, publiczne liczniki | zależna od tieru; bez płatnego dostępu próbka jest wąska, niestabilna i słabo porównywalna między oknami |
| Forum / Discourse | zwykle nie do odczytu, tak do pisania | publiczne endpointy JSON instancji; CDP tylko dla treści za logowaniem | wątki, posty, tagi, liczniki wyświetleń, profile | wysoka dla treści; instancja musi być osobnym rekordem registry, bo konfiguracje się różnią |
| Czat community (Discord/Slack) | tak — członkostwo i integracja zgodna z regulaminem | oficjalne API po dołączeniu | wyłącznie kanały, do których zasób ma dostęp | zależna od uprawnień; brak retrospektywy sprzed dołączenia |
| Grupa zamknięta bez API (grupa FB, prywatna społeczność, newsletter) | tak — członkostwo człowieka | import ręczny albo human task | to, co człowiek wyeksportuje lub wklei | niska częstotliwość, wysoka jakość jednostkowa; capability `MANUAL_ONLY` |

Wniosek dla registry i dla planu: `PUBLIC_API_NO_ACCOUNT` jest najtańszą i najstabilniejszą ścieżką i tam, gdzie istnieje, jest preferowana. `ACCOUNT_REQUIRED` daje dostęp do miejsc, których inaczej nie widać, ale kosztuje konto, sesję, tryb przeglądarki i utrzymanie parserów. `MANUAL_ONLY` jest legalnym, jawnym wyjściem, a nie porażką — tworzy human task i zachowuje uczciwą jakość pomiaru.

### 24.4. Scraper jest adapterem, nie osobnym systemem

Nie budujemy „scrapera” obok systemu. Zbieranie danych realizuje ten sam Read/Action Adapter, to samo capability mapping (`AUTOMATED`, `AUTOMATED_AFTER_APPROVAL`, `MANUAL_ONLY`, `UNSUPPORTED`, `UNKNOWN`) i ten sam kontrakt z 14.7. Odczyt jest po prostu capability bez side effectu.

Rozszerzenie minimalnego kontraktu adaptera o część odczytową:

~~~text
discover_surfaces(hypothesis)
search_content(query, surface_ref, window)
list_surface_items(surface_ref, window)
read_thread(thread_ref, depth)
read_author_profile(author_ref)
read_publication_metrics(publication_ref)
~~~

Zasady implementacyjne:

1. Preferujemy parsowanie odpowiedzi wewnętrznych API platformy nad parsowaniem DOM. Struktury JSON zmieniają się rzadziej niż layout, mają jawne pola i pozwalają na kontraktowe testy schematu. DOM jest fallbackiem, nie pierwszym wyborem.
2. Surowa odpowiedź zapisywana jest niezmiennie: `raw_response_ref`, `content_hash`, `fetched_at`, `source_url`, `adapter_version`, kształt żądania bez sekretów. Reparsowanie nowszym parserem działa na zapisanym blobie i nie zużywa budżetu odczytu ani sesji konta.
3. Parsowanie jest osobnym, wersjonowanym krokiem: `parse_run(raw_response_ref, parser_version)`. Zmiana parsera tworzy nowe obserwacje z tym samym `evidence_ref`, więc historię można poprawić wstecz bez kłamania o źródle.
4. Każdy odczyt wskazuje `metric_collection_run_id` i idempotency key, zgodnie z 7.6. Brak widocznej wartości to `UNKNOWN`, nigdy zero.
5. Adapter nie wykonuje nieograniczonych pętli, nie obchodzi rate limitów i nie retryuje po soft blocku. Limit platformy jest sygnałem do backoffu w granicach budżetu i do alertu, nie do kombinowania.

Pomiar w czasie zamiast wartości absolutnych:

~~~text
observation = (item_ref, metric_key, value, observed_at, item_age_at_observation)
velocity    = Δvalue / Δt w oknie, znormalizowane przez wiek itemu
              i baseline aktywności surface
~~~

Absolutny licznik niesie mało informacji, bo miesza wiek, porę publikacji i wielkość community. Post z 40 punktami po dwóch latach i post z 40 punktami po czterdziestu minutach to dwa różne zjawiska. Dla discovery z Etapu 1 sygnałem jest tempo w pierwszych oknach i jego odchylenie od baseline surface, a nie stan końcowy. Dlatego snapshoty są append-only, mają wiele okien i zawsze niosą `item_age_at_observation`.

### 24.5. Lifecycle utraty dostępu

Każda ścieżka dostępu może przestać działać i musi mieć deterministyczne zejście. Nic nie degraduje się po cichu.

~~~text
ReadCapability:
SUPPORTED → DEGRADED → UNKNOWN
UNKNOWN → SUPPORTED           (po udanym probe)
UNKNOWN → MANUAL_ONLY         (po klasyfikacji: wymaga człowieka)
UNKNOWN → UNSUPPORTED         (po klasyfikacji: trwale niedostępne)
DEGRADED → SUPPORTED          (po powrocie w granicach kontraktu)
~~~

| Zdarzenie | Detekcja | Stan capability | Alert | Skutek |
| --- | --- | --- | --- | --- |
| Wygaśnięcie sesji / wylogowanie | `verify_identity()` fail, redirect na login | `UNKNOWN` | `NEEDS_LOGIN_OR_MFA` | konto `REAUTH_REQUIRED`, zależne taski `SAFE_STOP`, `INTERACTIVE_REQUIRED` z powodem `REAUTH` |
| Challenge / CAPTCHA | `detect_block_reason()` | `MANUAL_ONLY` dla tej capability | `NEEDS_LOGIN_OR_MFA` | human task; brak retry i brak prób obejścia (sekcja 4) |
| Zmiana UI albo schematu odpowiedzi | contract test parsera, schema mismatch, spadek pokrycia pól | `DEGRADED`, po progu `UNKNOWN` | `CAPABILITY_LOST` | zależne taski wstrzymane, fixture eval, nowa wersja adaptera |
| Rate limit / throttling | odpowiedź limitująca, soft block | `DEGRADED` | `CAPABILITY_LOST` (WARNING) | backoff w granicach budżetu; limit nie jest do obejścia |
| Niezgodność tożsamości | `verify_identity()` mismatch | `UNKNOWN` | `IDENTITY_MISMATCH` | hard stop, profil do `QUARANTINED`, decyzja człowieka |
| Zamknięcie lub prywatyzacja surface | odczyt odrzucony mimo poprawnej sesji | `UNSUPPORTED` dla surface | `CAPABILITY_LOST` | `ChannelSurface` → `OBSERVE_ONLY` albo `REJECTED`, `ResourceRequirement` na członkostwo |
| Expiry capability snapshotu | `expires_at` z 14.7 | `UNKNOWN` | brak, jeśli probe się powiedzie | ponowny probe przed pierwszym taskiem |

Reguły spójne z 14.7 i 11.1:

- `MANUAL_ONLY` tworzy human task, a nie błąd techniczny,
- alerty są deduplikowane po fingerprincie (typ, workspace, konto, surface, wersja adaptera), więc jeden padnięty parser nie generuje lawiny kart,
- `DEGRADED` allocation zatrzymuje taski i wraca do Principala jako zdarzenie planistyczne,
- powrót do `SUPPORTED` wymaga udanego probe, a nie samego upływu czasu.

### 24.6. Koszt utrzymania adapterów jest jawną pozycją

Każdy adapter platformy to stały koszt, nie jednorazowa implementacja:

~~~text
koszt adaptera =
    implementacja
  + fixture evals i contract testy schematu
  + monitoring driftu parserów
  + reakcja na zmiany UI/API platformy
  + koszt odczytów i sesji konta
  + koszt trybu INTERACTIVE tam, gdzie jest wymagany
~~~

Dlatego adapter ma właściciela i mierzalny stan:

~~~text
adapter_health:
- parser_drift_rate
- capability_loss_events per okno
- mean_time_to_repair
- fixture_eval_pass_rate
- read_cost_per_useful_observation
~~~

Reguły portfela adapterów:

1. Kolejność preferencji przy wyborze źródła: `PUBLIC_API_NO_ACCOUNT` → `ACCOUNT_REQUIRED` → `ACCOUNT_REQUIRED` → `MANUAL_ONLY`. Droższą ścieżkę wybieramy tylko wtedy, gdy tańsza nie daje potrzebnego sygnału.
2. Etap 1 startuje na minimalnej liczbie adapterów: jedno źródło z oficjalnym API bez konta jako tani baseline i jedno źródło wymagające zalogowanej sesji jako dowód, że umiemy pracować za login wallem. To pokrywa wymóg walidacji na dwóch różnych archetypach kanału i nie rozlewa kosztu utrzymania.
3. Nowy adapter wchodzi do systemu dopiero z kompletem: deklaracja `access_mode` i `required_browser_mode`, capability mapping, fixture evals, oszacowany koszt utrzymania i wskazany właściciel.
4. Adapter bez zielonego fixture evala w kolejnych oknach ma capabilities przenoszone do `UNKNOWN`. Milczące gnicie adaptera jest gorsze od jawnego braku wsparcia.
5. Każdy dodatkowy adapter jest decyzją produktową z `DecisionProposal`, a nie skutkiem ubocznym pojedynczego taska researchowego.

W Etapie 1 obowiązuje dodatkowo reguła nadrzędna: odczyt może być `AUTOMATED`, ale każda akcja zewnętrzna — komentarz, reakcja, jakikolwiek zapis — jest `AUTOMATED_AFTER_APPROVAL` i wymaga zgody człowieka na konkretną, niezmienną rewizję `ActionProposal`.

## 25. Roadmapa realizacji w etapach

Roadmapa jest zorganizowana wokół **etapów produktu E1–E5** z sekcji „Mapa etapów produktu”, a nie wokół numerowanych faz technicznych. Etap opisuje, co ma zostać **udowodnione**; faza robocza opisuje, co ma zostać **zbudowane**, żeby dowód był możliwy. Wewnątrz etapu fazy są sekwencyjne i mają własne Definition of Done.

Zakres systemu nie zmienia się względem reszty dokumentu. Workspace'y, Workspace Principal, orkiestracja mniejszych modeli, headless CDP, registry, Connected Resource Pool, Decision Center i pomiar zostają — są tylko rozłożone w czasie i uzależnione od wyniku bramki E1.

### 25.1. Zasada estymacji

Estymacja pozostaje dwutorowa:

- **mały zespół 2–3 doświadczonych osób**, prowadzący równolegle browser/CDP, data layer i model runtime,
- **jeden doświadczony developer**, pracujący sekwencyjnie.

Reguły:

1. Numery tygodni są liczone od startu etapu, nie od startu projektu.
2. Tygodnie E2–E5 liczą się dopiero od momentu `PASS` na bramce E1.
3. Okna pomiarowe są kalendarzowe. Fazy E1.F6 nie skraca zespół — okno 6–8 tygodni trwa tyle samo niezależnie od liczby osób, bo presence track i powracający rozmówcy potrzebują czasu, a nie mocy przerobowej. Długość okna wynika z arytmetyki bramki: przy limitach z 22.8 zebranie 40 potwierdzonych wkładów na archetyp wymaga około sześciu tygodni ciągłej pracy.
4. Kamieni milowych correctness nie ściskamy. Skracamy zakres, nie testy.
5. Jeżeli faza przekroczy estymatę o więcej niż 50%, wymuszamy przegląd zakresu, a nie ciche przesunięcie kolejnych faz.

### 25.2. Etap 1 — wejście w community, bramka PASS/BLOCK

Etap 1 jest bramką całego projektu. Dowodzimy dwóch rzeczy jednocześnie:

~~~text
HIPOTEZA A — DISCOVERY
potrafimy znajdować w community dyskusje, które realnie
zdobywają zaufanie i duże zasięgi, zanim staną się oczywiste

HIPOTEZA B — COLD START
potrafimy zbudować konto OD ZERA, które faktycznie porusza się
w danym community i zbiera je wokół siebie
~~~

Nie da się zaliczyć etapu, dowodząc tylko jednej z nich. Każda akcja zewnętrzna w E1 (komentarz, odpowiedź, reakcja/lajk) jest `AUTOMATED_AFTER_APPROVAL` — powstaje jako `ActionProposal`, dostaje jednorazowy `ExecutionIntent` po zgodzie człowieka i dopiero wtedy trafia do adaptera. Nie istnieje w E1 ścieżka `AUTOMATED` dla akcji zapisującej.

#### E1.F0 — spike i Go/No-Go

Mały zespół: tydzień 1. Solo: tygodnie 1–2.

Zakres:

- ADR: Chrome for Testing + `connectOverCDP` w trybie **headless** jako domyślny runtime egzekucji,
- CDP feature contract w headless: odnalezienie persistent contextu, locatory, popupy, screenshoty i potrzebne traces, ponowne dołączenie Gatewaya po restarcie, odrzucenie niezgodnego builda, bezpieczna obsługa stale `DevToolsActivePort`, jeden kanoniczny zestaw argumentów BrowserSupervisora, brak publicznego CDP,
- jawne wskazanie, czego **nie da się** zrobić headless: pierwsze logowanie, MFA, challenge i human takeover wymagają renderowanego, interaktywnego ekranu; headless nie pokazuje okna i nie jest ścieżką logowania,
- headful capsule jako **ścieżka wyjątkowa**: virtual display + stream do dashboardu + one-time `StreamTicket` + `InteractionLease`, uruchamiana wyłącznie dla `LOGIN`, `MFA`, `CHALLENGE` i `HUMAN_TAKEOVER`,
- test przekazania profilu: profil zalogowany w trybie headful jest poprawnie używany przez headless bez ponownego loginu i bez kopiowania cookies,
- osobny `user-data-dir`, proces i lease per konto,
- spike Postgres lease + fencing,
- logowanie Codex przez ChatGPT i wielogodzinny unattended-auth/scheduler test na docelowym planie, z jawnym `NO_GO`, jeżeli kontrakt nie jest wspierany,
- Python Codex SDK spike, pełna paginacja `model/list`, model/effort, output schema, rate limits,
- cztery izolowane thread roles i test braku shell/exec,
- 30–50 fixture evals dla discovery, klasyfikacji jakości dyskusji i draftu odpowiedzi,
- fixture site,
- task-scoped read-only MCP,
- threat model.

Definition of Done:

- headless adapter otwiera, czyta i zamyka profil bez widocznego okna,
- ten sam profil da się natychmiast przejąć w widoku headful bez utraty sesji,
- każda capability ma zapisany `required_browser_mode`; te wymagające człowieka są oznaczone `MANUAL_ONLY`,
- stary fencing token jest odrzucany,
- Codex używa wyłącznie dozwolonego MCP,
- ustalono exact browser i Codex versions,
- zapadły trzy osobne Go/No-Go: headless CDP contract, headful exception path, Codex jako marketing runtime,
- potwierdzono albo odrzucono wspierany tryb wielogodzinnej pracy bez otwartego dashboardu.

#### E1.F1 — correctness kernel w wersji minimalnej

Mały zespół: tygodnie 2–3. Solo: tygodnie 3–5.

Zakres:

- FastAPI, PostgreSQL, Next.js,
- single-operator auth boundary,
- state machines i optimistic locking,
- PostgreSQL jobs jako canonical state; dispatcher w tym samym procesie na `SELECT ... FOR UPDATE SKIP LOCKED`,
- job leases, heartbeat, reaper, attempt limit i terminalizacja ostatniej próby,
- audit events,
- encrypted profile storage, artifacts ACL i retention,
- URL/egress policy,
- immutable schematy `ActionProposal` / `ExecutionIntent` / `AutomationRun` z payload hash,
- kolumny `organization_id` / `workspace_id` i composite FKs w schemacie od pierwszej migracji.

Świadomie odroczone:

| Element | Odroczone do | Dlaczego można | Koszt odroczenia |
| --- | --- | --- | --- |
| RabbitMQ, outbox/inbox, publisher confirms | E3 | jeden operator, jeden runner, jedna kolejka; PostgreSQL i tak jest źródłem prawdy, a broker jest wyłącznie transportem `job_id` | brak at-least-once transportu między procesami; dopuszczalne, dopóki dispatcher żyje w jednym procesie |
| multi-tenant RLS, osobne login roles, harness dwóch tenantów | E3 | jedna organizacja, jeden workspace, jeden operator, instalacja lokalna | brak defense-in-depth przy błędzie zapytania; akceptowane, bo wszystkie dane E1 pochodzą z jednego workspace'u |

Odroczenie jest ważne tylko dlatego, że oba elementy są schowane za portami: dispatcher za `JobTransportPort`, dostęp do danych za workspace-scoped Domain Data Gateway. Dołożenie brokera i RLS w E3 nie może zmienić komend domenowych ani schematu.

Definition of Done:

- restart komponentu nie gubi taska,
- dwa workery nie przejmują tego samego rekordu,
- lease expiry respektuje attempt limit; ostatnia próba terminalizuje job zamiast tworzyć nieskończony redispatch,
- kolumny tenantowe i composite FKs istnieją, nawet gdy RLS jest wyłączone,
- zmiana transportu jobów nie dotyka żadnej komendy domenowej — potwierdzone testem kontraktowym portu,
- żaden zapisujący side effect nie omija `ExecutionIntent`,
- approval wskazuje dokładny payload hash; edycja treści unieważnia zgodę.

#### E1.F2 — połączenie kont i headless read

Mały zespół: tygodnie 4–6. Solo: tygodnie 6–10.

Zakres:

- Connected Resource Pool w minimalnym zakresie: `SocialAccount`, `ForumAccount`, `BrowserProfile` 1:1, `resource_handles`,
- Profile Manager, osobny proces i profil per konto, `ProfileLease`, fencing, flock, crash reconciliation,
- headless Session Capsule jako domyślny runtime odczytu,
- headful capsule i interaktywny widok wyłącznie dla loginu, MFA, challenge i takeover; `Otwórz interaktywnie` przy aktywnym AUTOMATION lease przechodzi przez `HumanTakeover` i `SAFE_STOP`,
- `AccountOnboardingAdapter`: verify session, verify external identity, duplicate check, probe capabilities, collect baseline metrics, detect challenge/block,
- `AccountAssessment` i `presence track` w stanach `UNASSESSED → BASELINED → OBSERVE_ONLY`,
- capability probing zapisujące per konto i per `ChannelSurface`: `AUTOMATED`, `AUTOMATED_AFTER_APPROVAL`, `MANUAL_ONLY`, `UNSUPPORTED`, `UNKNOWN`,
- rozróżnienie w registry ścieżek odczytu: oficjalne API czytelne bez konta versus surface za login wallem,
- pause, reauth, quarantine, takeover.

Niezmienniki odczytu, zapisane jawnie:

1. Capability odczytu jest funkcją połączonego konta i jego aktualnego stanu, a nie założeniem planu.
2. Reddit, LinkedIn, X i większość grup są za login wallem — bez `READY` konta ich `READ_CONTENT` ma stan `UNSUPPORTED` albo `MANUAL_ONLY`.
3. Tam, gdzie istnieje oficjalne API czytelne bez konta (np. Hacker News), registry oznacza tańszą ścieżkę `PUBLIC_API_NO_ACCOUNT` i planer preferuje ją przed sesją przeglądarki.
4. Konto w `REAUTH_REQUIRED` lub `QUARANTINED` nie produkuje danych — produkuje alert `NEEDS_LOGIN_OR_MFA`.
5. Brak danych nigdy nie jest zamieniany na zero; jest `UNKNOWN` z powodem.
6. Logowania i MFA nie wykonuje automat. Robi je człowiek w widoku interaktywnym; hasło nie trafia do modelu.

Definition of Done:

- można dodać co najmniej trzy izolowane konta na co najmniej dwóch platformach,
- dwa konta tej samej platformy mają osobny profil, proces, capsule i sesję,
- każdy odczyt sprawdza faktycznie zalogowaną tożsamość przed użyciem danych,
- login i MFA są widoczne w dashboardzie i nie są próbowane headless,
- profil zalogowany headful jest używany przez headless bez ponownego loginu,
- surface bez połączonego konta ma jawny stan capability, a nie ciche zero,
- nie da się uruchomić dwóch sesji jednego profilu,
- awaria procesu nie powoduje split-brain.

#### E1.F3 — runtime modeli, discovery i silnik oceny jakości dyskusji

Mały zespół: tygodnie 7–9. Solo: tygodnie 11–15. To najdłuższa faza budowlana E1, bo poza discovery uruchamia w niej po raz pierwszy runtime modeli — bez niego nie ma czym wykonać ani klasyfikacji, ani draftów.

Zakres — runtime Codexa w minimalnym zakresie (sekcje 13.1–13.4):

- `CodexAccountAdapter` z logowaniem kontem ChatGPT i lifecycle połączenia,
- `CodexExecutionAdapter` na Python SDK, izolowany `CODEX_HOME`, hardened `config.toml`, brak shell/exec, per-turn MCP sidecar,
- katalog modeli z `model/list` i quota gate; stany jobów `WAITING_AUTH / WAITING_QUOTA / WAITING_CAPABILITY`,
- thread isolation dla ról oraz structured outputs i `PromptVersion` z eval harness,
- stałe, jawnie zapisane trasy model/effort per rola — **bez** pełnego Model Routera, fallback chains i polityki quota, które wchodzą w E2.

Zakres — discovery i ocena:

- minimalny, wersjonowany registry: `PlatformDefinition`, `ChannelSurface`, `Community`, `SemanticCapability` oraz `MarketingRegistrySnapshot` przypinany do każdego runa,
- capabilities `DISCOVER_SURFACES`, `SEARCH_CONTENT`, `READ_CONTENT`, `READ_THREAD`, `READ_METRICS` dla dwóch archetypów kanału,
- Community Map: tematy, częste pytania, język, formaty, godziny i dni aktywności, notable authors, rules snapshot, data quality,
- observed content i append-only snapshoty metryk postów w oknach 1/6/24 h tam, gdzie capability i budżet odczytu istnieją,
- silnik oceny jakości dyskusji: dopasowanie tematyczne, świeżość, tempo przyrostu odpowiedzi, obserwowalny zasięg, wielkość sieci autora, szansa na trafną odpowiedź, koszt wejścia, konkurencja w wątku,
- normalizacja względem wielkości community, wieku posta, dnia i godziny oraz kompletności danych,
- Opportunity Inbox w minimalnej wersji: ranking kandydatów z dowodami i uzasadnieniem cech,
- Luna do klasyfikacji i ekstrakcji, Terra do syntezy, na stałych trasach z bloku runtime powyżej.

Definition of Done:

- każdy kandydat ma `source_url`, `observed_at`, `collected_at`, confidence i freshness,
- ranking jest odtwarzalny: ten sam `MarketingRegistrySnapshot` i ten sam zestaw snapshotów dają ten sam wynik,
- definicja „wartościowej dyskusji” jest wersjonowana i przechodzi fixture evals z E1.F0,
- operator widzi, dlaczego post trafił do inboxa: cechy, wagi i dowody,
- brak konta na surface degraduje discovery do dozwolonej ścieżki albo do `RESOURCE_GAP`, nigdy do zmyślonych danych,
- discovery działa na obu archetypach bez branchy `if platform == ...` poza adapterem,
- brak klucza API: modele działają wyłącznie na połączeniu ChatGPT, a niedostępna kombinacja model/effort jest odrzucana przed turnem,
- utrata auth lub quoty przenosi job do właściwego stanu `WAITING_*` i nie trzyma runtime lease'u.

#### E1.F4 — drafty i kolejka approvali

Mały zespół: tygodnie 10–11. Solo: tygodnie 16–18.

Zakres:

- grounding w wiedzy o projekcie: ingest README/repozytorium/URL do PostgreSQL, `ProjectDefinitionVersion` i minimalny `WorkspaceIntelligenceProfileVersion` (pozycjonowanie, point of view, głos, disclosure, `prohibited_topics`, `prohibited_claims`),
- claims and evidence, `KnowledgeSnapshot` przypięty do każdego draftu,
- Draft Writer (Terra) i Policy/Claim Verifier w świeżym wątku,
- `ActionProposal` z immutable rewizją, payload hash i pełnym kontekstem źródłowym,
- trzy typy akcji w E1: komentarz, odpowiedź w wątku, reakcja/lajk,
- kolejka approvali w Decision Center: `Approve`, `Edit & Approve`, `Reject`, `Snooze`, `Ask AI`,
- dzienne `ACTION_TARGET` i `ACTION_BUDGET` na drafty i akcje, ze stop conditions,
- preference memory jako kandydaci z edycji i odrzuceń operatora.

Definition of Done:

- draft nie używa claimu `UNVERIFIED` jako faktu,
- każdy draft wskazuje dokładny snapshot wiedzy, wersję promptu, model i effort,
- karta approvalu pokazuje wątek źródłowy, autora i cytowany fragment, a nie samą treść odpowiedzi,
- edycja tworzy nową rewizję i unieważnia poprzednią zgodę,
- odrzucenie wymaga powodu, który trafia do preference memory jako kandydat,
- przekroczenie dziennego budżetu zamyka kolejkę zamiast zwiększać liczbę propozycji,
- verifier działa w świeżym wątku i nie widzi prywatnej argumentacji autora draftu.

#### E1.F5 — pierwsza akcja zapisująca przez CDP, evidence i pomiar

Mały zespół: tygodnie 12–13. Solo: tygodnie 19–21.

Zakres:

- jednorazowy `ExecutionIntent` związany z dokładną rewizją, kontem i assignmentem,
- identity preflight i source-change check przed każdym zapisem,
- deterministyczny Action Adapter: `prepare_approved_action`, `execute_approved_action`, `reconcile_action`,
- wykonanie przez headless CDP; challenge, MFA, `MANUAL_ONLY` i identity mismatch przechodzą do takeover w widoku interaktywnym,
- capability mapping dla `SUBMIT_COMMENT`, `PUBLISH_REPLY` i reakcji — wszystkie `AUTOMATED_AFTER_APPROVAL`, `side_effect: IRREVERSIBLE`, `retry_class: RECONCILE_ONLY`,
- result evidence: URL, content hash, screenshot, `adapter_version`,
- `UNKNOWN` + alert `UNKNOWN_EXTERNAL_RESULT` bez automatycznego retry, reconciliation i alert,
- post-action metric windows i publication snapshots,
- alert/decision flow dla challenge, capability loss i `UNKNOWN`.

Definition of Done:

- wykonanie nigdy nie używa niewłaściwego konta,
- akcja bez ważnego approvalu jest niewykonalna także przy ręcznym wywołaniu adaptera,
- po `SUBMIT_STARTED` nie ma automatycznego retry,
- każda potwierdzona akcja ma evidence i da się ją odnaleźć w źródle,
- `UNKNOWN` + alert `UNKNOWN_EXTERNAL_RESULT` zużywa budżet i tworzy alert, a nie kolejną próbę,
- challenge przechodzi do takeover, a nie do obchodzenia zabezpieczenia,
- pierwszy pomiar efektu akcji zapisuje się w oknach zdefiniowanych przed wykonaniem.

#### E1.F6 — okno walidacyjne na dwóch archetypach i decyzja PASS/BLOCK

Mały zespół: tygodnie 14–21. Solo: tygodnie 22–30. Samo okno to zawsze 6–8 tygodni kalendarzowych — tyle wymaga arytmetyka bramki z 22.11 przy limitach dziennych z 22.8.

Zakres:

- co najmniej dwa **różne** archetypy kanału, np. `THREADED_COMMUNITY` i `PROFESSIONAL_GRAPH`;
- co najmniej jedno konto zbudowane od zera i jedno konto z historią, jako dwa niezależne presence tracki,
- feature freeze: przez pełne okno nie dokładamy funkcji, tylko poprawki,
- cotygodniowy przegląd z zapisanym `AccountAssessment`, `presence_track_events` i community signals,
- metryki obu hipotez zdefiniowane i zamrożone **przed** startem okna, wraz z progami.

Kryteria `PASS` — twarde, wszystkie muszą być spełnione:

1. Trafność discovery jest istotnie lepsza od baseline'u losowego wyboru z tego samego surface w tym samym oknie, na próbce ustalonej przed startem.
2. Predykcja jakości ma dodatnią, powtarzalną zależność z faktycznym zasięgiem i reakcjami mierzonymi w oknach 1/6/24 h — na obu archetypach osobno.
3. Konto zbudowane od zera przechodzi `OBSERVE_ONLY → VISIBILITY_BUILDING → SOFT_PARTICIPATION` na dowodach stage gate, a nie decyzją operatora.
4. Konto od zera zebrało obserwowalny sygnał zwrotny: odpowiedzi innych ludzi, powracających rozmówców i wzrost obserwowalnej reputacji lub zasięgu — na obu archetypach.

Kryteria miękkie:

5. Odsetek draftów zaakceptowanych bez edycji przekracza próg ustalony przed oknem, a odsetek odrzuceń spada między tygodniem 1 a 4.
6. Czas operatora na jedną zaakceptowaną akcję spada i mieści się w progu ustalonym przed oknem.
7. Żaden z archetypów nie wymagał zmiany rdzenia — wyłącznie registry, adaptera i capability mapping.

Reguła decyzji:

~~~text
wszystkie twarde (1–4) spełnione AND wszystkie miękkie (5–7) spełnione
    → PASS → start E2

wszystkie twarde spełnione AND dokładnie jedno miękkie naruszone
    → CONDITIONAL_PASS
      → jawny plan naprawy + powtórzenie okna dla naruszonego kryterium
      → E2 nie startuje przed zamknięciem naprawy

dowolne twarde niespełnione
    → BLOCK
~~~

Definition of Done:

- decyzja `PASS` / `CONDITIONAL_PASS` / `BLOCK` jest zapisana z dowodami: snapshoty, presence track events, evidence akcji, koszty operatora,
- wyniki obu archetypów są porównywalne, bo używają tej samej definicji metryk i tych samych okien,
- raport rozdziela, co było wynikiem systemu, a co pracą człowieka w kolejce approvali,
- każdy przypadek `UNKNOWN` i każde takeover są policzone i sklasyfikowane,
- przy `BLOCK` istnieje lista hipotez do zmiany podejścia i nie startuje żadna faza E2.

### 25.3. Etapy 2–5 — zakres skrócony

Szczegóły mechanizmów są już opisane w sekcjach 5–20. Poniżej wyłącznie zakres, DoD i przybliżony czas. Czas liczy się od `PASS` na E1.

#### E2 — workspace i orkiestracja

6–8 tygodni zespołowo / 10–13 tygodni solo.

Zakres:

- pełny, wersjonowany `WorkspaceIntelligenceProfile` i Project Definition,
- `TargetDefinition` ustalany przez orkiestratora przy tworzeniu workspace'u: gdzie działamy (surfaces i community), do kogo mówimy (audience models), jak promujemy (marketing motions), czym płacimy (budżety i zasoby),
- Workspace Principal (Sol) jako agregat: bounded `PrincipalRun`, event coalescing, deny-by-default authority grants, typed proposal bundle,
- deterministyczny Model Router, quality floors, eval-approved routes, quota gate, `ROUTE_MISMATCH`,
- `ResourceRequirement → match → allocation/reservation`, `WorkspaceResourceAssignment`, `ResourceAllocationVersion`, `TaskResourceBinding`,
- drag-and-drop i Resource Needs UI,
- Alert Center i Decision Center: rewizje, typed handlers, `Ask AI`, `Resolve with AI`, `OperatorGuidance`,
- `MarketingPlanVersion`, stages, stage gates, presence tracks.

Definition of Done:

- utworzenie workspace'u kończy się `TargetDefinition` z dowodami i confidence, a nie pustym formularzem,
- Sol dobiera modele do zadań przez router, a nie przez nazwę modelu w promptcie,
- Sol wie, który zasób lub konto jest potrzebny do którego zadania, i mówi to jako `ResourceRequirement`, nie jako założenie,
- Principal nie wykonuje side effectu ani nie zatwierdza własnej decyzji,
- brak kandydata daje deduplikowany `RESOURCE_GAP` i decyzję, nie fikcyjną możliwość wykonania,
- plan powstaje wyłącznie z registry i capabilities, bez branchy po nazwie platformy.

#### E3 — skala i multi-surface

6–8 tygodni zespołowo / 10–14 tygodni solo.

Zakres:

- rozszerzenie registry o kolejne platformy, surface types i semantic capabilities,
- trzeci i czwarty adapter, w tym co najmniej jeden bez wymaganego konta (oficjalne API) i co najmniej jeden `MANUAL_ONLY`,
- równoległe workstreamy, Daily Planner, Kanban, weighted fair queue,
- hierarchiczne budżety i budget ledger `RESERVE/CONSUME/RELEASE/ADJUST`,
- **RabbitMQ** jako trwały transport `job_id` z outboxem, inboxem, publisher confirms i reaperem — wprowadzany dopiero tutaj i tylko jeżeli istnieje więcej niż jeden runner albo kolejka realnie się piętrzy,
- **FORCE RLS**, osobne dispatcher/outbox/allocator login roles i test harness dwóch tenantów — wprowadzane dopiero tutaj i tylko jeżeli istnieje drugi workspace albo drugi użytkownik.

Definition of Done:

- nowa platforma wymaga registry, adaptera i capability mapping, a nie zmiany core planera,
- trzy workstreamy postępują równolegle przy `BrowserProfile concurrency = 1`,
- dołożenie brokera nie zmieniło żadnej komendy domenowej; duplikat delivery nie duplikuje `job_attempt`,
- workspace A nie odczytuje, nie wiąże ani nie wyszukuje danych workspace'u B,
- jeden workspace nie zużywa całego globalnego budżetu konta,
- decyzja o wprowadzeniu brokera i RLS ma zapisane uzasadnienie z danych, a nie „bo tak było w planie”.

#### E4 — pomiar i replanning

4–5 tygodni zespołowo / 7–9 tygodni solo.

Zakres:

- post metric windows, account/community/publication snapshots, heatmapy dnia i godziny,
- community signals, sentiment observations, topic trends, content pattern insights, plan review windows,
- deterministyczny goal health: `NO_DATA | ON_TRACK | AT_RISK | OFF_TRACK`,
- `PlanRevisionProposal`, diff, grandfathering, stop-loss,
- prompt/model canary i eval harness w pętli produkcyjnej.

Definition of Done:

- pojedynczy komentarz lub lajk nie wywołuje replanu; próg to co najmniej dwa review windows albo twardy guardrail,
- replan tworzy nową wersję i czytelny diff, nie mutuje starej w miejscu,
- późna metryka aktualizuje goal progress, ale nie mutuje zamkniętego wyniku dnia,
- plan reaguje na blokady, brak postępu i utratę capability,
- zmiana promptu nie trafia na produkcję bez evala i canary.

#### E5 — ekspansja

6+ tygodni zespołowo / 10+ tygodni solo; zakres otwarty.

Zakres:

- kanały płatne: Google/Meta Ads execution adapters, budget policy, osobny ad ledger,
- wiele workspace'ów na jednym koncie z conflict checks i podziałem capacity,
- wielu użytkowników i RBAC, wiele `CodexConnection`,
- zdalne Browser Runnery, WebRTC jako alternatywny `InteractiveTransport`, jeżeli pomiary to uzasadnią,
- zaawansowana atrybucja, client portal, campaign calendar.

Definition of Done:

- ad spend rośnie wyłącznie po decyzji człowieka i mieści się w zatwierdzonym ledgerze,
- drugi write assignment tego samego konta nie łamie globalnej capacity ani fairness,
- RBAC rozdziela rolę autora treści od roli zatwierdzającego,
- brak ad connectora daje `MANUAL_ONLY` albo `RESOURCE_GAP`, nie fikcyjną możliwość wykonania.

### 25.4. Tabela zbiorcza

| Etap | Faza | Mały zespół | Solo | Co dowodzi |
| --- | --- | --- | --- | --- |
| E1 | F0 — spike i Go/No-Go | tydzień 1 | tygodnie 1–2 | headless CDP jest wykonalny, a login/MFA mają jawną ścieżkę wyjątkową |
| E1 | F1 — correctness kernel minimalny | tygodnie 2–3 | tygodnie 3–5 | stan i zgody przeżywają restart; nic nie omija `ExecutionIntent` |
| E1 | F2 — konta i headless read | tygodnie 4–6 | tygodnie 6–10 | odczyt jest funkcją połączonego konta, a konta są izolowane |
| E1 | F3 — runtime modeli, discovery i ocena jakości | tygodnie 7–9 | tygodnie 11–15 | potrafimy typować wartościowe dyskusje i uzasadnić wybór |
| E1 | F4 — drafty i kolejka approvali | tygodnie 10–11 | tygodnie 16–18 | treść jest ugruntowana w wiedzy o projekcie i zawsze przechodzi przez człowieka |
| E1 | F5 — pierwsza akcja przez CDP | tygodnie 12–13 | tygodnie 19–21 | zatwierdzona akcja wykonuje się raz, z dowodem i pomiarem |
| E1 | F6 — okno walidacyjne, PASS/BLOCK | tygodnie 14–21 | tygodnie 22–30 | discovery i cold start działają na dwóch różnych archetypach |
| E2 | całość | 6–8 tygodni | 10–13 tygodni | orkiestrator ustala target, dobiera modele i rozdziela pracę |
| E3 | całość | 6–8 tygodni | 10–14 tygodni | system skaluje się na kolejne surface bez zmiany rdzenia |
| E4 | całość | 4–5 tygodni | 7–9 tygodni | plan uczy się z pomiarów, nie z szumu |
| E5 | całość | 6+ tygodni | 10+ tygodni | kanały płatne, wiele workspace'ów i praca zespołowa |

Suma E1: około 19–21 tygodni zespołowo i około 28–30 tygodni solo, z czego 6–8 ostatnich tygodni to kalendarzowe okno pomiarowe, którego nie skraca dokładanie ludzi. Ta liczba jest kosztem odpowiedzi na pytanie, czy projekt ma sens — a nie kosztem zbudowania produktu.

### 25.5. Reguła przejścia między etapami

1. Przejście E1 → E2 wymaga `PASS` według kryteriów z sekcji 25.2 (E1.F6). `CONDITIONAL_PASS` nie odblokowuje E2 do czasu zamknięcia naprawy i powtórzenia naruszonego pomiaru.
2. `BLOCK` oznacza zatrzymanie budowy i decyzję o zmianie podejścia. Nie oznacza „róbmy E2, może się ułoży”.
3. Żadna faza E2+ nie startuje równolegle „na zapas”. Kod, którego nie potrzebuje bramka E1, nie powstaje przed decyzją.
4. Wyjątkiem są wyłącznie porty i schematy wymienione w E1.F1 jako świadome odroczenia — istnieją jako interfejsy, nie jako implementacje.
5. Każde następne przejście etapowe ma tę samą formę: zapisane przed startem kryteria, zamrożony zakres na czas pomiaru, zapisana decyzja z dowodami.
6. Decyzję etapową podejmuje człowiek. Sol może przygotować `DecisionProposal` z dowodami, ale nie zatwierdza własnej rekomendacji.

### 25.6. Ryzyka realizacji etapu 1

Ryzyka wykonawcze, nie produktowe. Dotyczą tego, co może rozłożyć harmonogram E1. Ryzyka produktowe i domenowe całego systemu są w sekcji 27 i nie są tu powtarzane — jeżeli pozycja występuje w obu, obowiązuje próg z sekcji 27.

| Ryzyko | Objaw | Odpowiedź |
| --- | --- | --- |
| Kruchość adapterów | zmiana DOM lub layoutu wywraca odczyt i selektory z dnia na dzień | wąski kontrakt adaptera, fixture site, contract tests, `adapter_version` w evidence, kontrolowana degradacja do `UNKNOWN` zamiast cichego błędu |
| Okno czasowe wątków | wartościowa dyskusja żyje kilka godzin; kolejka approvali dodaje opóźnienie i akcja trafia po czasie | pomiar `time_to_approval` jako metryka pierwszej klasy, priorytet w Opportunity Inbox po świeżości, dwa stałe okna przeglądu dziennie dla archetypów wolnych i natychmiastowa notyfikacja dla szybkich (`SHORT_FORM_FEED`, `PROFESSIONAL_GRAPH`), zgodnie z TTL z 22.10, jawna rezygnacja z wątków starszych niż próg |
| Dyscyplina operatora w kolejce approvali | kolejka rośnie, zgody stają się mechaniczne albo praca staje | dzienny `ACTION_TARGET` dopasowany do realnej dostępności człowieka, twardy limit rozmiaru kolejki, `Snooze` i wygasanie propozycji zamiast nieskończonego backlogu, tygodniowy przegląd odrzuceń |
| Jakość klasyfikatora bez ground truth | nie wiadomo, czy ranking jest dobry, bo nie ma etykiet | baseline losowego wyboru z tego samego surface, prospektywna rejestracja predykcji przed pomiarem, okna 1/6/24 h jako etykieta z opóźnieniem, fixture evals na sanitizowanych snapshotach |
| Zimny start konta i brak sygnału zwrotnego | nowe konto długo nie dostaje żadnej reakcji, więc pętla uczenia nie ma wejścia | presence track z `OBSERVE_ONLY` i realistycznym horyzontem, leading indicators zamiast wyłącznie outcome, drugie konto z historią jako punkt odniesienia, próg czasu po którym `NO_DATA` jest wynikiem, a nie porażką pomiaru |
| Koszt utrzymania | dwa adaptery, dwa profile i capsule zjadają czas, którego nie ma na produkt | limit dwóch archetypów w E1, zakaz dokładania trzeciego przed `PASS`, budżet czasu na utrzymanie zapisany w planie tygodnia, feature freeze w F6 |
| Rozjazd registry i adapterów | capability mapping mówi co innego niż faktyczne zachowanie adaptera | okresowy re-probe, `verified_at`/`expires_at` na capability snapshot, wygasła capability przechodzi do `UNKNOWN`, contract test capability mapping w CI |
| Ścieżka headful staje się regułą | coraz więcej operacji „tylko przez interaktywny widok” | lista dozwolonych powodów headful (`LOGIN`, `MFA`, `CHALLENGE`, `HUMAN_TAKEOVER`) jest zamknięta; każde inne użycie tworzy alert i wymaga decyzji, a nie cichego wyjątku |

## 26. Minimalne testy akceptacyjne

Testy są oznaczone etapem, w którym zaczynają obowiązywać. Test etapu późniejszego nie jest wymaganiem dla wcześniejszego — zakres odłożony w sekcji 21.5 nie jest testowany w E1.

### Etap 1 — bramka, odczyt i wejście w community

- surface z `access_mode = ACCOUNT_REQUIRED` albo `MEMBERSHIP_REQUIRED` bez aktywnego assignmentu konta nie daje `SEARCH_CONTENT`, `READ_THREAD` ani `READ_METRICS`, tylko `ResourceRequirement`,
- surface z `access_mode = PUBLIC_API_NO_ACCOUNT` jest czytany bez profilu przeglądarki i bez `ProfileLease`,
- przy równym audience fit planer wybiera `PUBLIC_API_NO_ACCOUNT` przed ścieżką wymagającą konta,
- niezweryfikowany `access_mode` daje `UNKNOWN` i jest traktowany jak `ACCOUNT_REQUIRED`,
- utrata sesji w trakcie discovery zatrzymuje zależne taski odczytowe i nie zwraca pustej listy jako wyniku,
- konto w `REAUTH_REQUIRED` nie produkuje danych, tylko alert `NEEDS_LOGIN_OR_MFA`,
- cała praca automatu wykonuje się w trybie `HEADLESS`; run E1 nie startuje displayu ani stream gatewaya,
- żądanie loginu, MFA, challenge albo takeover tworzy przejście trybu z zapisanym powodem i po `SAFE_STOP`,
- profil zalogowany w trybie headful jest używany przez headless bez ponownego loginu i bez kopiowania cookies,
- po powrocie do `HEADLESS` adapter ponownie wykonuje `verify_identity()` i `probe_capabilities()`,
- nieudane przełączenie trybu przenosi profil do `QUARANTINED` i nie uruchamia drugiej kapsuły,
- capability niedziałająca headless ma `required_browser_mode = INTERACTIVE` albo `MANUAL_ONLY`, nigdy cichy fallback,
- komentarz, odpowiedź i reakcja są niewykonalne bez approvalu dokładnej rewizji, także przy ręcznym wywołaniu adaptera,
- edycja draftu tworzy nową rewizję, unieważnia zgodę i nie przedłuża pierwotnego TTL,
- draft po `draft_ttl` przechodzi w `EXPIRED`, zużywa budżet i nie jest regenerowany bez decyzji operatora,
- wątek z `window_state = CLOSED` nie tworzy nowego draftu,
- przekroczenie dziennego capu kolejki daje `APPROVAL_CAPACITY_EXCEEDED`, a nadmiar nie przechodzi na następny dzień,
- cap kolejki approvali jest nadrzędny wobec budżetu presence tracku, gdy oba limity się różnią,
- kolejka approvali jest sortowana rosnąco po `expires_at` w obrębie `priority_tier`,
- akcja przekraczająca dzienny budżet presence tracku jest odrzucana przed wygenerowaniem draftu,
- awans presence tracku bez kompletu evidence jest odrzucany niezależnie od wieku konta,
- usunięcie wkładu przez moderację przenosi presence track do `REASSESSMENT_REQUIRED` i wstrzymuje kolejne akcje na tym surface,
- dwa konta tego samego workspace'u nie otrzymują akcji w tym samym wątku,
- odpowiedź człowieka na nasz wkład tworzy zadanie `CONVERSATION_FOLLOW_UP` o priorytecie wyższym niż każdy nowy wątek z Opportunity Inbox,
- sygnał negatywny w `ThreadQualityAssessment` nie jest kompensowany żadną liczbą sygnałów pozytywnych,
- flaga fake engagementu nie obniża `priority_tier` i nie odrzuca wątku,
- sygnał niewidoczny z zewnątrz zapisuje się jako `UNKNOWN`, nigdy jako zero,
- ranking kandydatów jest odtwarzalny na tym samym `MarketingRegistrySnapshot`, `heuristic_policy_version` i zestawie snapshotów,
- stan `UNKNOWN` po `SUBMIT_STARTED` zużywa budżet, tworzy alert `UNKNOWN_EXTERNAL_RESULT` i nie uruchamia drugiej próby,
- wkład bez potwierdzonego evidence nie jest liczony do progów bramki,
- metryki bramki są projektowane osobno per `ChannelArchetype`; komplet progów spełniony na jednym archetypie nie zamyka bramki,
- próba zmiany progu w trakcie okna tworzy nową wersję planu i oznacza bieżący wynik etapu jako unieważniony,
- decyzja `PASS` / `CONDITIONAL_PASS` / `BLOCK` nie może zostać zapisana bez kompletu evidence refs per metryka, archetyp i konto,
- Sol przygotowuje raport bramki, ale nie zatwierdza własnej rekomendacji awansu etapu.

### Account i assignment

- trzy konta posiadają trzy różne profile,
- przeciągnięcie konta nie kopiuje profilu,
- jeden account assignment nie może wskazać obcej organizacji,
- account extension nie ma własnego lifecycle'u i nie może rozszerzyć actions/capacity/approval parent assignmentu,
- drop nie zmienia aktywnego runa,
- DRAINING kończy się bez pozostawienia tasków,
- globalna capacity jest nadrzędna wobec sumy workspace'ów,
- ten sam `external_account_id` nie może utworzyć drugiego konta/profilu w organizacji,
- task zachowuje immutable `TaskResourceBinding` do allocation version; composite FKs wykluczają mieszanie assignmentu i reservation,
- ręczna próba związania allocation version z reservation innego łańcucha jest odrzucana przez composite FK,
- ResourceRequirement nie tworzy assignmentu bez właściwej decyzji,
- repozytorium, budżet ani analytics connection bez aktywnej wersji `WorkspaceResourceAssignment` nie mogą zostać użyte przez task,
- reservation z przeterminowaną generation nie może zostać consumed ani released przez starego workera,
- utrata capability degraduje allocation i zatrzymuje zależne taski,
- brak matchu nie uruchamia nieskończonego rematchingu.

### Lease i browser

- dwaj workerzy nie uruchamiają jednego profilu,
- stary runner po expiry nie wykona tool calla,
- restart Gatewaya przy żyjącym Chrome nie uruchamia drugiego procesu,
- PID reuse nie myli starego i nowego procesu,
- kill przed i po submit prowadzi do właściwego stanu,
- dwa konta tej samej platformy mają osobny proces, profil, display i stream,
- composite tenant FK blokuje profil, lease lub browser session wskazujące konto/profil obcej organizacji,
- login, MFA i popupy są widoczne w osadzonym panelu,
- replay StreamTicketu jest odrzucany,
- CONTROL ticket nie może zostać wydany bez atomowego HUMAN InteractionLease i jest związany z jego generation,
- `Otwórz interaktywnie` przy aktywnym AUTOMATION lease przechodzi przez HumanTakeover/SAFE_STOP przed wydaniem CONTROL ticketu,
- dwa równoległe claimy InteractionLease dla jednej browser session kończą się jednym aktywnym lease'em,
- input po odwołaniu interaction lease jest odrzucany nawet przez nadal otwarty WebSocket,
- VIEW_ONLY nie może wysłać inputu także po ręcznej próbie ramek RFB, niezależnie od ustawienia klienta noVNC,
- drugi controller nie może wysyłać inputu,
- dwie równoległe próby stworzenia BrowserProfile dla jednego konta kończą się jednym aktywnym profilem,
- crash Xvnc, window managera albo stream gatewaya uruchamia recovery całej kapsuły i nie zwalnia profilu przed potwierdzonym unmountem,
- osobne TTL ticketu, sesji, idle i cookie są egzekwowane, a reconnect wymaga cookie albo nowego ticketu,
- reconnect viewer'a nie uruchamia nowego Chrome,
- publiczna sieć nie ma dostępu do CDP ani VNC,
- identity mismatch blokuje akcję przed fill,
- capability loss daje `CAPABILITY_BLOCKED`,
- expired capability staje się `UNKNOWN`, a `MANUAL_ONLY` tworzy human task,
- takeover nie odtwarza automatycznie submitu.

### Goal Engine

- goal „karma +5” nie przekracza dziennego budżetu komentarzy,
- goal „10 accepted connections” tworzy child task `WAITING_EXTERNAL` i nie generuje dodatkowych wysyłek,
- goal „10 confirmed sends” kończy się dopiero po dziesięciu dowodach albo jako PARTIAL/BLOCKED,
- brak eligible targets nie obniża progu jakości,
- UNKNOWN_RESULT zużywa submit budget,
- pause workspace'u blokuje nowe taski,
- weighted fairness nie głodzi drugiego workspace'u (od E3).

### Plan, wiedza i memory — od E2

- zmiana Intelligence Profile nie mutuje zaakceptowanego proposal,
- stary task zachowuje stary plan snapshot,
- zmiana community culture/rules tworzy nowy snapshot, a stary task zachowuje poprzednie snapshot IDs,
- source file jest tylko wejściem, a task korzysta z wersji zapisanej w Postgresie,
- usunięcie źródła unieważnia chunki, embedding i claim evidence zgodnie z retencją,
- RLS/retrieval nie zwraca danych innego workspace'u (RLS od E3; do tego czasu obowiązuje workspace-scoped read model),
- memory conflict nie staje się faktem bez review,
- unverified claim nie przechodzi,
- replan tworzy diff i nową wersję,
- bardziej restrykcyjny Intelligence Profile/assignment/policy po approvalu daje `AUTHORIZATION_CONTEXT_CHANGED`.

### Codex

- nieobsługiwany model/effort jest odrzucany przed turnem,
- verifier używa świeżego threadu,
- shell, exec i niedozwolone MCP są niedostępne,
- quota RED nie uruchamia opcjonalnego planera,
- prompt candidate nie trafia na produkcję bez eval/canary,
- model nie może zmienić konta, budżetu ani approval policy,
- Principal nie może wykonać external action ani zatwierdzić własnej decyzji,
- Principal nie może commitować proposal kind lub command kind spoza aktywnego authority grantu,
- PrincipalRun kończy się przed oczekiwaniem na odpowiedź człowieka,
- przekroczenie wall time/turn/tool/output limitu wysyła interrupt, po grace period zabija runtime i nie pozwala commitować częściowego bundle'a,
- reroute poniżej quality floor daje `ROUTE_MISMATCH` i wynik nie jest commitowany,
- reroute bez obserwowalnego effective effort nie udaje wartości; unsupported requested effort albo verifier wymagający potwierdzenia failuje zamknięcie,
- `model/list` pobiera wszystkie strony do `nextCursor=null`; niepełny albo stary snapshot daje `WAITING_CAPABILITY`,
- expiry katalogu uruchamia `CAPABILITIES_SYNCING`, a sukces budzi oczekujące joby; błąd przechodzi przez bounded `SYNC_RETRY`,
- nowy run może resume'ować zgodny stable ThreadSecurityBinding, ale różnica workspace'u, roli, principal generation lub tool manifestu jest fail-closed,
- utrata auth/quota przenosi job do właściwego `WAITING_*` bez trzymania runtime lease'u,
- każdy turn jawnie ustawia model, effort i output schema,
- App Server i SDK nie uruchamiają dwóch procesów na jednym `CODEX_HOME`.

### Queue i recovery — od E3 (broker, outbox, inbox)

- commit domenowy bez publish zostaje później wysłany z outboxa,
- publisher confirm bez oznaczenia outboxa może dać duplikat, lecz inbox go deduplikuje,
- unroutable publish z `mandatory=true` i `basic.return` nie oznacza outboxa jako wysłanego,
- wiadomość zawsze niesie `message_id=outbox_event_id`, a duplicate/stale dispatch jest ACKniętym no-opem,
- ACK następuje po commit claimu joba,
- crash po ACK, ale przed wykonaniem, jest odzyskiwany przez lease/reaper,
- heartbeat i wynik starego attemptu po zmianie lease generation przegrywają CAS i nie zapisują outboxa,
- reaper w jednej transakcji zamyka stary attempt, zwiększa generation/dispatch version i tworzy nowy attempt z outboxem,
- reaper nie przekracza `attempt_limit`; ostatnia porażka terminalizuje job i tworzy alert/DLQ outbox,
- równoległe recovery nie może utworzyć dwóch prób z tym samym `(job_id, attempt_no)`,
- reconciler odbudowuje dispatch dla runnable joba, który nie ma lease'u ani żywego outbox eventu,
- prefetch i WIP nie pozwalają jednemu workerowi zabrać całej kolejki,
- reuse tego samego pooled connection po wyjątku nie zachowuje tenant contextu poprzedniego workspace'u,
- org-scope dispatcher nie może odczytać payloadu domenowego bez wejścia w jawny tenant context,
- dispatcher/outbox/allocator login roles nie mają bezpośredniego SELECT na tabele i mogą wywołać tylko swoje dokładne funkcje `control_api`,
- allocator nie przyjmuje tenant scope od callera; próba podmiany requirement ID nie ujawnia handles innej organizacji,
- DLQ tworzy alert, a job nadal istnieje w Postgresie.

### Alerty i decyzje

- identyczne zdarzenia zwiększają occurrence count jednego alertu,
- `Snooze` nie oznacza `Resolve`,
- edycja DecisionProposal tworzy nową rewizję i unieważnia approval,
- zmiana preconditions przed zastosowaniem daje `STALE`,
- `Resolve with AI` tworzy propozycję, nie wykonuje efektu,
- expiry decyzji domyślnie nie zwiększa uprawnień ani budżetu.

### Generalized marketing — od E2

- dwa różne archetypy biznesowe tworzą różne channel hypotheses i metrics,
- core Principal/Goal Engine nie rozgałęzia się po nazwie platformy,
- brak adaptera daje `MANUAL_ONLY`, `UNKNOWN` albo ResourceRequirement,
- pojedyncza negatywna reakcja nie wywołuje replanu bez event threshold.

### Approval

- edycja draftu unieważnia approval,
- approval innego konta nie pasuje do ExecutionIntent,
- source change zatrzymuje wykonanie,
- `SUBMIT_STARTED` nie jest automatycznie retry'owane,
- stary execution grant jest jednorazowy i wygasa.

## 27. Najważniejsze ryzyka i odpowiedzi

| Ryzyko | Odpowiedź |
| --- | --- |
| Pomylenie kont | assignment, task-scoped capability, identity preflight, fencing |
| Split-brain profilu | Postgres lease, generation, flock, reconciliation |
| Duplikat zewnętrznej akcji | one submit attempt, UNKNOWN, manual review |
| Zmiana UI | adapter capabilities, fixture evals, controlled degradation |
| Prompt injection | brak shell/sekretów, read-only MCP, narrow Action Adapter |
| Nieskończona pętla celu | daily budget, time window, stop conditions, no auto-compensation |
| Optymalizacja wyłącznie pod karmę | oddzielenie action target od external outcome i quality gates |
| Jeden workspace zużywa całe konto | global capacity i weighted fairness |
| AI sam zmienia limity | PlanRevisionProposal, policy envelope i approval |
| Zły routing modeli | model/list, quality floors, eval-approved routes |
| Wyczerpanie Codex usage | quota gate, backpressure, Luna/Terra routing |
| Pamięć utrwala błąd | candidates, provenance, confidence, conflict review |
| Principal staje się omnipotentny | typed proposals, authority grants, brak side effect i self-approval |
| Pętla event → Principal → event | causal depth, coalescing, cooldown, run budget i dedupe |
| Cross-workspace data leak | composite tenant FKs, FORCE RLS, scoped retrieval/cache i testy dwóch tenantów |
| Knowledge poisoning | provenance, candidate quarantine, conflicts i review przed canonical activation |
| Resource requirement storm | fingerprint, retry ceiling, terminal UNSATISFIED i human decision |
| Rabbit delivery duplicate/loss | PG source of truth, outbox, confirms, inbox dedupe, lease i reaper |
| Stara decyzja jest zastosowana | immutable revision, precondition hash, REVALIDATING → STALE |
| Migracja profilu nie działa | runner affinity, cold snapshot, reauth fallback |
| Wyciek ekranu/loginu | isolated stream origin, one-time tickets, no recording, interaction fencing |
| Dwa konta współdzielą sesję | osobny profile/process/display/capsule per account |
| noVNC ma zbyt duże opóźnienie | pomiar; wymienny InteractiveTransport i WebRTC dopiero po evidence |
| Brak dokładnej metryki | UNKNOWN, confidence, append-only snapshots |
| Hałas danych wywołuje replan | minimum sample, dwa review windows, human gate |
| Plan jest „Reddit/LinkedIn-only” | business/channel archetypes, semantic capabilities i contract test nowego connectora |
| Codex nie sprawdza się w marketingu | E1.F0 evale i jawny Go/No-Go; domena nie zależy od jednego runtime'u |
| Etap 1 nie zamyka się rozstrzygnięciem | progi zapisane przed oknem, `NO_DATA` z limitem jednego przedłużenia, twardy stop na zdarzeniach negatywnych |
| Zakres E1 rośnie w trakcie okna | feature freeze w E1.F6, limit dwóch archetypów, zakaz dokładania trzeciego przed `PASS` |
| Dryf w stronę polemiki i wątków viralowych | `ContributionMixReport` co tydzień, limit udziału wkładów polemicznych, selekcja wątków po dopasowaniu do ICP, nie po dynamice |

## 28. Proponowana struktura repozytorium

~~~text
community-os/
├── apps/
│   ├── dashboard/                 # Next.js
│   ├── api/                       # FastAPI control plane
│   ├── scheduler/                 # PG jobs, Rabbit consumers, leases, reaper
│   ├── codex-runtime/             # SDK, auth adapter, model router
│   ├── browser-runner/            # Session Capsules, CDP gateway
│   └── stream-gateway/            # noVNC tickets, WSS, interaction leases
├── packages/
│   ├── contracts/
│   ├── domain/
│   ├── knowledge/
│   ├── principal/
│   ├── decision-center/
│   ├── channel-registry/
│   ├── platform-adapters/
│   ├── policies/
│   ├── prompts/
│   ├── evals/
│   └── observability/
├── prompts/
│   ├── workspace-principal/
│   ├── plan-critic/
│   ├── daily-planner/
│   ├── researcher/
│   ├── draft-writer/
│   └── verifier/
├── tests/
│   ├── fixture-site/
│   ├── contracts/
│   ├── integration/
│   ├── fault-injection/
│   ├── evals/
│   └── e2e/
├── infra/
│   ├── compose/
│   ├── migrations/
│   ├── rabbitmq/
│   ├── browser-capsule/
│   └── runner/
└── docs/
    ├── architecture/
    ├── threat-model.md
    ├── state-machines.md
    ├── product-metrics.md
    └── runbooks/
~~~

Moduły są logicznie rozdzielone, ale system pozostaje modularnym monolitem z kilkoma procesami wykonawczymi.

## 29. Decyzje, których nie wolno rozmyć

1. Konto jest zasobem organizacji, a assignment łączy je z workspace'em.
2. Connected Resource Pool obejmuje również repozytoria, analytics, business profiles i ad accounts.
3. Jeden `SocialAccount` ma jeden `BrowserProfile`; aktywny profil ma jeden proces, display i sesję.
4. Dwa konta tej samej platformy nigdy nie współdzielą `user-data-dir`, procesu ani `BrowserContext`.
5. Warstwa wykonania działa w trybie headless Chrome sterowanym przez CDP. Tryb interaktywny to headful Chrome na virtual displayu streamowany do dashboardu i jest ścieżką wyjątkową wyłącznie dla logowania, MFA, challenge i takeover; nie „headless z ekranem”.
6. CDP i VNC nigdy nie są wystawiane do frontendu lub publicznej sieci.
7. Codex nie wybiera konta, nie widzi surowego CDP i nie otrzymuje DB credentials.
8. Ogólne browser tools są read-only; zapis wykonuje narrow Action Adapter na podstawie ExecutionIntent.
9. Approval jest związany z immutable payloadem; po zewnętrznym submit nie istnieje automatyczne retry.
10. Workspace Principal jest logiczną rolą, a nie stale działającym agentem lub publiczną personą.
11. Principal może tworzyć typed proposals, ale nie może wykonywać side effectów ani zatwierdzać własnych decyzji.
12. Alert, DecisionProposal, DecisionReview, DecisionApplication i Action Approval są oddzielnymi obiektami.
13. Plan, Intelligence Profile, knowledge, memory, decyzje i prompty są wersjonowane.
14. Pliki projektu są źródłami ingestu; kanoniczna wiedza oraz snapshoty istnieją w PostgreSQL.
15. Każdy worker widzi wyłącznie workspace-scoped read model; RLS i composite FKs są defense-in-depth.
16. `ResourceRequirement` opisuje potrzebę, ale nie nadaje assignmentu, authority ani lease'u.
17. LinkedIn, Reddit, HN, Facebook, Google i fora są connectorami/surfaces, nie hardcoded planem produktu.
18. Business archetype jest hipotezą; Sol dobiera kanały według celu, rynku, geografii, evidence i budżetu.
19. Cele zewnętrzne nie zwiększają automatycznie budżetu działań; ad spend rośnie tylko po decyzji człowieka.
20. Metryki i community signals są append-only, ze źródłem, oknem, confidence i sample size.
21. Replan reaguje na progi i okna, nie na pojedynczy like lub komentarz.
22. PostgreSQL jest źródłem prawdy; RabbitMQ jest at-least-once transportem job IDs i wchodzi dopiero w E3. Do tego czasu transportem jest PostgreSQL, a różnicę ukrywa `JobTransportPort`.
23. Python Codex SDK realizuje turny; eksperymentalny App Server control/auth wymaga E1.F0 Go/No-Go.
24. Użycie Codexa jako marketing runtime jest hipotezą testowaną evalami, nie założeniem bez dowodu.
25. Etap 1 ma jednego operatora i jedno `CodexConnection`; `CodexConnection concurrency = 1` oraz `BrowserProfile concurrency = 1`.
26. Etap 1 jest bramką pass/block całego projektu. `BLOCK` oznacza zatrzymanie budowy, nie obniżenie progów ani przedłużanie okna do skutku.
27. Etap awansuje przez evidence i decyzję człowieka. Upływ czasu, zamknięty sprint i skończona faza roadmapy nie są dowodem.
28. Etap 1 jest ważny wyłącznie na dwóch różnych `ChannelArchetype`. Jeden kanał dowodzi dopasowania do platformy, nie generalizacji.
29. W Etapie 1 każda akcja zewnętrzna — komentarz, odpowiedź, reakcja, dołączenie do community — jest `AUTOMATED_AFTER_APPROVAL` z jednorazowym `ExecutionIntent` dla dokładnej rewizji `ActionProposal`.
30. Capability odczytu jest funkcją połączonego konta i jego stanu. Na surface za login wallem brak konta oznacza brak capability, a nie „słabszy scraping”.
31. Registry rozróżnia ścieżki dostępu przez `access_mode` i preferuje najtańszą dostępną. Publiczne API nie jest zakładane domyślnie ani podstawiane pod platformę, która go nie ma.
32. Ranking wątków jest heurystyką porządkową klas sygnału. Nie liczymy score udającego rachunek rankingu platformy i zapisujemy `heuristic_policy_version` przy każdej ocenie.
33. Sygnał niewidoczny z zewnątrz ma stan `UNKNOWN`. Detekcja fake engagementu działa log-only do czasu ręcznie zaetykietowanej próbki i zmierzonej precision/recall.
34. Wątek ma skończone okno życia. Draft ma TTL, kolejka approvali ma twardy dzienny cap, a wygasła propozycja nie jest regenerowana automatycznie.
35. Target workspace'u jest wersjonowanym artefaktem `TargetDefinition` tworzonym przez orkiestratora i aktywowanym przez człowieka. Wpis w targecie nigdy nie nadaje authority.
36. Progi bramki są zapisane przed startem okna pomiarowego. Zmiana progu tworzy nową wersję planu i unieważnia bieżący wynik etapu.
37. Tryb `INTERACTIVE` ma zamkniętą listę dopuszczalnych powodów. Każde inne użycie headful tworzy alert i wymaga decyzji, a nie cichego wyjątku.
38. Kontynuacja rozmowy jest osobnym, priorytetowym typem zadania i prowadzi ją człowiek. Otwieranie rozmów bez ich kontynuowania buduje pozór obecności.

## 30. Źródła researchu

### OpenAI / Codex

- [Authentication — oficjalna dokumentacja OpenAI](https://learn.chatgpt.com/docs/auth)
- [Codex SDK — programowe sterowanie lokalnymi agentami](https://learn.chatgpt.com/docs/codex-sdk)
- [Codex App Server — auth, modele, limity i protokół](https://learn.chatgpt.com/docs/app-server)
- [Codex pricing and model roles — Sol, Terra, Luna](https://learn.chatgpt.com/docs/pricing)
- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [MCP w Codex](https://learn.chatgpt.com/docs/extend/mcp)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex sandboxing](https://learn.chatgpt.com/docs/sandboxing)

### Chromium / Playwright

- [Playwright BrowserType — connectOverCDP i persistent profiles](https://playwright.dev/docs/api/class-browsertype)
- [Playwright browsers](https://playwright.dev/docs/browsers)
- [Playwright Docker and Chromium sandbox](https://playwright.dev/docs/docker)
- [Chrome 136 remote debugging](https://developer.chrome.com/blog/remote-debugging-port)
- [Chrome for Testing](https://developer.chrome.com/docs/automation-and-testing/chrome-for-testing)
- [Chrome Headless](https://developer.chrome.com/docs/automation-and-testing/headless)
- [Chromium user data directory](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)

### Browser streaming

- [noVNC — HTML VNC client i WebSocket requirements](https://github.com/novnc/noVNC)
- [noVNC API](https://github.com/novnc/noVNC/blob/master/docs/API.md)
- [websockify — WebSocket-to-TCP bridge](https://github.com/novnc/websockify)
- [TigerVNC](https://github.com/TigerVNC/tigervnc)
- [Apache Guacamole architecture — alternatywny HTML5 gateway](https://guacamole.apache.org/doc/1.6.0/gug/guacamole-architecture.html)
- [W3C WebRTC — późniejszy InteractiveTransport](https://www.w3.org/TR/webrtc/)

### PostgreSQL / RabbitMQ execution

- [PostgreSQL Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PostgreSQL CREATE FUNCTION — SECURITY DEFINER i bezpieczny search_path](https://www.postgresql.org/docs/current/sql-createfunction.html)
- [RabbitMQ consumer acknowledgements and publisher confirms](https://www.rabbitmq.com/docs/confirms)
- [RabbitMQ reliability guide](https://www.rabbitmq.com/docs/reliability)
- [RabbitMQ quorum queues](https://www.rabbitmq.com/docs/4.2/quorum-queues)
- [RabbitMQ dead-letter exchanges](https://www.rabbitmq.com/docs/dlx)

## 31. Końcowy obraz produktu

~~~text
Jan łączy Codex przez konto ChatGPT
    ↓
tworzy workspace i dodaje README, repozytorium, URL-e lub materiały
    ↓
PostgreSQL przechowuje wersjonowane źródła, fakty i Intelligence Profile
    ↓
Sol Workspace Principal diagnozuje typ projektu i rynek
    ↓
proponuje channel hypotheses, marketing motions i potrzebne resources
    ↓
Decision Center pokazuje „połącz Reddit/HN/LinkedIn” albo
„potrzebny Google Business Profile, Meta/Google Ads i budżet”
    ↓
operator łączy zasób w streamowanym browserze w dashboardzie
    ↓
każde konto dostaje osobny profil, proces, display i AccountAssessment
    ↓
Sol tworzy wersjonowany, mutowalny plan, etapy, resources, modele i gates
    ↓
operator zatwierdza plan oraz autonomy/budget envelope
    ↓
Postgres jobs + RabbitMQ uruchamiają Daily Planner i Kanban
    ↓
Luna wykonuje research/ekstrakcję, Terra analizę i drafty
    ↓
świeży verifier ocenia evidence, claimy i wynik
    ↓
Alert/Decision Center prosi człowieka o zgodę lub dodatkową instrukcję
    ↓
zatwierdzona akcja otrzymuje jednorazowy ExecutionIntent
    ↓
Action Adapter działa na właściwej Session Capsule przez wewnętrzne CDP
    ↓
metryki, sentyment i community signals aktualizują goal/channel health
    ↓
Sol utrzymuje plan, tworzy alert albo proponuje immutable replan
    ↓
operator akceptuje, edytuje, odrzuca lub uruchamia Resolve with AI
    ↓
workspace przechodzi przez właściwy dla biznesu market-entry loop
~~~

To jest docelowy fundament: użytkownik dostarcza wiedzę o projekcie, a nie gotową strategię; Sol pełni rolę Principal, ale działa przez propozycje i decyzje; Terra i Luna wykonują tańszą pracę; konta są izolowanymi zasobami przypisywanymi do workspace'ów; Postgres zachowuje całą kanoniczną wiedzę i stan; RabbitMQ dowozi pracę; dashboard łączy plan, Kanban, alerty, approvals i interaktywny browser. System nie jest ograniczony do LinkedIna i Reddita — dobiera marketing mix do rodzaju biznesu, rynku, geografii, dostępnych kont, capabilities, budżetu i napływających dowodów.


## 32. Załącznik A — ustalenia z researchu i red teamu

> Data ustaleń: 2026-08-29. Załącznik zbiera dane źródłowe i wnioski, na których oparto Etap 1. Rozdzielono tu fakty zweryfikowane u źródła od interpretacji.

### 32.1. Zweryfikowane wagi rankingu X

Wartości odczytane bezpośrednio z `home-mixer/params/param.rs` w repozytorium `xai-org/x-algorithm` (odczyt 2026-08-29). Nie pochodzą z wtórnych streszczeń ani z archiwalnego `twitter/the-algorithm` z 2023, które miało inne wartości.

| Przewidywana akcja | Waga | Krotność lajka | Obserwowalne z zewnątrz |
| --- | --- | --- | --- |
| Reply, gdy autor posta jest mutualem (`5.0` + `15.0` boost) | 20.0 | 40× | treść tak, mutualność nie |
| Share przez skopiowanie linku | 20.0 | 40× | nie |
| Quote | 5.0 | 10× | tak |
| Reply | 5.0 | 10× | tak |
| Share przez DM | 5.0 | 10× | nie |
| Follow autora | 4.0 | 8× | tylko na własnym koncie |
| Share ogólny | 2.0 | 4× | nie |
| Repost | 1.0 | 2× | tak |
| Favorite / like | 0.5 | 1× | licznik tak, lista lajkujących nie |
| Click | 0.4 | 0.8× | nie |
| Open link | 0.2 | 0.4× | nie |
| Report | −234.0 | −468× | nie |
| Mute author | −58.8 | −117.6× | nie |
| Not interested | −43.2 | −86.4× | nie |
| Block author | −31.2 | −62.4× | nie |

Trzy zastrzeżenia, które decydują o sposobie użycia tych liczb:

1. Wagi mnożą **przewidywane przez model prawdopodobieństwa akcji konkretnego widza**, a nie surowe liczniki. Repozytorium wprost prostuje interpretację „jeden report kasuje 468 lajków”: bazowe prawdopodobieństwo reportu jest o rzędy wielkości niższe niż lajka i stąd wysoka waga.
2. Wagi opisują wyłącznie ranking feedu jednej platformy. Nie obowiązują na forach wątkowych, w społecznościach z własnymi normami ani w sieciach zawodowych.
3. Wartości są retrenowane, a repozytorium aktualizowane w cyklu kilkutygodniowym. Hardcodowany snapshot jest martwy w kwartał.

Dlatego system używa ich jako **porządkowej heurystyki intencji**, opisanej w 22.7, nigdy jako liczbowego score udającego rachunek platformy:

~~~text
reply od mutuala ≈ share przez link
    ≫ quote ≈ reply ≈ share przez DM
    ≫ follow
    ≫ share ogólny
    ≫ repost
    ≫ like
    ≫ click / dwell

sygnały negatywne są o dwa rzędy wielkości silniejsze
niż jakikolwiek sygnał pozytywny
~~~

Wniosek operacyjny, który przenosimy na wszystkie kanały: **wartość wkładu mierzy się intencją, jaką wywołuje, a nie liczbą reakcji**. Jedna odpowiedź od właściwej osoby jest warta więcej niż sto pustych lajków, a jedno zgłoszenie kasuje dorobek setek.

### 32.2. Sygnały nieobserwowalne i ich dopuszczalne proxy

Nie udajemy pomiaru tego, czego nie widać. Każdy sygnał z tej listy ma stan `UNKNOWN` i wyłącznie przybliżenie.

| Sygnał | Dlaczego niewidoczny | Dopuszczalne proxy |
| --- | --- | --- |
| Report, mute, block, „not interested” | prywatne sygnały platformy | schowane lub zwinięte odpowiedzi, przewaga krytycznych odpowiedzi przy niskiej liczbie reakcji, spadek widoczności kolejnych publikacji |
| Share przez link i DM | zdarzenie poza platformą | rozjazd między przyrostem wyświetleń a przyrostem reakcji w kolejnych snapshotach |
| Mutual-follow między obcymi kontami | listy relacji za login wallem, koszt paginacji | czy autor wątku odpowiada odpowiadającym, liczba wymian wieloturowych |
| Tożsamość lajkujących | listy lajków prywatne na części platform | całkowita rezygnacja z lajków w ocenie jakości |

### 32.3. Wnioski red teamu, które zmieniły plan

Koncepcja Etapu 1 przeszła przez adwersaryjny przegląd. Poniżej ustalenia, które wymusiły zmianę projektu, a nie tylko doprecyzowanie.

| Ustalenie | Konsekwencja w dokumencie |
| --- | --- |
| Score liczony z wag rankingu platformy jest błędem kategorii — wagi mnożą przewidywane prawdopodobieństwa per widz | zakaz liczbowego score udającego rachunek platformy; wagi wyłącznie jako heurystyka porządkowa (22.7, 32.1) |
| Optymalizacja pod przewidywany engagement to gradient w stronę polemiki i clickbaitu; funkcją celu rankingu feedu jest retencja, nie wartość wkładu | funkcją celu jest dopasowanie do odbiorcy i merytoryczność; wymagany okresowy raport rozkładu typów wkładu, bo dryf nie jest widoczny w pojedynczym approvalu |
| Wątek ma skończone okno życia; kolejka approvali konsumowana „gdy będzie czas” publikuje do martwych dyskusji | TTL draftu i wygasanie propozycji per archetyp kanału, twardy dzienny cap kolejki (22.10) |
| Detekcja kupionego engagementu z zewnątrz, bez sygnałów platformy, jest problemem badawczym | redefinicja na scoring substancji wątku; detekcja fałszywego engagementu startuje w trybie log-only |
| Human approval bez limitu wolumenu degeneruje do klikania bez czytania | cap dzienny, karta decyzyjna z kontekstem wątku, pomiar czasu decyzji jako metryka bramki |
| System, który otwiera rozmowy i ich nie kontynuuje, produkuje pozór obecności | ciągłość rozmowy jako osobny, priorytetowy typ zadania; kontynuacje prowadzi człowiek |
| Ocena „czy operator jest zadowolony z draftu” jest niefalsyfikowalna | progi bramki zapisane przed startem etapu, mierzone zachowaniem (publikacja bez istotnej edycji), nie deklaracją |
| Zimne konto nie wygeneruje sygnału zwrotnego w krótkim oknie | zasięg własnych publikacji nie jest metryką bramki; priorytet dla uczestnictwa w cudzych żywych wątkach |

### 32.4. Co z tego wynika dla zakresu

Etap 1 celowo nie zawiera warstw, które dokument opisuje w dalszych sekcjach: wielotenantowej izolacji, brokera wiadomości, ważonej sprawiedliwości kolejek, budżetów reklamowych ani wielu workspace'ów. Nie dlatego, że są błędne — opisano je, bo będą potrzebne — ale dlatego, że żadna z nich nie jest warunkiem udowodnienia tezy Etapu 1, a każda opóźnia moment, w którym poznamy odpowiedź na pytanie rozstrzygające o sensie projektu.
