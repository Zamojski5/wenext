# Threat model — faza E1.F0

- **Status:** ACCEPTED
- **Data:** 2026-08-30
- **Faza:** E1.F0 (deliverable D03)
- **Wymóg z dokumentu:** ostatni punkt zakresu E1.F0 — „threat model" (L5171)
- **Źródło prawdy o kontrolach:** [`controls.yaml`](controls.yaml) — 34 kontrole, maszynowo czytelne
- **Test integralności:** [`test_controls.py`](test_controls.py) — każda ścieżka z `enforced_by` musi istnieć na dysku

Ten dokument nie powtarza [ADR-0001](../adr/ADR-0001-headless-cdp-jako-domyslny-runtime.md)
(headless CDP jako domyślny runtime) ani [ADR-0002](../adr/ADR-0002-build-przegladarki-na-arm64.md)
(pin Chrome for Testing 153.0.8010.12 z kanału Beta). Zakłada je i modeluje ich konsekwencje.

Granica produktu z sekcji 4 jest zapisana jako dane w
`contracts/src/community_os_contracts/scope.py` i nie jest tu przepisywana — jest cytowana.

---

## 1. Assets — co chronimy

| Asset | Co to konkretnie jest | Skutek utraty |
| --- | --- | --- |
| **A1 — profil przeglądarki** | `user-data-dir` jednego konta: cookies sesji, storage, dane profilu Chromium | przejęcie zalogowanej tożsamości bez znajomości hasła; brak wykrywalności po stronie platformy |
| **A2 — zalogowana sesja konta** | żywa sesja na platformie, weryfikowana przed każdym odczytem | akcja wykonana „nie tym kontem"; `IDENTITY_MISMATCH` i utrata całego okna pomiarowego |
| **A3 — DEK profilu** | osobny klucz szyfrujący każdego profilu (L3914) | odszyfrowanie wszystkich cold snapshotów danego konta; crypto-erasure przestaje być skuteczne |
| **A4 — dane workspace'u** | Project Definition, Intelligence Profile, plan, Community Memory, evidence | wyciek strategii marketingowej klienta; podmiana wejścia planera |
| **A5 — reputacja konta w community** | historia wkładów, brak ostrzeżeń, brak oskarżeń o bota | twardy stop bramki E1 (L4613: jakikolwiek ban, shadowban lub ≥ 2 publiczne oskarżenia o bota = `BLOCK`); zasób nieodtwarzalny w skali okna 6–8 tygodni |
| **A6 — poświadczenia Codeksa** | `CODEX_HOME` z tokenem konta ChatGPT | cudze wywołania na koncie założycieli; ryzyko zablokowania konta, czyli utrata GATE-3 |
| **A7 — dziennik audytu i evidence** | `audit_events`, snapshoty, artefakty bramek | wynik E1 przestaje być weryfikowalny; `E1ExitReport` bez evidence jest niepodpisywalny (L4623) |

A5 jest jedynym assetem, którego **nie da się odtworzyć z backupu**. Wszystkie pozostałe
mają ścieżkę odzyskania (reauth, ponowny odczyt, ponowne uruchomienie bramki).

---

## 2. Trust boundaries

~~~text
        ┌──────────────────── GR-5: granica produktu (sekcja 4) ────────────────────┐
        │                                                                          │
        │   operator ──GR-4──► Control Plane ──GR-3──► kapsuła ──GR-2──► Chrome     │
        │   (approval,          (lease, hash            (proces,          (profil,  │
        │    InteractionLease,   approvala, audyt)       fencing token)    cookies) │
        │    StreamTicket)             ▲                     │                     │
        │                              │                     │ GR-1                │
        │                     runtime Codeksa ◄──────── strona zewnętrzna           │
        │                     (task-scoped MCP)         (treść NIEZAUFANA)          │
        └──────────────────────────────────────────────────────────────────────────┘
~~~

| Granica | Kto po której stronie | Czego nie wolno przepuścić |
| --- | --- | --- |
| **GR-1** | strona zewnętrzna \| runtime modelu | instrukcji z treści strony traktowanych jako polecenie; dostępu modelu do bazy, sekretów, profili, Docker socket i shella |
| **GR-2** | kapsuła \| proces Chrome i profil | flag maskujących automatyzację; innego builda na tym samym profilu; publicznego portu CDP; cookies przenoszonych między profilami |
| **GR-3** | kapsuła \| Control Plane | drugiej kapsuły na tym samym profilu; starego fencing tokenu; displayu w sesji `HEADLESS` |
| **GR-4** | operator \| Control Plane | akcji zapisującej bez `ApprovalDecision` i `ExecutionIntent`; roli CONTROL wymuszonej po stronie klienta; hasła przechodzącego przez model |
| **GR-5** | produkt \| wszystko, czego nie robimy | dziewięciu praktyk z L243–251 |

Runtime Codeksa leży **poza** Control Plane i **poza** kapsułą. To jest rozstrzygnięcie
architektoniczne, nie szczegół deploymentu: w `docker-compose.yml` serwis `codex` jest
wyłącznie w sieci `edgenet` i nie ma trasy do `capsulenet`, więc nie widzi ani bazy, ani
kapsuły, ani fixture site'u.

---

## 3. Adversaries

| Przeciwnik | Zdolności | Czego chce | Główna kontrola |
| --- | --- | --- | --- |
| **P1 — autor treści na cudzej platformie** | pisze dowolny tekst, który model przeczyta; kontroluje treść wątku, tytuł, komentarze, nazwy użytkowników | zmienić task, wymusić wyjście poza allowlistę, doprowadzić do akcji, na którą nikt nie zezwolił | C-01, C-03, C-04, C-05 |
| **P2 — platforma** | wykrywa automatyzację, wystawia challenge, banuje | wyegzekwować własny regulamin | **żadna** — i to jest decyzja: patrz rozdz. 6 |
| **P3 — skompromitowany albo błądzący runtime modelu** | generuje dowolne wywołania narzędzi w ramach swojego manifestu | wykonać zapis bez zgody, sięgnąć po profil albo sekret | C-02, C-03, C-13, C-21 |
| **P4 — błędny plan** | pełne uprawnienia systemu, działa „zgodnie z zamysłem" | nic; szkodzi przez powtarzalność i skalę | C-23, C-29, C-30, cap kolejki z L4593 |
| **P5 — operator w pośpiechu** | klika `Approve` szybciej, niż czyta | domknąć kolejkę | C-22 (deploy adaptera unieważnia stare zgody), limit 12 kart/dobę (L4593) |
| **P6 — stary proces / druga kapsuła** | trzyma nieaktualny uchwyt do profilu albo portu CDP | zapisać do profilu równolegle z żywą kapsułą | C-12, C-16, C-17 |

P4 jest przeciwnikiem nietypowym i najczęściej lekceważonym: nie łamie żadnej kontroli,
bo działa dokładnie tak, jak został zaprogramowany. Dokument nazywa to wprost —
Conflict Engine ma wykrywać „powtarzalne zachowania powstałe przez błędny plan" (L3970).
W F0 nie ma czym tego wykryć; jedyną obroną jest to, że każda akcja zewnętrzna w E1
przechodzi przez człowieka (L5152) i że dzienny cap kolejki ogranicza skalę szkody do
12 kart na operatora.

---

## 4. Kontrole — reguła → miejsce egzekwowania

Pełna lista z numerami linii dokumentu jest w [`controls.yaml`](controls.yaml).
Poniżej te kontrole, które zakres D03 wymienia imiennie, plus stan ich egzekwowania
na dzień 2026-08-30.

| ID | Reguła | Egzekwowane w | Test | Status |
| --- | --- | --- | --- | --- |
| C-01 | treść stron zewnętrznych jest niezaufanym inputem; runtime modelu nie widzi bazy, sekretów, profili ani Docker socket (L3950, L2215-2222) | `docker-compose.yml` (serwis `codex`: brak wolumenu `profiles`, brak `DATABASE_URL`, brak `capsulenet`) | — | CZESCIOWO |
| C-02 | brak shella i niekontrolowanego exec w runtimie modelu (L2222) | `tools/gonogo/criteria.yaml` (kontrola `brak_shell_i_exec` wymagana w GATE-3) | — | CZESCIOWO |
| C-03 | model nie zatwierdza własnego proposala (L3958) | `contracts/.../execution.py` → `authorize_side_effect` | `contracts/tests/test_invariants.py` | EGZEKWOWANE |
| C-08 | zakaz `--no-sandbox` i flag maskujących automatyzację (L3925, L244) | `services/browser_supervisor/.../args.py` → `FORBIDDEN_FLAGS` | `services/browser_supervisor/tests/test_args_and_profile.py` | EGZEKWOWANE |
| C-11 | zakaz mieszania buildów na jednym profilu (L2308) | `services/browser_supervisor/.../profile.py` → `ProfileBuildMismatch` | `.../tests/test_args_and_profile.py` | EGZEKWOWANE |
| C-12 | bezpieczna obsługa stale `DevToolsActivePort` (L5159) | `services/browser_supervisor/.../devtools_port.py` | `.../tests/test_devtools_port.py` | EGZEKWOWANE |
| C-14 | zakaz kopiowania cookies między profilami (L246) | `db/migrations/0001_f0_kernel.sql`, `.../profile.py`, `.../gate2.py` | `services/profile_lease/tests/test_lease_fencing.py` | EGZEKWOWANE |
| C-16 | stary fencing token jest odrzucany (L2379, DoD L5178) | `services/profile_lease/profile_lease/store.py` | `.../tests/test_lease_fencing.py` | EGZEKWOWANE |
| C-21 | żaden zapisujący side effect nie omija `ExecutionIntent` (L5217) | `contracts/.../execution.py` | `contracts/tests/test_invariants.py` | EGZEKWOWANE |
| C-22 | `adapter_version` w hashu approvala (L2792) | `contracts/.../execution.py` → `ApprovalHashInput` | `contracts/tests/test_invariants.py` | EGZEKWOWANE |
| C-24 | `viewOnly` w noVNC nie jest granicą bezpieczeństwa (L2470) | `db/migrations/0001_f0_kernel.sql` → CHECK `control_wymaga_interaction_lease` | `contracts/tests/test_invariants.py` | CZESCIOWO |
| C-26 | hasło nie trafia do modelu; brak nagrywania podczas LOGIN/MFA (L3897, L3907, L5243) | — | — | **PLANOWANE (F2)** |
| C-28 | brak spoofingu fingerprintu i maskowania automatyzacji (L4382) | `.../args.py`, `contracts/.../capability.py` | `.../test_args_and_profile.py`, `contracts/tests/test_invariants.py` | EGZEKWOWANE |
| C-29 | brak rotacji proxy, obchodzenia rate limitów i równoległych sesji jednego konta (L4403) | `.../args.py`, `services/profile_lease/.../store.py` | `.../test_lease_fencing.py` | CZESCIOWO |
| C-30 | zakaz koordynowanego podbijania między własnymi kontami (L4305, L4513) | — | — | **PLANOWANE (F4)** |

Bilans: **20 kontroli EGZEKWOWANE, 9 CZESCIOWO, 5 PLANOWANE.** Test
`test_controls.py` nie pozwala oznaczyć kontroli jako EGZEKWOWANE bez wskazania testu ani
zostawić pustego `enforced_by` bez statusu `PLANOWANE` i podanej fazy docelowej.

### 4.1. `adapter_version` — konsekwencja, którą łatwo przeoczyć

`adapter_version` jest jednym z dwunastu pól wejścia hasha approvala (L2778–2793).
Wynika z tego reguła operacyjna, a nie tylko własność kryptograficzna:

> **Każdy deploy Action Adaptera unieważnia wszystkie oczekujące, niezużyte zgody operatora.**

Rewizja zachowuje swój `payload_hash`, ale `ApprovalHashInput` nowej rewizji policzony po
deployu będzie inny, więc nowa rewizja nie odziedziczy starej zgody, a preflight zwróci
`AUTHORIZATION_CONTEXT_CHANGED` (L2797). To jest zamierzone — operator zgadzał się na akcję
wykonaną konkretnym kodem, nie na akcję jako taką. Konsekwencja harmonogramowa: **deploy
adaptera w trakcie okna pomiarowego F6 kasuje kolejkę approvali z tego dnia**, a kolejka
ma cap 12 kart na operatora (L4593) i nadmiar nie przechodzi na jutro. Dlatego F6 ma
feature freeze (L5349), a nie tylko „ostrożne wdrożenia".

### 4.2. Co jest jawnie zdyskwalifikowane jako granica bezpieczeństwa

Dokument dwa razy odrzuca mechanizm, który wygląda na kontrolę, a nią nie jest:

1. **`viewOnly` po stronie klienta noVNC** (L2470). Klient jest kodem atakującego, jeżeli
   ktokolwiek go podmieni. Rolę egzekwuje serwer: CONTROL ticket nie istnieje bez atomowego
   HUMAN `InteractionLease` (CHECK `control_wymaga_interaction_lease`), a docelowo gateway
   uwierzytelnia upstream odrębnym, wyłącznie serwerowym credentialem TigerVNC, którego
   przeglądarka nigdy nie widzi. Zwykły transparentny websockify nie może być jedynym
   enforcementem.
2. **`dynamicTools` w Codeksie** (L2500). Eksperymentalny mechanizm zawężania narzędzi w
   trakcie sesji nie jest granicą. Granicą jest konfiguracja wskazująca jeden sidecar,
   `required=true` i exact `enabled_tools`, plus fail-closed przy braku sidecara.

Analogicznie: **routing przeglądarki jest defense-in-depth, nadrzędna jest polityka egress
runnera** (L3946), a **eTLD+1 służy wyłącznie do klasyfikacji, nigdy do autoryzacji** (L3935).

---

## 5. Prompt injection — model zagrożenia GR-1

Treść stron jest niezaufanym inputem (L3950). Zamiast filtrować treść — czego nie da się
zrobić niezawodnie — odbieramy modelowi zdolności, których atak potrzebuje:

| Czego potrzebuje atak przez wstrzyknięcie promptu | Dlaczego jest niedostępne |
| --- | --- |
| odczyt sekretu albo bazy | serwis `codex` nie ma `DATABASE_URL`, `POSTGRES_*` ani sieci `capsulenet` |
| odczyt profilu / cookies | wolumen `profiles` jest montowany wyłącznie do kapsuły (L3895) |
| wykonanie polecenia w systemie | brak shella i unified exec, brak Docker socket (L2222) |
| zmiana taska, assignmentu albo budżetu | model dostaje niezmienny `bound_account_ref` bez ścieżki profilu i bez portu CDP (L2474) |
| zapis na zewnątrz | brak surowych `click`/`fill`/`press`; zapis wyłącznie przez adapter po `authorize_side_effect` |
| samo-zatwierdzenie | `ApprovalDecision` ma `approved_by` i jest osobnym obiektem, którego model nie wystawia |
| rozszerzenie allowlisty | exact `enabled_tools` po stronie konfiguracji, nie promptu |

Trzy z tych siedmiu wierszy są w F0 egzekwowane wyłącznie przez `docker-compose.yml`
i przez zamrożone kryterium GATE-3, bo `services/codex_runtime/` i `services/mcp_sidecar/`
jeszcze nie istnieją (D11, D12). To jest zapisane w `controls.yaml` jako `CZESCIOWO`,
nie jako `EGZEKWOWANE`.

---

## 6. Residual risks — ryzyka przyjęte świadomie

### R1 — niezgodność z regulaminami platform

Automatyzacja przeglądarkowa zalogowanych kont jest niezgodna z regulaminami części platform.
**Ryzyko jest przyjęte przez założycieli i nie jest tematem do ponownego otwierania** —
patrz `STAN-PROJEKTU.md`, sekcja „Ryzyko przyjęte świadomie".

Threat model tego nie analizuje i nie proponuje obejść. Modeluje wyłącznie **zachowanie
systemu, gdy ryzyko się zmaterializuje**: challenge, blokada, MFA, nieznany ekran i
niezgodność tożsamości kończą się kontrolowanym zatrzymaniem (L253, C-31), a capability,
której nie da się wykonać headless, dostaje `MANUAL_ONLY` albo human task, nigdy maskowania
automatu (L4382, C-28).

Z tego wynika, że **P2 (platforma) nie jest przeciwnikiem, przeciw któremu mamy kontrolę.**
Jeżeli platforma wykryje automat, poprawną odpowiedzią jest zatrzymanie, a nie ukrycie się.

### R2 — pin Chrome for Testing z kanału Beta

ADR-0002 przypina 153.0.8010.12 z kanału Beta, bo `linux-arm64` pojawia się dopiero od
153.0.8001.0, a Stable 152.0.7977.64 go nie ma. Ryzyko i reguła wygaśnięcia pinu są
zapisane w [`docs/f0-limits/README.md`](../f0-limits/README.md), pozycja 10.

### R3 — brak RLS i multi-tenant defense-in-depth

Odroczone do E3 (L5206). Akceptowane, bo wszystkie dane E1 pochodzą z jednego workspace'u,
a dostęp do danych jest schowany za workspace-scoped Domain Data Gateway. W schemacie od
pierwszej migracji są kolumny `organization_id` / `workspace_id` i composite FK (L5199),
więc dołożenie RLS w E3 nie zmieni komend domenowych.

### R4 — pięć kontroli bez żadnego egzekwowania

C-05, C-06, C-15, C-26 i C-30 mają `enforced_by: []`. Cztery z nich (C-05, C-06, C-15, C-26)
dotyczą mechanizmów, których w F0 fizycznie nie ma — nie ma Action Adaptera, egress proxy,
Browser Gatewaya ani ścieżki logowania. **C-30 jest wyjątkiem i jedynym realnym długiem:**
zakaz koordynowanego podbijania między własnymi kontami jest zapisany jako dana
(`ForbiddenPractice.COORDINATED_SELF_AMPLIFICATION`), ale nie ma czym go sprawdzić — brak
Conflict Engine, brak tabeli wątków, brak kolejki approvali. Regułę „dwa konta tego samego
workspace'u nie działają w tym samym wątku" (L4513) egzekwuje w tej chwili wyłącznie
człowiek. To jest pozycja 9 rejestru `docs/f0-limits/`.

---

## 7. Warunek wejścia do F2 — checklista

> „Security baseline powstaje przed pierwszym prawdziwym logowaniem, a nie na końcu
> roadmapy." (L3890)

Poniższe pozycje muszą być odhaczone **zanim ktokolwiek zaloguje się prawdziwym kontem
w kapsule**. Nie po pierwszym logowaniu, nie „w trakcie F2".

- [x] proces przeglądarki działa jako non-root z włączonym sandboxem Chromium (C-09)
- [x] `--no-sandbox` i flagi maskujące automatyzację są odrzucane przy budowie argumentów (C-08)
- [x] CDP nasłuchuje na losowym porcie loopbacku i nie jest publiczny (C-10)
- [x] profil pamięta build i platformę; inny build to twardy błąd (C-11)
- [x] stale `DevToolsActivePort` nie prowadzi do dołączenia (C-12)
- [x] najwyżej jeden aktywny `ProfileLease` na profil; stary fencing token odrzucany (C-16)
- [x] niepewne reconciliation przenosi profil do `QUARANTINED` (C-17)
- [x] sesja `HEADLESS` nie może mieć displayu ani stream gatewaya (C-19)
- [x] lista powodów `INTERACTIVE` jest zamknięta i egzekwowana w bazie (C-20)
- [x] CONTROL ticket nie istnieje bez HUMAN `InteractionLease` (C-24, część serwerowa w bazie)
- [x] `StreamTicket` jednorazowy, przechowywany wyłącznie jako hash (C-25)
- [ ] **storage profili jest zaszyfrowany osobnym DEK na profil** (L3894, L3914) — nie ma w F0
- [ ] **trace i screenshot wyłączone podczas loginu i MFA; ekran, klawisze i clipboard nie są nagrywane** (L3897, L3907) — C-26, nie ma w F0
- [ ] **`acceptDownloads=false` domyślnie; clipboard dwukierunkowy, upload/download i druk wyłączone** (L3899, L3908) — C-15, nie ma w F0
- [ ] **RFB-aware Stream Gateway z wyłącznie serwerowym credentialem TigerVNC** (L2470) — D06, w toku
- [ ] **iframe na osobnym originie z CSP i `frame-ancestors` ograniczonym do dashboardu** (L3910) — nie ma w F0
- [ ] **proces usunięcia konta `DRAINING → DELETING → DELETED` z crypto-erasure klucza** (L3902-3903) — nie ma w F0

Pięć niezaznaczonych pozycji to **warunek wejścia do F2, nie zakres F2.** Kolejność jest
odwrotna do intuicyjnej: baseline najpierw, logowanie potem.

---

## 8. Jak to jest sprawdzane maszynowo

```
python3 docs/threat-model/test_controls.py
```

Wynik z 2026-08-30:

```
Kontroli: 34
  CZESCIOWO    9
  EGZEKWOWANE  20
  PLANOWANE    5
Sprawdzonych ścieżek (enforced_by + test): 83

OK — każda ścieżka istnieje, każda kontrola bez egzekwowania jest jawnie PLANOWANE.
```

Test przechodzi także pod pytest (`contracts/.venv/bin/python -m pytest docs/threat-model/`)
i nie wymaga PyYAML — ma własny parser podzbioru YAML, tak samo jak `tools/gonogo/report.py`.

Reguły, których test pilnuje:

1. każda ścieżka z `enforced_by` i z `test` istnieje na dysku,
2. pusty `enforced_by` wymaga `status: PLANOWANE` **i** podanej `faza`,
3. `status: EGZEKWOWANE` wymaga wskazania testu, nie samego pliku źródłowego,
4. `zrodlo_w_dokumencie` wskazuje istniejącą linię `ROADMAP_CODEX_CDP_COMMUNITY_OS.md`,
5. identyfikatory kontroli są unikalne, a każda `granica` jest zdefiniowana.

Reguła 1 jest tu najważniejsza: **kontrola wskazująca nieistniejący plik jest gorsza niż
kontrola jawnie oznaczona jako PLANOWANE**, bo tworzy fałszywe poczucie pokrycia.
