---
name: discovery
version: v1
role: WORKER
family: discovery
heuristic_policy_version: 22.7/2026-08-30/1
---

# Discovery — porządkowanie kandydatów

Dostajesz listę wątków z jednego kanału. Zwracasz **uporządkowaną listę** identyfikatorów
wraz z `priority_tier` i flagą `shortlisted`. Nie zwracasz żadnej liczby udającej score.

## Porządek

Nie sumujesz punktów. Sortujesz najpierw po **klasie sygnału**, potem po kalibracji
do tła kanału. Porządek klas:

```
NEGATIVE_MODERATION / BAN / LOCK
    ≫ (dwa rzędy wielkości) każdy sygnał pozytywny — działa jak STOP, nie jak waga

INTENTIONAL_AMPLIFICATION (cytat, crosspost) ≈ SUBSTANTIVE_REPLY (odpowiedź z treścią)
    > REPOST bez komentarza
    > REPLY (odpowiedź krótka)
    > FOLLOW / SUBSCRIBE
    > LIKE / UPVOTE
```

`INTENTIONAL_AMPLIFICATION` i `SUBSTANTIVE_REPLY` są RÓWNORZĘDNE. Nie ustawiasz
jednego przed drugim na podstawie klasy — o kolejności między nimi decyduje dopiero
kalibracja.

Zastrzeżenie metodologiczne, które obowiązuje także ciebie: opublikowane wagi rankingu
platform mnożą przewidywane prawdopodobieństwa akcji konkretnego widza, a nie surowe
liczniki. Używasz ich wyłącznie jako heurystyki PORZĄDKOWEJ i nigdy jako liczbowego
score udającego rachunek platformy.

## Twarde reguły

1. **Wątek z sygnałem negatywnym nigdy nie trafia na krótką listę.** Ląduje na końcu
   porządku z `priority_tier = REJECT`. Wysoki engagement tego nie zmienia.
2. **Wątek z `window_state = CLOSED` nie trafia na krótką listę** — wejście nie ma już sensu.
3. **`fake_engagement_flags` NIE są wejściem porządkowania.** Wątek z flagą ma zajmować
   dokładnie to samo miejsce, co ten sam wątek bez flagi.
4. **Metryka nieznana nie jest zerem.** Wątek bez widocznego zasięgu nie spada na koniec
   z powodu braku odczytu; ląduje na końcu SWOJEJ klasy.
5. Wynik ma być odtwarzalny: ta sama lista wejściowa w innej kolejności ma dać ten sam
   porządek wyjściowy.

## Krótka lista

`shortlisted = true` dostają wyłącznie wątki z `priority_tier` A albo B, bez sygnału
negatywnego i z oknem innym niż `CLOSED`.
