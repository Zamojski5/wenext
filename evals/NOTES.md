# D17 — eval harness + fixture evale. Notatki wykonawcze

Deliverable: **D17 — Eval harness + 30-50 fixture evals (discovery, jakość dyskusji, draft)**
(zakres L5168, `owner_dir = evals/`). Zależności: **D16** (fixture site), **D21** (kontrakty),
**D19** (`criteria.yaml`), **D12** (Codex runtime — jeszcze nie istnieje, patrz niżej).

---

## Próg — najważniejsza rzecz w tym pliku

**`tools/gonogo/criteria.yaml` ma `evale.prog_zdawalnosci: 0.0` i harness nie stawia żadnego
progu procentowego. To jest DECYZJA, nie przeoczenie.**

W F0 nie ma ground truth (L5514). Nikt nie zaetykietował ręcznie ani jednego wątku, nie ma
baseline'u losowego wyboru z tego samego surface, nie ma etykiety z opóźnieniem z okien
1/6/24 h. Próg jakości predykcyjnej klasyfikatora należy do F3 (DoD definicji „wartościowej
dyskusji", L5283) i do bramki F6 (progi z 22.11), a nie do spike'a.

Te evale dowodzą dokładnie dwóch rzeczy:

1. **Harness działa** — offline, deterministycznie, z zapisaną wersją promptu, modelu i efortu,
   i produkuje maszynowo czytelny wynik.
2. **Definicja jakości z sekcji 22.7 jest ZAKODOWANA** w wykonywalnym kodzie, a nie tylko
   opisana w dokumencie: negatyw jest stopem, `UNKNOWN` nie jest zerem, flaga fake engagementu
   jest log-only, porządek klas sygnału obowiązuje, ranking zwraca listę a nie skalar.

Nie dowodzą **niczego** o tym, czy model jest dobry. Raport (`artifacts/evals/evals-stub.json`)
ma pole `"prog_zdawalnosci_zastosowany": null` — jawnie, żeby nikt nie uznał później, że próg
po prostu zapomniano dodać. Osobny test (`test_zero_pass_threshold_in_F0_is_a_decision`)
zapala się, gdyby ktoś ten próg w F0 podniósł.

---

## Co zbudowano

### Port runtime'u — dwie implementacje, jeden interfejs (wymóg A)

`harness/runtime.py` definiuje `ModelRuntime` z trzema metodami: `assess_thread`,
`rank_candidates`, `draft_reply`.

- **`StubRuntime`** — domyślny. Bez sieci, bez konta ChatGPT, bez `random`, bez `datetime.now()`,
  bez `uuid4()` (identyfikatory z `uuid5` na stałej przestrzeni nazw), bez stanu między
  wywołaniami. Nie udaje modelu językowego — jest wykonywalnym zapisem reguł 22.7. Jeżeli eval
  failuje na stubie, błąd jest w harnessie albo w definicji, nigdy „w modelu".
- **`CodexRuntime`** (`harness/codex_runtime.py`) — `@pytest.mark.live`, przez D12. Klasyfikacja
  i discovery idą rolą `WORKER` (Luna), draft rolą `PLANNER` (Terra), każde zadanie w OSOBNYM
  wątku, bez resume między rolami (L1952, L5313). Adapter odrzuca `model/rerouted` jako
  `ROUTE_MISMATCH` zamiast udawać, że wynik pochodzi z zamówionego modelu.

Runner (`harness/runner.py`) nie wie, który runtime dostał — obie ścieżki sprawdzają
DOKŁADNIE te same niezmienniki.

### Wersjonowanie promptów (wymóg B)

Trzy prompty w `prompts/*.md`, każdy z nagłówkiem `name`/`version` i haszem sha256 pełnej
treści pliku. Każdy rekord evala niesie `prompt_name`, `prompt_version`, `prompt_sha256`,
`runtime.model`, `runtime.effort` i `heuristic_policy_version` (L5308 rozciągnięte z draftu
na wszystkie trzy rodziny).

Zmiana promptu jest WIDOCZNA, bo `prompts/PROMPTS.lock` zamraża hasze, a
`test_manifest_matches_the_files` porównuje je z plikami. Edycja promptu bez aktualizacji
manifestu zatrzymuje suitę; aktualizacja manifestu (`python -m evals.harness.prompts`) jest
widoczna w diffie. Ta sama mechanika, co zamrożone progi bramki (L4303, L4619).

### 46 przypadków w trzech rodzinach (wymóg C)

| Rodzina | Przypadków | THREADED | GRAPH |
| --- | --- | --- | --- |
| `quality` — klasyfikacja jakości dyskusji | 20 | 10 | 10 |
| `discovery` — wybór i porządkowanie kandydatów | 14 (6 rankingów + 8 par klas sygnału) | 7 | 7 |
| `draft` — draft odpowiedzi | 12 | 6 | 6 |
| **razem** | **46** | 23 | 23 |

46 mieści się w `30-50` z L5168 i w zamrożonym zakresie z `criteria.yaml` (test to sprawdza).

Przypadki są DANYMI (`harness/cases.py`), nie kodem. Wejściem jest 48 wątków fixture'u D16;
każdy przypadek odwołuje się do wątku po `spec_key` z seeda, a test pilnuje, że każdy taki
klucz istnieje.

Przypadki graniczne wymagane przez DoD, wszystkie obecne i wszystkie zdane:

- **negatyw przy bardzo wysokim engagemencie → `REJECT`** — `q-f-neg-locked-high-engagement`
  (641 głosów + lock), `q-g-neg-locked-high-engagement` (2 771 reakcji + wyłączone komentarze),
  plus `d-f-negatyw-vs-engagement` i `d-g-negatyw-vs-engagement` po stronie rankingu;
- **brak widocznej metryki → `UNKNOWN`, nigdy 0** — `q-f-icp-hidden`, `q-g-icp-hidden`
  (oczekiwane `UNKNOWN` na `icp_share`), `q-f-calib-hidden`, `q-g-calib-hidden`;
- **flaga fake engagementu nie zmienia tieru** — `q-f-window-fake-burst`,
  `q-g-window-fake-burst`, sprawdzane KONTRFAKTYCZNYM BLIŹNIAKIEM: ten sam wątek jest oceniany
  drugi raz z usuniętymi flagami i tier musi wyjść identyczny. To samo w discovery: cały feed
  jest rankowany drugi raz bez flag i porządek musi być ten sam;
- **porządek klas sygnału (L4471-4479) na parach różniących się TYLKO klasą** — 8 przypadków
  `p-*`, w tym jawny REMIS `INTENTIONAL_AMPLIFICATION ≈ SUBSTANTIVE_REPLY` (L4474), traktowany
  jako wynik, a nie jako brak rozstrzygnięcia;
- **odtwarzalność porządku** — każdy przypadek discovery rankuje kandydatów drugi raz
  w odwróconej kolejności wejścia i porównuje PORZĄDEK, nigdy skalara (L5553);
- **draft nie używa claimu `UNVERIFIED` jako faktu** — sprawdzane dwojako: po
  `used_claims` i po obecności unikalnego markera claimu w treści (żeby nie dało się przemycić
  liczby bez identyfikatora). `EXPIRED` i `RETRACTED` są traktowane tak samo jak `UNVERIFIED`;
- **draft respektuje `prohibited_topics`** — dwa przypadki, w których ZAKAZANE SŁOWO JEST
  W TYTULE wątku (`f-icp-high`: „wyceniacie"; `g-icp-high`: „kontraktu", „sektorze publicznym"),
  więc odruchowe zacytowanie tytułu łamie regułę;
- **wątek z `window_state = CLOSED` nie tworzy draftu** — `r-g-window-closed-brak-draftu`.

### Scoring deterministyczny (wymóg D)

`scoring/checks.py`. Wyłącznie asercje na strukturze wyniku: zbiory, pozycje w liście,
typy pól, obecność `UNKNOWN`, wystąpienia podciągów. **Zero LLM-a jako sędziego, zero
podobieństwa semantycznego, zero progu.** Każda kontrola ma nazwę, więc raport z niezdanego
evala mówi CO poszło źle, a nie tylko że poszło.

Porządkowanie kandydatów (`scoring/ranking.py`) SKŁADA dwa symbole z kontraktów D21 —
`SIGNAL_CLASS_ORDER` i `ordering_key` — i nie tworzy trzeciej definicji. Klucz to krotka
`(stop, ranga_klasy, tier, okno, kalibracja)`, nigdy liczba.

### Znormalizowany dystans edycji (wymóg F, `open_decision` nr 6)

`evals/scoring/gate_metrics.py`, z komentarzem-kontraktem **„F4 IMPORTUJE, NIE
REIMPLEMENTUJE"**. Symbole: `MATERIAL_EDIT_THRESHOLD` (0,15 z L4607),
`normalized_edit_distance`, `assess_edit`, `approved_without_material_edit`.

Dokument nie mówi, na czym liczyć dystans, więc wybór trzeba było podjąć i jest zapisany
w docstringu: **poziom tokenów** (nie znaków), normalizacja przez `max(len(a), len(b))`,
tekst normalizowany NFC + casefold + sklejenie białych znaków + obcięcie interpunkcji
z brzegów tokenu. **Zmiana tezy nie jest zgadywana z tekstu** — jest osobnym wejściem
`thesis_changed`, bo L4607 każe zapisać „oba" przy approvalu.

`GOLDEN_VECTORS` — 12 zamrożonych par z wartościami wyliczonymi ręcznie z definicji
(nie skopiowanymi z wyjścia kodu; jedna pozycja w trakcie pisania okazała się błędna
i to złapało literówkę w moim rachunku, nie w kodzie). Osobny test pilnuje symetrii,
zakresu [0, 1] i niezależności trzech warunków L4607.

Funkcja jest realnie używana w evalu draftu: kontrola „draft nie jest kopią treści wątku"
liczy dystans między treścią wątku a draftem i wymaga > 0,15.

### Kontrola negatywna — dowód, że evale nie są puste

`tests/test_negative_control.py`. Osiem sabotowanych runtime'ów, każdy łamiący jedną
regułę, i asercja, że zapala się DOKŁADNIE ta kontrola:

| Sabotaż | Wykrywa |
| --- | --- |
| 0 zamiast `UNKNOWN` w ukrytej metryce | `icp_share = UNKNOWN, nie zero` |
| tier zależny od flagi fake engagementu | `identyczny tier jak bez flagi (kontrfaktyczny bliźniak)` |
| negatyw jako ujemna waga, przebity engagementem | `wątki z negatywem zajmują koniec porządku` |
| porządek zależny od kolejności wejścia | `porządek niezależny od kolejności wejścia` |
| claim `UNVERIFIED` podany jako fakt | dwie kontrole draftu naraz |
| zacytowanie zakazanego tematu z tytułu | `draft respektuje prohibited_topics` |
| draft do wątku z zamkniętym oknem | `odmowa albo draft — zgodnie z oczekiwaniem` |
| przepisanie treści wątku jako draft | `draft nie jest kopią treści wątku` |

Bez tego pliku „121 passed" nie znaczyłoby nic.

---

## Co zweryfikowano URUCHOMIENIEM

Wszystko poniżej ma zapisany prawdziwy wynik z tej sesji, host macOS arm64, CPython 3.12.4.

| Co | Jak | Wynik |
| --- | --- | --- |
| Cała suita offline | `pytest evals -m "not live"` | **121 passed, 46 deselected** w 0,36 s |
| 46 evali na stubie | z tego 46 przypadków rodzin `quality`/`discovery`/`draft` | 46/46 zdanych |
| Brak sieci i konta | uruchomienie bez compose'a, bez Dockera, bez konta ChatGPT | przechodzi |
| Evale `live` bez D12 | `pytest evals -m live -rs` | **46 skipped**, powód: brak `WENEXT_EVALS_MODEL`/`WENEXT_EVALS_EFFORT` |
| Determinizm w procesie | trzy tury: świeża instancja, odwrócona kolejność, ta sama instancja | identyczny JSON rekordów |
| Determinizm między procesami | dwa osobne `pytest`, porównanie sha256 raportu bez sekcji `srodowisko` | `47ffdc4f4f537736…` w obu |
| Raport maszynowy | `artifacts/evals/evals-stub.json` | 46 przypadków, hasze promptów, `prog_zdawalnosci_zastosowany: null` |
| Junit XML (ścieżka z Makefile'a) | `pytest evals -q -m "not live" --junitxml=…` | plik poprawny, `errors="0" failures="0" skipped="0" tests="121"` |
| Kontrola negatywna | 11 testów sabotujących | wszystkie wykrywają swoją regułę |
| Złoty wektor L4607 | 12 pozycji + symetria + zakres | zdane |
| Odczyt `criteria.yaml` | parserem z D19 (`tools/gonogo/report.py`), nie własnym | `prog_zdawalnosci = 0.0`, zakres 30-50 |
| Lint | `ruff check --config pyproject.toml evals` z korzenia | `All checks passed!` |
| `uv lock` po dopisaniu membera | `uv lock` | `Resolved 53 packages`, `Added wenext-evals v0.1.0` |
| Hash treści fixture'u D16 | `dataset.fixture_content_hash()` | `64ae82c814dbaf28200622d20b41a794dcc26846e9c31c6e55ec5fa3b17ed53b` |

Środowisko lokalne: `evals/.venv` (uv, Python 3.12.4) z `pydantic 2.12.5`, `pytest 9.1.1`
i `community-os-contracts` zainstalowanym editable z `../contracts`.

## Czego NIE zweryfikowano uruchomieniem

1. **`make evals` w kontenerze.** Cel Makefile'a uruchamia serwis `tests`, który buduje się
   z `tests/Dockerfile` — **tego pliku jeszcze nie ma** (D18 w toku). Zweryfikowałem samą
   komendę, którą Makefile wykonuje (`pytest evals -q -m "not live" --junitxml=…`), na hoście.
   Warunek, który `tests/Dockerfile` musi spełnić: obraz ma zawierać **cały** korzeń repo —
   `evals/`, `contracts/src/` **oraz** `fixtures/site/src/` — bo dataset importuje generator
   fixture'u po ścieżce. Kontekst builda już to daje (`.dockerignore` nie wyklucza tych katalogów).
2. **`make evals-live` i cokolwiek na prawdziwym Codeksie.** Wymaga D12 i konta ChatGPT.
   `CodexRuntime` jest napisany wobec kontraktu opisanego w docstringu modułu i **nigdy nie był
   wykonany**. Zweryfikowane jest tylko to, co da się bez konta: moduł się importuje, klasa ma
   właściwe atrybuty, leniwy import D12 rzuca `CodexRuntimeUnavailable` z czytelnym powodem,
   a konwersja odpowiedzi odrzuca `null` zamiast zamieniać go na zero.
   **Skip evali `live` NIE jest wynikiem pozytywnym**: GATE-3 bez `evals_live` to `NO_DATA`.
3. **Zgodność z prawdziwym `output schema` Codeksa.** Schematy w `harness/schemas.py` są
   napisane ręcznie (żeby `UNKNOWN` był sumą typów, a nie `null`) i nie były walidowane przez
   `codex app-server generate-json-schema` — to jest wejście z D12 i powinno zostać skonfrontowane,
   gdy D12 będzie gotowy.

---

## Zależności od innych deliverables

| Deliverable | Jak używany | Stan |
| --- | --- | --- |
| **D16** `fixtures/site` | 48 wątków jako wejście evali. Import **po ścieżce** (`fixtures/site/src` na `sys.path`), nie jako zależność pakietowa — pakiet ciągnie fastapi/uvicorn/jinja2, a evalom potrzebny jest wyłącznie czysto-stdlibowy generator. | gotowy, użyty |
| **D21** `contracts` | `ThreadQualityAssessment`, `Observed`, `SIGNAL_CLASS_ORDER`, `ordering_key`, `NEGATIVE_STOP_CLASSES`, `PriorityTier`, `WindowState`, `SignalClass`, `ThreadRole`, `TurnSpec`. Zależność runtime w `pyproject.toml`. | gotowy, użyty |
| **D19** `tools/gonogo` | `criteria.yaml` czytany **parserem z `report.py`**, nie własną kopią. Raport JSON leci do `artifacts/evals/`. | gotowy, użyty |
| **D12** `services/codex_runtime` | `from codex_runtime import CodexRuntimeClient` — import LENIWY, tylko w ścieżce `live`. | **nie istnieje**, kontrakt opisany |
| **D18** `tests/` | `tests/Dockerfile` dla `make evals`. | w toku |
| **F4** (przyszła faza) | ma importować `evals.scoring.gate_metrics`. | kontrakt zapisany |

---

## Rzeczy, które trzeba wiedzieć, zanim się to tknie

### 1. `evals/` jest pakietem Pythona w korzeniu repo, nie pakietem instalowalnym

Spec żąda ścieżki `evals/scoring/gate_metrics.py`, a F4 ma ten symbol importować. Żeby
`import evals.scoring.gate_metrics` działało, `evals/` jest zwykłym pakietem
(`evals/__init__.py`) w korzeniu repo, a `pyproject.toml` ma `[tool.uv] package = false` —
to zbiór testów i danych, nie biblioteka do dystrybucji. Korzeń repo trafia na `sys.path`
przez pytesta (tryb `prepend`) i dodatkowo jawnie przez `conftest.py`.

### 2. `SIGNAL_CLASS_ORDER` ma pułapkę dla konsumenta — i to jest zamierzone w D21

W kontraktach klasy negatywne mają rangę **0**, a docstring mówi „niższa liczba = wyższa
wartość sygnału". Naiwny konsument, który posortuje kandydatów po samej randze klasy,
postawi wątki zablokowane i usunięte NA POCZĄTKU listy. Ratują go dopiero
`NEGATIVE_STOP_CLASSES` i `ordering_key` (który dla negatywu zwraca `(9, 9, 0.0)`).

To nie jest błąd D21 — dokument mówi wprost, że negatyw „działa jak stop, nie jak waga"
(L4472) i że ma „własną, odrębną obsługę". Ale jest to pułapka, więc:
`scoring/ranking.py` trzyma regułę stopu jako PIERWSZY składnik klucza, a eval
`p-f-negatyw-vs-amplifikacja` sprawdza wprost, że pozycja wynika z reguły stopu, mimo że
porównanie rang dałoby wynik odwrotny. **Zastrzeżenie do D21: warto dopisać to zdanie
do docstringa `SIGNAL_CLASS_ORDER`** — nie zrobiłem tego, bo to cudzy katalog.

### 3. Pięć zjawisk negatywnych z L4444 mapuje się na trzy klasy `SignalClass`

Fixture używa etykiet `NEGATIVE_MODERATION`, `LOCK`, `DOWNVOTE_BELOW_ZERO`,
`SPAM_ACCUSATION`, `BOT_ACCUSATION`, `RATIO`. `SignalClass` ma na to trzy klasy negatywne
(`NEGATIVE_MODERATION`, `BAN`, `LOCK`). Mapowanie jest w jednym miejscu
(`dataset.FIXTURE_NEGATIVE_SIGNAL_TO_CLASS`), oryginalna etykieta zostaje w `evidence_refs`
oceny, a nieznana etykieta rzuca `UnknownNegativeSignal` zamiast zniknąć po cichu — ciche
pominięcie zamieniłoby twardy stop w brak sygnału. Osobny test pilnuje kompletności mapowania.

**Zastrzeżenie do D21:** kompresja jest bezpieczna dla F0 (wszystkie te zjawiska i tak dają
`REJECT`), ale w F3, gdzie trzeba będzie odróżnić „usunięcie moderacyjne" od „ratio"
w raportowaniu, trzech klas nie wystarczy.

### 4. Fixture D16 nie ma wątku `CLOSED` bez sygnału negatywnego w archetypie forum

`fixtures/site/NOTES.md` pisze: „`window_state = CLOSED` — po dwa na archetyp, **w tym po
jednym bez negatywu**". Faktycznie:

- `THREADED_COMMUNITY`: `f-neg-locked-high-engagement`, `f-neg-removed`, `f-window-closed` —
  **wszystkie trzy mają sygnał negatywny**;
- `PROFESSIONAL_GRAPH`: `g-neg-removed`, `g-neg-locked-high-engagement`, `g-window-closed` —
  ostatni bez negatywu, zgodnie z opisem.

Czyli czystego przypadku „kontekst wygasł, ale wątek nie jest zły" nie da się na forum
zbudować. Nie zmieniałem fixture'u (cudzy katalog). Zamiast tego eval
`r-f-window-closed-brak-draftu` sprawdza PIERWSZEŃSTWO powodów odmowy: negatyw przed
zamkniętym oknem — kolejność gałęzi jest częścią definicji, więc i tak warto ją mieć
zakodowaną. Czysty przypadek `WINDOW_CLOSED` pokrywa `r-g-window-closed-brak-draftu`.
**Do rozważenia przez D16: dodać `f-window-closed-clean` bez locka.**

### 5. Kalibracja do tła kanału jest daną wejściową, nie liczoną

Zgodnie ze specem D17 i z rejestrem niewykonalnego D04 poz. 4 — w F0 nie ma baseline'u
surface. `reach_calibration_basis` przechodzi z fixture'u do oceny bez przeliczania.

### 6. Zmieniłem jedną linię poza `evals/` — plus jeden efekt uboczny

- `pyproject.toml` w korzeniu: dopisane `"evals"` do `[tool.uv.workspace].members` (jedyny
  dozwolony wyjątek, wskazany w zadaniu).
- **`uv.lock` został zregenerowany** przez `uv lock` — inaczej `uv sync --frozen` zgłosiłby
  rozjazd lockfile'a z workspace'em. Zmiana to dodanie wpisu `wenext-evals v0.1.0`.
  Jeżeli ktoś równolegle dopisze swojego membera, wystarczy ponowne `uv lock`.
- Uruchomienie suity zapisuje `artifacts/evals/evals-stub.json` — katalog jest w `.gitignore`
  i jest w Makefile'u przewidziany jako miejsce na wyniki.

### 7. `rootdir` pytesta przy `pytest evals` to `evals/`

Bo `evals/pyproject.toml` ma własną sekcję `[tool.pytest.ini_options]` — potrzebną, żeby
markery `live`, `discovery`, `quality`, `draft` były zarejestrowane przy uruchomieniu
z Makefile'a. Skutek uboczny: przy `pytest` z korzenia bez argumentów obowiązuje konfiguracja
korzenia, która zna tylko `live`/`capsule`/`db`, więc trzy markery rodzin dadzą ostrzeżenie
(nie błąd — korzeń nie ma `--strict-markers`).

---

## Czego tu celowo nie ma

- **Gotowego frameworka evalowego.** Zgodnie z decyzją ze specu D17: deepeval odpada
  (telemetria domyślnie włączona), promptfoo odpada (Node jako drugi runtime), langsmith
  odpada (SaaS). Wersjonowanie robi git + manifest haszy, scoring to asercje, raport z pytesta.
  Udokumentowana alternatywa, gdyby harness urósł: `inspect-ai` (MIT, lokalny, logi `.eval`).
- **Przypadków `CONVERSATION_FOLLOW_UP`.** W E1 kontynuację rozmowy pisze człowiek
  (L4569-4571), więc model nie ma tu czego generować i nie ma dla tego promptu.
- **Pola `score` gdziekolwiek.** Ani w typach, ani w schematach output, ani w kluczu
  sortowania. Osobny test sprawdza jego nieobecność.
- **Detektora fake engagementu jako wejścia rankingu.** Flagi są przepisywane do oceny
  i tam się kończą (L4492, L5551).

## Zastrzeżenia do dokumentu

Brak. Nic w tym deliverable nie wymagało odejścia od dokumentu. Trzy uwagi wyżej
(punkty 2, 3 i 4) dotyczą sąsiednich deliverables, nie samego dokumentu, i są zapisane
zamiast cichej „poprawki" w cudzym katalogu.

---

## Jak to uruchomić

```bash
# offline, bez sieci i bez konta — to jest ścieżka domyślna
pytest evals -q -m "not live"

# z lokalnym venv-em, jeżeli workspace uv jest jeszcze niekompletny
cd evals
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'pydantic>=2.12' 'pytest>=8.3' -e ../contracts
cd .. && evals/.venv/bin/python -m pytest evals -q -m "not live"

# na prawdziwym Codeksie (wymaga D12 i konta ChatGPT)
WENEXT_EVALS_MODEL=<id-modelu> WENEXT_EVALS_EFFORT=<effort> pytest evals -q -m live

# regeneracja manifestu po świadomej zmianie promptu
python -m evals.harness.prompts

# inny katalog raportu
WENEXT_EVALS_REPORT_DIR=/tmp/moje pytest evals -q -m "not live"
```
