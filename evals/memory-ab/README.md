# memory-ab: czy pamięć zmienia następną decyzję

Ten katalog jest zbiorem ODŁOŻONYCH PRZYPADKÓW do porównania dwóch przebiegów tego samego
zadania: z pamięcią (doświadczenia i korekty człowieka) i bez niej. Kryteria są zapisane
**przed** pomiarem, w `criteria.yaml`, i tak ma zostać: próg dopisany po zobaczeniu wyników
mierzy to, co się udało, a nie to, co miało się udać.

## Czego ten katalog DOWODZI dziś

Dokładnie dwóch rzeczy, obu bez modelu:

1. przypadki są kompletne i czytelne maszynowo: każdy ma sytuację, dostępną pamięć i oczekiwane
   zachowanie, a kryteria pokrywają każdy wymiar, którego przypadki dotyczą;
2. materiał pamięci w przypadku z pamięcią naprawdę zawiera to, co ma zmienić decyzję (korektę
   albo doświadczenie), więc różnica między przebiegiem A i B nie jest różnicą przypadkową.

## Czego NIE dowodzi

Niczego o jakości modelu. Porównanie A/B na prawdziwym modelu ma werdykt `NIE_URUCHOMIONO`
i zostanie uruchomione po wdrożeniu, na zalogowanym koncie. Harness mówi to wprost zamiast
wypisywać zero, bo zero wygląda jak pomiar.

## Dane oceniające NIE trafiają do pamięci produktu

Przypadki z tego katalogu nie są nigdzie wpisywane do `knowledge_items`. Runner nie ma
połączenia z bazą, żaden moduł produkcyjny nie czyta tej ścieżki, a
`evals/tests/test_memory_ab.py` sprawdza jedno i drugie. Powód jest prosty: zbiór odłożony,
który wyciekł do pamięci, przestaje być odłożony, a pomiar na nim zaczyna mierzyć zapamiętanie.

## Uruchomienie

    make evals-memory-ab
