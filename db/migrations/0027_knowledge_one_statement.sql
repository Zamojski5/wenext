-- =============================================================================
-- Migracja 0027 — jedno ustalenie, jeden wiersz
--
-- POWÓD JEST ZMIERZONY, NIE WYMYŚLONY. Test restartu workera (`test_session_turn_e2e.py`) padł
-- 20.09 pod obciążeniem: zabija workera po `TOOL_CALL`, a strażnik powtórzeń w rejestrze narzędzi
-- rozpoznaje powtórkę po PARZE `TOOL_CALL` + `TOOL_RESULT` w śladzie. Gdy przerwanie trafi
-- dokładnie między te dwa zapisy, powtórzona tura woła `memory.write` drugi raz i to samo zdanie
-- ląduje w pamięci projektu dwa razy.
--
-- DLACZEGO W BAZIE, A NIE W KODZIE NARZĘDZIA. Sprawdzenie „czy już jest" przed zapisem przegrywa
-- dokładnie ten wyścig, który ma wygrać: dwie tury sprawdzają w tej samej chwili, obie widzą
-- pustkę, obie piszą. Unikalny indeks jest jedynym miejscem, w którym równoczesność rozstrzyga
-- się bez zgadywania, i to jest ta sama zasada, co przy „najwyżej jeden aktywny" w 0001 i 0013.
--
-- PO CZYM UZNAJEMY, ŻE TO TO SAMO ZDANIE: po skrócie treści ZNORMALIZOWANEJ, czyli bez różnic
-- w wielkości liter i w białych znakach. Model potrafi napisać to samo zdanie drugi raz z inną
-- spacją; dla człowieka to jest jedno ustalenie i dwa wiersze wyglądałyby jak usterka.
-- Ta sama normalizacja, co w `pilot/publication.py::normalised_draft`: „dwa teksty różniące się
-- tylko odstępem i wielkością liter to TEN SAM tekst".
--
-- ZAKRES NIEPOWTARZALNOŚCI: organizacja, przestrzeń, projekt, osoba i rodzaj. Osoba jest w kluczu,
-- bo to samo zdanie o dwóch różnych ludziach to dwa różne ustalenia; rodzaj, bo „fakt" i
-- „preferencja" o tej samej treści znaczą co innego. NULLS NOT DISTINCT, inaczej ustalenia bez
-- projektu albo bez osoby (NULL) omijałyby indeks w całości.
--
-- WYCOFANE NIE BLOKUJĄ. Indeks jest częściowy (`retracted_at IS NULL`): zdanie wycofane ma dać
-- się zapisać ponownie, bo wycofanie znaczy „to już nie obowiązuje", a nie „tego zdania nigdy
-- więcej".
-- =============================================================================

BEGIN;

-- Czyszczenie PRZED indeksem, bo w bazie mogą już leżeć duplikaty z tego samego wyścigu.
-- Zostaje NAJSTARSZY wiersz każdej grupy: to on ma identyfikator, na który mogły się już
-- powołać inne tabele i ślady sesji. Młodsze kopie są wycofywane z powodem, NIE kasowane:
-- `knowledge_items` jest pamięcią projektu i usunięty wiersz byłby zdaniem, które zniknęło
-- bez śladu.
WITH pogrupowane AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY organization_id, workspace_id, project_id, person_profile_id, kind,
                            md5(lower(regexp_replace(btrim(statement), '\s+', ' ', 'g')))
               ORDER BY created_at, id
           ) AS numer
      FROM knowledge_items
     WHERE retracted_at IS NULL
)
UPDATE knowledge_items
   SET retracted_at = now(),
       retracted_reason = 'duplikat tej samej treści, wycofany przy migracji 0027'
 WHERE id IN (SELECT id FROM pogrupowane WHERE numer > 1);

CREATE UNIQUE INDEX knowledge_items_one_live_statement
    ON knowledge_items (
        organization_id,
        workspace_id,
        project_id,
        person_profile_id,
        kind,
        md5(lower(regexp_replace(btrim(statement), '\s+', ' ', 'g')))
    )
    NULLS NOT DISTINCT
    WHERE retracted_at IS NULL;

COMMIT;
