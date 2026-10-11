# D13 — task-scoped read-only MCP sidecar

Zakres: L5170, DoD L5179, sekcja 14.6 dokumentu (L2472-2521).
Katalog jest jedynym miejscem, w którym pisałem kod tego deliverable'u.

## Co jest zrobione

| Plik | Co robi |
|---|---|
| `mcp_sidecar/capability.py` | `TaskScopedCapability` z dokładnie polami z L2478-2490; wejście WYŁĄCZNIE plikiem 0600 wskazanym przez `WENEXT_MCP_CAPABILITY_FILE` |
| `mcp_sidecar/origins.py` | exact-origin allowlist z kanonicalizacją; eTLD+1 nie autoryzuje niczego |
| `mcp_sidecar/browser.py` | wąski read-only backend przeglądarki; `BrakBackendu` i `BackendHttpReadOnly` |
| `mcp_sidecar/tools.py` | dokładnie sześć narzędzi z L2504-2511 + dispatch z kontrolą capability |
| `mcp_sidecar/server.py` | serwer MCP po stdio (`initialize`, `tools/list`, `tools/call`, `ping`) |
| `mcp_sidecar/lifecycle.py` | per-turn `SidecarProcess`, `sidecar_na_turn`, generator wpisu `config.toml` |
| `Dockerfile` | `python:3.12-slim-trixie`, non-root uid 1000, kontekst builda = korzeń repo |

## Co zweryfikowałem URUCHOMIENIEM

`63 passed` w `services/mcp_sidecar/tests/` (Python 3.12, macOS arm64). Konkretnie:

- **Manifest ma dokładnie sześć narzędzi.** Siódmego nie da się dodać — sprawdzone przez
  rozszerzenie `allowed_tools` o `click`/`fill`: `dostepne()` nadal zwraca sześć nazw,
  a `wywolaj("fill", ...)` kończy się `NarzedzieNieistnieje`.
- **Sidecar bez capability kończy się kodem wyjścia 2**, zanim odpowie na `initialize`
  (uruchomiony jako osobny proces, `subprocess.run`). To samo dla wygasłego capability.
- **Test na PID.** Proces sidecara żyje po `start()` (`os.kill(pid, 0)` przechodzi) i NIE
  żyje po `stop()`. To samo, gdy turn rzucił wyjątkiem, i to samo przy mismatchu bindingu.
- **Plik capability znika po turnie** — sprawdzone na ścieżce, nie na deklaracji.
- **Read-only na fixture z formularzem.** Lokalny serwer HTTP liczy `POST`-y. Po przejściu
  wszystkich sześciu narzędzi licznik wynosi zero, a próby wywołania
  `click`/`fill`/`press`/`submit`/`evaluate` kończą się `NarzedzieNieistnieje`.
- **eTLD+1 nie autoryzuje.** `https://zly.example.com` i `https://zly.fixture-site.example.com`
  są odrzucane przy allowliście `https://fixture-site.example.com`.
- **Handshake MCP po stdio z prawdziwym procesem** — `initialize` i `tools/list` przez
  potoki, nie przez wywołanie funkcji w tym samym procesie.
- **Obraz Dockera** buduje się i przechodzi wbudowaną asercję manifestu w `RUN`.

## Czego NIE zweryfikowałem

- **Rzeczywistej rozmowy Codexa z tym sidecarem.** Wymaga zalogowanego konta ChatGPT
  (turn), więc jest poza F0 bez D20. Zweryfikowana jest natomiast strona kontraktu:
  `SidecarProcess` spełnia protokół `codex_runtime.Sidecar`, a wygenerowany wpis
  `config.toml` ma `required = true` i dokładne `enabled_tools` — test integracyjny leży
  w `services/codex_runtime/tests/test_sidecar_integration.py`.
- **`screenshot` i pełnego `snapshot` na prawdziwej stronie.** `BackendHttpReadOnly` nie
  renderuje — `screenshot` kończy się jawnym błędem, a nie pustym PNG. Prawdziwy backend
  to BrowserSupervisor (D08) i podpięcie należy do niego.
- **Zachowania `required = true` w sesji app-server.** Dokumentacja opisuje twardy fail
  tylko dla `codex exec` („exits with an error if this MCP server fails to initialize").
  Nie zakładam identycznej semantyki w app-serverze — dlatego fail-closed jest ZDUBLOWANY:
  sidecar sam kończy się kodem 2, a `CodexExecutionAdapter.przygotuj_turn` nie startuje
  turnu bez żywego sidecara ze zgodnym odciskiem bindingu.

## Zależności od innych deliverables

| Od kogo | Co | Stan |
|---|---|---|
| D21 `contracts/` | `InteractiveReason`, `E1_ALLOWED_INTERACTIVE_REASONS` | istnieje, używane |
| D08 `services/browser_supervisor/` | prawdziwy backend przeglądarki | **nie podpięty** — interfejs `BackendPrzegladarki` czeka |
| D14 `services/scope_policy/` | granica zakresu z sekcji 4 | nie istniał w chwili pisania; allowlist originów jest tu lokalna |
| D16 `fixtures/site/` | fixture site | testy używają własnego, minimalnego serwera, żeby nie zależeć od D16 |

## Decyzje i zastrzeżenia

1. **Brak biblioteki MCP.** Implementuję podzbiór protokołu na bibliotece standardowej.
   Powód: powierzchnia sidecara jest granicą bezpieczeństwa, a `pip install mcp`
   wprowadziłby kilkadziesiąt tysięcy linii cudzego kodu po tej samej stronie granicy.
   Koszt: obsługujemy `initialize`, `notifications/initialized`, `tools/list`, `tools/call`
   i `ping` — nic więcej. Jeżeli Codex 0.147.0 okaże się wymagać czegoś ponadto, będzie to
   widoczne natychmiast (błąd inicjalizacji serwera MCP), a nie po cichu.

2. **`allowed_tools` może tylko ZAWĘŻAĆ manifest.** `RejestrNarzedzi.dostepne()` liczy
   przecięcie, nie sumę. Gdyby Control Plane przysłał capability z `click`, nic się nie
   stanie — narzędzia nie ma w kodzie.

3. **`disabled_tools` nie jest używane.** Deny-lista jest w Codeksie stosowana PO
   allow-liście, więc wpisanie narzędzia w oba miejsca kończy się jego usunięciem. Mając
   jawną allow-listę sześciu nazw, deny-lista może tylko wprowadzić w błąd co do tego,
   które miejsce jest źródłem prawdy.

4. **Skanujemy CAŁY payload capability, nie listę znanych kluczy.** Problemem nie jest
   nazwa pola, tylko wartość niosąca ścieżkę profilu albo port CDP — niezależnie od tego,
   jak ktoś to pole nazwie. Stąd `ZAKAZANE_WZORCE` i `naruszenia_zakazanych_pol`.

5. **`extract(schema)` nie wnioskuje za model.** Zwraca materiał źródłowy i schemat;
   wypełnia model. Zgadywanie wartości w sidecarze byłoby drugim, niewidocznym
   ekstraktorem — eval mierzyłby wtedy jakość heurystyki, nie jakość modelu.

6. **`BrakBackendu` rzuca zamiast zwracać pustkę.** Pusty snapshot i brak przeglądarki
   muszą być rozróżnialne, inaczej eval mierzy ciszę zamiast treści.

7. **Zastrzeżenie do dokumentu (nie „poprawka po cichu").** L2495 mówi, że capability nie
   trafia do promptu. To nie wystarcza: capability nie może też trafić do ARGUMENTÓW
   narzędzia, bo argumenty pochodzą od modelu. Dlatego jedynym wejściem jest plik od
   Control Plane, a `allowed_origins` podane w argumentach `open_allowed_url` jest
   ignorowane (jest na to test). Uważam to za wykonanie intencji L2495, nie odejście od niej.

## Jak uruchomić

```bash
cd services/mcp_sidecar
python3 -m venv .venv && ./.venv/bin/pip install "pytest>=8.3" -e ../../contracts
PYTHONPATH=.:tests ./.venv/bin/python -m pytest tests -q
```

Podgląd manifestu z obrazu:

```bash
DOCKER_CONFIG=$PWD/infra/docker-config docker compose --profile manual run --rm mcp-sidecar
```
