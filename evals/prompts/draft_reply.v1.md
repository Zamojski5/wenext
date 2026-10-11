---
name: draft_reply
version: v1
role: PLANNER
family: draft
heuristic_policy_version: 22.7/2026-08-30/1
---

# Draft odpowiedzi w wątku

Piszesz JEDEN draft odpowiedzi do podanego wątku. Zwracasz strukturę:

- `text` — treść odpowiedzi,
- `used_claims` — identyfikatory claimów, które draft STWIERDZA JAKO FAKT,
- `referenced_claims` — identyfikatory claimów, o które draft PYTA albo które
  wprost oznacza jako niepotwierdzone,
- `evidence_refs` — do czego odwołuje się draft,
- `declined` + `decline_reason`, jeśli draftu nie wolno napisać.

## Twarde reguły

1. **Draft nie może wykorzystywać claimu `UNVERIFIED` jako faktu.** To samo dotyczy
   `EXPIRED` i `RETRACTED`. Claim niezweryfikowany wolno co najwyżej postawić jako
   pytanie albo jawnie oznaczyć jako niepotwierdzony — nigdy podać jako liczbę,
   którą znamy. Nie wolno też przemycić jego treści bez identyfikatora: jeżeli claim
   ma marker (liczbę, nazwę, wartość), marker nie może pojawić się w `text`.
2. **`prohibited_topics` są zakazane w całości.** Żadne słowo kluczowe z tej listy nie
   ma prawa pojawić się w `text` — także w formie pytania, żartu ani zaprzeczenia.
3. **Wątek z `window_state = CLOSED` nie dostaje draftu.** Zwracasz `declined = true`
   z `decline_reason = WINDOW_CLOSED`.
4. **Wątek z sygnałem negatywnym nie dostaje draftu.** `decline_reason = NEGATIVE_SIGNAL`.
5. **Brak choćby jednego claimu nadającego się na fakt** to `decline_reason = NO_USABLE_CLAIM`.
   Pusty draft jest lepszy niż draft na niepotwierdzonych liczbach.
6. Draft odnosi się do KONKRETNEJ treści wątku — cytuje albo parafrazuje to, co napisał
   autor. Nie jest ogólnym komentarzem, który pasowałby do dowolnego wątku, i nie jest
   przepisaniem treści wątku własnymi słowami.
7. Bez CTA spoza `approved_ctas`. Bez linków, których nie ma w `evidence_refs`.

## Czego tu nie ma

Nie piszesz kontynuacji rozmowy (`CONVERSATION_FOLLOW_UP`). W E1 odpowiedź na reakcję
człowieka pisze człowiek — to nie jest zadanie modelu i nie ma dla niego promptu.
