# ADR-0001 — Chrome for Testing + `connectOverCDP` w trybie headless jako domyślny runtime egzekucji

- **Status:** ACCEPTED
- **Data:** 2026-08-30
- **Faza:** E1.F0
- **Wymóg z dokumentu:** pierwszy punkt zakresu E1.F0 (L5158)

## Kontekst

Sekcja 14.1 dokumentu wybiera jeden spójny wariant lifecycle'u kapsuły: `HEADLESS` jako tryb
domyślny obsługujący całą pracę automatu i `INTERACTIVE` uruchamiany na żądanie wyłącznie
dla logowania, MFA, challenge i human takeover. Sekcja 14.1 zaznacza też (L2298), że
`connectOverCDP()` działa tylko z Chromium i ma **niższą zgodność funkcji** niż natywny
protokół Playwrighta — dlatego feature contract jest warunkiem Go/No-Go.

Ten ADR zapisuje decyzję i, co ważniejsze, **wynik jej weryfikacji**.

## Decyzja

Warstwą wykonania jest **przypięty Chrome for Testing uruchamiany w trybie headless
i sterowany przez CDP**, do którego Playwright dołącza się przez `connect_over_cdp()`.
Nie używamy `launchPersistentContext()`. Reszta systemu widzi wyłącznie stabilny kontrakt
Browser Gatewaya, nigdy surowego portu CDP.

## Wynik weryfikacji — bramka Go/No-Go nr 1

Kontrakt uruchomiony 2026-08-30 wewnątrz obrazu `wenext-capsule:f0`, na hoście arm64,
jako użytkownik `pwuser` (non-root), z włączonym sandboxem Chromium, `cap_drop: ALL`
i jedną capability z powrotem (`SYS_CHROOT`).

| Wymóg z L5159 | Wynik |
| --- | --- |
| odnalezienie persistent contextu | ZDANY — 1 kontekst, 1 strona |
| locatory | ZDANY |
| popupy | ZDANY — `expect_page` przechwytuje `window.open` |
| screenshoty | ZDANY — 4035 B |
| potrzebne traces | ZDANY — 1288 B |
| ponowne dołączenie Gatewaya po restarcie | ZDANY |
| odrzucenie niezgodnego builda | ZDANY |
| bezpieczna obsługa stale `DevToolsActivePort` | ZDANY |
| jeden kanoniczny zestaw argumentów | ZDANY — 12 argumentów, zero zakazanych |
| brak publicznego CDP | ZDANY — adres kontenera odrzuca połączenie |

**Werdykt: GO.** Artefakt: `artifacts/gate1/cdp-contract.json`.

## Ograniczenia `connectOverCDP`, które potwierdziliśmy pomiarem

Dokument ostrzega przed niższą zgodnością funkcji. Zmierzone zachowania, które mają
konsekwencje dla reszty systemu:

1. **`browser.close()` NIE zabija procesu Chrome.** Zamyka połączenie CDP. Po `close()`
   przeglądarka nadal odpowiada na `/json/version`.
   *Konsekwencja:* zwolnienie `ProfileLease` nie może opierać się na `close()`. Wymaga
   zatrzymania całej kapsuły i potwierdzenia (L2378, L2431) — co egzekwuje
   `ProfileLeaseStore.release(kapsula_zatrzymana=...)`.

2. **Tracing DZIAŁA przez `connectOverCDP`.** To nie było oczywiste i było ryzykiem
   dla wymogu „potrzebne traces". Zweryfikowane: `context.tracing.start/stop` produkuje
   poprawny plik.

3. **`new_context()` i `page.route()` działają.** Również zweryfikowane, choć w E1 nie są
   na krytycznej ścieżce.

## Rzecz, która myliła i którą trzeba zapamiętać

Flaga **`--dump-dom` wiesza się** na aktualnym Chrome for Testing (to relikt starego
headless). Podczas pierwszych prób diagnostycznych doprowadziło to do błędnego wniosku,
że Chrome nie działa pod emulacją amd64 — w rzeczywistości nie działała tylko ta flaga,
także natywnie.

**Nie używać `--dump-dom` jako smoke testu.** Poprawnym testem żywotności kapsuły jest
zapytanie `/json/version` na porcie z `DevToolsActivePort` — i tak działa healthcheck
w `docker-compose.yml`.

## Granica, której ten ADR nie przekracza

Headless nie renderuje okna, którego dałoby się nie oddać człowiekowi. Pierwszego logowania,
MFA, challenge'u i takeoveru **nie da się** wykonać headless — opisuje to ADR-0003 i nie jest
to problem do obejścia. Odpowiedzią na wykrycie automatu przez platformę jest `MANUAL_ONLY`,
human task albo rezygnacja z surface, nigdy maskowanie automatyzacji (L4382, sekcja 4).

Z tego samego powodu `--no-sandbox` jest w tym projekcie zakazany, a kapsuła działa jako
użytkownik non-root — patrz `services/browser_supervisor/browser_supervisor/args.py`,
lista `FORBIDDEN_FLAGS`, i test `test_forbidden_flag_is_rejected`.
