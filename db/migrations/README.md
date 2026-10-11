# Migracje schematu

Krótki plik. Wszystko tu wynika z konkretnego błędu, który już raz kosztował czas —
nie z upodobań.

## Reguła

`tools/dev/apply_migrations.sh` stosuje **wszystkie** pliki `*.sql` z tego katalogu,
**posortowane po nazwie**, po jednym wywołaniu `psql` na plik, z `ON_ERROR_STOP=1`.
Numeryczny prefiks jest **obowiązkowy** i pilnuje go
`services/db_schema/tests/test_migration_order.py`:

- plik bez numeru w nazwie jest błędem, nie plikiem stosowanym na końcu,
- dwa pliki z tym samym numerem są błędem — „po nazwie” przestaje wtedy znaczyć
  „w kolejności zamierzonej”,
- `10_` sortuje się **przed** `0002_`, więc numery trzymamy czterocyfrowe.

Pusty katalog to **porażka**, nie stan „nic do zastosowania”. Skrypt kończący się zerem
nad zerem plików zostawia pustą bazę i wygląda jak sukces — to jest `all([]) == True`
napisane w powłoce (D-011).

## Zasada, której nie wolno złamać: migracja zastosowana jest niezmienna

**Nie edytuj w miejscu pliku, który został już gdziekolwiek zastosowany.**
`apply_migrations.sh` nie odtwarza schematu od zera i nie prowadzi tabeli wersji —
plik przepisany rozjeżdża się z każdą bazą, która wykonała wersję poprzednią, i różnica
ujawnia się dopiero jako niezrozumiała awaria testu, który dotyka innej tabeli.

Dlatego wszystko, co `0001` zrobiła źle, naprawia `0002`, a nie poprawka w `0001`.

## Co jest w którym pliku

| Plik | Faza | Zawartość |
| --- | --- | --- |
| `0001_f0_kernel.sql` | E1.F0 | tenant, konta, profile, `profile_leases` z fencingiem, sesje przeglądarki, `InteractionLease`, `StreamSession`, capability snapshoty, `audit_events`, przejścia trybu kapsuły |
| `0002_f0_naprawy_i_operator.sql` | E1.F1 | naprawy `0001` (dwanaście nazw, `NOT NULL`, TRUNCATE, composite FK audytu, `encrypted_storage_ref`) oraz tabela `operators` (D-016) |
| `0003_f1_kernel.sql` | E1.F1 | kolejka jobów, ścieżka zgody, `artifacts`, `profile_deks` |
| `0004_observe.sql` | Outcome A | `communities`, `observed_threads`, `observed_comments`, `thread_metric_snapshots` (append-only), `user_profile_versions`, `community_pictures`, `thread_judgments` (append-only), `judgment_feedback` — `docs/OUTCOMES.md` §5 |
| `0005_voice.sql` | Etap 3 M0 | `observed_comments.score` + `score_unknown_reason` (para `Observed[int]`, backfill `NOT_EXPOSED_BY_PLATFORM` dla HN), indeks `observed_comments_by_thread_parent`, `community_voice_cards` (wersjonowana karta głosu community, jak `community_pictures`) — `docs/OUTCOMES.md` §8 etap 3 |
| `0006_pilot.sql` | Pilot 24 h | `pilot_runs` (manifest runu z licznikiem budżetu wywołań modelu, wstrzymane automaty, konfiguracja do przywrócenia, dziennik zdarzeń hosta), `pilot_cycles` (jeden wiersz na slot 30 min, wynik cyklu), `pilot_reactions` (szkic / WAIT / SILENCE z celem, hashami, statusem oceny człowieka) — `docs/START-PILOTA-24H.md`, karta pilota w `docs/STAN-JAKOSCI-ROZMOW.md` |

## Cztery katalogi, nie jeden

Migracja `0002` przemianowuje **dwanaście** polskich identyfikatorów zostawionych przez
`0001` (D-010). Rzecz, o którą łatwo się rozbić: to są **cztery różne katalogi systemowe**,
a `ALTER TABLE ... RENAME CONSTRAINT` **nie dotyka** ani indeksów, ani funkcji, ani triggerów.

| Katalog | Ile | Czym się przemianowuje |
| --- | --- | --- |
| `pg_constraint` | 4 | `ALTER TABLE ... RENAME CONSTRAINT` |
| `pg_class` (`relkind='i'`) | 6 | `ALTER INDEX ... RENAME TO` |
| `pg_proc` | 1 | `DROP FUNCTION` + `CREATE FUNCTION` |
| `pg_trigger` | 1 | `DROP TRIGGER` + `CREATE TRIGGER` |

Test czytający wyłącznie `pg_constraint` zostawiłby **osiem z dwunastu** nazw na miejscu
i ogłosił sukces. Lista i katalogi są danymi w
`services/db_schema/db_schema/expected_schema.py` (`RENAMED_IN_0002`), a
`tests/test_migration_0002.py` sprawdza **oba kierunki**: że polskiej nazwy nie ma **i** że
obiekt istnieje pod nową. Sam brak starej nazwy nie jest dowodem przemianowania —
`DROP` bez `CREATE` daje dokładnie ten sam wynik przy zniknięciu niezmiennika.

## Zamknięte listy wartości: `text` + nazwany `CHECK`, nie `CREATE TYPE`

`ALTER TYPE ... ADD VALUE` **nie działa w bloku transakcyjnym** i jest nieodwracalny,
więc native enum zamienia „źle nazwaliśmy stan” w migrację jednokierunkową. Wzorzec
istnieje już w `0001` (`capsule_mode_transitions.reason`) i `0003` stosuje go do każdej
nowej listy.

CHECK-i są **nazwane**, a nie zdane na autonazywanie `<tabela>_<kolumna>_check`, bo
`tests/test_enum_parity.py` szuka ich **po nazwie** i pada, gdy nie ma. Wersja szukająca
„CHECK-a wspominającego tę kolumnę” po literówce nie znajduje nic i **milczy**.

Parytet z `contracts` jest sprawdzany w obie strony: dziewięć native enumów z `0001`
wobec `StrEnum`-ów z `community_os_contracts`, a listy `CHECK` wobec deklaracji
w `expected_schema.py`. Połowa listy statusów joba (`WAITING_*`) jest **czytana**
z `contracts.JobWaitingState`, żeby nowy stan waiting psuł test, a nie stawał się po cichu
wartością, której baza nie przyjmuje.

## Trójka tenantowa, nie para

Tabela **workspace'owa** ma `organization_id NOT NULL`, `workspace_id NOT NULL` i klucz
obcy w kanonicznej **trójce** (L3874-3876):

```text
(organization_id, workspace_id, parent_id) → parent(organization_id, workspace_id, id)
```

Para `(organization_id, parent_id)` przepuściłaby dziecko z workspace'u A pod rodzica
z workspace'u B tej samej organizacji. Tabela, której rodzicem jest sam workspace, ma parę
`(organization_id, workspace_id) → workspaces(organization_id, id)`.

Dwie tabele mają `workspace_id` **nullable**, bo tak stoi w dokumencie, i nie są tabelami
workspace'owymi: `audit_events` (0001, klucz dołożony w `0002`) oraz `jobs`
(L3825 „workspace_id nullable”, L3884 job organizacyjny). Oba te klucze są `MATCH SIMPLE` —
`MATCH FULL` zablokowałoby zapis rekordu organizacyjnego.

**Tabele z `0001` nie dostają retrofitu `workspace_id`** (D-020). Trójka obowiązuje tylko
nowe tabele z `0003`.

Jawne listy tabel — workspace'owych i o mieszanym scope — leżą w `expected_schema.py`,
a nie są wyliczane zapytaniem. Test „każda tabela z `workspace_id` ma klucz obcy”
**przechodzi próżno** na schemacie, w którym żadna tabela tej kolumny nie ma; dlatego
pętla jedzie po liście z asercją długości **przed** pętlą, a osobny test idzie
w przeciwną stronę i pada, gdy nowa tabela zdobędzie tę kolumnę bez wpisania na listę.

## Jak uruchomić

```bash
# jednorazowy Postgres na losowym porcie + migracje + suita jednego pakietu
REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh services/db_schema
REPO_ROOT=$PWD bash tools/dev/run_db_tests.sh services/profile_lease

# same migracje na wskazanym kontenerze
REPO_ROOT=$PWD WENEXT_PG_CONTAINER=<nazwa> bash tools/dev/apply_migrations.sh
```

`run_db_tests.sh` czyta wynik z raportu `--junitxml`, nie z kodu wyjścia, i **odrzuca**
przebieg z `passed == 0` albo `skipped > 0`. Pominięty test bazodanowy przechodzi
w podsumowaniu za zielony — moduł ze `skipif(not DSN)` daje „2 tests collected”, potem
„2 skipped” i kod wyjścia 0. Fixture bazodanowa robi `pytest.fail`, nigdy `skipif`.

## Czego w tych migracjach NIE MA

Świadomie, bo rozlanie zakresu jest wprost zakazane (L5500-5501):

- `outbox_events`, `inbox_receipts` — E3 (L5205); to transport, nie stan,
- RLS, `FORCE ROW LEVEL SECURITY`, role `dispatcher_login` / `outbox_publisher_login` /
  `allocator_login`, schemat `control_api` — E3 (L5206, L3878-3884). DoD L5215 mówi wprost:
  kolumny tenantowe i composite FK istnieją **nawet gdy RLS jest wyłączone**, i to jest
  cała izolacja, jaką ma E1,
- `tasks`, `task_dependencies`, `publications`, `action_budget_*`,
  `thread_quality_assessments`, registry platform — F3/F4/F5,
- `automation_runs` — D-020: tego łańcucha nie ma w dokumencie ani razu, model danych zna
  wyłącznie `execution_attempts` (L3129). `AutomationRun` jest **typem** w `contracts` nad
  tą tabelą, nie osobną tabelą,
- progi liczbowe D-017 (limit prób, TTL lease'u, retencja, limit treści) — mieszkają
  w **jednym** module konfiguracji control plane. Próg zduplikowany w `CHECK`-u rozjeżdża
  się z tamtym bez ostrzeżenia.
