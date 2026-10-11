# NOTES — obrazy runnera testów i raportu Go/No-Go

Zakres tej notatki: `tests/Dockerfile` i `tools/gonogo/Dockerfile`. Nic poza tymi dwoma
plikami nie zostało w repo zmienione.

## Co zostało uruchomione i jaki był PRAWDZIWY wynik

Wszystko poniżej odpalone z korzenia repo, `export DOCKER_CONFIG=$PWD/infra/docker-config`,
macOS arm64, Docker 29.1.2, 2026-08-30.

| # | Polecenie | Wynik |
| --- | --- | --- |
| 1 | `docker build -f tests/Dockerfile -t wenext-tests:test .` | OK, obraz 318 MB. Kontrola w buildzie wypisała `tests image OK: 48 watkow fixture` |
| 2 | `docker run --rm wenext-tests:test pytest tests -q` | **10 passed** |
| 3 | `docker run --rm wenext-tests:test pytest evals -q -m "not live"` | **121 passed**, raport zapisany do `/var/wenext/artifacts/evals/evals-stub.json` |
| 4 | `docker build -f tools/gonogo/Dockerfile -t wenext-gonogo:test .` | OK, obraz 203 MB. Kontrola w buildzie: `gonogo image OK: {'GATE-1': 'NIE_URUCHOMIONO', 'GATE-2': 'NIE_URUCHOMIONO', 'GATE-3': 'NIE_URUCHOMIONO'}` |
| 5 | `docker run --rm -v "$PWD/artifacts:/var/wenext/artifacts" wenext-gonogo:test` | `GATE-1 GO`, `GATE-2 GO`, `GATE-3 NIE_URUCHOMIONO` — zgodnie z oczekiwaniem |
| 6 | `docker run --rm wenext-tests:test` (domyślny CMD) | **131 passed** (`pytest tests evals -q -m "not live"`) + 4 ostrzeżenia o markerach, patrz niżej |
| 7 | `docker run --rm wenext-tests:test pytest evals -q -m live` | **46 skipped** — brak `WENEXT_EVALS_MODEL`/`WENEXT_EVALS_EFFORT`, czyli ścieżka `evals-live` bez konta ChatGPT zachowuje się tak, jak ma: pomija, nie udaje wyniku |
| 8 | `make evals` | Przeszło. Zbudowało obraz, uruchomiło `pytest evals -q -m "not live"`, zapisało `artifacts/evals/evals-stub.xml` i `artifacts/evals/evals-stub.json` |
| 9 | `make gonogo` | Przeszło. `GATE-1 GO`, `GATE-2 GO`, `GATE-3 NIE_URUCHOMIONO`, raport w `artifacts/gonogo/` |
| 10 | `docker compose -f docker-compose.yml -f compose.test.yml --profile test run --rm --no-deps tests pytest tests -q` | **10 passed** — serwis `tests` startuje w `capsulenet` z wolumenem artefaktów i zmiennymi z compose |

### Czego NIE uruchomiłem

**`make test` — nie uruchomione.** Ten cel robi
`docker compose -f docker-compose.yml -f compose.test.yml --profile test up -d --wait db capsule gateway fixture-site`,
a `compose.test.yml` przestawia bazę na tmpfs (`volumes: !reset []`). W momencie mojej pracy
stack `make up` był podniesiony przez innego agenta (`wenext-db-1`, `wenext-capsule-1`,
`wenext-fixture-site-1` działały). Uruchomienie `make test` zrekreowałoby bazę na tmpfs
i skasowało cudzą pracę w toku. Zamiast tego zweryfikowałem sam serwis `tests` przez
`run --rm --no-deps` (wiersz 10) — to pokrywa wszystko, za co odpowiada mój Dockerfile.
To, czy `up --wait` dochodzi do `healthy`, jest odpowiedzialnością D11/D14/D16, nie tego obrazu.

**`make evals-live` — nie uruchomione w całości.** Wymaga profilu `codex` i zalogowanego
konta ChatGPT (`make codex-gonogo`), czyli człowieka. Zweryfikowałem samą selekcję
markerem: `-m live` wybiera 46 przypadków i wszystkie kończą się `skip` z powodem
„brak WENEXT_EVALS_MODEL / WENEXT_EVALS_EFFORT" (wiersz 7). Pominięcie NIE jest wynikiem
pozytywnym i `criteria.yaml` traktuje je jak brak dowodu — tak ma być.

## Decyzje w `tests/Dockerfile`

- **Co jest kopiowane.** `test_repo_consistency.py` liczy `KORZEN = parents[1]`, więc leży
  w `/app/tests/` i traktuje `/app` jak korzeń repo. Czyta realne pliki, nie fixture'y:
  `versions.lock.toml`, `docker-compose.yml`, `compose.test.yml`, `Makefile`,
  `infra/capsule/Dockerfile`, `docs/adr/`, `tools/dev/check_versions.py`, `tools/gonogo/`
  oraz `pyproject.toml` każdego membera workspace'u. `check_versions.py` dokłada do tego
  `infra/browser/seccomp_profile.json` (liczy z niego sha256). Wszystko to jest w obrazie
  z zachowaną strukturą katalogów. `db/` jest kopiowane pod suity bazodanowe D18.
- **`fixtures/site` kopiowane w dwóch kawałkach** (`pyproject.toml` + `src`), nie w całości.
  `pyproject.toml` jest wymagany przez `test_every_workspace_member_has_a_pyproject`
  (katalog membera bez tego pliku wywala `uv lock` całemu workspace'owi), a `src` jest
  wstawiany na `sys.path` przez `evals/harness/dataset.py`.
- **Pakiet `wenext-fixture-site` jest instalowany**, mimo że evale importują go po ścieżce.
  Powód: piny `fastapi`, `jinja2`, `python-multipart`, `uvicorn[standard]` mają jedno
  źródło prawdy — `fixtures/site/pyproject.toml`. Przepisanie ich do Dockerfile'a byłoby
  drugim miejscem, w którym te wersje mogą się rozjechać.
- **Playwrighta ani przeglądarki nie ma i być nie może.** Suity dotykające CDP idą przez
  kapsułę (`docker compose exec capsule`). Druga kopia przeglądarki w innym buildzie
  unieważniałaby capability snapshot (L2580).
- **Wersje runnera przypięte dokładnie** (`pytest==9.1.1`, `pytest-asyncio==1.4.0`,
  `asyncpg==0.31.0`, `httpx==0.28.1`) — te same, które rozwiązał `uv.lock` w korzeniu.
  Obraz i host mają uruchamiać ten sam pytest, inaczej „u mnie przechodzi" nic nie znaczy.
- **Kontrakt obrazu w warstwie `RUN`**: import `community_os_contracts`, `wenext_fixture_site`
  i zbudowanie datasetu evali. Jeżeli to nie przejdzie, build pada, a nie `docker compose run`.
- **Non-root, uid 1000**, jak reszta obrazów repo. `/app` i `/var/wenext` należą do 1000,
  bo pytest pisze `.pytest_cache` w rootdirze, a evale raport do zamontowanych artefaktów.
- **`WENEXT_EVALS_REPORT_DIR=/var/wenext/artifacts/evals`** ustawione w ENV. Bez tego
  `evals/harness/report.py` pisałby do `/app/artifacts/evals`, czyli do wnętrza kontenera,
  i raport evali nigdy nie trafiłby na hosta ani do `tools/gonogo`.

## Zastrzeżenie: ostrzeżenia o markerach przy domyślnym CMD

`pytest tests evals -q -m "not live"` ustawia rootdir na `/app`, więc konfiguracją jest
`pyproject.toml` korzenia — a markery `discovery`, `quality`, `draft` są zadeklarowane
w `evals/pyproject.toml`. Efekt: cztery `PytestUnknownMarkWarning`. Testy przechodzą,
`-m "not live"` działa (marker `live` JEST w korzeniu), więc to kosmetyka.

Naprawa nie należy do mnie: trzeba dopisać te trzy markery do `[tool.pytest.ini_options]`
w korzeniowym `pyproject.toml` (właściciel D01/D02) albo przenieść je tam z `evals/`.
Nie robię tego po cichu w cudzym pliku. `make evals` i `make evals-live` uruchamiają
`pytest evals` osobno, więc tam rootdirem jest `/app/evals` i ostrzeżeń nie ma.

## Decyzje w `tools/gonogo/Dockerfile`

- **Jednostopniowy i bez `pip install` czegokolwiek.** `report.py` nie ma zależności
  z premedytacją (własny minimalny parser YAML), żeby raport dał się wygenerować także
  tam, gdzie nie ma pełnego środowiska. Dołożenie tu biblioteki cofałoby tę decyzję.
- **`report.py` nie czyta zmiennych środowiskowych** — i nie zmieniałem go, żeby czytał.
  `WENEXT_ARTIFACTS_DIR`, `WENEXT_CRITERIA` są rozwijane w `CMD` przez `sh -c` i podawane
  jako `--artifacts` / `--criteria` / `--out`. Forma exec bez powłoki przekazałaby
  dosłowne `$WENEXT_…`.
- **`--out` wskazuje na `$WENEXT_ARTIFACTS_DIR/gonogo`**, a nie na domyślne
  `ROOT/artifacts/gonogo`. Domyślna ścieżka to `/app/artifacts/gonogo` — wnętrze
  kontenera, którego nikt nie zobaczy.
- **`WENEXT_VERSIONS_LOCK` jest przekazywane, ale `report.py` go dziś nie używa.**
  Zmienna jest w kontrakcie compose'a, więc obraz ją ma i `versions.lock.toml` leży pod
  wskazaną ścieżką (compose montuje go dodatkowo `:ro`, nadpisując skopiowaną kopię).
  Gdyby D19 zaczął pinować wersje w raporcie, nic nie trzeba zmieniać w obrazie.
- **Kontrakt obrazu w warstwie `RUN`**: raport na nieistniejącym katalogu artefaktów musi
  dać trzy bramki i wyłącznie `NIE_URUCHOMIONO`. To ta sama własność, której pilnuje
  `test_report_does_not_treat_missing_evidence_as_a_pass` — sprawdzana już przy buildzie, bo obraz,
  który potrafi ogłosić GO bez dowodu, jest gorszy niż brak obrazu.
- **Non-root, uid 1000**, zgodnie z `cap_drop: [ALL]` i `no-new-privileges` w compose.

## Efekt uboczny mojej sesji

`uv run --frozen pytest tests` (przebieg porównawczy na hoście) utworzył `.venv/`
w korzeniu repo. Katalog jest w `.gitignore` i w `.dockerignore`, więc nie wpływa
ani na commit, ani na build. Nie kasuję go, bo równolegle pracują inni agenci.
