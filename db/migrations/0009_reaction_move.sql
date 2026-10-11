-- =============================================================================
-- Migracja 0009 — „ruch" szkicu (prompt reaction_baseline v3, 2026-09-13)
--
-- Założyciel: komentarze brzmią maszynowo, mają prowokować dyskusję jak wpisy ekspertów. Pomiar na
-- 2 245 komentarzach z punktami: górne 10% nie różni się formą (długość, pytania, listy), tylko
-- postawą: teza z granicą, kontrpogląd, historia z pierwszej ręki, obraz, konkretna liczba.
-- Prompt v3 kazał generatorowi wybrać JEDEN taki ruch przed pisaniem. Ta kolumna go zapisuje,
-- żeby wynik po 24 h (punkty, odpowiedzi) dało się czytać per ruch i per społeczność, czyli
-- żeby system miał z czego się uczyć (D-029 pkt 4).
--
-- NULL = reakcja sprzed v3 albo bez tekstu (WAIT, SILENCE). Zamknięta lista jak w
-- `observe/reactions.py::MOVES`; parytet sprawdza `services/db_schema/tests/test_enum_parity.py`.
-- =============================================================================

BEGIN;

ALTER TABLE pilot_reactions
    ADD COLUMN move text,
    ADD CONSTRAINT pilot_reactions_move_is_known CHECK (
        move IS NULL OR move IN (
            'CLAIM_WITH_LIMIT', 'COUNTERPOINT', 'FIRST_HAND', 'IMAGE', 'NUMBER', 'NONE'
        )
    );

COMMIT;
