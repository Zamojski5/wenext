# NOTES — D04 (rejestr niewykonalnego w F0)

## Co powstało

| Plik | Co to jest |
| --- | --- |
| `README.md` | 12 pozycji rejestru, w tym pełna arytmetyka bramki E1 (pozycja 8) |
| `test_registry.py` | test spójności z DoD F0 — uruchomiony, wynik niżej |

Osiem pozycji pochodzi wprost ze specyfikacji D04. Dołożone: 9 (zakaz koordynowanego
podbijania bez egzekwowania), 10 (pin Chrome for Testing z kanału Beta), 11 (niedomknięty
enum wyniku bramki E1), 12 (niekompletny security baseline jako warunek wejścia do F2).

## Co zweryfikowane uruchomieniem

```
$ python3 docs/f0-limits/test_registry.py
Pozycji w rejestrze: 12
OK — komplet pól, żadna pozycja nie powiela Definition of Done fazy F0.

$ contracts/.venv/bin/python -m pytest docs/ -q
7 passed

$ ruff check docs/
All checks passed!

$ python3 tools/gonogo/report.py --artifacts artifacts --out <scratchpad>
GATE-1 GO / GATE-2 GO / GATE-3 NIE_URUCHOMIONO
sekcja „Czego ten raport NIE dowodzi" → "Pełna lista: `docs/f0-limits/README.md`."
```

Ostatnie sprawdzenie było powodem, dla którego ten plik musiał powstać: `tools/gonogo/report.py`
(linie 260–263) warunkowo wypisuje ścieżkę `docs/f0-limits/README.md`. Przed moją pracą
warunek był fałszywy i raport milczał o swoich granicach. Raport wygenerowałem do
scratchpadu, nie do `artifacts/` — to nie jest mój katalog.

## Arytmetyka — jak liczona

Wszystkie liczby w pozycji 8 są przeliczone skryptem i wpisane ręcznie; skryptu nie zostawiam
w repo, bo nie ma czego uruchamiać cyklicznie — wejścia są stałymi z dokumentu.

Wejścia: L4602 (okno 6–8 tygodni, ≥ 2 archetypy, ≥ 2 konta od zera), L4608 (≥ 40
`PUBLISH_REPLY` na archetyp), L4503–4504 (capy 1 i 2 komentarze/dobę), L4593 (12 kart na
operatora, 8 na konto), L4614 (≤ 6 min na wkład), L5152 (każda akcja zewnętrzna to karta).

Wyniki: 80 wkładów; 30 dni minimum na konto (5 + 10 + 15); zapas 12 dni w oknie
6-tygodniowym i 26 w 8-tygodniowym; 28 kart/dobę popytu przy 4 kontach wobec capu 12;
480 minut budżetu operatora na całe okno, czyli 8,6–11,4 min/dobę.

**Trzy wnioski, których w dokumencie nie ma:**

1. Zapas 12 dni nie pokrywa jednego zdarzenia degradacji (odbudowa kosztuje 25 dni),
   więc okno 6-tygodniowe jest nominalne, a planować trzeba 8.
2. Przy pełnym capie 12 kart/dobę na kartę zostaje 57 sekund — cap jest sufitem, nie targetem.
3. Reakcje przy maksymalnym budżecie 22.8 (250 kart) przekraczają cały budżet czasu
   operatora (240 kart przy 2 min/kartę), a żaden dowód awansu presence tracku ich nie liczy.
   Wniosek: reakcje w E1 trzymać blisko zera.

Pozycja 8 odpowiada też liczbowo na otwarte pytanie nr 3 ze `STAN-PROJEKTU.md`.

## Zastrzeżenie do dokumentu (zgodnie z zasadą naczelną briefingu)

**Nie „poprawiłem" tego po cichu, zapisuję jawnie:** reguła sortowania kolejki approvali
z L4595 (rosnąco po `expires_at` w obrębie `priority_tier`) nie odróżnia komentarza od
reakcji. Przy 2–4 kontach w `SOFT_PARTICIPATION` popyt na karty przekracza cap operatora,
więc reakcja z krótszym TTL może wyprzeć komentarz — czyli akcję, którą bramka liczy.
Dokument tego nie rozstrzyga. Zapisane w pozycji 8.3 jako luka, nie jako interpretacja.

## Sprzeczność, którą test wykrył i którą rozwiązałem świadomie

Pozycja 2 (unattended-auth Codeksa) ociera się o DoD F0 z L5182 („potwierdzono albo
odrzucono wspierany tryb wielogodzinnej pracy"). Pierwsza wersja rejestru **oblała własny
test** z pokryciem 57%.

Rozstrzygnięcie: teza pozycji 2 nie brzmi „F0 tego nie dowodzi", tylko „nie da się tego
zamknąć bez człowieka z kontem ChatGPT". DoD jest spełnialny — także przez `NO_GO`. Dlatego:

- przeredagowałem pozycję 2 i dodałem w niej jawny akapit „Uwaga na granicę z DoD F0",
- zawęziłem test: porównuje **tezę** pozycji (tytuł + pole „Czego F0 nie dowodzi"), a nie
  całą jej treść, bo pozostałe pola mają prawo cytować dokument.

Po zmianie maksymalne pokrycie w całym rejestrze wynosi 29% przy progu 50%. Wartość
zmierzona i zapisana w komentarzu w `test_registry.py`, żeby przy kolejnej edycji rejestru
było wiadomo, ile marginesu zostało.

## Zależności od innych deliverables

- Pozycja 2 zamyka się dopiero razem z D20 (człowiek z kontem ChatGPT).
- Pozycja 10 odsyła do `ADR-0002` i do `tools/dev/check_versions.py` (D02 / D07);
  polityki maksymalnego patch lagu z L2313 nie ma nigdzie — to dług przypisany do F1.
- Pozycja 11 wymaga decyzji człowieka przed startem okna F6; nie da się jej zaimplementować
  bez tej decyzji.
- Pozycja 12 odsyła do checklisty w `docs/threat-model/README.md`, rozdz. 7.

## Czego NIE zrobiłem

- Nie umieściłem testu spójności w `tests/` (spec D04 sugeruje ten katalog) — `tests/`
  nie istnieje i jest poza moim `owner_dir`. Test leży w `docs/f0-limits/test_registry.py`
  i przechodzi pod pytest razem z resztą `docs/`.
- Nie dopisywałem niczego do `tools/gonogo/report.py`. Generator już cytuje ten rejestr;
  ewentualne wciągnięcie poszczególnych pozycji do sekcji „Czego ten raport nie stwierdza"
  należy do właściciela D19.
