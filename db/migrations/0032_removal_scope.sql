-- =============================================================================
-- 0032. CZEGO DOTYCZY USUNIĘCIE: naszej odpowiedzi czy całego wątku pod nią.
--
-- CO TO ZAMYKA, zmierzone 26.09.2026. 23.09 nasza jedyna nowa odpowiedź w r/LLMDevs
-- (`t1_pblmb3d`) pokazała się anonimowo jako „Comment removed by moderator”. Produkt zapisał
-- `public_visibility = REMOVED`, a ludzie czytający ten zapis postawili hipotezę o wieku i
-- karmie konta i wpisali ją do PROBA-RDZENIA-2026-09-23 jako możliwą przyczynę.
--
-- 26.09 odczyt tego samego wątku pokazał coś innego: `removed_by_category = reddit`,
-- `selftext = [removed]`, a wyrenderowany permalink mówi wprost „Sorry, this post was removed
-- by Reddit's filters”. Zniknął CAŁY POST kolegi `Extra_Credit8695`, nie nasz komentarz.
-- Nasze konto było wtedy i jest nadal czyste: `is_suspended = false`, 233 karmy,
-- `user_is_banned = false` w tej społeczności.
--
-- Różnica nie jest kosmetyczna. `REMOVED` bez zakresu znaczy dla następnej sesji „to miejsce
-- nas odrzuca”, więc agent zacznie unikać społeczności, która go nie odrzuciła, albo zacznie
-- „rozgrzewać konto”, które rozgrzania nie potrzebuje. ADR-0037 wymaga, żeby doświadczenie
-- wiązało sytuację z zaobserwowanym wynikiem; wynik przypisany złej przyczynie jest gorszy niż
-- jego brak, bo wygląda na wiedzę.
--
-- DLACZEGO OSOBNA KOLUMNA, a nie `removal_source`. Tamta mówi, GDZIE się dowiedzieliśmy
-- (`INBOX` albo `ANONYMOUS_READ`), i ma zamkniętą listę dokładnie po to, żeby nie mieszać
-- dwóch pytań w jednym polu. `removal_scope` odpowiada na pytanie CZEGO usunięcie dotyczy.
-- Dwa pytania, dwie kolumny, obie z zamkniętą listą, wzorzec z 0007 i 0012.
--
-- NULL zostaje wartością domyślną i znaczy „nie ustalono zakresu”, a nie „komentarza”.
-- Migracja NIE przepisuje żadnego istniejącego wiersza: zakresu dla piętnastu dotychczasowych
-- `REMOVED` nikt nie mierzył, a wpisanie go teraz byłoby werdyktem bez odczytu (D-007).
-- Jedyny wiersz, o którym wiemy, dostanie zakres z pomiaru, nie z tej migracji.
-- =============================================================================

BEGIN;

ALTER TABLE publication_requests
    ADD COLUMN removal_scope text,
    ADD CONSTRAINT publication_requests_removal_scope_is_known CHECK (
        removal_scope IS NULL OR removal_scope IN ('COMMENT', 'PARENT_POST')
    ),
    -- Zakres bez usunięcia jest zdaniem o niczym. Dopuszczamy go wyłącznie tam, gdzie werdykt
    -- widoczności faktycznie mówi o usunięciu.
    ADD CONSTRAINT publication_requests_removal_scope_needs_a_removal CHECK (
        removal_scope IS NULL OR public_visibility = 'REMOVED'
    );

COMMIT;
