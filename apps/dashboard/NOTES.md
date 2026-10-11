# apps/dashboard — notatki implementacyjne (pakiet P13, faza E1.F1)

> **Start wdrożenia po pivocie 19.09:** [START-PIVOT](../../START-PIVOT.md),
> [ROADMAP-PIVOT](../../docs/ROADMAP-PIVOT.md) i
> [przyjęty prototyp](../../docs/produkt/prototyp/workspace-studio.html).
> Prototyp pokazuje kierunek, nie gotową integrację. Poniższe notatki opisują
> istniejący panel; nie narzucają docelowego podziału widoków ani trybu akceptacji.

> **Docelowy UX, D-038 (2026-09-17):**
> [workspace w estetyce Codexa](../../docs/produkt/WIZJA-WORKSPACE.md).
> Projekty, rozmowa i Konta zastępują panel operatora jako główne doświadczenie.
> To decyzja do wdrożenia; poniżej są notatki o istniejącej implementacji.
> Interaktywność wymaga przeglądu wcześniejszych założeń renderowania i testów,
> przy zachowaniu sesji i poświadczeń control plane po stronie serwera.

> **2026-09-03 — przeprojektowanie wizualne (Apple HIG).** Kierunek, tokeny, system komponentów,
> mapa stan → ton pastylki, decyzje i dziennik: `docs/DASHBOARD-DESIGN.md`. Zmienił się wygląd
> (pasek boczny z ikonami, karty, kafelki, pastylki, `<details>` dla rzadkich formularzy), nie
> zachowanie: trzy stany ekranu, brak słownika sukcesu, brak afordancji ponowienia, formularze bez
> JS, kontrakty API — bez zmian, suita 113/113. Opis układu w sekcjach poniżej („nagłówek + pozioma
> nawigacja”) jest historyczny; aktualny układ to `app/layout.tsx` + `app/globals.css`.

Pierwszy **działający** panel operatora. Cztery widoki z danymi, wszystkie na prawdziwych
wierszach z PostgreSQL, wszystkie **tylko do odczytu**.

Poprzednia wersja tego pliku opisywała szkielet (pakiet P6): zero wywołań API, `items={[]}`
wszędzie. Ten pakiet zamienił to na odczyty i **dorzucił jedyny automatyczny dowód, że ekran
naprawdę czyta bazę** — `apps/dashboard/e2e/`.

---

## 0. Co tu jest i jak to uruchomić

| Trasa | Co pokazuje | Skąd bierze dane |
| --- | --- | --- |
| `/login` | hasło jednego operatora | `POST /api/session` (server-side) |
| `/jobs` | kolejka: 10 kolumn | `GET /api/jobs` + jeden `GET /api/jobs/{id}` **na wiersz** |
| `/jobs/[jobId]` | historia prób, generacje lease'ów, orzeczenie o ponowieniu | `GET /api/jobs/{id}` |
| `/audit` | `audit_events` malejąco, **bez filtra** | `GET /api/audit-events` |
| `/`, `/transitions`, `/approvals` | drogowskazy — **nic nie czytają** | — |

```bash
# suita jednostkowa (build + 105 testów + straż „zebrane ≠ wykonane")
bash apps/dashboard/tools/check.sh

# suita e2e: PostgreSQL → control plane → Next.js → HTML
REPO_ROOT=$PWD bash apps/dashboard/tools/e2e_db_render.sh

# ten sam przebieg, ale stack zostaje na kolejne iteracje (wypisze `export`-y)
REPO_ROOT=$PWD WENEXT_E2E_KEEP=1 bash apps/dashboard/tools/e2e_db_render.sh
```

---

## 1. Wersja Node'a: łańcuch narzędzi jedzie z PRZYPIĘTEGO OBRAZU, nie z PATH-u hosta

**To była realna niezgodność, nie formalność.** Zmierzone 2026-08-31:

| Skąd | Node | npm |
| --- | --- | --- |
| host (`node --version`) | **24.12.0** | 11.6.2 |
| `versions.lock.toml [stack.js]` | **24.20.0** | 11.19.0 |
| `node:24.20.0-trixie-slim@sha256:50c3b2…` | 24.20.0 | 11.19.0 |

**Całe budowanie i wszystkie testy JS wykonują się w obrazie przypiętym po digeście.** Wejście:
`tools/node.sh <polecenie>`. Powody (obraz produkcyjny stoi na 24.20.0; `node_modules` jest
platformowe, więc „trochę na hoście" nie istnieje; `check_versions.py` już pilnuje tagu w
Dockerfile'u) nie zmieniły się od pakietu P6.

**Dwie nowe, JAWNIE OPCJONALNE furtki w `tools/node.sh`**, obie wyłączone domyślnie i obie
istnieją tylko dla e2e:

- `WENEXT_NODE_HOST_GATEWAY=1` → `--add-host=host.docker.internal:host-gateway`;
- `WENEXT_NODE_ENV_PASSTHROUGH="A B C"` → przekazuje **wymienione z nazwy** zmienne.

Domyślnie wyłączone, bo suita jednostkowa ma zostać hermetyczna: nie rozmawia z niczym.
Przekazywanie zmiennych po nazwie, a nie hurtem, bo runner, który przepuszcza wszystko,
kiedyś przepuści poświadczenie.

**Zamki na piny (tag ma dwie kontrole, digest jedną):**

| Zamek | Gdzie | Czego pilnuje |
| --- | --- | --- |
| `engine-strict=true` + `engines` | `.npmrc`, `package.json` | wersja Node'a przy `npm ci` |
| `tools/dev/check_versions.py` | cudzy plik | **tylko tag** `node:<wersja>`; o digeście nie wie nic |
| `tests/version_pins.test.ts` | ten pakiet | `engines`, pin `next`, tag **i digest w KAŻDYM `FROM`** |

---

## 2. Komponenty serwerowe i sesja: przeglądarka NIE DOSTAJE tokenu control plane

D-016: sesja operatora żyje w ciasteczku `HttpOnly` i **nigdy** nie trafia do kodu
przeglądarkowego, a control plane **nie jest publikowany na host**.

**Przesłanie `Set-Cookie` z control plane 1:1 spełniłoby literę „HttpOnly" i złamałoby to
zdanie**, bo token operatora wylądowałby w słoiku ciasteczek przeglądarki — w każdym HAR-ze,
w każdej kopii profilu. Dlatego:

```
przeglądarka ── wenext_dashboard_handle=<32 losowe bajty> ──▶ serwer Next.js
serwer Next  ── wenext_operator_session=<token control plane> ──▶ control plane
```

Mapa `uchwyt → token` jest kluczowana **SHA-256 uchwytu**, tak jak
`control_plane.auth.SessionStore`: surowy uchwyt nie jest przechowywany.

### 2.1. Mapa sesji MUSI siedzieć na `globalThis` — to był błąd, który zmierzyliśmy

Pierwsza wersja `lib/session.ts` trzymała mapę w module (`const SESSIONS = new Map()`), na tej
samej zasadzie, na której `control_plane.auth.SESSIONS` jest singletonem modułu — i w Pythonie
to jest poprawne. **W produkcyjnym buildzie Next.js to rozumowanie jest fałszywe:**
`app/login/submit/route.ts` i `app/jobs/page.tsx` trafiają do **osobnych bundli**, każdy z
własnym rejestrem modułów, więc każdy dostał **własną mapę**. Uchwyt wybity przez route handler
był nieznany każdej stronie i panel przekierowywał na logowanie natychmiast po udanym logowaniu.

**Suita jednostkowa tego nie widziała** — `tests/session.test.ts` importuje jedną instancję
modułu i wszystko przechodzi. Wyszło **przy pierwszym uruchomieniu suity e2e** na prawdziwym
`next start`: **10 z 11 przypadków padło z 307 na `/login`**. To jest dokładnie powód, dla
którego ten pakiet ma suitę e2e, i dokładnie powód, dla którego „komponenty renderują się
poprawnie" nie jest dowodem, że produkt działa.

**Czego NIE zmierzono:** nic nie usuwa wygasłych uchwytów w tle. TTL sprawdzany jest przy
odczycie, więc martwy wpis może siedzieć w mapie do końca życia procesu. Nie ma reapera tej mapy
i nie ma testu, który by go obiecywał.

### 2.2. Pozostałe reguły warstwy przeglądarki

1. **Ani jednego `"use client"`.** Egzekwuje `tests/source_scan.test.ts`.
2. **Żadnej zmiennej `NEXT_PUBLIC_*`.** Adres API jedzie jako `WENEXT_CONTROL_PLANE_URL`.
   Skaner wymaga **nazwy po prefiksie** (`NEXT_PUBLIC_[A-Z]…`) — skan po samym prefiksie
   zgłaszał jako naruszenie **akapity, które ten zakaz wyjaśniają**. Skaner z fałszywymi
   alarmami zostaje usunięty, a razem z nim reguła.
3. **Nawigacja to zwykłe `<a>`, nie `next/link`.** Prefetch kazałby przeglądarce dociągać
   ekrany za granicą uwierzytelnienia — każdy z nich przekierowaniem na logowanie.

---

## 3. Trzy stany, nie dwa. I dlaczego `entity-screen.tsx` jest tym plikiem

| `rows` | Ekran | Fakt |
| --- | --- | --- |
| `undefined` | „brak odpowiedzi" + **powód** | nie było odczytu |
| `[]` | „brak danych" | odczyt się udał, nie ma nic |
| `[row, …]` | tabela | jest co pokazać |

`components/entity-screen.tsx` sprawdza `!Array.isArray(rows)` **przed** `rows.length === 0`.
Rozróżnienie **przeniesiono z `entity-list.tsx` do `entity-screen.tsx`** (i skasowano
`entity-list.tsx`), bo pakiet P6 miał straż o warstwę za nisko: mutacja `items ?? []` w
`entity-screen.tsx` przechodziła całą suitę na zielono, ponieważ testy renderowały `EntityList`
bezpośrednio, a **żadna strona tego nie robiła**.

Ekran szczegółów joba ma **czwarty** stan: `data-state="nie ma takiego joba"` dla 404 z control
plane. 404 to **udany** odczyt z negatywną odpowiedzią; wyrenderowanie go jako „brak danych"
twierdziłoby, że job istnieje i nie ma prób.

**Drogowskazy (`/`, `/transitions`, `/approvals`) nie mają ŻADNEGO z tych stanów** i nie mogą
mieć. Ekran, który niczego nie czyta, nie ma prawa powiedzieć „brak danych" — to byłoby
twierdzenie o pomiarze, którego nie było. Pilnuje tego `tests/signposts.test.tsx`.

---

## 4. L2673 w interfejsie: dlaczego BRAK przycisku jest funkcją

Po `SUBMIT_STARTED` skutek uboczny mógł się już wykonać po stronie platformy, więc automatyczne
ponowienie jest zabronione — `CONFIRMED` i `UNKNOWN` są w tym samym zbiorze
(`contracts.enums.STATES_FORBIDDING_AUTOMATIC_RETRY`). Panel z przyciskiem „ponów" pozwoliłby
człowiekowi zrobić ręcznie to, czego maszynie zabrania maszyna stanów: **regułę złamaną przez
interfejs, przy nietkniętej i zielonej maszynie stanów**.

**Nie ma akcji ponowienia dla ŻADNEGO z ośmiu stanów.** Zakres E1.F1 nie wymienia żadnej akcji
mutującej na jobach, a reaper i limit prób mają działać bez człowieka (L5213-5214). Przycisk
obecny dla pięciu stanów i nieobecny dla trzech nauczyłby operatora, że jego brak to warunek
UI do obejścia — a obejściem jest `curl`.

**Werdykt nie jest tu liczony.** `may_retry_automatically` przychodzi z control plane, który
liczy go w jednym miejscu (`contracts.execution.may_retry_automatically` przez
`JobAttemptView`). W `app/`, `components/` i `lib/` **nie ma żadnej listy stanów** — `grep -rn
SUBMIT_STARTED app components lib` znajduje go wyłącznie w prozie. Kopia listy istnieje
**tylko w teście**, żeby test mógł zbudować payload, który control plane by wysłał, i jest
sprawdzana wobec `contracts/src/community_os_contracts/enums.py` przez
`the_state_lists_match_the_python_contracts`.

`null` renderuje się jako **NIEZNANE**, nigdy jako zgoda. Brak dowodu to `NIE_URUCHOMIONO`.

---

## 5. Kolumny lease'u w liście: drugi odczyt na wiersz i trzy uczciwe stany komórki

`GET /api/jobs` **nie zwraca** `lease_owner`, `expires_at` ani `heartbeat_at` — zmierzone w
`api/jobs.py::_job_payload`, który wysyła `attempt_count` i `holds_live_lease` i żadnej kolumny
lease'u. Te dane siedzą w `job_attempts` i przychodzą z trasy szczegółów. Dlatego
`app/jobs/page.tsx` robi **jeden odczyt szczegółów na wypisany job**, po `CONTROL_PLANE_CONCURRENCY`
(8 — tyle, ile `max_size` puli control plane) naraz.

Komórka lease'u ma trzy stany i **żaden nie udaje innego**:

| Odczyt szczegółów wiersza | Trzy komórki lease'u |
| --- | --- |
| udany, są próby | właściciel + generacja, wygaśnięcie, wiek heartbeatu |
| udany, brak prób | „brak prób — job nigdy nie był pobrany", `data-missing="tak"` |
| **nieudany** | „nie odczytano: `<powód>`", `data-missing="tak"` |

„Nie ma lease'u" i „nie wiem, czy jest lease" to różne fakty o kolejce, której reaper odbiera
lease'y martwym workerom. Drugie przebrane za pierwsze to operator wyciągający wniosek o śmierci
workera z pomiaru, którego nie było.

**Cena wypisana wprost:** jedno wejście na `/jobs` to 1 + N żądań do control plane. Dlatego
`JOBS_PAGE_LIMIT` to 100, a nie 200 (domyślna strona API), a ekran drukuje `truncated`.

---

## 6. Straż „zebrane ≠ wykonane" DOSTAŁA WŁASNĄ STRAŻ (zarzut z fali 3, domknięty)

`tools/verify_run.mjs` odrzuca przebieg z `passed == 0`, z jakimkolwiek pominiętym testem i
poniżej progu. **Sam próg nie miał żadnej kontroli**: podniesiono go 10 → 18, a podmiana na `2`
przechodziła całą suitę na zielono, bo jedyną rzeczą, z którą liczba jest porównywana, jest
raport, który ma pilnować.

Domknięte dwoma niezależnymi sposobami w `tests/verify_run_guard.test.ts` — dwoma, bo padają
przy **różnych** edycjach:

1. **liczba**: każdy próg musi być ≥ liczby deklaracji `it(` w odpowiednim katalogu.
   `MINIMUM_TESTS = 2` pada tutaj, przy nietkniętym porównaniu;
2. **porównanie**: skrypt jest **uruchamiany** na syntetycznych raportach i widziany, jak
   odmawia na każdy sposób, na jaki musi (poniżej progu, pominięte, porażki, zero zdanych,
   brak raportu, niepoprawny JSON, nieznana nazwa suity). Usunięcie gałęzi `total < floor` pada
   tutaj, przy nietkniętej stałej.

Progi są dwa, po jednym na suitę (`unit`, `e2e`), i **nieznana nazwa suity to odmowa**, nie
cichy wybór niższego progu.

---

## 7. Suita e2e: jedyny automatyczny dowód, że ekran czyta bazę

```
psql INSERT → PostgreSQL → control plane (uvicorn, prawdziwe HTTP) → serwer Next.js → HTML
```

Wiersze wstawia `tools/e2e_seed.py` **surowym SQL-em**, przez żaden kod tego pakietu ani control
plane (jedyny import to `hash_credential`, bo hashowanie nie jest przedmiotem testu, a CHECK
`credential_hash_is_argon2id` odrzuciłby cokolwiek innego). Fixture nie może dowieść odczytu —
dowodzi tylko, co renderer robi z wierszem, który ktoś wpisał. Precedens jest w tym repo:
`TicketRegistry` w stream gatewayu trzyma rekordy w `dict`, ma zielone testy i traci dane przy
restarcie.

**Kształt sieci — ZMIERZONY, nie założony (2026-08-31):** kontener **osiąga** proces hosta
nasłuchujący **wyłącznie na 127.0.0.1** przez `--add-host=host.docker.internal:host-gateway`.
Sprawdzone `python3 -m http.server 18913 --bind 127.0.0.1` i `wget -O- http://host.docker.internal:18913/`
z kontenera alpine → 200 z listingiem katalogu. Dzięki temu control plane **nie musi** być
wystawiony na `0.0.0.0` i nic nie wychodzi poza pętlę zwrotną (D-014).

**Brak środowiska to PORAŻKA, nie pominięcie.** `beforeAll` rzuca, gdy brakuje którejkolwiek
zmiennej, co oznacza wszystkie testy pliku jako nieudane — reguła z
`services/control_plane/tests/conftest.py`. `verify_run.mjs` niezależnie odrzuca przebieg z
jakimkolwiek pominiętym testem.

**Dwie pułapki asercji, na które nadepnięto i które warto znać:**

1. **Nie asertuj `not.toContain("brak danych")` na całej stronie.** Nagłówek panelu mówi na
   każdym ekranie „Pusty ekran znaczy «brak danych»", więc test padał na stronie **pełnej
   prawdziwych wierszy**. Asercja idzie po markerze `data-state="brak danych"`, który renderuje
   wyłącznie `EmptyState`.
2. **Next.js odpowiada na przekierowany request dokumentu 307 z NIEPUSTYM ciałem** (zmierzone:
   6289 bajtów — powłoka plus flight payload), a payload **odbija segmenty żądanej ścieżki**.
   `/jobs/<id>` bez sesji zwraca więc `<id>` w ciele. To jest wejście samego wołającego oddane
   mu z powrotem, nie ujawnienie: nieuwierzytelniony wołający, który wpisał identyfikator, już
   go miał. Asercja obejmuje więc identyfikatory, **których wołający NIE podał**, plus brak
   `<table>`, `data-state`, `data-retry-verdict` i 64-znakowego hasha.

---

## 8. Czego ten pakiet NIE zawiera

Zakres z dokumentu, nie z pomysłu (L5500-5501):

- **żadnej akcji mutującej job** — ani ponowienia, ani anulowania, ani wymuszenia redispatchu;
- brak kolejki approvali jako produktu z akcjami (E1.F4, L5299);
- brak osadzonej przeglądarki, streamu i ticketów (E1.F2, L5229/L5250) — pilnuje skaner
  sześciu zakazanych literałów w `tests/source_scan.test.ts`;
- brak discovery i silnika oceny (E1.F3);
- **nie dodano serwisu do `docker-compose.yml`** — to cudzy plik. Wpis `dashboard` (profil
  `dashboard`, `127.0.0.1:3000`, `WENEXT_CONTROL_PLANE_URL: http://api:8000`) już tam jest
  i pasuje do tego kodu bez zmian.

---

## 9. Znane ograniczenia i długi

1. **Brak rate limitingu na trasie poświadczeń** (`/login/submit`). Tak samo jak w
   `api/session.py`, gdzie jest to zgłoszone jako luka, nie rozwiązane. Łagodzące, ale **nie
   będące limitem**: port tylko na pętli zwrotnej i koszt Argon2id.
2. **Mapa sesji panelu nie jest sprzątana w tle** i nie przeżywa restartu procesu (§2.1).
3. **`Dockerfile.dockerignore` przesłania korzeniowy `.dockerignore`.** BuildKit czyta plik
   `<dockerfile>.dockerignore` **zamiast** korzeniowego. Zawartość korzeniowego jest tu
   powtórzona i trzeba ją sprawdzić przy każdej jego zmianie. Ten sam brak w korzeniowym pliku
   dotyczy **wszystkich pozostałych obrazów** budowanych z `context: .` — poprawka należy do
   cudzego pliku, zgłoszone.
4. **Miernik dryfu D-010 nie widzi TypeScriptu.** `polish_identifiers` w
   `tools/dev/check_docs.py` iteruje po `ROOT.rglob("*.py")`, więc ten pakiet jest
   **niezmierzony**. Identyfikatory i komentarze są angielskie z ręki, nie z kontroli.
   Rozszerzenie miernika wymaga zmiany w cudzym pliku; zgłoszone, nie wykonane.
5. **Suita jednostkowa nie odpala przeglądarki.** Renderuje przez `react-dom/server` i czyta
   wynik `DOMParser`-em, więc nie dowodzi niczego o hydratacji ani o CSS-ie. Suita e2e odpala
   prawdziwy serwer, ale też nie przeglądarkę: czyta bajty odpowiedzi.
6. **Twierdzenie „control plane nie ma czytnika przejść ani ścieżki zgody" jest ZMIERZONE NA
   COMMICIE 98570ed** i żaden test w tym pakiecie nie może go pilnować — trasa powstała gdzieś
   indziej nie zepsuje asercji tutaj. Kontrola i wyzwalacz: `lib/routes.ts`.
7. **Odczyt jednego joba jest kluczowany po `job_id` z URL-a bez walidacji formatu.** Wartość
   idzie do trasy control plane przez `encodeURIComponent`, a control plane deklaruje parametr
   jako `UUID`, więc śmieci kończą się jego błędem walidacji, nie naszym. Nie ma testu na to,
   co wtedy widzi operator.
