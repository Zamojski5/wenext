# NOTES — D03 (threat model E1.F0)

## Co powstało

| Plik | Co to jest |
| --- | --- |
| `README.md` | threat model: assets, trust boundaries, adversaries, kontrole, residual risks, checklista wejścia do F2 |
| `controls.yaml` | 34 kontrole maszynowo czytelne: `id`, `granica`, `opis`, `zrodlo_w_dokumencie`, `status`, `enforced_by`, `test`, `uwagi` |
| `test_controls.py` | test integralności — uruchomiony, wynik niżej |

## Co zweryfikowane uruchomieniem

```
$ python3 docs/threat-model/test_controls.py
Kontroli: 34
  CZESCIOWO    9
  EGZEKWOWANE  20
  PLANOWANE    5
Sprawdzonych ścieżek (enforced_by + test): 83

OK — każda ścieżka istnieje, każda kontrola bez egzekwowania jest jawnie PLANOWANE.

$ contracts/.venv/bin/python -m pytest docs/ -q
7 passed

$ ruff check docs/
All checks passed!
```

Test uruchomiony w dwóch wariantach: z PyYAML (host, 3.12.4) i z wymuszonym brakiem PyYAML
(własny parser podzbioru YAML). Oba dają 34 kontrole i zero błędów.

## Decyzje, które podjąłem sam

1. **Trzy statusy zamiast dwóch.** Zadanie wymagało `PLANOWANE` dla kontroli bez
   egzekwowania. Dołożyłem `CZESCIOWO` dla kontroli, które mają kod albo konfigurację, ale
   nie mają testu. Bez tego 9 kontroli musiałoby udawać `EGZEKWOWANE` albo `PLANOWANE`,
   a obie odpowiedzi byłyby nieprawdziwe.
2. **Test sprawdza też pole `test`, nie tylko `enforced_by`.** DoD D03 mówi „wskazującą
   plik i test", więc obie ścieżki muszą istnieć. Test dodatkowo blokuje status
   `EGZEKWOWANE` bez wskazanego testu.
3. **Nie wpisałem ścieżek do `services/stream_gateway/`.** Pliki `rfb.py` i `vncauth.py`
   pojawiły się tam w trakcie mojej pracy (D06 pisany równolegle) i nie mam pewności co do
   ich docelowych nazw. Kontrola C-24 (`viewOnly` nie jest granicą bezpieczeństwa) wskazuje
   więc tylko część serwerową egzekwowaną w bazie i w kontraktach, ze statusem `CZESCIOWO`
   i z jawną adnotacją, że RFB-aware gateway jest deliverable'em D06. **Do dopisania, gdy
   D06 się ustabilizuje.**

## Rozbieżności, które znalazłem i których nie naprawiałem

1. **`contracts/src/community_os_contracts/scope.py` wskazuje w `enforced_by` katalogi,
   które nie istnieją:** `services/scope_policy`, `services/capability_registry`.
   Sześć z dziewięciu pozycji granicy zakresu odsyła tam. To nie jest mój plik i go nie
   ruszałem, ale mój `controls.yaml` celowo **nie** kopiuje tych ścieżek — dlatego moje
   kontrole GR-5 wskazują pliki, które faktycznie istnieją. Do rozstrzygnięcia przez
   właściciela D14 (ScopePolicy).
2. **Append-only na `audit_events` nie ma dosłownego zdania w dokumencie.** Dokument
   wymienia tabelę (L3202) i wymaga append-only od metryk (niezmiennik 17, L208).
   Migracja F0 rozszerza to na dziennik decyzji. Zapisane wprost w `uwagi` kontroli C-33,
   żeby nikt nie szukał cytatu, którego nie ma.
3. **Kontrola C-30 (zakaz koordynowanego podbijania) nie ma czym być egzekwowana.**
   To jedyna z dziewięciu pozycji sekcji 4, która w F0 zależy wyłącznie od człowieka.
   Zapisana jako pozycja 9 w `docs/f0-limits/README.md`.

## Zależności od innych deliverables

| Kontrola | Czeka na | Co się wtedy zmieni |
| --- | --- | --- |
| C-01, C-02, C-04 | D11 (mcp-sidecar), D12 (codex runtime) | `CZESCIOWO` → `EGZEKWOWANE`, dojdą testy `brak_shell_i_exec` i `tylko_dozwolony_mcp` |
| C-24 | D06 (stream gateway) | dojdzie `services/stream_gateway/...` do `enforced_by` |
| C-05, C-06, C-15, C-26, C-30 | F2, F4, F5 | `PLANOWANE` → docelowy status, `enforced_by` przestanie być puste |

## Czego NIE zrobiłem

- Nie dopisywałem niczego do `tools/gonogo/` ani do `tests/` — poza swoim `owner_dir`.
  `tools/gonogo/report.py` już czyta `docs/f0-limits/README.md`; wpięcie `controls.yaml`
  do raportu Go/No-Go zostawiam właścicielowi D19.
- Nie modelowałem zagrożeń dla faz po F0 (job queue, RLS, Model Router). Granica jest ta
  sama, co granica F0.
