# D12 — Codex runtime adapter

Zakres: L5165-5167, L5179-5180, sekcja 13 dokumentu (L2105-2267).
Katalog jest jedynym miejscem, w którym pisałem kod tego deliverable'u — poza jednym
wyjątkiem: `gate3.py` zapisuje artefakt do `artifacts/gate3/codex-runtime.json`, bo tak
wymaga `tools/gonogo/criteria.yaml`. `artifacts/` jest w `.gitignore` i jest katalogiem
wyjściowym, nie źródłem.

---

## ERRATA — co dokument mówi, a co robi PRZYPIĘTA WERSJA

Wszystko poniżej zostało zmierzone uruchomieniem `openai-codex-cli-bin 0.147.0`
(`codex-cli 0.147.0`, sha256 `19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37`,
Mach-O arm64, macOS, 2026-08-30). Nie jest to przepisane z dokumentacji online.

### 1. `experimentalApi` NIE jest kluczem `config.toml` (L2237)

To capability negocjowana w handshake `initialize`, w polu `capabilities.experimentalApi`.
W schemacie protokołu ma `"default": false`. **Ale Python SDK 0.147.0 ustawia
`CodexConfig.experimental_api: bool = True` DOMYŚLNIE** — sprawdzone w zainstalowanym pliku
`openai_codex/client.py` linia 209. Hardening bez jawnego `False` jest iluzoryczny.

Gdzie to żyje: `protocol.AppServerClient.experimental_api = False` i
`execution_adapter.zbuduj_config_sdk()` → `CodexConfig(..., experimental_api=False)`.
Testy: `test_app_server_contract.py::test_experimental_api_false_rejects_an_experimental_method`
oraz kontrola dodatnia z `experimental_api=True`.

**Czym to naprawdę bramkuje — wynik pomiaru:**

* Binarka wystawia 95 metod `ClientRequest` w schemacie stabilnym i 133 w schemacie
  `--experimental` (`codex app-server generate-json-schema [--experimental]`).
* Sprawdziłem WSZYSTKIE 95 metod stabilnych przy `experimentalApi` `false` i `true`:
  **zero różnic**.
* Bramka DZIAŁA, ale wąsko. `mock/experimentalMethod` (metoda istniejąca w binarce
  wyłącznie po to, żeby dało się to sprawdzić) przy `false` odpowiada
  `mock/experimentalMethod requires experimentalApi capability`, a przy `true` zwraca wynik.
* Metody obecne WYŁĄCZNIE w schemacie experimental — `process/spawn`, `thread/items/list` —
  **nie są bramkowane w runtime**: odpowiadają zwykłym błędem walidacji parametrów przy
  `experimentalApi=false`. Adnotacja gatingu jest więc nałożona wybiórczo.

Wniosek dla architektury: `experimentalApi=false` jest warstwą obrony, a nie granicą.
Granicą jest allowlist metod po stronie klienta.

### 2. `web_search` jest kluczem TOP-LEVEL, nie wpisem w `[features]` (L2226)

Potwierdzone odczytem `config/read` z żywego runtime'u: `config.web_search == "disabled"`
przy wpisie top-level. `[features].web_search` istnieje jako legacy alias, ale kanoniczne
feature-keys (`web_search_request`, `web_search_cached`) są oznaczone jako Deprecated.

**Uzupełnienie z 20.09 (D-046), zmierzone `codex_runtime.config_probe` na przypiętej 0.147.0.**
`web_search` przyjmuje CZTERY wartości: `disabled`, `cached`, `indexed`, `live`. Nie ma wartości
`enabled`, mimo że nazwa sama się narzuca: profil z `web_search = "enabled"` kończy się
odpowiedzią `[-32603] invalid configuration: unknown variant `enabled`, expected one of
`disabled`, `cached`, `indexed`, `live`` i runtime nie startuje. Sesje produktu (CONVERSATION)
dostają `live`, pilot zostaje przy `disabled`. Kod, który to wpisywał, przeszedł wszystkie testy
na atrapie, bo atrapa nie sprawdzała wartości; sprawdza od tego samego dnia.

### 3. **`command/exec` WYKONUJE POLECENIA POWŁOKI mimo wyłączonych flag** (do L2213-2222)

To jest najważniejsze ustalenie tego deliverable'u.

Przy `config.toml` zawierającym `features.shell_tool = false`,
`features.unified_exec = false`, przy `experimentalApi = false` i BEZ zalogowanego konta,
wywołanie:

```json
{"id": 6, "method": "command/exec", "params": {"command": ["/bin/echo", "hi"]}}
```

zwróciło:

```json
{"id": 6, "result": {"exitCode": 0, "stdout": "hi\n", "stderr": ""}}
```

Efektywna konfiguracja odczytana zaraz potem przez `config/read` potwierdzała, że wszystkie
pięć flag jest `false`, a `web_search` to `"disabled"`.

**Interpretacja:** flagi `[features]` sterują tym, jakie narzędzia dostaje MODEL. Nie
sterują powierzchnią JSON-RPC dostępną dla KLIENTA app-servera. Zdanie z L2222 („runtime
nie może wywołać `thread/shellCommand`, `command/exec` ani metod process/config/import/write")
jest realizowalne wyłącznie przez:

1. allowlist metod po stronie klienta (`protocol.ALLOWED_METHODS` — 13 metod),
2. sandbox OS i granicę egress, które dokument sam nazywa nadrzędnymi (L2239).

Test `test_shell_and_exec_are_blocked_client_side_not_server_side` utrwala OBA fakty
naraz — zarówno to, że allowlist blokuje, jak i to, że serwer by nie zablokował. Jeżeli
ktoś kiedyś „uprości" allowlist w przekonaniu, że `config.toml` wystarcza, ten test padnie.
`gate3.py` zapisuje ten fakt do artefaktu jako
`szczegoly.errata_command_exec_dziala_mimo_wylaczonych_flag`.

### 4. Python SDK nie ma paginacji `model/list` ani rate limitów

`Codex.models(*, include_hidden: bool = False)` → `CodexClient.model_list()` wywołuje
`model/list` z samym `includeHidden`, **bez kursora**. Odpowiednika
`account/rateLimits/read` nie ma w publicznej powierzchni SDK w ogóle.

Dlatego warstwa kontrolna (`CodexAccountAdapter`) rozmawia z app-serverem bezpośrednio przez
własny, wąski klient JSON-RPC, a SDK zostaje do wykonywania turnów. To jest dokładnie
podział z L2107-2115 i zdanie z L2125 o „wąskim adapterze niskopoziomowego JSON-RPC".

### 5. `CodexConfig` nie ma pola `codex_home`

Izolacja to `CodexConfig(env={"CODEX_HOME": ...})`. SDK robi `os.environ.copy()` i dopiero
nakłada `env`, więc dziedziczy CAŁE środowisko procesu — w tym `OPENAI_API_KEY`, który
zmieniłby tryb auth. `zbuduj_config_sdk()` nadpisuje go pustą wartością, a
`AppServerClient.start()` usuwa go ze środowiska podprocesu. Test:
`test_api_key_does_not_leak_into_the_app_server_process` — `account/read` nadal zwraca
`account: null` mimo ustawionego `OPENAI_API_KEY`.

### 6. `thread/resume` na świeżym wątku nie działa — i to nie jest błąd bindingu

`thread/start` zwraca wątek, ale `thread/resume` na nim odpowiada
`no rollout found for thread id <uuid>`. Rollout powstaje dopiero po wykonanym turnie.
Test rozróżnia ten błąd runtime'u od `ResumeRefused` z warstwy autoryzacji; wariant
pozytywny (resume po turnie) jest oznaczony `@pytest.mark.live`.

### 7. Nazwy ról: dokument ma dwa różne czwórki

Opis D12 w spec F0 mówi „Principal(Sol) / Planner(Terra) / Worker(Luna) / Verifier”, a
L2265 i `ThreadRole` z `contracts` mówią „Planner, worker, verifier i post-verifier”.
Użyłem enuma z `contracts` (PLANNER / WORKER / VERIFIER / POST_VERIFIER), bo brief mówi
wprost „`ThreadRole` z contracts”, a L2265 jest zdaniem z dokumentu źródłowego.
**Zastrzeżenie:** jeżeli intencją było, żeby czwartą rolą był Principal, a nie
post-verifier, to zmiana dotyczy `contracts` (D21) i musi iść przez ADR — nie „poprawiam"
tego lokalnie.

### 8. Co NIE jest erratą — sprawdziłem i dokument ma rację

Wszystkie nazwy metod z L2166-2176 są prawdziwe i obecne w przypiętej binarce:
`initialize`, `initialized`, `account/login/start`, `account/login/completed`,
`account/read`, `model/list`, `account/rateLimits/read`. Paginacja po `nextCursor`
i `includeHidden` są prawdziwe. Maszyna stanów z L2134-2141 jest przepisana jeden do
jednego i ma osobny test porównujący tabelę z dokumentem.

---

## Co zweryfikowałem URUCHOMIENIEM

`121 passed` w `services/codex_runtime/tests/` (Python 3.12, macOS arm64), **bez konta
ChatGPT**. Testy uruchamiają PRAWDZIWY proces `codex app-server` na izolowanym
`CODEX_HOME`. Co konkretnie zadziałało offline:

| Rzecz | Wynik |
|---|---|
| handshake `initialize` + `initialized` | działa, `codexHome` w odpowiedzi zgadza się z izolowanym katalogiem |
| `config/read` | zwraca EFEKTYWNĄ konfigurację z `origins` (który plik ustawił który klucz) |
| `model/list` z paginacją | 5 modeli na 1 stronie, `nextCursor=null`, cztery trasy walidują się wobec `supportedReasoningEfforts` |
| `account/read` bez logowania | `{"account": null, "requiresOpenaiAuth": true}` — nie błąd |
| `account/login/start(chatgptDeviceCode)` | zwraca prawdziwy `verificationUrl` i `userCode` bez konta |
| `account/rateLimits/read` bez konta | błąd `codex account authentication required to read rate limits` |
| `thread/start` × 4 role | cztery różne `threadId` i `sessionId` |
| `mcpServerStatus/list` | pusta lista aktywnych serwerów MCP |
| `mock/experimentalMethod` | odrzucona przy `experimentalApi=false`, przechodzi przy `true` |
| `generate-json-schema` | generuje kontrakt; `--experimental` daje 133 metody zamiast 95 |
| obraz Dockera | buduje się, `codex --version` na PATH działa |

`gate3.py` uruchomiony na tym środowisku: **5/6 kontroli zdanych**,
`unattended_rozstrzygniete` = `false` z dowodem „wymaga człowieka”.

## Czego NIE zweryfikowałem

- **Żadnego prawdziwego turnu.** Wymaga zalogowanego konta ChatGPT. Wszystko, co dotyka
  turnu, jest `@pytest.mark.live` albo testowane atrapą sidecara.
- **Wielogodzinnej pracy bez nadzoru.** To jest treść GATE-3 i bez konta jest
  nierozstrzygalne. `gate3.py` NIE zmyśla tu wyniku — zwraca `zdane: false`.
- **Device code login end-to-end.** Start działa; dokończenie wymaga człowieka wpisującego
  kod, a dodatkowo device code login musi być WŁĄCZONY w ustawieniach konta osobistego albo
  w uprawnieniach workspace'u. Bez tego `account/login/start` zwróci jawny błąd — i to ten
  błąd rozstrzyga dostępność metody, nie żaden preflight (L2181).
- **Zachowania pod obciążeniem i przy wyczerpaniu quoty.** Bez konta nie da się wywołać
  rate limitu.

## Zależności od innych deliverables

| Od kogo | Co | Stan |
|---|---|---|
| D21 `contracts/` | `ThreadRole`, `ThreadSecurityBinding`, `authorize_resume`, `CodexRunThread`, `TurnSpec`, `CodexConnectionState`, `JobWaitingState`, `CodexSupervisorMode` | istnieje, używane bez duplikowania |
| D13 `services/mcp_sidecar/` | per-turn sidecar | zaimplementowany; test integracyjny na prawdziwym procesie |
| D01 `versions.lock.toml` | piny `openai-codex` / `openai-codex-cli-bin` | zgodne z `pyproject.toml` tego serwisu |
| D07 `db/` | trwałość `ThreadSecurityBinding` i `CodexRunThread` | **nie podpięta** — F0 trzyma je w pamięci; zapis do bazy to F1 |
| D19 `tools/gonogo/` | format artefaktu bramki | `gate3.py` używa tego samego kształtu, co `browser_supervisor/contract.py` |

**Rozbieżność ze `versions.lock.toml` — rozwiązana w trakcie pracy.** W chwili, gdy
zaczynałem, plik wskazywał `schema_artifact = "contracts/codex/app-server-schema.json"`.
Ten katalog należy do D21, a brief zabrania mi pisania poza własnym `owner_dir`, więc
kontrakt trafił do `services/codex_runtime/codex_runtime/contract/`. Właściciel D01
poprawił w międzyczasie wpis na `services/codex_runtime/codex_runtime/contract/` — ścieżki
się zgadzają i nie ma nic do zrobienia. Odnotowane, bo to była realna rozbieżność.

## Decyzje i zastrzeżenia

1. **Allowlist ma 13 metod, nie 12.** Do listy z opisu D12 dołożyłem `config/read`
   i `mcpServerStatus/list`. Obie są czysto odczytowe i obie są WYMAGANE przez DoD:
   „efektywna konfiguracja weryfikowana odczytem, nie założeniem” i „weryfikowane listą
   aktywnych serwerów MCP w runtime, nie deklaracją w config”. Bez nich tych dwóch punktów
   nie da się spełnić inaczej niż deklaratywnie.

2. **`_request_bez_allowlisty` istnieje i ma brzydką nazwę celowo.** Bramka i testy muszą
   umieć pokazać, co zrobiłby serwer BEZ allowlisty — inaczej errata nr 3 byłaby tylko
   twierdzeniem. Kod produkcyjny wywołuje `request`.

3. **Trasy model/effort są stałą tabelą, nie routerem.** Cztery wpisy w `routing.TRASY`,
   `ROUTER_POLICY_VERSION = "f0-stala-tabela-1"`. Pełny Model Router to E2 (L5266).
   Zmienne `WENEXT_CODEX_MODEL`/`WENEXT_CODEX_EFFORT` z compose NIE nadpisują tabeli —
   `rozbieznosc_z_env()` tylko sprawdza, czy się z nią zgadzają. Nadpisywanie znaczyłoby,
   że „stała, jawnie zapisana trasa” jest stała wyłącznie w pliku.

4. **`cli_auth_credentials_store = "file"`, nie `"ephemeral"`.** Opis D12 sugeruje
   rozważenie `ephemeral`. Odrzuciłem: L2200-2203 wymaga wprost `file` i `auth.json` z 0600,
   a login jest jednorazowym aktem człowieka, który musi przeżyć restart runtime'u —
   a to jest jeden z punktów wielogodzinnego testu z L2129. `"auto"` jest zakazane
   konstrukcyjnie (`HardeningConfig` rzuca), bo L2205 mówi wprost, że `CODEX_HOME` nie daje
   izolacji przy wspólnym keyringu.

5. **`multi_agent_v2` dołożony do listy z L2228.** `multi_agent`, `multi_agent_v2`
   i `multi_agent_mode` to trzy różne klucze o różnych domyślnych. Wyłączenie pierwszego
   nie wyłącza drugiego. To rozszerzenie listy z dokumentu, nie odejście od niej.

6. **Lease runtime'u to plik z PID-em, nie tylko maszyna trybów.** `CodexSupervisorMode`
   żyje w procesie i nie widzi drugiego procesu, który sięgnąłby po ten sam `CODEX_HOME`.
   L2159 zabrania dwóch App Serverów na jednym katalogu, więc `supervisor.py` dokłada
   `wenext-runtime.lease` z PID-em i sprawdzeniem, czy proces żyje.

7. **Kontrakt w repo to metody + dziewięć fragmentów schematu, nie cały bundle.** Pełny
   `generate-json-schema` to ponad 600 kB w dwóch plikach zbiorczych plus ~290 osobnych.
   Commituję to, co jest realnym wejściem testów, i skrypt `generate_contract.py`, który
   odtwarza całość z przypiętej binarki.

## Jak uruchomić

```bash
cd services/codex_runtime
python3 -m venv .venv
./.venv/bin/pip install "openai-codex==0.147.0" "pytest>=8.3" -e ../../contracts

# testy bez konta
PYTHONPATH=.:tests ./.venv/bin/python -m pytest tests -q -m "not live"

# testy wymagające konta ChatGPT (D20)
PYTHONPATH=.:tests ./.venv/bin/python -m pytest tests -q -m live

# bramka nr 3
./.venv/bin/python gate3.py            # → artifacts/gate3/codex-runtime.json

# regeneracja kontraktu protokołu z przypiętej binarki
PYTHONPATH=. ./.venv/bin/python -m codex_runtime.generate_contract
```
