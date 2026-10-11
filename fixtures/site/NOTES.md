# D16 — fixture site. Notatki wykonawcze

Deliverable: **D16 — Fixture site: dwa archetypy, osiem wymiarów 22.7, formularz logowania**
(zakres L5169, `owner_dir = fixtures/site/`). Zależność: D01 (korzeń repo, compose, piny).

---

## Czym to jest, a czym nie jest

Deterministyczna, lokalna imitacja **struktur** dwóch archetypów kanału. Nie odwzorowuje
żadnej istniejącej platformy: brak marek, brak cudzych znaków towarowych, brak kopii
cudzego HTML-a i CSS-a. „Forum" i „sieć zawodowa" są nazwane generycznie, styl jest własny,
teksty są generowane.

Login wall, MFA i challenge są tu po to, żeby system pod testem miał się na czym
**ZATRZYMAĆ**. Fixture nie zawiera ani jednego mechanizmu ułatwiającego obejście tych
ekranów. Endpoint `/debug/mfa` istnieje wyłącznie dlatego, że suita automatyczna musi
móc przejść ścieżkę bez człowieka — prawdziwa platforma takiego endpointu nie ma i to
jest dokładnie ta różnica, którą F0 ma udokumentować.

---

## Co zbudowano

### Serwer

- FastAPI + Jinja2, `0.0.0.0:8000` (z `WENEXT_FIXTURE_BIND`), `/healthz` → 200 + JSON.
- `WENEXT_FIXTURE_SEED` (domyślnie `20260830`) — **jedyne** wejście generatora treści.
- Czas biegnie od stałej `FIXTURE_EPOCH = 2026-08-30T09:00:00Z`, nigdy od zegara.
  Etykiety „N godz. temu" też są liczone wobec epoki, inaczej HTML zmieniałby się co minutę.
- Uruchomienie: `python -m wenext_fixture_site` albo `wenext-fixture-site`.

### Archetyp 1 — `THREADED_COMMUNITY` pod `/c/`

Cztery społeczności, 24 wątki, feedy `new` i `rising`, zagnieżdżone komentarze
(do 6 poziomów), głosy (również ujemne), znaczniki czasu, wątki zablokowane i usunięte
przez moderację, formularz odpowiedzi (POST).

### Archetyp 2 — `PROFESSIONAL_GRAPH` pod `/g/`

24 posty, feed `new`/`rising`, komentarze **płaskie** (maks. 2 poziomy: komentarz +
odpowiedź autora — to różnica archetypu, nie niedoróbka), reakcje typowane
(LIKE / INSIGHTFUL / SUPPORT / CELEBRATE / DISAGREE), repost z komentarzem,
endpoint reakcji (POST) i endpoint komentarza (POST).

### Ścieżka publiczna — `PUBLIC_API_NO_ACCOUNT` pod `/api/public/`

Czyste JSON API bez sesji nad **tym samym** repozytorium treści:
`/`, `/surfaces`, `/communities`, `/threads` (+ `?archetype=`, `?community=`, `?sort=`,
`?full=1`), `/threads/{id}`, `/quality-dimensions`, `/content-hash`.
Dzięki temu da się porównać ścieżkę tanią z drogą na identycznych danych (L4400, L5528).

Każda strona wątku niesie dodatkowo snapshot JSON w
`<script type="application/json" id="wenext-thread-snapshot">` — dokument każe preferować
parsowanie wewnętrznego API nad DOM (L5034). Test sprawdza, że snapshot z HTML jest
bajt w bajt tym samym dokumentem co odpowiedź `/api/public/threads/{id}`.

### Ścieżki wymagające człowieka

- `/login` — login + hasło (`fixture-user` / `fixture-pass`, zmienne środowiskowe).
- `/mfa` — sześciocyfrowy kod. **Kodu nie ma w HTML ani w nagłówkach odpowiedzi.**
  Trafia do logu serwera (`MFA_CODE username=… code=…`) i pod `/debug/mfa`.
- `/challenge` — ekran w stylu captcha, wyzwalany po `WENEXT_FIXTURE_CHALLENGE_AFTER`
  żądaniach stron za wallem w oknie `WENEXT_FIXTURE_CHALLENGE_WINDOW_SECONDS`
  (domyślnie 120 / 10 s; `0` wyłącza). Zwraca **429**, trzyma sesję do potwierdzenia.
- `/lab/popup` — `window.open` (popup), `window.open(..., '_blank')` (nowa karta),
  `<a target="_blank">`, oraz `?auto=1` otwierające popup po `load`.
- `/lab/delayed` — treść dopisywana przez JS po ok. 2 s (`?delay_ms=` przestawia,
  limit 30 000). W pierwszej odpowiedzi HTML treści docelowej **nie ma** — inaczej
  `wait_for` nie miałby czego testować.

### Endpointy debugowe (jawnie dla testów)

`/debug/mfa`, `/debug/writes`, `/debug/seed`, `POST /debug/reset`.

---

## Osiem wymiarów 22.7 — jak są zakodowane

Katalog wątków żyje w `src/wenext_fixture_site/seed_data.py`. Każdy wpis deklaruje
`illustrates` (które wymiary ma rozróżniać) i `note` (po co jest w zestawie). To jest
jawne wejście do evali D17; wystawione również pod `/debug/seed`
i `/api/public/quality-dimensions`.

Pokrycie (liczba wątków, forum / sieć zawodowa):

| Wymiar (L4438-4445) | THREADED | GRAPH |
| --- | --- | --- |
| `THREAD_DEPTH` — głębokość wątku | 5 | 3 |
| `SUBSTANTIVE_REPLIES` — merytoryczność odpowiedzi | 8 | 7 |
| `AUTHOR_PARTICIPATION` — udział autora | 3 | 3 |
| `ICP_SHARE` — kto odpowiada vs ICP | 3 | 4 |
| `INTENTIONAL_AMPLIFICATION` — amplifikacja z intencją | 4 | 4 |
| `REACH_CALIBRATION` — kalibracja do tła kanału | 4 | 4 |
| `NEGATIVE_SIGNALS` — sygnały negatywne | 3 | 3 |
| `LIFE_WINDOW` — okno życia | 4 | 4 |

Przypadki graniczne wymagane przez DoD, wszystkie obecne w obu archetypach:

- **negatyw przy bardzo wysokim engagemencie** — `f-neg-locked-high-engagement`
  (641 głosów + lock), `g-neg-locked-high-engagement` (2 771 reakcji + lock);
- **brak widocznej metryki** — `f-calib-hidden`, `f-icp-hidden`, `g-calib-hidden`,
  `g-icp-hidden`: `reach_impressions` / `icp_share` = `null`, a nazwa metryki trafia
  do listy `hidden_metrics`. Konsument ma zapisać `UNKNOWN`, nie `0` (L4299, L5552);
- **burst reakcji bez przyrostu odpowiedzi** — `f-window-fake-burst`,
  `g-window-fake-burst`; oba mają `fake_engagement_flags` i **nie mają** sygnału
  negatywnego, bo w E1 detekcja jest log-only i nie odrzuca wątku (L4492, L5551);
- **`window_state = CLOSED`** — po dwa na archetyp, w tym po jednym **bez** negatywu,
  żeby dało się rozróżnić „kontekst wygasł" od „wątek jest zły";
- **amplifikacja z intencją** — `QUOTE`, `CROSSPOST`, `NEWSLETTER_LINK`,
  `REPOST_WITH_COMMENT`, `INBOUND_THREAD_LINK`, każda z własnym komentarzem
  podającego dalej.

Metryki, które da się policzyć, **są liczone** z faktycznie zbudowanego drzewa
(`depth_levels`, `unique_participants`, `median_reply_chars`, `substantive_reply_ratio`,
`replies_first_hour`, `author_reply_count`). Seed deklaruje intencję, generator ją realizuje.
Osobny test pilnuje, że deklarowana i zrealizowana liczba odpowiedzi autora są równe.

**Kalibracja do tła kanału jest DANĄ WEJŚCIOWĄ** (`reach_calibration_basis`), nie wynikiem
obliczenia — w F0 nie ma baseline'u surface. To jest zgodne z rejestrem niewykonalnego
(D04 poz. 4), a nie przeoczenie.

---

## Co zweryfikowano URUCHOMIENIEM

Wszystko poniżej ma zapisany prawdziwy wynik z tej sesji.

| Co | Jak | Wynik |
| --- | --- | --- |
| `/healthz` | `curl` na uvicornie na hoście | `200`, `{"status":"ok","seed":20260830,"threads":48,…}` |
| Login wall | `curl` bez cookie na `/c`, `/c/{slug}`, `/g` | `302 → /login?next=…` we wszystkich |
| Pełne logowanie | `POST /login` → `GET /debug/mfa` → `POST /mfa` → `GET /c/infra-praktycy` | `303`, `303`, `200` |
| Kod MFA nie w HTML | `grep` kodu w pobranym `/mfa` | `0` trafień; kod obecny w logu serwera |
| Zagnieżdżenie | render `f-depth-deep` | 42 komentarze, `max data-depth = 6` |
| Wątek zablokowany / usunięty | `POST …/comments` | `403` w obu |
| Formularz zapisu | `POST …/comments` na otwartym wątku | `303`, wpis w `/debug/writes`, `content_hash` bez zmian |
| Reakcja (graph) | `POST /g/p/{slug}/reactions` | `303` + wpis w rejestrze |
| Challenge | 200 szybkich `GET /c` w jednej sesji | wyzwolony po 120. żądaniu w 0,07 s; `GET /challenge` = `429`; kolejne `/c` = `302`; po `POST /challenge` = `200` |
| Publiczne API bez sesji | `curl` bez cookie na sześć endpointów | `200`, `application/json`, bez `Set-Cookie` sesji |
| Determinizm (proces) | dwie instancje aplikacji, ten sam seed | identyczny `content_hash`, identyczny bajt w bajt HTML wątku i pełny zrzut `/api/public/threads?full=1` |
| Determinizm (środowisko) | host macOS arm64 (CPython 3.12.4) vs kontener Debian trixie | ten sam hash: `7e1468d0db76fc34a0d55fcb4bb47706c737a6d67a7573ad2e2fd3d40d5699a0` |
| Wrażliwość na seed | `WENEXT_FIXTURE_SEED=1234` w kontenerze | inny hash (`56a914c9…`) — seed naprawdę steruje treścią |
| Testy | `pytest tests` | **74 passed** |
| Lint | `ruff check --config pyproject.toml fixtures/site` z korzenia | `All checks passed!` |
| Build obrazu | `DOCKER_CONFIG=infra/docker-config docker build -f fixtures/site/Dockerfile -t wenext-fixture-site:test .` | zbudowany, 277 MB |
| Non-root i architektura | `docker exec … id` / `uname -m` | `uid=1000(fixture)`, `aarch64` (natywny arm64, bez QEMU) |
| `cap_drop: [ALL]` | `docker run --cap-drop ALL --security-opt no-new-privileges:true` | serwis wstaje i odpowiada `200` |
| Compose | `docker compose up -d fixture-site` | `Up (healthy)`, `8000/tcp` **bez publikacji na host** |

Hash `7e1468d0…` odpowiada stanowi kodu z chwili pisania tej notatki. Zmiana treści
wątku albo pola `note` w `seed_data.py` zmieni go celowo — testem determinizmu jest
równość dwóch uruchomień, nie stałość wartości w czasie.

---

## Czego NIE zweryfikowano uruchomieniem

1. **Popupy, nowe karty i `wait_for` w prawdziwej przeglądarce.** W tym katalogu nie ma
   Chrome for Testing ani Playwrighta — kapsuła to D08. Zweryfikowano wyłącznie, że
   strony istnieją, zwracają `200` i zawierają stabilne znaczniki (`data-testid`) oraz
   właściwe wywołania `window.open` / `setTimeout`. Realne otwarcie popupu nad CDP
   zamyka suita `cdp_contract` w `tests/` (D18).
2. **Przekazanie profilu headful → headless.** Fixture dostarcza warunek konieczny:
   trwałe ciasteczko sesji z `Max-Age` (30 dni) i `HttpOnly`, ustawiane dopiero po
   przejściu drugiego składnika. Testem przekazania profilu jest suita `profile_handoff`
   (D18), nie ten deliverable.
3. **Rendering pod obciążeniem / wieloma równoległymi sesjami.** Poza zakresem F0.

---

## Zależności i decyzje, o których trzeba wiedzieć

- **Fixture nie importuje `community-os-contracts`.** Zależność szłaby w złą stronę
  i wciągnęłaby pydantic do obrazu, który ma być mały i szybki w buildzie. Wartości
  archetypów, `access_mode` i `window_state` są literałami, a plik
  `tests/test_contracts_alignment.py` **wczytuje `contracts/src/community_os_contracts/enums.py`
  po ścieżce** i porównuje je z enumami D21. Dryf nazw wyjdzie tam, a nie u konsumenta.
  Test pomija się (`skip`) z zapisanym powodem, jeżeli plik kontraktów nie istnieje.
- **`uv pip install -e fixtures/site` z korzenia w tej chwili NIE DZIAŁA** — nie z powodu
  tego deliverable'u: workspace uv rozwija `services/*`, a `services/profile_lease/`
  jeszcze nie ma `pyproject.toml` (D09 w toku). Komunikat:
  `Workspace member .../services/profile_lease is missing a pyproject.toml`.
  Obejścia użyte tutaj: `PYTHONPATH=src` przy testach lokalnych i zwykły `pip install .`
  wewnątrz obrazu (pip nie zna workspace'u uv). `fixtures/site` jest poprawnie
  zadeklarowany jako member w korzeniowym `pyproject.toml` i zbuduje się, gdy braki
  w `services/*` zostaną uzupełnione. **W korzeniu nie zmieniano niczego.**
- **`uv.lock` nie istnieje w repo** (stan na teraz), więc Dockerfile nie używa
  `uv sync --frozen`. Piny bibliotek (`fastapi==0.141.1`, `jinja2==3.1.6`,
  `uvicorn[standard]==0.52.4`, `python-multipart==0.0.32`) są **dokładne** w
  `fixtures/site/pyproject.toml` — dryf szablonu Jinja albo serializacji JSON
  zmieniłby `content_hash` bez zmiany seeda.
- **Fixture nie czyta `versions.lock.toml`.** Ten plik pinuje przeglądarkę i Codeksa;
  fixture nie używa ani jednego, ani drugiego. Kontekst builda i tak jest korzeniem repo,
  bo tak stanowi kontrakt z `docker-compose.yml`.
- **Zapisy nie mutują treści.** `POST` na formularzu i endpoincie reakcji jest przyjmowany
  (`303`) i rejestrowany w `/debug/writes`, ale nie zmienia repozytorium z seeda. Gdyby
  zmieniał, `content_hash` przestałby być funkcją seeda i determinizm evali by padł.
  Dla D13 („read-only MCP nie ma czym zapisać") istotne jest istnienie realnego POST-u —
  i ono jest spełnione.
- **`challenge_after` domyślnie 120.** Wartość dobrana tak, żeby normalny przegląd
  48 wątków nie wywoływał challenge, a wyraźny burst już tak. D18 może przestawić przez
  `WENEXT_FIXTURE_CHALLENGE_AFTER` (`0` = wyłączone).

## Zastrzeżenia do dokumentu

Brak. Nic w tym deliverable nie wymagało odejścia od dokumentu ani nie budzi
zastrzeżeń, które trzeba by tu zapisać.

---

## Jak to uruchomić

```bash
# lokalnie (dopóki workspace uv jest niekompletny)
cd fixtures/site
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python fastapi jinja2 'uvicorn[standard]' python-multipart httpx pytest
PYTHONPATH=src .venv/bin/python -m pytest tests -q
PYTHONPATH=src WENEXT_FIXTURE_BIND=127.0.0.1:8000 .venv/bin/python -m wenext_fixture_site

# obraz (z korzenia repo)
export DOCKER_CONFIG=$(pwd)/infra/docker-config
docker build -f fixtures/site/Dockerfile -t wenext-fixture-site:test .
docker compose up -d fixture-site
```
