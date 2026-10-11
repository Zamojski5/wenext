# community_observer — notatki wykonawcze

Element 1–3 ścieżki krytycznej Outcome A (`docs/OUTCOMES.md` §5). Pakiet czysty: `httpx`,
`pydantic`, `contracts`. Bez bazy, bez przeglądarki, bez zapisu gdziekolwiek.

## Co tu jest

| Moduł | Co robi |
| --- | --- |
| `source.py` | port `CommunitySource` (dwie operacje: lista nowych, jeden wątek z drzewem) i cztery klasy błędów — **źródło, które nie umie czytać, rzuca, nigdy nie zwraca pustej listy** |
| `models.py` | `ObservedThread`, `ObservedComment`, `ThreadSnapshot`, `ThreadMetrics` z `Observed[int]`; każdy rekord ma URL, `observed_at` ze strefą i SHA-256 surowego payloadu |
| `hackernews.py` | Algolia `search_by_date` + `items/{id}`; community `ask`/`show`/`new`/`front`; `PUBLIC_API_NO_ACCOUNT` |
| `reddit.py` | oficjalne Data API, OAuth aplikacji *script*; `ACCOUNT_REQUIRED`; bez poświadczeń `CredentialsMissing` z nazwami zmiennych |
| `text.py` | HTML HN → tekst (cztery tagi); markdown Reddita przechodzi bez zmian |

## Co zweryfikowano uruchomieniem (2026-09-03)

- Payloady w `tests/fixtures/` **nagrane `curl`-em z prawdziwego Algolii**, nie napisane ręcznie:
  5 najnowszych Ask HN, 3 Show HN, wątek 4102013 (44 komentarze, głębokość 4).
- Suita offline na `MockTransport` — liczby w `docs/STAN-PRAC.md` sekcja 5.
- `tests/test_hackernews_live.py` (`-m live`) — prawdziwe API, bez konta.

## Czego NIE zweryfikowano

- **Reddit na żywo.** Nie ma zarejestrowanej aplikacji. Kształty payloadów są udokumentowane,
  testy jadą na `MockTransport`. Pierwszy przebieg na żywo należy do założyciela
  (`docs/OUTCOMES.md` §6 pkt 1). Test `live` dla Reddita **celowo nie istnieje**, dopóki nie ma
  poświadczeń — test pomijany z braku konfiguracji to zieleń nad niczym.

## Decyzje zapisane w kodzie, o które łatwo się potknąć

1. `fetch_thread` dla HN liczy `comment_count` z pobranego drzewa (pomiar), a listing bierze
   `num_comments` z hitu (licznik platformy). Oba mogą się różnić o komentarze usunięte —
   i to jest informacja, nie błąd.
2. Usunięty komentarz zostaje wierszem bez autora i treści. Moderacja jest sygnałem (22.7).
3. `raw_sha256` hitu Algolii pomija `_highlightResult` — to dekoracja zapytania, nie dane.
4. Reddit `/new` nie ma filtra serwerowego po czasie; watermark `since` jest stosowany po
   stronie klienta. Przy ponad 100 nowych postach między odczytami listing jest niepełny —
   to ograniczenie do zmierzenia na żywym subreddicie, nie do obejścia paginacją w Outcome A.
