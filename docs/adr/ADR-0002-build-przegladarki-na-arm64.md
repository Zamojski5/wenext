# ADR-0002 — Który build przeglądarki na arm64

- **Status:** ACCEPTED
- **Data:** 2026-08-30
- **Faza:** E1.F0
- **Zastępuje:** —
- **Wymóg z dokumentu:** „ustalono exact browser i Codex versions" (L5180),
  „ten sam exact executable we wszystkich trybach danego profilu" (L2307),
  „zakaz mieszania Chrome, Chrome for Testing, Chromium i headless shell na jednym profilu" (L2308)

## Kontekst

ADR-0001 ustala Chrome for Testing sterowany przez CDP jako domyślny runtime egzekucji.
Środowisko deweloperskie założycieli to macOS na Apple Silicon (arm64), a kapsuła jest
kontenerem linuksowym. Pojawia się pytanie, którego dokument nie rozstrzyga: **jaki build
Chrome for Testing uruchomić na linuksie arm64.**

Stan faktyczny sprawdzony 2026-08-30 w `known-good-versions-with-downloads.json`:

| Kanał | Wersja | Czy ma `linux-arm64` |
| --- | --- | --- |
| Stable | 152.0.7977.64 | **nie** |
| Beta | 153.0.8010.12 | tak |
| Dev | 154.0.8025.0 | tak |
| Canary | 154.0.8031.0 | tak |

`linux-arm64` pojawia się dopiero od **153.0.8001.0** — w całym rejestrze jest 28 takich wersji.
Kanał Stable nadal go nie ma.

## Rozważane opcje

**A. Chrome for Testing linux64 pod emulacją `linux/amd64`.**
Sprawdzone empirycznie. Emulacja jako taka działa — `docker run --platform linux/amd64`
poprawnie zwraca `x86_64`. Odrzucone z innego powodu niż pierwotnie sądziłem: pierwsze
pomiary sugerowały, że Chrome pod emulacją wisi, ale przyczyną okazała się flaga `--dump-dom`
(relikt starego headless), która wiesza się także natywnie. Emulacja pozostaje jednak
odrzucona, bo jest wolniejsza, wprowadza drugą architekturę do łańcucha dostaw i nie daje
niczego, czego nie daje opcja C.

**B. Chromium z repozytorium dystrybucji albo z Playwrighta (natywny arm64).**
Playwright dostarcza Chromium 151.0.7922.34 dla aarch64 w obrazie bazowym. Odrzucone:
to jest **Chromium**, nie **Chrome for Testing**. Dokument zakazuje mieszania tych buildów
na jednym profilu (L2308), a ADR-0001 wybiera Chrome for Testing. Użycie Chromium w dev
i Chrome for Testing w CI oznaczałoby, że capability probe wykonany w jednym środowisku
nie mówi nic o drugim — a probe jest ważny wyłącznie dla builda, na którym powstał (L4381).

**C. Chrome for Testing 153.0.8010.12 (kanał Beta), natywnie na obu architekturach.**
Ta jedna wersja ma **jednocześnie** `linux-arm64` i `linux64`.

## Decyzja

**Przypinamy Chrome for Testing 153.0.8010.12 i budujemy natywnie na obu architekturach.**

Powody:

1. To jedyna opcja, w której dev na Apple Silicon i CI na amd64 używają **tej samej wersji**
   Chrome for Testing. Wymóg „ten sam exact executable" (L2307) jest wtedy spełniony nie tylko
   w obrębie jednego profilu, ale i między środowiskami.
2. Nie wprowadza drugiego rodzaju przeglądarki do systemu, więc zakaz z L2308 zostaje
   nienaruszony.
3. Nie wymaga emulacji, więc nie wprowadza różnicy wydajnościowej między dev a CI.

Cena, którą świadomie płacimy: **przypinamy build z kanału Beta, nie Stable.**
To jest odstępstwo od domyślnej ostrożności i wymaga jawnej reguły odświeżania (niżej).

## Konsekwencje dla kodu

| Konsekwencja | Gdzie egzekwowana |
| --- | --- |
| Wersja i sumy kontrolne istnieją w jednym miejscu | `versions.lock.toml`, sekcja `[browser.chrome_for_testing]` |
| Pobranie weryfikuje rozmiar i `md5Hash` z GCS | `infra/browser/fetch_chrome.py` |
| Wybór platformy po architekturze maszyny | `infra/browser/fetch_chrome.py`, mapa `ARCH_NA_PLATFORME` |
| Profil pamięta, którym buildem był otwierany | `services/browser_supervisor/browser_supervisor/profile.py` |
| Próba otwarcia profilu innym buildem lub inną platformą kończy się błędem | `ProfileBuildMismatch`, testy `test_args_and_profile.py` |
| Kapsuła odmawia pracy, gdy `/json/version` nie zgadza się z pinem | `devtools_port.zweryfikuj`, kontrola `zgodnosc_builda` w kontrakcie CDP |
| Żaden Dockerfile nie przepisuje wersji na własną rękę | `tools/dev/check_versions.py` |

## Reguła odświeżania pinu

M153 wchodzi na kanał Stable. **Gdy to nastąpi, pin należy przenieść na wersję Stable
z linii 153 albo wyższej**, a nie zostawiać na Becie „bo działa".

Podniesienie pinu wymaga:

1. nowej wersji w `versions.lock.toml` wraz z pobranymi na nowo metadanymi z GCS,
2. ponownego uruchomienia `make gonogo` — capability snapshot poprzedniego builda traci ważność,
3. decyzji, co zrobić z istniejącymi profilami: domyślnym recovery jest **reauth na nowym
   profilu**, nie przenoszenie cookies (L2312). Downgrade profilu otwartego nowszą wersją
   jest zakazany (L2311).

Do czasu przejścia na Stable ryzyko jest zapisane w `docs/f0-limits/`.

## Weryfikacja

Uruchomione 2026-08-30 w kontenerze `wenext-capsule:f0` na hoście arm64, jako `pwuser`,
z sandboxem Chromium i `cap_drop: ALL` + `cap_add: SYS_CHROOT`:

```
zgodnosc_builda: /json/version → 'Chrome/153.0.8010.12', oczekiwano 'Chrome/153.0.8010.12'
```

Pełny wynik: `artifacts/gate1/cdp-contract.json`.
