-- =============================================================================
-- Migracja 0010 — pętla uczenia, krok 1 (D-031, 2026-09-15)
--
-- Założyciel: „nie chcę takiego głupiego systemu, chcę, żeby się uczył… mamy tyle danych i chcę
-- je lepiej wykorzystywać, co ludzi angażuje… dodać ironię albo coś, co pomoże się wyróżnić".
-- Uczenie w tym systemie to nie zmiana modelu, tylko: (1) eksperyment przy każdym szkicu,
-- (2) wynik czytany per ruch i per społeczność, (3) wynik z powrotem na wejściu generatora.
--
-- Ta migracja daje (1): `requested_move` to ruch, o który cykl POPROSIŁ generator (najmniej
-- próbkowany w tej społeczności), a `move` (0009) to ruch, który model faktycznie wybrał.
-- Różnica między nimi to odmowa modelu z uzasadnieniem; wynik po 24 h czyta się po obu.
-- Lista ruchów rośnie o IRONY (sucha ironia wobec sytuacji, nie wobec pytającego) i ONE_LINER
-- (jedno zdanie z mocną opinią): dwa z czterech najwyżej ocenianych wzorów w pomiarze z 13.09.
--
-- NULL w `requested_move` = reakcja sprzed D-031. Zamknięta lista jak w
-- `observe/reactions.py::MOVES`.
-- =============================================================================

BEGIN;

ALTER TABLE pilot_reactions
    DROP CONSTRAINT pilot_reactions_move_is_known,
    ADD CONSTRAINT pilot_reactions_move_is_known CHECK (
        move IS NULL OR move IN (
            'CLAIM_WITH_LIMIT', 'COUNTERPOINT', 'FIRST_HAND', 'IMAGE', 'NUMBER',
            'IRONY', 'ONE_LINER', 'NONE'
        )
    ),
    ADD COLUMN requested_move text,
    ADD CONSTRAINT pilot_reactions_requested_move_is_known CHECK (
        requested_move IS NULL OR requested_move IN (
            'CLAIM_WITH_LIMIT', 'COUNTERPOINT', 'FIRST_HAND', 'IMAGE', 'NUMBER',
            'IRONY', 'ONE_LINER'
        )
    );

COMMIT;
