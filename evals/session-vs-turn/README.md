# session-vs-turn: czy środowisko agentowe daje lepszy wynik niż stara tura

P2 z roadmapy kończy się zdaniem: „porównanie ze starą turą na tym samym zadaniu i modelu
pozwala ocenić wpływ środowiska". Ten katalog jest zestawem zadań uruchamianych DWIEMA ścieżkami
na tym samym wątku, tym samym celu i tym samym modelu:

* **stara tura** (`observe/turns.py`): jedno wywołanie modelu z gotowym pakietem kontekstu.
  Model dostaje wszystko, co ktoś wcześniej wybrał, i nie ma jak poprosić o więcej;
* **nowa sesja** (P2): wątek silnika i narzędzia. Model sam pyta o to, czego potrzebuje, zapisuje
  ustalenia i widzi wynik swoich działań.

Różnica między nimi to WPŁYW ŚRODOWISKA, a nie modelu: model i prompt bazowy są te same.

## Czego ten katalog dowodzi dziś

Dwóch rzeczy, obu bez modelu:

1. zadania są kompletne i symetryczne: każde ma wątek, cel i oczekiwanie, i każde da się podać
   OBU ścieżkom bez zmiany treści. Zadanie, które dałoby się uruchomić tylko nową ścieżką,
   mierzyłoby jej przewagę przez konstrukcję;
2. kryteria zapisano PRZED pomiarem i żadne nie ma jeszcze progu.

## Czego NIE dowodzi

Niczego o jakości. Samo porównanie wymaga zalogowanego Codeksa i ma werdykt `NIE_URUCHOMIONO`
do czasu wdrożenia. Harness mówi to wprost, bo zero w raporcie wygląda jak pomiar.

## Zadania oceniające nie trafiają do pamięci produktu

Nic z tego katalogu nie jest wpisywane do `knowledge_items`; runner nie ma połączenia z bazą,
żaden moduł produkcyjny nie czyta tej ścieżki, a `evals/tests/test_session_vs_turn.py` sprawdza
jedno i drugie. Zbiór odłożony, który wyciekł do pamięci, przestaje być odłożony.

## Uruchomienie

    make evals-session-vs-turn
